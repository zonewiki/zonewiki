"""Following a task (docs/spec/tasks.md, News on your tasks): a third
way to have to do with a shared task, beside having it and having made it.
Who follows is told what who made it is told (a comment, closed, reopened,
given on), in the app and by mail, without the task being
theirs to do. Kept in the task (followers); only oneself; only who sees it.
The rule's cases are in go/testdata/news_cases.json, run through the server
and the browser."""

import json
import time

from tests import settings
from tests.conftest import HOST
from tests.test_task_notify import alice, login, mail_on, mails, new, smtp  # noqa: F401  (fixtures)


def put(client, task, **fields):
    return client.put(f"/api/tasks/{task['id']}", json=fields, headers=HOST)


def count(client):
    return client.get("/api/tasks/due-count", headers=HOST).get_json().get("new", 0)


def mine(client):
    return {t["id"]: t for t in client.get("/api/tasks?mine=1&with=order", headers=HOST).get_json()["tasks"]}


def updates(client):
    """What Updates holds for who is signed in (GET /api/tasks?updates=1), by id."""
    return {t["id"]: t for t in client.get("/api/tasks?updates=1", headers=HOST).get_json()}


def comment(client, task, text):
    assert client.post(f"/api/tasks/{task['id']}/comments", json={"text": text}, headers=HOST).status_code == 201


def test_follow_and_unfollow_are_kept_in_the_task(alice, wiki_dir, smtp):
    task = new(alice, title="Offer for Acme", assignee="bob")
    assert "followers" not in task
    login(alice, "dan")
    got = put(alice, task, follow=True)
    assert got.status_code == 200 and got.get_json()["followers"] == ["dan"]
    assert put(alice, task, follow=True).get_json()["followers"] == ["dan"]             # once
    login(alice, "alice")
    assert put(alice, task, follow=True).get_json()["followers"] == ["dan", "alice"]
    kept = next(t for t in json.loads((wiki_dir / "_tasks/lists/team/tasks.json").read_text()) if t["id"] == task["id"])
    assert kept["followers"] == ["dan", "alice"]
    # That someone follows is no news and no history: only looks (seen), which the API answers, are there.
    assert {e["event"] for e in kept["history"]} <= {"created", "assigned"}
    # No longer: the key goes with the last one.
    assert put(alice, task, follow=False).get_json()["followers"] == ["dan"]
    login(alice, "dan")
    assert "followers" not in put(alice, task, follow=False).get_json()
    assert "followers" not in (wiki_dir / "_tasks/lists/team/tasks.json").read_text()
    assert "followers" not in put(alice, task, follow=False).get_json()                 # twice changes nothing


def test_who_follows_is_told_what_its_maker_is_told(alice, smtp):
    task = new(alice, title="Offer for Acme", assignee="bob")
    other = new(alice, title="Not followed", assignee="bob")
    login(alice, "dan")
    assert count(alice) == 0
    assert put(alice, task, follow=True).status_code == 200
    assert count(alice) == 0
    # It is not his to do: My tasks doesn't hold it.
    assert task["id"] not in mine(alice)
    # bob comments on both: news on the one dan follows.
    login(alice, "bob")
    comment(alice, task, "Waiting for the contract")
    comment(alice, other, "Nothing yet")
    login(alice, "dan")
    assert count(alice) == 1
    assert set(updates(alice)) == {task["id"]}                                          # in Updates,
    assert task["id"] not in mine(alice)                                                # and still not his to do
    # He looks: gone. bob closes it: news again; reopened: again.
    assert put(alice, task, seen=True).status_code == 200
    assert count(alice) == 0
    for done in (True, False):
        login(alice, "bob")
        assert put(alice, task, completed=done).status_code == 200
        login(alice, "dan")
        assert count(alice) == 1
        assert put(alice, task, seen=True).status_code == 200
        assert count(alice) == 0
    # A date is no news; given to someone else is.
    login(alice, "bob")
    assert put(alice, task, due_date="2030-01-01").status_code == 200
    login(alice, "dan")
    assert count(alice) == 0
    login(alice, "bob")
    assert put(alice, task, assignee="alice").status_code == 200
    login(alice, "dan")
    assert count(alice) == 1
    # No longer following: nothing of it is told.
    assert put(alice, task, follow=False).status_code == 200
    assert count(alice) == 0 and task["id"] not in mine(alice)


def test_what_was_said_before_is_no_news_to_who_starts_to_follow(alice, smtp):
    task = new(alice, title="Offer for Acme", assignee="bob")
    login(alice, "bob")
    comment(alice, task, "Old news")
    time.sleep(0.05)
    login(alice, "dan")
    assert put(alice, task, follow=True).status_code == 200
    assert count(alice) == 0
    # His look names the comment it saw (what the browser's rule reads), and a
    # comment right after it, in the same minute, is news.
    got = alice.get("/api/tasks?list=team", headers=HOST).get_json()[0]
    (look,) = [e for e in got["history"] if e["event"] == "seen"]
    assert look["by"] == "dan" and look["to"] == got["comments"][-1]["id"]
    login(alice, "bob")
    comment(alice, task, "And this is new")
    login(alice, "dan")
    assert count(alice) == 1


