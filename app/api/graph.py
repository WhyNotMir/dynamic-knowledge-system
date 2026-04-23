from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.domain.linking.service import collect_hard_edge_specs
from app.models.article import Article
from app.models.graph_edge import EdgeKind, GraphEdge
from app.models.project import Project
from app.schemas.graph import GraphEdgeSchema, GraphNodeSchema, GraphPayloadSchema

router = APIRouter(prefix="/projects/{project_id}/graph", tags=["graph"])


@router.get("", response_model=GraphPayloadSchema)
async def get_project_graph(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    articles = (
        await db.execute(
            select(Article)
            .where(Article.project_id == project_id)
            .options(selectinload(Article.blocks))
            .order_by(Article.created_at, Article.id)
        )
    ).scalars().all()
    article_ids = [article.id for article in articles]

    edges: list[GraphEdge] = []
    if article_ids:
        edges = (
            await db.execute(
                select(GraphEdge).where(
                    GraphEdge.from_article_id.in_(article_ids),
                    GraphEdge.to_article_id.in_(article_ids),
                )
            )
        ).scalars().all()

    alias_map: dict[uuid.UUID, list[str]] = {}
    for article in articles:
        merged: list[str] = []
        seen: set[str] = set()
        for alias in [article.title, *(article.aliases or [])]:
            key = alias.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(alias)
        alias_map[article.id] = merged

    computed_hard_edges = [
        GraphEdgeSchema(
            id=uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hard:{from_article_id}:{to_article_id}:{source_block_id}",
            ),
            from_article_id=from_article_id,
            to_article_id=to_article_id,
            kind=EdgeKind.HARD,
            source_block_id=source_block_id,
            score=1.0,
        )
        for from_article_id, to_article_id, source_block_id in collect_hard_edge_specs(articles, alias_map)
    ]

    persisted_soft_edges = [
        GraphEdgeSchema(
            id=edge.id,
            from_article_id=edge.from_article_id,
            to_article_id=edge.to_article_id,
            kind=edge.kind,
            source_block_id=edge.source_block_id,
            score=edge.score,
        )
        for edge in edges
        if edge.kind == EdgeKind.SOFT
    ]

    return GraphPayloadSchema(
        nodes=[
            GraphNodeSchema(
                id=article.id,
                title=article.title,
                slug=article.slug,
                kind=article.kind,
                structural_block_id=article.structural_block_id,
                suggested_section=article.suggested_section,
                description=article.description,
                aliases=article.aliases,
            )
            for article in articles
        ],
        edges=[*computed_hard_edges, *persisted_soft_edges],
    )
