from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.source_fragment import SourceFragment


class SourceType(str, enum.Enum):
    # Only `pdf` and `docx` are active in the pipeline for now; `url`, `text`,
    # `markdown` are reserved so the Phase 12 URL-ingestion path and the Phase
    # 14 connector imports don't need another enum migration.
    PDF = "pdf"
    DOCX = "docx"
    URL = "url"
    TEXT = "text"
    MARKDOWN = "markdown"


class SourceStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # First-H1 title captured by the extractor; used as Project.name hint and
    # passed as domain context when TitleAgent composes article titles.
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus),
        default=SourceStatus.PENDING,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
        back_populates="sources",
    )
    fragments: Mapped[list["SourceFragment"]] = relationship(
        "SourceFragment",
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SourceFragment.position_index",
    )
