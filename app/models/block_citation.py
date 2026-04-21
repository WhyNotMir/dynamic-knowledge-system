from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.article import ArticleBlock
    from app.models.source_fragment import SourceFragment


class CitationStatus(str, enum.Enum):
    # `unvalidated` is the default when QAAgent first emits a citation.
    # `validated` / `rejected` are written by CitationVerifier (Phase 6).
    # `partial` = the claim is only partially supported by the fragment.
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PARTIAL = "partial"


class BlockCitation(Base):
    """A single attestation that an `ArticleBlock` is supported by a
    `SourceFragment`. Emitted by QAAgent when it cites the block in an
    answer (Phase 4) and by SynthesisAgent for every fragment that fed a
    merged block (Phase 7). CitationVerifier (Phase 6) flips the status
    to `validated` / `rejected` / `partial` and records `validated_at`
    + `verifier_version`."""

    __tablename__ = "block_citations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fragment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_fragments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[CitationStatus] = mapped_column(
        Enum(CitationStatus),
        default=CitationStatus.UNVALIDATED,
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Free-text surrounding snippet the verifier used for its judgement.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    verifier_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    block: Mapped["ArticleBlock"] = relationship(
        "ArticleBlock",
        back_populates="citations",
    )
    fragment: Mapped["SourceFragment"] = relationship(
        "SourceFragment",
    )
