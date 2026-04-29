import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.projects.service import (
    ProjectNotFoundError,
    cleanup_project_files,
    create_project as create_project_service,
    delete_project as delete_project_service,
    get_project as get_project_service,
    list_projects as list_projects_service,
)
from app.database import get_db
from app.schemas.project import ProjectCreate, ProjectResponse

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
    try:
        project = await create_project_service(data, db)
        await db.commit()
        return project
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception("DB error while creating project")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    return await list_projects_service(db)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project_uuid = _parse_uuid(project_id, "project ID")
    try:
        return await get_project_service(project_uuid, db)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    project_uuid = _parse_uuid(project_id, "project ID")
    try:
        await delete_project_service(project_uuid, db)
        await db.commit()
        cleanup_project_files(project_uuid)
    except ProjectNotFoundError:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Project not found")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while deleting project {project_uuid}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    return Response(status_code=204)
