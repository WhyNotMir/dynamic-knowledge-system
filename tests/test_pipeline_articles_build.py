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
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.chains.title_agent import _fallback_description
from app.domain.articles.article_text import (
    is_meaningful_body_fragment,
    looks_like_noise_text,
)
from app.domain.articles.article_builder import build_articles_from_proposal
from app.domain.articles.block_builder import has_meaningful_body, prepare_article_blocks
from app.domain.articles.fidelity import audit_source_article_fidelity
from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.alias import Alias
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalStatus,
    StructureProposal,
)
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_fragment import ElementType, SourceFragment
from app.models.graph_edge import EdgeKind, GraphEdge

from tests.helpers import (
    confirm_all_candidates,
    make_docx,
    make_linked_docx,
    make_rich_docx,
    unique_docx,
)


def test_production_code_has_no_attention_fixture_specific_markers():
    production_roots = [
        Path("app/domain"),
        Path("app/agents"),
    ]
    forbidden_markers = [
        "Attention Is All You Need",
        "Transformer (base model)",
        "GNMT",
        "EN-DE",
        "EN-FR",
        "WSJ",
        "Table 4",
        "P100",
    ]

    offenders: list[str] = []
    for root in production_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden_markers:
                if marker in text:
                    offenders.append(f"{path}: {marker}")

    assert offenders == []


def test_builder_noise_filter_identifies_pdf_artifacts():
    assert looks_like_noise_text("2")
    assert looks_like_noise_text("<EOS>")
    assert looks_like_noise_text("arXiv:1706.03762")
    assert looks_like_noise_text("Ashish Vaswani * Google Brain avaswani@google.com")
    assert not looks_like_noise_text("GNMT + RL [38] 24.6 39.92 2 . 3 . 10 19 1 . 4 . 10 20")
    assert not looks_like_noise_text("The transformer uses multi-head attention in three different ways.")


def test_prepare_article_blocks_keeps_scientific_tables_and_wsJ_paragraphs():
    source_id = uuid.uuid4()
    table = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content="GNMT + RL [38] 24.6 39.92 2 . 3 · 10 19 1 . 4 · 10 20",
        element_type=ElementType.TABLE,
        heading_level=None,
        list_level=None,
        group_id=None,
        page_number=8,
        section_path="6 Results",
        position_index=86,
        inline_spans=None,
        meta_json={"table": {"plain_text": "GNMT + RL [38] 24.6 39.92"}},
        embedding=None,
    )
    paragraph = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content=(
            "We trained a 4-layer transformer with d model = 1024 on the Wall "
            "Street Journal (WSJ) portion of the Penn Treebank, about 40K "
            "training sentences, and used a larger semi-supervised setting."
        ),
        element_type=ElementType.PARAGRAPH,
        heading_level=None,
        list_level=None,
        group_id=None,
        page_number=9,
        section_path="6.3 English Constituency Parsing",
        position_index=106,
        inline_spans=None,
        meta_json=None,
        embedding=None,
    )
    candidate = SimpleNamespace(
        title="Results",
        source_section_path="6 Results",
        candidate_fragments=[
            SimpleNamespace(position_index=0, fragment=table),
            SimpleNamespace(position_index=1, fragment=paragraph),
        ],
    )

    prepared = prepare_article_blocks(candidate)

    assert [fragment.position_index for fragment, _ in prepared] == [86, 106]


def test_prepare_article_blocks_treats_references_as_body_content():
    source_id = uuid.uuid4()
    reference = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content="[10] Alex Graves. Generating sequences with recurrent neural networks. arXiv preprint arXiv:1308.0850, 2013.",
        element_type=ElementType.FOOTNOTE,
        heading_level=None,
        list_level=None,
        group_id=None,
        page_number=11,
        section_path="References",
        position_index=124,
        inline_spans=None,
        meta_json={"references_section": True},
        embedding=None,
    )
    candidate = SimpleNamespace(
        title="References",
        source_section_path="References",
        candidate_fragments=[SimpleNamespace(position_index=0, fragment=reference)],
    )

    prepared = prepare_article_blocks(candidate)

    assert has_meaningful_body(prepared)
    assert [fragment.position_index for fragment, _ in prepared] == [124]


