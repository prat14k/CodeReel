"""Build a HyperFrames composition from narrated beats and render it to MP4.

The video is assembled as a sequence of beats, in this order:

    hook  →  title card  →  scenes…  →  close  →  end card

Each scene carries a *visual* — a real screenshot with a slow Ken Burns push, a
highlighted code card, a file tree, big numbers, the tech stack, a terminal, a
flow diagram, or an abstract mesh. `visuals.py` builds them as inline HTML, so a
composition never needs an asset it does not already have.

Pure stdlib + ffprobe + the `hyperframes` CLI (npx). No torch import, so this
module can be self-checked without loading the TTS model:

    python demo.py --selfcheck
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import visuals

HF_VERSION = os.environ.get("HYPERFRAMES_VERSION", "0.8.33")

TITLE_CARD_SECONDS = 2.4
SCENE_TAIL_SECONDS = 0.5   # breathing room after each narration line
END_CARD_SECONDS = 3.2
HOOK_FALLBACK_SECONDS = 2.6

ASPECTS = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}

# Every theme carries a full palette, because visuals.py draws panels, code and
# captions from it — not just a background and an accent.
THEMES: dict[str, dict] = {
    "midnight": {
        "label": "Midnight", "dark": True,
        "bg": "#07080d", "bg2": "#141a2e", "accent": "#7c93ff", "accent2": "#39d3c0",
        "text": "#f2f4ff", "muted": "#98a2c6",
        "surface": "rgba(255,255,255,0.055)", "border": "rgba(255,255,255,0.12)",
        "caption_bg": "rgba(8,10,18,0.86)", "scrim": "rgba(0,0,0,0.72)",
        "code_text": "#c9d3ff", "code_com": "#5b6b95", "code_str": "#9be8a0",
        "code_num": "#ffc46b", "code_kw": "#c58bff", "code_fn": "#7cc7ff",
    },
    "ember": {
        "label": "Ember", "dark": True,
        "bg": "#120806", "bg2": "#33150c", "accent": "#ff8a3d", "accent2": "#ffd166",
        "text": "#fff4ec", "muted": "#d8b3a0",
        "surface": "rgba(255,255,255,0.06)", "border": "rgba(255,180,120,0.20)",
        "caption_bg": "rgba(24,10,6,0.86)", "scrim": "rgba(0,0,0,0.74)",
        "code_text": "#ffd9c2", "code_com": "#8a5a44", "code_str": "#a8e6a0",
        "code_num": "#ffd166", "code_kw": "#ff9f68", "code_fn": "#7fd8ff",
    },
    "neon": {
        "label": "Neon", "dark": True,
        "bg": "#0a0016", "bg2": "#2b0546", "accent": "#26f5c8", "accent2": "#ff4fd8",
        "text": "#ffffff", "muted": "#b39ad6",
        "surface": "rgba(255,255,255,0.06)", "border": "rgba(38,245,200,0.24)",
        "caption_bg": "rgba(12,0,26,0.84)", "scrim": "rgba(4,0,12,0.72)",
        "code_text": "#d9fff6", "code_com": "#5f7a8a", "code_str": "#a0ffd0",
        "code_num": "#ffe066", "code_kw": "#26f5c8", "code_fn": "#ff4fd8",
    },
    "studio": {
        "label": "Studio", "dark": False,
        "bg": "#f7f5f0", "bg2": "#e8e2d6", "accent": "#b4552d", "accent2": "#2f6f6b",
        "text": "#1b1a17", "muted": "#6f6858",
        "surface": "rgba(255,255,255,0.72)", "border": "rgba(27,26,23,0.13)",
        "caption_bg": "rgba(255,255,255,0.93)", "scrim": "rgba(20,18,14,0.55)",
        "code_text": "#2a2822", "code_com": "#8a8272", "code_str": "#2f6f3f",
        "code_num": "#9a5b12", "code_kw": "#8a3ea8", "code_fn": "#1f5f8a",
    },
}

VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}


@dataclass
class Scene:
    text: str
    heading: str = ""
    role: str = ""          # on-screen label: "The problem", "How it works", …
    media: str = ""         # legacy direct image/video path
    audio: str = ""         # narration wav produced by the TTS side
    duration: float = 0.0   # narration length in seconds
    start: float = 0.0
    caption_chunks: list = field(default_factory=list)
    # richer visual description, resolved by visuals.build
    visual: str = ""
    visual_ref: str = ""
    visual_note: str = ""
    bullets: list = field(default_factory=list)
    kind: str = "scene"     # scene | hook | close
    cta: str = ""           # short line of real proof on the end card
    ctx: object = field(default=None, repr=False, compare=False)  # repocontext.RepoContext


# ------------------------------------------------------------------ helpers

def audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def split_scenes(script: str) -> list[str]:
    """Blank line separates scenes; a lone scene is split on sentence runs."""
    blocks = [re.sub(r"\s+", " ", b).strip() for b in re.split(r"\n\s*\n", script or "")]
    blocks = [b for b in blocks if b]
    if len(blocks) > 1:
        return blocks
    if not blocks:
        return []
    sentences = re.findall(r"[^.!?]+[.!?]*", blocks[0])
    sentences = [s.strip() for s in sentences if s.strip()]
    # group into ~2-sentence scenes so a single paragraph still gets a timeline
    return [" ".join(sentences[i:i + 2]) for i in range(0, len(sentences), 2)] or blocks


_DANGLING = {"a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for",
             "with", "into", "that", "is", "are", "as", "at", "by", "from"}


def initials(title: str) -> str:
    """Monogram for repos that ship no logo — a wordmark still reads as branding."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", title) if w]
    if not words:
        return "•"
    return (words[0][:2] if len(words) == 1 else words[0][0] + words[1][0]).upper()


