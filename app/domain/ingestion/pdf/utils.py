from __future__ import annotations

from dataclasses import dataclass
import re

PDF_CAPTION_RE = re.compile(r"^(figure|fig\.|table)\s+\d+[:.\s]", re.IGNORECASE)
PDF_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+[A-Za-z]")
PDF_FOOTNOTE_RE = re.compile(r"^\[\d+\]\s")
PDF_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
PDF_AFFILIATION_RE = re.compile(
    r"\b(university|institute|research|laboratory|lab|school|department|google brain|google research)\b",
    re.IGNORECASE,
)
PDF_LICENSE_RE = re.compile(
    r"\b(permission|reproduce|copyright|grants permission|journalistic|scholarly works)\b",
    re.IGNORECASE,
)
PDF_GARBAGE_TOKEN_RE = re.compile(
    r"^(?:<eos>|[a-z]{0,2}\d{2,}|[\d.\s\-+/()]{4,})$",
    re.IGNORECASE,
)
PDF_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
PDF_METADATA_LINE_RE = re.compile(
    r"^(arxiv:|31st conference on neural information processing systems)",
    re.IGNORECASE,
)
MONOSPACE_HINT_RE = re.compile(r"(courier|consolas|menlo|monaco|code)", re.IGNORECASE)


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
