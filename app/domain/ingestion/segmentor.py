from __future__ import annotations

from dataclasses import dataclass
import uuid

from app.domain.ingestion.block_semantics import (
    ROLE_BODY,
    VISIBILITY_ARTICLE,
    is_article_visible,
    with_semantic_meta,
)
from app.domain.ingestion.extractor import ExtractedElement


MAX_PARAGRAPH_MERGE = 900
SHORT_PARAGRAPH = 120


@dataclass
class FragmentData:
    content: str
    element_type: str
    page_number: int | None
    section_path: str
    position_index: int
    heading_level: int | None
    list_level: int | None = None
    group_id: uuid.UUID | None = None
    inline_spans: list[dict] | None = None
    meta_json: dict | None = None


def segment(elements: list[ExtractedElement]) -> list[FragmentData]:
    """Conservative source-preserving segmenter.

    The extractor owns source structure. The segmenter may only normalize tiny
    layout artifacts and must not create semantic groupings. In particular,
    headings, list items, captions, tables, images, formulas, and references
    stay as standalone fragments.
    """
    fragments: list[FragmentData] = []

    for element in elements:
        content = _normalise_element_content(element)
        if not content:
            continue

        if _can_merge_with_previous(fragments[-1] if fragments else None, element, content):
            previous = fragments[-1]
            separator_len = 1
            fragments[-1] = FragmentData(
                content=f"{previous.content} {content}",
                element_type="paragraph",
                page_number=previous.page_number,
                section_path=previous.section_path,
                position_index=previous.position_index,
                heading_level=None,
                list_level=None,
                group_id=previous.group_id,
                inline_spans=_merge_inline_spans(
                    previous.content,
                    previous.inline_spans,
                    element.inline_spans,
                    separator_len=separator_len,
                ),
                meta_json=_merge_pdf_meta(previous.meta_json, element.meta_json),
            )
            continue

        fragments.append(
            FragmentData(
                content=content,
                element_type=element.element_type,
                page_number=element.page_number,
                section_path=element.section_path,
                position_index=element.position_index,
                heading_level=element.heading_level,
                list_level=element.list_level,
                group_id=None,
                inline_spans=element.inline_spans,
                meta_json=element.meta_json,
            )
        )

    return fragments


def _can_merge_with_previous(
    previous: FragmentData | None,
    element: ExtractedElement,
    content: str,
) -> bool:
    if previous is None:
        return False
    if previous.element_type != "paragraph" or element.element_type != "paragraph":
        return False
    if not is_article_visible(previous.meta_json) or not is_article_visible(element.meta_json):
        return False
    if previous.section_path != element.section_path:
        return False
    if previous.page_number != element.page_number:
        return False
    if len(previous.content) + len(content) > MAX_PARAGRAPH_MERGE:
        return False
    if (
        (_is_pdf_meta(previous.meta_json) or _is_pdf_meta(element.meta_json))
        and _looks_like_standalone_title(previous.content)
    ):
        return False
    return len(previous.content) < SHORT_PARAGRAPH or len(content) < SHORT_PARAGRAPH


def _normalise_element_content(element: ExtractedElement) -> str:
    content = element.content or ""
    if element.element_type == "table":
        lines = [" ".join(line.split()).strip() for line in content.splitlines()]
        return "\n".join(line for line in lines if line)
    return " ".join(content.split()).strip()


def _looks_like_standalone_title(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) > 160:
        return False
    if stripped.endswith((".", "?", "!")):
        return False
    return any(char.isalpha() for char in stripped)


def _merge_inline_spans(
    left_content: str,
    left_spans: list[dict] | None,
    right_spans: list[dict] | None,
    *,
    separator_len: int,
) -> list[dict] | None:
    merged: list[dict] = [dict(span) for span in left_spans or []]
    offset = len(left_content) + separator_len
    for span in right_spans or []:
        shifted = dict(span)
        shifted["start"] = shifted["start"] + offset
        shifted["end"] = shifted["end"] + offset
        merged.append(shifted)
    return merged or None


def _merge_pdf_meta(left: dict | None, right: dict | None) -> dict | None:
    blocks: list[dict] = []
    for meta in (left, right):
        if not meta:
            continue
        if isinstance(meta.get("pdf"), dict):
            blocks.append(dict(meta["pdf"]))
        elif isinstance(meta.get("pdf_blocks"), list):
            blocks.extend(dict(item) for item in meta["pdf_blocks"])
        elif isinstance(meta.get("docling"), dict):
            blocks.append({"docling": dict(meta["docling"])})
        elif isinstance(meta.get("docling_blocks"), list):
            blocks.extend(dict(item) for item in meta["docling_blocks"])
        else:
            return left
    if not blocks:
        return left
    block_key = "pdf_blocks" if any("pdf" in item or "block_no" in item for item in blocks) else "docling_blocks"
    return with_semantic_meta(
        {block_key: blocks},
        role=ROLE_BODY,
        visibility=VISIBILITY_ARTICLE,
        confidence=min(_confidence(left), _confidence(right)),
        extraction_method="segmentor_paragraph_merge",
    )


def _confidence(meta: dict | None) -> float:
    if not meta:
        return 0.5
    value = meta.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    semantic = meta.get("semantic")
    if isinstance(semantic, dict) and isinstance(semantic.get("confidence"), (int, float)):
        return float(semantic["confidence"])
    pdf = meta.get("pdf")
    if isinstance(pdf, dict) and isinstance(pdf.get("confidence"), (int, float)):
        return float(pdf["confidence"])
    return 0.5


def _is_pdf_meta(meta: dict | None) -> bool:
    return bool(meta and ("pdf" in meta or "pdf_blocks" in meta or "docling" in meta or "docling_blocks" in meta))
