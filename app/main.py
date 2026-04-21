from contextlib import asynccontextmanager
from typing import AsyncIterator

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.base import configure_langsmith
from app.api.articles import router as articles_router
from app.api.projects import router as projects_router
from app.api.sources import router as sources_router
from app.api.structure import router as structure_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Global LangSmith tracing bootstrap (Phase 0 Slice B). No-op unless
    # LANGSMITH_TRACING=true in the environment.
    configure_langsmith()

    app.state.arq_pool = await create_pool(
        RedisSettings.from_dsn(settings.redis_url)
    )
    try:
        yield
    finally:
        await app.state.arq_pool.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dynamic Knowledge System",
        description="Source-preserving, article-based knowledge platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects_router)
    app.include_router(sources_router)
    app.include_router(structure_router)
    app.include_router(articles_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()