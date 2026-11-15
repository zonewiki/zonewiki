"""A task given to you is told to you (docs/spec/users.md, Assigning
tasks): a mail, at once, when someone else assigns you a task, where the
deployment sends mail and you have an address; off in your My tasks'
settings. In the app such a task's card says "new" (tests/test_tasks_core.py,
TestGivenToMe)."""

import time

import pytest

from tests import settings
from tests.conftest import HOST, make_list
from tests.test_signup import FakeSMTP

BASE = "http://wiki.example.org"
USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol", "dan")}
USER_SETTINGS = {"alice": {"email": "alice@example.org"}, "bob": {"email": "bob@example.org"},
                 "carol": {"email": "carol@example.org", "role": "viewer"}}       # dan has no address


@pytest.fixture()
def smtp():
    s = FakeSMTP()
    yield s
    s.shutdown()
    s.server_close()


@pytest.fixture()
def alice(client, wiki_dir, monkeypatch, smtp):
    """Signed in as alice, on a wiki whose deployment sends mail."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
    monkeypatch.setattr(settings, "BASE_URL", BASE)
    monkeypatch.setattr(settings, "ENV", {"ZONE_SMTP_HOST": "127.0.0.1", "ZONE_SMTP_PORT": str(smtp.server_address[1]),
                                          "ZONE_SMTP_TLS": "none", "ZONE_MAIL_FROM": "wiki@example.org"})
    login(client, "alice")
    make_list(client, "team", headers=HOST)   # a task for someone is in a shared list
    return client


def login(client, name):
    assert client.post("/login", data={"username": name, "password": f"{name}-pw"}, headers=HOST).status_code == 302


def new(client, **fields):
    resp = client.post("/api/tasks", json={"list": "team", **fields}, headers=HOST)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def mails(smtp, n, wait=3.0):
    """The mails once n have arrived (they are sent in the background);
    with n == 0, those that came within a moment."""
    end = time.time() + (wait if n else 0.4)
    while time.time() < end and (n == 0 or len(smtp.mails) < n):
        time.sleep(0.02)
    assert len(smtp.mails) == n, smtp.mails
    return list(smtp.mails)


def test_a_new_task_for_someone_is_mailed_to_them(alice, smtp):
    task = new(alice, title="Check the invoice", assignee="bob")
    (to, text), = mails(smtp, 1)
    assert to == "bob@example.org"
    assert "Subject: alice gave you a task: Check the invoice" in text
    assert "alice assigned this task to you on Zone:" in text and "  Check the invoice\n" in text.replace("\r\n", "\n")
    assert f"{BASE}/tasks?task={task['id']}" in text and f"All your tasks: {BASE}/tasks?mine=1" in text
    assert "Mail me when a task is assigned to me" in text


def test_no_mail_for_what_one_takes_or_what_is_no_ones(alice, smtp):
    new(alice, title="Mine", assignee="alice")
    new(alice, title="Nobody's")
    task = new(alice, title="Later mine")
    assert alice.put(f"/api/tasks/{task['id']}", json={"assignee": "me"}, headers=HOST).status_code == 200
    # No address, and another edit of a task bob has already.
    new(alice, title="For dan", assignee="dan")
    bobs = new(alice, title="For bob", assignee="bob")
    mails(smtp, 1)
    assert alice.put(f"/api/tasks/{bobs['id']}", json={"title": "For bob, renamed"}, headers=HOST).status_code == 200
    assert alice.put(f"/api/tasks/{bobs['id']}", json={"assignee": None}, headers=HOST).status_code == 200
    assert len(smtp.mails) == 1
    time.sleep(0.3)
    assert len(smtp.mails) == 1


def test_a_reassignment_and_a_closed_task(alice, smtp):
    task = new(alice, title="Passed on")
    assert alice.put(f"/api/tasks/{task['id']}", json={"assignee": "bob"}, headers=HOST).status_code == 200
    assert mails(smtp, 1)[0][0] == "bob@example.org"
    # bob gives it to alice: she is told, by him.
    login(alice, "bob")
    assert alice.put(f"/api/tasks/{task['id']}", json={"assignee": "alice"}, headers=HOST).status_code == 200
    to, text = mails(smtp, 2)[1]
    assert to == "alice@example.org" and "Subject: bob gave you a task: Passed on" in text
    # A task closed in the same write isn't told.
    done = new(alice, title="Done at once")
    assert alice.put(f"/api/tasks/{done['id']}", json={"assignee": "alice", "completed": True}, headers=HOST).status_code == 200
    time.sleep(0.3)
    assert len(smtp.mails) == 2


def test_several_at_once_are_one_mail_a_user(alice, smtp):
    tasks = [new(alice, title=f"T{i}") for i in range(3)]
    resp = alice.post("/api/tasks/batch-update", headers=HOST, json={"tasks": [
        {"id": tasks[0]["id"], "assignee": "bob"}, {"id": tasks[1]["id"], "assignee": "bob"},
        {"id": tasks[2]["id"], "assignee": "alice"}]})
    assert resp.status_code == 200
    (to, text), = mails(smtp, 1)
    assert to == "bob@example.org" and "Subject: alice gave you 2 tasks" in text
    assert "alice assigned these tasks to you" in text and "T0" in text and "T1" in text and "T2" not in text


def test_an_assistants_assignment_is_mailed_too(alice, smtp):
    resp = alice.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "create_task", "arguments": {"title": "From her assistant", "assignee": "bob", "list": "team"}}})
    assert "assigned to: bob" in "".join(c["text"] for c in resp.get_json()["result"]["content"])
    (to, text), = mails(smtp, 1)
    assert to == "bob@example.org" and "alice gave you a task: From her assistant" in text


def test_the_mail_is_turned_off_in_my_tasks_settings(alice, smtp, wiki_dir):
    # The dialog has the box where Zone could mail the user (My tasks is
    # there once the wiki has a list).
    make_list(alice, "team", headers=HOST)
    assert 'data-settings-mail="1"' in alice.get("/tasks?mine=1", headers=HOST).get_data(as_text=True)
    login(alice, "dan")
    assert "data-settings-mail" not in alice.get("/tasks?mine=1", headers=HOST).get_data(as_text=True)
    login(alice, "bob")
    off = lambda value, was: alice.post("/api/tasks/settings", headers=HOST,
                                        json={"mine": True, "was": was, "set": {"mail": value}})
    assert off("no", "").status_code == 400
    assert off("off", "").status_code == 200
    assert '"mine": {"mail": "off"}' in (wiki_dir / "private/bob/_tasks/lists.json").read_text()
    login(alice, "alice")
    new(alice, title="Quietly", assignee="bob")
    mails(smtp, 0)
    login(alice, "bob")
    assert off("", "mail=off").status_code == 200
    login(alice, "alice")
    new(alice, title="Aloud", assignee="bob")
    assert "Aloud" in mails(smtp, 1)[0][1]


def test_no_mail_without_a_mail_server(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
    login(client, "alice")
    make_list(client, "team", headers=HOST)
    assert new(client, title="No server", assignee="bob")["assignee"] == "bob"
    page = client.get("/tasks?mine=1", headers=HOST).get_data(as_text=True)
    assert "data-settings-mine" in page and "data-settings-mail" not in page


def test_the_tasks_links_count_what_is_new_for_you(alice, smtp):
    """A task someone gives you often has no date, so it is in neither of the
    Tasks links' counts: they have a third one, the tasks someone else gave
    you that you haven't touched yet."""
    task = new(alice, title="Check the invoice", assignee="bob")
    new(alice, title="Hers", assignee="alice")
    new(alice, title="Nobody's")
    # alice gave it: nothing new for her, and no key in the answer.
    assert alice.get("/api/tasks/due-count", headers=HOST).get_json() == {"count": 0, "overdue": 0, "today": 0, "focus": 0}
    login(alice, "bob")
    counts = alice.get("/api/tasks/due-count?places=1", headers=HOST).get_json()
    assert counts["new"] == 1 and counts["count"] == 0 and counts["focus"] == 0
    assert counts["places"]["mine"]["new"] == 1 and "new" not in counts["places"]["inbox"]
    page = alice.get("/page/index", headers=HOST).get_data(as_text=True)
    assert '<span class="module-new">1</span>' in page and 'title="Tasks: 1 new for you"' in page
    # In the list's own counts too (its pill), for him; the list has no pill here, so ask the route with it pinned.
    assert alice.post("/tasks/pin-list", data={"list": "team", "pin": "1"}, headers=HOST).status_code == 302
    assert alice.get("/api/tasks/due-count?places=1", headers=HOST).get_json()["places"]["list:team"]["new"] == 1
    # He touches it (a date): no longer new.
    assert alice.put(f"/api/tasks/{task['id']}", json={"due_date": "2031-01-01"}, headers=HOST).status_code == 200
    counts = alice.get("/api/tasks/due-count", headers=HOST).get_json()
    assert "new" not in counts
    page = alice.get("/page/index", headers=HOST).get_data(as_text=True)
    assert '<span class="module-new" hidden></span>' in page


