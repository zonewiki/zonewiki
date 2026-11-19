"""A task's assignee (docs/spec/users.md, Assigning tasks): one user per
shared task, who can be assigned, what a name that no longer can counts
as, the history, and the names' route."""

import json
import re

import pytest

from tests import settings
from tests.conftest import HOST, make_list

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
    team(client)
    return client


def team(client):
    """The shared list team, where new() puts this client's tasks from now
    on: with users a task has an assignee only in a shared list (one
    without a list is in its maker's Inbox, private)."""
    assert make_list(client, "team", headers=HOST).status_code == 302
    client.in_team = True


def new(client, **fields):
    if getattr(client, "in_team", False):
        fields.setdefault("list", "team")
    resp = client.post("/api/tasks", json={"title": "Call Sam", **fields}, headers=HOST)
    assert resp.status_code == 201, resp.data.decode()
    return resp.get_json()


def put(client, task, **fields):
    return client.put(f"/api/tasks/{task['id']}", json=fields, headers=HOST)


def stored(wiki_dir, task):
    """The task as its file has it."""
    for path in (wiki_dir / "_tasks").rglob("tasks.json"):       # the Inbox's and each list's
        for t in json.loads(path.read_text()):
            if t["id"] == task["id"]:
                return t
    raise LookupError(task["id"])


def events(task):
    return [(e["event"], e.get("from"), e.get("to"), e.get("by")) for e in task["history"]]


