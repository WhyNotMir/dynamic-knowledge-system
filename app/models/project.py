from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.source import Source
    from app.models.article import Article
    from app.models.article_candidate import StructureProposal
    from app.models.structural_block import StructuralBlock
    from app.models.user import ProjectMember, User


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Short project-level summary used as domain context by agents.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-project tunables (clustering thresholds, node/article cutoffs,
    # Q&A "don't know" thresholds, etc.). Free-form JSON until schemas settle.
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Owner is nullable so system-created projects and deleted users are both
    # representable.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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

    owner: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[owner_id],
    )
    sources: Mapped[list["Source"]] = relationship(
        "Source",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    structure_proposals: Mapped[list["StructureProposal"]] = relationship(
        "StructureProposal",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    articles: Mapped[list["Article"]] = relationship(
        "Article",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    structural_blocks: Mapped[list["StructuralBlock"]] = relationship(
        "StructuralBlock",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    members: Mapped[list["ProjectMember"]] = relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
