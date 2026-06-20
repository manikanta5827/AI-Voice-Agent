import os

from pipecat.services.cartesia.tts import CartesiaTTSService, CartesiaTTSSettings


def create_tts() -> CartesiaTTSService:
    # sonic-multilingual supports Telugu
    return CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        settings=CartesiaTTSSettings(
            voice=os.getenv("CARTESIA_VOICE_ID"),
            model="sonic-3.5",
            language="te"
        )
    )
