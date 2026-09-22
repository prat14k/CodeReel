"""Script-writing backends: the Claude Code CLI, or any OpenAI-compatible model.

Two very different shapes live behind one interface:

* **claude** — shells out to `claude -p`, which reads the repo with its own file
  tools. We send nothing but the prompt.
* **openai** — a plain chat endpoint (oMLX, Ollama, LM Studio, llama.cpp, vLLM,
  OpenAI, Groq, OpenRouter…). No tools, so `repocontext` reads the repo for it and
  we paste a digest into the prompt.

Both return the same dict, so the rest of the app never learns which one ran.

Stdlib only — urllib, not the `openai` package, so the venv stays as it is.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import repocontext

# ------------------------------------------------------------------ presets

# `local` marks endpoints that need no internet and no account. `key_required`
# is what the UI uses to decide whether to nag about a missing key.
PRESETS: dict[str, dict] = {
    "omlx": {
        "label": "oMLX",
        "base_url": "http://127.0.0.1:8000/v1",
        "local": True, "key_required": True, "default_key": "0000",
        "note": "Local MLX server for Apple Silicon. Models live in ~/.omlx/models.",
    },
    "ollama": {
        "label": "Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "local": True, "key_required": False, "default_key": "ollama",
        "note": "Ollama serves an OpenAI-compatible API at /v1.",
    },
    "lmstudio": {
        "label": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "local": True, "key_required": False, "default_key": "lm-studio",
        "note": "Start the LM Studio local server, then pick a loaded model.",
    },
    "llamacpp": {
        "label": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1",
        "local": True, "key_required": False, "default_key": "",
        "note": "`llama-server -m model.gguf --port 8080`.",
    },
    "vllm": {
        "label": "vLLM",
        "base_url": "http://127.0.0.1:8001/v1",
        "local": True, "key_required": False, "default_key": "",
        "note": "`vllm serve <model> --port 8001`.",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "local": False, "key_required": True, "default_key": "",
        "note": "Your prompt and a repo digest leave the machine.",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "local": False, "key_required": True, "default_key": "",
        "note": "Fast hosted inference. Data leaves the machine.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "local": False, "key_required": True, "default_key": "",
        "note": "One key for many hosted models. Data leaves the machine.",
    },
    "custom": {
        "label": "Custom endpoint",
        "base_url": "",
        "local": False, "key_required": False, "default_key": "",
        "note": "Any server that speaks POST /chat/completions.",
    },
}

# Ports worth probing when the user has not told us where their server is.
COMMON_PORTS = [(8000, "oMLX / vLLM"), (11434, "Ollama"), (1234, "LM Studio"),
                (8080, "llama.cpp"), (8001, "vLLM"), (5000, "text-gen-webui"),
                (3000, "LocalAI"), (8081, "Jan")]

CLAUDE_MODEL = os.environ.get("VOXDEMO_CLAUDE_MODEL", "sonnet")
ANALYZE_TIMEOUT = int(os.environ.get("VOXDEMO_ANALYZE_TIMEOUT", "600"))

VISUAL_KINDS = ("screenshot", "code", "tree", "stats", "stack",
                "terminal", "diagram", "mesh")


# ------------------------------------------------------------------ config

@dataclass
class Provider:
    """Everything needed to run one script-writing backend."""
    kind: str = "claude"              # "claude" | "openai"
    preset: str = "omlx"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.4
    max_tokens: int = 6000
    timeout: int = 300
    digest_budget: int = 48_000       # chars of repo digest sent to a local model
    claude_model: str = CLAUDE_MODEL

    def resolved(self) -> "Provider":
        """Fill base_url / api_key from the preset where the user left them blank."""
        p = PRESETS.get(self.preset)
        if p:
            if not self.base_url:
                self.base_url = p["base_url"]
            if not self.api_key and p.get("key_required"):
                self.api_key = p.get("default_key", "")
        return self

    @property
    def label(self) -> str:
        if self.kind == "claude":
            return f"Claude Code · {self.claude_model}"
        name = PRESETS.get(self.preset, {}).get("label", self.preset)
        return f"{name} · {self.model or 'no model selected'}"

    @property
    def is_local(self) -> bool:
        if self.kind == "claude":
            return False
        return bool(PRESETS.get(self.preset, {}).get("local"))

    def redacted(self) -> dict:
        d = asdict(self)
        if d["api_key"]:
            d["api_key_set"] = True
            d["api_key"] = ""            # never hand the secret back to the UI
        else:
            d["api_key_set"] = False
        d["label"] = self.label
        d["is_local"] = self.is_local
        return d


# ------------------------------------------------------------------ http

class ProviderError(RuntimeError):
    pass


def _headers(p: Provider) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if p.api_key:
        h["Authorization"] = f"Bearer {p.api_key}"
    return h


def _url(p: Provider, path: str) -> str:
    base = (p.base_url or "").rstrip("/")
    if not base:
        raise ProviderError("no base URL set — pick a preset or paste your endpoint")
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return base + path


def _open(p: Provider, url: str, body: bytes | None, accept: str, timeout: int):
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    for k, v in _headers(p).items():
        req.add_header(k, v)
    req.add_header("Accept", accept)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        if e.code in (401, 403):
            raise ProviderError(
                f"{e.code} from {url} — the API key was rejected. {detail}") from e
        raise ProviderError(f"HTTP {e.code} from {url}. {detail}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"could not reach {url} — {e.reason}") from e
    except socket.timeout as e:
        raise ProviderError(f"{url} timed out after {timeout}s") from e


# ------------------------------------------------------------------ models

def list_models(p: Provider, timeout: int = 15) -> list[str]:
    """GET /models. Every mainstream local server answers this OpenAI-style.

    15 s, not 8: a server that is still loading weights answers the socket and
    then stalls, and the Settings button has 60 s of headroom on the Swift side.
    Timing out here reads to the user as "my API key is wrong".
    """
    p = p.resolved()
    with _open(p, _url(p, "/v1/models"), None, "application/json", timeout) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    items = data.get("data") if isinstance(data, dict) else data
    out = []
    for m in items or []:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("name") or m.get("model")
            if mid:
                out.append(str(mid))
        elif isinstance(m, str):
            out.append(m)
    return sorted(set(out))


def probe_local(timeout: float = 0.6, budget: float = 20.0) -> list[dict]:
    """Find OpenAI-compatible servers already listening on this machine.

    Bounded by `budget` seconds overall. Without it, a port that accepts the
    connection but never answers costs the full per-key timeout five times over,
    and the Settings button just looks frozen.
    """
    found = []
    deadline = time.time() + budget
    for port, label in COMMON_PORTS:
        if time.time() > deadline:
            break
        with socket.socket() as s:
            s.settimeout(timeout)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                continue
        entry = {"port": port, "hint": label, "base_url": f"http://127.0.0.1:{port}/v1",
                 "models": [], "ok": False, "error": "", "api_key": ""}
        probe = Provider(kind="openai", preset="custom",
                         base_url=entry["base_url"], api_key="").resolved()
        for key in ("", "0000", "ollama", "lm-studio", "sk-local"):
            if time.time() > deadline:
                break
            probe.api_key = key
            try:
                entry["models"] = list_models(probe, timeout=3)
                entry["ok"] = True
                entry["api_key"] = key
                entry["error"] = ""      # a later key worked; forget the earlier 401
                break
            except Exception as e:                     # try the next likely key
                msg = str(e)
                entry["error"] = msg[:200]
                # A key only matters if something answered. Retrying five keys
                # against a port that refused the connection just burns the budget.
                if "could not reach" in msg:
                    break
        found.append(entry)
    return found


def test(p: Provider) -> dict:
    """What the Settings pane's Test button calls. Never raises."""
    p = p.resolved()
    out = {"ok": False, "models": [], "reply": "", "error": "", "latency_ms": 0}
    t0 = time.time()
    try:
        if p.kind == "claude":
            bin_ = claude_bin()
            r = subprocess.run([bin_, "--version"], capture_output=True, text=True, timeout=20)
            out.update(ok=r.returncode == 0, reply=(r.stdout or r.stderr).strip()[:120],
                       error="" if r.returncode == 0 else (r.stderr or "").strip()[:300])
        else:
            out["models"] = list_models(p, timeout=15)
            if p.model:
                reply = complete(p, "You are a test harness.",
                                 'Reply with exactly this JSON and nothing else: {"ok":true}',
                                 max_tokens=40, timeout=min(p.timeout, 120))
                out.update(ok=True, reply=reply.strip()[:160])
            else:
                out.update(ok=True, reply=f"{len(out['models'])} models available")
    except Exception as e:
        out["error"] = str(e)[:400]
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


