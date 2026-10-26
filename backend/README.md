# Backend

FastAPI modular monolith. Authentication and user records use PostgreSQL through the async account repository. MongoDB stores conversation documents and LangGraph checkpoints. Other focused areas include `core/`, `routes/`, `models/`, `websocket/`, `voice/`, `tts/`, `llm/`, `agent/`, `memory/`, and `tests/`.

Configure `VOICE_BOT_POSTGRES_DSN` and `VOICE_BOT_MONGODB_URI` in the root `.env` for the full application. PostgreSQL tables and indexes are created at startup. Existing MongoDB users are copied once to PostgreSQL when both databases are configured.


The authenticated WebSocket accepts text turns and continuous 16 kHz microphone audio. Browser-side speech detection pauses playback while Deepgram continues transcription; recognized interruptions cancel the active Gemini/Azure response, and short acknowledgments such as “mm-hmm” are ignored. Cascade output frames carry a response-generation tag so late PCM from an interrupted reply is discarded by the browser. Text turns can request spoken output when voice mode is active.

Gemini Live restores up to 40 messages from the selected MongoDB conversation. When Mem0 is configured, Live uses a synchronous function call to search up to five relevant memories under the authenticated user ID on each turn, then stores completed user/assistant exchanges in Mem0. Interrupted partial responses are not stored as long-term memories. Both integrations use the existing `VOICE_BOT_MONGODB_URI` and `VOICE_BOT_MEM0_API_KEY` settings.
