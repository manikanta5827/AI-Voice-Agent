import os

from pipecat.services.soniox.stt import SonioxSTTService, SonioxSTTSettings
from pipecat.transcriptions.language import Language


def create_stt() -> SonioxSTTService:
    return SonioxSTTService(
        api_key=os.getenv("SONIOX_API_KEY"),
        settings=SonioxSTTSettings(
            model="stt-rt-v5",
            language_hints=[Language.TE],
        ),
    )
