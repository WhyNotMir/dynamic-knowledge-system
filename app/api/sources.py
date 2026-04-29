import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.sources.service import (
    ProjectNotFoundError,
    SourceNotFoundError,
    UnsupportedSourceTypeError,
    create_uploaded_source,
    delete_source as delete_source_service,
    delete_source_file,
    get_project_asset_path,
    get_source as get_source_service,
    list_source_fragments,
    list_sources as list_sources_service,
)
from app.database import get_db
from app.schemas.source import SourceFragmentResponse, SourceResponse
from app.domain.source_service import SourceBusyError

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=202)
async def upload_source(
    project_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    try:
        source = await create_uploaded_source(
            project_id,
            filename=file.filename,
            upload=file,
            db=db,
        )
        await db.commit()
    except ProjectNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except UnsupportedSourceTypeError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as e:
        await db.rollback()
        logger.exception(f"Failed to persist upload for project {project_id}")
        raise HTTPException(status_code=507, detail=f"Failed to save uploaded file: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while creating source for project {project_id}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    await request.app.state.arq_pool.enqueue_job("ingest_source", str(source.id))
    return source


@router.get("", response_model=list[SourceResponse])
async def list_sources(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await list_sources_service(project_id, db)


@router.get("/assets/{asset_name}")
async def get_project_asset(project_id: uuid.UUID, asset_name: str, db: AsyncSession = Depends(get_db)):
    try:
        return FileResponse(await get_project_asset_path(project_id, asset_name, db))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(project_id: uuid.UUID, source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await get_source_service(project_id, source_id, db)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{source_id}/fragments", response_model=list[SourceFragmentResponse])
async def list_fragments(project_id: uuid.UUID, source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await list_source_fragments(project_id, source_id, db)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{source_id}", status_code=204)
async def delete_source(project_id: uuid.UUID, source_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    try:
        storage_path = await delete_source_service(project_id, source_id, db)
        await db.commit()
    except SourceNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except SourceBusyError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"DB error while deleting source {source_id}")
        raise HTTPException(status_code=500, detail=f"Database error: {e.__class__.__name__}")

    delete_source_file(storage_path)

    return Response(status_code=204)
