from __future__ import annotations

from typing import Any

import fitz

from app.domain.ingestion.common import ExtractedElement
from app.domain.ingestion.pdf.parsers import (
    classify_pdf_block,
    extract_pdf_embedded_images,
    extract_pdf_tables,
    filter_pdf_noise_blocks,
    filter_pdf_visualization_blocks,
    find_repeated_pdf_artifacts,
    is_pdf_visualization_page,
    order_prepared_pdf_blocks,
    pdf_block_overlaps_table_bbox,
    prepare_pdf_page_blocks,
    raw_pdf_table_meta,
    raw_pdf_text_block_meta,
    reconstruct_pdf_paragraphs,
)


def extract_pdf(file_path: str) -> list[ExtractedElement]:
    elements: list[ExtractedElement] = []
    heading_stack: list[tuple[int, str]] = []
    idx = 0

    doc = fitz.open(file_path)

    all_sizes: list[float] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["size"] > 4:
                        all_sizes.append(span["size"])

    sorted_sizes = sorted(all_sizes)
    body_size = sorted_sizes[len(sorted_sizes) // 2] if sorted_sizes else 12.0

    prepared_pages: list[tuple[fitz.Page, int, list[dict[str, Any]]]] = []
    for page in doc:
        page_num = page.number + 1
        prepared_pages.append(
            (page, page_num, prepare_pdf_page_blocks(page, page_num, body_size))
        )

    repeated_artifacts = find_repeated_pdf_artifacts(
        [blocks for _page, _page_num, blocks in prepared_pages]
    )

    for page, page_num, prepared_blocks in prepared_pages:
        image_elements, idx = extract_pdf_embedded_images(
            doc,
            page,
            page_number=page_num,
            section_path=" > ".join(h[1] for h in heading_stack),
            start_index=idx,
        )
        elements.extend(image_elements)

        is_visualization_page = is_pdf_visualization_page(prepared_blocks)
        table_items: list[dict[str, Any]] = (
            [] if is_visualization_page else extract_pdf_tables(page, page_num)
        )

        visible_blocks = [
            block
            for block in prepared_blocks
            if block["normalized_text"] not in repeated_artifacts
        ]
        if table_items:
            visible_blocks = [
                block
                for block in visible_blocks
                if not any(
                    pdf_block_overlaps_table_bbox(block["raw_block"], table_item["raw_table"].bbox)
                    for table_item in table_items
                )
            ]

        visible_blocks = reconstruct_pdf_paragraphs(
            order_prepared_pdf_blocks(visible_blocks),
            body_size=body_size,
        )
        visible_blocks = filter_pdf_visualization_blocks(visible_blocks)
        visible_blocks = filter_pdf_noise_blocks(visible_blocks)

        page_items: list[dict[str, Any]] = [
            {
                "kind": "text",
                "y0": prepared["raw_block"].bbox["y0"],
                "x0": prepared["raw_block"].bbox["x0"],
                "payload": prepared,
            }
            for prepared in visible_blocks
        ]
        page_items.extend(
            {
                "kind": "table",
                "y0": table_item["raw_table"].bbox["y0"],
                "x0": table_item["raw_table"].bbox["x0"],
                "payload": table_item,
            }
            for table_item in table_items
        )

        for item in sorted(page_items, key=lambda candidate: (candidate["y0"], candidate["x0"])):
            if item["kind"] == "table":
                idx = _append_table_element(
                    elements,
                    table_item=item["payload"],
                    page_number=page_num,
                    section_path=" > ".join(h[1] for h in heading_stack),
                    position_index=idx,
                )
                continue

            prepared = item["payload"]
            idx = _append_text_element(
                elements,
                prepared=prepared,
                body_size=body_size,
                heading_stack=heading_stack,
                page_number=page_num,
                position_index=idx,
            )

    doc.close()
    return elements


def _append_table_element(
    elements: list[ExtractedElement],
    *,
    table_item: dict[str, Any],
    page_number: int,
    section_path: str,
    position_index: int,
) -> int:
    raw_table = table_item["raw_table"]
    elements.append(
        ExtractedElement(
            content=table_item["content"],
            element_type="table",
            page_number=page_number,
            section_path=section_path,
            position_index=position_index,
            heading_level=None,
            list_level=None,
            inline_spans=None,
            meta_json={
                "rows": raw_table.rows,
                "pdf": raw_pdf_table_meta(raw_table),
            },
        )
    )
    return position_index + 1


def _append_text_element(
    elements: list[ExtractedElement],
    *,
    prepared: dict[str, Any],
    body_size: float,
    heading_stack: list[tuple[int, str]],
    page_number: int,
    position_index: int,
) -> int:
    full_text = prepared["text"]
    if not full_text:
        return position_index

    el_type, h_level = classify_pdf_block(
        full_text,
        prepared["avg_size"],
        body_size,
        prepared["is_bold"],
        prepared["is_italic"],
        prepared["is_monospace"],
    )

    if el_type == "heading" and h_level:
        while heading_stack and heading_stack[-1][0] >= h_level:
            heading_stack.pop()
        heading_stack.append((h_level, full_text))

    section_path = " > ".join(
        h[1] for h in heading_stack if h[0] < (h_level or 99)
    )

    elements.append(
        ExtractedElement(
            content=full_text,
            element_type=el_type,
            page_number=page_number,
            section_path=section_path,
            position_index=position_index,
            heading_level=h_level,
            list_level=None,
            inline_spans=prepared["inline_spans"] or None,
            meta_json={"pdf": raw_pdf_text_block_meta(prepared["raw_block"])},
        )
    )
    return position_index + 1
