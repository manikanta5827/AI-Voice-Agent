import os

from pipecat.services.cartesia.tts import CartesiaTTSService


def create_tts() -> CartesiaTTSService:
    # sonic-2 supports Telugu; voice_id must be a Cartesia voice trained on Telugu
    return CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id=os.getenv("CARTESIA_VOICE_ID"),
        model="sonic-2",
    )
