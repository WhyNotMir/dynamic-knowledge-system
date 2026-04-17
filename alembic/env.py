from logging.config import fileConfig

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import Base
import app.models  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url(async_url: str) -> str:
    return async_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def run_migrations_offline() -> None:
    url = _sync_database_url(settings.database_url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    def do_run_migrations(sync_connection):
        context.configure(
            connection=sync_connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    async def run_async_migrations():
        engine = create_async_engine(
            settings.database_url,
            poolclass=pool.NullPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(do_run_migrations)
        await engine.dispose()

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()