from __future__ import annotations

import base64
import re
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF


_BULLET_RE = re.compile(r"^(?:[\u2022•◦▪▫‣⁃*+-])\s+")
_CAPTION_RE = re.compile(
    r"^(?:table|figure|fig\.?|таблица|табл\.?|рисунок|рис\.?)\s*[\dIVXLCА-Яа-яA-Za-z]+(?:[.:)\-–]|\s)",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(
    r"^(?:table|таблица|табл\.?)\s*[\dIVXLCА-Яа-яA-Za-z]+(?:[.:)\-–]|\s)",
    re.IGNORECASE,
)
_FIGURE_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|рисунок|рис\.?)\s*[\dIVXLCА-Яа-яA-Za-z]+(?:[.:)\-–]|\s)",
    re.IGNORECASE,
)
_REFERENCE_HEADING_RE = re.compile(
    r"^(?:references|bibliography|works cited|литература|список литературы|источники)$",
    re.IGNORECASE,
)
_REFERENCE_ITEM_RE = re.compile(r"^(?:\[\d+\]|\d+[.)])\s+\S+")
_NUMBERED_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[.)])?\s+(.+)$")
_INLINE_MATH_SYMBOL_RE = re.compile(r"[∑∫≤≥≠≈±×÷√αβγδθλμπσφω→←↔∞∂∆∇]|(?:<=|>=|==|=)")
_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
_DOCUMENT_METADATA_RE = re.compile(
    r"(?:\barxiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?\b|\[[a-z]{2}\.[a-z]{2}\]|"
    r"\bconference on\b|\bproceedings\b|\bpermission to reproduce\b|"
    r"\ball rights reserved\b|\bcreative commons\b|\bcopyright\b)",
    re.IGNORECASE,
)
_AUTHOR_NOTE_RE = re.compile(r"^[*†‡§∗]\s*\S")
_SHORT_TABLE_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+./()_-]{0,28}(?:\s+\[[0-9]+\])?$")
_NUMERIC_TABLE_TOKEN_RE = re.compile(
    r"^(?:\d+(?:[.,]\d+)?|[.,]|[×x*·•]|\d+(?:[.,]\d+)?(?:e[+-]?\d+)?|\d+\s*[·•]\s*10\s*\d+)$",
    re.IGNORECASE,
)


@dataclass
class ExtractedElement:
    content: str
    element_type: str
    page_number: int | None
    section_path: str
    position_index: int
    heading_level: int | None
    list_level: int | None = None
    inline_spans: list[dict[str, Any]] | None = None
    meta_json: dict[str, Any] | None = None


@dataclass
class _TextBlock:
    text: str
    page_number: int
    bbox: dict[str, float]
    page_width: float
    page_height: float
    line_count: int
    avg_size: float
    max_size: float
    is_bold: bool
    is_italic: bool
    block_no: int


@dataclass
class _TableBlock:
    page_number: int
    bbox: dict[str, float]
    page_width: float
    page_height: float
    rows: list[list[str]]
    extraction_method: str
    snapshot_image_base64: str | None = None


def extract(file_path: str) -> list[ExtractedElement]:
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(file_path)

    raise ValueError(f"Only PDF extraction is supported by the active pipeline: {ext}")


