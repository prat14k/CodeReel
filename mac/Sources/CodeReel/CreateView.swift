import AVKit
import SwiftUI
import UniformTypeIdentifiers

// MARK: - Shell

struct CreateView: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store

    var body: some View {
        @Bindable var store = store
        VStack(spacing: 0) {
            StepBar(step: $store.step, canJumpToScript: store.hasScript)
            Divider()
            Group {
                switch store.step {
                case .source: SourceStep()
                case .script: ScriptStep()
                case .look: LookStep()
                case .render: RenderStep()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            Footer(step: $store.step)
        }
    }
}

private struct Footer: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store
    @Binding var step: CreateStep

    var body: some View {
        HStack(spacing: 12) {
            if step != .source {
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        step = CreateStep(rawValue: step.rawValue - 1) ?? .source
                    }
                } label: { Label("Back", systemImage: "chevron.left") }
            }
            Spacer()
            switch step {
            case .source:
                Text("Point CodeReel at a repository, or skip straight to the script.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Skip to script") {
                    if store.scenes.isEmpty { store.scenes = [SceneDraft()] }
                    withAnimation { step = .script }
                }
            case .script:
                Text("\(store.scenes.filter(\.hasNarration).count) scene(s) · about \(Int(store.narrationSeconds))s of narration")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Next: look") { withAnimation { step = .look } }
                    .disabled(!store.hasScript)
                    .keyboardShortcut(.defaultAction)
            case .look:
                Text("Theme and voice apply to the whole video.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Next: render") { withAnimation { step = .render } }
                    .keyboardShortcut(.defaultAction)
            case .render:
                EmptyView()
            }
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 11)
    }
}

// MARK: - 1. Source

