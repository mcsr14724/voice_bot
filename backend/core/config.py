"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are redacted by Pydantic on repr."""

    app_name: str = "Real-Time Voice AI Chatbot"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    api_v1_prefix: str = "/api/v1"
    frontend_origins: list[str] = ["http://localhost:3000"]
    secret_key: SecretStr | None = None
    postgres_dsn: SecretStr | None = None
    mongodb_uri: SecretStr | None = None
    deepgram_api_key: SecretStr | None = None
    deepgram_language: str = "multi"
    deepgram_language_hints: list[str] = ["en-IN", "te-IN"]
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    gemini_live_model: str = "gemini-3.8-live"
    mem0_api_key: SecretStr | None = None
    azure_speech_key: SecretStr | None = None
    azure_speech_region: str | None = None
    azure_speech_voice: str = "en-US-AvaMultilingualNeural"
    mongodb_database: str = "voice_chatbot"
    mongodb_server_selection_timeout_ms: int = Field(default=5_000, gt=0)
    postgres_server_settings_timeout_seconds: int = Field(default=5, gt=0)

    model_config = SettingsConfigDict(
        env_prefix="VOICE_BOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("frontend_origins")
    @classmethod
    def validate_frontend_origins(cls, origins: list[str]) -> list[str]:
        """Accept only explicit HTTP(S) origins, with no paths or credentials."""
        normalized: list[str] = []
        for origin in origins:
            parsed = urlsplit(origin)
            try:
                parsed.port  # Accessing this validates the port syntax and range.
            except ValueError as error:
                raise ValueError("frontend_origins entries must contain a valid port") from error
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("frontend_origins entries must be HTTP(S) origins")
            normalized.append(origin.rstrip("/"))
        return normalized

    @field_validator("postgres_dsn", mode="before")
    @classmethod
    def normalize_optional_postgres_dsn(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if (self.azure_speech_key is None) != (self.azure_speech_region is None):
            raise ValueError("azure_speech_key and azure_speech_region must be configured together")
        if self.azure_speech_region is not None and not self.azure_speech_region.strip():
            raise ValueError("azure_speech_region cannot be blank")
        if self.environment == "production":
            if self.debug:
                raise ValueError("debug must be disabled in production")
            if self.secret_key is None or len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("secret_key must contain at least 32 characters in production")
            if any(not origin.startswith("https://") for origin in self.frontend_origins):
                raise ValueError("frontend_origins must use HTTPS in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings; clear this cache when overriding configuration in tests."""
    return Settings()
