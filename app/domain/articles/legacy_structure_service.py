from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chains.structure_agent import propose_structure
from app.domain.clustering.center_detector import detect_centers
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    ProposalStatus,
    StructureProposal,
)


async def run_structure_proposal(proposal_id: uuid.UUID, db: AsyncSession) -> None:
    result = await db.execute(
        select(StructureProposal).where(StructureProposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        logger.error(f"[structure] proposal {proposal_id} not found, aborting")
        return

    # Snapshot identifiers BEFORE any commit/rollback so we can log them
    # without triggering a lazy-load on an expired ORM attribute (which in
    # async context blows up with MissingGreenlet).
    project_id = proposal.project_id

    try:
        candidates = await detect_centers(project_id, db)

        if not candidates:
            proposal.status = ProposalStatus.READY
            await db.flush()
            logger.warning(
                f"[structure] no candidates for project {project_id}; "
                f"proposal {proposal_id} marked READY with 0 candidates"
            )
            return

        enriched_candidates = await propose_structure(candidates)

        for candidate_data in enriched_candidates:
            candidate = ArticleCandidate(
                proposal_id=proposal_id,
                title=candidate_data["proposed_title"],
                suggested_section=candidate_data.get("suggested_section"),
                source_section_path=candidate_data.get("source_section_path"),
                confidence=1.0 if candidate_data["source"] == "section_structure" else 0.7,
            )
            db.add(candidate)
            await db.flush()

            fragments = sorted(
                candidate_data["fragments"],
                key=lambda fragment: fragment.position_index,
            )

            for index, fragment in enumerate(fragments):
                db.add(
                    ArticleCandidateFragment(
                        candidate_id=candidate.id,
                        fragment_id=fragment.id,
                        position_index=index,
                    )
                )

        proposal.status = ProposalStatus.READY
        await db.flush()

        logger.info(
            f"[structure] proposal {proposal_id} READY: "
            f"{len(enriched_candidates)} candidates"
        )

    except Exception as exc:
        logger.exception(f"[structure] proposal {proposal_id} failed: {exc}")