def test_prepare_article_blocks_moves_pdf_float_table_to_first_near_reference():
    source_id = uuid.uuid4()
    group_id = "caption-table-p8-100-100-0"
    caption = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content="Table 2: Translation quality and training cost.",
        element_type=ElementType.CAPTION,
        heading_level=None,
        list_level=None,
        group_id=None,
        page_number=8,
        section_path="6 Results",
        position_index=85,
        inline_spans=None,
        meta_json={"caption_group_id": group_id},
        embedding=None,
    )
    table = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content="Model | BLEU | Training Cost",
        element_type=ElementType.TABLE,
        heading_level=None,
        list_level=None,
        group_id=None,
        page_number=8,
        section_path="6 Results",
        position_index=86,
        inline_spans=None,
        meta_json={"caption_group_id": group_id, "table": {"display_mode": "preformatted"}},
        embedding=None,
    )
    heading = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content="6.1 Machine Translation",
        element_type=ElementType.HEADING,
        heading_level=2,
        list_level=None,
        group_id=None,
        page_number=8,
        section_path="6 Results",
        position_index=90,
        inline_spans=None,
        meta_json=None,
        embedding=None,
    )
    paragraph = SourceFragment(
        id=uuid.uuid4(),
        source_id=source_id,
        content="Table 2 summarizes our results and compares translation quality.",
        element_type=ElementType.PARAGRAPH,
        heading_level=None,
        list_level=None,
        group_id=None,
        page_number=8,
        section_path="6 Results > 6.1 Machine Translation",
        position_index=91,
        inline_spans=None,
        meta_json=None,
        embedding=None,
    )
    candidate = SimpleNamespace(
        title="Results",
        source_section_path="6 Results",
        candidate_fragments=[
            SimpleNamespace(position_index=0, fragment=caption),
            SimpleNamespace(position_index=1, fragment=table),
            SimpleNamespace(position_index=2, fragment=heading),
            SimpleNamespace(position_index=3, fragment=paragraph),
        ],
    )

    prepared = prepare_article_blocks(candidate)

    assert [fragment.position_index for fragment, _ in prepared] == [90, 91, 85, 86]


async def test_fidelity_audit_reports_missing_meaningful_fragments(
    project, session_factory
):
    source_id = uuid.uuid4()
    kept_id = uuid.uuid4()
    missing_id = uuid.uuid4()

    async with session_factory() as db:
        source = Source(
            id=source_id,
            project_id=uuid.UUID(project["id"]),
            filename="attention.pdf",
            title="Attention Is All You Need",
            source_type=SourceType.PDF,
            storage_path="/tmp/attention.pdf",
            status=SourceStatus.DONE,
        )
        kept = SourceFragment(
            id=kept_id,
            source_id=source_id,
            content="A paragraph represented in an article.",
            element_type=ElementType.PARAGRAPH,
            position_index=1,
            page_number=1,
        )
        missing_table = SourceFragment(
            id=missing_id,
            source_id=source_id,
            content="Transformer (base model) 27.3 38.1 3.3 · 10 18",
            element_type=ElementType.TABLE,
            position_index=2,
            page_number=8,
        )
        article = Article(
            project_id=uuid.UUID(project["id"]),
            title="Results",
            slug="results",
        )
        db.add_all([source, kept, missing_table, article])
        await db.flush()
        db.add(
            ArticleBlock(
                article_id=article.id,
                fragment_id=kept_id,
                content=kept.content,
                element_type=kept.element_type,
                position_index=0,
                source_position_index=kept.position_index,
                page_number=kept.page_number,
            )
        )
        await db.commit()

    async with session_factory() as db:
        report = await audit_source_article_fidelity(
            source_id=source_id,
            project_id=uuid.UUID(project["id"]),
            db=db,
        )

    assert report.meaningful_fragment_count == 2
    assert report.covered_fragment_count == 1
    assert report.missing_fragment_ids == [missing_id]
    assert report.issues[0].code == "missing_fragment"


