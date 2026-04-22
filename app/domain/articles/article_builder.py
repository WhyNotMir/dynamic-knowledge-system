from __future__ import annotations

import re
import unicodedata
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.chains.title_agent import TitleAgentOutput
from app.models.article import Article, ArticleBlock, ArticleKind, ArticleStatus
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalStatus,
    StructureProposal,
)
from app.models.project import Project
from app.models.source_fragment import ElementType
from app.models.structural_block import StructuralBlock


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_NOISE_ONLY_RE = re.compile(r"^[\W\d_]+$")


def _normalise_heading_text(value: str | None) -> str:
    if not value:
        return ""
    collapsed = " ".join(value.split())
    return collapsed.strip().casefold()


def _looks_like_noise_text(value: str | None) -> bool:
    if not value:
        return True

    stripped = " ".join(value.split()).strip()
    if not stripped:
        return True

    lowered = stripped.casefold()
    if lowered in {"<eos>", "eos"}:
        return True

    bad_markers = ("arxiv:", "[cs.", "gnmt", "en-de", "en-fr", "wsj")
    if any(marker in lowered for marker in bad_markers):
        return True

    alpha = sum(char.isalpha() for char in stripped)
    digits = sum(char.isdigit() for char in stripped)
    if len(stripped) <= 2 and alpha == 0:
        return True
    if _NOISE_ONLY_RE.fullmatch(stripped):
        return True
    if alpha == 0 and digits > 0:
        return True
    if stripped.isupper() and digits > 0 and alpha < 6:
        return True

    return False


def _candidate_description(candidate_fragments: list[ArticleCandidateFragment]) -> str | None:
    for item in candidate_fragments:
        fragment = item.fragment
        if fragment is None:
            continue
        if fragment.element_type in {ElementType.PARAGRAPH, ElementType.QUOTE, ElementType.CODE_BLOCK}:
            text = " ".join(fragment.content.split()).strip()
            if text:
                return text[:280]
    return None


def _internal_headings(candidate_fragments: list[ArticleCandidateFragment]) -> list[str]:
    headings: list[str] = []
    seen: set[str] = set()
    for item in candidate_fragments:
        fragment = item.fragment
        if fragment is None:
            continue
        if fragment.element_type != ElementType.HEADING:
            continue
        if (fragment.heading_level or 0) < 2:
            continue
        text = fragment.content.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        headings.append(text)
    return headings


def _fallback_metadata(
    candidate: ArticleCandidate,
    candidate_fragments: list[ArticleCandidateFragment],
    available_structural_blocks: list[str],
) -> TitleAgentOutput:
    suggested_block = None
    for value in (candidate.suggested_section, candidate.source_section_path):
        if value and value in available_structural_blocks:
            suggested_block = value
            break

    return TitleAgentOutput(
        title=candidate.title,
        description=_candidate_description(candidate_fragments),
        suggested_structural_block=suggested_block,
    )


def _slugify(title: str) -> str:
    """Cheap deterministic slug: NFKD → ASCII → lowercase → alnum-hyphen.

    Not meant to handle Cyrillic / CJK nicely (Phase 3 AliasAgent will own
    richer slug rules). Good enough for Phase 0: stable, URL-safe, and
    length-capped. Uniqueness inside a project is guaranteed by the caller
    via a short hex suffix when collisions occur.
    """
    normalised = unicodedata.normalize("NFKD", title)
    ascii_str = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_str.lower().strip()
    slug = _SLUG_NON_ALNUM.sub("-", lowered).strip("-")
    if not slug:
        # Fallback when the title collapses to empty ASCII (e.g. all Cyrillic).
        slug = f"article-{uuid.uuid4().hex[:8]}"
    return slug[:200]


