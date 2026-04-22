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

from app.domain.articles.article_builder import _looks_like_noise_text
from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import (
    ArticleCandidate,
    ProposalStatus,
    StructureProposal,
)

from tests.helpers import confirm_all_candidates, make_docx, make_rich_docx, unique_docx


def test_builder_noise_filter_identifies_pdf_artifacts():
    assert _looks_like_noise_text("2")
    assert _looks_like_noise_text("<EOS>")
    assert _looks_like_noise_text("GNMT + RL [38] 24.6 39.92 2 . 3 . 10 19 1 . 4 . 10 20")
    assert not _looks_like_noise_text("The transformer uses multi-head attention in three different ways.")


async def _prepare_ready_proposal(
    client, project, session_factory, tmp_path
) -> uuid.UUID:
    """Full pipeline up to (but not including) /articles/build.

    Every candidate is bulk-confirmed before returning, because the builder
    now only materialises CONFIRMED candidates. Tests that specifically care
    about the confirmation gate should set that up themselves instead of
    calling this helper.
    """
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

    await confirm_all_candidates(client, project["id"], str(proposal_id))

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


async def test_build_skips_duplicate_h1_heading_block(
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

    async with session_factory() as db:
        articles = (
            await db.execute(
                select(Article)
                .where(Article.project_id == uuid.UUID(project["id"]))
                .order_by(Article.title)
            )
        ).scalars().all()

        intro = next((article for article in articles if article.title == "Introduction"), None)
        assert intro is not None

        blocks = (
            await db.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == intro.id)
                .order_by(ArticleBlock.position_index)
            )
        ).scalars().all()

    assert blocks, "introduction article should still have body blocks"
    assert blocks[0].content != "Introduction"


async def test_build_marks_small_articles_as_nodes_and_links_structural_block(
    client, session_factory, tmp_path
):
    project_resp = await client.post(
        "/projects",
        json={
            "name": "Node thresholds",
            "settings": {"min_blocks": 10, "min_chars": 1000},
        },
    )
    project = project_resp.json()

    block_resp = await client.post(
        f"/projects/{project['id']}/structural-blocks",
        json={"name": "Introduction"},
    )
    assert block_resp.status_code == 201, block_resp.text

    proposal_id = await _prepare_ready_proposal(
        client, project, session_factory, tmp_path
    )

    build = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert build.status_code == 200, build.text

    listing = await client.get(f"/projects/{project['id']}/articles")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert rows
    assert all(row["kind"] == "node" for row in rows)

    intro = next((row for row in rows if row["title"] == "Introduction"), None)
    assert intro is not None
    assert intro["structural_block_id"] == block_resp.json()["id"]


async def test_build_refreshes_project_summary(
    client, session_factory, tmp_path
):
    created = await client.post("/projects", json={"name": "Summary project"})
    assert created.status_code == 201, created.text
    project = created.json()

    proposal_id = await _prepare_ready_proposal(
        client, project, session_factory, tmp_path
    )

    build = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert build.status_code == 200, build.text

    detail = await client.get(f"/projects/{project['id']}")
    assert detail.status_code == 200, detail.text
    summary = detail.json()["summary"]
    assert summary
    assert "Summary project knowledge base overview:" in summary


async def test_build_preserves_inline_spans_and_image_refs(
    client, project, session_factory, tmp_path
):
    docx = make_rich_docx(unique_docx(tmp_path))
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
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)

    await confirm_all_candidates(client, project["id"], str(proposal_id))

    build = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert build.status_code == 200, build.text
    article_id = build.json()["article_ids"][0]

    detail = await client.get(f"/projects/{project['id']}/articles/{article_id}")
    assert detail.status_code == 200, detail.text
    blocks = detail.json()["blocks"]

    paragraph = next((block for block in blocks if block["element_type"] == "paragraph"), None)
    assert paragraph is not None
    assert {span["style"] for span in paragraph["inline_spans"]} >= {"bold", "italic", "code"}

    image = next((block for block in blocks if block["element_type"] == "image"), None)
    assert image is not None
    assert image["meta_json"]["image_ref"].startswith(f"/projects/{project['id']}/sources/assets/")


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


