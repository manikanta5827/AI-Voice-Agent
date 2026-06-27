"""Extract background noise (accompaniment) from a competitor audio file.

Uses Demucs (htdemucs model) for source separation, then resamples
the accompaniment to 8kHz 16-bit mono PCM for real-time mixing in the pipeline.

Usage:
    source .venv/bin/activate
    python scripts/extract_noise.py [competitor-audio/realestate.mp3]
"""

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from demucs import pretrained
from demucs.apply import apply_model
from demucs.separate import load_track


SAMPLE_RATE = 8000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default="competitor-audio/ias_academy_appointment_confirmation.mp3")
    parser.add_argument("--out", default="assets/bg_noise_loop.pcm")
    parser.add_argument("--trim-start-s", type=float, default=0)
    parser.add_argument("--trim-end-s", type=float, default=0)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Loading htdemucs model...")
    model = pretrained.get_model("htdemucs")
    model.to("cpu")
    model.eval()

    # Get model sources and sample rate from first sub-model
    if hasattr(model, "models"):
        sub_model = model.models[0]
    else:
        sub_model = model
    sources = list(sub_model.sources)
    model_sr = sub_model.samplerate
    print(f"Model sources: {sources}, sample rate: {model_sr}")

    # Load and resample mix to model's sample rate
    print(f"Loading {input_path.name}...")
    wav, file_sr = sf.read(str(input_path), always_2d=True)
    if wav.shape[-1] > 1:
        wav = wav.mean(axis=1)
    wav = wav.flatten()

    if file_sr != model_sr:
        tmp = Path("/tmp/_noise_resample_in.wav")
        tmp2 = Path("/tmp/_noise_resample_out.wav")
        sf.write(str(tmp), wav, file_sr, subtype="PCM_16")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp), "-ar", str(model_sr), "-ac", "1",
             "-sample_fmt", "s16", str(tmp2)],
            capture_output=True, check=True,
        )
        wav, _ = sf.read(str(tmp2), dtype="float32")
        tmp.unlink(); tmp2.unlink()

    # HTDemucs expects 2 channels (stereo) — duplicate mono to stereo
    wav_stereo = np.stack([wav, wav], axis=0)  # (2, samples)
    mix = torch.from_numpy(wav_stereo).float().unsqueeze(0)  # (1, 2, samples)

    print(f"Separating ({len(mix[0,0])/model_sr:.1f}s)...")
    with torch.no_grad():
        sources_out = apply_model(model, mix, split=True, overlap=0.25, progress=True)[0]
    # sources_out: (num_sources, channels, samples)

    # Combine all non-vocal stems into accompaniment
    non_vocal_indices = [i for i, s in enumerate(sources) if s != "vocals"]
    accompaniment = sources_out[non_vocal_indices].sum(dim=0).numpy()  # (channels, samples)
    if accompaniment.ndim == 2 and accompaniment.shape[0] > 1:
        accompaniment = accompaniment.mean(axis=0)
    elif accompaniment.ndim == 2:
        accompaniment = accompaniment[0]
    # Now: (samples,)

    # Trim
    start_samp = int(args.trim_start_s * model_sr)
    end_samp = int(args.trim_end_s * model_sr)
    if start_samp or end_samp:
        end = len(accompaniment) - end_samp if end_samp else len(accompaniment)
        accompaniment = accompaniment[start_samp:end]

    print(f"Accompaniment: {len(accompaniment)/model_sr:.1f}s, resampling to {SAMPLE_RATE}Hz...")

    # Resample to 8kHz via ffmpeg
    tmp_in = Path("/tmp/_noise_extract_in.wav")
    tmp_out = Path("/tmp/_noise_extract_out.wav")
    sf.write(str(tmp_in), accompaniment, model_sr, subtype="PCM_16")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(tmp_in),
         "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16", str(tmp_out)],
        capture_output=True, check=True,
    )
    tmp_in.unlink()
    noise, out_sr = sf.read(str(tmp_out), dtype="int16")
    tmp_out.unlink()

    print(f"Resampled: {len(noise)/out_sr:.1f}s @ {out_sr}Hz")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(noise.tobytes())
    print(f"Saved: {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
