from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.article import ArticleKind, ArticleStatus
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
    # Phase 0 addition. Nullable for synthesized blocks (Phase 7) and
    # exposed so invariant checks can run client-side too.
    source_position_index: int | None = None
    page_number: int | None
    section_path: str | None
    list_level: int | None = None
    group_id: uuid.UUID | None = None
    inline_spans: list | None = None
    meta_json: dict | None = None
    synthesized: bool = False

    model_config = {"from_attributes": True}


class ArticleListItem(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    kind: ArticleKind = ArticleKind.ARTICLE
    structural_block_id: uuid.UUID | None = None
    suggested_section: str | None
    status: ArticleStatus
    description: str | None = None
    block_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ArticleDetail(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    candidate_id: uuid.UUID | None
    structural_block_id: uuid.UUID | None = None
    title: str
    slug: str
    kind: ArticleKind = ArticleKind.ARTICLE
    suggested_section: str | None
    description: str | None = None
    summary: str | None = None
    status: ArticleStatus
    aliases: list[str] | None = None
    revision_count: int = 0
    blocks: list[ArticleBlockSchema]
    created_at: datetime

    model_config = {"from_attributes": True}
