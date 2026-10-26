# Development Log

## 2026-10-23 — Phase 1: repository inspection and architecture foundation

- Inspected the repository; it contained no tracked project files or existing implementation.
- Recorded the requested modular-monolith architecture and provider boundaries.
- Added the initial project context, implementation plan, and repository README.
- No application code, integrations, or runtime behavior were added in this phase.
- Validation: documentation and planned directory layout reviewed; there is no runnable application to start yet.

## 2026-10-23 — Phase 2: backend configuration

- Added Pydantic Settings configuration with a `VOICE_BOT_` environment prefix and optional local `.env` loading.
- Added validation for environment, log level, browser origins, and production-only requirements for a 32-character secret and disabled debug mode.
- Added the single local `.env` file with safe defaults, ignored environment files, and pinned the validated backend runtime dependencies.
- Added unit tests for default settings and production/origin validation.
- Validation: 5 configuration unit tests passed; Python syntax compilation passed.

## 2026-10-23 — Phase 3: logging, errors, and executable API foundation

- Added application logging, a safe application error response, and a generic unexpected-error handler that logs details server-side without returning them to clients.
- Added the FastAPI application factory and `/api/v1/health` liveness endpoint.
- Documented local backend setup and run commands.
- Validation: all 7 backend unit tests passed; Python syntax compilation passed; started Uvicorn and confirmed the health endpoint returned `{"status":"ok"}`.

## 2026-10-23 — Phase 4: MongoDB connection lifecycle

- Added pinned PyMongo async driver dependency and secret-wrapped MongoDB URI configuration.
- Added an event-loop-scoped async client that pings on startup, reports only safe error types, and closes at application shutdown.
- MongoDB remains optional for local startup; configured database features require a reachable server. Domain repositories will be added alongside the features that own their data.
- Validation: all 9 backend unit tests passed, including mocked ping/close and credential-redaction checks; Python compilation and an application health check passed. A live MongoDB server was not available for integration validation.

## 2026-10-23 — Phase 5: authentication and authorization

- Added account registration, login, and `/auth/me` endpoints backed by a MongoDB user collection with a unique normalized email index.
- Added Argon2 password hashing off the event loop, generic credential failures, unknown-account timing mitigation, and 30-minute HS256 bearer tokens.
- Auth endpoints require a configured MongoDB connection and a random signing key of at least 32 characters; secrets are not returned or logged.
- Validation: all 17 backend unit tests passed, including password verification, token signature/configuration, input validation, Mongo duplicate handling, and credential-safe errors. The API started and health returned `ok`; auth correctly returned HTTP 503 without MongoDB. Live account creation/login could not be validated because no MongoDB server was available.

## 2026-10-23 — Phase 6: authenticated WebSocket infrastructure

- Login now sets a short-lived HttpOnly cookie scoped to the WebSocket path for browser socket authentication.
- Added `/api/v1/ws/voice`, which enforces the configured browser Origin allowlist, validates the signed user token, and checks that the user still exists before accepting a connection.
- Added a bounded text control protocol for session-ready, ping/pong, close, invalid-message, and oversized-message handling. Audio frames are not implemented yet.
- Validation: all 22 backend unit tests passed; Python compilation passed; live Uvicorn handshake checks rejected both an untrusted Origin and an allowed Origin without authentication with HTTP 403. A successful authenticated socket still requires MongoDB and was not integration-tested.

## 2026-10-23 — Phase 7: voice session lifecycle foundation

- Added `VoiceSession` state transitions and moved control-message handling out of the WebSocket route.
- Added a concurrency-safe in-process session registry with idempotent socket closure and application-shutdown cleanup.
- Session handlers validate bounded control messages and maintain a ready/ping/pong/interrupt/close flow. Per-session response task cancellation and generation checks prevent stale response work from continuing after interruption.
- Added `audio.start`/`audio.stop` negotiation for mono 16 kHz PCM S16LE frames (20–100 ms), exact frame-size validation, and a 32-frame per-session queue with backpressure closure.
- Added a session-owned queue consumer and RMS energy turn detector. It emits `speech.started` after consecutive energetic frames and `speech.ended` after sustained silence or `audio.stop`; socket JSON writes are serialized.
- This is basic endpointing only. It does not transcribe speech or adapt to background noise, and provider integration remains in Phase 9.
- Validation: all 31 backend unit tests passed, including energy thresholds, silence boundaries, stop finalization, malformed PCM, audio negotiation, and transport validation; Python compilation and `git diff --check` passed. Live authenticated session integration remains blocked on a reachable MongoDB instance.

