import AVFoundation
import SwiftUI

// MARK: - Microphone

@Observable
final class Recorder {
    var isRecording = false
    var elapsed: TimeInterval = 0
    var level: Float = 0
    var denied = false
    private(set) var url: URL?

    private var recorder: AVAudioRecorder?
    private var ticker: Timer?

    func toggle() async {
        isRecording ? stop() : await start()
    }

    private func start() async {
        guard await AVCaptureDevice.requestAccess(for: .audio) else { denied = true; return }
        denied = false
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("voxdemo-rec-\(Int(Date().timeIntervalSince1970)).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 48000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        guard let r = try? AVAudioRecorder(url: out, settings: settings) else { return }
        r.isMeteringEnabled = true
        guard r.record() else { return }
        recorder = r
        url = out
        elapsed = 0
        isRecording = true
        // Timer fires on the main run loop, so touching state here is safe.
        ticker = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            guard let self, let r = self.recorder else { return }
            r.updateMeters()
            self.elapsed = r.currentTime
            // dB (-160...0) mapped onto a 0...1 bar
            self.level = max(0, min(1, (r.averagePower(forChannel: 0) + 50) / 50))
        }
    }

    func stop() {
        recorder?.stop()
        recorder = nil
        ticker?.invalidate()
        ticker = nil
        isRecording = false
        level = 0
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
    @State private var transcript = ""
    @State private var importedFile: URL?
    @State private var picking = false
    @State private var busy = false
    @State private var error: String?

    @State private var sampleText = "This is how I sound. Ready whenever you are."
    @State private var previewing: String?

    private var source: URL? { importedFile ?? recorder.url }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                library
                cloneCard
                Text("Cloning a real person's voice needs their consent. Label AI-generated audio.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(20)
        }
        .alert("Something went wrong", isPresented: .init(
            get: { error != nil }, set: { if !$0 { error = nil } })
        ) {
            Button("OK") { error = nil }
        } message: { Text(error ?? "") }
    }

    // MARK: library

    private var library: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Voice library").font(.title3.bold())
                Spacer()
                TextField("Preview line", text: $sampleText)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 320)
            }
            if engine.voices.isEmpty {
                Text("Waiting for the engine…").foregroundStyle(.secondary)
            }
            ForEach(engine.voices) { voice in
                HStack(spacing: 12) {
                    Image(systemName: voice.isPreset ? "person.wave.2" : "person.crop.circle.badge.checkmark")
                        .foregroundStyle(voice.isPreset ? Color.secondary : Color.green)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(voice.name).fontWeight(.medium)
                            Text(voice.isPreset ? "PRESET" : "CLONED")
                                .font(.caption2.bold())
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(.quaternary, in: Capsule())
                            if !voice.enrolled {
                                Text("not yet voiced").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        Text(voice.description).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if previewing == voice.id {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Preview") { preview(voice) }
                            .disabled(engine.health?.ready != true || previewing != nil)
                    }
                    if !voice.isPreset {
                        Button(role: .destructive) { remove(voice) } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                    }
                }
                .padding(.vertical, 6)
                Divider()
            }
        }
        .card()
    }

    private func preview(_ voice: Voice) {
        previewing = voice.id
        Task {
            defer { previewing = nil }
            do {
                let r: SpeakResult = try await API.run(
                    "/speak",
                    ["voice_id": voice.id, "text": sampleText] as [String: String],
                    onStep: { _, _ in })
                Preview.shared.play(r.path)
                await engine.refreshVoices()
            } catch { self.error = error.localizedDescription }
        }
    }

    private func remove(_ voice: Voice) {
        Task {
            do {
                let _: Deleted = try await API.delete("/voices/\(voice.id)")
                await engine.refreshVoices()
            } catch { self.error = error.localizedDescription }
        }
    }

    // MARK: clone

    private var cloneCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Clone a voice").font(.title3.bold())
            Text("Record or import 5–15 seconds of clean, single-speaker speech.")
                .font(.caption).foregroundStyle(.secondary)

            TextField("Voice name", text: $newName)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 320)

            HStack(spacing: 14) {
                Button {
                    importedFile = nil
                    Task { await recorder.toggle() }
                } label: {
                    Label(recorder.isRecording ? "Stop recording" : "Record",
                          systemImage: recorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                }
                .tint(recorder.isRecording ? .red : .accentColor)

                if recorder.isRecording {
                    ProgressView(value: Double(recorder.level))
                        .frame(width: 120)
                    Text(String(format: "%.1fs", recorder.elapsed))
                        .monospacedDigit().foregroundStyle(.secondary)
                }

                Text("or").foregroundStyle(.secondary)
                Button("Choose audio file…") { picking = true }
            }

            if let source, !recorder.isRecording {
                HStack(spacing: 10) {
                    Image(systemName: "waveform.circle.fill").foregroundStyle(.green)
                    Text(source.lastPathComponent).lineLimit(1)
                    if recorder.url != nil && importedFile == nil {
                        Text(String(format: "%.1fs", recorder.elapsed))
                            .foregroundStyle(.secondary).monospacedDigit()
                    }
                    Button("Play") { Preview.shared.play(source.path) }
                        .buttonStyle(.borderless)
                    Button("Clear") { recorder.discard(); importedFile = nil }
                        .buttonStyle(.borderless)
                }
                .font(.callout)
            }

            TextField("Transcript of the clip (optional — unlocks highest-fidelity cloning)",
                      text: $transcript, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...4)

            HStack {
                Button {
                    save()
                } label: {
                    if busy { ProgressView().controlSize(.small) } else { Text("Clone & save voice") }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(busy || source == nil || newName.trimmingCharacters(in: .whitespaces).isEmpty)
                if recorder.denied {
                    Text("Microphone access denied — enable VoxDemo in System Settings › Privacy & Security › Microphone.")
                        .font(.caption).foregroundStyle(.red)
                }
            }
        }
        .card()
        .fileImporter(isPresented: $picking, allowedContentTypes: [.audio]) { result in
            if case .success(let url) = result {
                recorder.discard()
                importedFile = url
            }
        }
    }

    private func save() {
        guard let source else { return }
        busy = true
        Task {
            defer { busy = false }
            do {
                let _: Voice = try await API.post("/voices", [
                    "name": newName, "source_path": source.path, "transcript": transcript,
                ] as [String: String])
                await engine.refreshVoices()
                newName = ""
                transcript = ""
                recorder.discard()
                importedFile = nil
            } catch { self.error = error.localizedDescription }
        }
    }
}
