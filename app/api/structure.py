from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import Project
from app.models.article_candidate import StructureProposal, ArticleCandidate
from app.schemas.structure import (
    ProposeStructureResponse, StructureProposalSchema,
    ArticleCandidateSchema, UpdateCandidateRequest,
)

router = APIRouter(prefix="/projects/{project_id}/structure", tags=["structure"])


@router.post("/propose", response_model=ProposeStructureResponse, status_code=202)
async def propose_structure(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Validate project existence before enqueueing — otherwise the FK on
    # structure_proposals.project_id would raise an IntegrityError that
    # surfaces as 500.
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")

    proposal = StructureProposal(project_id=project_id)
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    await request.app.state.arq_pool.enqueue_job("propose_structure", str(proposal.id))
    return ProposeStructureResponse(proposal_id=proposal.id, message="Structure proposal queued")


@router.get("/proposals", response_model=list[StructureProposalSchema])
async def list_proposals(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StructureProposal)
        .where(StructureProposal.project_id == project_id)
        .options(selectinload(StructureProposal.candidates))
        .order_by(StructureProposal.created_at.desc())
    )
    return result.scalars().all()


@router.get("/proposals/{proposal_id}", response_model=StructureProposalSchema)
async def get_proposal(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StructureProposal)
        .where(StructureProposal.id == proposal_id, StructureProposal.project_id == project_id)
        .options(selectinload(StructureProposal.candidates))
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(404, "Proposal not found")
    return proposal


@router.patch("/candidates/{candidate_id}", response_model=ArticleCandidateSchema)
async def update_candidate(
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: UpdateCandidateRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ArticleCandidate)
        .where(ArticleCandidate.id == candidate_id, ArticleCandidate.project_id == project_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    if body.title is not None:
        candidate.title = body.title
    if body.suggested_section is not None:
        candidate.suggested_section = body.suggested_section
    if body.status is not None:
        candidate.status = body.status

    await db.commit()
    await db.refresh(candidate)
    return candidate