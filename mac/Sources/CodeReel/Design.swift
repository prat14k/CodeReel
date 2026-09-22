import AppKit
import SwiftUI

// MARK: - Tokens

extension Color {
    init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("#") { s.removeFirst() }
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        let r, g, b, a: Double
        if s.count == 8 {
            r = Double((v >> 24) & 0xFF) / 255
            g = Double((v >> 16) & 0xFF) / 255
            b = Double((v >> 8) & 0xFF) / 255
            a = Double(v & 0xFF) / 255
        } else {
            r = Double((v >> 16) & 0xFF) / 255
            g = Double((v >> 8) & 0xFF) / 255
            b = Double(v & 0xFF) / 255
            a = 1
        }
        self.init(.sRGB, red: r, green: g, blue: b, opacity: a)
    }
}

enum Palette {
    static let accent = Color(hex: "#7c93ff")
    static let accentSoft = Color(hex: "#7c93ff").opacity(0.14)
    static let warm = Color(hex: "#ff8a3d")
    static let good = Color(hex: "#3ddc97")
}

// MARK: - Building blocks

struct Card<Content: View>: View {
    var padding: CGFloat
    private let content: Content

    init(padding: CGFloat = 18, @ViewBuilder content: () -> Content) {
        self.padding = padding
        self.content = content()
    }

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.6),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
            )
    }
}

struct SectionTitle: View {
    let title: String
    var subtitle: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.headline)
            if let subtitle {
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct Chip: View {
    let text: String
    var color: Color = Palette.accent
    var filled = false

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .tracking(0.8)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(filled ? color.opacity(0.22) : Color.clear, in: Capsule())
            .overlay(Capsule().strokeBorder(color.opacity(filled ? 0 : 0.55), lineWidth: 1))
            .foregroundStyle(color)
    }
}

struct FieldLabel: View {
    let text: String
    var hint: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(text).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if let hint {
                Text(hint).font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }
}

/// A tappable row that behaves like a radio button but looks like a card.
struct SelectCard<Content: View>: View {
    let selected: Bool
    var action: () -> Void
    private let content: Content

    init(selected: Bool, action: @escaping () -> Void, @ViewBuilder content: () -> Content) {
        self.selected = selected
        self.action = action
        self.content = content()
    }

