from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.source import Source
    from app.models.article import Article
    from app.models.article_candidate import StructureProposal

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
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