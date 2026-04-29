from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article
from app.models.graph_edge import EdgeKind, GraphEdge


async def get_article_graph_panels(
    article_id: uuid.UUID,
    db: AsyncSession,
    *,
    related_limit: int = 5,
) -> tuple[list[dict], list[dict]]:
    incoming_result = await db.execute(
        select(GraphEdge, Article)
        .join(Article, Article.id == GraphEdge.from_article_id)
        .where(
            GraphEdge.to_article_id == article_id,
            GraphEdge.kind == EdgeKind.HARD,
        )
        .order_by(GraphEdge.created_at, Article.created_at)
    )
    referenced_by: list[dict] = []
    seen_incoming: set[uuid.UUID] = set()
    for edge, article in incoming_result.all():
        if article.id in seen_incoming:
            continue
        seen_incoming.add(article.id)
        referenced_by.append(
            {
                "id": article.id,
                "title": article.title,
                "slug": article.slug,
                "kind": article.kind,
                "structural_block_id": article.structural_block_id,
                "suggested_section": article.suggested_section,
                "description": article.description,
                "score": edge.score,
                "source_block_id": edge.source_block_id,
            }
        )

    outgoing_result = await db.execute(
        select(GraphEdge, Article)
        .join(Article, Article.id == GraphEdge.to_article_id)
        .where(
            GraphEdge.from_article_id == article_id,
            GraphEdge.kind == EdgeKind.SOFT,
        )
        .order_by(GraphEdge.score.desc().nullslast(), Article.created_at)
    )
    related_articles: list[dict] = []
    seen_related: set[uuid.UUID] = set()
    for edge, article in outgoing_result.all():
        if article.id in seen_related:
            continue
        seen_related.add(article.id)
        related_articles.append(
            {
                "id": article.id,
                "title": article.title,
                "slug": article.slug,
                "kind": article.kind,
                "structural_block_id": article.structural_block_id,
                "suggested_section": article.suggested_section,
                "description": article.description,
                "score": edge.score,
                "source_block_id": None,
            }
        )
        if len(related_articles) >= related_limit:
            break

    return referenced_by, related_articles
