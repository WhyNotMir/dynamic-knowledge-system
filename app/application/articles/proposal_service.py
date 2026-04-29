from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    StructureProposal,
)
from app.models.project import Project
from app.schemas.structure import UpdateCandidateRequest


class ProposalServiceError(RuntimeError):
    """Base error for proposal review operations."""


class ProjectNotFoundError(ProposalServiceError):
    """Raised when a project does not exist."""


class ProposalNotFoundError(ProposalServiceError):
    """Raised when a proposal does not exist in the requested project."""


class CandidateNotFoundError(ProposalServiceError):
    """Raised when a candidate does not exist in the requested project."""


def candidate_first_position(candidate: ArticleCandidate) -> float:
    positions = [
        item.fragment.position_index
        for item in candidate.candidate_fragments
        if item.fragment is not None
    ]
    return min(positions) if positions else float("inf")


def sort_candidates_by_document_order(proposal: StructureProposal) -> None:
    proposal.candidates.sort(key=candidate_first_position)


def _proposal_load_options():
    return (
        selectinload(StructureProposal.candidates)
        .selectinload(ArticleCandidate.candidate_fragments)
        .selectinload(ArticleCandidateFragment.fragment)
    )


async def create_structure_proposal(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> StructureProposal:
    if await db.get(Project, project_id) is None:
        raise ProjectNotFoundError(f"Project {project_id} not found")

    proposal = StructureProposal(project_id=project_id)
    db.add(proposal)
    await db.flush()
    return proposal


async def list_structure_proposals(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> list[StructureProposal]:
    result = await db.execute(
        select(StructureProposal)
        .where(StructureProposal.project_id == project_id)
        .options(_proposal_load_options())
        .order_by(StructureProposal.created_at.desc())
    )
    proposals = list(result.scalars().all())
    for proposal in proposals:
        sort_candidates_by_document_order(proposal)
    return proposals


async def get_structure_proposal(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession,
) -> StructureProposal:
    result = await db.execute(
        select(StructureProposal)
        .where(
            StructureProposal.id == proposal_id,
            StructureProposal.project_id == project_id,
        )
        .options(_proposal_load_options())
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise ProposalNotFoundError("Proposal not found")

    sort_candidates_by_document_order(proposal)
    return proposal


async def confirm_all_candidates(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[int, int]:
    proposal = await db.get(StructureProposal, proposal_id)
    if proposal is None or proposal.project_id != project_id:
        raise ProposalNotFoundError("Proposal not found")

    total_count = (
        await db.execute(
            select(func.count())
            .select_from(ArticleCandidate)
            .where(ArticleCandidate.proposal_id == proposal_id)
        )
    ).scalar_one()

    result = await db.execute(
        update(ArticleCandidate)
        .where(
            ArticleCandidate.proposal_id == proposal_id,
            ArticleCandidate.status == CandidateStatus.PROPOSED,
        )
        .values(status=CandidateStatus.CONFIRMED)
    )
    await db.flush()
    return result.rowcount or 0, total_count


async def update_candidate(
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: UpdateCandidateRequest,
    db: AsyncSession,
) -> ArticleCandidate:
    result = await db.execute(
        select(ArticleCandidate)
        .join(StructureProposal, StructureProposal.id == ArticleCandidate.proposal_id)
        .where(
            ArticleCandidate.id == candidate_id,
            StructureProposal.project_id == project_id,
        )
        .options(
            selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
    )
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise CandidateNotFoundError("Candidate not found")

    if body.title is not None:
        candidate.title = body.title
    if body.suggested_section is not None:
        candidate.suggested_section = body.suggested_section
    if body.status is not None:
        candidate.status = body.status

    await db.flush()
    await db.refresh(candidate)
    return candidate
