"""Tests for Gemini Live response-language configuration."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from backend.core.voice.gemini_live import GeminiLiveVoice


class FakeLiveConnection:
    def __init__(self) -> None:
        self.config = None

    def connect(self, *, model, config):
        self.config = config
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class GeminiLiveLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_prompt_prioritizes_explicit_output_language(self) -> None:
        connection = FakeLiveConnection()
        voice = GeminiLiveVoice.__new__(GeminiLiveVoice)
        voice._model = "test-live-model"
        voice._client = SimpleNamespace(
            aio=SimpleNamespace(live=SimpleNamespace(connect=connection.connect))
        )
        frames = asyncio.Queue()

        await voice.consume(
            frames,
            AsyncMock(),
            AsyncMock(),
            lambda: False,
            user_id="user-language-test",
        )

        instruction = connection.config.system_instruction
        self.assertIn("explicitly asks you to answer or speak", instruction)
        self.assertIn("regardless of the input language", instruction)
        self.assertIn("If no response language is explicitly requested", instruction)


if __name__ == "__main__":
    unittest.main()
