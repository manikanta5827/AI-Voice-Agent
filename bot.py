import asyncio
import os
import re
import time
import uuid

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from db import end_call, insert_call, insert_message

# Suppress verbose system prompt dump. Errors still surface via exception propagation.
logger.disable("pipecat.services.openai.base_llm")
logger.disable("pipecat.services.google.llm")
logger.disable("pipecat.services.anthropic.llm")
from services.llm import create_active_llm, get_system_prompt
from services.stt import create_stt
from services.telephony import build_transport, provider
from services.tts import create_tts
from services.welcome import get_welcome_audio

# English + Telugu phrases that signal the caller wants to end
END_SIGNALS = [
    "bye", "goodbye", "ok bye", "thank you", "thanks",
    "థాంక్యూ", "అయిపోయింది", "చాలు",
]

WELCOME_MSG = (
    "నమస్కారం, accounts team నుంచి priya మాట్లాడుతున్నా. "
    "మీ యాభై వేల రూపాయల invoice ముప్పై రోజులు overdue ఉంది. ఈ payment గురించే call చేస్తున్నా. "
    "ఎప్పుడు కడతారు sir?"
)
IDLE_BYE_MSG = "సరే, తర్వాత మాట్లాడదాం. Bye!"


class IdleDetector(FrameProcessor):
    """Hangs up after 45s of true silence. Resets on user or bot transcript."""

    def __init__(self, task: PipelineTask | None = None):
        super().__init__()
        self._task = task
        self._timer: asyncio.Task | None = None

    def set_task(self, task: PipelineTask):
        self._task = task

    def start(self):
        self._reset()

    def _reset(self):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            await asyncio.sleep(45)
            await self._task.queue_frames([TTSSpeakFrame(IDLE_BYE_MSG), EndFrame()])
        except asyncio.CancelledError:
            pass

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        # Reset on user or bot speech
        if isinstance(frame, (TranscriptionFrame, BotStartedSpeakingFrame)):
            self._reset()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        if self._timer:
            self._timer.cancel()
            try:
                await self._timer  # wait until fully cancelled, not just signalled
            except asyncio.CancelledError:
                pass
            self._timer = None
        await super().cleanup()


class HistoryCap(FrameProcessor):
    """Keeps LLM context bounded to system message + last N messages, so prompt
    prefill (and TTFT) doesn't grow unbounded over a long call. Trims in place
    before the context reaches the LLM. The system message at [0] stays fixed, so
    the cached prefix (DeepSeek/OpenAI prompt cache) keeps hitting."""

    def __init__(self, context: LLMContext, max_messages: int):
        super().__init__()
        self._ctx = context
        self._max = max_messages

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        msgs = self._ctx.messages
        if len(msgs) > self._max + 1:  # +1 keeps the system message at [0]
            self._ctx.messages[:] = [msgs[0]] + msgs[-self._max:]
        await self.push_frame(frame, direction)


class EchoGate(FrameProcessor):
    """Drops STT transcripts arriving during or just after bot speech to prevent echo pollution."""

    _TAIL = 0.4

    def __init__(self):
        super().__init__()
        self._bot_speaking = False
        self._unmute_at = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        match frame:
            case BotStartedSpeakingFrame():
                self._bot_speaking = True
            case BotStoppedSpeakingFrame():
                self._bot_speaking = False
                self._unmute_at = time.monotonic() + self._TAIL
            case (TranscriptionFrame() | InterimTranscriptionFrame()) if (
                self._bot_speaking or time.monotonic() < self._unmute_at
            ):
                logger.debug("EchoGate: dropped transcript during bot speech")
                return  # swallow the echo
        await self.push_frame(frame, direction)


class MarkerStripper(FrameProcessor):
    """Fixes Telugu<->Latin script spacing in LLM text before TTS."""

    # Telugu Unicode block <-> Latin letters, in both orders
    _TE_LAT = re.compile(r"([ఀ-౿])([A-Za-z])")
    _LAT_TE = re.compile(r"([A-Za-z])([ఀ-౿])")

    @staticmethod
    def _is_te(c: str) -> bool:
        return "ఀ" <= c <= "౿"

    @staticmethod
    def _is_lat(c: str) -> bool:
        return c.isascii() and c.isalpha()

    def __init__(self):
        super().__init__()
        self._last_char = ""  # last char pushed, to fix space across frame boundaries

    def _space(self, text: str) -> str:
        text = self._TE_LAT.sub(r"\1 \2", text)
        text = self._LAT_TE.sub(r"\1 \2", text)
        return text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            text = frame.text
            if not text:
                return  # drop empty chunk
            text = self._space(text)
            # fix script transition split across frames
            if self._last_char and (
                (self._is_te(self._last_char) and self._is_lat(text[0]))
                or (self._is_lat(self._last_char) and self._is_te(text[0]))
            ):
                text = " " + text
            self._last_char = text[-1]
            await self.push_frame(LLMTextFrame(text), direction)
            return
        if isinstance(frame, LLMFullResponseEndFrame):
            self._last_char = ""  # reset between responses
        await self.push_frame(frame, direction)


