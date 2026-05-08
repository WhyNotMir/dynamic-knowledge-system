from __future__ import annotations

import re

from app.models.article_candidate import ArticleCandidateFragment
from app.models.source_fragment import ElementType

_NOISE_ONLY_RE = re.compile(r"^[\W\d_]+$")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
_AFFILIATION_RE = re.compile(
    r"\b(university|institute|laboratory|department|research|school|faculty)\b",
    re.IGNORECASE,
)
_DOCUMENT_HEADER_ARTIFACT_RE = re.compile(
    r"(?:\barxiv\s*:\s*\d{4}\.\d{4,5}(?:v\d+)?\b|\[[a-z]{2}\.[a-z]{2}\])",
    re.IGNORECASE,
)


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
    if _looks_like_author_contact_text(stripped):
        return True

    if _DOCUMENT_HEADER_ARTIFACT_RE.search(stripped) and len(stripped) <= 120:
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


def looks_like_noise_fragment(fragment) -> bool:
    """Context-aware noise check for source/article blocks.

    Some PDF artifacts look noisy as raw text, but are meaningful once their
    element type is known. Tables, references, captions, formulas, and long
    scientific prose must not pass through the same text-only filter as page
    headers, footers, author contacts, or OCR residue.
    """
    if fragment is None:
        return True

    content = getattr(fragment, "content", None)
    if content is None:
        return True

    element_type = getattr(fragment, "element_type", None)
    value = getattr(element_type, "value", element_type)

    if value in {
        ElementType.TABLE.value,
        ElementType.IMAGE.value,
        ElementType.QUOTE.value,
        ElementType.CODE_BLOCK.value,
        ElementType.FORMULA.value,
    }:
        return False

    stripped = " ".join(str(content).split()).strip()
    if not stripped:
        return True

    if value == ElementType.CAPTION.value:
        return stripped.casefold() in {"<eos>", "eos"}

    if value == ElementType.FOOTNOTE.value:
        meta_json = getattr(fragment, "meta_json", None) or {}
        if meta_json.get("references_section") is True:
            return False
        if re.match(r"^\[\d+\]\s+", stripped):
            return False

    if value == ElementType.PARAGRAPH.value and _looks_like_scientific_sentence(stripped):
        return False

    return looks_like_noise_text(stripped)


def _looks_like_scientific_sentence(value: str) -> bool:
    """Return True for long prose even if it mentions PDF/table markers."""
    if len(value) < 80:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z-]+", value)
    if len(words) < 10:
        return False
    return any(char in value for char in ".;,") or any(
        token in value for token in ("Table ", "Figure ", "Section ")
    )


def _looks_like_author_contact_text(value: str) -> bool:
    if not _EMAIL_RE.search(value):
        return False
    if len(value) <= 260:
        return True
    if _AFFILIATION_RE.search(value) and len(value) <= 600:
        return True
    return False


def is_meaningful_body_fragment(fragment) -> bool:
    if fragment is None:
        return False
    if looks_like_noise_fragment(fragment):
        return False
    return fragment.element_type in {
        ElementType.PARAGRAPH,
        ElementType.LIST_ITEM,
        ElementType.TABLE,
        ElementType.QUOTE,
        ElementType.CODE_BLOCK,
        ElementType.IMAGE,
        ElementType.FOOTNOTE,
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
