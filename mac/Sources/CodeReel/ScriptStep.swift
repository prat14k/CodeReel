import SwiftUI
import UniformTypeIdentifiers

// MARK: - 2. Script

struct ScriptStep: View {
    @Environment(Engine.self) private var engine
    @Environment(CreateStore.self) private var store

    @State private var pickingLogo = false
    @State private var pickingRefFor: UUID?
    @State private var pasting = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            sceneList
        }
        .fileImporter(isPresented: $pickingLogo, allowedContentTypes: [.image]) { res in
            if case .success(let url) = res { store.logo = url.path }
        }
        .fileImporter(
            isPresented: Binding(get: { pickingRefFor != nil },
                                 set: { if !$0 { pickingRefFor = nil } }),
            allowedContentTypes: refTypes
        ) { res in
            if case .success(let url) = res, let id = pickingRefFor,
               let i = store.scenes.firstIndex(where: { $0.id == id }) {
                store.scenes[i].visualRef = url.path
            }
            pickingRefFor = nil
        }
        .sheet(isPresented: $pasting) { PasteScriptSheet { blocks in
            store.scenes = blocks.map { SceneDraft(text: $0) }
        } }
    }

    private var refTypes: [UTType] {
        guard let id = pickingRefFor,
              let draft = store.scenes.first(where: { $0.id == id }) else { return [.image, .sourceCode, .text] }
        return draft.kind.refKind == .image ? [.image] : [.sourceCode, .text, .data]
    }

    private var header: some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: 12) {            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 5) {
                    FieldLabel(text: "Title")
                    TextField("Product name", text: $store.title)
                        .textFieldStyle(.roundedBorder).frame(width: 250)
                }
                VStack(alignment: .leading, spacing: 5) {
                    FieldLabel(text: "Who it's for", hint: "the line above the title")
                    TextField("e.g. solo developers", text: $store.subtitle)
                        .textFieldStyle(.roundedBorder).frame(width: 250)
                }
                VStack(alignment: .leading, spacing: 5) {
                    FieldLabel(text: "Logo")
                    HStack(spacing: 7) {
                        if !store.logo.isEmpty,
                           let img = NSImage(contentsOfFile: store.logo) {
                            Image(nsImage: img).resizable().scaledToFit()
                                .frame(width: 22, height: 22)
                                .clipShape(RoundedRectangle(cornerRadius: 5))
                        }
                        Button(store.logo.isEmpty ? "Choose…" : "Change") { pickingLogo = true }
                            .controlSize(.small)
                        if !store.logo.isEmpty {
                            Button("Remove") { store.logo = "" }
                                .controlSize(.small).buttonStyle(.borderless)
                        }
                    }
                    .frame(height: 22)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 5) {
                    if !store.providerLabel.isEmpty {
                        Text("drafted by \(store.providerLabel)")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                    HStack(spacing: 7) {
                        Button { pasting = true } label: { Label("Paste script…", systemImage: "doc.on.clipboard") }
                            .controlSize(.small)
                        Button {
                            store.scenes.append(SceneDraft())
                        } label: { Label("Add scene", systemImage: "plus") }
                            .controlSize(.small)
                    }
                }
            }

            VStack(alignment: .leading, spacing: 5) {
                FieldLabel(text: "Opening hook", hint: "one spoken line before the product is named")
                TextField("Name the pain your audience feels today", text: $store.hook)
                    .textFieldStyle(.roundedBorder)
            }
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 14)
    }

    private var sceneList: some View {
        @Bindable var store = store
        return List {
            ForEach($store.scenes) { $scene in
                SceneEditor(
                    scene: $scene,
                    index: (store.scenes.firstIndex { $0.id == scene.id } ?? 0) + 1,
                    canDelete: store.scenes.count > 1,
                    onDelete: { store.scenes.removeAll { $0.id == scene.id } },
                    onPickRef: { pickingRefFor = scene.id }
                )
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 5, leading: 22, bottom: 5, trailing: 22))
            }
            .onMove { from, to in store.scenes.move(fromOffsets: from, toOffset: to) }

            Section {
                CloseEditor(close: $store.close)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 12, leading: 22, bottom: 22, trailing: 22))
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .environment(\.defaultMinListRowHeight, 10)
    }
}

