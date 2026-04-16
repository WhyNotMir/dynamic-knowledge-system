import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.source import SourceType, SourceStatus


class SourceResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    source_type: SourceType
    status: SourceStatus
    doc_metadata: dict | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceFragmentResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    project_id: uuid.UUID
    content: str
    element_type: str
    heading_level: int | None
    page_number: int | None
    section_path: str | None
    position_index: int

    model_config = {"from_attributes": True}