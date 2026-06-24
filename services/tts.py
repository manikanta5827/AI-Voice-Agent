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
        settings=CartesiaTTSSettings(
            voice=os.getenv("CARTESIA_VOICE_ID"),
            model="sonic-3.5",
            language="te",
            # default 1.0 sounds sluggish on phone; 1.1 = brisk but natural. Range [0.6, 1.5]
            generation_config=GenerationConfig(speed=1.1),
        ),
    )


def create_elevenlabs_tts() -> ElevenLabsTTSService:
    # WebSocket streaming: LLM token stream → ElevenLabs → Twilio MULAW, no buffering.
    # No language_code param — Telugu auto-detected from voice clone (set during creation).
    # stability=0.4: human-like variability (1.0 = robotic monotone, 0.0 = chaotic)
    # style=0.25: mild expressiveness boost without overdoing it on an IVC clone
    # similarity_boost=0.75: loose enough for IVC (5-min clone has less voice data)
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
