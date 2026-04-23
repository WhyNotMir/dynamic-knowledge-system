from __future__ import annotations

import math
import re
import statistics
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.alias import Alias, AliasSource
from app.models.article import Article, ArticleBlock
from app.models.graph_edge import EdgeKind, GraphEdge

_GENERIC_TITLES = {
    "abstract",
    "background",
    "conclusion",
    "discussion",
    "general",
    "introduction",
    "methods",
    "references",
    "results",
    "summary",
}

_MULTISPACE_RE = re.compile(r"\s+")


def _collapse_ws(value: str) -> str:
    return _MULTISPACE_RE.sub(" ", value).strip()


def _normalise_alias_key(value: str) -> str:
    return _collapse_ws(value).casefold()


def build_auto_aliases(title: str) -> list[str]:
    """Cheap deterministic alias seed.

    Phase 3 later adds AliasAgent; for now we keep this conservative:
    title itself and an optional colon-stripped lead. Acronyms were
    intentionally removed because they produced too many noisy hard
    links in the UI (e.g. short caps-like aliases matching irrelevant
    content). Very generic one-word titles are suppressed so the Linker
    doesn't create noisy hard edges around "Introduction", "Methods", etc.
    """
    collapsed = _collapse_ws(title)
    if not collapsed:
        return []

    aliases: list[str] = []
    lowered = collapsed.casefold()
    if lowered not in _GENERIC_TITLES:
        aliases.append(collapsed)

    if ":" in collapsed:
        lead = _collapse_ws(collapsed.split(":", 1)[0])
        if lead and lead.casefold() not in _GENERIC_TITLES:
            aliases.append(lead)

    seen: set[str] = set()
    unique_aliases: list[str] = []
    for alias in aliases:
        key = _normalise_alias_key(alias)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_aliases.append(alias)
    return unique_aliases


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    if any(char.isalnum() for char in alias):
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def _mean_embedding(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    width = len(vectors[0])
    if width == 0:
        return None
    return [
        statistics.fmean(vector[index] for vector in vectors)
        for index in range(width)
    ]


def collect_hard_edge_specs(
    articles: list[Article],
    alias_map: dict[uuid.UUID, list[str]],
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    alias_patterns_by_article = {
        article_id: [(_alias_pattern(alias), alias) for alias in aliases]
        for article_id, aliases in alias_map.items()
        if aliases
    }

    hard_edge_specs: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []
    for source_article in articles:
        for block in source_article.blocks:
            block_text = _collapse_ws(block.content)
            if not block_text:
                continue
            matched_targets: set[uuid.UUID] = set()
            for target_article_id, patterns in alias_patterns_by_article.items():
                if target_article_id == source_article.id:
                    continue
                for pattern, _alias in patterns:
                    if pattern.search(block_text):
                        matched_targets.add(target_article_id)
                        break
            for target_article_id in sorted(matched_targets):
                hard_edge_specs.append((source_article.id, target_article_id, block.id))

    return hard_edge_specs


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
        merged: list[str] = []
        seen: set[str] = set()
        for alias in [*build_auto_aliases(article.title), *manual_aliases_by_article[article.id]]:
            key = _normalise_alias_key(alias)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(alias)
        alias_map[article.id] = merged
        article.aliases = merged or None
        for alias in build_auto_aliases(article.title):
            generated_alias_rows.append(
                Alias(
                    article_id=article.id,
                    text=alias,
                    confidence=1.0,
                    source=AliasSource.AUTO,
                )
            )

    if generated_alias_rows:
        db.add_all(generated_alias_rows)
        await db.flush()

    hard_edges: list[GraphEdge] = []
    for from_article_id, to_article_id, source_block_id in collect_hard_edge_specs(articles, alias_map):
        hard_edges.append(
            GraphEdge(
                from_article_id=from_article_id,
                to_article_id=to_article_id,
                kind=EdgeKind.HARD,
                source_block_id=source_block_id,
                score=1.0,
            )
        )

    article_embeddings: dict[uuid.UUID, list[float]] = {}
    for article in articles:
        vectors = [
            list(block.fragment.embedding)
            for block in article.blocks
            if block.fragment is not None and block.fragment.embedding is not None
        ]
        mean_embedding = _mean_embedding(vectors)
        if mean_embedding is not None:
            article_embeddings[article.id] = mean_embedding

    soft_edges: list[GraphEdge] = []
    for source_article in articles:
        source_embedding = article_embeddings.get(source_article.id)
        if source_embedding is None:
            continue
        scored: list[tuple[float, uuid.UUID]] = []
        for target_article in articles:
            if target_article.id == source_article.id:
                continue
            target_embedding = article_embeddings.get(target_article.id)
            if target_embedding is None:
                continue
            score = _cosine_similarity(source_embedding, target_embedding)
            if score is None:
                continue
            scored.append((score, target_article.id))
        for score, target_article_id in sorted(scored, reverse=True)[:soft_top_k]:
            soft_edges.append(
                GraphEdge(
                    from_article_id=source_article.id,
                    to_article_id=target_article_id,
                    kind=EdgeKind.SOFT,
                    source_block_id=None,
                    score=score,
                )
            )

    if hard_edges or soft_edges:
        db.add_all([*hard_edges, *soft_edges])
        await db.flush()


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
