from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.linking.alias_service import build_auto_aliases, normalise_alias_key
from app.domain.linking.edge_builder import (
    build_hard_edges,
    build_soft_edges,
    collect_hard_edge_specs,
)
from app.domain.linking.graph_panels import get_article_graph_panels
from app.models.alias import Alias, AliasSource
from app.models.article import Article, ArticleBlock
from app.models.graph_edge import GraphEdge


async def refresh_project_links(
    project_id: uuid.UUID,
    db: AsyncSession,
    *,
    soft_top_k: int = 5,
) -> None:
    result = await db.execute(
        select(Article)
        .where(Article.project_id == project_id)
        .options(
            selectinload(Article.blocks).selectinload(ArticleBlock.fragment),
            selectinload(Article.alias_records),
        )
        .order_by(Article.created_at, Article.id)
    )
    articles = list(result.scalars().all())
    if not articles:
        return

    article_ids = [article.id for article in articles]
    manual_aliases_by_article = {
        article.id: [
            alias.text
            for alias in article.alias_records
            if alias.source != AliasSource.AUTO
        ]
        for article in articles
    }

    await db.execute(
        delete(GraphEdge).where(GraphEdge.from_article_id.in_(article_ids))
    )
    await db.execute(
        delete(Alias).where(
            Alias.article_id.in_(article_ids),
            Alias.source == AliasSource.AUTO,
        )
    )
    await db.flush()

    generated_alias_rows: list[Alias] = []
    alias_map: dict[uuid.UUID, list[str]] = {}
    for article in articles:
        auto_aliases = build_auto_aliases(article.title)
        merged: list[str] = []
        seen: set[str] = set()
        for alias in [*auto_aliases, *manual_aliases_by_article[article.id]]:
            key = normalise_alias_key(alias)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(alias)
        alias_map[article.id] = merged
        article.aliases = merged or None
        generated_alias_rows.extend(
            Alias(
                article_id=article.id,
                text=alias,
                confidence=1.0,
                source=AliasSource.AUTO,
            )
            for alias in auto_aliases
        )

    if generated_alias_rows:
        db.add_all(generated_alias_rows)
        await db.flush()

    edges = [
        *build_hard_edges(articles, alias_map),
        *build_soft_edges(articles, soft_top_k=soft_top_k),
    ]
    if edges:
        db.add_all(edges)
        await db.flush()
