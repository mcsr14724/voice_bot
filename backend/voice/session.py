"""Per-connection voice-session lifecycle and in-process session registry."""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Coroutine
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from backend.models.websocket import (
    ClientAudioStart,
    ClientAudioStop,
    ClientClose,
    ClientInterrupt,
    ClientMessage,
    ClientPing,
    ClientTextMessage,
)
from backend.core.config import get_settings
from backend.llm.protocol import ChatMessage
from backend.memory.protocol import LongTermMemory
from backend.voice.audio import AudioFormat, AudioFrame, AudioStreamEnd
from backend.voice.interruption import InterruptionController
from backend.voice.turn_detection import EnergyTurnDetector
from backend.voice.transcription import DeepgramTranscriber
from backend.agent.conversation import ConversationOrchestrator
from backend.tts.protocol import SpeechSynthesizer
from backend.database.repositories.conversation import ConversationRepository
from backend.voice.gemini_live import GeminiLiveVoice

logger = logging.getLogger(__name__)
message_adapter = TypeAdapter(ClientMessage)
MAX_CONTROL_MESSAGE_BYTES = 4_096
# Buffer five seconds of 20 ms microphone frames while Deepgram's socket starts.
MAX_QUEUED_AUDIO_FRAMES = 256
_BACKCHANNELS = {
    "hmm", "hm", "mm", "mmm", "mhm", "uh", "um", "uh huh", "uh huh uh",
    "uh huh", "mm hm", "mm hmm", "hmm hmm", "mmhm", "uhuh",
}


class SessionStatus(StrEnum):
    CONNECTING = "connecting"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(slots=True)
