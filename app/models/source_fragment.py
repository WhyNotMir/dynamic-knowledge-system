from __future__ import annotations
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, Enum, ForeignKey, Index, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from pgvector.sqlalchemy import Vector
from app.database import Base


class ElementType(str, enum.Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"


class SourceFragment(Base):
    __tablename__ = "source_fragments"

    id:             Mapped[uuid.UUID]  = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id:      Mapped[uuid.UUID]  = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    project_id:     Mapped[uuid.UUID]  = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    content:        Mapped[str]        = mapped_column(Text, nullable=False)
    element_type:   Mapped[ElementType]= mapped_column(Enum(ElementType), nullable=False)
    heading_level:  Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number:    Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path:   Mapped[str | None] = mapped_column(String(2048), nullable=True)
    position_index: Mapped[int]        = mapped_column(Integer, nullable=False)
    embedding                          = mapped_column(Vector(1536), nullable=True)
    created_at:     Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())

    source = relationship("Source", back_populates="fragments")

    __table_args__ = (
        Index(
            "ix_source_fragments_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )