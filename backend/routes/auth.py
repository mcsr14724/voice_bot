"""Registration, login, and authenticated-user endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.config import Settings, get_settings
from backend.core.exceptions import ApplicationError
from backend.core.security import (
    burn_password_verification,
    create_access_token,
    decode_access_token,
    hash_password,
    validate_token_configuration,
    verify_password,
)
from backend.database.repositories.user import UserRepository
from backend.models.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


def get_user_repository(request: Request) -> UserRepository:
    repository: UserRepository | None = getattr(request.app.state, "user_repository", None)
    if repository is None:
        raise ApplicationError(
            "Authentication storage is unavailable", status_code=503, code="database_unavailable"
        )
    return repository


def _user_response(user: dict[str, Any]) -> UserResponse:
    return UserResponse(id=str(user["_id"]), email=user["email"], created_at=user["created_at"])


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserResponse:
    try:
        validate_token_configuration(settings)
    except ValueError:
        raise ApplicationError(
            "Authentication is not configured", status_code=503, code="auth_not_configured"
        ) from None
    if credentials is None:
        raise _credentials_error()
    user_id = decode_access_token(credentials.credentials, settings)
    if user_id is None:
        raise _credentials_error()
    user = await repository.find_by_id(user_id)
    if user is None:
        raise _credentials_error()
    return _user_response(user)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    try:
        validate_token_configuration(settings)
    except ValueError:
        raise ApplicationError(
            "Authentication is not configured", status_code=503, code="auth_not_configured"
        ) from None
    password_hash = await hash_password(body.password)
    user = await repository.create(body.email, password_hash)
    return _user_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    repository: Annotated[UserRepository, Depends(get_user_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    try:
        validate_token_configuration(settings)
    except ValueError:
        raise ApplicationError(
            "Authentication is not configured", status_code=503, code="auth_not_configured"
        ) from None
    user = await repository.find_by_email(body.email)
    if user is None:
        await burn_password_verification(body.password)
        raise _credentials_error()
    if not await verify_password(body.password, user["password_hash"]):
        raise _credentials_error()
    token = create_access_token(str(user["_id"]), settings)
    response.set_cookie(
        key="voice_access_token",
        value=token,
        max_age=30 * 60,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path=f"{settings.api_v1_prefix}/ws",
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    return current_user
