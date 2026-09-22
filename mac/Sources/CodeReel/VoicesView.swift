import AVFoundation
import SwiftUI

// MARK: - Microphone

@Observable
final class Recorder {
    var isRecording = false
    var elapsed: TimeInterval = 0
    var level: Float = 0
    var problem: String?
    private(set) var url: URL?

    private var recorder: AVAudioRecorder?
    private var ticker: Timer?

    func toggle() async {
        isRecording ? stop() : await start()
    }

    private func start() async {
        // requestAccess resumes on an arbitrary thread, so everything below hops
        // to main — a Timer scheduled off-main is never added to a run loop and
        // silently never fires, which is why the elapsed counter sat at 0.0s.
        guard await AVCaptureDevice.requestAccess(for: .audio) else {
            await fail("Microphone access denied. Enable CodeReel in System Settings > "
                       + "Privacy & Security > Microphone, then press Record again.")
            return
        }
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("codereel-rec-\(Int(Date().timeIntervalSince1970)).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 48000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let r: AVAudioRecorder
        do {
            r = try AVAudioRecorder(url: out, settings: settings)
        } catch {
            await fail("Could not open the microphone: \(error.localizedDescription)")
            return
        }
        r.isMeteringEnabled = true
        guard r.record() else {
            await fail("The microphone refused to start - another app may be holding it.")
            return
        }
        await MainActor.run {
            problem = nil
            recorder = r
            url = out
            elapsed = 0
            isRecording = true
            let tick = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
                guard let self, let r = self.recorder else { return }
                r.updateMeters()
                self.elapsed = r.currentTime
                // dB (-160...0) mapped onto a 0...1 bar
                self.level = max(0, min(1, (r.averagePower(forChannel: 0) + 50) / 50))
            }
            // .common so the counter keeps running while a menu or scroll is tracking
            RunLoop.main.add(tick, forMode: .common)
            ticker = tick
        }
    }

    func stop() {
        recorder?.stop()
        // currentTime reads 0 once stopped — take the real length from the file.
        if let url, let probe = try? AVAudioPlayer(contentsOf: url) { elapsed = probe.duration }
        recorder = nil
        ticker?.invalidate()
        ticker = nil
        isRecording = false
        level = 0
    }

    @MainActor
    private func fail(_ message: String) {
        problem = message
        isRecording = false
    }

    func discard() {
        stop()
        if let url { try? FileManager.default.removeItem(at: url) }
        url = nil
        elapsed = 0
    }
}

// MARK: - View

struct VoicesView: View {
    @Environment(Engine.self) private var engine
    @State private var recorder = Recorder()

    @State private var newName = ""
    @State private var importedFile: URL?
    @State private var picking = false
    @State private var busy = false
    @State private var saveError = ""
    @State private var previewError = ""

    @State private var sampleText = "This is how I sound. Ready whenever you are."
    @State private var previewing: String?
    @State private var lastPreview: URL?
    @State private var lastPreviewName = ""

