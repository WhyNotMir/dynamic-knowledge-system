from __future__ import annotations

import uuid

from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion

from tests.helpers import confirm_all_candidates, make_linked_docx, unique_docx


async def _prepare_linked_articles(client, project, session_factory, tmp_path):
    docx = make_linked_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        up = await client.post(
            f"/projects/{project['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    assert up.status_code == 202, up.text
    source_id = uuid.UUID(up.json()["id"])

    async with session_factory() as db:
        await run_ingestion(source_id, db)

    proposal = await client.post(f"/projects/{project['id']}/structure/propose")
    assert proposal.status_code == 202, proposal.text
    proposal_id = uuid.UUID(proposal.json()["proposal_id"])

    async with session_factory() as db:
        await run_structure_proposal(proposal_id, db)

    await confirm_all_candidates(client, project["id"], str(proposal_id))

    build = await client.post(
        f"/projects/{project['id']}/articles/build",
        json={"proposal_id": str(proposal_id)},
    )
    assert build.status_code == 200, build.text

    listing = await client.get(f"/projects/{project['id']}/articles")
    assert listing.status_code == 200, listing.text
    return listing.json()


async def test_graph_endpoint_returns_real_nodes_and_edges(
    client, project, session_factory, tmp_path, mock_embeddings, mock_structure_agent
):
    rows = await _prepare_linked_articles(
        client, project, session_factory, tmp_path
    )

    graph = await client.get(f"/projects/{project['id']}/graph")
    assert graph.status_code == 200, graph.text
    body = graph.json()

    node_ids = {node["id"] for node in body["nodes"]}
    assert node_ids.issuperset({row["id"] for row in rows})
    assert any(edge["kind"] == "hard" for edge in body["edges"])
    assert any(edge["kind"] == "soft" for edge in body["edges"])


async def test_manual_alias_update_roundtrips_through_article_detail(
    client, project, session_factory, tmp_path, mock_embeddings, mock_structure_agent
):
    rows = await _prepare_linked_articles(
        client, project, session_factory, tmp_path
    )
    attention = next(row for row in rows if row["title"] == "Attention Is All You Need")

    update = await client.put(
        f"/projects/{project['id']}/articles/{attention['id']}/aliases",
        json={"aliases": ["Transformer Paper", "AIAYN", "Transformer Paper"]},
    )
    assert update.status_code == 200, update.text
    assert update.json()["aliases"] == [
        "Attention Is All You Need",
        "Transformer Paper",
        "AIAYN",
    ]

    detail = await client.get(f"/projects/{project['id']}/articles/{attention['id']}")
    assert detail.status_code == 200, detail.text
    aliases = detail.json()["aliases"]
    assert "Transformer Paper" in aliases
    assert aliases.count("Transformer Paper") == 1


async def test_manual_alias_update_filters_noisy_generic_aliases(
    client, project, session_factory, tmp_path, mock_embeddings, mock_structure_agent
):
    rows = await _prepare_linked_articles(
        client, project, session_factory, tmp_path
    )
    attention = next(row for row in rows if row["title"] == "Attention Is All You Need")

    update = await client.put(
        f"/projects/{project['id']}/articles/{attention['id']}/aliases",
        json={"aliases": ["AIAYN", "Section", "A", "%%%"]},
    )
    assert update.status_code == 200, update.text
    assert update.json()["aliases"] == [
        "Attention Is All You Need",
        "AIAYN",
    ]
