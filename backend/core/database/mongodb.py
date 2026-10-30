"""Async MongoDB connection lifecycle for the application."""

import logging

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from backend.core.config import Settings

logger = logging.getLogger(__name__)


class MongoDatabase:
    """Own one event-loop-scoped PyMongo async client and database handle."""

    def __init__(self, settings: Settings) -> None:
        if settings.mongodb_uri is None:
            raise ValueError("mongodb_uri must be configured before creating MongoDatabase")

        self._client: AsyncMongoClient = AsyncMongoClient(
            settings.mongodb_uri.get_secret_value(),
            serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        )
        self.database = self._client[settings.mongodb_database]

    @property
    def client(self) -> AsyncMongoClient:
        """Expose the owned client to async integrations sharing this connection pool."""
        return self._client

    async def connect(self) -> None:
        """Verify connectivity early so startup fails with a safe diagnostic."""
        try:
            await self._client.admin.command("ping")
        except PyMongoError as error:
            logger.error("MongoDB connection failed (%s)", type(error).__name__)
            await self.close()
            raise RuntimeError("MongoDB connection failed; check database availability and configuration") from None

    async def close(self) -> None:
        """Close the async client and its connection pool."""
        await self._client.close()
