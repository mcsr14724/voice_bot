"""Provider-neutral types used by the conversation graph."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A plain text message in a model conversation."""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatModel(Protocol):
    """Minimal streaming interface for replaceable chat model providers."""

    def stream_reply(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Yield response text fragments for the supplied conversation."""
        ...

    async def aclose(self) -> None:
        """Release provider resources."""
        ...
