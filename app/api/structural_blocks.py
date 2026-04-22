from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import Project
from app.models.structural_block import StructuralBlock
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


async def _require_project(db: AsyncSession, project_id: uuid.UUID) -> None:
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")


async def _get_block(
    db: AsyncSession,
    project_id: uuid.UUID,
    block_id: uuid.UUID,
) -> StructuralBlock:
    result = await db.execute(
        select(StructuralBlock)
        .where(
            StructuralBlock.id == block_id,
            StructuralBlock.project_id == project_id,
        )
        .options(
            selectinload(StructuralBlock.children).selectinload(StructuralBlock.children)
        )
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="Structural block not found")
    return block


@router.get("", response_model=list[StructuralBlockSchema])
async def list_structural_blocks(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    await _require_project(db, pid)

    result = await db.execute(
        select(StructuralBlock)
        .where(
            StructuralBlock.project_id == pid,
            StructuralBlock.parent_id.is_(None),
        )
        .options(
            selectinload(StructuralBlock.children).selectinload(StructuralBlock.children)
        )
        .order_by(StructuralBlock.position_index, StructuralBlock.created_at)
    )
    return list(result.scalars().all())


@router.post("", response_model=StructuralBlockSchema, status_code=201)
async def create_structural_block(
    project_id: str,
    body: StructuralBlockCreate,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    await _require_project(db, pid)

    if body.parent_id is not None:
        parent = await _get_block(db, pid, body.parent_id)
        parent_id = parent.id
    else:
        parent_id = None

    block = StructuralBlock(
        project_id=pid,
        parent_id=parent_id,
        name=body.name,
        description=body.description,
        position_index=body.position_index or 0,
    )
    db.add(block)
    await db.commit()
    return await _get_block(db, pid, block.id)


@router.patch("/{block_id}", response_model=StructuralBlockSchema)
async def update_structural_block(
    project_id: str,
    block_id: str,
    body: StructuralBlockUpdate,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    bid = _parse_uuid(block_id, "block ID")
    block = await _get_block(db, pid, bid)

    if "parent_id" in body.model_fields_set:
        if body.parent_id is not None and body.parent_id != block.id:
            parent = await _get_block(db, pid, body.parent_id)
            block.parent_id = parent.id
        else:
            block.parent_id = None

    if body.name is not None:
        block.name = body.name
    if body.description is not None:
        block.description = body.description
    if body.position_index is not None:
        block.position_index = body.position_index

    await db.commit()
    return await _get_block(db, pid, block.id)


@router.delete("/{block_id}", status_code=204)
async def delete_structural_block(
    project_id: str,
    block_id: str,
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")
    bid = _parse_uuid(block_id, "block ID")
    block = await _get_block(db, pid, bid)
    await db.delete(block)
    await db.commit()
    return Response(status_code=204)