def auto_heading(text: str) -> str:
    """First clause of the narration, trimmed so it doesn't end on a dangling word."""
    head = re.split(r"[,;:—]|\.\s", text, maxsplit=1)[0]
    words = head.split()[:8]
    while len(words) > 3 and words[-1].lower().strip(".,") in _DANGLING:
        words.pop()
    head = " ".join(words).rstrip(".,;:")
    return head if len(head) <= 64 else head[:61].rstrip() + "…"


def caption_chunks(text: str, duration: float, max_words: int = 7) -> list[tuple[float, float, str]]:
    """Split narration into on-screen phrases, timed by character weight.

    ponytail: character-proportional timing, not real alignment. Good enough for
    7-word chunks; install whisper-cpp and use word timings if you need frame-exact
    karaoke captions.
    """
    words = text.split()
    if not words or duration <= 0:
        return []
    chunks = [words[i:i + max_words] for i in range(0, len(words), max_words)]
    weights = [sum(len(w) + 1 for w in c) for c in chunks]
    total = sum(weights) or 1
    out, t = [], 0.0
    for chunk, w in zip(chunks, weights):
        dur = duration * w / total
        out.append((round(t, 3), round(dur, 3), " ".join(chunk)))
        t += dur
    return out


def heading_size(heading: str) -> int:
    """Long headings step down so they never wrap into a wall of text."""
    n = len(heading or "")
    if n <= 34:
        return 88
    if n <= 52:
        return 74
    if n <= 72:
        return 62
    return 52


def _esc(t: str) -> str:
    return html.escape(str(t or ""))


def _kinetic(text: str) -> str:
    """Wrap every word so the caption can reveal them one at a time."""
    return " ".join(f'<span class="w">{_esc(w)}</span>' for w in (text or "").split())


# ------------------------------------------------------------------ sfx

SFX_RECIPES = {
    # name: (ffmpeg -af filter chain, duration)
    "whoosh": ("highpass=f=260,lowpass=f=2600,afade=t=in:d=0.06,"
               "afade=t=out:st=0.30:d=0.30,volume=0.16", 0.62),
    "rise": ("highpass=f=180,lowpass=f=4200,afade=t=in:d=0.10,"
             "afade=t=out:st=0.55:d=0.55,volume=0.20", 1.15),
}


def make_sfx(dest: Path) -> dict[str, Path]:
    """Synthesize the transition sounds locally — no bundled audio, no licence."""
    dest.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}
    for name, (filters, dur) in SFX_RECIPES.items():
        out = dest / f"{name}.wav"
        if out.exists():
            made[name] = out
            continue
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"anoisesrc=d={dur}:c=pink:a=0.5:r=48000",
                 "-af", filters, "-ac", "1", "-ar", "48000", str(out)],
                check=True, capture_output=True)
            made[name] = out
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass                       # no ffmpeg, or it refused — the video is fine without
    return made


# ------------------------------------------------------------------ composition

