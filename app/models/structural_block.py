from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.article import Article


class StructuralBlock(Base):
    """Hierarchical taxonomy node (tree of arbitrary depth) used to group
    articles inside a project. An article may reference any node in the tree,
    not only leaves."""

    __tablename__ = "structural_blocks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("structural_blocks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Position among siblings under the same `parent_id`.
    position_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
        back_populates="structural_blocks",
    )
    parent: Mapped["StructuralBlock | None"] = relationship(
        "StructuralBlock",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list["StructuralBlock"]] = relationship(
        "StructuralBlock",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="StructuralBlock.position_index",
    )
    articles: Mapped[list["Article"]] = relationship(
        "Article",
        back_populates="structural_block",
    )
