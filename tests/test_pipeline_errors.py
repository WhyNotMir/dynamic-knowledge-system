"""Failure cases across the pipeline.

These are the specific error paths the UI relies on to avoid leaking 500s
and to surface actionable messages to the user:

  /articles/build
    - 404 if proposal_id is unknown
    - 404 if proposal belongs to a different project
    - 409 if proposal is not READY (still PENDING or already REVIEWED)
    - 409 if no proposal at all exists for the project (UI didn't propose)
    - 400 if a candidate carries malformed fragment_ids
    - article detail endpoint: 404 for unknown article id

  /structure/propose
    - 404 for unknown project

  /articles (GET):
    - empty list for a project with no built articles
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.article_candidate import (
    ArticleCandidate,
    ProposalStatus,
    StructureProposal,
)

from tests.helpers import make_docx, unique_docx


async def _setup_ready_proposal(client, project, session_factory, tmp_path):
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    source_id = uuid.UUID(up.json()["id"])
    async with session_factory() as db:
        await run_ingestion(source_id, db)

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
    return proposal_id


# ---------------------------------------------------------------------------
# /articles/build
# ---------------------------------------------------------------------------


async def test_build_with_unknown_proposal_id_returns_404(client, project):
    random_id = "00000000-0000-0000-0000-000000000000"
    r = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": random_id},
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


async def test_build_without_body_and_without_ready_proposal_returns_409(
    client, project
):
    """UI calls /articles/build immediately — there is no READY proposal yet."""
    r = await client.post(f"/projects/{project['id']}/articles/build")
    assert r.status_code == 409
    assert "ready" in r.json()["detail"].lower()


async def test_build_with_pending_proposal_returns_409(
    client, project, session_factory, tmp_path
):
    """Propose but don't run the worker — the proposal stays PENDING."""
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    source_id = uuid.UUID(up.json()["id"])
    async with session_factory() as db:
        await run_ingestion(source_id, db)

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = r.json()["proposal_id"]

    r2 = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": proposal_id},
    )
    assert r2.status_code == 409
    detail = r2.json()["detail"].lower()
    assert "pending" in detail or "expected 'ready'" in detail


async def test_build_with_already_reviewed_proposal_returns_409(
    client, project, session_factory, tmp_path
):
    """First build transitions READY -> REVIEWED. Second build must 409."""
    proposal_id = await _setup_ready_proposal(
        client, project, session_factory, tmp_path
    )
    r1 = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert r2.status_code == 409
    assert "reviewed" in r2.json()["detail"].lower()


async def test_build_with_cross_project_proposal_returns_404(
    client, session_factory, tmp_path
):
    """A proposal from project A must not be buildable under project B."""
    # Two projects.
    pA = (await client.post("/projects", json={"name": "A"})).json()
    pB = (await client.post("/projects", json={"name": "B"})).json()

    # Fully prep proposal for A.
    proposal_id = await _setup_ready_proposal(client, pA, session_factory, tmp_path)

    # Try to build it under B.
    r = await client.post(
        f"/projects/{pB['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert r.status_code == 404
    assert "does not belong" in r.json()["detail"].lower()


async def test_build_with_malformed_fragment_ids_returns_400(
    client, project, session_factory, tmp_path
):
    """If a candidate somehow has malformed fragment_ids, the builder raises
    ValueError and the API returns 400 (not 500)."""
    proposal_id = await _setup_ready_proposal(
        client, project, session_factory, tmp_path
    )

    # Corrupt one candidate's fragment_ids.
    async with session_factory() as db:
        cand = (
            await db.execute(
                select(ArticleCandidate).where(
                    ArticleCandidate.proposal_id == proposal_id
                )
            )
        ).scalars().first()
        assert cand is not None
        cand.fragment_ids = ["not-a-uuid"]
        await db.commit()

    r = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert r.status_code == 400
    assert "fragment_id" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /articles and /articles/{id}
# ---------------------------------------------------------------------------


async def test_get_unknown_article_returns_404(client, project):
    random_id = "00000000-0000-0000-0000-000000000000"
    r = await client.get(f"/projects/{project['id']}/articles/{random_id}")
    assert r.status_code == 404


async def test_list_articles_on_empty_project_returns_empty(client, project):
    r = await client.get(f"/projects/{project['id']}/articles")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# /structure/propose
# ---------------------------------------------------------------------------


async def test_propose_structure_for_unknown_project_returns_404(client):
    random_pid = "00000000-0000-0000-0000-000000000000"
    r = await client.post(f"/projects/{random_pid}/structure/propose")
    assert r.status_code == 404


async def test_get_unknown_proposal_returns_404(client, project):
    random_id = "00000000-0000-0000-0000-000000000000"
    r = await client.get(
        f"/projects/{project['id']}/structure/proposals/{random_id}"
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Structure worker robustness
# ---------------------------------------------------------------------------


async def test_structure_worker_on_empty_project_marks_ready_with_zero_candidates(
    client, project, session_factory
):
    """If the project has no fragments, the worker must still transition the
    proposal to READY (with 0 candidates) rather than hanging in PENDING."""
    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)

    async with session_factory() as db:
        p = await db.get(StructureProposal, proposal_id)
        assert p.status == ProposalStatus.READY
        cands = (
            await db.execute(
                select(ArticleCandidate).where(
                    ArticleCandidate.proposal_id == proposal_id
                )
            )
        ).scalars().all()
        assert cands == []

    # Building an empty-but-READY proposal yields zero articles (not an error).
    r2 = await client.post(f"/projects/{project['id']}/articles/build")
    assert r2.status_code == 200
    assert r2.json() == {"article_ids": [], "count": 0}
