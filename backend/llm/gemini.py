"""Google Gemini adapter using the official asynchronous Gen AI SDK."""

import logging
from collections.abc import AsyncIterator, Sequence

from google import genai
from google.genai import types

from backend.llm.language import LANGUAGE_RESPONSE_POLICY
from backend.llm.protocol import ChatMessage

logger = logging.getLogger(__name__)


class GeminiChatModel:
    """Stream text replies from Gemini while keeping the SDK behind an adapter."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.1-flash-lite",
        fallback_model: str | None = "gemini-3.5-flash-lite",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._async_client = self._client.aio
        self._model = model
        self._fallback_model = fallback_model if fallback_model != model else None

    def stream_reply(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        return self._stream_reply(messages)

    async def _stream_reply(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        system_context = [message.content for message in messages if message.role == "system"]
        contents = [
            types.Content(
                role="user" if message.role == "user" else "model",
                parts=[types.Part.from_text(text=message.content)],
            )
            for message in messages
            if message.role != "system"
        ]
        config = types.GenerateContentConfig(
            system_instruction=(
                "You are a helpful multilingual voice assistant. "
                + LANGUAGE_RESPONSE_POLICY
                + " Never translate the user's message unless they ask you to. "
                "Keep replies clear, warm, and concise so they are easy to follow when spoken aloud. "
                "Use flowing conversational phrasing; avoid unnecessary full stops, ellipses, and "
                "sentence fragments that create frequent pauses in spoken replies. "
                + " ".join(system_context)
            ),
            max_output_tokens=256,
        )
        emitted_text = False
        primary_error: Exception | None = None
        try:
            async for text in self._stream_model(self._model, contents, config):
                emitted_text = True
                yield text
        except Exception as error:
            # Once text has reached the client, replaying a fallback would duplicate speech.
            # Switch models only when the primary failed before producing its first token.
            if emitted_text:
                raise
            primary_error = error

        if emitted_text:
            return
        if self._fallback_model is None:
            if primary_error is not None:
                raise primary_error
            return
        logger.warning(
            "Primary Gemini model failed before streaming (%s); trying fallback",
            type(primary_error).__name__ if primary_error is not None else "empty_response",
            extra={"primary_model": self._model, "fallback_model": self._fallback_model},
        )
        async for text in self._stream_model(self._fallback_model, contents, config):
            yield text

    async def _stream_model(
        self,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> AsyncIterator[str]:
        response_stream = await self._async_client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        )
        async for chunk in response_stream:
            try:
                text = chunk.text
            except (AttributeError, ValueError):
                continue
            if isinstance(text, str) and text:
                yield text

    async def aclose(self) -> None:
        await self._async_client.aclose()
        self._client.close()
