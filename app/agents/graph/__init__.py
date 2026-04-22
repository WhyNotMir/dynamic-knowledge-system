"""LangGraph StateGraph modules.

Phase 0 reserves the directory; the actual StateGraphs arrive in Phase 2
(``ingest_pipeline.py``), Phase 8 (``maintenance_pipeline.py``), and
Phase 14 (``feedback_pipeline.py``).

Each module defines:
* a TypedDict state (imported from ``app.agents.state``)
* a builder function that returns a compiled ``StateGraph``
* a Postgres checkpointer setup

Nothing imports langgraph at the top of the ``__init__`` so environments
without langgraph installed stay importable.
"""

__all__ = [
    "ingest_pipeline",
    "runtime",
]
