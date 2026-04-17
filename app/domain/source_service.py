import uuid

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article, ArticleBlock
from app.models.source import Source, SourceStatus
from app.models.source_fragment import SourceFragment


class SourceBusyError(RuntimeError):
    """Raised when a source cannot be deleted because it's currently being processed."""


class SourceDeletionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def delete_source_and_prune_articles(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> Source | None:
        source = await self.db.get(Source, source_id)
        if source is None or source.project_id != project_id:
            return None

        if source.status == SourceStatus.PROCESSING:
            raise SourceBusyError(
                f"Source {source_id} is still being processed and cannot be deleted."
            )

        fragment_ids = list(
            (
                await self.db.execute(
                    select(SourceFragment.id).where(SourceFragment.source_id == source_id)
                )
            ).scalars()
        )

        if fragment_ids:
            affected_article_ids = list(
                (
                    await self.db.execute(
                        select(ArticleBlock.article_id)
                        .where(ArticleBlock.fragment_id.in_(fragment_ids))
                        .distinct()
                    )
                ).scalars()
            )

            await self.db.execute(
                delete(ArticleBlock).where(ArticleBlock.fragment_id.in_(fragment_ids))
            )

            if affected_article_ids:
                orphan_articles_stmt = (
                    delete(Article)
                    .where(Article.id.in_(affected_article_ids))
                    .where(
                        ~exists(
                            select(1).where(ArticleBlock.article_id == Article.id)
                        )
                    )
                )
                await self.db.execute(orphan_articles_stmt)

        await self.db.delete(source)
        await self.db.flush()
        return source