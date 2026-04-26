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

from tests.helpers import make_docx, make_pdf, make_rich_docx, make_table_pdf, unique_docx, unique_pdf


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
        assert src.title == "Introduction"
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


async def test_run_ingestion_preserves_rich_source_metadata(
    client, project, session_factory, tmp_path
):
    docx = make_rich_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    source_id = uuid.UUID(up.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    async with session_factory() as db:
        frags = (
            await db.execute(
                select(SourceFragment)
                .where(SourceFragment.source_id == source_id)
                .order_by(SourceFragment.position_index)
            )
        ).scalars().all()

    assert all(f.content_hash for f in frags)

    paragraph = next((f for f in frags if f.element_type.value == "paragraph"), None)
    assert paragraph is not None
    assert paragraph.inline_spans is not None
    assert {span["style"] for span in paragraph.inline_spans} >= {"bold", "italic", "code"}

    quote = next((f for f in frags if f.element_type.value == "quote"), None)
    assert quote is not None

    code_block = next((f for f in frags if f.element_type.value == "code_block"), None)
    assert code_block is not None

    list_block = next((f for f in frags if f.element_type.value == "list_item"), None)
    assert list_block is not None
    assert list_block.group_id is not None

    table = next((f for f in frags if f.element_type.value == "table"), None)
    assert table is not None
    assert table.meta_json is not None
    assert table.meta_json["rows"][0] == ["Column A", "Column B"]

    image = next((f for f in frags if f.element_type.value == "image"), None)
    assert image is not None
    assert image.meta_json is not None
    assert image.meta_json["image_ref"].startswith(f"/projects/{project['id']}/sources/assets/")

    footnote = next((f for f in frags if f.element_type.value == "footnote"), None)
    assert footnote is not None


async def test_run_ingestion_preserves_pdf_raw_metadata(
    client, project, session_factory, tmp_path
):
    pdf = make_pdf(unique_pdf(tmp_path))
    with pdf.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (pdf.name, fh, "application/pdf")},
        )
    source_id = uuid.UUID(up.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    async with session_factory() as db:
        src = await SourceRepository(db).get(source_id)
        assert src is not None
        assert src.status == SourceStatus.DONE
        assert src.source_type == SourceType.PDF

        frags = (
            await db.execute(
                select(SourceFragment)
                .where(SourceFragment.source_id == source_id)
                .order_by(SourceFragment.position_index)
            )
        ).scalars().all()

    assert len(frags) >= 2

    heading = next((f for f in frags if f.element_type.value == "heading"), None)
    paragraph = next((f for f in frags if f.element_type.value == "paragraph"), None)

    assert heading is not None
    assert paragraph is not None
    assert heading.meta_json is not None
    assert paragraph.meta_json is not None

    heading_pdf = heading.meta_json["pdf"]
    paragraph_pdf = paragraph.meta_json["pdf"]

    assert heading_pdf["page"]["number"] == 1
    assert heading_pdf["bbox"]["x0"] >= 0
    assert heading_pdf["bbox"]["y0"] >= 0
    assert heading_pdf["layout"]["line_count"] >= 1
    assert heading_pdf["font"]["avg_size"] > paragraph_pdf["font"]["avg_size"]
    assert paragraph_pdf["font"]["dominant_name"] is not None
    assert paragraph_pdf["page"]["width"] > 0
    assert paragraph_pdf["page"]["height"] > 0


async def test_run_ingestion_preserves_pdf_tables(
    client, project, session_factory, tmp_path
):
    pdf = make_table_pdf(unique_pdf(tmp_path))
    with pdf.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (pdf.name, fh, "application/pdf")},
        )
    source_id = uuid.UUID(up.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    async with session_factory() as db:
        frags = (
            await db.execute(
                select(SourceFragment)
                .where(SourceFragment.source_id == source_id)
                .order_by(SourceFragment.position_index)
            )
        ).scalars().all()

    table = next((f for f in frags if f.element_type.value == "table"), None)
    assert table is not None
    assert table.meta_json is not None
    assert table.meta_json["rows"][0] == ["Model", "BLEU"]
    assert table.meta_json["rows"][1] == ["Transformer", "28.4"]
