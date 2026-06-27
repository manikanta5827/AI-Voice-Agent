import asyncio
import os
import time
from contextlib import suppress

from loguru import logger
from pipecat.services.openai.llm import OpenAILLMService

# ---------------------------------------------------------------------------
# BASE_PROMPT — HOW to speak. Language mechanics shared by EVERY customer:
# brevity, spoken Telugu, language mixing, number/TTS rules. Business-specific
# identity, tone, and case data live in business.py (the single per-customer file).
# ---------------------------------------------------------------------------
BASE_PROMPT = (
    "# Reply length (HARDEST RULE — this is a phone call)\n"
    "1-2 short sentences, ~30 words max. ONE idea per turn, one option then stop. "
    "Always end with EXACTLY ONE follow-up question that moves things forward — ask "
    "it, but never stack two questions in one reply. Never dump multiple plans or "
    "premiums in one breath.\n\n"

    "# Spoken Telugu (not textbook)\n"
    "Real spoken Telugu is short and broken — drop words people don't say, let "
    "English words carry the meaning.\n"
    "  ❌ 'budget ఎంత ఉంది నెలకి?'   ✅ 'budget ఎంత సార్?'\n"
    "Contractions: చూస్తా (not చూస్తాను), చెప్తా, చేస్తా; ఏంటి (not ఏమిటి); commands "
    "-ండి: చెప్పండి, ఉండండి. AP future uses -ుద్ది not -ుంది: వస్తుద్ది, అవుద్ది, మారుద్ది.\n"
    "'or' questions: 'family కోసమా సార్, లేక individual?'\n\n"

    "# Language mixing\n"
    "Telugu is the base; these stay English: invoice, payment, amount, due, overdue, "
    "pending, discount, offer, deadline, legal, notice, account, UPI, online, link, "
    "settle, balance, days, today, tomorrow, number, details, last warning.\n"
    "Address: 'సార్' for men, 'అమ్మా' for women — but say it hard, not respectfully. "
    "Particles stay Telugu: కానీ, అయితే, కూడా, మళ్ళీ, కదా.\n\n"

    "# Numbers (TTS can't read digits or ₹)\n"
    "Spell in Telugu words: ₹500 → ఐదు వందల రూపాయలు, ₹5 lakh → ఐదు లక్షల రూపాయలు. "
    "Phone/policy digits one by one: ఒకటి రెండు మూడు నాలుగు ఐదు...\n"
)


# ---------------------------------------------------------------------------
# Build the system prompt: shared language mechanics + per-customer business config.
# ---------------------------------------------------------------------------
from datetime import date

from business import BUSINESS_PROMPT

def get_system_prompt() -> str:
    today = date.today().strftime("%B %d, %Y")
    date_context = f"\n\n# Today\nToday's date is {today}. Use this to answer if asked.\n"
    return BASE_PROMPT + "\n\n---\n\n" + BUSINESS_PROMPT + date_context


from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.groq.llm import GroqLLMService

# Safety cap for max tokens (brevity is enforced in prompt).
MAX_REPLY_TOKENS = 512


