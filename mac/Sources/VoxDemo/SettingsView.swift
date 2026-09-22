import SwiftUI

// MARK: - Settings

struct SettingsView: View {
    @Environment(Engine.self) private var engine

    @State private var draft = ProviderConfig()
    @State private var defaults = DefaultsConfig()
    @State private var loaded = false

    @State private var models: [String] = []
    @State private var loadingModels = false
    @State private var testing = false
    @State private var test: TestResult?
    @State private var detected: [DetectedServer] = []
    @State private var detecting = false
    @State private var savedFlash = false
    @State private var apiKeyInput = ""
    /// Any provider call that failed belongs next to the buttons that made it, not
    /// in a global modal that could have come from anywhere. Seeing which URL and
    /// key were actually sent is usually the whole answer to "I did set it
    /// correctly". All three controls share it — they fail for the same reason.
    @State private var providerError = ""

    private var presets: [(key: String, info: PresetInfo)] {
        (engine.settings?.presets ?? [:])
            .map { (key: $0.key, info: $0.value) }
            .sorted { $0.info.label < $1.info.label }
    }

    /// The provider controls need both the sidecar and the loaded settings.
    /// `watch()` sets `health` *before* calling `loadSettings()`, so for a brief
    /// window after engine start the buttons are enabled but `draft` is still
    /// the default config — `base_url` is "". Without this gate a Load models
    /// tap in that window sends "Sent to  with no key at all" and the inline
    /// error correctly names the real cause: the form isn't ready yet.
    private var formReady: Bool {
        engine.health != nil && engine.settings != nil
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                providerCard
                if draft.kind == "openai" { modelCard } else { claudeCard }
                engineCard
                defaultsCard
            }
            .padding(22)
            .frame(maxWidth: 860)
            .frame(maxWidth: .infinity)
        }
        .task { await sync() }
        .onChange(of: engine.settings?.provider) { _, _ in Task { await sync() } }
    }

    @MainActor
    private func sync() async {
        guard let s = engine.settings else { return }
        if !loaded {
            draft = s.provider
            defaults = s.defaults
            loaded = true
        }
    }

    // MARK: writer

    private var providerCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                SectionTitle(title: "Script writer",
                             subtitle: "Where the narration script is drafted. Everything else stays on this machine.")

                HStack(spacing: 9) {
                    SelectCard(selected: draft.kind == "claude") {
                        draft.kind = "claude"
                    } content: {
                        HStack(spacing: 9) {
                            Image(systemName: "sparkles").foregroundStyle(Palette.accent)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("Claude Code").font(.callout.weight(.medium))
                                Text(engine.settings?.claude_available == true
                                     ? "Reads the repo with its own file tools"
                                     : "Not installed on this machine")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    SelectCard(selected: draft.kind == "openai") {
                        draft.kind = "openai"
                    } content: {
                        HStack(spacing: 9) {
                            Image(systemName: "cpu").foregroundStyle(Palette.accent)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("OpenAI-compatible").font(.callout.weight(.medium))
                                Text("Any /chat/completions endpoint — local or hosted")
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                if draft.kind == "openai" {
                    Divider()
                    VStack(alignment: .leading, spacing: 9) {
                        FieldLabel(text: "Provider")
                        FlowRow(spacing: 7) {
                            ForEach(presets, id: \.key) { p in
                                Button {
                                    draft.preset = p.key
                                    draft.base_url = p.info.base_url
                                    if p.info.key_required && draft.api_key.isEmpty {
                                        draft.api_key = defaultKey(p.key)
                                    }
                                    models = []
                                    test = nil
                                    providerError = ""    // new attempt, no stale red
                                } label: {
                                    Text(p.info.label)
                                        .font(.caption)
                                        .padding(.horizontal, 10).padding(.vertical, 5)
                                        .background(draft.preset == p.key ? Palette.accentSoft
                                                    : Color.primary.opacity(0.05), in: Capsule())
                                        .overlay(Capsule().strokeBorder(
                                            draft.preset == p.key ? Palette.accent
                                            : Color.primary.opacity(0.12), lineWidth: 1))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        if let note = engine.settings?.presets[draft.preset]?.note {
                            Text(note).font(.caption2).foregroundStyle(.secondary)
                        }
                    }

                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 5) {
                            FieldLabel(text: "Base URL", hint: "…/v1")
                            TextField("http://127.0.0.1:8000/v1", text: $draft.base_url)
                                .textFieldStyle(.roundedBorder)
                                .font(.system(.caption, design: .monospaced))
                        }
                        VStack(alignment: .leading, spacing: 5) {
                            FieldLabel(text: "API key",
                                       hint: draft.api_key_set ? "stored — type to replace" : "optional")
                            SecureField(draft.api_key_set ? "••••••••" : "not set", text: $apiKeyInput)
                                .textFieldStyle(.roundedBorder)
                                .onChange(of: apiKeyInput) { _, v in draft.api_key = v }
                        }
                        .frame(width: 220)
                    }

                    // Every one of these needs the sidecar. Firing before it answers
                    // is a connection-refused that reads to the user as "your API key
                    // is wrong" — so they stay inert until the engine says hello.
                    HStack(spacing: 9) {
                        Button {
                            Task { await loadModels() }
                        } label: {
                            if loadingModels { ProgressView().controlSize(.small) }
                            else { Label("Load models", systemImage: "arrow.clockwise") }
                        }
                        .controlSize(.small).disabled(loadingModels || !formReady)

                        Button {
                            Task { await runTest() }
                        } label: {
                            if testing { ProgressView().controlSize(.small) }
                            else { Label("Test connection", systemImage: "bolt") }
                        }
                        .controlSize(.small).disabled(testing || !formReady)

                        Button {
                            Task { await detect() }
                        } label: {
                            if detecting { ProgressView().controlSize(.small) }
                            else { Label("Find local servers", systemImage: "antenna.radiowaves.left.and.right") }
                        }
                        .controlSize(.small).disabled(detecting || !formReady)
                        Spacer()
                    }

                    if !formReady {
                        Text("Waiting for the engine — these need it running.")
                            .font(.caption2).foregroundStyle(.secondary)
                    }

                    if !providerError.isEmpty {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
                            Text(providerError).font(.caption2).foregroundStyle(.red)
                            Spacer()
                        }
                        .padding(9)
                        .background(Color.red.opacity(0.07),
                                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }

                    if let test {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: test.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                                .foregroundStyle(test.ok ? Palette.good : .red)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(test.ok ? "Connected in \(test.latency_ms) ms" : "Failed")
                                    .font(.caption.weight(.medium))
                                if !test.reply.isEmpty {
                                    Text(test.reply).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                                }
                                if !test.error.isEmpty {
                                    Text(test.error).font(.caption2).foregroundStyle(.red).lineLimit(3)
                                }
                            }
                            Spacer()
                        }
                        .padding(9)
                        .background(Color.primary.opacity(0.04),
                                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }

                    ForEach(detected) { d in
                        HStack(spacing: 8) {
                            Image(systemName: d.ok ? "checkmark.circle.fill" : "xmark.circle")
                                .foregroundStyle(d.ok ? Palette.good : .secondary).font(.caption)
                            Text("\(d.hint) · port \(d.port)").font(.caption.weight(.medium))
                            if d.ok {
                                Text("\(d.models.count) models").font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                Button("Use") { apply(d) }.controlSize(.small)
                            } else {
                                Spacer()
                            }
                        }
                        .padding(7)
                        .background(Color.primary.opacity(0.035),
                                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }
                }

                Divider()
                HStack {
                    if savedFlash {
                        Label("Saved", systemImage: "checkmark.circle.fill")
                            .font(.caption).foregroundStyle(Palette.good)
                    }
                    Spacer()
                    Button("Save") { Task { await save() } }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
    }

    private func defaultKey(_ preset: String) -> String {
        ["omlx": "0000", "ollama": "ollama", "lmstudio": "lm-studio"][preset] ?? ""
    }

    // MARK: model

    private var modelCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 13) {
                SectionTitle(title: "Model", subtitle: "A bigger model writes a noticeably better script.")

                HStack(spacing: 9) {
                    if models.isEmpty {
                        TextField("model id", text: $draft.model)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(.caption, design: .monospaced))
                    } else {
                        Picker("", selection: $draft.model) {
                            Text("— choose —").tag("")
                            ForEach(models, id: \.self) { Text($0).tag($0) }
                        }
                        .labelsHidden()
                    }
                }

                HStack(spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        FieldLabel(text: "Temperature \(String(format: "%.2f", draft.temperature))")
                        Slider(value: $draft.temperature, in: 0...1.2, step: 0.05)
                            .frame(width: 170)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        FieldLabel(text: "Max tokens")
                        TextField("", value: $draft.max_tokens, format: .number)
                            .textFieldStyle(.roundedBorder).frame(width: 90)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        FieldLabel(text: "Repo digest", hint: "characters sent to the model")
                        TextField("", value: $draft.digest_budget, format: .number)
                            .textFieldStyle(.roundedBorder).frame(width: 110)
                    }
                    Spacer()
                }

                Notice(icon: "info.circle",
                       title: "Local models have no file tools",
                       detail: "VoxDemo walks your repo in Python, ranks the files and sends a digest. "
                             + "48k characters fits comfortably in a 32k-token context. Raise it if your "
                             + "model has a bigger window.")
            }
        }
    }

    private var claudeCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 13) {
                SectionTitle(title: "Claude Code", subtitle: "Runs the `claude` CLI already on this machine.")
                HStack(spacing: 16) {
                    VStack(alignment: .leading, spacing: 5) {
                        FieldLabel(text: "Model")
                        TextField("sonnet", text: $draft.claude_model)
                            .textFieldStyle(.roundedBorder).frame(width: 160)
                    }
                    Spacer()
                }
                Notice(icon: engine.settings?.claude_available == true
                       ? "checkmark.circle.fill" : "exclamationmark.triangle.fill",
                       title: engine.settings?.claude_available == true
                       ? "Found on PATH" : "Not found",
                       detail: "Runs read-only: only Read, Grep and Glob are allowed, and anything "
                             + "that would prompt for permission is denied instead.",
                       tint: engine.settings?.claude_available == true ? Palette.good : .orange)
            }
        }
    }

    // MARK: engine + defaults

    private var engineCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Engine")
                Grid(alignment: .leading, horizontalSpacing: 26, verticalSpacing: 7) {
                    row("Model", engine.health?.model ?? "—")
                    row("Device", engine.health?.device ?? "—")
                    row("Sample rate", engine.health.map { "\($0.sample_rate) Hz" } ?? "—")
                    row("Output", engine.health?.output_dir ?? "—")
                    row("Repo", engine.repoPath)
                }
                HStack(spacing: 9) {
                    Button("Restart engine") { engine.restart() }.controlSize(.small)
                    Button("Reveal output folder") {
                        if let p = engine.health?.output_dir {
                            NSWorkspace.shared.open(URL(fileURLWithPath: p))
                        }
                    }
                    .controlSize(.small)
                    .disabled(engine.health?.output_dir.isEmpty ?? true)
                }
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).font(.caption).foregroundStyle(.secondary).gridColumnAlignment(.leading)
            Text(value).font(.system(.caption, design: .monospaced))
                .textSelection(.enabled).lineLimit(1).truncationMode(.middle)
        }
    }

    private var defaultsCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 13) {
                SectionTitle(title: "New project defaults")
                HStack(spacing: 16) {
                    VStack(alignment: .leading, spacing: 5) {
                        FieldLabel(text: "Look")
                        Picker("", selection: $defaults.theme) {
                            ForEach(engine.health?.themes ?? ["midnight"], id: \.self) {
                                Text(engine.health?.theme_labels[$0] ?? $0.capitalized).tag($0)
                            }
                        }.labelsHidden().frame(width: 150)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        FieldLabel(text: "Frame")
                        Picker("", selection: $defaults.aspect) {
                            ForEach(engine.health?.aspects ?? ["landscape"], id: \.self) {
                                Text($0.capitalized).tag($0)
                            }
                        }.labelsHidden().frame(width: 140)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        FieldLabel(text: "Scenes")
                        Stepper("\(defaults.scenes)", value: $defaults.scenes, in: 3...12)
                            .fixedSize()
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        FieldLabel(text: "Sounds")
                        Toggle("", isOn: $defaults.sfx).labelsHidden().toggleStyle(.switch)
                    }
                    Spacer()
                }
                HStack {
                    Spacer()
                    Button("Save defaults") {
                        Task { _ = await engine.saveSettings(defaults: defaults); flash() }
                    }
                }
            }
        }
    }

    @MainActor
    private func flash() {
        savedFlash = true
        Task {
            try? await Task.sleep(for: .seconds(2))
            savedFlash = false
        }
    }

    @MainActor
    private func save() async {
        if await engine.saveSettings(provider: draft, defaults: defaults) {
            apiKeyInput = ""
            flash()
            providerError = ""    // committing new settings makes any prior red stale
            await engine.loadSettings()
        }
    }

    @MainActor
    private func loadModels() async {
        loadingModels = true
        providerError = ""
        defer { loadingModels = false }
        do {
            let r: ModelList = try await API.postRaw("/providers/models",
                                                     rawBody: try JSONSerialization.data(
                                                        withJSONObject: draft.asJSON()),
                                                     timeout: 60)
            models = r.models
            if draft.model.isEmpty, let first = r.models.first { draft.model = first }
            if !r.ok { providerError = "\(r.error)\n\n\(sentWith())" }
        } catch { providerError = "\(error.localizedDescription)\n\n\(sentWith())" }
    }

    /// What the request actually carried. Half of "I did set the key correctly"
    /// turns out to be "the key I typed never reached the request" — so say it.
    private func sentWith() -> String {
        let key = !draft.api_key.isEmpty ? "the key typed in the field"
                : draft.api_key_set ? "the stored key" : "no key at all"
        return "Sent to \(draft.base_url) with \(key)."
    }

    @MainActor
    private func runTest() async {
        testing = true
        test = nil
        providerError = ""
        defer { testing = false }
        do {
            // A cold 35B model takes far longer than a default request timeout.
            // That used to surface as "the engine isn't answering", sending you
            // looking in the wrong place. Now the timeout message names the path
            // and points at the model server it is waiting on.
            test = try await API.postRaw("/providers/test",
                                         rawBody: try JSONSerialization.data(
                                            withJSONObject: draft.asJSON()),
                                         timeout: 240)
            if let t = test, !t.models.isEmpty { models = t.models }
        } catch { providerError = "\(error.localizedDescription)\n\n\(sentWith())" }
    }

    @MainActor
    private func detect() async {
        detecting = true
        providerError = ""
        defer { detecting = false }
        do {
            let r: DetectResult = try await API.get("/providers/detect", timeout: 180)
            detected = r.found
        } catch {
            // Deliberately no sentWith() here, unlike the other two: this endpoint
            // scans COMMON_PORTS and never uses the base_url or key in the form.
            // Appending "Sent to …" would state something that did not happen.
            providerError = error.localizedDescription
        }
    }

    @MainActor
    private func apply(_ d: DetectedServer) {
        draft.kind = "openai"
        draft.preset = d.port == 8000 ? "omlx"
            : d.port == 11434 ? "ollama"
            : d.port == 1234 ? "lmstudio"
            : d.port == 8080 ? "llamacpp"
            : d.port == 8001 ? "vllm" : "custom"
        draft.base_url = d.base_url
        draft.api_key = d.api_key
        draft.model = d.models.first ?? ""
        models = d.models
        detected = []
        test = nil
        providerError = ""    // new connection, no stale red from the previous attempt
    }
}

/// A wrapping row of chips — SwiftUI has no built-in flow layout.
struct FlowRow: Layout {
    var spacing: CGFloat = 7

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth == .infinity ? x : maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