def test_opening_a_task_given_to_you_says_seen(alice, smtp):
    """The browser tells the server when the user opens a task someone gave
    them (PUT with seen): logged once in its history, so it is no longer new
    to them and whoever gave it sees that it arrived. No time takes the mark
    away, and for anyone else's task seen says nothing."""
    task = new(alice, title="Check the invoice", assignee="bob")
    url = f"/api/tasks/{task['id']}"
    events = lambda: [(e["event"], e.get("by")) for e in alice.get("/api/tasks", headers=HOST).get_json()[0]["history"]]
    # alice gave it: her opening it logs nothing, and writes nothing.
    head = lambda: alice.get("/api/tasks/due-count", headers=HOST).headers["Zone-Tasks"]
    word = head()
    assert alice.put(url, json={"seen": True}, headers=HOST).status_code == 200
    assert events() == [("created", "alice")] and head() == word
    login(alice, "bob")
    assert alice.get("/api/tasks/due-count", headers=HOST).get_json()["new"] == 1
    assert alice.put(url, json={"seen": True}, headers=HOST).status_code == 200
    assert events() == [("created", "alice"), ("seen", "bob")]
    assert "new" not in alice.get("/api/tasks/due-count", headers=HOST).get_json()
    # Once: opening it again logs nothing more.
    assert alice.put(url, json={"seen": True}, headers=HOST).status_code == 200
    assert events() == [("created", "alice"), ("seen", "bob")]
    assert alice.put(url, json={"seen": "yes"}, headers=HOST).status_code == 400
    # Given to someone else and back: new again.
    login(alice, "alice")
    assert alice.put(url, json={"assignee": "alice"}, headers=HOST).status_code == 200
    assert alice.put(url, json={"assignee": "bob"}, headers=HOST).status_code == 200
    login(alice, "bob")
    assert alice.get("/api/tasks/due-count", headers=HOST).get_json()["new"] == 1
