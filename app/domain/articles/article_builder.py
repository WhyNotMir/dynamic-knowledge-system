from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.article_candidate import StructureProposal, ArticleCandidate, ProposalStatus
from app.models.article import Article, ArticleBlock
from app.models.source_fragment import SourceFragment


async def build_articles_from_proposal(
    proposal_id: uuid.UUID, db: AsyncSession
) -> list[uuid.UUID]:
    """
    Строит статьи из всех кандидатов готового proposal.
    Возвращает список созданных article_id.
    """
    result = await db.execute(
        select(StructureProposal).where(StructureProposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    if not proposal or proposal.status != ProposalStatus.READY:
        raise ValueError(f"Proposal {proposal_id} not found or not ready")

    result = await db.execute(
        select(ArticleCandidate).where(ArticleCandidate.proposal_id == proposal_id)
    )
    candidates = result.scalars().all()

    article_ids = []
    for candidate in candidates:
        article_id = await _build_article(candidate, db)
        article_ids.append(article_id)

    proposal.status = ProposalStatus.REVIEWED
    await db.commit()

    logger.info(f"Built {len(article_ids)} articles from proposal {proposal_id}")
    return article_ids


async def _build_article(candidate: ArticleCandidate, db: AsyncSession) -> uuid.UUID:
    # Загружаем фрагменты по fragment_ids в порядке position_index
    fragment_uuids = [uuid.UUID(fid) for fid in candidate.fragment_ids]

    result = await db.execute(
        select(SourceFragment)
        .where(SourceFragment.id.in_(fragment_uuids))
        .order_by(SourceFragment.position_index)
    )
    fragments = result.scalars().all()

    article = Article(
        project_id=candidate.project_id,
        candidate_id=candidate.id,
        title=candidate.title,
        suggested_section=candidate.suggested_section,
    )
    db.add(article)
    await db.flush()  # получаем article.id

    for frag in fragments:
        db.add(ArticleBlock(
            article_id=article.id,
            fragment_id=frag.id,
            content=frag.content,
            element_type=frag.element_type.value,
            position_index=frag.position_index,
            page_number=frag.page_number,
            section_path=frag.section_path,
        ))

    logger.debug(f"Article '{article.title}': {len(fragments)} blocks")
    return article.id