class VoiceSession:
    """Own the protocol and cleanup for one authenticated WebSocket connection."""

    user_id: str
    websocket: WebSocket
    session_id: str = field(default_factory=lambda: str(uuid4()))
    connection_id: str = field(default_factory=lambda: str(uuid4()))
    status: SessionStatus = SessionStatus.CONNECTING
    _close_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    interruption: InterruptionController = field(default_factory=InterruptionController, init=False)
    audio_frames: asyncio.Queue[AudioFrame | AudioStreamEnd | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=MAX_QUEUED_AUDIO_FRAMES), init=False, repr=False
    )
    _audio_format: AudioFormat | None = field(default=None, init=False, repr=False)
    _audio_sequence: int = field(default=0, init=False, repr=False)
    _audio_finished: bool = field(default=False, init=False, repr=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _audio_consumer: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    conversation: ConversationOrchestrator | None = field(default=None, repr=False)
    speech_synthesizer: SpeechSynthesizer | None = field(default=None, repr=False)
    conversation_repository: ConversationRepository | None = field(default=None, repr=False)
    long_term_memory: LongTermMemory | None = field(default=None, repr=False)
    architecture: str = "cascade"
    gemini_live_voice: GeminiLiveVoice | None = field(default=None, repr=False)
    initial_history: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _pending_transcript_segments: list[str] = field(default_factory=list, init=False, repr=False)
    _pending_transcript_language: str | None = field(default=None, init=False, repr=False)

    async def run(self, *, accept_websocket: bool = True) -> None:
        try:
            if accept_websocket:
                await self.websocket.accept()
            self.status = SessionStatus.ACTIVE
            await self._send_json({"type": "session.ready", "session_id": self.session_id})
            logger.info("Voice session opened", extra={"session_id": self.session_id})
            while self.status is SessionStatus.ACTIVE:
                event = await self.websocket.receive()
                if event["type"] == "websocket.disconnect":
                    return
                binary_message = event.get("bytes")
                if binary_message is not None:
                    if self._audio_format is None:
                        await self.close(code=1002, reason="Send audio.start before audio frames")
                        return
                    if len(binary_message) != self._audio_format.frame_size_bytes:
                        await self.close(code=1003, reason="Audio frame does not match the negotiated format")
                        return
                    frame = AudioFrame(
                        sequence=self._audio_sequence,
                        pcm_s16le=binary_message,
                        frame_duration_ms=self._audio_format.frame_duration_ms,
                    )
                    try:
                        self.audio_frames.put_nowait(frame)
                    except asyncio.QueueFull:
                        await self._send_json({"type": "error", "code": "audio_backpressure"})
                        await self.close(code=1013, reason="Audio consumer is behind")
                        return
                    if self._audio_sequence == 0:
                        await self._send_json({"type": "audio.receiving", "frames_received": 1})
                    self._audio_sequence += 1
                    continue
                raw_message = event.get("text")
                if raw_message is None:
                    await self.close(code=1003, reason="Text control messages are required")
                    return
                if len(raw_message.encode("utf-8")) > MAX_CONTROL_MESSAGE_BYTES:
                    await self.close(code=1009, reason="Message is too large")
                    return
                try:
                    message: ClientMessage = message_adapter.validate_json(raw_message)
                except ValidationError:
                    await self._send_json({"type": "error", "code": "invalid_message"})
                    continue

                if isinstance(message, ClientClose):
                    await self.close()
                    return
                if isinstance(message, ClientInterrupt):
                    await self.interrupt_responses(notify_reason="user_request")
                    continue
                if isinstance(message, ClientTextMessage):
                    text = message.text.strip()
                    if text:
                        await self._start_user_turn(text, speak_response=message.voice_response)
                    continue
                if isinstance(message, ClientAudioStart):
                    if self._audio_format is not None:
                        await self._send_json({"type": "error", "code": "audio_already_started"})
                        continue
                    self._audio_format = AudioFormat(
                        sample_rate=message.sample_rate,
                        channels=message.channels,
                        frame_duration_ms=message.frame_duration_ms,
                    )
                    if self._audio_consumer is None or self._audio_consumer.done():
                        if self._audio_consumer is not None:
                            await asyncio.gather(self._audio_consumer, return_exceptions=True)
                            while not self.audio_frames.empty():
                                try:
                                    self.audio_frames.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                        self._audio_consumer = asyncio.create_task(self._consume_audio())
                    await self._send_json(
                        {
                            "type": "audio.ready",
                            "encoding": message.encoding,
                            "sample_rate": message.sample_rate,
                            "channels": message.channels,
                            "frame_duration_ms": message.frame_duration_ms,
                        }
                    )
                    if self.architecture == "gemini_live" and self.gemini_live_voice is None:
                        await self._send_json({"type": "assistant.error", "code": "provider_not_configured"})
                    elif self.architecture == "cascade" and get_settings().deepgram_api_key is None:
                        await self._send_json(
                            {"type": "transcription.unavailable", "code": "provider_not_configured"}
                        )
                    continue
                if isinstance(message, ClientAudioStop):
                    if self._audio_format is None:
                        await self._send_json({"type": "error", "code": "audio_not_started"})
                        continue
                    self._audio_format = None
                    try:
                        self.audio_frames.put_nowait(AudioStreamEnd())
                    except asyncio.QueueFull:
                        await self.close(code=1013, reason="Audio consumer is behind")
                        return
                    await self._send_json({"type": "audio.ended"})
                    continue
                if isinstance(message, ClientPing):
                    await self._send_json({"type": "pong", "request_id": message.request_id})
        except WebSocketDisconnect:
            pass
        finally:
            if self.status is not SessionStatus.CLOSED:
                self.status = SessionStatus.CLOSING
            await self.interrupt_responses()
            self._discard_audio_and_signal_end()
            if self._audio_consumer is not None:
                result = await asyncio.gather(self._audio_consumer, return_exceptions=True)
                if isinstance(result[0], Exception):
                    logger.warning(
                        "Voice audio consumer stopped unexpectedly",
                        extra={"session_id": self.session_id, "error_type": type(result[0]).__name__},
                    )
            self.status = SessionStatus.CLOSED
            logger.info("Voice session closed", extra={"session_id": self.session_id})

    async def close(self, *, code: int = 1000, reason: str | None = None) -> None:
        async with self._close_lock:
            if self.status in {SessionStatus.CLOSING, SessionStatus.CLOSED}:
                return
            self.status = SessionStatus.CLOSING
            try:
                self._discard_audio_and_signal_end()
                await self.interrupt_responses()
                await self.websocket.close(code=code, reason=reason)
            finally:
                self.status = SessionStatus.CLOSED

    def _discard_audio_and_signal_end(self) -> None:
        if self._audio_finished:
            return
        while not self.audio_frames.empty():
            try:
                self.audio_frames.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.audio_frames.put_nowait(None)
        self._audio_finished = True

    async def _send_json(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(message)

    async def _consume_audio(self) -> None:
        if self.architecture == "gemini_live":
            if self.gemini_live_voice is None:
                return
            try:
                await self.gemini_live_voice.consume(
                    self.audio_frames,
                    self._handle_live_event,
                    self._send_bytes,
                    lambda: self.status is SessionStatus.ACTIVE,
                    history=self.initial_history,
                    user_id=self.user_id,
                    memory=self.long_term_memory,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Gemini Live session is unavailable",
                    extra={"session_id": self.session_id, "error_type": type(error).__name__},
                )
                if self.status is SessionStatus.ACTIVE:
                    await self._send_json({"type": "assistant.error", "code": "provider_unavailable"})
            return
        api_key = get_settings().deepgram_api_key
        if api_key is not None:
            retry_delay = 0.5
            while self.status is SessionStatus.ACTIVE:
                transcriber = DeepgramTranscriber(
                    api_key.get_secret_value(),
                    language=get_settings().deepgram_language,
                    language_hints=get_settings().deepgram_language_hints,
                )
                try:
                    await transcriber.consume(self.audio_frames, self._handle_transcription_event)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    error_code = DeepgramTranscriber.connection_error_code(error)
                    extra: dict[str, Any] = {
                        "session_id": self.session_id,
                        "error_type": type(error).__name__,
                    }
                    logger.warning(
                        "Deepgram transcription is unavailable",
                        extra=extra,
                    )
                    if self.status is not SessionStatus.ACTIVE:
                        return
                    await self._send_json(
                        {"type": "transcription.error", "code": error_code}
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 8.0)
            return
        await self._consume_local_audio()

    async def _handle_live_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "live.turn_saved":
            user_text = str(event.get("user_text", ""))
            assistant_text = str(event.get("assistant_text", ""))
            if self.conversation_repository is not None:
                await self.conversation_repository.record_turn(
                    conversation_id=self.session_id,
                    user_id=self.user_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                )
            if (
                self.long_term_memory is not None
                and user_text
                and assistant_text
                and not event.get("interrupted")
            ):
                try:
                    await self.long_term_memory.add(
                        user_id=self.user_id,
                        messages=[
                            ChatMessage(role="user", content=user_text),
                            ChatMessage(role="assistant", content=assistant_text),
                        ],
                    )
                except Exception as error:
                    logger.warning(
                        "Gemini Live memory update failed",
                        extra={"session_id": self.session_id, "error_type": type(error).__name__},
                    )
            self._append_history_turn(user_text, assistant_text)
            await self._send_json({"type": "conversation.turn_saved", "session_id": self.session_id})
            return
        await self._send_json(event)

    def _append_history_turn(self, user_text: str, assistant_text: str) -> None:
        if user_text.strip():
            self.initial_history.append({"role": "user", "content": user_text.strip()})
        if assistant_text.strip():
            self.initial_history.append({"role": "assistant", "content": assistant_text.strip()})
        del self.initial_history[:-200]

    async def _handle_transcription_event(self, event: dict[str, Any]) -> None:
        """Forward provider events and checkpoint completed speech as conversation turns."""
        transcript_text = event.get("text")
        confidence = event.get("confidence")
        is_recognized_speech = (
            event.get("type") == "transcript"
            and isinstance(transcript_text, str)
            and bool(transcript_text.strip())
            # Interim ASR confidence can be modest even for clear speech,
            # especially with room noise or non-English audio. The transcript
            # itself is the speech confirmation; use confidence only to reject
            # very weak hypotheses.
            and (not isinstance(confidence, (int, float)) or confidence >= 0.2)
        )
        is_backchannel = (
            event.get("type") == "transcript"
            and isinstance(transcript_text, str)
            and self._is_backchannel(transcript_text)
        )
        if is_recognized_speech and not is_backchannel and self.interruption.has_active_tasks:
            # SpeechStarted only pauses browser playback provisionally. Cancel
            # generation once STT confirms speech that is more than a short acknowledgment.
            await self.interrupt_responses(notify_reason="user_speech")
        await self._send_json(event)
        if event.get("type") == "transcript":
            text = event.get("text")
            # Deepgram can mark the utterance boundary independently from the
            # segment-final flag. Keep the boundary transcript so the user's
            # last words are not dropped when they barge in.
            if isinstance(text, str) and text.strip() and (
                event.get("is_final") or event.get("speech_final")
            ):
                segment = text.strip()
                if not self._pending_transcript_segments or self._pending_transcript_segments[-1] != segment:
                    self._pending_transcript_segments.append(segment)
                languages = event.get("languages")
                if isinstance(languages, list) and languages:
                    selected_language = next(
                        (item for item in reversed(languages) if isinstance(item, str)), None
                    )
                    if selected_language:
                        self._pending_transcript_language = selected_language
            if event.get("speech_final"):
                await self._save_pending_transcript()
        elif event.get("type") == "speech.ended":
            await self._save_pending_transcript()

    async def _save_pending_transcript(self) -> None:
        if not self._pending_transcript_segments:
            return
        text = " ".join(self._pending_transcript_segments)
        language = self._pending_transcript_language
        self._pending_transcript_segments.clear()
        self._pending_transcript_language = None
        if self._is_backchannel(text):
            await self._send_json({"type": "speech.backchannel"})
            return
        if self.conversation is None:
            await self._send_json({"type": "conversation.error", "code": "orchestrator_unavailable"})
            return
        await self._start_user_turn(text, speak_response=True, language=language)

    @staticmethod
    def _is_backchannel(text: str) -> bool:
        """Ignore short listener acknowledgments while Vox is speaking."""
        normalized = " ".join(
            "".join(character.lower() if character.isalnum() else " " for character in text).split()
        )
        return normalized in _BACKCHANNELS

    async def _start_user_turn(
        self, text: str, *, speak_response: bool, language: str | None = None
    ) -> None:
        """Run one completed user turn, optionally speaking the assistant reply."""
        if self.conversation is None:
            await self._send_json({"type": "conversation.error", "code": "orchestrator_unavailable"})
            return
        # The server may have finished synthesizing while the browser still has
        # several seconds of PCM queued. Always tell the client to flush playback,
        # even when no backend response task remains.
        await self.interrupt_responses(notify_reason="new_user_turn")
        generation_ref: dict[str, int] = {}

        async def run_turn() -> None:
            await self._run_assistant_turn(
                text,
                generation_ref["generation"],
                speak_response=speak_response,
                language=language,
            )

        generation, _ = self.start_response_task(run_turn())
        generation_ref["generation"] = generation

    async def _run_assistant_turn(
        self,
        text: str,
        generation: int,
        *,
        speak_response: bool = True,
        language: str | None = None,
    ) -> None:
        """Stream a graph turn to the client while ignoring stale response output."""
        conversation = self.conversation
        if conversation is None:
            if self.is_response_current(generation):
                await self._send_json({"type": "conversation.error", "code": "orchestrator_unavailable"})
            return
        text_queue: asyncio.Queue[str | None] | None = None
        speech_task: asyncio.Task[None] | None = None
        try:
            assistant_text: list[str] = []
            if speak_response and self.speech_synthesizer is not None and conversation.model_configured:
                text_queue = asyncio.Queue()
                await self._send_json(
                    {
                        "type": "assistant.audio.started",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24_000,
                        "channels": 1,
                        "generation": generation,
                    }
                )
                speech_task = asyncio.create_task(
                    self._stream_speech_audio(text_queue, generation, language=language)
                )
            async for event in conversation.stream_user_turn(
                thread_id=self.session_id,
                user_id=self.user_id,
                text=text,
                language=language,
                history=self.initial_history,
            ):
                if not self.is_response_current(generation):
                    return
                if event.get("type") == "assistant.delta":
                    fragment = event.get("text")
                    if isinstance(fragment, str):
                        assistant_text.append(fragment)
                        if text_queue is not None:
                            await text_queue.put(fragment)
                    await self._send_json(event)
                    continue
                if event.get("type") == "assistant.completed":
                    if text_queue is not None and speech_task is not None:
                        await text_queue.put(None)
                        await speech_task
                        speech_task = None
                        text_queue = None
                    elif speak_response and self.speech_synthesizer is None:
                        await self._send_json(
                            {"type": "assistant.audio.unavailable", "code": "provider_not_configured"}
                        )
                if event.get("type") == "conversation.turn_saved":
                    if self.conversation_repository is not None:
                        await self.conversation_repository.record_turn(
                            conversation_id=self.session_id,
                            user_id=self.user_id,
                            user_text=text,
                            assistant_text="".join(assistant_text).strip(),
                        )
                    self._append_history_turn(text, "".join(assistant_text))
                await self._send_json(event)
        except asyncio.CancelledError:
            if speech_task is not None:
                speech_task.cancel()
                await asyncio.gather(speech_task, return_exceptions=True)
            raise
        except Exception as error:
            logger.warning(
                "Assistant response failed",
                extra={"session_id": self.session_id, "error_type": type(error).__name__},
            )
            if self.is_response_current(generation):
                await self._send_json({"type": "assistant.error", "code": "provider_unavailable"})
        finally:
            if speech_task is not None:
                if text_queue is not None:
                    await text_queue.put(None)
                await asyncio.gather(speech_task, return_exceptions=True)
    async def _stream_speech_audio(
        self,
        text_queue: asyncio.Queue[str | None],
        generation: int,
        *,
        language: str | None = None,
    ) -> None:
        """Synthesize model deltas as a live text stream and forward PCM immediately."""
        synthesizer = self.speech_synthesizer
        if synthesizer is None or not self.is_response_current(generation):
            return

        async def text_chunks() -> Any:
            while True:
                item = await text_queue.get()
                if item is None:
                    return
                yield item

        try:
            async for chunk in synthesizer.stream_audio(text_chunks(), language=language):
                if not await self._send_response_audio_bytes(chunk, generation):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Speech synthesis failed",
                extra={"session_id": self.session_id, "error_type": type(error).__name__},
            )
            if self.is_response_current(generation):
                await self._send_json(
                    {
                        "type": "assistant.audio.error",
                        "code": "provider_unavailable",
                        "generation": generation,
                    }
                )
            return
        if self.is_response_current(generation):
            await self._send_json({"type": "assistant.audio.ended", "generation": generation})

    async def _send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self.websocket.send_bytes(data)

    async def _send_response_audio_bytes(self, data: bytes, generation: int) -> bool:
        """Check freshness while holding the send lock to exclude late canceled chunks."""
        async with self._send_lock:
            if not self.is_response_current(generation):
                return False
            # Tag PCM so the browser can reject delayed frames from a reply that
            # was interrupted before a newer reply started.
            frame = (generation & 0xFFFFFFFF).to_bytes(4, "big") + data
            await self.websocket.send_bytes(frame)
            return True

    async def _consume_local_audio(self) -> None:
        detector = EnergyTurnDetector()
        while True:
            if self._audio_finished and self.audio_frames.empty():
                return
            try:
                item = await asyncio.wait_for(self.audio_frames.get(), timeout=0.5)
            except TimeoutError:
                continue
            if item is None:
                if self._audio_finished:
                    return
                continue
            if isinstance(item, AudioStreamEnd):
                for event in detector.end_stream(max(0, self._audio_sequence - 1)):
                    if self.status is SessionStatus.ACTIVE:
                        await self._send_json({"type": event.type, "sequence": event.sequence})
                continue
            events = detector.process(item)
            if self.status is SessionStatus.ACTIVE:
                for event in events:
                    await self._send_json({"type": event.type, "sequence": event.sequence})

    def start_response_task(self, coroutine: Coroutine[Any, Any, Any]) -> tuple[int, asyncio.Task[Any]]:
        """Start and track cancellable response work for future agent/TTS nodes."""
        if self.status is not SessionStatus.ACTIVE:
            coroutine.close()
            raise RuntimeError("Response work requires an active voice session")
        return self.interruption.start(coroutine)

    def is_response_current(self, generation: int) -> bool:
        """Prevent an interrupted response from emitting stale output."""
        return self.status is SessionStatus.ACTIVE and self.interruption.is_current(generation)

    async def interrupt_responses(self, *, notify_reason: str | None = None) -> int:
        """Invalidate responses, notify playback promptly, then await cleanup."""

        async def notify(generation: int) -> None:
            message: dict[str, Any] = {
                "type": "response.interrupted",
                "generation": generation,
            }
            if notify_reason is not None:
                message["reason"] = notify_reason
            await self._send_json(message)

        return await self.interruption.interrupt(on_started=notify if notify_reason else None)


class VoiceSessionManager:
    """Track active sessions and close them during application shutdown."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: VoiceSession) -> None:
        async with self._lock:
            if session.connection_id in self._sessions:
                raise ValueError("A voice connection with this identifier is already registered")
            self._sessions[session.connection_id] = session

    async def unregister(self, connection_id: str) -> None:
        async with self._lock:
            self._sessions.pop(connection_id, None)

    async def active_count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def shutdown(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
        await asyncio.gather(
            *(session.close(code=1012, reason="Server shutting down") for session in sessions),
            return_exceptions=True,
        )
        async with self._lock:
            self._sessions.clear()
