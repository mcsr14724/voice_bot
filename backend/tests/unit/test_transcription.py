"""Unit tests for the Deepgram streaming protocol adapter."""

import asyncio
import json
import unittest
from unittest.mock import patch

from websockets.exceptions import InvalidStatus

from backend.voice.audio import AudioFrame, AudioStreamEnd
from backend.voice.transcription import DeepgramTranscriber


class DeepgramTranscriberTests(unittest.IsolatedAsyncioTestCase):
    def test_classifies_connection_failures_without_provider_details(self) -> None:
        for status_code, expected in (
            (401, "credentials_rejected"),
            (403, "credentials_rejected"),
            (429, "rate_limited"),
            (400, "request_rejected"),
            (503, "provider_unavailable"),
        ):
            with self.subTest(status_code=status_code):
                response = type("Response", (), {"status_code": status_code})()
                error = InvalidStatus(response)

                self.assertEqual(
                    DeepgramTranscriber.connection_error_code(error),
                    expected,
                )

        self.assertEqual(
            DeepgramTranscriber.connection_error_code(TimeoutError()),
            "connection_timeout",
        )
        self.assertEqual(
            DeepgramTranscriber.connection_error_code(ConnectionError()),
            "connection_failed",
        )

    async def test_stream_connects_with_key_streams_audio_and_emits_transcript(self) -> None:
        closed = asyncio.Event()
        sent: list[bytes | str] = []
        received = json.dumps(
            {
                "type": "Results",
                "is_final": True,
                "speech_final": True,
                "channel": {"alternatives": [{"transcript": "Good morning."}]},
            }
        )

        class Socket:
            async def send(self, value: bytes | str) -> None:
                sent.append(value)
                if value == json.dumps({"type": "CloseStream"}):
                    closed.set()

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "sent_result"):
                    self.sent_result = True
                    return received
                await closed.wait()
                raise StopAsyncIteration

        class Connection:
            async def __aenter__(self):
                return socket

            async def __aexit__(self, *_args):
                return False

        socket = Socket()
        frames: asyncio.Queue[AudioFrame | AudioStreamEnd | None] = asyncio.Queue()
        await frames.put(AudioFrame(sequence=1, pcm_s16le=bytes(640), frame_duration_ms=20))
        await frames.put(AudioStreamEnd())
        await frames.put(None)
        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        with patch("backend.voice.transcription.connect", return_value=Connection()) as connector:
            await DeepgramTranscriber("test-key").consume(frames, emit)

        self.assertIn("model=nova-3", connector.call_args.args[0])
        self.assertIn("sample_rate=16000", connector.call_args.args[0])
        self.assertEqual(connector.call_args.kwargs["additional_headers"], {"Authorization": "Token test-key"})
        self.assertEqual(sent[0], bytes(640))
        self.assertEqual(
            events,
            [
                {"type": "transcription.connected"},
                {"type": "transcription.audio_forwarded"},
                {"type": "transcript", "text": "Good morning.", "is_final": True, "speech_final": True},
            ],
        )

    async def test_audio_queue_sends_pcm_and_stream_controls(self) -> None:
        frames: asyncio.Queue[AudioFrame | AudioStreamEnd | None] = asyncio.Queue()
        await frames.put(AudioFrame(sequence=3, pcm_s16le=bytes(640), frame_duration_ms=20))
        await frames.put(AudioStreamEnd())
        await frames.put(None)

        class Socket:
            def __init__(self) -> None:
                self.sent: list[bytes | str] = []

            async def send(self, value: bytes | str) -> None:
                self.sent.append(value)

        socket = Socket()
        forwarded: list[bool] = []

        async def on_first_audio() -> None:
            forwarded.append(True)

        await DeepgramTranscriber._send_frames(socket, frames, on_first_audio=on_first_audio)

        self.assertEqual(socket.sent[0], bytes(640))
        self.assertEqual(forwarded, [True])
        self.assertEqual(json.loads(socket.sent[1]), {"type": "Finalize"})
        self.assertEqual(json.loads(socket.sent[2]), {"type": "CloseStream"})

    async def test_normalizes_interim_final_and_provider_events(self) -> None:
        partial = DeepgramTranscriber.normalize_message(
            {
                "type": "Results",
                "is_final": False,
                "speech_final": False,
                "channel": {"alternatives": [{"transcript": "hello there"}]},
            }
        )
        final = DeepgramTranscriber.normalize_message(
            {
                "type": "Results",
                "is_final": True,
                "speech_final": True,
                "channel": {"alternatives": [{"transcript": "hello there."}]},
            }
        )

        self.assertEqual(
            partial,
            {"type": "transcript", "text": "hello there", "is_final": False, "speech_final": False},
        )
        self.assertTrue(final["is_final"])
        self.assertTrue(final["speech_final"])
        self.assertEqual(
            DeepgramTranscriber.normalize_message({"type": "SpeechStarted"}),
            {"type": "speech.started"},
        )
        self.assertEqual(
            DeepgramTranscriber.normalize_message({"type": "UtteranceEnd"}),
            {"type": "speech.ended"},
        )
        self.assertEqual(
            DeepgramTranscriber.normalize_message({"type": "Error"}),
            {"type": "transcription.error", "code": "provider_error"},
        )

    def test_ignores_empty_or_unknown_provider_messages(self) -> None:
        self.assertIsNone(
            DeepgramTranscriber.normalize_message(
                {"type": "Results", "channel": {"alternatives": [{"transcript": "  "}]}}
            )
        )
        self.assertIsNone(DeepgramTranscriber.normalize_message({"type": "Metadata"}))


if __name__ == "__main__":
    unittest.main()