private struct SourceStep: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store

    @State private var pickingRepo = false
    @State private var detecting = false
    @State private var detected: [DetectedServer] = []
    @State private var detectError = ""

    var body: some View {
        @Bindable var store = store
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                repoPicker
                providerRow
                analyseCard
            }
            .padding(22)
            .frame(maxWidth: 860)
            .frame(maxWidth: .infinity)
        }
        .fileImporter(isPresented: $pickingRepo, allowedContentTypes: [.folder]) { res in
            if case .success(let url) = res { store.repoPath = url.path }
        }
        .task {
            if store.repoPath.isEmpty { store.repoPath = engine.repoPath }
        }
    }

    private var repoPicker: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                SectionTitle(title: "Repository",
                             subtitle: "CodeReel reads it to write the script and to pull real visuals.")

                HStack(spacing: 10) {
                    Image(systemName: "folder")
                        .foregroundStyle(store.repoPath.isEmpty ? .secondary : Palette.accent)
                    TextField("Path to a local repository", text: Binding(
                        get: { store.repoPath },
                        set: { store.repoPath = $0 }))
                        .textFieldStyle(.plain)
                        .font(.system(.callout, design: .monospaced))
                    Button("Choose…") { pickingRepo = true }
                }
                .padding(11)
                .background(Color.primary.opacity(0.045),
                            in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(Color.primary.opacity(0.10), lineWidth: 1)
                )

                if !store.repoPath.isEmpty {
                    HStack(spacing: 8) {
                        let exists = FileManager.default.fileExists(atPath: store.repoPath)
                        Image(systemName: exists ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(exists ? Palette.good : .orange)
                            .font(.caption)
                        Text(exists ? "Found" : "That folder doesn't exist")
                            .font(.caption).foregroundStyle(.secondary)
                        Text((store.repoPath as NSString).lastPathComponent)
                            .font(.caption.weight(.medium))
                        Spacer()
                    }
                }
            }
        }
    }

    private var providerRow: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Script writer",
                             subtitle: "Claude Code, or any OpenAI-compatible model — local or hosted.")

                let p = engine.settings?.provider
                HStack(spacing: 11) {
                    Image(systemName: p?.kind == "claude" ? "sparkles" : "cpu")
                        .foregroundStyle(Palette.accent)
                        .frame(width: 20)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(engine.settings?.presets[p?.preset ?? ""]?.label ?? "Not configured")
                            .font(.callout.weight(.medium))
                        Text(p?.model.isEmpty == false ? (p?.model ?? "") : "No model selected")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer()
                    if let note = engine.settings?.presets[p?.preset ?? ""]?.note {
                        Text(note).font(.caption2).foregroundStyle(.tertiary)
                            .frame(maxWidth: 300, alignment: .trailing)
                            .lineLimit(2)
                    }
                }

                HStack(spacing: 8) {
                    Button {
                        Task { await engine.loadSettings() }
                    } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                        .controlSize(.small)

                    Button {
                        detect()
                    } label: {
                        if detecting { ProgressView().controlSize(.small) }
                        else { Label("Detect local servers", systemImage: "antenna.radiowaves.left.and.right") }
                    }
                    .controlSize(.small)
                    .disabled(detecting)

                    Spacer()
                    Text("Change it in Settings →")
                        .font(.caption2).foregroundStyle(.tertiary)
                }

                if !detectError.isEmpty {
                    Text(detectError)
                        .font(.caption2).foregroundStyle(.red).lineLimit(2)
                }

                ForEach(detected) { d in
                    HStack(spacing: 8) {
                        Image(systemName: d.ok ? "checkmark.circle.fill" : "xmark.circle")
                            .foregroundStyle(d.ok ? Palette.good : .secondary)
                            .font(.caption)
                        Text("\(d.hint) · port \(d.port)")
                            .font(.caption.weight(.medium))
                        if d.ok {
                            Text("\(d.models.count) models").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button("Use this") { use(d) }.controlSize(.small)
                        } else {
                            Text(d.error).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                            Spacer()
                        }
                    }
                    .padding(8)
                    .background(Color.primary.opacity(0.035),
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
            }
        }
    }

    private var analyseCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                SectionTitle(title: "Draft the script",
                             subtitle: "The beats come back editable — nothing renders until you say so.")

                VStack(alignment: .leading, spacing: 6) {
                    FieldLabel(text: "Angle", hint: "optional — who you're pitching to")
                    TextField("e.g. developers evaluating local AI tooling", text: Binding(
                        get: { store.angle }, set: { store.angle = $0 }))
                        .textFieldStyle(.roundedBorder)
                }

                HStack(spacing: 18) {
                    Stepper("Target \(store.sceneCount) scenes",
                            value: Binding(get: { store.sceneCount },
                                           set: { store.sceneCount = $0 }), in: 3...12)
                        .fixedSize()

                    Button {
                        analyse()
                    } label: {
                        HStack(spacing: 8) {
                            if store.analysing { ProgressView().controlSize(.small) }
                            else { Image(systemName: "sparkles.rectangle.stack") }
                            Text(store.analysing ? "Analysing…" : "Analyse repo")
                        }
                        .frame(minWidth: 150)
                    }
                    .controlSize(.large)
                    .buttonStyle(.borderedProminent)
                    .disabled(!store.canAnalyse || engine.health?.ready != true
                              && engine.settings == nil)

                    if let cost = store.analyseCost {
                        Text(String(format: "drafted · $%.2f", cost))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                if store.analysing {
                    VStack(alignment: .leading, spacing: 6) {
                        ProgressView(value: store.analyseProgress)
                        Text(store.analyseStage)
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }

                Divider()
                Notice(icon: "lock.shield",
                       title: "Read-only by construction",
                       detail: "Claude Code runs with only Read, Grep and Glob — anything that would "
                             + "prompt is denied. Local models get a digest built in Python; nothing "
                             + "is written back to your repo either way.")
            }
        }
    }

    @MainActor
    private func analyse() {
        store.analysing = true
        store.analyseStage = "starting"
        store.analyseCost = nil
        store.analyseProgress = 0
        Task {
            defer { store.analysing = false }
            do {
                let r: AnalyzeResult = try await API.runRaw("/analyze", [
                    "repo_path": store.repoPath,
                    "scenes": store.sceneCount,
                    "angle": store.angle,
                ]) { p, msg in
                    store.analyseProgress = p
                    store.analyseStage = msg
                }
                store.apply(r)
                withAnimation(.easeInOut(duration: 0.2)) { store.step = .script }
            } catch {
                engine.lastError = error.localizedDescription
            }
        }
    }

    @MainActor
    private func detect() {
        detecting = true
        detectError = ""
        Task {
            defer { detecting = false }
            do {
                let r: DetectResult = try await API.get("/providers/detect")
                detected = r.found
            } catch { detectError = error.localizedDescription }
        }
    }

    @MainActor
    private func use(_ server: DetectedServer) {
        guard var p = engine.settings?.provider else { return }
        p.kind = "openai"
        p.preset = server.port == 8000 ? "omlx"
            : server.port == 11434 ? "ollama"
            : server.port == 1234 ? "lmstudio"
            : server.port == 8080 ? "llamacpp"
            : server.port == 8001 ? "vllm" : "custom"
        p.base_url = server.base_url
        p.api_key = server.api_key
        p.model = server.models.first ?? p.model
        Task {
            if await engine.saveSettings(provider: p) {
                detected = []
                await engine.loadSettings()
            }
        }
    }
}

// MARK: - 3. Look

