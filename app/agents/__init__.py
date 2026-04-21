"""DKS agent framework.

Layout (Phase 0 Slice B):

  base.py     — protocol, retry, LLM factory, LangSmith bootstrap
  prompts/    — ChatPromptTemplate-s
  chains/     — LangChain agents (title, routing, placement, synthesis, ...)
  tools/      — shared tool functions (vector_search, hash_dedup, url_fetch,
                outlier) usable by any agent
  graph/      — LangGraph StateGraph modules (IngestPipeline,
                MaintenancePipeline, FeedbackPipeline)
  ml/         — non-LLM detectors (IsolationForest, LOF, rule-based
                classifiers)
  state/      — shared TypedDict state schemas

Each sub-package stays self-contained. Import from ``app.agents.base`` to
share retry / LLM / tracing primitives.
"""
from app.agents.base import (
    AgentError,
    AgentQuotaError,
    AgentTransientError,
    configure_langsmith,
    get_chat_llm,
    with_retry,
)

__all__ = [
    "AgentError",
    "AgentQuotaError",
    "AgentTransientError",
    "configure_langsmith",
    "get_chat_llm",
    "with_retry",
]
