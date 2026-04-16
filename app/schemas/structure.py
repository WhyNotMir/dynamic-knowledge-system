from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, computed_field
from app.models.article_candidate import ProposalStatus, CandidateStatus


class ProposeStructureResponse(BaseModel):
    proposal_id: uuid.UUID
    message: str


class ArticleCandidateSchema(BaseModel):
    id: uuid.UUID
    title: str
    suggested_section: str | None
    source_section_path: str | None
    fragment_ids: list[str]
    status: CandidateStatus
    confidence: float | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def fragment_count(self) -> int:
        return len(self.fragment_ids)


class StructureProposalSchema(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: ProposalStatus
    candidates: list[ArticleCandidateSchema]
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateCandidateRequest(BaseModel):
    title: str | None = None
    suggested_section: str | None = None
    status: CandidateStatus | None = None