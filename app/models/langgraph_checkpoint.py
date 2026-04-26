from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LangGraphCheckpoint(Base):
    __tablename__ = "langgraph_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(64))
    checkpoint_payload: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_type: Mapped[str] = mapped_column(String(64))
    metadata_payload: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_langgraph_checkpoints_thread_ns_created",
            "thread_id",
            "checkpoint_ns",
            "created_at",
        ),
    )
