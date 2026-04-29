from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import json

from app.database import get_db
from app.application.qa.ask_service import (
    ConversationNotFoundError,
    ProjectNotFoundError,
    answer_from_context,
    delete_conversation as delete_conversation_service,
    ensure_project_exists,
    list_conversation_messages as list_conversation_messages_service,
    list_conversations as list_conversations_service,
    persist_ask_response,
    retrieve_context,
    run_ask,
)
from app.schemas.qa import (
    AskRequest,
    AskResponse,
    ConversationListItem,
    ConversationMessage,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["qa"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _service_404(exc: ProjectNotFoundError | ConversationNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.post("/ask", response_model=AskResponse)
async def ask_project(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        response = await run_ask(project_id, body, db)
        await db.commit()
        return response
    except (ProjectNotFoundError, ConversationNotFoundError) as exc:
        await db.rollback()
        raise _service_404(exc) from exc


@router.post("/ask/stream")
async def ask_project_stream(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
):
    async def events() -> AsyncIterator[str]:
        try:
            await ensure_project_exists(project_id, db)
        except ProjectNotFoundError as exc:
            yield _sse("error", {"detail": str(exc)})
            return

        yield _sse("status", {"stage": "retrieving", "message": "Retrieving evidence"})
        context = await retrieve_context(project_id, body, db)
        yield _sse(
            "status",
            {
                "stage": "answering" if context.has_enough_evidence else "insufficient",
                "message": (
                    "Answering from retrieved evidence"
                    if context.has_enough_evidence
                    else "Not enough evidence found"
                ),
                "retrieved_count": len(context.items),
                "top_score": context.top_score,
            },
        )
        answer = await answer_from_context(body, context)
        try:
            response = await persist_ask_response(project_id, body, answer, context, db)
            await db.commit()
        except ConversationNotFoundError as exc:
            await db.rollback()
            yield _sse("error", {"detail": str(exc)})
            return
        yield _sse("answer", response.model_dump(mode="json"))
        yield _sse("done", {"conversation_id": str(response.conversation_id)})

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/conversations", response_model=list[ConversationListItem])
async def list_conversations(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_conversations_service(project_id, db)
    except ProjectNotFoundError as exc:
        raise _service_404(exc) from exc


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessage],
)
async def list_conversation_messages(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_conversation_messages_service(project_id, conversation_id, db)
    except ConversationNotFoundError as exc:
        raise _service_404(exc) from exc


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_conversation_service(project_id, conversation_id, db)
        await db.commit()
    except ConversationNotFoundError as exc:
        await db.rollback()
        raise _service_404(exc) from exc
    return Response(status_code=204)
