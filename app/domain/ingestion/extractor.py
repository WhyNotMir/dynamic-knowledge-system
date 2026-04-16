from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from docx import Document as DocxDocument


@dataclass
class ExtractedElement:
    content: str
    element_type: str         # heading | paragraph | list_item | table | caption
    page_number: Optional[int]
    section_path: str
    position_index: int
    heading_level: Optional[int]


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

    # Pass 2: extract and classify
    for page in doc:
        page_num = page.number + 1
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:   # skip image blocks
                continue

            lines_text: list[str] = []
            span_sizes: list[float] = []
            bold_spans = 0
            total_spans = 0

            for line in block["lines"]:
                line_parts: list[str] = []
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        line_parts.append(text)
                    if span["size"] > 4:
                        span_sizes.append(span["size"])
                    # PyMuPDF flags bitmask: bit 4 (value 16) = bold
                    if span["flags"] & 16:
                        bold_spans += 1
                    total_spans += 1
                if line_parts:
                    lines_text.append(" ".join(line_parts))

            full_text = " ".join(lines_text).strip()
            if not full_text:
                continue

            avg_size = sum(span_sizes) / len(span_sizes) if span_sizes else body_size
            is_bold = total_spans > 0 and (bold_spans / total_spans) >= 0.5

            el_type, h_level = _classify_pdf_block(full_text, avg_size, body_size, is_bold)

            if el_type == "heading" and h_level:
                while heading_stack and heading_stack[-1][0] >= h_level:
                    heading_stack.pop()
                heading_stack.append((h_level, full_text))

            section_path = " > ".join(
                h[1] for h in heading_stack if h[0] < (h_level or 99)
            )

            elements.append(ExtractedElement(
                content=full_text,
                element_type=el_type,
                page_number=page_num,
                section_path=section_path,
                position_index=idx,
                heading_level=h_level,
            ))
            idx += 1

    doc.close()
    return elements


def _classify_pdf_block(
    text: str, avg_size: float, body_size: float, is_bold: bool
) -> tuple[str, Optional[int]]:
    ratio = avg_size / body_size if body_size else 1.0
    short = len(text) < 150

    if ratio >= 1.5 and short:
        return "heading", 1
    if ratio >= 1.25 and short:
        return "heading", 2
    if ratio >= 1.1 and short:
        return "heading", 3
    if is_bold and short and len(text) > 3:
        return "heading", 3   # bold + short = structural heading
    if text.isupper() and short and len(text) > 3:
        return "heading", 2
    if text.startswith(("- ", "• ", "* ", "◦ ")) or re.match(r"^\d+[\.\)]\s", text):
        return "list_item", None
    return "paragraph", None


# ─── DOCX ────────────────────────────────────────────────────────────────────

_HEADING_STYLE_RE = re.compile(r"^Heading\s*(\d+)$", re.IGNORECASE)


def _extract_docx(file_path: str) -> list[ExtractedElement]:
    doc = DocxDocument(file_path)
    elements: list[ExtractedElement] = []
    heading_stack: list[tuple[int, str]] = []
    idx = 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        el_type, h_level = _classify_docx_para(para.style.name or "")

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
        ))
        idx += 1

    for table in doc.tables:
        rows: list[str] = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            elements.append(ExtractedElement(
                content="\n".join(rows),
                element_type="table",
                page_number=None,
                section_path=" > ".join(h[1] for h in heading_stack),
                position_index=idx,
                heading_level=None,
            ))
            idx += 1

    return elements


def _classify_docx_para(style_name: str) -> tuple[str, Optional[int]]:
    m = _HEADING_STYLE_RE.match(style_name)
    if m:
        return "heading", int(m.group(1))
    sl = style_name.lower()
    if "list" in sl or "bullet" in sl:
        return "list_item", None
    if "caption" in sl:
        return "caption", None
    return "paragraph", None