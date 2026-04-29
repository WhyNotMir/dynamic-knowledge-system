from __future__ import annotations

from app.agents.chains.title_agent import TitleAgentOutput, enrich_candidate_metadata
from app.domain.articles.article_text import candidate_description
from app.models.article_candidate import ArticleCandidate, ArticleCandidateFragment


def fallback_metadata(
    candidate: ArticleCandidate,
    candidate_fragments: list[ArticleCandidateFragment],
    available_structural_blocks: list[str],
) -> TitleAgentOutput:
    suggested_block = None
    for value in (candidate.suggested_section, candidate.source_section_path):
        if value and value in available_structural_blocks:
            suggested_block = value
            break

    return TitleAgentOutput(
        title=candidate.title,
        description=candidate_description(candidate_fragments),
        suggested_structural_block=suggested_block,
    )


async def enrich_or_fallback_candidate_metadata(
    *,
    project_name: str,
    kb_summary: str | None,
    candidate: ArticleCandidate,
    candidate_payload: dict,
    available_structural_blocks: list[str],
) -> TitleAgentOutput:
    try:
        return await enrich_candidate_metadata(
            project_name=project_name,
            kb_summary=kb_summary,
            candidate=candidate_payload,
            available_blocks=available_structural_blocks,
        )
    except Exception:
        return fallback_metadata(
            candidate,
            candidate.candidate_fragments,
            available_structural_blocks,
        )
