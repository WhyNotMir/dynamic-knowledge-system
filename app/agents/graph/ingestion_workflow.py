from __future__ import annotations

import uuid
from typing import Literal, cast

from loguru import logger
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents.graph.checkpointer import SQLAlchemyCheckpointSaver
from app.agents.graph.runtime import append_event, make_base_state, persist_buffered_events
from app.agents.state import IngestionWorkflowState
from app.database import AsyncSessionLocal
from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import mark_ingestion_failed, run_ingestion
from app.models.article_candidate import StructureProposal
from app.models.source import Source


JobKind = Literal["ingest_source", "propose_structure"]

_INGESTION_WORKFLOW_CHECKPOINTER = SQLAlchemyCheckpointSaver(
    session_factory=lambda: AsyncSessionLocal(),
)


def create_ingestion_workflow_state(
    *,
    project_id: uuid.UUID,
    job_kind: JobKind,
    source_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> IngestionWorkflowState:
    """Create the state envelope for an ingestion/proposal graph run."""

    state = cast(IngestionWorkflowState, make_base_state(project_id=project_id, run_id=run_id))
    state["job_kind"] = job_kind
    state["source_id"] = source_id
    state["proposal_id"] = proposal_id
    state["status"] = "pending"
    state["current_node"] = None
    state["result"] = {}
    return state


def _route_from_job_kind(state: IngestionWorkflowState) -> str:
    return state["job_kind"]


def _mark_running(node_name: str, state: IngestionWorkflowState) -> IngestionWorkflowState:
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
    state: IngestionWorkflowState,
    *,
    message: str,
    error: str | None = None,
) -> IngestionWorkflowState:
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


def _route_after_service(state: IngestionWorkflowState) -> str:
    return "failed" if state.get("status") == "failed" else "completed"


def _complete(state: IngestionWorkflowState) -> IngestionWorkflowState:
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
            message="ingestion workflow completed",
            payload={"completed_node": node_name},
        ),
    }


def _failed(state: IngestionWorkflowState) -> IngestionWorkflowState:
    node_name = state.get("current_node") or "graph"
    return {
        **state,
        "status": "failed",
        "current_node": "failed",
        "events": append_event(
            state,
            node="failed",
            message="ingestion workflow failed",
            payload={
                "failed_node": node_name,
                "error": (state.get("result") or {}).get("error"),
            },
        ),
    }


async def _ingest_source_entry(state: IngestionWorkflowState) -> IngestionWorkflowState:
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
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive catch around worker/runtime failures
        logger.exception(f"[graph] ingest_source failed for {source_id}: {exc}")
        try:
            async with AsyncSessionLocal() as db:
                await mark_ingestion_failed(source_id, db, error=str(exc))
                await db.commit()
        except Exception:
            logger.exception(f"[graph] could not persist failure for source {source_id}")
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


async def _propose_structure_entry(state: IngestionWorkflowState) -> IngestionWorkflowState:
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
            await db.commit()
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


async def resolve_ingestion_job_project_id(
    *,
    job_kind: JobKind,
    source_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Resolve project context for a worker-triggered graph run.

    The arq queue currently passes only the entity id (`source_id` or
    `proposal_id`), so the worker does a lightweight lookup before creating
    the graph state envelope.
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


def get_ingestion_workflow_checkpointer():
    """Return the shared checkpointer for the ingestion/proposal graph.

    The SQLAlchemy-backed saver persists checkpoints across worker restarts
    and mirrors them into MemorySaver for test doubles and same-process reads.
    """

    return _INGESTION_WORKFLOW_CHECKPOINTER


def build_ingestion_workflow_config(
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


async def get_ingestion_workflow_state_snapshot(
    *,
    run_id: uuid.UUID,
    checkpoint_id: str | None = None,
):
    graph = build_ingestion_workflow_graph()
    return await graph.aget_state(
        build_ingestion_workflow_config(run_id=run_id, checkpoint_id=checkpoint_id)
    )


def build_ingestion_workflow_graph():
    """Compile the ingestion/proposal graph."""

    graph = StateGraph(IngestionWorkflowState)
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

    return graph.compile(checkpointer=get_ingestion_workflow_checkpointer())


async def run_ingestion_workflow(
    state: IngestionWorkflowState,
    *,
    checkpoint_id: str | None = None,
) -> IngestionWorkflowState:
    """Invoke the compiled graph and return the final state."""

    graph = build_ingestion_workflow_graph()
    config = build_ingestion_workflow_config(
        run_id=state["run_id"],
        checkpoint_id=checkpoint_id,
    )
    result = await graph.ainvoke(state, config=config)
    final_state = cast(IngestionWorkflowState, result)
    snapshot = await graph.aget_state(build_ingestion_workflow_config(run_id=state["run_id"]))
    persisted_count = await persist_buffered_events(final_state)
    return cast(
        IngestionWorkflowState,
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
    "build_ingestion_workflow_graph",
    "build_ingestion_workflow_config",
    "create_ingestion_workflow_state",
    "get_ingestion_workflow_checkpointer",
    "get_ingestion_workflow_state_snapshot",
    "resolve_ingestion_job_project_id",
    "run_ingestion_workflow",
]