def test_only_oneself_only_a_shared_task_and_no_viewer(alice, smtp, monkeypatch):
    task = new(alice, title="Offer for Acme", assignee="bob")
    # A write names no one: follow is true or false.
    for bad in ("bob", ["bob"], 1):
        assert put(alice, task, follow=bad).status_code == 400
    # followers is no field of a write.
    assert "followers" not in put(alice, task, followers=["bob", "dan"], title="Offer for Acme AG").get_json()
    # A private task has nobody else.
    own = alice.post("/api/tasks", json={"title": "My own"}, headers=HOST).get_json()
    assert own["private"] is True
    resp = put(alice, own, follow=True)
    assert resp.status_code == 400 and "private task" in resp.get_json()["error"]
    # A viewer changes nothing.
    login(alice, "carol")
    assert put(alice, task, follow=True).status_code == 403
    login(alice, "alice")
    assert "followers" not in next(t for t in alice.get("/api/tasks?list=team", headers=HOST).get_json() if t["id"] == task["id"])


def test_without_users_nobody_follows(client, wiki_dir):
    task = client.post("/api/tasks", json={"title": "Alone"}, headers=HOST).get_json()
    resp = client.put(f"/api/tasks/{task['id']}", json={"follow": True}, headers=HOST)
    assert resp.status_code == 400 and "no users" in resp.get_json()["error"]
    assert client.put(f"/api/tasks/{task['id']}", json={"follow": False}, headers=HOST).status_code == 200


def test_a_comment_is_mailed_to_who_follows_and_asked_for_it(alice, smtp):
    task = new(alice, title="Offer for Acme", assignee="dan")       # dan has no address
    mail_on(alice, "bob", "assigned,comments", was="mail=assigned")
    comment(alice, task, "First")
    mails(smtp, 0)                                                  # bob doesn't follow it yet
    login(alice, "bob")
    assert put(alice, task, follow=True).status_code == 200
    login(alice, "alice")
    comment(alice, task, "Any news?")
    (to, text), = mails(smtp, 1)
    assert to == "bob@example.org" and "alice commented" in text and "Any news?" in text
    assert "a task you have, gave or follow" in text
    smtp.mails.clear()
    # His own comment is not mailed to him, and unfollowed nothing is.
    login(alice, "bob")
    comment(alice, task, "Mine")
    assert put(alice, task, follow=False).status_code == 200
    login(alice, "alice")
    comment(alice, task, "Still there?")
    mails(smtp, 0)


def test_the_next_occurrence_keeps_its_followers(alice, smtp):
    task = new(alice, title="Weekly report", assignee="bob", due_date="2030-01-07", rec_type="weekly")
    login(alice, "dan")
    assert put(alice, task, follow=True).status_code == 200
    login(alice, "bob")
    assert put(alice, task, completed=True).status_code == 200
    (following,) = [t for t in alice.get("/api/tasks?list=team", headers=HOST).get_json() if t["title"] == "Weekly report"]
    assert following["id"] != task["id"] and following["followers"] == ["dan"]


def test_is_following_filters_and_an_assistant_follows_for_its_user(alice, smtp):
    task = new(alice, title="Offer for Acme", assignee="bob")
    other = new(alice, title="Not followed", assignee="bob")
    call = lambda who, tool, **args: "".join(c["text"] for c in alice.post(
        "/mcp", headers={**HOST, "Authorization": f"Bearer {who}-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).get_json()["result"]["content"])
    call("dan", "update_task", id=task["id"], follow=True)
    out = call("dan", "search_tasks", list="team")
    assert out.count("the user follows it") == 1
    assert "the user follows it" not in call("bob", "search_tasks", list="team")
    # The filter's word, where the server filters (the Closed view's page).
    for t in (task, other):
        login(alice, "bob")
        assert put(alice, t, completed=True).status_code == 200
    login(alice, "dan")
    page = alice.get("/api/tasks/closed", query_string={"limit": "10", "q": "is:following"}, headers=HOST).get_json()
    assert [t["title"] for t in page["tasks"]] == ["Offer for Acme"] and page["total"] == 1
    login(alice, "bob")
    assert alice.get("/api/tasks/closed", query_string={"limit": "10", "q": "is:following"}, headers=HOST).get_json()["total"] == 0
    call("dan", "update_task", id=task["id"], follow=False)
    login(alice, "dan")
    assert alice.get("/api/tasks/closed", query_string={"limit": "10", "q": "is:following"}, headers=HOST).get_json()["total"] == 0


def test_an_assistant_is_told_all_that_happened_since_its_user_looked(alice, smtp):
    """search_tasks says of a task with news all of it, the oldest first, as
    its card in Updates does: not the latest alone."""
    task = new(alice, title="Offer for Acme", assignee="bob")
    call = lambda who, tool, **args: "".join(c["text"] for c in alice.post(
        "/mcp", headers={**HOST, "Authorization": f"Bearer {who}-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).get_json()["result"]["content"])
    assert "update:" not in call("alice", "search_tasks", status="updated")
    login(alice, "bob")
    comment(alice, task, "Called them")
    assert "update: bob commented (" in call("alice", "search_tasks", status="updated")
    comment(alice, task, "They said yes")
    login(alice, "dan")
    comment(alice, task, "Nice")
    login(alice, "bob")
    assert put(alice, task, completed=True).status_code == 200
    out = call("alice", "search_tasks", status="updated")
    # (All within a minute here, where a comment counts as the later one:
    # the order of the three isn't what this asks.)
    said = out.split("update: ", 1)[1].split(" (", 1)[0].split(", ")
    assert sorted(said) == ["bob closed it", "bob commented twice", "dan commented"]
    # Her look takes all of it away.
    login(alice, "alice")
    assert put(alice, task, seen=True).status_code == 200
    assert "update:" not in call("alice", "search_tasks", status="updated")
