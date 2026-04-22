from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
import base64
from typing import Any, Optional

import fitz  # PyMuPDF
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run


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
        seen_images: set[int] = set()
        page_num = page.number + 1
        for image in page.get_images(full=True):
            xref = image[0]
            if xref in seen_images:
                continue
            seen_images.add(xref)
            extracted = doc.extract_image(xref)
            ext = extracted.get("ext", "png")
            data = extracted.get("image")
            if data:
                elements.append(ExtractedElement(
                    content="[Image]",
                    element_type="image",
                    page_number=page_num,
                    section_path=" > ".join(h[1] for h in heading_stack),
                    position_index=idx,
                    heading_level=None,
                    list_level=None,
                    inline_spans=None,
                    meta_json={
                        "ext": ext,
                        "image_base64": base64.b64encode(data).decode("ascii"),
                    },
                ))
                idx += 1

        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue

            lines_text: list[str] = []
            span_sizes: list[float] = []
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
                        if _MONOSPACE_HINT_RE.search(font_name):
                            inline_spans.append({"start": start, "end": end, "style": "code", "data": None})
                            monospace_spans += 1
                        cursor = end + 1
                    if span["size"] > 4:
                        span_sizes.append(span["size"])
                    total_spans += 1
                if line_parts:
                    lines_text.append(" ".join(line_parts))

            full_text = " ".join(lines_text).strip()
            if not full_text:
                continue

            avg_size = sum(span_sizes) / len(span_sizes) if span_sizes else body_size
            is_bold = total_spans > 0 and (bold_spans / total_spans) >= 0.5
            is_italic = total_spans > 0 and (italic_spans / total_spans) >= 0.5
            is_monospace = total_spans > 0 and (monospace_spans / total_spans) >= 0.5

            el_type, h_level = _classify_pdf_block(
                full_text,
                avg_size,
                body_size,
                is_bold,
                is_italic,
                is_monospace,
            )

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
                list_level=None,
                inline_spans=inline_spans or None,
                meta_json=None,
            ))
            idx += 1

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
    if is_monospace and len(text) > 10:
        return "code_block", None
    if is_italic and len(text) < 400:
        return "quote", None
    if text.startswith(("\"", "“", "”", "'")) and len(text) < 400:
        return "quote", None
    if text.startswith(("- ", "• ", "* ", "◦ ")) or re.match(r"^\d+[\.\)]\s", text):
        return "list_item", None
    return "paragraph", None


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

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            image_runs = _extract_inline_images(para)
            for image in image_runs:
                image["section_path"] = " > ".join(h[1] for h in heading_stack)
                image["position_index"] = idx
                elements.append(ExtractedElement(**image))
                idx += 1
            continue

        list_level = _get_list_level(para)
        inline_spans = _extract_inline_spans(para)
        el_type, h_level = _classify_docx_para(para, list_level=list_level)

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

        for image in _extract_inline_images(para):
            image["section_path"] = section_path
            image["position_index"] = idx
            elements.append(ExtractedElement(**image))
            idx += 1

    for table in doc.tables:
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if cells:
                rows.append(cells)
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
