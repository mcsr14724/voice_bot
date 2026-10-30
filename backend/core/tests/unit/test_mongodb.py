"""Unit tests for the async MongoDB connection wrapper."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pymongo.errors import ServerSelectionTimeoutError

from backend.core.config import Settings
from backend.core.database.mongodb import MongoDatabase


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://test-user:test-password@localhost:27017",
    )


class MongoDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_pings_and_close_closes_client(self) -> None:
        client = MagicMock()
        client.admin.command = AsyncMock()
        client.close = AsyncMock()

        with patch("backend.core.database.mongodb.AsyncMongoClient", return_value=client) as client_factory:
            database = MongoDatabase(configured_settings())
            await database.connect()
            await database.close()

        client_factory.assert_called_once()
        client.admin.command.assert_awaited_once_with("ping")
        client.close.assert_awaited_once()

    async def test_connection_error_does_not_leak_driver_details(self) -> None:
        client = MagicMock()
        client.admin.command = AsyncMock(
            side_effect=ServerSelectionTimeoutError("test-password must not be disclosed")
        )
        client.close = AsyncMock()

        with patch("backend.core.database.mongodb.AsyncMongoClient", return_value=client):
            database = MongoDatabase(configured_settings())
            with self.assertRaisesRegex(RuntimeError, "MongoDB connection failed") as raised:
                await database.connect()

        self.assertNotIn("test-password", str(raised.exception))
        client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
