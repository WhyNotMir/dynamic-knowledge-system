from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    StructureProposal,
)
from app.models.project import Project
from app.schemas.structure import (
    ArticleCandidateSchema,
    ConfirmAllResponse,
    ProposeStructureResponse,
    StructureProposalSchema,
    UpdateCandidateRequest,
)


def _candidate_first_position(cand: ArticleCandidate) -> float:
    """Earliest SourceFragment.position_index across a candidate's fragments.

    Used to order candidates the way they appear in the original document.
    Candidates whose fragments were all detached (SET NULL) fall to the end.
    """
    positions = [
        cf.fragment.position_index
        for cf in cand.candidate_fragments
        if cf.fragment is not None
    ]
    return min(positions) if positions else float("inf")


def _sort_candidates_by_document_order(proposal: StructureProposal) -> None:
    """Mutates proposal.candidates into document order (in place).

    Safe to call on a loaded ORM object because we're just reordering a
    Python list attribute, not issuing any DB writes.
    """
    proposal.candidates.sort(key=_candidate_first_position)

router = APIRouter(prefix="/projects/{project_id}/structure", tags=["structure"])


@router.post("/propose", response_model=ProposeStructureResponse, status_code=202)
async def propose_structure(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    proposal = StructureProposal(project_id=project_id)
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)

    await request.app.state.arq_pool.enqueue_job("propose_structure", str(proposal.id))
    return ProposeStructureResponse(
        proposal_id=proposal.id,
        message="Structure proposal queued",
    )


@router.get("/proposals", response_model=list[StructureProposalSchema])
async def list_proposals(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StructureProposal)
        .where(StructureProposal.project_id == project_id)
        .options(
            selectinload(StructureProposal.candidates)
            .selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
        .order_by(StructureProposal.created_at.desc())
    )
    proposals = list(result.scalars().all())
    for proposal in proposals:
        _sort_candidates_by_document_order(proposal)
    return proposals


@router.get("/proposals/{proposal_id}", response_model=StructureProposalSchema)
async def get_proposal(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StructureProposal)
        .where(
            StructureProposal.id == proposal_id,
            StructureProposal.project_id == project_id,
        )
        .options(
            selectinload(StructureProposal.candidates)
            .selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    _sort_candidates_by_document_order(proposal)
    return proposal


@router.post(
    "/proposals/{proposal_id}/confirm-all",
    response_model=ConfirmAllResponse,
)
async def confirm_all_candidates(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Bulk-confirm every still-proposed candidate in a proposal.

    Useful as a "trust the LLM" shortcut: flips every `proposed` candidate
    to `confirmed` in one call. `rejected` and already-`confirmed` are left
    as-is so this is safe to call repeatedly or after partial review.
    """
    proposal = await db.get(StructureProposal, proposal_id)
    if proposal is None or proposal.project_id != project_id:
        raise HTTPException(status_code=404, detail="Proposal not found")

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
    await db.commit()

    return ConfirmAllResponse(
        confirmed_count=result.rowcount or 0,
        total_count=total_count,
    )


@router.patch("/candidates/{candidate_id}", response_model=ArticleCandidateSchema)
async def update_candidate(
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: UpdateCandidateRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ArticleCandidate)
        .join(StructureProposal, StructureProposal.id == ArticleCandidate.proposal_id)
        .where(
            ArticleCandidate.id == candidate_id,
            StructureProposal.project_id == project_id,
        )
        .options(selectinload(ArticleCandidate.candidate_fragments))
    )
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if body.title is not None:
        candidate.title = body.title
    if body.suggested_section is not None:
        candidate.suggested_section = body.suggested_section
    if body.status is not None:
        candidate.status = body.status

    await db.commit()
    await db.refresh(candidate)
    return candidate