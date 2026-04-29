from __future__ import annotations

import re
import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    normalised = unicodedata.normalize("NFKD", title)
    ascii_str = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_str.lower().strip()
    slug = _SLUG_NON_ALNUM.sub("-", lowered).strip("-")
    if not slug:
        slug = f"article-{uuid.uuid4().hex[:8]}"
    return slug[:200]


async def unique_slug_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    base_slug: str,
    taken: set[str],
) -> str:
    candidate = base_slug
    if candidate not in taken:
        existing = await db.execute(
            select(Article.id).where(
                Article.project_id == project_id,
                Article.slug == candidate,
            )
        )
        if existing.scalar_one_or_none() is None:
            taken.add(candidate)
            return candidate

    for _ in range(5):
        suffix = uuid.uuid4().hex[:6]
        candidate = f"{base_slug}-{suffix}"[:200]
        if candidate in taken:
            continue
        existing = await db.execute(
            select(Article.id).where(
                Article.project_id == project_id,
                Article.slug == candidate,
            )
        )
        if existing.scalar_one_or_none() is None:
            taken.add(candidate)
            return candidate

    raise RuntimeError(
        f"Unable to find a unique slug for base '{base_slug}' in project {project_id}"
    )
