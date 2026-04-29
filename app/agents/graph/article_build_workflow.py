from __future__ import annotations

import uuid
from typing import cast

from langgraph.graph import END, START, StateGraph

from app.agents.graph.runtime import append_event, make_base_state, persist_buffered_events
from app.agents.state import ArticleBuildWorkflowState
from app.application.articles.build_service import build_articles_for_project
from app.database import AsyncSessionLocal


class ArticleBuildWorkflowError(RuntimeError):
    def __init__(self, *, error: str, error_type: str):
        super().__init__(error)
        self.error_type = error_type


def create_article_build_workflow_state(
    *,
    project_id: uuid.UUID,
    proposal_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> ArticleBuildWorkflowState:
    state = cast(ArticleBuildWorkflowState, make_base_state(project_id=project_id, run_id=run_id))
    state["proposal_id"] = proposal_id
    state["article_ids"] = []
    state["status"] = "pending"
    state["current_node"] = None
    state["result"] = {}
    return state


def _mark_running(node_name: str, state: ArticleBuildWorkflowState) -> ArticleBuildWorkflowState:
    return {
        **state,
        "status": "running",
        "current_node": node_name,
        "events": append_event(
            state,
            node=node_name,
            message=f"{node_name} node entered",
            payload={
                "proposal_id": str(state.get("proposal_id")) if state.get("proposal_id") else None,
            },
        ),
    }


async def _build_articles(state: ArticleBuildWorkflowState) -> ArticleBuildWorkflowState:
    running_state = _mark_running("build_articles", state)
    try:
        async with AsyncSessionLocal() as db:
            article_ids = await build_articles_for_project(
                state["project_id"],
                db,
                proposal_id=state.get("proposal_id"),
            )
            await db.commit()
    except Exception as exc:
        return {
            **running_state,
            "status": "failed",
            "result": {
                **(running_state.get("result") or {}),
                "failed_node": "build_articles",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            },
            "events": append_event(
                running_state,
                node="build_articles",
                message="article build failed",
                payload={"error": str(exc), "error_type": exc.__class__.__name__},
            ),
        }

    return {
        **running_state,
        "article_ids": article_ids,
        "events": append_event(
            running_state,
            node="build_articles",
            message="articles built",
            payload={"count": len(article_ids)},
        ),
    }


def _route_after_build(state: ArticleBuildWorkflowState) -> str:
    return "failed" if state.get("status") == "failed" else "completed"


def _complete(state: ArticleBuildWorkflowState) -> ArticleBuildWorkflowState:
    return {
        **state,
        "status": "completed",
        "current_node": "completed",
        "events": append_event(
            state,
            node="completed",
            message="article build workflow completed",
            payload={"count": len(state.get("article_ids") or [])},
        ),
    }


def _failed(state: ArticleBuildWorkflowState) -> ArticleBuildWorkflowState:
    return {
        **state,
        "status": "failed",
        "current_node": "failed",
        "events": append_event(
            state,
            node="failed",
            message="article build workflow failed",
            payload={
                "failed_node": (state.get("result") or {}).get("failed_node"),
                "error": (state.get("result") or {}).get("error"),
            },
        ),
    }


def build_article_build_workflow_graph():
    graph = StateGraph(ArticleBuildWorkflowState)
    graph.add_node("build_articles", _build_articles)
    graph.add_node("completed", _complete)
    graph.add_node("failed", _failed)
    graph.add_edge(START, "build_articles")
    graph.add_conditional_edges(
        "build_articles",
        _route_after_build,
        {"completed": "completed", "failed": "failed"},
    )
    graph.add_edge("completed", END)
    graph.add_edge("failed", END)
    return graph.compile()


async def run_article_build_workflow(
    *,
    project_id: uuid.UUID,
    proposal_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    state = create_article_build_workflow_state(
        project_id=project_id,
        proposal_id=proposal_id,
        run_id=run_id,
    )
    graph = build_article_build_workflow_graph()
    result = cast(ArticleBuildWorkflowState, await graph.ainvoke(state))
    await persist_buffered_events(result, agent_name="article_build_workflow")
    if result.get("status") == "failed":
        details = result.get("result") or {}
        raise ArticleBuildWorkflowError(
            error=str(details.get("error") or "Article build workflow failed"),
            error_type=str(details.get("error_type") or "RuntimeError"),
        )
    return list(result.get("article_ids") or [])


__all__ = [
    "ArticleBuildWorkflowError",
    "build_article_build_workflow_graph",
    "create_article_build_workflow_state",
    "run_article_build_workflow",
]
