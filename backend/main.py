"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from backend.core.config import get_settings
from backend.core.exceptions import ApplicationError, application_error_handler, unexpected_error_handler
from backend.core.logging import configure_logging
from backend.core.middleware import RequestObservabilityMiddleware
from backend.core.agent.conversation import ConversationOrchestrator, build_conversation_graph
from backend.core.database.mongodb import MongoDatabase
from backend.core.database.postgres import PostgresDatabase
from backend.core.database.repositories.user import UserRepository
from backend.core.database.repositories.conversation import ConversationRepository
from backend.core.llm.gemini import GeminiChatModel
from backend.core.memory.mem0 import Mem0LongTermMemory
from backend.core.tts.azure import AzureSpeechSynthesizer
from backend.routes.auth import router as auth_router
from backend.routes.conversations import router as conversations_router
from backend.routes.health import router as health_router
from backend.core.websocket.voice import voice_websocket
from backend.core.voice.session import VoiceSessionManager
from backend.core.voice.gemini_live import GeminiLiveVoice


def create_app() -> FastAPI:
    """Create the application using validated runtime settings."""
    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = None
        postgres = None
        checkpoint_client = None
        chat_model = None
        live_voice = None
        speech_synthesizer = None
        long_term_memory = None
        session_manager = VoiceSessionManager()
        app.state.voice_session_manager = session_manager
        app.state.mongodb = None
        app.state.postgres = None
        app.state.user_repository = None
        app.state.conversation_repository = None
        try:
            if settings.postgres_dsn is not None:
                postgres = PostgresDatabase(settings)
                await postgres.connect()
                user_repository = UserRepository(postgres.pool)
                await user_repository.ensure_indexes()
                app.state.postgres = postgres
                app.state.user_repository = user_repository

            if settings.mongodb_uri is not None:
                database = MongoDatabase(settings)
                await database.connect()
                conversation_repository = ConversationRepository(
                    database.database["conversations"],
                    checkpoint_collection=database.database["conversation_checkpoints"],
                    checkpoint_writes_collection=database.database[
                        "conversation_checkpoint_writes"
                    ],
                )
                await conversation_repository.ensure_indexes()
                app.state.conversation_repository = conversation_repository
                app.state.mongodb = database
                if postgres is not None:
                    await postgres.migrate_legacy_mongo_users(database.database["users"])
                checkpoint_client = MongoClient(
                    settings.mongodb_uri.get_secret_value(),
                    serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
                )
                checkpointer = MongoDBSaver(
                    checkpoint_client,
                    db_name=settings.mongodb_database,
                    checkpoint_collection_name="conversation_checkpoints",
                    writes_collection_name="conversation_checkpoint_writes",
                )
                logger.info("MongoDB connection established")
            else:
                app.state.mongodb = None
                checkpointer = InMemorySaver()
                logger.info("MongoDB is not configured; database features are unavailable")
            if settings.gemini_api_key is not None:
                chat_model = GeminiChatModel(
                    settings.gemini_api_key.get_secret_value(),
                    model=settings.gemini_model,
                    fallback_model=settings.gemini_fallback_model,
                )
                live_voice = GeminiLiveVoice(
                    settings.gemini_api_key.get_secret_value(), model=settings.gemini_live_model
                )
            if settings.mem0_api_key is not None:
                long_term_memory = Mem0LongTermMemory(settings.mem0_api_key.get_secret_value())
            if settings.azure_speech_key is not None and settings.azure_speech_region is not None:
                speech_synthesizer = AzureSpeechSynthesizer(
                    settings.azure_speech_key.get_secret_value(),
                    settings.azure_speech_region,
                    voice=settings.azure_speech_voice,
                )
            app.state.speech_synthesizer = speech_synthesizer
            app.state.gemini_live_voice = live_voice
            app.state.long_term_memory = long_term_memory
            app.state.conversation_orchestrator = ConversationOrchestrator(
                build_conversation_graph(checkpointer, chat_model, long_term_memory),
                model_configured=chat_model is not None,
            )
            yield
        finally:
            await session_manager.shutdown()
            if speech_synthesizer is not None:
                await speech_synthesizer.aclose()
            if chat_model is not None:
                await chat_model.aclose()
            if live_voice is not None:
                await live_voice.aclose()
            if long_term_memory is not None:
                await long_term_memory.aclose()
            if checkpoint_client is not None:
                checkpoint_client.close()
            if database is not None:
                await database.close()
                logger.info("MongoDB connection closed")
            if postgres is not None:
                await postgres.close()
                logger.info("PostgreSQL connection closed")

    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.add_middleware(RequestObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(conversations_router, prefix=settings.api_v1_prefix)
    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.add_api_websocket_route(f"{settings.api_v1_prefix}/ws/voice", voice_websocket)
    logger.info("Application initialized", extra={"environment": settings.environment})
    return app


app = create_app()
