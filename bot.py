import asyncio
import os
import re
import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
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
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
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
from services.llm import SYSTEM_PROMPT, create_llm
from services.stt import create_stt
from services.tts import create_tts
from services.welcome import get_welcome_audio

# English + Telugu phrases that signal the caller wants to end
END_SIGNALS = [
    "bye", "goodbye", "ok bye", "thank you", "thanks",
    "థాంక్యూ", "అయిపోయింది", "చాలు",
]

WELCOME_MSG = (
    "నమస్కారం, SecureLife Insurance నుంచి priya మాట్లాడుతున్నా. "
    "ఏం help కావాలో చెప్పండి."
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


class EchoGate(FrameProcessor):
    """Drops STT transcripts arriving during or just after bot speech to prevent echo pollution."""

    _TAIL = 0.4

    def __init__(self):
        super().__init__()
        self._bot_speaking = False
        self._unmute_at = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._unmute_at = time.monotonic() + self._TAIL
        elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            if self._bot_speaking or time.monotonic() < self._unmute_at:
                logger.debug("EchoGate: dropped transcript during bot speech")
                return  # swallow the echo
        await self.push_frame(frame, direction)


class MarkerStripper(FrameProcessor):
    """Cleans LLM text before TTS: strips [emotion] markers and fixes script spacing."""

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
        self._pending = ""    # holds an unclosed '[...' carried to the next frame
        self._last_char = ""  # last char pushed, to fix space across frame boundaries

    def _space(self, text: str) -> str:
        text = self._TE_LAT.sub(r"\1 \2", text)
        text = self._LAT_TE.sub(r"\1 \2", text)
        return text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            text = re.sub(r"\[[^\]]*\]", "", self._pending + frame.text)
            self._pending = ""
            idx = text.rfind("[")  # unclosed bracket → hold it for the next chunk
            if idx != -1:
                self._pending, text = text[idx:], text[:idx]
            if text:
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
            if self._pending:
                await self.push_frame(LLMTextFrame(self._pending), direction)  # flush stray '['
                self._pending = ""
        await self.push_frame(frame, direction)


class TTFBLogger(FrameProcessor):
    """Logs per-service time-to-first-byte (requires enable_metrics=True)."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for d in frame.data:
                if isinstance(d, TTFBMetricsData):
                    logger.info(f"TTFB {d.processor}={d.value:.2f}s")
        await self.push_frame(frame, direction)


class BotResponseLogger(FrameProcessor):
    """Logs the bot's complete LLM response cleanly."""

    def __init__(self):
        super().__init__()
        self._buf = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self._buf += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._buf:
                logger.info(f"Agent: {self._buf.strip()}")
                self._buf = ""
        elif isinstance(frame, TTSSpeakFrame):
            # welcome/idle messages queued via task.queue_frames
            logger.info(f"Agent: {frame.text}")
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


async def run_bot(websocket, stream_sid: str, call_sid: str):
    """Wire up and run the STT → LLM → TTS pipeline for one call."""
    await insert_call(call_sid, stream_sid)

    # Twilio sends/receives MULAW 8kHz audio; TwilioFrameSerializer handles encoding
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=TwilioFrameSerializer(
                stream_sid=stream_sid,
                call_sid=call_sid,
                account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            ),
        ),
    )

    stt = create_stt()
    
    llm_provider = os.getenv("LLM_PROVIDER", "ai_gateway")
    if llm_provider == "huggingface":
        from services.hugging_llm import create_huggingface_llm
        llm = create_huggingface_llm()
        logger.info("Using Hugging Face LLM (Navarasa)")
    elif llm_provider == "openai":
        from services.llm import create_openai_llm
        llm = create_openai_llm()
        logger.info("Using direct OpenAI API")
    elif llm_provider == "gemini":
        from services.llm import create_gemini_llm
        llm = create_gemini_llm()
        logger.info("Using native Google Gemini API")
    elif llm_provider == "groq":
        from services.llm import create_groq_llm
        llm = create_groq_llm()
        logger.info("Using Groq LLM API")
    elif llm_provider == "deepseek":
        from services.llm import create_deepseek_llm
        llm = create_deepseek_llm()
        logger.info("Using DeepSeek API (thinking disabled)")
    elif llm_provider == "sarvam":
        from services.llm import create_sarvam_llm
        llm = create_sarvam_llm()
        logger.info("Using Sarvam API")
    else:
        llm = create_llm()
        logger.info("Using default AI Gateway LLM")

    tts = create_tts()

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    # Use VAD for start and SpeechTimeout for predictable turn boundaries
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.4)],
            ),
            # Fallback cap on how long to wait to confirm turn-end
            user_turn_stop_timeout=1.0,
        ),
    )

    # task=None here; assigned after PipelineTask is created to avoid circular dep
    echo_gate = EchoGate()
    idle = IdleDetector(None)
    transcript_logger = TranscriptLogger(None, call_sid)
    marker_stripper = MarkerStripper()
    bot_response_logger = BotResponseLogger()
    ttfb_logger = TTFBLogger()

    task = PipelineTask(
        Pipeline([
            transport.input(),
            stt,
            echo_gate,             # drop self-transcribed echo while bot speaks
            idle,                  # silence watchdog before LLM aggregation
            transcript_logger,
            pair.user(),           # buffers user speech into LLM context
            llm,
            marker_stripper,       # strip [thinking]/[sympathetic] tags before they're spoken
            bot_response_logger,   # logs "Agent: ..." cleanly (replaces context dump)
            tts,
            ttfb_logger,           # prints per-service TTFB (stt/llm/tts) each turn
            transport.output(),
            pair.assistant(),      # captures LLM reply into context for next turn
        ]),
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,        # emit per-service TTFB/processing metrics
            enable_usage_metrics=True,  # emit LLM token usage
        ),
    )

    idle.set_task(task)
    transcript_logger.set_task(task)

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        logger.info("Twilio connected — playing cached welcome")
        idle.start()
        context.messages.append({"role": "assistant", "content": WELCOME_MSG})
        # Play pre-rendered welcome audio to avoid initial TTS latency
        audio = await get_welcome_audio(WELCOME_MSG)
        await task.queue_frames([
            TTSAudioRawFrame(audio=audio, sample_rate=8000, num_channels=1)
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
