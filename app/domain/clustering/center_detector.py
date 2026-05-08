from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import Source, SourceType
from app.models.source_fragment import ElementType, SourceFragment


MIN_CANDIDATE_CHARS = 180
MAX_FALLBACK_CHARS = 5000
_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*(?:[.)])?\s+\S+")


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

    by_source: dict[uuid.UUID, dict[str, Any]] = {}
    for fragment, source_type, source_title in rows:
        entry = by_source.setdefault(
            fragment.source_id,
            {
                "source_type": source_type.value,
                "source_title": source_title,
                "fragments": [],
            },
        )
        entry["fragments"].append(fragment)

    candidates: list[dict[str, Any]] = []
    for entry in by_source.values():
        fragments = sorted(entry["fragments"], key=lambda item: item.position_index)
        if entry["source_type"] == SourceType.PDF.value:
            candidates.extend(_pdf_structure_candidates(fragments, entry["source_title"]))
        else:
            candidates.extend(_generic_section_candidates(fragments))

    logger.info(f"Detected {len(candidates)} candidates for project {project_id}")
    return candidates


def _pdf_structure_candidates(
    fragments: list[SourceFragment],
    source_title: str | None,
) -> list[dict[str, Any]]:
    groups: list[list[SourceFragment]] = []
    current: list[SourceFragment] = []
    seen_non_title_heading = False
    source_title_key = _key(source_title)

    for fragment in fragments:
        if (
            fragment.element_type == ElementType.HEADING
            and source_title_key
            and _key(fragment.content) == source_title_key
        ):
            continue
        if _is_top_level_pdf_heading(fragment, source_title_key=source_title_key):
            if current:
                groups.append(current)
            current = [fragment]
            seen_non_title_heading = True
            continue

        if not current:
            current = [fragment]
        else:
            current.append(fragment)

    if current:
        groups.append(current)

    if not seen_non_title_heading:
        groups = _fallback_page_or_size_groups(fragments)

    return [_candidate_from_group(group, source="pdf_structure") for group in groups if _group_is_meaningful(group)]


def _generic_section_candidates(fragments: list[SourceFragment]) -> list[dict[str, Any]]:
    grouped: dict[str, list[SourceFragment]] = defaultdict(list)
    orphans: list[SourceFragment] = []
    for fragment in fragments:
        path = (fragment.section_path or "").strip()
        if path:
            grouped[_top_level(path)].append(fragment)
        else:
            orphans.append(fragment)

    candidates = [
        _candidate_from_group(group, source="section_structure", title=path)
        for path, group in sorted(
            grouped.items(),
            key=lambda item: min(fragment.position_index for fragment in item[1]),
        )
        if _group_is_meaningful(group)
    ]
    if _group_is_meaningful(orphans):
        candidates.append(_candidate_from_group(orphans, source="orphan_source"))
    return candidates


def _fallback_page_or_size_groups(fragments: list[SourceFragment]) -> list[list[SourceFragment]]:
    groups: list[list[SourceFragment]] = []
    current: list[SourceFragment] = []
    current_chars = 0
    current_page = None

    for fragment in fragments:
        fragment_chars = len(fragment.content or "")
        should_cut = (
            current
            and (
                current_chars + fragment_chars > MAX_FALLBACK_CHARS
                or (
                    current_page is not None
                    and fragment.page_number is not None
                    and fragment.page_number != current_page
                    and current_chars >= MIN_CANDIDATE_CHARS
                )
            )
        )
        if should_cut:
            groups.append(current)
            current = []
            current_chars = 0

        current.append(fragment)
        current_chars += fragment_chars
        current_page = fragment.page_number

    if current:
        groups.append(current)
    return groups


