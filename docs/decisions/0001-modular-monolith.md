# ADR 0001: Modular Monolith

- Status: accepted
- Date: 2026-10-23

## Context

The product is one real-time voice assistant application. Its integrations need clear boundaries, but splitting them into independently deployed services would add unnecessary network hops and operational complexity.

## Decision

Keep the backend in one FastAPI application and organize it by responsibility. Isolate vendor-specific SDK code in the relevant technology modules. Keep routes thin and avoid a generic `services/` layer. MongoDB stores application data, LangGraph checkpointing stores short-lived execution state, and Mem0 owns long-term semantic memory.

## Consequences

The application remains deployable as one unit while provider adapters can be replaced independently. Modules must avoid reaching across boundaries for vendor-specific behavior. The architecture does not claim any integration is implemented yet.