// MARK: - One scene

private struct SceneEditor: View {
    @Environment(Engine.self) private var engine
    @Binding var scene: SceneDraft
    let index: Int
    let canDelete: Bool
    let onDelete: () -> Void
    let onPickRef: () -> Void

    @State private var expanded = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if expanded {
                Divider().opacity(0.5)
                VStack(alignment: .leading, spacing: 11) {
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 11) {
                            roleAndHeading
                            narration
                        }
                        .frame(maxWidth: .infinity)

                        VisualPicker(scene: $scene, onPickRef: onPickRef)
                            .frame(width: 260)
                    }
                }
                .padding(14)
            }
        }
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.6),
                    in: RoundedRectangle(cornerRadius: 13, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.09), lineWidth: 1)
        )
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.tertiary)
                .help("Drag to reorder")

            Text(String(format: "%02d", index))
                .font(.system(.caption, design: .monospaced).weight(.bold))
                .foregroundStyle(Palette.accent)

            if scene.role.isEmpty && scene.heading.isEmpty && !scene.hasNarration {
                Text("New scene").font(.caption).foregroundStyle(.tertiary)
            } else {
                Text(scene.heading.isEmpty ? scene.role : scene.heading)
                    .font(.callout.weight(.medium)).lineLimit(1)
            }

            Spacer()

            if !scene.hasNarration {
                Chip(text: "EMPTY", color: .orange)
            }
            Chip(text: scene.kind.label.uppercased(),
                 color: scene.kind == .auto ? .secondary : Palette.accent)

            Button {
                withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
            } label: {
                Image(systemName: expanded ? "chevron.up" : "chevron.down")
            }
            .buttonStyle(.borderless).controlSize(.small)

            Button(role: .destructive, action: onDelete) {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless).controlSize(.small)
            .disabled(!canDelete)
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 9)
    }

    private var roleAndHeading: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 5) {
                FieldLabel(text: "Label", hint: "shown on screen")
                TextField("e.g. The problem", text: $scene.role)
                    .textFieldStyle(.roundedBorder).frame(width: 150)
            }
            VStack(alignment: .leading, spacing: 5) {
                FieldLabel(text: "Heading", hint: "blank = taken from the narration")
                TextField("", text: $scene.heading).textFieldStyle(.roundedBorder)
            }
        }
    }

    private var narration: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                FieldLabel(text: "Narration", hint: "what the voice says")
                Spacer()
                Text("\(scene.text.split(separator: " ").count) words")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            TextField("", text: $scene.text, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...6)
        }
    }
}

// MARK: - Visual picker

private struct VisualPicker: View {
    @Binding var scene: SceneDraft
    let onPickRef: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VisualPreview(kind: scene.kind, ref: scene.visualRef.isEmpty ? scene.media : scene.visualRef)

            HStack(spacing: 7) {
                Menu {
                    ForEach(VisualKind.choosable) { k in
                        Button {
                            scene.visual = k.rawValue
                            if k.refKind == .none { scene.visualRef = "" }
                        } label: {
                            Label(k.label, systemImage: k.icon)
                        }
                    }
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: scene.kind.icon)
                        Text(scene.kind.label)
                        Image(systemName: "chevron.down").font(.system(size: 8))
                    }
                    .font(.caption)
                }
                .menuStyle(.borderlessButton)
                .fixedSize()

                Spacer()
            }

            switch scene.kind.refKind {
            case .image:
                refRow(icon: "photo", placeholder: "Choose an image…", action: onPickRef)
            case .file:
                refRow(icon: "doc.text", placeholder: "Choose a source file…", action: onPickRef)
            case .command:
                TextField("command to show, e.g. npm run dev", text: $scene.visualRef)
                    .textFieldStyle(.roundedBorder).font(.caption)
            case .none:
                Text(scene.kind.blurb)
                    .font(.caption2).foregroundStyle(.tertiary).lineLimit(2)
            }

            if scene.kind == .screenshot || scene.kind == .code || scene.kind == .terminal {
                TextField("caption for the visual (optional)", text: $scene.visualNote)
                    .textFieldStyle(.roundedBorder).font(.caption)
            }
        }
    }

    private func refRow(icon: String, placeholder: String, action: @escaping () -> Void) -> some View {
        HStack(spacing: 6) {
            Button(action: action) {
                HStack(spacing: 5) {
                    Image(systemName: icon)
                    Text(scene.visualRef.isEmpty ? placeholder
                         : (scene.visualRef as NSString).lastPathComponent)
                        .lineLimit(1)
                }
                .font(.caption)
            }
            .controlSize(.small)
            if !scene.visualRef.isEmpty {
                Button {
                    scene.visualRef = ""
                } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.borderless).controlSize(.small)
            }
        }
    }
}