class _Hedge:
    """Request hedging for any pipecat LLM service: fire N identical streams per
    turn and stream from whichever yields its first token first; cancel the losers.
    Cuts the tail latency from a single request hitting a slow/janky network path
    mid-conversation. Loser is killed at first byte, so wasted tokens are minimal.

    Provider-agnostic — mix into a service and override its streaming seam to call
    `self._hedge_streams(super().<seam>, context)`. Hedging touches only the stream;
    all frame/TTFB/context plumbing stays in the parent. No tool-calling here, so
    racing is safe (a single response is consumed; the rest are dropped)."""

    def _set_hedge(self, hedge: int):
        self._hedge_n = max(1, hedge)

    async def _hedge_streams(self, parent_seam, context):
        # parent_seam is an already-bound `super().<seam>` — calling it N times
        # issues N independent requests (super() is resolved in the caller frame,
        # which keeps its __class__ cell; gathering it directly would not).
        if getattr(self, "_hedge_n", 1) == 1:
            return await parent_seam(context)
        # ponytail: start_ttfb_metrics runs once per stream (parent side) — the last
        # start wins; TTFB is approximate under hedging. Fine for our logging.
        t0 = time.monotonic()  # request launch — measure winner TTFT from here
        streams = await asyncio.gather(
            *(parent_seam(context) for _ in range(self._hedge_n))
        )
        return self._race(streams, t0)

    async def _race(self, streams, t0):
        # Verification log (enable DEBUG_TTFB): which of the N streams won and its
        # time-to-first-token. Across turns you should see the winner index vary and
        # the TTFT track the fastest path — that's the hedge actually working.
        debug = os.getenv("DEBUG_TTFB", "").lower() in ("1", "true", "yes", "on")
        idx = {id(s): i for i, s in enumerate(streams)}
        tasks = {asyncio.ensure_future(s.__anext__()): s for s in streams}
        winner = first_chunk = None
        last_exc = None
        losses = 0  # streams that errored/ended on first byte before a winner emerged
        pending = set(tasks)
        while pending and winner is None:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                try:
                    first_chunk = t.result()
                except StopAsyncIteration:
                    losses += 1
                    continue  # empty stream — let the others race
                except Exception as e:  # this stream errored on first byte
                    last_exc = e
                    losses += 1
                    continue
                winner = tasks[t]
                break

        if debug and winner is not None:
            note = f", {losses} stream(s) failed first-byte" if losses else ""
            logger.info(
                f"hedge: stream {idx[id(winner)] + 1}/{len(streams)} won, "
                f"ttft={(time.monotonic() - t0) * 1000:.0f}ms{note}"
            )

        # Kill every loser: cancel its in-flight first-token pull, close its stream.
        for t, s in tasks.items():
            if s is winner:
                continue
            t.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await t
            with suppress(Exception):
                aclose = getattr(s, "aclose", None)
                if aclose:
                    await aclose()

        if winner is None:
            raise last_exc or RuntimeError("all hedged LLM streams failed")

        # finally closes the winner too — on a barge-in the consumer aclose()s this
        # generator mid-stream, and the open stream must not leak.
        try:
            yield first_chunk
            async for chunk in winner:
                yield chunk
        finally:
            with suppress(Exception):
                aclose = getattr(winner, "aclose", None)
                if aclose:
                    await aclose()


