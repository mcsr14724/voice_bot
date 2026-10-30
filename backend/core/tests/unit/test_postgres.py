"""Unit tests for PostgreSQL connection setup and legacy user migration."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg

from backend.core.config import Settings
from backend.core.database.postgres import PostgresDatabase


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class AsyncDocuments:
    def __init__(self, documents):
        self._documents = iter(documents)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._documents)
        except StopIteration:
            raise StopAsyncIteration from None


def configured_settings() -> Settings:
    return Settings(_env_file=None, postgres_dsn="postgresql://test:test@localhost/test")


class PostgresDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_creates_schema_and_close_closes_pool(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        pool.close = AsyncMock()
        database = PostgresDatabase(configured_settings())

        with patch("backend.core.database.postgres.asyncpg.create_pool", new=AsyncMock(return_value=pool)) as create_pool:
            await database.connect()
            await database.close()

        create_pool.assert_awaited_once()
        self.assertEqual(pool.execute.await_count, 3)
        pool.close.assert_awaited_once()

    async def test_connection_error_does_not_disclose_dsn(self) -> None:
        database = PostgresDatabase(configured_settings())
        error = asyncpg.InvalidPasswordError("password=do-not-log")

        with patch("backend.core.database.postgres.asyncpg.create_pool", new=AsyncMock(side_effect=error)):
            with self.assertRaisesRegex(
                RuntimeError,
                "PostgreSQL connection failed; check the configured host, network access, and DSN",
            ) as raised:
                await database.connect()

        self.assertNotIn("do-not-log", str(raised.exception))

    async def test_migration_preserves_legacy_ids_and_hashes_once(self) -> None:
        now = datetime.now(timezone.utc)
        connection = MagicMock()
        connection.transaction.return_value = AsyncContext(None)
        connection.fetchval = AsyncMock(return_value=None)
        connection.execute = AsyncMock(side_effect=["INSERT 0 1", "INSERT 0 1"])
        pool = MagicMock()
        pool.acquire.return_value = AsyncContext(connection)
        database = PostgresDatabase(configured_settings())
        database.pool = pool
        collection = MagicMock()
        collection.find.return_value = AsyncDocuments(
            [{"_id": "legacy-id", "email": " Person@Example.com ", "password_hash": "existing-hash", "created_at": now}]
        )

        migrated = await database.migrate_legacy_mongo_users(collection)

        self.assertEqual(migrated, 1)
        args = connection.execute.await_args_list[0].args
        self.assertEqual(args[1:4], ("legacy-id", "person@example.com", "existing-hash"))
        self.assertEqual(args[4], now)
        self.assertIn("mongo_users_to_postgres_v1", connection.execute.await_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
