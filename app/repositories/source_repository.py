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
        await self.db.flush()
        await self.db.refresh(source)
        return source

    async def get(self, source_id: uuid.UUID) -> Source | None:
        result = await self.db.execute(
            select(Source).where(Source.id == source_id)
        )
        return result.scalar_one_or_none()

    async def get_by_project(self, project_id: uuid.UUID, source_id: uuid.UUID) -> Source | None:
        result = await self.db.execute(
            select(Source).where(
                Source.id == source_id,
                Source.project_id == project_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[Source]:
        result = await self.db.execute(
            select(Source)
            .where(Source.project_id == project_id)
            .order_by(Source.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        source_id: uuid.UUID,
        status: SourceStatus,
        error: str | None = None,
    ) -> Source | None:
        source = await self.get(source_id)
        if source is None:
            return None

        source.status = status
        source.error_message = error
        await self.db.flush()
        return source

    async def update_metadata(self, source_id: uuid.UUID, metadata: dict) -> Source | None:
        source = await self.get(source_id)
        if source is None:
            return None

        source.doc_metadata = metadata
        await self.db.flush()
        return source

    async def update_title(self, source_id: uuid.UUID, title: str | None) -> Source | None:
        source = await self.get(source_id)
        if source is None:
            return None

        source.title = title
        await self.db.flush()
        return source

    async def save_fragments(self, fragments: list[SourceFragment]) -> None:
        self.db.add_all(fragments)
        await self.db.flush()

    async def list_fragments(self, source_id: uuid.UUID) -> list[SourceFragment]:
        result = await self.db.execute(
            select(SourceFragment)
            .where(SourceFragment.source_id == source_id)
            .order_by(SourceFragment.position_index)
        )
        return list(result.scalars().all())
