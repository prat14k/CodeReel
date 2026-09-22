"""Scene visuals: the thing on screen while the narration plays.

Every visual is generated as inline HTML/CSS plus GSAP statements, so a
composition needs no assets beyond the screenshots the repo already ships. That
keeps rendering hermetic — nothing to download, nothing to go missing.

Eight kinds, chosen by the script writer or filled in by `providers.enrich_visuals`:

    screenshot  a real image from the repo, with a slow Ken Burns push
    code        a source file as a highlighted code card
    tree        the project's file structure, revealed line by line
    stats       the repo's own numbers, big
    stack       the technologies it actually depends on
    terminal    a real command from the README or a manifest
    diagram     a flow of real components
    mesh        an abstract node graph — the cold open and the fallback

Pure stdlib. Returns markup; `demo.py` owns the timeline.
"""

from __future__ import annotations

import html
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import repocontext

MONO = '"SF Mono", ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace'

# Languages we have keyword tables for. Anything else still gets strings,
# numbers and comments coloured — just no keyword pass.
KEYWORDS: dict[str, str] = {
    "Python": "def class return if elif else for while import from as with try except "
              "finally raise yield lambda None True False and or not in is pass break "
              "continue global async await self match case",
    "JavaScript": "const let var function return if else for while import export from "
                  "default class new async await try catch finally throw typeof "
                  "instanceof null undefined true false this of in",
    "TypeScript": "const let var function return if else for while import export from "
                  "default class new async await try catch finally throw typeof "
                  "interface type enum implements extends public private readonly "
                  "null undefined true false this as of in",
    "Swift": "func let var return if else for while import struct class enum protocol "
             "extension guard defer try catch throw throws async await public private "
             "internal fileprivate static mutating init self nil true false some any "
             "switch case default where in as is",
    "Go": "func package import return if else for range var const type struct "
          "interface map chan go defer select switch case default break continue "
          "nil true false string int error",
    "Rust": "fn let mut return if else for while impl struct enum trait pub use mod "
            "match loop break continue const static async await move ref self Self "
            "Some None Ok Err true false where as dyn",
    "Shell": "if then else elif fi for while do done case esac function return export "
             "local set unset echo cd exit",
    "Ruby": "def end class module return if elsif else unless while until for do "
            "begin rescue ensure raise yield require attr_accessor self nil true false",
    "Java": "public private protected class interface extends implements static final "
            "void int long double float boolean String new return if else for while "
            "try catch finally throw throws import package this super null true false",
    "C": "int char float double void struct union enum typedef static const return if "
         "else for while do switch case break continue sizeof unsigned signed long "
         "short extern NULL",
    "C++": "int char float double void struct class enum typedef static const return "
           "if else for while do switch case break continue sizeof unsigned signed "
           "long short extern namespace using template public private protected "
           "virtual override new delete nullptr true false auto",
    "SQL": "SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER ON GROUP BY ORDER HAVING "
           "INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE INDEX AS AND OR NOT "
           "NULL PRIMARY KEY FOREIGN REFERENCES",
}
KEYWORDS["Objective-C"] = KEYWORDS["C"]
KEYWORDS["C#"] = KEYWORDS["Java"]
KEYWORDS["Kotlin"] = KEYWORDS["Swift"]
KEYWORDS["JSON"] = ""

# A few file types get a fixed accent colour for their icon chip.
EXT_COLOR = {
    ".py": "#4b8bbe", ".js": "#f7df1e", ".mjs": "#f7df1e", ".ts": "#3178c6",
    ".tsx": "#3178c6", ".jsx": "#61dafb", ".swift": "#f05138", ".go": "#00add8",
    ".rs": "#dea584", ".rb": "#cc342d", ".java": "#e76f00", ".kt": "#7f52ff",
    ".c": "#5b6ee1", ".h": "#5b6ee1", ".cpp": "#00599c", ".cs": "#68217a",
    ".sh": "#4eaa25", ".html": "#e34c26", ".css": "#2965f1", ".scss": "#c6538c",
    ".json": "#a6a6a6", ".yaml": "#cb171e", ".yml": "#cb171e", ".toml": "#9c4221",
    ".md": "#6a9fb5", ".sql": "#e38c00", ".php": "#777bb4", ".dart": "#0175c2",
    ".lua": "#000080", ".r": "#198ce7", ".plist": "#a6a6a6",
}


