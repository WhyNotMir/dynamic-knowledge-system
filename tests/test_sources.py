"""Source upload endpoint: validation, storage, arq enqueue.

We don't run the worker here (see test_pipeline_ingestion for that); we only
verify that:
  - a valid upload returns 202 and a Source row in PENDING,
  - the ingestion job is enqueued on the (fake) arq pool,
  - bad inputs produce the right 4xx response.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import make_docx, unique_docx


async def test_upload_valid_docx_accepted(client, project, arq_pool, tmp_path):
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        r = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["filename"] == docx.name
    assert body["source_type"] == "docx"
    assert body["status"] == "pending"
    assert body["project_id"] == project["id"]

    # Background job enqueued for this source.
    assert any(
        job_name == "ingest_source" and args == (body["id"],)
        for job_name, args in arq_pool.jobs
    )


async def test_upload_rejects_unsupported_extension(client, project, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with bad.open("rb") as fh:
        r = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (bad.name, fh, "text/plain")},
        )
    assert r.status_code == 400
    assert "Unsupported" in r.json()["detail"]


async def test_upload_to_unknown_project_returns_404(client, tmp_path):
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        r = await client.post(
            "/projects/00000000-0000-0000-0000-000000000000/sources",
            files={"file": (docx.name, fh)},
        )
    assert r.status_code == 404


async def test_list_sources_reflects_uploads(client, project, tmp_path):
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    assert up.status_code == 202
    r = await client.get(f"/projects/{project['id']}/sources")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == up.json()["id"]
