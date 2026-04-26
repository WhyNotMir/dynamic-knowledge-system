"""Phase 1 TitleAgent with deterministic fallback."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentQuotaError, AgentTransientError, get_chat_llm, with_retry
from app.config import settings


class TitleAgentOutput(BaseModel):
    title: str
    description: str | None = None
    suggested_structural_block: str | None = None


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _offline_mode() -> bool:
    return settings.groq_api_key in {"", "test-groq-key", "dummy", "test"}


def _article_blocks(fragments: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for fragment in fragments:
        text = " ".join(fragment.content.split()).strip()
        if not text:
            continue
        blocks.append(
            {
                "element_type": getattr(getattr(fragment, "element_type", None), "value", str(fragment.element_type)),
                "content": text,
                "heading_level": getattr(fragment, "heading_level", None),
                "list_level": getattr(fragment, "list_level", None),
                "page_number": getattr(fragment, "page_number", None),
                "section_path": getattr(fragment, "section_path", None),
            }
        )
    return blocks


def _description_from_text(text: str, max_chars: int = 320) -> str | None:
    collapsed = " ".join(text.split()).strip()
    if not collapsed:
        return None

    sentences = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(collapsed) if part.strip()]
    if not sentences:
        return collapsed[:max_chars]

    selected: list[str] = []
    total = 0
    for sentence in sentences[:3]:
        projected = total + len(sentence) + (1 if selected else 0)
        if selected and projected > max_chars:
            break
        selected.append(sentence)
        total = projected

    if selected:
        return " ".join(selected)[:max_chars]
    return collapsed[:max_chars]


def _trim_trailing_period(value: str) -> str:
    return value.strip().rstrip(".")


def _generic_description(
    *,
    title: str | None,
    internal_headings: list[str] | None = None,
    fallback_text: str | None = None,
) -> str | None:
    topic = _trim_trailing_period(title or "").strip()
    headings = [heading.strip() for heading in (internal_headings or []) if heading.strip()]

    if topic and headings:
        focus = ", ".join(headings[:3])
        if len(headings) > 3:
            focus += ", and related topics"
        return f"This article discusses {topic}, including {focus}."

    if topic and fallback_text:
        snippet = _trim_trailing_period(fallback_text)
        lowered = snippet[:1].lower() + snippet[1:] if snippet[:1].isupper() else snippet
        return f"This article discusses {topic} and covers how {lowered}."

    if topic:
        return f"This article discusses {topic}."

    if fallback_text:
        snippet = _trim_trailing_period(fallback_text)
        return f"This article discusses {snippet[:1].lower() + snippet[1:] if snippet[:1].isupper() else snippet}."

    return None


def _fallback_description(
    fragments: list[Any],
    *,
    title: str | None = None,
    internal_headings: list[str] | None = None,
) -> str | None:
    parts: list[str] = []
    for fragment in fragments:
        element_type = getattr(getattr(fragment, "element_type", None), "value", str(fragment.element_type))
        if element_type not in {"paragraph", "quote", "code_block", "list_item"}:
            continue
        text = " ".join(fragment.content.split()).strip()
        if not text:
            continue
        parts.append(text)
        if len(" ".join(parts)) >= 900:
            break

    summary = _description_from_text(" ".join(parts))
    return _generic_description(
        title=title,
        internal_headings=internal_headings,
        fallback_text=summary,
    )


def _fallback_block(candidate: dict[str, Any], available_blocks: list[str]) -> str | None:
    for value in (candidate.get("suggested_section"), candidate.get("source_section_path")):
        if value and value in available_blocks:
            return value
    return None


def _fallback(candidate: dict[str, Any], available_blocks: list[str]) -> TitleAgentOutput:
    return TitleAgentOutput(
        title=candidate["proposed_title"],
        description=_fallback_description(
            candidate["fragments"],
            title=candidate.get("proposed_title"),
            internal_headings=candidate.get("internal_headings") or [],
        ),
        suggested_structural_block=_fallback_block(candidate, available_blocks),
    )


def _apply_description_style(
    output: TitleAgentOutput,
    *,
    title: str | None,
    internal_headings: list[str] | None,
) -> TitleAgentOutput:
    description = (output.description or "").strip()
    if not description:
        output.description = _generic_description(
            title=title,
            internal_headings=internal_headings,
        )
        return output

    if description.casefold().startswith("this article discusses"):
        return output

    output.description = _generic_description(
        title=title,
        internal_headings=internal_headings,
        fallback_text=description,
    )
    return output


@with_retry
async def _invoke_llm(payload: dict[str, Any]) -> TitleAgentOutput:
    prompt = (
        "You generate metadata for a knowledge-base article. "
        "Return JSON only with keys: title, description, suggested_structural_block. "
        "Description should be 1-2 sentences, high-level, and start with 'This article discusses ...'. "
        "Keep it general and editorial rather than quoting or paraphrasing opening lines too literally. "
        "Summarise the whole article, not just the opening lines. "
        "Use the full article_blocks list as the main context. "
        "Only return suggested_structural_block if it exactly matches one of the provided block names."
    )
    try:
        message = await get_chat_llm(temperature=0.2).ainvoke(
            [("system", prompt), ("user", json.dumps(payload, ensure_ascii=False))]
        )
    except Exception as exc:
        name = exc.__class__.__name__
        if name in {"APIConnectionError", "APITimeoutError"}:
            raise AgentTransientError(str(exc))
        raise AgentQuotaError(str(exc))

    raw = getattr(message, "content", str(message)).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    return TitleAgentOutput.model_validate(json.loads(raw))


async def enrich_candidate_metadata(
    *,
    project_name: str,
    kb_summary: str | None,
    candidate: dict[str, Any],
    available_blocks: list[str],
) -> TitleAgentOutput:
    if _offline_mode():
        return _apply_description_style(
            _fallback(candidate, available_blocks),
            title=candidate.get("proposed_title"),
            internal_headings=candidate.get("internal_headings") or [],
        )

    payload = {
        "project_name": project_name,
        "kb_summary": kb_summary,
        "current_title": candidate["proposed_title"],
        "source_section_path": candidate.get("source_section_path"),
        "internal_headings": candidate.get("internal_headings") or [],
        "article_blocks": _article_blocks(candidate["fragments"]),
        "available_structural_blocks": available_blocks,
    }
    try:
        return _apply_description_style(
            await _invoke_llm(payload),
            title=candidate.get("proposed_title"),
            internal_headings=candidate.get("internal_headings") or [],
        )
    except Exception:
        return _apply_description_style(
            _fallback(candidate, available_blocks),
            title=candidate.get("proposed_title"),
            internal_headings=candidate.get("internal_headings") or [],
        )
