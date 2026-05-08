from __future__ import annotations
import re
from html import escape
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import base64
from typing import Any, Optional

import fitz  # PyMuPDF
from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run


_PDF_CAPTION_RE = re.compile(
    r"^(figure|fig\.|table|рис\.?|табл\.?)\s*[\dIVXLC]+\s*[:.\-–]",
    re.IGNORECASE,
)
_PDF_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+[A-Za-z]")
_PDF_FOOTNOTE_RE = re.compile(r"^(\[\d+\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|[*†‡§]+)\s")
_PDF_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
_PDF_AFFILIATION_RE = re.compile(
    r"\b(university|institute|laboratory|department)\b",
    re.IGNORECASE,
)
_PDF_LICENSE_RE = re.compile(
    r"(\bgrants permission\b|\ball rights reserved\b|©\s*\d{4}|\bcreative commons\b|\bcc[-\s]?by\b|\bcopyright\s+©?\s*\d{4})",
    re.IGNORECASE,
)
_PDF_GARBAGE_TOKEN_RE = re.compile(r"^(?:<eos>|[a-z]{0,2}\d{2,}|[\d.\s\-+/()]{4,})$", re.IGNORECASE)
_PDF_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_PDF_METADATA_LINE_RE = re.compile(
    r"^(arxiv:|(?:\d+(?:st|nd|rd|th)\s+)?(?:annual\s+)?(?:conference|workshop|symposium)\b|.*\b(preprint|submitted to)\b)",
    re.IGNORECASE,
)
_PDF_REFERENCE_HEADING_RE = re.compile(r"^(references|bibliography|works cited)$", re.IGNORECASE)
_PDF_MATH_SYMBOL_RE = re.compile(r"[∑∫≤≥≠≈±×÷√αβγδθλμπσφω→←↔∞∂∆∇]")
_PDF_MATH_OPERATOR_RE = re.compile(r"(?:<=|>=|==|=|≈|≤|≥|\*\*|\^|\bsqrt\b|[+*/=√])", re.IGNORECASE)
_PDF_MATH_FONT_RE = re.compile(r"(cmmi|cmsy|msam|msbm|symbol|mathjax|stixmath|latinmodernmath)", re.IGNORECASE)
_PDF_INLINE_MATH_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*\s*(?:<=|>=|==|=|≈|≤|≥)\s*[-+]?(?:\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9_]*)"
    r"|\b[A-Za-z][A-Za-z0-9_]*\s*(?:\*\*|\^)\s*[-+]?\d+(?:\.\d+)?"
)


@dataclass
class ExtractedElement:
    content: str
    element_type: str         # heading | paragraph | list_item | table | caption
    page_number: Optional[int]
    section_path: str
    position_index: int
    heading_level: Optional[int]
    list_level: Optional[int] = None
    inline_spans: list[dict[str, Any]] | None = None
    meta_json: dict[str, Any] | None = None


@dataclass
class RawPdfTextBlock:
    text: str
    page_number: int
    bbox: dict[str, float]
    page_width: float
    page_height: float
    line_count: int
    span_count: int
    avg_font_size: float
    min_font_size: float
    max_font_size: float
    dominant_font_name: str | None
    top_font_names: list[str]
    is_bold: bool
    is_italic: bool
    is_monospace: bool
    block_no: int
    column_hint: str
    is_numbered_heading: bool = False


@dataclass
class RawPdfTable:
    page_number: int
    bbox: dict[str, float]
    page_width: float
    page_height: float
    row_count: int
    column_count: int
    non_empty_cells: int
    rows: list[list[str]]
    reconstructed_rows: list[list[str]]
    text_lines: list[str]
    plain_text: str
    extraction_method: str = "pymupdf_find_tables"
    snapshot_image_base64: str | None = None
    snapshot_ext: str = "png"


@dataclass
class RawPdfImage:
    page_number: int
    bbox: dict[str, float]
    page_width: float
    page_height: float
    ext: str
    data: bytes
    xref: int


def extract(file_path: str) -> list[ExtractedElement]:
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext == ".docx":
        return _extract_docx(file_path)
    raise ValueError(f"Unsupported file type: {ext}")


# ─── PDF ─────────────────────────────────────────────────────────────────────

