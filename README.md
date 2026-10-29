# Real-Time Voice AI Chatbot

A modular monolith for browser-based, real-time voice conversations with streaming transcription, assistant responses, and speech output.

## Planned stack

- Frontend: Next.js, TypeScript, React, shadcn/ui, browser media APIs, and WebSocket
- Backend: Python, FastAPI, Pydantic, asyncio, and WebSocket
- Speech: Deepgram streaming STT and Azure Speech TTS
- Agent: LangGraph with a provider-neutral LLM boundary (Gemini is the initial provider)
- Persistence: PostgreSQL for user accounts and login; MongoDB for conversation documents and LangGraph checkpoints; Mem0 for long-term semantic memory

## Repository map

- `frontend/`: browser application
- `backend/`: FastAPI modular monolith and technology adapters
- `docs/decisions/`: architecture decision records
- `PROJECT_CONTEXT.md`: current implementation state and architectural constraints
- `IMPLEMENTATION_PLAN.md`: ordered delivery plan
- `DEVELOPMENT_LOG.md`: completed work by phase

## Backend development

Create and activate a virtual environment and install the pinned backend dependencies. Configure the single local `.env` file as needed; it is ignored by Git.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Start the API from the repository root:

```powershell
py -m uvicorn backend.main:app --reload
```

The liveness endpoint is `http://localhost:8000/api/v1/health`; readiness is reported at `/api/v1/ready` and checks PostgreSQL and MongoDB. HTTP responses include a generated `X-Request-ID`, and request logs contain method, path, status, and elapsed time without query strings or request bodies. Run backend unit tests with `py -m unittest discover -s backend\tests\unit -v`.

Start the browser client in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. Set `NEXT_PUBLIC_API_URL` to the backend API base URL, including `/api/v1` (locally: `http://localhost:8000/api/v1`); the client selects `ws://` or `wss://` for its voice socket from that URL. Keep the frontend and API on the same host name locally so the HttpOnly WebSocket authentication cookie is sent.

Set `VOICE_BOT_POSTGRES_DSN` in `.env` to connect the local PostgreSQL account database. The app creates its `users` and migration tables automatically. Set `VOICE_BOT_MONGODB_URI` for MongoDB conversation documents and LangGraph checkpoints. If both are configured, existing MongoDB account IDs and Argon2 password hashes are copied into PostgreSQL once, preserving ownership links; source MongoDB documents are left untouched. MongoDB is optional for login but required for conversation history and durable checkpoints.

Authentication endpoints are `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, and `GET /api/v1/auth/me`. Configure PostgreSQL and a random `VOICE_BOT_SECRET_KEY` (at least 32 characters) before using them. Passwords are stored as Argon2 hashes in PostgreSQL; access tokens expire after 30 minutes. Registration and login use JSON bodies, and `/me` accepts a bearer token.

Production configuration requires HTTPS frontend origins and keeps FastAPI debug mode disabled. The browser app sends `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy` headers.

The authenticated voice socket is at `/api/v1/ws/voice`. After connecting, the browser sends its short-lived access token in the first WebSocket message; this avoids relying on cross-site cookies while keeping the token out of the URL. The socket enforces the exact `VOICE_BOT_FRONTEND_ORIGINS` allowlist, validates the token, and confirms that its user still exists in PostgreSQL. The backend tracks session lifecycle and closes active sockets during application shutdown. The WebSocket supports text messages and continuous voice sessions. Once voice mode is enabled, the microphone stays live; a speech pause completes the turn, and new user speech automatically interrupts assistant playback while transcription continues. Audio input uses mono 16 kHz PCM S16LE binary frames of 20–100 ms, validated against the negotiated size and buffered up to 256 frames per session. Add `VOICE_BOT_DEEPGRAM_API_KEY` to the local `.env` to enable Nova-3 multilingual streaming transcription with language auto-detection and code-switching. `VOICE_BOT_DEEPGRAM_LANGUAGE` defaults to `multi` and can pin a specific language; without Deepgram, the local RMS detector continues to emit speech-start/end events. Add `VOICE_BOT_GEMINI_API_KEY` to enable streamed Gemini text replies; Vox follows the language of the user?s latest message. The primary model defaults to `gemini-3.1-flash-lite` and falls back to `gemini-3.5-flash-lite` if it fails before producing text. Configure them with `VOICE_BOT_GEMINI_MODEL` and `VOICE_BOT_GEMINI_FALLBACK_MODEL`. Azure Speech consumes Gemini text as it arrives and streams mono 24 kHz PCM audio when both `VOICE_BOT_AZURE_SPEECH_KEY` and `VOICE_BOT_AZURE_SPEECH_REGION` are configured; Vox automatically selects `en-IN-NeerjaNeural` for English and `te-IN-ShrutiNeural` for Telugu when Deepgram detects those languages; other languages use the configured multilingual fallback voice (`VOICE_BOT_AZURE_SPEECH_VOICE`). Add `VOICE_BOT_MEM0_API_KEY` to enable optional Mem0 Platform long-term memory; relevant memories are retrieved for each turn and completed exchanges are sent to Mem0 under the authenticated user's ID. The browser shows saved conversations after sign-in, lets users view transcripts, and can resume any saved conversation with its microphone off until explicitly started. The history API supports `GET /api/v1/conversations`, `GET /api/v1/conversations/{id}`, and authenticated `DELETE /api/v1/conversations/{id}`. Deleting a chat removes its MongoDB transcript and LangGraph checkpoints but does not delete Mem0 memories. Reconnect to `/api/v1/ws/voice?conversation_id={id}` to resume an owned conversation.

The voice controls include an architecture selector. Choose the cascade (Deepgram STT + Gemini text + Azure TTS) or Gemini Live (`gemini-3.8-live`) for native real-time audio. Gemini Live uses the existing `VOICE_BOT_GEMINI_API_KEY`, bypasses Deepgram and Azure for that session, and saves available transcripts into MongoDB history. Both voice architectures restore the selected conversation's MongoDB transcript as model context. When Mem0 is configured, Gemini Live calls a user-scoped memory search tool for each turn, then saves completed exchanges to Mem0; interrupted partial replies are not added as long-term memories. Set `VOICE_BOT_GEMINI_LIVE_MODEL` only if you want to override the default Live model. Text-only messages continue using the existing chat pipeline.

See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) for the next steps and [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md) for the current implementation state.

## Configuration and secrets

Never commit credentials. The root `.env` is ignored by Git. PostgreSQL and a random signing secret are required to register or log in. For local PostgreSQL, create a database such as `voice_bot` and set `VOICE_BOT_POSTGRES_DSN=postgresql://postgres:<your-local-password>@localhost:5432/voice_bot` (URL-encode special characters in the password). MongoDB is configured separately with `VOICE_BOT_MONGODB_URI` for conversation documents and checkpoints. Deepgram transcription is optional and requires `VOICE_BOT_DEEPGRAM_API_KEY`. Gemini text replies are optional and require `VOICE_BOT_GEMINI_API_KEY`. Azure speech output is optional and requires both `VOICE_BOT_AZURE_SPEECH_KEY` and `VOICE_BOT_AZURE_SPEECH_REGION`. Mem0 Platform memory is optional and requires `VOICE_BOT_MEM0_API_KEY`; conversation turns are sent to that service for memory extraction and retrieval. Without this key, chat behavior remains unchanged and no Mem0 calls are made.

Finalized turns are checkpointed in `conversation_checkpoints` and `conversation_checkpoint_writes`; bounded user-visible transcript history is stored in `conversations`. History endpoints filter by the signed-in user's ID.
