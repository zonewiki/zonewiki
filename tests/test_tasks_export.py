"""A list's tasks as a file (docs/spec/tasks.md, Export and import):
GET /api/tasks/export and POST /api/tasks/import."""

import re

from tests.conftest import HOST, create_task


def export(client, **query):
    return client.get("/api/tasks/export", query_string=query, headers=HOST)


def imp(client, file, **body):
    return client.post("/api/tasks/import", json={"file": file, **body}, headers=HOST)


def titles(client):
    return sorted(t["title"] for t in client.get("/api/tasks", headers=HOST).get_json())


def test_export_is_the_lists_tasks_open_and_completed(client, wiki_dir):
    create_task(client, title="Milk", tags=["shop", "weekly"], description="2 l", due_date="2030-01-02", keep=True)
    done = create_task(client, title="Eggs", tags=["shop"]).get_json()
    client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
    create_task(client, title="Report", tags=["work"])
    resp = export(client, list="shop")
    assert resp.status_code == 200
    assert re.fullmatch(r'attachment; filename=zone-tasks-shop-\d{4}-\d{2}-\d{2}\.json', resp.headers["Content-Disposition"])
    file = resp.get_json()
    assert file["zone_tasks"] == 1 and file["list"] == "shop" and file["exported_at"]
    assert [t["title"] for t in file["tasks"]] == ["Milk", "Eggs"]
    milk, eggs = file["tasks"]
    assert milk["description"] == "2 l" and milk["due_date"] == "2030-01-02" and milk["keep"] is True
    assert eggs["closed_at"]
    # A filter as an embed's tag=; neither: every task.
    assert [t["title"] for t in export(client, list="shop", tag="weekly").get_json()["tasks"]] == ["Milk"]
    assert [t["title"] for t in export(client, tag="work|weekly").get_json()["tasks"]] == ["Milk", "Report"]
    everything = export(client)
    assert len(everything.get_json()["tasks"]) == 3 and "zone-tasks-all-" in everything.headers["Content-Disposition"]


def test_the_lists_name_cant_add_to_the_download_header(client, wiki_dir):
    # The name is the request's: quoted and escaped, it stays one file name
    # (a bare " would end it, and what follows be a parameter of its own).
    header = export(client, list='x" filename="evil.html').headers["Content-Disposition"]
    assert re.fullmatch(r'attachment; filename="zone-tasks-x\\"-filename=\\"evil\.html-\d{4}-\d{2}-\d{2}\.json"', header), header


def test_import_brings_an_export_back_once(client, wiki_dir):
    create_task(client, title="Milk", tags=["shop"], description="2 l")
    done = create_task(client, title="Eggs", tags=["shop"]).get_json()
    client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
    file = export(client, list="shop").get_json()
    # Here already: nothing is added.
    assert imp(client, file, list="shop").get_json() == {"added": 0, "skipped": 2, "ids": []}
    ids = [t["id"] for t in file["tasks"]]
    client.post("/api/tasks/batch-delete", json={"ids": ids}, headers=HOST)
    assert titles(client) == []
    assert imp(client, file, list="shop").get_json() == {"added": 2, "skipped": 0, "ids": ids}
    assert imp(client, file, list="shop").get_json() == {"added": 0, "skipped": 2, "ids": []}
    back = client.get("/api/tasks", headers=HOST).get_json()
    assert [(t["id"], t["title"], t["description"], t["tags"]) for t in back] == [(ids[0], "Milk", "2 l", ["shop"])]
    completed = client.get("/api/tasks/closed", headers=HOST).get_json()
    assert [(t["id"], t["title"]) for t in completed] == [(ids[1], "Eggs")]


def test_import_into_another_list_changes_the_lists_tag(client, wiki_dir):
    file = {"zone_tasks": 1, "list": "shop", "tasks": [
        {"id": "11111111-2222-4333-8444-555555555555", "title": "Milk", "tags": ["shop", "weekly"]},
        {"id": "not an id", "title": "Bread", "tags": []},
        {"title": "Salt"},
    ]}
    out = imp(client, file, list="groceries", tag="staples,-old").get_json()
    assert (out["added"], out["skipped"], len(out["ids"])) == (3, 0, 3)
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Milk"]["tags"] == ["weekly", "groceries", "staples"]
    assert tasks["Milk"]["id"] == "11111111-2222-4333-8444-555555555555"
    assert tasks["Bread"]["tags"] == ["groceries", "staples"] and len(tasks["Bread"]["id"]) == 36
    assert tasks["Salt"]["tags"] == ["groceries", "staples"]
    # new= says the tags, as for a new task; All tasks adds none, and a bare list of tasks does.
    assert imp(client, [{"title": "Pepper", "tags": ["x"]}], list="groceries", new="pantry").get_json()["added"] == 1
    assert imp(client, [{"title": "Oil", "tags": ["x"]}]).get_json()["added"] == 1
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Pepper"]["tags"] == ["x", "pantry"] and tasks["Oil"]["tags"] == ["x"]
    # also=: tags of the user's own for this import, on top of the list's.
    assert imp(client, [{"title": "Rice", "tags": ["x"]}], list="groceries", also="#From Anna, x").get_json()["added"] == 1
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Rice"]["tags"] == ["x", "groceries", "From-Anna"]


def test_import_refuses_what_is_no_export(client, wiki_dir):
    for file in ("text", 3, None, {"pages": []}, {"tasks": "x"}, {"tasks": [{"title": "ok"}, {"no": "title"}]}, [7]):
        resp = imp(client, file)
        assert resp.status_code == 400 and "exported from Zone" in resp.get_json()["error"], file
    assert titles(client) == []
    assert imp(client, {"tasks": []}).get_json() == {"added": 0, "skipped": 0, "ids": []}
