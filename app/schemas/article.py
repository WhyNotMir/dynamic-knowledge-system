from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.article import ArticleStatus


class BuildArticlesRequest(BaseModel):
    # Опционально. Если не передано — берётся последняя proposal
    # проекта со статусом READY. UI не обязан знать этот внутренний ID.
    proposal_id: uuid.UUID | None = None


class BuildArticlesResponse(BaseModel):
    article_ids: list[uuid.UUID]
    count: int


class ArticleBlockSchema(BaseModel):
    id: uuid.UUID
    fragment_id: uuid.UUID
    content: str
    element_type: str
    position_index: int
    page_number: int | None
    section_path: str | None

    model_config = {"from_attributes": True}


class ArticleListItem(BaseModel):
    id: uuid.UUID
    title: str
    suggested_section: str | None
    status: ArticleStatus
    block_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ArticleDetail(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    candidate_id: uuid.UUID | None
    title: str
    suggested_section: str | None
    status: ArticleStatus
    blocks: list[ArticleBlockSchema]
    created_at: datetime

    model_config = {"from_attributes": True}