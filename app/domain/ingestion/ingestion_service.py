from __future__ import annotations
from loguru import logger
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ingestion.extractor import extract
from app.domain.ingestion.segmentor import segment
from app.domain.ingestion.embedding_service import embed_texts
from app.models.source import SourceStatus
from app.models.source_fragment import SourceFragment, ElementType
from app.repositories.source_repository import SourceRepository

_TYPE_MAP = {
    "heading": ElementType.HEADING,
    "paragraph": ElementType.PARAGRAPH,
    "list_item": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "caption": ElementType.CAPTION,
}


async def run_ingestion(source_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Pipeline:
      1. Extract structured elements from the file
      2. Segment into meaningful fragments
      3. Generate embeddings
      4. Persist fragments
      5. Update source status → DONE
    """
    repo = SourceRepository(db)
    source = await repo.get(source_id)
    if not source:
        logger.error(f"Source {source_id} not found — aborting ingestion")
        return

    await repo.update_status(source_id, SourceStatus.PROCESSING)

    try:
        # 1. Extract
        logger.info(f"[{source_id}] Extracting {source.storage_path}")
        elements = extract(source.storage_path)
        logger.info(f"[{source_id}] Extracted {len(elements)} elements")

        pages = [e.page_number for e in elements if e.page_number]
        await repo.update_metadata(source_id, {
            "element_count": len(elements),
            "page_count": max(pages) if pages else None,
        })

        # 2. Segment
        fragments_data = segment(elements)
        logger.info(f"[{source_id}] Segmented into {len(fragments_data)} fragments")

        # 3. Embed
        texts = [f.content for f in fragments_data]
        embeddings = await embed_texts(texts)
        logger.info(f"[{source_id}] Embedded {len(embeddings)} fragments")

        # 4. Persist
        models = [
            SourceFragment(
                source_id=source_id,
                project_id=source.project_id,
                content=frag.content,
                element_type=_TYPE_MAP.get(frag.element_type, ElementType.PARAGRAPH),
                heading_level=frag.heading_level,
                page_number=frag.page_number,
                section_path=frag.section_path,
                position_index=frag.position_index,
                embedding=emb,
            )
            for frag, emb in zip(fragments_data, embeddings)
        ]
        await repo.save_fragments(models)
        logger.info(f"[{source_id}] Saved {len(models)} fragments")

        # 5. Done
        await repo.update_status(source_id, SourceStatus.DONE)
        logger.info(f"[{source_id}] Ingestion complete")

    except Exception as exc:
        logger.exception(f"[{source_id}] Ingestion failed: {exc}")
        await repo.update_status(source_id, SourceStatus.FAILED, error=str(exc))