from __future__ import annotations

import uuid
import re

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.articles.article_text import (
    candidate_description,
    internal_headings,
    is_meaningful_body_fragment,
)
from app.domain.articles.block_builder import has_meaningful_body, prepare_article_blocks
from app.domain.articles.metadata_service import enrich_or_fallback_candidate_metadata
from app.domain.articles.slug_service import slugify, unique_slug_for_project
from app.domain.linking.service import refresh_project_links
from app.models.article import Article, ArticleBlock, ArticleKind, ArticleStatus
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalStatus,
    StructureProposal,
)
from app.models.project import Project
from app.models.source_fragment import ElementType, SourceFragment
from app.models.structural_block import StructuralBlock


_TABLE_REF_RE = re.compile(r"\bTable\s+(\d+)\b", re.IGNORECASE)
_FIGURE_REF_RE = re.compile(r"\bFigure\s+(\d+)\b", re.IGNORECASE)
_MAX_CAPTION_GROUP_DISTANCE = 3
_MAX_REFERENCED_CAPTION_DISTANCE = 8


async def build_articles_from_proposal(
    proposal: StructureProposal,
    db: AsyncSession,
) -> list[uuid.UUID]:
    if proposal.status != ProposalStatus.READY:
        raise ValueError(
            f"Proposal {proposal.id} has status '{proposal.status.value}', expected 'ready'"
        )

    result = await db.execute(
        select(ArticleCandidate)
        .where(ArticleCandidate.proposal_id == proposal.id)
        .options(
            selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
        .order_by(ArticleCandidate.created_at)
    )
    candidates = list(result.scalars().all())

    if not candidates:
        logger.warning(f"Proposal {proposal.id} has no candidates; no articles will be built")
        proposal.status = ProposalStatus.REVIEWED
        await db.flush()
        return []

    article_ids: list[uuid.UUID] = []
    slugs_in_pass: set[str] = set()
    project = await db.get(Project, proposal.project_id)
    settings = project.settings if project and project.settings else {}
    min_blocks = int(settings.get("min_blocks", 4))
    min_chars = int(settings.get("min_chars", 450))
    available_structural_blocks = (
        (
            await db.execute(
                select(StructuralBlock.name).where(
                    StructuralBlock.project_id == proposal.project_id
                )
            )
        ).scalars().all()
        if project is not None
        else []
    )

    for candidate in candidates:
        # Only build articles for candidates the user has explicitly
        # confirmed. Candidates still in `proposed` status are treated as
        # "not yet reviewed" and skipped — this is what makes the
        # accept / reject workflow in the Review UI actually mean something.
        # (Use POST /structure/proposals/{id}/confirm-all to bulk-confirm
        # everything at once if review isn't needed.)
        if candidate.status != CandidateStatus.CONFIRMED:
            continue
        if not candidate.candidate_fragments:
            raise ValueError(f"Candidate {candidate.id} has no linked fragments")

        base_slug = slugify(candidate.title)
        slug = await unique_slug_for_project(
            db, proposal.project_id, base_slug, slugs_in_pass
        )

        recovered_fragments = await _recover_linked_source_fragments(candidate, db)
        materialized_fragments = [
            item.fragment for item in candidate.candidate_fragments if item.fragment is not None
        ]
        materialized_fragments.extend(recovered_fragments)
        materialized_fragments = _dedupe_fragments(materialized_fragments)

        body_fragments = [
            fragment
            for fragment in materialized_fragments
            if is_meaningful_body_fragment(fragment)
        ]
        if not body_fragments:
            logger.warning(
                f"Skipping candidate {candidate.id}: no meaningful body fragments after noise filtering"
            )
            continue

        block_count = len(body_fragments)
        char_count = sum(len(fragment.content) for fragment in body_fragments)
        article_kind = (
            ArticleKind.NODE
            if block_count < min_blocks or char_count < min_chars
            else ArticleKind.ARTICLE
        )

        structural_block_id = None
        candidate_payload = {
            "proposed_title": candidate.title,
            "suggested_section": candidate.suggested_section,
            "source_section_path": candidate.source_section_path,
            "internal_headings": internal_headings(candidate.candidate_fragments),
            "fragments": [
                item.fragment for item in candidate.candidate_fragments if item.fragment is not None
            ],
        }
        metadata = await enrich_or_fallback_candidate_metadata(
            project_name=project.name if project is not None else "Knowledge Base",
            kb_summary=project.summary if project is not None else None,
            candidate=candidate,
            candidate_payload=candidate_payload,
            available_structural_blocks=list(available_structural_blocks),
        )

        if metadata.suggested_structural_block or candidate.suggested_section:
            result = await db.execute(
                select(StructuralBlock.id).where(
                    StructuralBlock.project_id == proposal.project_id,
                    StructuralBlock.name
                    == (metadata.suggested_structural_block or candidate.suggested_section),
                )
            )
            structural_block_id = result.scalar_one_or_none()

        prepared_blocks = prepare_article_blocks(
            candidate,
            extra_fragments=recovered_fragments,
        )

        if not has_meaningful_body(prepared_blocks):
            logger.warning(
                f"Skipping candidate {candidate.id}: article body collapses to headings/captions only"
            )
            continue

        article = Article(
            project_id=proposal.project_id,
            candidate_id=candidate.id,
            title=metadata.title,
            slug=slug,
            description=metadata.description or candidate_description(candidate.candidate_fragments),
            suggested_section=candidate.suggested_section,
            kind=article_kind,
            status=ArticleStatus.DRAFT,
            structural_block_id=structural_block_id,
        )
        db.add(article)
        await db.flush()

        blocks = [
            ArticleBlock(
                article_id=article.id,
                **payload,
            )
            for _, payload in prepared_blocks
        ]

        db.add_all(blocks)
        await db.flush()
        article_ids.append(article.id)

    proposal.status = ProposalStatus.REVIEWED
    await refresh_project_links(proposal.project_id, db)
    if project is not None:
        article_rows = (
            await db.execute(
                select(Article)
                .where(Article.project_id == proposal.project_id)
                .order_by(Article.created_at)
            )
        ).scalars().all()
        project.summary = (
            f"{project.name} knowledge base overview:\n"
            + "\n".join(f"- {article.title}" for article in article_rows[:8])
            if article_rows
            else None
        )
    await db.flush()

    logger.info(
        f"Proposal {proposal.id}: built {len(article_ids)} articles "
        f"from {len(candidates)} candidates"
    )
    return article_ids


async def _recover_linked_source_fragments(
    candidate: ArticleCandidate,
    db: AsyncSession,
) -> list[SourceFragment]:
    """Recover source-native neighbors that must travel with a candidate.

    The proposal layer is allowed to be semantic, but article materialisation
    must remain source-faithful. If a candidate contains a caption, its grouped
    table/image target should come along. If prose references a nearby numbered
    table or figure, the matching caption and grouped target should also come
    along when they live in the same source. Distant references are kept as text
    references only so one table does not get duplicated across multiple
    articles.
    """
    fragments = [
        item.fragment
        for item in candidate.candidate_fragments
        if item.fragment is not None
    ]
    if not fragments:
        return []

    selected_ids = {fragment.id for fragment in fragments}
    selected_positions = [
        fragment.position_index
        for fragment in fragments
        if fragment.position_index is not None
    ]
    source_ids = {fragment.source_id for fragment in fragments}
    result = await db.execute(
        select(SourceFragment)
        .where(SourceFragment.source_id.in_(source_ids))
        .order_by(SourceFragment.position_index)
    )
    source_fragments = list(result.scalars().all())

    by_caption_group: dict[str, list[SourceFragment]] = {}
    table_caption_numbers: dict[str, list[SourceFragment]] = {}
    figure_caption_numbers: dict[str, list[SourceFragment]] = {}

    for fragment in source_fragments:
        meta_json = fragment.meta_json or {}
        group_id = meta_json.get("caption_group_id")
        if isinstance(group_id, str) and group_id:
            by_caption_group.setdefault(group_id, []).append(fragment)

        if fragment.element_type == ElementType.CAPTION:
            stripped = " ".join(fragment.content.split()).strip()
            table_match = re.match(r"^Table\s+(\d+)\b", stripped, re.IGNORECASE)
            figure_match = re.match(r"^Figure\s+(\d+)\b", stripped, re.IGNORECASE)
            if table_match:
                table_caption_numbers.setdefault(table_match.group(1), []).append(fragment)
            if figure_match:
                figure_caption_numbers.setdefault(figure_match.group(1), []).append(fragment)

    wanted: list[SourceFragment] = []
    wanted_ids: set[uuid.UUID] = set()

    def add(fragment: SourceFragment) -> None:
        if fragment.id in selected_ids or fragment.id in wanted_ids:
            return
        wanted_ids.add(fragment.id)
        wanted.append(fragment)

    def add_caption_group(fragment: SourceFragment) -> None:
        group_id = (fragment.meta_json or {}).get("caption_group_id")
        if not isinstance(group_id, str) or not group_id:
            return
        for grouped in by_caption_group.get(group_id, []):
            if not _fragment_is_near_anchor(grouped, fragment):
                continue
            add(grouped)

    for fragment in fragments:
        add_caption_group(fragment)
        table_refs = _TABLE_REF_RE.findall(fragment.content or "")
        figure_refs = _FIGURE_REF_RE.findall(fragment.content or "")
        for table_number in table_refs:
            for caption in table_caption_numbers.get(table_number, []):
                if not _caption_is_near_candidate(caption, selected_positions):
                    continue
                add(caption)
                add_caption_group(caption)
        for figure_number in figure_refs:
            for caption in figure_caption_numbers.get(figure_number, []):
                if not _caption_is_near_candidate(caption, selected_positions):
                    continue
                add(caption)
                add_caption_group(caption)

    return sorted(wanted, key=lambda fragment: fragment.position_index)


def _caption_is_near_candidate(
    caption: SourceFragment,
    candidate_positions: list[int],
) -> bool:
    if caption.position_index is None or not candidate_positions:
        return False
    low = min(candidate_positions) - _MAX_REFERENCED_CAPTION_DISTANCE
    high = max(candidate_positions) + _MAX_REFERENCED_CAPTION_DISTANCE
    return low <= caption.position_index <= high


def _fragment_is_near_anchor(
    fragment: SourceFragment,
    anchor: SourceFragment,
) -> bool:
    if fragment.id == anchor.id:
        return True
    if fragment.position_index is None or anchor.position_index is None:
        return False
    if fragment.page_number and anchor.page_number and fragment.page_number != anchor.page_number:
        return False
    return abs(fragment.position_index - anchor.position_index) <= _MAX_CAPTION_GROUP_DISTANCE


def _dedupe_fragments(fragments: list[SourceFragment]) -> list[SourceFragment]:
    seen: set[uuid.UUID] = set()
    deduped: list[SourceFragment] = []
    for fragment in sorted(fragments, key=lambda item: item.position_index):
        if fragment.id in seen:
            continue
        seen.add(fragment.id)
        deduped.append(fragment)
    return deduped
