from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.article import ArticleKind
from app.models.graph_edge import EdgeKind


class GraphNodeSchema(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    kind: ArticleKind
    structural_block_id: uuid.UUID | None = None
    suggested_section: str | None = None
    description: str | None = None
    aliases: list[str] | None = None


class GraphEdgeSchema(BaseModel):
    id: uuid.UUID
    from_article_id: uuid.UUID
    to_article_id: uuid.UUID
    kind: EdgeKind
    source_block_id: uuid.UUID | None = None
    score: float | None = None


class GraphPayloadSchema(BaseModel):
    nodes: list[GraphNodeSchema]
    edges: list[GraphEdgeSchema]
