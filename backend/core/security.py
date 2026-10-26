"""Password hashing and signed access-token helpers."""

import asyncio
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from backend.core.config import Settings

password_hasher = PasswordHash.recommended()
ACCESS_TOKEN_LIFETIME = timedelta(minutes=30)
JWT_ALGORITHM = "HS256"


async def hash_password(password: str) -> str:
    """Hash a password off the event loop using the recommended Argon2 settings."""
    return await asyncio.to_thread(password_hasher.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password off the event loop."""
    return await asyncio.to_thread(password_hasher.verify, password, password_hash)


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return password_hasher.hash("not-a-real-user-password")


async def burn_password_verification(password: str) -> None:
    """Reduce email-enumeration timing differences for unknown accounts."""
    dummy_hash = await asyncio.to_thread(_dummy_password_hash)
    await verify_password(password, dummy_hash)


def create_access_token(user_id: str, settings: Settings) -> str:
    secret = _token_secret(settings)
    issued_at = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": issued_at,
            "exp": issued_at + ACCESS_TOKEN_LIFETIME,
            "type": "access",
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )


def validate_token_configuration(settings: Settings) -> None:
    """Fail before account operations if the server cannot issue valid tokens."""
    _token_secret(settings)


def decode_access_token(token: str, settings: Settings) -> str | None:
    try:
        payload = jwt.decode(
            token,
            _token_secret(settings),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "iat", "exp", "type"]},
        )
    except (InvalidTokenError, ValueError):
        return None
    subject = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(subject, str) or not subject:
        return None
    return subject


def _token_secret(settings: Settings) -> str:
    if settings.secret_key is None:
        raise ValueError("A signing secret is required for authentication")
    secret = settings.secret_key.get_secret_value()
    if len(secret) < 32:
        raise ValueError("The signing secret must contain at least 32 characters")
    return secret
