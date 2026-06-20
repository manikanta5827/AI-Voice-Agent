"""Module for handling the AI Voice Agent bot logic using Pipecat."""

import asyncio
import os

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    TranscriptionFrame,
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

from db import end_call, insert_call, insert_message
from services.llm import SYSTEM_PROMPT, create_llm
from services.stt import create_stt
from services.tts import create_tts

END_SIGNALS = [
    "bye", "goodbye", "ok bye", "thank you", "thanks",
    "థాంక్యూ", "అయిపోయింది", "చాలు",
]

WELCOME_MSG = (
    "నమస్కారం, SecureLife Insurance కి స్వాగతం. "
    "మీకు ఏ విషయంలో సహాయం కావాలి?"
)
IDLE_WARN_MSG = "అక్కడ ఉన్నారా?"
IDLE_BYE_MSG = "సరే, తర్వాత మాట్లాడదాం. Bye!"


class IdleDetector(FrameProcessor):
    """
    Resets on any user transcript.
    Warns after 5s silence, hangs up after 10s more.
    """

    def __init__(self, task: PipelineTask | None = None):
        super().__init__()
        self._task = task
        self._timer: asyncio.Task | None = None

    def set_task(self, task: PipelineTask):
        """Sets the pipeline task."""
        self._task = task

    def start(self):
        """Starts the idle detector timer."""
        self._reset()

    def _reset(self):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            await asyncio.sleep(5)
            await self._task.queue_frames([TTSSpeakFrame(IDLE_WARN_MSG)])
            await asyncio.sleep(10)
            await self._task.queue_frames(
                [TTSSpeakFrame(IDLE_BYE_MSG), EndFrame()]
            )
        except asyncio.CancelledError:
            pass

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self._reset()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        if self._timer:
            self._timer.cancel()
        await super().cleanup()


class TranscriptLogger(FrameProcessor):
    """Logs user transcripts to DB and detects end-call signals."""

    def __init__(self, task: PipelineTask | None, call_sid: str):
        super().__init__()
        self._task = task
        self._call_sid = call_sid

    def set_task(self, task: PipelineTask):
        """Sets the pipeline task."""
        self._task = task

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            text = frame.text
            logger.info(f"User: {text}")
            asyncio.create_task(insert_message(self._call_sid, "user", text))
            if any(s in text.lower() for s in END_SIGNALS):
                asyncio.create_task(end_call(self._call_sid))
                asyncio.create_task(_delayed_end(self._task, secs=4))
        await self.push_frame(frame, direction)


async def run_bot(websocket, stream_sid: str, call_sid: str):
    """Runs the main bot pipeline for the active call."""
    # pylint: disable=too-many-locals
    await insert_call(call_sid, stream_sid)

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
    llm = create_llm()
    tts = create_tts()

    context = LLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}]
    )
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    idle = IdleDetector(None)  # task assigned below
    transcript_logger = TranscriptLogger(None, call_sid)

    task = PipelineTask(
        Pipeline([
            transport.input(),
            stt,
            idle,
            transcript_logger,
            pair.user(),
            llm,
            tts,
            transport.output(),
            pair.assistant(),
        ]),
        params=PipelineParams(allow_interruptions=True),
    )

    idle.set_task(task)
    transcript_logger.set_task(task)

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        logger.info("Twilio connected — playing welcome")
        idle.start()
        await task.queue_frames([TTSSpeakFrame(WELCOME_MSG)])

    # Max call duration
    max_min = int(os.getenv("MAX_CALL_MINUTES", "3"))

    async def duration_guard():
        await asyncio.sleep(max_min * 60)
        logger.info(f"Max duration {max_min}min reached")
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
    await asyncio.sleep(secs)
    await task.queue_frames([EndFrame()])