class TailPadder(FrameProcessor):
    """Appends trailing silence after each TTS response so Twilio doesn't clip the
    final word. Twilio drops the tail of bot audio at the playback boundary (the
    one-shot welcome audio hit the same bug — see services/welcome._pad_tail); the
    streaming TTS path needs the same padding. Silence goes out just before the
    TTSStoppedFrame that closes the utterance."""

    _TAIL_MS = 300

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStoppedFrame):
            chunk = 320  # 20ms @ 8kHz, 16-bit mono — paced like the welcome audio
            silence = b"\x00" * (8000 * self._TAIL_MS // 1000 * 2)
            for i in range(0, len(silence), chunk):
                await self.push_frame(
                    TTSAudioRawFrame(audio=silence[i:i + chunk], sample_rate=8000, num_channels=1),
                    direction,
                )
        await self.push_frame(frame, direction)


class TTFBLogger(FrameProcessor):
    """Logs per-service time-to-first-byte (stt/llm/tts) to find the slow leg.
    Gated by DEBUG_TTFB; requires enable_metrics=True."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for d in frame.data:
                if isinstance(d, TTFBMetricsData):
                    logger.info(f"TTFB {d.processor}={d.value:.2f}s")
        await self.push_frame(frame, direction)


class Timeline:
    """Shared wall-clock marks for ONE user turn, written from several pipeline taps.
    Per-service TTFB is each service's private clock and hides the handoff gaps
    between stages; this stitches absolute timestamps across boundaries so the real
    culprit is visible. Dumps the breakdown when the first text reaches TTS, then
    disarms until the next turn. Aborted turns (no tts_send) are silently dropped."""

    def __init__(self):
        self._t: dict[str, float] = {}
        self._armed = False

    def mark(self, event: str):
        now = time.monotonic()
        if event == "user_start":
            self._t = {"user_start": now}  # a new turn begins; reset
            self._armed = True
            return
        if not self._armed or event in self._t:
            return  # keep the FIRST occurrence of each event per turn
        self._t[event] = now
        if event == "tts_send":
            self._dump()
            self._armed = False

    def _dump(self):
        t = self._t

        def gap(a: str, b: str) -> str:
            return f"{(t[b] - t[a]) * 1000:4.0f}ms" if a in t and b in t else "  -  "

        logger.info(
            "TURN timeline | "
            f"speech={gap('user_start', 'user_stop')} "
            f"stop->llm_call={gap('user_stop', 'llm_call')} "
            f"llm_ttft={gap('llm_call', 'llm_first')} "
            f"llm_first->tts={gap('llm_first', 'tts_send')} "
            f"|| stop->tts={gap('user_stop', 'tts_send')}"
        )


class TimelineTap(FrameProcessor):
    """Stamps the shared Timeline when a watched frame passes this pipeline point.
    `marks` maps a tuple of frame types -> event name."""

    def __init__(self, timeline: Timeline, marks: dict):
        super().__init__()
        self._tl = timeline
        self._marks = tuple(marks.items())

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        for ftypes, event in self._marks:
            if isinstance(frame, ftypes):
                self._tl.mark(event)
                break
        await self.push_frame(frame, direction)


class TranscriptLogger(FrameProcessor):
    """Writes every user utterance to DB and triggers end-call on goodbye phrases."""

    def __init__(self, task: PipelineTask | None, call_sid: str):
        super().__init__()
        self._task = task
        self._call_sid = call_sid

    def set_task(self, task: PipelineTask):
        self._task = task

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            text = frame.text
            logger.info(f"User: {text}")
            asyncio.create_task(insert_message(self._call_sid, "user", text))
            if any(s in text.lower() for s in END_SIGNALS):
                asyncio.create_task(end_call(self._call_sid))
                # Small delay so TTS can finish the farewell before disconnect
                asyncio.create_task(_delayed_end(self._task, secs=4))
        await self.push_frame(frame, direction)


async def run_bot(websocket):
    """Wire up and run the STT → LLM → TTS pipeline for one call. The active
    telephony provider (TELEPHONY env) builds the transport and handshake."""
    transport, call_sid, stream_sid = await build_transport(websocket)
    # Twilio with no SIDs = genuinely malformed handshake — nothing to run. For
    # Vobiz, empty IDs must NOT close the socket (that refuses the call); the
    # serializer is already built, so proceed with a synthetic id for the DB row.
    if provider() == "twilio" and not call_sid and not stream_sid:
        return
    call_sid = call_sid or stream_sid or f"vobiz-{uuid.uuid4().hex[:12]}"
    await insert_call(call_sid, stream_sid)

    stt = create_stt()
    llm = create_active_llm()
    tts = create_tts()

    context = LLMContext(messages=[{"role": "system", "content": get_system_prompt()}])
    # Silence (secs) after speech before turn-end fires. Lower = snappier but risks
    # cutting users mid-pause. Tune via env while latency-testing; 0.4 is the safe default.
    turn_silence = float(os.getenv("TURN_SILENCE_SECS", "0.3"))
    # Use VAD for start and SpeechTimeout for predictable turn boundaries
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=turn_silence)],
            ),
            # Fallback cap on how long to wait to confirm turn-end
            user_turn_stop_timeout=1.0,
        ),
    )

    # Cap context to system + last N messages (8 exchanges) to bound LLM prefill.
    history_cap = HistoryCap(context, max_messages=int(os.getenv("LLM_MAX_HISTORY", "16")))

    # task=None here; assigned after PipelineTask is created to avoid circular dep
    echo_gate = EchoGate()
    idle = IdleDetector(None)
    transcript_logger = TranscriptLogger(None, call_sid)
    marker_stripper = MarkerStripper()
    tail_padder = TailPadder()  # pad TTS tail so Twilio doesn't clip the last word

    # Latency debug (DEBUG_TTFB): per-service TTFB + a cross-stage turn timeline
    # that exposes the handoff gaps TTFB can't see. All None in prod.
    debug_latency = os.getenv("DEBUG_TTFB", "").lower() in ("1", "true", "yes", "on")
    ttfb_logger = TTFBLogger() if debug_latency else None
    timeline = Timeline() if debug_latency else None
    # Taps stamp absolute timestamps at the real pipeline boundaries.
    # user_start/user_stop are stamped BEFORE the aggregator, which may consume the
    # VAD speaking frames; llm_call is stamped right before the LLM.
    tap_user = TimelineTap(timeline, {
        (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame): "user_start",
        (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame): "user_stop",
    }) if debug_latency else None
    tap_pre_llm = TimelineTap(timeline, {
        (LLMContextFrame,): "llm_call",   # context frame entering the LLM
    }) if debug_latency else None
    tap_post_llm = TimelineTap(timeline, {
        (LLMTextFrame,): "llm_first",      # first token out of the LLM
    }) if debug_latency else None
    tap_pre_tts = TimelineTap(timeline, {
        (LLMTextFrame,): "tts_send",       # first text handed to Cartesia
    }) if debug_latency else None

    stages = [
        transport.input(),
        stt,
        echo_gate,             # drop self-transcribed echo while bot speaks
        idle,                  # silence watchdog before LLM aggregation
        transcript_logger,
        tap_user,              # marks user_start/user_stop (DEBUG_TTFB only)
        pair.user(),           # buffers user speech into LLM context
        history_cap,           # trims context to system + last N before LLM
        tap_pre_llm,           # marks llm_call (DEBUG_TTFB only)
        llm,
        tap_post_llm,          # marks llm_first (DEBUG_TTFB only)
        marker_stripper,       # fix Telugu<->Latin script spacing before TTS
        tap_pre_tts,           # marks tts_send (DEBUG_TTFB only)
        tts,
        tail_padder,           # trailing silence so Twilio doesn't clip the last word
        ttfb_logger,           # prints per-service TTFB each turn (DEBUG_TTFB only)
        transport.output(),
        pair.assistant(),      # captures LLM reply into context for next turn
    ]

    task = PipelineTask(
        Pipeline([s for s in stages if s is not None]),
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,        # emit per-service TTFB/processing metrics
            enable_usage_metrics=True,  # emit LLM token usage
        ),
    )

    idle.set_task(task)
    transcript_logger.set_task(task)

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):  # pyright: ignore[reportUnusedFunction]
        logger.info(f"{provider()} connected — playing cached welcome")
        idle.start()
        context.messages.append({"role": "assistant", "content": WELCOME_MSG})
        # Play pre-rendered welcome audio to avoid initial TTS latency.
        # Chunk into 20ms frames so the transport paces playback and a user barge-in
        # can interrupt it — a single large frame floods Twilio's buffer un-interruptibly.
        audio = await get_welcome_audio(WELCOME_MSG)
        chunk = 320  # 20ms @ 8kHz, 16-bit mono
        await task.queue_frames([
            TTSAudioRawFrame(audio=audio[i:i + chunk], sample_rate=8000, num_channels=1)
            for i in range(0, len(audio), chunk)
        ])

    max_min = int(os.getenv("MAX_CALL_MINUTES", "3"))

    async def duration_guard():
        """Ends the call after MAX_CALL_MINUTES regardless of conversation state."""
        await asyncio.sleep(max_min * 60)
        logger.info(f"Max duration {max_min}min reached")
        # Inject a system nudge for a natural farewell
        context.messages.append({
            "role": "system",
            "content": (
                "Maximum call duration reached. Give one brief, warm Telugu "
                "farewell — one sentence only. Do not mention time limits."
            ),
        })
        await end_call(call_sid)
        asyncio.create_task(_delayed_end(task, secs=5))

    asyncio.create_task(duration_guard())

    runner = PipelineRunner()
    await runner.run(task)
    await idle.cleanup()


async def _delayed_end(task: PipelineTask, secs: float):
    """Queues EndFrame after a short delay so TTS audio finishes before disconnect."""
    await asyncio.sleep(secs)
    await task.queue_frames([EndFrame()])
