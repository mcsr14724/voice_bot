"""HTTP tests for paired-file STT and TTS evaluation endpoints."""

import io
import json
import unittest
import wave
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.models.auth import UserResponse
from backend.routes.audio_evaluation import router
from backend.routes.auth import get_current_user


def make_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(bytes(4_800))
    return output.getvalue()


class FakeSpeechSynthesizer:
    def stream_audio(self, text_chunks, *, language=None):
        async def chunks():
            yield bytes(4_800)

        return chunks()

    async def aclose(self) -> None:
        return None


class AudioEvaluationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            _env_file=None,
            environment="development",
            enable_audio_evaluation=True,
            frontend_origins=["http://localhost:3000"],
            deepgram_api_key="test-key",
        )
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.app.state.speech_synthesizer = FakeSpeechSynthesizer()
        self.app.dependency_overrides[get_current_user] = lambda: UserResponse(
            id="test-user",
            email="test@example.com",
            created_at=datetime.now(timezone.utc),
        )
        from backend.core.config import get_settings

        self.app.dependency_overrides[get_settings] = lambda: settings
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_stt_accepts_one_pair_and_returns_metrics(self) -> None:
        with (
            patch("backend.routes.audio_evaluation._get_transcriber", return_value=object()),
            patch(
                "backend.routes.audio_evaluation._transcribe_wav",
                new=AsyncMock(return_value="Hello world"),
            ),
        ):
            response = self.client.post(
                "/api/v1/audio-evaluation/stt",
                files=[
                    ("audio_file", ("sample.wav", b"wav", "audio/wav")),
                    ("transcript_file", ("sample.txt", b"Hello, world!", "text/plain")),
                ],
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["corpus"]["word_error_rate"], 0)
        self.assertEqual(result["sample"]["recognized_transcript"], "Hello world")

    def test_stt_is_available_in_production_when_explicitly_enabled(self) -> None:
        production_settings = Settings(
            _env_file=None,
            environment="production",
            enable_audio_evaluation=True,
            frontend_origins=["https://frontend.example.com"],
            secret_key="x" * 32,
            deepgram_api_key="test-key",
        )
        from backend.core.config import get_settings

        self.app.dependency_overrides[get_settings] = lambda: production_settings
        with (
            patch("backend.routes.audio_evaluation._get_transcriber", return_value=object()),
            patch(
                "backend.routes.audio_evaluation._transcribe_wav",
                new=AsyncMock(return_value="Hello world"),
            ),
        ):
            response = self.client.post(
                "/api/v1/audio-evaluation/stt",
                files=[
                    ("audio_file", ("sample.wav", b"wav", "audio/wav")),
                    ("transcript_file", ("sample.txt", b"Hello, world!", "text/plain")),
                ],
            )

        self.assertEqual(response.status_code, 200)

    def test_stt_rejects_files_with_unmatched_stems(self) -> None:
        response = self.client.post(
            "/api/v1/audio-evaluation/stt",
            files=[
                ("audio_file", ("audio.wav", b"wav", "audio/wav")),
                ("transcript_file", ("other.txt", b"expected words", "text/plain")),
            ],
        )

        self.assertEqual(response.status_code, 422)

    def test_stt_rejects_multiple_audio_files(self) -> None:
        response = self.client.post(
            "/api/v1/audio-evaluation/stt",
            files=[
                ("audio_file", ("first.wav", b"wav", "audio/wav")),
                ("audio_file", ("second.wav", b"wav", "audio/wav")),
                ("transcript_file", ("first.txt", b"expected words", "text/plain")),
            ],
        )

        self.assertEqual(response.status_code, 422)

    def test_tts_returns_wav_archive_with_recognition_metrics(self) -> None:
        with (
            patch("backend.routes.audio_evaluation._get_transcriber", return_value=object()),
            patch(
                "backend.routes.audio_evaluation._transcribe_wav",
                new=AsyncMock(side_effect=["hello world", "hello world"]),
            ),
        ):
            response = self.client.post(
                "/api/v1/audio-evaluation/tts",
                files=[
                    ("reference_audio_file", ("sample.wav", make_wav(), "audio/wav")),
                    ("transcript_file", ("sample.txt", b"Hello world", "text/plain")),
                ],
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            summary = json.loads(archive.read("summary.json"))
            self.assertEqual(summary["generated_audio"]["word_error_rate"], 0)
            self.assertEqual(summary["sample"]["generated_audio_wer"], 0)
            self.assertIn("sample.generated.wav", archive.namelist())


if __name__ == "__main__":
    unittest.main()