@dataclass
class Visual:
    kind: str
    layout: str                 # full | left | right
    html: str
    css: str
    anim: list[str] = field(default_factory=list)
    note: str = ""


# ------------------------------------------------------------------ highlight

def _token_re(lang: str) -> re.Pattern:
    kw = KEYWORDS.get(lang, "")
    parts = [
        ("com", r"#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/|--[^\n]*"),
        ("str", r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\\n]|\\.)*"'
                r"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`"),
        ("num", r"\b0[xX][0-9a-fA-F]+\b|\b\d+(?:\.\d+)?\b"),
    ]
    if kw.strip():
        words = "|".join(re.escape(w) for w in kw.split())
        parts.append(("kw", rf"\b(?:{words})\b"))
    parts.append(("fn", r"\b[A-Za-z_]\w*(?=\s*\()"))
    return re.compile("|".join(f"(?P<{n}>{p})" for n, p in parts))


_HL_CACHE: dict[str, re.Pattern] = {}


def highlight(code: str, lang: str = "") -> str:
    """Escape and colour a snippet. Unknown languages get strings/numbers only."""
    rx = _HL_CACHE.get(lang)
    if rx is None:
        rx = _HL_CACHE[lang] = _token_re(lang)
    out, pos = [], 0
    for m in rx.finditer(code):
        if m.start() > pos:
            out.append(html.escape(code[pos:m.start()]))
        kind = m.lastgroup or ""
        out.append(f'<span class="t-{kind}">{html.escape(m.group())}</span>')
        pos = m.end()
    if pos < len(code):
        out.append(html.escape(code[pos:]))
    return "".join(out)


# ------------------------------------------------------------------ shared css

def base_css(prefix: str, p: dict, scale: float) -> str:
    """Panel chrome shared by every generated visual."""
    s = scale
    return f"""
      #{prefix} .vpanel {{
        background: {p['surface']}; border: 1px solid {p['border']};
        border-radius: {int(20 * s)}px; backdrop-filter: blur(18px);
        box-shadow: 0 {int(30 * s)}px {int(90 * s)}px rgba(0,0,0,.5);
        overflow: hidden; position: relative; }}
      #{prefix} .vbar {{
        display: flex; align-items: center; gap: {int(9 * s)}px;
        padding: {int(13 * s)}px {int(18 * s)}px;
        border-bottom: 1px solid {p['border']};
        background: rgba(255,255,255,.035); }}
      #{prefix} .dot {{ width: {int(11 * s)}px; height: {int(11 * s)}px;
        border-radius: 50%; flex: none; }}
      #{prefix} .vtitle {{
        font-family: {MONO}; font-size: {int(21 * s)}px; color: {p['muted']};
        margin-left: {int(8 * s)}px; letter-spacing: .01em; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis; }}
      #{prefix} .vbody {{ padding: {int(26 * s)}px {int(30 * s)}px; }}
      #{prefix} .note {{
        margin-top: {int(16 * s)}px; font-size: {int(24 * s)}px; color: {p['muted']};
        letter-spacing: .01em; }}
    """


