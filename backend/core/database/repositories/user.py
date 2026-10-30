"""PostgreSQL persistence operations for user accounts."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg

from backend.core.exceptions import ApplicationError


class UserRepository:
    """Read and write account records in the PostgreSQL pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_indexes(self) -> None:
        await self._pool.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uidx ON users (lower(email))"
        )

    async def find_by_email(self, email: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, email, password_hash, created_at
            FROM users
            WHERE lower(email) = lower($1)
            """,
            email,
        )
        return self._as_user(row) if row is not None else None

    async def find_by_id(self, user_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT id, email, password_hash, created_at FROM users WHERE id = $1",
            user_id,
        )
        return self._as_user(row) if row is not None else None

    async def create(self, email: str, password_hash: str) -> dict[str, Any]:
        normalized_email = email.strip().lower()
        user = {
            "_id": str(uuid4()),
            "email": normalized_email,
            "password_hash": password_hash,
            "created_at": datetime.now(timezone.utc),
        }
        try:
            await self._pool.execute(
                """
                INSERT INTO users (id, email, password_hash, created_at)
                VALUES ($1, $2, $3, $4)
                """,
                user["_id"],
                user["email"],
                user["password_hash"],
                user["created_at"],
            )
        except asyncpg.UniqueViolationError:
            raise ApplicationError(
                "An account with this email already exists", status_code=409, code="account_exists"
            ) from None
        return user

    @staticmethod
    def _as_user(row: asyncpg.Record) -> dict[str, Any]:
        return {
            "_id": str(row["id"]),
            "email": row["email"],
            "password_hash": row["password_hash"],
            "created_at": row["created_at"],
        }
