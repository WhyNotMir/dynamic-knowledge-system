from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

import numpy as np
from loguru import logger
from sklearn.cluster import KMeans
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import Source, SourceType
from app.models.source_fragment import SourceFragment


MIN_CANDIDATE_CHARS = 100
MIN_ORPHAN_CLUSTER = 3
MIN_PDF_CANDIDATE_CHARS = 300
LARGE_PDF_GROUP_FRAGMENTS = 40
LARGE_PDF_GROUP_CHARS = 6000
_PDF_MAJOR_HEADING_RE = re.compile(r"^\d+\s+[A-Za-z]")
_PDF_METADATA_RE = re.compile(r"(arxiv:|\[cs\.|gnmt|en-de|en-fr|wsj)", re.IGNORECASE)


def _top_level_section(path: str | None) -> str:
    if not path:
        return ""
    return path.split(" > ", 1)[0].strip()


def _normalise_key(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip().casefold()


def _candidate_group_path(
    source_type: str,
    source_title: str | None,
    path: str | None,
) -> str:
    """Return the grouping key for a fragment within one source.

    DOCX benefits from Phase 1's H1-centric grouping: one candidate per
    top-level section, with H2/H3 preserved as internal headings.
    PDFs are much noisier because heading detection is heuristic; grouping
    by the first semantic section below the document title produces larger,
    more coherent multi-topic candidates instead of dozens of tiny heading
    shards.
    """
    raw = (path or "").strip()
    if not raw:
        return ""
    if source_type == SourceType.DOCX.value:
        return _top_level_section(raw)

    parts = [part.strip() for part in raw.split(" > ") if part.strip()]
    if not parts:
        return ""

    title_key = _normalise_key(source_title)
    while parts and _normalise_key(parts[0]) == title_key:
        parts.pop(0)
    if not parts:
        return ""

    return parts[0]


def _looks_like_pdf_major_heading(fragment: SourceFragment) -> bool:
    if fragment.element_type.value != "heading":
        return False

    text = " ".join(fragment.content.split()).strip()
    if not text:
        return False
    if _PDF_METADATA_RE.search(text):
        return False
    if _PDF_MAJOR_HEADING_RE.match(text):
        return True

    return bool(fragment.heading_level and fragment.heading_level <= 2 and len(text) < 90)


def _split_large_pdf_group(section_fragments: list[SourceFragment]) -> list[list[SourceFragment]]:
    """Split giant PDF candidates by major headings when possible.

    Some PDFs still end up with one huge section_path bucket because heading
    detection on the raw blocks is imperfect. When the group is obviously too
    large, we recover a more article-like shape by cutting on major numbered
    headings such as "1 Introduction", "2 Background", "3 Model Architecture".
    """
    if (
        len(section_fragments) < LARGE_PDF_GROUP_FRAGMENTS
        and sum(len(fragment.content) for fragment in section_fragments) < LARGE_PDF_GROUP_CHARS
    ):
        return [section_fragments]

    groups: list[list[SourceFragment]] = []
    current: list[SourceFragment] = []

    for fragment in section_fragments:
        if _looks_like_pdf_major_heading(fragment) and current:
            groups.append(current)
            current = []
        current.append(fragment)

    if current:
        groups.append(current)

    if len(groups) <= 1:
        return [section_fragments]

    meaningful = [
        group
        for group in groups
        if sum(len(fragment.content) for fragment in group) >= MIN_PDF_CANDIDATE_CHARS
    ]
    return meaningful or [section_fragments]


def _group_section_label(section_fragments: list[SourceFragment], fallback: str) -> str:
    for fragment in section_fragments:
        if _looks_like_pdf_major_heading(fragment):
            return " ".join(fragment.content.split()).strip()
    return fallback


async def detect_centers(project_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        select(SourceFragment, Source.source_type, Source.title)
        .join(Source, Source.id == SourceFragment.source_id)
        .where(Source.project_id == project_id)
        .order_by(SourceFragment.source_id, SourceFragment.position_index)
    )
    rows = list(result.all())

    if not rows:
        logger.warning(f"No fragments for project {project_id}")
        return []

    section_groups: dict[tuple[uuid.UUID, str, str], list[SourceFragment]] = defaultdict(list)
    orphans: list[SourceFragment] = []

    for fragment, source_type, source_title in rows:
        path = _candidate_group_path(
            source_type.value,
            source_title,
            fragment.section_path,
        )
        if path:
            section_groups[(fragment.source_id, source_type.value, path)].append(fragment)
        else:
            orphans.append(fragment)

    candidates: list[dict[str, Any]] = []

    for (_, source_type, section_path), section_fragments in section_groups.items():
        grouped_fragments = (
            _split_large_pdf_group(section_fragments)
            if source_type == SourceType.PDF.value
            else [section_fragments]
        )

        for candidate_fragments in grouped_fragments:
            total_chars = sum(len(fragment.content) for fragment in candidate_fragments)
            min_chars = (
                MIN_PDF_CANDIDATE_CHARS
                if source_type == SourceType.PDF.value
                else MIN_CANDIDATE_CHARS
            )
            if total_chars < min_chars:
                orphans.extend(candidate_fragments)
                continue

            candidates.append(
                {
                    "source_section_path": _group_section_label(candidate_fragments, section_path),
                    "fragments": candidate_fragments,
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
