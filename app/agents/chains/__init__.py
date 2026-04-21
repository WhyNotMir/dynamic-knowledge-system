"""LangChain-based agents.

Each agent lives in its own module and exposes a single public entry
point:

* ``structure_agent.py`` — title + section hierarchy for a set of
  candidates (Phase 0 Slice C port of the legacy agent).
* ``title_agent.py``     — per-candidate title + description + suggested
  block (Phase 1).
* ``alias_agent.py``     — synonyms / abbreviations for articles
  (Phase 3).
* ``routing_agent.py``   — coarse Stage-1 router for incremental updates
  (Phase 7).
* ``placement_agent.py`` — fine Stage-2A append-only placement (Phase 7).
* ``synthesis_agent.py`` — merge two duplicates into one synthesized
  block (Phase 7).
* ``qa_agent.py``        — strict-RAG Q&A (Phase 4).
* ``citation_verifier.py`` — post-answer claim verifier (Phase 6).
* ``kb_summarizer.py``   — ~10-line KB summary (Phase 1).
* ``query_decomposer.py`` — multi-step query splitter (Phase 10).

Phase 0 only ports ``structure_agent``. Other modules are stubs added as
their phase lands.
"""

__all__: list[str] = []
