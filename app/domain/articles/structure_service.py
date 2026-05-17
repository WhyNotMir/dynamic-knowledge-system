from __future__ import annotations

import re
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chains.taxonomy_agent import propose_taxonomy_paths
from app.domain.clustering.center_detector import detect_centers
from app.domain.articles.article_text import internal_headings
from app.domain.articles.metadata_service import enrich_or_fallback_candidate_metadata
from app.models.article_candidate import (
    ArticleCandidate,
    ArticleCandidateFragment,
    ProposalStatus,
    StructureProposal,
)
from app.models.article import Article
from app.models.project import Project
from app.models.structural_block import StructuralBlock


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
        project = await db.get(Project, project_id)
        detected_candidates = await detect_centers(project_id, db)
        raw_candidates = await propose_structure(detected_candidates)
        candidates = [
            candidate
            for candidate in raw_candidates
            if candidate.get("fragments")
        ]
        existing_titles = await _existing_article_titles(db, project_id)
        existing_paths = await _structural_block_paths(db, project_id)
        await _enrich_candidate_titles(
            project=project,
            candidates=candidates,
            existing_titles=existing_titles,
            existing_paths=existing_paths,
        )
        await _apply_taxonomy(
            db,
            project=project,
            project_id=project_id,
            candidates=candidates,
            existing_paths=existing_paths,
        )

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
    text = re.sub(r"^(?:[IVXL]+|[A-Z])(?:[.)])\s+", "", text).strip()
    text = re.sub(r"^\d+(?:\.\d+)*(?:[.)])?\s*", "", text).strip()
    return _humanize_title(text) or "Untitled Section"


def _humanize_title(value: str) -> str:
    if not value or not value.isupper():
        return value
    acronyms = {
        "ai",
        "bert",
        "gpt",
        "nlp",
        "qk",
        "ov",
        "rnn",
        "lstm",
        "t5",
        "xlnet",
        "electra",
        "roberta",
        "albert",
    }
    small_words = {"and", "as", "for", "in", "of", "on", "the", "to", "with"}
    words: list[str] = []
    for index, raw in enumerate(value.casefold().split()):
        stripped = raw.strip(":;,")
        punctuation = raw[len(stripped) :] if stripped else ""
        if stripped in acronyms:
            word = stripped.upper()
        elif "-" in stripped:
            word = "-".join(
                part if part in small_words and index > 0 else part[:1].upper() + part[1:]
                for part in stripped.split("-")
                if part
            )
        elif index > 0 and stripped in small_words:
            word = stripped
        else:
            word = stripped[:1].upper() + stripped[1:]
        words.append(f"{word}{punctuation}")
    return " ".join(words)


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


async def _enrich_candidate_titles(
    *,
    project: Project | None,
    candidates: list[dict],
    existing_titles: set[str],
    existing_paths: list[str],
) -> None:
    project_name = project.name if project else "Knowledge Base"
    kb_summary = project.summary if project else None
    used_titles = set(existing_titles)
    for index, candidate in enumerate(candidates):
        fragments = candidate.get("fragments") or []
        payload = {
            **candidate,
            "proposed_title": _candidate_title(candidate, fragments),
            "internal_headings": _internal_headings_from_fragments(fragments),
        }
        metadata = await enrich_or_fallback_candidate_metadata(
            project_name=project_name,
            kb_summary=kb_summary,
            candidate=_candidate_stub(payload["proposed_title"], candidate),
            candidate_payload=payload,
            available_structural_blocks=existing_paths,
        )
        title = _unique_title(metadata.title or payload["proposed_title"], used_titles, candidate, index)
        used_titles.add(title.casefold())
        candidate["proposed_title"] = title
        candidate["description"] = metadata.description
        candidate["internal_headings"] = payload["internal_headings"]
        if metadata.suggested_structural_block:
            candidate["suggested_section"] = metadata.suggested_structural_block


