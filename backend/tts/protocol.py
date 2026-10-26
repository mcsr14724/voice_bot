"""Provider-neutral streaming speech interface."""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Protocol


class SpeechSynthesizer(Protocol):
    """Interface for streaming raw mono PCM audio for a text response."""

    def stream_audio(
        self, text_chunks: AsyncIterable[str], *, language: str | None = None
    ) -> AsyncIterator[bytes]:
        """Synthesize an incoming text stream and yield raw 24 kHz mono PCM chunks."""
        ...

    async def aclose(self) -> None:
        """Release provider resources."""
        ...
