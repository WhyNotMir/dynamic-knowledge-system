from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

    project_id = proposal.project_id

    try:
        detected_candidates = await detect_centers(project_id, db)
        raw_candidates = await propose_structure(detected_candidates)
        candidates = [
            candidate
            for candidate in raw_candidates
            if candidate.get("fragments")
        ]

        for candidate_data in candidates:
            fragments = sorted(
                candidate_data["fragments"],
                key=lambda fragment: fragment.position_index,
            )
            if not fragments:
                continue

            title = _candidate_title(candidate_data, fragments)
            candidate = ArticleCandidate(
                proposal_id=proposal_id,
                title=title,
                suggested_section=candidate_data.get("suggested_section")
                or _candidate_section(candidate_data),
                source_section_path=candidate_data.get("source_section_path"),
                confidence=_candidate_confidence(candidate_data),
            )
            db.add(candidate)
            await db.flush()

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
            f"{len(candidates)} candidates"
        )

    except Exception as exc:
        logger.exception(f"[structure] proposal {proposal_id} failed: {exc}")
        raise


def _candidate_title(candidate_data: dict, fragments: list) -> str:
    proposed_title = candidate_data.get("proposed_title")
    if isinstance(proposed_title, str) and proposed_title.strip():
        return _clean_article_title(proposed_title)

    source_path = candidate_data.get("source_section_path")
    if isinstance(source_path, str) and source_path.strip():
        return _clean_article_title(source_path)

    for fragment in fragments:
        if getattr(fragment.element_type, "value", fragment.element_type) == "heading":
            text = " ".join(fragment.content.split()).strip()
            if text:
                return _clean_article_title(text[:140])

    first = next((fragment for fragment in fragments if fragment.content.strip()), None)
    if first is None:
        return "Untitled Section"
    return _clean_article_title(" ".join(first.content.split()).strip()[:96])


def _candidate_section(candidate_data: dict) -> str | None:
    source = candidate_data.get("source")
    if source == "pdf_structure":
        return "Document Sections"
    if source == "orphan_source":
        return "Unassigned Source"
    return "Source Sections"


def _clean_article_title(value: str | None) -> str:
    text = " ".join((value or "").split()).strip()
    text = re.sub(r"^\d+(?:\.\d+)*(?:[.)])?\s*", "", text).strip()
    return text or "Untitled Section"


def _candidate_confidence(candidate_data: dict) -> float:
    source = candidate_data.get("source")
    if source in {"pdf_structure", "section_structure"}:
        return 0.9
    if source == "orphan_source":
        return 0.55
    return 0.7


async def propose_structure(candidates: list[dict]) -> list[dict]:
    """Deterministic structure hook.

    Kept as a patch point for tests and future title polishing, but the active
    pipeline does not ask an LLM to invent or reshape candidates. It only adds
    the title fields that older callers expect.
    """
    enriched: list[dict] = []
    for candidate in candidates:
        fragments = candidate.get("fragments") or []
        if not fragments:
            continue
        enriched.append(
            {
                **candidate,
                "proposed_title": _candidate_title(candidate, fragments),
                "suggested_section": candidate.get("suggested_section")
                or _candidate_section(candidate),
            }
        )
    return enriched
