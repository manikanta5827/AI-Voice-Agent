import os

from pipecat.frames.frames import StartFrame
from pipecat.services.soniox.stt import SonioxSTTService, SonioxSTTSettings
from pipecat.transcriptions.language import Language


def _fast_start() -> bool:
    return os.getenv("FAST_START", "1").lower() in ("1", "true", "yes", "on")


class _FastStartSonioxSTT(SonioxSTTService):
    """Connect the Soniox websocket in the BACKGROUND instead of blocking the
    pipeline StartFrame on the handshake. The cached welcome audio is queued right
    after StartFrame; if start() awaits the connect, the caller hears nothing until
    the (fresh, per-call) websocket handshake completes. STT isn't needed during the
    ~10s welcome, so connect off the critical path and finish before the user speaks.

    ponytail: the first few hundred ms of caller audio may arrive before the socket
    is up and get dropped — that's bot-welcome time, not real speech. Set FAST_START=0
    to revert to blocking connect if first-utterance drops ever show up."""

    async def start(self, frame: StartFrame):
        # Grandparent start() (STTService) sets up everything except the connect,
        # which SonioxSTTService.start would await — we background it instead.
        await super(SonioxSTTService, self).start(frame)
        self.create_task(self._connect())


def create_stt() -> SonioxSTTService:
    cls = _FastStartSonioxSTT if _fast_start() else SonioxSTTService
    return cls(
        api_key=os.getenv("SONIOX_API_KEY"),
        settings=SonioxSTTSettings(
            model="stt-rt-v5",
            language_hints=[Language.TE],
        ),
    )
