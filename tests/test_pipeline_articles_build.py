"""Articles/build stage (happy paths).

We walk the whole pipeline — project -> upload -> ingest -> propose ->
run_structure_proposal -> /articles/build — and assert that every stage
produces the shape the UI expects:

  - /articles/build returns {article_ids: [...], count: N} with N > 0
  - each Article has matching ArticleBlocks
  - GET /articles lists all articles with block_count
  - GET /articles/{id} returns the full detail with blocks ordered by
    position_index
  - After a successful build the originating proposal transitions
    READY -> REVIEWED so it can't be rebuilt accidentally.
  - /articles/build with proposal_id omitted (the common UI path) resolves
    the latest READY proposal automatically.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import (
    ArticleCandidate,
    ProposalStatus,
    StructureProposal,
)

from tests.helpers import make_docx, unique_docx


async def _prepare_ready_proposal(
    client, project, session_factory, tmp_path
) -> uuid.UUID:
    """Full pipeline up to (but not including) /articles/build."""
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    assert up.status_code == 202, up.text
    source_id = uuid.UUID(up.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    assert r.status_code == 202
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)

    return proposal_id


async def test_build_articles_with_explicit_proposal_id(
    client, project, session_factory, tmp_path
):
    proposal_id = await _prepare_ready_proposal(
        client, project, session_factory, tmp_path
    )

    r = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 2
    assert len(body["article_ids"]) == body["count"]
    # Each article id must be a valid UUID.
    for aid in body["article_ids"]:
        uuid.UUID(aid)

    # DB: Articles created with matching candidate_id, and ArticleBlocks exist.
    async with session_factory() as db:
        articles = (
            await db.execute(
                select(Article).where(Article.project_id == uuid.UUID(project["id"]))
            )
        ).scalars().all()
        assert len(articles) == body["count"]

        for art in articles:
            assert art.title
            blocks = (
                await db.execute(
                    select(ArticleBlock)
                    .where(ArticleBlock.article_id == art.id)
                    .order_by(ArticleBlock.position_index)
                )
            ).scalars().all()
            assert len(blocks) > 0
            # Blocks are sorted by position_index.
            pis = [b.position_index for b in blocks]
            assert pis == sorted(pis)
            # Every block has valid element_type string.
            for b in blocks:
                assert b.element_type
                assert b.content

        # The proposal transitioned to REVIEWED.
        p = await db.get(StructureProposal, proposal_id)
        assert p.status == ProposalStatus.REVIEWED


async def test_build_articles_without_proposal_id_resolves_latest_ready(
    client, project, session_factory, tmp_path
):
    await _prepare_ready_proposal(client, project, session_factory, tmp_path)

    # No body — the UI happy path.
    r = await client.post(f"/projects/{project['id']}/articles/build")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 2


async def test_list_articles_returns_block_counts(
    client, project, session_factory, tmp_path
):
    await _prepare_ready_proposal(client, project, session_factory, tmp_path)
    r = await client.post(f"/projects/{project['id']}/articles/build")
    assert r.status_code == 200
    built = r.json()

    r2 = await client.get(f"/projects/{project['id']}/articles")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == built["count"]
    for row in rows:
        assert row["block_count"] > 0
        assert row["title"]
        assert row["status"] == "draft"
        assert row["created_at"]


async def test_get_article_detail_returns_blocks_ordered(
    client, project, session_factory, tmp_path
):
    await _prepare_ready_proposal(client, project, session_factory, tmp_path)
    r = await client.post(f"/projects/{project['id']}/articles/build")
    article_ids = r.json()["article_ids"]

    for aid in article_ids:
        rd = await client.get(f"/projects/{project['id']}/articles/{aid}")
        assert rd.status_code == 200, rd.text
        detail = rd.json()
        assert detail["id"] == aid
        assert detail["title"]
        assert isinstance(detail["blocks"], list)
        assert len(detail["blocks"]) > 0
        # Blocks sorted by position_index (ORM relationship order_by).
        pis = [b["position_index"] for b in detail["blocks"]]
        assert pis == sorted(pis)


async def test_articles_are_linked_back_to_candidates(
    client, project, session_factory, tmp_path
):
    """Article.candidate_id should be set so the UI can trace provenance."""
    proposal_id = await _prepare_ready_proposal(
        client, project, session_factory, tmp_path
    )
    r = await client.post(f"/projects/{project['id']}/articles/build")
    assert r.status_code == 200

    async with session_factory() as db:
        articles = (
            await db.execute(
                select(Article).where(Article.project_id == uuid.UUID(project["id"]))
            )
        ).scalars().all()
        cand_ids = (
            await db.execute(
                select(ArticleCandidate.id).where(
                    ArticleCandidate.proposal_id == proposal_id
                )
            )
        ).scalars().all()

    linked = {a.candidate_id for a in articles if a.candidate_id is not None}
    # Every built article came from one of the proposal's candidates.
    assert linked.issubset(set(cand_ids))
    assert len(linked) == len(articles)
