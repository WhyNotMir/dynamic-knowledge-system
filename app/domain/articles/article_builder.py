from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.articles.article_text import (
    candidate_description,
    internal_headings,
    is_meaningful_body_fragment,
)
from app.domain.articles.block_builder import has_meaningful_body, prepare_article_blocks
from app.domain.articles.metadata_service import enrich_or_fallback_candidate_metadata
from app.domain.articles.slug_service import slugify, unique_slug_for_project
from app.domain.linking.service import refresh_project_links
from app.models.article import Article, ArticleBlock, ArticleKind, ArticleStatus
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalStatus,
    StructureProposal,
)
from app.models.project import Project
from app.models.structural_block import StructuralBlock


async def build_articles_from_proposal(
    proposal: StructureProposal,
    db: AsyncSession,
) -> list[uuid.UUID]:
    if proposal.status != ProposalStatus.READY:
        raise ValueError(
            f"Proposal {proposal.id} has status '{proposal.status.value}', expected 'ready'"
        )

    result = await db.execute(
        select(ArticleCandidate)
        .where(ArticleCandidate.proposal_id == proposal.id)
        .options(
            selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
        .order_by(ArticleCandidate.created_at)
    )
    candidates = list(result.scalars().all())

    if not candidates:
        logger.warning(f"Proposal {proposal.id} has no candidates; no articles will be built")
        proposal.status = ProposalStatus.REVIEWED
        await db.flush()
        return []

    article_ids: list[uuid.UUID] = []
    slugs_in_pass: set[str] = set()
    project = await db.get(Project, proposal.project_id)
    settings = project.settings if project and project.settings else {}
    min_blocks = int(settings.get("min_blocks", 4))
    min_chars = int(settings.get("min_chars", 450))
    available_structural_blocks = (
        (
            await db.execute(
                select(StructuralBlock.name).where(
                    StructuralBlock.project_id == proposal.project_id
                )
            )
        ).scalars().all()
        if project is not None
        else []
    )

    for candidate in candidates:
        # Only build articles for candidates the user has explicitly
        # confirmed. Candidates still in `proposed` status are treated as
        # "not yet reviewed" and skipped — this is what makes the
        # accept / reject workflow in the Review UI actually mean something.
        # (Use POST /structure/proposals/{id}/confirm-all to bulk-confirm
        # everything at once if review isn't needed.)
        if candidate.status != CandidateStatus.CONFIRMED:
            continue
        if not candidate.candidate_fragments:
            raise ValueError(f"Candidate {candidate.id} has no linked fragments")

        base_slug = slugify(candidate.title)
        slug = await unique_slug_for_project(
            db, proposal.project_id, base_slug, slugs_in_pass
        )

        body_fragments = [
            item.fragment
            for item in candidate.candidate_fragments
            if is_meaningful_body_fragment(item.fragment)
        ]
        if not body_fragments:
            logger.warning(
                f"Skipping candidate {candidate.id}: no meaningful body fragments after noise filtering"
            )
            continue

        block_count = len(body_fragments)
        char_count = sum(len(fragment.content) for fragment in body_fragments)
        article_kind = (
            ArticleKind.NODE
            if block_count < min_blocks or char_count < min_chars
            else ArticleKind.ARTICLE
        )

        structural_block_id = None
        candidate_payload = {
            "proposed_title": candidate.title,
            "suggested_section": candidate.suggested_section,
            "source_section_path": candidate.source_section_path,
            "internal_headings": internal_headings(candidate.candidate_fragments),
            "fragments": [
                item.fragment for item in candidate.candidate_fragments if item.fragment is not None
            ],
        }
        metadata = await enrich_or_fallback_candidate_metadata(
            project_name=project.name if project is not None else "Knowledge Base",
            kb_summary=project.summary if project is not None else None,
            candidate=candidate,
            candidate_payload=candidate_payload,
            available_structural_blocks=list(available_structural_blocks),
        )

        if metadata.suggested_structural_block or candidate.suggested_section:
            result = await db.execute(
                select(StructuralBlock.id).where(
                    StructuralBlock.project_id == proposal.project_id,
                    StructuralBlock.name
                    == (metadata.suggested_structural_block or candidate.suggested_section),
                )
            )
            structural_block_id = result.scalar_one_or_none()

        prepared_blocks = prepare_article_blocks(candidate)

        if not has_meaningful_body(prepared_blocks):
            logger.warning(
                f"Skipping candidate {candidate.id}: article body collapses to headings/captions only"
            )
            continue

        article = Article(
            project_id=proposal.project_id,
            candidate_id=candidate.id,
            title=metadata.title,
            slug=slug,
            description=metadata.description or candidate_description(candidate.candidate_fragments),
            suggested_section=candidate.suggested_section,
            kind=article_kind,
            status=ArticleStatus.DRAFT,
            structural_block_id=structural_block_id,
        )
        db.add(article)
        await db.flush()

        blocks = [
            ArticleBlock(
                article_id=article.id,
                **payload,
            )
            for _, payload in prepared_blocks
        ]

        db.add_all(blocks)
        await db.flush()
        article_ids.append(article.id)

    proposal.status = ProposalStatus.REVIEWED
    await refresh_project_links(proposal.project_id, db)
    if project is not None:
        article_rows = (
            await db.execute(
                select(Article)
                .where(Article.project_id == proposal.project_id)
                .order_by(Article.created_at)
            )
        ).scalars().all()
        project.summary = (
            f"{project.name} knowledge base overview:\n"
            + "\n".join(f"- {article.title}" for article in article_rows[:8])
            if article_rows
            else None
        )
    await db.flush()

    logger.info(
        f"Proposal {proposal.id}: built {len(article_ids)} articles "
        f"from {len(candidates)} candidates"
    )
    return article_ids
