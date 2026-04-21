"""Agent framework base (Phase 0 Slice B).

Primitives shared by every LangChain / LangGraph agent in the system:

* ``get_chat_llm()`` — single place that constructs a ``ChatGroq``
  instance. Centralising it means later phases can swap providers, add
  streaming, or attach callbacks without touching every agent.
* ``AgentError`` — normalised failure type. Agents that want to fall
  back to deterministic behaviour (cf. ``structure_agent._fallback_title``)
  catch this and proceed.
* ``@with_retry`` — tenacity wrapper tuned for our LLM use. Only retries
  transient network errors; does *not* retry rate-limit / quota errors.
* ``configure_langsmith()`` — env-gated tracing bootstrap. Called once at
  app startup.

Nothing in this module imports ``langchain_groq`` at module top level so
the import graph stays clean for tests that don't need LangChain.
"""
from __future__ import annotations

import os
from typing import Any

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentError(RuntimeError):
    """Raised when an agent can no longer make progress.

    Subclasses may add structured context (which candidate / block was in
    flight, upstream exception, etc.). Callers that can fall back to a
    deterministic path catch this type.
    """


class AgentTransientError(AgentError):
    """Transient failure — safe to retry (connection refused, 5xx, timeout)."""


class AgentQuotaError(AgentError):
    """Upstream quota / rate-limit exhausted. Not safe to retry in-loop."""


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def get_chat_llm(*, temperature: float | None = None, model: str | None = None) -> Any:
    """Return a lazily-imported ``ChatGroq`` instance.

    The import is inside the function so the module stays importable in
    environments that don't have langchain-groq installed (e.g. the unit
    tests that only exercise the domain layer).
    """
    from langchain_groq import ChatGroq  # type: ignore[import-not-found]

    return ChatGroq(
        api_key=settings.groq_api_key,
        model=model or settings.llm_model,
        temperature=settings.agent_temperature if temperature is None else temperature,
    )


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------


def with_retry(fn):
    """Tenacity wrapper tuned for our LLM calls.

    Only retries on ``AgentTransientError`` — quota / rate-limit errors
    should flip the caller into fallback mode instead of looping.
    """

    @retry(
        retry=retry_if_exception_type(AgentTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
        reraise=True,
    )
    async def wrapper(*args, **kwargs):
        return await fn(*args, **kwargs)

    wrapper.__name__ = getattr(fn, "__name__", "wrapped")
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# LangSmith tracing bootstrap
# ---------------------------------------------------------------------------


def configure_langsmith() -> None:
    """Enable LangSmith tracing if the env flag is set.

    LangChain's tracer reads ``LANGCHAIN_*`` env vars at import / call
    time, so we translate our settings into the env before any agent
    runs. Idempotent; safe to call more than once.
    """
    if not settings.langsmith_tracing:
        return
    if not settings.langsmith_api_key:
        logger.warning(
            "langsmith_tracing=True but no langsmith_api_key set — skipping."
        )
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    logger.info(
        f"LangSmith tracing enabled (project={settings.langsmith_project!r})"
    )
