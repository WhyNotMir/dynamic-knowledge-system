from __future__ import annotations
from dataclasses import dataclass
import uuid

from app.domain.ingestion.extractor import ExtractedElement

MIN_CHARS = 180    # short paragraphs are merged with neighbors to reduce choppy one-liners
MAX_MERGE = 1200   # never merge beyond this total length


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


def _merge_inline_spans(
    left_content: str,
    left_spans: list[dict] | None,
    right_spans: list[dict] | None,
    *,
    separator_len: int,
) -> list[dict] | None:
    merged: list[dict] = [dict(span) for span in left_spans or []]
    if right_spans:
        offset = len(left_content) + separator_len
        for span in right_spans:
            shifted = dict(span)
            shifted["start"] = shifted["start"] + offset
            shifted["end"] = shifted["end"] + offset
            merged.append(shifted)
    return merged or None


def _is_pdf_raw_meta(meta_json: dict | None) -> bool:
    if not meta_json:
        return True
    return set(meta_json.keys()) <= {"pdf", "pdf_blocks"}


def _collect_pdf_blocks(meta_json: dict | None) -> list[dict]:
    if not meta_json:
        return []
    if isinstance(meta_json.get("pdf_blocks"), list):
        return [dict(item) for item in meta_json["pdf_blocks"]]
    if isinstance(meta_json.get("pdf"), dict):
        return [dict(meta_json["pdf"])]
    return []


def _merge_meta_json(left: dict | None, right: dict | None) -> dict | None:
    if not left and not right:
        return None
    if _is_pdf_raw_meta(left) and _is_pdf_raw_meta(right):
        blocks = _collect_pdf_blocks(left) + _collect_pdf_blocks(right)
        return {"pdf_blocks": blocks} if blocks else None
    return None


def segment(elements: list[ExtractedElement]) -> list[FragmentData]:
    """
    Rules:
    - Headings, tables, captions → always their own fragment.
    - Consecutive list items in the same section → merged into one fragment.
    - Short paragraphs in the same section → merged into the previous paragraph
      if the result stays under MAX_MERGE.
    - Empty fragments are dropped.
    """
    fragments: list[FragmentData] = []
    pending_list: list[str] = []
    list_meta: dict | None = None

    def flush_list() -> None:
        if pending_list and list_meta:
            fragments.append(FragmentData(content="\n".join(pending_list), **list_meta))
            pending_list.clear()

    for el in elements:
        if el.element_type == "list_item":
            if (
                pending_list
                and list_meta
                and (
                    list_meta["section_path"] != el.section_path
                    or list_meta["list_level"] != el.list_level
                )
            ):
                flush_list()
                list_meta = None

            if not pending_list:
                list_meta = {
                    "element_type": "list_item",
                    "page_number": el.page_number,
                    "section_path": el.section_path,
                    "position_index": el.position_index,
                    "heading_level": None,
                    "list_level": el.list_level,
                    "group_id": uuid.uuid4(),
                    "inline_spans": el.inline_spans,
                    "meta_json": el.meta_json,
                }
            pending_list.append(el.content)
            continue

        flush_list()

        if el.element_type in ("heading", "table", "caption", "quote", "code_block", "image", "footnote", "formula"):
            fragments.append(FragmentData(
                content=el.content,
                element_type=el.element_type,
                page_number=el.page_number,
                section_path=el.section_path,
                position_index=el.position_index,
                heading_level=el.heading_level,
                list_level=el.list_level,
                group_id=None,
                inline_spans=el.inline_spans,
                meta_json=el.meta_json,
            ))
            continue

        # Paragraph: merge short follow-up paragraphs into their neighbour so
        # the reader doesn't get a page full of one-sentence blocks.
        if (
            fragments
            and fragments[-1].element_type == "paragraph"
            and fragments[-1].section_path == el.section_path
            and (
                len(fragments[-1].content) < MIN_CHARS
                or len(el.content) < MIN_CHARS
            )
            and len(fragments[-1].content) + len(el.content) < MAX_MERGE
            and _is_pdf_raw_meta(fragments[-1].meta_json)
            and _is_pdf_raw_meta(el.meta_json)
        ):
            prev = fragments[-1]
            fragments[-1] = FragmentData(
                content=prev.content + " " + el.content,
                element_type="paragraph",
                page_number=prev.page_number,
                section_path=el.section_path,
                position_index=prev.position_index,
                heading_level=None,
                list_level=None,
                group_id=prev.group_id,
                inline_spans=_merge_inline_spans(
                    prev.content,
                    prev.inline_spans,
                    el.inline_spans,
                    separator_len=1,
                ),
                meta_json=_merge_meta_json(prev.meta_json, el.meta_json),
            )
        else:
            fragments.append(FragmentData(
                content=el.content,
                element_type=el.element_type,
                page_number=el.page_number,
                section_path=el.section_path,
                position_index=el.position_index,
                heading_level=el.heading_level,
                list_level=el.list_level,
                group_id=None,
                inline_spans=el.inline_spans,
                meta_json=el.meta_json,
            ))

    flush_list()
    return [f for f in fragments if f.content.strip()]