def build_composition(
    title: str,
    subtitle: str,
    scenes: list[Scene],
    theme: str = "midnight",
    aspect: str = "landscape",
    logo: str = "",
    hook: Scene | None = None,
    close: Scene | None = None,
    sfx: dict[str, Path] | None = None,
    repo: dict | None = None,
) -> str:
    w, h = ASPECTS.get(aspect, ASPECTS["landscape"])
    p = dict(THEMES.get(theme, THEMES["midnight"]))
    p.setdefault("accent2", p["accent"])
    scale = min(w, h) / 1080
    portrait = h > w
    s = scale

    total = _total_seconds(hook, scenes, close)

    def brand_mark(ident: str, start: float, dur: float) -> str:
        """The logo lands in three places. Each copy needs its own timing and id:
        three <img> tags with identical empty timing read to the renderer as one
        media node discovered twice, and an untargeted one cannot be edited in
        HyperFrames Studio.
        """
        if not logo:
            return f'<span class="mark-mono" id="{ident}">{_esc(initials(title))}</span>'
        return (f'<img class="mark-img" id="{ident}" src="{logo}" alt="" '
                f'data-start="{start:.3f}" data-duration="{dur:.3f}" />')

    title_start = hook.duration if hook else 0.0
    end_start = (close.start + close.duration + SCENE_TAIL_SECONDS) if close else (
        (scenes[-1].start + scenes[-1].duration + SCENE_TAIL_SECONDS) if scenes
        else title_start + TITLE_CARD_SECONDS)

    # ---- chapter rail: one tick per beat, so the viewer always knows where they are
    chapters: list[tuple[str, float, float]] = []
    if hook:
        chapters.append((hook.role or "Intro", hook.start, hook.duration))
    chapters.append(("Overview", title_start, TITLE_CARD_SECONDS))
    for sc in scenes:
        chapters.append((sc.role or sc.heading or "Scene", sc.start, sc.duration))
    if close:
        chapters.append((close.role or "Get it", close.start, close.duration))
    chapters.append(("Thanks", end_start, END_CARD_SECONDS))

    body: list[str] = []
    caps: list[str] = []
    tl: list[str] = []
    css_extra: list[str] = []
    sfx_tags: list[str] = []

    # ---- animated background
    body.append(
        '<div id="bg">'
        f'<div class="bgorb o1" style="background:radial-gradient(circle at 40% 40%,'
        f'{p["accent"]}, transparent 70%)"></div>'
        f'<div class="bgorb o2" style="background:radial-gradient(circle at 60% 60%,'
        f'{p["accent2"]}, transparent 70%)"></div>'
        '<div class="bgrain"></div>'
        '</div>'
    )
    tl.append(f'tl.to("#bg .o1", {{x:"8vw", y:"-6vh", duration:{total:.2f}, ease:"sine.inOut"}}, 0);')
    tl.append(f'tl.to("#bg .o2", {{x:"-7vw", y:"7vh", duration:{total:.2f}, ease:"sine.inOut"}}, 0);')

    # ---- progress bar + chapter rail
    body.append('<div id="progress"><div id="progress-fill"></div></div>')
    tl.append(f'tl.fromTo("#progress-fill", {{scaleX:0}}, '
              f'{{scaleX:1, duration:{total:.3f}, ease:"none"}}, 0);')

    for i, (label, start, dur) in enumerate(chapters, start=1):
        body.append(
            f'<div id="chap-{i}" class="chap clip" data-start="{start:.3f}" '
            f'data-duration="{dur:.3f}"><span class="chapnum">{i:02d}</span>'
            f'<span class="chaplab">{_esc(str(label).upper())}</span></div>'
        )
        tl.append(f'tl.from("#chap-{i} .chapnum", {{opacity:0, y:-8, duration:0.35}}, '
                  f'{start + 0.15:.3f});')
        tl.append(f'tl.from("#chap-{i} .chaplab", {{opacity:0, x:-10, duration:0.4}}, '
                  f'{start + 0.22:.3f});')

    # ---- wordmark rides after the title card so the brand never leaves frame
    wordmark_start = title_start + TITLE_CARD_SECONDS
    body.append(
        f'<div id="wordmark" class="clip" data-start="{wordmark_start:.3f}" '
        f'data-duration="{max(total - wordmark_start, 0.1):.3f}">'
        f'{brand_mark("mark-wordmark", wordmark_start, max(total - wordmark_start, 0.1))}'
        f'<span class="mark-name">{_esc(title)}</span></div>'
    )
    tl.append(f'tl.from("#wordmark", {{opacity:0, y:-14, duration:0.5}}, {wordmark_start:.3f});')

    # ---- HOOK: cold open, before the product is named
    if hook:
        v = visuals.build(_vis_dict(hook, "mesh"), hook.ctx, p, s, 900,
                          hook.start, hook.duration)
        body.append(
            f'<div id="hook" class="clip" data-start="0" data-duration="{hook.duration:.3f}">'
            f'{v.html}<div class="hookwrap">{_kinetic(hook.text)}</div></div>'
        )
        css_extra.append(v.css)
        tl.extend(v.anim)
        words = max(len(hook.text.split()), 1)
        step = min(hook.duration * 0.6 / words, 0.30)
        tl.append(f'tl.from("#hook .hookwrap .w", {{opacity:0, y:34, duration:0.5, '
                  f'stagger:{step:.3f}, ease:"power3.out"}}, 0.25);')
        # No caption bar here: the kinetic headline already is the sentence, and
        # showing it twice on one frame just looks like a bug.
        if sfx and "rise" in sfx:
            sfx_tags.append(f'<audio id="sfx-rise" src="audio/rise.wav" data-start="0" '
                            f'data-duration="1.2" data-volume="1"></audio>')

    # ---- TITLE CARD
    body.append(
        f'<div id="title-card" class="clip pad" data-start="{title_start:.3f}" '
        f'data-duration="{TITLE_CARD_SECONDS}" style="justify-content:center">'
        f'<div class="brand">{brand_mark("mark-title", title_start, TITLE_CARD_SECONDS)}</div>'
        f'<div class="kicker">{_esc(subtitle or "Product demo")}</div>'
        f'<div class="title">{_esc(title)}</div>'
        f'<div class="rule"></div></div>'
    )
    tl.append(f'tl.from("#title-card .brand", {{opacity:0, scale:0.7, duration:0.7, '
              f'ease:"back.out(2)"}}, {title_start:.3f});')
    tl.append(f'tl.from("#title-card .title", {{opacity:0, y:60, duration:0.9, '
              f'ease:"power3.out"}}, {title_start + 0.25:.3f});')
    tl.append(f'tl.from("#title-card .kicker", {{opacity:0, duration:0.6}}, '
              f'{title_start + 0.15:.3f});')
    tl.append(f'tl.from("#title-card .rule", {{scaleX:0, transformOrigin:"left center", '
              f'duration:0.8, ease:"power2.out"}}, {title_start + 0.55:.3f});')

    # ---- SCENES
    for i, sc in enumerate(scenes, start=1):
        _emit_scene(i, sc, p, s, portrait, body, caps, tl, css_extra, sfx_tags, sfx)

    # ---- CLOSE
    if close:
        _emit_scene(800, close, p, s, portrait, body, caps, tl, css_extra, sfx_tags, sfx)

    # ---- END CARD
    cta_line = (close.cta if close else "") or ""
    body.append(
        f'<div id="endcard" class="clip pad" data-start="{end_start:.3f}" '
        f'data-duration="{END_CARD_SECONDS}" style="justify-content:center">'
        f'<div class="brand endbrand">'
        f'{brand_mark("mark-end", end_start, END_CARD_SECONDS)}</div>'
        f'<div class="title endtitle">{_esc(title)}</div>'
        + (f'<div class="cta">{_esc(cta_line)}</div>' if cta_line else "") +
        f'<div class="rule"></div></div>'
    )
    tl.append(f'tl.from("#endcard .endbrand", {{opacity:0, scale:0.8, duration:0.6, '
              f'ease:"back.out(1.8)"}}, {end_start + 0.1:.3f});')
    tl.append(f'tl.from("#endcard .endtitle", {{opacity:0, y:36, duration:0.7, '
              f'ease:"power3.out"}}, {end_start + 0.3:.3f});')
    tl.append(f'tl.from("#endcard .cta", {{opacity:0, y:20, duration:0.6}}, '
              f'{end_start + 0.55:.3f});')
    tl.append(f'tl.from("#endcard .rule", {{scaleX:0, transformOrigin:"left center", '
              f'duration:0.7}}, {end_start + 0.7:.3f});')
    tl.append(f'tl.to("#endcard", {{opacity:0, duration:0.5}}, '
              f'{end_start + END_CARD_SECONDS - 0.5:.3f});')

    if sfx and "whoosh" in sfx:
        sfx_tags.append(f'<audio id="sfx-end" src="audio/whoosh.wav" '
                        f'data-start="{end_start:.3f}" data-duration="0.6" '
                        f'data-volume="1"></audio>')

    root_class = "portrait" if portrait else "landscape"

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>{_esc(title)}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      html, body {{ margin:0; padding:0; width:{w}px; height:{h}px; overflow:hidden;
        background:{p['bg']}; color:{p['text']};
        font-family: -apple-system, "Helvetica Neue", Inter, system-ui, sans-serif;
        -webkit-font-smoothing: antialiased; }}
      #root {{ position:relative; width:{w}px; height:{h}px; overflow:hidden;
        background: radial-gradient(130% 100% at 12% -10%, {p['bg2']} 0%, {p['bg']} 64%); }}
      .clip {{ position:absolute; inset:0; }}
      .pad {{ display:flex; flex-direction:column; justify-content:flex-end;
        padding:{int(120 * s)}px {int(130 * s)}px {int(250 * s)}px; box-sizing:border-box; }}

      /* ---- background ---- */
      #bg {{ position:absolute; inset:-10%; z-index:0; overflow:hidden; }}
      #bg .bgorb {{ position:absolute; width:70vmin; height:70vmin; border-radius:50%;
        filter: blur(70px); opacity:{0.30 if p['dark'] else 0.20}; }}
      #bg .o1 {{ left:-8%; top:-6%; }}
      #bg .o2 {{ right:-10%; bottom:-12%; }}
      #bg .bgrain {{ position:absolute; inset:0; opacity:{0.045 if p['dark'] else 0.03};
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E"); }}

      /* ---- chrome ---- */
      #progress {{ position:absolute; top:0; left:0; right:0; height:{max(3, int(6 * s))}px;
        background: rgba(127,127,127,0.22); z-index:60; }}
      #progress-fill {{ width:100%; height:100%; background:{p['accent']};
        transform-origin:left center; }}

      .chap {{ position:absolute; top:{int(54 * s)}px; left:{int(72 * s)}px;
        right:auto; bottom:auto; z-index:55;
        display:flex; align-items:baseline; gap:{int(12 * s)}px; }}
      .chapnum {{ font-size:{int(24 * s)}px; font-weight:800; color:{p['accent']};
        font-variant-numeric:tabular-nums; }}
      .chaplab {{ font-size:{int(24 * s)}px; font-weight:600; letter-spacing:.20em;
        text-transform:uppercase; color:{p['muted']}; }}

      #wordmark {{ position:absolute; top:{int(50 * s)}px; right:{int(72 * s)}px;
        left:auto; bottom:auto;
        display:flex; align-items:center; gap:{int(16 * s)}px; z-index:55; opacity:.9; }}
      #wordmark .mark-img, #wordmark .mark-mono {{ width:{int(52 * s)}px;
        height:{int(52 * s)}px; border-radius:{int(13 * s)}px; font-size:{int(23 * s)}px; }}
      .mark-name {{ font-size:{int(30 * s)}px; font-weight:700; letter-spacing:-.01em;
        text-shadow: 0 2px 16px rgba(0,0,0,.45); }}

      /* ---- brand mark ---- */
      .brand {{ position:relative; display:flex; align-items:center;
        margin-bottom:{int(38 * s)}px; }}
      .mark-img {{ width:{int(128 * s)}px; height:{int(128 * s)}px; object-fit:contain;
        border-radius:{int(28 * s)}px; }}
      .mark-mono {{ display:flex; align-items:center; justify-content:center;
        width:{int(128 * s)}px; height:{int(128 * s)}px; border-radius:{int(32 * s)}px;
        background:{p['accent']}; color:{p['bg']};
        font-size:{int(56 * s)}px; font-weight:800; letter-spacing:-.02em; }}

      .kicker {{ position:relative; font-size:{int(32 * s)}px; letter-spacing:.22em;
        text-transform:uppercase; color:{p['accent']}; margin-bottom:{int(26 * s)}px; }}
      .title {{ position:relative; font-size:{int(108 * s)}px; font-weight:800;
        line-height:1.04; letter-spacing:-.025em; }}
      .endtitle {{ font-size:{int(92 * s)}px; }}
      .cta {{ position:relative; margin-top:{int(30 * s)}px; font-size:{int(32 * s)}px;
        color:{p['muted']}; font-family:{visuals.MONO}; letter-spacing:.01em; }}
      .rule {{ position:relative; width:{int(260 * s)}px; height:{max(4, int(7 * s))}px;
        background:{p['accent']}; margin-top:{int(42 * s)}px; border-radius:99px; }}

      /* ---- scene visuals ---- */
      .vstage {{ position:absolute; z-index:12; }}
      .vstage.full {{ inset:0; }}
      .vstage.left {{ left:{int(96 * s)}px; top:50%; transform:translateY(-50%);
        width:{int(760 * s)}px; }}
      .vstage.right {{ right:{int(96 * s)}px; top:50%; transform:translateY(-50%);
        width:{int(760 * s)}px; }}
      .portrait .vstage.left, .portrait .vstage.right {{
        left:{int(64 * s)}px; right:{int(64 * s)}px; top:{int(210 * s)}px;
        width:auto; transform:none; }}

      /* ---- words ---- */
      .words {{ position:absolute; z-index:20; box-sizing:border-box; }}
      .words.split {{ top:50%; transform:translateY(-50%);
        width:{int(760 * s)}px; }}
      .words.right {{ right:{int(96 * s)}px; }}
      .words.left  {{ left:{int(96 * s)}px; }}
      .words.full {{ left:{int(130 * s)}px; right:{int(130 * s)}px;
        bottom:{int(300 * s)}px; }}
      .portrait .words.split {{ left:{int(64 * s)}px; right:{int(64 * s)}px;
        top:auto; bottom:{int(430 * s)}px; transform:none; width:auto; }}
      .portrait .words.full {{ bottom:{int(400 * s)}px; }}

      .role {{ display:inline-block; font-size:{int(26 * s)}px; font-weight:700;
        letter-spacing:.22em; text-transform:uppercase; color:{p['accent']};
        padding:{int(8 * s)}px {int(20 * s)}px; margin-bottom:{int(22 * s)}px;
        border:{max(2, int(3 * s))}px solid {p['accent']}; border-radius:99px; }}
      .heading {{ font-size:var(--hs, {int(80 * s)}px); font-weight:700; line-height:1.12;
        letter-spacing:-.02em; text-shadow: 0 2px 24px rgba(0,0,0,{0.35 if p['dark'] else 0.18}); }}
      .hrule {{ width:{int(150 * s)}px; height:{max(3, int(5 * s))}px;
        background:{p['accent']}; margin-top:{int(30 * s)}px; border-radius:99px; }}
      .scrim {{ position:absolute; inset:0; z-index:11;
        background: linear-gradient(to top, {p['bg']} 0%, {p['scrim']} 30%,
        rgba(0,0,0,0) 66%); }}

      /* ---- hook ---- */
      .hookwrap {{ position:absolute; left:{int(150 * s)}px; right:{int(150 * s)}px;
        top:50%; transform:translateY(-50%); z-index:20;
        font-size:{int(86 * s)}px; font-weight:800; line-height:1.16;
        letter-spacing:-.028em; text-align:left; }}
      .hookwrap .w {{ display:inline-block; margin-right:{int(18 * s)}px; }}

      /* ---- captions ---- */
      #captions {{ position:absolute; left:0; right:0; bottom:{int(74 * s)}px;
        z-index:40; display:flex; justify-content:center; pointer-events:none; }}
      .cap {{ position:absolute; top:auto; right:auto; bottom:0;
        left:50%; transform:translateX(-50%);
        background:{p['caption_bg']};
        color:{p['text']}; font-size:{int(44 * s)}px; font-weight:600; line-height:1.28;
        text-align:center; padding:{int(15 * s)}px {int(36 * s)}px;
        border-radius:{int(18 * s)}px; max-width:min({int(1480 * s)}px, 88%);
        backdrop-filter: blur(10px); box-shadow: 0 8px 40px rgba(0,0,0,.35); }}
      .cap .w {{ display:inline-block; margin-right:{int(11 * s)}px; }}
{os.linesep.join(css_extra)}
    </style>
  </head>
  <body>
    <div id="root" class="{root_class}" data-composition-id="main" data-start="0"
         data-duration="{total:.3f}" data-width="{w}" data-height="{h}">
{os.linesep.join("      " + b for b in body)}
{os.linesep.join("      " + a for a in sfx_tags)}
      <div id="captions">
{os.linesep.join("        " + c for c in caps)}
      </div>
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{os.linesep.join("      " + t for t in tl)}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def _vis_dict(sc: Scene, default: str = "") -> dict:
    """Adapt a Scene into the dict shape visuals.build expects."""
    ref = sc.visual_ref
    if sc.visual == "screenshot" and not ref and sc.media:
        ref = sc.media          # legacy direct image path
    return {
        "visual": sc.visual or default,
        "visual_ref": ref,
        "visual_note": sc.visual_note,
        "bullets": sc.bullets,
        "heading": sc.heading,
        "text": sc.text,
    }