async def test_builder_recovers_referenced_table_caption_group(
    project, session_factory, monkeypatch
):
    async def fake_metadata(**kwargs):
        candidate = kwargs["candidate"]
        return SimpleNamespace(
            title=candidate.title,
            description="Recovered table article.",
            suggested_structural_block=None,
        )

    monkeypatch.setattr(
        "app.domain.articles.article_builder.enrich_or_fallback_candidate_metadata",
        fake_metadata,
    )

    project_id = uuid.UUID(project["id"])
    source_id = uuid.uuid4()
    group_id = "caption-table-4"

    async with session_factory() as db:
        source = Source(
            id=source_id,
            project_id=project_id,
            filename="attention.pdf",
            title="Attention Is All You Need",
            source_type=SourceType.PDF,
            storage_path="/tmp/attention.pdf",
            status=SourceStatus.DONE,
        )
        body = SourceFragment(
            source_id=source_id,
            content=(
                "Our results in Table 4 show that despite the lack of task-specific "
                "tuning the Transformer performs surprisingly well on parsing."
            ),
            element_type=ElementType.PARAGRAPH,
            position_index=10,
            page_number=10,
            section_path="6.3 English Constituency Parsing",
        )
        caption = SourceFragment(
            source_id=source_id,
            content="Table 4: The Transformer generalizes well to English constituency parsing.",
            element_type=ElementType.CAPTION,
            position_index=11,
            page_number=10,
            section_path="6.3 English Constituency Parsing",
            meta_json={"caption_group_id": group_id},
        )
        table = SourceFragment(
            source_id=source_id,
            content="Parser | Training | WSJ 23 F1\nTransformer (4 layers) | semi-supervised | 92.7",
            element_type=ElementType.TABLE,
            position_index=12,
            page_number=10,
            section_path="6.3 English Constituency Parsing",
            meta_json={"caption_group_id": group_id},
        )
        proposal = StructureProposal(project_id=project_id, status=ProposalStatus.READY)
        db.add_all([source, body, caption, table, proposal])
        await db.flush()
        candidate = ArticleCandidate(
            proposal_id=proposal.id,
            title="English Constituency Parsing",
            source_section_path="6.3 English Constituency Parsing",
            status=CandidateStatus.CONFIRMED,
        )
        db.add(candidate)
        await db.flush()
        db.add(
            ArticleCandidateFragment(
                candidate_id=candidate.id,
                fragment_id=body.id,
                position_index=0,
            )
        )
        await db.flush()

        article_ids = await build_articles_from_proposal(proposal, db)
        await db.commit()

    assert len(article_ids) == 1
    async with session_factory() as db:
        blocks = (
            await db.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == article_ids[0])
                .order_by(ArticleBlock.source_position_index)
            )
        ).scalars().all()

    assert [block.element_type for block in blocks] == [
        ElementType.PARAGRAPH,
        ElementType.CAPTION,
        ElementType.TABLE,
    ]


