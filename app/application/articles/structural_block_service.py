from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.project import Project
from app.models.structural_block import StructuralBlock
from app.schemas.structural_block import StructuralBlockCreate, StructuralBlockUpdate


class StructuralBlockServiceError(RuntimeError):
    """Base error for structural-block operations."""


class ProjectNotFoundError(StructuralBlockServiceError):
    """Raised when a project does not exist."""


class StructuralBlockNotFoundError(StructuralBlockServiceError):
    """Raised when a structural block does not exist in the requested project."""


async def ensure_project_exists(db: AsyncSession, project_id: uuid.UUID) -> None:
    if await db.get(Project, project_id) is None:
        raise ProjectNotFoundError("Project not found")


async def get_structural_block(
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
        raise StructuralBlockNotFoundError("Structural block not found")
    return block


async def list_structural_blocks(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[StructuralBlock]:
    await ensure_project_exists(db, project_id)
    result = await db.execute(
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
    return list(result.scalars().all())


async def create_structural_block(
    db: AsyncSession,
    project_id: uuid.UUID,
    body: StructuralBlockCreate,
) -> StructuralBlock:
    await ensure_project_exists(db, project_id)

    parent_id = None
    if body.parent_id is not None:
        parent = await get_structural_block(db, project_id, body.parent_id)
        parent_id = parent.id

    block = StructuralBlock(
        project_id=project_id,
        parent_id=parent_id,
        name=body.name,
        description=body.description,
        position_index=body.position_index or 0,
    )
    db.add(block)
    await db.flush()
    return await get_structural_block(db, project_id, block.id)


async def update_structural_block(
    db: AsyncSession,
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: StructuralBlockUpdate,
) -> StructuralBlock:
    block = await get_structural_block(db, project_id, block_id)

    if "parent_id" in body.model_fields_set:
        if body.parent_id is not None and body.parent_id != block.id:
            parent = await get_structural_block(db, project_id, body.parent_id)
            block.parent_id = parent.id
        else:
            block.parent_id = None

    if body.name is not None:
        block.name = body.name
    if body.description is not None:
        block.description = body.description
    if body.position_index is not None:
        block.position_index = body.position_index

    await db.flush()
    return await get_structural_block(db, project_id, block.id)


async def delete_structural_block(
    db: AsyncSession,
    project_id: uuid.UUID,
    block_id: uuid.UUID,
) -> None:
    block = await get_structural_block(db, project_id, block_id)
    await db.delete(block)
    await db.flush()
