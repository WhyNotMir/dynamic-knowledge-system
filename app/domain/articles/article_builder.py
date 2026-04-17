from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.article_candidate import StructureProposal, ArticleCandidate, ProposalStatus
from app.models.article import Article, ArticleBlock
from app.models.source_fragment import SourceFragment


async def build_articles_from_proposal(
    proposal: StructureProposal,
    db: AsyncSession,
) -> list[uuid.UUID]:
    """Materialize articles from a READY StructureProposal.

    Contract:
      - The caller is responsible for validating proposal existence, ownership
        and status (READY). This function treats `proposal` as trusted.
      - Commits once at the end. If any candidate fails, the whole transaction
        is rolled back and the exception propagates, so partial state never
        leaks into the DB.
      - On success, the proposal is transitioned READY → REVIEWED to prevent
        accidental re-builds that would duplicate articles.
    """
    if proposal.status != ProposalStatus.READY:
        # Defensive: the API already validates, but keep a safety net so that
        # direct domain callers don't create duplicates.
        raise ValueError(
            f"Proposal {proposal.id} has status '{proposal.status.value}', "
            f"expected 'ready'"
        )

    result = await db.execute(
        select(ArticleCandidate).where(ArticleCandidate.proposal_id == proposal.id)
    )
    candidates = list(result.scalars().all())

    if not candidates:
        logger.warning(
            f"Proposal {proposal.id} has no candidates; no articles will be built"
        )
        proposal.status = ProposalStatus.REVIEWED
        await db.commit()
        return []

    article_ids: list[uuid.UUID] = []
    try:
        for candidate in candidates:
            article_id = await _build_article(candidate, db)
            article_ids.append(article_id)

        proposal.status = ProposalStatus.REVIEWED
        await db.commit()
    except Exception:
        # Ensure the session is clean before it is returned to the caller.
        await db.rollback()
        raise

    logger.info(
        f"Proposal {proposal.id}: built {len(article_ids)} articles "
        f"from {len(candidates)} candidates"
    )
    return article_ids


async def _build_article(candidate: ArticleCandidate, db: AsyncSession) -> uuid.UUID:
    """Create one Article + its ArticleBlocks from one ArticleCandidate."""
    raw_ids = candidate.fragment_ids or []

    # Parse fragment_ids defensively: a malformed string would surface as a
    # ValueError at the API boundary (400), not a generic 500.
    fragment_uuids: list[uuid.UUID] = []
    for fid in raw_ids:
        try:
            fragment_uuids.append(uuid.UUID(str(fid)))
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Candidate {candidate.id} has invalid fragment_id {fid!r}: {e}"
            )

    if not fragment_uuids:
        logger.warning(
            f"Candidate {candidate.id} ('{candidate.title}') has no fragment_ids; "
            f"article will be created with 0 blocks"
        )
        fragments: list[SourceFragment] = []
    else:
        result = await db.execute(
            select(SourceFragment)
            .where(SourceFragment.id.in_(fragment_uuids))
            .order_by(SourceFragment.position_index)
        )
        fragments = list(result.scalars().all())

        missing = len(fragment_uuids) - len(fragments)
        if missing > 0:
            # Don't crash — log so the operator sees data drift but still
            # produce the best-effort article.
            logger.warning(
                f"Candidate {candidate.id}: {missing}/{len(fragment_uuids)} "
                f"fragments not found (likely deleted); article will be partial"
            )

    article = Article(
        project_id=candidate.project_id,
        candidate_id=candidate.id,
        title=candidate.title,
        suggested_section=candidate.suggested_section,
    )
    db.add(article)
    await db.flush()  # obtain article.id before adding blocks

    for frag in fragments:
        # element_type on SourceFragment is an Enum; .value is safe because the
        # model's type annotation enforces it. Fall back to str() if someone
        # bypasses the ORM and stores a raw string.
        try:
            element_type_value = frag.element_type.value  # type: ignore[union-attr]
        except AttributeError:
            element_type_value = str(frag.element_type)

        db.add(ArticleBlock(
            article_id=article.id,
            fragment_id=frag.id,
            content=frag.content,
            element_type=element_type_value,
            position_index=frag.position_index,
            page_number=frag.page_number,
            section_path=frag.section_path,
        ))

    logger.debug(
        f"Article '{article.title}' (id={article.id}): {len(fragments)} blocks"
    )
    return article.id