## 2026-10-23 — Phase 8: browser microphone transport

- Added a Next.js 16.4.2 / React 19.3.1 client with registration/login, voice-room connection, explicit microphone start/stop, and a compact session activity view.
- Added an AudioWorklet that mixes microphone channels to mono, resamples to 16 kHz, and sends 20 ms PCM S16LE frames through the authenticated WebSocket cookie.
- Enabled credentialed HTTP CORS for configured frontend origins so browser registration and login can call the backend.
- Validation: all 32 backend unit tests passed, including a credentialed CORS preflight; Python compilation and `git diff --check` passed. Frontend TypeScript check and optimized production build passed. Live authentication and microphone streaming still require MongoDB and a browser permission grant.
- No provider API key is needed for this transport phase.

## 2026-10-23 — Phase 9: Deepgram streaming STT foundation

- Added optional `VOICE_BOT_DEEPGRAM_API_KEY` configuration; credentials remain `SecretStr` and are only read by the backend.
- Added a Deepgram Listen v1 Nova-3 WebSocket adapter that streams negotiated raw 16 kHz linear16 PCM, handles finalize/close controls, and normalizes interim/final transcript and turn events.
- Provider connections start only after the client begins an audio stream; idle authenticated voice rooms do not open billable transcription connections.
- Browser UI now displays live/final transcripts and distinguishes missing or unavailable provider configuration. The local energy detector remains the no-key/provider-failure fallback.
- Validation: all 37 backend unit tests passed, including mocked Deepgram connection headers, audio/control forwarding, transcript normalization, idle-room behavior, and CORS; Python compilation and `git diff --check` passed. Frontend typecheck and production build passed. A live Deepgram session still requires a valid API key and reachable MongoDB-backed login.

## 2026-10-23 — Phase 10: LangGraph conversation checkpoint foundation

- Added a LangGraph message-state workflow and a provider-neutral conversation orchestrator. Finalized Deepgram utterances are added to the graph under the server-generated voice session ID.
- Configured the MongoDB-backed checkpointer to use the supported `MongoDBSaver` adapter and dedicated conversation checkpoint collections.
- Added an in-memory checkpointer when MongoDB is not configured; authenticated voice sessions still require MongoDB.
- This phase persists user transcript state only. Model generation, assistant messages, and resuming a prior session are still planned.
- Validation: source compilation and runtime integration validation are pending because the LangGraph packages are newly added; no MongoDB connection was tested.

## 2026-10-23 — Phase 11: Gemini response streaming foundation

- Added a provider-neutral async chat-model protocol and a Google Gen AI SDK adapter using the stable Gemini 3.1 Flash-Lite model by default.
- The LangGraph response node now streams assistant text deltas, then checkpoints the completed assistant message with the user transcript. Model context is bounded to the most recent 40 messages.
- Added optional `VOICE_BOT_GEMINI_API_KEY` and `VOICE_BOT_GEMINI_MODEL` settings. Without a key, transcript checkpointing continues and the socket reports that assistant replies are unavailable.
- Updated the voice activity panel to show streamed assistant text. Speech synthesis remains in Phase 12.
- Validation: source compilation and runtime/API validation are pending because the Gemini SDK dependency is new; no provider request was made.

## 2026-10-23 — Phase 12: Azure Speech audio streaming foundation

- Added a provider-neutral speech output interface and an Azure Speech adapter that streams raw 24 kHz mono PCM chunks from synthesis callbacks.
- Azure Speech accepts the live model text stream through its WebSocket v2 `TextStream` request while PCM callback chunks are sent to browser playback; synthesis no longer waits for the full Gemini reply.
- Added optional `VOICE_BOT_AZURE_SPEECH_KEY`, `VOICE_BOT_AZURE_SPEECH_REGION`, and `VOICE_BOT_AZURE_SPEECH_VOICE` settings. Speech output stays off when the key and region are absent.
- Validation: source compilation and runtime/provider validation are pending because the Azure Speech SDK dependency is new; no provider request was made.

## Next

Continue with end-to-end validation, observability, and production hardening. Live authentication persistence, microphone-to-Deepgram validation, Gemini/Azure/Mem0 integrations, and conversation resumption still need validation against configured services.

## 2026-10-23 — Streaming TTS refinement

- Connected Gemini deltas directly to Azure Speech WebSocket v2 text-stream input. Synthesis starts as LLM text arrives, and PCM callbacks are forwarded during generation.
- Kept the existing output format and `.env` requirements; no new credential is needed.
- Validation: source compilation and frontend typecheck are pending for this refinement; no provider request was made.

