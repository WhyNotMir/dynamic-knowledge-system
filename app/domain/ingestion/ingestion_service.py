from __future__ import annotations

import hashlib
import uuid
import base64
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ingestion.embedding_service import embed_texts
from app.domain.ingestion.extractor import extract
from app.domain.ingestion.segmentor import segment
from app.models.source import SourceStatus
from app.models.source_fragment import ElementType, SourceFragment
from app.repositories.source_repository import SourceRepository
from app.storage.file_storage import file_storage


_TYPE_MAP = {
    "heading": ElementType.HEADING,
    "paragraph": ElementType.PARAGRAPH,
    "list_item": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "caption": ElementType.CAPTION,
    "quote": ElementType.QUOTE,
    "code_block": ElementType.CODE_BLOCK,
    "image": ElementType.IMAGE,
    "footnote": ElementType.FOOTNOTE,
    "formula": ElementType.FORMULA,
}


def _content_hash(content: str) -> str:
    normalised = " ".join(content.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


async def _materialize_asset_meta(
    *,
    project_id: uuid.UUID,
    meta_json: dict | None,
) -> dict | None:
    if not meta_json or "image_base64" not in meta_json:
        return meta_json

    encoded = meta_json.pop("image_base64", None)
    if not encoded:
        return meta_json
    ext = meta_json.get("ext", "png")
    storage_path = await file_storage.save_bytes(
        project_id=project_id,
        content=base64.b64decode(encoded),
        ext=ext,
    )
    filename = Path(storage_path).name
    meta_json["image_ref"] = f"/projects/{project_id}/sources/assets/{filename}"
    return meta_json


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

        source_title = next(
            (
                element.content
                for element in elements
                if element.element_type == "heading" and element.heading_level == 1
            ),
            None,
        )

        pages = [element.page_number for element in elements if element.page_number is not None]
        await repo.update_metadata(
            source_id,
            {
                "element_count": len(elements),
                "page_count": max(pages) if pages else None,
            },
        )
        await repo.update_title(source_id, source_title)

        fragments_data = segment(elements)
        logger.info(f"[{source_id}] Segmented into {len(fragments_data)} fragments")

        texts = [fragment.content for fragment in fragments_data]
        embeddings = await embed_texts(texts)
        logger.info(f"[{source_id}] Embedded {len(embeddings)} fragments")

        fragments = []
        for fragment, embedding in zip(fragments_data, embeddings):
            meta_json = await _materialize_asset_meta(
                project_id=source.project_id,
                meta_json=dict(fragment.meta_json) if fragment.meta_json else None,
            )
            fragments.append(
                SourceFragment(
                    source_id=source_id,
                    content=fragment.content,
                    content_hash=_content_hash(fragment.content),
                    element_type=_TYPE_MAP.get(fragment.element_type, ElementType.PARAGRAPH),
                    heading_level=fragment.heading_level,
                    list_level=fragment.list_level,
                    group_id=fragment.group_id,
                    page_number=fragment.page_number,
                    section_path=fragment.section_path,
                    position_index=fragment.position_index,
                    inline_spans=fragment.inline_spans,
                    meta_json=meta_json,
                    embedding=embedding,
                )
            )

        await repo.save_fragments(fragments)
        await repo.update_status(source_id, SourceStatus.DONE)
        logger.info(f"[{source_id}] Ingestion complete")
    except Exception as exc:
        logger.exception(f"[{source_id}] Ingestion failed: {exc}")
        await repo.update_status(source_id, SourceStatus.FAILED, error=str(exc))


async def mark_ingestion_failed(
    source_id: uuid.UUID,
    db: AsyncSession,
    *,
    error: str,
) -> None:
    await SourceRepository(db).update_status(source_id, SourceStatus.FAILED, error=error)
