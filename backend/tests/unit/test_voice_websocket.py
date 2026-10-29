"""Unit tests for WebSocket authentication and session bootstrap."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.security import create_access_token
from backend.websocket.voice import voice_websocket
from backend.voice.session import VoiceSessionManager


class VoiceWebSocketTests(unittest.IsolatedAsyncioTestCase):
    def make_websocket(self, *, origin: str, token: str | None = None) -> MagicMock:
        websocket = MagicMock()
        websocket.headers = {"origin": origin}
        websocket.cookies = {"voice_access_token": token} if token else {}
        websocket.app.state.user_repository = None
        websocket.app.state.conversation_repository = None
        websocket.query_params = {}
        websocket.app.state.voice_session_manager = VoiceSessionManager()
        websocket.accept = AsyncMock()
        websocket.close = AsyncMock()
        websocket.send_json = AsyncMock()
        websocket.receive_json = AsyncMock()
        return websocket

    async def test_allowed_origin_and_valid_user_can_open_control_session(self) -> None:
        settings = Settings(_env_file=None, secret_key="s" * 32)
        token = create_access_token("507f1f77bcf86cd799439011", settings)
        websocket = self.make_websocket(origin="http://localhost:3000")
        websocket.receive_json.return_value = {"type": "auth", "access_token": token}
        websocket.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": '{"type":"ping","request_id":"r1"}'},
                {"type": "websocket.disconnect"},
            ]
        )
        repository = MagicMock()
        repository.find_by_id = AsyncMock(return_value={"_id": "user-id"})
        websocket.app.state.user_repository = repository

        with (
            patch("backend.websocket.voice.get_settings", return_value=settings),
        ):
            await voice_websocket(websocket)

        websocket.accept.assert_awaited_once()
        self.assertEqual(websocket.send_json.await_args_list[0].args[0]["type"], "session.ready")
        self.assertEqual(websocket.send_json.await_args_list[1].args[0], {"type": "pong", "request_id": "r1"})
        repository.find_by_id.assert_awaited_once_with("507f1f77bcf86cd799439011")
        self.assertEqual(await websocket.app.state.voice_session_manager.active_count(), 0)

    async def test_untrusted_origin_is_rejected_before_accept(self) -> None:
        websocket = self.make_websocket(origin="https://attacker.example")

        await voice_websocket(websocket)

        websocket.close.assert_awaited_once()
        websocket.accept.assert_not_awaited()

    async def test_missing_authentication_is_rejected_after_accept(self) -> None:
        websocket = self.make_websocket(origin="http://localhost:3000")
        websocket.receive_json.side_effect = TimeoutError

        await voice_websocket(websocket)

        websocket.close.assert_awaited_once()
        websocket.accept.assert_awaited_once()
        self.assertEqual(websocket.close.await_args.kwargs["code"], 4401)


class VoiceWebSocketASGITests(unittest.TestCase):
    def test_authenticated_asgi_websocket_runs_control_protocol(self) -> None:
        settings = Settings(_env_file=None, secret_key="s" * 32)
        token = create_access_token("507f1f77bcf86cd799439011", settings)
        app = FastAPI()
        app.add_api_websocket_route("/ws", voice_websocket)
        app.state.user_repository = repository = MagicMock()
        app.state.conversation_repository = None
        app.state.voice_session_manager = VoiceSessionManager()
        repository.find_by_id = AsyncMock(return_value={"_id": "user-id"})

        with (
            patch("backend.websocket.voice.get_settings", return_value=settings),
            TestClient(app) as client,
        ):
            with client.websocket_connect(
                "/ws",
                headers={"origin": "http://localhost:3000"},
            ) as websocket:
                websocket.send_json({"type": "auth", "access_token": token})
                self.assertEqual(websocket.receive_json()["type"], "session.ready")
                websocket.send_json({"type": "ping", "request_id": "asgi-check"})
                self.assertEqual(
                    websocket.receive_json(),
                    {"type": "pong", "request_id": "asgi-check"},
                )

        repository.find_by_id.assert_awaited_once_with("507f1f77bcf86cd799439011")

if __name__ == "__main__":
    unittest.main()