def _extract_pdf(file_path: str) -> list[ExtractedElement]:
    doc = fitz.open(file_path)
    body_size = _document_body_size(doc)
    raw_items: list[dict[str, Any]] = []

    for page in doc:
        page_number = page.number + 1
        text_blocks = _extract_text_blocks(page, page_number)
        table_blocks = _extract_tables(page, page_number)
        visual_text_salad = _page_looks_like_visual_text_salad(text_blocks)

        table_bboxes = [table.bbox for table in table_blocks]
        visible_text = [
            block
            for block in text_blocks
            if not any(_bbox_overlap_ratio(block.bbox, bbox) > 0.55 for bbox in table_bboxes)
            and (not visual_text_salad or _looks_like_caption(_normalise_text(block.text)))
        ]

        for block in visible_text:
            raw_items.append(
                {
                    "kind": "text",
                    "page": page_number,
                    "bbox": block.bbox,
                    "page_width": block.page_width,
                    "payload": block,
                }
            )
        for table in table_blocks:
            raw_items.append(
                {
                    "kind": "table",
                    "page": page_number,
                    "bbox": table.bbox,
                    "page_width": table.page_width,
                    "payload": table,
                }
            )

    raw_items.sort(key=_order_key)
    elements: list[ExtractedElement] = []
    heading_stack: list[tuple[int, str]] = []
    in_references = False

    for item in raw_items:
        if item["kind"] == "table":
            elements.append(
                _table_element(
                    item["payload"],
                    len(elements),
                    _section_path(heading_stack),
                )
            )
            continue

        block: _TextBlock = item["payload"]
        text = _normalise_text(block.text)
        if not text:
            continue
        if _is_document_artifact(text, block, in_references=in_references):
            continue
        if elements and _looks_like_figure_label(text, block):
            continue

        element_type, level, list_level = _classify_text_block(
            block,
            body_size=body_size,
            is_first_content=not elements,
            in_references=in_references,
        )

        if element_type == "heading":
            if _REFERENCE_HEADING_RE.match(text):
                in_references = True
            heading_stack = [
                (existing_level, title)
                for existing_level, title in heading_stack
                if existing_level < (level or 1)
            ]
            heading_stack.append((level or 1, text))
        elif element_type == "paragraph" and not heading_stack and _EMAIL_RE.search(text):
            continue

        if in_references and element_type == "paragraph" and _REFERENCE_ITEM_RE.match(text):
            element_type = "footnote"

        elements.append(
            ExtractedElement(
                content=text,
                element_type=element_type,
                page_number=block.page_number,
                section_path=_section_path(heading_stack),
                position_index=len(elements),
                heading_level=level if element_type == "heading" else None,
                list_level=list_level,
                inline_spans=_inline_spans(text, block),
                meta_json={
                    "pdf": {
                        "bbox": block.bbox,
                        "page_width": block.page_width,
                        "page_height": block.page_height,
                        "avg_font_size": block.avg_size,
                        "max_font_size": block.max_size,
                        "is_bold": block.is_bold,
                        "is_italic": block.is_italic,
                        "line_count": block.line_count,
                        "confidence": _classification_confidence(element_type, block, body_size),
                    }
                },
            )
        )

    return _attach_captions(_promote_captioned_text_tables(elements))


def _document_body_size(doc: fitz.Document) -> float:
    sizes: list[float] = []
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = float(span.get("size", 0))
                    text = str(span.get("text", "")).strip()
                    if size > 4 and text:
                        sizes.append(size)
    return statistics.median(sizes) if sizes else 12.0


def _extract_text_blocks(page: fitz.Page, page_number: int) -> list[_TextBlock]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    blocks: list[_TextBlock] = []

    for raw in page.get_text("dict").get("blocks", []):
        if raw.get("type") != 0:
            continue

        lines: list[str] = []
        sizes: list[float] = []
        fonts: list[str] = []
        flags: list[int] = []
        for line in raw.get("lines", []):
            parts: list[str] = []
            for span in line.get("spans", []):
                text = str(span.get("text", ""))
                if text.strip():
                    parts.append(text)
                    sizes.append(float(span.get("size", 0)))
                    fonts.append(str(span.get("font", "")))
                    flags.append(int(span.get("flags", 0)))
            line_text = _normalise_text(" ".join(parts))
            if line_text:
                lines.append(line_text)

        text = "\n".join(lines).strip()
        if not text:
            continue

        font_blob = " ".join(fonts).lower()
        bbox = _bbox_dict(raw.get("bbox", (0, 0, 0, 0)))
        blocks.append(
            _TextBlock(
                text=text,
                page_number=page_number,
                bbox=bbox,
                page_width=page_width,
                page_height=page_height,
                line_count=max(1, len(lines)),
                avg_size=sum(sizes) / len(sizes) if sizes else 12.0,
                max_size=max(sizes) if sizes else 12.0,
                is_bold=("bold" in font_blob) or any(flag & 16 for flag in flags),
                is_italic=("italic" in font_blob) or ("oblique" in font_blob) or any(flag & 2 for flag in flags),
                block_no=int(raw.get("number", len(blocks))),
            )
        )

    return _merge_wrapped_text_blocks(sorted(blocks, key=lambda b: (b.bbox["y0"], b.bbox["x0"], b.block_no)))


