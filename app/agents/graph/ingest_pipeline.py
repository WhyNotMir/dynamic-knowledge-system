from __future__ import annotations

import uuid
from typing import Literal, cast

from loguru import logger
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents.graph.checkpointer import SQLAlchemyCheckpointSaver
from app.agents.graph.runtime import append_event, make_base_state, persist_buffered_events
from app.agents.state import IngestPipelineState
from app.database import AsyncSessionLocal
from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.article_candidate import StructureProposal
from app.models.source import Source


JobKind = Literal["ingest_source", "propose_structure"]

_INGEST_PIPELINE_CHECKPOINTER = SQLAlchemyCheckpointSaver(
    session_factory=lambda: AsyncSessionLocal(),
)


def create_ingest_pipeline_state(
    *,
    project_id: uuid.UUID,
    job_kind: JobKind,
    source_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> IngestPipelineState:
    """Create the minimal state envelope for the Phase 2 pipeline graph."""

    state = cast(IngestPipelineState, make_base_state(project_id=project_id, run_id=run_id))
    state["job_kind"] = job_kind
    state["source_id"] = source_id
    state["proposal_id"] = proposal_id
    state["status"] = "pending"
    state["current_node"] = None
    state["result"] = {}
    return state


def _route_from_job_kind(state: IngestPipelineState) -> str:
    return state["job_kind"]


def _mark_running(node_name: str, state: IngestPipelineState) -> IngestPipelineState:
    return {
        **state,
        "status": "running",
        "current_node": node_name,
        "events": append_event(
            state,
            node=node_name,
            message=f"{node_name} node entered",
            payload={
                "source_id": str(state.get("source_id")) if state.get("source_id") else None,
                "proposal_id": str(state.get("proposal_id")) if state.get("proposal_id") else None,
            },
        ),
    }


def _mark_failed(
    node_name: str,
    state: IngestPipelineState,
    *,
    message: str,
    error: str | None = None,
) -> IngestPipelineState:
    payload = {
        "source_id": str(state.get("source_id")) if state.get("source_id") else None,
        "proposal_id": str(state.get("proposal_id")) if state.get("proposal_id") else None,
    }
    if error:
        payload["error"] = error

    return {
        **state,
        "status": "failed",
        "current_node": node_name,
        "result": {
            **(state.get("result") or {}),
            "failed_node": node_name,
            "error": error or message,
        },
        "events": append_event(
            state,
            node=node_name,
            message=message,
            payload=payload,
        ),
    }


def _route_after_service(state: IngestPipelineState) -> str:
    return "failed" if state.get("status") == "failed" else "completed"


def _complete(state: IngestPipelineState) -> IngestPipelineState:
    node_name = state.get("current_node") or "graph"
    return {
        **state,
        "status": "completed",
        "current_node": "completed",
        "result": {
            **(state.get("result") or {}),
            "completed_node": node_name,
        },
        "events": append_event(
            state,
            node="completed",
            message="pipeline skeleton completed",
            payload={"completed_node": node_name},
        ),
    }


def _failed(state: IngestPipelineState) -> IngestPipelineState:
    node_name = state.get("current_node") or "graph"
    return {
        **state,
        "status": "failed",
        "current_node": "failed",
        "events": append_event(
            state,
            node="failed",
            message="pipeline failed",
            payload={
                "failed_node": node_name,
                "error": (state.get("result") or {}).get("error"),
            },
        ),
    }


async def _ingest_source_entry(state: IngestPipelineState) -> IngestPipelineState:
    source_id = state.get("source_id")
    if source_id is None:
        return _mark_failed(
            "ingest_source",
            state,
            message="ingest_source missing source_id",
            error="source_id is required",
        )

    running_state = _mark_running("ingest_source", state)

    try:
        async with AsyncSessionLocal() as db:
            await run_ingestion(source_id, db)
    except Exception as exc:  # pragma: no cover - defensive catch around worker/runtime failures
        logger.exception(f"[graph] ingest_source failed for {source_id}: {exc}")
        return _mark_failed(
            "ingest_source",
            running_state,
            message="ingest_source execution failed",
            error=str(exc),
        )

    return {
        **running_state,
        "status": "completed",
        "result": {
            **(running_state.get("result") or {}),
            "completed_node": "ingest_source",
            "source_id": str(source_id),
        },
        "events": append_event(
            running_state,
            node="ingest_source",
            message="ingest_source execution completed",
            payload={"source_id": str(source_id)},
        ),
    }


async def _propose_structure_entry(state: IngestPipelineState) -> IngestPipelineState:
    proposal_id = state.get("proposal_id")
    if proposal_id is None:
        return _mark_failed(
            "propose_structure",
            state,
            message="propose_structure missing proposal_id",
            error="proposal_id is required",
        )

    running_state = _mark_running("propose_structure", state)

    try:
        async with AsyncSessionLocal() as db:
            await run_structure_proposal(proposal_id, db)
    except Exception as exc:  # pragma: no cover - defensive catch around worker/runtime failures
        logger.exception(f"[graph] propose_structure failed for {proposal_id}: {exc}")
        return _mark_failed(
            "propose_structure",
            running_state,
            message="propose_structure execution failed",
            error=str(exc),
        )

    return {
        **running_state,
        "status": "completed",
        "result": {
            **(running_state.get("result") or {}),
            "completed_node": "propose_structure",
            "proposal_id": str(proposal_id),
        },
        "events": append_event(
            running_state,
            node="propose_structure",
            message="propose_structure execution completed",
            payload={"proposal_id": str(proposal_id)},
        ),
    }


async def resolve_job_project_id(
    *,
    job_kind: JobKind,
    source_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Resolve project context for a worker-triggered graph run.

    The arq queue currently passes only the entity id (`source_id` or
    `proposal_id`). Slice B keeps the external job signatures stable and does a
    lightweight lookup here before creating the graph state envelope.
    """

    async with AsyncSessionLocal() as db:
        if job_kind == "ingest_source":
            if source_id is None:
                return None
            result = await db.execute(select(Source.project_id).where(Source.id == source_id))
            return result.scalar_one_or_none()

        if proposal_id is None:
            return None
        result = await db.execute(
            select(StructureProposal.project_id).where(StructureProposal.id == proposal_id)
        )
        return result.scalar_one_or_none()


def get_ingest_pipeline_checkpointer():
    """Return the shared checkpointer for the ingest/propose graph.

    Phase 2 hardening uses a SQLAlchemy-backed saver so checkpoints survive
    worker restarts. The saver keeps a MemorySaver mirror as a low-friction
    fallback for mocked sessions in tests.
    """

    return _INGEST_PIPELINE_CHECKPOINTER


def build_ingest_pipeline_config(
    *,
    run_id: uuid.UUID,
    checkpoint_id: str | None = None,
) -> dict:
    config = {
        "configurable": {
            "thread_id": str(run_id),
        }
    }
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    return config


async def get_ingest_pipeline_state_snapshot(
    *,
    run_id: uuid.UUID,
    checkpoint_id: str | None = None,
):
    graph = build_ingest_pipeline_graph()
    return await graph.aget_state(
        build_ingest_pipeline_config(run_id=run_id, checkpoint_id=checkpoint_id)
    )


def build_ingest_pipeline_graph():
    """Compile the Phase 2 Slice A graph skeleton.
    """

    graph = StateGraph(IngestPipelineState)
    graph.add_node("ingest_source", _ingest_source_entry)
    graph.add_node("propose_structure", _propose_structure_entry)
    graph.add_node("completed", _complete)
    graph.add_node("failed", _failed)

    graph.add_conditional_edges(
        START,
        _route_from_job_kind,
        {
            "ingest_source": "ingest_source",
            "propose_structure": "propose_structure",
        },
    )
    graph.add_conditional_edges(
        "ingest_source",
        _route_after_service,
        {
            "completed": "completed",
            "failed": "failed",
        },
    )
    graph.add_conditional_edges(
        "propose_structure",
        _route_after_service,
        {
            "completed": "completed",
            "failed": "failed",
        },
    )
    graph.add_edge("completed", END)
    graph.add_edge("failed", END)

    return graph.compile(checkpointer=get_ingest_pipeline_checkpointer())


async def run_ingest_pipeline(
    state: IngestPipelineState,
    *,
    checkpoint_id: str | None = None,
) -> IngestPipelineState:
    """Invoke the compiled graph and return the final state."""

    graph = build_ingest_pipeline_graph()
    config = build_ingest_pipeline_config(
        run_id=state["run_id"],
        checkpoint_id=checkpoint_id,
    )
    result = await graph.ainvoke(state, config=config)
    final_state = cast(IngestPipelineState, result)
    snapshot = await graph.aget_state(build_ingest_pipeline_config(run_id=state["run_id"]))
    persisted_count = await persist_buffered_events(final_state)
    return cast(
        IngestPipelineState,
        {
            **final_state,
            "result": {
                **(final_state.get("result") or {}),
                "checkpoint_id": snapshot.config["configurable"].get("checkpoint_id"),
                "persisted_event_count": persisted_count,
            },
        },
    )


__all__ = [
    "build_ingest_pipeline_graph",
    "build_ingest_pipeline_config",
    "create_ingest_pipeline_state",
    "get_ingest_pipeline_checkpointer",
    "get_ingest_pipeline_state_snapshot",
    "resolve_job_project_id",
    "run_ingest_pipeline",
]
