"""Unit tests for safe API exception responses."""

import unittest

from starlette.requests import Request

from backend.core.exceptions import (
    ApplicationError,
    application_error_handler,
    unexpected_error_handler,
)


def make_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/"})


class ExceptionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_application_error_returns_public_code_and_message(self) -> None:
        response = await application_error_handler(
            make_request(), ApplicationError("Conversation not found", status_code=404, code="not_found")
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.body,
            b'{"error":{"code":"not_found","message":"Conversation not found"}}',
        )

    async def test_unexpected_error_does_not_expose_internal_details(self) -> None:
        response = await unexpected_error_handler(
            make_request(), RuntimeError("database password was invalid")
        )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b"database password", response.body)
        self.assertIn(b"An unexpected error occurred.", response.body)


if __name__ == "__main__":
    unittest.main()
