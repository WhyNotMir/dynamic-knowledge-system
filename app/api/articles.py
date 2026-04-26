from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.domain.articles.view_model import (
    build_article_breadcrumb,
    build_sidebar_tree,
    build_source_context_label,
    collect_link_ranges,
    is_noise_block,
    is_reasonable_manual_alias,
    parse_heading_label,
)
from app.domain.articles.article_builder import build_articles_from_proposal
from app.domain.linking.service import get_article_graph_panels, refresh_project_links
from app.models.alias import Alias, AliasSource
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    ProposalStatus,
    StructureProposal,
)
from app.models.source import Source
from app.models.source_fragment import SourceFragment
from app.models.structural_block import StructuralBlock
from app.schemas.article import (
    ArticleBreadcrumbItem,
    ArticleDetail,
    ArticleAliasesResponse,
    ArticleAliasesUpdate,
    ArticleLinkSummary,
    ArticleListItem,
    ArticleSidebarGroup,
    ArticleTocItem,
    BuildArticlesRequest,
    BuildArticlesResponse,
    DeleteAllArticlesResponse,
)

router = APIRouter(prefix="/projects/{project_id}/articles", tags=["articles"])

async def _resolve_latest_ready_proposal_id(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> uuid.UUID:
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
    proposal_id = body.proposal_id if body and body.proposal_id else None
    if proposal_id is None:
        proposal_id = await _resolve_latest_ready_proposal_id(project_id, db)

    result = await db.execute(
        select(StructureProposal)
        .where(StructureProposal.id == proposal_id)
        .options(
            selectinload(StructureProposal.candidates)
            .selectinload(ArticleCandidate.candidate_fragments)
            .selectinload(ArticleCandidateFragment.fragment)
        )
    )
    proposal = result.scalar_one_or_none()

    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    if proposal.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail=f"Proposal {proposal_id} does not belong to project {project_id}",
        )
    if proposal.status != ProposalStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Proposal {proposal_id} has status '{proposal.status.value}', "
                "expected 'ready'."
            ),
        )

    try:
        article_ids = await build_articles_from_proposal(proposal=proposal, db=db)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception(f"DB error while building articles for proposal {proposal_id}")
        raise HTTPException(status_code=500, detail=f"Database error: {exc.__class__.__name__}")

    return BuildArticlesResponse(article_ids=article_ids, count=len(article_ids))


