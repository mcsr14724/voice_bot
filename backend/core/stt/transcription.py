"""Deepgram v1 WebSocket bridge for session PCM frames."""

import asyncio
import json
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from backend.core.voice.audio import AudioFrame, AudioStreamEnd

TranscriptEmitter = Callable[[dict[str, Any]], Awaitable[None]]
AudioQueue = asyncio.Queue[AudioFrame | AudioStreamEnd | None]


class DeepgramTranscriber:
    """Forward PCM to Deepgram and normalize result events for the browser."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "multi",
        language_hints: Sequence[str] = (),
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._language = language
        self._language_hints = tuple(hint.strip() for hint in language_hints if hint.strip())

    @staticmethod
    def connection_error_code(error: Exception) -> str:
        """Classify connection failures without exposing provider response details."""
        if isinstance(error, InvalidStatus):
            status_code = error.response.status_code
            if status_code in {401, 403}:
                return "credentials_rejected"
            if status_code == 429:
                return "rate_limited"
            if 400 <= status_code < 500:
                return "request_rejected"
            if status_code >= 500:
                return "provider_unavailable"
        if isinstance(error, TimeoutError):
            return "connection_timeout"
        if isinstance(error, OSError):
            return "connection_failed"
        return "provider_unavailable"

    async def consume(self, frames: AudioQueue, emit: TranscriptEmitter) -> None:
        query_parameters = [
            ("model", self._model),
            ("language", self._language),
            ("encoding", "linear16"),
            ("sample_rate", "16000"),
            ("channels", "1"),
            ("interim_results", "true"),
            ("vad_events", "true"),
            ("smart_format", "true"),
            ("endpointing", "300"),
            ("utterance_end_ms", "1000"),
        ]
        query_parameters.extend(("language_hint", hint) for hint in self._language_hints)
        query = urlencode(query_parameters)
        async with connect(
            f"wss://api.deepgram.com/v1/listen?{query}",
            additional_headers={"Authorization": f"Token {self._api_key}"},
            open_timeout=10,
            max_size=1_048_576,
        ) as socket:
            await emit({"type": "transcription.connected"})
            sender = asyncio.create_task(
                self._send_frames(
                    socket,
                    frames,
                    on_first_audio=lambda: emit({"type": "transcription.audio_forwarded"}),
                )
            )
            receiver = asyncio.create_task(self._receive_results(socket, emit))
            try:
                done, _ = await asyncio.wait(
                    {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                if receiver in done:
                    await receiver
                    if not sender.done():
                        sender.cancel()
                        raise ConnectionError("Deepgram closed before the audio stream ended")
                else:
                    await sender
                    try:
                        await asyncio.wait_for(receiver, timeout=3)
                    except TimeoutError:
                        receiver.cancel()
            finally:
                for task in (sender, receiver):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(sender, receiver, return_exceptions=True)

    @staticmethod
    async def _send_frames(
        socket: Any,
        frames: AudioQueue,
        *,
        on_first_audio: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        while True:
            frame = await frames.get()
            if frame is None:
                await socket.send(json.dumps({"type": "CloseStream"}))
                return
            if isinstance(frame, AudioStreamEnd):
                await socket.send(json.dumps({"type": "Finalize"}))
                continue
            await socket.send(frame.pcm_s16le)
            if on_first_audio is not None:
                await on_first_audio()
                on_first_audio = None

    @classmethod
    async def _receive_results(cls, socket: Any, emit: TranscriptEmitter) -> None:
        async for raw_message in socket:
            if not isinstance(raw_message, str):
                continue
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                continue
            event = cls.normalize_message(message)
            if event is not None:
                await emit(event)

    @staticmethod
    def normalize_message(message: dict[str, Any]) -> dict[str, Any] | None:
        """Convert selected provider messages without exposing raw payloads."""
        message_type = message.get("type")
        if message_type == "Results":
            channel = message.get("channel") or {}
            alternatives = channel.get("alternatives") or []
            if not alternatives:
                return None
            transcript = alternatives[0].get("transcript", "")
            if not isinstance(transcript, str) or not transcript.strip():
                return None
            event = {
                "type": "transcript",
                "text": transcript[:8_000],
                "is_final": bool(message.get("is_final")),
                "speech_final": bool(message.get("speech_final")),
            }
            confidence = alternatives[0].get("confidence")
            if isinstance(confidence, (int, float)):
                event["confidence"] = float(confidence)
            languages = alternatives[0].get("languages")
            if not languages:
                words = alternatives[0].get("words") or []
                languages = list(dict.fromkeys(
                    word.get("language")
                    for word in words
                    if isinstance(word, dict) and isinstance(word.get("language"), str)
                ))
            if not languages and isinstance(message.get("language"), str):
                languages = [message["language"]]
            if isinstance(languages, list) and languages:
                event["languages"] = languages
            return event
        if message_type == "SpeechStarted":
            return {"type": "speech.started"}
        if message_type == "UtteranceEnd":
            return {"type": "speech.ended"}
        if message_type == "Error":
            return {"type": "transcription.error", "code": "provider_error"}
        return None
