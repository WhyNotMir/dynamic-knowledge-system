from contextlib import asynccontextmanager
from fastapi import FastAPI
from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings
import app.models
from app.api.projects import router as projects_router
from app.api.sources import router as sources_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.arq_pool.close()


app = FastAPI(
    title="Dynamic Knowledge System",
    description="Source-preserving, article-based knowledge platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(projects_router)
app.include_router(sources_router)


@app.get("/health")
async def health():
    return {"status": "ok"}