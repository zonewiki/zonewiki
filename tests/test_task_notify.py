"""A task given to you is told to you (docs/spec/users.md, Assigning
tasks): a mail, at once, when someone else assigns you a task, where the
deployment sends mail and you have an address; off in your My tasks'
settings. In the app such a task's card says "new" (tests/test_tasks_core.py,
TestGivenToMe)."""

import re
import json
import time

import pytest

from tests import settings
from tests.conftest import HOST, make_list, write_page
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
    # Mail is for who asks for it: alice and bob turned on the one about a task given to them.
    for name in ("bob", "alice"):
        mail_on(client, name, "assigned")
    return client


def mail_on(client, name, kinds):
    """name turns the kinds of mail on under Settings, Notifications, and the others off ("": none);
    alice is signed in afterwards."""
    login(client, name)
    resp = client.post("/notifications", headers=HOST, data={"mail": [k for k in kinds.split(",") if k]})
    assert resp.status_code == 303 and resp.headers["Location"] == "/notifications?done=saved", resp.get_data(as_text=True)
    login(client, "alice")


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
    assert "alice assigned this task to you on Zonewiki:" in text and "  Check the invoice\n" in text.replace("\r\n", "\n")
    assert f"{BASE}/tasks?task={task['id']}" in text and f"All your tasks: {BASE}/tasks?mine=1" in text
    assert f"To turn it off, open {BASE}/notifications." in text


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


def test_the_mail_is_turned_on_and_off_under_notifications(alice, smtp, wiki_dir):
    """Settings, Notifications has a box a kind of mail; what is ticked is in
    the user's own watch file, not in their task lists' settings."""
    chosen = lambda name: json.loads((wiki_dir / ".zone" / "watch" / f"{name}.json").read_text())["mail"]
    boxes = lambda body: {kind: bool(checked) for kind, checked in
                          re.findall(r'<input type="checkbox" name="mail" value="(\w+)"( checked)?>', body)}
    login(alice, "bob")
    body = alice.get("/notifications", headers=HOST).get_data(as_text=True)
    assert "<h2>Mail me when</h2>" in body and "<legend>Tasks</legend>" in body and "<legend>Each morning</legend>" in body
    assert boxes(body) == {"assigned": True, "mentioned": False, "comments": False, "digest": False}
    # My tasks' settings have none of it any more, and refuse it.
    make_list(alice, "team", headers=HOST)
    page = alice.get("/tasks?mine=1", headers=HOST).get_data(as_text=True)
    assert "data-settings-mine" in page and "Mail me when" not in page and "data-settings-mail" not in page
    resp = alice.post("/api/tasks/settings", headers=HOST, json={"mine": True, "was": "", "set": {"mail": "assigned"}})
    assert resp.status_code == 400
    mail_on(alice, "bob", "")
    assert not (wiki_dir / ".zone" / "watch" / "bob.json").exists()
    assert not (wiki_dir / "private/bob/_tasks/lists.json").exists()   # nothing of it in his task lists
    new(alice, title="Quietly", assignee="bob")
    mails(smtp, 0)
    mail_on(alice, "bob", "comments,assigned,nonsense")           # the kinds Zonewiki knows, in its order
    assert chosen("bob") == ["assigned", "comments"]
    new(alice, title="Aloud", assignee="bob")
    assert "Aloud" in mails(smtp, 1)[0][1]
    # The morning's mail is a kind like the others (what it says: go/digest_test.go), and sends nothing by itself.
    mail_on(alice, "bob", "digest,assigned")
    assert chosen("bob") == ["assigned", "digest"]
    login(alice, "bob")
    assert boxes(alice.get("/notifications?done=saved", headers=HOST).get_data(as_text=True)) == {
        "assigned": True, "mentioned": False, "comments": False, "digest": True}
    mails(smtp, 1)


def test_a_choice_left_in_my_tasks_settings_says_nothing(alice, smtp, wiki_dir):
    """What mail a user wanted was once kept with their My tasks' settings:
    such a line is passed over, and no mail goes out for it."""
    make_list(alice, "team", headers=HOST)
    mail_on(alice, "bob", "")
    path = wiki_dir / "private/bob/_tasks/lists.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"lists": {}, "mine": {"mail": "assigned,digest", "limit": "4"}}\n')
    login(alice, "bob")
    page = alice.get("/tasks?mine=1", headers=HOST).get_data(as_text=True)
    assert 'data-init-limit="4"' in page
    login(alice, "alice")
    new(alice, title="Quietly", assignee="bob")
    mails(smtp, 0)