def code_css(prefix: str, p: dict, scale: float) -> str:
    s = scale
    return f"""
      #{prefix} .codewrap {{
        font-family: {MONO}; font-size: {int(22 * s)}px; line-height: 1.62;
        display: flex; gap: {int(18 * s)}px; }}
      #{prefix} .gutter {{
        color: {p['muted']}; opacity: .45; text-align: right; user-select: none;
        font-variant-numeric: tabular-nums; flex: none; white-space: pre; }}
      #{prefix} .code {{ white-space: pre; overflow: hidden; flex: 1;
        color: {p['code_text']}; }}
      #{prefix} .code .cl {{ display: block; }}
      #{prefix} .t-com {{ color: {p['code_com']}; font-style: italic; }}
      #{prefix} .t-str {{ color: {p['code_str']}; }}
      #{prefix} .t-num {{ color: {p['code_num']}; }}
      #{prefix} .t-kw  {{ color: {p['code_kw']}; font-weight: 600; }}
      #{prefix} .t-fn  {{ color: {p['code_fn']}; }}
    """


# ------------------------------------------------------------------ builders

def _shell(prefix: str, title: str, body: str, p: dict, note: str = "") -> str:
    dots = "".join(f'<span class="dot" style="background:{c}"></span>'
                   for c in (p.get("dot1", "#ff5f57"), p.get("dot2", "#febc2e"),
                             p.get("dot3", "#28c840")))
    cap = f'<div class="note">{html.escape(note)}</div>' if note else ""
    return (f'<div class="vpanel">'
            f'<div class="vbar">{dots}<span class="vtitle">{html.escape(title)}</span></div>'
            f'<div class="vbody">{body}</div></div>{cap}')


def _code(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
          prefix: str, start: float, dur: float, idx: int) -> Visual:
    rel = scene.get("visual_ref") or ""
    code, lang = repocontext.code_snippet(ctx, rel, max_lines=22) if ctx else ("", "")
    if not code:
        return _stats(scene, ctx, p, s, prefix, start, dur, idx)

    lines = code.split("\n")
    gutter = "\n".join(str(i + 1) for i in range(len(lines)))
    body = "\n".join(
        f'<span class="cl">{highlight(ln, lang) or "&nbsp;"}</span>' for ln in lines)
    title = Path(rel).name if rel else "source"
    inner = _shell(prefix, title, f'<div class="codewrap"><div class="gutter">{gutter}</div>'
                                  f'<div class="code">{body}</div></div>',
                   p, scene.get("visual_note", ""))
    anim = [
        f'tl.from("#{prefix} .cl", {{opacity:0, x:-10, duration:0.34, stagger:0.028, '
        f'ease:"power2.out"}}, {start + 0.18:.3f});',
        f'tl.from("#{prefix} .vbar", {{opacity:0, y:-12, duration:0.4}}, {start + 0.1:.3f});',
    ]
    return Visual("code", "right" if idx % 2 else "left", inner,
                  base_css(prefix, p, s) + code_css(prefix, p, s), anim)


def _tree(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
          prefix: str, start: float, dur: float, idx: int) -> Visual:
    raw = (ctx.tree if ctx else "") or ""
    lines = [ln for ln in raw.split("\n") if ln.strip()][:17]
    if not lines:
        return _mesh(scene, ctx, p, s, prefix, start, dur, idx)

    rows = []
    for ln in lines:
        depth = (len(ln) - len(ln.lstrip(" "))) // 2
        name = ln.strip()
        is_dir = name.endswith("/")
        ext = Path(name.rstrip("/")).suffix.lower()
        color = EXT_COLOR.get(ext, p["accent"]) if not is_dir else p["accent"]
        icon = "▸" if is_dir else "·"
        rows.append(
            f'<div class="trow" style="padding-left:{depth * 22}px">'
            f'<span class="ticon" style="color:{color}">{icon}</span>'
            f'<span class="tname{" dir" if is_dir else ""}">{html.escape(name)}</span></div>')

    body = f'<div class="tree">{"".join(rows)}</div>'
    inner = _shell(prefix, (ctx.name if ctx else "project") + "/", body, p,
                   scene.get("visual_note", ""))
    css = base_css(prefix, p, s) + f"""
      #{prefix} .tree {{ font-family: {MONO}; font-size: {int(22 * s)}px; line-height: 1.72; }}
      #{prefix} .trow {{ display: flex; gap: {int(12 * s)}px; align-items: baseline; }}
      #{prefix} .ticon {{ flex: none; width: {int(16 * s)}px; opacity: .9; }}
      #{prefix} .tname {{ color: {p['code_text']}; }}
      #{prefix} .tname.dir {{ color: {p['text']}; font-weight: 600; }}
    """
    anim = [
        f'tl.from("#{prefix} .trow", {{opacity:0, x:-16, duration:0.3, stagger:0.045, '
        f'ease:"power2.out"}}, {start + 0.18:.3f});',
    ]
    return Visual("tree", "left" if idx % 2 else "right", inner, css, anim)