# ---------------------------------------------------------------------------
# Confirmation gate — the builder now only materialises CONFIRMED candidates.
# ---------------------------------------------------------------------------


async def _prepare_ready_proposal_unconfirmed(
    client, project, session_factory, tmp_path
) -> uuid.UUID:
    """Same as _prepare_ready_proposal, but WITHOUT bulk-confirming — every
    candidate stays in `proposed` status so we can exercise the gate."""
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


async def test_build_skips_unconfirmed_candidates(
    client, project, session_factory, tmp_path
):
    """Without any user confirmation, build produces zero articles (not an
    error) — the proposal still transitions to REVIEWED so the UI doesn't
    loop, and the user gets a clear signal to go confirm something."""
    await _prepare_ready_proposal_unconfirmed(
        client, project, session_factory, tmp_path
    )

    r = await client.post(f"/projects/{project['id']}/articles/build")
    assert r.status_code == 200, r.text
    assert r.json() == {"article_ids": [], "count": 0}


async def test_build_only_materialises_confirmed(
    client, project, session_factory, tmp_path
):
    """When only some candidates are confirmed, build produces exactly
    that many articles — not one per candidate, not zero."""
    proposal_id = await _prepare_ready_proposal_unconfirmed(
        client, project, session_factory, tmp_path
    )

    # Confirm exactly one candidate via the PATCH endpoint.
    async with session_factory() as db:
        cands = (
            await db.execute(
                select(ArticleCandidate).where(
                    ArticleCandidate.proposal_id == proposal_id
                )
            )
        ).scalars().all()
    assert len(cands) >= 2, "docx fixture must yield >=2 candidates"
    target = cands[0]

    r = await client.patch(
        f"/projects/{project['id']}/structure/candidates/{target.id}",
        json={"status": "confirmed"},
    )
    assert r.status_code == 200

    r = await client.post(f"/projects/{project['id']}/articles/build")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert len(body["article_ids"]) == 1


async def test_confirm_all_flips_only_proposed(
    client, project, session_factory, tmp_path
):
    """confirm-all is idempotent: `rejected` and already-`confirmed`
    candidates are untouched, only `proposed` becomes `confirmed`."""
    proposal_id = await _prepare_ready_proposal_unconfirmed(
        client, project, session_factory, tmp_path
    )

    # Reject one candidate manually before calling confirm-all.
    async with session_factory() as db:
        cands = (
            await db.execute(
                select(ArticleCandidate).where(
                    ArticleCandidate.proposal_id == proposal_id
                )
            )
        ).scalars().all()
    rejected = cands[0]
    r = await client.patch(
        f"/projects/{project['id']}/structure/candidates/{rejected.id}",
        json={"status": "rejected"},
    )
    assert r.status_code == 200

    r = await client.post(
        f"/projects/{project['id']}/structure/proposals/{proposal_id}/confirm-all"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == len(cands)
    # Flipped N-1 (everything except the one we rejected).
    assert body["confirmed_count"] == len(cands) - 1

    # Second call is a no-op (everything is already confirmed or rejected).
    r2 = await client.post(
        f"/projects/{project['id']}/structure/proposals/{proposal_id}/confirm-all"
    )
    assert r2.status_code == 200
    assert r2.json()["confirmed_count"] == 0

    # Build: only the confirmed ones become articles.
    r3 = await client.post(f"/projects/{project['id']}/articles/build")
    assert r3.status_code == 200
    assert r3.json()["count"] == len(cands) - 1


async def test_confirm_all_unknown_proposal_returns_404(client, project):
    random_id = "00000000-0000-0000-0000-000000000000"
    r = await client.post(
        f"/projects/{project['id']}/structure/proposals/{random_id}/confirm-all"
    )
    assert r.status_code == 404
