import os

from pipecat.frames.frames import StartFrame
from pipecat.services.cartesia.tts import (
    CartesiaTTSService,
    CartesiaTTSSettings,
    GenerationConfig,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings


def _fast_start() -> bool:
    return os.getenv("FAST_START", "1").lower() in ("1", "true", "yes", "on")


class _FastStartCartesiaTTS(CartesiaTTSService):
    """Connect the Cartesia websocket in the BACKGROUND so the pipeline StartFrame
    doesn't block on the handshake. TTS sits between the LLM and transport.output, so
    a blocking connect here also delays the cached welcome audio reaching the caller.
    The welcome is raw audio (not TTS-generated), and the first real TTS request only
    comes after the user speaks — plenty of time for the background connect to finish.
    Set FAST_START=0 to revert to blocking connect."""

    async def start(self, frame: StartFrame):
        await super(CartesiaTTSService, self).start(frame)  # base start, skip _connect
        self._output_sample_rate = self.sample_rate  # preserved from CartesiaTTSService.start
        self.create_task(self._connect())


def create_cartesia_tts() -> CartesiaTTSService:
    cls = _FastStartCartesiaTTS if _fast_start() else CartesiaTTSService
    return cls(
        api_key=os.getenv("CARTESIA_API_KEY"),
        # Generate natively at Twilio's 8kHz — skips the 24k->8k resample + 3x bytes.
        sample_rate=8000,
        settings=CartesiaTTSSettings(
            voice=os.getenv("CARTESIA_VOICE_ID"),
            model="sonic-3.5",
            language="te",
            # Default 1.0 sounds sluggish; 1.1 is brisk but natural.
            generation_config=GenerationConfig(speed=1.1),
        ),
    )


def create_elevenlabs_tts() -> ElevenLabsTTSService:
    # WebSocket streaming: LLM token stream -> ElevenLabs -> Twilio MULAW.
    return ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSSettings(
            voice=os.getenv("ELEVENLABS_VOICE_ID"),
            model="eleven_turbo_v2_5",
            stability=0.5,
            similarity_boost=0.95,
            style=0.25,
            use_speaker_boost=True,
        ),
    )


# To revert to Cartesia: change this line to create_cartesia_tts
create_tts = create_cartesia_tts