// MARK: - Visual preview

/// A miniature of what the scene will actually look like, drawn in SwiftUI.
/// It is an approximation of the rendered frame, not a render of it.
struct VisualPreview: View {
    let kind: VisualKind
    let ref: String
    var height: CGFloat = 82

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(LinearGradient(colors: [Color(hex: "#141a2e"), Color(hex: "#07080d")],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
            content.padding(9)
        }
        .frame(height: height)
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .strokeBorder(Color.white.opacity(0.10), lineWidth: 1)
        )
    }

    @ViewBuilder
    private var content: some View {
        switch kind {
        case .screenshot:
            if !ref.isEmpty, let img = NSImage(contentsOfFile: ref) {
                Image(nsImage: img).resizable().scaledToFill()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipped()
            } else {
                placeholder("photo", "No image chosen")
            }
        case .code:
            panel(title: ref.isEmpty ? "source.py" : (ref as NSString).lastPathComponent) {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(0..<4, id: \.self) { i in
                        HStack(spacing: 4) {
                            ForEach(0..<(3 + i % 3), id: \.self) { j in
                                Capsule()
                                    .fill(i == 1 && j == 0 ? Color(hex: "#c58bff")
                                          : j % 3 == 0 ? Color(hex: "#7cc7ff")
                                          : Color.white.opacity(0.28))
                                    .frame(width: CGFloat(14 + (j * 7 + i * 5) % 34), height: 4)
                            }
                        }
                    }
                }
            }
        case .tree:
            panel(title: "project/") {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(0..<5, id: \.self) { i in
                        HStack(spacing: 4) {
                            Image(systemName: i % 3 == 0 ? "folder" : "doc")
                                .font(.system(size: 6))
                                .foregroundStyle(Palette.accent.opacity(0.85))
                            Capsule().fill(Color.white.opacity(0.26))
                                .frame(width: CGFloat(30 + (i * 13) % 46), height: 4)
                        }
                        .padding(.leading, CGFloat(i % 3) * 10)
                    }
                }
            }
        case .stats:
            panel(title: "by the numbers") {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 12) {
                        bigStat("4.9k"); bigStat("13")
                    }
                    HStack(spacing: 12) {
                        bigStat("Python"); bigStat("3")
                    }
                }
            }
        case .stack:
            panel(title: "built with") {
                HStack(spacing: 5) {
                    ForEach(["voxcpm", "gradio", "torch"], id: \.self) { s in
                        Text(s).font(.system(size: 7, design: .monospaced))
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(Color.white.opacity(0.09), in: Capsule())
                            .foregroundStyle(.white.opacity(0.85))
                    }
                }
            }
        case .terminal:
            panel(title: "terminal") {
                HStack(spacing: 4) {
                    Text("$").font(.system(size: 9, weight: .bold, design: .monospaced))
                        .foregroundStyle(Palette.accent)
                    Text(ref.isEmpty ? "run the app" : ref)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.9)).lineLimit(1)
                }
            }
        case .diagram:
            panel(title: "how it flows") {
                HStack(spacing: 4) {
                    ForEach(0..<3, id: \.self) { i in
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Palette.accent.opacity(0.20))
                            .overlay(RoundedRectangle(cornerRadius: 3)
                                .strokeBorder(Palette.accent.opacity(0.6), lineWidth: 0.7))
                            .frame(width: 34, height: 12)
                        if i < 2 {
                            Image(systemName: "arrow.right")
                                .font(.system(size: 6)).foregroundStyle(Palette.accent.opacity(0.7))
                        }
                    }
                }
            }
        case .mesh, .auto:
            ZStack {
                Circle().fill(Palette.accent.opacity(0.5)).frame(width: 46)
                    .blur(radius: 12).offset(x: -16, y: -6)
                Circle().fill(Color(hex: "#39d3c0").opacity(0.4)).frame(width: 34)
                    .blur(radius: 12).offset(x: 18, y: 8)
                Text(kind == .auto ? "CodeReel picks" : "Abstract")
                    .font(.system(size: 8, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.75))
            }
        }
    }

    private func panel<C: View>(title: String, @ViewBuilder body: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 3) {
                ForEach(0..<3, id: \.self) { i in
                    Circle().fill([Color(hex: "#ff5f57"), Color(hex: "#febc2e"),
                                   Color(hex: "#28c840")][i])
                        .frame(width: 4, height: 4)
                }
                Text(title).font(.system(size: 6.5, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.45)).lineLimit(1)
            }
            body()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func bigStat(_ v: String) -> some View {
        Text(v).font(.system(size: 15, weight: .heavy)).foregroundStyle(.white.opacity(0.9))
    }

    private func placeholder(_ icon: String, _ text: String) -> some View {
        VStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 15)).foregroundStyle(.white.opacity(0.35))
            Text(text).font(.system(size: 8)).foregroundStyle(.white.opacity(0.45))
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Close beat

