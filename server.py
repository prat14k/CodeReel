#!/usr/bin/env python3
"""CodeReel sidecar — script writing, VoxCPM2 voices and HyperFrames rendering.

The macOS app spawns this and talks JSON to 127.0.0.1. Everything that can run
on-device does: the TTS model, the repo reading, the video render.

    .venv/bin/python server.py            # serve on 127.0.0.1:8809
    .venv/bin/python server.py --dry-run  # API only, no model (for UI work)

Script writing goes through `providers`, which supports both the Claude Code CLI
and any OpenAI-compatible endpoint (oMLX, Ollama, LM Studio, vLLM, OpenAI…).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import demo as demolib
import providers
import repocontext

DRY_RUN = "--dry-run" in sys.argv
MODEL_ID = os.environ.get("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
DEVICE = os.environ.get("VOXCPM_DEVICE", "auto")
PORT = int(os.environ.get("CODEREEL_PORT", os.environ.get("VOXDEMO_PORT", "8809")))


def _resolve_support() -> Path:
    override = os.environ.get("CODEREEL_HOME", os.environ.get("VOXDEMO_HOME"))
    if override:
        return Path(override)
    modern = Path.home() / "Library" / "Application Support" / "CodeReel"
    legacy = Path.home() / "Library" / "Application Support" / "VoxDemo"
    if not modern.exists() and legacy.exists():
        try:
            shutil.copytree(legacy, modern)
        except Exception:
            return legacy
    return modern


SUPPORT = _resolve_support()
VOICES_DIR = SUPPORT / "voices"
CACHE_DIR = SUPPORT / "cache"
SETTINGS_FILE = SUPPORT / "settings.json"
OUTPUT_DIR = Path(os.environ.get("CODEREEL_OUTPUT", os.environ.get("VOXDEMO_OUTPUT", Path.home() / "Movies" / "CodeReel")))
LEGACY_OUTPUT_DIR = Path.home() / "Movies" / "VoxDemo"
for d in (VOICES_DIR, CACHE_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Enrollment line for preset voices: read once per preset, then reused as the
# cloning reference so a preset sounds identical across scenes and sessions.
#
# ponytail: no denoiser and no "ultimate" (transcript-continuation) cloning.
# Measured on this M5 Pro: ZipEnhancer takes 421s to clean a 21.7s clip and the
# generate path runs it twice per call; transcript-continuation returns the prompt
# read back plus the line (12.2s of audio for a 2.5s request) at 12x the cost.
# Plain reference cloning is 3.8s and correct. See README > Performance.
ENROLL_TEXT = ("Hello, and welcome. In this short demo I will walk you through the "
               "product, step by step, so you can see exactly how it works.")
ENROLL_SEED = 20260101

PRESETS = [
    ("aria", "Aria", "a young woman, warm and friendly, clear articulation"),
    ("nolan", "Nolan", "a middle-aged man, deep and calm, documentary narrator"),
    ("sable", "Sable", "a woman in her thirties, confident and crisp, corporate presenter"),
    ("kit", "Kit", "a young man, upbeat and energetic, startup pitch energy"),
    ("juniper", "Juniper", "a bright cheerful young woman, playful and light"),
    ("atlas", "Atlas", "an older man, gravelly and authoritative, cinematic trailer voice"),
    ("wren", "Wren", "a soft-spoken woman, gentle and intimate, unhurried"),
    ("rio", "Rio", "a man with a lively storytelling cadence, warm and expressive"),
]

ANALYZE_TIMEOUT = int(os.environ.get("CODEREEL_ANALYZE_TIMEOUT", os.environ.get("VOXDEMO_ANALYZE_TIMEOUT", "600")))
IMAGE_EXT = repocontext.IMAGE_EXT
BUILD_DIRS = repocontext.IGNORE_DIRS


# ---------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "provider": {
        "kind": "openai", "preset": "omlx", "base_url": "", "api_key": "",
        "model": "", "temperature": 0.4, "max_tokens": 6000, "timeout": 300,
        "digest_budget": 48000, "claude_model": providers.CLAUDE_MODEL,
    },
    "defaults": {
        "theme": "midnight", "aspect": "landscape", "voice_id": "aria",
        "sfx": True, "scenes": 6, "angle": "",
    },
}
# Reentrant: save_settings() holds the lock and calls load_settings() inside it.
_settings_lock = threading.RLock()


def load_settings() -> dict:
    """Read settings.json, filling in anything a previous version did not have."""
    with _settings_lock:
        data = {}
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
        out = {}
        for section, defaults in DEFAULT_SETTINGS.items():
            merged = dict(defaults)
            if isinstance(data.get(section), dict):
                merged.update({k: v for k, v in data[section].items() if k in defaults})
            out[section] = merged
        return out


def save_settings(patch: dict) -> dict:
    """Merge a partial update and write it back, 0600 — it holds an API key."""
    with _settings_lock:
        current = load_settings()
        for section, values in (patch or {}).items():
            if section in current and isinstance(values, dict):
                current[section].update({k: v for k, v in values.items()
                                         if k in current[section]})
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(current, indent=2))
        try:
            SETTINGS_FILE.chmod(0o600)
        except OSError:
            pass
        return current


def provider_from(body: dict | None) -> providers.Provider:
    """A request may override the saved provider; otherwise use the saved one."""
    saved = load_settings()["provider"]
    merged = dict(saved)
    if body:
        merged.update({k: v for k, v in body.items() if v not in (None, "")})
    known = {f for f in providers.Provider.__dataclass_fields__}
    return providers.Provider(**{k: v for k, v in merged.items() if k in known}).resolved()


# ---------------------------------------------------------------- repo context

_ctx_cache: dict[str, tuple[float, repocontext.RepoContext]] = {}
_ctx_lock = threading.Lock()


def repo_ctx(repo_path: str, max_age: float = 900) -> repocontext.RepoContext | None:
    """Scan a repo, cached briefly so a render does not re-walk it every time."""
    if not repo_path:
        return None
    key = str(Path(repo_path).expanduser().resolve())
    with _ctx_lock:
        hit = _ctx_cache.get(key)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
    try:
        ctx = repocontext.scan(Path(key))
    except Exception as e:
        print(f"[codereel] could not scan {key}: {e}", flush=True)
        return None
    with _ctx_lock:
        _ctx_cache[key] = (time.time(), ctx)
    return ctx


def _abs_asset(repo: Path, rel: str) -> str:
    """Repo-relative asset path → absolute, refusing anything outside the repo."""
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        return ""
    root = repo.resolve()
    try:
        full = (root / rel).resolve()
        full.relative_to(root)
    except (ValueError, OSError):
        return ""
    if not full.is_file():
        return ""
    if full.suffix.lower() == ".icns":
        png = CACHE_DIR / f"logo_{abs(hash(str(full))) % 10**10}.png"
        if not png.exists():
            import subprocess
            subprocess.run(["sips", "-s", "format", "png", str(full), "--out", str(png)],
                           capture_output=True)
        return str(png) if png.exists() else ""
    return str(full)


def _resolve_assets(repo: Path, result: dict) -> dict:
    """Turn the model's repo-relative paths into paths the app can open."""
    result["logo"] = _abs_asset(repo, result.get("logo", ""))
    for sc in result.get("scenes", []):
        if sc.get("visual") == "screenshot":
            sc["visual_ref"] = _abs_asset(repo, sc.get("visual_ref", ""))
            if not sc["visual_ref"]:
                sc["visual"] = ""            # fall back to a generated visual
    return result


