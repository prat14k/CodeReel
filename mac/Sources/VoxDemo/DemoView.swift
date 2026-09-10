import AVKit
import SwiftUI

struct SceneDraft: Identifiable, Equatable {
    let id = UUID()
    var text = ""
    var heading = ""
    var media = ""
}

private struct SceneBody: Encodable {
    var text: String
    var heading: String
    var media: String
}

private struct DemoRequest: Encodable {
    var title: String
    var subtitle: String
    var voice_id: String
    var scenes: [SceneBody]
    var theme: String
    var aspect: String
    var style: String
    var cfg: Double
    var timesteps: Int
    var seed: Int
}

struct DemoView: View {
    @Environment(Engine.self) private var engine

    @State private var title = "Ship faster with Acme"
    @State private var subtitle = "Product demo"
    @State private var voiceID = "aria"
    @State private var theme = "midnight"
    @State private var aspect = "landscape"
    @State private var style = ""
    @State private var cfg = 2.0
    @State private var timesteps = 12.0
    @State private var seed = 42
    @State private var showAdvanced = false

    @State private var scenes: [SceneDraft] = [
        SceneDraft(text: "Acme turns a rough idea into a working prototype in an afternoon."),
        SceneDraft(text: "Start from a template, wire up your data, and ship — no infrastructure to babysit."),
    ]

    @State private var pasting = false
    @State private var pasteBuffer = ""
    @State private var pickingMediaFor: SceneDraft.ID?

    @State private var busy = false
    @State private var progress = 0.0
    @State private var stage = ""
    @State private var error: String?
    @State private var result: DemoResult?
    @State private var player: AVPlayer?

