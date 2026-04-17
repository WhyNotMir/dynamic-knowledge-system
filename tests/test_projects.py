"""Basic CRUD sanity for /projects. These calls have no external dependencies
and are the prerequisite for every other test in the suite."""
from __future__ import annotations


async def test_create_project_returns_201_and_persists(client):
    r = await client.post(
        "/projects",
        json={"name": "My project", "description": "A description"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "My project"
    assert body["description"] == "A description"
    assert body["id"]  # UUID string
    assert body["created_at"]

    # The project is discoverable via GET /projects
    r2 = await client.get("/projects")
    assert r2.status_code == 200
    assert any(p["id"] == body["id"] for p in r2.json())


async def test_get_project_by_id(client, project):
    r = await client.get(f"/projects/{project['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == project["id"]


async def test_get_unknown_project_returns_404(client):
    r = await client.get("/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_get_project_with_bad_uuid_returns_400(client):
    r = await client.get("/projects/not-a-uuid")
    assert r.status_code == 400
