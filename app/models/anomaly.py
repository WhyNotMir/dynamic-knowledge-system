from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnomalyKind(str, enum.Enum):
    OFF_TOPIC_ARTICLE = "off_topic_article"
    OUTLIER_BLOCK = "outlier_block"
    OFF_DOMAIN_SOURCE = "off_domain_source"
    OTHER = "other"


class AnomalyStatus(str, enum.Enum):
    OPEN = "open"
    IGNORED = "ignored"
    REMOVED = "removed"
    MARKED_VALID = "marked_valid"


class AnomalyTarget(str, enum.Enum):
    ARTICLE = "article"
    BLOCK = "block"
    SOURCE = "source"


class Anomaly(Base):
    """An article, block, or source flagged as semantically anomalous."""

    __tablename__ = "anomalies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Plain UUID because the target can live in three different tables.
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    target_kind: Mapped[AnomalyTarget] = mapped_column(
        Enum(AnomalyTarget),
        nullable=False,
    )
    kind: Mapped[AnomalyKind] = mapped_column(
        Enum(AnomalyKind),
        default=AnomalyKind.OTHER,
        nullable=False,
    )
    status: Mapped[AnomalyStatus] = mapped_column(
        Enum(AnomalyStatus),
        default=AnomalyStatus.OPEN,
        nullable=False,
    )
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Detector-specific payload (e.g. neighbour ids, cluster stats).
    meta_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
