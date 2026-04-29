from __future__ import annotations

import re

from app.models.article_candidate import ArticleCandidateFragment
from app.models.source_fragment import ElementType

_NOISE_ONLY_RE = re.compile(r"^[\W\d_]+$")


def normalise_heading_text(value: str | None) -> str:
    if not value:
        return ""
    collapsed = " ".join(value.split())
    return collapsed.strip().casefold()


def looks_like_noise_text(value: str | None) -> bool:
    if not value:
        return True

    stripped = " ".join(value.split()).strip()
    if not stripped:
        return True

    lowered = stripped.casefold()
    if lowered in {"<eos>", "eos"}:
        return True

    bad_markers = ("arxiv:", "[cs.", "gnmt", "en-de", "en-fr", "wsj")
    if any(marker in lowered for marker in bad_markers):
        return True

    alpha = sum(char.isalpha() for char in stripped)
    digits = sum(char.isdigit() for char in stripped)
    if len(stripped) <= 2 and alpha == 0:
        return True
    if _NOISE_ONLY_RE.fullmatch(stripped):
        return True
    if alpha == 0 and digits > 0:
        return True
    if stripped.isupper() and digits > 0 and alpha < 6:
        return True

    return False


def is_meaningful_body_fragment(fragment) -> bool:
    if fragment is None:
        return False
    if looks_like_noise_text(fragment.content):
        return False
    return fragment.element_type in {
        ElementType.PARAGRAPH,
        ElementType.LIST_ITEM,
        ElementType.TABLE,
        ElementType.QUOTE,
        ElementType.CODE_BLOCK,
        ElementType.IMAGE,
    }


def candidate_description(
    candidate_fragments: list[ArticleCandidateFragment],
) -> str | None:
    for item in candidate_fragments:
        fragment = item.fragment
        if fragment is None:
            continue
        if fragment.element_type in {
            ElementType.PARAGRAPH,
            ElementType.QUOTE,
            ElementType.CODE_BLOCK,
        }:
            text = " ".join(fragment.content.split()).strip()
            if text:
                return text[:280]
    return None


def internal_headings(candidate_fragments: list[ArticleCandidateFragment]) -> list[str]:
    headings: list[str] = []
    seen: set[str] = set()
    for item in candidate_fragments:
        fragment = item.fragment
        if fragment is None:
            continue
        if fragment.element_type != ElementType.HEADING:
            continue
        if (fragment.heading_level or 0) < 2:
            continue
        text = fragment.content.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        headings.append(text)
    return headings
