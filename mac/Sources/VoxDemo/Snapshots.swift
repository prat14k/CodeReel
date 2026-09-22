import AppKit
import SwiftUI

/// Renders the app's real views to PNGs offscreen.
///
/// `screencapture` needs the screen-recording TCC grant, which a build shell does
/// not have, so the only way to look at this UI without a display is to rasterise
/// it from inside the app.
///
/// **What these images are good for:** layout, spacing, typography, colour, card
/// and chip drawing, custom borders, and anything SwiftUI renders itself. They are
/// real evidence that a screen is not clipped, misaligned or empty.
///
/// **What they cannot show:** `List` and `TextField` are AppKit-backed
/// (NSTableView / the field editor) and do not draw through `cacheDisplay` in a
/// window that was never on screen, so list rows and field contents come out blank.
/// `control-list.png` is a two-row `List` kept as a control — it renders empty too,
/// which is how we know an empty sidebar here says nothing about the app. Button
/// bezels and materials are unreliable for the same reason.
///
/// Enable with `VOXDEMO_SNAPSHOT=<dir>`; the app writes PNGs there and exits.
@MainActor
enum Snapshots {

    static func runIfRequested() {
        if ProcessInfo.processInfo.environment["VOXDEMO_PROBE"] != nil {
            probe()
            exit(0)
        }
        guard let dir = ProcessInfo.processInfo.environment["VOXDEMO_SNAPSHOT"] else { return }
        run(into: URL(fileURLWithPath: dir))
        exit(0)
    }