def _extract_pdf(file_path: str) -> list[ExtractedElement]:
    elements: list[ExtractedElement] = []
    heading_stack: list[tuple[int, str]] = []
    idx = 0

    doc = fitz.open(file_path)

    # Pass 1: collect all font sizes to find the body baseline (median)
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

    # Pass 2: prepare page blocks, strip repeated artifacts, then classify in
    # reconstructed reading order.
    prepared_pages: list[tuple[fitz.Page, int, list[dict[str, Any]]]] = []
    for page in doc:
        page_num = page.number + 1
        prepared_pages.append(
            (page, page_num, _prepare_pdf_page_blocks(page, page_num, body_size))
        )

    repeated_artifacts = _find_repeated_pdf_artifacts(
        [blocks for _page, _page_num, blocks in prepared_pages]
    )

    in_references_section = False
    for page, page_num, prepared_blocks in prepared_pages:
        is_visualization_page = _is_pdf_visualization_page(prepared_blocks)
        table_items: list[dict[str, Any]] = (
            [] if is_visualization_page else _extract_pdf_tables(page, page_num)
        )
        image_items: list[dict[str, Any]] = (
            _extract_pdf_visualization_snapshot(page, page_num)
            if is_visualization_page
            else _extract_pdf_images(doc, page, page_num)
        )

        visible_blocks = [
            block
            for block in prepared_blocks
            if block["normalized_text"] not in repeated_artifacts
        ]
        visible_blocks = _merge_pdf_math_lines_by_position(visible_blocks, body_size=body_size)
        if not is_visualization_page:
            table_items.extend(_extract_pdf_text_tables(page, visible_blocks, page_num, body_size=body_size))
            table_items = _dedupe_pdf_table_items(table_items)
        if table_items:
            visible_blocks = [
                block
                for block in visible_blocks
                if not any(
                    _pdf_block_overlaps_table_bbox(block["raw_block"], table_item["raw_table"].bbox)
                    for table_item in table_items
                )
            ]
        page_in_references = in_references_section or _page_enters_references_section(visible_blocks)
        if page_in_references:
            visible_blocks = sorted(
                visible_blocks,
                key=lambda block: (
                    block["raw_block"].bbox["y0"],
                    block["raw_block"].bbox["x0"],
                    block["raw_block"].block_no,
                ),
            )
        else:
            visible_blocks = _reconstruct_pdf_paragraphs(
                _order_prepared_pdf_blocks(visible_blocks),
                body_size=body_size,
            )
        visible_blocks = _filter_pdf_visualization_blocks(visible_blocks)
        visible_blocks = _filter_pdf_noise_blocks(visible_blocks)

        page_items: list[dict[str, Any]] = [
            {
                "kind": "text",
                "y0": prepared["raw_block"].bbox["y0"],
                "x0": prepared["raw_block"].bbox["x0"],
                "y1": prepared["raw_block"].bbox["y1"],
                "bbox": prepared["raw_block"].bbox,
                "payload": prepared,
            }
            for prepared in visible_blocks
        ]
        page_items.extend(
            {
                "kind": "table",
                "y0": table_item["raw_table"].bbox["y0"],
                "x0": table_item["raw_table"].bbox["x0"],
                "y1": table_item["raw_table"].bbox["y1"],
                "bbox": table_item["raw_table"].bbox,
                "payload": table_item,
            }
            for table_item in table_items
        )
        page_items.extend(
            {
                "kind": "image",
                "y0": image_item["raw_image"].bbox["y0"],
                "x0": image_item["raw_image"].bbox["x0"],
                "y1": image_item["raw_image"].bbox["y1"],
                "bbox": image_item["raw_image"].bbox,
                "payload": image_item,
            }
            for image_item in image_items
        )

        sorted_page_items = sorted(page_items, key=lambda candidate: (candidate["y0"], candidate["x0"]))
        _attach_pdf_caption_groups(sorted_page_items)

        for item in sorted_page_items:
            if item["kind"] == "table":
                table_item = item["payload"]
                raw_table = table_item["raw_table"]
                meta_json = {
                    "rows": raw_table.rows,
                    "markdown": _pdf_table_markdown(raw_table.rows),
                    "html": _pdf_table_html(raw_table.rows),
                    "table": _pdf_table_display_meta(raw_table),
                    "pdf": _raw_pdf_table_meta(raw_table),
                }
                if item.get("caption_group_id"):
                    meta_json["caption_group_id"] = item["caption_group_id"]
                    meta_json["caption"] = _caption_link_meta(item)
                elements.append(ExtractedElement(
                    content=table_item["content"],
                    element_type="table",
                    page_number=page_num,
                    section_path=" > ".join(h[1] for h in heading_stack),
                    position_index=idx,
                    heading_level=None,
                    list_level=None,
                    inline_spans=None,
                    meta_json=meta_json,
                ))
                idx += 1
                continue
            if item["kind"] == "image":
                image_item = item["payload"]
                raw_image = image_item["raw_image"]
                meta_json = {
                    "ext": raw_image.ext,
                    "image_base64": base64.b64encode(raw_image.data).decode("ascii"),
                    "image": _pdf_image_display_meta(raw_image),
                    "pdf": _raw_pdf_image_meta(raw_image),
                }
                if image_item.get("visualization_snapshot"):
                    meta_json["visualization_snapshot"] = True
                    meta_json["image"]["extraction_method"] = "pymupdf_page_snapshot"
                if item.get("caption_group_id"):
                    meta_json["caption_group_id"] = item["caption_group_id"]
                    meta_json["caption"] = _caption_link_meta(item)
                elements.append(ExtractedElement(
                    content="[Image]",
                    element_type="image",
                    page_number=page_num,
                    section_path=" > ".join(h[1] for h in heading_stack),
                    position_index=idx,
                    heading_level=None,
                    list_level=None,
                    inline_spans=None,
                    meta_json=meta_json,
                ))
                idx += 1
                continue

            prepared = item["payload"]
            full_text = prepared["text"]
            if not full_text:
                continue

            el_type, h_level = _classify_pdf_block(
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
                if _PDF_REFERENCE_HEADING_RE.match(full_text.strip()):
                    in_references_section = True

            section_path = " > ".join(
                h[1] for h in heading_stack if h[0] < (h_level or 99)
            )

            meta_json = {"pdf": _raw_pdf_text_block_meta(prepared["raw_block"])}
            if in_references_section or _PDF_REFERENCE_HEADING_RE.match(full_text.strip()):
                meta_json["references_section"] = True
            if el_type == "footnote":
                meta_json["footnote"] = _pdf_footnote_meta(full_text)
            if item.get("caption_group_id"):
                meta_json["caption_group_id"] = item["caption_group_id"]
                meta_json["caption"] = _caption_link_meta(item)
            if prepared.get("has_inline_math"):
                meta_json["has_inline_math"] = True
                meta_json["math_spans"] = prepared.get("math_spans", [])
            elements.append(ExtractedElement(
                content=full_text,
                element_type=el_type,
                page_number=page_num,
                section_path=section_path,
                position_index=idx,
                heading_level=h_level,
                list_level=None,
                inline_spans=prepared["inline_spans"] or None,
                meta_json=meta_json,
            ))
            idx += 1

        if page_in_references:
            in_references_section = True

    doc.close()
    return elements


def _classify_pdf_block(
    text: str,
    avg_size: float,
    body_size: float,
    is_bold: bool,
    is_italic: bool,
    is_monospace: bool,
) -> tuple[str, Optional[int]]:
    numbered_heading = _PDF_NUMBERED_HEADING_RE.match(text)
    if numbered_heading and _looks_like_valid_pdf_numbered_heading(text):
        return "heading", numbered_heading.group(1).count(".") + 1
    if text.strip().lower() == "abstract":
        return "heading", 2
    if _PDF_REFERENCE_HEADING_RE.match(text.strip()):
        return "heading", 2
    if _looks_like_pdf_numbered_footnote(text):
        return "footnote", None

    if _PDF_CAPTION_RE.match(text):
        return "caption", None
    if _PDF_FOOTNOTE_RE.match(text):
        return "footnote", None
    ratio = avg_size / body_size if body_size else 1.0
    short = len(text) < 150

    if ratio >= 1.5 and short and _looks_like_pdf_heading_text(text):
        return "heading", 1
    if ratio >= 1.25 and short and _looks_like_pdf_heading_text(text):
        return "heading", 2
    if ratio >= 1.1 and short and _looks_like_pdf_heading_text(text):
        return "heading", 3
    if is_bold and short and _looks_like_pdf_heading_text(text):
        return "heading", 3
    if text.isupper() and short and _looks_like_pdf_heading_text(text):
        return "heading", 2
    if is_monospace and len(text) > 10:
        return "code_block", None
    if is_italic and len(text) < 400 and not _looks_like_pdf_math_expression_text(text):
        return "quote", None
    if text.startswith(("\"", "“", "”", "'")) and len(text) < 400:
        return "quote", None
    if text.startswith(("- ", "• ", "* ", "◦ ")) or re.match(r"^\d+[\.\)]\s", text):
        return "list_item", None
    return "paragraph", None


def _line_ends_with_soft_wrap(text: str) -> bool:
    text = text.rstrip()
    if not text:
        return False
    return text[-1] not in ".!?;:]})\"'"


def _merge_pdf_inline_spans(
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


def _should_merge_pdf_blocks(
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

    vertical_gap = right_raw.bbox["y0"] - left_raw.bbox["y1"]
    if _should_merge_pdf_math_continuation(left, right, vertical_gap=vertical_gap, body_size=body_size):
        return True

    if _looks_like_pdf_structural_candidate(left["text"], left["avg_size"], body_size, left["is_bold"]):
        return False
    if _looks_like_pdf_structural_candidate(right["text"], right["avg_size"], body_size, right["is_bold"]):
        return False

    x_delta = abs(left_raw.bbox["x0"] - right_raw.bbox["x0"])
    if x_delta > 18:
        return False

    size_delta = abs(left["avg_size"] - right["avg_size"])
    if size_delta > max(1.0, body_size * 0.12):
        return False

    if vertical_gap > max(body_size * 1.4, 18):
        return False

    left_text = left["text"].rstrip()
    right_text = right["text"].lstrip()
    if not left_text or not right_text:
        return False

    if left_text.endswith("-") and right_text[:1].islower():
        return True
    if _line_ends_with_soft_wrap(left_text):
        return True
    if right_text[:1].islower():
        return True

    return False


def _should_merge_pdf_math_continuation(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    vertical_gap: float,
    body_size: float,
) -> bool:
    if vertical_gap < -max(body_size * 1.4, 16) or vertical_gap > max(body_size * 2.2, 28):
        return False
    left_text = left["text"].rstrip()
    right_text = right["text"].lstrip()
    if not left_text or not right_text:
        return False
    if not (_looks_like_pdf_math_expression_text(left_text) and _looks_like_pdf_math_expression_text(right_text)):
        return False
    if _PDF_CAPTION_RE.match(right_text) or _PDF_FOOTNOTE_RE.match(right_text):
        return False
    if not _looks_like_pdf_math_continuation_start(right_text):
        return False
    if _looks_like_pdf_math_expression_open(left_text):
        return True
    return bool(_PDF_MATH_OPERATOR_RE.search(left_text)) and right_text[:1] in ")]}/√abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _looks_like_pdf_math_continuation_start(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    if stripped[:1] in ")]}/√":
        return True
    if stripped.casefold().startswith("sqrt"):
        return True
    if _PDF_MATH_SYMBOL_RE.match(stripped):
        return True
    if re.match(r"^(?:where\b|and\b|[A-Za-z]\s*(?:=|∈|\+|-|/|\*))", stripped, re.IGNORECASE):
        return True
    return False


