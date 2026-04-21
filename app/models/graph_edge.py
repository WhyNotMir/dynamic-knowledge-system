from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.article import Article, ArticleBlock


class EdgeKind(str, enum.Enum):
    # `hard` = explicit inline reference found by the Phase 3 Linker
    # (alias match inside ArticleBlock content). `soft` = top-K cosine
    # neighbour at article level, refreshed by MaintenancePipeline.
    HARD = "hard"
    SOFT = "soft"


class GraphEdge(Base):
    """Directed edge between two Articles. Written by Linker (`hard`) or
    by similarity refresh (`soft`). Consumed by the graph UI and
    Article-Detail "Referenced by" / "Related" panels (Phase 3)."""

    __tablename__ = "graph_edges"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    from_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[EdgeKind] = mapped_column(Enum(EdgeKind), nullable=False)
    # For `hard` edges: the source `ArticleBlock` where the alias match
    # was found. NULL for `soft` edges.
    source_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    from_article: Mapped["Article"] = relationship(
        "Article",
        foreign_keys=[from_article_id],
    )
    to_article: Mapped["Article"] = relationship(
        "Article",
        foreign_keys=[to_article_id],
    )
    source_block: Mapped["ArticleBlock | None"] = relationship(
        "ArticleBlock",
        foreign_keys=[source_block_id],
    )

    __table_args__ = (
        # At most one edge of a given kind between the same pair from the
        # same source block. `source_block_id` is part of the key so that
        # distinct hard mentions from different blocks coexist.
        UniqueConstraint(
            "from_article_id",
            "to_article_id",
            "kind",
            "source_block_id",
            name="uq_graph_edge_unique",
        ),
    )