# ---------------------------------------------------------------- model

model = None
SAMPLE_RATE = 48000
_state = {"ready": DRY_RUN, "error": "", "loading": not DRY_RUN, "warm": ""}
_gpu_lock = threading.Lock()  # VoxCPM inference is serialized


def _load_model() -> None:
    global model, SAMPLE_RATE
    try:
        from voxcpm import VoxCPM
        model = VoxCPM.from_pretrained(
            MODEL_ID, load_denoiser=False,
            optimize=(os.environ.get("VOXCPM_OPTIMIZE", "1") == "1"), device=DEVICE)
        SAMPLE_RATE = model.tts_model.sample_rate
        _state.update(ready=True, loading=False)
        print(f"[codereel] model ready sample_rate={SAMPLE_RATE}", flush=True)
        threading.Thread(target=_warm_presets, daemon=True).start()
    except Exception as e:  # surfaced in /health so the app can show it
        _state.update(ready=False, loading=False, error=f"{type(e).__name__}: {e}")
        print(f"[codereel] model load failed: {e}", flush=True)


if not DRY_RUN:
    threading.Thread(target=_load_model, daemon=True).start()


def _warm_presets() -> None:
    """Give every preset its reference clip up front, so Preview is never a 13s wait.

    Each enrollment holds _gpu_lock for ~13s, so a user request queues behind at
    most one of them rather than behind the whole set.
    """
    todo = [p for p in PRESETS if not (VOICES_DIR / p[0] / "ref.wav").exists()]
    for i, (pid, name, _) in enumerate(todo, start=1):
        _state["warm"] = f"{name} ({i}/{len(todo)})"
        try:
            ensure_enrolled(pid)
        except Exception as e:
            print(f"[codereel] warming {pid} failed: {e}", flush=True)
    _state["warm"] = ""


