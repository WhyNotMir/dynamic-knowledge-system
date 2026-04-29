from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.application.linking.graph_payload_service import (
    ProjectNotFoundError,
    get_project_graph_payload,
)
from app.schemas.graph import GraphPayloadSchema

router = APIRouter(prefix="/projects/{project_id}/graph", tags=["graph"])


@router.get("", response_model=GraphPayloadSchema)
async def get_project_graph(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_project_graph_payload(project_id, db)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
