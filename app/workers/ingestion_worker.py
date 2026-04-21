import uuid

from arq.connections import RedisSettings
from loguru import logger

from app.agents.base import configure_langsmith
from app.config import settings
from app.database import AsyncSessionLocal
from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion


async def _startup(ctx: dict) -> None:
    # Bootstrap LangSmith tracing inside the arq worker process so any
    # agent call from a background job is traced (Phase 0 Slice B).
    configure_langsmith()


async def ingest_source(ctx: dict, source_id_str: str) -> None:
    source_id = uuid.UUID(source_id_str)
    logger.info(f"[worker] Starting ingestion for source {source_id}")

    async with AsyncSessionLocal() as db:
        await run_ingestion(source_id, db)

    logger.info(f"[worker] Finished ingestion for source {source_id}")


async def propose_structure(ctx: dict, proposal_id_str: str) -> None:
    proposal_id = uuid.UUID(proposal_id_str)
    logger.info(f"[worker] Starting structure proposal for {proposal_id}")

    async with AsyncSessionLocal() as db:
        await run_structure_proposal(proposal_id, db)

    logger.info(f"[worker] Finished structure proposal for {proposal_id}")


class WorkerSettings:
    functions = [ingest_source, propose_structure]
    on_startup = _startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 600