def _require_model():
    if DRY_RUN:
        raise HTTPException(503, "server started with --dry-run; no model loaded")
    if not _state["ready"]:
        raise HTTPException(503, _state["error"] or "model still loading")
    return model


# ---------------------------------------------------------------- audio utils

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()


def _trim_silence(wav: np.ndarray, sr: int, pad: float = 0.06) -> np.ndarray:
    """Trim leading/trailing near-silence so caption timing tracks the speech."""
    mono = wav if wav.ndim == 1 else wav.mean(axis=1)
    win = max(1, sr // 100)
    frames = mono[: len(mono) // win * win].reshape(-1, win)
    if not len(frames):
        return wav
    energy = np.abs(frames).max(axis=1)
    loud = np.flatnonzero(energy > max(energy.max() * 0.02, 1e-4))
    if not len(loud):
        return wav
    p = int(pad * sr)
    return wav[max(0, loud[0] * win - p): min(len(wav), (loud[-1] + 1) * win + p)]


def _write(wav: np.ndarray, path: Path) -> tuple[Path, float]:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), wav, SAMPLE_RATE)
    return path, len(wav) / SAMPLE_RATE


def _generate(text: str, ref: Path | None, style: str,
              cfg: float, timesteps: int, seed: int) -> np.ndarray:
    import torch
    m = _require_model()
    text = _clean(text)
    if not text:
        raise HTTPException(400, "nothing to say — text is empty")
    styled = (f"({_clean(style)})" if style else "") + text
    kwargs = dict(cfg_value=float(cfg), inference_timesteps=int(timesteps))
    with _gpu_lock:
        torch.manual_seed(int(seed))
        wav = (m.generate(text=styled, reference_wav_path=str(ref), **kwargs) if ref
               else m.generate(text=styled, **kwargs))
    return _trim_silence(np.asarray(wav), SAMPLE_RATE)


# ---------------------------------------------------------------- voice store

