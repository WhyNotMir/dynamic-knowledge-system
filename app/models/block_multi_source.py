from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.article import ArticleBlock
    from app.models.source_fragment import SourceFragment


class BlockMultiSource(Base):
    """Many-to-many link between `ArticleBlock` and `SourceFragment`."""

    __tablename__ = "block_multi_sources"

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    block: Mapped["ArticleBlock"] = relationship(
        "ArticleBlock",
        back_populates="multi_source_links",
    )
    fragment: Mapped["SourceFragment"] = relationship(
        "SourceFragment",
        back_populates="multi_source_links",
    )

    __table_args__ = (
        UniqueConstraint("block_id", "fragment_id", name="uq_block_multi_source_pair"),
    )
