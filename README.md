# Real-Time Voice AI Chatbot

A modular monolith for browser-based, real-time voice conversations. This repository is at the architecture and project-structure stage; provider integrations and the voice pipeline have not been implemented.

## Planned stack

- Frontend: Next.js, TypeScript, React, shadcn/ui, browser media APIs, and WebSocket
- Backend: Python, FastAPI, Pydantic, asyncio, and WebSocket
- Speech: Deepgram streaming STT and Azure Speech TTS
- Agent: LangGraph with a provider-neutral LLM boundary (Gemini is the initial provider)
- Persistence: MongoDB for application records, Mem0 for long-term semantic memory, and a LangGraph checkpointer for short-term execution state

## Repository map

- `frontend/`: browser application
- `backend/`: FastAPI modular monolith and technology adapters
- `docs/decisions/`: architecture decision records
- `PROJECT_CONTEXT.md`: current implementation state and architectural constraints
- `IMPLEMENTATION_PLAN.md`: ordered delivery plan
- `DEVELOPMENT_LOG.md`: completed work by phase

## Development

The project is not runnable yet. Setup instructions will be added with the first executable backend and frontend foundations. See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) for the next steps and [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md) for what exists today.

## Configuration and secrets

Never commit credentials. Local environment files will be covered by `.gitignore`; a safe example configuration will be introduced when configuration is implemented.