def _stats(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
           prefix: str, start: float, dur: float, idx: int) -> Visual:
    stats = []
    if ctx:
        stats = ctx.stats()
        langs = sorted(ctx.languages.items(), key=lambda kv: -kv[1])
        if len(langs) > 1:
            stats.append({"value": str(len(langs)), "label": "languages"})
        if ctx.images:
            stats.append({"value": str(len(ctx.images)), "label": "images"})
    stats = stats[:4] or [{"value": "—", "label": "no data"}]

    cells = "".join(
        f'<div class="scell"><div class="sval">{html.escape(st["value"])}</div>'
        f'<div class="slab">{html.escape(st["label"])}</div></div>' for st in stats)
    body = f'<div class="stats">{cells}</div>'
    inner = _shell(prefix, "by the numbers", body, p, scene.get("visual_note", ""))
    css = base_css(prefix, p, s) + f"""
      #{prefix} .stats {{ display: grid; grid-template-columns: repeat(2, 1fr);
        gap: {int(26 * s)}px {int(30 * s)}px; }}
      #{prefix} .sval {{ font-size: {int(72 * s)}px; font-weight: 800; letter-spacing: -.03em;
        color: {p['text']}; line-height: 1; }}
      #{prefix} .slab {{ font-size: {int(22 * s)}px; color: {p['muted']};
        margin-top: {int(8 * s)}px; letter-spacing: .04em; }}
    """
    anim = [
        f'tl.from("#{prefix} .scell", {{opacity:0, y:26, scale:0.9, duration:0.55, '
        f'stagger:0.12, ease:"back.out(1.6)"}}, {start + 0.2:.3f});',
    ]
    return Visual("stats", "left" if idx % 2 else "right", inner, css, anim)


def _stack(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
           prefix: str, start: float, dur: float, idx: int) -> Visual:
    items = repocontext.tech_stack(ctx) if ctx else []
    if not items:
        return _stats(scene, ctx, p, s, prefix, start, dur, idx)
    pills = "".join(f'<span class="pill">{html.escape(i)}</span>' for i in items[:12])
    body = f'<div class="pills">{pills}</div>'
    inner = _shell(prefix, "built with", body, p, scene.get("visual_note", ""))
    css = base_css(prefix, p, s) + f"""
      #{prefix} .pills {{ display: flex; flex-wrap: wrap; gap: {int(14 * s)}px; }}
      #{prefix} .pill {{
        font-family: {MONO}; font-size: {int(23 * s)}px; color: {p['text']};
        padding: {int(10 * s)}px {int(20 * s)}px; border-radius: 99px;
        border: 1px solid {p['border']}; background: rgba(255,255,255,.05); }}
    """
    anim = [
        f'tl.from("#{prefix} .pill", {{opacity:0, scale:0.75, duration:0.4, stagger:0.06, '
        f'ease:"back.out(2)"}}, {start + 0.2:.3f});',
    ]
    return Visual("stack", "left" if idx % 2 else "right", inner, css, anim)


