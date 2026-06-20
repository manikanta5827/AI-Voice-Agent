import os

from pipecat.services.soniox.stt import SonioxSTTService, SonioxSTTSettings
from pipecat.transcriptions.language import Language


def create_stt() -> SonioxSTTService:
    # Language.TE = Telugu; stt-rt-v5 is Soniox's real-time multilingual model
    # max_endpoint_delay_ms=500 → minimum allowed; finalizes speech faster
    # endpoint_sensitivity=0.5 → biased toward finalizing sooner (range -1.0 to 1.0)
    return SonioxSTTService(
        api_key=os.getenv("SONIOX_API_KEY"),
        settings=SonioxSTTSettings(
            model="stt-rt-v5",
            language_hints=[Language.TE],
            max_endpoint_delay_ms=500,
            endpoint_sensitivity=0.5,
        ),
    )
