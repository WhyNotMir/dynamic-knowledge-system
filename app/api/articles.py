from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.article import Article, ArticleBlock
from app.domain.articles.article_builder import build_articles_from_proposal
from app.schemas.article import (
    BuildArticlesRequest, BuildArticlesResponse,
    ArticleListItem, ArticleDetail,
)

router = APIRouter(prefix="/projects/{project_id}/articles", tags=["articles"])


@router.post("/build", response_model=BuildArticlesResponse)
async def build_articles(
    project_id: uuid.UUID,
    body: BuildArticlesRequest,
    db: AsyncSession = Depends(get_db),
):
    article_ids = await build_articles_from_proposal(body.proposal_id, db)
    return BuildArticlesResponse(article_ids=article_ids, count=len(article_ids))


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(
            Article,
            func.count(ArticleBlock.id).label("block_count")
        )
        .outerjoin(ArticleBlock, ArticleBlock.article_id == Article.id)
        .where(Article.project_id == project_id)
        .group_by(Article.id)
        .order_by(Article.created_at)
    )
    rows = result.all()
    return [
        ArticleListItem(
            id=row.Article.id,
            title=row.Article.title,
            suggested_section=row.Article.suggested_section,
            status=row.Article.status,
            block_count=row.block_count,
            created_at=row.Article.created_at,
        )
        for row in rows
    ]


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Article)
        .where(Article.id == article_id, Article.project_id == project_id)
        .options(selectinload(Article.blocks))
    )
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(404, "Article not found")
    return article