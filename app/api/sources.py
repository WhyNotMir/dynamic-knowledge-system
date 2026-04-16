import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.source import SourceType
from app.models.source_fragment import SourceFragment
from app.repositories.project_repository import ProjectRepository
from app.repositories.source_repository import SourceRepository
from app.schemas.source import SourceResponse, SourceFragmentResponse
from app.storage.file_storage import file_storage

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])
ALLOWED = {".pdf", ".docx"}


@router.post("", response_model=SourceResponse, status_code=202)
async def upload_source(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(400, "Invalid project ID")

    if not await ProjectRepository(db).get(pid):
        raise HTTPException(404, "Project not found")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {ALLOWED}")

    storage_path = await file_storage.save(file, pid)
    source_type = SourceType.PDF if ext == ".pdf" else SourceType.DOCX
    source = await SourceRepository(db).create(pid, file.filename, source_type, storage_path)

    await request.app.state.arq_pool.enqueue_job("ingest_source", str(source.id))

    return source


@router.get("", response_model=list[SourceResponse])
async def list_sources(project_id: str, db: AsyncSession = Depends(get_db)):
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(400, "Invalid project ID")
    return await SourceRepository(db).list_by_project(pid)


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(project_id: str, source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(400, "Invalid source ID")
    source = await SourceRepository(db).get(sid)
    if not source:
        raise HTTPException(404, "Source not found")
    return source


@router.get("/{source_id}/fragments", response_model=list[SourceFragmentResponse])
async def list_fragments(project_id: str, source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(400, "Invalid source ID")
    result = await db.execute(
        select(SourceFragment)
        .where(SourceFragment.source_id == sid)
        .order_by(SourceFragment.position_index)
    )
    return list(result.scalars().all())