"""A task's assignee (docs/spec/users.md, Assigning tasks): one user per
shared task, who can be assigned, what a name that no longer can counts
as, the history, and the names' route."""

import json

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {name: (f"{name}-pw", "") for name in ("alice", "bob", "carol", "dan", "eve")}
USER_SETTINGS = {"alice": {"role": "admin"}, "carol": {"role": "viewer"}, "dan": {"disabled": "yes"},
                 "eve": {"disabled": "yes", "pending": "yes"}}


def login(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": f"{name}-pw"}, headers=HOST).status_code == 302


@pytest.fixture()
def alice(client, wiki_dir, monkeypatch):
    """Signed in as alice, an admin; bob an editor, carol a viewer, dan
    disabled, eve waiting for approval."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
    login(client, "alice")
    return client


def new(client, **fields):
    resp = client.post("/api/tasks", json={"title": "Call Sam", **fields}, headers=HOST)
    assert resp.status_code == 201, resp.data.decode()
    return resp.get_json()


def put(client, task, **fields):
    return client.put(f"/api/tasks/{task['id']}", json=fields, headers=HOST)


def stored(wiki_dir, task):
    """The task as its file has it."""
    tasks = json.loads((wiki_dir / "_tasks" / "tasks.json").read_text())
    return next(t for t in tasks if t["id"] == task["id"])


def events(task):
    return [(e["event"], e.get("from"), e.get("to"), e.get("by")) for e in task["history"]]


class TestAssigning:

    def test_a_new_task_can_start_assigned(self, alice, wiki_dir):
        task = new(alice, assignee="bob", tags=["work"])
        assert task["assignee"] == "bob"
        assert events(task) == [("created", None, None, "alice")]   # its first assignee is no change
        keys = list(stored(wiki_dir, task))
        assert keys[keys.index("tags") + 1] == "assignee"

    def test_a_task_without_one_has_no_key(self, alice, wiki_dir):
        task = new(alice)
        assert "assignee" not in task and "assignee" not in stored(wiki_dir, task)

    def test_assign_reassign_unassign(self, alice, wiki_dir):
        task = new(alice)
        assert put(alice, task, assignee="bob").get_json()["assignee"] == "bob"
        login(alice, "bob")
        task = put(alice, task, assignee="alice").get_json()
        assert task["assignee"] == "alice" and stored(wiki_dir, task)["assignee"] == "alice"
        task = put(alice, task, assignee="").get_json()
        assert "assignee" not in task and "assignee" not in stored(wiki_dir, task)
        assert events(task) == [("created", None, None, "alice"), ("assigned", None, "bob", "alice"),
                                ("assigned", "bob", "alice", "bob"), ("assigned", "alice", None, "bob")]

    def test_the_same_name_again_is_no_change(self, alice):
        task = new(alice, assignee="bob")
        assert events(put(alice, task, assignee="bob").get_json()) == [("created", None, None, "alice")]

    @pytest.mark.parametrize("value,name", [("me", "alice"), ("@Bob", "bob"), (" bob ", "bob")])
    def test_how_a_name_may_be_written(self, alice, value, name):
        assert new(alice, assignee=value)["assignee"] == name

    @pytest.mark.parametrize("value", [None, "", "none", "@none"])
    def test_how_no_one_may_be_written(self, alice, value):
        task = new(alice, assignee="bob")
        assert "assignee" not in put(alice, task, assignee=value).get_json()

    def test_other_edits_leave_it(self, alice):
        task = new(alice, assignee="bob")
        assert put(alice, task, title="Call Sam today").get_json()["assignee"] == "bob"

    def test_batch_update_assigns(self, alice):
        one, two = new(alice), new(alice)
        resp = alice.post("/api/tasks/batch-update", headers=HOST, json={
            "tasks": [{"id": one["id"], "assignee": "bob"}, {"id": two["id"], "assignee": "me"}]})
        assert resp.status_code == 200, resp.data.decode()
        by_id = {t["id"]: t.get("assignee") for t in alice.get("/api/tasks", headers=HOST).get_json()}
        assert (by_id[one["id"]], by_id[two["id"]]) == ("bob", "alice")

    def test_a_series_next_occurrence_keeps_it(self, alice):
        task = new(alice, assignee="bob", due_date="2030-01-01", rec_type="weekly")
        put(alice, task, completed=True)
        [spawned] = [t for t in alice.get("/api/tasks", headers=HOST).get_json() if t["spawned_from"] == task["id"]]
        assert spawned["assignee"] == "bob"

    def test_an_undo_leaves_no_trace(self, alice):
        task = new(alice)
        put(alice, task, assignee="bob")
        undone = put(alice, task, assignee=None, history=task["history"]).get_json()
        assert "assignee" not in undone and events(undone) == [("created", None, None, "alice")]

    def test_a_history_sent_back_keeps_the_assignments(self, alice):
        task = put(alice, new(alice), assignee="bob").get_json()
        again = put(alice, task, title="Call Sam!", history=task["history"]).get_json()
        assert events(again)[-1] == ("assigned", None, "bob", "alice")


class TestWhoCanBeAssigned:

    @pytest.mark.parametrize("name,why", [
        ("zed", '"zed" is not a user of this wiki'),
        ("carol", "carol is a viewer"),
        ("dan", "dan's account is disabled"),
        ("eve", "eve's account is waiting"),
        ("Not A Name", "is not a user"),
    ])
    def test_only_an_active_user_who_can_change_tasks(self, alice, wiki_dir, name, why):
        resp = alice.post("/api/tasks", json={"title": "No", "assignee": name}, headers=HOST)
        assert resp.status_code == 400 and why in resp.get_json()["error"]
        assert alice.get("/api/tasks", headers=HOST).get_json() == []
        task = new(alice, assignee="bob")
        resp = put(alice, task, assignee=name, title="Changed")
        assert resp.status_code == 400 and why in resp.get_json()["error"]
        assert stored(wiki_dir, task)["assignee"] == "bob" and stored(wiki_dir, task)["title"] == "Call Sam"

    def test_it_must_be_a_string(self, alice):
        resp = alice.post("/api/tasks", json={"title": "No", "assignee": ["bob"]}, headers=HOST)
        assert resp.status_code == 400 and "assignee must be a string" in resp.get_json()["error"]

    def test_a_private_task_has_none(self, alice, wiki_dir):
        resp = alice.post("/api/tasks", json={"title": "Mine", "private": True, "assignee": "bob"}, headers=HOST)
        assert resp.status_code == 400 and "private task" in resp.get_json()["error"]
        mine = new(alice, private=True)
        assert put(alice, mine, assignee="alice").status_code == 400
        assert put(alice, mine, assignee="").status_code == 200       # none is what it has
        # Shared and assigned in one write.
        shared = put(alice, mine, private=False, assignee="bob").get_json()
        assert shared["private"] is False and shared["assignee"] == "bob"

    def test_without_users_there_is_no_one(self, client, wiki_dir):
        resp = client.post("/api/tasks", json={"title": "No", "assignee": "bob"}, headers=HOST)
        assert resp.status_code == 400 and "no users" in resp.get_json()["error"]
        task = new(client, assignee="")
        assert "assignee" not in task

    def test_without_users_a_file_keeps_its_assignee_unseen(self, client, wiki_dir):
        task = new(client)
        path = wiki_dir / "_tasks" / "tasks.json"
        tasks = json.loads(path.read_text())
        tasks[0]["assignee"] = "bob"
        path.write_text(json.dumps(tasks))
        assert "assignee" not in client.get("/api/tasks", headers=HOST).get_json()[0]
        assert "assignee" not in put(client, task, title="Still Bob's").get_json()
        assert stored(wiki_dir, task)["assignee"] == "bob"

    def test_a_user_named_me_or_none_cant_be_assigned(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "USERS", {**USERS, "me": ("me-pw", ""), "none": ("none-pw", "")})
        monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
        login(client, "alice")
        assert new(client, assignee="me")["assignee"] == "alice"
        assert "assignee" not in new(client, assignee="none")
        login(client, "me")       # the account still works
        resp = client.post("/api/tasks", json={"title": "No", "assignee": "me"}, headers=HOST)
        assert resp.status_code == 400 and "word for whoever is signed in" in resp.get_json()["error"]
        names = client.get("/api/tasks/assignable", headers=HOST).get_json()
        assert names == {"users": ["alice", "bob"], "me": "me"}


class TestFormerAssignee:
    """A name that can no longer be assigned stays on its task; it is only
    never put on a task that didn't have it."""

    @pytest.fixture()
    def bobs(self, alice, monkeypatch):
        task = new(alice, assignee="bob")
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"disabled": "yes"}})
        return task

    def test_it_stays_through_other_edits(self, alice, wiki_dir, bobs):
        task = put(alice, bobs, title="Call Sam today", assignee="bob")
        assert task.status_code == 200 and task.get_json()["assignee"] == "bob"
        assert stored(wiki_dir, bobs)["assignee"] == "bob"

    def test_it_can_be_replaced_and_put_back_only_where_it_was(self, alice, bobs):
        replaced = put(alice, bobs, assignee="alice").get_json()
        assert replaced["assignee"] == "alice"
        # The undo of that: the task's history says it was bob's.
        undone = put(alice, bobs, assignee="bob", history=bobs["history"]).get_json()
        assert undone["assignee"] == "bob" and events(undone) == [("created", None, None, "alice")]
        resp = put(alice, new(alice), assignee="bob")       # not on a task that never was
        assert resp.status_code == 400 and "bob's account is disabled" in resp.get_json()["error"]

    def test_a_removed_users_name_stays_too(self, alice, wiki_dir, bobs, monkeypatch):
        monkeypatch.setattr(settings, "USERS", {k: v for k, v in USERS.items() if k != "bob"})
        monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
        login(alice, "alice")
        [task] = alice.get("/api/tasks", headers=HOST).get_json()
        assert task["assignee"] == "bob"
        assert put(alice, task, assignee=None).status_code == 200


