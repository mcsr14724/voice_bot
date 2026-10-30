"""Unit tests for WebSocket control-message validation."""

import unittest

from pydantic import TypeAdapter, ValidationError

from backend.models.websocket import ClientMessage, ClientPing


class WebSocketMessageTests(unittest.TestCase):
    def test_ping_message_is_validated(self) -> None:
        message = TypeAdapter(ClientMessage).validate_json('{"type":"ping","request_id":"r-1"}')

        self.assertIsInstance(message, ClientPing)
        self.assertEqual(message.request_id, "r-1")

    def test_interrupt_message_is_validated(self) -> None:
        message = TypeAdapter(ClientMessage).validate_json('{"type":"interrupt"}')

        self.assertEqual(message.type, "interrupt")

    def test_unknown_or_oversized_fields_are_rejected(self) -> None:
        adapter = TypeAdapter(ClientMessage)

        with self.assertRaises(ValidationError):
            adapter.validate_json('{"type":"ping","request_id":"r-1","user_id":"attacker"}')
        with self.assertRaises(ValidationError):
            adapter.validate_json('{"type":"ping","request_id":"' + ("x" * 65) + '"}')


if __name__ == "__main__":
    unittest.main()
