# Browser voice client

This Next.js app provides account registration/login, authenticated WebSocket setup, microphone capture, and mono 16 kHz PCM streaming to the FastAPI backend.

## Run locally

From this directory, install dependencies and start the development server:

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`. Start the backend separately from the repository root with `py -m uvicorn backend.main:app --reload`. The local client targets `http://127.0.0.1:8000`; no frontend environment file is needed.

The microphone API requires a secure browser context (localhost qualifies) and explicit permission. Add `VOICE_BOT_DEEPGRAM_API_KEY` to the repository's single local `.env` to enable interim and final Nova-3 transcripts. Without it, the backend only reports basic local speech boundaries. Assistant responses are not connected yet.
