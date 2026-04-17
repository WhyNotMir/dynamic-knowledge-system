from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.article import ArticleStatus
from app.models.source_fragment import ElementType


class BuildArticlesRequest(BaseModel):
    proposal_id: uuid.UUID | None = None


class BuildArticlesResponse(BaseModel):
    article_ids: list[uuid.UUID]
    count: int


class ArticleBlockSchema(BaseModel):
    id: uuid.UUID
    fragment_id: uuid.UUID | None
    content: str
    element_type: ElementType
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