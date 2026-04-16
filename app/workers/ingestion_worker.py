import uuid
from arq.connections import RedisSettings
from loguru import logger

from app.config import settings
from app.database import AsyncSessionLocal
from app.domain.ingestion.ingestion_service import run_ingestion
from app.domain.articles.structure_service import run_structure_proposal


async def ingest_source(ctx: dict, source_id_str: str) -> None:
    """arq task: run full ingestion pipeline for one source."""
    source_id = uuid.UUID(source_id_str)
    logger.info(f"[worker] Starting ingestion for source {source_id}")
    async with AsyncSessionLocal() as db:
        await run_ingestion(source_id, db)
    logger.info(f"[worker] Finished ingestion for source {source_id}")


async def propose_structure(ctx: dict, proposal_id_str: str) -> None:
    proposal_id = uuid.UUID(proposal_id_str)
    logger.info(f"[arq] propose_structure {proposal_id}")
    async with AsyncSessionLocal() as db:
        await run_structure_proposal(proposal_id, db)

class WorkerSettings:
    functions = [ingest_source, propose_structure]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 600