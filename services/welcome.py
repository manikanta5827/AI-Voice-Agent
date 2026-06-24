"""
Pre-generates welcome audio once via Cartesia REST API and caches to disk.
On every subsequent call, reads from file — zero TTS latency on connect.

Cache key = hash(text + voice id), so changing the welcome text or voice
auto-regenerates. No manual deletion needed.
"""

import hashlib
import os
from pathlib import Path

import aiofiles
import aiohttp
from loguru import logger

CACHE_DIR = Path("assets")
_audio_cache: bytes | None = None


def _cache_path(text: str) -> Path:
    # 16-bit signed LE PCM, 8kHz mono. Key on text + voice so stale audio can't linger.
    key = hashlib.sha1(f"{text}|{os.getenv('CARTESIA_VOICE_ID')}".encode()).hexdigest()[:12]
    return CACHE_DIR / f"welcome_{key}.pcm"


async def get_welcome_audio(text: str) -> bytes:
    global _audio_cache
    if _audio_cache is not None:
        return _audio_cache

    path = _cache_path(text)
    if path.exists():
        async with aiofiles.open(path, "rb") as f:
            _audio_cache = await f.read()
        logger.info(f"Welcome audio loaded from cache ({len(_audio_cache)} bytes)")
        return _audio_cache

    logger.info("Generating welcome audio via Cartesia...")
    _audio_cache = await _generate_cartesia(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "wb") as f:
        await f.write(_audio_cache)
    logger.info(f"Welcome audio saved to {path} ({len(_audio_cache)} bytes)")
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