async def _apply_taxonomy(
    db: AsyncSession,
    *,
    project: Project | None,
    project_id: uuid.UUID,
    candidates: list[dict],
    existing_paths: list[str],
) -> None:
    assignments = await propose_taxonomy_paths(
        project_name=project.name if project else "Knowledge Base",
        kb_summary=project.summary if project else None,
        candidates=candidates,
        existing_structural_paths=existing_paths,
    )
    for index, candidate in enumerate(candidates):
        path = assignments.get(index) or candidate.get("suggested_section")
        if not path:
            continue
        await _ensure_structural_block_path(db, project_id=project_id, path=path)
        candidate["suggested_section"] = path


async def _existing_article_titles(db: AsyncSession, project_id: uuid.UUID) -> set[str]:
    result = await db.execute(select(Article.title).where(Article.project_id == project_id))
    return {str(title).casefold() for title in result.scalars().all()}


def _internal_headings_from_fragments(fragments: list) -> list[str]:
    links = [type("CandidateFragment", (), {"fragment": fragment}) for fragment in fragments]
    return internal_headings(links)


def _candidate_stub(title: str, candidate_data: dict):
    return type(
        "CandidateStub",
        (),
        {
            "title": title,
            "suggested_section": candidate_data.get("suggested_section"),
            "source_section_path": candidate_data.get("source_section_path"),
            "candidate_fragments": [],
        },
    )()


def _unique_title(
    value: str,
    used_titles: set[str],
    candidate: dict,
    index: int,
) -> str:
    title = _clean_article_title(value)[:250] or f"Article {index + 1}"
    key = title.casefold()
    if key not in used_titles:
        return title

    source_leaf = _clean_article_title(candidate.get("source_section_path"))[:120]
    if source_leaf and source_leaf.casefold() != key:
        candidate_title = f"{title}: {source_leaf}"[:250]
        if candidate_title.casefold() not in used_titles:
            return candidate_title

    suffix = 2
    while True:
        candidate_title = f"{title} ({suffix})"[:250]
        if candidate_title.casefold() not in used_titles:
            return candidate_title
        suffix += 1


async def _structural_block_paths(db: AsyncSession, project_id: uuid.UUID) -> list[str]:
    result = await db.execute(
        select(StructuralBlock).where(StructuralBlock.project_id == project_id)
    )
    blocks = list(result.scalars().all())
    by_id = {block.id: block for block in blocks}
    paths: list[str] = []
    for block in blocks:
        paths.append(_block_path(block, by_id))
    return sorted(set(paths))


async def _ensure_structural_block_path(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    path: str,
) -> uuid.UUID | None:
    parts = [_clean_article_title(part) for part in path.split(">") if _clean_article_title(part)]
    if not parts:
        return None

    parent_id: uuid.UUID | None = None
    current_id: uuid.UUID | None = None
    for index, part in enumerate(parts[:4]):
        conditions = [
            StructuralBlock.project_id == project_id,
            StructuralBlock.name == part,
        ]
        conditions.append(
            StructuralBlock.parent_id.is_(None)
            if parent_id is None
            else StructuralBlock.parent_id == parent_id
        )
        result = await db.execute(
            select(StructuralBlock).where(*conditions)
        )
        block = result.scalar_one_or_none()
        if block is None:
            block = StructuralBlock(
                project_id=project_id,
                parent_id=parent_id,
                name=part,
                position_index=index,
            )
            db.add(block)
            await db.flush()
        current_id = block.id
        parent_id = block.id
    return current_id


def _block_path(block: StructuralBlock, by_id: dict[uuid.UUID, StructuralBlock]) -> str:
    parts = [block.name]
    parent_id = block.parent_id
    while parent_id and parent_id in by_id:
        parent = by_id[parent_id]
        parts.append(parent.name)
        parent_id = parent.parent_id
    return " > ".join(reversed(parts))
