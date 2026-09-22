import SwiftUI

/// First-run welcome. Explains the shape of the app in one screen, then gets out
/// of the way — it is shown once and never again.
struct WelcomeSheet: View {
    @Environment(Engine.self) private var engine
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 16) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(LinearGradient(colors: [Palette.accent, Color(hex: "#5a6ff0")],
                                             startPoint: .topLeading, endPoint: .bottomTrailing))
                        .frame(width: 54, height: 54)
                    Image(systemName: "film.stack")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundStyle(.white)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Turn a repository into a narrated demo")
                        .font(.title2.weight(.semibold))
                    Text("CodeReel reads the code, writes the script, speaks it and renders the video — "
                         + "all on this machine.")
                        .font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
            .padding(.bottom, 22)

            VStack(alignment: .leading, spacing: 14) {
                step(1, "sparkles.rectangle.stack", "Point it at a repo",
                     "Claude Code or a local model reads it and drafts the beats: the problem, "
                     + "the solution, the features, the close.")
                step(2, "slider.horizontal.3", "Edit the script",
                     "Every line is yours to change. Drag scenes to reorder them, and pick what "
                     + "each one shows — a real screenshot, a code card, the file tree, the numbers.")
                step(3, "waveform", "Choose a voice",
                     "Eight presets, or clone your own from a short recording. Every scene speaks "
                     + "in the same voice.")
                step(4, "play.rectangle", "Render",
                     "A narrated MP4 lands in ~/Movies/CodeReel with its HyperFrames project beside "
                     + "it, ready to keep editing.")
            }

            Divider().padding(.vertical, 20)

            HStack(spacing: 14) {
                Image(systemName: engine.installed ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(engine.installed ? Palette.good : .orange)
                VStack(alignment: .leading, spacing: 1) {
                    Text(engine.installed ? "Engine found" : "Engine not found")
                        .font(.callout.weight(.medium))
                    Text(engine.installed
                         ? engine.repoPath
                         : "Expected a .venv and server.py. Use Locate repo… in the sidebar.")
                        .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                Text("The first render loads ~5 GB of voice weights.")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            .padding(.bottom, 20)

            HStack {
                Spacer()
                Button("Get started") { done() }
                    .controlSize(.large)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(28)
        .frame(width: 620)
    }

    private func step(_ n: Int, _ icon: String, _ title: String, _ body: String) -> some View {
        HStack(alignment: .top, spacing: 13) {
            ZStack {
                Circle().fill(Palette.accentSoft).frame(width: 30, height: 30)
                Image(systemName: icon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.accent)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.semibold))
                Text(body).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }
}
