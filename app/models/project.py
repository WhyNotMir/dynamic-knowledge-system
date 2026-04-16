from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id:           Mapped[uuid.UUID]      = mapped_column(primary_key=True, default=uuid.uuid4)
    name:         Mapped[str]            = mapped_column(String(255), nullable=False)
    description:  Mapped[str | None]     = mapped_column(Text, nullable=True)
    scope_hint:   Mapped[str | None]     = mapped_column(Text, nullable=True)
    created_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    sources                              = relationship("Source", back_populates="project", cascade="all, delete-orphan")