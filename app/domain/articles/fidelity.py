from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.articles.article_text import looks_like_noise_fragment
from app.models.article import Article, ArticleBlock
from app.models.source_fragment import ElementType, SourceFragment


_TABLE_REF_RE = re.compile(r"\bTable\s+(\d+)\b", re.IGNORECASE)
_FIGURE_REF_RE = re.compile(r"\bFigure\s+(\d+)\b", re.IGNORECASE)


@dataclass(slots=True)
class FidelityIssue:
    code: str
    position_index: int | None
    page_number: int | None
    element_type: str | None
    message: str


@dataclass(slots=True)
class SourceArticleFidelityReport:
    source_id: uuid.UUID
    project_id: uuid.UUID
    meaningful_fragment_count: int
    covered_fragment_count: int
    missing_fragment_ids: list[uuid.UUID] = field(default_factory=list)
    duplicated_fragment_ids: list[uuid.UUID] = field(default_factory=list)
    issues: list[FidelityIssue] = field(default_factory=list)

    @property
    def is_lossless(self) -> bool:
        return not self.missing_fragment_ids and not self.duplicated_fragment_ids


async def audit_source_article_fidelity(
    *,
    source_id: uuid.UUID,
    project_id: uuid.UUID,
    db: AsyncSession,
) -> SourceArticleFidelityReport:
    fragments = list(
        (
            await db.execute(
                select(SourceFragment)
                .where(SourceFragment.source_id == source_id)
                .order_by(SourceFragment.position_index)
            )
        )
        .scalars()
        .all()
    )

    block_rows = list(
        (
            await db.execute(
                select(ArticleBlock, Article.title)
                .join(Article, Article.id == ArticleBlock.article_id)
                .where(Article.project_id == project_id)
                .where(ArticleBlock.fragment_id.is_not(None))
            )
        ).all()
    )
    block_counts = Counter(block.fragment_id for block, _ in block_rows)
    meaningful = [fragment for fragment in fragments if _is_meaningful_for_fidelity(fragment)]
    meaningful_ids = {fragment.id for fragment in meaningful}

    missing = [
        fragment
        for fragment in meaningful
        if block_counts.get(fragment.id, 0) == 0
    ]
    duplicated = [
        fragment
        for fragment in meaningful
        if block_counts.get(fragment.id, 0) > 1
    ]

    issues = [
        FidelityIssue(
            code="missing_fragment",
            position_index=fragment.position_index,
            page_number=fragment.page_number,
            element_type=fragment.element_type.value,
            message=f"Meaningful source fragment is not represented in any article: {fragment.content[:120]}",
        )
        for fragment in missing
    ]
    issues.extend(
        FidelityIssue(
            code="duplicated_fragment",
            position_index=fragment.position_index,
            page_number=fragment.page_number,
            element_type=fragment.element_type.value,
            message=f"Source fragment is represented {block_counts[fragment.id]} times: {fragment.content[:120]}",
        )
        for fragment in duplicated
    )
    issues.extend(_caption_target_issues(meaningful, meaningful_ids))

    return SourceArticleFidelityReport(
        source_id=source_id,
        project_id=project_id,
        meaningful_fragment_count=len(meaningful),
        covered_fragment_count=sum(
            1 for fragment_id in meaningful_ids if block_counts.get(fragment_id, 0) >= 1
        ),
        missing_fragment_ids=[fragment.id for fragment in missing],
        duplicated_fragment_ids=[fragment.id for fragment in duplicated],
        issues=issues,
    )


def _is_meaningful_for_fidelity(fragment: SourceFragment) -> bool:
    if looks_like_noise_fragment(fragment):
        return False
    if fragment.element_type == ElementType.HEADING:
        return bool(fragment.content.strip())
    if fragment.element_type == ElementType.FOOTNOTE:
        meta_json = fragment.meta_json or {}
        return meta_json.get("references_section") is True or bool(
            re.match(r"^\[\d+\]\s+", fragment.content.strip())
        )
    return fragment.element_type in {
        ElementType.PARAGRAPH,
        ElementType.LIST_ITEM,
        ElementType.TABLE,
        ElementType.CAPTION,
        ElementType.QUOTE,
        ElementType.CODE_BLOCK,
        ElementType.IMAGE,
        ElementType.FORMULA,
    }


def _caption_target_issues(
    fragments: list[SourceFragment],
    meaningful_ids: set[uuid.UUID],
) -> list[FidelityIssue]:
    del meaningful_ids
    issues: list[FidelityIssue] = []
    by_caption_group: dict[str, list[SourceFragment]] = {}

    for fragment in fragments:
        meta_json = fragment.meta_json or {}
        group_id = meta_json.get("caption_group_id")
        if isinstance(group_id, str) and group_id:
            by_caption_group.setdefault(group_id, []).append(fragment)

    for group in by_caption_group.values():
        has_caption = any(fragment.element_type == ElementType.CAPTION for fragment in group)
        has_target = any(
            fragment.element_type in {ElementType.TABLE, ElementType.IMAGE}
            for fragment in group
        )
        if has_caption and not has_target:
            caption = next(fragment for fragment in group if fragment.element_type == ElementType.CAPTION)
            issues.append(
                FidelityIssue(
                    code="orphan_caption",
                    position_index=caption.position_index,
                    page_number=caption.page_number,
                    element_type=caption.element_type.value,
                    message=f"Caption has no grouped table/image target: {caption.content[:120]}",
                )
            )
        if has_target and not has_caption:
            target = next(
                fragment
                for fragment in group
                if fragment.element_type in {ElementType.TABLE, ElementType.IMAGE}
            )
            issues.append(
                FidelityIssue(
                    code="orphan_caption_target",
                    position_index=target.position_index,
                    page_number=target.page_number,
                    element_type=target.element_type.value,
                    message="Table/image target has no grouped caption.",
                )
            )

    return issues


def referenced_table_numbers(text: str) -> set[str]:
    return set(_TABLE_REF_RE.findall(text or ""))


def referenced_figure_numbers(text: str) -> set[str]:
    return set(_FIGURE_REF_RE.findall(text or ""))
