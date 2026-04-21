from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import ArticleCandidate, StructureProposal
from app.models.source_fragment import SourceFragment

from tests.helpers import confirm_all_candidates


GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_DOCX = GOLDEN_DIR / "phase0_foundation.docx"
GOLDEN_BASELINE = GOLDEN_DIR / "phase0_foundation_baseline.json"


async def _run_pipeline_from_docx(
    client,
    project,
    session_factory,
    docx_path: Path,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with docx_path.open("rb") as fh:
        upload = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx_path.name, fh)},
        )
    assert upload.status_code == 202, upload.text
    source_id = uuid.UUID(upload.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    proposal_resp = await client.post(f"/projects/{project['id']}/structure/propose")
    assert proposal_resp.status_code == 202, proposal_resp.text
    proposal_id = uuid.UUID(proposal_resp.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)

    await confirm_all_candidates(client, project["id"], str(proposal_id))

    build_resp = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert build_resp.status_code == 200, build_resp.text
    body = build_resp.json()
    return proposal_id, [uuid.UUID(item) for item in body["article_ids"]]


async def test_source_order_monotonic(
    client, project, session_factory
):
    proposal_id, article_ids = await _run_pipeline_from_docx(
        client, project, session_factory, GOLDEN_DOCX
    )
    assert article_ids, f"proposal {proposal_id} should build at least one article"

    async with session_factory() as db:
        result = await db.execute(
            select(ArticleBlock)
            .where(ArticleBlock.article_id.in_(article_ids))
            .options(selectinload(ArticleBlock.fragment))
            .order_by(ArticleBlock.article_id, ArticleBlock.position_index)
        )
        blocks = result.scalars().all()

    per_article_source: dict[tuple[uuid.UUID, uuid.UUID], list[int]] = {}
    for block in blocks:
        if block.synthesized or block.fragment is None or block.source_position_index is None:
            continue
        key = (block.article_id, block.fragment.source_id)
        per_article_source.setdefault(key, []).append(block.source_position_index)

    assert per_article_source, "expected at least one (article, source) sequence to validate"
    for key, positions in per_article_source.items():
        assert positions == sorted(positions), f"non-monotonic source order for {key}: {positions}"


async def test_content_immutability(
    client, project, session_factory
):
    _, article_ids = await _run_pipeline_from_docx(
        client, project, session_factory, GOLDEN_DOCX
    )

    async with session_factory() as db:
        result = await db.execute(
            select(ArticleBlock)
            .where(ArticleBlock.article_id.in_(article_ids))
            .options(selectinload(ArticleBlock.fragment))
            .order_by(ArticleBlock.article_id, ArticleBlock.position_index)
        )
        blocks = result.scalars().all()

    assert blocks, "expected at least one article block"
    for block in blocks:
        if block.synthesized:
            continue
        assert block.fragment is not None, f"non-synthesized block {block.id} lost its source fragment"
        assert block.content == block.fragment.content
        assert block.source_position_index == block.fragment.position_index


async def test_phase0_golden_pipeline_matches_baseline(
    client, project, session_factory
):
    expected = json.loads(GOLDEN_BASELINE.read_text())
    proposal_id, article_ids = await _run_pipeline_from_docx(
        client, project, session_factory, GOLDEN_DOCX
    )

    async with session_factory() as db:
        proposal = await db.get(
            StructureProposal,
            proposal_id,
            options=(
                selectinload(StructureProposal.candidates).selectinload(
                    ArticleCandidate.candidate_fragments
                ),
            ),
        )
        assert proposal is not None

        articles = (
            await db.execute(
                select(Article)
                .where(Article.id.in_(article_ids))
                .options(selectinload(Article.blocks))
            )
        ).scalars().all()

    actual = {
        "candidate_count": len(proposal.candidates),
        "candidates": sorted(
            [
                {
                    "title": candidate.title,
                    "source_section_path": candidate.source_section_path,
                    "fragment_count": len(candidate.candidate_fragments),
                }
                for candidate in proposal.candidates
            ],
            key=lambda item: item["title"],
        ),
        "article_count": len(articles),
        "articles": sorted(
            [
                {
                    "title": article.title,
                    "slug": article.slug,
                    "block_count": len(article.blocks),
                }
                for article in articles
            ],
            key=lambda item: item["title"],
        ),
    }

    assert actual == expected


async def test_phase0_golden_fixture_exists():
    assert GOLDEN_DOCX.exists(), f"missing golden fixture: {GOLDEN_DOCX}"
    assert GOLDEN_BASELINE.exists(), f"missing golden baseline: {GOLDEN_BASELINE}"

    baseline = json.loads(GOLDEN_BASELINE.read_text())
    assert baseline["candidate_count"] >= 1
    assert baseline["article_count"] >= 1
