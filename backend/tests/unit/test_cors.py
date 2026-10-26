"""Unit tests for browser API cross-origin configuration."""

import unittest

from backend.core.config import get_settings
from backend.main import create_app


class CorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_frontend_can_send_credentialed_api_requests(self) -> None:
        get_settings.cache_clear()
        app = create_app()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "OPTIONS",
            "scheme": "http",
            "path": "/api/v1/auth/login",
            "raw_path": b"/api/v1/auth/login",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"origin", b"http://localhost:3000"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"content-type"),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50000),
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)

        headers = dict(sent[0]["headers"])
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(headers[b"access-control-allow-origin"], b"http://localhost:3000")
        self.assertEqual(headers[b"access-control-allow-credentials"], b"true")
