"""Request correlation and access logging for HTTP traffic."""

import logging
import json
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core.logging import request_id_context

logger = logging.getLogger(__name__)


class RequestObservabilityMiddleware:
    """Add a generated request ID and log safe request timing metadata."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        token = request_id_context.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as error:
            logger.error("Unhandled HTTP request error (%s)", type(error).__name__)
            if response_started:
                raise
            body = json.dumps(
                {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
            ).encode("utf-8")
            await send_with_request_id(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send_with_request_id(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "HTTP request completed method=%s path=%s status=%s elapsed_ms=%.2f",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status_code,
                elapsed_ms,
            )
            request_id_context.reset(token)

