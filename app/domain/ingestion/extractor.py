from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from app.domain.ingestion.block_semantics import (
    ROLE_BODY,
    ROLE_CAPTION,
    ROLE_FIGURE,
    ROLE_FOOTNOTE,
    ROLE_FORMULA,
    ROLE_HEADING,
    ROLE_LIST,
    ROLE_REFERENCE,
    ROLE_TABLE,
    ROLE_TITLE,
    VISIBILITY_ARTICLE,
    with_semantic_meta,
)


_SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
_CAPTION_RE = re.compile(
    r"^(?:table|figure|fig\.?|таблица|табл\.?|рисунок|рис\.?)\s*[\wIVXLCА-Яа-я]+(?:[.:)\-–]|\s)",
    re.IGNORECASE,
)
_FIGURE_CAPTION_RE = re.compile(r"^(?:figure|fig\.?|рисунок|рис\.?)\s+", re.IGNORECASE)
_REFERENCE_HEADING_RE = re.compile(
    r"^(?:references|bibliography|works cited|литература|список литературы|источники)$",
    re.IGNORECASE,
)
_REFERENCE_ITEM_RE = re.compile(r"^(?:\[\d+\]|\d+[.)])\s+\S+")


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


def extract(file_path: str) -> list[ExtractedElement]:
    path = Path(file_path)
    if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported document type for extraction: {path.suffix.lower()}")

    result = _converter().convert(path)
    elements = _normalise_docling_document(result.document, source_ext=path.suffix.lower())
    return _attach_captions(elements)


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    pdf_options = PdfPipelineOptions()
    pdf_options.do_ocr = False
    pdf_options.do_table_structure = True
    for option_name in ("generate_picture_images", "generate_page_images", "images_scale"):
        if hasattr(pdf_options, option_name):
            try:
                setattr(pdf_options, option_name, True if option_name != "images_scale" else 1.5)
            except Exception:
                pass

    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        },
    )


def _normalise_docling_document(document: Any, *, source_ext: str) -> list[ExtractedElement]:
    elements: list[ExtractedElement] = []
    heading_stack: list[tuple[int, str]] = []
    in_references = False

    for item, raw_level in document.iterate_items():
        label = _label_value(item)
        text = _normalise_text(getattr(item, "text", "") or "")
        position = len(elements)

        if label == "table":
            element = _table_element(item, document, position, _section_path(heading_stack), source_ext)
        elif label in {"picture", "figure"}:
            element = _picture_element(item, position, _section_path(heading_stack), source_ext)
        else:
            if not text:
                continue
            element_type, heading_level, list_level = _classify_text(
                label=label,
                text=text,
                raw_level=raw_level,
                is_first_content=not elements,
                in_references=in_references,
            )
            if element_type == "heading":
                if _REFERENCE_HEADING_RE.match(text):
                    in_references = True
                heading_stack = [
                    (level, title)
                    for level, title in heading_stack
                    if level < (heading_level or 1)
                ]
                heading_stack.append((heading_level or 1, text))
            elif in_references and element_type != "heading":
                element_type = "footnote"

            element = _text_element(
                item=item,
                text=text,
                element_type=element_type,
                heading_level=heading_level,
                list_level=list_level,
                section_path=_section_path(heading_stack),
                position=position,
                source_ext=source_ext,
                in_references=in_references,
                is_first_content=position == 0,
            )

        elements.append(element)

    return elements


def _classify_text(
    *,
    label: str,
    text: str,
    raw_level: int | None,
    is_first_content: bool,
    in_references: bool,
) -> tuple[str, int | None, int | None]:
    if label in {"section_header", "title"}:
        return "heading", _heading_level(raw_level), None
    if label == "list_item":
        return "list_item", None, max(0, (_safe_int(raw_level) or 1) - 1)
    if label in {"code", "code_item"}:
        return "code_block", None, None
    if label in {"formula", "equation"}:
        return "formula", None, None
    if label in {"footnote", "reference"}:
        return "footnote", None, None
    if _CAPTION_RE.match(text):
        return "caption", None, None
    if in_references and _REFERENCE_ITEM_RE.match(text):
        return "footnote", None, None
    if is_first_content and len(text) <= 180 and not text.endswith((".", "?", "!")):
        return "heading", 1, None
    return "paragraph", None, None