class TestSnapshots:
    """A restored or imported task has an assignee only as a write would take it."""

    def restore(self, client, **fields):
        snap = {"id": "restored-1", "title": "Back", **fields}
        assert client.post("/api/tasks/restore", json={"tasks": [snap]}, headers=HOST).status_code == 201
        [task] = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] == "restored-1"]
        return task

    def test_an_undone_delete_keeps_it(self, alice):
        assert self.restore(alice, assignee="bob")["assignee"] == "bob"

    @pytest.mark.parametrize("fields", [{"assignee": "carol"}, {"assignee": "zed"}, {"assignee": 7},
                                        {"assignee": "bob", "private": True}])
    def test_but_not_a_name_that_cant_be_assigned_nor_on_a_private_task(self, alice, fields):
        assert "assignee" not in self.restore(alice, **fields)

    def test_an_import_drops_other_wikis_people(self, alice):
        export = {"zone_tasks": 1, "tasks": [{"title": "Theirs", "assignee": "zed"}, {"title": "Ours", "assignee": "bob"}]}
        assert alice.post("/api/tasks/import", json={"file": export}, headers=HOST).status_code == 200
        got = {t["title"]: t.get("assignee") for t in alice.get("/api/tasks", headers=HOST).get_json()}
        assert got == {"Theirs": None, "Ours": "bob"}


class TestNames:
    """GET /api/tasks/assignable: who a task can be assigned to, and who asks."""

    def test_the_users_who_can_be_assigned(self, alice):
        assert alice.get("/api/tasks/assignable", headers=HOST).get_json() == {"users": ["alice", "bob"], "me": "alice"}

    def test_a_viewer_reads_them(self, alice):
        login(alice, "carol")
        assert alice.get("/api/tasks/assignable", headers=HOST).get_json() == {"users": ["alice", "bob"], "me": "carol"}

    def test_none_in_sharing_mode(self, alice):
        alice.set_cookie("zone-sharing", "1")
        assert alice.get("/api/tasks/assignable", headers=HOST).get_json()["users"] == []

    def test_none_without_users(self, client):
        assert client.get("/api/tasks/assignable", headers=HOST).get_json() == {"users": [], "me": ""}

    def test_not_without_signing_in(self, alice):
        alice.post("/logout", headers=HOST)
        resp = alice.get("/api/tasks/assignable", headers=HOST)
        assert resp.status_code != 200 and b"alice" not in resp.data
