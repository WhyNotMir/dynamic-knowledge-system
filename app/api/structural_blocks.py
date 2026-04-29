from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.application.articles.structural_block_service import (
    ProjectNotFoundError,
    StructuralBlockNotFoundError,
    create_structural_block as create_structural_block_service,
    delete_structural_block as delete_structural_block_service,
    list_structural_blocks as list_structural_blocks_service,
    update_structural_block as update_structural_block_service,
)
from app.schemas.structural_block import (
    StructuralBlockCreate,
    StructuralBlockSchema,
    StructuralBlockUpdate,
)

router = APIRouter(
    prefix="/projects/{project_id}/structural-blocks",
    tags=["structural-blocks"],
)


def _parse_uuid(raw: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


def _not_found(exc: ProjectNotFoundError | StructuralBlockNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("", response_model=list[StructuralBlockSchema])
async def list_structural_blocks(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    try:
        return await list_structural_blocks_service(db, pid)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("", response_model=StructuralBlockSchema, status_code=201)
async def create_structural_block(
    project_id: str,
    body: StructuralBlockCreate,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    try:
        block = await create_structural_block_service(db, pid, body)
        await db.commit()
        return block
    except (ProjectNotFoundError, StructuralBlockNotFoundError) as exc:
        await db.rollback()
        raise _not_found(exc) from exc


@router.patch("/{block_id}", response_model=StructuralBlockSchema)
async def update_structural_block(
    project_id: str,
    block_id: str,
    body: StructuralBlockUpdate,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    bid = _parse_uuid(block_id, "block ID")
    try:
        block = await update_structural_block_service(db, pid, bid, body)
        await db.commit()
        return block
    except StructuralBlockNotFoundError as exc:
        await db.rollback()
        raise _not_found(exc) from exc


@router.delete("/{block_id}", status_code=204)
async def delete_structural_block(
    project_id: str,
    block_id: str,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    bid = _parse_uuid(block_id, "block ID")
    try:
        await delete_structural_block_service(db, pid, bid)
        await db.commit()
    except StructuralBlockNotFoundError as exc:
        await db.rollback()
        raise _not_found(exc) from exc
    return Response(status_code=204)
