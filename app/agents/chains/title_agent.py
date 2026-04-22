"""Phase 1 TitleAgent with deterministic fallback."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentQuotaError, AgentTransientError, get_chat_llm, with_retry
from app.config import settings


class TitleAgentOutput(BaseModel):
    title: str
    description: str | None = None
    suggested_structural_block: str | None = None


def _offline_mode() -> bool:
    return settings.groq_api_key in {"", "test-groq-key", "dummy", "test"}


def _preview(fragments: list[Any], max_chars: int = 1800) -> str:
    parts: list[str] = []
    total = 0
    for fragment in fragments:
        text = " ".join(fragment.content.split()).strip()
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(parts)


def _fallback_description(fragments: list[Any]) -> str | None:
    for fragment in fragments:
        if fragment.element_type.value not in {"paragraph", "quote", "code_block"}:
            continue
        text = " ".join(fragment.content.split()).strip()
        if text:
            return text[:280]
    return None


def _fallback_block(candidate: dict[str, Any], available_blocks: list[str]) -> str | None:
    for value in (candidate.get("suggested_section"), candidate.get("source_section_path")):
        if value and value in available_blocks:
            return value
    return None


def _fallback(candidate: dict[str, Any], available_blocks: list[str]) -> TitleAgentOutput:
    return TitleAgentOutput(
        title=candidate["proposed_title"],
        description=_fallback_description(candidate["fragments"]),
        suggested_structural_block=_fallback_block(candidate, available_blocks),
    )


@with_retry
async def _invoke_llm(payload: dict[str, Any]) -> TitleAgentOutput:
    prompt = (
        "You generate metadata for a knowledge-base article. "
        "Return JSON only with keys: title, description, suggested_structural_block. "
        "Description should be 1-2 sentences. "
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
        return _fallback(candidate, available_blocks)

    payload = {
        "project_name": project_name,
        "kb_summary": kb_summary,
        "current_title": candidate["proposed_title"],
        "source_section_path": candidate.get("source_section_path"),
        "internal_headings": candidate.get("internal_headings") or [],
        "fragment_preview": _preview(candidate["fragments"]),
        "available_structural_blocks": available_blocks,
    }
    try:
        return await _invoke_llm(payload)
    except Exception:
        return _fallback(candidate, available_blocks)
