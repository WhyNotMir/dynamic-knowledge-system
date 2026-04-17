"""Drive the ingestion pipeline directly (the same code that the arq worker
runs). This verifies that a DOCX upload, when processed, produces fragments
in the DB with the right shape."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_fragment import SourceFragment
from app.repositories.source_repository import SourceRepository

from tests.helpers import make_docx, unique_docx


async def test_run_ingestion_produces_fragments(
    client, project, session_factory, tmp_path
):
    # Upload a real file through the API so the Source + on-disk file exist.
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    source_id = uuid.UUID(up.json()["id"])

    # Run the worker inline against a fresh session (matches what
    # `workers/ingestion_worker.py` does per job).
    async with session_factory() as db:
        await run_ingestion(source_id, db)

    # Source status advanced to DONE, metadata recorded.
    async with session_factory() as db:
        src = await SourceRepository(db).get(source_id)
        assert src is not None
        assert src.status == SourceStatus.DONE
        assert src.source_type == SourceType.DOCX
        assert src.doc_metadata is not None
        assert src.doc_metadata.get("element_count", 0) > 0

        frags = (
            await db.execute(
                select(SourceFragment)
                .where(SourceFragment.source_id == source_id)
                .order_by(SourceFragment.position_index)
            )
        ).scalars().all()

    # We expect at least one heading per top-level section + some body
    # paragraphs, so the fragment count should be > 2 for our helper doc.
    assert len(frags) >= 3
    types = {f.element_type.value for f in frags}
    assert "heading" in types
    assert "paragraph" in types

    # section_path should be populated for fragments under a heading.
    assert any((f.section_path or "").strip() for f in frags)

    # Every fragment has an embedding (our deterministic mock fills them).
    assert all(f.embedding is not None for f in frags)


async def test_ingestion_marks_source_failed_on_extractor_error(
    client, project, session_factory, tmp_path, monkeypatch
):
    """If extraction blows up, the source status must become FAILED with a
    human-readable error message — never silently left in PROCESSING."""
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    source_id = uuid.UUID(up.json()["id"])

    # Force the extractor to raise.
    def boom(_path):
        raise RuntimeError("synthetic extract failure")

    monkeypatch.setattr("app.domain.ingestion.ingestion_service.extract", boom)

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    async with session_factory() as db:
        src = await SourceRepository(db).get(source_id)
        assert src.status == SourceStatus.FAILED
        assert src.error_message and "synthetic extract failure" in src.error_message