class TestAssigning:

    def test_a_new_task_can_start_assigned(self, alice, wiki_dir):
        task = new(alice, assignee="bob", tags=["work"])
        assert task["assignee"] == "bob"
        assert events(task) == [("created", None, None, "alice")]   # its first assignee is no change
        keys = list(stored(wiki_dir, task))
        assert keys[keys.index("tags") + 1:keys.index("tags") + 3] == ["list", "assignee"]

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
        resp = alice.post("/api/tasks", json={"title": "No", "list": "team", "assignee": name}, headers=HOST)
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
        make_list(alice, "own", private=True, headers=HOST)
        resp = alice.post("/api/tasks", json={"title": "Mine", "list": "own", "assignee": "bob"}, headers=HOST)
        assert resp.status_code == 400 and "private task" in resp.get_json()["error"]
        mine = new(alice, list="own")
        assert put(alice, mine, assignee="alice").status_code == 400
        assert put(alice, mine, assignee="").status_code == 200       # none is what it has
        # Out of its private list it is in her Inbox, private still: no assignee there.
        resp = put(alice, mine, list="", share=True, assignee="bob")
        assert resp.status_code == 400 and "private task" in resp.get_json()["error"]
        # Shared (into a shared list) and assigned in one write.
        shared = put(alice, mine, list="team", share=True, assignee="bob").get_json()
        assert shared["private"] is False and shared["assignee"] == "bob"

    @pytest.mark.parametrize("list_", [None, "", "inbox", "none"])
    def test_an_inbox_task_has_none(self, alice, wiki_dir, list_):
        """A task without a list is in its maker's Inbox, theirs alone: it
        can't be given to anyone, not even to themselves, when made or later."""
        named = {} if list_ is None else {"list": list_}
        for who in ("bob", "alice", "me"):
            resp = alice.post("/api/tasks", json={"title": "No", "assignee": who, **named}, headers=HOST)
            assert resp.status_code == 400, who
            assert "A private task is yours alone. Put it in a shared list to assign it" in resp.get_json()["error"]
        assert alice.get("/api/tasks", headers=HOST).get_json() == []
        mine = new(alice, list=list_)
        assert mine["private"] is True and "list" not in mine and "assignee" not in mine
        resp = put(alice, mine, assignee="bob")
        assert resp.status_code == 400 and "shared list" in resp.get_json()["error"]
        assert put(alice, mine, assignee="").status_code == 200       # none is what it has
        # An empty assignee on a new one is no assignee.
        assert "assignee" not in new(alice, list=list_, assignee="")
        # Filed into a shared list (share), it takes one in the same write.
        filed = put(alice, mine, list="team", share=True, assignee="me").get_json()
        assert filed["private"] is False and filed["list"] == "team" and filed["assignee"] == "alice"
        assert stored(wiki_dir, mine)["assignee"] == "alice"

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

    def test_me_and_none_are_words_not_users(self, client, wiki_dir, monkeypatch):
        """me is whoever is signed in and none no one; no user is called
        either (a settings file with one is refused: TestReservedUserNames
        in go/assignee_test.go)."""
        monkeypatch.setattr(settings, "USERS", dict(USERS))
        monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
        login(client, "alice")
        team(client)
        assert new(client, assignee="me")["assignee"] == "alice"
        assert "assignee" not in new(client, assignee="none")


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
        """And those here who can't be given tasks, a viewer or a disabled
        user: the toast counts them all as people who can't be given tasks
        here, not as people who are not users of this wiki."""
        export = {"zone_tasks": 1, "tasks": [{"title": "Theirs", "assignee": "zed"}, {"title": "Ours", "assignee": "bob"},
                                             {"title": "Viewer's", "assignee": "carol"},
                                             {"title": "Disabled's", "assignee": "dan"}]}
        resp = alice.post("/api/tasks/import", json={"file": export}, headers=HOST)
        assert resp.status_code == 200 and resp.get_json()["unassigned"] == 3      # the toast says so
        got = {t["title"]: t.get("assignee") for t in alice.get("/api/tasks", headers=HOST).get_json()}
        assert got == {"Theirs": None, "Ours": "bob", "Viewer's": None, "Disabled's": None}


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
        return json.loads(html.split('class="tasks-initial">', 1)[1].split("</script>", 1)[0])

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
    the export and the settings (the embed's own "+ New" assigns as its
    filter says, in the browser)."""

    @pytest.fixture()
    def tasks(self, alice):
        from datetime import date
        today = date.today().isoformat()
        for title, who in (("A1", "alice"), ("A2", "alice"), ("B1", "bob"), ("N1", None)):
            new(alice, title=title, due_date=today, **({"assignee": who} if who else {}))
        return alice

    def today(self, client, wiki_dir, params):
        """How many tasks an embed with these params shows, as its export has them."""
        query = dict(w.split("=", 1) for w in params.split())
        return len(client.get("/api/tasks/export", query_string=query, headers=HOST).get_json()["tasks"])

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
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=me]]\n")
        assert "data-init-assignee" not in client.get("/page/team", headers=HOST).get_data(as_text=True)

    def test_the_page_tells_the_browser(self, alice, wiki_dir):
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=@Me]]\n")
        html = alice.get("/page/team", headers=HOST).get_data(as_text=True)
        assert 'data-init-assignee="me"' in html
        assert 'name="assignee"' in html.split('id="task-settings-modal', 1)[1]     # the settings dialog's row

    def test_a_pinned_filter_gives_new_tasks_no_assignee(self, alice, wiki_dir):
        """pin= in an embed means nothing since pins are of lists, and a list
        has no assignee: New task from anywhere assigns no one."""
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=me pin=default]]\n")
        assert "data-tasks-assignee" not in alice.get("/page/index", headers=HOST).get_data(as_text=True)

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


class TestMyTasks:
    """My tasks with users (docs/spec/tasks.md, Lists → My tasks): what is
    yours: your own tasks (your Inbox and your private lists, but a list
    left out of it) and the shared tasks assigned to you, wherever they
    are. A pin changes nothing. The Tasks links open it and count it."""

    @pytest.fixture()
    def team(self, alice, wiki_dir):
        """alice pinned the shared list team: a task of hers, one of bob's
        and one of no one's in it. A shared list she hasn't pinned, side,
        with a task of hers and one of no one's; her private list own, not
        pinned, with a task; her private list someday, left out of My tasks
        (home=off), with a task; a task in her Inbox, and one in bob's. All
        due today."""
        from datetime import date
        today = date.today().isoformat()
        for name, private in (("side", False), ("own", True), ("someday", True)):
            make_list(alice, name, private=private, headers=HOST)
        self.pin(alice, "team")
        assert self.home(alice, "someday", "off").status_code == 200
        for title, fields in (("A1", {"list": "team", "assignee": "alice"}), ("B1", {"list": "team", "assignee": "bob"}),
                              ("N1", {"list": "team"}), ("A2", {"list": "side", "assignee": "alice"}),
                              ("N2", {"list": "side"}), ("P1", {"list": "own"}), ("S1", {"list": "someday"}),
                              ("I1", {"list": ""})):
            new(alice, title=title, due_date=today, **fields)
        login(alice, "bob")
        new(alice, title="BI", due_date=today, list="")
        login(alice, "alice")
        return alice

    pin = staticmethod(lambda client, name, how="1": client.post(
        "/tasks/pin-list", data={"list": name, "pin": how}, headers=HOST))
    home = staticmethod(lambda client, name, value: client.post(
        "/api/tasks/settings", json={"list": name, "set": {"home": value}}, headers=HOST))
    count = staticmethod(lambda client: client.get("/api/tasks/due-count", headers=HOST).get_json()["count"])
    page = staticmethod(lambda client, url: client.get(url, headers=HOST).get_data(as_text=True))
    titles = staticmethod(lambda client, query="mine=1": sorted(t["title"] for t in client.get(
        "/api/tasks/export?" + query, headers=HOST).get_json()["tasks"]))

    @staticmethod
    def line(html):
        """The line under My tasks' heading, as text."""
        line = html.split('class="tasks-list-home tasks-mine-line">', 1)[1].split("</p>", 1)[0]
        return re.sub(r"<[^>]+>", "", line)

    def test_what_it_holds(self, team):
        # Hers in the pinned list and in the one she hasn't pinned, her
        # private list's and her Inbox's; not bob's, not no one's (a shared
        # list's backlog), not the task of the list she left out, and
        # nothing of bob's Inbox.
        assert self.titles(team) == ["A1", "A2", "I1", "P1"]
        assert self.count(team) == 4
        index = self.page(team, "/page/index")
        assert 'href="/tasks?mine=1"' in index and "data-tasks-assignee" not in index
        html = self.page(team, "/tasks?mine=1")
        assert "<h1>My tasks</h1>" in html and 'data-init-mine="assign" data-init-out="someday"' in html
        assert "data-init-assignee" not in html and "mine-on-btn" not in html and "data-init-lists" not in html
        assert self.line(html) == "Your own tasks and what is assigned to you, but the lists left out of it (#someday). Given to others: 1"
        assert '<a href="/tasks?list=someday">#someday</a>' in html
        # The list itself shows everyone's.
        assert self.titles(team, "list=team") == ["A1", "B1", "N1"]
        assert "data-init-mine" not in self.page(team, "/tasks?list=team")
        # A task given to someone else leaves it, one given to her enters it.
        tasks = {t["title"]: t for t in team.get("/api/tasks", headers=HOST).get_json()}
        assert put(team, tasks["A1"], assignee="bob").status_code == 200
        assert put(team, tasks["N2"], assignee="alice").status_code == 200
        assert self.titles(team) == ["A2", "I1", "N2", "P1"]
        # An Inbox task filed into a shared list as no one's is the list's:
        # it leaves My tasks (filed without a word of who has it, it is hers).
        assert put(team, tasks["I1"], list="team", share=True, assignee=None).status_code == 200
        assert self.titles(team) == ["A2", "N2", "P1"]
        assert put(team, tasks["I1"], assignee="me").status_code == 200
        assert self.titles(team) == ["A2", "I1", "N2", "P1"]
        # The closed ones it counts are its own.
        for title in ("A1", "B1", "N2", "P1"):
            assert put(team, tasks[title], completed=True).status_code == 200
        assert team.get("/api/tasks/closed-count?mine=1", headers=HOST).get_json()["count"] == 2
        assert self.count(team) == 2

    def test_a_pin_changes_nothing(self, team):
        """A pin is a pill of the switcher: pinning, unpinning and the
        pins' order take no task into My tasks and none out of it."""
        base = ["A1", "A2", "I1", "P1"]
        for name, how in (("own", "1"), ("side", "1"), ("someday", "first"), ("team", "0"), ("own", "0"),
                          ("side", "0"), ("someday", "0")):
            assert self.pin(team, name, how).status_code == 302
            assert self.titles(team) == base and self.count(team) == 4, (name, how)

    def test_a_list_left_out(self, team, wiki_dir):
        """home=off, a setting of one's own list: its tasks are left out of
        My tasks and of the number on the Tasks links; taken back, they are
        in it again. On a shared list it changes nothing for who can be
        given tasks: a shared task is in My tasks by its assignee."""
        assert self.home(team, "own", "off").status_code == 200
        assert '{"name": "own", "home": "off"}' in (wiki_dir / "private/alice/_tasks/lists.json").read_text()
        assert self.titles(team) == ["A1", "A2", "I1"] and self.count(team) == 3
        html = self.page(team, "/tasks?mine=1")
        assert 'data-init-out="own|someday"' in html
        assert self.line(html) == "Your own tasks and what is assigned to you, but the lists left out of it (#own, #someday). Given to others: 1"
        # The list says so, and still shows its tasks.
        own = self.page(team, "/tasks?list=own")
        assert 'Left out of <a href="/tasks?mine=1">My tasks</a>' in own and self.titles(team, "list=own") == ["P1"]
        assert "Left out of" not in self.page(team, "/tasks?list=team")
        for bad in ("on", "yes", "1"):
            assert self.home(team, "own", bad).status_code == 400, bad
        assert self.home(team, "own", "").status_code == 200
        assert self.home(team, "someday", "").status_code == 200
        assert self.titles(team) == ["A1", "A2", "I1", "P1", "S1"] and self.count(team) == 5
        assert self.line(self.page(team, "/tasks?mine=1")) == "Your own tasks and what is assigned to you. Given to others: 1"
        # A shared list left out: hers in it stays hers.
        assert self.home(team, "side", "off").status_code == 200
        assert self.titles(team) == ["A1", "A2", "I1", "P1", "S1"]
        html = self.page(team, "/tasks?mine=1")
        assert self.line(html) == "Your own tasks and what is assigned to you. Given to others: 1"
        assert "Left out of" not in self.page(team, "/tasks?list=side")
        # The Inbox can't be left out: it is no list with settings.
        assert self.home(team, "inbox", "off").status_code in (400, 404)
        assert self.titles(team) == ["A1", "A2", "I1", "P1", "S1"]

    def test_it_is_each_users_own(self, team):
        login(team, "bob")
        # bob: his tasks wherever they are, and his own Inbox; nothing of alice's.
        assert self.titles(team) == ["B1", "BI"] and self.count(team) == 2
        html = self.page(team, "/tasks?mine=1")
        assert 'data-init-mine="assign" data-init-out=""' in html and "data-init-lists" not in html
        assert self.line(html) == "Your own tasks and what is assigned to you."
        assert "#someday" not in html and "list=someday" not in html    # alice's private list is nothing he hears of
        self.pin(team, "side")
        assert self.titles(team) == ["B1", "BI"]
        login(team, "alice")
        assert self.titles(team) == ["A1", "A2", "I1", "P1"]

    def test_assignment_doesnt_count_for_who_cant_be_given_tasks(self, team):
        # A viewer: every task she sees, whoever has it; no one's private
        # task, so no Inbox's.
        login(team, "carol")
        assert self.titles(team) == ["A1", "A2", "B1", "N1", "N2"] and self.count(team) == 5
        html = self.page(team, "/tasks?mine=1")
        assert 'data-init-mine="lists" data-init-out=""' in html and "data-settings-" not in html
        assert self.line(html) == "Every open task."
        assert team.post("/api/tasks/settings", json={"mine": True, "was": "", "set": {"view": "open"}},
                         headers=HOST).status_code == 403
        assert self.home(team, "side", "off").status_code == 403
        # For her a shared list that is left out is: no task is hers by assignment.
        login(team, "alice")
        assert self.home(team, "side", "off").status_code == 200
        login(team, "carol")
        assert self.titles(team) == ["A1", "B1", "N1"] and self.count(team) == 3
        html = self.page(team, "/tasks?mine=1")
        assert 'data-init-out="side"' in html
        assert self.line(html) == "Every open task, but the lists left out of it (#side)."
        assert 'Left out of <a href="/tasks?mine=1">My tasks</a>' in self.page(team, "/tasks?list=side")

    def test_without_users_every_task_is_in_it(self, client, wiki_dir):
        for name in ("team", "later"):
            make_list(client, name, headers=HOST)
        self.pin(client, "team")
        for title, fields in (("T1", {"list": "team"}), ("L1", {"list": "later"}), ("I1", {})):
            new(client, title=title, due_date="2020-01-01", **fields)
        html = self.page(client, "/tasks?mine=1")
        assert 'data-init-mine="lists" data-init-out=""' in html and "data-init-lists" not in html
        assert self.line(html) == "Every open task."
        assert self.titles(client) == ["I1", "L1", "T1"] and self.count(client) == 3
        # But a list left out; pinning it brings nothing back.
        assert self.home(client, "later", "off").status_code == 200
        assert '{"name": "later", "home": "off"}' in (wiki_dir / "_tasks/lists.json").read_text()
        self.pin(client, "later")
        html = self.page(client, "/tasks?mine=1")
        assert 'data-init-mine="lists" data-init-out="later"' in html
        assert self.line(html) == "Every open task, but the lists left out of it (#later)."
        assert self.titles(client) == ["I1", "T1"] and self.count(client) == 2
        assert 'Left out of <a href="/tasks?mine=1">My tasks</a>' in self.page(client, "/tasks?list=later")

    def test_only_my_tasks_is_no_setting_anymore(self, team, wiki_dir):
        # Until 2026-11 "mine": true in a user's registry narrowed Pinned to
        # their own tasks, set by POST /tasks/pin-list with mine. It is read
        # as nothing and written back no more; the route takes no mine.
        path = wiki_dir / "private/alice/_tasks/lists.json"
        path.write_text(path.read_text().replace("\n}\n", ',\n  "mine": true\n}\n'))
        assert '"mine": true' in path.read_text()
        assert self.titles(team) == ["A1", "A2", "I1", "P1"]
        assert team.post("/tasks/pin-list", data={"mine": "1"}, headers=HOST).status_code in (400, 404)
        self.pin(team, "side")
        assert '"mine"' not in path.read_text()

    def test_its_settings_are_each_users_own(self, team, wiki_dir):
        save = lambda: team.post("/api/tasks/settings", json={"mine": True, "was": "", "set": {"view": "open"}}, headers=HOST)
        assert save().status_code == 200
        assert '"mine": {"view": "open"}' in (wiki_dir / "private/alice/_tasks/lists.json").read_text()
        assert '"mine"' not in (wiki_dir / "_tasks/lists.json").read_text()
        assert 'data-init-view="open"' in self.page(team, "/tasks?mine=1")
        login(team, "bob")
        assert 'data-init-view=""' in self.page(team, "/tasks?mine=1")

    def test_the_assistants_new_task_is_in_the_inbox(self, team, wiki_dir, monkeypatch):
        # Without a list: the user's Inbox, as c t; Zone assigns it to no
        # one, and whatever is pinned first takes nothing.
        monkeypatch.setattr(settings, "USERS", {**USERS, "alice": ("alice-pw", "alice-token")})

        def mcp(tool, **args):
            resp = team.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
            return "".join(c["text"] for c in resp.get_json()["result"]["content"])
        assert "assigned to" not in mcp("create_task", title="From the assistant")
        # A task for someone needs a shared list: the error names them, and no task is made.
        refused = mcp("create_task", title="Said so", assignee="bob")
        assert "has to be in a shared list" in refused and "Pass list: side, team" in refused
        assert "own" not in refused.split("Pass list:", 1)[1] and "someday" not in refused
        assert "assigned to: bob" in mcp("create_task", title="Said so, in team", assignee="bob", list="team")
        login(team, "alice")
        made = {t["title"]: t for t in team.get("/api/tasks", headers=HOST).get_json()}
        assert "Said so" not in made
        inbox = made["From the assistant"]
        assert "list" not in inbox and inbox["private"] is True and "assignee" not in inbox
        assert "From the assistant" in self.titles(team) and "Said so, in team" not in self.titles(team)
        login(team, "bob")
        seen = [t["title"] for t in team.get("/api/tasks", headers=HOST).get_json()]
        assert "From the assistant" not in seen and "Said so, in team" in seen

    def test_a_private_task_is_mine_and_not_no_ones(self, team):
        # The filter word @me, as before: a private task is its user's,
        # in a list of hers or in her Inbox.
        export = lambda who: self.titles(team, f"assignee={who}")
        assert export("me") == ["A1", "A2", "I1", "P1", "S1"] and export("none") == ["N1", "N2"]


