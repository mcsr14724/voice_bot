"""Unit tests for authentication input validation."""

import unittest

from pydantic import ValidationError

from backend.models.auth import LoginRequest, RegisterRequest


class AuthModelTests(unittest.TestCase):
    def test_email_is_normalized(self) -> None:
        request = RegisterRequest(email="  Person@Example.com ", password="a-long-secure-password")

        self.assertEqual(request.email, "person@example.com")

    def test_registration_rejects_short_password(self) -> None:
        with self.assertRaises(ValidationError):
            RegisterRequest(email="person@example.com", password="short")

    def test_login_rejects_invalid_email(self) -> None:
        with self.assertRaises(ValidationError):
            LoginRequest(email="not-an-email", password="password")


if __name__ == "__main__":
    unittest.main()