    /// Drives the three Settings buttons through the app's own `Engine` and `API`,
    /// so a failure shows up here with its real message instead of as a dialog
    /// nobody can read. `VOXDEMO_PROBE=1`.
    static func probe() {
        let sem = DispatchSemaphore(value: 0)
        Task { @MainActor in
            let engine = Engine()
            print("[probe] repo     = \(engine.repoPath)")
            print("[probe] installed= \(engine.installed)")
            engine.start()
            print("[probe] port     = \(API.port)")
            if let e = engine.lastError { print("[probe] start err= \(e)") }

            var health: Health?
            let deadline = Date().addingTimeInterval(120)
            while Date() < deadline {
                if let h: Health = try? await API.get("/health") {
                    health = h
                    if h.ready || !h.error.isEmpty { break }
                }
                try? await Task.sleep(for: .milliseconds(400))
            }
            if let h = health {
                print("[probe] health   = ready=\(h.ready) loading=\(h.loading) "
                      + "error=\(h.error.isEmpty ? "-" : h.error)")
            } else {
                print("[probe] health   = NO REPLY")
            }

            await engine.loadSettings()
            let cfg = engine.settings?.provider ?? ProviderConfig()
            print("[probe] settings = preset=\(cfg.preset) url=\(cfg.base_url) "
                  + "key_set=\(cfg.api_key_set) model=\(cfg.model)")

            var draft = cfg
            draft.api_key = ""      // exactly what the form sends after a save
            let body = (try? JSONSerialization.data(withJSONObject: draft.asJSON()))
                ?? Data()

            do {
                let r: ModelList = try await API.postRaw("/providers/models",
                                                         rawBody: body, timeout: 60)
                print("[probe] models   = ok=\(r.ok) n=\(r.models.count) err=\(r.error)")
            } catch { print("[probe] models   = THREW \(error.localizedDescription)") }

            do {
                let r: TestResult = try await API.postRaw("/providers/test",
                                                          rawBody: body, timeout: 240)
                print("[probe] test     = ok=\(r.ok) reply=\(r.reply) "
                      + "latency=\(r.latency_ms) err=\(r.error)")
            } catch { print("[probe] test     = THREW \(error.localizedDescription)") }

            do {
                let r: DetectResult = try await API.get("/providers/detect", timeout: 180)
                print("[probe] detect   = found=\(r.found.count)")
            } catch { print("[probe] detect   = THREW \(error.localizedDescription)") }

            print("[probe] engine log: \(engine.engineLogTail(6))")
            engine.stopAndWait()
            sem.signal()
        }
        // Pump the run loop while the task above runs.
        while sem.wait(timeout: .now()) == .timedOut {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
    }

    static func run(into out: URL) {
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        // Both layers have to agree on the appearance. Forcing only SwiftUI's
        // colorScheme leaves AppKit light, and then text fields come out as white
        // boxes with white text in them.
        NSApp.appearance = NSAppearance(named: .darkAqua)
        let engine = sampleEngine()
        let store = sampleStore()

        // One shell shot per destination, at the app's default window size.
        let shell = CGSize(width: 1240, height: 820)
        for dest in Destination.allCases {
            shoot("shell-\(dest.rawValue)", size: shell, engine: engine, store: store,
                  settle: dest == .library ? 9 : 2.2) {
                SnapshotShell(destination: dest)
            }
        }

        // Same screen, but with the engine still booting. The three provider buttons
        // should be inert and a "Waiting for the engine…" caption visible — proving
        // the gate works, since the only way to see it is for engine.health to be nil.
        // A *fresh* sample engine, not `var deadEngine = engine`: Engine is a class,
        // and setting `deadEngine.health = nil` on a copy of the reference would
        // mutate the shared instance and poison the step-* shots that follow.
        let deadEngine = sampleEngine()
        deadEngine.health = nil
        shoot("shell-settings-no-engine", size: shell, engine: deadEngine, store: store,
              settle: 2.2) {
            SnapshotShell(destination: .settings)
        }

        // Every step of the create flow, so each one gets looked at.
        for step in CreateStep.allCases {
            store.step = step
            if step == .render {
                store.busy = false
                store.progress = 1
                store.stage = "done"
                store.result = DemoResult(
                    path: "/Users/prat14k/Movies/VoxDemo/voxcpm-20260918-162226/demo.mp4",
                    project: "/Users/prat14k/Movies/VoxDemo/voxcpm-20260918-162226",
                    scenes: 5, duration: 60.0, voice: "Aria", title: "VoxCPM",
                    theme: "midnight", aspect: "landscape", has_hook: true, has_close: true)
            }
            if step == .source {
                store.analysing = true
                store.analyseProgress = 0.42
                store.analyseStage = "sending 27k characters to Qwen3.8-9B-mlx-4Bit"
            }
            shoot("step-\(step.rawValue)", size: shell, engine: engine, store: store) {
                SnapshotShell(destination: .create)
            }
        }
        store.analysing = false
        store.step = .script

        // A control: if a bare List does not draw here either, then an empty sidebar
        // in these images says nothing about the app — it is the capture layer.
        shoot("control-list", size: CGSize(width: 420, height: 240),
              engine: engine, store: store) { ControlList() }

        shoot("sheet-welcome", size: CGSize(width: 620, height: 560),
              engine: engine, store: store) { WelcomeSheet(done: {}) }

        print("[snapshots] wrote to \(out.path)")
    }

    private struct ControlList: View {
        var body: some View {
            List {
                Section("Destinations") {
                    Label("Create", systemImage: "wand.and.stars")
                    Label("Library", systemImage: "rectangle.stack")
                }
            }
            .listStyle(.sidebar)
        }
    }

    // MARK: - The window shell

    /// `NavigationSplitView` does not rasterise through `cacheDisplay`, so the
    /// shell is rebuilt as the equivalent HStack. Same sidebar, same detail.
    private struct SnapshotShell: View {
        let destination: Destination

        var body: some View {
            HStack(spacing: 0) {
                Sidebar(destination: .constant(destination))
                    .frame(width: 226)
                Divider()
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
            .background(Color(nsColor: .windowBackgroundColor))
        }
    }

    // MARK: - Capture

    private static func shoot<V: View>(_ name: String, size: CGSize,
                                       engine: Engine, store: CreateStore,
                                       settle: TimeInterval = 1.6,
                                       @ViewBuilder _ view: () -> V) {
        let host = NSHostingView(rootView: view()
            .environment(engine)
            .environment(store)
            .frame(width: size.width, height: size.height))

        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        window.appearance = NSAppearance(named: .darkAqua)

        // `List` and `TextField` are AppKit-backed, and AppKit only draws them once
        // the window has actually been put on screen — capturing an offscreen window
        // yields an empty list and unpopulated fields. So show it briefly, behind
        // everything, and give it a real display cycle before grabbing the pixels.
        window.orderBack(nil)
        NSApp.activate(ignoringOtherApps: false)
        // The library pulls poster frames out of six videos, so it needs longer.
        let deadline = Date().addingTimeInterval(settle)
        while Date() < deadline {
            window.displayIfNeeded()
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.25))
        }

        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
        rep.size = host.bounds.size
        host.cacheDisplay(in: host.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return }
        try? png.write(to: out.appendingPathComponent("\(name).png"))
        window.orderOut(nil)
    }

    private static let out = URL(fileURLWithPath:
        ProcessInfo.processInfo.environment["VOXDEMO_SNAPSHOT"] ?? "/tmp")

    // MARK: - Sample state

