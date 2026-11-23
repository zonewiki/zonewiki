"""The manual order, changed by moves (docs/spec/tasks.md, The manual order):
POST /api/tasks/reorder with `moves` places each moved task between the
tasks that are before and after it, in the order the server has, and writes
no other task. The order is one for every task of the wiki, those a user
doesn't see too; My tasks' is the user's own, and is changed by moves as
well."""

import json
import subprocess

import pytest

from tests import settings
from tests.conftest import HOST, create_task


def _tasks(client):
    return {t["title"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}


def _ranked(client):
    ranked = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["sort_order"] is not None]
    return [t["title"] for t in sorted(ranked, key=lambda t: t["sort_order"])]


def _setup(client, *titles):
    """Tasks in this manual order (each made at the bottom)."""
    for title in titles:
        assert create_task(client, title=title, position="bottom").status_code == 201
    return {title: t["id"] for title, t in _tasks(client).items()}


def _move(client, moves, **more):
    return client.post("/api/tasks/reorder", json={"moves": moves, **more}, headers=HOST)


def _unplace(wiki_dir, *ids):
    """The tasks ids without a place, as in a file edited by hand."""
    path = wiki_dir / "_tasks" / "tasks.json"
    tasks = json.loads(path.read_text())
    for t in tasks:
        if t["id"] in ids:
            t["sort_order"] = None
    path.write_text(json.dumps(tasks, indent=1) + "\n")


def test_a_move_places_a_task_between_two_and_writes_no_other(client, wiki_dir):
    ids = _setup(client, "A", "B", "C", "D")
    before = {t: v["sort_order"] for t, v in _tasks(client).items()}
    assert _move(client, [{"id": ids["D"], "after": ids["A"], "before": ids["B"]}]).status_code == 204
    assert _ranked(client) == ["A", "D", "B", "C"]
    after = {t: v["sort_order"] for t, v in _tasks(client).items()}
    assert {t for t in after if after[t] != before[t]} == {"D"}
    # First, last, and into an order from outside it.
    assert _move(client, [{"id": ids["C"], "after": None, "before": ids["A"]}]).status_code == 204
    assert _ranked(client) == ["C", "A", "D", "B"]
    assert _move(client, [{"id": ids["C"], "after": ids["B"], "before": None}]).status_code == 204
    assert _ranked(client) == ["A", "D", "B", "C"]
    # A new task is on top, as it is made.
    create_task(client, title="New")
    assert _ranked(client) == ["New", "A", "D", "B", "C"]
    new = _tasks(client)["New"]["id"]
    assert _move(client, [{"id": new, "after": ids["D"], "before": ids["B"]}]).status_code == 204
    assert _ranked(client) == ["A", "D", "New", "B", "C"]
    # Several at once, each after the one before it.
    assert _move(client, [{"id": ids["B"], "after": None, "before": ids["A"]},
                          {"id": ids["C"], "after": ids["B"], "before": ids["A"]}]).status_code == 204
    assert _ranked(client) == ["B", "C", "A", "D", "New"]
    # Tasks with no place (a file edited by hand) are shown on top, newest
    # first; a move places them there first, so it lands where it was shown.
    _unplace(wiki_dir, *ids.values(), new)
    assert _ranked(client) == []
    assert _move(client, [{"id": ids["A"], "after": None, "before": new}]).status_code == 204
    assert _ranked(client) == ["A", "New", "D", "C", "B"]


def test_a_stale_list_does_not_put_its_old_order_back(client, wiki_dir):
    """Two people rank one list. The second one's browser still has the old
    order: its move names the neighbours it saw, and what the first one did
    stays."""
    ids = _setup(client, "A", "B", "C", "D")
    # The first moves D to the top.
    assert _move(client, [{"id": ids["D"], "after": None, "before": ids["A"]}]).status_code == 204
    # The second, who still sees A B C D, puts C between A and B.
    assert _move(client, [{"id": ids["C"], "after": ids["A"], "before": ids["B"]}]).status_code == 204
    assert _ranked(client) == ["D", "A", "C", "B"]
    # Neighbours that are the other way round by now leave "after" to say where.
    assert _move(client, [{"id": ids["D"], "after": ids["B"], "before": ids["A"]}]).status_code == 204
    assert _ranked(client)[-1] == "D"
    # A neighbour with no place (a file edited by hand) is placed where it
    # is shown, on top, first; one that is no task counts as not named.
    _unplace(wiki_dir, ids["A"])
    assert _move(client, [{"id": ids["B"], "after": ids["A"], "before": ids["C"]}]).status_code == 204
    assert _ranked(client) == ["A", "B", "C", "D"]
    assert _move(client, [{"id": ids["D"], "after": "no-such-task", "before": ids["B"]}]).status_code == 204
    assert _ranked(client) == ["D", "A", "B", "C"]
    assert _move(client, [{"id": "no-such-task", "after": ids["B"], "before": ids["C"]}]).status_code == 204
    assert _ranked(client) == ["D", "A", "B", "C"]


