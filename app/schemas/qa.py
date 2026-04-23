from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.source_fragment import ElementType


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    top_k: int = Field(default=15, ge=1, le=30)
    max_per_article: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=0.2, ge=0.0, le=1.0)


class AskCitation(BaseModel):
    block_id: uuid.UUID
    article_id: uuid.UUID
    article_title: str
    fragment_id: uuid.UUID
    content: str
    element_type: ElementType
    page_number: int | None = None
    section_path: str | None = None
    score: float


class AskResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    answer: str
    citations: list[AskCitation]
    confidence: float
    insufficient_context: bool


class ConversationListItem(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationMessage(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    position_index: int
    meta_json: dict | None = None
    created_at: datetime
