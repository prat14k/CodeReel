import os
import re
import tempfile
from datetime import datetime

import gradio as gr
import numpy as np
import soundfile as sf
import torch

MODEL_ID = os.environ.get("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
DEVICE = os.environ.get("VOXCPM_DEVICE", "auto")

print("Loading VoxCPM2 ... (first run downloads ~5 GB weights)", flush=True)
from voxcpm import VoxCPM

model = VoxCPM.from_pretrained(
    MODEL_ID,
    load_denoiser=True,
    optimize=(os.environ.get("VOXCPM_OPTIMIZE", "1") == "1"),
    device=DEVICE,
)
SAMPLE_RATE = model.tts_model.sample_rate
print(f"Model ready. sample_rate={SAMPLE_RATE}", flush=True)


# ---------------------------------------------------------------- helpers

def _sanitize(text: str) -> str:
    text = (text or "").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _save_wav(wav: np.ndarray, sample_rate: int) -> str:
    path = os.path.join(tempfile.gettempdir(), f"voxcpm_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.wav")
    sf.write(path, wav, sample_rate)
    return path


def _style_prefix(style: str) -> str:
    style = _sanitize(style)
    if not style:
        return ""
    return f"({style})"


# ---------------------------------------------------------------- generation

def tts_generate(text, style, cfg_value, timesteps, seed):
    text = _sanitize(text)
    if not text:
        raise gr.Error("Please enter some text to synthesize.")
    full_text = _style_prefix(style) + text
    torch.manual_seed(int(seed))
    wav = model.generate(
        text=full_text,
        cfg_value=float(cfg_value),
        inference_timesteps=int(timesteps),
    )
    return _save_wav(wav, SAMPLE_RATE), wav.shape[0] / SAMPLE_RATE


def _extract_audio_path(value) -> str | None:
    # Gradio Audio with type="filepath" normally returns a str path,
    # but be tolerant: dict with path/url, tuple, or empty string.
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("path") or value.get("url") or value.get("name")
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def clone_generate(
    text, reference_audio, prompt_text, use_ultimate,
    style, cfg_value, timesteps, seed, denoise,
):
    text = _sanitize(text)
    if not text:
        raise gr.Error("Please enter some text to synthesize.")
    ref_path = _extract_audio_path(reference_audio)
    print(f"clone_generate: reference_audio raw={type(reference_audio).__name__} path={ref_path!r}", flush=True)
    if not ref_path:
        raise gr.Error(
            "No reference audio received. The mic recording didn't stick — "
            "wait for the waveform to appear after pressing Stop, check the browser mic permission, "
            "or upload a .wav file instead (5–15 s, single speaker)."
        )
    if not os.path.exists(ref_path):
        raise gr.Error(f"Reference audio file not found on server: {ref_path}")
    try:
        info = sf.info(ref_path)
        print(f"clone_generate: ref duration={info.duration:.2f}s sr={info.samplerate} frames={info.frames}", flush=True)
        if info.duration < 1.0:
            raise gr.Error(f"Reference clip is only {info.duration:.1f}s — record at least 3–5 s (ideally 5–15 s).")
    except gr.Error:
        raise
    except Exception as e:
        print(f"clone_generate: could not probe ref audio: {e}", flush=True)
    reference_audio = ref_path
    torch.manual_seed(int(seed))
    if use_ultimate:
        ptext = _sanitize(prompt_text)
        if not ptext:
            raise gr.Error("Ultimate Cloning requires the transcript of the reference audio.")
        wav = model.generate(
            text=text,
            prompt_wav_path=reference_audio,
            prompt_text=ptext,
            reference_wav_path=reference_audio,
            cfg_value=float(cfg_value),
            inference_timesteps=int(timesteps),
            denoise=bool(denoise),
        )
    else:
        full_text = _style_prefix(style) + text
        wav = model.generate(
            text=full_text,
            reference_wav_path=reference_audio,
            cfg_value=float(cfg_value),
            inference_timesteps=int(timesteps),
            denoise=bool(denoise),
        )
    return _save_wav(wav, SAMPLE_RATE), wav.shape[0] / SAMPLE_RATE


# ---------------------------------------------------------------- ui

HEADER = """
# VoxCPM2 Demo
**2B tokenizer-free TTS · 30 languages · 48 kHz · local voice cloning**

Built on [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM) · Weights: [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2)
"""

CLONE_GUIDE = """
**How to clone a voice:**
1. Upload or **record** 5–15 s of clean speech (16 kHz+).
2. *Timbre Cloning* — clones the voice's timbre; optional style prompt steers emotion/pace.
3. *Ultimate Cloning* — also paste the clip's transcript for near-perfect reproduction of rhythm and nuance.
"""

with gr.Blocks(title="VoxCPM2 Demo") as demo:
    gr.Markdown(HEADER)

    with gr.Tabs():
        # -------------------------------------------------- cloning tab
        with gr.Tab("Voice Cloning") as tab_clone:
            gr.Markdown(CLONE_GUIDE)
            with gr.Row():
                with gr.Column(scale=1):
                    clone_text = gr.Textbox(
                        label="Text to synthesize",
                        placeholder="Say anything you want in the cloned voice...",
                        lines=3,
                        value="Hello! This is my cloned voice, speaking with Vox C P M two, running locally on this machine.",
                    )
                    ref_upload = gr.Audio(
                        label="Reference audio — upload (.wav, 5–15 s)",
                        sources=["upload"],
                        type="filepath",
                        format="wav",
                    )
                    ref_mic = gr.Audio(
                        label="Reference audio — or record with mic (press Stop, wait for waveform)",
                        sources=["microphone"],
                        type="filepath",
                        format="wav",
                    )
                    with gr.Accordion("Ultimate Cloning (reference + transcript)", open=False):
                        use_ultimate = gr.Checkbox(
                            label="Enable Ultimate Cloning",
                            value=False,
                            info="Uses the transcript for continuation-style cloning — best similarity.",
                        )
                        prompt_text = gr.Textbox(
                            label="Transcript of the reference audio",
                            placeholder="Type exactly what is said in the clip...",
                            lines=2,
                        )
                    with gr.Accordion("Advanced", open=False):
                        clone_style = gr.Textbox(
                            label="Style control (timbre cloning only)",
                            placeholder="e.g. cheerful tone, slightly faster",
                            info="Natural-language instruction placed in parentheses before the text.",
                        )
                        cfg_value = gr.Slider(1.0, 4.0, value=2.0, step=0.1, label="CFG (guidance scale)")
                        timesteps = gr.Slider(4, 40, value=10, step=1, label="Inference timesteps")
                        seed = gr.Number(value=42, precision=0, label="Seed")
                        denoise = gr.Checkbox(value=True, label="Denoise reference audio")
                    clone_btn = gr.Button("Generate cloned speech", variant="primary")
                with gr.Column(scale=1):
                    clone_audio = gr.Audio(label="Output", type="filepath")
                    clone_status = gr.Markdown()

            def clone_run(text, upload, mic, prompt_text, use_ultimate, style, cfg_v, steps, seed, denoise, progress=gr.Progress()):
                ref = _extract_audio_path(mic) or _extract_audio_path(upload)
                progress(0.1, desc="Cloning voice...")
                path, dur = clone_generate(text, ref, prompt_text, use_ultimate, style, cfg_v, steps, seed, denoise)
                return path, f"**Done** — {dur:.1f}s of audio generated."

            clone_btn.click(
                clone_run,
                inputs=[
                    clone_text, ref_upload, ref_mic, prompt_text, use_ultimate,
                    clone_style, cfg_value, timesteps, seed, denoise,
                ],
                outputs=[clone_audio, clone_status],
                show_progress=True,
            )

        # -------------------------------------------------- design tab
        with gr.Tab("Voice Design"):
            gr.Markdown("Describe a brand-new voice in natural language — no reference audio needed.")
            with gr.Row():
                with gr.Column():
                    design_desc = gr.Textbox(
                        label="Voice description (goes in parentheses before the text)",
                        placeholder="A young woman, gentle and sweet voice",
                        value="A young woman, gentle and sweet voice",
                    )
                    design_text = gr.Textbox(
                        label="Text to synthesize",
                        value="Hello, welcome to the VoxCPM voice design demo. I can be anyone you describe.",
                        lines=3,
                    )
                    with gr.Accordion("Advanced", open=False):
                        design_cfg = gr.Slider(1.0, 4.0, value=2.0, step=0.1, label="CFG (guidance scale)")
                        design_timesteps = gr.Slider(4, 40, value=10, step=1, label="Inference timesteps")
                        design_seed = gr.Number(value=42, precision=0, label="Seed")
                    design_btn = gr.Button("Generate designed voice", variant="primary")
                with gr.Column():
                    design_audio = gr.Audio(label="Output", type="filepath")

            def design_run(desc, text, cfg_v, steps, seed):
                desc, text = _sanitize(desc), _sanitize(text)
                if not text:
                    raise gr.Error("Please enter some text to synthesize.")
                full = f"({desc}){text}" if desc else text
                torch.manual_seed(int(seed))
                wav = model.generate(
                    text=full,
                    cfg_value=float(cfg_v),
                    inference_timesteps=int(steps),
                )
                return _save_wav(wav, SAMPLE_RATE)

            design_btn.click(
                design_run,
                inputs=[design_desc, design_text, design_cfg, design_timesteps, design_seed],
                outputs=[design_audio],
            )

        # -------------------------------------------------- plain tts tab
        with gr.Tab("Plain TTS"):
            gr.Markdown("Plain text-to-speech in 30 languages. Just type — no language tag needed.")
            with gr.Row():
                with gr.Column():
                    tts_text = gr.Textbox(
                        label="Text to synthesize",
                        value="VoxCPM is a tokenizer-free text-to-speech system that generates highly natural and expressive speech.",
                        lines=3,
                    )
                    with gr.Accordion("Advanced", open=False):
                        tts_cfg = gr.Slider(1.0, 4.0, value=2.0, step=0.1, label="CFG (guidance scale)")
                        tts_timesteps = gr.Slider(4, 40, value=10, step=1, label="Inference timesteps")
                        tts_seed = gr.Number(value=42, precision=0, label="Seed")
                    tts_btn = gr.Button("Generate speech", variant="primary")
                with gr.Column():
                    tts_audio = gr.Audio(label="Output", type="filepath")

            def tts_run(text, cfg_v, steps, seed):
                return tts_generate(text, "", cfg_v, steps, seed)[0]

            tts_btn.click(
                tts_run,
                inputs=[tts_text, tts_cfg, tts_timesteps, tts_seed],
                outputs=[tts_audio],
            )

    gr.Markdown(
        "> ⚠️ Voice cloning can produce highly realistic synthetic speech. Use only with consent; "
        "clearly label AI-generated content. Apache-2.0 — OpenBMB."
    )

if __name__ == "__main__":
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "8808")),
        share=False,
        theme=gr.themes.Soft(),
        css="footer {display: none !important}",
    )
