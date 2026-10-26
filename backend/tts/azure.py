"""Azure Speech text-to-speech adapter with incremental PCM output."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import azure.cognitiveservices.speech as speechsdk


class AzureSpeechSynthesizer:
    """Synthesize text and forward SDK audio callbacks as async PCM chunks."""

    def __init__(
        self,
        key: str,
        region: str,
        *,
        voice: str = "en-US-JennyNeural",
    ) -> None:
        self._key = key
        self._region = region
        self._voice = voice

    def stream_audio(
        self, text_chunks: AsyncIterable[str], *, language: str | None = None
    ) -> AsyncIterator[bytes]:
        return self._stream_audio(text_chunks, language=language)

    async def _stream_audio(
        self, text_chunks: AsyncIterable[str], *, language: str | None = None
    ) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        endpoint = f"wss://{self._region}.tts.speech.microsoft.com/cognitiveservices/websocket/v2"
        config = speechsdk.SpeechConfig(endpoint=endpoint, subscription=self._key)
        config.speech_synthesis_voice_name = _voice_for_language(language, self._voice)
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
        )
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        request = speechsdk.SpeechSynthesisRequest(
            input_type=speechsdk.SpeechSynthesisRequestInputType.TextStream
        )

        def on_audio(event: Any) -> None:
            audio = bytes(event.result.audio_data)
            if audio:
                loop.call_soon_threadsafe(chunks.put_nowait, audio)

        synthesizer.synthesizing.connect(on_audio)
        future = synthesizer.speak_async(request)

        async def feed_text() -> None:
            try:
                async for text in text_chunks:
                    if text:
                        await asyncio.to_thread(request.input_stream.write, text)
            finally:
                await asyncio.to_thread(request.input_stream.close)

        input_task = asyncio.create_task(feed_text())

        async def wait_for_completion() -> None:
            try:
                result = await asyncio.to_thread(future.get)
                if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
                    raise RuntimeError("Azure Speech did not complete the synthesis")
            finally:
                loop.call_soon_threadsafe(chunks.put_nowait, None)

        completion = asyncio.create_task(wait_for_completion())
        try:
            while True:
                chunk = await chunks.get()
                if chunk is None:
                    break
                yield chunk
            await asyncio.shield(completion)
            await input_task
        except asyncio.CancelledError:
            await asyncio.to_thread(synthesizer.stop_speaking_async().get)
            input_task.cancel()
            await asyncio.gather(input_task, return_exceptions=True)
            await asyncio.gather(completion, return_exceptions=True)
            raise
        finally:
            if not input_task.done():
                input_task.cancel()
                await asyncio.gather(input_task, return_exceptions=True)

    async def aclose(self) -> None:
        """The adapter owns no persistent SDK objects between synthesis calls."""


def _voice_for_language(language: str | None, fallback: str) -> str:
    """Choose a regional Azure neural voice when ASR provides a language tag."""
    if not language:
        return fallback
    normalized = language.replace("_", "-").lower()
    if normalized in {"en-in", "en"}:
        return "en-IN-NeerjaNeural"
    if normalized in {"te", "te-in"}:
        return "te-IN-ShrutiNeural"
    return fallback
