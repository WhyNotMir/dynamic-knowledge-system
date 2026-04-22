from __future__ import annotations

import uuid

from app.agents.graph.ingest_pipeline import (
    build_ingest_pipeline_graph,
    build_ingest_pipeline_config,
    create_ingest_pipeline_state,
    get_ingest_pipeline_state_snapshot,
    run_ingest_pipeline,
)
from app.agents.graph.runtime import append_event, persist_buffered_events
from app.models.ingestion_event import IngestionEventLevel


def test_ingest_pipeline_graph_compiles():
    graph = build_ingest_pipeline_graph()
    assert graph is not None
    assert graph.checkpointer is not None


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def add_all(self, rows):
        self.added.extend(rows)

    async def commit(self):
        self.committed = True


async def test_ingest_pipeline_runs_real_ingest_node(monkeypatch):
    calls: list[tuple[str, object]] = []
    persist_session = _FakeSession()

    async def fake_run_ingestion(source_id, db):
        calls.append(("ingest", source_id))

    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.run_ingestion",
        fake_run_ingestion,
    )
    monkeypatch.setattr(
        "app.agents.graph.runtime.AsyncSessionLocal",
        lambda: persist_session,
    )

    project_id = uuid.uuid4()
    source_id = uuid.uuid4()
    state = create_ingest_pipeline_state(
        project_id=project_id,
        job_kind="ingest_source",
        source_id=source_id,
    )

    result = await run_ingest_pipeline(state)

    assert result["status"] == "completed"
    assert result["result"]["completed_node"] == "ingest_source"
    assert calls == [("ingest", source_id)]
    assert result["events"][-1]["node"] == "completed"


async def test_ingest_pipeline_runs_real_propose_node(monkeypatch):
    calls: list[tuple[str, object]] = []
    persist_session = _FakeSession()

    async def fake_run_structure_proposal(proposal_id, db):
        calls.append(("propose", proposal_id))

    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.run_structure_proposal",
        fake_run_structure_proposal,
    )
    monkeypatch.setattr(
        "app.agents.graph.runtime.AsyncSessionLocal",
        lambda: persist_session,
    )

    project_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    state = create_ingest_pipeline_state(
        project_id=project_id,
        job_kind="propose_structure",
        proposal_id=proposal_id,
    )

    result = await run_ingest_pipeline(state)

    assert result["status"] == "completed"
    assert result["result"]["completed_node"] == "propose_structure"
    assert calls == [("propose", proposal_id)]
    assert result["events"][0]["node"] == "propose_structure"


async def test_ingest_pipeline_marks_failure_when_service_raises(monkeypatch):
    persist_session = _FakeSession()

    async def fake_run_ingestion(source_id, db):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.run_ingestion",
        fake_run_ingestion,
    )
    monkeypatch.setattr(
        "app.agents.graph.runtime.AsyncSessionLocal",
        lambda: persist_session,
    )

    state = create_ingest_pipeline_state(
        project_id=uuid.uuid4(),
        job_kind="ingest_source",
        source_id=uuid.uuid4(),
    )

    result = await run_ingest_pipeline(state)

    assert result["status"] == "failed"
    assert result["result"]["failed_node"] == "ingest_source"
    assert "boom" in result["result"]["error"]
    assert result["events"][-1]["node"] == "failed"


async def test_resolve_job_project_id_for_source(monkeypatch):
    project_id = uuid.uuid4()

    class _FakeResult:
        def scalar_one_or_none(self):
            return project_id

    class _FakeLookupSession(_FakeSession):
        async def execute(self, stmt):
            return _FakeResult()

    from app.agents.graph.ingest_pipeline import resolve_job_project_id

    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.AsyncSessionLocal",
        lambda: _FakeLookupSession(),
    )

    resolved = await resolve_job_project_id(
        job_kind="ingest_source",
        source_id=uuid.uuid4(),
    )

    assert resolved == project_id


async def test_persist_buffered_events_writes_ingestion_events(monkeypatch):
    fake_session = _FakeSession()

    monkeypatch.setattr(
        "app.agents.graph.runtime.AsyncSessionLocal",
        lambda: fake_session,
    )

    state = {
        "project_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "events": [],
    }
    state["events"] = append_event(
        state,
        node="ingest_source",
        message="entered",
        payload={"source_id": "abc"},
    )
    state["events"] = append_event(
        state,
        node="failed",
        message="boom",
        payload={"error": "bad"},
        level=IngestionEventLevel.ERROR.value,
    )

    persisted = await persist_buffered_events(state)

    assert persisted == 2
    assert fake_session.committed is True
    assert len(fake_session.added) == 2
    assert fake_session.added[0].agent_name == "ingest_pipeline"
    assert fake_session.added[0].node == "ingest_source"
    assert fake_session.added[1].level == IngestionEventLevel.ERROR


async def test_run_ingest_pipeline_persists_events(monkeypatch):
    fake_session = _FakeSession()
    calls = []

    async def fake_run_ingestion(source_id, db):
        calls.append(("ingest", source_id))

    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.agents.graph.runtime.AsyncSessionLocal",
        lambda: fake_session,
    )
    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.run_ingestion",
        fake_run_ingestion,
    )

    state = create_ingest_pipeline_state(
        project_id=uuid.uuid4(),
        job_kind="ingest_source",
        source_id=uuid.uuid4(),
    )

    result = await run_ingest_pipeline(state)

    assert calls
    assert result["result"]["checkpoint_id"] is not None
    assert result["result"]["persisted_event_count"] == len(fake_session.added)
    assert len(fake_session.added) >= 2


def test_build_ingest_pipeline_config_uses_run_id_as_thread_id():
    run_id = uuid.uuid4()
    config = build_ingest_pipeline_config(run_id=run_id, checkpoint_id="ckpt-1")

    assert config["configurable"]["thread_id"] == str(run_id)
    assert config["configurable"]["checkpoint_id"] == "ckpt-1"


async def test_get_ingest_pipeline_state_snapshot_returns_latest_checkpoint(monkeypatch):
    persist_session = _FakeSession()

    async def fake_run_ingestion(source_id, db):
        return None

    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.agents.graph.runtime.AsyncSessionLocal",
        lambda: persist_session,
    )
    monkeypatch.setattr(
        "app.agents.graph.ingest_pipeline.run_ingestion",
        fake_run_ingestion,
    )

    state = create_ingest_pipeline_state(
        project_id=uuid.uuid4(),
        job_kind="ingest_source",
        source_id=uuid.uuid4(),
    )
    result = await run_ingest_pipeline(state)
    snapshot = await get_ingest_pipeline_state_snapshot(run_id=state["run_id"])

    assert snapshot.values["status"] == "completed"
    assert snapshot.config["configurable"]["checkpoint_id"] == result["result"]["checkpoint_id"]
