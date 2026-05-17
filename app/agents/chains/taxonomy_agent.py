from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentQuotaError, AgentTransientError, get_chat_llm, with_retry
from app.config import settings


class TaxonomyAssignment(BaseModel):
    candidate_index: int
    structural_path: list[str] = Field(default_factory=list)


class TaxonomyAgentOutput(BaseModel):
    assignments: list[TaxonomyAssignment] = Field(default_factory=list)


def _offline_mode() -> bool:
    return settings.groq_api_key in {"", "test-groq-key", "dummy", "test"}


def _clean_segment(value: str | None) -> str:
    text = " ".join((value or "").split()).strip()
    text = re.sub(r"^(?:[IVXL]+|[A-Z]|\d+(?:\.\d+)*)(?:[.)])?\s+", "", text).strip()
    return text[:96] or "General"


def _path_key(path: str) -> str:
    return " > ".join(_clean_segment(part).casefold() for part in path.split(">") if part.strip())


def _best_existing_path(title: str, existing_paths: list[str]) -> str | None:
    title_key = _clean_segment(title).casefold()
    for path in existing_paths:
        if _path_key(path).split(" > ")[-1:] == [title_key]:
            return path
    return None


def _bucket(title: str, source_path: str | None) -> str:
    text = f"{title} {source_path or ''}".casefold()
    if any(token in text for token in ("abstract", "introduction", "background", "motivation")):
        return "Foundations"
    if any(token in text for token in ("architecture", "attention", "encoding", "normalization", "forward pass")):
        return "Architecture"
    if any(token in text for token in ("training", "optimization", "pre-training", "distributed")):
        return "Training"
    if any(token in text for token in ("bert", "gpt", "t5", "roberta", "xlnet", "electra", "variant")):
        return "Model Variants"
    if any(token in text for token in ("vision", "speech", "multimodal", "translation", "summarization", "healthcare", "search", "application")):
        return "Applications"
    if any(token in text for token in ("complexity", "constraint", "efficient", "context", "quantization", "mixture", "scaling")):
        return "Efficiency and Constraints"
    if any(token in text for token in ("conclusion", "future", "challenge", "outlook")):
        return "Outlook"
    if any(token in text for token in ("reference", "bibliography", "acknowledg")):
        return "Source Apparatus"
    return "General"


def _fallback_assignments(candidates: list[dict[str, Any]], existing_paths: list[str]) -> dict[int, str]:
    assignments: dict[int, str] = {}
    for index, candidate in enumerate(candidates):
        title = str(candidate.get("title") or candidate.get("proposed_title") or "Article")
        existing = _best_existing_path(title, existing_paths)
        if existing:
            assignments[index] = existing
            continue
        assignments[index] = f"Knowledge Base > {_bucket(title, candidate.get('source_section_path'))}"
    return assignments


def _strip_json_fence(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    parts = raw.split("```")
    if len(parts) < 2:
        return raw
    body = parts[1]
    if body.startswith("json"):
        body = body[4:]
    return body.strip()


@with_retry
async def _invoke_llm(payload: dict[str, Any]) -> TaxonomyAgentOutput:
    prompt = (
        "You design a semantic taxonomy for a living knowledge base. "
        "Return JSON only: {\"assignments\":[{\"candidate_index\":0,\"structural_path\":[\"...\",\"...\"]}]}. "
        "Use 2-4 concise levels. Prefer semantic categories over document order. "
        "Reuse existing_structural_paths when they fit. Do not include numbering like I., A., 1. "
        "Do not create one category per article unless necessary."
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
    return TaxonomyAgentOutput.model_validate(json.loads(_strip_json_fence(getattr(message, "content", str(message)))))


async def propose_taxonomy_paths(
    *,
    project_name: str,
    kb_summary: str | None,
    candidates: list[dict[str, Any]],
    existing_structural_paths: list[str],
) -> dict[int, str]:
    if _offline_mode():
        return _fallback_assignments(candidates, existing_structural_paths)

    payload = {
        "project_name": project_name,
        "kb_summary": kb_summary,
        "existing_structural_paths": existing_structural_paths,
        "candidates": [
            {
                "candidate_index": index,
                "title": candidate.get("title") or candidate.get("proposed_title"),
                "source_section_path": candidate.get("source_section_path"),
                "internal_headings": candidate.get("internal_headings") or [],
                "description": candidate.get("description"),
            }
            for index, candidate in enumerate(candidates)
        ],
    }
    try:
        output = await _invoke_llm(payload)
    except Exception:
        return _fallback_assignments(candidates, existing_structural_paths)

    assignments = _fallback_assignments(candidates, existing_structural_paths)
    for item in output.assignments:
        parts = [_clean_segment(part) for part in item.structural_path if _clean_segment(part)]
        if item.candidate_index >= 0 and parts:
            assignments[item.candidate_index] = " > ".join(parts[:4])
    return assignments
