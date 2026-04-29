from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.application.articles.build_service import (
    ProposalNotFoundError,
    ProposalNotReadyError,
    ReadyProposalNotFoundError,
    build_articles_for_project,
)
from app.application.articles.query_service import (
    ArticleNotFoundError,
    delete_all_articles as delete_all_articles_service,
    get_article_detail,
    get_articles_sidebar as get_articles_sidebar_service,
    list_article_items,
    update_article_aliases as update_article_aliases_service,
)
from app.schemas.article import (
    ArticleDetail,
    ArticleAliasesResponse,
    ArticleAliasesUpdate,
    ArticleListItem,
    ArticleSidebarGroup,
    BuildArticlesRequest,
    BuildArticlesResponse,
    DeleteAllArticlesResponse,
)

router = APIRouter(prefix="/projects/{project_id}/articles", tags=["articles"])

@router.post("/build", response_model=BuildArticlesResponse)
async def build_articles(
    project_id: uuid.UUID,
    body: BuildArticlesRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    proposal_id = body.proposal_id if body and body.proposal_id else None
    try:
        article_ids = await build_articles_for_project(
            project_id,
            db,
            proposal_id=proposal_id,
        )
        await db.commit()
    except ProposalNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except (ProposalNotReadyError, ReadyProposalNotFoundError) as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception(f"DB error while building articles for project {project_id}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc.__class__.__name__}")

    return BuildArticlesResponse(article_ids=article_ids, count=len(article_ids))


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await list_article_items(project_id, db)


@router.get("/sidebar", response_model=list[ArticleSidebarGroup])
async def get_articles_sidebar(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await get_articles_sidebar_service(project_id, db)


@router.delete("", response_model=DeleteAllArticlesResponse)
async def delete_all_articles(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    deleted_count = await delete_all_articles_service(project_id, db)
    await db.commit()
    return DeleteAllArticlesResponse(deleted_count=deleted_count)


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_article_detail(project_id, article_id, db)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="Article not found")


@router.put("/{article_id}/aliases", response_model=ArticleAliasesResponse)
async def update_article_aliases(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    body: ArticleAliasesUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        response = await update_article_aliases_service(
            project_id,
            article_id,
            body,
            db,
        )
        await db.commit()
        return response
    except ArticleNotFoundError:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Article not found")