def _merge_wrapped_text_blocks(blocks: list[_TextBlock]) -> list[_TextBlock]:
    merged: list[_TextBlock] = []
    for block in blocks:
        if not merged:
            merged.append(block)
            continue
        previous = merged[-1]
        gap = block.bbox["y0"] - previous.bbox["y1"]
        same_column = abs(block.bbox["x0"] - previous.bbox["x0"]) < 18
        previous_text = _normalise_text(previous.text)
        current_text = _normalise_text(block.text)
        if (
            same_column
            and 0 <= gap <= max(6, previous.avg_size * 0.45)
            and not _hard_boundary(previous_text)
            and not _starts_structural(current_text)
        ):
            merged[-1] = _TextBlock(
                text=f"{previous.text} {block.text}",
                page_number=previous.page_number,
                bbox={
                    "x0": min(previous.bbox["x0"], block.bbox["x0"]),
                    "y0": min(previous.bbox["y0"], block.bbox["y0"]),
                    "x1": max(previous.bbox["x1"], block.bbox["x1"]),
                    "y1": max(previous.bbox["y1"], block.bbox["y1"]),
                },
                page_width=previous.page_width,
                page_height=previous.page_height,
                line_count=previous.line_count + block.line_count,
                avg_size=(previous.avg_size + block.avg_size) / 2,
                max_size=max(previous.max_size, block.max_size),
                is_bold=previous.is_bold and block.is_bold,
                is_italic=previous.is_italic and block.is_italic,
                block_no=previous.block_no,
            )
        else:
            merged.append(block)
    return merged


def _extract_tables(page: fitz.Page, page_number: int) -> list[_TableBlock]:
    tables: list[_TableBlock] = []
    finder = getattr(page, "find_tables", None)
    if finder is None:
        return tables

    try:
        found = finder()
    except Exception:
        return tables

    for table in getattr(found, "tables", []) or []:
        rows = _normalise_rows(table.extract() or [])
        if not _rows_are_structured(rows):
            continue
        bbox = _bbox_dict(table.bbox)
        tables.append(
            _TableBlock(
                page_number=page_number,
                bbox=bbox,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
                rows=rows,
                extraction_method="pymupdf_find_tables",
                snapshot_image_base64=_table_snapshot(page, bbox),
            )
        )
    return tables


def _table_element(table: _TableBlock, position: int, section_path: str) -> ExtractedElement:
    row_count = len(table.rows)
    column_count = max((len(row) for row in table.rows), default=0)
    non_empty = sum(1 for row in table.rows for cell in row if cell)
    plain_text = "\n".join(" | ".join(row) for row in table.rows)
    meta = {
        "rows": table.rows,
        "table": {
            "mode": "structured",
            "display_mode": "grid",
            "rows": table.rows,
            "row_count": row_count,
            "column_count": column_count,
            "non_empty_cells": non_empty,
            "bbox": table.bbox,
            "page_width": table.page_width,
            "page_height": table.page_height,
            "page_number": table.page_number,
            "plain_text": plain_text,
            "extraction_method": table.extraction_method,
            "confidence": 0.92 if non_empty >= max(4, row_count) else 0.65,
        },
    }
    if table.snapshot_image_base64:
        meta["table"]["snapshot_image_base64"] = table.snapshot_image_base64
        meta["table"]["snapshot_ext"] = "png"

    return ExtractedElement(
        content=plain_text,
        element_type="table",
        page_number=table.page_number,
        section_path=section_path,
        position_index=position,
        heading_level=None,
        meta_json=meta,
    )


def _promote_captioned_text_tables(elements: list[ExtractedElement]) -> list[ExtractedElement]:
    promoted: list[ExtractedElement] = []
    index = 0
    while index < len(elements):
        element = elements[index]
        promoted.append(element)
        if not _is_table_caption(element):
            index += 1
            continue

        rows: list[list[str]] = []
        source_indexes: list[int] = []
        scan = index + 1
        while scan < len(elements):
            candidate = elements[scan]
            if candidate.page_number != element.page_number:
                break
            if candidate.element_type in {"caption", "image", "table"}:
                break
            if candidate.element_type == "heading" and not _looks_like_table_residue(candidate.content):
                break
            row = _split_text_table_row(candidate.content)
            if row is None:
                if rows:
                    break
                scan += 1
                continue
            rows.append(row)
            source_indexes.append(scan)
            scan += 1

        if _enough_text_table_rows(rows) and source_indexes:
            table = _text_table_element(
                caption=element,
                rows=rows,
                position=len(promoted),
                section_path=element.section_path,
            )
            promoted.append(table)
            index = source_indexes[-1] + 1
            continue

        index += 1

    for position, element in enumerate(promoted):
        element.position_index = position
    return promoted


