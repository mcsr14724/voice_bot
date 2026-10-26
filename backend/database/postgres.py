"""Async PostgreSQL connection lifecycle and legacy account migration."""

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
from pymongo.asynchronous.collection import AsyncCollection

from backend.core.config import Settings

logger = logging.getLogger(__name__)


class PostgresDatabase:
    """Own the PostgreSQL pool used for account and credential storage."""

    def __init__(self, settings: Settings) -> None:
        if settings.postgres_dsn is None:
            raise ValueError("postgres_dsn must be configured before creating PostgresDatabase")
        self._dsn = settings.postgres_dsn.get_secret_value()
        self._timeout = settings.postgres_server_settings_timeout_seconds
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        try:
            self.pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=1,
                max_size=10,
                timeout=self._timeout,
            )
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await self.pool.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uidx ON users (lower(email))"
            )
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS app_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        except (asyncpg.PostgresError, OSError, TimeoutError) as error:
            await self.close()
            logger.error("PostgreSQL connection failed (%s)", type(error).__name__)
            raise RuntimeError(
                "PostgreSQL connection failed; check the local service and configured DSN"
            ) from None

    async def migrate_legacy_mongo_users(
        self, collection: AsyncCollection[dict[str, Any]]
    ) -> int:
        """Copy existing MongoDB account IDs and password hashes once, without deleting source rows."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL must be connected before migrating accounts")

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.fetchval(
                    "SELECT pg_advisory_xact_lock(hashtext('mongo_users_to_postgres_v1'))"
                )
                already_migrated = await connection.fetchval(
                    "SELECT 1 FROM app_migrations WHERE name = 'mongo_users_to_postgres_v1'"
                )
                if already_migrated:
                    return 0

                migrated = 0
                async for document in collection.find({}):
                    mongo_id = document.get("_id")
                    email = document.get("email")
                    password_hash = document.get("password_hash")
                    if mongo_id is None or not isinstance(email, str) or not isinstance(password_hash, str):
                        continue
                    created_at = document.get("created_at")
                    if not isinstance(created_at, datetime):
                        created_at = datetime.now(timezone.utc)
                    elif created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    result = await connection.execute(
                        """
                        INSERT INTO users (id, email, password_hash, created_at)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT DO NOTHING
                        """,
                        str(mongo_id),
                        email.strip().lower(),
                        password_hash,
                        created_at,
                    )
                    migrated += result.endswith(" 1")

                await connection.execute(
                    "INSERT INTO app_migrations (name) VALUES ('mongo_users_to_postgres_v1')"
                )
                logger.info("Legacy MongoDB account migration completed (rows=%s)", migrated)
                return migrated

    async def close(self) -> None:
        """Close the owned connection pool."""
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