class TestASharedTaskIsInAList:
    """With users a shared task is always in a list (docs/spec/tasks.md,
    Lists): it never loses its list for none, and where one would (its list
    deleted, a restore or an import whose list is gone, a task left without
    one by an older Zone) it goes to the shared list general, made when it
    isn't there. A private task without a list is in its user's Inbox."""

    @staticmethod
    def tasks(client):
        return {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}

    @staticmethod
    def delete(client, name, to="", **more):
        return client.post("/tasks/delete-list", data={"list": name, "to": to, **more}, headers=HOST).headers["Location"]

    @staticmethod
    def targets(client, name):
        """The Delete… dialog's choices for the list, as (value, text)."""
        html = client.get(f"/tasks?list={name}", headers=HOST).get_data(as_text=True)
        select = html.split('id="list-delete-to"', 1)[1].split("</select>", 1)[0]
        return re.findall(r'<option value="([^"]*)">([^<]*)</option>', select)

    def test_it_cant_leave_its_list_for_none(self, alice, wiki_dir):
        task = new(alice, title="Shared")
        for none in ("", None, "inbox", "none", " #Inbox "):
            for share in ({}, {"share": True}):
                resp = put(alice, task, list=none, **share)
                assert resp.status_code == 400, none
                assert resp.get_json()["error"] == "A shared task is always in a list: move it to another shared list"
        resp = alice.post("/api/tasks/batch-update", json={"tasks": [{"id": task["id"], "list": ""}]}, headers=HOST)
        assert resp.status_code == 400 and "always in a list" in resp.get_json()["error"]
        assert stored(wiki_dir, task)["list"] == "team"
        # To another shared list it moves, and unchanged is no error.
        make_list(alice, "side", headers=HOST)
        assert put(alice, task, list="side").get_json()["list"] == "side"
        assert put(alice, task, list="side").status_code == 200
        # Without users a task leaves its list for the Inbox: test_tasks.py.

    def test_a_deleted_shared_lists_tasks_go_to_general(self, alice, wiki_dir):
        make_list(alice, "side", headers=HOST)
        make_list(alice, "own", private=True, headers=HOST)
        for title, fields in (("One", {"list": "side", "assignee": "bob"}), ("Two", {"list": "side"}), ("Mine", {"list": "own"})):
            new(alice, title=title, **fields)
        done = new(alice, title="Done", list="side")
        put(alice, done, completed=True)
        # The dialog: no list named is #general for a shared list, the Inbox for a private one.
        assert self.targets(alice, "side") == [("", "#general"), ("team", "#team")]
        assert self.targets(alice, "own") == [("", "Your Inbox"), ("side", "#side (shared)"), ("team", "#team (shared)")]
        assert self.delete(alice, "side") == "/tasks"
        registry = json.loads((wiki_dir / "_tasks/lists.json").read_text())
        assert [l["name"] for l in registry["lists"]] == ["team", "general"]
        tasks = self.tasks(alice)
        assert tasks["One"]["list"] == tasks["Two"]["list"] == "general" and tasks["One"]["assignee"] == "bob"
        assert tasks["One"]["private"] is False
        closed = json.loads((wiki_dir / "_tasks/lists/general/closed.json").read_text())
        assert [(t["title"], t["list"]) for t in closed] == [("Done", "general")]
        login(alice, "bob")
        assert self.tasks(alice)["One"]["list"] == "general"        # everyone's, as its tasks were
        login(alice, "alice")
        # #general itself, while it has tasks, needs a list named: the dialog offers none without.
        assert self.targets(alice, "general") == [("team", "#team")]
        assert "delete_error=" in self.delete(alice, "general") and "general" in (wiki_dir / "_tasks/lists.json").read_text()
        assert self.tasks(alice)["One"]["list"] == "general"
        assert self.delete(alice, "general", to="team") == "/tasks?list=team"
        assert self.tasks(alice)["One"]["list"] == "team"
        # Closing them instead leaves no open task to place.
        assert self.delete(alice, "team", close="1") == "/tasks"
        assert "One" not in self.tasks(alice)
        # A private list's go to her Inbox: private still, no share asked.
        assert self.delete(alice, "own") == "/tasks"
        mine = self.tasks(alice)["Mine"]
        assert mine["private"] is True and "list" not in mine
        assert "Mine" not in (wiki_dir / "_tasks/tasks.json").read_text()

    def test_a_restored_task_whose_list_is_gone(self, alice, wiki_dir):
        make_list(alice, "own", private=True, headers=HOST)
        snaps = [{"id": "r-1", "title": "In team", "list": "team", "assignee": "bob"},
                 {"id": "r-2", "title": "Gone", "list": "long-gone", "assignee": "bob"},
                 {"id": "r-3", "title": "Stray"},
                 {"id": "r-4", "title": "Own", "list": "own", "private": True},
                 {"id": "r-5", "title": "Own gone", "list": "long-gone", "private": True, "assignee": "bob"},
                 {"id": "r-6", "title": "Inbox", "private": True},
                 {"id": "r-7", "title": "Private, a shared list's name", "list": "team", "private": True},
                 {"id": "r-8", "title": "Shared, her list's name", "list": "own"}]
        assert alice.post("/api/tasks/restore", json={"tasks": snaps}, headers=HOST).status_code == 201
        got = {t["title"]: (t.get("list"), t["private"], t.get("assignee")) for t in self.tasks(alice).values()}
        assert got == {"In team": ("team", False, "bob"), "Gone": ("general", False, "bob"), "Stray": ("general", False, None),
                       "Own": ("own", True, None), "Own gone": (None, True, None), "Inbox": (None, True, None),
                       "Private, a shared list's name": (None, True, None), "Shared, her list's name": ("general", False, None)}
        # #general is made, shared; no list "private" is made for her any more.
        assert '{"name": "general"}' in (wiki_dir / "_tasks/lists.json").read_text()
        assert '"private"' not in (wiki_dir / "private/alice/_tasks/lists.json").read_text()
        shared = (wiki_dir / "_tasks/tasks.json").read_text()
        assert "Own gone" not in shared and '"Inbox"' not in shared and "Private, a shared" not in shared
        login(alice, "bob")
        assert sorted(self.tasks(alice)) == ["Gone", "In team", "Shared, her list's name", "Stray"]

    def test_an_imported_task(self, alice, wiki_dir):
        imp = lambda file, **body: alice.post("/api/tasks/import", json={"file": file, **body}, headers=HOST)
        file = [{"title": "Theirs", "list": "elsewhere", "assignee": "bob"}, {"title": "Ours", "list": "team", "assignee": "bob"},
                {"title": "Hers", "private": True, "list": "elsewhere"}]
        # Into All tasks: where each says, as a restore.
        assert imp(file).get_json()["added"] == 3
        got = {t["title"]: (t.get("list"), t["private"], t.get("assignee")) for t in self.tasks(alice).values()}
        assert got == {"Theirs": ("general", False, "bob"), "Ours": ("team", False, "bob"), "Hers": (None, True, None)}
        # Into the Inbox: hers alone, whatever the file says, and no one's.
        alice.post("/api/tasks/batch-delete", json={"ids": [t["id"] for t in self.tasks(alice).values()]}, headers=HOST)
        for name in ("inbox", "none"):
            assert imp([{"title": f"Into {name}", "list": "team", "assignee": "bob"}], list=name).get_json()["added"] == 1
            task = self.tasks(alice)[f"Into {name}"]
            assert task["private"] is True and "list" not in task and "assignee" not in task
        assert "Into inbox" not in (wiki_dir / "_tasks/tasks.json").read_text()
        # Into a shared list: there; one exported private stays private, in her Inbox.
        assert imp([{"title": "Listed", "assignee": "bob"}, {"title": "Was private", "private": True}], list="team").get_json()["added"] == 2
        tasks = self.tasks(alice)
        assert tasks["Listed"]["list"] == "team" and tasks["Listed"]["assignee"] == "bob"
        assert tasks["Was private"]["private"] is True and "list" not in tasks["Was private"]
        login(alice, "bob")
        assert sorted(self.tasks(alice)) == ["Listed"]

    def test_a_task_left_without_a_list_is_filed_when_zone_starts(self, alice, wiki_dir, monkeypatch):
        """A shared task in no list (a wiki whose users were turned on
        later, a file edited by hand) goes to #general at the next start,
        pinned for the wiki; a user's own without a list is their Inbox."""
        mine = new(alice, title="Hers", list="")
        path = wiki_dir / "_tasks" / "tasks.json"
        stray = {**json.loads((wiki_dir / "private/alice/_tasks/tasks.json").read_text())[0], "id": "stray-1", "title": "Stray"}
        path.write_text(json.dumps(json.loads(path.read_text()) + [stray]))
        assert "list" not in self.tasks(alice)["Stray"] and self.tasks(alice)["Stray"]["private"] is False
        # Until then it is no one's: in nobody's My tasks.
        assert [t["id"] for t in alice.get("/api/tasks/export?mine=1", headers=HOST).get_json()["tasks"]] == [mine["id"]]
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"role": "admin"}})   # a restart
        tasks = self.tasks(alice)
        assert tasks["Stray"]["list"] == "general" and tasks["Stray"]["private"] is False
        assert tasks["Hers"]["private"] is True and "list" not in tasks["Hers"]
        registry = json.loads((wiki_dir / "_tasks/lists.json").read_text())
        assert registry["version"] == 3 and "general" in [l["name"] for l in registry["lists"]] and "general" in registry["pinned"]
        assert [t["id"] for t in alice.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]] == [mine["id"]]
        assert [t["id"] for t in alice.get("/api/tasks/export?list=general", headers=HOST).get_json()["tasks"]] == ["stray-1"]

    def test_a_shared_lists_backlog_is_counted_on_its_page(self, alice, wiki_dir):
        """The tasks of a shared list that no one has: "N unassigned" under
        its heading, a link to them."""
        assert "tasks-unassigned" not in alice.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
        for title, who in (("A", "alice"), ("B", "bob"), ("N1", None), ("N2", None)):
            new(alice, title=title, **({"assignee": who} if who else {}))
        html = alice.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
        link = html.split('class="tasks-unassigned"', 1)[0].rsplit("<a ", 1)[1] + html.split('class="tasks-unassigned"', 1)[1].split("</a>", 1)[0]
        assert 'href="/tasks?list=team&amp;assignee=none"' in link and link.endswith(">2 unassigned")
        assert sorted(t["title"] for t in alice.get("/api/tasks/export?list=team&assignee=none", headers=HOST).get_json()["tasks"]) == ["N1", "N2"]
        # A private list has no backlog: its tasks are its user's.
        make_list(alice, "own", private=True, headers=HOST)
        new(alice, title="Mine", list="own")
        assert "tasks-unassigned" not in alice.get("/tasks?list=own", headers=HOST).get_data(as_text=True)


