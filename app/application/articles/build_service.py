from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.articles.article_builder import build_articles_from_proposal
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    ProposalStatus,
    StructureProposal,
)


class ArticleBuildServiceError(RuntimeError):
    """Base error for article-build workflow failures."""


class ReadyProposalNotFoundError(ArticleBuildServiceError):
    """Raised when a project has no ready proposal to build."""


class ProposalNotFoundError(ArticleBuildServiceError):
    """Raised when the requested proposal is missing or belongs elsewhere."""


class ProposalNotReadyError(ArticleBuildServiceError):
    """Raised when the requested proposal is not ready to build."""


async def resolve_latest_ready_proposal_id(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> uuid.UUID:
    result = await db.execute(
        select(StructureProposal.id)
        .where(
            StructureProposal.project_id == project_id,
            StructureProposal.status == ProposalStatus.READY,
        )
        .order_by(StructureProposal.created_at.desc())
        .limit(1)
    )
    proposal_id = result.scalar_one_or_none()
    if proposal_id is None:
        raise ReadyProposalNotFoundError(
            "No READY structure proposal found for this project. "
            "Call POST /projects/{project_id}/structure/propose first "
            "and wait for the proposal to reach status=READY."
        )
    return proposal_id


async def get_ready_proposal_for_project(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession,
) -> StructureProposal:
    result = await db.execute(
        select(StructureProposal)
        .where(StructureProposal.id == proposal_id)
        .options(
            selectinload(StructureProposal.candidates)
            .selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
    )
    proposal = result.scalar_one_or_none()

    if proposal is None or proposal.project_id != project_id:
        raise ProposalNotFoundError(
            f"Proposal {proposal_id} not found"
            if proposal is None
            else f"Proposal {proposal_id} does not belong to project {project_id}"
        )
    if proposal.status != ProposalStatus.READY:
        raise ProposalNotReadyError(
            f"Proposal {proposal_id} has status '{proposal.status.value}', "
            "expected 'ready'."
        )
    return proposal


async def build_articles_for_project(
    project_id: uuid.UUID,
    db: AsyncSession,
    *,
    proposal_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    target_proposal_id = proposal_id or await resolve_latest_ready_proposal_id(
        project_id,
        db,
    )
    proposal = await get_ready_proposal_for_project(
        project_id,
        target_proposal_id,
        db,
    )
    return await build_articles_from_proposal(proposal=proposal, db=db)
