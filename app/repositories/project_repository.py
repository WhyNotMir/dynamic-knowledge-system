import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.schemas.project import ProjectCreate


class ProjectRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: ProjectCreate) -> Project:
        project = Project(
            name=data.name,
            description=data.description,
            scope_hint=data.scope_hint,
        )
        self.db.add(project)
        await self.db.flush()
        await self.db.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Project]:
        result = await self.db.execute(
            select(Project).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, project_id: uuid.UUID) -> bool:
        project = await self.get(project_id)
        if project is None:
            return False

        await self.db.delete(project)
        await self.db.flush()
        return True