def _terminal(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
              prefix: str, start: float, dur: float, idx: int) -> Visual:
    from providers import _guess_commands

    cmds: list[str] = []
    ref = (scene.get("visual_ref") or "").strip()
    real = _guess_commands(ctx, 3) if ctx else []
    if len(real) >= 2:
        cmds = real            # the README's own sequence reads as one session
    elif ref:
        cmds = [ref] + [c for c in real if c != ref]
    else:
        cmds = real
    cmds = cmds[:3]
    if not cmds:
        return _stats(scene, ctx, p, s, prefix, start, dur, idx)

    rows = []
    for i, cmd in enumerate(cmds):
        caret = '<span class="caret"></span>' if i == len(cmds) - 1 else ""
        rows.append(f'<div class="tline"><span class="prompt">$</span>'
                    f'<span class="cmd">{highlight(cmd, "Shell")}</span>{caret}</div>')

    # A 74-character install line does not fit a split-layout card at full size, and
    # an ellipsis mid-command reads as a bug. Step the whole card down instead, so
    # every line stays legible and nothing is cut.
    longest = max(len(c) for c in cmds)
    fs = 25.0 if longest <= 44 else max(13.0, 25.0 * 44.0 / longest)

    body = f'<div class="term">{"".join(rows)}</div>'
    inner = _shell(prefix, "terminal", body, p, scene.get("visual_note", ""))
    css = base_css(prefix, p, s) + f"""
      #{prefix} .term {{ font-family: {MONO}; font-size: {int(fs * s)}px;
        color: {p['code_text']}; display: flex; flex-direction: column;
        gap: {int(15 * s)}px; }}
      #{prefix} .tline {{ display: flex; gap: {int(14 * s)}px; align-items: center;
        white-space: nowrap; overflow: hidden; }}
      #{prefix} .cmd {{ overflow: hidden; text-overflow: ellipsis; }}
      #{prefix} .prompt {{ color: {p['accent']}; font-weight: 700; flex: none; }}
      #{prefix} .caret {{ display: inline-block; width: {int(fs * 0.5 * s)}px;
        height: {int(fs * 1.15 * s)}px; background: {p['accent']}; flex: none; }}
    """
    blinks = max(2, int(max(dur - 0.6, 0.4) / 0.9))
    anim = [
        f'tl.from("#{prefix} .tline", {{opacity:0, x:-14, duration:0.32, stagger:0.42, '
        f'ease:"power2.out"}}, {start + 0.2:.3f});',
        f'tl.to("#{prefix} .caret", {{opacity:0, duration:0.45, repeat:{blinks - 1}, '
        f'yoyo:true, ease:"steps(1)"}}, {start + 0.5:.3f});',
    ]
    return Visual("terminal", "left" if idx % 2 else "right", inner, css, anim)


def _diagram(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
             prefix: str, start: float, dur: float, idx: int) -> Visual:
    nodes = scene.get("bullets") or []
    if not nodes and ctx:
        nodes = [Path(f.path).name for f in ctx.files if f.kind == "entry"][:3]
    nodes = [str(n)[:22] for n in nodes][:4] or ["input", "process", "output"]

    rows = []
    for i, n in enumerate(nodes):
        rows.append(f'<div class="node">{html.escape(n)}</div>')
        if i < len(nodes) - 1:
            rows.append('<div class="arrow">↓</div>')
    body = f'<div class="flow">{"".join(rows)}</div>'
    inner = _shell(prefix, "how it flows", body, p, scene.get("visual_note", ""))
    css = base_css(prefix, p, s) + f"""
      #{prefix} .flow {{ display: flex; flex-direction: column; align-items: center;
        gap: {int(10 * s)}px; }}
      #{prefix} .node {{
        font-family: {MONO}; font-size: {int(23 * s)}px; color: {p['text']};
        border: 1px solid {p['accent']}; border-radius: {int(12 * s)}px;
        padding: {int(12 * s)}px {int(26 * s)}px; background: rgba(255,255,255,.04);
        min-width: {int(260 * s)}px; text-align: center; }}
      #{prefix} .arrow {{ color: {p['accent']}; font-size: {int(22 * s)}px; opacity: .8; }}
    """
    anim = [
        f'tl.from("#{prefix} .node", {{opacity:0, y:20, duration:0.45, stagger:0.16, '
        f'ease:"power3.out"}}, {start + 0.2:.3f});',
        f'tl.from("#{prefix} .arrow", {{opacity:0, duration:0.25, stagger:0.16}}, '
        f'{start + 0.32:.3f});',
    ]
    return Visual("diagram", "left" if idx % 2 else "right", inner, css, anim)


