import os

from pipecat.services.cartesia.tts import (
    CartesiaTTSService,
    CartesiaTTSSettings,
    GenerationConfig,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings
from pipecat.services.tts_service import TextAggregationMode


def create_cartesia_tts() -> CartesiaTTSService:
    return CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        # Use TOKEN aggregation to stream words to Cartesia immediately instead of waiting for full sentences.
        text_aggregation_mode=TextAggregationMode.TOKEN,
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
        text_aggregation_mode=TextAggregationMode.TOKEN,  # stream words, don't wait for full sentence
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
