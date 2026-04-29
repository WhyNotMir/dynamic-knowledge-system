from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.article import Article
    from app.models.source_fragment import SourceFragment


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    REVIEWED = "reviewed"


class ProposalKind(str, enum.Enum):
    INITIAL = "initial"
    INCREMENTAL_UPDATE = "incremental_update"


class CandidateStatus(str, enum.Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    MERGED = "merged"


class StructureProposal(Base):
    __tablename__ = "structure_proposals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ProposalKind] = mapped_column(
        Enum(ProposalKind),
        default=ProposalKind.INITIAL,
        nullable=False,
    )
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus),
        default=ProposalStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="structure_proposals",
    )
    candidates: Mapped[list["ArticleCandidate"]] = relationship(
        "ArticleCandidate",
        back_populates="proposal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ArticleCandidate.created_at",
    )


class ArticleCandidate(Base):
    __tablename__ = "article_candidates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("structure_proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    suggested_section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_section_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus),
        default=CandidateStatus.PROPOSED,
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    proposal: Mapped["StructureProposal"] = relationship(
        "StructureProposal",
        back_populates="candidates",
    )
    candidate_fragments: Mapped[list["ArticleCandidateFragment"]] = relationship(
        "ArticleCandidateFragment",
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ArticleCandidateFragment.position_index",
    )
    articles: Mapped[list["Article"]] = relationship(
        "Article",
        back_populates="candidate",
    )


class ArticleCandidateFragment(Base):
    __tablename__ = "article_candidate_fragments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fragment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_fragments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)

    candidate: Mapped["ArticleCandidate"] = relationship(
        "ArticleCandidate",
        back_populates="candidate_fragments",
    )
    fragment: Mapped["SourceFragment"] = relationship(
        "SourceFragment",
        back_populates="candidate_fragments",
    )

    __table_args__ = (
        UniqueConstraint("candidate_id", "fragment_id", name="uq_candidate_fragment_pair"),
        UniqueConstraint("candidate_id", "position_index", name="uq_candidate_fragment_position"),
    )
