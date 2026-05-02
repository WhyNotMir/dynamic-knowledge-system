from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chains.qa_agent import QAAgentOutput, answer_question
from app.domain.qa.retrieval import RetrievalResult, retrieve_relevant_blocks
from app.domain.qa.settings import QASettings, qa_settings_from_project_settings
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


class AskServiceError(RuntimeError):
    """Base error for Q&A service failures that the API maps to HTTP."""


class ProjectNotFoundError(AskServiceError):
    """Raised when a Q&A operation targets a missing project."""


class ConversationNotFoundError(AskServiceError):
    """Raised when a conversation does not exist in the requested project."""


@dataclass(frozen=True)
class RetrievedContext:
    items: list[RetrievalResult]
    settings: QASettings

    @property
    def top_score(self) -> float:
        return max((item.score for item in self.items), default=0.0)

    @property
    def has_enough_evidence(self) -> bool:
        if len(self.items) < self.settings.min_evidence_blocks:
            return False
        return self.top_score >= self.settings.min_evidence_score


def citation_from_result(result: RetrievalResult) -> AskCitation:
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


def insufficient_answer(question: str) -> str:
    return (
        "There is not enough information in this knowledge base to answer: "
        f"{question}"
    )


async def ensure_project_exists(project_id: uuid.UUID, db: AsyncSession) -> None:
    if await db.get(Project, project_id) is None:
        raise ProjectNotFoundError("Project not found")


async def load_qa_settings(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession,
) -> QASettings:
    project = await db.get(Project, project_id)
    if project is None:
        raise ProjectNotFoundError("Project not found")

    settings = qa_settings_from_project_settings(project.settings)
    fields = body.model_fields_set
    return QASettings(
        top_k=body.top_k if "top_k" in fields else settings.top_k,
        max_per_article=(
            body.max_per_article
            if "max_per_article" in fields
            else settings.max_per_article
        ),
        min_score=body.min_score if "min_score" in fields else settings.min_score,
        min_evidence_score=settings.min_evidence_score,
        min_evidence_blocks=settings.min_evidence_blocks,
    )


async def retrieve_context(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession,
) -> RetrievedContext:
    settings = await load_qa_settings(project_id, body, db)
    context = await retrieve_relevant_blocks(
        project_id,
        body.question,
        db,
        top_k=settings.top_k,
        max_per_article=settings.max_per_article,
        min_score=settings.min_score,
    )
    return RetrievedContext(items=context, settings=settings)


async def answer_from_context(
    body: AskRequest,
    context: RetrievedContext,
) -> QAAgentOutput:
    if context.has_enough_evidence:
        return await answer_question(body.question, context.items)
    return QAAgentOutput(
        answer=insufficient_answer(body.question),
        citation_block_ids=[],
        confidence=0.0,
        insufficient_context=True,
    )


async def get_or_create_conversation(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession,
) -> Conversation:
    if body.conversation_id is not None:
        conversation = await db.get(Conversation, body.conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise ConversationNotFoundError("Conversation not found")
        return conversation

    title = body.question.strip().replace("\n", " ")
    if len(title) > 96:
        title = f"{title[:93]}..."
    conversation = Conversation(project_id=project_id, title=title or None)
    db.add(conversation)
    await db.flush()
    return conversation


async def next_message_position(
    conversation_id: uuid.UUID,
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
    )
    return int(result.scalar_one())


async def persist_ask_response(
    project_id: uuid.UUID,
    body: AskRequest,
    answer: QAAgentOutput,
    context: RetrievedContext,
    db: AsyncSession,
) -> AskResponse:
    context_by_block_id = {item.block_id: item for item in context.items}
    cited_results = [
        context_by_block_id[block_id]
        for block_id in answer.citation_block_ids
        if block_id in context_by_block_id
    ]
    citations = [citation_from_result(result) for result in cited_results]

    conversation = await get_or_create_conversation(project_id, body, db)
    conversation.updated_at = datetime.now(timezone.utc)
    start_position = await next_message_position(conversation.id, db)

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
    await db.flush()

    return AskResponse(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        answer=answer.answer,
        citations=citations,
        confidence=answer.confidence,
        insufficient_context=answer.insufficient_context,
    )


async def run_ask(
    project_id: uuid.UUID,
    body: AskRequest,
    db: AsyncSession,
) -> AskResponse:
    await ensure_project_exists(project_id, db)
    context = await retrieve_context(project_id, body, db)
    answer = await answer_from_context(body, context)
    return await persist_ask_response(project_id, body, answer, context, db)


async def list_conversations(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> list[ConversationListItem]:
    await ensure_project_exists(project_id, db)

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


async def list_conversation_messages(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession,
) -> list[ConversationMessage]:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project_id:
        raise ConversationNotFoundError("Conversation not found")

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


async def delete_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project_id:
        raise ConversationNotFoundError("Conversation not found")

    await db.delete(conversation)
    await db.flush()
