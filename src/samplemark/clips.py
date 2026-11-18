"""Writing clips: a WAV and a JSON sidecar, each written atomically."""

import json
import os
import re
import wave
from pathlib import Path


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "untitled"


def clip_basename(outdir: Path, date: str, slug: str) -> str:
    prefix = f"{date}_{slug}_"
    numbers = [0]
    if outdir.is_dir():
        for path in outdir.glob(f"{prefix}*.wav"):
            tail = path.stem[len(prefix):]
            if tail.isdigit():
                numbers.append(int(tail))
    return f"{prefix}{max(numbers) + 1:03d}"


def write_clip(outdir: Path, basename: str, pcm: bytes, rate: int,
               channels: int, sidecar: dict) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    wav_path = outdir / f"{basename}.wav"
    tmp = outdir / f".{basename}.wav.tmp"
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.replace(tmp, wav_path)
    json_tmp = outdir / f".{basename}.json.tmp"
    json_tmp.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n")
    os.replace(json_tmp, outdir / f"{basename}.json")
    return wav_path
