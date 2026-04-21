from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.article import ArticleBlock


class ConflictKind(str, enum.Enum):
    # Baseline set for Phase 8 ContradictionJudge. Refined as evals grow.
    CONTRADICTION = "contradiction"
    NUMERIC_MISMATCH = "numeric_mismatch"
    DATE_MISMATCH = "date_mismatch"
    DEFINITION_DRIFT = "definition_drift"
    OTHER = "other"


class ConflictStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED_A = "resolved_a"
    RESOLVED_B = "resolved_b"
    RESOLVED_EDIT = "resolved_edit"
    IGNORED = "ignored"


class Conflict(Base):
    """A pair of `ArticleBlock`s that ContradictionJudge (Phase 8) flagged
    as contradictory. Surfaced in the Review Inbox for user resolution."""

    __tablename__ = "conflicts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    block_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    block_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ConflictKind] = mapped_column(
        Enum(ConflictKind),
        default=ConflictKind.OTHER,
        nullable=False,
    )
    status: Mapped[ConflictStatus] = mapped_column(
        Enum(ConflictStatus),
        default=ConflictStatus.OPEN,
        nullable=False,
    )
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    block_a: Mapped["ArticleBlock"] = relationship(
        "ArticleBlock",
        foreign_keys=[block_a_id],
    )
    block_b: Mapped["ArticleBlock"] = relationship(
        "ArticleBlock",
        foreign_keys=[block_b_id],
    )

    __table_args__ = (
        UniqueConstraint("block_a_id", "block_b_id", name="uq_conflict_pair"),
    )
