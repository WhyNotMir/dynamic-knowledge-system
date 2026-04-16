from __future__ import annotations
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from loguru import logger

from app.config import settings

BATCH_SIZE = 100
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def _embed_batch(client: AsyncOpenAI, batch: list[str]) -> list[list[float]]:
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=batch,
        dimensions=settings.embedding_dimensions,
    )
    return [item.embedding for item in response.data]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts in batches with automatic retry. Returns one vector per input."""
    client = _get_client()
    results: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i: i + BATCH_SIZE]
        try:
            batch_result = await _embed_batch(client, batch)
            results.extend(batch_result)
            logger.debug(f"Embedded batch {i // BATCH_SIZE + 1} ({len(batch)} texts)")
        except Exception as exc:
            logger.error(f"Embedding batch {i // BATCH_SIZE + 1} failed after retries: {exc}")
            results.extend([0.0] * settings.embedding_dimensions for _ in batch)

    return results