def test_many_moves_into_one_place_make_room_there_only(client):
    """The numbers between two tasks run out after some twenty halvings:
    room is made around that place then, and a task far from it keeps its
    number (a team ranking in one spot doesn't write every task)."""
    far = [f"Far{i}" for i in range(12)]
    ids = _setup(client, "A", "B", *far)
    titles = [f"T{i:02d}" for i in range(70)]
    for title in titles:
        create_task(client, title=title)
    by = {t: v["id"] for t, v in _tasks(client).items()}
    kept = {t: v["sort_order"] for t, v in _tasks(client).items() if t in far}
    below = "B"
    for title in titles:                       # each right after A, before the one moved just before
        assert _move(client, [{"id": by[title], "after": ids["A"], "before": by[below]}]).status_code == 204
        below = title
    assert _ranked(client) == ["A"] + titles[::-1] + ["B"] + far
    order = [v["sort_order"] for v in _tasks(client).values()]
    assert len(set(order)) == len(order)
    assert {t: v["sort_order"] for t, v in _tasks(client).items() if t in far[4:]} == {t: kept[t] for t in far[4:]}


def test_what_a_reorder_refuses(client):
    assert client.post("/api/tasks/reorder", json={"moves": "x"}).status_code == 400
    assert client.post("/api/tasks/reorder", json={"moves": ["x"]}).status_code == 400
    assert client.post("/api/tasks/reorder", json={}).status_code == 400


def test_a_new_or_changed_task_takes_a_position(client):
    """position on POST and PUT /api/tasks: the task at the top or the
    bottom of the manual order, or right after or before a task, in the
    write's own commit (the MCP tools' create_task and update_task send it)."""
    ids = _setup(client, "A", "B")
    made = lambda title, position: create_task(client, title=title, position=position)
    assert made("Last", "bottom").status_code == 201
    assert made("First", "top").status_code == 201
    assert made("Middle", {"after": ids["A"]}).get_json()["sort_order"] is not None
    assert _ranked(client) == ["First", "A", "Middle", "B", "Last"]
    last = _tasks(client)["Last"]["id"]
    resp = client.put(f"/api/tasks/{last}", json={"title": "Now second", "position": {"before": ids["A"]}})
    assert resp.status_code == 200 and resp.get_json()["title"] == "Now second"
    assert _ranked(client) == ["First", "Now second", "A", "Middle", "B"]


def test_what_a_position_refuses(client):
    """Refused with nothing written: what isn't a position, a task to put it
    by that is closed or not there (in the same words: it may be another
    user's), and a task closed by the write."""
    ids = _setup(client, "A", "B")
    create_task(client, title="Loose")
    done = create_task(client, title="Done").get_json()["id"]
    assert client.put(f"/api/tasks/{done}", json={"completed": True}).status_code == 200
    before = {t: v["sort_order"] for t, v in _tasks(client).items()}
    for position in ("middle", 3, {"after": ids["A"], "before": ids["B"]}, {"next": ids["A"]}, {"after": ""}):
        resp = create_task(client, title="New", position=position)
        assert resp.status_code == 400 and "position must be" in resp.get_json()["error"], position
    gone = create_task(client, title="New", position={"after": done}).get_json()["error"]
    assert gone == create_task(client, title="New", position={"after": "no-such-task"}).get_json()["error"]
    assert "closed or not there" in gone
    resp = client.put(f"/api/tasks/{ids['A']}", json={"completed": True, "position": "top"})
    assert resp.status_code == 400 and "Only an open task" in resp.get_json()["error"]
    assert {t: v["sort_order"] for t, v in _tasks(client).items()} == before


# ── My tasks' own order ──────────────────────────────────────────────────────

def _mine(client):
    """My tasks' order as the view shows it, by title."""
    got = client.get("/api/tasks?with=order", headers=HOST).get_json()
    title = {t["id"]: t["title"] for t in got["tasks"]}
    return [title[i] for i in got["order"]]


def test_my_tasks_order_is_changed_by_moves_and_writes_no_task(client):
    ids = _setup(client, "One", "Two", "Three")
    places = {t: v["sort_order"] for t, v in _tasks(client).items()}
    assert _mine(client) == ["One", "Two", "Three"]          # the lists' order, until something is ranked there
    assert _move(client, [{"id": ids["Three"], "after": None, "before": ids["One"]}], mine=True).status_code == 204
    assert _mine(client) == ["Three", "One", "Two"]
    assert {t: v["sort_order"] for t, v in _tasks(client).items()} == places
    # A second device still shows One, Two, Three, and puts Two first there:
    # Three stays where the first device put it (a whole order sent from
    # here would have put it back last).
    assert _move(client, [{"id": ids["Two"], "after": None, "before": ids["One"]}], mine=True).status_code == 204
    assert _mine(client) == ["Three", "Two", "One"]
    # After a task, and a task that is no task.
    assert _move(client, [{"id": ids["Three"], "after": ids["One"], "before": None}], mine=True).status_code == 204
    assert _mine(client) == ["Two", "One", "Three"]
    assert _move(client, [{"id": "no-such-task", "after": ids["One"], "before": None}], mine=True).status_code == 204
    assert _mine(client) == ["Two", "One", "Three"]
    # No list's order moved with it.
    assert _ranked(client) == ["One", "Two", "Three"]


