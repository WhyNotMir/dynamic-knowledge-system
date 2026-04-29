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


class IngestPipelineState(BaseRunState, total=False):
    """State for the ingest/propose workflow."""

    job_kind: Literal["ingest_source", "propose_structure"]
    source_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    status: Literal["pending", "running", "completed", "failed"]
    current_node: str | None
    result: dict[str, Any]


__all__ = ["BaseRunState", "IngestPipelineState"]
