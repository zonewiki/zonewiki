"""A list's tasks as a file (docs/spec/tasks.md, Export and import):
GET /api/tasks/export and POST /api/tasks/import."""

import re

from tests.conftest import HOST, create_task, make_list


def export(client, **query):
    return client.get("/api/tasks/export", query_string=query, headers=HOST)


def imp(client, file, **body):
    return client.post("/api/tasks/import", json={"file": file, **body}, headers=HOST)


def titles(client):
    return sorted(t["title"] for t in client.get("/api/tasks", headers=HOST).get_json())


def test_export_is_the_lists_tasks_open_and_completed(client, wiki_dir):
    make_list(client, "shop", headers=HOST)
    create_task(client, title="Milk", list="shop", tags=["weekly"], description="2 l", due_date="2030-01-02", keep=True)
    done = create_task(client, title="Eggs", list="shop").get_json()
    client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
    create_task(client, title="Report", tags=["work", "shop"])     # a tag of the list's name: not in it
    resp = export(client, list="shop")
    assert resp.status_code == 200
    assert re.fullmatch(r'attachment; filename=zone-tasks-shop-\d{4}-\d{2}-\d{2}\.json', resp.headers["Content-Disposition"])
    file = resp.get_json()
    assert file["zone_tasks"] == 1 and file["list"] == "shop" and file["exported_at"]
    assert [t["title"] for t in file["tasks"]] == ["Milk", "Eggs"]
    milk, eggs = file["tasks"]
    assert milk["description"] == "2 l" and milk["due_date"] == "2030-01-02" and milk["keep"] is True
    assert milk["list"] == "shop" and milk["tags"] == ["weekly"]
    assert eggs["closed_at"]
    assert export(client, list="nosuch").get_json()["tasks"] == []
    # A filter as an embed's tag=; neither: every task.
    assert [t["title"] for t in export(client, list="shop", tag="weekly").get_json()["tasks"]] == ["Milk"]
    assert sorted(t["title"] for t in export(client, tag="work|weekly").get_json()["tasks"]) == ["Milk", "Report"]
    everything = export(client)
    assert len(everything.get_json()["tasks"]) == 3 and "zone-tasks-all-" in everything.headers["Content-Disposition"]


def test_the_lists_name_cant_add_to_the_download_header(client, wiki_dir):
    # The name is the request's: quoted and escaped, it stays one file name
    # (a bare " would end it, and what follows be a parameter of its own).
    header = export(client, list='x" filename="evil.html').headers["Content-Disposition"]
    assert re.fullmatch(r'attachment; filename="zone-tasks-x\\"-filename=\\"evil\.html-\d{4}-\d{2}-\d{2}\.json"', header), header


def test_import_brings_an_export_back_once(client, wiki_dir):
    make_list(client, "shop", headers=HOST)
    create_task(client, title="Milk", list="shop", tags=["weekly"], description="2 l")
    done = create_task(client, title="Eggs", list="shop").get_json()
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
    assert [(t["id"], t["title"], t["description"], t["tags"], t["list"]) for t in back] == [(ids[0], "Milk", "2 l", ["weekly"], "shop")]
    completed = client.get("/api/tasks/closed", headers=HOST).get_json()
    assert [(t["id"], t["title"]) for t in completed] == [(ids[1], "Eggs")]


