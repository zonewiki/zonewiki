"""A list's manual order, changed by moves (docs/spec/tasks.md, Sections):
POST /api/tasks/reorder with `moves` places each moved task between the
tasks that are before and after it, in the order the server has, and writes
no other task."""

from tests.conftest import create_task


def _tasks(client):
    return {t["title"]: t for t in client.get("/api/tasks").get_json()}


def _ranked(client):
    ranked = [t for t in client.get("/api/tasks").get_json() if t["sort_order"] is not None]
    return [t["title"] for t in sorted(ranked, key=lambda t: t["sort_order"])]


def _setup(client, *titles):
    for title in titles:
        create_task(client, title=title)
    ids = {title: t["id"] for title, t in _tasks(client).items()}
    assert client.post("/api/tasks/reorder", json={"order": [ids[t] for t in titles]}).status_code == 204
    return ids


def _move(client, moves):
    return client.post("/api/tasks/reorder", json={"moves": moves})


def test_a_move_places_a_task_between_two_and_writes_no_other(client):
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
    create_task(client, title="New")
    new = _tasks(client)["New"]["id"]
    assert _move(client, [{"id": new, "after": ids["D"], "before": ids["B"]}]).status_code == 204
    assert _ranked(client) == ["A", "D", "New", "B", "C"]
    # Several at once, each after the one before it.
    assert _move(client, [{"id": ids["B"], "after": None, "before": ids["A"]},
                          {"id": ids["C"], "after": ids["B"], "before": ids["A"]}]).status_code == 204
    assert _ranked(client) == ["B", "C", "A", "D", "New"]
    # The only ranked task: a place of its own.
    assert client.post("/api/tasks/reorder", json={"updates": [{"id": i, "sort_order": None} for i in list(ids.values()) + [new]]}).status_code == 204
    assert _move(client, [{"id": ids["A"], "after": None, "before": None}]).status_code == 204
    assert _ranked(client) == ["A"]


def test_a_stale_list_does_not_put_its_old_order_back(client):
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
    # A neighbour that is gone from the order, or no task: as if not named.
    assert client.post("/api/tasks/reorder", json={"updates": [{"id": ids["A"], "sort_order": None}]}).status_code == 204
    assert _move(client, [{"id": ids["B"], "after": ids["A"], "before": ids["C"]}]).status_code == 204
    assert _ranked(client) == ["B", "C", "D"]
    assert _move(client, [{"id": ids["D"], "after": "no-such-task", "before": ids["B"]}]).status_code == 204
    assert _ranked(client) == ["D", "B", "C"]
    assert _move(client, [{"id": "no-such-task", "after": ids["B"], "before": ids["C"]}]).status_code == 204
    assert _ranked(client) == ["D", "B", "C"]


def test_many_moves_into_one_place_keep_their_order(client):
    """The numbers between two tasks run out after some fifty halvings: the
    order is numbered anew then, as it stands."""
    ids = _setup(client, "A", "B")
    titles = [f"T{i:02d}" for i in range(70)]
    for title in titles:
        create_task(client, title=title)
    by = {t: v["id"] for t, v in _tasks(client).items()}
    for title in titles:                       # each right after A, so before the one moved just before
        assert _move(client, [{"id": by[title], "after": ids["A"], "before": None if title == titles[0] else by[titles[titles.index(title) - 1]]}]).status_code == 204
    # T00 went after A with nothing named after it: it is last; the others each before its predecessor.
    assert _ranked(client) == ["A"] + titles[:0:-1] + ["B", "T00"] or _ranked(client) == ["A"] + titles[:0:-1] + ["T00", "B"]
    order = [v["sort_order"] for v in sorted(_tasks(client).values(), key=lambda t: t["sort_order"])]
    assert len(set(order)) == len(order)


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
    user's), one that isn't ranked yet, and a task closed by the write."""
    ids = _setup(client, "A", "B")
    loose = create_task(client, title="Loose").get_json()["id"]
    done = create_task(client, title="Done").get_json()["id"]
    assert client.put(f"/api/tasks/{done}", json={"completed": True}).status_code == 200
    before = {t: v["sort_order"] for t, v in _tasks(client).items()}
    for position in ("middle", 3, {"after": ids["A"], "before": ids["B"]}, {"next": ids["A"]}, {"after": ""}):
        resp = create_task(client, title="New", position=position)
        assert resp.status_code == 400 and "position must be" in resp.get_json()["error"], position
    gone = create_task(client, title="New", position={"after": done}).get_json()["error"]
    assert gone == create_task(client, title="New", position={"after": "no-such-task"}).get_json()["error"]
    assert "closed or not there" in gone
    assert "isn't in the manual order yet" in create_task(client, title="New", position={"after": loose}).get_json()["error"]
    resp = client.put(f"/api/tasks/{ids['A']}", json={"completed": True, "position": "top"})
    assert resp.status_code == 400 and "Only an open task" in resp.get_json()["error"]
    assert {t: v["sort_order"] for t, v in _tasks(client).items()} == before
