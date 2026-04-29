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
_PDF_PAD_RE = re.compile(r"<pad>", re.IGNORECASE)


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

    DOCX benefits from H1-centric grouping: one candidate per top-level
    section, with H2/H3 preserved as internal headings.
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

    return bool(fragment.heading_level == 1 and len(text) < 90)


def _looks_like_noise_fragment(fragment: SourceFragment) -> bool:
    text = " ".join(fragment.content.split()).strip()
    if not text:
        return True

    lowered = text.casefold()
    if lowered in {"<eos>", "eos"}:
        return True
    if _PDF_METADATA_RE.search(text):
        return True
    if _PDF_PAD_RE.search(text):
        return True

    alpha = sum(char.isalpha() for char in text)
    digits = sum(char.isdigit() for char in text)
    if len(text) <= 2 and alpha == 0:
        return True
    if alpha == 0 and digits > 0:
        return True
    if re.fullmatch(r"[\W\d_]+", text):
        return True

    return False


def _candidate_body_fragments(fragments: list[SourceFragment]) -> list[SourceFragment]:
    body_types = {"paragraph", "list_item", "table", "quote", "code_block", "image"}
    return [
        fragment
        for fragment in fragments
        if fragment.element_type.value in body_types and not _looks_like_noise_fragment(fragment)
    ]


def _clean_candidate_fragments(fragments: list[SourceFragment]) -> list[SourceFragment]:
    return [
        fragment
        for fragment in fragments
        if not _looks_like_noise_fragment(fragment)
    ]


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


def _numeric_heading_prefix(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(\d+(?:\.\d+)*)\s+", value.strip())
    if not match:
        return None
    return match.group(1)


def _merge_pdf_nested_section_groups(
    section_groups: dict[tuple[uuid.UUID, str, str], list[SourceFragment]],
) -> dict[tuple[uuid.UUID, str, str], list[SourceFragment]]:
    merged: dict[tuple[uuid.UUID, str, str], list[SourceFragment]] = {}
    by_source: dict[uuid.UUID, list[tuple[str, list[SourceFragment]]]] = defaultdict(list)

    for (source_id, source_type, section_path), fragments in section_groups.items():
        if source_type != SourceType.PDF.value:
            merged[(source_id, source_type, section_path)] = fragments
            continue
        by_source[source_id].append((section_path, fragments))

    for source_id, entries in by_source.items():
        entries = sorted(
            entries,
            key=lambda item: min(fragment.position_index for fragment in item[1]),
        )
        merged_entries: list[tuple[str, list[SourceFragment]]] = []

        for section_path, fragments in entries:
            section_prefix = _numeric_heading_prefix(section_path)
            parent_index = None

            if section_prefix and "." in section_prefix:
                for index in range(len(merged_entries) - 1, -1, -1):
                    parent_path, parent_fragments = merged_entries[index]
                    parent_prefix = _numeric_heading_prefix(parent_path)
                    if not parent_prefix:
                        continue
                    if section_prefix.startswith(parent_prefix + "."):
                        parent_last_pos = max(
                            fragment.position_index for fragment in parent_fragments
                        )
                        section_first_pos = min(
                            fragment.position_index for fragment in fragments
                        )
                        if section_first_pos >= parent_last_pos:
                            parent_index = index
                            break

            if parent_index is None:
                merged_entries.append((section_path, list(fragments)))
            else:
                merged_entries[parent_index][1].extend(fragments)

        for section_path, fragments in merged_entries:
            merged[(source_id, SourceType.PDF.value, section_path)] = fragments

    return merged


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
    orphan_buckets: dict[tuple[uuid.UUID, str], list[SourceFragment]] = defaultdict(list)

    for fragment, source_type, source_title in rows:
        path = _candidate_group_path(
            source_type.value,
            source_title,
            fragment.section_path,
        )
        if path:
            section_groups[(fragment.source_id, source_type.value, path)].append(fragment)
        else:
            orphan_buckets[(fragment.source_id, source_type.value)].append(fragment)

    section_groups = _merge_pdf_nested_section_groups(section_groups)

    candidates: list[dict[str, Any]] = []

    for (_, source_type, section_path), section_fragments in section_groups.items():
        grouped_fragments = (
            _split_large_pdf_group(section_fragments)
            if source_type == SourceType.PDF.value
            else [section_fragments]
        )

        for candidate_fragments in grouped_fragments:
            cleaned_fragments = _clean_candidate_fragments(candidate_fragments)
            body_fragments = _candidate_body_fragments(cleaned_fragments)
            total_chars = sum(len(fragment.content) for fragment in body_fragments)
            min_chars = (
                MIN_PDF_CANDIDATE_CHARS
                if source_type == SourceType.PDF.value
                else MIN_CANDIDATE_CHARS
            )
            if not cleaned_fragments or not body_fragments or total_chars < min_chars:
                orphan_buckets[(candidate_fragments[0].source_id, source_type)].extend(cleaned_fragments)
                continue

            candidates.append(
                {
                    "source_section_path": _group_section_label(candidate_fragments, section_path),
                    "fragments": cleaned_fragments,
                    "source": "section_structure",
                }
            )

    if orphan_buckets:
        candidates.extend(_cluster_orphan_buckets(orphan_buckets))

    logger.info(f"Detected {len(candidates)} candidates for project {project_id}")
    return candidates


def _cluster_orphan_buckets(
    orphan_buckets: dict[tuple[uuid.UUID, str], list[SourceFragment]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for (_, source_type), fragments in orphan_buckets.items():
        candidates.extend(_cluster_orphans(fragments, source_type=source_type))
    return candidates


def _cluster_orphans(
    orphans: list[SourceFragment],
    *,
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    cleaned_orphans = _clean_candidate_fragments(orphans)
    if not cleaned_orphans:
        return []

    if source_type == SourceType.PDF.value:
        body_fragments = _candidate_body_fragments(cleaned_orphans)
        if not body_fragments:
            return []
        total_chars = sum(len(fragment.content) for fragment in body_fragments)
        if total_chars < MIN_PDF_CANDIDATE_CHARS:
            return []
        return [
            {
                "source_section_path": None,
                "fragments": sorted(cleaned_orphans, key=lambda fragment: fragment.position_index),
                "source": "pdf_orphan_source",
            }
        ]

    with_embeddings = [fragment for fragment in cleaned_orphans if fragment.embedding is not None]
    without_embeddings = [fragment for fragment in cleaned_orphans if fragment.embedding is None]

    if len(with_embeddings) < MIN_ORPHAN_CLUSTER:
        return [
            {
                "source_section_path": None,
                "fragments": sorted(cleaned_orphans, key=lambda fragment: fragment.position_index),
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
