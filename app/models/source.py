import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Enum, JSON, ForeignKey, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from app.database import Base


class SourceType(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"


class SourceStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Source(Base):
    __tablename__ = "sources"

    id:             Mapped[uuid.UUID]              = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id:     Mapped[uuid.UUID]              = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    filename:       Mapped[str]                    = mapped_column(String(512), nullable=False)
    source_type:    Mapped[SourceType]             = mapped_column(Enum(SourceType), nullable=False)
    storage_path:   Mapped[str]                    = mapped_column(String(1024), nullable=False)
    status:         Mapped[SourceStatus]           = mapped_column(Enum(SourceStatus), default=SourceStatus.PENDING, nullable=False)
    error_message:  Mapped[str | None]             = mapped_column(Text, nullable=True)
    doc_metadata:   Mapped[dict | None]            = mapped_column(JSON, nullable=True)
    created_at:     Mapped[datetime]               = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:     Mapped[datetime]               = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    project                                        = relationship("Project", back_populates="sources")
    fragments                                      = relationship("SourceFragment", back_populates="source", cascade="all, delete-orphan")