## 2026-10-23 — Phase 13: conversation history and resumption foundation

- Added per-user MongoDB conversation records with bounded transcript history and a user/update-time index.
- Added authenticated history list and detail endpoints that only return the signed-in user's conversations.
- Voice sockets resume an existing conversation by ID only after an ownership check; LangGraph uses that ID as the checkpoint thread.
- Validation: source compilation, frontend typecheck, and diff checks; database-backed behavior was not exercised and no provider or MongoDB connection was made.

## 2026-10-23 — Phase 14: Mem0 long-term memory foundation

- Added an optional Mem0 Platform adapter, disabled when `VOICE_BOT_MEM0_API_KEY` is absent.
- Before generation, the graph searches a small set of memories scoped to the authenticated user and adds them as untrusted factual context. Completed user/assistant turns are sent for memory extraction.
- Mem0 network errors are logged by exception type and do not stop the conversation. MongoDB checkpoints and transcript records remain separate from Mem0 memory.
- Validation: source compilation and frontend typecheck pending; no Mem0 request was made.

## 2026-10-23 — Phase 15: conversation and voice UI foundation

- Added an authenticated saved-conversation list with refresh, transcript viewing, and a resume action for each conversation.
- The browser reconnects using the selected conversation ID and refreshes history after a completed turn. The microphone remains off until the user starts it.
- Validation: backend compilation, frontend typecheck, and diff checks; no live API or provider calls were made.

## 2026-10-23 — Phase 16: observability and production hardening foundation

- Added generated HTTP request IDs, response correlation headers, and safe method/path/status/latency access logs.
- Added a readiness endpoint that checks configured MongoDB and returns only a generic dependency status; the existing health endpoint remains a liveness check.
- Production settings now require HTTPS frontend origins. The browser app adds security headers and derives its API and WebSocket endpoints from optional `NEXT_PUBLIC_API_URL`.
- Automated end-to-end checks and live provider/database validation remain outstanding.

## 2026-10-23 — Virtual environment and offline test fixes

- Created the ignored project `.venv` and installed the pinned requirements there; `pip check` reports no dependency conflicts.
- Updated the MongoDB checkpointer import and client to match the installed LangGraph MongoDB integration. The checkpointer uses its asynchronous interface over a separately managed synchronous PyMongo client.
- Fixed the WebSocket authentication test fixture to explicitly represent an unresumed session.
- No live MongoDB or provider connections were made. Live API checks remain pending user confirmation.

## 2026-10-23 — Phase 16: offline hardening checks

- Added offline tests for request correlation, generic failure responses, and production HTTPS-origin enforcement.
- Fixed the production settings test fixture and aligned `websockets` with the Google Gen AI SDK requirement (`<17`).
- Validation: 8 focused configuration/middleware tests passed; Python compilation and `git diff --check` passed. The broader unit run passed 30 tests but 3 app-level test modules could not import because installing the complete dependency set hit a Windows file-lock error. No app, MongoDB, or provider connections were started. Full integration validation remains outstanding.

## 2026-10-25 — Phase-by-phase integration validation

- Installed project dependencies in the local `.venv`; removed the project requirements from the global Python installation. The virtual environment passes `pip check`.
- The full offline backend unit suite passes: 46 tests. Frontend typecheck and production build pass.
- Live checks passed for Gemini (`gemini-3.1-flash-lite`) streaming, Azure Speech streaming synthesis, Mem0 read-only search, and Deepgram WebSocket streaming transcription using generated test audio.
- Atlas host discovery and TCP connectivity succeeded, but MongoDB ping failed during TLS negotiation with an internal TLS alert. No MongoDB application data was read or changed. MongoDB connection lifecycle, checkpoint persistence, and conversation persistence remain partial until Atlas TLS/connectivity is fixed.
- Browser UI rendering and the sign-in/create-account toggle were checked. Browser microphone and signed-in full-stack voice/history flows remain partial because they depend on working MongoDB connectivity.
- Current phase tally: 9 of 16 complete; phases 4, 8, 10, and 13–16 are partial. No `.env` values were displayed or changed.

## 2026-10-25 — MongoDB Atlas access restored