@router.get("", response_model=list[ArticleListItem])
async def list_articles(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    # Order articles by where their *earliest* source fragment appears in
    # the original document (SourceFragment.position_index is the ingestion
    # order within a source). Articles whose fragments have all been detached
    # via SET NULL fall to the end.
    result = await db.execute(
        select(
            Article,
            func.count(ArticleBlock.id).label("block_count"),
            func.min(SourceFragment.position_index).label("first_pos"),
        )
        .outerjoin(ArticleBlock, ArticleBlock.article_id == Article.id)
        .outerjoin(SourceFragment, SourceFragment.id == ArticleBlock.fragment_id)
        .where(Article.project_id == project_id)
        .group_by(Article.id)
        .order_by(
            func.min(SourceFragment.position_index).asc().nullslast(),
            Article.created_at,
        )
    )
    rows = result.all()

    return [
        ArticleListItem(
            id=row.Article.id,
            title=row.Article.title,
            slug=row.Article.slug,
            kind=row.Article.kind,
            structural_block_id=row.Article.structural_block_id,
            suggested_section=row.Article.suggested_section,
            status=row.Article.status,
            description=row.Article.description,
            block_count=row.block_count,
            created_at=row.Article.created_at,
        )
        for row in rows
    ]


@router.get("/sidebar", response_model=list[ArticleSidebarGroup])
async def get_articles_sidebar(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    article_result = await db.execute(
        select(Article)
        .where(Article.project_id == project_id)
        .options(selectinload(Article.blocks))
        .order_by(Article.created_at, Article.title)
    )
    articles = list(article_result.scalars().unique().all())

    block_result = await db.execute(
        select(StructuralBlock)
        .where(
            StructuralBlock.project_id == project_id,
            StructuralBlock.parent_id.is_(None),
        )
        .options(
            selectinload(StructuralBlock.children).selectinload(StructuralBlock.children)
        )
        .order_by(StructuralBlock.position_index, StructuralBlock.created_at)
    )
    structural_blocks = list(block_result.scalars().all())

    return build_sidebar_tree(structural_blocks, articles)


@router.delete("", response_model=DeleteAllArticlesResponse)
async def delete_all_articles(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count(Article.id)).where(Article.project_id == project_id)
    )
    deleted_count = int(result.scalar_one() or 0)

    if deleted_count == 0:
        return DeleteAllArticlesResponse(deleted_count=0)

    await db.execute(delete(Article).where(Article.project_id == project_id))
    await db.commit()
    logger.info(f"Deleted {deleted_count} articles for project {project_id}")
    return DeleteAllArticlesResponse(deleted_count=deleted_count)


@router.get("/{article_id}", response_model=ArticleDetail)
async def get_article(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Article)
        .where(
            Article.id == article_id,
            Article.project_id == project_id,
        )
        .options(
            selectinload(Article.blocks)
            .selectinload(ArticleBlock.fragment)
            .selectinload(SourceFragment.source)
        )
    )
    article = result.scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    sibling_articles = (
        await db.execute(
            select(Article).where(
                Article.project_id == project_id,
                Article.id != article_id,
            )
        )
    ).scalars().all()
    project_blocks = (
        await db.execute(
            select(StructuralBlock).where(StructuralBlock.project_id == project_id)
        )
    ).scalars().all()
    block_by_id = {block.id: block for block in project_blocks}

    link_targets: list[dict[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()
    for sibling in sibling_articles:
        labels = [sibling.title, *(sibling.aliases or [])]
        for raw_label in labels:
            label = " ".join(str(raw_label).split()).strip()
            if len(label) < 2:
                continue
            key = (str(sibling.id), label.casefold())
            if key in seen_targets:
                continue
            seen_targets.add(key)
            link_targets.append({"article_id": str(sibling.id), "label": label})

    link_targets.sort(key=lambda item: len(item["label"]), reverse=True)

    referenced_by_rows, related_rows = await get_article_graph_panels(article.id, db)

    block_payload = []
    toc_payload: list[ArticleTocItem] = []
    for block in article.blocks:
        source_title = None
        if block.fragment is not None and block.fragment.source is not None:
            source_title = block.fragment.source.title or block.fragment.source.filename
        heading_depth = None
        heading_label = None
        heading_prefix = None
        if block.element_type.value == "heading":
            heading_info = parse_heading_label(block.content)
            heading_depth = heading_info.depth
            heading_label = heading_info.label
            heading_prefix = heading_info.prefix
            if not is_noise_block(block):
                toc_payload.append(
                    ArticleTocItem(
                        id=f"block-{block.id}",
                        block_id=block.id,
                        label=heading_label,
                        level=heading_depth,
                        prefix=heading_prefix,
                    )
                )
        block_payload.append(
            {
                "id": block.id,
                "fragment_id": block.fragment_id,
                "source_title": source_title,
                "source_context_label": build_source_context_label(block, source_title),
                "content": block.content,
                "element_type": block.element_type,
                "position_index": block.position_index,
                "source_position_index": block.source_position_index,
                "page_number": block.page_number,
                "section_path": block.section_path,
                "list_level": block.list_level,
                "group_id": block.group_id,
                "inline_spans": block.inline_spans,
                "meta_json": block.meta_json,
                "synthesized": block.synthesized,
                "heading_depth": heading_depth,
                "heading_label": heading_label,
                "heading_prefix": heading_prefix,
                "is_noise": is_noise_block(block),
                "link_ranges": collect_link_ranges(block.content, link_targets),
            }
        )

    return ArticleDetail(
        id=article.id,
        project_id=article.project_id,
        candidate_id=article.candidate_id,
        structural_block_id=article.structural_block_id,
        title=article.title,
        slug=article.slug,
        kind=article.kind,
        suggested_section=article.suggested_section,
        description=article.description,
        summary=article.summary,
        status=article.status,
        aliases=article.aliases,
        referenced_by=[
            ArticleLinkSummary.model_validate(row) for row in referenced_by_rows
        ],
        related_articles=[
            ArticleLinkSummary.model_validate(row) for row in related_rows
        ],
        revision_count=article.revision_count,
        blocks=block_payload,
        toc=toc_payload,
        breadcrumb=[
            ArticleBreadcrumbItem.model_validate(item)
            for item in build_article_breadcrumb(article, block_by_id)
        ],
        created_at=article.created_at,
    )


@router.put("/{article_id}/aliases", response_model=ArticleAliasesResponse)
async def update_article_aliases(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    body: ArticleAliasesUpdate,
    db: AsyncSession = Depends(get_db),
):
    article = (
        await db.execute(
            select(Article).where(
                Article.id == article_id,
                Article.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    cleaned: list[str] = []
    seen: set[str] = set()
    for alias in body.aliases:
        value = " ".join(alias.split()).strip()
        if not value:
            continue
        if not is_reasonable_manual_alias(value):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)

    existing_auto_aliases = {
        row[0].casefold()
        for row in (
            await db.execute(
                select(Alias.text).where(
                    Alias.article_id == article.id,
                    Alias.source == AliasSource.AUTO,
                )
            )
        ).all()
    }
    manual_aliases = [
        alias for alias in cleaned if alias.casefold() not in existing_auto_aliases
    ]

    await db.execute(
        delete(Alias).where(
            Alias.article_id == article.id,
            Alias.source == AliasSource.MANUAL,
        )
    )
    if manual_aliases:
        db.add_all(
            [
                Alias(
                    article_id=article.id,
                    text=alias,
                    confidence=1.0,
                    source=AliasSource.MANUAL,
                )
                for alias in manual_aliases
            ]
        )
    await db.flush()
    await refresh_project_links(project_id, db)
    await db.commit()
    result = await db.execute(
        select(Article).where(Article.id == article.id)
    )
    refreshed_article = result.scalar_one()
    await db.refresh(refreshed_article)

    return ArticleAliasesResponse(
        article_id=refreshed_article.id,
        aliases=refreshed_article.aliases or [],
    )