def _mesh(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
          prefix: str, start: float, dur: float, idx: int) -> Visual:
    """An abstract node graph — a system coming alive.

    This is the visual for the cold open, so it has to carry a frame on its own.
    Points sit on a jittered grid, each wired to its two nearest neighbours; the
    nodes fade up, the edges draw in, and pulses travel the wires for as long as
    the beat lasts.
    """
    rng = random.Random(11)
    cols, rows = 7, 4
    pts: list[tuple[float, float]] = []
    for r in range(rows):
        for c in range(cols):
            pts.append((9.0 + c * 13.7 + rng.uniform(-3.6, 3.6),
                        14.0 + r * 21.0 + rng.uniform(-4.4, 4.4)))

    edges: list[tuple[int, int]] = []
    for i in range(len(pts)):
        near = sorted((math.dist(pts[i], pts[j]), j)
                      for j in range(len(pts)) if j != i)[:2]
        for dist, j in near:
            if dist < 27 and (i, j) not in edges and (j, i) not in edges:
                edges.append((i, j))

    accent2 = p.get("accent2", p["accent"])
    wires = "".join(
        f'<line class="medge" x1="{pts[a][0]:.2f}" y1="{pts[a][1]:.2f}" '
        f'x2="{pts[b][0]:.2f}" y2="{pts[b][1]:.2f}"/>' for a, b in edges)
    dots = "".join(
        f'<circle class="mnode" cx="{x:.2f}" cy="{y:.2f}" r="{1.75 if i % 6 == 0 else 1.05:.2f}" '
        f'fill="{p["accent"] if i % 6 == 0 else (accent2 if i % 3 else p["text"])}"/>'
        for i, (x, y) in enumerate(pts))

    # A few pulses ride random wires, staggered so they never march in step. The
    # repeat count is finite — an infinite loop makes the composition non-seekable.
    pulses = ""
    anim: list[str] = []
    picks = rng.sample(edges, min(7, len(edges)))
    for i, (a, b) in enumerate(picks):
        (x1, y1), (x2, y2) = pts[a], pts[b]
        pulses += (f'<circle class="mpulse" cx="{x1:.2f}" cy="{y1:.2f}" r="0.62" '
                   f'fill="{p["text"]}"/>')
        period = 1.5 + i * 0.22
        gap = 0.5 + i * 0.31
        begin = start + 0.9 + i * 0.18
        cycles = max(1, int(max(dur - (begin - start), 0.4) / (period + gap)))
        anim.append(
            f'tl.fromTo("#{prefix} .mpulse:nth-child({i + 1})", '
            f'{{attr:{{cx:{x1:.2f}, cy:{y1:.2f}}}, opacity:0.95}}, '
            f'{{attr:{{cx:{x2:.2f}, cy:{y2:.2f}}}, duration:{period:.2f}, '
            f'ease:"none", repeat:{cycles - 1}, repeatDelay:{gap:.2f}}}, '
            f'{begin:.3f});')

    inner = (
        f'<div class="meshwrap">'
        f'<svg class="meshg" viewBox="0 0 100 88" preserveAspectRatio="xMidYMid slice">'
        f'<g class="mwires">{wires}</g>'
        f'<g class="mnodes">{dots}</g>'
        f'<g class="mpulses">{pulses}</g>'
        f'</svg></div>'
    )
    css = f"""
      #{prefix} .meshwrap {{ position: absolute; inset: 0; overflow: hidden; }}
      #{prefix} .meshg {{ width: 100%; height: 100%; display: block;
        transform-box: view-box; transform-origin: 50% 50%; opacity: .9; }}
      #{prefix} .medge {{ stroke: {p['accent']}; stroke-width: 0.16; opacity: .30; }}
      #{prefix} .mnode {{ opacity: .8; }}
      #{prefix} .mpulse {{ opacity: 0; }}
      #{prefix} .meshwrap::after {{ content: ""; position: absolute; inset: 0;
        background: radial-gradient(58% 62% at 50% 52%, {p['bg']} 0%, transparent 74%);
        opacity: .72; pointer-events: none; }}
    """
    anim = [
        f'tl.from("#{prefix} .medge", {{opacity:0, duration:0.7, stagger:0.013, '
        f'ease:"power2.out"}}, {start + 0.05:.3f});',
        f'tl.from("#{prefix} .mnode", {{opacity:0, duration:0.55, stagger:0.022, '
        f'ease:"power2.out"}}, {start + 0.25:.3f});',
        f'tl.to("#{prefix} .meshg", {{scale:1.07, duration:{max(dur, 4):.2f}, '
        f'ease:"sine.inOut"}}, {start:.3f});',
    ] + anim
    return Visual("mesh", "full", inner, css, anim)


