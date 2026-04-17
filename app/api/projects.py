import uuid
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database import get_db
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import ProjectCreate, ProjectResponse
from app.storage.file_storage import file_storage

router = APIRouter(prefix="/projects", tags=["projects"])


def _parse_uuid(raw: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(400, f"Invalid {label}")


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)):
    return await ProjectRepository(db).create(data)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    return await ProjectRepository(db).list_all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    project = await ProjectRepository(db).get(pid)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Permanently delete a project and every downstream artifact:
    sources (with their uploaded files on disk), fragments, structure
    proposals, article candidates, articles and article blocks.

    This is destructive and irreversible — the UI should confirm.
    """
    pid = _parse_uuid(project_id, "project ID")
    try:
        deleted = await ProjectRepository(db).delete(pid)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while deleting project {pid}")
        raise HTTPException(500, f"Database error: {e.__class__.__name__}")

    if not deleted:
        raise HTTPException(404, "Project not found")

    # DB transaction committed — now the upload dir can go. Any failure here
    # leaves stale files behind but the DB is already consistent, so the UI
    # is free to pretend the delete succeeded.
    file_storage.delete_project_dir(pid)

    return Response(status_code=204)