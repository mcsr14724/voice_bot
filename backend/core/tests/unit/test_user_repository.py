"""Unit tests for user-account persistence behavior."""

import unittest
from unittest.mock import AsyncMock, MagicMock

import asyncpg

from backend.core.exceptions import ApplicationError
from backend.core.database.repositories.user import UserRepository


class UserRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_email_returns_safe_conflict_error(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=asyncpg.UniqueViolationError("duplicate email index"))
        repository = UserRepository(pool)

        with self.assertRaises(ApplicationError) as raised:
            await repository.create("person@example.com", "argon-hash")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "account_exists")
        self.assertNotIn("argon-hash", raised.exception.message)

    async def test_create_normalizes_email_and_uses_a_string_id(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="INSERT 0 1")
        repository = UserRepository(pool)

        user = await repository.create(" Person@Example.com ", "argon-hash")

        self.assertEqual(user["email"], "person@example.com")
        self.assertIsInstance(user["_id"], str)
        self.assertEqual(pool.execute.await_args.args[1:3], (user["_id"], "person@example.com"))

    async def test_find_by_email_and_id_map_postgres_rows_to_user_shape(self) -> None:
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        row = {"id": "legacy-object-id", "email": "person@example.com", "password_hash": "argon-hash", "created_at": now}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)
        repository = UserRepository(pool)

        by_email = await repository.find_by_email("PERSON@example.com")
        by_id = await repository.find_by_id("legacy-object-id")

        self.assertEqual(by_email["_id"], "legacy-object-id")
        self.assertEqual(by_id["email"], "person@example.com")
        self.assertEqual(pool.fetchrow.await_count, 2)


if __name__ == "__main__":
    unittest.main()