private struct LookStep: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store

    @State private var showAdvanced = false
    @State private var previewing = false
    @State private var previewURL: URL?

    private var themes: [String] { engine.health?.themes ?? ["midnight"] }

    var body: some View {
        @Bindable var store = store
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                themeCard
                HStack(alignment: .top, spacing: 16) {
                    frameCard
                    voiceCard
                }
                advancedCard
            }
            .padding(22)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity)
        }
    }

    private var themeCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                SectionTitle(title: "Look", subtitle: "Sets the background, panels, code colours and captions.")
                HStack(spacing: 12) {
                    ForEach(themes, id: \.self) { key in
                        let p = engine.health?.theme_palettes[key]
                        SelectCard(selected: store.theme == key) { store.theme = key } content: {
                            VStack(alignment: .leading, spacing: 9) {
                                ZStack(alignment: .bottomLeading) {
                                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                                        .fill(LinearGradient(
                                            colors: [Color(hex: p?.bg2 ?? "#141a2e"),
                                                     Color(hex: p?.bg ?? "#07080d")],
                                            startPoint: .topLeading, endPoint: .bottomTrailing))
                                        .frame(height: 54)
                                    HStack(spacing: 5) {
                                        Capsule().fill(Color(hex: p?.accent ?? "#7c93ff"))
                                            .frame(width: 22, height: 5)
                                        Circle().fill(Color(hex: p?.accent2 ?? "#39d3c0"))
                                            .frame(width: 5, height: 5)
                                    }
                                    .padding(9)
                                }
                                Text(engine.health?.theme_labels[key] ?? key.capitalized)
                                    .font(.callout.weight(.medium))
                            }
                        }
                    }
                }
            }
        }
    }

    private var frameCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                SectionTitle(title: "Frame")
                HStack(spacing: 12) {
                    ForEach(engine.health?.aspects ?? ["landscape"], id: \.self) { a in
                        SelectCard(selected: store.aspect == a) { store.aspect = a } content: {
                            VStack(spacing: 7) {
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(Palette.accent.opacity(store.aspect == a ? 0.35 : 0.15))
                                    .frame(width: shape(a).width, height: shape(a).height)
                                Text(a.capitalized).font(.caption2)
                            }
                            .frame(maxWidth: .infinity)
                        }
                    }
                }
                Toggle("Transition sounds", isOn: Binding(
                    get: { store.sfx }, set: { store.sfx = $0 }))
                    .font(.callout)
                Text("A soft whoosh on each cut, synthesized locally.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private func shape(_ a: String) -> CGSize {
        switch a {
        case "portrait": return CGSize(width: 26, height: 42)
        case "square": return CGSize(width: 36, height: 36)
        default: return CGSize(width: 52, height: 30)
        }
    }

    private var voiceCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Voice", subtitle: "Every scene speaks in this voice.")
                Picker("", selection: Binding(get: { store.voiceID },
                                              set: { store.voiceID = $0 })) {
                    ForEach(engine.voices) { v in
                        Text(v.isPreset ? v.name : "\(v.name) (cloned)").tag(v.id)
                    }
                }
                .labelsHidden()

                HStack(spacing: 10) {
                    Button {
                        preview()
                    } label: {
                        if previewing { ProgressView().controlSize(.small) }
                        else { Label("Preview", systemImage: "play.circle") }
                    }
                    .disabled(previewing || engine.health?.ready != true)
                    Text("Uses a short sample line.")
                        .font(.caption2).foregroundStyle(.secondary)
                }

                if let previewURL {
                    AudioPlayerBar(url: previewURL, autoplay: true)
                        .frame(height: 34)
                        .clipShape(RoundedRectangle(cornerRadius: 7))
                }

                Text(engine.voices.first { $0.id == store.voiceID }?.description ?? "")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }
    }

    private var advancedCard: some View {
        Card {
            DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
                VStack(alignment: .leading, spacing: 12) {
                    VStack(alignment: .leading, spacing: 5) {
                        FieldLabel(text: "Style direction",
                                   hint: "spoken into the model, e.g. \"cheerful, slightly faster\"")
                        TextField("", text: Binding(get: { store.style }, set: { store.style = $0 }))
                            .textFieldStyle(.roundedBorder)
                    }
                    HStack(spacing: 16) {
                        VStack(alignment: .leading, spacing: 4) {
                            FieldLabel(text: "CFG \(String(format: "%.1f", store.cfg))")
                            Slider(value: Binding(get: { store.cfg }, set: { store.cfg = $0 }),
                                   in: 1...4, step: 0.1).frame(width: 180)
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            FieldLabel(text: "Steps \(Int(store.timesteps))")
                            Slider(value: Binding(get: { store.timesteps },
                                                  set: { store.timesteps = $0 }),
                                   in: 4...40, step: 1).frame(width: 180)
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            FieldLabel(text: "Seed")
                            TextField("", value: Binding(get: { store.seed },
                                                         set: { store.seed = $0 }),
                                      format: .number)
                                .textFieldStyle(.roundedBorder).frame(width: 90)
                        }
                    }
                    Text("Same seed and same script renders identically.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                .padding(.top, 10)
            }
            .font(.callout)
        }
    }

    @MainActor
    private func preview() {
        previewing = true
        Task {
            defer { previewing = false }
            do {
                let r: SpeakResult = try await API.run(
                    "/speak",
                    ["voice_id": store.voiceID,
                     "text": "This is how I sound. Ready whenever you are."] as [String: String],
                    onStep: { _, _ in })
                previewURL = URL(fileURLWithPath: r.path)
                await engine.refreshVoices()
            } catch { engine.lastError = error.localizedDescription }
        }
    }
}

