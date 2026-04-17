import uuid
from datetime import datetime

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    scope_hint: str | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    scope_hint: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}