- After the user allowlisted the client IP in Atlas, a read-only MongoDB ping passed.
- Live Atlas checks passed for FastAPI startup/readiness, app collection indexes, temporary user registration/login/profile, conversation list/detail with ownership scoping, repository create/read/list/authorization, and LangGraph checkpoint save/read/delete.
- All temporary MongoDB user, conversation, and checkpoint test data was deleted. The auth route smoke test used an `.example.com` synthetic address; no real account was created.
- Phases 4, 10, and 13 are now complete. Phase 5 was also exercised through the live MongoDB-backed auth routes. Total: 12 of 16 phases complete; phases 8, 14, 15, and 16 remain partial.
- Mem0 temporary write/search did not return the expected search result; scoped cleanup was requested. Keep phase 14 partial until that path is verified.

## 2026-10-25 — Remaining phases completed

- Fixed local browser authentication by aligning `NEXT_PUBLIC_API_URL` in `frontend/.env` with the frontend's `localhost` origin; the cross-site cookie had caused authenticated WebSocket connections to be rejected. No API keys or secret values were changed.
- Increased the bounded voice-session audio queue to five seconds to tolerate Deepgram WebSocket startup while the browser begins streaming microphone frames.
- Browser end-to-end flow passed: sign-in, voice-room WebSocket, microphone capture and PCM transport, Deepgram transcription, streaming Gemini response, streaming Azure Speech audio, MongoDB transcript save, history transcript view, and conversation resume.
- Mem0 user-scoped write/search passed for the completed test turn. The unique test user's Mem0 memories were deleted.
- Removed the unique browser-test MongoDB user, conversation, and LangGraph checkpoints after verification.
- Final validation: 46 backend unit tests passed; Python compile and `git diff --check` passed; frontend typecheck and production build passed.
- All 16 implementation phases are complete.

## 2026-10-25 — PostgreSQL account-store change

- Replaced MongoDB-backed account repository access with an async PostgreSQL account repository. Registration, login, `/me`, and voice WebSocket user validation now use PostgreSQL.
- Added async PostgreSQL pool lifecycle, schema/index creation, and an idempotent one-time legacy migration that preserves MongoDB user IDs and password hashes. MongoDB remains the conversation/checkpoint document store.
- Added `asyncpg==0.31.0`; installed it in `.venv`.
- The local PostgreSQL 18 service is running and accepting connections, but neither the `postgres` role nor the Windows user can connect without a configured password. The root `.env` currently has no PostgreSQL DSN, so live PostgreSQL/full-browser auth verification remains pending. No Postgres data was created or changed.
- Validation status is intentionally partial for phases 5, 6, 15, and 16 until the user supplies `VOICE_BOT_POSTGRES_DSN`; existing MongoDB/provider integration checks remain as previously passed.


## 2026-10-25 ? PostgreSQL login verified

- The configured PostgreSQL role reached the local server and created the missing `voice_bot` database. The DSN and credentials were not displayed or changed.
- Verified real PostgreSQL schema/index creation and temporary account insert, email lookup, ID lookup, and cleanup.
- Ran the app lifespan against PostgreSQL and MongoDB; the one-time legacy account migration completed with zero source users to copy.
- Live API registration, login, and authenticated `/auth/me` passed against PostgreSQL. The synthetic account was deleted. Mem0 readiness ping returned HTTP 200.
- All 51 backend unit tests passed. Phases 5, 6, 15, and 16 are complete; total: 16 of 16.


## 2026-10-25 ? Gemini primary and fallback models

- Set Gemini 3.1 Flash-Lite as the primary model and Gemini 3.5 Flash-Lite as the fallback in local configuration. The fallback is used when the primary errors or returns no text before streaming begins.
- Verified the configured API key against both models: the 3.1 request failed, while the adapter retried with 3.5 and streamed a response.
- Added unit coverage for primary success, initial failure fallback, empty-response fallback, and avoiding duplicate output after a partial stream.
- The existing local FastAPI development process stopped answering readiness checks after reload; the browser voice pipeline still needs revalidation after the backend is restarted.


## 2026-10-25 ? Chat UI and hands-free voice mode

- Replaced the voice-room dashboard with a chat-style workspace containing text chat, recent conversation navigation, and an optional voice-mode toggle.
- Voice mode keeps the microphone stream open; speech pauses complete turns. Speaking while Vox is responding now cancels the active model/TTS generation while leaving transcription active for the next user turn.
- Added authenticated WebSocket text turns; text replies remain text-only unless voice mode requests spoken output. Removed separate interrupt and stop controls.
- Updated phase 16 to partial until the backend is restarted and the changed voice flow is checked in the browser.

## 2026-10-26 — Phase 17: selectable voice architectures

