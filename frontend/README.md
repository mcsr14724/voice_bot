# Browser voice client

This Next.js app provides account registration/login, authenticated WebSocket setup, microphone capture, and mono 16 kHz PCM streaming to the FastAPI backend.

## Run locally

From this directory, install dependencies and start the development server:

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`. Start the backend separately from the repository root with `py -m uvicorn backend.main:app --reload`. The local client targets `http://127.0.0.1:8000`; no frontend environment file is needed.

After signing in, choose **Speech evaluation** in the sidebar and select the dataset folder. WAV and TXT files are paired by their relative path and filename stem; unmatched files are ignored. Run STT or TTS to see transcripts and WER scores in the page, and play or download generated TTS audio. The folder picker accepts the multilingual dataset layout with language subfolders.

The UI sends one matched pair at a time to the authenticated development evaluation API, so provider keys remain on the backend. Enable it before starting the backend with `$env:VOICE_BOT_ENABLE_AUDIO_EVALUATION = "true"`; see [`docs/AUDIO_EVALUATION.md`](../docs/AUDIO_EVALUATION.md) for WAV requirements and limits.

The microphone API requires a secure browser context (localhost qualifies) and explicit permission. Add `VOICE_BOT_DEEPGRAM_API_KEY` to the repository's single local `.env` to enable interim and final Nova-3 transcripts. Without it, the backend only reports basic local speech boundaries. Assistant responses are not connected yet.
