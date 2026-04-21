Phase 0 golden fixture pack.

Files:
- `phase0_foundation.docx` — deterministic DOCX used by the Phase 0 regression test.
- `phase0_foundation_baseline.json` — expected candidate/article shape produced by the mocked Phase 0 pipeline.

The regression test runs the real ingestion + proposal + build flow against this document and compares the normalized result to the baseline.
