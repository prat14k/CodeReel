#!/usr/bin/env python3
"""VoxDemo sidecar — VoxCPM2 voice store + HyperFrames demo rendering over localhost HTTP.

The macOS app spawns this and talks JSON to 127.0.0.1. Everything runs on-device.

    .venv/bin/python server.py            # serve on 127.0.0.1:8809
    .venv/bin/python server.py --dry-run  # API only, no model (for UI work)
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import demo as demolib

DRY_RUN = "--dry-run" in sys.argv
MODEL_ID = os.environ.get("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
DEVICE = os.environ.get("VOXCPM_DEVICE", "auto")
PORT = int(os.environ.get("VOXDEMO_PORT", "8809"))

SUPPORT = Path(os.environ.get(
    "VOXDEMO_HOME", Path.home() / "Library" / "Application Support" / "VoxDemo"))
VOICES_DIR = SUPPORT / "voices"
CACHE_DIR = SUPPORT / "cache"
OUTPUT_DIR = Path(os.environ.get("VOXDEMO_OUTPUT", Path.home() / "Movies" / "VoxDemo"))
for d in (VOICES_DIR, CACHE_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Enrollment line for preset voices: read once per preset, then reused as the
# cloning reference so a preset sounds identical across scenes and sessions.
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

# ---------------------------------------------------------------- model

model = None
SAMPLE_RATE = 48000
_state = {"ready": DRY_RUN, "error": "", "loading": not DRY_RUN}
_gpu_lock = threading.Lock()  # VoxCPM inference is serialized


def _load_model() -> None:
    global model, SAMPLE_RATE
    try:
        from voxcpm import VoxCPM
        model = VoxCPM.from_pretrained(
            MODEL_ID, load_denoiser=True,
            optimize=(os.environ.get("VOXCPM_OPTIMIZE", "1") == "1"), device=DEVICE)
        SAMPLE_RATE = model.tts_model.sample_rate
        _state.update(ready=True, loading=False)
        print(f"[voxdemo] model ready sample_rate={SAMPLE_RATE}", flush=True)
    except Exception as e:  # surfaced in /health so the app can show it
        _state.update(ready=False, loading=False, error=f"{type(e).__name__}: {e}")
        print(f"[voxdemo] model load failed: {e}", flush=True)


if not DRY_RUN:
    threading.Thread(target=_load_model, daemon=True).start()


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


def _generate(text: str, ref: Path | None, transcript: str, style: str,
              cfg: float, timesteps: int, seed: int, denoise: bool = False) -> np.ndarray:
    import torch
    m = _require_model()
    text = _clean(text)
    if not text:
        raise HTTPException(400, "nothing to say — text is empty")
    kwargs = dict(cfg_value=float(cfg), inference_timesteps=int(timesteps))
    with _gpu_lock:
        torch.manual_seed(int(seed))
        if ref and transcript:
            # Ultimate cloning: reference audio + its transcript, best similarity.
            wav = m.generate(text=text, prompt_wav_path=str(ref), prompt_text=_clean(transcript),
                             reference_wav_path=str(ref), denoise=denoise, **kwargs)
        elif ref:
            wav = m.generate(text=(f"({_clean(style)})" if style else "") + text,
                             reference_wav_path=str(ref), denoise=denoise, **kwargs)
        else:
            wav = m.generate(text=(f"({_clean(style)})" if style else "") + text, **kwargs)
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
                              "description": desc, "transcript": "", "enrolled": False}
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
    wav = _generate(ENROLL_TEXT, None, "", desc, cfg=2.0, timesteps=16, seed=ENROLL_SEED)
    d.mkdir(parents=True, exist_ok=True)
    _write(wav, d / "ref.wav")
    return _save_voice({"id": vid, "name": name, "kind": "preset", "description": desc,
                        "transcript": ENROLL_TEXT,
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def _slug(text: str, fallback: str = "voice") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or fallback


# ---------------------------------------------------------------- jobs

JOBS: dict[str, dict] = {}
_pool = ThreadPoolExecutor(max_workers=1)  # one generation at a time


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
    print(f"[voxdemo] {jid} {j['progress']:.0%} {message}", flush=True)


def _run(jid: str, fn) -> None:
    def wrapped():
        try:
            JOBS[jid]["result"] = fn(jid)
            JOBS[jid].update(state="done", progress=1.0, message="done")
        except Exception as e:
            msg = str(getattr(e, "detail", None) or e)
            JOBS[jid].update(state="error", error=msg, message=msg[:400])
            print(f"[voxdemo] {jid} failed: {e}", flush=True)
    _pool.submit(wrapped)


# ---------------------------------------------------------------- api

app = FastAPI(title="VoxDemo")


class CloneReq(BaseModel):
    name: str
    source_path: str
    transcript: str = ""
    denoise: bool = True


class SpeakReq(BaseModel):
    voice_id: str
    text: str
    style: str = ""
    cfg: float = 2.0
    timesteps: int = 12
    seed: int = 42


class SceneIn(BaseModel):
    text: str
    heading: str = ""
    media: str = ""


class DemoReq(BaseModel):
    title: str
    subtitle: str = ""
    voice_id: str
    script: str = ""
    scenes: list[SceneIn] = []
    theme: str = "midnight"
    aspect: str = "landscape"
    style: str = ""
    cfg: float = 2.0
    timesteps: int = 12
    seed: int = 42


@app.get("/health")
def health():
    return {"ready": _state["ready"], "loading": _state["loading"], "error": _state["error"],
            "dry_run": DRY_RUN, "model": MODEL_ID, "device": DEVICE,
            "sample_rate": SAMPLE_RATE, "output_dir": str(OUTPUT_DIR),
            "themes": list(demolib.THEMES), "aspects": list(demolib.ASPECTS)}


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
                        "transcript": _clean(req.transcript), "denoise": bool(req.denoise),
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})


@app.delete("/voices/{vid}")
def delete_voice(vid: str):
    import shutil
    d = _voice_dir(vid)
    if not d.exists():
        raise HTTPException(404, f"voice {vid!r} not found")
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
        _step(jid, 0.35, f"speaking as {v['name']}")
        wav = _generate(req.text, ref, v.get("transcript", ""), req.style,
                        req.cfg, req.timesteps, req.seed, v.get("denoise", False))
        path, dur = _write(wav, CACHE_DIR / f"speak_{jid}.wav")
        return {"path": str(path), "duration": round(dur, 2), "voice": v["name"]}

    _run(jid, work)
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
    jid = _job("demo")

    def work(jid):
        _step(jid, 0.02, "preparing voice")
        if not (_voice_dir(req.voice_id) / "ref.wav").exists():
            ensure_enrolled(req.voice_id)
        v = _read_voice(_voice_dir(req.voice_id)) or {}
        ref = _voice_dir(req.voice_id) / "ref.wav"

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        proj = OUTPUT_DIR / f"{_slug(title, 'demo')}-{stamp}"
        scenes, n = [], len(scenes_in)
        for i, s in enumerate(scenes_in, start=1):
            _step(jid, 0.05 + 0.65 * (i - 1) / n, f"narrating scene {i} of {n}")
            wav = _generate(s.text, ref, v.get("transcript", ""), req.style,
                            req.cfg, req.timesteps, req.seed + i, v.get("denoise", False))
            path, dur = _write(wav, CACHE_DIR / jid / f"scene{i}.wav")
            media = str(Path(s.media).expanduser()) if _clean(s.media) else ""
            if media and not Path(media).is_file():
                raise HTTPException(400, f"scene {i} media not found: {media}")
            scenes.append(demolib.Scene(text=_clean(s.text), heading=_clean(s.heading),
                                        media=media, audio=str(path), duration=dur))

        _step(jid, 0.72, "building composition")
        demolib.write_project(proj, title, _clean(req.subtitle), scenes, req.theme, req.aspect)
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
        total = demolib.TITLE_CARD_SECONDS + sum(s.duration + demolib.SCENE_TAIL_SECONDS
                                                 for s in scenes)
        return {"path": str(out), "project": str(proj), "scenes": len(scenes),
                "duration": round(total, 2), "voice": v.get("name", req.voice_id)}

    _run(jid, work)
    return {"job_id": jid}


@app.get("/jobs/{jid}")
def job(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, f"no job {jid!r}")
    return j


@app.exception_handler(Exception)
def unhandled(request, exc):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    print(f"[voxdemo] serving on 127.0.0.1:{PORT} (dry_run={DRY_RUN})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
