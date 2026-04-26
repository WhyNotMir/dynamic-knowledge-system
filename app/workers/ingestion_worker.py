import uuid

from arq.connections import RedisSettings
from loguru import logger

from app.agents.base import configure_langsmith
from app.agents.graph.checkpointer import ensure_langgraph_checkpoint_schema
from app.agents.graph.ingest_pipeline import (
    create_ingest_pipeline_state,
    resolve_job_project_id,
    run_ingest_pipeline,
)
from app.config import settings


async def _startup(ctx: dict) -> None:
    # Bootstrap LangSmith tracing inside the arq worker process so any
    # agent call from a background job is traced (Phase 0 Slice B).
    configure_langsmith()
    await ensure_langgraph_checkpoint_schema()


async def ingest_source(ctx: dict, source_id_str: str) -> None:
    source_id = uuid.UUID(source_id_str)
    logger.info(f"[worker] Starting ingestion for source {source_id}")

    project_id = await resolve_job_project_id(
        job_kind="ingest_source",
        source_id=source_id,
    )
    if project_id is None:
        logger.error(f"[worker] Could not resolve project for source {source_id}")
        return

    state = create_ingest_pipeline_state(
        project_id=project_id,
        job_kind="ingest_source",
        source_id=source_id,
    )
    result = await run_ingest_pipeline(state)
    if result["status"] == "failed":
        logger.warning(
            f"[worker] Ingestion graph failed for source {source_id}: "
            f"{(result.get('result') or {}).get('error')}"
        )

    logger.info(f"[worker] Finished ingestion for source {source_id}")


async def propose_structure(ctx: dict, proposal_id_str: str) -> None:
    proposal_id = uuid.UUID(proposal_id_str)
    logger.info(f"[worker] Starting structure proposal for {proposal_id}")

    project_id = await resolve_job_project_id(
        job_kind="propose_structure",
        proposal_id=proposal_id,
    )
    if project_id is None:
        logger.error(f"[worker] Could not resolve project for proposal {proposal_id}")
        return

    state = create_ingest_pipeline_state(
        project_id=project_id,
        job_kind="propose_structure",
        proposal_id=proposal_id,
    )
    result = await run_ingest_pipeline(state)
    if result["status"] == "failed":
        logger.warning(
            f"[worker] Structure graph failed for proposal {proposal_id}: "
            f"{(result.get('result') or {}).get('error')}"
        )

    logger.info(f"[worker] Finished structure proposal for {proposal_id}")


class WorkerSettings:
    functions = [ingest_source, propose_structure]
    on_startup = _startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 600