def _caption_tag(idx: int, j: int, off: float, dur: float, text: str,
                 p: dict, tl: list[str], base: float) -> str:
    words = max(len((text or "").split()), 1)
    step = min((dur * 0.72) / words, 0.26)
    tl.append(f'tl.from("#cap-{idx}-{j} .w", {{opacity:0, y:14, duration:0.28, '
              f'stagger:{step:.3f}, ease:"power2.out"}}, {base + off + 0.06:.3f});')
    return (f'<div id="cap-{idx}-{j}" class="cap clip" data-start="{base + off:.3f}" '
            f'data-duration="{dur:.3f}">{_kinetic(text)}</div>')


def _emit_scene(idx: int, sc: Scene, p: dict, s: float, portrait: bool,
                body: list[str], caps: list[str], tl: list[str],
                css_extra: list[str], sfx_tags: list[str],
                sfx: dict[str, Path] | None) -> None:
    """Render one narrated beat: visual + words + captions + transitions."""
    # A legacy direct media path behaves as a screenshot visual.
    if sc.media and not sc.visual:
        sc.visual = "screenshot"

    v = visuals.build(_vis_dict(sc, "mesh"), sc.ctx, p, s, idx, sc.start, sc.duration)
    css_extra.append(v.css)

    label = _esc(sc.role.upper()) if sc.role else f"{idx:02d}"
    hs = heading_size(sc.heading)
    full = v.layout == "full"
    words_cls = "words full" if full else f"words split {'right' if v.layout == 'left' else 'left'}"

    scrim = '<div class="scrim"></div>' if full else ""
    body.append(
        f'<div id="scene-{idx}" class="clip" data-start="{sc.start:.3f}" '
        f'data-duration="{sc.duration + SCENE_TAIL_SECONDS:.3f}">'
        f'{v.html}{scrim}'
        f'<div class="{words_cls}">'
        f'<div class="role">{label}</div>'
        f'<div class="heading" style="--hs:{int(hs * s)}px">{_esc(sc.heading)}</div>'
        f'<div class="hrule"></div></div>'
        f'</div>'
    )

    t = sc.start
    tl.extend(v.anim)
    tl.append(f'tl.from("#scene-{idx} .role", {{opacity:0, x:-22, duration:0.45}}, {t + 0.05:.3f});')
    tl.append(f'tl.from("#scene-{idx} .heading", {{opacity:0, y:40, duration:0.7, '
              f'ease:"power3.out"}}, {t + 0.12:.3f});')
    tl.append(f'tl.from("#scene-{idx} .hrule", {{scaleX:0, transformOrigin:"left center", '
              f'duration:0.6, ease:"power2.out"}}, {t + 0.38:.3f});')

    for j, (off, dur, phrase) in enumerate(sc.caption_chunks, start=1):
        caps.append(_caption_tag(idx, j, off, dur, phrase, p, tl, sc.start))

    if sfx and "whoosh" in sfx:
        sfx_tags.append(f'<audio id="sfx-{idx}" src="audio/whoosh.wav" '
                        f'data-start="{max(t - 0.12, 0):.3f}" data-duration="0.6" '
                        f'data-volume="1"></audio>')