def _enough_text_table_rows(rows: list[list[str]]) -> bool:
    if len(rows) >= 3:
        return True
    if len(rows) == 2 and sum(len(row) for row in rows) >= 8:
        return True
    return False


def _is_table_caption(element: ExtractedElement) -> bool:
    return element.element_type == "caption" and re.match(
        r"^(?:table|таблица|табл\.?)\s*[\dIVXLCА-Яа-яA-Za-z]+",
        element.content,
        re.IGNORECASE,
    ) is not None


def _text_table_element(
    *,
    caption: ExtractedElement,
    rows: list[list[str]],
    position: int,
    section_path: str,
) -> ExtractedElement:
    width = max((len(row) for row in rows), default=0)
    padded = [row + [""] * (width - len(row)) for row in rows]
    plain_text = "\n".join(" | ".join(cell for cell in row if cell) for row in padded)
    return ExtractedElement(
        content=plain_text,
        element_type="table",
        page_number=caption.page_number,
        section_path=section_path,
        position_index=position,
        heading_level=None,
        meta_json={
            "rows": padded,
            "table": {
                "mode": "structured",
                "display_mode": "grid",
                "rows": padded,
                "row_count": len(padded),
                "column_count": width,
                "non_empty_cells": sum(1 for row in padded for cell in row if cell),
                "page_number": caption.page_number,
                "plain_text": plain_text,
                "caption": caption.content,
                "extraction_method": "captioned_text_table",
                "confidence": 0.68,
            },
        },
    )


def _split_text_table_row(text: str) -> list[str] | None:
    text = _normalise_text(text)
    if not text:
        return None
    if _looks_like_table_header_row(text):
        return _split_header_row(text)
    if not _looks_like_table_residue(text):
        return None

    tokens = text.split()
    first_measure_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if _NUMERIC_TABLE_TOKEN_RE.match(token) and index > 0
        ),
        None,
    )
    if first_measure_index is None:
        return tokens

    model = " ".join(tokens[:first_measure_index]).strip()
    values = _merge_numeric_table_tokens(tokens[first_measure_index:])
    return [model, *values] if model else values


def _looks_like_table_header_row(text: str) -> bool:
    lowered = text.casefold()
    keywords = (
        "model",
        "bleu",
        "cost",
        "flops",
        "params",
        "ppl",
        "train",
        "steps",
        "layer",
        "complexity",
        "sequential",
        "operations",
        "path",
    )
    tokens = text.split()
    if sum(1 for keyword in keywords if keyword in lowered) >= 2 and len(tokens) <= 14:
        return True
    return bool(
        2 <= len(tokens) <= 8
        and all(re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z]{1,5})?", token) for token in tokens)
    )


def _split_header_row(text: str) -> list[str]:
    parts = re.split(r"\s{2,}", text)
    if len(parts) > 1:
        return [part.strip() for part in parts if part.strip()]
    return text.split()


def _merge_numeric_table_tokens(tokens: list[str]) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            index + 4 < len(tokens)
            and re.fullmatch(r"\d+[.,]", token)
            and re.fullmatch(r"\d+", tokens[index + 1])
            and tokens[index + 2] in {"·", "•", "x", "×", "*"}
            and tokens[index + 3] == "10"
            and re.fullmatch(r"\d+", tokens[index + 4])
        ):
            values.append(f"{token}{tokens[index + 1]} {tokens[index + 2]} 10^{tokens[index + 4]}")
            index += 5
            continue
        if (
            index + 3 < len(tokens)
            and re.fullmatch(r"\d+(?:[.,]\d+)?", token)
            and tokens[index + 1] in {"·", "•", "x", "×", "*"}
            and tokens[index + 2] == "10"
            and re.fullmatch(r"\d+", tokens[index + 3])
        ):
            values.append(f"{token} {tokens[index + 1]} 10^{tokens[index + 3]}")
            index += 4
            continue
        if token in {".", ","} and values and index + 1 < len(tokens):
            values[-1] = f"{values[-1]}{token}{tokens[index + 1]}"
            index += 2
            continue
        values.append(token)
        index += 1
    return values


