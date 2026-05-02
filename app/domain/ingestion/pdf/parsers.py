from __future__ import annotations

import re
import base64
from collections import Counter
from typing import Any

import fitz

from app.domain.ingestion.common import ExtractedElement
from app.domain.ingestion.pdf.utils import (
    MONOSPACE_HINT_RE,
    PDF_AFFILIATION_RE,
    PDF_CAPTION_RE,
    PDF_EMAIL_RE,
    PDF_FOOTNOTE_RE,
    PDF_GARBAGE_TOKEN_RE,
    PDF_LICENSE_RE,
    PDF_METADATA_LINE_RE,
    PDF_NUMBERED_HEADING_RE,
    PDF_WORD_RE,
    RawPdfTable,
    RawPdfTextBlock,
)


def classify_pdf_block(
    text: str,
    avg_size: float,
    body_size: float,
    is_bold: bool,
    is_italic: bool,
    is_monospace: bool,
) -> tuple[str, int | None]:
    numbered_heading = PDF_NUMBERED_HEADING_RE.match(text)
    if numbered_heading and looks_like_valid_pdf_numbered_heading(text):
        return "heading", numbered_heading.group(1).count(".") + 1
    if looks_like_pdf_numbered_footnote(text):
        return "footnote", None

    if PDF_CAPTION_RE.match(text):
        return "caption", None
    if PDF_FOOTNOTE_RE.match(text):
        return "footnote", None
    if PDF_METADATA_LINE_RE.match(text):
        return "footnote", None

    ratio = avg_size / body_size if body_size else 1.0
    short = len(text) < 150

    if ratio >= 1.5 and short:
        return "heading", 1
    if ratio >= 1.25 and short:
        return "heading", 2
    if ratio >= 1.1 and short:
        return "heading", 3
    if is_bold and short and len(text) > 3:
        return "heading", 3
    if text.isupper() and short and len(text) > 3:
        return "heading", 2
    if is_monospace and len(text) > 10:
        return "code_block", None
    if is_italic and len(text) < 400:
        return "quote", None
    if text.startswith(("\"", "“", "”", "'")) and len(text) < 400:
        return "quote", None
    if text.startswith(("- ", "• ", "* ", "◦ ")) or re.match(r"^\d+[\.\)]\s", text):
        return "list_item", None
    return "paragraph", None


def looks_like_pdf_structural_candidate(
    text: str,
    avg_size: float,
    body_size: float,
    is_bold: bool,
) -> bool:
    if looks_like_valid_pdf_numbered_heading(text):
        return True
    if PDF_CAPTION_RE.match(text) or PDF_FOOTNOTE_RE.match(text):
        return True
    short = len(text) < 160
    ratio = avg_size / body_size if body_size else 1.0
    if ratio >= 1.1 and short:
        return True
    if is_bold and short:
        return True
    if text.startswith(("- ", "• ", "* ", "◦ ")) or re.match(r"^\d+[\.\)]\s", text):
        return True
    return False


def looks_like_valid_pdf_numbered_heading(text: str) -> bool:
    if not PDF_NUMBERED_HEADING_RE.match(text):
        return False
    stripped = " ".join(text.split()).strip()
    if len(stripped) > 110:
        return False
    if stripped.endswith("."):
        return False
    alpha_tokens = PDF_WORD_RE.findall(stripped)
    if len(alpha_tokens) > 12:
        return False
    return True


def looks_like_pdf_numbered_footnote(text: str) -> bool:
    match = PDF_NUMBERED_HEADING_RE.match(text)
    if not match:
        return False
    stripped = " ".join(text.split()).strip()
    number = match.group(1)
    alpha_tokens = PDF_WORD_RE.findall(stripped)
    return "." not in number and (len(stripped) > 110 or len(alpha_tokens) > 12 or stripped.endswith("."))


