# VoxDemo

A macOS app that turns a local repository into a narrated demo video. It reads the code,
writes the script, speaks it in a cloned voice and renders the MP4 — all on-device.
[HyperFrames](https://hyperframes.heygen.com) renders (HTML → MP4), [VoxCPM2](https://github.com/OpenBMB/VoxCPM)
speaks. No account, no API key, no upload.

```
mac/            SwiftUI app (VoxDemo.app)
server.py       localhost sidecar: script writing + VoxCPM2 voices + render jobs
providers.py    script writing: the Claude Code CLI, or any OpenAI-compatible model
repocontext.py  reads a repo into a digest that a tool-less model can work from
visuals.py      scene visuals — screenshots, code cards, file trees, stats, diagrams
demo.py         beats + narration → HyperFrames composition → MP4
app.py          the original Gradio playground (still works, unchanged)
```

## Build & run

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python voxcpm soundfile gradio fastapi uvicorn
mac/build.sh
open mac/build/VoxDemo.app
```

The app launches `server.py` itself. First launch loads ~5 GB of voice weights (already
cached here). Output lands in `~/Movies/VoxDemo/<demo>-<stamp>/` — `demo.mp4` plus the full
HyperFrames project next to it, so you can open it in HyperFrames Studio
(`cd <project> && npx hyperframes preview`) and keep editing by hand.

## Writing the script

Two backends, same output. Pick one in **Settings → Script writer**.

| | |
|---|---|
| **Claude Code** | Shells out to the `claude` CLI already on this machine, which reads the repo with its own file tools. Best quality. Read-only by construction: `--allowedTools Read Grep Glob`, `--disallowedTools Bash Write Edit`, and anything that would prompt is denied instead. |
| **Any OpenAI-compatible model** | oMLX, Ollama, LM Studio, llama.cpp, vLLM, OpenAI, Groq, OpenRouter, or a custom endpoint. Runs entirely locally if the server does. |

Presets ship for the common runners and **Find local servers** probes the usual ports
(8000, 11434, 1234, 8080, 8001, 5000, 3000, 8081) and offers whichever ones answer.

### How a local model reads a repo

A chat endpoint has no file tools, so VoxDemo does the reading itself. `repocontext.py`
walks the tree, ranks every file — README and manifests first, then entry points, then
source by substance — and emits a digest that fits a character budget (48k by default,
which sits comfortably inside a 32k-token window). It also inventories every image with
its real pixel dimensions, and reads the dependency list out of `package.json`,
`pyproject.toml`, `Cargo.toml`, `go.mod`, `Package.swift`, or failing all of those, the
install commands in the README.

That digest is what the model sees. Raise **Repo digest** in Settings if your model has a
bigger window; a 35B local model writes a noticeably better script than a 9B one.

### The beats

Both backends are asked for the same shape, in this order:

**hook → title → problem → solution → features → close → end card**

The hook is a spoken cold open that names the pain before the product is named. Its kinetic
headline *is* the sentence, so it carries no caption bar of its own. The close carries a call
to action that has to be real — a licence, a URL, a price — and lands on an end card. The
scene count is a target, not a quota: the beats win.

## The video

Every scene gets a **visual**, chosen by the script writer and resolved against the repo —
never a bare heading over a gradient. Eight kinds:

| | |
|---|---|
| `screenshot` | a real image from the repo, with a slow Ken Burns push |
| `code` | a source file as a syntax-highlighted card, opened at its most interesting block, not line 1 |
| `tree` | the project's file structure, revealed line by line |
| `stats` | the repo's own numbers — lines, files, languages |
| `terminal` | up to three real commands pulled from the README's shell blocks or a manifest |
| `stack` | the technologies it actually depends on |
| `diagram` | a flow built from real components |
| `mesh` | an abstract node graph — the cold open, and the fallback |

Paths are validated: if the model names an image that is not in the repo, the scene falls
back to a generated visual rather than shipping a broken frame. Real screenshots always beat
decoration.

Repetition is bounded three ways: consecutive runs of the same generated kind are rotated
apart, no single generated kind may take more than a quarter of the beats, and the cold open
is a full-bleed node graph so the video never starts on a plain title.

On top of that: a chapter rail that names the current beat, a progress bar, an animated
background, alternating split layouts, per-scene transitions, word-by-word kinetic captions,
and optional transition whooshes synthesized locally with ffmpeg.

Visuals are inline HTML/CSS — a composition needs no assets beyond the screenshots the repo
already ships, so nothing can go missing at render time.

## Voices

| | |
|---|---|
| **8 presets** | Aria, Nolan, Sable, Kit, Juniper, Atlas, Wren, Rio. These are *voice-design* personas, not recordings: the first time you use one, VoxCPM designs it from its description, and that clip becomes the preset's permanent reference. Same voice on every scene, every session. |
| **Cloning** | Record with the mic or import a file (5–15 s, clean, single speaker) → saved to the voice library at `~/Library/Application Support/VoxDemo/voices/`. |

Every voice — preset or cloned — speaks through a reference clip, so timbre is stable across
scenes. Style direction ("cheerful, slightly faster") is available under Advanced. Presets are
enrolled in the background as soon as the model loads, so Preview is never a first-use wait.

**The mic:** the app records with `AVAudioRecorder`, not the browser, which is what was broken
in the Gradio version. macOS asks for microphone access on the first Record. The build is ad-hoc
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

A 5-scene render with a hook and a close is roughly 40 s of video and about 30 s of rendering
on top of the narration.

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

0. Optionally, a model reads a repo and drafts the beats; you edit them.
1. The hook, each scene and the close go through VoxCPM with the voice's reference clip; leading
   and trailing silence is trimmed so the measured length matches the speech.
2. Durations come from those measured lengths, so the timeline can't drift out of sync.
3. `visuals.py` builds each scene's visual; `demo.py` emits one `index.html` — brand mark,
   chapter rail, progress bar, per-scene `<audio>`, role chip + heading, the visual, timed
   kinetic captions, GSAP entrances and transitions.
4. `hyperframes lint --json` gates it, then `hyperframes render` produces the MP4.

Looks: `midnight`, `ember`, `neon`, `studio`. Frames: landscape, portrait, square. Same seed +
same script renders identically.

## The app

Sidebar with four places to be: **Create**, **Library**, **Voices**, **Settings**. Create is a
four-step flow — Source, Script, Look, Render — and you can jump to any step once a script exists.

- **Script** is the part that matters. Every scene is a card you can drag to reorder, with a
  live miniature of the visual it will render, a picker for the visual kind, and a file picker
  when that kind needs one. Blank headings are taken from the narration.
- **Look** shows the themes as swatches of their actual palettes, not names.
- **Library** lists everything you have rendered with a real poster frame pulled from the video.
- Progress is streamed from the sidecar, so a slow local model shows tokens arriving instead of
  a frozen spinner.

## Checks

```sh
.venv/bin/python demo.py --selfcheck        # timing math + composition lints clean
.venv/bin/python contract_check.py          # every endpoint matches the Swift wire types
swift mac/timer-check.swift                 # why the record timer read 0.0s, and that the fix ticks
.venv/bin/python server.py --dry-run        # API without loading the model
mac/make-icon.sh                            # regenerate VoxDemo.icns (only if the artwork changes)
```

`contract_check.py` earns its place: Swift's synthesized `Decodable` throws on a missing key
even when the property has a default value, so an omitted field is a hard failure in the app
rather than a fallback. It boots the sidecar in dry-run, walks every response shape and reports
anything the client would choke on.

## Config

Settings live at `~/Library/Application Support/VoxDemo/settings.json` (0600 — it holds an API
key) and are editable in the app. Environment overrides:

`VOXCPM_MODEL_ID` · `VOXCPM_DEVICE` (`auto|cpu|mps|cuda`) · `VOXDEMO_PORT` (8809) ·
`VOXDEMO_HOME` · `VOXDEMO_OUTPUT` · `HYPERFRAMES_VERSION` · `VOXDEMO_CLAUDE` ·
`VOXDEMO_CLAUDE_MODEL` · `VOXDEMO_ANALYZE_TIMEOUT` (600 s)

## Misuse

Cloning a real person's voice without their consent is prohibited. Label AI-generated audio.
Apache-2.0 — OpenBMB (VoxCPM), HeyGen (HyperFrames).
