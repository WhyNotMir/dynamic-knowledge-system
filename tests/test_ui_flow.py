"""End-to-end integration test that walks the UI happy path.

This is the single most important test in the suite: it mirrors what the
Next.js frontend does in `dks-ui/`, from "User clicks New Project" all
the way to "User browses built articles". If this test passes we know
every wire the UI depends on is intact.

What the UI actually does (see `dks-ui/lib/api.ts`):
  1.  POST /projects                                   (ProjectsPage)
  2.  GET  /projects                                   (ProjectsPage list refresh)
  3.  POST /projects/{id}/sources   (multipart upload) (UploadPanel)
  4.  GET  /projects/{id}/sources                      (polling — wait for DONE)
  5.  POST /projects/{id}/structure/propose            (Propose button)
  6.  GET  /projects/{id}/structure/proposals/{pid}    (polling — wait for READY)
  7.  POST /projects/{id}/articles/build               (Build button)
  8.  GET  /projects/{id}/articles                     (ArticlesPage)
  9.  GET  /projects/{id}/articles/{aid}               (ArticleDetail)

In tests steps 4 and 6 are normally served by background workers. Here we
bypass arq and drive the workers inline — the HTTP contract is the same.
"""
from __future__ import annotations

import uuid

from app.domain.articles.structure_service import run_structure_proposal
from app.domain.ingestion.ingestion_service import run_ingestion

from tests.helpers import confirm_all_candidates, make_docx, unique_docx


async def test_full_ui_happy_path(client, session_factory, tmp_path):
    # 1. Create project
    r = await client.post(
        "/projects",
        json={"name": "UI-flow project", "description": "e2e"},
    )
    assert r.status_code == 201, r.text
    project = r.json()
    project_id = project["id"]

    # 2. List projects — includes it
    r = await client.get("/projects")
    assert r.status_code == 200
    assert any(p["id"] == project_id for p in r.json())

    # 3. Upload source (multipart, no Content-Type header — just like the UI)
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        r = await client.post(
            f"/projects/{project_id}/sources",
            files={"file": (docx.name, fh)},
        )
    assert r.status_code == 202, r.text
    source = r.json()
    source_id = source["id"]
    assert source["status"] == "pending"
    assert source["source_type"] == "docx"

    # Simulate the worker (UI would be polling GET /sources while this runs).
    async with session_factory() as db:
        await run_ingestion(uuid.UUID(source_id), db)
        await db.commit()

    # 4. UI polls list + detail until status == done
    r = await client.get(f"/projects/{project_id}/sources")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "done"

    r = await client.get(f"/projects/{project_id}/sources/{source_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "done"

    # UI also lists fragments for preview.
    r = await client.get(f"/projects/{project_id}/sources/{source_id}/fragments")
    assert r.status_code == 200
    frags = r.json()
    assert len(frags) >= 3
    for f in frags:
        assert f["content"]
        assert f["element_type"] in {"heading", "paragraph", "list_item", "table", "caption"}

    # 5. Propose structure
    r = await client.post(f"/projects/{project_id}/structure/propose")
    assert r.status_code == 202
    proposal_id = r.json()["proposal_id"]

    # UI sees it in the proposals list, with status=pending.
    r = await client.get(f"/projects/{project_id}/structure/proposals")
    assert r.status_code == 200
    listing = r.json()
    found = next((p for p in listing if p["id"] == proposal_id), None)
    assert found is not None
    assert found["status"] == "pending"

    # Inbox v1 mirrors reviewable proposals as `new_candidates` items.
    r = await client.get(f"/projects/{project_id}/inbox")
    assert r.status_code == 200
    inbox = r.json()
    assert len(inbox) == 1
    assert inbox[0]["item_type"] == "new_candidates"
    assert inbox[0]["proposal_id"] == proposal_id
    assert inbox[0]["status"] == "pending"
    assert inbox[0]["gate"]["requires_human_review"] is True
    assert inbox[0]["gate"]["auto_apply_allowed"] is False
    assert any(action["action"] == "confirm" for action in inbox[0]["gate"]["available_actions"])
    assert any(action["action"] == "merge" for action in inbox[0]["gate"]["reserved_actions"])

    # Worker runs.
    async with session_factory() as db:
        await run_structure_proposal(uuid.UUID(proposal_id), db)
        await db.commit()

    # 6. UI polls the single proposal until status == ready
    r = await client.get(
        f"/projects/{project_id}/structure/proposals/{proposal_id}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert len(body["candidates"]) >= 2
    # Candidate shape — the review page relies on these fields.
    for c in body["candidates"]:
        assert c["id"]
        assert c["title"]
        assert c["status"] == "proposed"
        assert isinstance(c["fragment_ids"], list)

    r = await client.get(f"/projects/{project_id}/inbox")
    assert r.status_code == 200
    inbox = r.json()
    assert inbox[0]["status"] == "ready"
    assert inbox[0]["proposal"]["id"] == proposal_id
    assert len(inbox[0]["proposal"]["candidates"]) >= 2

    # 7. Optional: user edits a candidate's title via PATCH
    renamed_candidate = body["candidates"][-1]
    first_cand_id = renamed_candidate["id"]
    r = await client.patch(
        f"/projects/{project_id}/structure/candidates/{first_cand_id}",
        json={"title": "Renamed by user"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed by user"

    # 7a. User clicks "Accept All" in the Review UI — required now that the
    # builder only materialises CONFIRMED candidates.
    await confirm_all_candidates(client, project_id, proposal_id)

    # 8. Build articles — UI sends proposal_id but backend would accept empty body too.
    r = await client.post(
        f"/projects/{project_id}/articles/build",
        json={"proposal_id": proposal_id},
    )
    assert r.status_code == 200, r.text
    built = r.json()
    assert built["count"] >= 2

    # 9. Articles list renders in the Articles page.
    r = await client.get(f"/projects/{project_id}/articles")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == built["count"]
    # Find the renamed article — it must have made it into an article.
    assert any(row["title"] == "Renamed by user" for row in rows)

    # 10. Detail page for one article.
    aid = rows[0]["id"]
    r = await client.get(f"/projects/{project_id}/articles/{aid}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == aid
    assert detail["project_id"] == project_id
    assert len(detail["blocks"]) > 0
    # position_index is sorted ascending.
    pis = [b["position_index"] for b in detail["blocks"]]
    assert pis == sorted(pis)


async def test_ui_flow_two_projects_isolated(client, session_factory, tmp_path):
    """Two projects never see each other's data — the UI relies on this for
    filtering sources/proposals/articles under /projects/{id}/..."""
    p1 = (await client.post("/projects", json={"name": "one"})).json()
    p2 = (await client.post("/projects", json={"name": "two"})).json()

    # Upload only to p1.
    docx = make_docx(unique_docx(tmp_path))
    with docx.open("rb") as fh:
        r = await client.post(
            f"/projects/{p1['id']}/sources",
            files={"file": (docx.name, fh)},
        )
    source_id = uuid.UUID(r.json()["id"])
    async with session_factory() as db:
        await run_ingestion(source_id, db)
        await db.commit()

    # p1 sees its source; p2 sees nothing.
    r1 = await client.get(f"/projects/{p1['id']}/sources")
    r2 = await client.get(f"/projects/{p2['id']}/sources")
    assert len(r1.json()) == 1
    assert r2.json() == []

    # p2 has no proposals and no articles.
    assert (await client.get(f"/projects/{p2['id']}/structure/proposals")).json() == []
    assert (await client.get(f"/projects/{p2['id']}/inbox")).json() == []
    assert (await client.get(f"/projects/{p2['id']}/articles")).json() == []
