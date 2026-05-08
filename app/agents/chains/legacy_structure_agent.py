"""Structure agent for candidate titles and section grouping.

The public entry point enriches candidate dictionaries with proposed article
titles and top-level sections. If the LLM is unavailable, deterministic
fallbacks keep the proposal pipeline moving.
"""
from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from app.agents.base import (
    AgentQuotaError,
    AgentTransientError,
    get_chat_llm,
    with_retry,
)
from app.agents.prompts.structure_prompts import HIERARCHY_PROMPT, TITLE_PROMPT


def _preview(fragments: list, max_chars: int = 2500) -> str:
    """Concatenate fragment contents up to a rough char budget."""
    lines: list[str] = []
    total = 0
    for fragment in fragments:
        lines.append(fragment.content)
        total += len(fragment.content)
        if total >= max_chars:
            break
    return "\n".join(lines)


def _fallback_title(hint: str | None, index: int) -> str:
    """Heuristic title used when the LLM is unavailable.

    Keeps the pipeline unblocked on rate-limit / quota errors — the user
    can rename via the PATCH candidate endpoint.
    """
    if hint:
        return _clean_source_heading_title(hint)[:250]
    return f"Article {index + 1}"


def _clean_source_heading_title(hint: str | None) -> str:
    if not hint:
        return ""
    leaf = " ".join(hint.split(" > ")[-1].split()).strip()
    if not leaf:
        return ""
    numbered = leaf.split(" ", 1)
    if len(numbered) == 2 and _looks_like_numbered_heading_prefix(numbered[0]):
        return numbered[1].strip()
    return leaf


def _looks_like_numbered_heading_prefix(value: str) -> bool:
    parts = value.split(".")
    return all(part.isdigit() for part in parts if part) and bool(parts)


def _dedupe_titles(candidates: list[dict[str, Any]]) -> None:
    """Ensure proposal titles are unique within one proposal.

    PDF heuristics and LLM outputs can easily collapse multiple neighbouring
    candidates into the same short title. That makes the review screen look
    broken and later causes confusing article slugs. We keep the first title
    untouched and only rewrite later duplicates deterministically.
    """
    seen: dict[str, int] = {}
    normalized_seen: dict[str, int] = {}

    for candidate in candidates:
        title = " ".join(candidate["proposed_title"].split()).strip() or "Article"
        base = title
        key = base.casefold()
        normalized_key = _title_similarity_key(base)
        count = seen.get(key, 0)
        near_duplicate_count = normalized_seen.get(normalized_key, 0)

        if count or near_duplicate_count:
            hint = (candidate.get("source_section_path") or "").split(" > ")
            leaf = _clean_source_heading_title(hint[-1].strip() if hint else "")
            leaf_key = _title_similarity_key(leaf)
            if leaf and leaf_key != normalized_key:
                title = f"{base}: {leaf}"[:250]
                dedupe_key = title.casefold()
                suffix = 2
                while dedupe_key in seen:
                    title = f"{base}: {leaf} ({suffix})"[:250]
                    dedupe_key = title.casefold()
                    suffix += 1
                key = dedupe_key
            else:
                title = f"{base} ({max(count, near_duplicate_count) + 1})"[:250]
                key = title.casefold()

        candidate["proposed_title"] = title
        seen[key] = seen.get(key, 0) + 1
        normalized_seen[_title_similarity_key(title)] = normalized_seen.get(_title_similarity_key(title), 0) + 1


def _title_similarity_key(value: str | None) -> str:
    if not value:
        return ""
    cleaned = " ".join(value.split()).strip().casefold()
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned)
    cleaned = "".join(char if char.isalnum() else " " for char in cleaned)
    return " ".join(cleaned.split())


def _classify_llm_error(exc: Exception) -> Exception:
    """Map provider-specific errors into our normalised AgentError family.

    LangChain surfaces the underlying Groq SDK exception on failure, so
    we inspect the type by name to keep the import surface small (the
    provider SDKs are heavy).
    """
    name = exc.__class__.__name__
    if name in {"RateLimitError", "AuthenticationError", "PermissionDeniedError"}:
        return AgentQuotaError(str(exc))
    if name in {"APIConnectionError", "APITimeoutError"}:
        return AgentTransientError(str(exc))
    # Default: treat any other provider error as quota-class so we fall
    # back deterministically instead of looping.
    return AgentQuotaError(str(exc))


