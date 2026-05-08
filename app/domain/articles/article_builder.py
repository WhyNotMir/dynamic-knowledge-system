from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.articles.article_text import candidate_description, is_meaningful_body_fragment
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

    project = await db.get(Project, proposal.project_id)
    settings = project.settings if project and project.settings else {}
    min_blocks = int(settings.get("min_blocks", 4))
    min_chars = int(settings.get("min_chars", 450))
    article_ids: list[uuid.UUID] = []
    slugs_in_pass: set[str] = set()

    for candidate in candidates:
        if candidate.status != CandidateStatus.CONFIRMED:
            continue
        if not candidate.candidate_fragments:
            logger.warning(f"Skipping candidate {candidate.id}: no linked fragments")
            continue

        prepared_blocks = prepare_article_blocks(candidate)
        if not has_meaningful_body(prepared_blocks):
            logger.warning(f"Skipping candidate {candidate.id}: no meaningful body")
            continue

        body_fragments = [
            fragment
            for fragment, _payload in prepared_blocks
            if is_meaningful_body_fragment(fragment)
        ]
        block_count = len(body_fragments)
        char_count = sum(len(fragment.content or "") for fragment in body_fragments)
        article_kind = (
            ArticleKind.NODE
            if block_count < min_blocks or char_count < min_chars
            else ArticleKind.ARTICLE
        )

        structural_block_id = await _structural_block_id(
            db,
            project_id=proposal.project_id,
            name=candidate.suggested_section,
        )
        base_slug = slugify(candidate.title)
        slug = await unique_slug_for_project(db, proposal.project_id, base_slug, slugs_in_pass)

        article = Article(
            project_id=proposal.project_id,
            candidate_id=candidate.id,
            title=candidate.title,
            slug=slug,
            description=candidate_description(candidate.candidate_fragments),
            suggested_section=candidate.suggested_section,
            kind=article_kind,
            status=ArticleStatus.DRAFT,
            structural_block_id=structural_block_id,
        )
        db.add(article)
        await db.flush()

        db.add_all([
            ArticleBlock(article_id=article.id, **payload)
            for _fragment, payload in prepared_blocks
        ])
        await db.flush()
        article_ids.append(article.id)

    proposal.status = ProposalStatus.REVIEWED
    await refresh_project_links(proposal.project_id, db)
    await _refresh_project_summary(db, project, proposal.project_id)
    await db.flush()

    logger.info(
        f"Proposal {proposal.id}: built {len(article_ids)} articles "
        f"from {len(candidates)} candidates"
    )
    return article_ids


async def _structural_block_id(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str | None,
) -> uuid.UUID | None:
    if not name:
        return None
    result = await db.execute(
        select(StructuralBlock.id).where(
            StructuralBlock.project_id == project_id,
            StructuralBlock.name == name,
        )
    )
    return result.scalar_one_or_none()


async def _refresh_project_summary(
    db: AsyncSession,
    project: Project | None,
    project_id: uuid.UUID,
) -> None:
    if project is None:
        return
    article_rows = (
        await db.execute(
            select(Article)
            .where(Article.project_id == project_id)
            .order_by(Article.created_at)
        )
    ).scalars().all()
    project.summary = (
        f"{project.name} knowledge base overview:\n"
        + "\n".join(f"- {article.title}" for article in article_rows[:8])
        if article_rows
        else None
    )
