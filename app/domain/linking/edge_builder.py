from __future__ import annotations

import math
import re
import statistics
import uuid

from app.domain.linking.alias_service import collapse_ws
from app.models.article import Article
from app.models.graph_edge import EdgeKind, GraphEdge


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
            block_text = collapse_ws(block.content)
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


def build_hard_edges(
    articles: list[Article],
    alias_map: dict[uuid.UUID, list[str]],
) -> list[GraphEdge]:
    return [
        GraphEdge(
            from_article_id=from_article_id,
            to_article_id=to_article_id,
            kind=EdgeKind.HARD,
            source_block_id=source_block_id,
            score=1.0,
        )
        for from_article_id, to_article_id, source_block_id in collect_hard_edge_specs(
            articles,
            alias_map,
        )
    ]


def build_soft_edges(
    articles: list[Article],
    *,
    soft_top_k: int,
) -> list[GraphEdge]:
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

    return soft_edges
