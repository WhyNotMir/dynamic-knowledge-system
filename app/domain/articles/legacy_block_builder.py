from __future__ import annotations

import re

from app.domain.articles.article_text import (
    is_meaningful_body_fragment,
    looks_like_noise_fragment,
    normalise_heading_text,
)
from app.models.article_candidate import ArticleCandidate
from app.models.source_fragment import ElementType, SourceFragment

PreparedBlock = tuple[SourceFragment, dict]
_TABLE_CAPTION_RE = re.compile(r"^Table\s+(\d+)\b", re.IGNORECASE)
_FIGURE_CAPTION_RE = re.compile(r"^(?:Figure|Fig\.)\s+(\d+)\b", re.IGNORECASE)
_TABLE_REF_TEMPLATE = r"\bTable\s+{number}\b"
_FIGURE_REF_TEMPLATE = r"\b(?:Figure|Fig\.)\s+{number}\b"


def prepare_article_blocks(
    candidate: ArticleCandidate,
    *,
    extra_fragments: list[SourceFragment] | None = None,
) -> list[PreparedBlock]:
    candidate_fragments = sorted(
        candidate.candidate_fragments,
        key=lambda item: item.position_index,
    )
    prepared_blocks: list[PreparedBlock] = []
    candidate_title_key = normalise_heading_text(candidate.title)
    source_heading_key = normalise_heading_text(candidate.source_section_path)
    skipped_title_heading = False

    fragments = [item.fragment for item in candidate_fragments if item.fragment is not None]
    if extra_fragments:
        fragments.extend(extra_fragments)

    seen_fragment_ids: set = set()
    unique_fragments: list[SourceFragment] = []
    for fragment in fragments:
        if fragment.id in seen_fragment_ids:
            continue
        seen_fragment_ids.add(fragment.id)
        unique_fragments.append(fragment)

    for fragment in _order_article_fragments(unique_fragments):
        if fragment is None:
            continue
        if looks_like_noise_fragment(fragment):
            continue

        if (
            not skipped_title_heading
            and fragment.element_type == ElementType.HEADING
            and fragment.heading_level == 1
        ):
            fragment_heading_key = normalise_heading_text(fragment.content)
            if fragment_heading_key and fragment_heading_key in {
                candidate_title_key,
                source_heading_key,
            }:
                skipped_title_heading = True
                continue

        prepared_blocks.append(
            (
                fragment,
                {
                    "fragment_id": fragment.id,
                    "content": fragment.content,
                    "element_type": fragment.element_type,
                    "position_index": len(prepared_blocks),
                    "source_position_index": fragment.position_index,
                    "page_number": fragment.page_number,
                    "section_path": fragment.section_path,
                    "list_level": fragment.list_level,
                    "group_id": fragment.group_id,
                    "inline_spans": fragment.inline_spans,
                    "meta_json": fragment.meta_json,
                    "synthesized": False,
                },
            )
        )

    return prepared_blocks


def _order_article_fragments(fragments: list[SourceFragment]) -> list[SourceFragment]:
    ordered = sorted(fragments, key=lambda item: item.position_index)
    if len(ordered) < 3:
        return ordered

    groups = _caption_target_groups(ordered)
    if not groups:
        return ordered

    moved_ids: set = set()
    insert_after: dict[int, list[SourceFragment]] = {}
    for group_id, group in groups.items():
        anchor_index = _floating_caption_group_anchor_index(ordered, group)
        if anchor_index is None:
            continue
        moved_ids.update(fragment.id for fragment in group)
        insert_after.setdefault(anchor_index, []).extend(group)

    if not moved_ids:
        return ordered

    reordered: list[SourceFragment] = []
    for index, fragment in enumerate(ordered):
        if fragment.id not in moved_ids:
            reordered.append(fragment)
        for floated in insert_after.get(index, []):
            reordered.append(floated)
    return reordered


def _caption_target_groups(fragments: list[SourceFragment]) -> dict[str, list[SourceFragment]]:
    groups: dict[str, list[SourceFragment]] = {}
    for fragment in fragments:
        group_id = (fragment.meta_json or {}).get("caption_group_id")
        if isinstance(group_id, str) and group_id:
            groups.setdefault(group_id, []).append(fragment)
    return {
        group_id: sorted(group, key=lambda item: item.position_index)
        for group_id, group in groups.items()
        if any(fragment.element_type == ElementType.CAPTION for fragment in group)
        and any(fragment.element_type in {ElementType.TABLE, ElementType.IMAGE} for fragment in group)
    }


def _floating_caption_group_anchor_index(
    ordered: list[SourceFragment],
    group: list[SourceFragment],
) -> int | None:
    caption = next((fragment for fragment in group if fragment.element_type == ElementType.CAPTION), None)
    if caption is None:
        return None
    reference_pattern = _caption_reference_pattern(caption.content)
    if reference_pattern is None:
        return None

    group_ids = {fragment.id for fragment in group}
    group_positions = [fragment.position_index for fragment in group if fragment.position_index is not None]
    if not group_positions:
        return None
    group_end = max(group_positions)

    first_heading_after_group: int | None = None
    for index, fragment in enumerate(ordered):
        if fragment.id in group_ids:
            continue
        if fragment.position_index is None or fragment.position_index <= group_end:
            continue
        if first_heading_after_group is None and fragment.element_type == ElementType.HEADING:
            first_heading_after_group = index
        if first_heading_after_group is None:
            continue
        if reference_pattern.search(fragment.content or ""):
            return index
    return None


def _caption_reference_pattern(caption_text: str) -> re.Pattern[str] | None:
    stripped = " ".join((caption_text or "").split()).strip()
    if match := _TABLE_CAPTION_RE.match(stripped):
        return re.compile(_TABLE_REF_TEMPLATE.format(number=re.escape(match.group(1))), re.IGNORECASE)
    if match := _FIGURE_CAPTION_RE.match(stripped):
        return re.compile(_FIGURE_REF_TEMPLATE.format(number=re.escape(match.group(1))), re.IGNORECASE)
    return None


def has_meaningful_body(prepared_blocks: list[PreparedBlock]) -> bool:
    return any(is_meaningful_body_fragment(fragment) for fragment, _ in prepared_blocks)
