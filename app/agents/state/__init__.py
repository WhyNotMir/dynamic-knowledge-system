"""Shared TypedDict state schemas for LangGraph nodes.

Each StateGraph has its own state type, but fragments of state are
shared (e.g. ``project_id``, ``run_id``, ``dlq`` lists). Centralising
the TypedDicts here keeps the graph modules short and lets us migrate
state shape in one place.

Phase 0 defines the placeholders; the real TypedDicts arrive with the
StateGraph they serve.
"""
from __future__ import annotations

import uuid
from typing import Any, TypedDict


class BaseRunState(TypedDict, total=False):
    """Fields every pipeline run carries for audit + checkpointing."""

    project_id: uuid.UUID
    run_id: uuid.UUID
    # Accumulates IngestionEvent payloads buffered by individual nodes.
    events: list[dict[str, Any]]
    # Candidate-level failures routed to the dead-letter queue instead
    # of failing the whole run (Phase 2).
    dlq: list[dict[str, Any]]


__all__ = ["BaseRunState"]
