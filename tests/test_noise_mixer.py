"""Unit tests for BackgroundNoiseMixer audio mixing logic and PCM management."""

import array
import struct
import tempfile
from pathlib import Path

import pytest

from services.noise_mixer import BackgroundNoiseMixer


class TestMixingMath:
    """Test the core PCM sample mixing with gain and clamping."""

    def _mix(self, speech, noise, sg=0.9, ng=0.15):
        return array.array("h", (
            max(-32768, min(32767, int(s * sg + n * ng)))
            for s, n in zip(speech, noise)
        ))

    def test_silence_plus_noise_has_content(self):
        noise = array.array("h", [1000] * 320)
        silence = array.array("h", [0] * 320)
        result = self._mix(silence, noise, sg=0.9, ng=0.1)
        assert all(s == 100 for s in result)  # 1000 * 0.1

    def test_no_clipping_at_int16_max(self):
        noise = array.array("h", [32767] * 320)
        speech = array.array("h", [32767] * 320)
        result = self._mix(speech, noise, sg=1.0, ng=1.0)
        assert all(s == 32767 for s in result)

    def test_no_clipping_at_int16_min(self):
        noise = array.array("h", [-32768] * 320)
        speech = array.array("h", [-32768] * 320)
        result = self._mix(speech, noise, sg=1.0, ng=1.0)
        assert all(s == -32768 for s in result)

    def test_gains_reduce_amplitude(self):
        noise = array.array("h", [10000] * 320)
        speech = array.array("h", [20000] * 320)
        result = self._mix(speech, noise, sg=0.5, ng=0.1)
        assert all(s == 11000 for s in result)

    def test_negative_noise_mixes_correctly(self):
        noise = array.array("h", [-5000] * 320)
        speech = array.array("h", [10000] * 320)
        result = self._mix(speech, noise, sg=1.0, ng=0.5)
        assert all(s == 7500 for s in result)


@pytest.fixture
def noise_pcm():
    """1s 8kHz noise loop — 440Hz sine at ~-12dB."""
    import math
    sr = 8000
    amplitude = 8200
    samples = array.array(
        "h",
        (int(amplitude * math.sin(2 * math.pi * 440 * i / sr)) for i in range(sr)),
    )
    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
        f.write(samples.tobytes())
        return Path(f.name)


class TestNoiseLoading:
    def test_loads_noise_from_file(self, noise_pcm):
        mixer = BackgroundNoiseMixer(noise_path=noise_pcm)
        assert len(mixer._noise_pcm) == 8000 * 2
        assert mixer._pos == 0

    def test_disabled_when_file_missing(self):
        mixer = BackgroundNoiseMixer(noise_path=Path("/nonexistent.pcm"))
        assert mixer._noise_pcm == b""


class TestPositionTracking:
    def test_position_advances_normally(self, noise_pcm):
        mixer = BackgroundNoiseMixer(noise_path=noise_pcm)
        mixer._pos = 0
        n_speech = 320  # 20ms @ 8kHz
        n_total = len(mixer._noise_pcm) // 2
        mixer._pos = (mixer._pos + n_speech) % n_total
        assert mixer._pos == 320

    def test_position_wraps_around(self, noise_pcm):
        mixer = BackgroundNoiseMixer(noise_path=noise_pcm)
        n_total = len(mixer._noise_pcm) // 2
        mixer._pos = n_total - 200  # near end
        n_speech = 1000
        mixer._pos = (mixer._pos + n_speech) % n_total
        assert mixer._pos == 800


class TestNoiseSliceWrapping:
    """Test the byte-slicing logic that handles loop wraparound."""

    def test_simple_slice_no_wrap(self, noise_pcm):
        mixer = BackgroundNoiseMixer(noise_path=noise_pcm)
        mixer._pos = 0
        n_speech = 320
        n_total = len(mixer._noise_pcm) // 2

        end = mixer._pos + n_speech
        noise_seg = mixer._noise_pcm[mixer._pos * 2 : end * 2]
        assert len(noise_seg) == n_speech * 2

    def test_slice_with_wrap(self, noise_pcm):
        mixer = BackgroundNoiseMixer(noise_path=noise_pcm)
        n_total = len(mixer._noise_pcm) // 2
        mixer._pos = n_total - 100
        n_speech = 200

        end = mixer._pos + n_speech
        first_part = mixer._noise_pcm[mixer._pos * 2 :]
        remaining = n_speech - (n_total - mixer._pos)
        noise_seg = first_part + mixer._noise_pcm[: remaining * 2]

        assert len(noise_seg) == n_speech * 2
        # First 100 samples from tail, next 100 from head
        assert noise_seg[0:200] == mixer._noise_pcm[(n_total - 100) * 2 : n_total * 2]
        assert noise_seg[-200:] == mixer._noise_pcm[0:200]


class TestGainValues:
    def test_default_gains_within_range(self):
        mixer = BackgroundNoiseMixer(noise_path=Path("/nonexistent.pcm"))
        assert 0.0 < mixer._noise_gain < 0.5
        assert 0.5 < mixer._speech_gain <= 1.0

    def test_custom_gains_accepted(self):
        mixer = BackgroundNoiseMixer(
            noise_path=Path("/nonexistent.pcm"),
            noise_gain=0.25,
            speech_gain=0.80,
        )
        assert mixer._noise_gain == 0.25
        assert mixer._speech_gain == 0.80
