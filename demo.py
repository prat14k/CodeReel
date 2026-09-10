"""Build a HyperFrames composition from narrated scenes and render it to MP4.

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

HF_VERSION = os.environ.get("HYPERFRAMES_VERSION", "0.8.33")
TITLE_CARD_SECONDS = 2.4
SCENE_TAIL_SECONDS = 0.45  # breathing room after each narration line

ASPECTS = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}

THEMES = {
    "midnight": {
        "bg": "#07080d",
        "bg2": "#141a2e",
        "accent": "#7c93ff",
        "text": "#f2f4ff",
        "caption_bg": "rgba(10,12,22,0.82)",
    },
    "studio": {
        "bg": "#f6f4ef",
        "bg2": "#e6e1d6",
        "accent": "#b4552d",
        "text": "#1b1a17",
        "caption_bg": "rgba(255,255,255,0.9)",
    },
    "neon": {
        "bg": "#0a0016",
        "bg2": "#2b0546",
        "accent": "#26f5c8",
        "text": "#ffffff",
        "caption_bg": "rgba(12,0,26,0.8)",
    },
}

VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}


@dataclass
class Scene:
    text: str
    heading: str = ""
    media: str = ""  # local image or video path, optional
    audio: str = ""  # narration wav produced by the TTS side
    duration: float = 0.0  # narration length in seconds
    start: float = 0.0
    caption_chunks: list = field(default_factory=list)


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


# ------------------------------------------------------------------ composition

def _media_tag(scene: Scene, idx: int) -> str:
    if not scene.media:
        return ""
    ext = Path(scene.media).suffix.lower()
    name = f"media/scene{idx}{ext}"
    common = (
        f'data-start="{scene.start:.3f}" data-duration="{scene.duration:.3f}" '
        f'class="bleed"'
    )
    if ext in VIDEO_EXT:
        return (
            f'<video id="media-{idx}" src="{name}" muted data-has-audio="false" '
            f'data-media-start="0" {common}></video>'
        )
    return f'<img id="media-{idx}" src="{name}" {common} alt="" />'


def build_composition(
    title: str,
    subtitle: str,
    scenes: list[Scene],
    theme: str = "midnight",
    aspect: str = "landscape",
) -> str:
    w, h = ASPECTS.get(aspect, ASPECTS["landscape"])
    p = THEMES.get(theme, THEMES["midnight"])
    total = TITLE_CARD_SECONDS + sum(s.duration + SCENE_TAIL_SECONDS for s in scenes)
    scale = min(w, h) / 1080  # type is sized off the short edge, so portrait matches

    body, caps, tl = [], [], []
    body.append(
        f'<div id="title-card" class="clip pad" data-start="0" '
        f'data-duration="{TITLE_CARD_SECONDS}" style="justify-content:center">'
        f'<div class="kicker">{html.escape(subtitle or "AI generated demo")}</div>'
        f'<div class="title">{html.escape(title)}</div>'
        f'<div class="rule"></div></div>'
    )
    tl.append('tl.from("#title-card .title", {opacity:0, y:60, duration:0.9, ease:"power3.out"}, 0.1);')
    tl.append('tl.from("#title-card .kicker", {opacity:0, duration:0.6}, 0);')
    tl.append('tl.from("#title-card .rule", {scaleX:0, transformOrigin:"left center", duration:0.8, ease:"power2.out"}, 0.4);')

    for i, s in enumerate(scenes, start=1):
        body.append(f'<audio id="nar-{i}" src="audio/scene{i}.wav" data-start="{s.start:.3f}" '
                    f'data-duration="{s.duration:.3f}" data-volume="1"></audio>')
        media = _media_tag(s, i)
        scrim = '<div class="scrim"></div>' if media else ""
        body.append(
            f'<div id="scene-{i}" class="clip pad" data-start="{s.start:.3f}" '
            f'data-duration="{s.duration + SCENE_TAIL_SECONDS:.3f}">'
            f'{media}{scrim}'
            f'<div class="index">{i:02d}</div>'
            f'<div class="heading">{html.escape(s.heading)}</div>'
            f'</div>'
        )
        tl.append(f'tl.from("#scene-{i} .heading", {{opacity:0, y:40, duration:0.7, ease:"power3.out"}}, {s.start:.3f});')
        tl.append(f'tl.from("#scene-{i} .index", {{opacity:0, x:-24, duration:0.5}}, {s.start:.3f});')
        for j, (off, dur, phrase) in enumerate(s.caption_chunks, start=1):
            caps.append(
                f'<div id="cap-{i}-{j}" class="cap" data-start="{s.start + off:.3f}" '
                f'data-duration="{dur:.3f}">{html.escape(phrase)}</div>'
            )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>{html.escape(title)}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      html, body {{ margin:0; padding:0; width:{w}px; height:{h}px; overflow:hidden;
        background:{p['bg']}; color:{p['text']};
        font-family: -apple-system, "Helvetica Neue", Inter, system-ui, sans-serif; }}
      #root {{ position:relative; width:{w}px; height:{h}px; overflow:hidden;
        background: radial-gradient(120% 90% at 15% 0%, {p['bg2']} 0%, {p['bg']} 62%); }}
      .clip {{ position:absolute; inset:0; }}
      .pad {{ display:flex; flex-direction:column; justify-content:flex-end;
        padding:{int(110 * scale)}px {int(130 * scale)}px {int(300 * scale)}px; box-sizing:border-box; }}
      .bleed {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
      .scrim {{ position:absolute; inset:0;
        background: linear-gradient(to top, {p['bg']} 0%, rgba(0,0,0,0.72) 26%, rgba(0,0,0,0) 58%); }}
      .kicker {{ position:relative; font-size:{int(34 * scale)}px; letter-spacing:.28em;
        text-transform:uppercase; color:{p['accent']}; margin-bottom:{int(28 * scale)}px; }}
      .title {{ position:relative; font-size:{int(112 * scale)}px; font-weight:800; line-height:1.05;
        letter-spacing:-.02em; max-width:100%; }}
      .rule {{ position:relative; width:{int(280 * scale)}px; height:{max(4, int(7 * scale))}px;
        background:{p['accent']}; margin-top:{int(44 * scale)}px; border-radius:99px; }}
      .index {{ position:relative; font-size:{int(30 * scale)}px; font-weight:700;
        letter-spacing:.2em; color:{p['accent']}; margin-bottom:{int(20 * scale)}px; }}
      .heading {{ position:relative; font-size:{int(76 * scale)}px; font-weight:700; line-height:1.12;
        letter-spacing:-.015em; max-width:100%;
        text-shadow: 0 2px 24px rgba(0,0,0,.45); }}
      #captions {{ position:absolute; left:0; right:0; bottom:{int(96 * scale)}px;
        display:flex; justify-content:center; pointer-events:none; }}
      .cap {{ position:absolute; bottom:0; background:{p['caption_bg']}; color:{p['text']};
        font-size:{int(46 * scale)}px; font-weight:600; line-height:1.25; text-align:center;
        padding:{int(16 * scale)}px {int(38 * scale)}px; border-radius:{int(20 * scale)}px;
        max-width:min({int(1440 * scale)}px, 86%); backdrop-filter: blur(8px);
        box-shadow: 0 8px 40px rgba(0,0,0,.35); }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total:.3f}"
         data-width="{w}" data-height="{h}">
{os.linesep.join("      " + b for b in body)}
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


# ------------------------------------------------------------------ project + render

def write_project(project_dir: Path, title: str, subtitle: str, scenes: list[Scene],
                  theme: str, aspect: str) -> Path:
    """Lay out a HyperFrames project directory. Narration wavs are copied in."""
    if project_dir.exists():
        shutil.rmtree(project_dir)
    (project_dir / "audio").mkdir(parents=True)
    (project_dir / "media").mkdir()

    t = 0.0
    for i, s in enumerate(scenes, start=1):
        s.heading = s.heading.strip() or auto_heading(s.text)
        s.duration = s.duration or (audio_duration(s.audio) if s.audio else 3.0)
        s.start = TITLE_CARD_SECONDS + t
        s.caption_chunks = caption_chunks(s.text, s.duration)
        t += s.duration + SCENE_TAIL_SECONDS
        if s.audio:
            shutil.copyfile(s.audio, project_dir / "audio" / f"scene{i}.wav")
        if s.media:
            shutil.copyfile(s.media, project_dir / "media" / f"scene{i}{Path(s.media).suffix.lower()}")

    (project_dir / "index.html").write_text(
        build_composition(title, subtitle, scenes, theme, aspect), encoding="utf-8")
    (project_dir / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "assets": "assets"},
        "media": {"autoProxy": True},
    }, indent=2), encoding="utf-8")
    (project_dir / "meta.json").write_text(json.dumps(
        {"id": project_dir.name, "name": title}, indent=2), encoding="utf-8")
    return project_dir / "index.html"


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
    assert auto_heading("It turns a script into a narrated demo, locally.") == "It turns a script into a narrated demo"

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wav = td / "s.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
                        "-ar", "48000", str(wav)], check=True, capture_output=True)
        assert abs(audio_duration(str(wav)) - 3.0) < 0.1
        scenes = [
            Scene(text="This is the first thing the demo says out loud.", audio=str(wav)),
            Scene(text="And this is the second beat of the story.", heading="Second beat", audio=str(wav)),
        ]
        proj = td / "proj"
        write_project(proj, "Selfcheck Demo", "VoxDemo", scenes, "midnight", "landscape")
        assert scenes[0].start == TITLE_CARD_SECONDS
        assert abs(scenes[1].start - (TITLE_CARD_SECONDS + 3.0 + SCENE_TAIL_SECONDS)) < 0.15
        errors = lint(proj)
        assert not errors, json.dumps(errors, indent=2)
        print("demo.py selfcheck OK — composition lints clean")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        print(__doc__)
