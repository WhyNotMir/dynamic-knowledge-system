from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.article_candidate import ProposalStatus
from app.schemas.structure import StructureProposalSchema


InboxItemType = Literal["new_candidates"]
InboxActionType = Literal[
    "confirm",
    "reject",
    "rename",
    "view_source",
    "confirm_all",
    "build_articles",
    "merge",
    "restructure",
    "promote_node",
    "mass_reroute",
]


class InboxActionSchema(BaseModel):
    action: InboxActionType
    label: str
    destructive: bool = False
    enabled: bool = True
    requires_human_review: bool = True
    available_now: bool = True


class InboxGateSchema(BaseModel):
    requires_human_review: bool = True
    auto_apply_allowed: bool = False
    reason: str
    available_actions: list[InboxActionSchema]
    reserved_actions: list[InboxActionSchema] = []


class InboxItemSchema(BaseModel):
    id: str
    item_type: InboxItemType
    project_id: uuid.UUID
    proposal_id: uuid.UUID
    status: ProposalStatus
    title: str
    created_at: datetime
    gate: InboxGateSchema
    proposal: StructureProposalSchema


__all__ = [
    "InboxActionSchema",
    "InboxActionType",
    "InboxGateSchema",
    "InboxItemSchema",
    "InboxItemType",
]
