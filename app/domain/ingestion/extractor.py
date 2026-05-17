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
from PIL import ImageOps

from app.domain.ingestion.block_semantics import (
    ROLE_BODY,
    ROLE_CAPTION,
    ROLE_FIGURE,
    ROLE_FOOTNOTE,
    ROLE_FORMULA,
    ROLE_HEADING,
    ROLE_LIST,
    ROLE_METADATA,
    ROLE_REFERENCE,
    ROLE_TABLE,
    ROLE_TITLE,
    VISIBILITY_ARTICLE,
    VISIBILITY_HIDDEN,
    VISIBILITY_METADATA,
    with_semantic_meta,
)


_SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
_CAPTION_RE = re.compile(
    r"^(?:table|figure|fig\.?|таблица|табл\.?|рисунок|рис\.?)\s*[\wIVXLCА-Яа-я]+[.:)\-–]",
    re.IGNORECASE,
)
_FIGURE_CAPTION_RE = re.compile(r"^(?:figure|fig\.?|рисунок|рис\.?)\s+", re.IGNORECASE)
_REFERENCE_HEADING_RE = re.compile(
    r"^(?:references|bibliography|works cited|литература|список литературы|источники)$",
    re.IGNORECASE,
)
_REFERENCE_ITEM_RE = re.compile(r"^(?:\[\d+\]|\d+[.)])\s+\S+")
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[.)])?\s+\S+")
_PAREN_NUMBERED_HEADING_RE = re.compile(r"^\((\d+)\)\s+\S+")
_ROMAN_HEADING_RE = re.compile(r"^[IVXL]+\.\s+\S+", re.IGNORECASE)
_LETTER_HEADING_RE = re.compile(r"^[A-Z]\.\s+\S+")
_DOCUMENT_METADATA_RE = re.compile(
    r"(?:provided proper attribution|grants permission to reproduce|"
    r"permission to reproduce|all rights reserved|copyright|"
    r"\barxiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?\b|"
    r"\bconference on\b|\bproceedings\b|"
    r"^\(?table\s+deleted\)?$)",
    re.IGNORECASE,
)
_BYLINE_RE = re.compile(r"^by\s+[A-ZА-Я][A-Za-zА-Яа-я.\-'\s]{2,120}$", re.IGNORECASE)
_DIALOGUE_PROMPT_RE = re.compile(r"^[A-ZА-Я]\s*:\s+.{8,}[?？]$")
_FIGURE_PANEL_LABEL_RE = re.compile(r"^(?:[A-Za-z]+-[A-Za-z]+\s+)?Layer\s*\d+$|^[A-Za-z]+-[A-Za-z]+\s+Layer\s*\d+$", re.IGNORECASE)
_TRAILING_FIGURE_PANEL_LABEL_RE = re.compile(r"\s+[A-Za-z]+-[A-Za-z]+\s+Layer\s*\d+$", re.IGNORECASE)


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
    elements = _reconstruct_document_stream(elements)
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
            element = _picture_element(item, document, position, _section_path(heading_stack), source_ext)
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
                elif in_references:
                    in_references = False
                heading_stack = [
                    (level, title)
                    for level, title in heading_stack
                    if level < (heading_level or 1)
                ]
                heading_stack.append((heading_level or 1, text))
            elif in_references and element_type not in {"heading", "caption"}:
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
        if _looks_like_dialogue_prompt(text):
            return "paragraph", None, None
        return "heading", _heading_level_from_text(text) or _heading_level(raw_level), None
    if label == "list_item":
        return "list_item", None, max(0, (_safe_int(raw_level) or 1) - 1)
    if label in {"code", "code_item"}:
        return "code_block", None, None
    if label in {"formula", "equation"}:
        return "formula", None, None
    if _CAPTION_RE.match(text):
        return "caption", None, None
    if label in {"footnote", "reference"}:
        return "footnote", None, None
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
    visibility = VISIBILITY_ARTICLE
    confidence = 0.86
    meta = _base_meta(item, source_ext=source_ext)
    if not in_references and _looks_like_document_metadata(text):
        role = ROLE_METADATA
        visibility = VISIBILITY_METADATA
        confidence = 0.72
        meta["reconstruction"] = {"reason": "document_metadata_noise"}
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
            visibility=visibility,
            confidence=confidence,
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
    document: Any,
    position: int,
    section_path: str,
    source_ext: str,
) -> ExtractedElement:
    meta = _base_meta(item, source_ext=source_ext)
    image_payload = _picture_base64(item, document)
    issues: list[str] = []
    if image_payload:
        meta.update(image_payload)
    else:
        issues.append("missing_image_payload")
    meta["image"] = {
        "bbox": meta.get("docling", {}).get("bbox"),
        "extraction_method": "docling_picture",
        "has_payload": bool(image_payload),
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
            confidence=0.72 if image_payload else 0.45,
            extraction_method="docling_picture",
            issues=issues,
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


def _picture_base64(item: Any, document: Any) -> dict[str, Any] | None:
    image = getattr(item, "image", None)
    pil_image = getattr(image, "pil_image", None) or getattr(item, "pil_image", None)
    if pil_image is None:
        get_image = getattr(item, "get_image", None)
        if callable(get_image):
            try:
                pil_image = get_image(document)
            except Exception:
                try:
                    pil_image = get_image()
                except Exception:
                    pil_image = None
    if pil_image is None:
        return None
    pil_image = ImageOps.exif_transpose(pil_image)
    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return {
        "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "ext": "png",
        "image_width": pil_image.width,
        "image_height": pil_image.height,
    }


def _attach_captions(elements: list[ExtractedElement]) -> list[ExtractedElement]:
    for index, element in enumerate(elements):
        if element.element_type != "caption":
            continue
        target_indexes = _caption_target_indexes(elements, index)
        if not target_indexes:
            continue
        group_id = f"caption-{index}-{'-'.join(str(target_index) for target_index in target_indexes)}"
        target_kind = "image" if any(elements[target_index].element_type == "image" for target_index in target_indexes) else "table"
        element.meta_json = dict(element.meta_json or {})
        element.meta_json.update(
            {
                "caption_group_id": group_id,
                "caption": {"target_kind": target_kind, "text": element.content},
            }
        )
        for target_index in target_indexes:
            target = elements[target_index]
            target.meta_json = dict(target.meta_json or {})
            target.meta_json.update(
                {
                    "caption_group_id": group_id,
                    "caption": {"target_kind": target_kind, "text": element.content},
                }
            )
    return elements


def _reconstruct_document_stream(elements: list[ExtractedElement]) -> list[ExtractedElement]:
    """Apply DKS reconstruction that Docling intentionally does not own."""
    _clean_figure_panel_heading_labels(elements)
    _hide_figure_panel_label_fragments(elements)
    _hide_duplicate_heading_preludes(elements)
    _rebuild_section_paths(elements)
    return elements


def _clean_figure_panel_heading_labels(elements: list[ExtractedElement]) -> None:
    for element in elements:
        if element.element_type != "heading":
            continue
        cleaned = _TRAILING_FIGURE_PANEL_LABEL_RE.sub("", element.content).strip()
        if cleaned and cleaned != element.content:
            element.content = cleaned
            element.meta_json = {
                **(element.meta_json or {}),
                "reconstruction": {"reason": "trailing_figure_panel_label_removed"},
            }


def _hide_figure_panel_label_fragments(elements: list[ExtractedElement]) -> None:
    for index, element in enumerate(elements):
        if element.element_type not in {"paragraph", "heading"}:
            continue
        if not _looks_like_figure_panel_label(element.content):
            continue
        if not any(candidate.element_type == "image" for candidate in elements[index + 1 : index + 3]):
            continue
        element.meta_json = with_semantic_meta(
            {
                **(element.meta_json or {}),
                "reconstruction": {"reason": "standalone_figure_panel_label"},
            },
            role=ROLE_METADATA,
            visibility=VISIBILITY_HIDDEN,
            confidence=0.88,
            extraction_method="dks_reconstruction",
        )


def _hide_duplicate_heading_preludes(elements: list[ExtractedElement]) -> None:
    for index, element in enumerate(elements[:-1]):
        if element.element_type != "heading" or _numeric_prefix(element.content):
            continue
        next_heading = next(
            (
                candidate
                for candidate in elements[index + 1 : index + 8]
                if candidate.element_type == "heading"
            ),
            None,
        )
        if next_heading is None or not _numeric_prefix(next_heading.content):
            continue
        if _clean_heading_key(element.content) != _clean_heading_key(next_heading.content):
            continue
        element.meta_json = with_semantic_meta(
            {
                **(element.meta_json or {}),
                "reconstruction": {
                    "reason": "duplicate_unnumbered_heading_before_numbered_heading",
                    "duplicate_of": next_heading.content,
                },
            },
            role=ROLE_METADATA,
            visibility=VISIBILITY_HIDDEN,
            confidence=0.9,
            extraction_method="dks_reconstruction",
        )


def _rebuild_section_paths(elements: list[ExtractedElement]) -> None:
    heading_stack: list[tuple[int, str]] = []
    for element in elements:
        if element.element_type == "heading":
            if _is_hidden_or_metadata(element):
                element.section_path = _section_path(heading_stack)
                continue
            level = element.heading_level or 1
            heading_stack = [
                (existing_level, title)
                for existing_level, title in heading_stack
                if existing_level < level
            ]
            heading_stack.append((level, element.content))
            element.section_path = _section_path(heading_stack)
            continue
        element.section_path = _section_path(heading_stack)


def _is_hidden_or_metadata(element: ExtractedElement) -> bool:
    visibility = (element.meta_json or {}).get("visibility")
    semantic = (element.meta_json or {}).get("semantic")
    if isinstance(semantic, dict):
        visibility = semantic.get("visibility", visibility)
    return visibility in {VISIBILITY_HIDDEN, VISIBILITY_METADATA}


def _caption_target_indexes(elements: list[ExtractedElement], caption_index: int) -> list[int]:
    caption = elements[caption_index]
    preferred = "image" if _FIGURE_CAPTION_RE.match(caption.content) else "table"
    geometric = _caption_target_by_geometry(elements, caption_index, preferred)
    if geometric is not None:
        return _expand_multi_image_caption_targets(elements, caption_index, geometric)
    for offset in (1, 2, 3, -1, -2, -3):
        target_index = caption_index + offset
        if target_index < 0 or target_index >= len(elements):
            continue
        if elements[target_index].element_type == preferred:
            return _expand_multi_image_caption_targets(elements, caption_index, target_index)
    for offset in (1, 2, 3, -1, -2, -3):
        target_index = caption_index + offset
        if target_index < 0 or target_index >= len(elements):
            continue
        if elements[target_index].element_type in {"table", "image"}:
            return _expand_multi_image_caption_targets(elements, caption_index, target_index)
    return []


def _expand_multi_image_caption_targets(
    elements: list[ExtractedElement],
    caption_index: int,
    primary_index: int,
) -> list[int]:
    caption = elements[caption_index]
    if elements[primary_index].element_type != "image" or not _caption_implies_multiple_panels(caption.content):
        return [primary_index]

    indexes = {primary_index}
    for index, candidate in enumerate(elements):
        if candidate.element_type != "image":
            continue
        if candidate.page_number != caption.page_number:
            continue
        if abs(index - caption_index) > 5:
            continue
        indexes.add(index)
    return sorted(indexes)


def _caption_implies_multiple_panels(value: str) -> bool:
    text = value.casefold()
    return any(token in text for token in ("left", "right", "top", "bottom"))


def _caption_target_by_geometry(
    elements: list[ExtractedElement],
    caption_index: int,
    preferred: str,
) -> int | None:
    caption = elements[caption_index]
    caption_bbox = _meta_bbox(caption.meta_json)
    if caption_bbox is None or caption.page_number is None:
        return None

    best_index: int | None = None
    best_score: float | None = None
    for index, candidate in enumerate(elements):
        if index == caption_index:
            continue
        if candidate.page_number != caption.page_number:
            continue
        if candidate.element_type not in {"table", "image"}:
            continue
        candidate_bbox = _meta_bbox(candidate.meta_json)
        if candidate_bbox is None:
            continue
        type_penalty = 0.0 if candidate.element_type == preferred else 500.0
        score = _bbox_distance(caption_bbox, candidate_bbox) + type_penalty
        if best_score is None or score < best_score:
            best_score = score
            best_index = index
    return best_index


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


def _heading_level_from_text(value: str) -> int | None:
    text = (value or "").strip()
    if _ROMAN_HEADING_RE.match(text):
        return 1
    if _LETTER_HEADING_RE.match(text):
        return 2
    if _PAREN_NUMBERED_HEADING_RE.match(text):
        return 2
    prefix = _numeric_prefix(value)
    if not prefix:
        return None
    return min(6, prefix.count(".") + 1)


def _numeric_prefix(value: str | None) -> str | None:
    match = _NUMBERED_HEADING_RE.match((value or "").strip())
    return match.group(1) if match else None


def _clean_heading_key(value: str | None) -> str:
    text = re.sub(r"^\d+(?:\.\d+)*(?:[.)])?\s*", "", value or "")
    return " ".join(text.split()).casefold()


def _looks_like_document_metadata(value: str) -> bool:
    text = " ".join(value.split()).strip()
    if not text:
        return False
    if _DOCUMENT_METADATA_RE.search(text):
        return True
    if _BYLINE_RE.match(text):
        return True
    if "@" in text and len(text) < 500:
        return True
    return False


def _looks_like_dialogue_prompt(value: str) -> bool:
    return bool(_DIALOGUE_PROMPT_RE.match(" ".join(value.split()).strip()))


def _looks_like_figure_panel_label(value: str) -> bool:
    return bool(_FIGURE_PANEL_LABEL_RE.match(" ".join(value.split()).strip()))


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


def _meta_bbox(meta_json: dict[str, Any] | None) -> dict[str, float] | None:
    if not meta_json:
        return None
    for path in (("docling", "bbox"), ("pdf", "bbox"), ("table", "bbox"), ("image", "bbox")):
        value: Any = meta_json
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, dict) and {"x0", "y0", "x1", "y1"}.issubset(value):
            return value
    return None


def _bbox_distance(left: dict[str, float], right: dict[str, float]) -> float:
    left_center_x = (float(left["x0"]) + float(left["x1"])) / 2
    right_center_x = (float(right["x0"]) + float(right["x1"])) / 2
    horizontal = abs(left_center_x - right_center_x)

    if float(left["y0"]) > float(right["y1"]):
        vertical = float(left["y0"]) - float(right["y1"])
    elif float(right["y0"]) > float(left["y1"]):
        vertical = float(right["y0"]) - float(left["y1"])
    else:
        vertical = 0.0
    return horizontal * 0.25 + vertical


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