def _total_seconds(hook: Scene | None, scenes: list[Scene], close: Scene | None) -> float:
    t = (hook.duration if hook else 0.0) + TITLE_CARD_SECONDS
    for sc in scenes:
        t += sc.duration + SCENE_TAIL_SECONDS
    if close:
        t += close.duration + SCENE_TAIL_SECONDS
    return t + END_CARD_SECONDS


# ------------------------------------------------------------------ project + render

def write_project(project_dir: Path, title: str, subtitle: str, scenes: list[Scene],
                  theme: str, aspect: str, logo: str = "",
                  hook: Scene | None = None, close: Scene | None = None,
                  with_sfx: bool = True, repo: dict | None = None,
                  ctx=None) -> Path:
    """Lay out a HyperFrames project directory. Narration wavs are copied in."""
    if project_dir.exists():
        shutil.rmtree(project_dir)
    (project_dir / "audio").mkdir(parents=True)
    (project_dir / "media").mkdir()

    def prepare(sc: Scene, kind: str) -> None:
        sc.kind = kind
        sc.ctx = ctx
        sc.heading = (sc.heading or "").strip() or auto_heading(sc.text)
        sc.duration = sc.duration or (audio_duration(sc.audio) if sc.audio else 3.0)
        sc.caption_chunks = caption_chunks(sc.text, sc.duration)

    t = 0.0
    if hook:
        prepare(hook, "hook")
        hook.start = 0.0
        hook.role = hook.role or "Intro"
        if hook.audio:
            shutil.copyfile(hook.audio, project_dir / "audio" / "hook.wav")
        t = hook.duration
    else:
        t = 0.0

    t += TITLE_CARD_SECONDS

    for i, sc in enumerate(scenes, start=1):
        prepare(sc, "scene")
        sc.start = t
        t += sc.duration + SCENE_TAIL_SECONDS
        if sc.audio:
            shutil.copyfile(sc.audio, project_dir / "audio" / f"scene{i}.wav")
        _copy_media(sc, i, project_dir)

    if close:
        prepare(close, "close")
        close.start = t
        close.role = close.role or "Get it"
        t += close.duration + SCENE_TAIL_SECONDS
        if close.audio:
            shutil.copyfile(close.audio, project_dir / "audio" / "close.wav")
        _copy_media(close, 800, project_dir)

    logo_src = ""
    if logo and Path(logo).is_file():
        logo_src = f"media/logo{Path(logo).suffix.lower()}"
        shutil.copyfile(logo, project_dir / logo_src)

    sfx = make_sfx(project_dir / "audio") if with_sfx else {}

    (project_dir / "index.html").write_text(
        build_composition(title, subtitle, scenes, theme, aspect, logo_src,
                          hook=hook, close=close, sfx=sfx, repo=repo),
        encoding="utf-8")
    (project_dir / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "assets": "assets"},
        "media": {"autoProxy": True},
    }, indent=2), encoding="utf-8")
    (project_dir / "meta.json").write_text(json.dumps(
        {"id": project_dir.name, "name": title}, indent=2), encoding="utf-8")
    return project_dir / "index.html"


