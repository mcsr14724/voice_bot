"""Provider-neutral interface for user-scoped long-term memory."""

from collections.abc import Sequence
from typing import Protocol

from backend.llm.protocol import ChatMessage


class LongTermMemory(Protocol):
    """Search relevant user memories and store completed conversation turns."""

    async def search(self, *, user_id: str, query: str, limit: int = 5) -> list[str]:
        ...

    async def add(self, *, user_id: str, messages: Sequence[ChatMessage]) -> None:
        ...