    private var source: URL? { importedFile ?? recorder.url }
    private var presets: [Voice] { engine.voices.filter(\.isPreset) }
    private var cloned: [Voice] { engine.voices.filter { !$0.isPreset } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                previewCard
                if !cloned.isEmpty { clonedSection }
                presetSection
                cloneCard
                Text("Cloning a real person's voice needs their consent. Label AI-generated audio.")
                    .font(.caption2).foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(22)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity)
        }
    }

    // MARK: preview

    private var previewCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Hear a voice",
                             subtitle: "Every voice speaks through a reference clip, so timbre is "
                                     + "stable across scenes and sessions.")
                HStack(spacing: 10) {
                    TextField("Line to speak", text: $sampleText)
                        .textFieldStyle(.roundedBorder)
                    if !lastPreviewName.isEmpty {
                        Text(lastPreviewName).font(.caption).foregroundStyle(.secondary)
                            .frame(width: 90, alignment: .trailing)
                    }
                }
                if let lastPreview {
                    AudioPlayerBar(url: lastPreview, autoplay: true)
                        .frame(height: 38)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                if engine.health?.ready != true {
                    Notice(icon: "hourglass", title: "Waiting for the engine",
                           detail: "The voice model loads once per launch.", tint: .orange)
                }
                if !previewError.isEmpty {
                    Text(previewError).font(.caption).foregroundStyle(.red).lineLimit(2)
                }
            }
        }
    }

    // MARK: lists

    private var presetSection: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Presets",
                             subtitle: "Voice-design personas. The first use designs the voice; "
                                     + "that clip becomes its permanent reference.")
                ForEach(presets) { v in
                    VoiceRow(voice: v, previewing: previewing,
                             ready: engine.health?.ready == true,
                             onPreview: { preview(v) }, onDelete: nil)
                }
            }
        }
    }

    private var clonedSection: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Your voices", subtitle: "Cloned from a recording or a file.")
                ForEach(cloned) { v in
                    VoiceRow(voice: v, previewing: previewing,
                             ready: engine.health?.ready == true,
                             onPreview: { preview(v) }, onDelete: { remove(v) })
                }
            }
        }
    }

    // MARK: clone

    private var cloneCard: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                SectionTitle(title: "Clone a voice",
                             subtitle: "Record or import 5–15 seconds of clean, single-speaker speech.")

                HStack(spacing: 12) {
                    TextField("Voice name", text: $newName)
                        .textFieldStyle(.roundedBorder).frame(width: 240)

                    Button {
                        importedFile = nil
                        Task { await recorder.toggle() }
                    } label: {
                        Label(recorder.isRecording ? "Stop recording" : "Record",
                              systemImage: recorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                    }
                    .tint(recorder.isRecording ? .red : Palette.accent)

                    if recorder.isRecording {
                        ProgressView(value: Double(recorder.level)).frame(width: 110)
                        Text(String(format: "%.1fs", recorder.elapsed))
                            .monospacedDigit().foregroundStyle(.secondary)
                    }

                    Text("or").font(.caption).foregroundStyle(.tertiary)
                    Button("Choose file…") { picking = true }
                    Spacer()
                }

                if let source, !recorder.isRecording {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 9) {
                            Image(systemName: "waveform.circle.fill").foregroundStyle(Palette.good)
                            Text(source.lastPathComponent).font(.callout).lineLimit(1)
                            if recorder.url != nil && importedFile == nil {
                                Text(String(format: "%.1fs", recorder.elapsed))
                                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                            }
                            Spacer()
                            Button("Clear") { recorder.discard(); importedFile = nil }
                                .buttonStyle(.borderless).controlSize(.small)
                        }
                        AudioPlayerBar(url: source)
                            .frame(height: 34)
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                    }
                }

                HStack(spacing: 12) {
                    Button {
                        save()
                    } label: {
                        HStack(spacing: 7) {
                            if busy { ProgressView().controlSize(.small) }
                            else { Image(systemName: "person.crop.circle.badge.plus") }
                            Text("Clone & save voice")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || source == nil
                              || newName.trimmingCharacters(in: .whitespaces).isEmpty)

                    if let problem = recorder.problem {
                        Text(problem).font(.caption).foregroundStyle(.red).lineLimit(2)
                    }
                    if !saveError.isEmpty {
                        Text(saveError).font(.caption).foregroundStyle(.red).lineLimit(2)
                    }
                    Spacer()
                }
            }
        }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.audio]) { result in
            if case .success(let url) = result {
                recorder.discard()
                importedFile = url
            }
        }
    }

    // MARK: actions

    private func preview(_ voice: Voice) {
        previewing = voice.id
        previewError = ""
        Task {
            defer { previewing = nil }
            do {
                let r: SpeakResult = try await API.run(
                    "/speak",
                    ["voice_id": voice.id, "text": sampleText] as [String: String],
                    onStep: { _, _ in })
                lastPreview = URL(fileURLWithPath: r.path)
                lastPreviewName = r.voice
                await engine.refreshVoices()
            } catch { previewError = error.localizedDescription }
        }
    }

    private func remove(_ voice: Voice) {
        Task {
            do {
                let _: Deleted = try await API.delete("/voices/\(voice.id)")
                await engine.refreshVoices()
            } catch { engine.lastError = error.localizedDescription }
        }
    }

    private func save() {
        guard let source else { return }
        busy = true
        saveError = ""
        Task {
            defer { busy = false }
            do {
                let _: Voice = try await API.post("/voices", [
                    "name": newName, "source_path": source.path,
                ] as [String: String])
                await engine.refreshVoices()
                newName = ""
                recorder.discard()
                importedFile = nil
            } catch { saveError = error.localizedDescription }
        }
    }
}

private struct VoiceRow: View {
    let voice: Voice
    let previewing: String?
    let ready: Bool
    let onPreview: () -> Void
    let onDelete: (() -> Void)?

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle().fill(Palette.accentSoft).frame(width: 30, height: 30)
                Image(systemName: voice.isPreset ? "person.wave.2" : "person.crop.circle.badge.checkmark")
                    .font(.system(size: 13))
                    .foregroundStyle(voice.isPreset ? Palette.accent : Palette.good)
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(voice.name).font(.callout.weight(.medium))
                    Chip(text: voice.isPreset ? "PRESET" : "CLONED",
                         color: voice.isPreset ? .secondary : Palette.good)
                    if !voice.enrolled {
                        Text("not yet voiced").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                Text(voice.description).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if previewing == voice.id {
                ProgressView().controlSize(.small)
            } else {
                Button("Preview", action: onPreview)
                    .controlSize(.small)
                    .disabled(!ready || previewing != nil)
            }
            if let onDelete {
                Button(role: .destructive, action: onDelete) {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless).controlSize(.small)
            }
        }
        .padding(.vertical, 5)
        Divider().opacity(0.5)
    }
}
