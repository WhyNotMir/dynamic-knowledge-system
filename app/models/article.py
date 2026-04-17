from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.source_fragment import ElementType

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.article_candidate import ArticleCandidate
    from app.models.source_fragment import SourceFragment

class ArticleStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    suggested_section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(ArticleStatus),
        default=ArticleStatus.DRAFT,
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
        back_populates="articles",
    )
    candidate: Mapped["ArticleCandidate | None"] = relationship(
        "ArticleCandidate",
        back_populates="articles",
    )
    blocks: Mapped[list["ArticleBlock"]] = relationship(
        "ArticleBlock",
        back_populates="article",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ArticleBlock.position_index",
    )


class ArticleBlock(Base):
    __tablename__ = "article_blocks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fragment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_fragments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    element_type: Mapped[ElementType] = mapped_column(Enum(ElementType), nullable=False)
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    article: Mapped["Article"] = relationship(
        "Article",
        back_populates="blocks",
    )
    fragment: Mapped["SourceFragment | None"] = relationship(
        "SourceFragment",
        back_populates="article_blocks",
    )

    __table_args__ = (
        UniqueConstraint("article_id", "position_index", name="uq_article_block_position"),
    )