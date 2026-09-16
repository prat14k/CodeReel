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

## From a repo

Point the Demo tab at a local repository and press **Analyse repo**. VoxDemo shells out to the
Claude Code CLI already installed on this machine (`claude -p`), which reads the repo with its own
file tools and drafts a script that follows a pitch, not a feature dump:

**who it's for → the problem they hit → what this is and how it fixes it → standout features →
the close.** Each scene carries that beat as an on-screen label, and the audience line becomes the
title card's kicker.

It also hunts for the app's **own** logo and any real screenshots, and attaches them — a repo with
no assets of its own gets a monogram wordmark instead, so the video is still branded. Vendor icons
of products the app merely integrates with are rejected, as is anything under a build directory.

Everything lands in the fields below for you to edit — nothing is generated until you press
Generate, and the app ships no placeholder script to delete first. The scene count is a target,
not a quota: the beats win.

It is read-only by construction: `--allowedTools Read Grep Glob`, `--disallowedTools Bash Write
Edit`, and `--permission-prompts none` so anything that would prompt is denied instead. Progress
shows what it is reading. A 4-scene draft of this repo takes ~30 s and costs ~$0.15 on Sonnet.

Needs the `claude` CLI on PATH and signed in. Override with `VOXDEMO_CLAUDE` (full path) and
`VOXDEMO_CLAUDE_MODEL` (default `sonnet`).

## Voices

| | |
|---|---|
| **8 presets** | Aria, Nolan, Sable, Kit, Juniper, Atlas, Wren, Rio. These are *voice-design* personas, not recordings: the first time you use one, VoxCPM designs it from its description, and that clip becomes the preset's permanent reference. Same voice on every scene, every session. |
| **Cloning** | Record with the mic or import a file (5–15 s, clean, single speaker) → saved to the voice library at `~/Library/Application Support/VoxDemo/voices/`. |

Every voice — preset or cloned — speaks through a reference clip, so timbre is stable across
scenes. Style direction ("cheerful, slightly faster") is available under Advanced. Presets are
enrolled in the background as soon as the model loads, so Preview is never a first-use wait.

**The mic:** the app records with `AVAudioRecorder`, not the browser, which is what was broken in
the Gradio version. macOS asks for microphone access on the first Record. The build is ad-hoc
signed, so that grant resets each time you rebuild — expect the prompt again.

## Performance

Measured on this M5 Pro (MPS, float32). Generation is ~1.4× real time — a 2.5 s line takes
~3.8 s — and two things that sounded like features were costing far more than that:

| | |
|---|---|
| `denoiser.enhance()` on a 21.7 s reference | **421 s**, on CPU — and VoxCPM runs it *twice* per call (prompt + reference). A single cloned-voice preview was ~14 minutes. |
| Ultimate Cloning (reference + transcript) | **47 s**, and it returns the prompt read back *plus* the line — 12.2 s of audio for a 2.5 s request. Wrong output, 12× the cost. |
| Plain reference cloning | **3.8 s**, correct. |

So VoxDemo loads with `load_denoiser=False` and only ever uses reference cloning. Previews now
land in 3–6 s. If you want denoising back, clean the clip once before importing it — a one-shot
`ffmpeg -af afftdn` or any editor beats 7 minutes of ZipEnhancer per generation.

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

0. Optionally, Claude Code reads a repo and drafts the scenes; you edit them.
1. Each scene's narration goes through VoxCPM with the voice's reference clip; leading and
   trailing silence is trimmed so the measured length matches the speech.
2. Scene durations come from those measured lengths, so the timeline can't drift out of sync.
3. `demo.py` emits one `index.html` — branded title card (logo or monogram), a wordmark that
   rides the whole video, a progress bar, per-scene `<audio>`, role label + heading, optional
   full-bleed screenshot or clip, timed caption chunks, GSAP entrances.
4. `hyperframes lint --json` gates it, then `hyperframes render` produces the MP4.

Looks: `midnight`, `studio`, `neon`. Frames: landscape, portrait, square. Same seed + same script
renders identically.

## Checks

```sh
python3 demo.py --selfcheck            # timing math + composition lints clean
swift mac/timer-check.swift            # why the record timer read 0.0s, and that the fix ticks
.venv/bin/python server.py --dry-run   # API without loading the model
```

## Config

`VOXCPM_MODEL_ID` · `VOXCPM_DEVICE` (`auto|cpu|mps|cuda`) · `VOXDEMO_PORT` (8809) ·
`VOXDEMO_HOME` · `VOXDEMO_OUTPUT` · `HYPERFRAMES_VERSION` · `VOXDEMO_CLAUDE` ·
`VOXDEMO_CLAUDE_MODEL` · `VOXDEMO_ANALYZE_TIMEOUT` (420 s)

## Misuse

Cloning a real person's voice without their consent is prohibited. Label AI-generated audio.
Apache-2.0 — OpenBMB (VoxCPM), HeyGen (HyperFrames).
