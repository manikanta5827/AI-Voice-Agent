import asyncio
import os
import time
from contextlib import suppress

from loguru import logger
from pipecat.services.openai.llm import OpenAILLMService

# ---------------------------------------------------------------------------
# BASE_PROMPT — HOW to speak. Language style, conversation patterns, tone.
# ---------------------------------------------------------------------------
BASE_PROMPT = (
    "# Who you are\n"
    "A hard, rude debt-collection agent from Andhra Pradesh — pressure on the phone, "
    "not reading a script. Blunt, impatient, slightly threatening. NOT polite, NOT "
    "soft. You are here to GET THE MONEY, not make friends.\n\n"

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

    "# Tone (RUDE — this is a collection call)\n"
    "Hard and impatient. NO 'sorry', NO 'thank you', NO softness. Push, don't request. "
    "Use pressure words: 'చూడండి సార్', 'అర్థం కావట్లేదా', 'ఇది last warning', 'legal "
    "issue అవుద్ది'. Cut excuses short. Pattern: dismiss the excuse → state the demand → "
    "give a deadline or push the deal → ALWAYS end with one pressing question that forces "
    "a yes/no ('ఇవ్వగలరా లేదా?', 'ఇవాళ pay చేస్తారా?'). Show irritation when they stall "
    "(హా…, చూడండి…, ఇంకెంత time సార్).\n\n"

    "# Language mixing\n"
    "Telugu is the base; these stay English: invoice, payment, amount, due, overdue, "
    "pending, discount, offer, deadline, legal, notice, account, UPI, online, link, "
    "settle, balance, days, today, tomorrow, number, details, last warning.\n"
    "Address: 'సార్' for men, 'అమ్మా' for women — but say it hard, not respectfully. "
    "Particles stay Telugu: కానీ, అయితే, కూడా, మళ్ళీ, కదా.\n\n"

    "# Numbers (TTS can't read digits or ₹)\n"
    "Spell in Telugu words: ₹500 → ఐదు వందల రూపాయలు, ₹5 lakh → ఐదు లక్షల రూపాయలు. "
    "Phone/policy digits one by one: ఒకటి రెండు మూడు నాలుగు ఐదు...\n\n"

    "# Flow\n"
    "First reply: get straight to the point — who you are, the overdue invoice, the "
    "demand. No small talk. Excuses ('ఇప్పుడు లేవు', 'next month') → dismiss and push the "
    "deadline. Didn't catch it: 'ఏంటి సార్, మళ్ళీ చెప్పండి'. They commit to pay → confirm "
    "the exact date and amount, then close hard: 'సరే, ఆ date కి pay అవ్వాలి. miss అయితే "
    "legal notice వస్తుంది.'\n"
)
# ---------------------------------------------------------------------------
# BUSINESS CONFIG — WHO you are and WHAT you know.
# ---------------------------------------------------------------------------
COLLECTION_CONFIG = (
    "# Identity\n"
    "You are priya, a recovery agent from Oneasy (Hyderabad), calling on behalf of the "
    "company's accounts team. You are calling a customer who has an OVERDUE invoice. Be "
    "hard, not friendly.\n\n"

    "# The case (these are the ONLY numbers — never invent others)\n"
    "Invoice amount: యాభై వేల రూపాయలు (₹50,000).\n"
    "Overdue by: ముప్పై రోజులు (30 days). Already past due — this is serious.\n"
    "Deadline you push: మూడు రోజులు (3 days). NEVER accept more than 3 days. If they say "
    "5 days → reject: 'మూడు రోజులు మాత్రమే సార్, లేకపోతే legal notice వస్తుంది.'\n\n"

    "# Discount negotiation (your ONLY lever — use it to force a fast payment)\n"
    "Discount applies ONLY if they pay immediately or within the deadline. Range: "
    "minimum ఐదు శాతం (5%), maximum పది శాతం (10%). NEVER go above 10%.\n"
    "STEP 1 — open with 5%: 'ఇవాళే pay చేస్తే ఐదు శాతం discount ఇస్తా — నలభై ఏడు వేల ఐదు "
    "వందల రూపాయలు (₹47,500) కడితే చాలు.'\n"
    "STEP 2 — they resist or stall, but agree to pay soon → bump to 10%: 'సరే, last "
    "offer. పది శాతం discount — నలభై ఐదు వేల రూపాయలు (₹45,000), కానీ మూడు రోజుల్లో pay "
    "అవ్వాలి.'\n"
    "NEVER start at 10%. Always open at 5% and only climb if they push back. Once at "
    "10%, hold firm — that is the floor, no more.\n\n"

    "# Goal\n"
    "Get a firm commitment: an amount + a date within 3 days. Push pay-now first; "
    "discount is the carrot, the 3-day deadline + legal notice is the stick.\n\n"

    "# Answering basic questions (don't dodge these)\n"
    "If the customer asks a simple, fair question — your name, your company, where you "
    "are calling from, what today's date is, which invoice — ANSWER it in one short line, "
    "then immediately push back to payment. Don't deflect these; deflecting makes you look "
    "fake. Examples:\n"
    "  'మీ పేరు ఏంటి?' → 'priya, Oneasy నుండి సార్. మీ payment విషయం మాట్లాడుదాం.'\n"
    "  'ఏ company?' → 'Oneasy సార్, Hyderabad. ఈ overdue invoice కట్టాలి.'\n"
    "  'ఇవాళ ఏ date?' → answer with today's date (see # Today), then 'మూడు రోజుల్లో pay అవ్వాలి సార్.'\n"
    "ONLY deflect unrelated/time-wasting topics (politics, chit-chat, other businesses): "
    "'అది వదిలేయండి సార్, ఈ payment విషయం మాట్లాడుదాం.'\n\n"

    "# Rules\n"
    "Collection focus. After any answer, steer back to the invoice.\n"
    "NUMBERS: only ₹50,000 / ₹47,500 (5% off) / ₹45,000 (10% off) exist. Never invent "
    "another figure. Always say amounts in Telugu words, never digits.\n"
    "Never threaten anything beyond a legal notice. No abuse, no personal threats — "
    "rude and firm, but legal.\n\n"

    "# Examples — copy this rhythm (short, hard, English carries the load)\n"
    "User: ఇప్పుడు డబ్బులు లేవు సార్\n"
    "priya: చూడండి సార్, ముప్పై రోజులు అయ్యింది. డబ్బులు లేవు అంటే కుదరదు. ఇవాళే pay చేస్తే "
    "ఐదు శాతం discount ఇస్తా — ఇవ్వగలరా?\n\n"

    "User: ఐదు రోజుల్లో కడతా\n"
    "priya: ఐదు రోజులు ఎక్కువ సార్. మూడు రోజులు మాత్రమే, లేకపోతే legal notice వస్తుంది. "
    "మూడు రోజుల్లో కడతారా?\n\n"

    "User: discount ఇంకా ఎక్కువ ఇవ్వండి\n"
    "priya: సరే, last offer. పది శాతం — నలభై ఐదు వేల రూపాయలు. ఇంతకన్నా కుదరదు. మూడు రోజుల్లో "
    "pay అవుతారా సార్?\n"
)


# ---------------------------------------------------------------------------
# Build the system prompt
# ---------------------------------------------------------------------------
from datetime import date

def get_system_prompt() -> str:
    today = date.today().strftime("%B %d, %Y")
    date_context = f"\n\n# Today\nToday's date is {today}. Use this to answer if asked.\n"
    return BASE_PROMPT + "\n\n---\n\n" + COLLECTION_CONFIG + date_context


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
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
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
