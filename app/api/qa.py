from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import json

from app.agents.chains.qa_agent import QAAgentOutput, answer_question
from app.database import get_db
from app.domain.qa.retrieval import RetrievalResult, retrieve_relevant_blocks
from app.models.block_citation import BlockCitation, CitationStatus
from app.models.conversation import Conversation, Message, MessageRole
from app.models.project import Project
from app.schemas.qa import (
    AskCitation,
    AskRequest,
    AskResponse,
    ConversationListItem,
    ConversationMessage,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["qa"])

MIN_EVIDENCE_SCORE = 0.32
MIN_EVIDENCE_BLOCKS = 1


def _citation_from_result(result: RetrievalResult) -> AskCitation:
    return AskCitation(
        block_id=result.block_id,
        article_id=result.article_id,
        article_title=result.article_title,
        fragment_id=result.fragment_id,
        content=result.content,
        element_type=result.element_type,
        page_number=result.page_number,
        section_path=result.section_path,
        score=result.score,
    )


def _has_enough_evidence(context: list[RetrievalResult]) -> bool:
    if len(context) < MIN_EVIDENCE_BLOCKS:
        return False
    return max((item.score for item in context), default=0.0) >= MIN_EVIDENCE_SCORE


def _insufficient_answer(question: str) -> str:
    return (
        "There is not enough information in this knowledge base to answer: "
        f"{question}"
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _get_or_create_conversation(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession,
) -> Conversation:
    if body.conversation_id is not None:
        conversation = await db.get(Conversation, body.conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    title = body.question.strip().replace("\n", " ")
    if len(title) > 96:
        title = f"{title[:93]}..."
    conversation = Conversation(project_id=project_id, title=title or None)
    db.add(conversation)
    await db.flush()
    return conversation


async def _next_message_position(
    conversation_id: uuid.UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
    )
    return int(result.scalar_one())


async def _answer_from_context(
    body: AskRequest,
    context: list[RetrievalResult],
) -> QAAgentOutput:
    if _has_enough_evidence(context):
        return await answer_question(body.question, context)
    return QAAgentOutput(
        answer=_insufficient_answer(body.question),
        citation_block_ids=[],
        confidence=0.0,
        insufficient_context=True,
    )


async def _persist_ask_response(
    project_id: uuid.UUID,
    body: AskRequest,
    answer: QAAgentOutput,
    context: list[RetrievalResult],
    db: AsyncSession,
) -> AskResponse:
    context_by_block_id = {item.block_id: item for item in context}
    cited_results = [
        context_by_block_id[block_id]
        for block_id in answer.citation_block_ids
        if block_id in context_by_block_id
    ]
    citations = [_citation_from_result(result) for result in cited_results]

    conversation = await _get_or_create_conversation(project_id, body, db)
    conversation.updated_at = datetime.now(timezone.utc)
    start_position = await _next_message_position(conversation.id, db)

    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=body.question,
        position_index=start_position,
        meta_json=None,
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer.answer,
        position_index=start_position + 1,
        meta_json={
            "citations": [
                citation.model_dump(mode="json") for citation in citations
            ],
            "confidence": answer.confidence,
            "insufficient_context": answer.insufficient_context,
        },
    )
    db.add_all([user_message, assistant_message])
    await db.flush()

    db.add_all(
        [
            BlockCitation(
                block_id=result.block_id,
                fragment_id=result.fragment_id,
                status=CitationStatus.UNVALIDATED,
                confidence=answer.confidence,
                context=result.content,
            )
            for result in cited_results
        ]
    )
    await db.commit()

    return AskResponse(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        answer=answer.answer,
        citations=citations,
        confidence=answer.confidence,
        insufficient_context=answer.insufficient_context,
    )


async def _run_ask(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession,
) -> AskResponse:
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    context = await retrieve_relevant_blocks(
        project_id,
        body.question,
        db,
        top_k=body.top_k,
        max_per_article=body.max_per_article,
        min_score=body.min_score,
    )
    answer = await _answer_from_context(body, context)
    return await _persist_ask_response(project_id, body, answer, context, db)


@router.post("/ask", response_model=AskResponse)
async def ask_project(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
):
    return await _run_ask(project_id, body, db)


@router.post("/ask/stream")
async def ask_project_stream(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
):
    async def events() -> AsyncIterator[str]:
        if await db.get(Project, project_id) is None:
            yield _sse("error", {"detail": "Project not found"})
            return

        yield _sse("status", {"stage": "retrieving", "message": "Retrieving evidence"})
        context = await retrieve_relevant_blocks(
            project_id,
            body.question,
            db,
            top_k=body.top_k,
            max_per_article=body.max_per_article,
            min_score=body.min_score,
        )
        yield _sse(
            "status",
            {
                "stage": "answering" if _has_enough_evidence(context) else "insufficient",
                "message": (
                    "Answering from retrieved evidence"
                    if _has_enough_evidence(context)
                    else "Not enough evidence found"
                ),
                "retrieved_count": len(context),
                "top_score": max((item.score for item in context), default=0.0),
            },
        )
        answer = await _answer_from_context(body, context)
        response = await _persist_ask_response(project_id, body, answer, context, db)
        yield _sse("answer", response.model_dump(mode="json"))
        yield _sse("done", {"conversation_id": str(response.conversation_id)})

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/conversations", response_model=list[ConversationListItem])
async def list_conversations(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(Conversation, func.count(Message.id).label("message_count"))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.project_id == project_id)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    )
    return [
        ConversationListItem(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=int(message_count),
        )
        for conversation, message_count in result.all()
    ]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessage],
)
async def list_conversation_messages(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.position_index)
    )
    return [
        ConversationMessage(
            id=message.id,
            role=message.role.value,
            content=message.content,
            position_index=message.position_index,
            meta_json=message.meta_json,
            created_at=message.created_at,
        )
        for message in result.scalars().all()
    ]


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.delete(conversation)
    await db.commit()
    return Response(status_code=204)