# ------------------------------------------------------------------ chat

def complete(p: Provider, system: str, user: str, *,
             max_tokens: int | None = None, timeout: int | None = None,
             on_token=None, json_mode: bool = False) -> str:
    """One chat completion. Streams when on_token is given, so slow local
    models still show movement in the UI instead of a frozen spinner."""
    p = p.resolved()
    if not p.model:
        raise ProviderError("no model selected — pick one from the list")

    body = {
        "model": p.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": p.temperature,
        "max_tokens": max_tokens or p.max_tokens,
        "stream": bool(on_token),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    payload = json.dumps(body).encode()
    to = timeout or p.timeout

    try:
        resp = _open(p, _url(p, "/v1/chat/completions"), payload, "application/json", to)
    except ProviderError as e:
        # Some servers reject response_format outright — retry without it. Only on a
        # 4xx: a timeout or an unreachable host would just burn the budget twice.
        if not json_mode or not re.match(r"HTTP 4\d\d ", str(e)):
            raise
        body.pop("response_format", None)
        resp = _open(p, _url(p, "/v1/chat/completions"),
                     json.dumps(body).encode(), "application/json", to)

    with resp:
        if not on_token:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return _choice_text(data)

        text, n, other = [], 0, []
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                other.append(line)
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                delta = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            piece = _delta_text(delta)
            if piece:
                text.append(piece)
                n += 1
                if n % 6 == 0:
                    on_token("".join(text))
        if not text and other:
            # Not SSE after all — the server ignored `stream` and sent one body.
            try:
                return _choice_text(json.loads("".join(other)))
            except json.JSONDecodeError:
                pass
        return "".join(text)


def _choice_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        err = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
        raise ProviderError(err or f"model returned no choices: {json.dumps(data)[:300]}")
    msg = choices[0].get("message") or {}
    return msg.get("content") or choices[0].get("text") or ""


def _delta_text(delta: dict) -> str:
    choices = delta.get("choices") or []
    if not choices:
        return ""
    d = choices[0].get("delta") or {}
    return d.get("content") or ""


# ------------------------------------------------------------------ prompt

SYSTEM_PROMPT = (
    "You are a senior product marketer who writes short, spoken demo-video scripts "
    "for software. You write for the ear, not the page. You never invent facts: "
    "every claim you make must be visible in the material you are given. "
    "You always answer with a single valid JSON object and nothing else."
)

BEATS = """Land these beats, in this order:

1. HOOK — one sentence, spoken before the product is even named. Name the pain the
   audience feels today, in their words. Concrete, a little provocative, never a slogan.
2. PROBLEM — why that pain exists and what it costs them.
3. SOLUTION — what this app is, and in one breath HOW it removes that pain.
4. FEATURES — one scene per genuinely notable capability. Only real ones. If the app
   has nothing worth calling out, spend these scenes deepening the solution instead.
5. CLOSE — the payoff of using it. What it costs, how to get it, or what changes after."""

VISUAL_RULES = """Each scene also gets a visual — the thing on screen while the narration plays.
Choose `visual` from exactly these, and use the guide:

- "screenshot" — a REAL screenshot of this app. Only if one exists in the file list.
  Put its repo-relative path in `visual_ref`. If you are not certain the image is a
  screenshot OF THIS APP, do not choose this.
- "code"       — a real source file, shown as a highlighted code card. Put the
  repo-relative path in `visual_ref`. Choose the file that best proves the point.
- "tree"       — the project's file structure. Good for "how it is organised".
- "stats"      — the repo's own numbers (size, languages, file count).
- "stack"      — the real dependencies and technologies this app uses.
- "terminal"   — a command someone would actually type. Put the command in `visual_ref`.
- "diagram"    — a simple flow of how the thing works, built from your `bullets`.
- "mesh"       — abstract motion background. Use this at most once, for the hook.

Prefer "screenshot" and "code" whenever the repo can support them — real material beats
decoration. Never invent a path: `visual_ref` must be copied from the file list."""


def _schema_hint() -> str:
    return (
        'Reply with ONLY this JSON object, no prose and no code fence:\n'
        '{"title":"the app\'s name, <=6 words",'
        '"audience":"2-6 words naming who it is for",'
        '"hook":"one spoken sentence that opens the video",'
        '"logo":"repo-relative path to the app\'s own logo, or \\"\\"",'
        '"scenes":[{"role":"1-3 word on-screen label","heading":"2-6 words shown on screen",'
        '"narration":"1-2 spoken sentences, 12-30 words","visual":"one kind from the list",'
        '"visual_ref":"path or command, or \\"\\"","visual_note":"<=5 word caption for the visual, or \\"\\""}],'
        '"close":{"heading":"2-6 words","narration":"1-2 spoken sentences",'
        '"cta":"a short line of real proof — a licence, a URL, or how to get it"}}'
    )


def build_prompt(ctx: repocontext.RepoContext | None, n_scenes: int, angle: str,
                 digest_text: str = "") -> str:
    parts = [
        f"Write the narration script for a short demo video introducing the app in "
        f"this repository to someone who has never seen it.",
        "",
        f"Read the material below properly before writing. Do not skim and guess.",
        "",
        BEATS,
        "",
        VISUAL_RULES,
        "",
        "Writing rules:",
        "- Each narration is 1-2 sentences, 12-30 words, written to be SPOKEN ALOUD. No",
        "  markdown, no bullets, no file paths, and no code identifiers a person would not say.",
        "- Speak to the viewer as \"you\". Warm and concrete, not a feature list read out.",
        "- Each heading is 2-6 words and appears on screen. It is not the narration.",
        f"- Aim for about {n_scenes} scenes, but the beats win: never drop one to hit the",
        "  number, and never pad with filler to reach it.",
        "- Never invent features, numbers, prices, or platforms. Everything must come from",
        "  the material. If a fact is not there, leave it out.",
    ]
    if angle.strip():
        parts += ["", f"Angle the whole script for: {angle.strip()}"]
    if ctx is not None:
        parts += [
            "",
            f"The app is called {ctx.name}. It has {len(ctx.files)} source files and "
            f"{repocontext.human_int(ctx.total_lines)} lines of code.",
        ]
        stack = repocontext.tech_stack(ctx)
        if stack:
            parts += [f"Real dependencies detected: {', '.join(stack)}"]
        shots = ctx.screenshots()
        if shots:
            parts += ["Real screenshots found (valid `visual_ref` values for \"screenshot\"): "
                      + ", ".join(i.path for i in shots[:12])]
        else:
            parts += ["No screenshots exist in this repo — do not use the \"screenshot\" visual."]
        if ctx.videos:
            parts += [f"Video files present: {', '.join(ctx.videos[:5])}"]
    if digest_text:
        parts += ["", "=== REPOSITORY MATERIAL ===", digest_text, "=== END MATERIAL ==="]
    parts += ["", _schema_hint()]
    return "\n".join(parts)


# ------------------------------------------------------------------ json repair

def extract_json(text: str) -> dict:
    """Models wrap JSON in prose or fences, or stop mid-object. Recover what we can."""
    body = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    body = re.sub(r"^.*?</think>", "", body, flags=re.S | re.I)   # unclosed opener
    body = re.sub(r"```[a-zA-Z]*", "", body).strip()              # fences anywhere
    start = body.find("{")
    if start < 0:
        raise ProviderError(f"the model did not return JSON. It said: {body[:300]}")

    end = body.rfind("}")
    clipped = body[start:end + 1] if end > start else ""
    whole = body[start:]
    # Every clean parse before any repaired one, and repair the WHOLE fragment
    # first: when the model was cut off mid-object, clipping at the last brace
    # would silently drop the scenes that came after it.
    err: json.JSONDecodeError | None = None
    for candidate in (clipped, whole, _repair(whole), _repair(clipped)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            err = e
    raise ProviderError(f"could not parse the model's JSON ({err}). "
                        f"It began: {body[start:start + 240]}")


_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _repair(frag: str) -> str:
    """One pass over a JSON fragment, fixing what local models get wrong.

    Inside strings: raw control characters get escaped (a narration with a real
    newline in it is the single most common way a local model's JSON fails to
    parse). Outside them: // and /* */ comments and trailing commas are dropped.
    At the end: whatever the model left open when it hit max_tokens is closed.
    """
    out: list[str] = []
    stack: list[str] = []
    in_str = esc = False
    i, n = 0, len(frag)

    while i < n:
        ch = frag[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch in _ESCAPES:
                out.append(_ESCAPES[ch])
                i += 1
                continue
            elif ch < " ":
                i += 1                      # any other control char: drop it
                continue
            out.append(ch)
            i += 1
            continue

        if ch == '"':
            in_str = True
        elif ch == "/" and frag[i + 1:i + 2] in ("/", "*"):
            line = frag[i + 1] == "/"
            stop = frag.find("\n" if line else "*/", i)
            i = n if stop < 0 else stop + (1 if line else 2)
            continue
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            while out and out[-1].isspace():
                out.pop()
            if out and out[-1] == ",":        # trailing comma, whatever sat between
                out.pop()
            if stack:
                stack.pop()
        out.append(ch)
        i += 1

    tail = "".join(out).rstrip()
    if in_str:
        tail += '"'
    else:
        tail = tail.rstrip(",")
    if tail.endswith(":"):                    # a key whose value never arrived
        tail += '""'
    return tail + "".join(reversed(stack))


# ------------------------------------------------------------------ claude cli

def claude_bin() -> str:
    override = os.environ.get("VOXDEMO_CLAUDE")
    if override and Path(override).is_file():
        return override
    path = os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".local" / "bin"),
                            "/opt/homebrew/bin", "/usr/local/bin"])
    found = shutil.which("claude", path=path)
    if not found:
        raise ProviderError(
            "Claude Code CLI not found. Install it, pick a local model instead, or set "
            "VOXDEMO_CLAUDE to the full path of the `claude` binary.")
    return found


