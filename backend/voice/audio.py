"""PCM audio-frame metadata shared by browser transport and speech adapters."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate: int
    channels: int
    frame_duration_ms: int

    @property
    def frame_size_bytes(self) -> int:
        return self.sample_rate * self.channels * 2 * self.frame_duration_ms // 1_000


@dataclass(frozen=True, slots=True)
class AudioFrame:
    sequence: int
    pcm_s16le: bytes
    frame_duration_ms: int


@dataclass(frozen=True, slots=True)
class AudioStreamEnd:
    """Mark audio.stop while keeping the session-level consumer alive."""