async def _unique_slug_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    base_slug: str,
    taken: set[str],
) -> str:
    """Return a slug unique inside `project_id`.

    Takes an in-memory `taken` set so that inside a single build pass we
    don't collide with slugs just created in the same transaction (those
    are not visible to the SELECT until flush).
    """
    candidate = base_slug
    if candidate not in taken:
        existing = await db.execute(
            select(Article.id).where(
                Article.project_id == project_id,
                Article.slug == candidate,
            )
        )
        if existing.scalar_one_or_none() is None:
            taken.add(candidate)
            return candidate

    # Collision — append a short uuid suffix and retry once. Statistically
    # safe for any realistic scale.
    for _ in range(5):
        suffix = uuid.uuid4().hex[:6]
        candidate = f"{base_slug}-{suffix}"[:200]
        if candidate in taken:
            continue
        existing = await db.execute(
            select(Article.id).where(
                Article.project_id == project_id,
                Article.slug == candidate,
            )
        )
        if existing.scalar_one_or_none() is None:
            taken.add(candidate)
            return candidate

    raise RuntimeError(
        f"Unable to find a unique slug for base '{base_slug}' in project {project_id}"
    )


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

        base_slug = _slugify(candidate.title)
        slug = await _unique_slug_for_project(
            db, proposal.project_id, base_slug, slugs_in_pass
        )

        block_count = len(candidate.candidate_fragments)
        char_count = sum(
            len(item.fragment.content)
            for item in candidate.candidate_fragments
            if item.fragment is not None
        )
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
            "internal_headings": _internal_headings(candidate.candidate_fragments),
            "fragments": [
                item.fragment for item in candidate.candidate_fragments if item.fragment is not None
            ],
        }
        # Build is the user's synchronous "materialise content now" action.
        # Keep it fast and deterministic: metadata enrichment can happen in a
        # separate async pass later, but the critical path must not depend on
        # dozens of network round-trips.
        metadata = _fallback_metadata(
            candidate,
            candidate.candidate_fragments,
            list(available_structural_blocks),
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

        article = Article(
            project_id=proposal.project_id,
            candidate_id=candidate.id,
            title=metadata.title,
            slug=slug,
            description=metadata.description or _candidate_description(candidate.candidate_fragments),
            suggested_section=candidate.suggested_section,
            kind=article_kind,
            status=ArticleStatus.DRAFT,
            structural_block_id=structural_block_id,
        )
        db.add(article)
        await db.flush()

        candidate_fragments = sorted(
            candidate.candidate_fragments,
            key=lambda item: item.position_index,
        )

        blocks: list[ArticleBlock] = []
        candidate_title_key = _normalise_heading_text(candidate.title)
        source_heading_key = _normalise_heading_text(candidate.source_section_path)
        skipped_title_heading = False

        for candidate_fragment in candidate_fragments:
            fragment = candidate_fragment.fragment
            if fragment is None:
                continue
            if _looks_like_noise_text(fragment.content):
                continue

            # Phase 1 wiki-shape prep: if the first H1 fragment just repeats
            # the article title hint, omit it from the body so the page doesn't
            # render title -> identical heading immediately below.
            if (
                not skipped_title_heading
                and fragment.element_type == ElementType.HEADING
                and fragment.heading_level == 1
            ):
                fragment_heading_key = _normalise_heading_text(fragment.content)
                if fragment_heading_key and fragment_heading_key in {
                    candidate_title_key,
                    source_heading_key,
                }:
                    skipped_title_heading = True
                    continue

            # Invariant § 6.1 — `source_position_index` mirrors the origin
            # fragment's `position_index`. Invariant CI asserts
            # monotonicity per (source_id, article_id).
            blocks.append(
                ArticleBlock(
                    article_id=article.id,
                    fragment_id=fragment.id,
                    content=fragment.content,
                    element_type=fragment.element_type,
                    position_index=len(blocks),
                    source_position_index=fragment.position_index,
                    page_number=fragment.page_number,
                    section_path=fragment.section_path,
                    list_level=fragment.list_level,
                    group_id=fragment.group_id,
                    inline_spans=fragment.inline_spans,
                    meta_json=fragment.meta_json,
                    synthesized=False,
                )
            )

        if not blocks:
            raise ValueError(
                f"Candidate {candidate.id} has no fragments and cannot be converted into an article."
            )

        db.add_all(blocks)
        await db.flush()
        article_ids.append(article.id)

    proposal.status = ProposalStatus.REVIEWED
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
