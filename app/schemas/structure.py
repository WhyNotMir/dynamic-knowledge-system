from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field

from app.models.article_candidate import CandidateStatus, ProposalKind, ProposalStatus


def _looks_like_real_heading(text: str) -> bool:
    stripped = " ".join(text.split()).strip()
    if not stripped:
        return False
    if len(stripped) > 90:
        return False
    alpha = sum(char.isalpha() for char in stripped)
    digits = sum(char.isdigit() for char in stripped)
    if alpha < 3:
        return False
    if digits > alpha:
        return False
    bad_markers = ["<eos>", "arxiv:", "[cs.", "wsj", "gnmt", "en-de", "en-fr"]
    lowered = stripped.casefold()
    if any(marker in lowered for marker in bad_markers):
        return False
    return True


class ProposeStructureResponse(BaseModel):
    proposal_id: uuid.UUID
    message: str


class ArticleCandidateSchema(BaseModel):
    id: uuid.UUID
    title: str
    suggested_section: str | None
    source_section_path: str | None
    status: CandidateStatus
    confidence: float | None
    created_at: datetime

    # Source attribute from the ORM — declared so Pydantic v2 actually
    # reads it from the ArticleCandidate object under `from_attributes=True`
    # (undeclared attributes are silently dropped during validation).
    # Excluded from the JSON payload; the computed fields below derive the
    # fragment count / ids that the UI and API consumers actually need.
    candidate_fragments: list[Any] = Field(default_factory=list, exclude=True)

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def fragment_ids(self) -> list[uuid.UUID]:
        sorted_items = sorted(
            self.candidate_fragments, key=lambda item: item.position_index
        )
        return [item.fragment_id for item in sorted_items]

    @computed_field
    @property
    def fragment_count(self) -> int:
        return len(self.candidate_fragments)

    @computed_field
    @property
    def internal_headings(self) -> list[str]:
        headings: list[tuple[int, str]] = []
        seen: set[str] = set()

        sorted_items = sorted(
            self.candidate_fragments, key=lambda item: item.position_index
        )
        for item in sorted_items:
            fragment = getattr(item, "fragment", None)
            if fragment is None:
                continue
            if fragment.element_type.value != "heading":
                continue
            if not fragment.heading_level or fragment.heading_level < 2:
                continue

            title = fragment.content.strip()
            if not title:
                continue
            if not _looks_like_real_heading(title):
                continue
            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)
            headings.append((fragment.heading_level, title))

        return [title for _, title in headings[:12]]


class StructureProposalSchema(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: ProposalKind = ProposalKind.INITIAL
    status: ProposalStatus
    candidates: list[ArticleCandidateSchema]
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateCandidateRequest(BaseModel):
    title: str | None = None
    suggested_section: str | None = None
    status: CandidateStatus | None = None


class ConfirmAllResponse(BaseModel):
    confirmed_count: int
    total_count: int
