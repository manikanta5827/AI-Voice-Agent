import os

from pipecat.services.soniox.stt import SonioxSTTService, SonioxSTTSettings
from pipecat.transcriptions.language import Language


def create_stt() -> SonioxSTTService:
    return SonioxSTTService(
        api_key=os.getenv("SONIOX_API_KEY"),
        model="stt-rt-v5",
        settings=SonioxSTTSettings(language_hints=[Language.TE]),
    )