class HedgedOpenAILLMService(_Hedge, OpenAILLMService):
    """OpenAI-compatible (ai_gateway / openai / deepseek / sarvam). Seam: get_chat_completions."""

    def __init__(self, *args, hedge: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_hedge(hedge)

    async def get_chat_completions(self, context):
        return await self._hedge_streams(super().get_chat_completions, context)


class HedgedGroqLLMService(_Hedge, GroqLLMService):
    """Groq inherits the OpenAI seam but needs its own client init, so its own subclass."""

    def __init__(self, *args, hedge: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_hedge(hedge)

    async def get_chat_completions(self, context):
        return await self._hedge_streams(super().get_chat_completions, context)


class HedgedGoogleLLMService(_Hedge, GoogleLLMService):
    """Gemini. Seam: _stream_content."""

    def __init__(self, *args, hedge: int = 2, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_hedge(hedge)

    async def _stream_content(self, context):
        return await self._hedge_streams(super()._stream_content, context)


def _hedge_n() -> int:
    """Hedge factor for all network LLMs. N parallel requests/turn, fastest first-token
    wins, losers cancelled. 1 disables (single request)."""
    return int(os.getenv("LLM_HEDGE", "2"))


def create_llm() -> OpenAILLMService:
    model_name = os.getenv("AI_GATEWAY_MODEL", "anthropic/claude-haiku-4.5")
    return HedgedOpenAILLMService(
        api_key=os.getenv("AI_GATEWAY_API_KEY"),
        settings=OpenAILLMService.Settings(model=model_name, max_tokens=MAX_REPLY_TOKENS),
        base_url="https://ai-gateway.vercel.sh/v1",
        hedge=_hedge_n(),
    )


def create_openai_llm() -> OpenAILLMService:
    """Direct OpenAI inference (no gateway hop). Default model picked by benchmark:
    best balance of low TTFT + native Telugu quality, non-reasoning (no thinking hop).
    gpt-5* models reject `max_tokens` — use `max_completion_tokens` (works for 4.x too)."""
    model_name = os.getenv("OPENAI_MODEL", "gpt-5.1-chat-latest")
    return HedgedOpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            model=model_name, max_completion_tokens=MAX_REPLY_TOKENS
        ),
        hedge=_hedge_n(),
    )


def create_deepseek_llm() -> OpenAILLMService:
    """DeepSeek via its OpenAI-compatible endpoint. thinking MUST be disabled —
    default-on reasoning makes it ~5x slower (3.5s vs 0.96s TTFT in benchmark)."""
    model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    return HedgedOpenAILLMService(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        settings=OpenAILLMService.Settings(
            model=model_name,
            max_tokens=MAX_REPLY_TOKENS,
            extra={"extra_body": {"thinking": {"type": "disabled"}}},
        ),
        hedge=_hedge_n(),
    )


def create_sarvam_llm() -> OpenAILLMService:
    """Sarvam via its OpenAI-compatible endpoint. Most native Telugu, but slower."""
    model_name = os.getenv("SARVAM_MODEL", "sarvam-105b")
    return HedgedOpenAILLMService(
        api_key=os.getenv("SARVAM_API_KEY"),
        base_url="https://api.sarvam.ai/v1",
        settings=OpenAILLMService.Settings(model=model_name, max_tokens=MAX_REPLY_TOKENS),
        hedge=_hedge_n(),
    )


def create_gemini_llm() -> GoogleLLMService:
    # flash-lite: lowest TTFT (the dominant latency leg). Set GEMINI_MODEL=gemini-2.5-flash for max quality.
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return HedgedGoogleLLMService(
        api_key=os.getenv("GEMINI_API_KEY"),
        settings=GoogleLLMService.Settings(model=model_name, max_tokens=MAX_REPLY_TOKENS),
        hedge=_hedge_n(),
    )


def create_groq_llm() -> GroqLLMService:
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    return HedgedGroqLLMService(
        api_key=os.getenv("GROQ_API_KEY"),
        settings=GroqLLMService.Settings(model=model_name, max_tokens=MAX_REPLY_TOKENS),
        hedge=_hedge_n(),
    )


def create_active_llm():
    """Build the LLM service for the configured LLM_PROVIDER. Single source of
    truth shared by the call pipeline (bot.run_bot) and startup warmup, so both
    always exercise the same provider."""
    provider = os.getenv("LLM_PROVIDER", "ai_gateway")
    logger.info(f"LLM provider: {provider}, hedge: {_hedge_n()}")
    match provider:
        case "huggingface":
            from services.hugging_llm import create_huggingface_llm
            return create_huggingface_llm()
        case "openai":
            return create_openai_llm()
        case "gemini":
            return create_gemini_llm()
        case "groq":
            return create_groq_llm()
        case "deepseek":
            return create_deepseek_llm()
        case "sarvam":
            return create_sarvam_llm()
        case _:
            return create_llm()


async def _selfcheck():
    """Race picks the fastest first-token, drops/closes the loser, tolerates a
    first-byte error, and raises only when every stream fails."""

    class FakeStream:
        def __init__(self, chunks, delay, boom=False):
            self._chunks, self._delay, self._boom = list(chunks), delay, boom
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(self._delay)
            if self._boom:
                raise ValueError("network blip")
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

        async def aclose(self):
            self.closed = True

    svc = HedgedGoogleLLMService(api_key="x", hedge=2)

    # Fastest first-token wins; loser closed; full winner streamed.
    fast, slow = FakeStream(["A", "B"], 0.01), FakeStream(["X", "Y"], 0.30)
    out = [c async for c in svc._race([slow, fast], time.monotonic())]
    assert out == ["A", "B"], out
    assert slow.closed, "loser stream not closed"

    # A stream that errors on first byte loses to a healthy one.
    boom, good = FakeStream([], 0.0, boom=True), FakeStream(["Z"], 0.05)
    out = [c async for c in svc._race([boom, good], time.monotonic())]
    assert out == ["Z"], out

    # All fail -> the error propagates.
    try:
        [c async for c in svc._race([FakeStream([], 0.0, boom=True),
                                     FakeStream([], 0.0, boom=True)], time.monotonic())]
    except ValueError:
        pass
    else:
        raise AssertionError("expected failure when all streams error")

    print("llm hedge selfcheck OK")


if __name__ == "__main__":
    asyncio.run(_selfcheck())
