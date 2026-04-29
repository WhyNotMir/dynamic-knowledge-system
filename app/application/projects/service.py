from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import ProjectCreate
from app.storage.file_storage import file_storage


class ProjectServiceError(RuntimeError):
    """Base error for project use cases."""


class ProjectNotFoundError(ProjectServiceError):
    """Raised when a project does not exist."""


async def create_project(data: ProjectCreate, db: AsyncSession) -> Project:
    return await ProjectRepository(db).create(
        name=data.name,
        description=data.description,
        scope_hint=data.scope_hint,
        settings=data.settings,
    )


async def list_projects(db: AsyncSession) -> list[Project]:
    return await ProjectRepository(db).list_all()


async def get_project(project_id: uuid.UUID, db: AsyncSession) -> Project:
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise ProjectNotFoundError("Project not found")
    return project


async def delete_project(project_id: uuid.UUID, db: AsyncSession) -> None:
    deleted = await ProjectRepository(db).delete(project_id)
    if not deleted:
        raise ProjectNotFoundError("Project not found")


def cleanup_project_files(project_id: uuid.UUID) -> None:
    file_storage.delete_project_dir(project_id)
