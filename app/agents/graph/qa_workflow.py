from __future__ import annotations

import uuid
from typing import cast

from langgraph.graph import END, START, StateGraph

from app.agents.graph.runtime import append_event, make_base_state, persist_buffered_events
from app.agents.state import QAWorkflowState
from app.application.qa.ask_service import (
    answer_from_context,
    ensure_project_exists,
    persist_ask_response,
    retrieve_context,
)
from app.database import AsyncSessionLocal
from app.schemas.qa import AskRequest, AskResponse


class QAWorkflowError(RuntimeError):
    def __init__(self, *, error: str, error_type: str):
        super().__init__(error)
        self.error_type = error_type


def create_qa_workflow_state(
    *,
    project_id: uuid.UUID,
    request: AskRequest,
    run_id: uuid.UUID | None = None,
) -> QAWorkflowState:
    state = cast(QAWorkflowState, make_base_state(project_id=project_id, run_id=run_id))
    state["request"] = request
    state["status"] = "pending"
    state["current_node"] = None
    state["result"] = {}
    return state


def _mark_running(node_name: str, state: QAWorkflowState) -> QAWorkflowState:
    return {
        **state,
        "status": "running",
        "current_node": node_name,
        "events": append_event(
            state,
            node=node_name,
            message=f"{node_name} node entered",
        ),
    }


def _mark_failed(node_name: str, state: QAWorkflowState, exc: Exception) -> QAWorkflowState:
    return {
        **state,
        "status": "failed",
        "current_node": node_name,
        "result": {
            **(state.get("result") or {}),
            "failed_node": node_name,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        },
        "events": append_event(
            state,
            node=node_name,
            message=f"{node_name} execution failed",
            payload={"error": str(exc), "error_type": exc.__class__.__name__},
        ),
    }


def _route_after_node(state: QAWorkflowState) -> str:
    return "failed" if state.get("status") == "failed" else "next"


async def _retrieve_context(state: QAWorkflowState) -> QAWorkflowState:
    running_state = _mark_running("retrieve_context", state)
    try:
        async with AsyncSessionLocal() as db:
            await ensure_project_exists(state["project_id"], db)
            context = await retrieve_context(state["project_id"], state["request"], db)
    except Exception as exc:
        return _mark_failed("retrieve_context", running_state, exc)

    return {
        **running_state,
        "context": context,
        "events": append_event(
            running_state,
            node="retrieve_context",
            message="retrieval completed",
            payload={
                "retrieved_count": len(context.items),
                "top_score": context.top_score,
                "has_enough_evidence": context.has_enough_evidence,
            },
        ),
    }


async def _answer_from_context(state: QAWorkflowState) -> QAWorkflowState:
    running_state = _mark_running("answer_from_context", state)
    try:
        answer = await answer_from_context(state["request"], state["context"])
    except Exception as exc:
        return _mark_failed("answer_from_context", running_state, exc)

    return {
        **running_state,
        "answer": answer,
        "events": append_event(
            running_state,
            node="answer_from_context",
            message="answer generated",
            payload={
                "confidence": answer.confidence,
                "insufficient_context": answer.insufficient_context,
                "citation_count": len(answer.citation_block_ids),
            },
        ),
    }


async def _persist_answer(state: QAWorkflowState) -> QAWorkflowState:
    running_state = _mark_running("persist_answer", state)
    try:
        async with AsyncSessionLocal() as db:
            response = await persist_ask_response(
                state["project_id"],
                state["request"],
                state["answer"],
                state["context"],
                db,
            )
            await db.commit()
    except Exception as exc:
        return _mark_failed("persist_answer", running_state, exc)

    return {
        **running_state,
        "response": response,
        "events": append_event(
            running_state,
            node="persist_answer",
            message="answer persisted",
            payload={"conversation_id": str(response.conversation_id)},
        ),
    }


def _complete(state: QAWorkflowState) -> QAWorkflowState:
    return {
        **state,
        "status": "completed",
        "current_node": "completed",
        "events": append_event(
            state,
            node="completed",
            message="qa workflow completed",
        ),
    }


def _failed(state: QAWorkflowState) -> QAWorkflowState:
    return {
        **state,
        "status": "failed",
        "current_node": "failed",
        "events": append_event(
            state,
            node="failed",
            message="qa workflow failed",
            payload={
                "failed_node": (state.get("result") or {}).get("failed_node"),
                "error": (state.get("result") or {}).get("error"),
            },
        ),
    }


def build_qa_workflow_graph():
    graph = StateGraph(QAWorkflowState)
    graph.add_node("retrieve_context", _retrieve_context)
    graph.add_node("answer_from_context", _answer_from_context)
    graph.add_node("persist_answer", _persist_answer)
    graph.add_node("completed", _complete)
    graph.add_node("failed", _failed)

    graph.add_edge(START, "retrieve_context")
    graph.add_conditional_edges(
        "retrieve_context",
        _route_after_node,
        {"next": "answer_from_context", "failed": "failed"},
    )
    graph.add_conditional_edges(
        "answer_from_context",
        _route_after_node,
        {"next": "persist_answer", "failed": "failed"},
    )
    graph.add_conditional_edges(
        "persist_answer",
        _route_after_node,
        {"next": "completed", "failed": "failed"},
    )
    graph.add_edge("completed", END)
    graph.add_edge("failed", END)
    return graph.compile()


async def run_qa_workflow(
    *,
    project_id: uuid.UUID,
    request: AskRequest,
    run_id: uuid.UUID | None = None,
) -> AskResponse:
    state = create_qa_workflow_state(
        project_id=project_id,
        request=request,
        run_id=run_id,
    )
    graph = build_qa_workflow_graph()
    result = cast(QAWorkflowState, await graph.ainvoke(state))
    await persist_buffered_events(result, agent_name="qa_workflow")
    if result.get("status") == "failed":
        details = result.get("result") or {}
        raise QAWorkflowError(
            error=str(details.get("error") or "Q&A workflow failed"),
            error_type=str(details.get("error_type") or "RuntimeError"),
        )
    return cast(AskResponse, result["response"])


__all__ = [
    "QAWorkflowError",
    "build_qa_workflow_graph",
    "create_qa_workflow_state",
    "run_qa_workflow",
]
