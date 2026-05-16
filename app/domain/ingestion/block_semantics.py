from __future__ import annotations

from typing import Any


ROLE_BODY = "body"
ROLE_TITLE = "title"
ROLE_HEADING = "heading"
ROLE_LIST = "list"
ROLE_CAPTION = "caption"
ROLE_REFERENCE = "reference"
ROLE_FOOTNOTE = "footnote"
ROLE_TABLE = "table"
ROLE_FIGURE = "figure"
ROLE_FORMULA = "formula"
ROLE_METADATA = "metadata"
ROLE_HEADER_FOOTER = "header_footer"
ROLE_PAGE_NUMBER = "page_number"
ROLE_ARTIFACT = "artifact"

VISIBILITY_ARTICLE = "article"
VISIBILITY_METADATA = "metadata"
VISIBILITY_HIDDEN = "hidden"
VISIBILITY_FALLBACK = "fallback"

SEMANTIC_META_KEY = "semantic"


def semantic_meta(
    *,
    role: str,
    visibility: str,
    confidence: float,
    extraction_method: str,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "role": role,
        "visibility": visibility,
        "confidence": max(0.0, min(1.0, confidence)),
        "extraction_method": extraction_method,
        "issues": issues or [],
    }


def with_semantic_meta(
    meta_json: dict[str, Any] | None,
    *,
    role: str,
    visibility: str,
    confidence: float,
    extraction_method: str,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    meta = dict(meta_json or {})
    meta[SEMANTIC_META_KEY] = semantic_meta(
        role=role,
        visibility=visibility,
        confidence=confidence,
        extraction_method=extraction_method,
        issues=issues,
    )
    # Keep top-level mirrors for simple SQL/debug inspection and legacy callers.
    meta["role"] = role
    meta["visibility"] = visibility
    meta["confidence"] = max(0.0, min(1.0, confidence))
    if issues:
        meta["issues"] = issues
    meta["extraction_method"] = extraction_method
    return meta


def get_visibility(meta_json: dict[str, Any] | None) -> str:
    if not meta_json:
        return VISIBILITY_ARTICLE
    semantic = meta_json.get(SEMANTIC_META_KEY)
    if isinstance(semantic, dict) and isinstance(semantic.get("visibility"), str):
        return semantic["visibility"]
    visibility = meta_json.get("visibility")
    return visibility if isinstance(visibility, str) else VISIBILITY_ARTICLE


def get_role(meta_json: dict[str, Any] | None) -> str:
    if not meta_json:
        return ROLE_BODY
    semantic = meta_json.get(SEMANTIC_META_KEY)
    if isinstance(semantic, dict) and isinstance(semantic.get("role"), str):
        return semantic["role"]
    role = meta_json.get("role")
    return role if isinstance(role, str) else ROLE_BODY


def is_article_visible(meta_json: dict[str, Any] | None) -> bool:
    return get_visibility(meta_json) == VISIBILITY_ARTICLE
