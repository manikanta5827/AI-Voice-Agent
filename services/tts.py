import os

from pipecat.services.cartesia.tts import (
    CartesiaTTSService,
    CartesiaTTSSettings,
    GenerationConfig,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings


def create_cartesia_tts() -> CartesiaTTSService:
    return CartesiaTTSService(
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
