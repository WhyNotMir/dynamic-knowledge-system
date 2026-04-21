"""Structure agent — LangChain implementation (Phase 0 Slice C).

Behaviour stays 1:1 with the pre-LangChain structure agent it replaced:

* Per-candidate title: LLM call, retried on transient network errors.
  On quota / rate-limit / persistent-HTTP errors we flip into fallback
  mode for the rest of the proposal and synthesize titles from the
  source heading (``_fallback_title``).
* Hierarchy: a single LLM call that groups the candidate titles into
  2–5 sections. On any failure we fall back to flat grouping (section =
  ``source_section_path`` or ``"General"``).
* Public entry point ``propose_structure(candidates)`` has the exact
  same signature + side-effects as before — tests monkeypatch it by
  string path, so the shape is load-bearing.

The LangChain parts:
* ``ChatGroq`` via ``get_chat_llm()`` — same Groq model from config.
* ``ChatPromptTemplate`` for both calls.
* ``PydanticOutputParser`` for hierarchy (structured output with a
  simple recovery on malformed JSON).
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.agents.base import (
    AgentQuotaError,
    AgentTransientError,
    get_chat_llm,
    with_retry,
)
from app.agents.prompts.structure_prompts import HIERARCHY_PROMPT, TITLE_PROMPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        return hint[:250]
    return f"Article {index + 1}"


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


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public entry point (same signature as the legacy agent)
# ---------------------------------------------------------------------------


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
                candidate["proposed_title"], "General"
            )
    else:
        for candidate in candidates:
            candidate["suggested_section"] = (
                candidate.get("source_section_path") or "General"
            )

    return candidates
