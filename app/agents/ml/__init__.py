"""Non-LLM detectors.

Phase 8 activates these:

* ``outlier_detector.py``    — IsolationForest on article-level
  embeddings + LOF inside articles.
* ``source_scope.py``        — per-source mean-embedding distance from
  project centroid.
* ``contradiction_filter.py`` — cheap pairwise similarity filter
  (cosine > 0.85, different ``source_id``) feeding the LLM pairwise
  judge.
"""

__all__: list[str] = []