def test_import_into_another_list_puts_the_tasks_there(client, wiki_dir):
    make_list(client, "groceries", headers=HOST)
    file = {"zone_tasks": 1, "list": "shop", "tasks": [
        {"id": "11111111-2222-4333-8444-555555555555", "title": "Milk", "list": "shop", "tags": ["weekly"]},
        {"id": "not an id", "title": "Bread", "tags": []},
        {"title": "Salt"},
    ]}
    assert imp(client, file, list="nosuch").status_code == 400      # a list there is, or none
    out = imp(client, file, list="groceries", tag="staples,-old").get_json()
    assert (out["added"], out["skipped"], len(out["ids"])) == (3, 0, 3)
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Milk"]["tags"] == ["weekly", "staples"] and tasks["Milk"]["list"] == "groceries"
    assert tasks["Milk"]["id"] == "11111111-2222-4333-8444-555555555555"
    assert tasks["Bread"]["tags"] == ["staples"] and len(tasks["Bread"]["id"]) == 36
    assert tasks["Salt"]["tags"] == ["staples"] and tasks["Salt"]["list"] == "groceries"
    # new= says the tags, as for a new task; All tasks adds none, and a bare list of tasks does.
    assert imp(client, [{"title": "Pepper", "tags": ["x"]}], list="groceries", new="pantry").get_json()["added"] == 1
    assert imp(client, [{"title": "Oil", "tags": ["x"]}]).get_json()["added"] == 1
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Pepper"]["tags"] == ["x", "pantry"] and tasks["Oil"]["tags"] == ["x"] and "list" not in tasks["Oil"]
    # also=: tags of the user's own for this import, on top of the list's.
    assert imp(client, [{"title": "Rice", "tags": ["x"]}], list="groceries", also="#From Anna, x").get_json()["added"] == 1
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Rice"]["tags"] == ["x", "From-Anna"] and tasks["Rice"]["list"] == "groceries"
    # Into All tasks a task goes where its own list says: the list of that name here, or none.
    assert imp(client, [{"title": "Tea", "list": "groceries"}, {"title": "Soap", "list": "shop"}]).get_json()["added"] == 2
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks["Tea"]["list"] == "groceries" and "list" not in tasks["Soap"]


def test_import_keeps_dates_zero_padded(client, wiki_dir):
    """A date of one digit in a file (as the API kept one before 2026-11-20)
    comes in as YYYY-MM-DD; what is no day comes in as none."""
    file = [{"title": "Old", "due_date": "2026-1-5", "start_date": "2026-1-3", "last_done": "2025-12-1"},
            {"title": "Bad", "due_date": "2026-2-30", "start_date": "soon"}]
    assert imp(client, file).get_json()["added"] == 2
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert (tasks["Old"]["due_date"], tasks["Old"]["start_date"], tasks["Old"]["last_done"]) == ("2026-01-05", "2026-01-03", "2025-12-01")
    assert tasks["Bad"]["due_date"] is None and tasks["Bad"]["start_date"] is None


def test_import_refuses_what_is_no_export(client, wiki_dir):
    for file in ("text", 3, None, {"pages": []}, {"tasks": "x"}, {"tasks": [{"title": "ok"}, {"no": "title"}]}, [7]):
        resp = imp(client, file)
        assert resp.status_code == 400 and "exported from Zonewiki" in resp.get_json()["error"], file
    assert titles(client) == []
    assert imp(client, {"tasks": []}).get_json() == {"added": 0, "skipped": 0, "ids": []}


def test_import_into_the_inbox_puts_the_tasks_in_no_list(client, wiki_dir):
    """list=inbox (or none, its name until 2026-11): the Inbox, whatever
    list the file's tasks name; without users that is no list, shared as
    everything is."""
    make_list(client, "shop", headers=HOST)
    file = [{"title": "Milk", "list": "shop"}, {"title": "Salt", "list": "gone"}, {"title": "Tea", "private": True}]
    assert imp(client, file, list="inbox").get_json()["added"] == 3
    assert imp(client, [{"title": "Rice", "list": "shop"}], list="none").get_json()["added"] == 1
    tasks = {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert all("list" not in t and "private" not in t for t in tasks.values()) and len(tasks) == 4
    assert [t["title"] for t in export(client, list="inbox").get_json()["tasks"]] == ["Milk", "Salt", "Tea", "Rice"]
    assert not (wiki_dir / "private").exists() and '"general"' not in (wiki_dir / "_tasks/lists.json").read_text()


def test_an_undone_delete_goes_back_where_it_was_or_into_the_inbox(client, wiki_dir):
    """POST /api/tasks/restore without users: the list the snapshot names
    when it is there, else no list (the Inbox); no list is made for it."""
    make_list(client, "shop", headers=HOST)
    snaps = [{"id": "r-1", "title": "Milk", "list": "shop"}, {"id": "r-2", "title": "Salt", "list": "gone"},
             {"id": "r-3", "title": "Tea"}, {"id": "r-4", "title": "Rice", "list": "gone", "private": True}]
    assert client.post("/api/tasks/restore", json={"tasks": snaps}, headers=HOST).status_code == 201
    tasks = {t["title"]: t.get("list") for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert tasks == {"Milk": "shop", "Salt": None, "Tea": None, "Rice": None}
    registry = (wiki_dir / "_tasks/lists.json").read_text()
    assert '"general"' not in registry and '"private"' not in registry and not (wiki_dir / "private").exists()
