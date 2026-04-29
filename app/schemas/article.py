from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.article import ArticleKind, ArticleStatus
from app.models.source_fragment import ElementType


class BuildArticlesRequest(BaseModel):
    proposal_id: uuid.UUID | None = None


class BuildArticlesResponse(BaseModel):
    article_ids: list[uuid.UUID]
    count: int


class DeleteAllArticlesResponse(BaseModel):
    deleted_count: int


class ArticleAliasesUpdate(BaseModel):
    aliases: list[str]


class ArticleAliasesResponse(BaseModel):
    article_id: uuid.UUID
    aliases: list[str]


class ArticleBlockSchema(BaseModel):
    id: uuid.UUID
    fragment_id: uuid.UUID | None
    source_title: str | None = None
    source_context_label: str | None = None
    content: str
    element_type: ElementType
    position_index: int
    # Nullable for synthesized blocks; exposed so invariant checks can run
    # client-side too.
    source_position_index: int | None = None
    page_number: int | None
    section_path: str | None
    list_level: int | None = None
    group_id: uuid.UUID | None = None
    inline_spans: list | None = None
    meta_json: dict | None = None
    synthesized: bool = False
    heading_depth: int | None = None
    heading_label: str | None = None
    heading_prefix: str | None = None
    is_noise: bool = False
    link_ranges: list["ArticleInlineLink"] = Field(default_factory=list)

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


class ArticleLinkSummary(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    kind: ArticleKind = ArticleKind.ARTICLE
    structural_block_id: uuid.UUID | None = None
    suggested_section: str | None = None
    description: str | None = None
    score: float | None = None
    source_block_id: uuid.UUID | None = None


class ArticleTocItem(BaseModel):
    id: str
    block_id: uuid.UUID
    label: str
    level: int
    prefix: str | None = None


class ArticleInlineLink(BaseModel):
    start: int
    end: int
    article_id: uuid.UUID
    label: str


class SidebarArticleItem(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    kind: ArticleKind = ArticleKind.ARTICLE
    suggested_section: str | None = None
    status: ArticleStatus
    block_count: int


class ArticleSidebarGroup(BaseModel):
    key: str
    label: str
    kind: str
    block_id: uuid.UUID | None = None
    total_count: int
    articles: list[SidebarArticleItem] = Field(default_factory=list)
    groups: list["ArticleSidebarGroup"] = Field(default_factory=list)


class ArticleBreadcrumbItem(BaseModel):
    id: uuid.UUID
    name: str


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
    referenced_by: list[ArticleLinkSummary] = Field(default_factory=list)
    related_articles: list[ArticleLinkSummary] = Field(default_factory=list)
    revision_count: int = 0
    blocks: list[ArticleBlockSchema]
    toc: list[ArticleTocItem] = Field(default_factory=list)
    breadcrumb: list[ArticleBreadcrumbItem] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


ArticleBlockSchema.model_rebuild()
ArticleSidebarGroup.model_rebuild()
