from __future__ import annotations
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from app.database import Base


class ArticleStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class Article(Base):
    __tablename__ = "articles"

    id:                Mapped[uuid.UUID]   = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id:        Mapped[uuid.UUID]   = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    candidate_id:      Mapped[uuid.UUID | None] = mapped_column(ForeignKey("article_candidates.id"), nullable=True)
    title:             Mapped[str]         = mapped_column(String(512), nullable=False)
    suggested_section: Mapped[str | None]  = mapped_column(String(512), nullable=True)
    status:            Mapped[ArticleStatus] = mapped_column(Enum(ArticleStatus), default=ArticleStatus.DRAFT, nullable=False)
    created_at:        Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:        Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    blocks = relationship("ArticleBlock", back_populates="article",
                          cascade="all, delete-orphan", order_by="ArticleBlock.position_index")


class ArticleBlock(Base):
    __tablename__ = "article_blocks"

    id:             Mapped[uuid.UUID]   = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id:     Mapped[uuid.UUID]   = mapped_column(ForeignKey("articles.id"), nullable=False, index=True)
    fragment_id:    Mapped[uuid.UUID]   = mapped_column(ForeignKey("source_fragments.id"), nullable=False)
    content:        Mapped[str]         = mapped_column(Text, nullable=False)
    element_type:   Mapped[str]         = mapped_column(String(64), nullable=False)
    position_index: Mapped[int]         = mapped_column(Integer, nullable=False)
    page_number:    Mapped[int | None]  = mapped_column(Integer, nullable=True)
    section_path:   Mapped[str | None]  = mapped_column(String(2048), nullable=True)
    created_at:     Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    article = relationship("Article", back_populates="blocks")