def _classify_text_block(
    block: _TextBlock,
    *,
    body_size: float,
    is_first_content: bool,
    in_references: bool,
) -> tuple[str, int | None, int | None]:
    text = _normalise_text(block.text)
    if in_references and _REFERENCE_ITEM_RE.match(text):
        return "footnote", None, None
    if _AUTHOR_NOTE_RE.match(text):
        return "footnote", None, None
    if _REFERENCE_HEADING_RE.match(text):
        return "heading", 1, None
    if _looks_like_caption(text):
        return "caption", None, None
    if _looks_like_table_residue(text):
        return "paragraph", None, None
    if _looks_like_heading(text, block, body_size=body_size, is_first_content=is_first_content):
        return "heading", 1 if is_first_content else _heading_level(text, block, body_size), None
    if _looks_like_list_item(text):
        return "list_item", None, _list_level(block)
    if _looks_like_formula(text):
        return "formula", None, None
    return "paragraph", None, None


def _looks_like_heading(
    text: str,
    block: _TextBlock,
    *,
    body_size: float,
    is_first_content: bool,
) -> bool:
    if len(text) > 180:
        return False
    if _EMAIL_RE.search(text):
        return False
    if _DOCUMENT_METADATA_RE.search(text):
        return False
    if _looks_like_numbered_heading(text):
        return True
    if is_first_content and len(text) <= 140 and not text.endswith("."):
        return True
    if block.max_size >= body_size * 1.22 and len(text) <= 160:
        return True
    if block.is_bold and len(text) <= 120 and not text.endswith("."):
        return True
    return False


def _looks_like_caption(text: str) -> bool:
    if _FIGURE_CAPTION_RE.match(text):
        return True
    if not _TABLE_CAPTION_RE.match(text):
        return False
    return ":" in text[:24] or " - " in text[:24] or " – " in text[:24] or ". " in text[:24]


def _is_document_artifact(text: str, block: _TextBlock, *, in_references: bool) -> bool:
    compact = " ".join(text.split()).strip()
    if not compact:
        return True
    if compact.isdigit() and block.page_height and block.bbox["y0"] > block.page_height * 0.86:
        return True
    if not in_references and _DOCUMENT_METADATA_RE.search(compact):
        return True
    if _EMAIL_RE.search(compact) and len(compact) <= 180:
        return True
    return False


def _looks_like_figure_label(text: str, block: _TextBlock) -> bool:
    if block.line_count > 1:
        return False
    if len(text) > 80 or text.endswith((".", ":", ";")):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z-]+", text)
    if not 2 <= len(words) <= 6:
        return False
    if any(word.islower() for word in words):
        return False
    return any("-" in word for word in words) or any(word in {"Attention", "Softmax", "Linear", "MatMul"} for word in words)


def _page_looks_like_visual_text_salad(blocks: list[_TextBlock]) -> bool:
    if len(blocks) < 24:
        return False
    short = 0
    long_prose = 0
    for block in blocks:
        text = _normalise_text(block.text)
        if _looks_like_caption(text):
            continue
        words = re.findall(r"[A-Za-zА-Яа-я]+", text)
        if len(words) <= 2 and len(text) <= 32:
            short += 1
        if len(words) >= 12 and text.endswith((".", "!", "?")):
            long_prose += 1
    return short >= 18 and long_prose == 0


def _looks_like_pdf_visual_word_salad(text: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-я]+", text or "")
    if len(words) < 18:
        return False
    short_words = sum(1 for word in words if len(word) <= 12)
    unique_ratio = len({word.casefold() for word in words}) / max(len(words), 1)
    punctuation = sum(1 for char in text if char in {",", "-", "–", "—"})
    return short_words / len(words) >= 0.9 and unique_ratio < 0.7 and punctuation >= 3


def _looks_like_numbered_heading(text: str) -> bool:
    match = _NUMBERED_PREFIX_RE.match(text)
    if not match:
        return False
    rest = match.group(2).strip()
    if not rest or len(rest) > 140:
        return False
    if re.search(r"\s\d+(?:[.)]|\.\d+)\s+\S", rest):
        return False
    if rest.endswith((".", ";", ",")):
        return False
    return any(char.isalpha() for char in rest)


