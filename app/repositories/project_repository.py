import uuid
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.article import Article, ArticleBlock
from app.models.article_candidate import ArticleCandidate, StructureProposal
from app.models.project import Project
from app.models.source import Source
from app.models.source_fragment import SourceFragment
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
        await self.db.commit()
        await self.db.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        result = await self.db.execute(select(Project).where(Project.id == project_id))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Project]:
        result = await self.db.execute(
            select(Project).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, project_id: uuid.UUID) -> bool:
        """Wipe a project and every downstream row.

        The schema has a few FKs without ON DELETE CASCADE at the DB level
        (article_blocks.fragment_id, article_candidates.project_id, etc.),
        so we delete children → parents explicitly in the right order.
        Runs in a single transaction — either the whole project is gone or
        nothing is.

        Returns True on deletion, False if the project didn't exist.
        """
        project = await self.get(project_id)
        if project is None:
            return False

        # 1. article_blocks reference articles (their parent) AND fragments.
        #    articles is what we'll delete, so the article-side FK is satisfied
        #    by cascading-through-articles; but the fragment-side FK means we
        #    must drop the blocks before the fragments go away.
        article_ids_subq = select(Article.id).where(Article.project_id == project_id)
        await self.db.execute(
            delete(ArticleBlock).where(ArticleBlock.article_id.in_(article_ids_subq))
        )

        # 2. articles themselves.
        await self.db.execute(delete(Article).where(Article.project_id == project_id))

        # 3. candidates, then proposals.
        await self.db.execute(
            delete(ArticleCandidate).where(ArticleCandidate.project_id == project_id)
        )
        await self.db.execute(
            delete(StructureProposal).where(StructureProposal.project_id == project_id)
        )

        # 4. source_fragments, then sources.
        await self.db.execute(
            delete(SourceFragment).where(SourceFragment.project_id == project_id)
        )
        await self.db.execute(delete(Source).where(Source.project_id == project_id))

        # 5. the project row itself.
        await self.db.execute(delete(Project).where(Project.id == project_id))

        await self.db.commit()
        return True