    var body: some View {
        HSplitView {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    setup
                    sceneList
                }
                .padding(20)
                .frame(minWidth: 520)
            }
            output.frame(minWidth: 340)
        }
        .sheet(isPresented: $pasting) { pasteSheet }
        .fileImporter(
            isPresented: .init(get: { pickingMediaFor != nil },
                               set: { if !$0 { pickingMediaFor = nil } }),
            allowedContentTypes: [.image, .movie]
        ) { res in
            if case .success(let url) = res, let id = pickingMediaFor,
               let i = scenes.firstIndex(where: { $0.id == id }) {
                scenes[i].media = url.path
            }
            pickingMediaFor = nil
        }
        .alert("Render failed", isPresented: .init(
            get: { error != nil }, set: { if !$0 { error = nil } })
        ) {
            Button("OK") { error = nil }
        } message: { Text(error ?? "") }
    }

    // MARK: setup

    private var setup: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Demo").font(.title3.bold())
            TextField("Title", text: $title).textFieldStyle(.roundedBorder)
            TextField("Kicker (small line above the title)", text: $subtitle)
                .textFieldStyle(.roundedBorder)

            HStack(spacing: 16) {
                Picker("Voice", selection: $voiceID) {
                    ForEach(engine.voices) { v in
                        Text(v.isPreset ? v.name : "\(v.name) (cloned)").tag(v.id)
                    }
                }
                .frame(maxWidth: 260)
                Picker("Look", selection: $theme) {
                    ForEach(engine.health?.themes ?? ["midnight"], id: \.self) {
                        Text($0.capitalized).tag($0)
                    }
                }
                .frame(maxWidth: 180)
                Picker("Frame", selection: $aspect) {
                    ForEach(engine.health?.aspects ?? ["landscape"], id: \.self) {
                        Text($0.capitalized).tag($0)
                    }
                }
                .frame(maxWidth: 190)
            }

            DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("Style direction, e.g. cheerful, slightly faster", text: $style)
                        .textFieldStyle(.roundedBorder)
                    HStack {
                        Text("CFG \(cfg, specifier: "%.1f")").frame(width: 80, alignment: .leading)
                        Slider(value: $cfg, in: 1...4, step: 0.1)
                    }
                    HStack {
                        Text("Steps \(Int(timesteps))").frame(width: 80, alignment: .leading)
                        Slider(value: $timesteps, in: 4...40, step: 1)
                    }
                    HStack {
                        Text("Seed").frame(width: 80, alignment: .leading)
                        TextField("", value: $seed, format: .number)
                            .textFieldStyle(.roundedBorder).frame(width: 100)
                        Text("same seed + same script = same render")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding(.top, 8)
            }
            .font(.callout)
        }
        .card()
    }

    // MARK: scenes

    private var sceneList: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Scenes").font(.title3.bold())
                Text("one narration beat each")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Paste script…") { pasteBuffer = ""; pasting = true }
                Button {
                    scenes.append(SceneDraft())
                } label: { Image(systemName: "plus") }
            }

            ForEach($scenes) { $scene in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(String(format: "%02d", (scenes.firstIndex(of: scene) ?? 0) + 1))
                            .font(.caption.bold().monospaced()).foregroundStyle(.secondary)
                        TextField("On-screen heading (optional — taken from the narration if blank)",
                                  text: $scene.heading)
                            .textFieldStyle(.roundedBorder)
                        Button(role: .destructive) {
                            scenes.removeAll { $0.id == scene.id }
                        } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                            .disabled(scenes.count == 1)
                    }
                    TextField("What the voice says in this scene", text: $scene.text, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(2...6)
                    HStack(spacing: 8) {
                        Button("Screenshot / clip…") { pickingMediaFor = scene.id }
                            .controlSize(.small)
                        if !scene.media.isEmpty {
                            Text((scene.media as NSString).lastPathComponent)
                                .font(.caption).lineLimit(1)
                            Button("Remove") { scene.media = "" }
                                .buttonStyle(.borderless).controlSize(.small)
                        } else {
                            Text("optional — fills the frame behind the text")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(12)
                .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
            }
        }
        .card()
    }

    private var pasteSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Paste your script").font(.headline)
            Text("A blank line starts a new scene.").font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $pasteBuffer)
                .font(.body)
                .frame(width: 520, height: 260)
                .border(.quaternary)
            HStack {
                Spacer()
                Button("Cancel") { pasting = false }
                Button("Split into scenes") {
                    let blocks = pasteBuffer
                        .replacingOccurrences(of: "\r\n", with: "\n")
                        .components(separatedBy: "\n\n")
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                        .filter { !$0.isEmpty }
                    if !blocks.isEmpty { scenes = blocks.map { SceneDraft(text: $0) } }
                    pasting = false
                }
                .keyboardShortcut(.defaultAction)
                .disabled(pasteBuffer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
    }

    // MARK: output

    private var output: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Render").font(.title3.bold())

            Button {
                generate()
            } label: {
                Label(busy ? "Generating…" : "Generate demo", systemImage: "wand.and.stars")
                    .frame(maxWidth: .infinity)
            }
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .disabled(busy || engine.health?.ready != true || !hasScript)

            if busy {
                ProgressView(value: progress) { Text(stage).font(.caption).lineLimit(2) }
            } else if engine.health?.ready != true {
                Text("Waiting for the engine to finish loading.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if !hasScript {
                Text("Write at least one scene of narration.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            if let player {
                VideoPlayer(player: player)
                    .aspectRatio(aspect == "portrait" ? 9.0/16 : aspect == "square" ? 1 : 16.0/9,
                                 contentMode: .fit)
                    .frame(maxWidth: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            if let result {
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(result.scenes) scenes · \(result.duration, specifier: "%.1f")s · \(result.voice)")
                        .font(.callout.weight(.medium))
                    Text(result.path).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    HStack {
                        Button("Show in Finder") {
                            NSWorkspace.shared.activateFileViewerSelecting(
                                [URL(fileURLWithPath: result.path)])
                        }
                        Button("Open HyperFrames project") {
                            NSWorkspace.shared.activateFileViewerSelecting(
                                [URL(fileURLWithPath: result.project)])
                        }
                    }
                    .controlSize(.small)
                }
            }
            Spacer()
        }
        .padding(20)
    }

    private var hasScript: Bool {
        scenes.contains { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    private func generate() {
        busy = true
        progress = 0
        stage = "starting"
        result = nil
        player = nil
        let body = DemoRequest(
            title: title, subtitle: subtitle, voice_id: voiceID,
            scenes: scenes
                .filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
                .map { SceneBody(text: $0.text, heading: $0.heading, media: $0.media) },
            theme: theme, aspect: aspect, style: style,
            cfg: cfg, timesteps: Int(timesteps), seed: seed)
        Task {
            defer { busy = false }
            do {
                let r: DemoResult = try await API.run("/demos", body) { p, msg in
                    progress = p
                    stage = msg
                }
                result = r
                player = AVPlayer(url: URL(fileURLWithPath: r.path))
                player?.play()
            } catch { self.error = error.localizedDescription }
        }
    }
}
