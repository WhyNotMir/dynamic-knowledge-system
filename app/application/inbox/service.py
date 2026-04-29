from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.articles.proposal_service import list_structure_proposals
from app.schemas.inbox import InboxActionSchema, InboxGateSchema, InboxItemSchema


def _candidate_review_gate() -> InboxGateSchema:
    return InboxGateSchema(
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
    )


async def list_inbox_items(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> list[InboxItemSchema]:
    proposals = await list_structure_proposals(project_id, db)

    items: list[InboxItemSchema] = []
    for proposal in proposals:
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
                gate=_candidate_review_gate(),
                proposal=proposal,
            )
        )

    return items