def _screenshot(scene: dict, ctx: repocontext.RepoContext | None, p: dict, s: float,
                prefix: str, start: float, dur: float, idx: int) -> Visual:
    rel = scene.get("visual_ref") or ""
    if not rel:
        return _mesh(scene, ctx, p, s, prefix, start, dur, idx)
    # media/sceneN.<ext> is how demo.write_project copies it in.
    ext = Path(rel).suffix.lower()
    src = f"media/scene{idx}{ext}"
    direction = 1 if idx % 2 == 0 else -1
    inner = f'<div class="shotframe"><img class="shot" src="{src}" alt="" /></div>'
    # A screenshot sits in its own card beside the words rather than under them:
    # full-bleed `cover` crops wide captures to a sliver and puts the heading on
    # top of whatever survives.
    css = f"""
      #{prefix} .shotframe {{ position: relative; width: 100%; overflow: hidden;
        border-radius: {int(22 * s)}px; background: {p['surface']};
        border: {max(1, int(2 * s))}px solid {p['border']};
        box-shadow: 0 {int(28 * s)}px {int(70 * s)}px rgba(0,0,0,0.38); }}
      #{prefix} .shot {{ display: block; width: 100%; height: auto;
        max-height: {int(660 * s)}px; object-fit: contain; }}
    """
    # ponytail: the card drifts instead of the image, so the push never crops it.
    anim = [
        f'tl.fromTo("#{prefix} .shotframe", '
        f'{{scale:1, xPercent:{-0.6 * direction}}}, '
        f'{{scale:1.035, xPercent:{0.6 * direction}, '
        f'duration:{max(dur, 2.5):.2f}, ease:"none"}}, {start:.3f});',
    ]
    return Visual("screenshot", "split", inner, css, anim)


BUILDERS = {
    "screenshot": _screenshot,
    "code": _code,
    "tree": _tree,
    "stats": _stats,
    "stack": _stack,
    "terminal": _terminal,
    "diagram": _diagram,
    "mesh": _mesh,
}


def build(scene: dict, ctx: repocontext.RepoContext | None, palette: dict,
          scale: float, idx: int, start: float, duration: float) -> Visual:
    """Pick and render the visual for one scene."""
    prefix = f"v{idx}"
    kind = scene.get("visual") or "mesh"
    fn = BUILDERS.get(kind, _mesh)
    try:
        v = fn(scene, ctx, palette, scale, prefix, start, duration, idx)
    except Exception:
        v = _mesh(scene, ctx, palette, scale, prefix, start, duration, idx)
        v.kind = "mesh"
    # Layout: a screenshot is the frame; a panel sits beside the words.
    if v.layout != "full":
        v.layout = "left" if idx % 2 == 0 else "right"
    v.html = f'<div id="{prefix}" class="vstage {v.layout}">{v.html}</div>'
    return v
