"""Persistence for user-owned voice conversation history."""

from datetime import datetime, timezone
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection


class ConversationRepository:
    """Store bounded transcript history with strict user ownership filters."""

    MAX_MESSAGES = 200

    def __init__(
        self,
        collection: AsyncCollection[dict[str, Any]],
        *,
        checkpoint_collection: AsyncCollection[dict[str, Any]] | None = None,
        checkpoint_writes_collection: AsyncCollection[dict[str, Any]] | None = None,
    ) -> None:
        self._collection = collection
        self._checkpoint_collection = checkpoint_collection
        self._checkpoint_writes_collection = checkpoint_writes_collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index([("user_id", 1), ("updated_at", -1)])

    async def find_for_user(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        return await self._collection.find_one({"_id": conversation_id, "user_id": user_id})

    async def delete_for_user(self, conversation_id: str, user_id: str) -> bool:
        """Delete an owned transcript and its graph state, without touching user memories."""
        if await self.find_for_user(conversation_id, user_id) is None:
            return False

        # LangGraph checkpoints use the conversation ID as thread_id.
        if self._checkpoint_collection is not None:
            await self._checkpoint_collection.delete_many({"thread_id": conversation_id})
        if self._checkpoint_writes_collection is not None:
            await self._checkpoint_writes_collection.delete_many({"thread_id": conversation_id})

        result = await self._collection.delete_one(
            {"_id": conversation_id, "user_id": user_id}
        )
        return result.deleted_count > 0

    async def list_for_user(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        cursor = self._collection.find({"user_id": user_id}).sort("updated_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def record_turn(
        self, *, conversation_id: str, user_id: str, user_text: str, assistant_text: str = ""
    ) -> None:
        now = datetime.now(timezone.utc)
        messages = [{"role": "user", "content": user_text, "created_at": now}]
        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text, "created_at": now})
        await self._collection.update_one(
            {"_id": conversation_id, "user_id": user_id},
            {
                "$setOnInsert": {"user_id": user_id, "created_at": now},
                "$set": {"updated_at": now, "preview": user_text[:240]},
                "$push": {"messages": {"$each": messages, "$slice": -self.MAX_MESSAGES}},
            },
            upsert=True,
        )
