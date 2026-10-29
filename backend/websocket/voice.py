"""Authenticated WebSocket session bootstrap and control-message handling."""

import asyncio
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from backend.core.config import get_settings
from backend.core.security import decode_access_token
from backend.database.repositories.user import UserRepository
from backend.models.websocket import ClientAuthMessage
from backend.voice.session import VoiceSession, VoiceSessionManager


async def voice_websocket(websocket: WebSocket) -> None:
    """Authenticate, establish a session, then handle minimal control messages."""
    settings = get_settings()
    origin = websocket.headers.get("origin")
    if origin not in settings.frontend_origins:
        await websocket.close(code=1008, reason="Origin is not allowed")
        return

    await websocket.accept()
    user_repository: UserRepository | None = getattr(websocket.app.state, "user_repository", None)
    if user_repository is None:
        await websocket.close(code=4401, reason="Authentication required")
        return
    try:
        auth_message = ClientAuthMessage.model_validate(
            await asyncio.wait_for(websocket.receive_json(), timeout=5)
        )
    except (TimeoutError, WebSocketDisconnect, ValidationError, ValueError):
        await websocket.close(code=4401, reason="Authentication required")
        return

    user_id = decode_access_token(auth_message.access_token, settings)
    if user_id is None:
        await websocket.close(code=4401, reason="Authentication required")
        return

    user = await user_repository.find_by_id(user_id)
    if user is None:
        await websocket.close(code=4401, reason="Authentication required")
        return

    requested_conversation_id = websocket.query_params.get("conversation_id")
    architecture = websocket.query_params.get("architecture", "cascade")
    if architecture not in {"cascade", "gemini_live"}:
        await websocket.close(code=1008, reason="Voice architecture is invalid")
        return
    conversation_repository = getattr(websocket.app.state, "conversation_repository", None)
    conversation_record = None
    if requested_conversation_id is not None:
        if conversation_repository is not None:
            conversation_record = await conversation_repository.find_for_user(
                requested_conversation_id, user_id
            )
        if conversation_record is None:
            await websocket.close(code=1008, reason="Conversation is unavailable")
            return

    manager: VoiceSessionManager | None = getattr(websocket.app.state, "voice_session_manager", None)
    if manager is None:
        await websocket.close(code=1011, reason="Voice sessions are unavailable")
        return
    session = VoiceSession(
        user_id=user_id,
        websocket=websocket,
        session_id=requested_conversation_id or str(uuid4()),
        conversation=getattr(websocket.app.state, "conversation_orchestrator", None),
        speech_synthesizer=getattr(websocket.app.state, "speech_synthesizer", None),
        conversation_repository=conversation_repository,
        long_term_memory=getattr(websocket.app.state, "long_term_memory", None),
        architecture=architecture,
        gemini_live_voice=getattr(websocket.app.state, "gemini_live_voice", None),
        initial_history=(conversation_record or {}).get("messages", []),
    )
    await manager.register(session)
    try:
        await session.run(accept_websocket=False)
    finally:
        await manager.unregister(session.session_id)