    private static func sampleEngine() -> Engine {
        let e = Engine()
        e.repoPath = "/Users/prat14k/Office/VoxCPM"
        e.health = Health(
            ready: true, loading: false, error: "", warm: "", dry_run: false,
            model: "openbmb/VoxCPM2", device: "mps", sample_rate: 48000,
            output_dir: "/Users/prat14k/Movies/VoxDemo",
            themes: ["midnight", "ember", "neon", "studio"],
            aspects: ["landscape", "portrait", "square"],
            theme_labels: ["midnight": "Midnight", "ember": "Ember",
                           "neon": "Neon", "studio": "Studio"],
            theme_palettes: [
                "midnight": ThemePalette(bg: "#07080d", bg2: "#141a2e", accent: "#7c93ff",
                                         accent2: "#39d3c0", text: "#f2f4ff", dark: true),
                "ember": ThemePalette(bg: "#120806", bg2: "#2a1109", accent: "#ff8a3d",
                                      accent2: "#ffd166", text: "#fff4ec", dark: true),
                "neon": ThemePalette(bg: "#06060f", bg2: "#171033", accent: "#b16cff",
                                     accent2: "#3ddcff", text: "#f4f0ff", dark: true),
                "studio": ThemePalette(bg: "#f7f8fc", bg2: "#e8ecf7", accent: "#3b5bdb",
                                       accent2: "#0ca678", text: "#12141c", dark: false),
            ],
            visual_kinds: ["screenshot", "code", "tree", "stats", "stack",
                           "terminal", "diagram", "mesh"],
            claude_available: true)

        e.voices = [
            Voice(id: "aria", name: "Aria", kind: "preset",
                  description: "Warm, neutral, reads technical copy well", enrolled: true),
            Voice(id: "atlas", name: "Atlas", kind: "preset",
                  description: "Lower register, slower cadence", enrolled: true),
            Voice(id: "juno", name: "Juno", kind: "preset",
                  description: "Bright and quick, good for short demos", enrolled: true),
            Voice(id: "my-voice", name: "My voice", kind: "clone",
                  description: "Recorded 2026-09-18 · 14.2s", enrolled: true),
        ]

        e.settings = SettingsPayload(
            provider: ProviderConfig(
                kind: "openai", preset: "omlx", base_url: "http://127.0.0.1:8000/v1",
                api_key: "", model: "Qwen3.8-9B-mlx-4Bit", temperature: 0.4,
                max_tokens: 6000, timeout: 300, digest_budget: 48000,
                claude_model: "sonnet", api_key_set: true),
            defaults: DefaultsConfig(theme: "midnight", aspect: "landscape",
                                     voice_id: "aria", sfx: true, scenes: 6,
                                     angle: "developers who want to keep everything local"),
            presets: [
                "omlx": PresetInfo(label: "oMLX", base_url: "http://127.0.0.1:8000/v1",
                                   local: true, key_required: false,
                                   note: "Apple-silicon MLX server"),
                "ollama": PresetInfo(label: "Ollama", base_url: "http://127.0.0.1:11434/v1",
                                     local: true, key_required: false, note: "Local runner"),
                "lmstudio": PresetInfo(label: "LM Studio",
                                       base_url: "http://127.0.0.1:1234/v1",
                                       local: true, key_required: false, note: "Local runner"),
                "openai": PresetInfo(label: "OpenAI", base_url: "https://api.openai.com/v1",
                                     local: false, key_required: true, note: "Hosted"),
            ],
            claude_available: true,
            output_dir: "/Users/prat14k/Movies/VoxDemo")
        return e
    }

    private static func sampleStore() -> CreateStore {
        let s = CreateStore()
        s.repoPath = "/Users/prat14k/Office/VoxCPM"
        s.angle = "developers who want to keep everything local"
        s.sceneCount = 6
        s.analysing = false
        s.analyseProgress = 1
        s.analyseStage = "done"
        s.providerLabel = "oMLX · Qwen3.8-9B-mlx-4Bit"
        s.apply(AnalyzeResult(
            title: "VoxCPM",
            subtitle: "local developers",
            hook: "You're tired of uploading your code to a stranger's server.",
            logo: "/Users/prat14k/Office/VoxCPM/mac/VoxDemo.icns",
            scenes: [
                AnalyzeScene(heading: "The Upload Trap", role: "Problem",
                             media: "", text: "Every demo tool wants your repo on their "
                             + "machine first. That is a hard sell when the code is the "
                             + "product.",
                             visual: "tree", visual_ref: "",
                             visual_note: "what gets uploaded"),
                AnalyzeScene(heading: "Everything Stays Local", role: "Solution",
                             media: "", text: "Point it at a folder on your own disk. It "
                             + "reads the code there, writes the script there, and renders "
                             + "the video there.",
                             visual: "terminal", visual_ref: "mac/build.sh",
                             visual_note: "three commands, no account"),
                AnalyzeScene(heading: "Local AI, Real Voices", role: "Features",
                             media: "", text: "Script writing runs on a local model. "
                             + "Narration comes from a short mic recording of you.",
                             visual: "stack", visual_ref: "",
                             visual_note: "what it depends on"),
                AnalyzeScene(heading: "The Script Writes Itself", role: "Features",
                             media: "", text: "It ranks your files, reads the interesting "
                             + "ones, and turns them into beats with a visual for each.",
                             visual: "code", visual_ref: "providers.py",
                             visual_note: "the digest it reads"),
                AnalyzeScene(heading: "Render, Edit, Share", role: "Features",
                             media: "", text: "The video renders to an HTML composition "
                             + "you can keep editing by hand.",
                             visual: "diagram", visual_ref: "",
                             visual_note: "repo to video"),
            ],
            close: CloseBeat(heading: "Start creating",
                             text: "It runs on your machine, and you own every frame.",
                             cta: "github.com/openbmb/voxcpm"),
            provider: "oMLX · Qwen3.8-9B-mlx-4Bit", cost_usd: 0))
        s.close.enabled = true
        s.logo = ""
        return s
    }
}