class TestAssistant:
    """The MCP tools: create_task and update_task take an assignee,
    search_tasks filters by it, and a task's text names it."""

    @pytest.fixture()
    def mcp(self, bare_mcp):
        """bare_mcp in a wiki with the shared list team: a task has an
        assignee only in a shared list."""
        assert "Created the list #team" in bare_mcp("create_task_list", name="team")
        return bare_mcp

    @pytest.fixture()
    def bare_mcp(self, client, wiki_dir, monkeypatch):
        """Calls a tool as alice's assistant; returns the answer's text."""
        monkeypatch.setattr(settings, "USERS", {**USERS, "alice": ("alice-pw", "alice-token")})
        monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))

        def call(tool, **args):
            resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
            return "".join(c["text"] for c in resp.get_json()["result"]["content"])
        return call

    def test_create_and_update_assign(self, mcp):
        text = mcp("create_task", title="Call Sam", assignee="bob", list="team")
        assert "assigned to: bob" in text and "bob (the user)" not in text and "from: alice (the user)" in text
        task_id = text.split("id: ", 1)[1].split()[0]
        assert "assigned to: alice (the user)" in mcp("update_task", id=task_id, assignee="me")
        assert "assigned to" not in mcp("update_task", id=task_id, assignee="none")
        assert "assigned to: bob" in mcp("update_task", id=task_id, assignee="@Bob")
        assert "assigned to" not in mcp("update_task", id=task_id, assignee="")

    @pytest.mark.parametrize("name,why", [("zed", "is not a user of this wiki"), ("carol", "is a viewer"),
                                          ("dan", "account is disabled")])
    def test_a_name_that_cant_be_assigned_is_a_tool_error(self, mcp, name, why):
        assert why in mcp("create_task", title="No", assignee=name, list="team")
        assert "Task: No" not in mcp("search_tasks")

    def test_a_private_task_has_none(self, mcp):
        assert "Created the list #own" in mcp("create_task_list", name="own", private=True)
        assert "private task" in mcp("create_task", title="Mine", list="own", assignee="bob")
        assert "Task: Mine" not in mcp("search_tasks")

    @pytest.mark.parametrize("named", [{}, {"list": ""}, {"list": "inbox"}, {"list": "none"}])
    def test_a_task_for_someone_needs_a_shared_list(self, mcp, named):
        """Without a list a new task is in the user's Inbox, which only they
        see: with an assignee that is a tool error naming the shared lists,
        the user's own name too."""
        mcp("create_task_list", name="own", private=True)
        mcp("create_task_list", name="side")
        for who in ("bob", "me", "@Alice"):
            text = mcp("create_task", title="For someone", assignee=who, **named)
            assert "A task assigned to someone has to be in a shared list" in text, who
            assert text.rstrip().endswith("Pass list: side, team"), text
        assert "Task: For someone" not in mcp("search_tasks")
        # No assignee, or none: the Inbox takes it.
        assert "assigned to" not in mcp("create_task", title="Nobody's", assignee="none", **named)
        assert "Task: Nobody's" in mcp("search_tasks", list="inbox")

    def test_a_task_for_someone_in_a_wiki_without_a_shared_list(self, bare_mcp):
        bare_mcp("create_task_list", name="own", private=True)
        text = bare_mcp("create_task", title="For bob", assignee="bob")
        assert "this wiki has none: make one with create_task_list" in text and "own" not in text
        assert "Task: For bob" not in bare_mcp("search_tasks")

    def test_search_filters_by_assignee(self, mcp):
        mcp("create_task", title="Alices", assignee="me", list="team")
        mcp("create_task", title="Bobs", assignee="bob", list="team")
        mcp("create_task", title="Nobodys", list="team")   # shared, and no one's
        mcp("create_task", title="Inboxed")                # in her Inbox: her own, as @me reads it
        found = lambda **args: sorted(t for t in ("Alices", "Bobs", "Nobodys", "Inboxed") if f"Task: {t}" in mcp("search_tasks", **args))
        assert found(assignee="me") == ["Alices", "Inboxed"]
        assert found(assignee="bob") == ["Bobs"]
        assert found(assignee="none") == ["Nobodys"]
        assert found(assignee="me|bob") == ["Alices", "Bobs", "Inboxed"]
        assert found(assignee="-bob") == ["Alices", "Inboxed", "Nobodys"]
        assert found() == ["Alices", "Bobs", "Inboxed", "Nobodys"]

    def test_a_former_assignee_is_said_to_be_one(self, mcp, monkeypatch):
        mcp("create_task", title="Bobs", assignee="bob", list="team")
        monkeypatch.setattr(settings, "USER_SETTINGS", {**USER_SETTINGS, "bob": {"disabled": "yes"}})
        assert "assigned to: bob (who can no longer be given tasks" in mcp("search_tasks", assignee="none")

    def test_a_new_task_is_assigned_only_when_told(self, mcp, wiki_dir):
        (wiki_dir / "team.md").write_text("# Team\n\n[[tasks: assignee=me pin=default]]\n")
        assert "assigned to" not in mcp("create_task", title="No one by default")
        assert "assigned to: bob" in mcp("create_task", title="Said so", assignee="bob", list="team")
        assert "assigned to: alice (the user)" in mcp("create_task", title="Mine", assignee="me", list="team")

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


