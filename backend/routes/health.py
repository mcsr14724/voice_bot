"""Liveness endpoint for process and deployment checks."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from asyncpg import PostgresError
from pymongo.errors import PyMongoError

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Report that the HTTP application is responsive."""
    return {"status": "ok"}


@router.get("/ready", tags=["health"])
async def readiness_check(request: Request) -> JSONResponse:
    """Check the PostgreSQL account store and MongoDB conversation store."""
    postgres = getattr(request.app.state, "postgres", None)
    if postgres is None or postgres.pool is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "postgres_not_configured"},
        )
    try:
        await postgres.pool.fetchval("SELECT 1")
    except PostgresError as error:
        logger.warning("PostgreSQL readiness check failed (%s)", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "postgres_unavailable"},
        )

    database = getattr(request.app.state, "mongodb", None)
    if database is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "mongodb_not_configured"},
        )
    try:
        await database.client.admin.command("ping")
    except PyMongoError as error:
        logger.warning("Readiness check failed (%s)", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependency": "mongodb_unavailable"},
        )
    return JSONResponse(content={"status": "ready"})
