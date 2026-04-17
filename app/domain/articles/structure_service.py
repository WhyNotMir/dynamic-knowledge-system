from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.article_candidate import StructureProposal, ArticleCandidate, ProposalStatus
from app.domain.clustering.center_detector import detect_centers
from app.agents.structure_agent import propose_structure


async def run_structure_proposal(proposal_id: uuid.UUID, db: AsyncSession) -> None:
    """Build ArticleCandidates for a StructureProposal and mark it READY.

    Error contract:
      - If the proposal is missing, abort without raising so the worker
        doesn't endlessly retry a lost record.
      - If the pipeline raises, rollback the session so no partial candidates
        are persisted, log, and re-raise so arq marks the job as failed.
        The proposal stays in PENDING — a subsequent /structure/propose call
        will create a fresh one.
    """
    result = await db.execute(
        select(StructureProposal).where(StructureProposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        logger.error(f"[structure] proposal {proposal_id} not found, aborting")
        return

    project_id = proposal.project_id

    try:
        candidates = await detect_centers(project_id, db)

        if not candidates:
            logger.warning(
                f"[structure] no candidates for project {project_id}; "
                f"marking proposal {proposal.id} READY with 0 candidates"
            )
            proposal.status = ProposalStatus.READY
            await db.commit()
            return

        enriched = await propose_structure(candidates)

        for cand in enriched:
            db.add(ArticleCandidate(
                project_id=project_id,
                proposal_id=proposal.id,
                title=cand["proposed_title"],
                suggested_section=cand.get("suggested_section"),
                source_section_path=cand.get("source_section_path"),
                fragment_ids=[str(f.id) for f in cand["fragments"]],
                confidence=1.0 if cand["source"] == "section_structure" else 0.7,
            ))

        proposal.status = ProposalStatus.READY
        await db.commit()
        logger.info(
            f"[structure] proposal {proposal.id} READY: {len(enriched)} candidates"
        )

    except Exception as e:
        logger.exception(
            f"[structure] proposal {proposal.id} failed: {e}"
        )
        await db.rollback()
        raise
