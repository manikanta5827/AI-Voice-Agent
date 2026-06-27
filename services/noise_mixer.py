"""Mixes pre-recorded background noise into TTS output for call-center realism."""

import array
import logging
from pathlib import Path

from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

_PCM_PATH = Path("assets/bg_noise_loop.pcm")


class BackgroundNoiseMixer(FrameProcessor):
    def __init__(
        self,
        *,
        noise_path: Path = _PCM_PATH,
        noise_gain: float = 0.15,
        speech_gain: float = 0.90,
        name: str = "NoiseMixer",
    ):
        super().__init__(name=name)
        self._noise_gain = noise_gain
        self._speech_gain = speech_gain
        self._noise_pcm: bytes = b""
        self._pos = 0

        if not noise_path.exists():
            logger.warning("Noise loop file not found at %s — mixer disabled.", noise_path)
            return
        self._noise_pcm = noise_path.read_bytes()
        logger.info(
            "Loaded noise loop: %s (%.1fs @ 8kHz, gain=%.2f)",
            noise_path, len(self._noise_pcm) / 2 / 8000, self._noise_gain,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            not self._noise_pcm
            or direction != FrameDirection.DOWNSTREAM
            or not isinstance(frame, TTSAudioRawFrame)
        ):
            await self.push_frame(frame, direction)
            return

        # Unpack speech samples (int16)
        n_speech = len(frame.audio) // 2
        if n_speech == 0:
            await self.push_frame(frame, direction)
            return

        speech = array.array("h", frame.audio)

        # Slice noise segment matching speech length
        n_noise_total = len(self._noise_pcm) // 2
        end = self._pos + n_speech

        if end <= n_noise_total:
            noise_seg = self._noise_pcm[self._pos * 2 : end * 2]
        else:
            # Wrap around
            first_part = self._noise_pcm[self._pos * 2 :]
            remaining = n_speech - (n_noise_total - self._pos)
            noise_seg = first_part + self._noise_pcm[: remaining * 2]

        noise = array.array("h", noise_seg)

        # Mix with gain
        sg = self._speech_gain
        ng = self._noise_gain
        mixed = array.array("h", (
            max(-32768, min(32767, int(s * sg + n * ng)))
            for s, n in zip(speech, noise)
        ))

        self._pos = (self._pos + n_speech) % n_noise_total

        out_frame = TTSAudioRawFrame(
            audio=mixed.tobytes(),
            sample_rate=frame.sample_rate,
            num_channels=frame.num_channels,
        )

        await self.push_frame(out_frame, direction)