def claude_available() -> tuple[bool, str]:
    try:
        return True, claude_bin()
    except ProviderError as e:
        return False, str(e)


def _analyze_claude(p: Provider, repo: Path, prompt: str, on_step) -> tuple[dict, float]:
    """Read-only by construction: only Read/Grep/Glob, prompts denied not asked."""
    cmd = [claude_bin(), "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--model", p.claude_model,
           "--permission-prompts", "none",
           "--allowedTools", "Read", "Grep", "Glob",
           "--disallowedTools", "Bash", "Write", "Edit"]
    proc = subprocess.Popen(cmd, cwd=repo, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    deadline = time.time() + ANALYZE_TIMEOUT
    result, reads = None, 0
    try:
        for line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                raise ProviderError(f"repo analysis went past {ANALYZE_TIMEOUT}s — stopped it")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        arg = block.get("input") or {}
                        label = arg.get("file_path") or arg.get("pattern") or arg.get("path") or ""
                        reads += 1
                        on_step(min(0.08 + 0.045 * reads, 0.80),
                                f"reading {Path(str(label)).name or 'the repo'}")
            elif event.get("type") == "result":
                result = event
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        proc.wait()

    if result is None:
        raise ProviderError(f"Claude Code produced no result. {stderr[:400]}")
    if result.get("is_error") or result.get("subtype") != "success":
        raise ProviderError(f"Claude Code failed ({result.get('subtype')}): "
                            f"{str(result.get('result'))[:300]}")
    return extract_json(result.get("result") or ""), float(result.get("total_cost_usd") or 0)


def _analyze_openai(p: Provider, ctx: repocontext.RepoContext, prompt: str,
                    on_step) -> tuple[dict, float]:
    on_step(0.22, f"sending {len(prompt) // 1000}k characters to {p.model}")

    seen = [0]

    def on_token(text: str) -> None:
        seen[0] += 1
        on_step(min(0.30 + seen[0] * 0.006, 0.92), f"writing the script — {len(text)} chars")

    raw = complete(p, SYSTEM_PROMPT, prompt, on_token=on_token, json_mode=True)
    if not raw.strip():
        raise ProviderError("the model returned an empty response")
    try:
        return extract_json(raw), 0.0
    except ProviderError:
        on_step(0.93, "the JSON came back malformed — asking again")
        repair = (prompt + "\n\nYour previous reply could not be parsed as JSON. "
                  "Reply with the JSON object only. No prose, no code fence, no comments.")
        raw2 = complete(p, SYSTEM_PROMPT, repair, max_tokens=p.max_tokens,
                        on_token=on_token, json_mode=True)
        return extract_json(raw2), 0.0


# ------------------------------------------------------------------ analyze

def analyze(p: Provider, repo_path: str, n_scenes: int, angle: str,
            on_step=lambda frac, msg: None) -> dict:
    """Read the repo and draft a script. Returns the normalised script dict."""
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        raise ProviderError(f"not a folder: {repo}")
    n = max(3, min(int(n_scenes), 12))
    p = p.resolved()

    ctx = None
    if p.kind != "claude":
        on_step(0.04, "scanning the repo")
        ctx = repocontext.scan(repo)
        if not ctx.files:
            raise ProviderError(f"found no readable files in {repo}")
        on_step(0.08, f"found {len(ctx.files)} files, "
                      f"{repocontext.human_int(ctx.total_lines)} lines")

    prompt = build_prompt(ctx, n, angle,
                          digest_text="" if p.kind == "claude" else repocontext.digest(
                              ctx, budget_chars=p.digest_budget))

    on_step(0.12, "asking " + p.label)
    if p.kind == "claude":
        data, cost = _analyze_claude(p, repo, prompt, on_step)
    else:
        data, cost = _analyze_openai(p, ctx, prompt, on_step)

    on_step(0.95, "cleaning up the draft")
    return normalise(data, repo, ctx, p, cost)


def normalise(data: dict, repo: Path, ctx: repocontext.RepoContext | None,
              p: Provider, cost: float = 0.0) -> dict:
    """Validate every path the model produced. A hallucinated path becomes ""."""
    if ctx is None:
        try:
            ctx = repocontext.scan(repo)
        except Exception:
            ctx = None
    known_images = {i.path for i in ctx.images} if ctx else set()
    known_files = {f.path for f in ctx.files} if ctx else set()

    def rel_path(raw: str, allowed: set[str]) -> str:
        rel = (raw or "").strip().lstrip("/")
        if not rel or rel not in allowed:
            return ""
        return rel

    scenes = []
    for sc in data.get("scenes", []) or []:
        narration = _clean(sc.get("narration", ""))
        if not narration:
            continue
        kind = _clean(sc.get("visual", "")).lower()
        if kind not in VISUAL_KINDS:
            kind = ""
        ref = _clean(sc.get("visual_ref", ""))
        if kind == "screenshot":
            ref = rel_path(ref, known_images)
            if not ref:
                kind = ""                     # the model named an image that is not there
        elif kind == "code":
            ref = rel_path(ref, known_files)
            if not ref:
                kind = ""
        elif kind == "terminal":
            ref = ref[:120]
        else:
            ref = ""
        scenes.append({
            "role": _clean(sc.get("role", "")),
            "heading": _clean(sc.get("heading", "")),
            "text": narration,
            "media": "",
            "visual": kind,
            "visual_ref": ref,
            "visual_note": _clean(sc.get("visual_note", ""))[:60],
        })

    if not scenes:
        raise ProviderError("the model returned no usable scenes")

    # Models tend to reach for the same visual over and over; spread them out and
    # fill in the blanks so no scene ever renders as bare text.
    if ctx is not None:
        scenes = enrich_visuals(ctx, scenes)

    close_raw = data.get("close") or {}
    close = {
        "heading": _clean(close_raw.get("heading", "")) or "Get started",
        "text": _clean(close_raw.get("narration", "")),
        "cta": _clean(close_raw.get("cta", ""))[:90],
    }

    return {
        "title": _clean(data.get("title", "")) or repo.name,
        "subtitle": _clean(data.get("audience", "")),
        "hook": _clean(data.get("hook", "")),
        "logo": rel_path(data.get("logo", ""), known_images),
        "scenes": scenes,
        "close": close if close["text"] else None,
        "repo": ctx.to_json() if ctx else {"name": repo.name},
        "provider": p.label,
        "cost_usd": round(cost, 4),
    }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()


def _swap_kind(kind: str, counts: dict[str, int], recent: list[str],
               ctx, quota: int) -> str | None:
    """Pick a renderable alternative that is still under quota, or None.

    Only kinds that are guaranteed to draw something are offered, and `stack` is
    withheld when the repo declares no dependencies — it would collapse into a
    second stats card and defeat the point of the swap.
    """
    options = [k for k in SAFE_ROTATE
               if k != kind and counts.get(k, 0) < quota and recent[-1:] != [k]]
    if not options:
        return None
    if not (ctx is not None and repocontext.tech_stack(ctx)):
        options = [k for k in options if k != "stack"] or options
    return min(options, key=lambda k: (counts.get(k, 0), SAFE_ROTATE.index(k)))


def enrich_visuals(ctx: repocontext.RepoContext, scenes: list[dict]) -> list[dict]:
    """Fill in a visual for scenes the model left blank, and resolve the refs.

    Auto-choice walks a preference ladder based on the scene's role, then two
    repetition guards run: consecutive runs of one kind are broken up, and no single
    generated kind may take more than a quarter of the beats. Real screenshots are
    exempt — they are material, not filler.
    """
    used_images: set[str] = set()
    used_code: set[str] = set()
    recent: list[str] = []
    counts: dict[str, int] = {}
    # No single generated kind may take more than a quarter of the beats. Six kinds
    # over seven scenes should not mean three code cards and four wildcards.
    quota = max(2, -(-len(scenes) // 4))

    for i, sc in enumerate(scenes):
        role = (sc.get("role") or "").lower()
        kind = sc.get("visual") or ""

        if not kind:
            if "problem" in role:
                kind = "terminal" if any(f.kind == "entry" for f in ctx.files) else "mesh"
            elif "solution" in role or "how" in role:
                kind = "tree" if i > 0 else "mesh"
            elif "who" in role or "audience" in role:
                kind = "stack"
            elif "close" in role or "get" in role or "start" in role:
                kind = "stats"
            else:
                kind = "code"

        # A run of the same generated visual reads as a template, and so does the
        # same kind four times across the video. Rotate on both counts — but never
        # touch "screenshot", which is real material worth showing twice.
        if kind in ROTATE and (recent[-1:] == [kind] or counts.get(kind, 0) >= quota):
            kind = _swap_kind(kind, counts, recent, ctx, quota) or kind

        if kind == "screenshot":
            if not sc.get("visual_ref"):
                shot = repocontext.screenshot_for(ctx, used_images, sc.get("heading", ""))
                if not shot:
                    kind = "stats"      # no real image left; fall through below
                else:
                    sc["visual_ref"] = shot
            if kind == "screenshot":
                used_images.add(sc["visual_ref"])

        if kind == "code":
            ref = sc.get("visual_ref") or ""
            if not ref or ref in used_code:
                ref = repocontext.guess_code_file(
                    ctx, sc.get("heading", "") + " " + sc.get("text", ""))
            if ref:
                used_code.add(ref)
                sc["visual_ref"] = ref
            else:
                kind = "stats"
        elif kind == "terminal":
            if not sc.get("visual_ref"):
                sc["visual_ref"] = _guess_command(ctx)
            if not sc.get("visual_ref"):
                kind = "stats"
        elif kind == "diagram":
            if not sc.get("bullets"):
                sc["bullets"] = _guess_bullets(ctx, sc)
        elif kind in ("tree", "stats", "stack"):
            sc["visual_ref"] = ""

        # A fallback can land on a kind that is already at quota (a screenshot
        # request with no image left becomes a stats card), so check once more now
        # that every ref has been resolved.
        if kind in ROTATE and counts.get(kind, 0) >= quota:
            kind = _swap_kind(kind, counts, recent, ctx, quota) or kind

        sc["visual"] = kind
        recent.append(kind)
        counts[kind] = counts.get(kind, 0) + 1

    # Any scene still on the abstract fallback takes a screenshot if one is spare.
    for sc in scenes:
        if sc.get("visual") == "mesh":
            shot = repocontext.screenshot_for(ctx, used_images, sc.get("heading", ""))
            if shot:
                used_images.add(shot)
                sc["visual"] = "screenshot"
                sc["visual_ref"] = shot
    return scenes


# Generated kinds worth rotating through, in a sensible order.
ROTATE = ("code", "tree", "stats", "stack", "terminal", "diagram")

# Same set, ordered so that a forced swap lands on a kind that always renders.
SAFE_ROTATE = ("tree", "stats", "diagram", "terminal", "stack", "code")


def _guess_command(ctx: repocontext.RepoContext) -> str:
    repo = Path(ctx.root)
    for name in ("Makefile", "justfile", "Taskfile.yml", "package.json",
                 "pyproject.toml", "Cargo.toml", "Package.swift"):
        if (repo / name).is_file():
            if name == "package.json":
                try:
                    scripts = json.loads((repo / name).read_text()).get("scripts", {})
                    for key in ("dev", "start", "build", "test"):
                        if key in scripts:
                            return f"npm run {key}"
                except Exception:
                    pass
            if name == "Makefile":
                return "make"
            if name == "Cargo.toml":
                return "cargo run"
            if name == "Package.swift":
                return "swift run"
            if name == "pyproject.toml":
                return "uv run python -m " + ctx.name.lower().replace("-", "_")
    for fi in ctx.files:
        if fi.kind == "entry" and fi.path.endswith(".py"):
            return f"python {fi.path}"
    return ""


_SHELL_LANGS = {"sh", "bash", "zsh", "shell", "console", "terminal", "shell-session"}
# A line only counts as a command if it opens with a known runner or is a bare
# executable path. Without this, a README's directory-tree block gets scraped and
# the terminal visual ends up showing "demo.py   beats + narration → …".
_CMD_START = re.compile(
    r"^(?:\./|sudo\s|npx\s|npm\s|pnpm\s|yarn\s|bun\s|uv\s|pip3?\s|python3?\s|"
    r"make\b|cargo\s|go\s|swift\s|docker\s|gradle\s|\./mvnw\s|mvn\s|dotnet\s|"
    r"brew\s|git\s|node\s|deno\s|poetry\s|conda\s|flutter\s|cmake\s|just\s|"
    r"task\s|curl\s|wget\s)")
_BARE_EXE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


def _looks_like_command(line: str) -> bool:
    return bool(_CMD_START.match(line) or _BARE_EXE.match(line))


def _readme_commands(ctx: repocontext.RepoContext, limit: int = 3) -> list[str]:
    """Shell commands lifted from the README's fenced blocks — nothing invented.

    A one-line terminal card looks broken on a 1920px frame, so the terminal visual
    wants two or three real lines. Shell-labelled blocks are trusted first; failing
    that, every block is filtered down to lines that actually look runnable.
    """
    repo = Path(ctx.root)
    for name in ("README.md", "Readme.md", "readme.md", "README.rst"):
        path = repo / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            return []

        blocks = re.findall(r"```([a-zA-Z-]*)[ \t]*\n(.*?)```", text, re.S)
        for trusted_only in (True, False):
            out: list[str] = []
            for lang, block in blocks:
                if trusted_only and lang.lower() not in _SHELL_LANGS:
                    continue
                for raw in block.splitlines():
                    line = re.sub(r"\s+#\s+.*$", "", raw.strip().lstrip("$").strip())
                    line = re.sub(r"\s{2,}", " ", line).strip()
                    if not line or len(line) > 88:
                        continue
                    if line.startswith(("#", "//", "cd ", "export ")):
                        continue
                    if not trusted_only and not _looks_like_command(line):
                        continue
                    if line not in out:
                        out.append(line)
                    if len(out) >= limit:
                        break
                if len(out) >= limit:
                    break
            if out:
                return out
        return []
    return []


def _guess_commands(ctx: repocontext.RepoContext, limit: int = 3) -> list[str]:
    """Up to `limit` real commands for the terminal visual, install-first."""
    out: list[str] = []
    primary = _guess_command(ctx)
    for cmd in _readme_commands(ctx, limit) + ([primary] if primary else []):
        if cmd and cmd not in out:
            out.append(cmd)
        if len(out) >= limit:
            break
    return out


def _guess_bullets(ctx: repocontext.RepoContext, sc: dict) -> list[str]:
    out = []
    for fi in ctx.files:
        if fi.kind in ("entry", "manifest") and fi.path not in ("README.md",):
            out.append(Path(fi.path).name)
        if len(out) >= 4:
            break
    return out or ["input", "process", "output"]