def _copy_media(sc: Scene, idx: int, project_dir: Path) -> None:
    src = sc.media
    if not src and sc.visual == "screenshot" and sc.visual_ref:
        src = sc.visual_ref
    if not src or not Path(src).is_file():
        return
    ext = Path(src).suffix.lower()
    dest = project_dir / "media" / f"scene{idx}{ext}"
    shutil.copyfile(src, dest)
    if sc.visual == "screenshot":
        sc.media = str(dest)     # visuals.py refers to it as media/sceneN.ext


def _hf(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "--yes", f"hyperframes@{HF_VERSION}", *args],
        cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1", "npm_config_yes": "true"},
    )


def lint(project_dir: Path) -> list[dict]:
    r = _hf(["lint", ".", "--json"], project_dir)
    try:
        start = r.stdout.index("{")
        data = json.loads(r.stdout[start:])
    except (ValueError, json.JSONDecodeError):
        return []
    return [f for f in data.get("findings", []) if f.get("severity") == "error"]


def render(project_dir: Path, out_path: Path, on_log=None) -> Path:
    proc = subprocess.Popen(
        ["npx", "--yes", f"hyperframes@{HF_VERSION}", "render", "-o", str(out_path)],
        cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1", "npm_config_yes": "true"},
    )
    tail = []
    for line in proc.stdout:
        tail = (tail + [line.rstrip()])[-40:]
        if on_log:
            on_log(line.rstrip())
    if proc.wait() != 0 or not out_path.exists():
        raise RuntimeError("hyperframes render failed:\n" + "\n".join(tail))
    return out_path


