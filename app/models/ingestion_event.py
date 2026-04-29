from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IngestionEventLevel(str, enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IngestionEvent(Base):
    """Audit log for graph nodes, agents, and background workflow events."""

    __tablename__ = "ingestion_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Correlation id shared by all events within the same pipeline run.
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    # Agent / node name (e.g. "structure_agent", "ingest.extract",
    # "RoutingAgent"). Free-form on purpose.
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    level: Mapped[IngestionEventLevel] = mapped_column(
        Enum(IngestionEventLevel),
        default=IngestionEventLevel.INFO,
        nullable=False,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Cost accounting — populated when the event wraps an LLM call.
    llm_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