def _text_element(
    *,
    item: Any,
    text: str,
    element_type: str,
    heading_level: int | None,
    list_level: int | None,
    section_path: str,
    position: int,
    source_ext: str,
    in_references: bool,
    is_first_content: bool,
) -> ExtractedElement:
    role = _role_for_text(
        element_type=element_type,
        heading_level=heading_level,
        in_references=in_references,
        is_first_content=is_first_content,
    )
    meta = _base_meta(item, source_ext=source_ext)
    if in_references and element_type == "footnote":
        meta["references_section"] = True
    if element_type == "footnote":
        marker = _footnote_marker(text)
        if marker:
            meta["footnote"] = marker
    return ExtractedElement(
        content=text,
        element_type=element_type,
        page_number=_page_number(item),
        section_path=section_path,
        position_index=position,
        heading_level=heading_level,
        list_level=list_level,
        inline_spans=None,
        meta_json=with_semantic_meta(
            meta,
            role=role,
            visibility=VISIBILITY_ARTICLE,
            confidence=0.86,
            extraction_method="docling",
        ),
    )


def _table_element(
    item: Any,
    document: Any,
    position: int,
    section_path: str,
    source_ext: str,
) -> ExtractedElement:
    rows = _table_rows(item, document)
    plain_text = "\n".join(" | ".join(cell for cell in row if cell) for row in rows).strip()
    content = plain_text or _normalise_text(getattr(item, "text", "") or "") or "Table"
    meta = _base_meta(item, source_ext=source_ext)
    meta.update(
        {
            "rows": rows,
            "table": {
                "rows": rows,
                "plain_text": content,
                "display_mode": "grid" if rows else "text",
                "confidence": 0.82 if rows else 0.55,
                "bbox": meta.get("docling", {}).get("bbox"),
                "extraction_method": "docling_table",
            },
        }
    )
    return ExtractedElement(
        content=content,
        element_type="table",
        page_number=_page_number(item),
        section_path=section_path,
        position_index=position,
        heading_level=None,
        meta_json=with_semantic_meta(
            meta,
            role=ROLE_TABLE,
            visibility=VISIBILITY_ARTICLE,
            confidence=0.82 if rows else 0.55,
            extraction_method="docling_table",
        ),
    )


def _picture_element(
    item: Any,
    position: int,
    section_path: str,
    source_ext: str,
) -> ExtractedElement:
    meta = _base_meta(item, source_ext=source_ext)
    image_payload = _picture_base64(item)
    if image_payload:
        meta.update(image_payload)
    meta["image"] = {
        "bbox": meta.get("docling", {}).get("bbox"),
        "extraction_method": "docling_picture",
    }
    return ExtractedElement(
        content=_caption_text(item) or "Source figure",
        element_type="image",
        page_number=_page_number(item),
        section_path=section_path,
        position_index=position,
        heading_level=None,
        meta_json=with_semantic_meta(
            meta,
            role=ROLE_FIGURE,
            visibility=VISIBILITY_ARTICLE,
            confidence=0.72,
            extraction_method="docling_picture",
        ),
    )


def _base_meta(item: Any, *, source_ext: str) -> dict[str, Any]:
    prov = _first_provenance(item)
    bbox = _bbox_dict(getattr(prov, "bbox", None))
    page_number = getattr(prov, "page_no", None)
    docling_meta: dict[str, Any] = {
        "label": _label_value(item),
        "source_format": source_ext.lstrip("."),
    }
    if page_number is not None:
        docling_meta["page"] = {"number": page_number}
    if bbox:
        docling_meta["bbox"] = bbox

    meta: dict[str, Any] = {"docling": docling_meta}
    if source_ext == ".pdf":
        line_count = max(1, text_line_count(getattr(item, "text", "") or ""))
        font_size = 14.0 if _label_value(item) in {"section_header", "title"} else 12.0
        meta["pdf"] = {
            "page": {"number": page_number},
            "bbox": bbox,
            "layout": {"line_count": line_count, "span_count": 1},
            "font": {"avg_size": font_size, "dominant_name": "docling"},
            "extraction_method": "docling",
        }
    return meta


def _table_rows(item: Any, document: Any) -> list[list[str]]:
    try:
        dataframe = item.export_to_dataframe(doc=document)
    except Exception:
        return []
    rows: list[list[str]] = []
    for row in dataframe.fillna("").astype(str).values.tolist():
        cleaned = [_normalise_text(cell) for cell in row]
        if any(cleaned):
            rows.append(cleaned)
    return rows


