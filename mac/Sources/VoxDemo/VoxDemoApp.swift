import AVFoundation
import SwiftUI

// MARK: - Sidecar

/// Owns the Python process that hosts VoxCPM2 and the HyperFrames renderer.
@Observable
final class Engine {
    var health: Health?
    var voices: [Voice] = []
    var lastError: String?
    var repoPath: String {
        didSet { UserDefaults.standard.set(repoPath, forKey: "repoPath") }
    }

    private var process: Process?
    private var logHandle: FileHandle?
    let logURL = FileManager.default.temporaryDirectory.appendingPathComponent("voxdemo-engine.log")

    // ponytail: the repo is the app's payload — path is remembered, not embedded.
    // Default is wherever this source tree lived at build time.
    static let defaultRepo = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().path

    init() {
        repoPath = UserDefaults.standard.string(forKey: "repoPath") ?? Engine.defaultRepo
    }

    var python: String { repoPath + "/.venv/bin/python" }
    var serverScript: String { repoPath + "/server.py" }
    var installed: Bool {
        FileManager.default.isExecutableFile(atPath: python)
            && FileManager.default.fileExists(atPath: serverScript)
    }

    func start() {
        guard process == nil else { return }
        guard installed else {
            lastError = "Engine not found. Expected \(python) and \(serverScript)."
            return
        }
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        logHandle = try? FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [serverScript]
        p.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        var env = ProcessInfo.processInfo.environment
        env["VOXDEMO_PORT"] = String(API.port)
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        if let logHandle { p.standardOutput = logHandle; p.standardError = logHandle }
        do { try p.run() } catch { lastError = "Could not start engine: \(error.localizedDescription)"; return }
        process = p
        lastError = nil
    }

    func stop() {
        process?.terminate()
        process = nil
    }

    func restart() {
        stop()
        health = nil
        start()
    }

    /// Poll /health until the model is loaded, then keep an eye on it.
    func watch() async {
        while !Task.isCancelled {
            do {
                let h: Health = try await API.get("/health")
                health = h
                if h.ready { await refreshVoices() }
            } catch {
                health = nil
                // The sidecar died (crash, OOM, manual kill) — bring it back.
                if let p = process, !p.isRunning { process = nil; start() }
            }
            try? await Task.sleep(for: .seconds(health?.ready == true ? 8 : 2))
        }
    }

    func refreshVoices() async {
        do {
            let r: VoiceList = try await API.get("/voices")
            if r.voices != voices { voices = r.voices }
        } catch { /* /health already reports engine trouble */ }
    }

    var statusText: String {
        if let lastError { return lastError }
        guard let h = health else { return process == nil ? "Engine stopped" : "Starting engine…" }
        if !h.error.isEmpty { return "Model error: \(h.error)" }
        if h.loading { return "Loading VoxCPM2 (~5 GB, first run downloads weights)…" }
        return h.ready ? "Ready · \(h.model) · \(h.device)" : "Engine idle"
    }
}

// MARK: - Wire types

struct Health: Codable, Equatable {
    var ready: Bool
    var loading: Bool
    var error: String
    var dry_run: Bool
    var model: String
    var device: String
    var sample_rate: Int
    var output_dir: String
    var themes: [String]
    var aspects: [String]
}

struct Voice: Codable, Identifiable, Equatable, Hashable {
    var id: String
    var name: String
    var kind: String
    var description: String
    var transcript: String
    var enrolled: Bool
    var isPreset: Bool { kind == "preset" }
}

struct VoiceList: Codable { var voices: [Voice] }
struct JobRef: Codable { var job_id: String }
struct Deleted: Codable { var deleted: String }

struct Job<R: Codable>: Codable {
    var id: String
    var state: String
    var progress: Double
    var message: String
    var error: String
    var result: R?
}

struct SpeakResult: Codable { var path: String; var duration: Double; var voice: String }
struct DemoResult: Codable {
    var path: String
    var project: String
    var scenes: Int
    var duration: Double
    var voice: String
}

// MARK: - API client

