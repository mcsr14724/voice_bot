# Project Context

## Current state

The repository was empty at initial inspection. This phase establishes the modular-monolith layout and records the intended architecture. No API, UI, authentication, database connection, voice streaming, or provider integration is implemented yet.

## Product

A browser-based conversational voice assistant with authenticated users, streaming transcription and assistant responses, natural speech output, interruption support, conversation history/resumption, and long-term user memory. Tool use through LangGraph is a future extension.

## Architecture

One application, split into focused backend modules. Provider SDKs stay inside their respective adapters (`stt/`, `tts/`, `llm/`, `memory/`, and `database/`). API routes stay thin. Do not add microservices or a generic `services/` layer. Keep LangGraph checkpoint state separate from Mem0 semantic memory and MongoDB application records.

## Planned backend areas

- `core/`: settings, security, logging, errors, dependencies, constants
- `routes/` and `websocket/`: HTTP and authenticated real-time entry points
- `models/`: validated API and domain transport models
- `voice/`: session lifecycle, audio flow, interruption, turn detection, streaming
- `stt/`, `tts/`, `llm/`: replaceable provider adapters
- `agent/`: LangGraph orchestration and nodes
- `memory/`: checkpoint and semantic/conversation memory integration
- `database/`: MongoDB connection and repositories
- `tests/`: unit, integration, and WebSocket coverage, added alongside implementation

## Security constraints

Do not expose provider keys to the browser, trust a client-supplied user ID, allow cross-user conversation access, log secrets, or return stack traces/provider credentials. Authenticate WebSocket connections and authorize each conversation. Validate external input.

## Current phase

Phase 1: inspect repository, establish structure, and document the plan. The next phase is configuration; the voice pipeline remains unstarted.
