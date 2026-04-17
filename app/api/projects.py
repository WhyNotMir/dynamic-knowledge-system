import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.repositories.project_repository import ProjectRepository
from app.schemas.project import ProjectCreate, ProjectResponse
from app.storage.file_storage import file_storage

router = APIRouter(prefix="/projects", tags=["projects"])


def _parse_uuid(raw: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
):
    repo = ProjectRepository(db)
    try:
        project = await repo.create(data)
        await db.commit()
        return project
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception("DB error while creating project")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    return await ProjectRepository(db).list_all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    project = await ProjectRepository(db).get(pid)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    repo = ProjectRepository(db)

    try:
        deleted = await repo.delete(pid)
        if not deleted:
            raise HTTPException(status_code=404, detail="Project not found")

        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while deleting project {pid}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    file_storage.delete_project_dir(pid)
    return Response(status_code=204)