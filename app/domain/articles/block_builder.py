from __future__ import annotations

from app.domain.articles.article_text import (
    is_meaningful_body_fragment,
    looks_like_noise_fragment,
    normalise_heading_text,
)
from app.models.article_candidate import ArticleCandidate
from app.models.source_fragment import ElementType, SourceFragment

PreparedBlock = tuple[SourceFragment | None, dict]
INTRO_HEADING_LABEL = "Overview"


def prepare_article_blocks(candidate: ArticleCandidate) -> list[PreparedBlock]:
    """Materialise candidate fragments without recovery or reordering."""
    candidate_title_key = normalise_heading_text(candidate.title)
    source_heading_key = normalise_heading_text(candidate.source_section_path)
    fragments = [
        item.fragment
        for item in sorted(candidate.candidate_fragments, key=lambda item: item.position_index)
        if item.fragment is not None
    ]

    prepared_blocks: list[PreparedBlock] = []
    skipped_title_heading = False
    skipped_title_fragment: SourceFragment | None = None
    seen_ids: set = set()

    for fragment in sorted(fragments, key=lambda item: item.position_index):
        if fragment.id in seen_ids:
            continue
        seen_ids.add(fragment.id)
        if looks_like_noise_fragment(fragment):
            continue

        if (
            not skipped_title_heading
            and fragment.element_type == ElementType.HEADING
            and (fragment.heading_level or 1) <= 1
        ):
            heading_key = normalise_heading_text(fragment.content)
            if heading_key and heading_key in {candidate_title_key, source_heading_key}:
                skipped_title_heading = True
                skipped_title_fragment = fragment
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

    if _needs_intro_heading(prepared_blocks, skipped_title_fragment):
        intro_payload = {
            "fragment_id": None,
            "content": INTRO_HEADING_LABEL,
            "element_type": ElementType.HEADING,
            "position_index": 0,
            "source_position_index": skipped_title_fragment.position_index,
            "page_number": skipped_title_fragment.page_number,
            "section_path": skipped_title_fragment.section_path,
            "list_level": None,
            "group_id": None,
            "inline_spans": None,
            "meta_json": {
                "role": "heading",
                "visibility": "article",
                "synthetic_reason": "title_heading_replaced",
                "source_heading": skipped_title_fragment.content,
            },
            "synthesized": True,
        }
        prepared_blocks = [(None, intro_payload)] + [
            (
                fragment,
                {
                    **payload,
                    "position_index": index + 1,
                },
            )
            for index, (fragment, payload) in enumerate(prepared_blocks)
        ]

    return prepared_blocks


def has_meaningful_body(prepared_blocks: list[PreparedBlock]) -> bool:
    return any(
        fragment is not None and is_meaningful_body_fragment(fragment)
        for fragment, _ in prepared_blocks
    )


def _needs_intro_heading(
    prepared_blocks: list[PreparedBlock],
    skipped_title_fragment: SourceFragment | None,
) -> bool:
    if skipped_title_fragment is None or not prepared_blocks:
        return False
    first_fragment, first_payload = prepared_blocks[0]
    if first_payload["element_type"] == ElementType.HEADING:
        return False
    if first_fragment is None:
        return False
    return (first_fragment.position_index or 0) > skipped_title_fragment.position_index