    var body: some View {
        Button(action: action) {
            content
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(selected ? Palette.accentSoft : Color.primary.opacity(0.035),
                            in: RoundedRectangle(cornerRadius: 11, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 11, style: .continuous)
                        .strokeBorder(selected ? Palette.accent : Color.primary.opacity(0.10),
                                      lineWidth: selected ? 1.5 : 1)
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Horizontal step indicator for the create flow.
struct StepBar: View {
    @Binding var step: CreateStep
    var canJumpToScript: Bool

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(CreateStep.allCases.enumerated()), id: \.element) { index, item in
                let enabled = item == .source || canJumpToScript
                Button {
                    if enabled { withAnimation(.easeInOut(duration: 0.18)) { step = item } }
                } label: {
                    HStack(spacing: 9) {
                        ZStack {
                            Circle()
                                .fill(step == item ? Palette.accent
                                      : (item.rawValue < step.rawValue ? Palette.accent.opacity(0.22)
                                         : Color.primary.opacity(0.09)))
                                .frame(width: 22, height: 22)
                            if item.rawValue < step.rawValue {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundStyle(Palette.accent)
                            } else {
                                Text("\(item.rawValue + 1)")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(step == item ? .white : .secondary)
                            }
                        }
                        Text(item.title)
                            .font(.callout.weight(step == item ? .semibold : .regular))
                            .foregroundStyle(step == item ? .primary : .secondary)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 7)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!enabled)
                .opacity(enabled ? 1 : 0.45)

                if index < CreateStep.allCases.count - 1 {
                    Rectangle()
                        .fill(Color.primary.opacity(0.10))
                        .frame(height: 1)
                        .frame(maxWidth: 34)
                }
            }
            Spacer()
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 12)
    }
}

/// An inline message with an icon — used for empty states and warnings.
struct Notice: View {
    let icon: String
    let title: String
    var detail: String?
    var tint: Color = .secondary

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: icon)
                .font(.system(size: 15))
                .foregroundStyle(tint)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.medium))
                if let detail {
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

extension View {
    func card(_ padding: CGFloat = 18) -> some View {
        Card(padding: padding) { self }
    }

    /// A soft tinted panel, for the step currently in focus.
    func panel(_ tint: Color = Palette.accent, radius: CGFloat = 13) -> some View {
        padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(tint.opacity(0.055), in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(tint.opacity(0.20), lineWidth: 1)
            )
    }
}

// MARK: - Visual kinds

enum RefKind { case none, image, file, command }

enum VisualKind: String, CaseIterable, Identifiable {
    case auto = ""
    case screenshot, code, tree, stats, stack, terminal, diagram, mesh

    var id: String { rawValue }

    static var choosable: [VisualKind] { [.auto] + allCases.filter { $0 != .auto } }

    var label: String {
        switch self {
        case .auto: return "Auto"
        case .screenshot: return "Screenshot"
        case .code: return "Code"
        case .tree: return "File tree"
        case .stats: return "Numbers"
        case .stack: return "Tech stack"
        case .terminal: return "Terminal"
        case .diagram: return "Flow"
        case .mesh: return "Abstract"
        }
    }

    var icon: String {
        switch self {
        case .auto: return "sparkles"
        case .screenshot: return "photo"
        case .code: return "chevron.left.forwardslash.chevron.right"
        case .tree: return "folder"
        case .stats: return "chart.bar"
        case .stack: return "square.stack.3d.up"
        case .terminal: return "terminal"
        case .diagram: return "arrow.triangle.branch"
        case .mesh: return "circle.hexagongrid"
        }
    }

    var blurb: String {
        switch self {
        case .auto: return "Let CodeReel choose from what the repo has."
        case .screenshot: return "A real screenshot from the repo, with a slow push."
        case .code: return "A source file as a highlighted code card."
        case .tree: return "The project's file structure, revealed line by line."
        case .stats: return "The repo's own numbers, big."
        case .stack: return "The technologies it actually depends on."
        case .terminal: return "A real command from the README or a manifest."
        case .diagram: return "A simple flow of real components."
        case .mesh: return "Abstract motion background. Use it sparingly."
        }
    }

    var refKind: RefKind {
        switch self {
        case .screenshot: return .image
        case .code: return .file
        case .terminal: return .command
        default: return .none
        }
    }
}

// MARK: - Drafts

struct SceneDraft: Identifiable, Equatable {
    var id = UUID()
    var role = ""
    var heading = ""
    var text = ""
    var visual = ""
    var visualRef = ""
    var visualNote = ""
    var media = ""
    var bullets: [String] = []

    var kind: VisualKind { VisualKind(rawValue: visual) ?? .auto }
    var hasNarration: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}

struct CloseDraft: Equatable {
    var enabled = false
    var role = "Get it"
    var heading = "Get started"
    var text = ""
    var cta = ""
    var visual = "stats"
}

enum CreateStep: Int, CaseIterable, Identifiable {
    case source, script, look, render
    var id: Int { rawValue }
    var title: String {
        switch self {
        case .source: return "Source"
        case .script: return "Script"
        case .look: return "Look"
        case .render: return "Render"
        }
    }
}

// MARK: - Flow state

@Observable
final class CreateStore {
    var step: CreateStep = .source

    // source
    var repoPath = ""
    var angle = ""
    var sceneCount = 6
    var analysing = false
    var analyseProgress = 0.0
    var analyseStage = ""
    var analyseCost: Double?
    var providerLabel = ""

    // script
    var title = ""
    var subtitle = ""
    var hook = ""
    var logo = ""
    var scenes: [SceneDraft] = []
    var close = CloseDraft()

    // look
    var voiceID = "aria"
    var theme = "midnight"
    var aspect = "landscape"
    var sfx = true
    var style = ""
    var cfg = 2.0
    var timesteps = 12.0
    var seed = 42

    // render
    var busy = false
    var progress = 0.0
    var stage = ""
    var result: DemoResult?

    var hasScript: Bool { scenes.contains(where: \.hasNarration) }
    var canAnalyse: Bool {
        !analysing && !repoPath.trimmingCharacters(in: .whitespaces).isEmpty
    }
    var canRender: Bool {
        !busy && !title.trimmingCharacters(in: .whitespaces).isEmpty && hasScript
    }
    var narrationSeconds: Double {
        // ~2.6 words a second is close enough for a running estimate.
        var words = 0
        for scene in scenes where scene.hasNarration {
            words += scene.text.split(separator: " ").count
        }
        if close.enabled {
            words += close.text.split(separator: " ").count
        }
        if !hook.isEmpty {
            words += hook.split(separator: " ").count
        }
        return Double(words) / 2.6
    }

    func apply(_ result: AnalyzeResult) {
        title = result.title
        subtitle = result.subtitle
        hook = result.hook
        logo = result.logo
        providerLabel = result.provider
        scenes = result.scenes.map {
            SceneDraft(role: $0.role, heading: $0.heading, text: $0.text,
                       visual: $0.visual, visualRef: $0.visual_ref,
                       visualNote: $0.visual_note, media: $0.media)
        }
        if let c = result.close, !c.text.isEmpty {
            close = CloseDraft(enabled: true, role: "Get it",
                               heading: c.heading.isEmpty ? "Get started" : c.heading,
                               text: c.text, cta: c.cta, visual: "stats")
        }
        analyseCost = result.cost_usd
    }

    func reset() {
        step = .source
        title = ""; subtitle = ""; hook = ""; logo = ""
        scenes = []; close = CloseDraft()
        busy = false; progress = 0; stage = ""; result = nil
        analyseCost = nil; analyseStage = ""
    }

    func body(for draft: SceneDraft) -> [String: Any] {
        ["text": draft.text, "heading": draft.heading, "role": draft.role,
         "media": draft.media, "visual": draft.visual, "visual_ref": draft.visualRef,
         "visual_note": draft.visualNote, "bullets": draft.bullets]
    }

    func request() -> [String: Any] {
        var body: [String: Any] = [
            "title": title, "subtitle": subtitle, "logo": logo, "voice_id": voiceID,
            "hook": hook, "theme": theme, "aspect": aspect, "style": style,
            "cfg": cfg, "timesteps": Int(timesteps), "seed": seed,
            "sfx": sfx, "repo_path": repoPath,
            "scenes": scenes.filter(\.hasNarration).map(self.body),
        ]
        if close.enabled, !close.text.isEmpty {
            body["close"] = ["text": close.text, "heading": close.heading,
                             "role": close.role, "cta": close.cta,
                             "visual": close.visual, "visual_ref": "", "visual_note": ""]
        }
        return body
    }
}