def _looks_like_pdf_math_expression_text(text: str) -> bool:
    stripped = " ".join(text.split()).strip()
    if not stripped:
        return False
    if _PDF_INLINE_MATH_RE.search(stripped) or _PDF_MATH_SYMBOL_RE.search(stripped):
        return True
    if not _PDF_MATH_OPERATOR_RE.search(stripped):
        return False
    if not re.search(r"[A-Za-z]", stripped):
        return False
    words = _PDF_WORD_RE.findall(stripped)
    operator_count = len(_PDF_MATH_OPERATOR_RE.findall(stripped))
    symbol_count = len(re.findall(r"[=+\-*/^_(){}\[\],]", stripped))
    if len(words) <= 12:
        return True
    return len(words) <= 20 and (operator_count + symbol_count) >= 5


def _looks_like_pdf_math_expression_open(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped[-1] in "+-*/=([{,√":
        return True
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    return any(stripped.count(open_char) > stripped.count(close_char) for open_char, close_char in pairs)


def _merge_pdf_math_lines_by_position(
    blocks: list[dict[str, Any]],
    *,
    body_size: float,
) -> list[dict[str, Any]]:
    if len(blocks) < 2:
        return blocks

    ordered = sorted(
        blocks,
        key=lambda block: (
            block["raw_block"].page_number,
            block["raw_block"].bbox["y0"],
            block["raw_block"].bbox["x0"],
            block["raw_block"].block_no,
        ),
    )
    consumed: set[int] = set()
    merged_by_block_no: dict[int, dict[str, Any]] = {}

    for index, block in enumerate(ordered):
        raw = block["raw_block"]
        if raw.block_no in consumed:
            continue
        current = block
        for candidate in ordered[index + 1 : index + 5]:
            candidate_raw = candidate["raw_block"]
            if candidate_raw.block_no in consumed:
                continue
            vertical_gap = candidate_raw.bbox["y0"] - current["raw_block"].bbox["y1"]
            if vertical_gap > max(body_size * 2.8, 34):
                break
            if _should_merge_pdf_math_continuation(
                current,
                candidate,
                vertical_gap=vertical_gap,
                body_size=body_size,
            ):
                current = _merge_pdf_prepared_blocks(current, candidate)
                consumed.add(candidate_raw.block_no)
                continue
            break
        if current is not block:
            merged_by_block_no[raw.block_no] = current

    if not consumed and not merged_by_block_no:
        return blocks

    result: list[dict[str, Any]] = []
    for block in blocks:
        block_no = block["raw_block"].block_no
        if block_no in consumed:
            continue
        result.append(merged_by_block_no.get(block_no, block))
    return result


def _merge_pdf_prepared_blocks(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
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
        "inline_spans": _merge_pdf_inline_spans(
            left["text"],
            left["inline_spans"],
            right["inline_spans"],
            separator_len=separator_len,
        ),
        "has_inline_math": bool(left.get("has_inline_math") or right.get("has_inline_math")),
        "math_spans": _merge_pdf_inline_spans(
            left["text"],
            left.get("math_spans"),
            right.get("math_spans"),
            separator_len=separator_len,
        )
        or [],
        "normalized_text": _normalize_pdf_artifact_text(merged_text),
    }


def _reconstruct_pdf_paragraphs(
    ordered_blocks: list[dict[str, Any]],
    *,
    body_size: float,
) -> list[dict[str, Any]]:
    if not ordered_blocks:
        return []

    rebuilt: list[dict[str, Any]] = [ordered_blocks[0]]
    for block in ordered_blocks[1:]:
        previous = rebuilt[-1]
        if _should_merge_pdf_blocks(previous, block, body_size=body_size):
            rebuilt[-1] = _merge_pdf_prepared_blocks(previous, block)
        else:
            rebuilt.append(block)
    return rebuilt


def _looks_like_pdf_structural_candidate(
    text: str,
    avg_size: float,
    body_size: float,
    is_bold: bool,
) -> bool:
    if _looks_like_valid_pdf_numbered_heading(text):
        return True
    if _PDF_CAPTION_RE.match(text) or _PDF_FOOTNOTE_RE.match(text):
        return True
    short = len(text) < 160
    ratio = avg_size / body_size if body_size else 1.0
    if ratio >= 1.1 and short and _looks_like_pdf_heading_text(text):
        return True
    if is_bold and short and _looks_like_pdf_heading_text(text):
        return True
    if text.startswith(("- ", "• ", "* ", "◦ ")) or re.match(r"^\d+[\.\)]\s", text):
        return True
    return False


def _looks_like_valid_pdf_numbered_heading(text: str) -> bool:
    if not _PDF_NUMBERED_HEADING_RE.match(text):
        return False
    stripped = " ".join(text.split()).strip()
    if len(stripped) > 110:
        return False
    if stripped.endswith("."):
        return False
    alpha_tokens = _PDF_WORD_RE.findall(stripped)
    if len(alpha_tokens) < 1:
        return False
    if len(alpha_tokens) > 12:
        return False
    return True


def _looks_like_pdf_heading_text(text: str) -> bool:
    stripped = " ".join(text.split()).strip()
    if len(stripped) < 4 or len(stripped) > 150:
        return False
    if stripped.endswith("."):
        return False
    if stripped.lower().startswith(("note:", "where ", "if ", "then ")):
        return False
    alpha_tokens = _PDF_WORD_RE.findall(stripped)
    if len(alpha_tokens) < 2:
        return False
    if sum(len(token) >= 3 for token in alpha_tokens) < 1:
        return False
    return True


def _looks_like_pdf_numbered_footnote(text: str) -> bool:
    match = _PDF_NUMBERED_HEADING_RE.match(text)
    if not match:
        return False
    stripped = " ".join(text.split()).strip()
    number = match.group(1)
    alpha_tokens = _PDF_WORD_RE.findall(stripped)
    return "." not in number and (len(stripped) > 110 or len(alpha_tokens) > 12 or stripped.endswith("."))


def _pdf_footnote_meta(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = _PDF_FOOTNOTE_RE.match(stripped)
    if match:
        marker = match.group(1)
    else:
        numbered = _PDF_NUMBERED_HEADING_RE.match(stripped)
        marker = numbered.group(1) if numbered else None

    if marker is None:
        marker_style = "unknown"
    elif marker.startswith("["):
        marker_style = "bracket"
    elif marker.isdigit():
        marker_style = "number"
    elif any(char in marker for char in "*†‡§"):
        marker_style = "symbol"
    else:
        marker_style = "superscript"

    return {
        "marker": marker,
        "marker_style": marker_style,
    }


def _looks_like_pdf_noise(text: str, *, before_content_start: bool) -> bool:
    normalized = _normalize_pdf_artifact_text(text)
    if not normalized:
        return True
    if _PDF_GARBAGE_TOKEN_RE.match(normalized):
        return True
    if before_content_start and _looks_like_pdf_author_or_contact_line(text):
        return True
    if before_content_start and _PDF_METADATA_LINE_RE.match(text):
        return True
    if before_content_start and _PDF_LICENSE_RE.search(text):
        return True
    digit_ratio = sum(1 for char in normalized if char.isdigit()) / max(1, len(normalized))
    if digit_ratio > 0.45 and len(normalized) < 80:
        return True
    if _looks_like_pdf_visual_word_salad(text):
        return True
    return False


def _looks_like_pdf_author_or_contact_line(text: str) -> bool:
    normalized = _normalize_pdf_artifact_text(text)
    if _PDF_EMAIL_RE.search(text):
        return True
    if _PDF_AFFILIATION_RE.search(text) and len(normalized) < 220:
        return True
    if re.search(r"^\s*[*†‡§]+\s", text) and len(normalized) < 220:
        return True
    return False


def _looks_like_pdf_visual_word_salad(text: str) -> bool:
    tokens = [token.casefold() for token in _PDF_WORD_RE.findall(text)]
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

    # Visualization residue tends to look like many short repeated tokens with
    # almost no sentence punctuation. Real academic prose can repeat terms and
    # still be perfectly valid, so keep this detector conservative.
    if sentence_marks >= 1:
        return False

    if unique_ratio < 0.72 and repeated_token_types >= 4:
        return True
    if unique_ratio < 0.62 and heavily_repeated_types >= 2:
        return True
    if long_token_count <= max(2, len(tokens) // 6) and repeated_token_types >= 5:
        return True

    return False


def _is_pdf_visualization_page(blocks: list[dict[str, Any]]) -> bool:
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
        tokens = _PDF_WORD_RE.findall(text)
        token_counts.append(len(tokens))
        if len(tokens) <= 2 and len(text) <= 20:
            tiny_blocks += 1

    if not token_counts:
        return False

    tiny_ratio = tiny_blocks / len(token_counts)
    return pad_like >= 3 or (len(token_counts) >= 20 and tiny_ratio >= 0.7)


def _filter_pdf_visualization_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not _is_pdf_visualization_page(blocks):
        return blocks
    return []


def _is_pdf_content_start_heading(block: dict[str, Any]) -> bool:
    text = block["text"]
    if _PDF_NUMBERED_HEADING_RE.match(text):
        return True
    if text.strip().lower() == "abstract":
        return True
    return False


def _is_pdf_numbered_section_heading(block: dict[str, Any]) -> bool:
    return bool(_PDF_NUMBERED_HEADING_RE.match(block["text"]))


def _filter_pdf_noise_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not blocks:
        return []

    filtered: list[dict[str, Any]] = []
    before_content_start = True
    before_numbered_section = True

    for block in blocks:
        text = block["text"]
        # Bracketed numeric entries are meaningful references/footnotes even
        # when they are short arXiv/citation lines that otherwise resemble
        # front-matter metadata.
        if _PDF_FOOTNOTE_RE.match(text):
            filtered.append(block)
            continue
        if before_numbered_section and (_PDF_METADATA_LINE_RE.match(text) or _PDF_LICENSE_RE.search(text)):
            continue
        if _is_pdf_content_start_heading(block):
            before_content_start = False
            filtered.append(block)
            if _is_pdf_numbered_section_heading(block):
                before_numbered_section = False
            continue
        if _looks_like_pdf_noise(text, before_content_start=before_content_start):
            continue
        filtered.append(block)
        if _is_pdf_numbered_section_heading(block):
            before_numbered_section = False

    return filtered


def _extract_pdf_tables(
    page: fitz.Page,
    page_number: int,
) -> list[dict[str, Any]]:
    found = page.find_tables()
    tables = list(getattr(found, "tables", []) or [])
    extracted: list[dict[str, Any]] = []

    for table in tables:
        try:
            rows = table.extract()
        except Exception:
            continue
        normalized_rows = _normalize_pdf_table_rows(rows)
        if not _looks_like_meaningful_pdf_table(normalized_rows):
            continue
        raw_table = _build_raw_pdf_table(page, page_number, table, normalized_rows)
        extracted.append(
            {
                "content": _pdf_table_content(normalized_rows),
                "raw_table": raw_table,
            }
        )

    return extracted


def _extract_pdf_text_tables(
    page: fitz.Page,
    blocks: list[dict[str, Any]],
    page_number: int,
    *,
    body_size: float,
) -> list[dict[str, Any]]:
    ordered = sorted(
        blocks,
        key=lambda block: (
            block["raw_block"].bbox["y0"],
            block["raw_block"].bbox["x0"],
            block["raw_block"].block_no,
        ),
    )
    extracted: list[dict[str, Any]] = []
    used_block_numbers: set[int] = set()

    for index, block in enumerate(ordered):
        if block["raw_block"].block_no in used_block_numbers:
            continue
        if not _PDF_CAPTION_RE.match(block["text"].strip()):
            continue
        if not block["text"].strip().lower().startswith(("table", "табл")):
            continue

        candidates: list[dict[str, Any]] = []
        caption_bottom = block["raw_block"].bbox["y1"]
        for next_block in ordered[index + 1 : index + 16]:
            if next_block["raw_block"].block_no in used_block_numbers:
                continue
            if next_block["raw_block"].bbox["y0"] - caption_bottom > 220:
                break
            if candidates and _looks_like_pdf_text_table_boundary_block(next_block, body_size=body_size):
                break
            if candidates:
                previous_bottom = candidates[-1]["raw_block"].bbox["y1"]
                vertical_gap = next_block["raw_block"].bbox["y0"] - previous_bottom
                if vertical_gap > max(18.0, body_size * 1.5):
                    break
            if _looks_like_pdf_text_table_line_block(next_block):
                candidates.append(next_block)
                continue
            if candidates:
                break

        if not candidates:
            continue

        rows = _parse_pdf_text_table_rows(candidates)
        if not _looks_like_meaningful_pdf_table(rows):
            continue

        raw_table = _build_raw_pdf_text_table(page, page_number, candidates, rows)
        extracted.append(
            {
                "content": raw_table.plain_text or _pdf_table_content(rows),
                "raw_table": raw_table,
            }
        )
        used_block_numbers.update(candidate["raw_block"].block_no for candidate in candidates)

    return extracted


def _dedupe_pdf_table_items(table_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(table_items) <= 1:
        return table_items

    kept: list[dict[str, Any]] = []
    for table_item in sorted(
        table_items,
        key=lambda item: (
            item["raw_table"].page_number,
            item["raw_table"].bbox["y0"],
            item["raw_table"].bbox["x0"],
            *[-value for value in _pdf_table_item_quality(item)],
        ),
    ):
        duplicate_index = next(
            (
                index
                for index, kept_item in enumerate(kept)
                if _pdf_table_items_overlap(table_item, kept_item)
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(table_item)
            continue

        if _pdf_table_item_quality(table_item) > _pdf_table_item_quality(kept[duplicate_index]):
            kept[duplicate_index] = table_item

    return sorted(
        kept,
        key=lambda item: (
            item["raw_table"].page_number,
            item["raw_table"].bbox["y0"],
            item["raw_table"].bbox["x0"],
        ),
    )


def _pdf_table_items_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_table = left["raw_table"]
    right_table = right["raw_table"]
    if left_table.page_number != right_table.page_number:
        return False
    return _pdf_bbox_overlap_ratio(left_table.bbox, right_table.bbox) >= 0.45


def _pdf_bbox_overlap_ratio(left: dict[str, float], right: dict[str, float]) -> float:
    x0 = max(float(left["x0"]), float(right["x0"]))
    y0 = max(float(left["y0"]), float(right["y0"]))
    x1 = min(float(left["x1"]), float(right["x1"]))
    y1 = min(float(left["y1"]), float(right["y1"]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    left_area = max(1.0, float(left["width"]) * float(left["height"]))
    right_area = max(1.0, float(right["width"]) * float(right["height"]))
    return intersection / min(left_area, right_area)


def _pdf_table_item_quality(table_item: dict[str, Any]) -> tuple[int, int, int, int]:
    table = table_item["raw_table"]
    method_score = 2 if table.extraction_method == "pymupdf_find_tables" else 1
    line_score = len(table.text_lines)
    cell_score = table.non_empty_cells
    area_score = int(float(table.bbox["width"]) * float(table.bbox["height"]))
    return (method_score, line_score, cell_score, area_score)


def _looks_like_pdf_text_table_line_block(block: dict[str, Any]) -> bool:
    text = " ".join(block.get("lines", [])).strip()
    if not text:
        return False
    if len(text) > 420:
        return False
    tokens = text.split()
    if len(tokens) < 2:
        return False
    if len(text) > 140 and re.search(r"[A-Za-z]\.\s+[A-Z]", text):
        return False
    numeric_tokens = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    if numeric_tokens >= 1 and len(tokens) >= 3:
        return True
    if len(text) > 140:
        operator_tokens = len(re.findall(r"[·×*/=()]", text))
        return operator_tokens >= 3 and len(tokens) >= 8 and not text.endswith(".")
    return len(tokens) >= 3 and not text.endswith(".")


def _looks_like_pdf_text_table_boundary_block(block: dict[str, Any], *, body_size: float) -> bool:
    text = " ".join(block["text"].split()).strip()
    if not text:
        return True
    if _PDF_CAPTION_RE.match(text) or _PDF_REFERENCE_HEADING_RE.match(text):
        return True
    if _looks_like_valid_pdf_numbered_heading(text):
        return True
    ratio = block["avg_size"] / body_size if body_size else 1.0
    return ratio >= 1.2 and _looks_like_pdf_heading_text(text)


def _parse_pdf_text_table_rows(blocks: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for block in blocks:
        for line in block.get("lines", []):
            stripped = " ".join(line.split()).strip()
            if not stripped:
                continue
            if re.search(r"\s{2,}", line):
                cells = [cell.strip() for cell in re.split(r"\s{2,}", line) if cell.strip()]
            else:
                cells = stripped.split()
            if len(cells) >= 2:
                rows.append(cells)
    return rows


def _build_raw_pdf_text_table(
    page: fitz.Page,
    page_number: int,
    blocks: list[dict[str, Any]],
    rows: list[list[str]],
) -> RawPdfTable:
    first = blocks[0]["raw_block"]
    x0 = min(block["raw_block"].bbox["x0"] for block in blocks)
    y0 = min(block["raw_block"].bbox["y0"] for block in blocks)
    x1 = max(block["raw_block"].bbox["x1"] for block in blocks)
    y1 = max(block["raw_block"].bbox["y1"] for block in blocks)
    bbox = {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": x1 - x0,
        "height": y1 - y0,
    }
    non_empty_cells = sum(1 for row in rows for cell in row if cell)
    text_lines = [line for block in blocks for line in block.get("lines", []) if line.strip()]
    return RawPdfTable(
        page_number=page_number,
        bbox=bbox,
        page_width=first.page_width,
        page_height=first.page_height,
        row_count=len(rows),
        column_count=max((len(row) for row in rows), default=0),
        non_empty_cells=non_empty_cells,
        rows=rows,
        reconstructed_rows=rows,
        text_lines=text_lines,
        plain_text="\n".join(text_lines),
        extraction_method="text_table_fallback",
        snapshot_image_base64=_pdf_table_snapshot_base64(page, bbox),
    )


def _normalize_pdf_table_rows(rows: list[list[Any]] | None) -> list[list[str]]:
    normalized: list[list[str]] = []
    for row in rows or []:
        cleaned = []
        for cell in row or []:
            text = " ".join(str(cell or "").split()).strip()
            cleaned.append(text)
        if any(cleaned):
            normalized.append(cleaned)
    return normalized


def _looks_like_meaningful_pdf_table(rows: list[list[str]]) -> bool:
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
        if len(_PDF_WORD_RE.findall(cell)) >= 1 or any(char.isdigit() for char in cell)
    )
    if meaningful_text_cells < 4:
        return False

    return True


def _pdf_table_content(rows: list[list[str]]) -> str:
    return "\n".join(
        " | ".join(cell for cell in row if cell)
        for row in rows
        if any(row)
    )


def _pdf_block_overlaps_table_bbox(
    block: RawPdfTextBlock,
    table_bbox: dict[str, float],
) -> bool:
    left = max(block.bbox["x0"], table_bbox["x0"])
    top = max(block.bbox["y0"], table_bbox["y0"])
    right = min(block.bbox["x1"], table_bbox["x1"])
    bottom = min(block.bbox["y1"], table_bbox["y1"])
    if right <= left or bottom <= top:
        return False

    overlap_area = (right - left) * (bottom - top)
    block_area = max(1.0, block.bbox["width"] * block.bbox["height"])
    return (overlap_area / block_area) >= 0.45


def _prepare_pdf_page_blocks(
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
        math_spans: list[dict[str, Any]] = []
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
                    if _MONOSPACE_HINT_RE.search(font_name):
                        inline_spans.append({"start": start, "end": end, "style": "code", "data": None})
                        monospace_spans += 1
                    detected_math_spans = _extract_pdf_inline_math_spans(
                        text,
                        offset=start,
                        font_name=font_name,
                    )
                    inline_spans.extend(detected_math_spans)
                    math_spans.extend(detected_math_spans)
                    cursor = end + 1
                if span["size"] > 4:
                    span_sizes.append(span["size"])
                font_name = span.get("font", "")
                if font_name:
                    font_names.append(font_name)
                total_spans += 1
            if line_parts:
                lines_text.append(" ".join(line_parts))

        full_text = _join_pdf_lines(lines_text)
        if not full_text:
            continue
        if not math_spans and _looks_like_pdf_math_expression_text(full_text):
            math_span = {
                "start": 0,
                "end": len(full_text),
                "style": "math",
                "data": _math_span_data(full_text, source="regex", confidence=0.4),
            }
            inline_spans.append(math_span)
            math_spans.append(math_span)

        avg_size = sum(span_sizes) / len(span_sizes) if span_sizes else body_size
        is_bold = total_spans > 0 and (bold_spans / total_spans) >= 0.5
        is_italic = total_spans > 0 and (italic_spans / total_spans) >= 0.5
        is_monospace = total_spans > 0 and (monospace_spans / total_spans) >= 0.5
        raw_block = _build_raw_pdf_text_block(
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
                "lines": lines_text,
                "inline_spans": inline_spans,
                "has_inline_math": bool(math_spans),
                "math_spans": math_spans,
                "normalized_text": _normalize_pdf_artifact_text(full_text),
            }
        )
    return prepared


def _normalize_pdf_artifact_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _join_pdf_lines(lines: list[str]) -> str:
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


def _artifact_signature(text: str) -> str:
    return re.sub(r"\d+", "#", _normalize_pdf_artifact_text(text))


def _find_repeated_pdf_artifacts(
    pages: list[list[dict[str, Any]]],
) -> set[str]:
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
                signature = _artifact_signature(normalized)
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


def _order_prepared_pdf_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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


def _build_raw_pdf_text_block(
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


def _extract_pdf_inline_math_spans(
    text: str,
    *,
    offset: int,
    font_name: str,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    source = "font" if _PDF_MATH_FONT_RE.search(font_name) else "regex"

    if _PDF_MATH_FONT_RE.search(font_name) and len(_PDF_WORD_RE.findall(text)) <= 4:
        return [
            {
                "start": offset,
                "end": offset + len(text),
                "style": "math",
                "data": _math_span_data(text, source=source, confidence=0.45),
            }
        ]

    for match in _PDF_INLINE_MATH_RE.finditer(text):
        raw = match.group(0).strip()
        if not raw:
            continue
        spans.append(
            {
                "start": offset + match.start(),
                "end": offset + match.end(),
                "style": "math",
                "data": _math_span_data(raw, source="regex", confidence=0.55),
            }
        )

    for match in _PDF_MATH_SYMBOL_RE.finditer(text):
        if any(span["start"] <= offset + match.start() < span["end"] for span in spans):
            continue
        raw = match.group(0)
        spans.append(
            {
                "start": offset + match.start(),
                "end": offset + match.end(),
                "style": "math",
                "data": _math_span_data(raw, source="unicode", confidence=0.35),
            }
        )

    return spans


def _math_span_data(raw: str, *, source: str, confidence: float) -> dict[str, Any]:
    return {
        "raw": raw,
        "latex": _simple_unicode_math_to_latex(raw),
        "latex_confidence": confidence,
        "source": source,
    }


def _raw_pdf_text_block_meta(block: RawPdfTextBlock) -> dict[str, Any]:
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


def _build_raw_pdf_table(
    page: fitz.Page,
    page_number: int,
    table: Any,
    rows: list[list[str]],
) -> RawPdfTable:
    bbox = fitz.Rect(table.bbox)
    total_cells = sum(len(row) for row in rows)
    non_empty_cells = sum(1 for row in rows for cell in row if cell)
    table_words = _extract_pdf_words_in_bbox(page, bbox)
    text_lines = _render_pdf_words_as_lines(table_words)
    column_count = max((len(row) for row in rows), default=0)
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
        column_count=column_count,
        non_empty_cells=non_empty_cells,
        rows=rows,
        reconstructed_rows=_reconstruct_pdf_table_rows_from_words(
            table_words,
            bbox=bbox,
            expected_columns=column_count,
        ),
        text_lines=text_lines,
        plain_text="\n".join(text_lines),
        snapshot_image_base64=_pdf_table_snapshot_base64(page, {
            "x0": float(bbox.x0),
            "y0": float(bbox.y0),
            "x1": float(bbox.x1),
            "y1": float(bbox.y1),
            "width": float(bbox.width),
            "height": float(bbox.height),
        }),
    )


def _raw_pdf_table_meta(table: RawPdfTable) -> dict[str, Any]:
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


def _pdf_table_display_meta(table: RawPdfTable) -> dict[str, Any]:
    confidence = _pdf_table_confidence(table)
    display_rows = _pdf_table_display_rows(table)
    meta = {
        "extraction_method": table.extraction_method,
        "reconstruction_method": "word_bbox_columns",
        "confidence": confidence,
        "display_mode": _pdf_table_display_mode(table, confidence),
        "row_count": table.row_count,
        "column_count": table.column_count,
        "non_empty_cells": table.non_empty_cells,
        "display_rows": display_rows,
        "reconstructed_rows": table.reconstructed_rows,
        "plain_text": table.plain_text,
        "text_lines": table.text_lines,
        "bbox": table.bbox,
        "page_number": table.page_number,
    }
    if table.snapshot_image_base64:
        meta["snapshot_image_base64"] = table.snapshot_image_base64
        meta["snapshot_ext"] = table.snapshot_ext
    return meta


def _pdf_table_confidence(table: RawPdfTable) -> float:
    total_cells = max(1, table.row_count * max(1, table.column_count))
    fill_ratio = table.non_empty_cells / total_cells
    if table.row_count >= 2 and table.column_count >= 2 and fill_ratio >= 0.5:
        return 0.85
    if table.row_count >= 2 and table.non_empty_cells >= 4:
        return 0.65
    return 0.4


def _pdf_table_display_mode(table: RawPdfTable, confidence: float) -> str:
    uneven_rows = len({len(row) for row in table.rows}) > 1
    if table.column_count >= 8 or confidence < 0.7 or uneven_rows:
        return "preformatted"
    return "grid"


def _pdf_table_display_rows(table: RawPdfTable) -> list[list[str]]:
    reconstructed_score = _score_pdf_table_rows(table.reconstructed_rows)
    extracted_score = _score_pdf_table_rows(table.rows)
    if reconstructed_score > extracted_score:
        return table.reconstructed_rows
    return table.rows


def _score_pdf_table_rows(rows: list[list[str]]) -> tuple[int, int, int]:
    if not rows:
        return (0, 0, 0)
    non_empty = sum(1 for row in rows for cell in row if cell.strip())
    stable_columns = -len({len(row) for row in rows})
    return (len(rows), non_empty, stable_columns)


def _extract_pdf_words_in_bbox(page: fitz.Page, bbox: fitz.Rect) -> list[dict[str, Any]]:
    words = []
    for word in page.get_text("words"):
        x0, y0, x1, y1, text = word[:5]
        center = fitz.Point((x0 + x1) / 2, (y0 + y1) / 2)
        if bbox.contains(center):
            words.append(
                {
                    "text": str(text),
                    "x0": float(x0),
                    "y0": float(y0),
                    "x1": float(x1),
                    "y1": float(y1),
                }
            )

    return words


def _render_pdf_words_as_lines(words: list[dict[str, Any]]) -> list[str]:
    if not words:
        return []

    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["y0"], item["x0"])):
        if not rows or abs(rows[-1][0]["y0"] - word["y0"]) > 5:
            rows.append([word])
        else:
            rows[-1].append(word)

    return [_render_pdf_table_text_line(row) for row in rows if row]


def _reconstruct_pdf_table_rows_from_words(
    words: list[dict[str, Any]],
    *,
    bbox: fitz.Rect,
    expected_columns: int,
) -> list[list[str]]:
    if not words or expected_columns <= 0:
        return []

    line_rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["y0"], item["x0"])):
        if not line_rows or abs(line_rows[-1][0]["y0"] - word["y0"]) > 5:
            line_rows.append([word])
        else:
            line_rows[-1].append(word)

    reconstructed: list[list[str]] = []
    table_width = max(1.0, float(bbox.width))
    for line in line_rows:
        cells: list[list[str]] = [[] for _ in range(expected_columns)]
        for word in sorted(line, key=lambda item: item["x0"]):
            center_x = (word["x0"] + word["x1"]) / 2
            column_index = int(((center_x - float(bbox.x0)) / table_width) * expected_columns)
            column_index = max(0, min(expected_columns - 1, column_index))
            cells[column_index].append(word["text"])
        row = [" ".join(cell).strip() for cell in cells]
        if any(row):
            reconstructed.append(row)

    return reconstructed


def _render_pdf_table_text_line(words: list[dict[str, Any]]) -> str:
    sorted_words = sorted(words, key=lambda item: item["x0"])
    if not sorted_words:
        return ""

    pieces: list[str] = []
    previous_x1: float | None = None
    for word in sorted_words:
        if previous_x1 is not None:
            gap = word["x0"] - previous_x1
            pieces.append(" " * max(1, min(8, int(gap / 6))))
        pieces.append(word["text"])
        previous_x1 = word["x1"]
    return "".join(pieces).strip()


def _pdf_table_snapshot_base64(page: fitz.Page, bbox: dict[str, float]) -> str | None:
    try:
        clip = fitz.Rect(
            max(0.0, float(bbox["x0"]) - 10),
            max(0.0, float(bbox["y0"]) - 12),
            min(float(page.rect.width), float(bbox["x1"]) + 10),
            min(float(page.rect.height), float(bbox["y1"]) + 12),
        )
        if clip.is_empty or clip.width < 8 or clip.height < 8:
            return None
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
        return base64.b64encode(pixmap.tobytes("png")).decode("ascii")
    except Exception:
        return None


def _pdf_table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""

    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    header = normalized_rows[0]
    separator = ["---"] * max_columns
    body = normalized_rows[1:]

    def render_row(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join([render_row(header), render_row(separator), *(render_row(row) for row in body)])


def _pdf_table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""

    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    header = normalized_rows[0]
    body = normalized_rows[1:]
    head_cells = "".join(f"<th>{escape(cell)}</th>" for cell in header)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in body
    )
    return f"<table><thead><tr>{head_cells}</tr></thead><tbody>{body_rows}</tbody></table>"


def _extract_pdf_images(
    doc: fitz.Document,
    page: fitz.Page,
    page_number: int,
) -> list[dict[str, Any]]:
    extracted_items: list[dict[str, Any]] = []
    seen: set[tuple[int, float, float, float, float]] = set()

    for image in page.get_images(full=True):
        xref = image[0]
        try:
            rects = page.get_image_rects(xref)
            extracted = doc.extract_image(xref)
        except Exception:
            continue
        data = extracted.get("image")
        if not data:
            continue
        ext = extracted.get("ext", "png")
        for rect in rects:
            bbox = {
                "x0": float(rect.x0),
                "y0": float(rect.y0),
                "x1": float(rect.x1),
                "y1": float(rect.y1),
                "width": float(rect.width),
                "height": float(rect.height),
            }
            if _looks_like_small_pdf_image_noise(bbox):
                continue
            signature = (xref, bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
            if signature in seen:
                continue
            seen.add(signature)
            extracted_items.append(
                {
                    "content": "[Image]",
                    "raw_image": RawPdfImage(
                        page_number=page_number,
                        bbox=bbox,
                        page_width=float(page.rect.width),
                        page_height=float(page.rect.height),
                        ext=ext,
                        data=data,
                        xref=xref,
                    ),
                }
            )

    return extracted_items


def _extract_pdf_visualization_snapshot(
    page: fitz.Page,
    page_number: int,
) -> list[dict[str, Any]]:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    data = pixmap.tobytes("png")
    bbox = {
        "x0": 0.0,
        "y0": 0.0,
        "x1": float(page.rect.width),
        "y1": float(page.rect.height),
        "width": float(page.rect.width),
        "height": float(page.rect.height),
    }
    return [
        {
            "content": "[Visualization Page]",
            "raw_image": RawPdfImage(
                page_number=page_number,
                bbox=bbox,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
                ext="png",
                data=data,
                xref=-page_number,
            ),
            "visualization_snapshot": True,
        }
    ]


def _looks_like_small_pdf_image_noise(bbox: dict[str, float]) -> bool:
    width = bbox["width"]
    height = bbox["height"]
    if width < 50 or height < 50:
        return True
    if width < 120 and height < 120:
        return True
    if width / max(height, 1.0) > 12 or height / max(width, 1.0) > 12:
        return True
    return False


def _raw_pdf_image_meta(image: RawPdfImage) -> dict[str, Any]:
    return {
        "xref": image.xref,
        "bbox": image.bbox,
        "page": {
            "number": image.page_number,
            "width": image.page_width,
            "height": image.page_height,
        },
    }


def _pdf_image_display_meta(image: RawPdfImage) -> dict[str, Any]:
    return {
        "extraction_method": "pymupdf_extract_image",
        "xref": image.xref,
        "width": image.bbox["width"],
        "height": image.bbox["height"],
        "bbox": image.bbox,
        "page_number": image.page_number,
    }


def _page_enters_references_section(blocks: list[dict[str, Any]]) -> bool:
    return any(_PDF_REFERENCE_HEADING_RE.match(block["text"].strip()) for block in blocks)


def _attach_pdf_caption_groups(items: list[dict[str, Any]]) -> None:
    group_index = 0
    for index, item in enumerate(items):
        if item["kind"] != "text":
            continue
        text = item["payload"]["text"].strip()
        if not _PDF_CAPTION_RE.match(text):
            continue

        candidates: list[tuple[float, int]] = []
        for neighbor_index in (index - 1, index + 1):
            if neighbor_index < 0 or neighbor_index >= len(items):
                continue
            neighbor = items[neighbor_index]
            if neighbor["kind"] not in {"image", "table"}:
                continue
            gap = _vertical_gap_between_bboxes(item["bbox"], neighbor["bbox"])
            if gap <= 45 and _horizontal_overlap_ratio(item["bbox"], neighbor["bbox"]) >= 0.2:
                candidates.append((gap, neighbor_index))

        if not candidates:
            continue

        _gap, target_index = min(candidates, key=lambda candidate: candidate[0])
        group_id = _pdf_caption_group_id(items[target_index], group_index)
        group_index += 1
        item["caption_group_id"] = group_id
        item["caption_target_kind"] = items[target_index]["kind"]
        item["caption_distance"] = _gap
        item["caption_position"] = _caption_position(item["bbox"], items[target_index]["bbox"])
        items[target_index]["caption_group_id"] = group_id
        items[target_index]["caption_text"] = text
        items[target_index]["caption_distance"] = _gap
        items[target_index]["caption_position"] = _caption_position(item["bbox"], items[target_index]["bbox"])


def _pdf_caption_group_id(target_item: dict[str, Any], group_index: int) -> str:
    payload = target_item.get("payload") or {}
    raw = payload.get("raw_table") or payload.get("raw_image")
    page_number = getattr(raw, "page_number", None)
    bbox = target_item.get("bbox") or {}
    y0 = int(round(float(bbox.get("y0", 0.0))))
    x0 = int(round(float(bbox.get("x0", 0.0))))
    page_part = f"p{page_number}" if page_number is not None else "p?"
    return f"caption-{target_item['kind']}-{page_part}-{y0}-{x0}-{group_index}"


def _vertical_gap_between_bboxes(left: dict[str, float], right: dict[str, float]) -> float:
    if left["y1"] <= right["y0"]:
        return right["y0"] - left["y1"]
    if right["y1"] <= left["y0"]:
        return left["y0"] - right["y1"]
    return 0.0


def _horizontal_overlap_ratio(left: dict[str, float], right: dict[str, float]) -> float:
    overlap = max(0.0, min(left["x1"], right["x1"]) - max(left["x0"], right["x0"]))
    smaller_width = max(1.0, min(left["width"], right["width"]))
    return overlap / smaller_width


def _caption_position(caption_bbox: dict[str, float], target_bbox: dict[str, float]) -> str:
    if caption_bbox["y1"] <= target_bbox["y0"]:
        return "above"
    if caption_bbox["y0"] >= target_bbox["y1"]:
        return "below"
    return "overlap"


def _caption_link_meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "group_id": item.get("caption_group_id"),
        "position": item.get("caption_position"),
        "distance": item.get("caption_distance"),
    }
    if item["kind"] == "text":
        meta["target_kind"] = item.get("caption_target_kind")
    else:
        meta["text"] = item.get("caption_text")
    return {key: value for key, value in meta.items() if value is not None}


def _simple_unicode_math_to_latex(text: str) -> str:
    replacements = {
        "∑": r"\sum",
        "∫": r"\int",
        "≤": r"\leq",
        "≥": r"\geq",
        "≠": r"\neq",
        "≈": r"\approx",
        "±": r"\pm",
        "×": r"\times",
        "÷": r"\div",
        "√": r"\sqrt",
        "α": r"\alpha",
        "β": r"\beta",
        "γ": r"\gamma",
        "δ": r"\delta",
        "θ": r"\theta",
        "λ": r"\lambda",
        "μ": r"\mu",
        "π": r"\pi",
        "σ": r"\sigma",
        "φ": r"\phi",
        "ω": r"\omega",
        "→": r"\to",
        "∞": r"\infty",
    }
    converted = text
    for raw, latex in replacements.items():
        converted = converted.replace(raw, latex)
    return converted


# ─── DOCX ────────────────────────────────────────────────────────────────────

_HEADING_STYLE_RE = re.compile(r"^Heading\s*(\d+)$", re.IGNORECASE)
_MONOSPACE_HINT_RE = re.compile(r"(courier|consolas|menlo|monaco|code)", re.IGNORECASE)


def _run_is_monospace(run: Run) -> bool:
    names = [
        getattr(run.font, "name", None),
        getattr(run.style, "name", None) if run.style else None,
    ]
    return any(name and _MONOSPACE_HINT_RE.search(name) for name in names)


def _paragraph_looks_monospace(para: Paragraph) -> bool:
    text_runs = [run for run in para.runs if run.text]
    if not text_runs:
        return False
    monospace_runs = sum(1 for run in text_runs if _run_is_monospace(run))
    return monospace_runs >= max(1, len(text_runs) // 2)


def _extract_inline_spans(para: Paragraph) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    cursor = 0

    for run in para.runs:
        text = run.text or ""
        if not text:
            continue

        start = cursor
        end = cursor + len(text)
        style_names: list[tuple[str, dict[str, Any] | None]] = []

        if run.bold:
            style_names.append(("bold", None))
        if run.italic:
            style_names.append(("italic", None))
        if _run_is_monospace(run):
            style_names.append(("code", None))
        if run.style and (run.style.name or "").lower() == "hyperlink":
            style_names.append(("link", {}))

        for style, data in style_names:
            spans.append(
                {
                    "start": start,
                    "end": end,
                    "style": style,
                    "data": data,
                }
            )

        cursor = end

    return spans


def _get_list_level(para: Paragraph) -> int | None:
    p_pr = getattr(para._p, "pPr", None)
    if p_pr is None or p_pr.numPr is None or p_pr.numPr.ilvl is None:
        return None
    try:
        return int(p_pr.numPr.ilvl.val)
    except (TypeError, ValueError):
        return None


def _extract_docx(file_path: str) -> list[ExtractedElement]:
    doc = DocxDocument(file_path)
    elements: list[ExtractedElement] = []
    heading_stack: list[tuple[int, str]] = []
    idx = 0

    for block in _iter_docx_body_blocks(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                image_runs = _extract_inline_images(block)
                for image in image_runs:
                    image["section_path"] = " > ".join(h[1] for h in heading_stack)
                    image["position_index"] = idx
                    elements.append(ExtractedElement(**image))
                    idx += 1
                continue

            list_level = _get_list_level(block)
            inline_spans = _extract_inline_spans(block)
            el_type, h_level = _classify_docx_para(block, list_level=list_level)

            if el_type == "heading" and h_level:
                while heading_stack and heading_stack[-1][0] >= h_level:
                    heading_stack.pop()
                heading_stack.append((h_level, text))

            section_path = " > ".join(
                h[1] for h in heading_stack if h[0] < (h_level or 99)
            )

            elements.append(ExtractedElement(
                content=text,
                element_type=el_type,
                page_number=None,
                section_path=section_path,
                position_index=idx,
                heading_level=h_level,
                list_level=list_level,
                inline_spans=inline_spans or None,
                meta_json=None,
            ))
            idx += 1

            for image in _extract_inline_images(block):
                image["section_path"] = section_path
                image["position_index"] = idx
                elements.append(ExtractedElement(**image))
                idx += 1
            continue

        rows = _extract_docx_table_rows(block)
        if rows:
            elements.append(ExtractedElement(
                content="\n".join(" | ".join(cell for cell in row if cell) for row in rows),
                element_type="table",
                page_number=None,
                section_path=" > ".join(h[1] for h in heading_stack),
                position_index=idx,
                heading_level=None,
                list_level=None,
                inline_spans=None,
                meta_json={"rows": rows},
            ))
            idx += 1

    return elements


def _iter_docx_body_blocks(doc: DocxDocumentType) -> list[Paragraph | Table]:
    blocks: list[Paragraph | Table] = []
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            blocks.append(Paragraph(child, doc))
        elif isinstance(child, CT_Tbl):
            blocks.append(Table(child, doc))
    return blocks


def _extract_docx_table_rows(table: Table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [" ".join(c.text.split()).strip() for c in row.cells]
        if any(cells):
            rows.append(cells)
    return rows


def _classify_docx_para(
    para: Paragraph,
    *,
    list_level: int | None,
) -> tuple[str, Optional[int]]:
    style_name = para.style.name or ""
    m = _HEADING_STYLE_RE.match(style_name)
    if m:
        return "heading", int(m.group(1))
    sl = style_name.lower()
    if "quote" in sl:
        return "quote", None
    if "footnote" in sl or re.match(r"^\[\d+\]\s", para.text.strip()):
        return "footnote", None
    if "code" in sl or _paragraph_looks_monospace(para):
        return "code_block", None
    if list_level is not None or "list" in sl or "bullet" in sl:
        return "list_item", None
    if "caption" in sl:
        return "caption", None
    return "paragraph", None


def _extract_inline_images(para: Paragraph) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    rels = para.part.related_parts
    for drawing in para._p.xpath(".//w:drawing"):
        blips = drawing.xpath(".//a:blip")
        for blip in blips:
            embed = blip.get(qn("r:embed"))
            if not embed or embed not in rels:
                continue
            part = rels[embed]
            image_bytes = part.blob
            ext = Path(part.filename).suffix.lower() or ".png"
            images.append(
                {
                    "content": "[Image]",
                    "element_type": "image",
                    "page_number": None,
                    "heading_level": None,
                    "list_level": None,
                    "inline_spans": None,
                    "meta_json": {
                        "ext": ext.lstrip("."),
                        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
                    },
                }
            )
    return images
