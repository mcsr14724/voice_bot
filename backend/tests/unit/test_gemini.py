"""Tests for Gemini primary/fallback streaming behavior."""

from types import SimpleNamespace
import unittest

from backend.llm.gemini import GeminiChatModel
from backend.llm.protocol import ChatMessage


class FakeModels:
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[str] = []
        self.configs = []

    async def generate_content_stream(self, *, model, contents, config):
        self.calls.append(model)
        self.configs.append(config)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAsyncClient:
    def __init__(self, results):
        self.models = FakeModels(results)


def chunks(*texts):
    async def stream():
        for text in texts:
            if isinstance(text, Exception):
                raise text
            yield SimpleNamespace(text=text)

    return stream()


def make_model(results):
    model = GeminiChatModel.__new__(GeminiChatModel)
    model._async_client = FakeAsyncClient(results)
    model._model = "gemini-3.1-flash-lite"
    model._fallback_model = "gemini-3.5-flash-lite"
    return model


class GeminiFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_language_policy_prefers_requested_language_then_latest_input_language(self) -> None:
        model = make_model([chunks("नमस्ते")])

        output = [
            part
            async for part in model.stream_reply(
                [ChatMessage(role="user", content="Please answer in Hindi: say hello.")]
            )
        ]

        instruction = model._async_client.models.configs[0].system_instruction
        self.assertEqual(output, ["नमस्ते"])
        self.assertIn("explicitly asks you to answer or speak", instruction)
        self.assertIn("regardless of the input language", instruction)
        self.assertIn("If no response language is explicitly requested", instruction)

    async def test_primary_error_before_first_token_uses_fallback(self) -> None:
        model = make_model([RuntimeError("primary unavailable"), chunks("Backup", " reply.")])

        output = [
            part
            async for part in model.stream_reply(
                [ChatMessage(role="user", content="Say hello.")]
            )
        ]

        self.assertEqual(output, ["Backup", " reply."])
        self.assertEqual(
            model._async_client.models.calls,
            ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
        )

    async def test_successful_primary_does_not_call_fallback(self) -> None:
        model = make_model([chunks("Primary reply.")])

        output = [
            part
            async for part in model.stream_reply(
                [ChatMessage(role="user", content="Say hello.")]
            )
        ]

        self.assertEqual(output, ["Primary reply."])
        self.assertEqual(model._async_client.models.calls, ["gemini-3.1-flash-lite"])

    async def test_empty_primary_response_uses_fallback(self) -> None:
        model = make_model([chunks(), chunks("Fallback reply.")])

        output = [
            part
            async for part in model.stream_reply(
                [ChatMessage(role="user", content="Say hello.")]
            )
        ]

        self.assertEqual(output, ["Fallback reply."])
        self.assertEqual(
            model._async_client.models.calls,
            ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
        )

    async def test_primary_stream_error_after_text_does_not_replay_response(self) -> None:
        model = make_model([chunks("Partial", RuntimeError("stream interrupted"))])

        output = []
        with self.assertRaisesRegex(RuntimeError, "stream interrupted"):
            async for part in model.stream_reply(
                [ChatMessage(role="user", content="Say hello.")]
            ):
                output.append(part)

        self.assertEqual(output, ["Partial"])
        self.assertEqual(model._async_client.models.calls, ["gemini-3.1-flash-lite"])


if __name__ == "__main__":
    unittest.main()
