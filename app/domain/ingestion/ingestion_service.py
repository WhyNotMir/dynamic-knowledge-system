from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ingestion.embedding_service import embed_texts
from app.domain.ingestion.extractor import extract
from app.domain.ingestion.segmentor import segment
from app.models.source import SourceStatus
from app.models.source_fragment import ElementType, SourceFragment
from app.repositories.source_repository import SourceRepository


_TYPE_MAP = {
    "heading": ElementType.HEADING,
    "paragraph": ElementType.PARAGRAPH,
    "list_item": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "caption": ElementType.CAPTION,
}


async def run_ingestion(source_id: uuid.UUID, db: AsyncSession) -> None:
    repo = SourceRepository(db)
    source = await repo.get(source_id)
    if source is None:
        logger.error(f"Source {source_id} not found — aborting ingestion")
        return

    try:
        await repo.update_status(source_id, SourceStatus.PROCESSING)

        logger.info(f"[{source_id}] Extracting {source.storage_path}")
        elements = extract(source.storage_path)
        logger.info(f"[{source_id}] Extracted {len(elements)} elements")

        pages = [element.page_number for element in elements if element.page_number is not None]
        await repo.update_metadata(
            source_id,
            {
                "element_count": len(elements),
                "page_count": max(pages) if pages else None,
            },
        )

        fragments_data = segment(elements)
        logger.info(f"[{source_id}] Segmented into {len(fragments_data)} fragments")

        texts = [fragment.content for fragment in fragments_data]
        embeddings = await embed_texts(texts)
        logger.info(f"[{source_id}] Embedded {len(embeddings)} fragments")

        fragments = [
            SourceFragment(
                source_id=source_id,
                content=fragment.content,
                element_type=_TYPE_MAP.get(fragment.element_type, ElementType.PARAGRAPH),
                heading_level=fragment.heading_level,
                page_number=fragment.page_number,
                section_path=fragment.section_path,
                position_index=fragment.position_index,
                embedding=embedding,
            )
            for fragment, embedding in zip(fragments_data, embeddings)
        ]

        await repo.save_fragments(fragments)
        await repo.update_status(source_id, SourceStatus.DONE)

        await db.commit()
        logger.info(f"[{source_id}] Ingestion complete")

    except Exception as exc:
        await db.rollback()
        logger.exception(f"[{source_id}] Ingestion failed: {exc}")

        # Record the failure in the DB so the UI can show a clear error and
        # the user can re-upload. We do NOT re-raise: extraction errors are
        # deterministic (bad file), so arq retries would just loop forever.
        try:
            await repo.update_status(source_id, SourceStatus.FAILED, error=str(exc))
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(f"[{source_id}] Failed to persist FAILED status")