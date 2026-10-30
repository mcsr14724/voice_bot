"""Small energy-based turn boundary detector for PCM S16LE frames."""

from dataclasses import dataclass
from math import isqrt
from struct import iter_unpack

from backend.core.voice.audio import AudioFrame


@dataclass(frozen=True, slots=True)
class TurnEvent:
    type: str
    sequence: int


class EnergyTurnDetector:
    """Detect speech onset and end after sustained low-energy audio."""

    def __init__(
        self,
        *,
        speech_threshold_rms: int = 500,
        min_speech_frames: int = 2,
        silence_duration_ms: int = 600,
    ) -> None:
        if speech_threshold_rms < 1 or min_speech_frames < 1 or silence_duration_ms < 1:
            raise ValueError("Turn detector thresholds must be positive")
        self.speech_threshold_rms = speech_threshold_rms
        self.min_speech_frames = min_speech_frames
        self.silence_duration_ms = silence_duration_ms
        self._speech_candidates = 0
        self._speaking = False
        self._silence_ms = 0

    def process(self, frame: AudioFrame) -> list[TurnEvent]:
        rms = self._rms(frame.pcm_s16le)
        events: list[TurnEvent] = []
        if rms >= self.speech_threshold_rms:
            self._silence_ms = 0
            if not self._speaking:
                self._speech_candidates += 1
                if self._speech_candidates >= self.min_speech_frames:
                    self._speaking = True
                    events.append(TurnEvent("speech.started", frame.sequence))
        elif self._speaking:
            self._silence_ms += frame.frame_duration_ms
            if self._silence_ms >= self.silence_duration_ms:
                self._speaking = False
                self._speech_candidates = 0
                self._silence_ms = 0
                events.append(TurnEvent("speech.ended", frame.sequence))
        else:
            self._speech_candidates = 0
        return events

    def end_stream(self, sequence: int) -> list[TurnEvent]:
        if not self._speaking:
            self._reset()
            return []
        self._reset()
        return [TurnEvent("speech.ended", sequence)]

    def _reset(self) -> None:
        self._speech_candidates = 0
        self._speaking = False
        self._silence_ms = 0

    @staticmethod
    def _rms(pcm_s16le: bytes) -> int:
        if not pcm_s16le or len(pcm_s16le) % 2:
            raise ValueError("PCM S16LE frames must contain complete 16-bit samples")
        sample_count = len(pcm_s16le) // 2
        square_sum = sum(
            (value - 65_536 if value >= 32_768 else value) ** 2
            for (value,) in iter_unpack("<H", pcm_s16le)
        )
        return isqrt(square_sum // sample_count)
