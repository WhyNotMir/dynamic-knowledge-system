from __future__ import annotations

import re
import unicodedata
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.article import Article, ArticleBlock, ArticleKind, ArticleStatus
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    CandidateStatus,
    ProposalStatus,
    StructureProposal,
)


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


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

        article = Article(
            project_id=proposal.project_id,
            candidate_id=candidate.id,
            title=candidate.title,
            slug=slug,
            suggested_section=candidate.suggested_section,
            kind=ArticleKind.ARTICLE,
            status=ArticleStatus.DRAFT,
        )
        db.add(article)
        await db.flush()

        candidate_fragments = sorted(
            candidate.candidate_fragments,
            key=lambda item: item.position_index,
        )

        blocks: list[ArticleBlock] = []
        for index, candidate_fragment in enumerate(candidate_fragments):
            fragment = candidate_fragment.fragment
            if fragment is None:
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
                    position_index=index,
                    source_position_index=fragment.position_index,
                    page_number=fragment.page_number,
                    section_path=fragment.section_path,
                    list_level=fragment.list_level,
                    group_id=fragment.group_id,
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
    await db.flush()

    logger.info(
        f"Proposal {proposal.id}: built {len(article_ids)} articles "
        f"from {len(candidates)} candidates"
    )
    return article_ids
