"""Structure-proposal stage.

Covers:
  - POST /projects/{id}/structure/propose creates a PENDING proposal and
    enqueues the arq job.
  - Running the proposal inline (same code the worker runs) transitions
    PENDING -> READY and creates ArticleCandidate rows, one per section
    candidate produced by the center detector.
  - The candidates carry valid fragment_ids pointing at existing
    SourceFragment rows from the ingestion step.
  - The proposal can be fetched via GET /structure/proposals/{id}.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.chains.structure_agent import propose_structure
from app.domain.clustering.center_detector import (
    _pdf_structure_candidates,
)
from app.domain.ingestion.block_semantics import (
    ROLE_METADATA,
    VISIBILITY_ARTICLE,
    VISIBILITY_HIDDEN,
    with_semantic_meta,
)
from app.domain.articles.structure_service import (
    _clean_article_title,
    _unique_title,
    run_structure_proposal,
)
from app.domain.ingestion.ingestion_service import run_ingestion
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    ProposalStatus,
    StructureProposal,
)
from app.models.source_fragment import ElementType
from app.models.source_fragment import SourceFragment

from tests.helpers import make_docx, make_multitopic_docx, unique_docx


async def _upload_and_ingest(client, project, session_factory, tmp_path) -> uuid.UUID:
    """Upload a small DOCX and run ingestion inline. Returns the source_id."""
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
    return source_id


async def test_propose_endpoint_creates_pending_proposal_and_enqueues_job(
    client, project, arq_pool, session_factory, tmp_path
):
    await _upload_and_ingest(client, project, session_factory, tmp_path)

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    assert r.status_code == 202, r.text
    body = r.json()
    proposal_id = uuid.UUID(body["proposal_id"])

    # Job enqueued on the fake arq pool.
    assert any(
        name == "propose_structure" and args == (str(proposal_id),)
        for name, args in arq_pool.jobs
    )

    # DB row exists, in PENDING.
    async with session_factory() as db:
        p = await db.get(StructureProposal, proposal_id)
        assert p is not None
        assert p.status == ProposalStatus.PENDING
        assert str(p.project_id) == project["id"]


async def test_run_structure_proposal_marks_ready_and_creates_candidates(
    client, project, session_factory, tmp_path
):
    await _upload_and_ingest(client, project, session_factory, tmp_path)

    r = await client.post(f"/projects/{project['id']}/structure/propose")
    assert r.status_code == 202
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    # Drive the worker inline.
    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
        await db.commit()

    async with session_factory() as db:
        p = await db.get(StructureProposal, proposal_id)
        assert p is not None
        assert p.status == ProposalStatus.READY

        cands = (
            await db.execute(
                select(ArticleCandidate)
                .where(ArticleCandidate.proposal_id == proposal_id)
                .options(
                    selectinload(ArticleCandidate.proposal),
                    selectinload(ArticleCandidate.candidate_fragments),
                )
            )
        ).scalars().all()

    # Our helper DOCX has two H1 sections — center detector should produce
    # two section-structure candidates.
    assert len(cands) >= 2, f"expected >=2 candidates, got {len(cands)}"

    for c in cands:
        assert c.title, "every candidate must have a non-empty title"
        # Candidates don't carry project_id directly — it lives on the parent
        # proposal. Check it via the loaded relationship.
        assert c.proposal.project_id == uuid.UUID(project["id"])
        # Fragments are linked through ArticleCandidateFragment rows.
        assert len(c.candidate_fragments) > 0
        for link in c.candidate_fragments:
            assert isinstance(link.fragment_id, uuid.UUID)

    titles = {c.title for c in cands}
    # Our deterministic mock_structure_agent echoes the section path as title,
    # so Introduction / Methods should be present.
    assert "Introduction" in titles
    assert "Methods" in titles


async def test_candidate_fragment_ids_point_to_real_fragments(
    client, project, session_factory, tmp_path
):
    await _upload_and_ingest(client, project, session_factory, tmp_path)
    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = uuid.UUID(r.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)
        await db.commit()

    async with session_factory() as db:
        cands = (
            await db.execute(
                select(ArticleCandidate)
                .where(ArticleCandidate.proposal_id == proposal_id)
                .options(selectinload(ArticleCandidate.candidate_fragments))
            )
        ).scalars().all()

        all_frag_ids: list[uuid.UUID] = []
        for c in cands:
            all_frag_ids.extend(link.fragment_id for link in c.candidate_fragments)

        existing = (
            await db.execute(
                select(SourceFragment.id).where(
                    SourceFragment.id.in_(all_frag_ids)
                )
            )
        ).scalars().all()

    # Every fragment_id referenced by candidates must exist in source_fragments.
    assert set(all_frag_ids) == set(existing)


async def test_get_proposal_endpoint_returns_candidates(
    client, project, session_factory, tmp_path
):
    await _upload_and_ingest(client, project, session_factory, tmp_path)
    r = await client.post(f"/projects/{project['id']}/structure/propose")
    proposal_id = r.json()["proposal_id"]

    async with session_factory() as db:
        await run_structure_proposal(uuid.UUID(proposal_id), db)
        await db.commit()

    r2 = await client.get(
        f"/projects/{project['id']}/structure/proposals/{proposal_id}"
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["id"] == proposal_id
    assert body["status"] == "ready"
    assert len(body["candidates"]) >= 2


async def test_run_structure_proposal_missing_proposal_noops(session_factory):
    """A lost proposal must not raise — otherwise arq would retry forever."""
    random_id = uuid.uuid4()
    async with session_factory() as db:
        # Should simply log + return.
        await run_structure_proposal(random_id, db)
        await db.commit()


async def test_list_proposals_endpoint(
    client, project, session_factory, tmp_path
):
    await _upload_and_ingest(client, project, session_factory, tmp_path)
    r = await client.post(f"/projects/{project['id']}/structure/propose")
    assert r.status_code == 202
    proposal_id = r.json()["proposal_id"]

    r2 = await client.get(f"/projects/{project['id']}/structure/proposals")
    assert r2.status_code == 200
    rows = r2.json()
    assert any(row["id"] == proposal_id for row in rows)


async def test_proposal_exposes_internal_headings_for_multi_topic_candidate(
    client, project, session_factory, tmp_path
):
    docx = make_multitopic_docx(unique_docx(tmp_path))
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

    detail = await client.get(
        f"/projects/{project['id']}/structure/proposals/{proposal_id}"
    )
    assert detail.status_code == 200, detail.text
    proposal = detail.json()

    candidate = next(
        (
            c
            for c in proposal["candidates"]
            if c["internal_headings"] == ["Architecture", "Worker Layer", "Operations"]
        ),
        None,
    )
    assert candidate is not None


async def test_structure_agent_dedupes_duplicate_titles(monkeypatch):
    async def fake_title_llm(content: str, hint: str | None) -> str:
        return "Attention Is All You Need"

    monkeypatch.setattr(
        "app.agents.chains.structure_agent._propose_title_llm",
        fake_title_llm,
    )

    candidates = [
        {
            "source_section_path": "Attention Is All You Need",
            "fragments": [SimpleNamespace(content="doc title")],
        },
        {
            "source_section_path": "Attention Is All You Need > Abstract",
            "fragments": [SimpleNamespace(content="abstract")],
        },
    ]

    result = await propose_structure(candidates)

    assert result[0]["proposed_title"] == "Attention Is All You Need"
    assert result[1]["proposed_title"] == "Attention Is All You Need: Abstract"


async def test_structure_agent_dedupes_near_duplicate_titles(monkeypatch):
    async def fake_title_llm(content: str, hint: str | None) -> str:
        if hint == "1 Introduction":
            return "The Transformer Model Architecture"
        return "Transformer Model Architecture"

    monkeypatch.setattr(
        "app.agents.chains.structure_agent._propose_title_llm",
        fake_title_llm,
    )

    candidates = [
        {
            "source_section_path": "1 Introduction",
            "fragments": [SimpleNamespace(content="intro")],
        },
        {
            "source_section_path": "3 Model Architecture",
            "fragments": [SimpleNamespace(content="architecture")],
        },
    ]

    result = await propose_structure(candidates)

    assert result[0]["proposed_title"] == "The Transformer Model Architecture"
    assert result[1]["proposed_title"] == "Transformer Model Architecture: Model Architecture"


async def test_structure_agent_uses_llm_titles_for_pdf_candidates(monkeypatch):
    async def fake_title_llm(content: str, hint: str | None) -> str:
        if hint == "1 Introduction":
            return "Opening Argument"
        if hint == "3.2 Attention":
            return "Mechanism Details"
        return "Generated Title"

    monkeypatch.setattr(
        "app.agents.chains.structure_agent._propose_title_llm",
        fake_title_llm,
    )

    candidates = [
        {
            "source_type": "pdf",
            "source_section_path": "1 Introduction",
            "fragments": [SimpleNamespace(content="intro body")],
        },
        {
            "source_type": "pdf",
            "source_section_path": "3.2 Attention",
            "fragments": [SimpleNamespace(content="attention body")],
        },
    ]

    result = await propose_structure(candidates)

    assert [candidate["proposed_title"] for candidate in result] == [
        "Opening Argument",
        "Mechanism Details",
    ]
    assert [candidate["suggested_section"] for candidate in result] == [
        "1 Introduction",
        "3.2 Attention",
    ]


def test_pdf_structure_candidates_keep_nested_sections_and_drop_hidden_noise():
    def meta(visibility: str = VISIBILITY_ARTICLE):
        return with_semantic_meta(
            {},
            role=ROLE_METADATA if visibility == VISIBILITY_HIDDEN else "body",
            visibility=visibility,
            confidence=0.9,
            extraction_method="test",
        )

    def make_fragment(
        index: int,
        content: str,
        *,
        element_type: ElementType,
        heading_level: int | None = None,
        visibility: str = VISIBILITY_ARTICLE,
    ):
        return SimpleNamespace(
            content=content,
            element_type=element_type,
            heading_level=heading_level,
            position_index=index,
            page_number=1,
            meta_json=meta(visibility),
        )

    fragments = [
        make_fragment(
            0,
            "Provided proper attribution is provided",
            element_type=ElementType.PARAGRAPH,
            visibility=VISIBILITY_HIDDEN,
        ),
        make_fragment(1, "Attention Is All You Need", element_type=ElementType.HEADING, heading_level=1),
        make_fragment(2, "3 Model Architecture", element_type=ElementType.HEADING, heading_level=1),
        make_fragment(3, "Architecture body " * 40, element_type=ElementType.PARAGRAPH),
        make_fragment(4, "3.2 Attention", element_type=ElementType.HEADING, heading_level=2),
        make_fragment(5, "Attention body " * 40, element_type=ElementType.PARAGRAPH),
        make_fragment(6, "4 Why Self-Attention", element_type=ElementType.HEADING, heading_level=1),
        make_fragment(7, "Self attention body " * 40, element_type=ElementType.PARAGRAPH),
    ]

    candidates = _pdf_structure_candidates(fragments, source_title="Attention Is All You Need")

    assert [candidate["source_section_path"] for candidate in candidates] == [
        "Model Architecture",
        "Why Self-Attention",
    ]
    first_contents = [fragment.content for fragment in candidates[0]["fragments"]]
    assert "Provided proper attribution is provided" not in first_contents
    assert "3.2 Attention" in first_contents


def test_pdf_structure_candidates_keep_ieee_letter_headings_inside_roman_sections():
    def make_fragment(index: int, content: str, *, element_type: ElementType, heading_level: int | None = None):
        return SimpleNamespace(
            content=content,
            element_type=element_type,
            heading_level=heading_level,
            position_index=index,
            page_number=1,
            meta_json=with_semantic_meta(
                {},
                role="body",
                visibility=VISIBILITY_ARTICLE,
                confidence=0.9,
                extraction_method="test",
            ),
        )

    fragments = [
        make_fragment(0, "I. INTRODUCTION", element_type=ElementType.HEADING, heading_level=1),
        make_fragment(1, "Introduction body " * 20, element_type=ElementType.PARAGRAPH),
        make_fragment(2, "A. Historical Context", element_type=ElementType.HEADING, heading_level=2),
        make_fragment(3, "Historical body " * 20, element_type=ElementType.PARAGRAPH),
        make_fragment(4, "B. Impact", element_type=ElementType.HEADING, heading_level=2),
        make_fragment(5, "Impact body " * 20, element_type=ElementType.PARAGRAPH),
        make_fragment(6, "II. METHODS", element_type=ElementType.HEADING, heading_level=1),
        make_fragment(7, "Methods body " * 20, element_type=ElementType.PARAGRAPH),
        make_fragment(8, "A. Setup", element_type=ElementType.HEADING, heading_level=2),
        make_fragment(9, "Setup body " * 20, element_type=ElementType.PARAGRAPH),
    ]

    candidates = _pdf_structure_candidates(fragments, source_title=None)

    assert [candidate["source_section_path"] for candidate in candidates] == [
        "I. INTRODUCTION",
        "II. METHODS",
    ]
    assert [fragment.content for fragment in candidates[0]["fragments"]] == [
        "I. INTRODUCTION",
        "Introduction body " * 20,
        "A. Historical Context",
        "Historical body " * 20,
        "B. Impact",
        "Impact body " * 20,
    ]


def test_article_title_cleanup_strips_document_numbering_and_humanizes_acronyms():
    assert _clean_article_title("I. INTRODUCTION") == "Introduction"
    assert _clean_article_title("C. GPT SERIES: SCALING DECODER-ONLY MODELS") == (
        "GPT Series: Scaling Decoder-Only Models"
    )


def test_article_title_uniqueness_adds_source_context_for_duplicates():
    title = _unique_title(
        "Introduction",
        {"introduction"},
        {"source_section_path": "II. Background"},
        1,
    )

    assert title == "Introduction: Background"
