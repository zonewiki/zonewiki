"""A shared list has owners (Who manages a list, docs/spec/tasks.md): who made
it, and every admin. Owners rename it, delete it and set its settings;
whoever else may write tasks makes and changes its tasks and leaves the
list as it is. A list from before owners is its admins'; without users, and
in a wiki without an admin, whoever may write may manage, as before."""

import json

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token"), "dave": ("dave-pw", "dave-token")}


def login(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def registry(wiki):
    return json.loads((wiki / "_tasks/lists.json").read_text())


def entry(wiki, name):
    return next(l for l in registry(wiki)["lists"] if l["name"] == name)


def page(client, ref):
    return client.get(f"/tasks?list={ref}", headers=HOST).get_data(as_text=True)


@pytest.fixture()
def wiki(client, git_wiki_dir, monkeypatch):
    """alice is an admin, bob and dave editors; bob made the shared list ops."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    login(client, "bob")
    assert client.post("/tasks/new-list", data={"name": "ops"}, headers=HOST).status_code == 302
    return git_wiki_dir


def test_a_shared_list_is_its_makers(client, wiki):
    assert entry(wiki, "ops") == {"name": "ops", "owners": ["bob"]}
    # A private list has no owners to name: it is its user's.
    assert client.post("/tasks/new-list", data={"name": "mine", "private": "1"}, headers=HOST).status_code == 302
    assert '"owners"' not in (wiki / "private/bob/_tasks/lists.json").read_text()
    # Made shared, it is theirs to manage.
    assert client.post("/tasks/share-list", data={"list": "mine"}, headers=HOST).status_code == 302
    assert entry(wiki, "mine") == {"name": "mine", "owners": ["bob"]}


def test_a_member_changes_its_tasks_and_leaves_the_list(client, wiki):
    login(client, "dave")
    # Its tasks are his to make and change.
    made = client.post("/api/tasks", json={"title": "Rotate the keys", "list": "ops"}, headers=HOST)
    assert made.status_code == 201
    assert client.put(f"/api/tasks/{made.get_json()['id']}", json={"title": "Rotate keys"}, headers=HOST).status_code == 200
    # The list itself is not: no Rename… or Delete… in its menu, and the routes refuse.
    html = page(client, "ops")
    assert 'id="list-rename-btn"' not in html and 'id="list-delete-btn"' not in html and 'id="list-pin-btn"' in html
    assert "data-settings-list=" not in html                     # no List settings… for the list
    before = (wiki / "_tasks/lists.json").read_text()
    resp = client.post("/tasks/rename-list", data={"list": "ops", "new_name": "daves"}, headers=HOST)
    assert resp.status_code == 403 and "bob's list" in resp.get_data(as_text=True)
    assert client.post("/tasks/delete-list", data={"list": "ops", "to": ""}, headers=HOST).status_code == 403
    resp = client.post("/api/tasks/settings", json={"list": "ops", "set": {"limit": "3"}}, headers=HOST)
    assert resp.status_code == 403 and "bob's list" in resp.get_json()["error"]
    assert (wiki / "_tasks/lists.json").read_text() == before
    # An assistant of his is refused the same way.
    out = client.post("/mcp", headers={**HOST, "Authorization": "Bearer dave-token"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "rename_task_list", "arguments": {"list": "ops", "new_name": "daves", "summary": "x"}}}).get_json()
    assert "bob's list" in json.dumps(out) and (wiki / "_tasks/lists.json").read_text() == before


@pytest.mark.parametrize("who", ["bob", "alice"])
def test_its_owner_and_an_admin_manage_it(client, wiki, who):
    login(client, who)
    html = page(client, "ops")
    assert 'id="list-rename-btn"' in html and 'id="list-delete-btn"' in html and 'data-settings-list="ops"' in html
    assert client.post("/api/tasks/settings", json={"list": "ops", "set": {"limit": "3"}}, headers=HOST).status_code == 200
    resp = client.post("/tasks/rename-list", data={"list": "ops", "new_name": "operations"}, headers=HOST)
    assert resp.headers["Location"] == "/tasks?list=operations"
    assert entry(wiki, "operations") == {"name": "operations", "limit": "3", "owners": ["bob"]}   # still bob's
    assert client.post("/tasks/delete-list", data={"list": "operations", "to": ""}, headers=HOST).headers["Location"] == "/tasks"
    assert all(l["name"] != "operations" for l in registry(wiki)["lists"])


def test_a_member_exports_and_imports_the_lists_tasks(client, wiki):
    # Export and Import… are no list settings: dave, who doesn't manage
    # the list, has them in the ⋯ menu (on /tasks the page's) all the same.
    client.post("/api/tasks", json={"title": "Rotate the keys", "list": "ops"}, headers=HOST)
    login(client, "dave")
    html = page(client, "ops")
    assert 'id="task-more-btn"' in html and 'data-writes="1"' in html and 'id="task-import-modal"' in html
    assert 'data-tasks-action="export"' in html and 'data-tasks-action="import"' in html
    assert "data-settings-" not in html                               # no List settings…
    out = client.get("/api/tasks/export?list=ops", headers=HOST).get_json()
    assert [t["title"] for t in out["tasks"]] == ["Rotate the keys"]


def test_a_list_from_before_owners_is_its_admins(client, wiki):
    reg = registry(wiki)
    reg["lists"].append({"name": "old"})
    (wiki / "_tasks/lists.json").write_text(json.dumps(reg))
    login(client, "bob")
    assert 'id="list-rename-btn"' not in page(client, "old")
    resp = client.post("/tasks/rename-list", data={"list": "old", "new_name": "older"}, headers=HOST)
    assert resp.status_code == 403 and "changed by an admin" in resp.get_data(as_text=True)
    login(client, "alice")
    assert client.post("/tasks/rename-list", data={"list": "old", "new_name": "older"}, headers=HOST).headers["Location"] == "/tasks?list=older"


def test_without_an_admin_whoever_writes_manages(client, git_wiki_dir, monkeypatch):
    # A wiki from before roles has no admin: nobody could manage a list else.
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    login(client, "bob")
    assert client.post("/tasks/new-list", data={"name": "ops"}, headers=HOST).status_code == 302
    login(client, "dave")
    assert 'id="list-rename-btn"' in page(client, "ops")
    assert client.post("/tasks/rename-list", data={"list": "ops", "new_name": "daves"}, headers=HOST).headers["Location"] == "/tasks?list=daves"


def test_without_users_a_list_has_no_owners(client, git_wiki_dir):
    assert client.post("/tasks/new-list", data={"name": "shop"}, headers=HOST).status_code == 302
    assert entry(git_wiki_dir, "shop") == {"name": "shop"}
    assert 'id="list-rename-btn"' in page(client, "shop")
