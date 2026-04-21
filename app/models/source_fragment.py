from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.source import Source
    from app.models.article_candidate import ArticleCandidateFragment
    from app.models.article import ArticleBlock
    from app.models.block_multi_source import BlockMultiSource


class ElementType(str, enum.Enum):
    # Baseline set established in Phase 0. Phase 1 adds `quote`, `code_block`,
    # `image`, `footnote`; Phase 12 adds `formula`. The enum is widened at the
    # migration level — source-driven: the extractor only emits a type if the
    # source actually contains it.
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"
    QUOTE = "quote"
    CODE_BLOCK = "code_block"
    IMAGE = "image"
    FOOTNOTE = "footnote"
    FORMULA = "formula"


class SourceFragment(Base):
    __tablename__ = "source_fragments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of the normalised content. Used by DedupService (Phase 7) for
    # exact-duplicate detection across documents. Indexed for fast lookup.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    element_type: Mapped[ElementType] = mapped_column(Enum(ElementType), nullable=False)
    heading_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Depth in nested lists (0 = top-level). Populated by DOCX via
    # `w:numPr/w:ilvl` and by PDF via indent heuristics. Nullable everywhere
    # else.
    list_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Groups contiguous fragments that should be treated as a sequence by the
    # PlacementAgent (Phase 7) — e.g. an enumerated "Step 1/2/3" list, or a
    # block of consecutive list items at the same level. `NULL` = not grouped.
    group_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Inline markup spans preserved from the source: bold / italic / code /
    # link runs. Array of `{start, end, style, data}`. Phase 1 populates it;
    # in Phase 0 the column exists but is always `[]`.
    inline_spans: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Generic payload for per-type extras: `{"language": "python"}` on a
    # code_block, `{"attribution": "..."}` on a quote, `{"image_ref": "..."}`
    # on an image, `{"rows": [[...]]}` on a structured table, etc.
    meta_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="fragments",
    )
    candidate_fragments: Mapped[list["ArticleCandidateFragment"]] = relationship(
        "ArticleCandidateFragment",
        back_populates="fragment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    article_blocks: Mapped[list["ArticleBlock"]] = relationship(
        "ArticleBlock",
        back_populates="fragment",
        passive_deletes=True,
    )
    multi_source_links: Mapped[list["BlockMultiSource"]] = relationship(
        "BlockMultiSource",
        back_populates="fragment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("source_id", "position_index", name="uq_source_fragments_source_position"),
        Index(
            "ix_source_fragments_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
