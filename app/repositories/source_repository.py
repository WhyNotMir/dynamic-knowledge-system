import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_fragment import SourceFragment


class SourceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        project_id: uuid.UUID,
        filename: str,
        source_type: SourceType,
        storage_path: str,
    ) -> Source:
        source = Source(
            project_id=project_id,
            filename=filename,
            source_type=source_type,
            storage_path=storage_path,
            status=SourceStatus.PENDING,
        )
        self.db.add(source)
        await self.db.commit()
        await self.db.refresh(source)
        return source

    async def get(self, source_id: uuid.UUID) -> Source | None:
        result = await self.db.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[Source]:
        result = await self.db.execute(
            select(Source)
            .where(Source.project_id == project_id)
            .order_by(Source.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_status(
        self, source_id: uuid.UUID, status: SourceStatus, error: str | None = None
    ) -> None:
        source = await self.get(source_id)
        if source:
            source.status = status
            if error:
                source.error_message = error
            await self.db.commit()

    async def update_metadata(self, source_id: uuid.UUID, metadata: dict) -> None:
        source = await self.get(source_id)
        if source:
            source.doc_metadata = metadata
            await self.db.commit()

    async def save_fragments(self, fragments: list[SourceFragment]) -> None:
        self.db.add_all(fragments)
        await self.db.commit()