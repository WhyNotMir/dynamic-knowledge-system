"""Shared TypedDict state schemas for LangGraph workflows."""
from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict


class BaseRunState(TypedDict, total=False):
    """Fields every pipeline run carries for audit + checkpointing."""

    project_id: uuid.UUID
    run_id: uuid.UUID
    # Accumulates IngestionEvent payloads buffered by individual nodes.
    events: list[dict[str, Any]]
    # Candidate-level failures can be routed here instead of failing a run.
    dlq: list[dict[str, Any]]


class IngestionWorkflowState(BaseRunState, total=False):
    """State for the ingestion/proposal workflow."""

    job_kind: Literal["ingest_source", "propose_structure"]
    source_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    status: Literal["pending", "running", "completed", "failed"]
    current_node: str | None
    result: dict[str, Any]


class QAWorkflowState(BaseRunState, total=False):
    """State for a single Q&A turn."""

    request: Any
    context: Any
    answer: Any
    response: Any
    status: Literal["pending", "running", "completed", "failed"]
    current_node: str | None
    result: dict[str, Any]


class ArticleBuildWorkflowState(BaseRunState, total=False):
    """State for article materialisation from a reviewed proposal."""

    proposal_id: uuid.UUID | None
    article_ids: list[uuid.UUID]
    status: Literal["pending", "running", "completed", "failed"]
    current_node: str | None
    result: dict[str, Any]


__all__ = [
    "ArticleBuildWorkflowState",
    "BaseRunState",
    "IngestionWorkflowState",
    "QAWorkflowState",
]
