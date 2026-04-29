from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.article import Article


class AliasSource(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"
    MERGED = "merged"


class Alias(Base):
    """Synonym / abbreviation / alternative spelling for an Article, used
    by the linker to discover cross-references."""

    __tablename__ = "aliases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[AliasSource] = mapped_column(
        Enum(AliasSource),
        default=AliasSource.AUTO,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    article: Mapped["Article"] = relationship(
        "Article",
        back_populates="alias_records",
    )

    __table_args__ = (
        UniqueConstraint("article_id", "text", name="uq_alias_article_text"),
    )
