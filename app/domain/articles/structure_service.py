from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.article_candidate import StructureProposal, ArticleCandidate, ProposalStatus
from app.domain.clustering.center_detector import detect_centers
from app.agents.structure_agent import propose_structure


async def run_structure_proposal(proposal_id: uuid.UUID, db: AsyncSession) -> None:
    result = await db.execute(
        select(StructureProposal).where(StructureProposal.id == proposal_id)
    )
    proposal = result.scalar_one()
    project_id = proposal.project_id

    try:
        candidates = await detect_centers(project_id, db)
        if not candidates:
            logger.warning(f"No candidates for project {project_id}")
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
        logger.info(f"Proposal {proposal.id} ready: {len(enriched)} candidates")

    except Exception as e:
        logger.error(f"Proposal failed: {e}")
        raise