# Project Context

## Current state

The repository has an executable FastAPI backend and Next.js browser client. PostgreSQL owns account records and login; MongoDB owns conversation documents and LangGraph checkpoints. Existing MongoDB account IDs and Argon2 hashes are copied once at startup when both databases are configured, preserving ownership IDs and leaving source MongoDB records untouched. The local PostgreSQL `voice_bot` database and account tables are configured and live-verified.

## Product

A browser-based conversational voice assistant with authenticated users, streaming transcription and assistant responses, natural speech output, interruption support, conversation history/resumption, and long-term user memory. Tool use through LangGraph is a future extension.

## Architecture

One application, split into focused backend modules. Provider SDKs stay inside their respective adapters (`stt/`, `tts/`, `llm/`, `memory/`, and `database/`). API routes stay thin. Do not add microservices or a generic `services/` layer. PostgreSQL owns accounts; MongoDB owns conversation records and LangGraph checkpoint state; Mem0 owns semantic memory.

## Backend areas

- `core/`: settings, security, logging, errors, dependencies, constants
- `routes/` and `websocket/`: HTTP and authenticated real-time entry points
- `models/`: validated API and domain transport models
- `voice/`: session lifecycle, audio flow, interruption, turn detection, streaming
- `stt/`, `tts/`, `llm/`: replaceable provider adapters
- `agent/`: LangGraph orchestration and nodes
- `memory/`: checkpoint and semantic/conversation memory integration
- `database/`: PostgreSQL account repository and MongoDB conversation/checkpoint connections
- `tests/`: unit, integration, and WebSocket coverage

## Security constraints

Do not expose provider keys to the browser, trust a client-supplied user ID, allow cross-user conversation access, log secrets, or return stack traces/provider credentials. Authenticate WebSocket connections and authorize each conversation. Validate external input.

## Current phase

PostgreSQL-backed registration, login, authenticated profile retrieval, and schema creation passed against the configured local database. MongoDB remains configured separately for conversation documents and checkpoints. Gemini uses 3.1 Flash-Lite with a 3.5 Flash-Lite fallback before any output is streamed; the fallback was verified with the configured key. The chat-style UI now supports text turns and an optional continuous voice mode, with automatic speech barge-in handling. The local FastAPI dev server currently needs a restart before browser voice behavior can be rechecked.

Voice architecture selection is implemented: the cascade uses Deepgram, Gemini text, and Azure TTS; Gemini Live (`gemini-3.8-live`) handles native audio for the selected session. Both paths restore the selected conversation's MongoDB history. Cascade retrieves up to five user-scoped Mem0 memories for each turn; Live uses a synchronous function call to retrieve relevant memories and saves completed exchanges to Mem0. Switching architectures reloads the saved transcript into either model path, and an active voice session reconnects automatically on a switch. Live provider validation is pending.
