"""Best-effort cold->hot warmup, fired once at server startup so the FIRST real
call doesn't pay provider model spin-up + DNS/TLS on the live audio path.

What this actually buys: STT/TTS/LLM services are built fresh per call (pipecat
lifecycle), so the warmup's own connections are thrown away. What carries over to
the real call is provider-side model warmth + the OS DNS cache (and TLS session
reuse). The per-call websocket/TLS handshake itself can't be skipped without
reusing service instances across calls, which pipecat doesn't support cleanly.

ponytail: startup-only warm, and warmth decays once the provider goes idle. If
calls are sparse and first-after-idle is still cold, add a periodic re-warm
(asyncio task every N min calling warmup_all).
"""

import asyncio
import os
import time
from contextlib import suppress

from loguru import logger


def enabled() -> bool:
    return os.getenv("WARMUP", "1").lower() in ("1", "true", "yes", "on")


async def _llm_request(client, model: str):
    """Fire a 1-token completion via whichever SDK client the service holds.
    Two shapes cover every provider here: AsyncOpenAI (ai_gateway/openai/deepseek/
    sarvam/groq) and google-genai (gemini)."""
    if hasattr(client, "chat"):  # AsyncOpenAI-compatible
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
    elif hasattr(client, "aio"):  # google.genai
        await client.aio.models.generate_content(model=model, contents="hi")
    else:
        raise RuntimeError(f"unknown LLM client shape: {type(client).__name__}")


async def _warm_llm():
    from services.llm import create_active_llm

    llm = create_active_llm()
    # ponytail: reaches into the pipecat service's private client/settings; these
    # have been stable across 1.x. If they move, fall back to a direct SDK call.
    client = getattr(llm, "_client", None)
    model = getattr(getattr(llm, "_settings", None), "model", None)
    if client is None or not model:
        raise RuntimeError("LLM client/model not introspectable")
    await _llm_request(client, model)
    with suppress(Exception):
        await client.close()


async def _warm_ws(make):
    """Open the provider websocket exactly as a real call would (DNS + TLS + WS
    handshake + provider session config), then drop it."""
    svc = make()
    await svc._connect_websocket()  # no-op event handlers; primes the network path
    ws = getattr(svc, "_websocket", None)
    if ws is not None:
        with suppress(Exception):
            await ws.close()


async def _warm_stt():
    from services.stt import create_stt

    await _warm_ws(create_stt)


async def _warm_tts():
    from services.tts import create_tts

    await _warm_ws(create_tts)


async def warmup_all(timeout: float = 8.0):
    """Warm LLM + STT + TTS concurrently. Each leg is best-effort: a failure is
    logged and never propagates, so a flaky provider can't block server boot."""
    if not enabled():
        logger.info("warmup disabled (WARMUP=0)")
        return

    async def guarded(coro, label):
        t = time.monotonic()
        try:
            await asyncio.wait_for(coro, timeout)
            logger.info(f"warmup {label}: hot ({(time.monotonic() - t) * 1000:.0f}ms)")
        except Exception as e:
            logger.warning(f"warmup {label} failed (non-fatal): {e}")

    await asyncio.gather(
        guarded(_warm_llm(), "llm"),
        guarded(_warm_stt(), "stt"),
        guarded(_warm_tts(), "tts"),
    )


async def _selfcheck():
    """Runnable check: client-shape dispatch hits the right SDK, and warmup_all
    swallows a failing leg instead of raising."""
    calls = []

    class FakeOpenAI:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    calls.append(("openai", kw["model"]))

    class FakeGenAI:
        class aio:
            class models:
                @staticmethod
                async def generate_content(**kw):
                    calls.append(("genai", kw["model"]))

    await _llm_request(FakeOpenAI(), "m1")
    await _llm_request(FakeGenAI(), "m2")
    assert calls == [("openai", "m1"), ("genai", "m2")], calls

    try:
        await _llm_request(object(), "x")
    except RuntimeError:
        pass
    else:
        raise AssertionError("unknown client shape should raise")

    # A leg that raises must not propagate out of warmup_all. Stub the legs in the
    # running module's globals (warmup_all resolves them there).
    async def boom():
        raise RuntimeError("provider down")

    g = globals()
    g["_warm_llm"] = g["_warm_stt"] = g["_warm_tts"] = boom
    os.environ["WARMUP"] = "1"
    await warmup_all(timeout=1.0)  # must return, not raise

    os.environ["WARMUP"] = "0"
    await warmup_all()  # disabled path is a no-op
    print("warmup selfcheck OK")


if __name__ == "__main__":
    asyncio.run(_selfcheck())
