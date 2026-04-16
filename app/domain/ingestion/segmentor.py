from __future__ import annotations
from dataclasses import dataclass
from app.domain.ingestion.extractor import ExtractedElement

MIN_CHARS = 80     # fragments shorter than this are merged with neighbors
MAX_MERGE = 1200   # never merge beyond this total length


@dataclass
class FragmentData:
    content: str
    element_type: str
    page_number: int | None
    section_path: str
    position_index: int
    heading_level: int | None


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
            if not pending_list:
                list_meta = {
                    "element_type": "list_item",
                    "page_number": el.page_number,
                    "section_path": el.section_path,
                    "position_index": el.position_index,
                    "heading_level": None,
                }
            pending_list.append(el.content)
            continue

        flush_list()

        if el.element_type in ("heading", "table", "caption"):
            fragments.append(FragmentData(
                content=el.content,
                element_type=el.element_type,
                page_number=el.page_number,
                section_path=el.section_path,
                position_index=el.position_index,
                heading_level=el.heading_level,
            ))
            continue

        # Paragraph: merge with previous if previous is short and same section
        if (
            fragments
            and fragments[-1].element_type == "paragraph"
            and fragments[-1].section_path == el.section_path
            and len(fragments[-1].content) < MIN_CHARS
            and len(fragments[-1].content) + len(el.content) < MAX_MERGE
        ):
            prev = fragments[-1]
            fragments[-1] = FragmentData(
                content=prev.content + " " + el.content,
                element_type="paragraph",
                page_number=prev.page_number,
                section_path=el.section_path,
                position_index=prev.position_index,
                heading_level=None,
            )
        else:
            fragments.append(FragmentData(
                content=el.content,
                element_type=el.element_type,
                page_number=el.page_number,
                section_path=el.section_path,
                position_index=el.position_index,
                heading_level=el.heading_level,
            ))

    flush_list()
    return [f for f in fragments if f.content.strip()]