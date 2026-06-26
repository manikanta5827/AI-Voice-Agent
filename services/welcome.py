"""
Pre-generates and caches welcome audio via Cartesia REST API for zero TTS latency on connect.
"""

import hashlib
import os
from pathlib import Path

import aiofiles
import aiohttp
from loguru import logger

CACHE_DIR = Path("assets")
_audio_cache: bytes | None = None

# Trailing silence so the final word isn't truncated at stream-end on Twilio.
# Cached file stays pure Cartesia output; padding is a playback concern, added on load.
_TAIL_MS = 300


def _pad_tail(pcm: bytes, sample_rate: int = 8000, ms: int = _TAIL_MS) -> bytes:
    return pcm + b"\x00" * (sample_rate * ms // 1000 * 2)  # 16-bit mono silence


def _cache_path(text: str) -> Path:
    # 16-bit signed LE PCM, 8kHz mono. Key on text + voice.
    key = hashlib.sha1(f"{text}|{os.getenv('CARTESIA_VOICE_ID')}".encode()).hexdigest()[:12]
    return CACHE_DIR / f"welcome_{key}.pcm"


async def get_welcome_audio(text: str) -> bytes:
    global _audio_cache
    if _audio_cache is not None:
        return _audio_cache

    path = _cache_path(text)
    if path.exists():
        async with aiofiles.open(path, "rb") as f:
            raw = await f.read()
        logger.info(f"Welcome audio loaded from cache ({len(raw)} bytes)")
    else:
        logger.info("Generating welcome audio via Cartesia...")
        raw = await _generate_cartesia(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(raw)
        logger.info(f"Welcome audio saved to {path} ({len(raw)} bytes)")

    _audio_cache = _pad_tail(raw)
    return _audio_cache


async def _generate_cartesia(text: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": os.getenv("CARTESIA_API_KEY"),
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json",
            },
            json={
                "transcript": text,
                "model_id": "sonic-3.5",
                "voice": {"mode": "id", "id": os.getenv("CARTESIA_VOICE_ID")},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 8000,
                },
                "language": "te",
            },
        ) as resp:
            resp.raise_for_status()
            return await resp.read()
