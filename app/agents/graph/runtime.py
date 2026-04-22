from __future__ import annotations

import uuid
from typing import Any

from app.agents.state import BaseRunState
from app.database import AsyncSessionLocal
from app.models.ingestion_event import IngestionEvent, IngestionEventLevel


def make_base_state(
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
) -> BaseRunState:
    """Seed a graph run with the common audit/checkpoint fields."""

    return BaseRunState(
        project_id=project_id,
        run_id=run_id or uuid.uuid4(),
        events=[],
        dlq=[],
    )


def append_event(
    state: BaseRunState,
    *,
    node: str,
    message: str,
    payload: dict[str, Any] | None = None,
    level: str = IngestionEventLevel.INFO.value,
) -> list[dict[str, Any]]:
    """Return a new event buffer with one extra in-memory audit event.

    Slice A only buffers events on the graph state; Slice D will persist them
    into the ``ingestion_events`` table.
    """

    return [
        *(state.get("events") or []),
        {
            "node": node,
            "message": message,
            "payload": payload or {},
            "level": level,
        },
    ]


async def persist_buffered_events(
    state: BaseRunState,
    *,
    agent_name: str = "ingest_pipeline",
) -> int:
    """Persist the in-memory event buffer into ``ingestion_events``.

    Slice D keeps event emission lightweight inside graph nodes and flushes the
    buffered events once per graph run. The state is left intact so callers can
    still inspect the in-memory events after persistence.
    """

    events = state.get("events") or []
    if not events:
        return 0

    project_id = state.get("project_id")
    run_id = state.get("run_id")

    rows = []
    for event in events:
        raw_level = str(event.get("level") or IngestionEventLevel.INFO.value).lower()
        try:
            level = IngestionEventLevel(raw_level)
        except ValueError:
            level = IngestionEventLevel.INFO

        rows.append(
            IngestionEvent(
                project_id=project_id,
                run_id=run_id,
                agent_name=agent_name,
                node=event.get("node"),
                level=level,
                message=event.get("message"),
                payload=event.get("payload") or {},
            )
        )

    async with AsyncSessionLocal() as db:
        db.add_all(rows)
        await db.commit()

    return len(rows)


__all__ = ["append_event", "make_base_state", "persist_buffered_events"]