def _picture_base64(item: Any) -> dict[str, Any] | None:
    image = getattr(item, "image", None)
    pil_image = getattr(image, "pil_image", None) or getattr(item, "pil_image", None)
    if pil_image is None:
        get_image = getattr(item, "get_image", None)
        if callable(get_image):
            try:
                pil_image = get_image()
            except Exception:
                pil_image = None
    if pil_image is None:
        return None
    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return {
        "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "ext": "png",
    }


def _attach_captions(elements: list[ExtractedElement]) -> list[ExtractedElement]:
    for index, element in enumerate(elements):
        if element.element_type != "caption":
            continue
        target_index = _caption_target_index(elements, index)
        if target_index is None:
            continue
        group_id = f"caption-{index}-{target_index}"
        target = elements[target_index]
        target_kind = "image" if target.element_type == "image" else "table"
        element.meta_json = dict(element.meta_json or {})
        target.meta_json = dict(target.meta_json or {})
        element.meta_json.update(
            {
                "caption_group_id": group_id,
                "caption": {"target_kind": target_kind, "text": element.content},
            }
        )
        target.meta_json.update(
            {
                "caption_group_id": group_id,
                "caption": {"target_kind": target_kind, "text": element.content},
            }
        )
    return elements


def _caption_target_index(elements: list[ExtractedElement], caption_index: int) -> int | None:
    caption = elements[caption_index]
    preferred = "image" if _FIGURE_CAPTION_RE.match(caption.content) else "table"
    for offset in (1, 2, 3, -1, -2, -3):
        target_index = caption_index + offset
        if target_index < 0 or target_index >= len(elements):
            continue
        if elements[target_index].element_type == preferred:
            return target_index
    for offset in (1, 2, 3, -1, -2, -3):
        target_index = caption_index + offset
        if target_index < 0 or target_index >= len(elements):
            continue
        if elements[target_index].element_type in {"table", "image"}:
            return target_index
    return None


def _role_for_text(
    *,
    element_type: str,
    heading_level: int | None,
    in_references: bool,
    is_first_content: bool,
) -> str:
    if in_references and element_type == "footnote":
        return ROLE_REFERENCE
    if element_type == "heading":
        return ROLE_TITLE if is_first_content or heading_level == 1 else ROLE_HEADING
    if element_type == "list_item":
        return ROLE_LIST
    if element_type == "caption":
        return ROLE_CAPTION
    if element_type == "footnote":
        return ROLE_FOOTNOTE
    if element_type == "formula":
        return ROLE_FORMULA
    return ROLE_BODY


def _label_value(item: Any) -> str:
    label = getattr(item, "label", "")
    return str(getattr(label, "value", label) or "").casefold()


def _heading_level(raw_level: int | None) -> int:
    value = _safe_int(raw_level) or 1
    return max(1, value - 1) if value > 1 else 1


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _section_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(title for _level, title in stack)


def _page_number(item: Any) -> int | None:
    prov = _first_provenance(item)
    value = getattr(prov, "page_no", None)
    return _safe_int(value)


def _first_provenance(item: Any) -> Any | None:
    prov = getattr(item, "prov", None) or []
    return prov[0] if prov else None


def _bbox_dict(bbox: Any) -> dict[str, float] | None:
    if bbox is None:
        return None
    left = getattr(bbox, "l", None)
    top = getattr(bbox, "t", None)
    right = getattr(bbox, "r", None)
    bottom = getattr(bbox, "b", None)
    values = [left, top, right, bottom]
    if any(value is None for value in values):
        return None
    x0, y0, x1, y1 = (float(value) for value in values)
    return {
        "x0": min(x0, x1),
        "y0": min(y0, y1),
        "x1": max(x0, x1),
        "y1": max(y0, y1),
        "width": abs(x1 - x0),
        "height": abs(y1 - y0),
    }


def _caption_text(item: Any) -> str | None:
    caption_text = getattr(item, "caption_text", None)
    if isinstance(caption_text, str) and caption_text.strip():
        return _normalise_text(caption_text)
    return None


def _footnote_marker(text: str) -> dict[str, str] | None:
    match = re.match(r"^([*†‡§]|\[\d+\]|\d+[.)])", text)
    if not match:
        return None
    marker = match.group(1)
    return {
        "marker": marker,
        "marker_style": "symbol" if marker in {"*", "†", "‡", "§"} else "numeric",
    }


def _normalise_text(value: str) -> str:
    return " ".join(value.split()).strip()


def text_line_count(value: str) -> int:
    return len([line for line in value.splitlines() if line.strip()]) or 1
