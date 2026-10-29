"""Offline API tests for account registration, login, and bearer auth."""

import unittest
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from backend.core.config import Settings, get_settings
from backend.core.exceptions import ApplicationError, application_error_handler
from backend.core.security import hash_password, verify_password
from backend.routes.auth import router as auth_router


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}

    async def create(self, email: str, password_hash: str) -> dict[str, Any]:
        if email in self.users:
            raise ApplicationError("An account with this email already exists", status_code=409, code="account_exists")
        user = {
            "_id": "507f1f77bcf86cd799439011",
            "email": email,
            "password_hash": password_hash,
            "created_at": datetime.now(timezone.utc),
        }
        self.users[email] = user
        return user

    async def find_by_email(self, email: str) -> dict[str, Any] | None:
        return self.users.get(email)

    async def find_by_id(self, user_id: str) -> dict[str, Any] | None:
        return next((user for user in self.users.values() if user["_id"] == user_id), None)


class AuthenticationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = Settings(_env_file=None, secret_key="s" * 32)
        self.repository = InMemoryUserRepository()
        self.app = FastAPI()
        self.app.include_router(auth_router, prefix=self.settings.api_v1_prefix)
        self.app.add_exception_handler(ApplicationError, application_error_handler)
        self.app.state.user_repository = self.repository
        self.app.dependency_overrides[get_settings] = lambda: self.settings
        from backend.routes.auth import get_user_repository

        self.app.dependency_overrides[get_user_repository] = lambda: self.repository
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_register_login_and_authenticated_me(self) -> None:
        registration = await self.client.post(
            "/api/v1/auth/register",
            json={"email": " Person@Example.com ", "password": "correct-horse-battery"},
        )

        self.assertEqual(registration.status_code, 201)
        self.assertEqual(registration.json()["email"], "person@example.com")
        stored_user = self.repository.users["person@example.com"]
        self.assertNotEqual(stored_user["password_hash"], "correct-horse-battery")
        self.assertTrue(await verify_password("correct-horse-battery", stored_user["password_hash"]))

        login = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "PERSON@example.com", "password": "correct-horse-battery"},
        )

        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["access_token"])
        cookie = login.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertIn("path=/api/v1/ws", cookie)

        current_user = await self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        self.assertEqual(current_user.status_code, 200)
        self.assertEqual(current_user.json()["email"], "person@example.com")

    async def test_production_login_cookie_supports_cross_site_websocket(self) -> None:
        self.settings = Settings(
            _env_file=None,
            environment="production",
            frontend_origins=["https://frontend.example.com"],
            secret_key="s" * 32,
        )
        await self.repository.create(
            "person@example.com", await hash_password("correct-horse-battery")
        )

        login = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.com", "password": "correct-horse-battery"},
        )

        self.assertEqual(login.status_code, 200)
        cookie = login.headers["set-cookie"].lower()
        self.assertIn("samesite=none", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("path=/api/v1/ws", cookie)

    async def test_bad_password_uses_generic_authentication_error(self) -> None:
        await self.repository.create("person@example.com", await hash_password("correct-password"))

        response = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.com", "password": "incorrect-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid authentication credentials")

    async def test_duplicate_registration_returns_safe_conflict(self) -> None:
        await self.repository.create("person@example.com", "existing-hash")

        response = await self.client.post(
            "/api/v1/auth/register",
            json={"email": "person@example.com", "password": "another-long-password"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "account_exists")
        self.assertNotIn("existing-hash", response.text)


if __name__ == "__main__":
    unittest.main()
