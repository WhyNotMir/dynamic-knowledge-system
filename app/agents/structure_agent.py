from __future__ import annotations
import json
from typing import Any
from groq import AsyncGroq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from loguru import logger

from app.config import settings
from app.agents.prompts.structure_prompts import (
    TITLE_SYSTEM, TITLE_USER,
    HIERARCHY_SYSTEM, HIERARCHY_USER,
)


def _client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.groq_api_key)


def _preview(fragments: list, max_chars: int = 2500) -> str:
    lines, total = [], 0
    for f in fragments:
        lines.append(f.content)
        total += len(f.content)
        if total >= max_chars:
            break
    return "\n".join(lines)


@retry(retry=retry_if_exception_type(Exception),
       stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _propose_title(client: AsyncGroq, content: str, hint: str | None) -> str:
    hint_line = f"Document section: {hint}\n" if hint else ""
    r = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": TITLE_SYSTEM},
            {"role": "user",   "content": TITLE_USER.format(hint=hint_line, content=content)},
        ],
        max_tokens=50,
        temperature=0.3,
    )
    return r.choices[0].message.content.strip().strip('"').strip("'")


@retry(retry=retry_if_exception_type(Exception),
       stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _propose_hierarchy(client: AsyncGroq, titles: list[str]) -> dict[str, list[str]]:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    r = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": HIERARCHY_SYSTEM},
            {"role": "user",   "content": HIERARCHY_USER.format(numbered=numbered)},
        ],
        max_tokens=800,
        temperature=0.3,
    )
    raw = r.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse hierarchy JSON, falling back to flat")
        return {"General": titles}


async def propose_structure(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    client = _client()

    for cand in candidates:
        content = _preview(cand["fragments"])
        cand["proposed_title"] = await _propose_title(
            client, content, cand.get("source_section_path")
        )
        logger.debug(f"Title: {cand['proposed_title']!r}")

    titles = [c["proposed_title"] for c in candidates]
    if len(titles) > 1:
        hier = await _propose_hierarchy(client, titles)
        title_to_section = {t: sec for sec, ts in hier.items() for t in ts}
        for cand in candidates:
            cand["suggested_section"] = title_to_section.get(cand["proposed_title"], "General")
    else:
        for cand in candidates:
            cand["suggested_section"] = cand.get("source_section_path") or "General"

    return candidates