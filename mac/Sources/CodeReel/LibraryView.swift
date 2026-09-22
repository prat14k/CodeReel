import AVKit
import SwiftUI

// MARK: - Library

struct LibraryView: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store

    @State private var demos: [DemoItem] = []
    @State private var thumbs: [String: NSImage] = [:]
    @State private var loading = true
    @State private var loadError = ""
    @State private var playing: DemoItem?
    @State private var player: AVPlayer?
    @State private var confirming: DemoItem?

    private let columns = [GridItem(.adaptive(minimum: 300, maximum: 420), spacing: 16)]

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if loading {
                ProgressView("Looking for rendered demos…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if !loadError.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 36)).foregroundStyle(.orange)
                    Text("Couldn't load library").font(.title3.weight(.semibold))
                    Text(loadError).font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).lineLimit(3)
                    Button { Task { await refresh() } } label: {
                        Label("Retry", systemImage: "arrow.clockwise")
                    }.controlSize(.small)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(22)
            } else if demos.isEmpty {
                empty
            } else {
                ScrollView {
                    LazyVGrid(columns: columns, spacing: 16) {
                        ForEach(demos) { item in
                            DemoCard(item: item, thumb: thumbs[item.id]) {
                                play(item)
                            } onReveal: {
                                NSWorkspace.shared.activateFileViewerSelecting(
                                    [URL(fileURLWithPath: item.path)])
                            } onDelete: {
                                confirming = item
                            } onEdit: {
                                store.repoPath = ""
                                store.title = item.name
                                store.subtitle = item.subtitle
                                withAnimation { store.step = .script }
                            }
                            .onAppear { loadThumb(item) }
                        }
                    }
                    .padding(22)
                }
            }
        }
        .task { await refresh() }
        .sheet(item: $playing) { item in
            PlayerSheet(item: item, player: player)
        }
        .alert("Delete this demo?", isPresented: .init(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } })
        ) {
            Button("Cancel", role: .cancel) { confirming = nil }
            Button("Delete", role: .destructive) {
                if let d = confirming { remove(d) }
                confirming = nil
            }
        } message: {
            Text("The video and its HyperFrames project folder are removed from disk. "
                 + "This cannot be undone.")
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            SectionTitle(title: "Library",
                         subtitle: "Everything rendered on this machine, newest first.")
            Spacer()
            if let dir = engine.health?.output_dir {
                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: dir))
                } label: { Label("Open folder", systemImage: "folder") }
                    .controlSize(.small)
            }
            Button {
                Task { await refresh() }
            } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                .controlSize(.small)
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 14)
    }

    private var empty: some View {
        VStack(spacing: 14) {
            Image(systemName: "rectangle.stack.badge.play")
                .font(.system(size: 40))
                .foregroundStyle(Palette.accent.opacity(0.7))
            Text("No demos yet").font(.title3.weight(.semibold))
            Text("Render one from the Create tab and it will show up here.")
                .font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @MainActor
    private func refresh() async {
        loading = true
        loadError = ""
        defer { loading = false }
        do {
            let r: LibraryResult = try await API.get("/library")
            demos = r.demos
        } catch {
            // A silent empty list here reads as "everything got deleted". It
            // almost always means the output folder is gone or unreadable —
            // the global modal would be fair, but a Retry next to the failure
            // is faster to recover from.
            demos = []
            loadError = error.localizedDescription
        }
    }

    private func play(_ item: DemoItem) {
        player = AVPlayer(url: URL(fileURLWithPath: item.path))
        playing = item
        player?.play()
    }

    @MainActor
    private func remove(_ item: DemoItem) {
        Task {
            do {
                let _: Deleted = try await API.delete("/library/\(item.id)")
                demos.removeAll { $0.id == item.id }
            } catch { engine.lastError = error.localizedDescription }
        }
    }

    private func loadThumb(_ item: DemoItem) {
        guard thumbs[item.id] == nil else { return }
        let path = item.path
        let id = item.id
        Task.detached(priority: .utility) {
            let asset = AVURLAsset(url: URL(fileURLWithPath: path))
            let gen = AVAssetImageGenerator(asset: asset)
            gen.appliesPreferredTrackTransform = true
            gen.maximumSize = CGSize(width: 640, height: 640)
            let at = CMTime(seconds: min(4.0, max(1.0, item.duration * 0.25)),
                            preferredTimescale: 600)
            guard let (cg, _) = try? await gen.image(at: at) else { return }
            let img = NSImage(cgImage: cg, size: .zero)
            await MainActor.run { thumbs[id] = img }
        }
    }
}

private struct DemoCard: View {
    let item: DemoItem
    let thumb: NSImage?
    let onPlay: () -> Void
    let onReveal: () -> Void
    let onDelete: () -> Void
    let onEdit: () -> Void

    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack {
                Color.black.opacity(0.35)
                if let thumb {
                    Image(nsImage: thumb)
                        .resizable().scaledToFill()
                } else {
                    ProgressView().controlSize(.small)
                }
                if hovering {
                    Color.black.opacity(0.35)
                    Image(systemName: "play.circle.fill")
                        .font(.system(size: 42))
                        .foregroundStyle(.white)
                        .shadow(radius: 8)
                }
            }
            .frame(height: 168)
            .clipped()
            .contentShape(Rectangle())
            .onTapGesture(perform: onPlay)
            .onHover { hovering = $0 }

            VStack(alignment: .leading, spacing: 9) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.name).font(.callout.weight(.semibold)).lineLimit(1)
                    HStack(spacing: 6) {
                        if item.duration > 0 {
                            Text("\(String(format: "%.0f", item.duration))s")
                        }
                        if item.scenes > 0 { Text("· \(item.scenes) scenes") }
                        if !item.voice.isEmpty { Text("· \(item.voice)") }
                    }
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }

                HStack(spacing: 6) {
                    if item.has_hook { Chip(text: "HOOK", color: Palette.accent) }
                    if item.has_close { Chip(text: "CLOSE", color: Palette.accent) }
                    Chip(text: item.aspect.uppercased(), color: .secondary)
                    Spacer()
                    Text(item.sizeText).font(.caption2).foregroundStyle(.tertiary)
                }

                Text(item.dateText).font(.caption2).foregroundStyle(.tertiary)

                HStack(spacing: 6) {
                    Button("Play", action: onPlay).controlSize(.small)
                    Button("Reveal", action: onReveal).controlSize(.small)
                    Menu {
                        Button("Edit the script again", action: onEdit)
                        Button("Open HyperFrames project") {
                            NSWorkspace.shared.open(URL(fileURLWithPath: item.project))
                        }
                        Button("Copy path") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(item.path, forType: .string)
                        }
                        Divider()
                        Button("Delete…", role: .destructive, action: onDelete)
                    } label: {
                        Image(systemName: "ellipsis")
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    Spacer()
                }
            }
            .padding(12)
        }
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.6),
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.09), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

private struct PlayerSheet: View {
    let item: DemoItem
    let player: AVPlayer?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name).font(.title3.weight(.semibold))
                    Text("\(item.scenes) scenes · \(String(format: "%.1f", item.duration))s · \(item.voice)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }

            if let player {
                VideoPlayer(player: player)
                    .aspectRatio(item.aspectRatio, contentMode: .fit)
                    .frame(maxWidth: 900, maxHeight: 560)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            HStack(spacing: 9) {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: item.path)])
                } label: { Label("Show in Finder", systemImage: "folder") }
                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: item.project))
                } label: { Label("Open project", systemImage: "hammer") }
                Spacer()
                Text(item.path).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                    .truncationMode(.middle)
            }
            .controlSize(.small)
        }
        .padding(20)
    }
}
