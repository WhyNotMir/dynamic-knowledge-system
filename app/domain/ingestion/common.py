from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