def _voice_dir(vid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", vid or ""):
        raise HTTPException(400, f"bad voice id {vid!r}")
    return VOICES_DIR / vid


def _read_voice(d: Path) -> dict | None:
    meta = d / "voice.json"
    if not meta.exists():
        return None
    v = json.loads(meta.read_text())
    v["enrolled"] = (d / "ref.wav").exists()
    return v


def list_voices() -> list[dict]:
    out = {}
    for pid, name, desc in PRESETS:
        stored = _read_voice(_voice_dir(pid))
        out[pid] = stored or {"id": pid, "name": name, "kind": "preset",
                              "description": desc, "enrolled": False}
    for d in sorted(VOICES_DIR.iterdir()) if VOICES_DIR.exists() else []:
        v = _read_voice(d) if d.is_dir() else None
        if v:
            out[v["id"]] = v
    return sorted(out.values(), key=lambda v: (v["kind"] != "preset", v["name"].lower()))


def _save_voice(v: dict) -> dict:
    d = _voice_dir(v["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "voice.json").write_text(json.dumps(v, indent=2))
    return _read_voice(d)


def ensure_enrolled(vid: str) -> dict:
    """Every voice speaks through a reference clip. Presets synthesize theirs once."""
    d = _voice_dir(vid)
    v = _read_voice(d)
    if v and v.get("enrolled"):
        return v
    preset = next((p for p in PRESETS if p[0] == vid), None)
    if not preset:
        raise HTTPException(404, f"voice {vid!r} not found")
    _, name, desc = preset
    wav = _generate(ENROLL_TEXT, None, desc, cfg=2.0, timesteps=12, seed=ENROLL_SEED)
    d.mkdir(parents=True, exist_ok=True)
    _write(wav, d / "ref.wav")
    return _save_voice({"id": vid, "name": name, "kind": "preset", "description": desc,
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def _slug(text: str, fallback: str = "voice") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or fallback


# ---------------------------------------------------------------- jobs

JOBS: dict[str, dict] = {}
_pool = ThreadPoolExecutor(max_workers=1)      # one generation at a time
_cpu_pool = ThreadPoolExecutor(max_workers=2)  # repo analysis: no model, no queueing behind it


def _job(kind: str) -> str:
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"id": jid, "kind": kind, "state": "running", "progress": 0.0,
                 "message": "starting", "log": [], "result": None, "error": ""}
    return jid


def _step(jid: str, progress: float, message: str) -> None:
    j = JOBS[jid]
    j["progress"] = round(min(max(progress, 0.0), 1.0), 3)
    j["message"] = message
    j["log"] = (j["log"] + [message])[-80:]
    print(f"[codereel] {jid} {j['progress']:.0%} {message}", flush=True)


def _run(jid: str, fn, pool: ThreadPoolExecutor | None = None) -> None:
    def wrapped():
        try:
            JOBS[jid]["result"] = fn(jid)
            JOBS[jid].update(state="done", progress=1.0, message="done")
        except Exception as e:
            msg = str(getattr(e, "detail", None) or e)
            JOBS[jid].update(state="error", error=msg, message=msg[:400])
            print(f"[codereel] {jid} failed: {e}", flush=True)
    (pool or _pool).submit(wrapped)


# ---------------------------------------------------------------- api

app = FastAPI(title="CodeReel")


class CloneReq(BaseModel):
    name: str
    source_path: str


class SpeakReq(BaseModel):
    voice_id: str
    text: str
    style: str = ""
    cfg: float = 2.0
    timesteps: int = 12
    seed: int = 42


class AnalyzeReq(BaseModel):
    repo_path: str
    scenes: int = 6
    angle: str = ""
    provider: dict | None = None


class SceneIn(BaseModel):
    text: str
    heading: str = ""
    role: str = ""
    media: str = ""
    visual: str = ""
    visual_ref: str = ""
    visual_note: str = ""
    bullets: list[str] = Field(default_factory=list)


class CloseIn(BaseModel):
    text: str
    heading: str = ""
    role: str = ""
    cta: str = ""
    visual: str = ""
    visual_ref: str = ""
    visual_note: str = ""


class DemoReq(BaseModel):
    title: str
    subtitle: str = ""
    logo: str = ""
    voice_id: str
    script: str = ""
    hook: str = ""
    close: CloseIn | None = None
    scenes: list[SceneIn] = Field(default_factory=list)
    theme: str = "midnight"
    aspect: str = "landscape"
    style: str = ""
    cfg: float = 2.0
    timesteps: int = 12
    seed: int = 42
    sfx: bool = True
    repo_path: str = ""


class SettingsPatch(BaseModel):
    provider: dict | None = None
    defaults: dict | None = None


@app.get("/health")
def health():
    ok, _ = providers.claude_available()
    prov = load_settings()["provider"]
    return {"ready": _state["ready"], "loading": _state["loading"], "error": _state["error"],
            "warm": _state["warm"],
            "dry_run": DRY_RUN, "model": MODEL_ID, "device": DEVICE,
            "sample_rate": SAMPLE_RATE, "output_dir": str(OUTPUT_DIR),
            "themes": list(demolib.THEMES), "aspects": list(demolib.ASPECTS),
            "theme_labels": {k: v.get("label", k.title()) for k, v in demolib.THEMES.items()},
            "theme_palettes": {k: {"bg": v["bg"], "bg2": v["bg2"], "accent": v["accent"],
                                   "accent2": v.get("accent2", v["accent"]),
                                   "text": v["text"], "dark": v.get("dark", True)}
                               for k, v in demolib.THEMES.items()},
            "visual_kinds": list(providers.VISUAL_KINDS),
            "provider": {"kind": prov.get("kind"), "preset": prov.get("preset"),
                         "model": prov.get("model"), "base_url": prov.get("base_url")},
            "claude_available": ok}


@app.get("/settings")
def get_settings():
    s = load_settings()
    s["provider"]["api_key_set"] = bool(s["provider"].get("api_key"))
    s["provider"]["api_key"] = ""
    s["presets"] = {k: {kk: vv for kk, vv in v.items() if kk != "default_key"}
                    for k, v in providers.PRESETS.items()}
    s["claude_available"] = providers.claude_available()[0]
    s["output_dir"] = str(OUTPUT_DIR)
    return s


@app.put("/settings")
def put_settings(patch: SettingsPatch):
    body = patch.model_dump(exclude_none=True)
    prov = body.get("provider") or {}
    # An empty key means "leave it alone", not "erase it" — the UI never sees it.
    if prov.get("api_key") == "":
        prov.pop("api_key", None)
    saved = save_settings({"provider": prov, "defaults": body.get("defaults") or {}})
    saved["provider"]["api_key_set"] = bool(saved["provider"].get("api_key"))
    saved["provider"]["api_key"] = ""
    return saved


@app.post("/providers/models")
def provider_models(cfg: dict | None = None):
    p = provider_from(cfg)
    try:
        return {"ok": True, "models": providers.list_models(p), "provider": p.label,
                "error": ""}
    except Exception as e:
        return {"ok": False, "models": [], "error": str(e)[:400], "provider": p.label}


@app.post("/providers/test")
def provider_test(cfg: dict | None = None):
    return providers.test(provider_from(cfg))


@app.get("/providers/detect")
def provider_detect():
    """Find OpenAI-compatible servers already running on this machine."""
    return {"found": providers.probe_local()}


@app.get("/voices")
def voices():
    return {"voices": list_voices()}


@app.post("/voices")
def clone_voice(req: CloneReq):
    src = Path(req.source_path).expanduser()
    if not src.is_file():
        raise HTTPException(400, f"reference audio not found: {src}")
    try:
        info = sf.info(str(src))
    except Exception as e:
        raise HTTPException(400, f"not a readable audio file: {e}")
    if info.duration < 3.0:
        raise HTTPException(400, f"reference is {info.duration:.1f}s — record at least 3s "
                                 "(5–15s of clean single-speaker speech is best)")
    name = _clean(req.name) or "My voice"
    base = _slug(name)
    vid, n = base, 2
    while (VOICES_DIR / vid / "voice.json").exists() or any(p[0] == vid for p in PRESETS):
        vid, n = f"{base}-{n}", n + 1

    d = _voice_dir(vid)
    d.mkdir(parents=True, exist_ok=True)
    wav, sr = sf.read(str(src), dtype="float32", always_2d=False)
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    sf.write(str(d / "ref.wav"), wav, sr)
    return _save_voice({"id": vid, "name": name, "kind": "clone",
                        "description": f"cloned from {src.name} ({info.duration:.1f}s)",
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})


@app.delete("/voices/{vid}")
def delete_voice(vid: str):
    d = _voice_dir(vid)
    if not d.exists():
        raise HTTPException(404, f"voice {vid!r} not found")
    if any(p[0] == vid for p in PRESETS):
        raise HTTPException(400, "preset voices cannot be deleted")
    shutil.rmtree(d)
    return {"deleted": vid}


@app.post("/speak")
def speak(req: SpeakReq):
    jid = _job("speak")

    def work(jid):
        _step(jid, 0.1, "preparing voice")
        if not (_voice_dir(req.voice_id) / "ref.wav").exists():
            ensure_enrolled(req.voice_id)
        v = _read_voice(_voice_dir(req.voice_id)) or {"name": req.voice_id}
        ref = _voice_dir(req.voice_id) / "ref.wav"
        if _state["warm"]:
            _step(jid, 0.2, f"waiting for preset warm-up ({_state['warm']})")
        _step(jid, 0.35, f"speaking as {v['name']}")
        wav = _generate(req.text, ref, req.style, req.cfg, req.timesteps, req.seed)
        path, dur = _write(wav, CACHE_DIR / f"speak_{jid}.wav")
        return {"path": str(path), "duration": round(dur, 2), "voice": v["name"]}

    _run(jid, work)
    return {"job_id": jid}


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    repo = Path(req.repo_path).expanduser()
    if not repo.is_dir():
        raise HTTPException(400, f"not a folder: {repo}")
    n = max(3, min(int(req.scenes), 12))
    p = provider_from(req.provider)
    jid = _job("analyze")

    def work(jid):
        def on_step(frac, msg):
            _step(jid, frac, msg)

        result = providers.analyze(p, str(repo), n, req.angle, on_step)
        _step(jid, 0.97, "resolving assets")
        return _resolve_assets(repo, result)

    _run(jid, work, pool=_cpu_pool)
    return {"job_id": jid}


@app.post("/demos")
def create_demo(req: DemoReq):
    scenes_in = [s for s in req.scenes if _clean(s.text)] or \
                [SceneIn(text=t) for t in demolib.split_scenes(req.script)]
    if not scenes_in:
        raise HTTPException(400, "no script — give at least one scene of narration")
    if len(scenes_in) > 40:
        raise HTTPException(400, f"{len(scenes_in)} scenes is more than this build renders (40 max)")
    title = _clean(req.title) or "Untitled demo"
    ctx = repo_ctx(req.repo_path)
    jid = _job("demo")

    def work(jid):
        _step(jid, 0.02, "preparing voice")
        if not (_voice_dir(req.voice_id) / "ref.wav").exists():
            ensure_enrolled(req.voice_id)
        v = _read_voice(_voice_dir(req.voice_id)) or {}
        ref = _voice_dir(req.voice_id) / "ref.wav"

        def narrate(text: str, frac: float, label: str, name: str) -> demolib.Scene:
            _step(jid, frac, label)
            wav = _generate(text, ref, req.style, req.cfg, req.timesteps, req.seed + abs(hash(name)) % 97)
            path, dur = _write(wav, CACHE_DIR / jid / f"{name}.wav")
            return demolib.Scene(text=_clean(text), audio=str(path), duration=dur, ctx=ctx)

        # The hook and close are narrated too, so the video opens and ends on a voice.
        hook = None
        if _clean(req.hook):
            hook = narrate(req.hook, 0.04, "recording the opening", "hook")
            hook.role = "Intro"
            hook.visual = "mesh"

        total_units = len(scenes_in) + (1 if req.close else 0)
        scenes = []
        for i, s in enumerate(scenes_in, start=1):
            sc = narrate(s.text, 0.06 + 0.60 * (i - 1) / max(total_units, 1),
                         f"narrating scene {i} of {len(scenes_in)}", f"scene{i}")
            sc.heading = _clean(s.heading)
            sc.role = _clean(s.role)
            sc.visual = _clean(s.visual)
            sc.visual_ref = _clean(s.visual_ref)
            sc.visual_note = _clean(s.visual_note)
            sc.bullets = [b for b in (s.bullets or []) if _clean(b)]
            media = str(Path(s.media).expanduser()) if _clean(s.media) else ""
            if media and not Path(media).is_file():
                raise HTTPException(400, f"scene {i} media not found: {media}")
            sc.media = media
            if media and not sc.visual:
                sc.visual = "screenshot"
            scenes.append(sc)

        close = None
        if req.close and _clean(req.close.text):
            close = narrate(req.close.text, 0.68, "recording the close", "close")
            close.heading = _clean(req.close.heading) or "Get started"
            close.role = _clean(req.close.role) or "Get it"
            close.cta = _clean(req.close.cta)
            close.visual = _clean(req.close.visual) or "stats"
            close.visual_ref = _clean(req.close.visual_ref)
            close.visual_note = _clean(req.close.visual_note)

        _step(jid, 0.72, "building composition")
        logo = str(Path(req.logo).expanduser()) if _clean(req.logo) else ""
        if logo and not Path(logo).is_file():
            raise HTTPException(400, f"logo not found: {logo}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        proj = OUTPUT_DIR / f"{_slug(title, 'demo')}-{stamp}"
        demolib.write_project(proj, title, _clean(req.subtitle), scenes,
                              req.theme, req.aspect, logo=logo,
                              hook=hook, close=close, with_sfx=bool(req.sfx),
                              ctx=ctx)
        errors = demolib.lint(proj)
        if errors:
            raise RuntimeError("composition failed lint: " +
                               json.dumps([e.get("message") for e in errors]))

        _step(jid, 0.75, "rendering video")

        def on_log(line: str):
            m = re.search(r"(\d{1,3})%\s+(\S.*)", line)
            if m:
                _step(jid, 0.75 + 0.24 * int(m.group(1)) / 100, m.group(2).strip()[:80])

        out = demolib.render(proj, proj / "demo.mp4", on_log=on_log)
        total = demolib._total_seconds(hook, scenes, close)
        info = {"id": proj.name, "name": title, "title": title,
                "subtitle": _clean(req.subtitle),
                "scenes": len(scenes), "duration": round(total, 2),
                "voice": v.get("name", req.voice_id), "theme": req.theme,
                "aspect": req.aspect, "sfx": bool(req.sfx),
                "has_hook": hook is not None, "has_close": close is not None,
                "repo_path": req.repo_path,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        # Rewrite meta.json with the real numbers, so the Library can show them.
        (proj / "meta.json").write_text(json.dumps(info, indent=2))
        return {"path": str(out), "project": str(proj), "scenes": len(scenes),
                "duration": round(total, 2), "voice": info["voice"],
                "title": title, "theme": req.theme, "aspect": req.aspect,
                "has_hook": hook is not None, "has_close": close is not None}

    _run(jid, work)
    return {"job_id": jid}


@app.get("/library")
def library(limit: int = 40):
    """Every demo this machine has rendered, most recently changed first."""
    out = []
    seen = set()
    dirs = []
    if OUTPUT_DIR.exists():
        dirs.extend(OUTPUT_DIR.iterdir())
    if LEGACY_OUTPUT_DIR.exists() and LEGACY_OUTPUT_DIR.resolve() != OUTPUT_DIR.resolve():
        dirs.extend(LEGACY_OUTPUT_DIR.iterdir())

    # Order by the mp4's mtime, not the directory name: a re-render updates the
    # file but keeps the timestamp baked into the name.
    ready = []
    for d in dirs:
        mp4 = d / "demo.mp4"
        if d.is_dir() and mp4.is_file():
            ready.append((mp4.stat().st_mtime, d, mp4))

    for _, d, mp4 in sorted(ready, key=lambda r: r[0], reverse=True):
        if d.name in seen:
            continue
        seen.add(d.name)
        meta = {}
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        st = mp4.stat()
        out.append({
            "id": d.name,
            "name": meta.get("name") or d.name.rsplit("-", 2)[0].replace("-", " ").title(),
            "subtitle": meta.get("subtitle", ""),
            "path": str(mp4),
            "project": str(d),
            "bytes": st.st_size,
            "scenes": int(meta.get("scenes") or 0),
            "duration": float(meta.get("duration") or 0),
            "voice": meta.get("voice", ""),
            "theme": meta.get("theme", ""),
            "aspect": meta.get("aspect", ""),
            "has_hook": bool(meta.get("has_hook")),
            "has_close": bool(meta.get("has_close")),
            "created": meta.get("created") or datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "modified": datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        })
        if len(out) >= limit:
            break
    return {"demos": out}


@app.delete("/library/{demo_id}")
def delete_demo(demo_id: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", demo_id or ""):
        raise HTTPException(400, "bad demo id")
    d = (OUTPUT_DIR / demo_id).resolve()
    valid = False
    try:
        d.relative_to(OUTPUT_DIR.resolve())
        valid = True
    except ValueError:
        pass
    if not valid and LEGACY_OUTPUT_DIR.exists():
        d_legacy = (LEGACY_OUTPUT_DIR / demo_id).resolve()
        try:
            d_legacy.relative_to(LEGACY_OUTPUT_DIR.resolve())
            d = d_legacy
            valid = True
        except ValueError:
            pass
    if not valid:
        raise HTTPException(400, "bad demo id")
    if not d.is_dir():
        raise HTTPException(404, f"no demo {demo_id!r}")
    shutil.rmtree(d)
    return {"deleted": demo_id}


@app.get("/jobs/{jid}")
def job(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, f"no job {jid!r}")
    return j


@app.exception_handler(Exception)
def unhandled(request, exc):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


def _watch_parent(interval: float = 1.0) -> None:
    """Leave when the app that spawned us is gone.

    The app owns this process, but macOS does not guarantee it gets to say so: a
    SIGTERM to a Cocoa app (or a crash, or a force-quit) kills it without running
    applicationWillTerminate, and this child is left holding the port and several
    GB of weights. The next launch then fails to bind, dies, and the app goes on
    talking to the orphan — which answers /health perfectly while being unable to
    reach the model server. That surfaces as "oMLX refused the connection", so the
    user re-types a correct API key over and over.

    The app passes its pid in VOXDEMO_PARENT_PID. Absent (a hand-run server), we
    watch nothing and behave as before.
    """
    raw = os.environ.get("CODEREEL_PARENT_PID", os.environ.get("VOXDEMO_PARENT_PID", ""))
    if not raw.isdigit():
        return
    parent = int(raw)

    def gone() -> bool:
        try:
            os.kill(parent, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        # Reparenting to launchd is the same event seen from the other side, and
        # it also covers pid reuse — getppid() cannot be fooled by it.
        return os.getppid() != parent

    while not gone():
        time.sleep(interval)
    print(f"[codereel] parent {parent} is gone — shutting down", flush=True)
    # _exit, not sys.exit: uvicorn may be mid-request holding the GIL, and a
    # graceful shutdown there can take as long as the request. The listening
    # socket is closed by the kernel either way, which is the whole point.
    os._exit(0)


if __name__ == "__main__":
    print(f"[codereel] serving on 127.0.0.1:{PORT} (dry_run={DRY_RUN})", flush=True)
    threading.Thread(target=_watch_parent, daemon=True, name="parent-watch").start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
