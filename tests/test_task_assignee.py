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
        resp = alice.post("/api/tasks/import", json={"file": export}, headers=HOST)
        assert resp.status_code == 200 and resp.get_json()["unassigned"] == 1      # the toast says so
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

    def block(self, client, wiki_dir):
        """The data block a page with a task list brings along."""
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks:]]\n")
        html = client.get("/page/team", headers=HOST).get_data(as_text=True)
        return json.loads(html.split('id="tasks-initial">', 1)[1].split("</script>", 1)[0])

    def test_a_page_with_a_task_list_brings_them_along(self, alice, wiki_dir):
        assert self.block(alice, wiki_dir)["people"] == {"users": ["alice", "bob"], "me": "alice"}
        alice.set_cookie("zone-sharing", "1")
        assert self.block(alice, wiki_dir)["people"] == {"users": [], "me": "alice"}

    def test_but_not_without_users(self, client, wiki_dir):
        assert "people" not in self.block(client, wiki_dir)

    def test_not_without_signing_in(self, alice):
        alice.post("/logout", headers=HOST)
        resp = alice.get("/api/tasks/assignable", headers=HOST)
        assert resp.status_code != 200 and b"alice" not in resp.data


class TestEmbed:
    """[[tasks: assignee=…]]: whose tasks an embed shows; the server's side is
    the counts, the export, the settings and what a new task gets."""

    @pytest.fixture()
    def tasks(self, alice):
        from datetime import date
        today = date.today().isoformat()
        for title, who in (("A1", "alice"), ("A2", "alice"), ("B1", "bob"), ("N1", None)):
            new(alice, title=title, due_date=today, **({"assignee": who} if who else {}))
        return alice

    def today(self, client, wiki_dir, params):
        """How many tasks due today the pinned list with these params counts."""
        (wiki_dir / "team.md").write_text(f"# Team\n\n[[tasks: {params} pin=1]]\n")
        return client.get("/api/tasks/due-count", headers=HOST).get_json()["today"]

    def test_the_counts_are_each_users_own(self, tasks, wiki_dir):
        assert self.today(tasks, wiki_dir, "assignee=me") == 2
        login(tasks, "bob")
        assert self.today(tasks, wiki_dir, "assignee=me") == 1
        login(tasks, "carol")                       # a viewer has none
        assert self.today(tasks, wiki_dir, "assignee=me") == 0

    @pytest.mark.parametrize("params,count", [
        ("assignee=bob", 1), ("assignee=@Bob", 1), ("assignee=alice|bob", 3), ("assignee=none", 1),
        ("assignee=-alice", 2), ("assignee=-alice,-none", 1), ("assignee=me|none", 3), ("assignee=zed", 0),
        ("tag=x assignee=me", 0), ("", 4),
    ])
    def test_the_param_is_read_as_the_filter_is(self, tasks, wiki_dir, params, count):
        assert self.today(tasks, wiki_dir, params) == count

    def test_a_former_assignees_tasks_count_as_no_ones(self, tasks, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"disabled": "yes"}})
        assert self.today(tasks, wiki_dir, "assignee=none") == 2
        assert self.today(tasks, wiki_dir, "assignee=bob") == 1

    def test_without_users_the_param_filters_nothing(self, client, wiki_dir):
        from datetime import date
        new(client, title="One", due_date=date.today().isoformat())
        assert self.today(client, wiki_dir, "assignee=me") == 1
        assert "data-init-assignee" not in client.get("/page/team", headers=HOST).get_data(as_text=True)

    def test_the_page_tells_the_browser(self, alice, wiki_dir):
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=@Me]]\n")
        html = alice.get("/page/team", headers=HOST).get_data(as_text=True)
        assert 'data-init-assignee="me"' in html
        assert 'name="assignee"' in html.split('id="task-settings-modal', 1)[1]     # the settings dialog's row

    def test_a_new_task_from_anywhere_gets_the_default_lists_assignee(self, alice, wiki_dir):
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=me pin=default]]\n")
        assert 'data-tasks-assignee="alice"' in alice.get("/page/index", headers=HOST).get_data(as_text=True)
        login(alice, "bob")
        assert 'data-tasks-assignee="bob"' in alice.get("/page/index", headers=HOST).get_data(as_text=True)
        for params in ("assignee=alice|bob", "assignee=none", "assignee=-bob", "assignee=carol", ""):
            (wiki_dir / "team.md").write_text(f"# Team\n\n[[tasks: {params} pin=default]]\n")
            assert "data-tasks-assignee" not in alice.get("/page/index", headers=HOST).get_data(as_text=True), params

    def test_the_export_takes_it(self, tasks):
        got = tasks.get("/api/tasks/export?assignee=me", headers=HOST).get_json()["tasks"]
        assert sorted(t["title"] for t in got) == ["A1", "A2"]

    def test_the_settings_dialog_writes_it(self, alice, wiki_dir):
        path = wiki_dir / "team.md"
        path.write_text("[[tasks: tag=a]]\n")
        save = lambda was, **set: alice.post("/api/tasks/settings", headers=HOST,
                                             json={"page": "team", "index": 0, "was": was, "set": set})
        assert save("tag=a", assignee="Me|@Bob").status_code == 200
        assert path.read_text() == "[[tasks: tag=a assignee=me|bob]]\n"
        for bad in ("a b", "bob;", "|bob", "-", "bob,,"):
            assert save("tag=a assignee=me|bob", assignee=bad).status_code == 400, bad
        assert save("tag=a assignee=me|bob", assignee="").status_code == 200
        assert path.read_text() == "[[tasks: tag=a]]\n"
        # All tasks has no filter.
        resp = alice.post("/api/tasks/settings", headers=HOST, json={"all": True, "was": "", "set": {"assignee": "me"}})
        assert resp.status_code == 400