enum APIError: LocalizedError {
    case http(String)
    case offline
    var errorDescription: String? {
        switch self {
        case .http(let m): return m
        case .offline: return "The engine isn't answering on 127.0.0.1:\(API.port)."
        }
    }
}

enum API {
    static let port = 8809
    static let base = URL(string: "http://127.0.0.1:\(port)")!
    private static let decoder = JSONDecoder()

    private static func send<T: Decodable>(_ path: String, method: String, body: Data?) async throws -> T {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = method
        req.timeoutInterval = 30
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, resp): (Data, URLResponse)
        do { (data, resp) = try await URLSession.shared.data(for: req) }
        catch { throw APIError.offline }
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let detail = (try? decoder.decode([String: String].self, from: data))?["detail"]
            throw APIError.http(detail ?? "HTTP \(code)")
        }
        return try decoder.decode(T.self, from: data)
    }

    static func get<T: Decodable>(_ path: String) async throws -> T {
        try await send(path, method: "GET", body: nil)
    }

    static func post<B: Encodable, T: Decodable>(_ path: String, _ body: B) async throws -> T {
        try await send(path, method: "POST", body: try JSONEncoder().encode(body))
    }

    static func delete<T: Decodable>(_ path: String) async throws -> T {
        try await send(path, method: "DELETE", body: nil)
    }

    /// Kick off a job and poll it to completion, reporting progress as it goes.
    static func run<B: Encodable, R: Codable>(
        _ path: String, _ body: B, onStep: @MainActor (Double, String) -> Void
    ) async throws -> R {
        let ref: JobRef = try await post(path, body)
        while true {
            try await Task.sleep(for: .milliseconds(600))
            let job: Job<R> = try await get("/jobs/\(ref.job_id)")
            await onStep(job.progress, job.message)
            if job.state == "error" { throw APIError.http(job.error) }
            if job.state == "done", let result = job.result { return result }
        }
    }
}

// MARK: - Shared bits

@MainActor
final class Preview {
    static let shared = Preview()
    private var player: AVAudioPlayer?
    func play(_ path: String) {
        player?.stop()
        player = try? AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
        player?.play()
    }
    func stop() { player?.stop() }
}

extension View {
    func card() -> some View {
        padding(16)
            .background(Color(nsColor: .controlBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    var engine: Engine?
    func applicationWillTerminate(_ notification: Notification) { engine?.stop() }
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

@main
struct VoxDemoApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var engine = Engine()
    @State private var watcher: Task<Void, Never>?

    var body: some Scene {
        Window("VoxDemo", id: "main") {
            ContentView()
                .environment(engine)
                .frame(minWidth: 940, minHeight: 660)
                .task {
                    delegate.engine = engine
                    engine.start()
                    watcher = Task { await engine.watch() }
                }
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Restart Engine") { engine.restart() }
                Button("Open Engine Log") { NSWorkspace.shared.open(engine.logURL) }
            }
        }
    }
}

struct ContentView: View {
    @Environment(Engine.self) private var engine
    @State private var tab = 0

    var body: some View {
        VStack(spacing: 0) {
            TabView(selection: $tab) {
                DemoView().tabItem { Label("Demo", systemImage: "film.stack") }.tag(0)
                VoicesView().tabItem { Label("Voices", systemImage: "waveform") }.tag(1)
            }
            .padding(.top, 8)
            Divider()
            StatusBar()
        }
    }
}

struct StatusBar: View {
    @Environment(Engine.self) private var engine
    @State private var pickingRepo = false

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(engine.health?.ready == true ? .green
                      : engine.health?.loading == true ? .orange : .red)
                .frame(width: 8, height: 8)
            Text(engine.statusText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            if engine.health?.ready != true && engine.health?.loading != false {
                ProgressView().controlSize(.small)
            }
            Spacer()
            if !engine.installed {
                Button("Locate repo…") { pickingRepo = true }.controlSize(.small)
            }
            Button("Restart") { engine.restart() }.controlSize(.small)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
        .fileImporter(isPresented: $pickingRepo, allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result {
                engine.repoPath = url.path
                engine.restart()
            }
        }
    }
}