// MARK: - 4. Render

private struct RenderStep: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store

    @State private var player: AVPlayer?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                summary
                generateCard
                if let result = store.result { resultCard(result) }
            }
            .padding(22)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity)
        }
    }

    private var summary: some View {
        Card {
            VStack(alignment: .leading, spacing: 13) {
                SectionTitle(title: "Ready to render", subtitle: "Nothing is generated until you press the button.")
                HStack(spacing: 26) {
                    stat("Scenes", "\(store.scenes.filter(\.hasNarration).count)")
                    stat("Est. length", "~\(Int(store.narrationSeconds + 9))s")
                    stat("Voice", engine.voices.first { $0.id == store.voiceID }?.name ?? store.voiceID)
                    stat("Theme", engine.health?.theme_labels[store.theme] ?? store.theme.capitalized)
                    stat("Frame", store.aspect.capitalized)
                    Spacer()
                }
                if store.hook.isEmpty && !store.close.enabled {
                    Notice(icon: "info.circle",
                           title: "No hook or close beat",
                           detail: "Adding an opening line and a closing call to action makes the "
                                 + "video land much harder. You can add them in the Script step.")
                }
            }
        }
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.callout.weight(.semibold))
        }
    }

    private var generateCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 13) {
                HStack(spacing: 12) {
                    Button {
                        generate()
                    } label: {
                        HStack(spacing: 8) {
                            if store.busy { ProgressView().controlSize(.small) }
                            else { Image(systemName: "wand.and.stars") }
                            Text(store.busy ? "Rendering…" : "Generate demo")
                        }
                        .frame(minWidth: 170)
                    }
                    .controlSize(.large)
                    .buttonStyle(.borderedProminent)
                    .disabled(!store.canRender || engine.health?.ready != true)

                    if !store.busy && engine.health?.ready != true {
                        Text("Waiting for the engine to finish loading.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                if store.busy {
                    VStack(alignment: .leading, spacing: 6) {
                        ProgressView(value: store.progress)
                        Text(store.stage).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
            }
        }
    }

    private func resultCard(_ r: DemoResult) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Image(systemName: "checkmark.seal.fill").foregroundStyle(Palette.good)
                    Text("Rendered").font(.headline)
                    Spacer()
                    Text("\(r.scenes) scenes · \(String(format: "%.1f", r.duration))s · \(r.voice)")
                        .font(.caption).foregroundStyle(.secondary)
                }

                if let player {
                    VideoPlayer(player: player)
                        .aspectRatio(store.aspect == "portrait" ? 9.0 / 16
                                     : store.aspect == "square" ? 1 : 16.0 / 9,
                                     contentMode: .fit)
                        .frame(maxWidth: 620)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                Text(r.path)
                    .font(.caption).foregroundStyle(.secondary)
                    .textSelection(.enabled).lineLimit(2)

                HStack(spacing: 9) {
                    Button {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: r.path)])
                    } label: { Label("Show in Finder", systemImage: "folder") }

                    Button {
                        NSWorkspace.shared.open(URL(fileURLWithPath: r.project))
                    } label: { Label("Open project", systemImage: "hammer") }

                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(r.path, forType: .string)
                    } label: { Label("Copy path", systemImage: "doc.on.doc") }

                    Spacer()
                    Button {
                        player = nil
                        store.result = nil
                        withAnimation { store.step = .script }
                    } label: { Label("Keep editing", systemImage: "pencil") }
                }
                .controlSize(.small)
            }
        }
    }

    @MainActor
    private func generate() {
        store.busy = true
        store.progress = 0
        store.stage = "starting"
        store.result = nil
        player = nil
        Task {
            defer { store.busy = false }
            do {
                let r: DemoResult = try await API.runRaw("/demos", store.request()) { p, msg in
                    store.progress = p
                    store.stage = msg
                }
                store.result = r
                player = AVPlayer(url: URL(fileURLWithPath: r.path))
                player?.play()
            } catch { engine.lastError = error.localizedDescription }
        }
    }
}
