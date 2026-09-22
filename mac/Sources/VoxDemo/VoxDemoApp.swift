import AVFoundation
import AVKit
import SwiftUI

// MARK: - Sidecar

/// Owns the Python process that hosts VoxCPM2, the script writer and the renderer.
@Observable
final class Engine {
    var health: Health?
    var voices: [Voice] = []
    var settings: SettingsPayload?
    var lastError: String?

    /// Set when the preferred port was taken, so the status bar can say so instead
    /// of quietly running on a different port than the user expects.
    var portNote = ""
    private var childStartedAt = Date()
    private var startAttempts = 0

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

        // Never spawn into an occupied port. The child dies on the bind, and the app
        // would then be talking to whatever is already sitting there — which answers
        // /health perfectly while being an older build, or one started from a context
        // that cannot reach the model server. That failure looks like "oMLX refused
        // the connection" no matter what the user types into Settings.
        let wanted = API.preferredPort
        if API.portInUse(wanted) {
            let free = API.freePort(from: wanted + 1)
            API.port = free
            portNote = "Port \(wanted) was already in use, so the engine is on \(free). "
                     + "Another copy of VoxDemo may still be running."
        } else {
            API.port = wanted
            portNote = ""
        }

        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        logHandle = try? FileHandle(forWritingTo: logURL)
        // Start each run with an empty log. Appending would let engineLogTail
        // report the previous run's failure as if it were this one's — which is
        // exactly how a port conflict gets misread as a bad API key.
        try? logHandle?.truncate(atOffset: 0)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [serverScript]
        p.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        var env = ProcessInfo.processInfo.environment
        env["VOXDEMO_PORT"] = String(API.port)
        // The sidecar watches this pid and exits when it disappears. We cannot
        // promise to clean up after ourselves — a force-quit or a crash skips
        // applicationWillTerminate entirely — so the child is made responsible.
        env["VOXDEMO_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        p.environment = env
        if let logHandle { p.standardOutput = logHandle; p.standardError = logHandle }
        do { try p.run() } catch {
            lastError = "Could not start engine: \(error.localizedDescription)"
            return
        }
        process = p
        childStartedAt = Date()
        startAttempts += 1
        lastError = nil
    }

