import os

from pipecat.services.cartesia.tts import CartesiaTTSService


def create_tts() -> CartesiaTTSService:
    return CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id=os.getenv("CARTESIA_VOICE_ID"),
        model="sonic-2",
    )
