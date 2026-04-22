from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.source_fragment import ElementType

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.article_candidate import ArticleCandidate
    from app.models.source_fragment import SourceFragment
    from app.models.structural_block import StructuralBlock
    from app.models.alias import Alias
    from app.models.block_multi_source import BlockMultiSource
    from app.models.block_citation import BlockCitation
    from app.models.revisions import ArticleRevision, BlockRevision


class ArticleStatus(str, enum.Enum):
    # Extended in Phase 0 with `outdated` (article needs regen after new
    # content landed — Phase 7) and `deprecated` (article explicitly retired).
    DRAFT = "draft"
    PUBLISHED = "published"
    OUTDATED = "outdated"
    DEPRECATED = "deprecated"


class ArticleKind(str, enum.Enum):
    # Phase 1 activates the split: `article` = full topic page, `node` = a
    # thinner Knowledge Node (short, reference-only). The threshold between
    # the two is a Project setting (`min_blocks`, `min_chars`).
    ARTICLE = "article"
    NODE = "node"


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    structural_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("structural_blocks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # Slug is unique within a project — the stable canonical identifier the
    # Phase 3 Linker uses and the Phase 14 public-sharing URL is built on.
    slug: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    kind: Mapped[ArticleKind] = mapped_column(
        Enum(ArticleKind),
        default=ArticleKind.ARTICLE,
        nullable=False,
    )
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(ArticleStatus),
        default=ArticleStatus.DRAFT,
        nullable=False,
    )
    # Denormalised list of alias strings (synonyms / abbreviations) for fast
    # Linker lookup. The authoritative record lives in the `aliases` table;
    # this column is a read-only cache refreshed by AliasAgent (Phase 3).
    aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    revision_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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
        back_populates="articles",
    )
    candidate: Mapped["ArticleCandidate | None"] = relationship(
        "ArticleCandidate",
        back_populates="articles",
    )
    structural_block: Mapped["StructuralBlock | None"] = relationship(
        "StructuralBlock",
        back_populates="articles",
    )
    blocks: Mapped[list["ArticleBlock"]] = relationship(
        "ArticleBlock",
        back_populates="article",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ArticleBlock.position_index",
    )
    alias_records: Mapped[list["Alias"]] = relationship(
        "Alias",
        back_populates="article",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    revisions: Mapped[list["ArticleRevision"]] = relationship(
        "ArticleRevision",
        back_populates="article",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_articles_project_slug"),
    )


class ArticleBlock(Base):
    __tablename__ = "article_blocks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fragment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_fragments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    element_type: Mapped[ElementType] = mapped_column(Enum(ElementType), nullable=False)
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # `source_position_index` mirrors `SourceFragment.position_index` of the
    # block's origin fragment. The core invariant § 6.1 is enforced over this
    # column: for every (source_id, article_id) pair, blocks from that source
    # have monotonically increasing `source_position_index`. Synthesized
    # blocks may have `NULL` here and are excluded from the monotonicity check.
    source_position_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    list_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    inline_spans: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # True for blocks produced by SynthesisAgent (Phase 7) from a merge of
    # two duplicates. Such blocks are the single exception to invariant § 6.2
    # (content immutability) and must carry verified citations via
    # BlockMultiSource + BlockCitation.
    synthesized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    article: Mapped["Article"] = relationship(
        "Article",
        back_populates="blocks",
    )
    fragment: Mapped["SourceFragment | None"] = relationship(
        "SourceFragment",
        back_populates="article_blocks",
    )
    multi_source_links: Mapped[list["BlockMultiSource"]] = relationship(
        "BlockMultiSource",
        back_populates="block",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    citations: Mapped[list["BlockCitation"]] = relationship(
        "BlockCitation",
        back_populates="block",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    revisions: Mapped[list["BlockRevision"]] = relationship(
        "BlockRevision",
        back_populates="block",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("article_id", "position_index", name="uq_article_block_position"),
    )
