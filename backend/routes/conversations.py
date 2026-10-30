"""Authenticated conversation history endpoints."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from backend.core.exceptions import ApplicationError
from backend.core.database.repositories.conversation import ConversationRepository
from backend.models.auth import UserResponse
from backend.routes.auth import get_current_user

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    preview: str


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessage]


def get_conversation_repository(request: Request) -> ConversationRepository:
    repository = getattr(request.app.state, "conversation_repository", None)
    if repository is None:
        raise ApplicationError(
            "Conversation storage is unavailable", status_code=503, code="database_unavailable"
        )
    return repository


def _summary(document: dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        id=str(document["_id"]),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        preview=document.get("preview", ""),
    )


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
) -> list[ConversationSummary]:
    documents = await repository.list_for_user(current_user.id)
    return [_summary(document) for document in documents]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def read_conversation(
    conversation_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
) -> ConversationDetail:
    document = await repository.find_for_user(conversation_id, current_user.id)
    if document is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetail(
        **_summary(document).model_dump(),
        messages=document.get("messages", []),
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
) -> Response:
    deleted = await repository.delete_for_user(conversation_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