    /// The tail of the sidecar's own log. A child that dies on the way up explains
    /// itself there and nowhere else — a bare spinner tells the user nothing.
    func engineLogTail(_ lines: Int = 3) -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        return text.split(separator: "\n")
            .suffix(40)
            .filter { !$0.hasPrefix("INFO:") && !$0.isEmpty }
            .suffix(lines)
            .joined(separator: " · ")
            .trimmingCharacters(in: .whitespaces)
    }

    /// SIGTERM, escalating to SIGKILL if the child ignores it. The wait runs off
    /// the main thread so a button press cannot stall behind a stubborn child.
    func stop() {
        guard let p = process else { return }
        process = nil
        logHandle?.closeFile()
        logHandle = nil
        guard p.isRunning else { return }
        p.terminate()
        DispatchQueue.global(qos: .utility).async {
            var waited = 0
            while p.isRunning, waited < 40 {          // ~2 s
                usleep(50_000)
                waited += 1
            }
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    /// The quit path: `stop()`, but block until the child is really gone. macOS
    /// does not give a terminating app unlimited time, so this is bounded.
    func stopAndWait(timeout: TimeInterval = 3) {
        let p = process
        stop()
        guard let p else { return }
        let deadline = Date().addingTimeInterval(timeout)
        while p.isRunning, Date() < deadline { usleep(50_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }

    func restart() {
        // Wait for the old child to release the port, or start() reads the dying
        // process as "port in use" and the engine drifts to a new port on every
        // restart — with a status note blaming a second copy of the app.
        stopAndWait(timeout: 2)
        health = nil
        lastError = nil
        startAttempts = 0
        start()
    }

    /// Poll /health until the model is loaded, then keep an eye on it.
    func watch() async {
        while !Task.isCancelled {
            do {
                let h: Health = try await API.get("/health")
                health = h
                startAttempts = 0
                if h.ready { await refreshVoices() }
                if settings == nil { await loadSettings() }
            } catch {
                health = nil
                // The sidecar died (crash, OOM, manual kill) — bring it back.
                if let p = process, !p.isRunning {
                    process = nil
                    // Dying on the way up is not a hiccup. Twice means something is
                    // wrong that a restart cannot fix, so stop looping and show what
                    // the sidecar actually said.
                    if Date().timeIntervalSince(childStartedAt) < 20, startAttempts >= 2 {
                        let tail = engineLogTail()
                        lastError = "The engine stopped right after starting."
                            + (tail.isEmpty ? "" : "\n\n\(tail)")
                        try? await Task.sleep(for: .seconds(10))
                        continue
                    }
                    start()
                }
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

    func loadSettings() async {
        do { settings = try await API.get("/settings") } catch { /* shown by the status bar */ }
    }

    @discardableResult
    func saveSettings(provider: ProviderConfig? = nil, defaults: DefaultsConfig? = nil) async -> Bool {
        var body: [String: Any] = [:]
        if let provider { body["provider"] = provider.asJSON() }
        if let defaults { body["defaults"] = defaults.asJSON() }
        do {
            let data = try JSONSerialization.data(withJSONObject: body)
            let r: SettingsPayload = try await API.put("/settings", rawBody: data)
            settings = r
            return true
        } catch {
            lastError = error.localizedDescription
            return false
        }
    }

    var statusText: String {
        if let lastError { return lastError }
        guard let h = health else {
            return process == nil ? "Engine stopped" : "Starting engine on 127.0.0.1:\(API.port)…"
        }
        if !h.error.isEmpty { return "Model error: \(h.error)" }
        if h.loading { return "Loading VoxCPM2 (~5 GB, first run downloads weights)…" }
        if !h.warm.isEmpty { return "Ready · preparing preset voice \(h.warm)" }
        let base = h.ready ? "Ready · \(h.device) · port \(API.port)" : "Engine idle"
        return portNote.isEmpty ? base : "\(base) — \(portNote)"
    }

    var ready: Bool { health?.ready == true }
}

// MARK: - Wire types

struct Health: Codable, Equatable {
    var ready: Bool = false
    var loading: Bool = false
    var error: String = ""
    var warm: String = ""
    var dry_run: Bool = false
    var model: String = ""
    var device: String = ""
    var sample_rate: Int = 0
    var output_dir: String = ""
    var themes: [String] = []
    var aspects: [String] = []
    var theme_labels: [String: String] = [:]
    var theme_palettes: [String: ThemePalette] = [:]
    var visual_kinds: [String] = []
    var claude_available: Bool = false
}

struct ThemePalette: Codable, Equatable, Hashable {
    var bg: String = "#07080d"
    var bg2: String = "#141a2e"
    var accent: String = "#7c93ff"
    var accent2: String = "#39d3c0"
    var text: String = "#f2f4ff"
    var dark: Bool = true
}

struct Voice: Codable, Identifiable, Equatable, Hashable {
    var id: String
    var name: String
    var kind: String
    var description: String
    var enrolled: Bool
    var isPreset: Bool { kind == "preset" }
}

struct VoiceList: Codable { var voices: [Voice] = [] }
struct JobRef: Codable { var job_id: String }
struct Deleted: Codable { var deleted: String }

struct Job<R: Codable>: Codable {
    var id: String = ""
    var state: String = "running"
    var progress: Double = 0
    var message: String = ""
    var error: String = ""
    var result: R?
}

struct SpeakResult: Codable {
    var path: String
    var duration: Double
    var voice: String
}

struct DemoResult: Codable {
    var path: String
    var project: String
    var scenes: Int
    var duration: Double
    var voice: String
    var title: String = ""
    var theme: String = ""
    var aspect: String = ""
    var has_hook: Bool = false
    var has_close: Bool = false
}

struct CloseBeat: Codable, Equatable {
    var heading: String = ""
    var text: String = ""
    var cta: String = ""
}

struct AnalyzeResult: Codable {
    var title: String = ""
    var subtitle: String = ""
    var hook: String = ""
    var logo: String = ""
    var scenes: [AnalyzeScene] = []
    var close: CloseBeat?
    var provider: String = ""
    var cost_usd: Double = 0
}

struct AnalyzeScene: Codable {
    var heading: String = ""
    var role: String = ""
    var media: String = ""
    var text: String = ""
    var visual: String = ""
    var visual_ref: String = ""
    var visual_note: String = ""
}

struct ProviderConfig: Codable, Equatable {
    var kind: String = "openai"
    var preset: String = "omlx"
    var base_url: String = ""
    var api_key: String = ""
    var model: String = ""
    var temperature: Double = 0.4
    var max_tokens: Int = 6000
    var timeout: Int = 300
    var digest_budget: Int = 48000
    var claude_model: String = "sonnet"
    var api_key_set: Bool = false

    func asJSON() -> [String: Any] {
        ["kind": kind, "preset": preset, "base_url": base_url, "api_key": api_key,
         "model": model, "temperature": temperature, "max_tokens": max_tokens,
         "timeout": timeout, "digest_budget": digest_budget, "claude_model": claude_model]
    }
}

struct DefaultsConfig: Codable, Equatable {
    var theme: String = "midnight"
    var aspect: String = "landscape"
    var voice_id: String = "aria"
    var sfx: Bool = true
    var scenes: Int = 6
    var angle: String = ""

    func asJSON() -> [String: Any] {
        ["theme": theme, "aspect": aspect, "voice_id": voice_id,
         "sfx": sfx, "scenes": scenes, "angle": angle]
    }
}

struct PresetInfo: Codable, Equatable {
    var label: String = ""
    var base_url: String = ""
    var local: Bool = false
    var key_required: Bool = false
    var note: String = ""
}

struct SettingsPayload: Codable {
    var provider: ProviderConfig = ProviderConfig()
    var defaults: DefaultsConfig = DefaultsConfig()
    var presets: [String: PresetInfo] = [:]
    var claude_available: Bool = false
    var output_dir: String = ""
}

struct ModelList: Codable {
    var ok: Bool = false
    var models: [String] = []
    var error: String = ""
    var provider: String = ""
}

struct TestResult: Codable {
    var ok: Bool = false
    var models: [String] = []
    var reply: String = ""
    var error: String = ""
    var latency_ms: Int = 0
}

struct DetectedServer: Codable, Identifiable {
    var port: Int
    var hint: String = ""
    var base_url: String = ""
    var models: [String] = []
    var ok: Bool = false
    var error: String = ""
    var api_key: String = ""
    var id: Int { port }
}

struct DetectResult: Codable { var found: [DetectedServer] = [] }

struct DemoItem: Codable, Identifiable, Hashable {
    var id: String
    var name: String
    var subtitle: String = ""
    var path: String
    var project: String
    var bytes: Int = 0
    var scenes: Int = 0
    var duration: Double = 0
    var voice: String = ""
    var theme: String = ""
    var aspect: String = ""
    var has_hook: Bool = false
    var has_close: Bool = false
    var created: String = ""

    var aspectRatio: CGFloat {
        aspect == "portrait" ? 9.0 / 16 : aspect == "square" ? 1 : 16.0 / 9
    }
    var sizeText: String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
    var dateText: String {
        guard let d = ISO8601DateFormatter().date(from: created) else { return "" }
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f.string(from: d)
    }
}

struct LibraryResult: Codable { var demos: [DemoItem] = [] }

// MARK: - API client

enum APIError: LocalizedError {
    case http(String)
    case offline
    case timedOut(TimeInterval, String)
    var errorDescription: String? {
        switch self {
        case .http(let m): return m
        case .offline: return "The engine isn't answering on 127.0.0.1:\(API.port)."
        case .timedOut(let t, let path):
            // A timeout is not an unreachable engine. Saying "isn't answering"
            // sends you to restart the engine when the real cause is upstream —
            // usually a local model server still loading weights on first use.
            return "\(path) did not finish within \(Int(t)) s. The engine is "
                + "running, so this is almost always the model server it is "
                + "waiting on. If it is still warming up, give it a minute and retry."
        }
    }
}

enum API {
    /// Not a constant: a sidecar orphaned by a force-quit or a crash can still be
    /// sitting on the preferred port. Spawning into that fails the bind, the child
    /// dies, and the app goes on talking to the stranger — which answers /health
    /// perfectly while being an older build, or one started in a context that
    /// cannot reach your model server. So the port is chosen per launch.
    static var port = 8809
    static let preferredPort = 8809
    static var base: URL { URL(string: "http://127.0.0.1:\(port)")! }
    private static let decoder = JSONDecoder()

    /// Plain BSD connect — no side effects, nothing kept open.
    static func portInUse(_ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var tv = timeval(tv_sec: 0, tv_usec: 250_000)
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(port).bigEndian)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let r = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return r == 0
    }

    /// The first free port at or after `from`, so the app always owns its sidecar.
    static func freePort(from: Int = preferredPort, tries: Int = 24) -> Int {
        for candidate in from..<(from + tries) where !portInUse(candidate) {
            return candidate
        }
        return from
    }

    private static func send<T: Decodable>(_ path: String, method: String, body: Data?,
                                           timeout: TimeInterval = 30) async throws -> T {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, resp): (Data, URLResponse)
        do { (data, resp) = try await URLSession.shared.data(for: req) }
        catch {
            let ns = error as NSError
            if ns.domain == NSURLErrorDomain, ns.code == NSURLErrorTimedOut {
                throw APIError.timedOut(timeout, path)
            }
            throw APIError.offline
        }
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            let detail = (try? decoder.decode([String: String].self, from: data))?["detail"]
            throw APIError.http(detail ?? "HTTP \(code)")
        }
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.http("Unexpected reply from the engine: \(error.localizedDescription)") }
    }

    static func get<T: Decodable>(_ path: String, timeout: TimeInterval = 30) async throws -> T {
        try await send(path, method: "GET", body: nil, timeout: timeout)
    }

    static func post<B: Encodable, T: Decodable>(_ path: String, _ body: B) async throws -> T {
        try await send(path, method: "POST", body: try JSONEncoder().encode(body))
    }

    /// For bodies that are easier to build as dictionaries than as Codable structs.
    static func put<T: Decodable>(_ path: String, rawBody: Data) async throws -> T {
        try await send(path, method: "PUT", body: rawBody)
    }

    static func postRaw<T: Decodable>(_ path: String, rawBody: Data,
                                      timeout: TimeInterval = 30) async throws -> T {
        try await send(path, method: "POST", body: rawBody, timeout: timeout)
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

    /// Same, for a request whose body is a dictionary.
    static func runRaw<R: Codable>(
        _ path: String, _ body: [String: Any], onStep: @MainActor (Double, String) -> Void
    ) async throws -> R {
        let data = try JSONSerialization.data(withJSONObject: body)
        let ref: JobRef = try await postRaw(path, rawBody: data)
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

/// Native transport with a scrub bar — AVPlayerView already is one, so don't build one.
struct AudioPlayerBar: NSViewRepresentable {
    let url: URL
    var autoplay = false

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .inline
        view.showsFullScreenToggleButton = false
        view.player = AVPlayer(url: url)
        if autoplay { view.player?.play() }
        return view
    }

    func updateNSView(_ view: AVPlayerView, context: Context) {
        guard (view.player?.currentItem?.asset as? AVURLAsset)?.url != url else { return }
        view.player = AVPlayer(url: url)
        if autoplay { view.player?.play() }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    var engine: Engine?
    func applicationWillFinishLaunching(_ notification: Notification) {
        // Offscreen UI snapshots — runs before the window and the sidecar exist,
        // then exits. See Snapshots.swift.
        Snapshots.runIfRequested()
    }
    /// Cmd-Q and a scripted quit land here. A `kill` on the app does not — which
    /// is why the sidecar also watches us from the inside (VOXDEMO_PARENT_PID).
    func applicationWillTerminate(_ notification: Notification) { engine?.stopAndWait() }
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

@main
struct VoxDemoApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var engine = Engine()
    @State private var watcher: Task<Void, Never>?
    @State private var store = CreateStore()

    var body: some Scene {
        Window("VoxDemo", id: "main") {
            RootView()
                .environment(engine)
                .environment(store)
                .frame(minWidth: 1080, minHeight: 720)
                .task {
                    delegate.engine = engine
                    engine.start()
                    watcher = Task { await engine.watch() }
                }
        }
        .defaultSize(width: 1240, height: 820)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Restart Engine") { engine.restart() }
                Button("Open Engine Log") { NSWorkspace.shared.open(engine.logURL) }
            }
        }
    }
}

// MARK: - Shell

enum Destination: String, CaseIterable, Identifiable {
    case create, library, voices, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .create: return "Create"
        case .library: return "Library"
        case .voices: return "Voices"
        case .settings: return "Settings"
        }
    }
    var icon: String {
        switch self {
        case .create: return "wand.and.stars"
        case .library: return "rectangle.stack"
        case .voices: return "waveform"
        case .settings: return "gearshape"
        }
    }
}

struct RootView: View {
    @Environment(Engine.self) private var engine
    @State private var destination: Destination = .create
    @AppStorage("hasOnboarded") private var hasOnboarded = false

    /// The modal is shared by a dozen call sites, so the title is derived from
    /// the error text. The previous "Something went wrong" sent the user looking
    /// at the wrong part of the app — these categories at least point in the
    /// right direction so the message body is in context.
    private var alertTitle: String {
        let e = (engine.lastError ?? "").lowercased()
        if e.contains("api key") || e.contains("401") || e.contains("provider")
            || e.contains("/v1/") { return "Provider problem" }
        if e.contains("engine") || e.contains("model") || e.contains("weights")
            || e.contains("127.0.0.1") { return "Engine problem" }
        return "Something went wrong"
    }

    var body: some View {
        NavigationSplitView {
            Sidebar(destination: $destination)
                .navigationSplitViewColumnWidth(min: 210, ideal: 226, max: 260)
        } detail: {
            Group {
                switch destination {
                case .create: CreateView()
                case .library: LibraryView()
                case .voices: VoicesView()
                case .settings: SettingsView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .sheet(isPresented: .init(get: { !hasOnboarded },
                                  set: { if !$0 { hasOnboarded = true } })) {
            WelcomeSheet { hasOnboarded = true }
        }
        .alert(alertTitle, isPresented: .init(
            get: { engine.lastError != nil }, set: { if !$0 { engine.lastError = nil } })
        ) {
            Button("OK") { engine.lastError = nil }
        } message: { Text(engine.lastError ?? "") }
    }
}

struct Sidebar: View {
    @Environment(Engine.self) private var engine
    @Binding var destination: Destination

    var body: some View {
        VStack(spacing: 0) {
            List(selection: Binding(get: { destination },
                                    set: { if let v = $0 { destination = v } })) {
                Section {
                    ForEach(Destination.allCases) { item in
                        Label(item.title, systemImage: item.icon)
                            .tag(item)
                    }
                }
            }
            .listStyle(.sidebar)
            Divider()
            EngineStatus()
        }
    }
}

struct EngineStatus: View {
    @Environment(Engine.self) private var engine
    @State private var pickingRepo = false
    @State private var showingLog = false

    private var dot: Color {
        if engine.health?.ready == true { return .green }
        if engine.health?.loading == true { return .orange }
        return .red
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle().fill(dot).frame(width: 8, height: 8)
                Text(engine.health?.ready == true ? "Engine ready"
                     : engine.health?.loading == true ? "Loading model…" : "Engine stopped")
                    .font(.caption.weight(.medium))
                Spacer()
                if engine.health?.loading == true || engine.health == nil {
                    ProgressView().controlSize(.mini)
                }
            }
            Text(engine.statusText)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 6) {
                if !engine.installed {
                    Button("Locate repo…") { pickingRepo = true }
                        .controlSize(.small)
                }
                Button("Restart") { engine.restart() }.controlSize(.small)
                Button("Log") { showingLog = true }.controlSize(.small)
            }
        }
        .padding(12)
        .fileImporter(isPresented: $pickingRepo, allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result {
                engine.repoPath = url.path
                engine.restart()
            }
        }
        .sheet(isPresented: $showingLog) { LogSheet(url: engine.logURL) }
    }
}

struct LogSheet: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Engine log").font(.headline)
                Spacer()
                Button("Refresh") { load() }
                Button("Close") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            ScrollView {
                Text(text.isEmpty ? "No output yet." : text)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: 700, height: 420)
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(18)
        .onAppear(perform: load)
    }

    private func load() {
        text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        if text.count > 60_000 { text = String(text.suffix(60_000)) }
    }
}