def _looks_like_table_residue(text: str) -> bool:
    tokens = text.split()
    if len(tokens) < 2 or len(tokens) > 60:
        return False
    if text.count("O (") >= 2:
        return True
    numeric = sum(1 for token in tokens if _NUMERIC_TABLE_TOKEN_RE.match(token) or any(char.isdigit() for char in token))
    bracket_refs = sum(1 for token in tokens if re.fullmatch(r"\[\d+\]", token))
    short_words = sum(1 for token in tokens if _SHORT_TABLE_WORD_RE.match(token))
    if numeric >= 2 and (short_words + bracket_refs) >= 1:
        return True
    if numeric >= 1 and bracket_refs >= 1 and len(tokens) <= 8:
        return True
    return False


def _heading_level(text: str, block: _TextBlock, body_size: float) -> int:
    if match := _NUMBERED_PREFIX_RE.match(text):
        return min(4, match.group(1).count(".") + 1)
    if block.max_size >= body_size * 1.55:
        return 1
    if block.max_size >= body_size * 1.3:
        return 2
    return 2 if block.is_bold else 3


def _looks_like_list_item(text: str) -> bool:
    if _BULLET_RE.match(text):
        return True
    if _NUMBERED_PREFIX_RE.match(text):
        return not _looks_like_numbered_heading(text)
    return False


def _list_level(block: _TextBlock) -> int:
    left_ratio = block.bbox["x0"] / max(block.page_width, 1)
    if left_ratio > 0.18:
        return 2
    if left_ratio > 0.11:
        return 1
    return 0


def _looks_like_formula(text: str) -> bool:
    if len(text) > 220:
        return False
    if not _INLINE_MATH_SYMBOL_RE.search(text):
        return False
    alpha = sum(char.isalpha() for char in text)
    operators = len(_INLINE_MATH_SYMBOL_RE.findall(text))
    return operators >= 1 and alpha <= max(80, len(text) * 0.65)


def _inline_spans(text: str, block: _TextBlock) -> list[dict[str, Any]] | None:
    spans: list[dict[str, Any]] = []
    if block.is_italic and text:
        spans.append({"start": 0, "end": len(text), "style": "italic"})
    if _looks_like_formula(text):
        spans.append({"start": 0, "end": len(text), "style": "math"})
    return spans or None


def _classification_confidence(element_type: str, block: _TextBlock, body_size: float) -> float:
    if element_type in {"caption", "footnote", "formula"}:
        return 0.9
    if element_type == "heading":
        if _looks_like_numbered_heading(_normalise_text(block.text)):
            return 0.92
        if block.max_size >= body_size * 1.22 or block.is_bold:
            return 0.82
        return 0.68
    if element_type == "list_item":
        return 0.82
    return 0.75


def _attach_captions(elements: list[ExtractedElement]) -> list[ExtractedElement]:
    for index, element in enumerate(elements):
        if element.element_type != "caption":
            continue
        target_index = _caption_target_index(elements, index)
        if target_index is None:
            continue
        group_id = str(uuid.uuid4())
        caption_meta = dict(element.meta_json or {})
        caption_meta["caption_group_id"] = group_id
        caption_meta["caption_target_position"] = elements[target_index].position_index
        element.meta_json = caption_meta

        target = elements[target_index]
        target_meta = dict(target.meta_json or {})
        target_meta["caption_group_id"] = group_id
        target_meta["caption"] = {
            "text": element.content,
            "position_index": element.position_index,
        }
        if isinstance(target_meta.get("table"), dict):
            target_meta["table"] = {
                **target_meta["table"],
                "caption": element.content,
                "caption_group_id": group_id,
            }
        target.meta_json = target_meta
    return elements


def _caption_target_index(elements: list[ExtractedElement], caption_index: int) -> int | None:
    page = elements[caption_index].page_number
    for step in (1, -1, 2, -2):
        index = caption_index + step
        if index < 0 or index >= len(elements):
            continue
        candidate = elements[index]
        if candidate.page_number != page:
            continue
        if candidate.element_type in {"table", "image"}:
            return index
    return None


def _order_key(item: dict[str, Any]) -> tuple[int, int, float, float, float]:
    bbox = item["bbox"]
    page_width = max(float(item.get("page_width") or 1), 1)
    column = 0 if bbox["x0"] < page_width * 0.52 else 1
    return (item["page"], column, bbox["y0"], bbox["x0"], bbox["y1"])