async def test_builder_does_not_duplicate_distant_referenced_table(
    project, session_factory, monkeypatch
):
    async def fake_metadata(**kwargs):
        candidate = kwargs["candidate"]
        return SimpleNamespace(
            title=candidate.title,
            description="Table duplication check.",
            suggested_structural_block=None,
        )

    monkeypatch.setattr(
        "app.domain.articles.article_builder.enrich_or_fallback_candidate_metadata",
        fake_metadata,
    )

    project_id = uuid.UUID(project["id"])
    source_id = uuid.uuid4()
    group_id = "caption-table-results"

    async with session_factory() as db:
        source = Source(
            id=source_id,
            project_id=project_id,
            filename="generic.pdf",
            title="Generic Research Report",
            source_type=SourceType.PDF,
            storage_path="/tmp/generic.pdf",
            status=SourceStatus.DONE,
        )
        distant_ref = SourceFragment(
            source_id=source_id,
            content="Earlier context mentions Table 4 as related background evidence.",
            element_type=ElementType.PARAGRAPH,
            position_index=1,
            page_number=1,
            section_path="1 Background",
        )
        owner_body = SourceFragment(
            source_id=source_id,
            content="The local results in Table 4 provide the full comparison.",
            element_type=ElementType.PARAGRAPH,
            position_index=40,
            page_number=6,
            section_path="6 Results",
        )
        caption = SourceFragment(
            source_id=source_id,
            content="Table 4: Full comparison.",
            element_type=ElementType.CAPTION,
            position_index=41,
            page_number=6,
            section_path="6 Results",
            meta_json={"caption_group_id": group_id},
        )
        table = SourceFragment(
            source_id=source_id,
            content="System | Score\nBase | 90",
            element_type=ElementType.TABLE,
            position_index=42,
            page_number=6,
            section_path="6 Results",
            meta_json={"caption_group_id": group_id},
        )
        unrelated_caption = SourceFragment(
            source_id=source_id,
            content="Table 9: Unrelated appendix data.",
            element_type=ElementType.CAPTION,
            position_index=80,
            page_number=12,
            section_path="Appendix",
            meta_json={"caption_group_id": group_id},
        )
        unrelated_table = SourceFragment(
            source_id=source_id,
            content="Other | Value\nFar | 1",
            element_type=ElementType.TABLE,
            position_index=81,
            page_number=12,
            section_path="Appendix",
            meta_json={"caption_group_id": group_id},
        )
        proposal = StructureProposal(project_id=project_id, status=ProposalStatus.READY)
        db.add_all(
            [
                source,
                distant_ref,
                owner_body,
                caption,
                table,
                unrelated_caption,
                unrelated_table,
                proposal,
            ]
        )
        await db.flush()

        distant_candidate = ArticleCandidate(
            proposal_id=proposal.id,
            title="Background",
            source_section_path="1 Background",
            status=CandidateStatus.CONFIRMED,
        )
        owner_candidate = ArticleCandidate(
            proposal_id=proposal.id,
            title="Results",
            source_section_path="6 Results",
            status=CandidateStatus.CONFIRMED,
        )
        db.add_all([distant_candidate, owner_candidate])
        await db.flush()
        db.add_all(
            [
                ArticleCandidateFragment(
                    candidate_id=distant_candidate.id,
                    fragment_id=distant_ref.id,
                    position_index=0,
                ),
                ArticleCandidateFragment(
                    candidate_id=owner_candidate.id,
                    fragment_id=owner_body.id,
                    position_index=0,
                ),
            ]
        )
        await db.flush()

        article_ids = await build_articles_from_proposal(proposal, db)
        await db.commit()

    assert len(article_ids) == 2
    async with session_factory() as db:
        articles = (
            await db.execute(
                select(Article)
                .where(Article.id.in_(article_ids))
                .options(selectinload(Article.blocks))
            )
        ).scalars().all()

    by_title = {article.title: article for article in articles}
    assert [block.element_type for block in by_title["Background"].blocks] == [
        ElementType.PARAGRAPH
    ]
    assert sorted(block.element_type.value for block in by_title["Results"].blocks) == [
        ElementType.CAPTION.value,
        ElementType.PARAGRAPH.value,
        ElementType.TABLE.value,
    ]
    assert all(
        "Unrelated appendix data" not in block.content
        and "Other | Value" not in block.content
        for block in by_title["Results"].blocks
    )


def test_fallback_description_uses_more_than_opening_line():
    fragments = [
        SimpleNamespace(
            element_type=SimpleNamespace(value="paragraph"),
            content="The transformer uses multi-head attention in three different ways.",
        ),
        SimpleNamespace(
            element_type=SimpleNamespace(value="paragraph"),
            content="The encoder contains self-attention layers, while the decoder combines masked self-attention with encoder-decoder attention.",
        ),
    ]

    description = _fallback_description(fragments)

    assert description is not None
    assert description.startswith("This article discusses")
    assert "three different ways" in description or "Transformer" in description


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
        await db.commit()

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    assert r.status_code == 202
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
        await db.commit()

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


