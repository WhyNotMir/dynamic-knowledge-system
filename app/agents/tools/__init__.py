"""Shared tool functions for agents.

Phase 0 establishes the directory layout; individual tools arrive with
the phases that need them:

* ``vector_search.py`` — k-NN over pgvector, optional diversity filter
  (Phase 3 / 4 / 7).
* ``hash_dedup.py``    — exact-duplicate detection on SHA-256 of
  normalised content (Phase 7).
* ``url_fetch.py``     — httpx + trafilatura extractor for URL ingestion
  (Phase 12).
* ``outlier.py``       — IsolationForest / LOF wrappers (Phase 8).

Tools are plain async functions. They do not take a LangChain
``ToolContext``: we invoke them directly from chain code to keep control
flow explicit.
"""

__all__: list[str] = []
