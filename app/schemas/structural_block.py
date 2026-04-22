from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class StructuralBlockCreate(BaseModel):
    name: str
    description: str | None = None
    parent_id: uuid.UUID | None = None
    position_index: int | None = None


class StructuralBlockUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: uuid.UUID | None = None
    position_index: int | None = None


class StructuralBlockSchema(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    description: str | None = None
    position_index: int
    children: list["StructuralBlockSchema"] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


StructuralBlockSchema.model_rebuild()
