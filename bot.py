import asyncio
import os
import re

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
)
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

# Suppress pipecat's verbose "Generating chat from context [full system prompt...]" dump.
# LLM errors still surface via exception propagation in the async pipeline.
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
    "నమస్కారం, SecureLife Insurance నుంచి raghu మాట్లాడుతున్నా. "
    "ఏం help కావాలో చెప్పండి."
)
IDLE_BYE_MSG = "సరే, తర్వాత మాట్లాడదాం. Bye!"


class IdleDetector(FrameProcessor):
    """Hangs up after 15s of silence. Timer resets on every user transcript."""

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
        if isinstance(frame, TranscriptionFrame):
            self._reset()  # user spoke — restart silence timer
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


class MarkerStripper(FrameProcessor):
    """Removes [thinking], [sympathetic], etc. emotion markers from LLM text before TTS.

    Cartesia/ElevenLabs don't parse [...] tags — they'd read them aloud ("thinking"),
    which is exactly the robotic artifact we're killing. Buffers a partial '[...'
    that spans token-stream frames so a marker split across chunks is still caught.
    """

    def __init__(self):
        super().__init__()
        self._pending = ""  # holds an unclosed '[...' carried to the next frame

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            text = re.sub(r"\[[^\]]*\]", "", self._pending + frame.text)
            self._pending = ""
            idx = text.rfind("[")  # unclosed bracket → hold it for the next chunk
            if idx != -1:
                self._pending, text = text[idx:], text[:idx]
            if text:
                await self.push_frame(LLMTextFrame(text), direction)
            return
        if isinstance(frame, LLMFullResponseEndFrame) and self._pending:
            await self.push_frame(LLMTextFrame(self._pending), direction)  # flush stray '['
            self._pending = ""
        await self.push_frame(frame, direction)


class BotResponseLogger(FrameProcessor):
    """Logs the bot's complete LLM response as one clean line instead of the full context dump."""

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
    elif llm_provider == "gemini":
        from services.llm import create_gemini_llm
        llm = create_gemini_llm()
        logger.info("Using native Google Gemini API")
    else:
        llm = create_llm()
        logger.info("Using default AI Gateway LLM")

    tts = create_tts()

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    # Bug fixes:
    # 1. Only VADUserTurnStartStrategy — removing TranscriptionUserTurnStartStrategy
    #    prevents late Soniox chunks from re-triggering the LLM after a turn ends.
    # 2. SpeechTimeoutUserTurnStopStrategy replaces Smart Turn v3 (English-tuned ML
    #    model that marks Telugu short phrases INCOMPLETE, causing silent freezes).
    #    0.8s silence after last transcript → LLM fires. Predictable for phone calls.
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.4)],
            ),
            user_turn_stop_timeout=2.0,
        ),
    )

    # task=None here; assigned after PipelineTask is created to avoid circular dep
    idle = IdleDetector(None)
    transcript_logger = TranscriptLogger(None, call_sid)
    marker_stripper = MarkerStripper()
    bot_response_logger = BotResponseLogger()

    task = PipelineTask(
        Pipeline([
            transport.input(),
            stt,
            idle,                  # silence watchdog before LLM aggregation
            transcript_logger,
            pair.user(),           # buffers user speech into LLM context
            llm,
            marker_stripper,       # strip [thinking]/[sympathetic] tags before they're spoken
            bot_response_logger,   # logs "Agent: ..." cleanly (replaces context dump)
            tts,
            transport.output(),
            pair.assistant(),      # captures LLM reply into context for next turn
        ]),
        params=PipelineParams(allow_interruptions=True),
    )

    idle.set_task(task)
    transcript_logger.set_task(task)

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        logger.info("Twilio connected — playing cached welcome")
        idle.start()
        context.messages.append({"role": "assistant", "content": WELCOME_MSG})
        # Play PRE-RENDERED welcome audio (assets/welcome_*.pcm), not live TTS.
        # Live TTSSpeakFrame meant a 5-7s cold Cartesia WS connect + synthesis before
        # any sound — during which the caller said "hello?" into silence, triggering an
        # LLM reply that overlapped the late-arriving welcome. Cached bytes play
        # instantly; TTSAudioRawFrame sets bot-speaking state so a caller talking over
        # it interrupts cleanly (Twilio buffer flushed). 8kHz mono PCM matches Twilio.
        audio = await get_welcome_audio(WELCOME_MSG)
        await task.queue_frames([
            TTSAudioRawFrame(audio=audio, sample_rate=8000, num_channels=1)
        ])

    max_min = int(os.getenv("MAX_CALL_MINUTES", "3"))

    async def duration_guard():
        """Ends the call after MAX_CALL_MINUTES regardless of conversation state."""
        await asyncio.sleep(max_min * 60)
        logger.info(f"Max duration {max_min}min reached")
        # Inject a system nudge so the LLM gives a natural farewell, not a cut-off
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
