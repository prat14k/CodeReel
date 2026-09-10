# VoxDemo

A macOS app that generates narrated AI product demos. [HyperFrames](https://hyperframes.heygen.com)
renders the video (HTML → MP4), [VoxCPM2](https://github.com/OpenBMB/VoxCPM) speaks it. Both run
on-device — no API keys, no accounts, no upload.

```
mac/          SwiftUI app (VoxDemo.app)
server.py     localhost sidecar: VoxCPM2 + voice store + render jobs
demo.py       script + narration → HyperFrames composition → MP4
app.py        the original Gradio playground (still works, unchanged)
```

## Build & run

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python voxcpm soundfile gradio
mac/build.sh
open mac/build/VoxDemo.app
```

The app launches `server.py` itself and shows engine state in the status bar. First launch loads
~5 GB of weights (already cached here). Output lands in `~/Movies/VoxDemo/<demo>-<stamp>/` —
`demo.mp4` plus the full HyperFrames project next to it, so you can open it in HyperFrames Studio
(`cd <project> && npx hyperframes preview`) and keep editing by hand.

## Voices

| | |
|---|---|
| **8 presets** | Aria, Nolan, Sable, Kit, Juniper, Atlas, Wren, Rio. These are *voice-design* personas, not recordings: the first time you use one, VoxCPM designs it from its description, and that clip becomes the preset's permanent reference. Same voice on every scene, every session. |
| **Cloning** | Record with the mic or import a file (5–15 s, clean, single speaker) → saved to the voice library at `~/Library/Application Support/VoxDemo/voices/`. Add the clip's transcript to switch on VoxCPM's Ultimate Cloning (reference + transcript continuation) for the closest match. |

Every voice — preset or cloned — speaks through a reference clip, so timbre is stable across
scenes. Style direction ("cheerful, slightly faster") is available under Advanced.

**The mic:** the app records with `AVAudioRecorder`, not the browser, which is what was broken in
the Gradio version. macOS asks for microphone access on the first Record. The build is ad-hoc
signed, so that grant resets each time you rebuild — expect the prompt again.

## HyperFrames requirements — all already met on this machine

Local rendering needs **no HeyGen account and no API key**.

| | Status here |
|---|---|
| Node.js ≥ 22 | ✅ v24.16.0 |
| FFmpeg + ffprobe | ✅ 8.1.2 (`/opt/homebrew/bin`) |
| Chrome headless shell | ✅ cached in `~/.cache/puppeteer` (HyperFrames fetches it once) |
| `hyperframes` CLI | pulled by `npx` on first render (pinned to 0.8.33) — needs internet once, then cached |

Two things worth knowing:

- **Telemetry is on by default.** HyperFrames sends anonymous usage data (not file paths or
  composition content). Turn it off with `npx hyperframes telemetry disable`.
- **An account is only for sharing.** `hyperframes publish` (hosted links) and their cloud/Lambda
  renderers want a HeyGen sign-in. Nothing in VoxDemo touches either.

Optional, not required:

- `brew install whisper-cpp` — real word-level caption timing. VoxDemo currently spaces captions
  by character weight across each scene's measured narration length, which drifts a little inside
  a long sentence.
- Docker — only for HyperFrames' containerized render path. Unused.

## How a demo is built

1. Each scene's narration goes through VoxCPM with the voice's reference clip; leading and
   trailing silence is trimmed so the measured length matches the speech.
2. Scene durations come from those measured lengths, so the timeline can't drift out of sync.
3. `demo.py` emits one `index.html` — title card, per-scene `<audio>`, headings, optional
   full-bleed screenshot or clip, timed caption chunks, GSAP entrances.
4. `hyperframes lint --json` gates it, then `hyperframes render` produces the MP4.

Looks: `midnight`, `studio`, `neon`. Frames: landscape, portrait, square. Same seed + same script
renders identically.

## Checks

```sh
python3 demo.py --selfcheck                       # timing math + composition lints clean
.venv/bin/python server.py --dry-run              # API without loading the model
```

## Config

`VOXCPM_MODEL_ID` · `VOXCPM_DEVICE` (`auto|cpu|mps|cuda`) · `VOXDEMO_PORT` (8809) ·
`VOXDEMO_HOME` · `VOXDEMO_OUTPUT` · `HYPERFRAMES_VERSION`

## Misuse

Cloning a real person's voice without their consent is prohibited. Label AI-generated audio.
Apache-2.0 — OpenBMB (VoxCPM), HeyGen (HyperFrames).
