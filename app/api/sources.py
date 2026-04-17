import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source import SourceType
from app.repositories.project_repository import ProjectRepository
from app.repositories.source_repository import SourceRepository
from app.schemas.source import SourceFragmentResponse, SourceResponse
from app.domain.source_service import SourceBusyError, SourceDeletionService
from app.storage.file_storage import file_storage

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])

ALLOWED = {".pdf", ".docx"}


def _parse_uuid(raw: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


@router.post("", response_model=SourceResponse, status_code=202)
async def upload_source(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    pid = _parse_uuid(project_id, "project ID")

    if await ProjectRepository(db).get(pid) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED)}",
        )

    try:
        storage_path = await file_storage.save(file, pid)
    except OSError as e:
        logger.exception(f"Failed to persist upload for project {pid}")
        raise HTTPException(status_code=507, detail=f"Failed to save uploaded file: {e}")

    source_type = SourceType.PDF if ext == ".pdf" else SourceType.DOCX
    repo = SourceRepository(db)

    try:
        source = await repo.create(
            project_id=pid,
            filename=file.filename,
            source_type=source_type,
            storage_path=storage_path,
        )
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while creating source for project {pid}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    await request.app.state.arq_pool.enqueue_job("ingest_source", str(source.id))
    return source


@router.get("", response_model=list[SourceResponse])
async def list_sources(project_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    return await SourceRepository(db).list_by_project(pid)


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(project_id: str, source_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    sid = _parse_uuid(source_id, "source ID")

    source = await SourceRepository(db).get_by_project(pid, sid)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    return source


@router.get("/{source_id}/fragments", response_model=list[SourceFragmentResponse])
async def list_fragments(project_id: str, source_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    sid = _parse_uuid(source_id, "source ID")

    source = await SourceRepository(db).get_by_project(pid, sid)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    return await SourceRepository(db).list_fragments(sid)


@router.delete("/{source_id}", status_code=204)
async def delete_source(project_id: str, source_id: str, db: AsyncSession = Depends(get_db)):
    pid = _parse_uuid(project_id, "project ID")
    sid = _parse_uuid(source_id, "source ID")

    service = SourceDeletionService(db)

    try:
        deleted_source = await service.delete_source_and_prune_articles(
            project_id=pid,
            source_id=sid,
        )
        if deleted_source is None:
            raise HTTPException(status_code=404, detail="Source not found")

        storage_path = deleted_source.storage_path
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SourceBusyError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while deleting source {sid}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    if storage_path:
        file_storage.delete_file(storage_path)

    return Response(status_code=204)