def _strip_json_fence(raw: str) -> str:
    """Remove a surrounding ```json ... ``` fence if present."""
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    inner = raw.split("```")
    if len(inner) < 2:
        return raw
    body = inner[1]
    if body.startswith("json"):
        body = body[4:]
    return body.strip()


@with_retry
async def _propose_title_llm(content: str, hint: str | None) -> str:
    """Call the title chain once, with transient retries."""
    chain = TITLE_PROMPT | get_chat_llm(temperature=0.3)
    hint_line = f"Document section: {hint}\n" if hint else ""
    try:
        message = await chain.ainvoke({"hint": hint_line, "content": content})
    except Exception as exc:
        raise _classify_llm_error(exc)
    text = getattr(message, "content", str(message))
    return text.strip().strip('"').strip("'")


@with_retry
async def _propose_hierarchy_llm(titles: list[str]) -> dict[str, list[str]]:
    """Call the hierarchy chain once, with transient retries."""
    chain = HIERARCHY_PROMPT | get_chat_llm(temperature=0.3)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    try:
        message = await chain.ainvoke({"numbered": numbered})
    except Exception as exc:
        raise _classify_llm_error(exc)

    raw = getattr(message, "content", str(message))
    cleaned = _strip_json_fence(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse hierarchy JSON, falling back to flat")
        return {"General": list(titles)}

    if not isinstance(parsed, dict):
        logger.warning(
            f"Hierarchy JSON was not an object (got {type(parsed).__name__}); "
            "falling back to flat"
        )
        return {"General": list(titles)}

    # Defensive coercion — values must be lists of strings.
    result: dict[str, list[str]] = {}
    for section, entries in parsed.items():
        if isinstance(entries, list):
            result[str(section)] = [str(t) for t in entries]
    return result or {"General": list(titles)}


async def propose_structure(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich candidates with ``proposed_title`` and ``suggested_section``.

    The pipeline stays unblocked even if the LLM is unavailable: on any
    unrecoverable Groq error we fall back to heuristic titles built
    from ``source_section_path`` and a flat hierarchy. Users can always
    rename via the PATCH candidate endpoint.
    """
    llm_available = True

    for index, candidate in enumerate(candidates):
        hint = candidate.get("source_section_path")
        content = _preview(candidate["fragments"])

        if llm_available:
            try:
                candidate["proposed_title"] = await _propose_title_llm(content, hint)
                logger.debug(f"Title: {candidate['proposed_title']!r}")
                continue
            except AgentTransientError as exc:
                logger.warning(
                    f"Structure agent: transient error after retries ({exc}); "
                    "falling back to heuristic titles for the rest of this proposal."
                )
                llm_available = False
            except AgentQuotaError as exc:
                logger.warning(
                    f"Structure agent: quota exhausted ({exc}); "
                    "falling back to heuristic titles for the rest of this proposal."
                )
                llm_available = False

        candidate["proposed_title"] = _fallback_title(hint, index)

    _dedupe_titles(candidates)

    titles = [c["proposed_title"] for c in candidates]
    hierarchy: dict[str, list[str]] | None = None
    if llm_available and len(titles) > 1:
        try:
            hierarchy = await _propose_hierarchy_llm(titles)
        except (AgentTransientError, AgentQuotaError) as exc:
            logger.warning(
                f"Structure agent: hierarchy generation failed ({exc}); "
                "using flat fallback."
            )
            hierarchy = None

    if hierarchy:
        title_to_section = {
            title: section for section, entries in hierarchy.items() for title in entries
        }
        for candidate in candidates:
            candidate["suggested_section"] = title_to_section.get(
                candidate["proposed_title"],
                candidate.get("source_section_path") or "General",
            )
    else:
        for candidate in candidates:
            candidate["suggested_section"] = candidate.get("source_section_path") or "General"

    return candidates
