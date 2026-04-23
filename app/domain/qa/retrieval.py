from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ingestion.embedding_service import embed_texts
from app.models.article import Article, ArticleBlock
from app.models.source_fragment import ElementType, SourceFragment


RETRIEVABLE_ELEMENT_TYPES = (
    ElementType.PARAGRAPH,
    ElementType.LIST_ITEM,
    ElementType.TABLE,
    ElementType.CAPTION,
    ElementType.QUOTE,
    ElementType.CODE_BLOCK,
    ElementType.FOOTNOTE,
)


@dataclass(frozen=True)
class RetrievalResult:
    block_id: uuid.UUID
    article_id: uuid.UUID
    article_title: str
    fragment_id: uuid.UUID
    content: str
    element_type: ElementType
    page_number: int | None
    section_path: str | None
    score: float


async def retrieve_relevant_blocks(
    project_id: uuid.UUID,
    query: str,
    db: AsyncSession,
    *,
    top_k: int = 15,
    max_per_article: int = 3,
    min_score: float = 0.2,
    candidate_limit: int | None = None,
) -> list[RetrievalResult]:
    """Retrieve project-local article blocks for strict RAG.

    The vector lives on SourceFragment, not ArticleBlock, so retrieval joins
    block -> origin fragment. Diversity is applied after vector ordering to
    avoid one article monopolising the context window.
    """
    cleaned_query = " ".join(query.split()).strip()
    if not cleaned_query or top_k <= 0 or max_per_article <= 0:
        return []

    query_embedding = (await embed_texts([cleaned_query]))[0]
    distance = SourceFragment.embedding.cosine_distance(query_embedding).label("distance")
    limit = candidate_limit or max(top_k * max_per_article * 2, top_k)

    result = await db.execute(
        select(ArticleBlock, Article, SourceFragment, distance)
        .join(Article, Article.id == ArticleBlock.article_id)
        .join(SourceFragment, SourceFragment.id == ArticleBlock.fragment_id)
        .where(
            Article.project_id == project_id,
            ArticleBlock.element_type.in_(RETRIEVABLE_ELEMENT_TYPES),
            SourceFragment.embedding.is_not(None),
        )
        .order_by(distance, Article.created_at, ArticleBlock.position_index)
        .limit(limit)
    )

    per_article: dict[uuid.UUID, int] = {}
    retrieved: list[RetrievalResult] = []
    for block, article, fragment, raw_distance in result.all():
        article_count = per_article.get(article.id, 0)
        if article_count >= max_per_article:
            continue

        per_article[article.id] = article_count + 1
        distance_value = float(raw_distance or 0.0)
        score = max(0.0, min(1.0, 1.0 - distance_value))
        if score < min_score:
            continue
        retrieved.append(
            RetrievalResult(
                block_id=block.id,
                article_id=article.id,
                article_title=article.title,
                fragment_id=fragment.id,
                content=block.content,
                element_type=block.element_type,
                page_number=block.page_number,
                section_path=block.section_path,
                score=score,
            )
        )
        if len(retrieved) >= top_k:
            break

    return retrieved