- Added a chat-header selector for the existing Deepgram STT + Gemini text + Azure TTS cascade and native Gemini Live (`gemini-3.8-live`).
- Added a backend Live API adapter that forwards Vox's 16 kHz PCM microphone frames and returns Gemini's 24 kHz PCM audio, input/output transcript events, and interruption signals over the existing authenticated socket.
- Completed and interrupted Live turns are recorded in the same MongoDB conversation history. Live mode uses the existing Gemini API key; its model can be configured with `VOICE_BOT_GEMINI_LIVE_MODEL`.
- Confirmed the installed Google GenAI SDK exposes the Live config/transcription fields and async client close method. No provider request or app/browser test was run; live provider validation remains pending.
- Progress: 15 of 17 phases complete; phases 16 and 17 still need their pending runtime validation.
- Follow-up fix: kept the Gemini Live socket active after each turn because the SDK receive iterator ends at `turn_complete`; the mic sender now continues into the next receive iterator. Microphone stop/restart also forwards the Live API `audio_stream_end` signal.
- Follow-up context fix: restore the owned MongoDB transcript when a voice socket opens. Gemini Live receives prior user/model turns as context, while LangGraph reconciles its checkpoint with saved turns from Live; turns typed or spoken in the current socket are kept in the handoff history too.
- Follow-up stop/restart fix: `audio.start` now replaces a completed provider consumer, and configured Deepgram sessions retry with bounded backoff instead of switching to the transcript-free local energy detector after a provider disconnect.
- Follow-up language and barge-in fix: the cascade now passes Deepgram's detected language into the per-turn reply instruction and interrupts playback only after a non-empty transcript with acceptable confidence (or when confidence is unavailable). Gemini Live now has explicit same-language/script instructions and lower start-of-speech sensitivity with short prefix padding to reduce false interruptions from background sounds.
- Cascade speech pacing: Gemini is instructed to avoid unnecessary periods, ellipses, and sentence fragments that create frequent pauses in Azure's spoken output. Text and audio remain streamed as they arrive; Azure's text-stream mode does not expose SSML prosody controls for directly setting a faster speech rate.
- Barge-in responsiveness: cascade interruption now sends the browser stop signal immediately after invalidating/canceling the response, before awaiting Azure/provider shutdown, so already-buffered browser audio is cut off promptly. Gemini Live uses high start-of-speech sensitivity with browser echo cancellation and noise suppression enabled, to improve detection of quieter user speech.
- Cascade transcript handling: removed the assistant-text similarity filter after it proved capable of suppressing real user turns that overlap Vox's reply. Browser echo cancellation and noise suppression remain enabled; Deepgram transcript recognition is the barge-in confirmation.
- Stale-audio guard: cascade PCM sends recheck the response generation while holding the WebSocket send lock, and the browser suppresses audio frames after an interruption until the next response starts audio. This prevents delayed PCM packets from resuming an interrupted reply.
- Cascade barge-in sensitivity: accept recognized interim transcripts down to 0.2 confidence (or when confidence is omitted) so valid speech in noisy rooms is less likely to be missed.
- Cascade speech-onset interruption: enabled Deepgram's `vad_events` subscription (it defaults off). Browser-side VAD and Deepgram `SpeechStarted` provisionally pause playback; recognized Deepgram transcript evidence cancels the current generation and confirms the interruption. If no transcript follows, browser playback resumes after a short hold. The actual transcript assembles the next turn.
- Cascade backchannel handling: short acknowledgments such as “uh-huh” and “mm-hmm” no longer cancel the current response or start a separate assistant turn. Playback resumes after the acknowledgment; other recognized user speech still interrupts the active Gemini/Azure response.
- Cascade stale-audio fix: prefix each Azure PCM frame with its response generation and accept audio only for the currently active generation. On interruption, the browser invalidates that generation, stops queued sources, and ignores delayed frames even after the next reply begins.
- Gemini Live context parity: Live now restores the selected conversation's MongoDB transcript, exposes a synchronous user-scoped Mem0 search function for each turn, and stores completed exchanges in Mem0. Interrupted partial replies remain in MongoDB transcript history but are excluded from long-term memory. Local Python compilation, installed-SDK tool config construction, and `git diff --check` passed; no Gemini or Mem0 provider call was made.
- Gemini Live audio follow-up: explicitly set the Mem0 function to blocking behavior for Gemini 3.8 Live and continue processing any server audio content that accompanies a tool call. SDK configuration construction, Python compilation, and `git diff --check` passed; live browser/provider validation remains pending.