def _candidate_from_group(
    group: list[SourceFragment],
    *,
    source: str,
    title: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(group, key=lambda fragment: fragment.position_index)
    title = _clean_title(title or _group_title(ordered))
    return {
        "source_section_path": title,
        "fragments": ordered,
        "source": source,
        "source_type": None,
    }


def _group_title(group: list[SourceFragment]) -> str | None:
    for fragment in group:
        if fragment.element_type == ElementType.HEADING:
            text = " ".join(fragment.content.split()).strip()
            if text:
                return text
    first = next((fragment for fragment in group if _is_body(fragment)), None)
    if first is None:
        return None
    text = " ".join(first.content.split()).strip()
    return text[:96] if text else None


def _group_is_meaningful(group: list[SourceFragment]) -> bool:
    body = [fragment for fragment in group if _is_body(fragment)]
    if not body:
        return False
    if any(fragment.element_type in {ElementType.TABLE, ElementType.IMAGE} for fragment in body):
        return True
    if _looks_like_text_salad(body):
        return False
    return sum(len(fragment.content or "") for fragment in body) >= MIN_CANDIDATE_CHARS


def _is_body(fragment: SourceFragment) -> bool:
    if _looks_like_noise(fragment):
        return False
    return fragment.element_type in {
        ElementType.PARAGRAPH,
        ElementType.LIST_ITEM,
        ElementType.TABLE,
        ElementType.QUOTE,
        ElementType.CODE_BLOCK,
        ElementType.IMAGE,
        ElementType.FOOTNOTE,
        ElementType.FORMULA,
    }


def _is_top_level_pdf_heading(fragment: SourceFragment, *, source_title_key: str) -> bool:
    if fragment.element_type != ElementType.HEADING:
        return False
    text = " ".join(fragment.content.split()).strip()
    if not text:
        return False
    if source_title_key and _key(text) == source_title_key:
        return False
    if fragment.heading_level == 1:
        return True
    return bool(_NUMBERED_HEADING_RE.match(text) and "." not in text.split()[0].rstrip(".)"))


def _looks_like_noise(fragment: SourceFragment) -> bool:
    text = " ".join((fragment.content or "").split()).strip()
    if not text:
        return True
    lowered = text.casefold()
    if lowered in {"<eos>", "eos"}:
        return True
    if fragment.element_type in {ElementType.TABLE, ElementType.IMAGE, ElementType.FORMULA}:
        return False
    alpha = sum(char.isalpha() for char in text)
    if alpha == 0 and len(text) <= 24:
        return True
    return False


def _top_level(path: str) -> str:
    return path.split(" > ", 1)[0].strip()


def _key(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _clean_title(value: str | None) -> str | None:
    text = " ".join((value or "").split()).strip()
    if not text:
        return None
    text = re.sub(r"^\d+(?:\.\d+)*(?:[.)])?\s*", "", text).strip()
    return text or None


def _looks_like_text_salad(fragments: list[SourceFragment]) -> bool:
    if len(fragments) < 16:
        return False
    short = 0
    alpha_only = 0
    for fragment in fragments:
        words = re.findall(r"[A-Za-zА-Яа-я]+", fragment.content or "")
        if len(words) <= 2:
            short += 1
        compact = " ".join((fragment.content or "").split())
        if compact.isalpha() and len(compact) <= 24:
            alpha_only += 1
    return (short / len(fragments)) >= 0.72 and (alpha_only / len(fragments)) >= 0.5


# Compatibility helpers kept for older tests/callers during the refactor window.
# The active detector above no longer depends on these.
def _cluster_orphan_buckets(
    orphan_buckets: dict[tuple[uuid.UUID, str], list[SourceFragment]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for (_source_id, source_type), fragments in orphan_buckets.items():
        candidates.extend(_cluster_orphans(fragments, source_type=source_type))
    return candidates


def _cluster_orphans(
    fragments: list[SourceFragment],
    *,
    source_type: str,
) -> list[dict[str, Any]]:
    ordered = sorted(fragments, key=lambda fragment: fragment.position_index)
    if source_type == SourceType.PDF.value:
        groups = _fallback_page_or_size_groups(ordered)
    else:
        groups = [ordered]
    return [
        _candidate_from_group(group, source="orphan_source")
        for group in groups
        if _group_is_meaningful(group)
    ]


def _looks_like_pdf_major_heading(fragment: SourceFragment) -> bool:
    return _is_top_level_pdf_heading(fragment, source_title_key="")


def _candidate_group_path(
    source_type: str,
    source_title: str | None,
    path: str | None,
) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    if source_type != SourceType.PDF.value:
        return _top_level(raw)
    parts = [part.strip() for part in raw.split(" > ") if part.strip()]
    title_key = _key(source_title)
    while parts and _key(parts[0]) == title_key:
        parts.pop(0)
    return parts[0] if parts else ""


def _split_large_pdf_group(section_fragments: list[SourceFragment]) -> list[list[SourceFragment]]:
    groups: list[list[SourceFragment]] = []
    current: list[SourceFragment] = []
    for fragment in section_fragments:
        if _is_major_heading_for_compat(fragment) and current:
            groups.append(current)
            current = []
        current.append(fragment)
    if current:
        groups.append(current)
    return groups or [section_fragments]


def _merge_pdf_nested_section_groups(
    section_groups: dict[tuple[uuid.UUID, str, str], list[SourceFragment]],
) -> dict[tuple[uuid.UUID, str, str], list[SourceFragment]]:
    merged: dict[tuple[uuid.UUID, str, str], list[SourceFragment]] = {}
    by_source: dict[uuid.UUID, list[tuple[str, list[SourceFragment]]]] = defaultdict(list)

    for (source_id, source_type, section_path), fragments in section_groups.items():
        if source_type != SourceType.PDF.value:
            merged[(source_id, source_type, section_path)] = fragments
        else:
            by_source[source_id].append((section_path, fragments))

    for source_id, entries in by_source.items():
        entries = sorted(entries, key=lambda item: min(fragment.position_index for fragment in item[1]))
        parents: list[tuple[str, list[SourceFragment]]] = []
        for path, fragments in entries:
            prefix = _numeric_prefix(path)
            parent_index = None
            if prefix and "." in prefix:
                for index in range(len(parents) - 1, -1, -1):
                    parent_prefix = _numeric_prefix(parents[index][0])
                    if parent_prefix and prefix.startswith(parent_prefix + "."):
                        parent_index = index
                        break
            if parent_index is None:
                parents.append((path, list(fragments)))
            else:
                parents[parent_index][1].extend(fragments)
        for path, fragments in parents:
            merged[(source_id, SourceType.PDF.value, path)] = fragments
    return merged


def _is_major_heading_for_compat(fragment: SourceFragment) -> bool:
    element_type = getattr(fragment.element_type, "value", fragment.element_type)
    if element_type != ElementType.HEADING.value:
        return False
    text = " ".join((fragment.content or "").split()).strip()
    return bool((fragment.heading_level or 0) == 1 or _NUMBERED_HEADING_RE.match(text))


def _numeric_prefix(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(\d+(?:\.\d+)*)", value.strip())
    return match.group(1) if match else None
