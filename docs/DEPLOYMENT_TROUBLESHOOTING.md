# Deployment troubleshooting history

This document records the deployment and connectivity issues encountered while bringing up the Vox frontend and FastAPI backend. It distinguishes service failures from expected responses and local-development problems.

## Issues encountered

### Backend refused to start: production frontend origin must use HTTPS

**Observed:** Pydantic raised `frontend_origins must use HTTPS in production`, then the process exited before binding a port.

**Cause:** `VOICE_BOT_ENVIRONMENT` was `production` while one of the configured frontend origins used HTTP or did not match the deployed HTTPS frontend.

**Resolution:** In the backend hosting environment, set `VOICE_BOT_FRONTEND_ORIGINS` to a JSON list containing the exact HTTPS frontend origin, for example:

```text
["https://your-frontend.example.com"]
```

Keep `VOICE_BOT_ENVIRONMENT=production` in deployment and leave debug mode disabled. Do not use the local development origin in production.

### WebSocket handshake returned HTTP 403

**Observed:** The browser reported a failed WebSocket handshake with status `403`.

**Cause to check first:** The backend checks the request `Origin` against `VOICE_BOT_FRONTEND_ORIGINS` before accepting the socket. A missing or non-exact frontend origin is rejected before the WebSocket session starts.

**Resolution:** Add the browser's exact origin (scheme and hostname, without a path) to the backend's `VOICE_BOT_FRONTEND_ORIGINS`, then redeploy the backend. The frontend and backend must also use the matching deployed API URL. After the handshake, the client authenticates by sending its access token as the first WebSocket message; authentication failures at this stage are distinct from an origin rejection.

### Service root `/` returned 404 after deployment

**Observed:** Render's probe and a browser request to `/` returned `404 Not Found`, even though startup logs later showed Uvicorn running and Render reported the service live.

**Cause:** The API does not define a root route. A 404 at `/` is expected and does not by itself mean the backend failed.

**Check instead:**

- Liveness: `https://your-backend.example.com/api/v1/health`
- Readiness and configured database checks: `https://your-backend.example.com/api/v1/ready`

Use `/api/v1/health` as the hosting platform's health-check path if one is configured.

### Voice connected but produced no transcript

**Observed:** The diagnostic panel showed that the WebSocket was authenticated, the backend received audio, and Deepgram received frames, but no transcript appeared. Later, the microphone diagnostic reported RMS `0.0000` / below the speech threshold while the user was speaking.

**Interpretation:** If the backend confirms that audio frames reached Deepgram, the connection and provider setup progressed beyond deployment. An input level of zero points to silence entering the browser audio capture path, commonly the wrong selected microphone, a mute state, or an OS/browser input-routing issue.

**Resolution:** In voice diagnostics, choose the actual microphone and check that the displayed input RMS rises above the shown speech threshold while speaking. Check browser microphone permission, OS input selection/volume, and hardware mute. If RMS rises but there is still no transcript, inspect the backend's categorized transcription errors and Deepgram model, language, and account settings.

### WebSocket closed unexpectedly (1006) when changing voice engine or continuing a saved chat

**Observed:** Switching between cascade and Gemini Live, or reconnecting to a saved conversation, could leave the UI at “Authenticating” and then show a WebSocket close code `1006`.

**Cause:** A conversation ID was also being used as the backend's active WebSocket-connection key. A quick reconnect could collide with the previous connection during cleanup. In addition, an old socket's delayed close handler could update state belonging to a newer socket.

**Resolution:** The backend now tracks each WebSocket connection with a unique connection ID while retaining the conversation ID for transcript/checkpoint ownership. The frontend ignores close/error callbacks from superseded sockets. Deploy both frontend and backend to receive the complete fix.

## Local development is not the Render network

### Production-origin validation while running `fastapi dev`

The application reads `VOICE_BOT_ENVIRONMENT`; `fastapi dev` setting `FASTAPI_ENV=development` does not override it. For local runs, explicitly set the application environment and local origin in the current PowerShell session:

```powershell
$env:VOICE_BOT_ENVIRONMENT = "development"
$env:VOICE_BOT_FRONTEND_ORIGINS = '["http://localhost:3000"]'
fastapi dev backend\main.py
```

These settings are for local development only. Keep the production HTTPS origin configured in the hosting provider.

### PostgreSQL hostname could not be resolved locally

**Observed:** PostgreSQL startup logged a `gaierror`. Initially, the process-level `VOICE_BOT_POSTGRES_DSN` contained a short Render internal hostname. A process-level variable takes precedence over the same key in `.env`.

**Cause:** Render's internal database hostname resolves only from Render's private network, not from a developer's local machine.

**Resolution:** Use a local PostgreSQL DSN, or use Render's external database URL if external access is enabled. Check which source is active without printing the DSN:

```powershell
if ($env:VOICE_BOT_POSTGRES_DSN) {
    "Using process-level PostgreSQL DSN"
} else {
    "Using repository-root .env PostgreSQL DSN"
}
```

To check external connectivity without displaying credentials, extract only the hostname and test the standard PostgreSQL port:

```powershell
$uri = [System.Uri]$env:VOICE_BOT_POSTGRES_DSN
$hostName = $uri.DnsSafeHost
Resolve-DnsName $hostName
Test-NetConnection $hostName -Port 5432
```

If `TcpTestSucceeded` is true, the host and TCP route are reachable; the application still needs a valid DSN, credentials, and database name. `Uri.Port` returns `-1` when the URL omits an explicit port; PostgreSQL commonly uses port `5432`.

## Credential handling

Database URLs, API keys, JWT signing secrets, and account passwords must not be pasted into logs, issues, or chat. If a real credential is exposed, rotate it with its provider, then update the relevant deployment environment and local secret store. Never copy a credential from an old log into a new deployment.

## Deployment checklist

- Backend has `VOICE_BOT_ENVIRONMENT=production`, a strong `VOICE_BOT_SECRET_KEY`, and the exact HTTPS frontend origin in `VOICE_BOT_FRONTEND_ORIGINS`.
- Backend database and provider secrets are configured in the hosting provider, not committed to the repository.
- Frontend `NEXT_PUBLIC_API_URL` points to the deployed backend API base and includes `/api/v1`.
- The deployed backend starts with the existing `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` command.
- The hosting health check targets `/api/v1/health`; readiness can be inspected at `/api/v1/ready`.
- After moving backend feature packages under `backend/core/`, the deployment includes the new package files and updated imports. The application entry point remains `backend.main:app`.
- Deploy both frontend and backend when a change modifies both sides of the voice protocol or reconnect lifecycle.
