from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.article import Article, ArticleBlock


class RevisionReason(str, enum.Enum):
    # Snapshots are created by the Executor / agents and by manual edits.
    # Used in Phase 9 (semantic versioning) for diff UI + rollback.
    INGEST = "ingest"
    SYNTHESIS = "synthesis"
    RESTRUCTURE = "restructure"
    MERGE = "merge"
    MANUAL = "manual"
    ROUTING = "routing"
    FEEDBACK = "feedback"
    OTHER = "other"


class BlockRevision(Base):
    """Immutable snapshot of an `ArticleBlock` prior to a change. The
    current content stays on the block; this table preserves history."""

    __tablename__ = "block_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Snapshot of the block as it was before this revision.
    previous_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[RevisionReason] = mapped_column(
        Enum(RevisionReason),
        default=RevisionReason.OTHER,
        nullable=False,
    )
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # `actor` is either an agent name (e.g. "SynthesisAgent") or a
    # stringified user_id. Free-form until Phase 5 wires real auth.
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    block: Mapped["ArticleBlock"] = relationship(
        "ArticleBlock",
        back_populates="revisions",
    )


class ArticleRevision(Base):
    """Immutable snapshot of an `Article` (title, description, summary,
    structural_block_id, status, slug) prior to a change. Block-level
    content changes live on `BlockRevision`; this captures the meta."""

    __tablename__ = "article_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[RevisionReason] = mapped_column(
        Enum(RevisionReason),
        default=RevisionReason.OTHER,
        nullable=False,
    )
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    article: Mapped["Article"] = relationship(
        "Article",
        back_populates="revisions",
    )