def test_a_task_new_to_my_tasks_is_on_top_until_it_is_placed(client):
    ids = _setup(client, "One", "Two")
    assert _move(client, [{"id": ids["Two"], "after": None, "before": ids["One"]}], mine=True).status_code == 204
    older = create_task(client, title="Older", position="bottom").get_json()["id"]
    create_task(client, title="Newer", position="bottom")
    assert _mine(client) == ["Newer", "Older", "Two", "One"]      # newest first, whatever their place in their list
    # A move places them where they are shown, then itself: between the two.
    assert _move(client, [{"id": ids["One"], "after": _tasks(client)["Newer"]["id"], "before": older}], mine=True).status_code == 204
    assert _mine(client) == ["Newer", "One", "Older", "Two"]
    # A closed task is in no order.
    assert client.put(f"/api/tasks/{older}", json={"completed": True}, headers=HOST).status_code == 200
    assert _mine(client) == ["Newer", "One", "Two"]


# ── One order for every task of the wiki ─────────────────────────────────────

USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob")}


def _sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def _git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True, env=settings.GIT_ENV).stdout


@pytest.fixture()
def team(client, git_wiki_dir, monkeypatch):
    """alice and bob, and the shared list team."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    _sign_in(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    return git_wiki_dir


def _new(client, title, **fields):
    resp = client.post("/api/tasks", json={"title": title, **fields}, headers=HOST)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def test_the_top_is_above_every_task_also_those_its_maker_doesnt_see(client, team):
    """bob doesn't see alice's private task. His new task still goes above
    it, and one he puts last below it: alice, who sees both, has one order."""
    private = _new(client, "Alice's own")                               # in her Inbox
    _sign_in(client, "bob")
    assert "Alice's own" not in _tasks(client)
    top = _new(client, "Bob's first", list="team")
    last = _new(client, "Bob's last", list="team", position="bottom")
    assert top["sort_order"] < private["sort_order"] < last["sort_order"]
    _sign_in(client, "alice")
    assert _ranked(client) == ["Bob's first", "Alice's own", "Bob's last"]
    # Dragged to the top of what he sees, a task is above every task too.
    _sign_in(client, "bob")
    assert _move(client, [{"id": last["id"], "after": None, "before": top["id"]}]).status_code == 204
    _sign_in(client, "alice")
    assert _ranked(client) == ["Bob's last", "Bob's first", "Alice's own"]


def test_room_is_made_among_tasks_of_others_and_all_keep_their_order(client, team):
    """bob ranks in one spot until the numbers there are used up. alice's
    private task stands near it: it gets a new number with its neighbours,
    in a commit of Zonewiki's own that bob's history doesn't show, and she
    still sees every task in the order it was in."""
    made = {t: _new(client, t, list="team", position="bottom")["id"] for t in ("Z", "A", "S", "B")}
    own = _new(client, "Alice's own", position={"after": made["S"]})
    assert _ranked(client) == ["Z", "A", "S", "Alice's own", "B"]
    _sign_in(client, "bob")
    x, y = _new(client, "X", list="team")["id"], _new(client, "Y", list="team")["id"]
    assert _move(client, [{"id": x, "after": made["A"], "before": made["S"]}]).status_code == 204
    moved, below = y, x
    for _ in range(40):                                                # each right after A, before the other
        assert _move(client, [{"id": moved, "after": made["A"], "before": below}]).status_code == 204
        moved, below = below, moved
    assert _ranked(client) == ["Z", "A", "X", "Y", "S", "B"]
    _sign_in(client, "alice")
    assert _ranked(client) == ["Z", "A", "X", "Y", "S", "Alice's own", "B"]
    assert _tasks(client)["Alice's own"]["sort_order"] != own["sort_order"]
    numbers = [t["sort_order"] for t in _tasks(client).values()]
    assert len(set(numbers)) == len(numbers)
    # Her file was written in a commit that is not bob's, and names no task.
    log = _git(team, "log", "--format=%an|%s", "--", "private/alice/_tasks/tasks.json").splitlines()
    assert log[0].endswith("|Make room in the tasks' order") and not log[0].startswith("bob|")
    commit = _git(team, "log", "-1", "--format=%H", "--", "private/alice/_tasks/tasks.json").strip()
    assert _git(team, "show", "--format=", "--name-only", commit).split() == ["private/alice/_tasks/tasks.json"]
    assert "Task:" not in _git(team, "show", "-s", "--format=%B", commit)
    _sign_in(client, "bob")
    assert "Make room" not in client.get("/changes", headers=HOST).get_data(as_text=True)
