"""Phase 4 strict QA agent with deterministic fallback."""
from __future__ import annotations

import json
import re
import uuid

from pydantic import BaseModel, Field

from app.agents.base import AgentQuotaError, AgentTransientError, get_chat_llm, with_retry
from app.config import settings
from app.domain.qa.retrieval import RetrievalResult


class QAAgentCitation(BaseModel):
    block_id: uuid.UUID
    fragment_id: uuid.UUID
    verbatim_quote: str


class QAAgentOutput(BaseModel):
    answer: str
    citation_block_ids: list[uuid.UUID] = Field(default_factory=list)
    confidence: float = 0.0
    insufficient_context: bool = True


MAX_CONTEXT_BLOCKS = 12
MAX_BLOCK_CHARS = 1200
MIN_CITATION_OVERLAP = 0.08
STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "been",
    "being",
    "but",
    "can",
    "does",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "not",
    "one",
    "only",
    "our",
    "that",
    "the",
    "their",
    "then",
    "there",
    "this",
    "using",
    "was",
    "were",
    "what",
    "when",
    "which",
    "with",
}


def _offline_mode() -> bool:
    return settings.groq_api_key in {"", "test-groq-key", "dummy", "test"}


def _clean_snippet(text: str, *, limit: int = 280) -> str:
    snippet = " ".join(text.split()).strip()
    if len(snippet) <= limit:
        return snippet
    return f"{snippet[: limit - 3].rstrip()}..."


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9-]{2,}", text.lower())
        if token not in STOP_WORDS
    }


def _citation_overlap(answer: str, content: str) -> float:
    answer_tokens = _tokens(answer)
    if not answer_tokens:
        return 0.0
    content_tokens = _tokens(content)
    if not content_tokens:
        return 0.0
    return len(answer_tokens & content_tokens) / len(answer_tokens)


def _fallback(question: str, context: list[RetrievalResult]) -> QAAgentOutput:
    if not context:
        return QAAgentOutput(
            answer=(
                "There is not enough information in this knowledge base to "
                f"answer: {question}"
            ),
            citation_block_ids=[],
            confidence=0.0,
            insufficient_context=True,
        )

    top = context[:1]
    evidence = []
    for item in top:
        prefix = item.article_title
        if item.section_path:
            prefix = f"{prefix} / {item.section_path}"
        evidence.append(f"{prefix}: {_clean_snippet(item.content)}")

    answer = (
        "The knowledge base has relevant evidence, but the LLM answerer is "
        "currently unavailable. Retrieved evidence: "
        + " ".join(evidence)
    )
    return QAAgentOutput(
        answer=answer,
        citation_block_ids=[item.block_id for item in top],
        confidence=max((item.score for item in top), default=0.0),
        insufficient_context=False,
    )


def _extract_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("QAAgent response did not contain a JSON object")
    return json.loads(cleaned[start : end + 1])


def _context_payload(context: list[RetrievalResult]) -> list[dict]:
    return [
        {
            "block_ref": f"B{index}",
            "block_id": str(item.block_id),
            "article_title": item.article_title,
            "section_path": item.section_path,
            "page_number": item.page_number,
            "content": _clean_snippet(item.content, limit=MAX_BLOCK_CHARS),
        }
        for index, item in enumerate(context[:MAX_CONTEXT_BLOCKS], start=1)
    ]


def _normalise_output(
    output: QAAgentOutput,
    context: list[RetrievalResult],
) -> QAAgentOutput:
    valid_block_ids = {item.block_id for item in context}
    seen: set[uuid.UUID] = set()
    by_block_id = {item.block_id: item for item in context}
    citations: list[uuid.UUID] = []
    for block_id in output.citation_block_ids:
        if block_id not in valid_block_ids or block_id in seen:
            continue
        if _citation_overlap(output.answer, by_block_id[block_id].content) < MIN_CITATION_OVERLAP:
            continue
        seen.add(block_id)
        citations.append(block_id)

    confidence = min(max(float(output.confidence), 0.0), 1.0)
    answer = " ".join(output.answer.split()).strip()
    insufficient = bool(output.insufficient_context)

    if insufficient:
        return QAAgentOutput(
            answer=answer
            or "There is not enough information in this knowledge base to answer.",
            citation_block_ids=[],
            confidence=min(confidence, 0.2),
            insufficient_context=True,
        )

    if not citations or not answer:
        return _fallback("", context)

    return QAAgentOutput(
        answer=answer,
        citation_block_ids=citations[:5],
        confidence=confidence,
        insufficient_context=False,
    )


@with_retry
async def _invoke_llm(question: str, context: list[RetrievalResult]) -> QAAgentOutput:
    prompt = (
        "You are a strict retrieval-augmented QA agent. Answer using only the "
        "supplied knowledge-base blocks. Every factual sentence must be "
        "supported by at least one cited block_id. If the blocks do not contain "
        "enough direct evidence, set insufficient_context=true and explain what "
        "is missing. Do not use outside knowledge, do not infer beyond the "
        "blocks, and do not cite block ids that were not supplied. Return JSON "
        "only with keys: answer, citation_block_ids, confidence, "
        "insufficient_context. confidence must be between 0 and 1."
    )
    payload = {
        "question": question,
        "blocks": _context_payload(context),
    }
    try:
        message = await get_chat_llm(temperature=0.0).ainvoke(
            [("system", prompt), ("user", json.dumps(payload, ensure_ascii=False))]
        )
    except Exception as exc:
        name = exc.__class__.__name__
        if name in {"APIConnectionError", "APITimeoutError"}:
            raise AgentTransientError(str(exc))
        raise AgentQuotaError(str(exc))

    raw = getattr(message, "content", str(message)).strip()
    return QAAgentOutput.model_validate(_extract_json(raw))


async def answer_question(question: str, context: list[RetrievalResult]) -> QAAgentOutput:
    if _offline_mode():
        return _fallback(question, context)
    try:
        output = await _invoke_llm(question, context)
    except Exception:
        return _fallback(question, context)

    return _normalise_output(output, context)
