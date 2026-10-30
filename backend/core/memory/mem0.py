"""Mem0 Platform adapter with user-scoped semantic search and writes."""

import asyncio
from collections.abc import Sequence
from mem0 import MemoryClient

from backend.core.llm.protocol import ChatMessage


class Mem0LongTermMemory:
    """Use Mem0 Platform's hosted memory API without blocking the event loop."""

    def __init__(self, api_key: str) -> None:
        self._client = MemoryClient(api_key=api_key)

    async def search(self, *, user_id: str, query: str, limit: int = 5) -> list[str]:
        results = await asyncio.to_thread(
            self._client.search,
            query,
            filters={"user_id": user_id},
        )
        if isinstance(results, dict):
            items = results.get("results", [])
        else:
            items = results
        if not isinstance(items, list):
            return []
        memories: list[str] = []
        for item in items[:limit]:
            if isinstance(item, dict):
                memory = item.get("memory")
                if isinstance(memory, str) and memory.strip():
                    memories.append(memory.strip())
        return memories

    async def add(self, *, user_id: str, messages: Sequence[ChatMessage]) -> None:
        payload: list[dict[str, str]] = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"} and message.content.strip()
        ]
        if payload:
            await asyncio.to_thread(self._client.add, payload, user_id=user_id)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)
