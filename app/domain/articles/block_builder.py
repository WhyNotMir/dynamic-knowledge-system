from __future__ import annotations

from app.domain.articles.article_text import (
    is_meaningful_body_fragment,
    looks_like_noise_text,
    normalise_heading_text,
)
from app.models.article_candidate import ArticleCandidate
from app.models.source_fragment import ElementType, SourceFragment

PreparedBlock = tuple[SourceFragment, dict]


def prepare_article_blocks(candidate: ArticleCandidate) -> list[PreparedBlock]:
    candidate_fragments = sorted(
        candidate.candidate_fragments,
        key=lambda item: item.position_index,
    )
    prepared_blocks: list[PreparedBlock] = []
    candidate_title_key = normalise_heading_text(candidate.title)
    source_heading_key = normalise_heading_text(candidate.source_section_path)
    skipped_title_heading = False

    for candidate_fragment in candidate_fragments:
        fragment = candidate_fragment.fragment
        if fragment is None:
            continue
        if looks_like_noise_text(fragment.content):
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


def has_meaningful_body(prepared_blocks: list[PreparedBlock]) -> bool:
    return any(is_meaningful_body_fragment(fragment) for fragment, _ in prepared_blocks)