def test_no_mail_without_a_mail_server(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", dict(USER_SETTINGS))
    login(client, "alice")
    make_list(client, "team", headers=HOST)
    assert new(client, title="No server", assignee="bob")["assignee"] == "bob"
    assert client.get("/notifications", headers=HOST).status_code == 404
    assert ">Notifications</a>" not in client.get("/password", headers=HOST).get_data(as_text=True)


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
    assert counts["new"] == 1 and counts["updates"] == 1 and counts["count"] == 0 and counts["focus"] == 0
    # Of the pills Updates' alone has it: news is counted where it is listed.
    assert counts["places"]["updates"] == {"count": 0, "overdue": 0, "today": 0, "focus": 0, "new": 1}
    assert "new" not in counts["places"]["mine"] and "new" not in counts["places"]["inbox"]
    page = alice.get("/page/index", headers=HOST).get_data(as_text=True)
    # On every page the top bar's bell has it; the Tasks links say what is due, not the news.
    assert '<span class="top-updates-count menu-count">1</span>' in page
    assert '<span class="module-new">' not in page and 'title="Tasks"' in page and "update for you" not in page
    # Not in the list's own counts (its pill, once pinned).
    assert alice.post("/tasks/pin-list", data={"list": "team", "pin": "1"}, headers=HOST).status_code == 302
    assert "new" not in alice.get("/api/tasks/due-count?places=1", headers=HOST).get_json()["places"]["list:team"]
    # He touches it (a date): no longer new.
    assert alice.put(f"/api/tasks/{task['id']}", json={"due_date": "2031-01-01"}, headers=HOST).status_code == 200
    counts = alice.get("/api/tasks/due-count", headers=HOST).get_json()
    assert "new" not in counts and "updates" not in counts
    page = alice.get("/page/index", headers=HOST).get_data(as_text=True)
    assert '<span class="top-updates-count menu-count" hidden></span>' in page   # the bell stays, without a number


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


def test_no_mail_until_it_is_turned_on(alice, smtp, wiki_dir):
    """Mail is opt-in: someone who never opened the settings gets none, nor
    who turned it off again."""
    new(alice, title="For carol's colleague", assignee="bob")
    mails(smtp, 1)                                             # bob turned it on (the fixture)
    smtp.mails.clear()
    mail_on(alice, "bob", "")
    new(alice, title="Quiet", assignee="bob")
    mails(smtp, 0)


def test_a_comment_and_a_mention_are_mailed_to_who_asked(alice, smtp):
    """A comment on a task you have or gave, and a mention of you: each a
    mail of its own kind, to who turned that kind on, never to its author."""
    task = new(alice, title="Offer for Acme", assignee="bob")
    mails(smtp, 1)
    smtp.mails.clear()
    url = f"/api/tasks/{task['id']}/comments"
    mail_on(alice, "bob", "assigned,comments")
    assert alice.post(url, json={"text": "Any news?"}, headers=HOST).status_code == 201      # alice, its maker, to bob who has it
    (to, text), = mails(smtp, 1)
    assert to == "bob@example.org" and "alice commented" in text and "Any news?" in text and f"{BASE}/tasks?task={task['id']}" in text
    smtp.mails.clear()
    # bob answers: alice gave the task, but didn't ask for that mail.
    login(alice, "bob")
    assert alice.post(url, json={"text": "Waiting for the contract"}, headers=HOST).status_code == 201
    mails(smtp, 0)
    # A mention reaches who it names, whatever the task: alice turned that on.
    mail_on(alice, "alice", "assigned,mentioned")
    login(alice, "bob")
    other = new(alice, title="Nobody's yet")
    assert alice.post(f"/api/tasks/{other['id']}/comments", json={"text": "@alice can you look? not bob@example.org"}, headers=HOST).status_code == 201
    (to, text), = mails(smtp, 1)
    assert to == "alice@example.org" and "bob mentioned you" in text
    login(alice, "alice")
    news = alice.get("/api/tasks?updates=1", headers=HOST).get_json()
    assert [e["event"] for e in next(t for t in news if t["id"] == other["id"])["history"]][-1] == "mentioned"


def test_the_count_is_of_every_task_with_news_for_you(alice, smtp):
    """The Tasks links' count: a comment on a task you gave away, a mention,
    a task of yours that someone closed; opening the task (seen) takes it
    away, with one seen entry a user however often they look."""
    count = lambda: alice.get("/api/tasks/due-count", headers=HOST).get_json().get("new", 0)
    given = new(alice, title="Given away", assignee="bob")
    assert count() == 0
    login(alice, "bob")
    assert count() == 1                                                     # given to him
    assert alice.post(f"/api/tasks/{given['id']}/comments", json={"text": "On it"}, headers=HOST).status_code == 201
    assert count() == 0                                                     # his own comment: he has seen it
    login(alice, "alice")
    assert count() == 1                                                     # bob commented on what she gave him
    updates = lambda: {t["id"]: t for t in alice.get("/api/tasks?updates=1", headers=HOST).get_json()}
    mine = lambda: {t["id"] for t in alice.get("/api/tasks?mine=1&with=order", headers=HOST).get_json()["tasks"]}
    assert set(updates()) == {given["id"]} and given["id"] not in mine()     # in Updates; not hers to do
    assert alice.put(f"/api/tasks/{given['id']}", json={"seen": True}, headers=HOST).status_code == 200
    assert count() == 0
    # bob closes it: news again, though it is closed; she looks again: one seen of hers.
    login(alice, "bob")
    assert alice.put(f"/api/tasks/{given['id']}", json={"completed": True}, headers=HOST).status_code == 200
    login(alice, "alice")
    assert count() == 1
    assert updates()[given["id"]]["closed_at"] and given["id"] not in mine()  # a closed task is in Updates too
    seen = alice.put(f"/api/tasks/{given['id']}", json={"seen": True}, headers=HOST).get_json()
    assert count() == 0 and updates() == {}
    assert sum(1 for e in seen["history"] if e["event"] == "seen" and e["by"] == "alice") == 1


def test_updates_is_a_page_with_the_tasks_that_have_news(alice, wiki_dir, smtp):
    """Updates (/updates; Updates in docs/spec/navigation.md): a page of its
    own, not a view of Tasks, whose Tasks section is the task app with the
    tasks that have news for who looks, and nobody else's. The top bar's
    bell leads there from every page, and so does the list switcher."""
    given = new(alice, title="Check the invoice", assignee="bob")
    new(alice, title="Hers", assignee="alice")
    page = lambda url: alice.get(url, headers=HOST).get_data(as_text=True)
    ids = lambda: [t["id"] for t in alice.get("/api/tasks?updates=1", headers=HOST).get_json()]
    assert ids() == []                                                      # she gave it: no news for her
    resp = alice.get("/tasks?updates=1", headers=HOST)                      # where it was
    assert resp.status_code == 302 and resp.headers["Location"] == "/updates"
    html = page("/updates")
    assert 'data-init-updates="1"' in html and "<h1>Updates</h1>" in html and "<title>Updates" in html
    assert '<h2 id="updates-tasks-h">Tasks</h2>' in html and 'id="updates-read-btn"' in html
    assert 'class="top-updates" href="/updates"' in html and 'aria-current="page">' in html.split('class="top-updates"')[1][:200]
    assert 'data-place="updates"' not in html and 'class="module-link" data-module="tasks" title="Tasks" aria-current' not in html
    # No settings, no menu of the list's, no tasks of anyone's in the page.
    assert "data-settings-" not in html and 'id="task-more-btn"' not in html
    assert "Check the invoice" not in html and "Hers" not in html
    # The Tasks links don't come back to it: they keep the view before.
    assert 'href="/tasks?mine=1" class="module-link" data-module="tasks"' in page("/page/index")
    mine = page("/tasks?mine=1")
    assert "tasks-updates-note" not in mine and "data-init-updates" not in mine
    assert '<a href="/updates" class="tasks-place' in mine                  # the switcher's Updates leads to the page
    # bob has it: on his Updates, with the task in the page.
    login(alice, "bob")
    assert ids() == [given["id"]]
    assert "Check the invoice" in page("/updates") and "Hers" not in page("/updates")
    # dan sees the list and has nothing to do with the task: no news.
    login(alice, "dan")
    assert ids() == [] and "Check the invoice" not in page("/updates")
    # An embed doesn't show it: updates=1 on a page is every task.
    write_page(wiki_dir, "desk", "# Desk\n\n[[tasks: updates=1]]\n")
    assert "data-init-updates" not in page("/page/desk") and "Check the invoice" in page("/page/desk")
    # In sharing mode what others wrote to you isn't shown: no page, no bell, no pill.
    login(alice, "bob")
    alice.set_cookie("zone-sharing", "1")
    resp = alice.get("/updates", headers=HOST)
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    assert 'data-place="updates"' not in page("/tasks?mine=1") and "top-updates" not in page("/tasks?mine=1")


def test_updates_needs_users(client):
    """Without users nobody gives anyone a task: no Updates, its address
    goes home, no bell, and its scope holds nothing."""
    assert client.post("/api/tasks", json={"title": "Mine"}).status_code == 201
    resp = client.get("/updates")
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    assert client.get("/api/tasks?updates=1").get_json() == []
    page = client.get("/tasks").get_data(as_text=True)
    assert 'data-place="updates"' not in page and "top-updates" not in page


def test_a_look_is_the_users_not_the_tasks(alice, wiki_dir, smtp):
    """What a user has seen is kept beside the tasks (.zone/seen/<user>.json):
    opening a task with news writes no task file and changes no list's word,
    so nobody's list loads again for a look; the API still answers who saw
    it, in the task's history; an undo that sends the history back writes no
    look into the task; and a "seen" entry in a task's file is no look."""
    import json
    task = new(alice, title="Check the invoice", assignee="bob", due_date="2030-01-01")
    url = f"/api/tasks/{task['id']}"
    path = wiki_dir / "_tasks" / "lists" / "team" / "tasks.json"
    before = path.read_text()
    login(alice, "bob")
    word = alice.get("/api/tasks/due-count", headers=HOST).headers["Zone-Tasks"]
    assert alice.get("/api/tasks/due-count", headers=HOST).get_json()["new"] == 1
    seen = alice.put(url, json={"seen": True}, headers=HOST).get_json()
    assert [(e["event"], e.get("by")) for e in seen["history"]] == [("created", "alice"), ("seen", "bob")]
    assert path.read_text() == before                                           # no task was written
    assert alice.get("/api/tasks/due-count", headers=HOST).headers["Zone-Tasks"] == word
    assert "new" not in alice.get("/api/tasks/due-count", headers=HOST).get_json()
    looks = json.loads((wiki_dir / ".zone" / "seen" / "bob.json").read_text())
    assert list(looks) == [task["id"]] and looks[task["id"]]["at"][:4].isdigit()
    # alice, who gave it, reads that it arrived.
    login(alice, "alice")
    assert [e["event"] for e in alice.get("/api/tasks", headers=HOST).get_json()[0]["history"]] == ["created", "seen"]
    # An undo sends the old history back, with bob's look in it: the task's file gets none.
    changed = alice.put(url, json={"due_date": "2030-02-02"}, headers=HOST).get_json()
    assert alice.put(url, json={"due_date": "2030-01-01", "history": seen["history"]}, headers=HOST).status_code == 200
    assert '"seen"' not in path.read_text() and [e["event"] for e in changed["history"]] == ["created", "seen", "due"]
    assert [e["event"] for e in alice.get("/api/tasks", headers=HOST).get_json()[0]["history"]] == ["created", "seen"]
    # A task whose file says that dan saw it: he hasn't, as far as Zonewiki knows.
    other = new(alice, title="An old one", assignee="dan")
    tasks = json.loads(path.read_text())
    for t in tasks:
        if t["id"] == other["id"]:
            t["history"].append({"at": "2030-01-01T10:00", "event": "seen", "by": "dan"})
    path.write_text(json.dumps(tasks))
    login(alice, "dan")
    assert alice.get("/api/tasks/due-count", headers=HOST).get_json()["new"] == 1
    [old] = [t for t in alice.get("/api/tasks", headers=HOST).get_json() if t["id"] == other["id"]]
    assert "seen" not in [e["event"] for e in old["history"]]


def test_the_history_keeps_who_made_a_task_and_who_has_it(alice, smtp):
    """The history holds 30 entries beside those a task is read by for good:
    created, the last assigned, the last shared."""
    task = new(alice, title="Moved often")
    url = f"/api/tasks/{task['id']}"
    assert alice.put(url, json={"assignee": "bob"}, headers=HOST).status_code == 200
    for day in range(1, 41):
        assert alice.put(url, json={"due_date": f"2031-{1 + day // 28:02d}-{1 + day % 28:02d}"}, headers=HOST).status_code == 200
    history = alice.get("/api/tasks", headers=HOST).get_json()[0]["history"]
    assert [e["event"] for e in history[:2]] == ["created", "assigned"] and len(history) == 32
    assert alice.get("/api/tasks", headers=HOST).get_json()[0]["created_by"] == "alice"