def looks_like_pdf_noise(text: str, *, before_major_heading: bool) -> bool:
    normalized = normalize_pdf_artifact_text(text)
    if not normalized:
        return True
    if PDF_GARBAGE_TOKEN_RE.match(normalized):
        return True
    if PDF_EMAIL_RE.search(text):
        return True
    if PDF_AFFILIATION_RE.search(text) and len(normalized) < 180:
        return True
    if before_major_heading and (PDF_EMAIL_RE.search(text) or PDF_AFFILIATION_RE.search(text)):
        return True
    if before_major_heading and PDF_LICENSE_RE.search(text):
        return True
    digit_ratio = sum(1 for char in normalized if char.isdigit()) / max(1, len(normalized))
    if digit_ratio > 0.45 and len(normalized) < 80:
        return True
    if looks_like_pdf_visual_word_salad(text):
        return True
    return False


def looks_like_pdf_visual_word_salad(text: str) -> bool:
    tokens = [token.casefold() for token in PDF_WORD_RE.findall(text)]
    if len(tokens) < 12:
        return False

    counts = Counter(tokens)
    unique_ratio = len(counts) / len(tokens)
    repeated_token_types = sum(1 for count in counts.values() if count >= 2)
    heavily_repeated_types = sum(1 for count in counts.values() if count >= 3)
    long_token_count = sum(1 for token in tokens if len(token) >= 6)
    sentence_marks = sum(text.count(mark) for mark in ".!?;:")

    if "<pad>" in text.casefold():
        return True
    if sentence_marks >= 1:
        return False
    if unique_ratio < 0.72 and repeated_token_types >= 4:
        return True
    if unique_ratio < 0.62 and heavily_repeated_types >= 2:
        return True
    if long_token_count <= max(2, len(tokens) // 6) and repeated_token_types >= 5:
        return True
    return False


def is_major_semantic_heading(block: dict[str, Any], body_size: float) -> bool:
    text = block["text"]
    avg_size = block["avg_size"]
    if PDF_NUMBERED_HEADING_RE.match(text):
        return True
    if text.strip().lower() == "abstract":
        return True
    return avg_size / body_size >= 1.2 and len(text) < 120


def filter_pdf_noise_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocks:
        return []

    body_size_candidates = [block["avg_size"] for block in blocks if len(block["text"]) > 40]
    body_size = sorted(body_size_candidates)[len(body_size_candidates) // 2] if body_size_candidates else 12.0
    filtered: list[dict[str, Any]] = []
    before_major_heading = True

    for block in blocks:
        text = block["text"]
        if is_major_semantic_heading(block, body_size):
            before_major_heading = False
            filtered.append(block)
            continue
        if looks_like_pdf_noise(text, before_major_heading=before_major_heading):
            continue
        filtered.append(block)

    return filtered


def line_ends_with_soft_wrap(text: str) -> bool:
    text = text.rstrip()
    if not text:
        return False
    return text[-1] not in ".!?;:]})\"'"


def merge_pdf_inline_spans(
    left_content: str,
    left_spans: list[dict[str, Any]] | None,
    right_spans: list[dict[str, Any]] | None,
    *,
    separator_len: int,
) -> list[dict[str, Any]] | None:
    merged: list[dict[str, Any]] = [dict(span) for span in left_spans or []]
    if right_spans:
        offset = len(left_content) + separator_len
        for span in right_spans:
            shifted = dict(span)
            shifted["start"] = shifted["start"] + offset
            shifted["end"] = shifted["end"] + offset
            merged.append(shifted)
    return merged or None


def should_merge_pdf_blocks(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    body_size: float,
) -> bool:
    left_raw = left["raw_block"]
    right_raw = right["raw_block"]

    if left_raw.page_number != right_raw.page_number:
        return False
    if left_raw.column_hint != right_raw.column_hint:
        return False
    if looks_like_pdf_structural_candidate(left["text"], left["avg_size"], body_size, left["is_bold"]):
        return False
    if looks_like_pdf_structural_candidate(right["text"], right["avg_size"], body_size, right["is_bold"]):
        return False

    x_delta = abs(left_raw.bbox["x0"] - right_raw.bbox["x0"])
    if x_delta > 18:
        return False

    size_delta = abs(left["avg_size"] - right["avg_size"])
    if size_delta > max(1.0, body_size * 0.12):
        return False

    vertical_gap = right_raw.bbox["y0"] - left_raw.bbox["y1"]
    if vertical_gap > max(body_size * 1.4, 18):
        return False

    left_text = left["text"].rstrip()
    right_text = right["text"].lstrip()
    if not left_text or not right_text:
        return False

    if left_text.endswith("-") and right_text[:1].islower():
        return True
    if line_ends_with_soft_wrap(left_text):
        return True
    if right_text[:1].islower():
        return True

    return False


def merge_pdf_prepared_blocks(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_text = left["text"].rstrip()
    right_text = right["text"].lstrip()
    if left_text.endswith("-") and right_text[:1].islower():
        merged_text = left_text[:-1] + right_text
    else:
        merged_text = left_text + " " + right_text

    separator_len = max(1, len(merged_text) - len(left_text) - len(right_text))
    if left_text.endswith("-") and right_text[:1].islower():
        separator_len = 0

    return {
        "text": merged_text,
        "raw_block": left["raw_block"],
        "avg_size": (left["avg_size"] + right["avg_size"]) / 2,
        "is_bold": left["is_bold"] and right["is_bold"],
        "is_italic": left["is_italic"] and right["is_italic"],
        "is_monospace": left["is_monospace"] and right["is_monospace"],
        "inline_spans": merge_pdf_inline_spans(
            left["text"],
            left["inline_spans"],
            right["inline_spans"],
            separator_len=separator_len,
        ),
        "normalized_text": normalize_pdf_artifact_text(merged_text),
    }


def reconstruct_pdf_paragraphs(
    ordered_blocks: list[dict[str, Any]],
    *,
    body_size: float,
) -> list[dict[str, Any]]:
    if not ordered_blocks:
        return []

    rebuilt: list[dict[str, Any]] = [ordered_blocks[0]]
    for block in ordered_blocks[1:]:
        previous = rebuilt[-1]
        if should_merge_pdf_blocks(previous, block, body_size=body_size):
            rebuilt[-1] = merge_pdf_prepared_blocks(previous, block)
        else:
            rebuilt.append(block)
    return rebuilt


def prepare_pdf_page_blocks(
    page: fitz.Page,
    page_number: int,
    body_size: float,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for block_no, block in enumerate(page.get_text("dict")["blocks"]):
        if block["type"] != 0:
            continue

        lines_text: list[str] = []
        span_sizes: list[float] = []
        font_names: list[str] = []
        bold_spans = 0
        italic_spans = 0
        monospace_spans = 0
        total_spans = 0
        inline_spans: list[dict[str, Any]] = []
        cursor = 0

        for line in block["lines"]:
            line_parts: list[str] = []
            for span in line["spans"]:
                text = span["text"].strip()
                if text:
                    line_parts.append(text)
                    start = cursor
                    end = cursor + len(text)
                    flags = span["flags"]
                    font_name = span.get("font", "")
                    if flags & 16:
                        inline_spans.append({"start": start, "end": end, "style": "bold", "data": None})
                        bold_spans += 1
                    if flags & 2:
                        inline_spans.append({"start": start, "end": end, "style": "italic", "data": None})
                        italic_spans += 1
                    if MONOSPACE_HINT_RE.search(font_name):
                        inline_spans.append({"start": start, "end": end, "style": "code", "data": None})
                        monospace_spans += 1
                    cursor = end + 1
                if span["size"] > 4:
                    span_sizes.append(span["size"])
                font_name = span.get("font", "")
                if font_name:
                    font_names.append(font_name)
                total_spans += 1
            if line_parts:
                lines_text.append(" ".join(line_parts))

        full_text = join_pdf_lines(lines_text)
        if not full_text:
            continue

        avg_size = sum(span_sizes) / len(span_sizes) if span_sizes else body_size
        is_bold = total_spans > 0 and (bold_spans / total_spans) >= 0.5
        is_italic = total_spans > 0 and (italic_spans / total_spans) >= 0.5
        is_monospace = total_spans > 0 and (monospace_spans / total_spans) >= 0.5
        raw_block = build_raw_pdf_text_block(
            page=page,
            page_number=page_number,
            block=block,
            block_no=block_no,
            text=full_text,
            span_sizes=span_sizes,
            font_names=font_names,
            avg_font_size=avg_size,
            is_bold=is_bold,
            is_italic=is_italic,
            is_monospace=is_monospace,
            total_spans=total_spans,
        )
        prepared.append(
            {
                "text": full_text,
                "raw_block": raw_block,
                "avg_size": avg_size,
                "is_bold": is_bold,
                "is_italic": is_italic,
                "is_monospace": is_monospace,
                "inline_spans": inline_spans,
                "normalized_text": normalize_pdf_artifact_text(full_text),
            }
        )
    return prepared


def normalize_pdf_artifact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def join_pdf_lines(lines: list[str]) -> str:
    merged = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not merged:
            merged = line
            continue
        if merged.endswith("-") and line[:1].islower():
            merged = merged[:-1] + line
        else:
            merged = merged + " " + line
    return merged.strip()


def artifact_signature(text: str) -> str:
    return re.sub(r"\d+", "#", normalize_pdf_artifact_text(text))


def find_repeated_pdf_artifacts(pages: list[list[dict[str, Any]]]) -> set[str]:
    candidate_pages: dict[str, set[int]] = {}
    signatures_to_texts: dict[str, set[str]] = {}
    for page_index, blocks in enumerate(pages):
        for block in blocks:
            raw = block["raw_block"]
            normalized = block["normalized_text"]
            if not normalized or len(normalized) > 140:
                continue
            y0 = raw.bbox["y0"]
            y1 = raw.bbox["y1"]
            top_edge = raw.page_height * 0.06
            bottom_edge = raw.page_height * 0.94
            if y0 <= top_edge or y1 >= bottom_edge:
                signature = artifact_signature(normalized)
                candidate_pages.setdefault(signature, set()).add(page_index)
                signatures_to_texts.setdefault(signature, set()).add(normalized)

    repeated_signatures = {
        signature
        for signature, page_indexes in candidate_pages.items()
        if len(page_indexes) >= 2
    }
    return {
        normalized
        for signature in repeated_signatures
        for normalized in signatures_to_texts.get(signature, set())
    }


def order_prepared_pdf_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocks:
        return []

    page_width = blocks[0]["raw_block"].page_width
    narrow_blocks = [
        block
        for block in blocks
        if block["raw_block"].bbox["width"] <= page_width * 0.48
    ]
    left_blocks = [
        block for block in narrow_blocks if block["raw_block"].column_hint == "left"
    ]
    right_blocks = [
        block for block in narrow_blocks if block["raw_block"].column_hint == "right"
    ]
    two_column = len(left_blocks) >= 1 and len(right_blocks) >= 1

    if not two_column:
        return sorted(
            blocks,
            key=lambda block: (
                block["raw_block"].bbox["y0"],
                block["raw_block"].bbox["x0"],
                block["raw_block"].block_no,
            ),
        )

    column_top = min(
        block["raw_block"].bbox["y0"] for block in (left_blocks + right_blocks)
    )
    column_bottom = max(
        block["raw_block"].bbox["y1"] for block in (left_blocks + right_blocks)
    )

    def sort_key(block: dict[str, Any]) -> tuple[float, float, float]:
        raw = block["raw_block"]
        y0 = raw.bbox["y0"]
        x0 = raw.bbox["x0"]
        if raw.column_hint == "full":
            bucket = 0 if raw.bbox["y1"] <= column_top else 3
        elif raw.column_hint == "left":
            bucket = 1
        else:
            bucket = 2
        if raw.column_hint == "full" and raw.bbox["y0"] > column_bottom:
            bucket = 3
        return (bucket, y0, x0)

    return sorted(blocks, key=sort_key)


def build_raw_pdf_text_block(
    *,
    page: fitz.Page,
    page_number: int,
    block: dict[str, Any],
    block_no: int,
    text: str,
    span_sizes: list[float],
    font_names: list[str],
    avg_font_size: float,
    is_bold: bool,
    is_italic: bool,
    is_monospace: bool,
    total_spans: int,
) -> RawPdfTextBlock:
    bbox = fitz.Rect(block["bbox"])
    page_width = float(page.rect.width)
    column_hint = "left" if bbox.x0 < (page_width / 2) else "right"
    if page_width <= 0 or bbox.width >= page_width * 0.7:
        column_hint = "full"

    font_counts = Counter(font_names)
    top_font_names = [name for name, _count in font_counts.most_common(3)]

    return RawPdfTextBlock(
        text=text,
        page_number=page_number,
        bbox={
            "x0": float(bbox.x0),
            "y0": float(bbox.y0),
            "x1": float(bbox.x1),
            "y1": float(bbox.y1),
            "width": float(bbox.width),
            "height": float(bbox.height),
        },
        page_width=page_width,
        page_height=float(page.rect.height),
        line_count=len(block.get("lines", [])),
        span_count=total_spans,
        avg_font_size=float(avg_font_size),
        min_font_size=float(min(span_sizes)) if span_sizes else float(avg_font_size),
        max_font_size=float(max(span_sizes)) if span_sizes else float(avg_font_size),
        dominant_font_name=top_font_names[0] if top_font_names else None,
        top_font_names=top_font_names,
        is_bold=is_bold,
        is_italic=is_italic,
        is_monospace=is_monospace,
        block_no=block_no,
        column_hint=column_hint,
    )


def raw_pdf_text_block_meta(block: RawPdfTextBlock) -> dict[str, Any]:
    return {
        "text_length": len(block.text),
        "block_no": block.block_no,
        "bbox": block.bbox,
        "page": {
            "number": block.page_number,
            "width": block.page_width,
            "height": block.page_height,
        },
        "layout": {
            "column_hint": block.column_hint,
            "line_count": block.line_count,
            "span_count": block.span_count,
        },
        "font": {
            "avg_size": block.avg_font_size,
            "min_size": block.min_font_size,
            "max_size": block.max_font_size,
            "dominant_name": block.dominant_font_name,
            "top_names": block.top_font_names,
            "is_bold": block.is_bold,
            "is_italic": block.is_italic,
            "is_monospace": block.is_monospace,
        },
    }


def extract_pdf_tables(page: fitz.Page, page_number: int) -> list[dict[str, Any]]:
    found = page.find_tables()
    tables = list(getattr(found, "tables", []) or [])
    extracted: list[dict[str, Any]] = []

    for table in tables:
        try:
            rows = table.extract()
        except Exception:
            continue
        normalized_rows = normalize_pdf_table_rows(rows)
        if not looks_like_meaningful_pdf_table(normalized_rows):
            continue
        raw_table = build_raw_pdf_table(page, page_number, table, normalized_rows)
        extracted.append(
            {
                "content": pdf_table_content(normalized_rows),
                "raw_table": raw_table,
            }
        )

    return extracted


def normalize_pdf_table_rows(rows: list[list[Any]] | None) -> list[list[str]]:
    normalized: list[list[str]] = []
    for row in rows or []:
        cleaned = []
        for cell in row or []:
            text = " ".join(str(cell or "").split()).strip()
            cleaned.append(text)
        if any(cleaned):
            normalized.append(cleaned)
    return normalized


def looks_like_meaningful_pdf_table(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False

    total_cells = sum(len(row) for row in rows)
    if total_cells == 0:
        return False

    non_empty_cells = sum(1 for row in rows for cell in row if cell)
    if non_empty_cells < 4:
        return False

    fill_ratio = non_empty_cells / total_cells
    if fill_ratio < 0.28:
        return False

    max_columns = max(len(row) for row in rows)
    if max_columns >= 12 and fill_ratio < 0.45:
        return False

    meaningful_text_cells = sum(
        1
        for row in rows
        for cell in row
        if len(PDF_WORD_RE.findall(cell)) >= 1 or any(char.isdigit() for char in cell)
    )
    if meaningful_text_cells < 4:
        return False

    return True


def pdf_table_content(rows: list[list[str]]) -> str:
    return "\n".join(
        " | ".join(cell for cell in row if cell)
        for row in rows
        if any(row)
    )


def pdf_block_overlaps_table_bbox(block: RawPdfTextBlock, table_bbox: dict[str, float]) -> bool:
    left = max(block.bbox["x0"], table_bbox["x0"])
    top = max(block.bbox["y0"], table_bbox["y0"])
    right = min(block.bbox["x1"], table_bbox["x1"])
    bottom = min(block.bbox["y1"], table_bbox["y1"])
    if right <= left or bottom <= top:
        return False

    overlap_area = (right - left) * (bottom - top)
    block_area = max(1.0, block.bbox["width"] * block.bbox["height"])
    return (overlap_area / block_area) >= 0.45


def build_raw_pdf_table(
    page: fitz.Page,
    page_number: int,
    table: Any,
    rows: list[list[str]],
) -> RawPdfTable:
    bbox = fitz.Rect(table.bbox)
    non_empty_cells = sum(1 for row in rows for cell in row if cell)
    return RawPdfTable(
        page_number=page_number,
        bbox={
            "x0": float(bbox.x0),
            "y0": float(bbox.y0),
            "x1": float(bbox.x1),
            "y1": float(bbox.y1),
            "width": float(bbox.width),
            "height": float(bbox.height),
        },
        page_width=float(page.rect.width),
        page_height=float(page.rect.height),
        row_count=len(rows),
        column_count=max((len(row) for row in rows), default=0),
        non_empty_cells=non_empty_cells,
        rows=rows,
    )


def raw_pdf_table_meta(table: RawPdfTable) -> dict[str, Any]:
    return {
        "bbox": table.bbox,
        "page": {
            "number": table.page_number,
            "width": table.page_width,
            "height": table.page_height,
        },
        "layout": {
            "row_count": table.row_count,
            "column_count": table.column_count,
            "non_empty_cells": table.non_empty_cells,
        },
    }


def extract_pdf_embedded_images(
    doc: fitz.Document,
    page: fitz.Page,
    *,
    page_number: int,
    section_path: str,
    start_index: int,
) -> tuple[list[ExtractedElement], int]:
    elements: list[ExtractedElement] = []
    idx = start_index
    seen_images: set[int] = set()

    for image in page.get_images(full=True):
        xref = image[0]
        if xref in seen_images:
            continue
        seen_images.add(xref)
        extracted = doc.extract_image(xref)
        ext = extracted.get("ext", "png")
        data = extracted.get("image")
        if not data:
            continue
        elements.append(
            ExtractedElement(
                content="[Image]",
                element_type="image",
                page_number=page_number,
                section_path=section_path,
                position_index=idx,
                heading_level=None,
                list_level=None,
                inline_spans=None,
                meta_json={
                    "ext": ext,
                    "image_base64": base64.b64encode(data).decode("ascii"),
                },
            )
        )
        idx += 1

    return elements, idx


def is_pdf_visualization_page(blocks: list[dict[str, Any]]) -> bool:
    if len(blocks) < 20:
        return False

    token_counts: list[int] = []
    pad_like = 0
    tiny_blocks = 0
    for block in blocks:
        text = block["text"].strip()
        if not text:
            continue
        lowered = text.casefold()
        if "<pad>" in lowered or lowered == "<eos>":
            pad_like += 1
        tokens = PDF_WORD_RE.findall(text)
        token_counts.append(len(tokens))
        if len(tokens) <= 2 and len(text) <= 20:
            tiny_blocks += 1

    if not token_counts:
        return False

    tiny_ratio = tiny_blocks / len(token_counts)
    return pad_like >= 3 or (len(token_counts) >= 20 and tiny_ratio >= 0.7)


def filter_pdf_visualization_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not is_pdf_visualization_page(blocks):
        return blocks
    return []
