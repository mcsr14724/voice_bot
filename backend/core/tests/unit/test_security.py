"""Unit tests for password and access-token security helpers."""

import unittest

from backend.core.config import Settings
from backend.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    validate_token_configuration,
    verify_password,
)


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_hash_verifies_without_storing_plaintext(self) -> None:
        password = "correct-horse-battery-staple"
        password_hash = await hash_password(password)

        self.assertNotEqual(password_hash, password)
        self.assertTrue(await verify_password(password, password_hash))
        self.assertFalse(await verify_password("wrong-password", password_hash))

    def test_access_token_uses_user_subject_and_rejects_another_secret(self) -> None:
        settings = Settings(_env_file=None, secret_key="a" * 32)
        other_settings = Settings(_env_file=None, secret_key="b" * 32)
        token = create_access_token("507f1f77bcf86cd799439011", settings)

        self.assertEqual(decode_access_token(token, settings), "507f1f77bcf86cd799439011")
        self.assertIsNone(decode_access_token(token, other_settings))

    def test_access_token_requires_configured_strong_secret(self) -> None:
        settings = Settings(_env_file=None, secret_key="short")

        with self.assertRaisesRegex(ValueError, "32 characters"):
            create_access_token("user-id", settings)

    def test_authentication_requires_a_configured_secret(self) -> None:
        with self.assertRaisesRegex(ValueError, "signing secret"):
            validate_token_configuration(Settings(_env_file=None))


if __name__ == "__main__":
    unittest.main()
