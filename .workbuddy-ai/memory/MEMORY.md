# VoxDemo — project notes

Repo: `/Users/prat14k/Office/VoxCPM`. A macOS app that turns a local repo into a
narrated demo video, entirely on-device.

## Layout

| file | role |
|---|---|
| `mac/` | SwiftUI app (`VoxDemo.app`), SwiftPM, macOS 14+ |
| `server.py` | FastAPI sidecar on 127.0.0.1:8809 — script writing, voice store, render jobs |
| `providers.py` | script-writing backends: `claude` CLI, or any OpenAI-compatible endpoint |
| `repocontext.py` | repo → digest for models that have no file tools; logo + screenshot picking |
| `visuals.py` | scene visuals as inline HTML/CSS (8 kinds) |
| `demo.py` | beats + narration → HyperFrames composition → MP4 |
| `contract_check.py` | asserts endpoint payloads match the Swift wire types |
| `mac/Sources/VoxDemo/Snapshots.swift` | `VOXDEMO_SNAPSHOT=<dir>` renders the UI to PNGs and exits |
| `app.py` | original Gradio playground, untouched |

## Conventions

- **Python side is stdlib-only** apart from the server's FastAPI/uvicorn/numpy/soundfile.
  No `openai` package — `providers.py` uses `urllib`. Keep it that way so the venv stays small.
- **`demo.py` never imports torch** so `--selfcheck` runs without loading the model.
- **Visuals are inline HTML/CSS.** A composition must not need an asset the repo does not
  already ship, or renders break when files move.
- **Never invent facts.** The prompt forbids it, but small local models still do it. Anything
  the model names as a path is validated against the repo before it is used.
- Themes carry a full palette (bg, accent, code_*, caption_bg, scrim) because `visuals.py`
  draws panels from it — adding a theme means adding all of it.
- GSAP statements are built as Python f-strings; unit values must be quoted (`"6vmin"`).
- **No infinite GSAP loops.** HyperFrames lints `repeat: -1` as `gsap_infinite_repeat` because
  a composition is a finite, seekable window. Derive a finite cycle count from the beat
  duration instead.
- **No single generated visual may exceed `max(2, ceil(n/4))` beats.** `providers.enrich_visuals`
  enforces this twice — on the model's request and again after ref resolution, because
  fallbacks (a `screenshot` with no image left becomes `stats`) bypass the first check. Use
  `_swap_kind()`, which only offers kinds guaranteed to draw something.
- The cold open is a full-bleed node graph (`visuals._mesh`), and its kinetic headline is the
  only text on screen — no caption bar, or the same sentence appears twice.
- Anything shown as a "real" detail (commands, paths, numbers) is scraped from the repo, never
  written by the model. `providers._readme_commands()` pulls shell commands from the README's
  fenced blocks, preferring shell-labelled ones and filtering out directory-tree lines.

## Gotchas

- Swift's synthesized `Decodable` **does not fall back to property defaults**. A missing key
  throws. Run `contract_check.py` after changing any endpoint.
- `swift build` needs `--disable-sandbox` when run inside a sandboxed shell.
- The Bash tool sandbox isolates network between invocations — start a server and its client
  in the same call, or from one Python process.
- **A proxy env var is set in this shell.** `urllib` honours it, so a request to
  `127.0.0.1:<port>` from Python comes back `HTTP 502 … upstream connect failed` instead
  of a connection error — and the *sidecar's own* outgoing `urllib` calls in
  `providers.py` inherit it too. Use `urllib.request.ProxyHandler({})` with
  `build_opener`, or `curl --noproxy '*'`. Without this, a dead-port test reports a
  proxy failure and you chase the wrong thing entirely.
- `server.py --dry-run` skips the 5 GB model load; use it for all API work.
- macOS resets the microphone grant on every ad-hoc re-sign, so a rebuild re-prompts.
- **`screencapture` does not work in this shell** (`could not create image from display` —
  the screen-recording TCC grant belongs to the terminal). Use
  `VOXDEMO_SNAPSHOT=<dir> mac/build/VoxDemo.app/Contents/MacOS/VoxDemo` to see the UI.
  It captures layout/typography faithfully but **cannot** capture `List` rows or
  `TextField` contents — those are AppKit-backed and come out blank. Keep the
  `control-list` shot as the control that proves this.
- **Run `demo.py --selfcheck` after touching the composition.** It already builds a
  logo-bearing project (`logo=` at demo.py:794, asserted at :819), so no separate
  "with a logo" run is needed — several findings only appear once an `<img>` is in
  the composition. Target: 0 errors, 0 warnings.
- **`--selfcheck` cannot see warnings.** `demo.lint()` filters `severity == "error"`
  (demo.py:723), so a green selfcheck says nothing about warnings. To check both,
  call `demo._hf(["lint", ".", "--json"], proj)` directly and read
  `data["findings"]` unfiltered.
- **Running the checks needs `PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"`.** The
  sandbox PATH omits it, so `ffmpeg` is missing and `--selfcheck` dies with
  `FileNotFoundError: 'ffmpeg'`. There is no `timeout` command on macOS either — a
  `timeout 300 …` wrapper fails as `command not found` while the trailing
  `echo exit=$?` still prints 0, i.e. a false green.
- The sandbox's bulk-delete guard (`CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`, threshold 50)
  counts deletions cumulatively per session. A render costs ~30, so heavy `rm -rf` cleanup
  can starve it and block the next render mid-TTS with no error. Check the counter first.
- **There is exactly one error surface:** `.alert("Something went wrong")` in
  `VoxDemoApp.swift`, bound to `engine.lastError != nil`, with ~15 sites writing it
  across five views. Any of them can pop over whatever the user is doing, and the title
  never says which. When a user reports "something went wrong", that is the alert — find
  the *message* before guessing at the cause. Keep routine, localised failures (a
  provider call) inline in their card instead; reserve the modal for real transport faults.
- **Curl and standalone clients will not reproduce UI-only failures.** Use
  `VOXDEMO_PROBE=1` (wired into `Snapshots.runIfRequested()`): it boots the app's own
  `Engine`, waits for `/health`, then runs the Settings calls through `API` and prints
  the real error. That distinguishes "the transport is broken" from "the alert is
  misreporting it".
- A cold local model server accepts the socket and then stalls. `URLSession` reports that
  as `NSURLErrorDomain` code `-1001`, the same channel as every other failure — so
  classify it explicitly rather than flattening to "the engine isn't answering".

## Local model server

oMLX at `http://127.0.0.1:8000/v1`, key `0000`. `POST /providers/detect` finds it and other
runners on the usual ports. Prefer the 35B model for scripts; the 9B is roughly 4× faster but
noticeably more generic.
