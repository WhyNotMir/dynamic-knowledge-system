from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine
from app.models.langgraph_checkpoint import LangGraphCheckpoint


async def ensure_langgraph_checkpoint_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            LangGraphCheckpoint.metadata.create_all,
            tables=[LangGraphCheckpoint.__table__],
        )


class SQLAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """Lightweight persistent saver for LangGraph without extra dependencies.

    We keep the implementation intentionally small: checkpoints are stored
    as typed serialized blobs in Postgres, and we retain an in-memory fallback
    for environments where the DB session is intentionally mocked (tests) or
    temporarily unavailable.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
    ) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._fallback = MemorySaver(serde=self.serde)

    async def _use_fallback(self, func_name: str, *args: Any, **kwargs: Any):
        func = getattr(self._fallback, func_name)
        return await func(*args, **kwargs)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        try:
            async with self._session_factory() as db:
                if not hasattr(db, "execute"):
                    return await self._use_fallback("aget_tuple", config)

                stmt = select(LangGraphCheckpoint).where(
                    LangGraphCheckpoint.thread_id == thread_id,
                    LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
                )
                if checkpoint_id:
                    stmt = stmt.where(LangGraphCheckpoint.checkpoint_id == checkpoint_id)
                else:
                    stmt = stmt.order_by(
                        LangGraphCheckpoint.created_at.desc(),
                        LangGraphCheckpoint.checkpoint_id.desc(),
                    ).limit(1)

                row = (await db.execute(stmt)).scalar_one_or_none()
                if row is None:
                    return await self._use_fallback("aget_tuple", config)

                checkpoint = self.serde.loads_typed((row.checkpoint_type, row.checkpoint_payload))
                metadata = self.serde.loads_typed((row.metadata_type, row.metadata_payload))
                return CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": row.thread_id,
                            "checkpoint_ns": row.checkpoint_ns,
                            "checkpoint_id": row.checkpoint_id,
                        }
                    },
                    checkpoint=checkpoint,
                    metadata=metadata,
                    parent_config=(
                        {
                            "configurable": {
                                "thread_id": row.thread_id,
                                "checkpoint_ns": row.checkpoint_ns,
                                "checkpoint_id": row.parent_checkpoint_id,
                            }
                        }
                        if row.parent_checkpoint_id
                        else None
                    ),
                    pending_writes=[],
                )
        except Exception:
            return await self._use_fallback("aget_tuple", config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        del filter, before  # metadata filtering is not needed by the current pipeline

        if config is None:
            return

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        try:
            async with self._session_factory() as db:
                if not hasattr(db, "execute"):
                    async for item in self._fallback.alist(config, limit=limit):
                        yield item
                    return

                stmt = (
                    select(LangGraphCheckpoint)
                    .where(
                        LangGraphCheckpoint.thread_id == thread_id,
                        LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
                    )
                    .order_by(
                        LangGraphCheckpoint.created_at.desc(),
                        LangGraphCheckpoint.checkpoint_id.desc(),
                    )
                )
                if checkpoint_id:
                    stmt = stmt.where(LangGraphCheckpoint.checkpoint_id == checkpoint_id)
                if limit is not None:
                    stmt = stmt.limit(limit)

                rows = (await db.execute(stmt)).scalars().all()
                if not rows:
                    async for item in self._fallback.alist(config, limit=limit):
                        yield item
                    return

                for row in rows:
                    yield CheckpointTuple(
                        config={
                            "configurable": {
                                "thread_id": row.thread_id,
                                "checkpoint_ns": row.checkpoint_ns,
                                "checkpoint_id": row.checkpoint_id,
                            }
                        },
                        checkpoint=self.serde.loads_typed((row.checkpoint_type, row.checkpoint_payload)),
                        metadata=self.serde.loads_typed((row.metadata_type, row.metadata_payload)),
                        parent_config=(
                            {
                                "configurable": {
                                    "thread_id": row.thread_id,
                                    "checkpoint_ns": row.checkpoint_ns,
                                    "checkpoint_id": row.parent_checkpoint_id,
                                }
                            }
                            if row.parent_checkpoint_id
                            else None
                        ),
                        pending_writes=[],
                    )
        except Exception:
            async for item in self._fallback.alist(config, limit=limit):
                yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        payload_type, payload = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_payload = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )

        try:
            async with self._session_factory() as db:
                if not hasattr(db, "execute"):
                    return await self._use_fallback("aput", config, checkpoint, metadata, new_versions)

                await db.execute(
                    delete(LangGraphCheckpoint).where(
                        LangGraphCheckpoint.thread_id == thread_id,
                        LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
                        LangGraphCheckpoint.checkpoint_id == checkpoint_id,
                    )
                )
                db.add(
                    LangGraphCheckpoint(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        checkpoint_id=checkpoint_id,
                        parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
                        checkpoint_type=payload_type,
                        checkpoint_payload=payload,
                        metadata_type=metadata_type,
                        metadata_payload=metadata_payload,
                    )
                )
                await db.commit()
        except Exception:
            return await self._use_fallback("aput", config, checkpoint, metadata, new_versions)

        await self._use_fallback("aput", config, checkpoint, metadata, new_versions)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        # The current ingest/propose graph does not rely on persisted pending
        # writes. We still mirror them into the in-memory fallback so test
        # doubles and any immediate in-process resume path behave like
        # MemorySaver.
        await self._use_fallback(
            "aput_writes",
            config,
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        try:
            async with self._session_factory() as db:
                if hasattr(db, "execute"):
                    await db.execute(
                        delete(LangGraphCheckpoint).where(
                            LangGraphCheckpoint.thread_id == thread_id
                        )
                    )
                    await db.commit()
        except Exception:
            pass

        await self._use_fallback("adelete_thread", thread_id)
