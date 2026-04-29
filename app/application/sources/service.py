from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.source_service import SourceBusyError, SourceDeletionService
from app.models.source import Source, SourceType
from app.models.source_fragment import SourceFragment
from app.repositories.project_repository import ProjectRepository
from app.repositories.source_repository import SourceRepository
from app.storage.file_storage import file_storage

ALLOWED_SOURCE_EXTENSIONS = {".pdf", ".docx"}


class SourceServiceError(RuntimeError):
    """Base error for source use cases."""


class ProjectNotFoundError(SourceServiceError):
    """Raised when a project does not exist."""


class SourceNotFoundError(SourceServiceError):
    """Raised when a source does not exist in the requested project."""


class UnsupportedSourceTypeError(SourceServiceError):
    """Raised when an uploaded source has an unsupported extension."""


def source_type_from_filename(filename: str) -> SourceType:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_SOURCE_EXTENSIONS:
        raise UnsupportedSourceTypeError(
            f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_SOURCE_EXTENSIONS)}"
        )
    return SourceType.PDF if ext == ".pdf" else SourceType.DOCX


async def ensure_project_exists(project_id: uuid.UUID, db: AsyncSession) -> None:
    if await ProjectRepository(db).get(project_id) is None:
        raise ProjectNotFoundError("Project not found")


async def create_uploaded_source(
    project_id: uuid.UUID,
    *,
    filename: str,
    upload: Any,
    db: AsyncSession,
) -> Source:
    await ensure_project_exists(project_id, db)
    source_type = source_type_from_filename(filename)
    storage_path = await file_storage.save(upload, project_id)
    return await SourceRepository(db).create(
        project_id=project_id,
        filename=filename,
        source_type=source_type,
        storage_path=storage_path,
    )


async def list_sources(project_id: uuid.UUID, db: AsyncSession) -> list[Source]:
    return await SourceRepository(db).list_by_project(project_id)


async def get_source(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession,
) -> Source:
    source = await SourceRepository(db).get_by_project(project_id, source_id)
    if source is None:
        raise SourceNotFoundError("Source not found")
    return source


async def list_source_fragments(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession,
) -> list[SourceFragment]:
    await get_source(project_id, source_id, db)
    return await SourceRepository(db).list_fragments(source_id)


async def get_project_asset_path(
    project_id: uuid.UUID,
    asset_name: str,
    db: AsyncSession,
) -> Path:
    await ensure_project_exists(project_id, db)
    asset_path = file_storage.project_path(project_id, asset_name)
    if not asset_path.is_file():
        raise SourceNotFoundError("Asset not found")
    return asset_path


async def delete_source(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession,
) -> str | None:
    deleted_source = await SourceDeletionService(db).delete_source_and_prune_articles(
        project_id=project_id,
        source_id=source_id,
    )
    if deleted_source is None:
        raise SourceNotFoundError("Source not found")
    return deleted_source.storage_path


def delete_source_file(storage_path: str | None) -> None:
    if storage_path:
        file_storage.delete_file(storage_path)
