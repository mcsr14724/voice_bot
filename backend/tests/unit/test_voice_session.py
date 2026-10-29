"""Unit tests for voice session lifecycle and manager cleanup."""

import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.voice.session import SessionStatus, VoiceSession, VoiceSessionManager


class VoiceSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_runs_control_loop_and_closes_cleanly(self) -> None:
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        websocket.send_json = AsyncMock()
        websocket.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": '{"type":"ping","request_id":"p1"}'},
                {"type": "websocket.receive", "text": '{"type":"interrupt"}'},
                {"type": "websocket.receive", "text": '{"type":"close"}'},
            ]
        )
        websocket.close = AsyncMock()
        session = VoiceSession(user_id="user-1", websocket=websocket)

        await session.run()

        self.assertEqual(session.status, SessionStatus.CLOSED)
        self.assertEqual(websocket.send_json.await_args_list[1].args[0], {"type": "pong", "request_id": "p1"})
        self.assertEqual(websocket.send_json.await_args_list[2].args[0]["type"], "response.interrupted")
        websocket.close.assert_awaited_once_with(code=1000, reason=None)

    async def test_interrupt_cancels_response_and_invalidates_generation(self) -> None:
        websocket = MagicMock()
        session = VoiceSession(user_id="user-1", websocket=websocket, status=SessionStatus.ACTIVE)
        started = asyncio.Event()

        async def pending_response() -> None:
            started.set()
            await asyncio.Event().wait()

        generation, task = session.start_response_task(pending_response())
        await started.wait()
        self.assertTrue(session.is_response_current(generation))

        interrupted_generation = await session.interrupt_responses()

        self.assertTrue(task.cancelled())
        self.assertGreater(interrupted_generation, generation)
        self.assertFalse(session.is_response_current(generation))

    async def test_audio_start_frames_and_stop_use_bounded_queue(self) -> None:
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        websocket.close = AsyncMock()
        audio_ended = asyncio.Event()
        sent_messages: list[dict] = []

        async def send_json(message: dict) -> None:
            sent_messages.append(message)
            if message.get("type") == "audio.ended":
                audio_ended.set()

        receive_events: asyncio.Queue[dict] = asyncio.Queue()
        await receive_events.put({"type": "websocket.receive", "text": '{"type":"audio.start","encoding":"pcm_s16le","sample_rate":16000,"channels":1,"frame_duration_ms":20}'})
        speech_frame = bytes([0xE8, 0x03]) * 320
        await receive_events.put({"type": "websocket.receive", "bytes": speech_frame})
        await receive_events.put({"type": "websocket.receive", "bytes": speech_frame})
        await receive_events.put({"type": "websocket.receive", "text": '{"type":"audio.stop"}'})

        async def receive() -> dict:
            return await receive_events.get()

        websocket.send_json = AsyncMock(side_effect=send_json)
        websocket.receive = receive
        session = VoiceSession(user_id="user-1", websocket=websocket)
        with patch("backend.voice.session.get_settings", return_value=MagicMock(deepgram_api_key=None)):
            run_task = asyncio.create_task(session.run())
            await asyncio.wait_for(audio_ended.wait(), timeout=1)
            await asyncio.sleep(0)
            event_types = [message["type"] for message in sent_messages]
            self.assertIn("speech.started", event_types)
            self.assertIn("speech.ended", event_types)
            self.assertIn("audio.receiving", event_types)
            self.assertLess(event_types.index("audio.ready"), event_types.index("transcription.unavailable"))
            self.assertEqual(session.audio_frames.qsize(), 0)
            self.assertIn({"type": "audio.ready", "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_duration_ms": 20}, sent_messages)
            await receive_events.put({"type": "websocket.disconnect"})
            await asyncio.wait_for(run_task, timeout=1)

    async def test_audio_frame_without_negotiation_closes_protocol(self) -> None:
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        websocket.send_json = AsyncMock()
        websocket.receive = AsyncMock(return_value={"type": "websocket.receive", "bytes": bytes(640)})
        websocket.close = AsyncMock()
        session = VoiceSession(user_id="user-1", websocket=websocket)

        await session.run()

        websocket.close.assert_awaited_once_with(code=1002, reason="Send audio.start before audio frames")
        self.assertEqual(session.status, SessionStatus.CLOSED)

    async def test_idle_voice_room_does_not_open_transcription_provider(self) -> None:
        websocket = MagicMock()
        websocket.accept = AsyncMock()
        websocket.send_json = AsyncMock()
        websocket.receive = AsyncMock(return_value={"type": "websocket.disconnect"})
        websocket.close = AsyncMock()
        session = VoiceSession(user_id="user-1", websocket=websocket)

        with patch("backend.voice.session.DeepgramTranscriber") as transcriber:
            await session.run()

        transcriber.assert_not_called()
        self.assertIsNone(session._audio_consumer)

    async def test_manager_tracks_and_closes_sessions_on_shutdown(self) -> None:
        websocket = MagicMock()
        websocket.close = AsyncMock()
        session = VoiceSession(user_id="user-1", websocket=websocket, status=SessionStatus.ACTIVE)
        manager = VoiceSessionManager()
        await manager.register(session)

        self.assertEqual(await manager.active_count(), 1)
        await manager.shutdown()

        self.assertEqual(session.status, SessionStatus.CLOSED)
        self.assertEqual(await manager.active_count(), 0)
        websocket.close.assert_awaited_once_with(code=1012, reason="Server shutting down")


if __name__ == "__main__":
    unittest.main()
