"""Knowledge-base summarizer with deterministic fallback."""
from __future__ import annotations

import json

from app.agents.base import AgentQuotaError, AgentTransientError, get_chat_llm, with_retry
from app.config import settings
from app.models.article import Article


def _offline_mode() -> bool:
    return settings.groq_api_key in {"", "test-groq-key", "dummy", "test"}


def _fallback(project_name: str, articles: list[Article]) -> str | None:
    if not articles:
        return None
    lines = [f"{project_name} knowledge base overview:"]
    for article in articles[:8]:
        detail = article.description or article.suggested_section or "Topic page"
        lines.append(f"- {article.title}: {detail[:120]}")
    return "\n".join(lines)


@with_retry
async def _invoke_llm(project_name: str, articles: list[Article]) -> str:
    prompt = (
        "Summarize this knowledge base in around 10 lines. "
        "Focus on domain scope, major topics, and terminology. Return plain text only."
    )
    payload = {
        "project_name": project_name,
        "articles": [
            {
                "title": article.title,
                "description": article.description,
                "kind": article.kind.value,
            }
            for article in articles[:25]
        ],
    }
    try:
        message = await get_chat_llm(temperature=0.2).ainvoke(
            [("system", prompt), ("user", json.dumps(payload))]
        )
    except Exception as exc:
        name = exc.__class__.__name__
        if name in {"APIConnectionError", "APITimeoutError"}:
            raise AgentTransientError(str(exc))
        raise AgentQuotaError(str(exc))
    return getattr(message, "content", str(message)).strip()


async def summarize_project(project_name: str, articles: list[Article]) -> str | None:
    if _offline_mode():
        return _fallback(project_name, articles)
    try:
        return await _invoke_llm(project_name, articles)
    except Exception:
        return _fallback(project_name, articles)
