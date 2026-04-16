from __future__ import annotations
import uuid
from collections import defaultdict
from typing import Any
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sklearn.cluster import KMeans
from loguru import logger

from app.models import SourceFragment

MIN_CANDIDATE_CHARS = 100
MIN_ORPHAN_CLUSTER = 3


async def detect_centers(project_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(SourceFragment)
        .where(SourceFragment.project_id == project_id)
        .order_by(SourceFragment.position_index)
    )
    fragments = result.scalars().all()

    if not fragments:
        logger.warning(f"No fragments for project {project_id}")
        return []

    section_groups: dict[str, list] = defaultdict(list)
    orphans: list = []

    for f in fragments:
        path = (f.section_path or "").strip()
        if path:
            section_groups[path].append(f)
        else:
            orphans.append(f)

    candidates: list[dict[str, Any]] = []

    for section_path, frags in section_groups.items():
        total_chars = sum(len(f.content) for f in frags)
        if total_chars < MIN_CANDIDATE_CHARS:
            orphans.extend(frags)
            continue
        candidates.append({
            "source_section_path": section_path,
            "fragments": frags,
            "source": "section_structure",
        })

    if orphans:
        candidates.extend(_cluster_orphans(orphans))

    logger.info(f"Detected {len(candidates)} candidates for project {project_id}")
    return candidates


def _cluster_orphans(orphans: list) -> list[dict[str, Any]]:
    with_emb = [f for f in orphans if f.embedding is not None]
    without_emb = [f for f in orphans if f.embedding is None]

    if len(with_emb) < MIN_ORPHAN_CLUSTER:
        return [{"source_section_path": None, "fragments": orphans, "source": "orphan"}]

    embeddings = np.array([f.embedding for f in with_emb])
    n_clusters = max(1, min(len(with_emb) // 5, 8))
    labels = KMeans(n_clusters=n_clusters, n_init=10, random_state=42).fit_predict(embeddings)

    groups: dict[int, list] = defaultdict(list)
    for frag, label in zip(with_emb, labels):
        groups[int(label)].append(frag)

    if without_emb and groups:
        largest = max(groups, key=lambda k: len(groups[k]))
        groups[largest].extend(without_emb)

    return [
        {
            "source_section_path": None,
            "fragments": sorted(frags, key=lambda f: f.position_index),
            "source": "embedding_cluster",
        }
        for frags in groups.values() if frags
    ]