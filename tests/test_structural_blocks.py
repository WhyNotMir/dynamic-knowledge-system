from __future__ import annotations


async def test_structural_block_crud(client, project):
    create = await client.post(
        f"/projects/{project['id']}/structural-blocks",
        json={"name": "Platform", "description": "Top level"},
    )
    assert create.status_code == 201, create.text
    root = create.json()
    assert root["name"] == "Platform"

    child = await client.post(
        f"/projects/{project['id']}/structural-blocks",
        json={"name": "Workers", "parent_id": root["id"], "position_index": 1},
    )
    assert child.status_code == 201, child.text

    listing = await client.get(f"/projects/{project['id']}/structural-blocks")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Platform"
    assert rows[0]["children"][0]["name"] == "Workers"

    patch = await client.patch(
        f"/projects/{project['id']}/structural-blocks/{root['id']}",
        json={"description": "Updated"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["description"] == "Updated"
