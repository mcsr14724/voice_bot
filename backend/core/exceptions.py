"""Safe application-level HTTP errors and their FastAPI handler."""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApplicationError(Exception):
    """An expected error that can be returned safely to an API client."""

    def __init__(self, message: str, *, status_code: int = 400, code: str = "request_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


async def application_error_handler(_request: Request, error: ApplicationError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
    )


async def unexpected_error_handler(_request: Request, error: Exception) -> JSONResponse:
    """Log a server-side failure and return a generic response without internals."""
    # Exception messages and tracebacks can contain provider URLs or credentials.
    logger.error("Unhandled application error (%s)", type(error).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
    )
