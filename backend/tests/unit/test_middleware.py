"""Offline tests for request correlation and safe error responses."""

import json
import unittest

from backend.core.middleware import RequestObservabilityMiddleware


class RequestObservabilityMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_request_id_and_returns_safe_error_on_unhandled_failure(self) -> None:
        async def failing_app(scope, receive, send):
            raise RuntimeError("private credential detail")

        middleware = RequestObservabilityMiddleware(failing_app)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "GET", "path": "/private?token=secret"}
        await middleware(scope, receive, send)

        headers = dict(sent[0]["headers"])
        body = json.loads(sent[1]["body"])
        self.assertEqual(sent[0]["status"], 500)
        self.assertRegex(headers[b"x-request-id"].decode(), r"^[0-9a-f]{32}$")
        self.assertNotIn("private credential detail", str(body))
        self.assertEqual(body["error"]["code"], "internal_error")

    async def test_non_http_scope_passes_through(self) -> None:
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        async def unused():
            return {}

        await RequestObservabilityMiddleware(app)(
            {"type": "websocket"}, unused, unused
        )

        self.assertEqual(seen, ["websocket"])


if __name__ == "__main__":
    unittest.main()
