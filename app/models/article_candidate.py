from __future__ import annotations
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Enum, JSON, ForeignKey, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from app.database import Base


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    REVIEWED = "reviewed"


class CandidateStatus(str, enum.Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    MERGED = "merged"


class StructureProposal(Base):
    __tablename__ = "structure_proposals"

    id:         Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    status      = mapped_column(Enum(ProposalStatus), default=ProposalStatus.PENDING, nullable=False)
    created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    candidates  = relationship("ArticleCandidate", back_populates="proposal", cascade="all, delete-orphan")


class ArticleCandidate(Base):
    __tablename__ = "article_candidates"

    id:                  Mapped[uuid.UUID]   = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id:          Mapped[uuid.UUID]   = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    proposal_id:         Mapped[uuid.UUID]   = mapped_column(ForeignKey("structure_proposals.id"), nullable=False, index=True)
    title:               Mapped[str]         = mapped_column(String(512), nullable=False)
    suggested_section:   Mapped[str | None]  = mapped_column(String(512), nullable=True)
    source_section_path: Mapped[str | None]  = mapped_column(String(2048), nullable=True)
    fragment_ids         = mapped_column(JSON, nullable=False)   # list[str] uuid strings
    status               = mapped_column(Enum(CandidateStatus), default=CandidateStatus.PROPOSED, nullable=False)
    confidence:          Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at:          Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:          Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    proposal = relationship("StructureProposal", back_populates="candidates")