class TestTheWordOfWhatAPageHolds:
    """Zone-Tasks (docs/spec/tasks.md, Keeping lists current): another word after a
    write to a list the page holds (?held=), not after one elsewhere or
    to another user's own tasks."""

    def word(self, client, held=""):
        return client.get("/api/tasks/due-count" + (f"?held={held}" if held else ""), headers=HOST).headers["Zone-Tasks"]

    def test_a_write_elsewhere_leaves_it(self, alice):
        client = alice
        assert make_list(client, "side", headers=HOST).status_code == 302
        new(client, title="The first")        # the first write also makes the file of the tasks in no list
        team, side, everything = self.word(client, "team"), self.word(client, "side"), self.word(client)
        task = new(client, title="In team")
        assert self.word(client, "team") != team and self.word(client, "side") == side and self.word(client) != everything
        # Moved to the other list: both lists' words.
        team, side = self.word(client, "team"), self.word(client, "side")
        assert put(client, task, list="side").status_code == 200
        assert self.word(client, "team") != team and self.word(client, "side") != side
        # bob's Inbox is his: alice's page isn't told, whatever it holds; bob's is.
        everything, inbox = self.word(client), self.word(client, "inbox")
        login(client, "bob")
        bobs = self.word(client)
        assert client.post("/api/tasks", json={"title": "Bob's own"}, headers=HOST).status_code == 201
        assert self.word(client) != bobs
        login(client, "alice")
        assert self.word(client) == everything and self.word(client, "inbox") == inbox
        # A list made: every page's word, since its switcher shows it.
        team = self.word(client, "team")
        assert make_list(client, "third", headers=HOST).status_code == 302
        assert self.word(client, "team") != team