private struct CloseEditor: View {
    @Binding var close: CloseDraft

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "flag.checkered").foregroundStyle(Palette.accent)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Closing beat").font(.callout.weight(.semibold))
                    Text("The last thing the voice says, followed by an end card.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("", isOn: $close.enabled).labelsHidden().toggleStyle(.switch)
            }

            if close.enabled {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 11) {
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 5) {
                                FieldLabel(text: "Label")
                                TextField("Get it", text: $close.role)
                                    .textFieldStyle(.roundedBorder).frame(width: 130)
                            }
                            VStack(alignment: .leading, spacing: 5) {
                                FieldLabel(text: "Heading")
                                TextField("", text: $close.heading)
                                    .textFieldStyle(.roundedBorder)
                            }
                        }
                        VStack(alignment: .leading, spacing: 5) {
                            FieldLabel(text: "Narration")
                            TextField("", text: $close.text, axis: .vertical)
                                .textFieldStyle(.roundedBorder).lineLimit(2...4)
                        }
                        VStack(alignment: .leading, spacing: 5) {
                            FieldLabel(text: "End-card line", hint: "real proof — a licence, a URL, a price")
                            TextField("e.g. Apache-2.0 · no account, no API key", text: $close.cta)
                                .textFieldStyle(.roundedBorder)
                        }
                    }
                    VisualPreview(kind: VisualKind(rawValue: close.visual) ?? .stats, ref: "")
                        .frame(width: 200)
                }
            }
        }
        .padding(14)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.6),
                    in: RoundedRectangle(cornerRadius: 13, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .strokeBorder(Palette.accent.opacity(close.enabled ? 0.30 : 0.09), lineWidth: 1)
        )
    }
}

// MARK: - Paste

private struct PasteScriptSheet: View {
    let onSplit: ([String]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var buffer = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Paste your script").font(.headline)
            Text("A blank line starts a new scene.")
                .font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $buffer)
                .font(.system(.body, design: .monospaced))
                .frame(width: 560, height: 280)
                .padding(6)
                .background(Color(nsColor: .textBackgroundColor),
                            in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.primary.opacity(0.12), lineWidth: 1))
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Split into scenes") {
                    let blocks = buffer
                        .replacingOccurrences(of: "\r\n", with: "\n")
                        .components(separatedBy: "\n\n")
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                        .filter { !$0.isEmpty }
                    if !blocks.isEmpty { onSplit(blocks) }
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(buffer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
    }
}
