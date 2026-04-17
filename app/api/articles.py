from __future__ import annotations
import uuid
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from loguru import logger

from app.database import get_db
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import StructureProposal, ProposalStatus
from app.domain.articles.article_builder import build_articles_from_proposal
from app.schemas.article import (
    BuildArticlesRequest, BuildArticlesResponse,
    ArticleListItem, ArticleDetail,
)

router = APIRouter(prefix="/projects/{project_id}/articles", tags=["articles"])


async def _resolve_latest_ready_proposal_id(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> uuid.UUID:
    """Return the id of the latest READY proposal for a project.

    Raises 409 if none exists, so callers understand they must wait for
    / trigger the structure-proposal stage first.
    """
    result = await db.execute(
        select(StructureProposal.id)
        .where(
            StructureProposal.project_id == project_id,
            StructureProposal.status == ProposalStatus.READY,
        )
        .order_by(StructureProposal.created_at.desc())
        .limit(1)
    )
    proposal_id = result.scalar_one_or_none()
    if proposal_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No READY structure proposal found for this project. "
                "Call POST /projects/{project_id}/structure/propose first "
                "and wait for the proposal to reach status=READY."
            ),
        )
    return proposal_id


@router.post("/build", response_model=BuildArticlesResponse)
async def build_articles(
    project_id: uuid.UUID,
    body: BuildArticlesRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Build articles from a structure proposal.

    `proposal_id` in the body is optional. If omitted, the backend uses the
    latest proposal of this project with status=READY. The UI therefore
    only needs `project_id` from the URL.

    Errors:
      - 404: proposal not found or does not belong to this project
      - 409: proposal is not READY (or no READY proposal exists for the project)
      - 500: unexpected DB error (wrapped, not leaked)
    """
    # 1. Resolve target proposal
    proposal_id = body.proposal_id if body and body.proposal_id else None
    if proposal_id is None:
        proposal_id = await _resolve_latest_ready_proposal_id(project_id, db)

    # 2. Validate proposal existence / ownership / state at the API boundary,
    #    so the domain layer stays thin and the HTTP codes are meaningful.
    proposal = await db.get(StructureProposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, f"Proposal {proposal_id} not found")
    if proposal.project_id != project_id:
        raise HTTPException(
            404,
            f"Proposal {proposal_id} does not belong to project {project_id}",
        )
    if proposal.status != ProposalStatus.READY:
        raise HTTPException(
            409,
            f"Proposal {proposal_id} has status '{proposal.status.value}', "
            f"expected 'ready'. Articles can only be built from READY proposals.",
        )

    # 3. Build. Any domain-level value errors become 400; DB errors become 500
    #    with a rollback so the session is left clean.
    try:
        article_ids = await build_articles_from_proposal(
            proposal=proposal,
            db=db,
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while building articles for proposal {proposal_id}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    return BuildArticlesResponse(article_ids=article_ids, count=len(article_ids))


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(
            Article,
            func.count(ArticleBlock.id).label("block_count"),
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
