"""
Pytest configuration for the DKS backend.

What you need to run these tests
--------------------------------
1. A PostgreSQL instance with the `pgvector` extension available. The tests
   will auto-create the extension and schema. Configure via environment:

       TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dks_test

   If not set, the default above is used. `docker-compose up db` from the repo
   root spins up a compatible server; just create a `dks_test` database next
   to it (`createdb -U postgres dks_test`).

2. The backend's own env vars (`OPENAI_API_KEY`, `GROQ_API_KEY`) are set to
   dummy values here *before* `app.config` is imported, because Pydantic
   settings require them. All external calls are mocked by fixtures below,
   so the values are never actually used.

Isolation strategy
------------------
- Schema is created once per pytest session (session fixture `_setup_schema`).
- Between tests, every table is TRUNCATEd with RESTART IDENTITY + CASCADE so
  each test starts from a clean, deterministic state. This is simpler than
  nested-savepoint rollback and plays nicely with code paths that call
  `db.commit()` directly (the worker does this).
- Async engine/session factories are module-scoped to avoid re-creating
  connections on every test.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import AsyncIterator

# Ensure required settings exist before `app.config` imports. Real values are
# never used — OpenAI/Groq calls are mocked.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/dks_test",
    ),
)
# Keep uploads out of the real upload dir; use a tmp subdir.
_UPLOAD_ROOT = Path(os.environ.get("TEST_UPLOAD_DIR", "/tmp/dks-test-uploads"))
_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("UPLOAD_DIR", str(_UPLOAD_ROOT))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import AsyncClient, ASGITransport  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import app AFTER env vars are set.
import app.models  # noqa: F401,E402  — registers every model on Base.metadata
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

# ---------------------------------------------------------------------------
# Event loop — one loop per session so async fixtures share state cleanly.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Database engine and schema
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ["DATABASE_URL"]


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Session-scoped async engine pointing at the test DB."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        # Sanity-check the connection up front so we fail fast with a clear
        # error if Postgres is not reachable.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        pytest.skip(
            f"PostgreSQL test DB at {TEST_DATABASE_URL!r} is unavailable: {e}. "
            "Set TEST_DATABASE_URL to a reachable instance with pgvector."
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_schema(test_engine):
    """Create extension + schema once for the whole session."""
    async with test_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Drop everything first so a previous run's leftovers don't interfere.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Keep the schema at the end so developers can inspect failures; drop on
    # next start. Uncomment below to auto-clean.
    # async with test_engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="session")
async def session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Per-test isolation: truncate all tables.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(test_engine):
    """Reset all tables between tests so each one starts from a clean slate."""
    yield
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    async with test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


# ---------------------------------------------------------------------------
# DB session fixture for tests that touch the DB directly.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI test client with dependency overrides.
# ---------------------------------------------------------------------------


class _FakeArqPool:
    """Captures enqueue_job calls so tests can inspect / drive the worker."""

    def __init__(self):
        self.jobs: list[tuple[str, tuple]] = []

    async def enqueue_job(self, name: str, *args):  # signature matches arq
        self.jobs.append((name, args))

    async def close(self):
        pass


@pytest_asyncio.fixture
async def arq_pool() -> _FakeArqPool:
    """A fresh fake arq pool per test. Exposed so tests can inspect jobs."""
    return _FakeArqPool()


@pytest_asyncio.fixture
async def client(session_factory, arq_pool) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app.

    - get_db is overridden so every request uses the test session factory.
    - arq pool is replaced with `_FakeArqPool` so upload / propose endpoints
      don't need Redis.
    - Lifespan events are skipped (we set app.state manually below).
    """

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    app.state.arq_pool = arq_pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# External-service mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_embeddings(monkeypatch):
    """Return deterministic pseudo-embeddings based on the text hash.

    The vectors are non-zero and all different so KMeans (sklearn) won't
    misbehave if the orphan-clustering path is hit.
    """
    import hashlib

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        dim = 1536
        out: list[list[float]] = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            # repeat the 32-byte digest enough times to fill the dim, then
            # scale to [-1, 1] to look like a real embedding.
            raw = (h * ((dim // 32) + 1))[:dim]
            out.append([(b - 128) / 128.0 for b in raw])
        return out

    monkeypatch.setattr(
        "app.domain.ingestion.embedding_service.embed_texts",
        fake_embed_texts,
    )
    # ingestion_service imports by name, so patch there too.
    monkeypatch.setattr(
        "app.domain.ingestion.ingestion_service.embed_texts",
        fake_embed_texts,
    )
    return fake_embed_texts


@pytest.fixture
def mock_structure_agent(monkeypatch):
    """Bypass the Groq LLM and synthesize titles / sections deterministically."""

    async def fake_propose_structure(candidates: list[dict]) -> list[dict]:
        for i, cand in enumerate(candidates):
            title = (
                cand.get("source_section_path")
                or f"Generated Article {i + 1}"
            )
            cand["proposed_title"] = title[:250]
            cand["suggested_section"] = cand.get("source_section_path") or "General"
        return candidates

    monkeypatch.setattr(
        "app.domain.articles.structure_service.propose_structure",
        fake_propose_structure,
    )
    return fake_propose_structure


@pytest.fixture(autouse=True)
def _auto_mocks(mock_embeddings, mock_structure_agent):
    """Auto-apply external-service mocks for every test. Individual tests
    can still override by using the raw fixtures and re-patching."""
    yield


# ---------------------------------------------------------------------------
# Factories — convenience helpers for building fixtures via the API.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def project(client: AsyncClient) -> dict:
    """Create and return a fresh project."""
    r = await client.post(
        "/projects",
        json={"name": f"Test {uuid.uuid4().hex[:8]}", "description": "pytest"},
    )
    assert r.status_code == 201, r.text
    return r.json()