class TestTheClosedViewsPage:
    """GET /api/tasks/closed?limit= (docs/spec/tasks.md, Views → Closed): the
    page the Closed view shows, of its scope and filter, the latest closed
    first, with how many there are; a task a link names is among them."""

    def closed(self, client, **q):
        resp = client.get("/api/tasks/closed", query_string={"limit": 50, **q}, headers=HOST)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()

    def test_a_page_of_the_scope_and_the_filter(self, alice):
        client = alice
        assert make_list(client, "side", headers=HOST).status_code == 302
        ids = {}
        for i, (title, fields) in enumerate([("One", {"tags": ["x"]}), ("Two", {"assignee": "bob"}), ("Three", {"tags": ["x"], "assignee": "bob"}),
                                             ("Aside", {"list": "side"}), ("Four", {"description": "about the invoice", "importance": "high"})]):
            task = new(client, title=title, **fields)
            ids[title] = task["id"]
            assert put(client, task, completed=True).status_code == 200
        own = client.post("/api/tasks", json={"title": "Hers alone"}, headers=HOST).get_json()      # in her Inbox
        assert put(client, own, completed=True).status_code == 200
        titles = lambda page: sorted(t["title"] for t in page["tasks"])
        everything = self.closed(client)
        assert everything["total"] == 6 and len(everything["tasks"]) == 6
        # Its scope: one list, My tasks.
        assert titles(self.closed(client, list="team")) == ["Four", "One", "Three", "Two"]
        assert titles(self.closed(client, list="side")) == ["Aside"]
        assert titles(self.closed(client, mine="1")) == ["Hers alone"]
        # The filter field's words, as the browser reads them.
        for q, want in (("#x", ["One", "Three"]), ("-#x", ["Aside", "Four", "Hers alone", "Two"]), ("@bob", ["Three", "Two"]),
                        ("#x @bob", ["Three"]), ("@none", ["Aside", "Four", "One"]), ("@me", ["Hers alone"]), ("list:side", ["Aside"]),
                        ("-list:team", ["Aside", "Hers alone"]), ("list:inbox", ["Hers alone"]), ("invoice", ["Four"]), ("!high", ["Four"]),
                        ("from:me -@me", ["Aside", "Four", "One", "Three", "Two"]), ("nothing-like-it", [])):
            page = self.closed(client, q=q)
            assert titles(page) == want and page["total"] == len(want), q
        # A page, and how many there are; a linked task is among them however far down.
        page = self.closed(client, limit=2)
        assert len(page["tasks"]) == 2 and page["total"] == 6
        last = everything["tasks"][-1]                                               # the one furthest down
        assert [t["id"] for t in page["tasks"]] == [t["id"] for t in everything["tasks"][:2]]
        assert last["id"] in [t["id"] for t in self.closed(client, limit=2, task=last["id"])["tasks"]]
        assert client.get("/api/tasks/closed?limit=0", headers=HOST).status_code == 400
        # Without limit: the year's closed tasks, as ever.
        assert len(client.get("/api/tasks/closed", headers=HOST).get_json()) == 6
        # bob doesn't get her Inbox's.
        login(client, "bob")
        assert self.closed(client)["total"] == 5


