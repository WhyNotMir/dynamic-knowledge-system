from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.application.articles.proposal_service import (
    CandidateNotFoundError,
    ProjectNotFoundError,
    ProposalNotFoundError,
    confirm_all_candidates as confirm_all_candidates_service,
    create_structure_proposal,
    get_structure_proposal,
    list_structure_proposals,
    update_candidate as update_candidate_service,
)
from app.schemas.structure import (
    ArticleCandidateSchema,
    ConfirmAllResponse,
    ProposeStructureResponse,
    StructureProposalSchema,
    UpdateCandidateRequest,
)


router = APIRouter(prefix="/projects/{project_id}/structure", tags=["structure"])


@router.post("/propose", response_model=ProposeStructureResponse, status_code=202)
async def propose_structure(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        proposal = await create_structure_proposal(project_id, db)
        await db.commit()
        await db.refresh(proposal)
    except ProjectNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))

    await request.app.state.arq_pool.enqueue_job("propose_structure", str(proposal.id))
    return ProposeStructureResponse(
        proposal_id=proposal.id,
        message="Structure proposal queued",
    )


@router.get("/proposals", response_model=list[StructureProposalSchema])
async def list_proposals(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await list_structure_proposals(project_id, db)


@router.get("/proposals/{proposal_id}", response_model=StructureProposalSchema)
async def get_proposal(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_structure_proposal(project_id, proposal_id, db)
    except ProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/proposals/{proposal_id}/confirm-all",
    response_model=ConfirmAllResponse,
)
async def confirm_all_candidates(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        confirmed_count, total_count = await confirm_all_candidates_service(
            project_id,
            proposal_id,
            db,
        )
        await db.commit()
    except ProposalNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))

    return ConfirmAllResponse(
        confirmed_count=confirmed_count,
        total_count=total_count,
    )


@router.patch("/candidates/{candidate_id}", response_model=ArticleCandidateSchema)
async def update_candidate(
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: UpdateCandidateRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        candidate = await update_candidate_service(project_id, candidate_id, body, db)
        await db.commit()
        return candidate
    except CandidateNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
