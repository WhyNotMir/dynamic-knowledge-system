from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import numpy as np
from loguru import logger
from sklearn.cluster import KMeans
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import Source
from app.models.source_fragment import SourceFragment


MIN_CANDIDATE_CHARS = 100
MIN_ORPHAN_CLUSTER = 3


async def detect_centers(project_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(SourceFragment)
        .join(Source, Source.id == SourceFragment.source_id)
        .where(Source.project_id == project_id)
        .order_by(SourceFragment.position_index)
    )
    fragments = list(result.scalars().all())

    if not fragments:
        logger.warning(f"No fragments for project {project_id}")
        return []

    section_groups: dict[str, list[SourceFragment]] = defaultdict(list)
    orphans: list[SourceFragment] = []

    for fragment in fragments:
        path = (fragment.section_path or "").strip()
        if path:
            section_groups[path].append(fragment)
        else:
            orphans.append(fragment)

    candidates: list[dict[str, Any]] = []

    for section_path, section_fragments in section_groups.items():
        total_chars = sum(len(fragment.content) for fragment in section_fragments)
        if total_chars < MIN_CANDIDATE_CHARS:
            orphans.extend(section_fragments)
            continue

        candidates.append(
            {
                "source_section_path": section_path,
                "fragments": section_fragments,
                "source": "section_structure",
            }
        )

    if orphans:
        candidates.extend(_cluster_orphans(orphans))

    logger.info(f"Detected {len(candidates)} candidates for project {project_id}")
    return candidates


def _cluster_orphans(orphans: list[SourceFragment]) -> list[dict[str, Any]]:
    with_embeddings = [fragment for fragment in orphans if fragment.embedding is not None]
    without_embeddings = [fragment for fragment in orphans if fragment.embedding is None]

    if len(with_embeddings) < MIN_ORPHAN_CLUSTER:
        return [
            {
                "source_section_path": None,
                "fragments": sorted(orphans, key=lambda fragment: fragment.position_index),
                "source": "orphan",
            }
        ]

    embeddings = np.array([fragment.embedding for fragment in with_embeddings])
    n_clusters = max(1, min(len(with_embeddings) // 5, 8))
    labels = KMeans(n_clusters=n_clusters, n_init=10, random_state=42).fit_predict(embeddings)

    groups: dict[int, list[SourceFragment]] = defaultdict(list)
    for fragment, label in zip(with_embeddings, labels):
        groups[int(label)].append(fragment)

    if without_embeddings and groups:
        largest_group = max(groups, key=lambda key: len(groups[key]))
        groups[largest_group].extend(without_embeddings)

    return [
        {
            "source_section_path": None,
            "fragments": sorted(group_fragments, key=lambda fragment: fragment.position_index),
            "source": "embedding_cluster",
        }
        for group_fragments in groups.values()
        if group_fragments
    ]