def test_a_task_shared_is_given_to_who_shares_it_whatever_the_door(alice, wiki_dir):
    """A private task that becomes shared without a word of who has it is
    its user's: by the API, a batch (the move dialog, a drop), a list made
    shared, a list deleted into a shared one. A write that names someone, or
    no one, decides for itself."""
    get = lambda title: next(t for t in alice.get("/api/tasks", headers=HOST).get_json() if t["title"] == title)
    events = lambda title: [(e["event"], e.get("to")) for e in get(title)["history"]]
    own = lambda title, **f: alice.post("/api/tasks", json={"title": title, "list": "", **f}, headers=HOST).get_json()
    # PUT with share (the dialog, an assistant's update_task).
    t = own("By put")
    assert put(alice, t, list="team", share=True).status_code == 200
    assert get("By put")["assignee"] == "alice" and events("By put") == [("created", None), ("shared", None), ("assigned", "alice")]
    # … naming someone else, or no one.
    t = own("For bob")
    assert put(alice, t, list="team", share=True, assignee="bob").status_code == 200
    assert get("For bob")["assignee"] == "bob"
    t = own("No one's")
    assert put(alice, t, list="team", share=True, assignee=None).status_code == 200
    assert "assignee" not in get("No one's") and events("No one's") == [("created", None), ("shared", None)]
    # A batch.
    t = own("By batch")
    resp = alice.post("/api/tasks/batch-update", json={"tasks": [{"id": t["id"], "list": "team", "share": True}]}, headers=HOST)
    assert resp.status_code == 200 and get("By batch")["assignee"] == "alice"
    # A private list made shared, and one deleted into a shared list.
    assert make_list(alice, "gifts", private=True, headers=HOST).status_code == 302
    assert make_list(alice, "someday", private=True, headers=HOST).status_code == 302
    own("A gift", list="gifts")
    own("Some day", list="someday")
    assert alice.post("/tasks/share-list", data={"list": "gifts"}, headers=HOST).status_code == 302
    assert get("A gift")["assignee"] == "alice" and not get("A gift")["private"]
    assert alice.post("/tasks/delete-list", data={"list": "someday", "to": "team", "share": "1"}, headers=HOST).status_code == 302
    assert get("Some day")["assignee"] == "alice" and get("Some day")["list"] == "team"
    # A viewer can't be given tasks, so nothing of theirs is shared by them; a
    # task that stays private has no one.
    assert "assignee" not in own("Still mine")