# ------------------------------------------------------------------ selfcheck

def demo_lint_errors(project_dir: Path) -> list[dict]:
    return lint(project_dir)


def _selfcheck() -> None:
    import tempfile

    chunks = caption_chunks("one two three four five six seven eight nine", 9.0)
    assert chunks, "expected caption chunks"
    assert abs(sum(c[1] for c in chunks) - 9.0) < 1e-6, chunks
    assert all(c[2] for c in chunks)
    assert len(split_scenes("a b.\n\nc d.\n\ne f.")) == 3
    assert len(split_scenes("One. Two. Three. Four. Five.")) == 3  # 2 sentences per scene
    assert split_scenes("") == []
    assert auto_heading("hello there general kenobi") == "hello there general kenobi"
    assert initials("Agent Island") == "AI"
    assert initials("VoxDemo") == "VO"
    assert initials("") == "•"
    assert auto_heading("It turns a script into a narrated demo, locally.") == "It turns a script into a narrated demo"
    assert heading_size("Short") == 88 and heading_size("x" * 80) == 52

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wav = td / "s.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
                        "-ar", "48000", str(wav)], check=True, capture_output=True)
        assert abs(audio_duration(str(wav)) - 3.0) < 0.1
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=256x256:d=1",
                        "-frames:v", "1", str(td / "logo.png")], check=True, capture_output=True)
        shot = td / "shot.png"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=1",
                        "-frames:v", "1", str(shot)], check=True, capture_output=True)

        scenes = [
            Scene(text="This is the first thing the demo says out loud.",
                  role="The problem", visual="code", visual_ref="demo.py", audio=str(wav)),
            Scene(text="And this is the second beat of the story.", heading="Second beat",
                  role="How it works", visual="tree", audio=str(wav)),
            Scene(text="A third beat that shows a real screenshot.",
                  role="Feature", visual="screenshot", visual_ref=str(shot),
                  media=str(shot), audio=str(wav)),
            Scene(text="A fourth beat with numbers on screen.",
                  role="Feature", visual="stats", audio=str(wav)),
        ]
        hook = Scene(text="Nobody wants to make a demo video.", audio=str(wav), role="Intro")
        close = Scene(text="Get it today, and ship your first demo tonight.",
                      audio=str(wav), visual="stack", visual_note="Apache-2.0")

        proj = td / "proj"
        write_project(proj, "Selfcheck Demo", "For people who check things", scenes,
                      "midnight", "landscape", logo=str(td / "logo.png"),
                      hook=hook, close=close, with_sfx=False)
        page = (proj / "index.html").read_text()

        assert (proj / "media" / "logo.png").exists(), "logo was not copied in"
        assert 'src="media/logo.png"' in page, "logo not referenced"
        assert "THE PROBLEM" in page and "HOW IT WORKS" in page, "role labels missing"
        assert 'id="progress-fill"' in page and 'id="wordmark"' in page
        assert 'id="hook"' in page and 'id="endcard"' in page and 'id="title-card"' in page
        assert 'class="chap clip"' in page, "chapter rail missing"
        assert "vstage left" in page and "vstage right" in page, "visual layouts not alternating"
        assert 'class="w"' in page, "kinetic caption words missing"
        # hook + title + 4 scenes + close + end card = 8 chapter ticks
        assert page.count('<div id="chap-') == 8, "expected a chapter tick per beat"
        assert hook.start == 0.0 and scenes[0].start == 3.0 + TITLE_CARD_SECONDS
        assert "media/scene3.png" in page, "screenshot not referenced by visuals"
        assert (proj / "media" / "scene3.png").exists(), "screenshot not copied"

        # …and the same composition with no logo falls back to a monogram
        nolo = td / "proj2"
        write_project(nolo, "Agent Island", "For solo developers", scenes, "neon", "portrait")
        page2 = (nolo / "index.html").read_text()
        assert "mark-mono" in page2 and ">AI<" in page2, "monogram fallback missing"
        assert 'class="portrait"' in page2, "portrait root class missing"
        assert not demo_lint_errors(nolo), "no-logo composition failed lint"
        errors = lint(proj)
        assert not errors, json.dumps(errors, indent=2)
        print("demo.py selfcheck OK — composition lints clean")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        print(__doc__)
