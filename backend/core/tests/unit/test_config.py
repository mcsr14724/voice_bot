"""Unit tests for environment-backed application configuration."""

import unittest

from pydantic import ValidationError

from backend.core.config import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_safe_for_local_development(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.environment, "development")
        self.assertFalse(settings.debug)
        self.assertEqual(settings.frontend_origins, ["http://localhost:3000"])
        self.assertIsNone(settings.deepgram_api_key)
        self.assertEqual(settings.gemini_model, "gemini-3.1-flash-lite")
        self.assertEqual(settings.gemini_fallback_model, "gemini-3.5-flash-lite")

    def test_rejects_frontend_origin_with_path(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, frontend_origins=["https://example.com/app"])

    def test_production_requires_a_long_secret(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, environment="production", secret_key="too-short")

    def test_production_rejects_debug_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                environment="production",
                debug=True,
                secret_key="x" * 32,
            )

    def test_production_accepts_valid_settings(self) -> None:
        settings = Settings(
            _env_file=None,
            environment="production",
            frontend_origins=["https://voice.example.com"],
            secret_key="x" * 32,
        )

        self.assertEqual(settings.environment, "production")
        self.assertEqual(settings.frontend_origins, ["https://voice.example.com"])
        self.assertIsNotNone(settings.secret_key)

    def test_production_rejects_http_frontend_origin(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                environment="production",
                frontend_origins=["http://voice.example.com"],
                secret_key="x" * 32,
            )


if __name__ == "__main__":
    unittest.main()