def test_a_task_is_deleted_by_who_made_it_or_an_admin(alice, wiki_dir):
    """Delete for good: a task's maker and an admin delete it; anyone else
    closes it (Won't do), since others have seen it. One's own private task,
    and what one just imported (the import's undo), go too."""
    made = new(alice, title="Alice made it")
    login(alice, "bob")
    alice.in_team = True
    assert alice.delete(f"/api/tasks/{made['id']}", headers=HOST).status_code == 403
    assert alice.post("/api/tasks/batch-delete", json={"ids": [made["id"]]}, headers=HOST).status_code == 403
    own = new(alice, title="Bob made it")
    private = alice.post("/api/tasks", json={"title": "Bob's own", "list": ""}, headers=HOST).get_json()
    assert alice.delete(f"/api/tasks/{own['id']}", headers=HOST).status_code == 204
    assert alice.delete(f"/api/tasks/{private['id']}", headers=HOST).status_code == 204
    # What bob imports was made by someone else, and its undo deletes it again.
    file = {"zone_tasks": 1, "tasks": [dict(made, id="0b7c1b0e-3c6e-4c56-9d8e-2a6f1f3b9a10", title="Imported")]}
    resp = alice.post("/api/tasks/import", json={"file": file, "list": "team"}, headers=HOST)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert alice.post("/api/tasks/batch-delete", json={"ids": resp.get_json()["ids"]}, headers=HOST).status_code == 204
    # An admin deletes any.
    theirs = new(alice, title="Bob's, for the admin")
    login(alice, "alice")
    assert alice.delete(f"/api/tasks/{theirs['id']}", headers=HOST).status_code == 204
    assert [t["title"] for t in alice.get("/api/tasks", headers=HOST).get_json()] == ["Alice made it"]
