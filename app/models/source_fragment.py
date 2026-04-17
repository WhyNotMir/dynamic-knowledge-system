from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.source import Source
    from app.models.article_candidate import ArticleCandidateFragment
    from app.models.article import ArticleBlock

class ElementType(str, enum.Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"


class SourceFragment(Base):
    __tablename__ = "source_fragments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    element_type: Mapped[ElementType] = mapped_column(Enum(ElementType), nullable=False)
    heading_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="fragments",
    )
    candidate_fragments: Mapped[list["ArticleCandidateFragment"]] = relationship(
        "ArticleCandidateFragment",
        back_populates="fragment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    article_blocks: Mapped[list["ArticleBlock"]] = relationship(
        "ArticleBlock",
        back_populates="fragment",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("source_id", "position_index", name="uq_source_fragments_source_position"),
        Index(
            "ix_source_fragments_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )