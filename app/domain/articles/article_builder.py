from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.article import Article, ArticleBlock, ArticleStatus
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalStatus,
    StructureProposal,
)


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

    for candidate in candidates:
        if candidate.status == CandidateStatus.REJECTED:
            continue

        article = Article(
            project_id=proposal.project_id,
            candidate_id=candidate.id,
            title=candidate.title,
            suggested_section=candidate.suggested_section,
            status=ArticleStatus.DRAFT,
        )
        db.add(article)
        await db.flush()

        candidate_fragments = sorted(
            candidate.candidate_fragments,
            key=lambda item: item.position_index,
        )

        blocks: list[ArticleBlock] = []
        for index, candidate_fragment in enumerate(candidate_fragments):
            fragment = candidate_fragment.fragment
            if fragment is None:
                continue

            blocks.append(
                ArticleBlock(
                    article_id=article.id,
                    fragment_id=fragment.id,
                    content=fragment.content,
                    element_type=fragment.element_type,
                    position_index=index,
                    page_number=fragment.page_number,
                    section_path=fragment.section_path,
                )
            )

        if not blocks:
            raise ValueError(
                f"Candidate {candidate.id} has no fragments and cannot be converted into an article."
            )

        db.add_all(blocks)
        await db.flush()
        article_ids.append(article.id)

    proposal.status = ProposalStatus.REVIEWED
    await db.flush()

    logger.info(
        f"Proposal {proposal.id}: built {len(article_ids)} articles "
        f"from {len(candidates)} candidates"
    )
    return article_ids