class TestAssistant:
    """The MCP tools: create_task and update_task take an assignee,
    search_tasks filters by it, and a task's text names it."""

    @pytest.fixture()
    def mcp(self, client, wiki_dir, monkeypatch):
        """Calls a tool as alice's assistant; returns the answer's text."""
        monkeypatch.setattr(settings, "USERS", {**USERS, "alice": ("alice-pw", "alice-token")})
        monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))

        def call(tool, **args):
            resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
            return "".join(c["text"] for c in resp.get_json()["result"]["content"])
        return call

    def test_create_and_update_assign(self, mcp):
        text = mcp("create_task", title="Call Sam", assignee="bob")
        assert "assigned to: bob" in text and "(the user)" not in text
        task_id = text.split("id: ", 1)[1].split()[0]
        assert "assigned to: alice (the user)" in mcp("update_task", id=task_id, assignee="me")
        assert "assigned to" not in mcp("update_task", id=task_id, assignee="none")
        assert "assigned to: bob" in mcp("update_task", id=task_id, assignee="@Bob")
        assert "assigned to" not in mcp("update_task", id=task_id, assignee="")

    @pytest.mark.parametrize("name,why", [("zed", "is not a user of this wiki"), ("carol", "is a viewer"),
                                          ("dan", "account is disabled")])
    def test_a_name_that_cant_be_assigned_is_a_tool_error(self, mcp, name, why):
        assert why in mcp("create_task", title="No", assignee=name)
        assert "Task: No" not in mcp("search_tasks")

    def test_a_private_task_has_none(self, mcp):
        assert "private task" in mcp("create_task", title="Mine", private=True, assignee="bob")

    def test_search_filters_by_assignee(self, mcp):
        mcp("create_task", title="Alices", assignee="me")
        mcp("create_task", title="Bobs", assignee="bob")
        mcp("create_task", title="Nobodys")
        found = lambda **args: sorted(t for t in ("Alices", "Bobs", "Nobodys") if f"Task: {t}" in mcp("search_tasks", **args))
        assert found(assignee="me") == ["Alices"]
        assert found(assignee="bob") == ["Bobs"]
        assert found(assignee="none") == ["Nobodys"]
        assert found(assignee="me|bob") == ["Alices", "Bobs"]
        assert found(assignee="-bob") == ["Alices", "Nobodys"]
        assert found() == ["Alices", "Bobs", "Nobodys"]

    def test_a_former_assignee_is_said_to_be_one(self, mcp, monkeypatch):
        mcp("create_task", title="Bobs", assignee="bob")
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"disabled": "yes"}})
        assert "assigned to: bob (who can no longer be given tasks" in mcp("search_tasks", assignee="none")

    def test_a_new_task_gets_the_default_lists_assignee(self, mcp, wiki_dir):
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=me pin=default]]\n")
        assert "assigned to: alice (the user)" in mcp("create_task", title="Mine by default")
        assert "assigned to: bob" in mcp("create_task", title="Said otherwise", assignee="bob")
        assert "assigned to" not in mcp("create_task", title="No one", assignee="none")

    def test_without_users_search_says_there_are_no_assignees(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "MCP_TOKEN", "tok")
        resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer tok"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search_tasks", "arguments": {"assignee": "me"}}})
        assert "no users" in resp.get_json()["result"]["content"][0]["text"]


class TestHandingOn:
    """The tasks of a user who can no longer be given any: the Users tab
    says how many, with a link to them."""

    def test_the_users_tab_counts_them(self, alice, monkeypatch):
        for title in ("One", "Two"):
            new(alice, title=title, assignee="bob")
        done = new(alice, title="Done", assignee="bob")
        put(alice, done, completed=True)
        body = alice.get("/admin/users", headers=HOST).get_data(as_text=True)
        assert "to hand on" not in body                    # bob can be given tasks
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"disabled": "yes"}})
        body = alice.get("/admin/users", headers=HOST).get_data(as_text=True)
        assert '<a class="user-tasks" href="/tasks?view=open&amp;assignee=bob"' in body
        assert ">2 open tasks to hand on</a>" in body
        # Made a viewer instead: the same.
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"role": "viewer"}})
        assert ">2 open tasks to hand on</a>" in alice.get("/admin/users", headers=HOST).get_data(as_text=True)