async def test_delete_all_articles_removes_everything_for_project(
    client, project, session_factory, tmp_path
):
    await _prepare_ready_proposal(client, project, session_factory, tmp_path)
    build = await client.post(f"/projects/{project['id']}/articles/build")
    assert build.status_code == 200, build.text
    assert build.json()["count"] >= 2

    delete_resp = await client.delete(f"/projects/{project['id']}/articles")
    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json()["deleted_count"] >= 2

    list_resp = await client.get(f"/projects/{project['id']}/articles")
    assert list_resp.status_code == 200, list_resp.text
    assert list_resp.json() == []


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
        await db.commit()

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
        await db.commit()

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


async def test_build_refreshes_aliases_and_graph_links(
    client, project, session_factory, tmp_path, mock_embeddings, mock_structure_agent
):
    docx = make_linked_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    assert up.status_code == 202, up.text
    source_id = uuid.UUID(up.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)
        await db.commit()

    proposal = await client.post(f"/projects/{project['id']}/structure/propose")
    assert proposal.status_code == 202, proposal.text
    proposal_id = uuid.UUID(proposal.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
        await db.commit()

    await confirm_all_candidates(client, project["id"], str(proposal_id))

    build = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert build.status_code == 200, build.text

    listing = await client.get(f"/projects/{project['id']}/articles")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) >= 2

    attention = next(row for row in rows if row["title"] == "Attention Is All You Need")
    models = next(row for row in rows if row["title"] == "Neural Sequence Transduction Models")

    attention_detail = await client.get(
        f"/projects/{project['id']}/articles/{attention['id']}"
    )
    assert attention_detail.status_code == 200, attention_detail.text
    attention_body = attention_detail.json()
    assert "Attention Is All You Need" in attention_body["aliases"]
    assert any(
        item["id"] == models["id"] for item in attention_body["referenced_by"]
    )
    assert any(
        item["id"] == models["id"] for item in attention_body["related_articles"]
    )

    async with session_factory() as db:
        aliases = (
            await db.execute(
                select(Alias)
                .where(Alias.article_id == uuid.UUID(attention["id"]))
                .order_by(Alias.text)
            )
        ).scalars().all()
        assert "Attention Is All You Need" in [alias.text for alias in aliases]

        hard_edges = (
            await db.execute(
                select(GraphEdge)
                .where(
                    GraphEdge.kind == EdgeKind.HARD,
                    GraphEdge.from_article_id == uuid.UUID(models["id"]),
                    GraphEdge.to_article_id == uuid.UUID(attention["id"]),
                )
            )
        ).scalars().all()
        assert hard_edges
        assert all(edge.source_block_id is not None for edge in hard_edges)

        soft_edges = (
            await db.execute(
                select(GraphEdge)
                .where(
                    GraphEdge.kind == EdgeKind.SOFT,
                    GraphEdge.from_article_id == uuid.UUID(attention["id"]),
                    GraphEdge.to_article_id == uuid.UUID(models["id"]),
                )
            )
        ).scalars().all()
        assert soft_edges
        assert all(edge.score is not None for edge in soft_edges)


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
        await db.commit()
    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = uuid.UUID(r.json()["proposal_id"])
    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
        await db.commit()
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

    async with session_factory() as db:
        refreshed = (
            await db.execute(
                select(ArticleCandidate)
                .where(ArticleCandidate.proposal_id == proposal_id)
                .options(
                    selectinload(ArticleCandidate.candidate_fragments).selectinload(
                        ArticleCandidateFragment.fragment
                    )
                )
            )
        ).scalars().all()
    expected_buildable = sum(
        1
        for candidate in refreshed
        if candidate.status.value == "confirmed"
        and any(
            is_meaningful_body_fragment(item.fragment)
            for item in candidate.candidate_fragments
        )
    )

    # Build: only confirmed candidates with meaningful body content become articles.
    r3 = await client.post(f"/projects/{project['id']}/articles/build")
    assert r3.status_code == 200
    assert r3.json()["count"] == expected_buildable


async def test_confirm_all_unknown_proposal_returns_404(client, project):
    random_id = "00000000-0000-0000-0000-000000000000"
    r = await client.post(
        f"/projects/{project['id']}/structure/proposals/{random_id}/confirm-all"
    )
    assert r.status_code == 404