def _bbox_dict(value: Any) -> dict[str, float]:
    x0, y0, x1, y1 = value
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)}


def _bbox_overlap_ratio(left: dict[str, float], right: dict[str, float]) -> float:
    x0 = max(left["x0"], right["x0"])
    y0 = max(left["y0"], right["y0"])
    x1 = min(left["x1"], right["x1"])
    y1 = min(left["y1"], right["y1"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    overlap = (x1 - x0) * (y1 - y0)
    left_area = max((left["x1"] - left["x0"]) * (left["y1"] - left["y0"]), 1)
    return overlap / left_area


def _table_snapshot(page: fitz.Page, bbox: dict[str, float]) -> str | None:
    try:
        rect = fitz.Rect(
            max(0, bbox["x0"] - 6),
            max(0, bbox["y0"] - 6),
            min(page.rect.width, bbox["x1"] + 6),
            min(page.rect.height, bbox["y1"] + 6),
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
        return base64.b64encode(pix.tobytes("png")).decode("ascii")
    except Exception:
        return None


def _normalise_rows(rows: list[list[Any]]) -> list[list[str]]:
    normalized = [
        [_normalise_table_cell(str(cell or "")) for cell in row]
        for row in rows
        if isinstance(row, list)
    ]
    return _merge_continuation_table_rows([row for row in normalized if any(cell for cell in row)])


def _normalise_table_cell(value: str) -> str:
    cell = _normalise_text(value)
    if "http://" in cell or "https://" in cell:
        cell = _compact_urls(cell)
    cell = re.sub(r"\s+/", "/", cell)
    cell = re.sub(r"/\s+", "/", cell)
    return cell


def _compact_urls(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "")

    return re.sub(r"https?://[^\s]+(?:\s+[^\s]+)*", replace, value)


def _merge_continuation_table_rows(rows: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    for row in rows:
        if (
            merged
            and len(row) >= 2
            and not any(cell for cell in row[:-1])
            and row[-1]
        ):
            previous = merged[-1]
            while len(previous) < len(row):
                previous.append("")
            previous[-1] = f"{previous[-1]}{row[-1]}" if previous[-1] else row[-1]
            continue
        merged.append(row)
    return merged


def _rows_are_structured(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False
    width = max((len(row) for row in rows), default=0)
    if width < 2:
        return False
    non_empty = sum(1 for row in rows for cell in row if cell)
    occupancy = non_empty / max(len(rows) * width, 1)
    if width > 12 and occupancy < 0.55:
        return False
    if width > 18:
        return False
    multi = sum(1 for row in rows if sum(1 for cell in row if cell) >= 2)
    return multi >= min(2, len(rows))


def _normalise_text(value: str) -> str:
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    text = " ".join(line for line in lines if line)
    text = re.sub(r"(?<=[a-z])-\s+(?=[a-z])", "", text)
    text = re.sub(
        r"\[\s*([0-9,\s]+)\s*\]",
        lambda match: "[" + ", ".join(part for part in match.group(1).replace(" ", "").split(",") if part) + "]",
        text,
    )
    text = re.sub(r"\b([A-Za-z]{5,})(aligned|based)\b", r"\1-\2", text)
    text = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", text)
    text = re.sub(r"(?<=\d)\s+([+\-−])\s+(?=\d)", r"\1", text)
    text = re.sub(r"(?<=\d)\s+([.,])\s+(?=\d)", r"\1", text)
    text = re.sub(r"(https?://)\s+", r"\1", text)
    text = re.sub(r"(/\s+)(?=[A-Za-z0-9_./-])", "/", text)
    return (
        text.replace(" ,", ",")
        .replace(" .", ".")
        .replace(" ;", ";")
        .replace(" :", ":")
        .replace("( ", "(")
        .replace(" )", ")")
        .strip()
    )


def _hard_boundary(text: str) -> bool:
    return (
        text.endswith((".", "?", "!", ":"))
        or _looks_like_caption(text)
        or _REFERENCE_HEADING_RE.match(text) is not None
        or _looks_like_numbered_heading(text)
    )


def _starts_structural(text: str) -> bool:
    return (
        _BULLET_RE.match(text) is not None
        or _looks_like_caption(text)
        or _REFERENCE_HEADING_RE.match(text) is not None
        or _looks_like_numbered_heading(text)
    )


def _section_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(title for _level, title in stack)
