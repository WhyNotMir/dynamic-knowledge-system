from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.structure import _sort_candidates_by_document_order
from app.database import get_db
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    StructureProposal,
)
from app.schemas.inbox import InboxActionSchema, InboxGateSchema, InboxItemSchema


router = APIRouter(prefix="/projects/{project_id}/inbox", tags=["inbox"])


@router.get("", response_model=list[InboxItemSchema])
async def list_inbox_items(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
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

    items: list[InboxItemSchema] = []
    for proposal in proposals:
        _sort_candidates_by_document_order(proposal)
        candidate_count = len(proposal.candidates)
        items.append(
            InboxItemSchema(
                id=f"new-candidates:{proposal.id}",
                item_type="new_candidates",
                project_id=proposal.project_id,
                proposal_id=proposal.id,
                status=proposal.status,
                title=f"{candidate_count} new candidate{'s' if candidate_count != 1 else ''}",
                created_at=proposal.created_at,
                gate=InboxGateSchema(
                    requires_human_review=True,
                    auto_apply_allowed=False,
                    reason=(
                        "New candidate proposals are always reviewed by a human "
                        "before articles are built."
                    ),
                    available_actions=[
                        InboxActionSchema(action="confirm", label="Confirm"),
                        InboxActionSchema(action="reject", label="Reject"),
                        InboxActionSchema(action="rename", label="Rename"),
                        InboxActionSchema(action="view_source", label="View source"),
                        InboxActionSchema(action="confirm_all", label="Accept all"),
                        InboxActionSchema(action="build_articles", label="Build articles"),
                    ],
                    reserved_actions=[
                        InboxActionSchema(
                            action="merge",
                            label="Merge",
                            destructive=True,
                            enabled=False,
                            available_now=False,
                        ),
                        InboxActionSchema(
                            action="restructure",
                            label="Restructure",
                            destructive=True,
                            enabled=False,
                            available_now=False,
                        ),
                        InboxActionSchema(
                            action="promote_node",
                            label="Promote node",
                            destructive=True,
                            enabled=False,
                            available_now=False,
                        ),
                        InboxActionSchema(
                            action="mass_reroute",
                            label="Mass reroute",
                            destructive=True,
                            enabled=False,
                            available_now=False,
                        ),
                    ],
                ),
                proposal=proposal,
            )
        )

    return items
