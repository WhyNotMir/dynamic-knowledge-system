from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.application.inbox.service import list_inbox_items as list_inbox_items_service
from app.schemas.inbox import InboxItemSchema


router = APIRouter(prefix="/projects/{project_id}/inbox", tags=["inbox"])


@router.get("", response_model=list[InboxItemSchema])
async def list_inbox_items(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await list_inbox_items_service(project_id, db)
