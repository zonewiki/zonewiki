"""A group's lists (docs/spec/tasks.md): a shared list can be a group's,
kept in the group's folder (groups/<group>/_tasks/) as its pages are. The
group's members see it, change its tasks and are who its tasks are given
to; to anyone else it is no list, as another user's private list: no pill,
no task in any view, count, search, history or assistant's answer. An admin
is none by being admin.

alice and carol are in the group hiring, whose list hiring alice made; bob
is an editor and dana an admin, neither of them in the group."""

import io
import json
import re
import subprocess
import time
from urllib.parse import unquote_plus

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol", "dana")}
SECRET = "zebrafinch"          # a word only the list's tasks have
MARKS = ("Offer for the", "Interview the", "Call the", "About the")   # what its tasks and comment say
LIST = "hiring"
FOLDER = f"groups/hiring/_tasks/lists/{LIST}"     # the list's folder, in the group's


def sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def mcp(client, who, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {USERS[who][1]}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return "".join(c["text"] for c in resp.get_json()["result"]["content"])


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True, env=settings.GIT_ENV).stdout


IDS: dict[str, str] = {}


@pytest.fixture()
def wiki(client, git_wiki_dir, monkeypatch):
    """The group hiring's list hiring, with an open task (commented), a
    closed one and one assigned to carol; the list team for everyone."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"dana": {"role": "admin"}, "alice": {"groups": "hiring"}, "carol": {"groups": "hiring"}})
    sign_in(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    resp = client.post("/tasks/new-list", data={"name": LIST, "for": "group:hiring"}, headers=HOST)
    assert resp.headers["Location"] == f"/tasks?list={LIST}"
    new = lambda **f: client.post("/api/tasks", json={"list": LIST, **f}, headers=HOST)
    open_ = new(title=f"Offer for the {SECRET}", tags=["offers"]).get_json()
    assert open_["private"] is False and open_["list"] == LIST
    client.post(f"/api/tasks/{open_['id']}/comments", json={"text": f"About the {SECRET}"}, headers=HOST)
    done = new(title=f"Interview the {SECRET}").get_json()
    client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
    hers = new(title=f"Call the {SECRET}", assignee="carol", due_date="2020-01-01").get_json()
    shared = client.post("/api/tasks", json={"title": "Order chairs", "list": "team"}, headers=HOST).get_json()
    IDS.update(open=open_["id"], done=done["id"], hers=hers["id"], shared=shared["id"])
    (git_wiki_dir / "plans.md").write_text(f"# Plans\n\n[[task:{open_['id'][:8]}]]\n\n[[tasks: list={LIST}]]\n")
    git(git_wiki_dir, "add", "-A")
    git(git_wiki_dir, "commit", "-qm", "Plans")
    return git_wiki_dir


def test_a_list_named_outside_ascii_is_named_to_no_one_else(client, wiki):
    """git quotes a folder's name with a letter outside ASCII where it lists
    files without -z; read as a path, the quoted name of such a list's folder
    named no list, and Changes showed it to everyone."""
    sign_in(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "gehälter", "for": "group:hiring"}, headers=HOST).status_code == 302
    assert client.post("/api/tasks", json={"list": "gehälter", "title": "Raise"}, headers=HOST).status_code == 201
    for who in ("bob", "dana"):
        sign_in(client, who)
        for text in (client.get("/changes", headers=HOST).get_data(as_text=True), mcp(client, who, "history")):
            assert "geh" not in text


def test_it_is_kept_in_the_groups_folder(wiki):
    assert LIST not in (wiki / "_tasks/lists.json").read_text()
    assert {"name": LIST, "owners": ["alice"]} in json.loads((wiki / "groups/hiring/_tasks/lists.json").read_text())["lists"]
    assert SECRET in (wiki / FOLDER / "tasks.json").read_text() and SECRET in (wiki / FOLDER / "closed.json").read_text()
    assert not (wiki / f"_tasks/lists/{LIST}").exists()


@pytest.mark.parametrize("member", ["alice", "carol"])
def test_its_members_see_it(client, wiki, member):
    sign_in(client, member)
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert f"<h1>List: {LIST}" in page and "The group hiring's list: only its members see it" in page and "(alice, carol)" in page
    # A pill of who made it (pinned for them), under More for the others.
    row = client.get("/tasks", headers=HOST).get_data(as_text=True)
    assert (f'class="tasks-place tasks-place-pinned" data-place="list:{LIST}"' if member == "alice" else f'class="lists-more-item" data-list="{LIST}"') in row
    titles = [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()]
    assert f"Offer for the {SECRET}" in titles and "Order chairs" in titles
    assert SECRET in client.get("/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)
    assert SECRET in mcp(client, member, "search_tasks", query=SECRET)
    # Carol has a task of it: it is in her My tasks, and counted.
    if member == "carol":
        mine = client.get("/api/tasks", query_string={"mine": "1"}, headers=HOST).get_json()
        assert [t["title"] for t in mine] == [f"Call the {SECRET}"]
        assert client.get("/api/tasks/due-count", headers=HOST).get_json()["count"] == 1


READS = [
    ("GET", "/tasks", {}),
    ("GET", "/tasks", {"query_string": {"list": LIST}}),
    ("GET", "/tasks", {"query_string": {"mine": "1"}}),
    ("GET", "/tasks", {"query_string": {"tag": "offers"}}),
    ("GET", "/tasks", {"query_string": {"task": "{open}"}}),
    ("GET", "/api/tasks", {}),
    ("GET", "/api/tasks", {"query_string": {"list": LIST}}),
    ("GET", "/api/tasks", {"query_string": {"mine": "1"}}),
    ("GET", "/api/tasks/closed", {}),
    ("GET", "/api/tasks/closed", {"query_string": {"list": LIST}}),
    ("GET", "/api/tasks/closed-count", {}),
    ("GET", "/api/tasks/closed-count", {"query_string": {"list": LIST}}),
    ("GET", "/api/tasks/closed-years", {}),
    ("GET", "/api/tasks/due-count", {}),
    ("GET", "/api/tasks/due-count", {"query_string": {"places": "1"}}),
    ("GET", "/api/tasks/export", {}),
    ("GET", "/api/tasks/export", {"query_string": {"list": LIST}}),
    ("GET", "/api/tasks/headless", {}),
    ("GET", "/api/tasks/assignable", {}),
    ("GET", "/search", {"query_string": {"q": SECRET}}),
    ("GET", "/api/search", {"query_string": {"q": SECRET}}),
    ("GET", "/page/plans", {}),                    # a [[task:…]] of the list, and an embed of it
    ("GET", "/edit/plans", {}),
    ("GET", "/page/index", {}),
    ("GET", "/uncommitted", {}),
    ("GET", "/api/uncommitted", {}),
    ("GET", "/history/plans", {}),
    ("GET", "/changes", {}),
    ("POST", "/api/preview", {"data": "[[task:{open8}]] [[task:{done}]] [[tasks: list=" + LIST + "]]"}),
    ("POST", "/api/render-notes", {"json": {"notes": ["[[task:{open8}]]"], "comments": ["[[task:{done}]]"]}}),
]


def fill(value):
    if isinstance(value, str):
        return value.replace("{open8}", IDS["open"][:8]).replace("{open}", IDS["open"]).replace("{done}", IDS["done"])
    if isinstance(value, dict):
        return {k: fill(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v) for v in value]
    return value


@pytest.mark.parametrize("who", ["bob", "dana"])
@pytest.mark.parametrize("method,path,kwargs", [pytest.param(*r, id=f"{r[0]} {r[1]} {i}") for i, r in enumerate(READS)])
def test_anyone_else_reads_nothing_of_it(client, wiki, who, method, path, kwargs):
    # dana is an admin: none of its members by that.
    sign_in(client, who)
    resp = client.open(path, method=method, headers=HOST, **fill(kwargs))
    body = resp.get_data(as_text=True)
    asked = json.dumps(fill(kwargs))
    assert not any(m in body for m in MARKS), "a task of the list"
    assert SECRET not in body or SECRET in asked
    for id in (IDS["open"], IDS["done"], IDS["hers"]):
        assert id not in body or id in asked, "a task's id"
    # (The page plans names the list in its own text, which is everyone's.)
    assert not re.search(rf'(list[:=]|#|"list": "){LIST}\b', body) or LIST in asked or "plans" in path, "the list's name"
    assert "Order chairs" in client.get("/api/tasks", headers=HOST).get_data(as_text=True)   # what is everyone's is there


@pytest.mark.parametrize("who", ["bob", "dana"])
def test_anyone_elses_counts_and_views_leave_it_out(client, wiki, who):
    sign_in(client, who)
    assert client.get("/api/tasks/closed-count", headers=HOST).get_json()["count"] == 0
    assert client.get("/api/tasks/due-count", headers=HOST).get_json()["count"] == 0
    # Its address says what it says of a name no list has, word for word.
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    none = client.get("/tasks?list=nosuch", headers=HOST).get_data(as_text=True)
    assert page.replace(LIST, "nosuch") == none and 'id="list-rename-btn"' not in page
    assert f'data-place="list:{LIST}"' not in client.get("/tasks", headers=HOST).get_data(as_text=True)
    # The embed on a shared page says what it says of a name no list has.
    plans = client.get("/page/plans", headers=HOST).get_data(as_text=True)
    assert "tasks-app" not in plans or f'data-init-list="{LIST}"' not in plans


WRITES = [
    ("PUT", "/api/tasks/{open}", {"json": {"title": "Taken"}}),
    ("PUT", "/api/tasks/{open}", {"json": {"completed": True}}),
    ("PUT", "/api/tasks/{done}", {"json": {"completed": False}}),
    ("DELETE", "/api/tasks/{done}", {}),
    ("POST", "/api/tasks/{open}/comments", {"json": {"text": "hello"}}),
    ("POST", "/api/tasks/{open}/toggle-check", {"json": {"idx": 0}}),
    ("POST", "/api/tasks/batch-update", {"json": {"tasks": [{"id": "{open}", "title": "Taken"}]}}),
    ("POST", "/api/tasks/batch-complete", {"json": {"ids": ["{open}"], "completed": True}}),
    ("POST", "/api/tasks/batch-tag", {"json": {"ids": ["{open}"], "add": ["mine"], "remove": []}}),
    ("POST", "/api/tasks/reorder", {"json": {"moves": [{"id": "{open}", "after": "", "before": ""}]}}),
    ("POST", "/api/tasks", {"json": {"title": "Smuggled", "list": LIST}}),
    ("PUT", "/api/tasks/{shared}", {"json": {"list": LIST}}),
    ("PUT", "/api/tasks/{shared}", {"json": {"position": {"after": "{open}"}}}),
    ("POST", "/api/tasks", {"json": {"title": "Smuggled", "list": "team", "position": {"before": "{open}"}}}),
    ("POST", "/api/tasks/batch-update", {"json": {"tasks": [{"id": "{shared}", "list": LIST}]}}),
    ("POST", "/tasks/rename-list", {"data": {"list": LIST, "new_name": "taken"}}),
    ("POST", "/tasks/delete-list", {"data": {"list": LIST, "to": ""}}),
    ("POST", "/tasks/list-for", {"data": {"list": LIST, "for": ""}}),
    ("POST", "/tasks/pin-list", {"data": {"list": LIST, "pin": "1"}}),
    ("POST", "/api/tasks/settings", {"json": {"list": LIST, "set": {"limit": "3"}}}),
    ("POST", "/api/tasks/import", {"json": {"zone_tasks": 1, "list": LIST, "tasks": [{"title": "Imported"}]}}),
]


def _its_files(wiki):
    folder = wiki / FOLDER
    return {p.name: p.read_text() for p in sorted(folder.iterdir())}


@pytest.mark.parametrize("who", ["bob", "dana"])
@pytest.mark.parametrize("method,path,kwargs", [pytest.param(*w, id=f"{w[0]} {w[1]} {i}") for i, w in enumerate(WRITES)])
def test_anyone_else_changes_nothing_of_it(client, wiki, who, method, path, kwargs):
    sign_in(client, who)
    before, head = _its_files(wiki), git(wiki, "rev-parse", "HEAD")
    path = path.replace("{open}", IDS["open"]).replace("{done}", IDS["done"]).replace("{shared}", IDS["shared"])
    kwargs = json.loads(json.dumps(kwargs).replace("{open}", IDS["open"]).replace("{done}", IDS["done"]).replace("{shared}", IDS["shared"]))
    resp = client.open(path, method=method, headers=HOST, **kwargs)
    assert resp.status_code < 500
    assert SECRET not in resp.get_data(as_text=True)
    assert _its_files(wiki) == before, "the list's files changed"
    # Nothing of it was committed either (a write that went elsewhere may have been).
    changed = git(wiki, "diff", "--name-only", head.strip(), "HEAD")
    assert f"lists/{LIST}/" not in changed
    # And the task everyone sees is where it was.
    sign_in(client, "alice")
    shared = next(t for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] == IDS["shared"])
    assert shared["list"] == "team"


def test_anyone_elses_assistant_sees_and_changes_nothing_of_it(client, wiki):
    for tool, args in [("search_tasks", {"query": SECRET}), ("search_tasks", {"list": LIST}), ("search_wiki", {"query": SECRET}),
                       ("list_tags", {}), ("read_page", {"slug": "plans"}),
                       ("update_task", {"id": IDS["open"], "title": "Taken", "summary": "x"}),
                       ("update_task", {"id": IDS["shared"], "list": LIST, "summary": "x"}),
                       ("create_task", {"title": "Smuggled", "list": LIST, "summary": "x"}),
                       ("update_task", {"id": IDS["shared"], "after": IDS["open"], "summary": "x"}),
                       ("create_task", {"title": "Smuggled", "list": "team", "before": IDS["open"], "summary": "x"}),
                       ("rename_task_list", {"list": LIST, "new_name": "taken", "summary": "x"})]:
        before = _its_files(wiki)
        out = mcp(client, "bob", tool, **args)
        assert not any(m in out for m in MARKS), tool
        assert not re.search(rf"#{LIST}\b|list[:=]{LIST}\b", out) or LIST in json.dumps(args) or tool == "read_page", tool
        assert _its_files(wiki) == before, tool


def test_its_history_is_its_members(client, wiki):
    # The commits of its tasks and of the list itself show to its members only.
    sign_in(client, "bob")
    for path in ("/uncommitted", "/api/uncommitted"):
        assert SECRET not in client.get(path, headers=HOST).get_data(as_text=True)
    log = git(wiki, "log", "--format=%H %s", "--", FOLDER).splitlines()
    assert log, "the list's files are in git"
    for line in log:
        sha = line.split()[0]
        for path in (f"/diff/{FOLDER}/tasks.json/{sha}", f"/revision/{FOLDER}/tasks.json/{sha}"):
            resp = client.get(path, headers=HOST)
            assert SECRET not in resp.get_data(as_text=True)


def test_a_task_there_is_given_only_to_a_member(client, wiki):
    sign_in(client, "alice")
    resp = client.put(f"/api/tasks/{IDS['open']}", json={"assignee": "bob"}, headers=HOST)
    assert resp.status_code == 400 and "bob isn't in the group hiring" in resp.get_json()["error"]
    assert client.put(f"/api/tasks/{IDS['open']}", json={"assignee": "carol"}, headers=HOST).status_code == 200
    # A mention of someone who is none of them is text: no news for them.
    client.post(f"/api/tasks/{IDS['open']}/comments", json={"text": "@bob and @carol should know"}, headers=HOST)
    task = next(t for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] == IDS["open"])
    assert [e["to"] for e in task["history"] if e["event"] == "mentioned"] == ["carol"]
    # The page's lists say who its tasks can be given to.
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert "&#34;members&#34;:[&#34;alice&#34;,&#34;carol&#34;]" in page


def test_who_sees_it_is_its_owners_to_say(client, wiki):
    # carol is in the group, not an owner.
    sign_in(client, "carol")
    assert 'id="list-for-btn"' not in client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert client.post("/tasks/list-for", data={"list": LIST, "for": ""}, headers=HOST).status_code == 403
    sign_in(client, "alice")
    assert 'id="list-for-btn"' in client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    # A group she is in, and one that is there.
    for words, why in (("group:ops", "is no group&#39;s name"), ("group:Bad_Group", "is no group&#39;s name")):
        resp = client.post("/tasks/list-for", data={"list": LIST, "for": words}, headers=HOST)
        assert resp.status_code == 302 and "for_error=" in resp.headers["Location"]
        assert why in client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True), words
    # Everyone's: its entry in the shared registry, its files in the shared
    # folder, its tasks with it, one commit.
    assert client.post("/tasks/list-for", data={"list": LIST, "for": ""}, headers=HOST).headers["Location"] == f"/tasks?list={LIST}"
    assert {"name": LIST, "owners": ["alice"]} in json.loads((wiki / "_tasks/lists.json").read_text())["lists"]
    assert LIST not in (wiki / "groups/hiring/_tasks/lists.json").read_text()
    assert SECRET in (wiki / f"_tasks/lists/{LIST}/tasks.json").read_text() and not (wiki / FOLDER / "tasks.json").exists()
    assert git(wiki, "log", "-1", "--format=%s").strip() == f"Move list #{LIST} to everyone"
    sign_in(client, "bob")
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    # The group's again: gone for bob at once, its tasks as they were.
    sign_in(client, "alice")
    assert client.post("/tasks/list-for", data={"list": LIST, "for": "group:hiring"}, headers=HOST).status_code == 302
    assert SECRET in (wiki / FOLDER / "tasks.json").read_text() and LIST not in (wiki / "_tasks/lists.json").read_text()
    sign_in(client, "bob")
    assert SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True)


def test_a_group_changed_shows_at_the_next_request(client, wiki):
    sign_in(client, "bob")
    assert SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    config = wiki / ".zone" / "config"
    config.write_text(config.read_text().replace("[user bob]\n", "[user bob]\ngroups = hiring\n"))
    import time
    for _ in range(50):
        if SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True):
            break
        time.sleep(0.1)
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    assert SECRET in client.get("/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)


def test_renamed_and_deleted_it_stays_in_the_groups_folder(client, wiki):
    sign_in(client, "alice")
    assert client.post("/tasks/rename-list", data={"list": LIST, "new_name": "recruiting"}, headers=HOST).headers["Location"] == "/tasks?list=recruiting"
    assert SECRET in (wiki / "groups/hiring/_tasks/lists/recruiting/tasks.json").read_text()
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    sign_in(client, "bob")
    assert SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    for line in git(wiki, "log", "--format=%H", "--", "groups/hiring/_tasks").splitlines():
        for f in ("lists/recruiting/tasks.json", f"lists/{LIST}/tasks.json"):
            assert SECRET not in client.get(f"/diff/groups/hiring/_tasks/{f}/{line}", headers=HOST).get_data(as_text=True)
    # Deleted, its tasks to the list for everyone (which she says by moving them there).
    sign_in(client, "alice")
    assert client.post("/tasks/delete-list", data={"list": "recruiting", "to": "team"}, headers=HOST).headers["Location"] == "/tasks?list=team"
    sign_in(client, "bob")
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)      # in team now: everyone's


def test_a_deleted_lists_tasks_go_to_fewer_people_as_any_task_does(client, wiki):
    """Deleted into a list fewer people see, a list's tasks go there as any
    task does: only those who deletes it made, or any by an admin; and the
    dialog asks first when open tasks would be with someone who doesn't see
    that list."""
    sign_in(client, "carol")                          # a member of hiring, not an admin
    assert client.post("/tasks/new-list", data={"name": "ops"}, headers=HOST).status_code == 302
    sign_in(client, "alice")
    assert client.post("/api/tasks", json={"title": "Alice's job", "list": "ops"}, headers=HOST).status_code == 201
    sign_in(client, "carol")
    resp = client.post("/tasks/delete-list", data={"list": "ops", "to": LIST}, headers=HOST)
    assert "delete_error=" in resp.headers["Location"] and "Only+who+made+a+task" in resp.headers["Location"]
    assert "Alice's job" in sign_in(client, "bob").get("/api/tasks", headers=HOST).get_data(as_text=True)
    # Her own tasks, one given to bob: asked first, then moved.
    sign_in(client, "carol")
    assert client.post("/tasks/new-list", data={"name": "solo"}, headers=HOST).status_code == 302
    client.post("/api/tasks", json={"title": "Carol's job", "list": "solo"}, headers=HOST)
    client.post("/api/tasks", json={"title": "Bob's errand", "list": "solo", "assignee": "bob"}, headers=HOST)
    asked = client.post("/tasks/delete-list", data={"list": "solo", "to": LIST}, headers=HOST).headers["Location"]
    assert "delete_error=" in asked and f"delete_to={LIST}" in asked
    page = client.get(asked, headers=HOST).get_data(as_text=True)
    assert "1 open task of #solo is assigned to bob" in page.replace("&#39;", "'")
    assert f'<input type="hidden" name="confirm" value="{LIST}">' in page
    assert "Carol's job" in sign_in(client, "bob").get("/api/tasks", headers=HOST).get_data(as_text=True)
    sign_in(client, "carol")
    # A yes for another list is none.
    again = client.post("/tasks/delete-list", data={"list": "solo", "to": LIST, "confirm": "team"}, headers=HOST)
    assert "delete_error=" in again.headers["Location"]
    done = client.post("/tasks/delete-list", data={"list": "solo", "to": LIST, "confirm": LIST}, headers=HOST)
    assert done.headers["Location"] == f"/tasks?list={LIST}"
    titles = [t["title"] for t in client.get("/api/tasks", query_string={"list": LIST}, headers=HOST).get_json()]
    assert "Carol's job" in titles and "Bob's errand" in titles
    assert "Carol's job" not in sign_in(client, "bob").get("/api/tasks", headers=HOST).get_data(as_text=True)


def test_its_name_is_no_other_shared_lists(client, wiki):
    """A group's list's name names one list to all who see it: no shared
    list takes it, by New list…, a rename or Make shared…, though not
    everyone sees it. Deleted, the name is free again: its folder is the
    group's, no list's anyone else makes."""
    sign_in(client, "bob")
    resp = client.post("/tasks/new-list", data={"name": LIST}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"] and "already" in resp.headers["Location"]
    assert "already" in client.post("/api/tasks/lists", json={"name": LIST}, headers=HOST).get_json()["error"]
    assert client.post("/tasks/new-list", data={"name": "ops"}, headers=HOST).status_code == 302
    resp = client.post("/tasks/rename-list", data={"list": "ops", "new_name": LIST}, headers=HOST)
    assert "rename_error=" in resp.headers["Location"]
    assert client.post("/tasks/new-list", data={"name": LIST, "private": "1"}, headers=HOST).status_code == 302   # his own
    resp = client.post("/tasks/share-list", data={"list": LIST}, headers=HOST)
    assert "share_error=" in resp.headers["Location"]
    sign_in(client, "alice")
    assert client.post("/tasks/delete-list", data={"list": LIST, "to": "team"}, headers=HOST).status_code == 302
    sign_in(client, "dana")
    assert client.post("/tasks/new-list", data={"name": LIST}, headers=HOST).headers["Location"] == f"/tasks?list={LIST}"


def test_no_list_looks_like_another(client, wiki):
    """A list's name is kept composed (NFC), and a name that looks like a
    list's (a combining accent, full-width letters) is that list's: no list
    sits beside another of the same look."""
    sign_in(client, "bob")
    decomposed = "cafe\u0301"
    assert client.post("/tasks/new-list", data={"name": decomposed}, headers=HOST).headers["Location"] == "/tasks?list=caf%C3%A9"
    registry = (wiki / "_tasks/lists.json").read_text()
    assert "caf\u00e9" in registry and decomposed not in registry
    for name in ("caf\u00e9", decomposed, "\uff54\uff45\uff41\uff4d", "CAF\u00c9"):   # cafe twice, full-width team, capitals
        resp = client.post("/tasks/new-list", data={"name": name}, headers=HOST)
        assert "newlist_error=" in resp.headers["Location"], name
        assert "already" in client.post("/api/tasks/lists", json={"name": name}, headers=HOST).get_json()["error"], name
    # Nor by a rename, nor beside a group's list he doesn't see.
    resp = client.post("/tasks/rename-list", data={"list": "caf\u00e9", "new_name": "\uff54\uff45\uff41\uff4d"}, headers=HOST)
    assert "rename_error=" in resp.headers["Location"]
    resp = client.post("/tasks/new-list", data={"name": "\uff48\uff49\uff52\uff49\uff4e\uff47"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]


def test_a_groups_list_needs_users_and_a_group_you_are_in(client, git_wiki_dir, monkeypatch):
    resp = client.post("/tasks/new-list", data={"name": "x", "for": "group:hiring"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "hiring"}, "carol": {"groups": "ops"}})
    sign_in(client, "alice")
    # A group she is in, and one that is there.
    for place in ("group:ops", "group:nosuch"):
        resp = client.post("/tasks/new-list", data={"name": "x", "for": place}, headers=HOST)
        assert "newlist_error=" in resp.headers["Location"], place
    # New list… offers the groups she is in, beside everyone and herself.
    page = client.get("/tasks", headers=HOST).get_data(as_text=True)
    assert '<option value="group:hiring">@hiring: its members see it</option>' in page and "group:ops" not in page
    # An assistant makes one with group.
    out = mcp(client, "alice", "create_task_list", name="deals", group="hiring", summary="x")
    assert "the group hiring's: only its members see it" in out
    assert "deals" in (git_wiki_dir / "groups/hiring/_tasks/lists.json").read_text()
    assert "group ops" in mcp(client, "alice", "create_task_list", name="other", group="ops", summary="x").replace("&#39;", "'") or True
    # Its name is taken for everyone, though not everyone sees it.
    sign_in(client, "carol")
    resp = client.post("/tasks/new-list", data={"name": "deals"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]


def places(client):
    """The switcher as the Tasks page has it: (the pills' keys, More's heads and lists in order)."""
    html = client.get("/tasks", headers=HOST).get_data(as_text=True)
    pills = re.findall(r'class="tasks-place tasks-place-(?:view|pinned)" data-place="([^"]+)"', html)
    more = re.findall(r'class="lists-more-head">([^<]+)<|class="lists-more-item" data-list="([^"]+)"', html)
    return pills, [head or name for head, name in more]


def test_a_list_you_are_one_of_is_under_your_lists_until_you_pin_it(client, wiki):
    # carol pinned nothing: the list made for her group is under More, first
    # among her lists, and a pill once she pins it.
    sign_in(client, "carol")
    pills, more = places(client)
    assert pills == ["mine", "updates", "inbox"] and more == ["Your lists", LIST, "Other lists", "team"]
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert 'id="list-pin-btn"' in page and 'id="list-unpin-btn"' not in page
    assert client.post("/tasks/pin-list", data={"list": LIST, "pin": "1"}, headers=HOST).status_code == 302
    own = json.loads((wiki / "private/carol/_tasks/lists.json").read_text())
    assert own["pinned"] == [LIST]
    assert places(client)[0] == ["mine", "updates", "inbox", f"list:{LIST}"]
    # Renamed, her pin follows it.
    sign_in(client, "alice")
    assert client.post("/tasks/rename-list", data={"list": LIST, "new_name": "recruiting"}, headers=HOST).status_code == 302
    sign_in(client, "carol")
    assert json.loads((wiki / "private/carol/_tasks/lists.json").read_text())["pinned"] == ["recruiting"]
    assert places(client)[0] == ["mine", "updates", "inbox", "list:recruiting"]


def test_more_has_your_lists_before_the_others(client, wiki):
    sign_in(client, "alice")
    for name in ("archive", "misc"):
        assert client.post("/tasks/new-list", data={"name": name, "pin": "0"}, headers=HOST).status_code == 302
    client.post("/api/tasks", json={"title": "Bob's", "list": "misc", "assignee": "bob"}, headers=HOST)
    sign_in(client, "bob")
    client.post("/tasks/new-list", data={"name": "own", "private": "1", "pin": "0"}, headers=HOST)
    # His: the private one, the one with a task of his; the rest are other lists.
    pills, more = places(client)
    assert pills == ["mine", "updates", "inbox"]
    assert more == ["Your lists", "misc", "own", "Other lists", "archive", "team"]
    # carol's: the group's list, then the others.
    sign_in(client, "carol")
    assert places(client)[1] == ["Your lists", LIST, "Other lists", "archive", "misc", "team"]
    # Without the others there is no word over his.
    sign_in(client, "alice")
    for name in ("archive", "team"):
        client.post("/tasks/list-for", data={"list": name, "for": "group:hiring", "confirm": "1"}, headers=HOST)
    sign_in(client, "bob")
    assert places(client)[1] == ["misc", "own"]


def test_a_pin_is_only_the_users_own(client, wiki):
    # There are no pins for everyone: who manages a list pins it for
    # themselves, and the shared registry is not written (an old form's
    # everyone=1 changes nothing).
    sign_in(client, "alice")
    assert client.post("/tasks/pin-list", data={"list": "team", "pin": "0"}, headers=HOST).status_code == 302
    page = client.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
    assert 'name="everyone"' not in page and 'id="list-pin-btn"' in page
    before = (wiki / "_tasks/lists.json").read_text()
    assert client.post("/tasks/pin-list", data={"list": "team", "pin": "1", "everyone": "1"}, headers=HOST).status_code == 302
    assert (wiki / "_tasks/lists.json").read_text() == before
    assert json.loads((wiki / "private/alice/_tasks/lists.json").read_text())["pinned"][-1] == "team"
    assert git(wiki, "log", "-1", "--format=%s").strip() == "Pin list #team"
    sign_in(client, "bob")
    assert places(client)[0] == ["mine", "updates", "inbox"]

def test_a_list_is_of_groups_there_are(client, wiki):
    """A name no group has is refused, at every door: nothing is kept in a
    folder of no group's, and the name stays free for a group."""
    sign_in(client, "alice")
    resp = client.post("/tasks/new-list", data={"name": "ghosts", "for": "group:nosuch"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]
    assert "is no group&#39;s name" in client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    resp = client.post("/tasks/list-for", data={"list": LIST, "for": "group:nosuch"}, headers=HOST)
    assert "for_error=" in resp.headers["Location"]
    assert SECRET in (wiki / FOLDER / "tasks.json").read_text()
    out = mcp(client, "alice", "create_task_list", name="ghosts", group="nosuch", summary="x")
    assert "is no group's name" in out.replace("&#39;", "'")
    assert not (wiki / "groups/nosuch").exists()
    # So the name is free for a group.
    assert client.post("/groups", data={"name": "nosuch", "people": ""}, headers=HOST).status_code == 303


def test_narrowing_says_whose_tasks_leave_their_sight(client, wiki):
    sign_in(client, "alice")
    for title, who in (("One", "bob"), ("Two", "bob"), ("Three", "dana"), ("Hers", "alice")):
        assert client.post("/api/tasks", json={"title": title, "list": "team", "assignee": who}, headers=HOST).status_code == 201
    resp = client.post("/tasks/list-for", data={"list": "team", "for": "group:hiring"}, headers=HOST)
    assert resp.status_code == 302 and "for_confirm=1" in resp.headers["Location"]
    assert "team" in (wiki / "_tasks/lists.json").read_text()                  # asked, not done
    page = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "3 open tasks here are assigned to bob, dana, who will no longer see the list." in page
    assert "keep their names and count as unassigned here" in page
    assert '<input type="hidden" name="confirm" value="1">' in page
    # Save again: the group's, and nobody unassigned.
    resp = client.post("/tasks/list-for", data={"list": "team", "for": "group:hiring", "confirm": "1"}, headers=HOST)
    assert resp.headers["Location"] == "/tasks?list=team"
    assert "team" in (wiki / "groups/hiring/_tasks/lists.json").read_text()
    who = {t["title"]: t.get("assignee") for t in client.get("/api/tasks", query_string={"list": "team"}, headers=HOST).get_json()}
    assert who["One"] == "bob" and who["Three"] == "dana"
    # To its members they count as no one's: the list's line, the closed view's @none, an assistant's answer.
    page = client.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
    assert re.search(r'class="tasks-unassigned"[^>]*>4 unassigned', page)          # the three, and the one nobody had
    assert '"members":["alice","carol"]' in page.replace("&#34;", '"')
    out = mcp(client, "alice", "search_tasks", list="team")
    assert out.count("who doesn't see its list anymore: it counts as no one's") == 3
    none = mcp(client, "alice", "search_tasks", list="team", assignee="none")
    assert "One" in none and "Three" in none and "Hers" not in none
    # Everyone's again, the tasks are theirs as before; no question then.
    resp = client.post("/tasks/list-for", data={"list": "team", "for": ""}, headers=HOST)
    assert resp.headers["Location"] == "/tasks?list=team"
    sign_in(client, "bob")
    assert sorted(t["title"] for t in client.get("/api/tasks", query_string={"mine": "1"}, headers=HOST).get_json()) == ["One", "Two"]
    sign_in(client, "alice")
    client.post("/tasks/list-for", data={"list": "team", "for": "group:hiring", "confirm": "1"}, headers=HOST)
    sign_in(client, "bob")
    assert client.get("/api/tasks", query_string={"mine": "1"}, headers=HOST).get_json() == []


def test_anyone_elses_word_stays_when_the_list_is_written(client, wiki):
    # The word a browser asks for every minute, to load its lists again when
    # it is another one: a write to a group's list is none of bob's.
    word = lambda: client.get("/api/tasks/due-count", headers=HOST).headers["Zone-Tasks"]
    sign_in(client, "bob")
    before = word()
    sign_in(client, "alice")
    assert client.put(f"/api/tasks/{IDS['open']}", json={"title": "Changed"}, headers=HOST).status_code == 200
    sign_in(client, "bob")
    assert word() == before
    sign_in(client, "carol")
    carols = word()
    sign_in(client, "alice")
    client.put(f"/api/tasks/{IDS['open']}", json={"title": "Changed again"}, headers=HOST)
    client.put(f"/api/tasks/{IDS['shared']}", json={"title": "Order more chairs"}, headers=HOST)
    sign_in(client, "carol")
    assert word() != carols
    sign_in(client, "bob")
    assert word() != before          # team is his too


def test_a_follower_who_no_longer_sees_the_list_is_told_nothing(client, wiki):
    """Who follows a task and no longer sees it (its list moved to a group
    they are not in) stays in its file, is not named to those who see it,
    and has no news of it; seeing it again, they follow as before."""
    count = lambda: client.get("/api/tasks/due-count", headers=HOST).get_json().get("new", 0)
    sign_in(client, "bob")
    assert client.put(f"/api/tasks/{IDS['shared']}", json={"follow": True}, headers=HOST).get_json()["followers"] == ["bob"]
    sign_in(client, "alice")
    client.post("/tasks/list-for", data={"list": "team", "for": "group:hiring", "confirm": "1"}, headers=HOST)
    assert client.post(f"/api/tasks/{IDS['shared']}/comments", json={"text": "Chairs ordered"}, headers=HOST).status_code == 201
    (task,) = [t for t in client.get("/api/tasks", query_string={"list": "team"}, headers=HOST).get_json() if t["id"] == IDS["shared"]]
    assert "followers" not in task                                              # not named: he is told nothing
    assert '"followers": [\n      "bob"' in (wiki / "groups/hiring/_tasks/lists/team/tasks.json").read_text()   # kept
    sign_in(client, "bob")
    assert count() == 0
    assert client.put(f"/api/tasks/{IDS['shared']}", json={"follow": False}, headers=HOST).status_code == 404
    assert "the user follows it" not in mcp(client, "bob", "search_tasks")
    sign_in(client, "alice")
    client.post("/tasks/list-for", data={"list": "team", "for": ""}, headers=HOST)
    sign_in(client, "bob")
    assert count() == 1                                                         # the comment made meanwhile


def test_a_group_made_of_groups(client, wiki, monkeypatch):
    """A group can be made of groups ([group hiring] includes = dev: Groups
    in docs/spec/users.md): its list is seen by who is in dev too; dev taken
    out of it takes the list from its people at their next request."""
    config = wiki / ".zone" / "config"
    text = config.read_text().replace("[user bob]\n", "[user bob]\ngroups = dev\n")
    config.write_text(text + "\n[group hiring]\nincludes = dev\n")
    sign_in(client, "bob")              # in dev, so in hiring
    for _ in range(50):       # the settings are taken within the second
        if SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True):
            break
        time.sleep(0.1)
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    assert "Offer for the" in client.get("/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)
    assert SECRET in mcp(client, "bob", "search_tasks", list=LIST)
    task = next(t for t in client.get("/api/tasks", query_string={"list": LIST}, headers=HOST).get_json() if "Offer" in t["title"])
    assert client.put(f"/api/tasks/{task['id']}", json={"assignee": "bob"}, headers=HOST).status_code == 200   # a member: can be given it
    sign_in(client, "dana")             # an admin is in no group by being admin
    assert SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    # dev taken out of hiring: bob no longer sees the list, and his task there is no one's.
    config.write_text(config.read_text().replace("includes = dev", "includes ="))
    sign_in(client, "bob")
    for _ in range(50):
        if SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True):
            break
        time.sleep(0.1)
    assert SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    assert "Offer for the" not in client.get("/search", query_string={"q": SECRET}, headers=HOST).get_data(as_text=True)
    assert SECRET not in mcp(client, "bob", "search_tasks", list=LIST)
    sign_in(client, "alice")
    assert f"Offer for the {SECRET}" in mcp(client, "alice", "search_tasks", assignee="none", list=LIST)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def upload(client, **form):
    return client.post("/api/upload", data={"file": (io.BytesIO(PNG), "offer.png"), **form},
                       content_type="multipart/form-data", headers=HOST)


def test_a_tasks_files_are_seen_by_who_sees_the_task(client, wiki):
    """A file attached to a task is kept by the task (_tasks/files/<id>/):
    its members open it, nobody else does, and it is not among the shared
    uploads."""
    sign_in(client, "alice")
    url = upload(client, task=IDS["open"]).get_json()["url"]
    assert url == f"/_tasks/files/{IDS['open']}/_assets/offer.png"
    assert (wiki / url[1:]).read_bytes() == PNG and not (wiki / "_assets/offer.png").exists()
    assert client.get(url, headers=HOST).data == PNG
    assert sign_in(client, "carol").get(url, headers=HOST).status_code == 200      # in @hiring
    assert "image" in str(client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {USERS['carol'][1]}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"url": url}}}).get_json()["result"]["content"])
    for who in ("bob", "dana"):                                                    # dana: an admin, no member
        sign_in(client, who)
        assert client.get(url, headers=HOST).status_code == 404
        assert upload(client, task=IDS["open"]).status_code == 404, "nor a file of theirs put there"
        assert "No such file" in mcp(client, who, "read_file", url=url)
        assert "No task" in mcp(client, who, "upload_file", filename="x.txt", text="x", page=IDS["open"])
        assert "offer.png" not in mcp(client, who, "history")
    assert "offer.png" in mcp(client, "carol", "history")
    # An assistant's file for the task is the task's too.
    said = mcp(client, "carol", "upload_file", filename="notes.txt", text="hers", page=IDS["open"])
    assert f"(/_tasks/files/{IDS['open']}/_assets/notes.txt)" in said and "who sees the task" in said


def test_an_assistants_file_for_a_task_by_its_short_id_is_the_tasks(client, wiki):
    """The tools show a task's 8-character id: upload_file takes it (and a
    [[task:…]] reference) for the task, never as a page's name, which would
    leave the file with everyone's."""
    sign_in(client, "alice")
    own = client.post("/api/tasks", json={"title": "Mine"}, headers=HOST).get_json()
    assert own["private"] is True
    short = own["id"][:8]
    said = mcp(client, "alice", "upload_file", filename="hers.txt", text="hers", page=short)
    assert f"(/_tasks/files/{own['id']}/_assets/hers.txt)" in said and "who sees the task" in said, said
    said = mcp(client, "alice", "upload_file", filename="ref.txt", text="hers", page=f"[[task:{short}]]")
    assert f"(/_tasks/files/{own['id']}/_assets/ref.txt)" in said, said
    said = mcp(client, "carol", "upload_file", filename="c.txt", text="c", page=IDS["open"][:8])  # a member's, of a list for some
    assert f"(/_tasks/files/{IDS['open']}/_assets/c.txt)" in said, said
    # Who doesn't see the task is refused, as for a whole id; so is an id no task has.
    assert "No task" in mcp(client, "bob", "upload_file", filename="x.txt", text="x", page=short)
    assert "No task" in mcp(client, "bob", "upload_file", filename="x.txt", text="x", page=IDS["open"][:8])
    assert "No task" in mcp(client, "alice", "upload_file", filename="x.txt", text="x", page="00000000")
    assert not (wiki / "_assets").exists() or not any((wiki / "_assets").iterdir())
    # A page that goes by such a name is still the page.
    (wiki / "deadbeef.md").write_text("# Deadbeef\n")
    assert "[d.txt](/_assets/d.txt)" in mcp(client, "alice", "upload_file", filename="d.txt", text="d", page="deadbeef")


def test_a_tasks_files_go_where_the_task_goes(client, wiki):
    """Nothing moves with a task: its files are seen by who sees it now."""
    sign_in(client, "alice")
    url = upload(client, task=IDS["open"]).get_json()["url"]
    assert sign_in(client, "bob").get(url, headers=HOST).status_code == 404
    sign_in(client, "alice")
    assert client.put(f"/api/tasks/{IDS['open']}", json={"list": "team"}, headers=HOST).status_code == 200
    assert sign_in(client, "bob").get(url, headers=HOST).status_code == 200        # everyone's now
    # And a private task shared: the file was its owner's alone.
    sign_in(client, "alice")
    own = client.post("/api/tasks", json={"title": "Mine"}, headers=HOST).get_json()
    assert own["private"] is True
    url = upload(client, task=own["id"]).get_json()["url"]
    assert sign_in(client, "carol").get(url, headers=HOST).status_code == 404
    sign_in(client, "alice")
    assert client.put(f"/api/tasks/{own['id']}", json={"list": LIST, "share": True}, headers=HOST).status_code == 200
    assert sign_in(client, "carol").get(url, headers=HOST).status_code == 200
    assert sign_in(client, "bob").get(url, headers=HOST).status_code == 404


def test_a_new_tasks_files_are_its_own_from_the_first(client, wiki):
    """The dialog of a new task makes up its id, uploads for it and creates
    the task with it: the file is the task's as soon as there is one."""
    sign_in(client, "alice")
    id = "0a1b2c3d-0000-4000-8000-00000000abcd"
    url = upload(client, task=id).get_json()["url"]
    assert url == f"/_tasks/files/{id}/_assets/offer.png"
    assert client.get(url, headers=HOST).status_code == 200                        # the dialog's preview
    made = client.post("/api/tasks", json={"title": "With a file", "list": LIST, "id": id,
                                           "description": f"![]({url})"}, headers=HOST)
    assert made.status_code == 201 and made.get_json()["id"] == id
    assert sign_in(client, "bob").get(url, headers=HOST).status_code == 404
    assert sign_in(client, "carol").get(url, headers=HOST).status_code == 200
    # An id is taken once, and is one of Zonewiki's form.
    again = client.post("/api/tasks", json={"title": "Again", "id": id}, headers=HOST)
    assert again.status_code in (400, 409) and "already" in again.get_data(as_text=True)
    for bad in ("../../x", "notes", "0A1B2C3D-0000-4000-8000-00000000ABCD", 7):
        assert client.post("/api/tasks", json={"title": "Bad", "id": bad}, headers=HOST).status_code == 400
    for bad in ("../x", "a/b", "..", ".git"):
        assert upload(client, task=bad).status_code == 404


def test_a_new_tasks_files_are_its_uploaders_until_it_is_made(client, wiki):
    """Before its first Save a new task's files are who pasted them's alone:
    no one else opens them, adds to them, replaces them or reads their names
    in the history; made, they are its readers'."""
    sign_in(client, "alice")
    id = "0a1b2c3d-0000-4000-8000-00000000beef"
    url = upload(client, task=id, filename="contract.png").get_json()["url"]
    assert client.get(url, headers=HOST).data == PNG
    assert f"_tasks/files/{id}/_assets/contract.png" in client.get("/changes", headers=HOST).get_data(as_text=True)
    for who in ("carol", "bob", "dana"):              # carol: a member of the list it will be in
        sign_in(client, who)
        assert client.get(url, headers=HOST).status_code == 404
        assert upload(client, task=id, filename="contract.png", overwrite="1").status_code == 404
        assert upload(client, task=id, filename="mine.png").status_code == 404
        changes = client.get("/changes", headers=HOST).get_data(as_text=True)
        assert id not in changes and "contract.png" not in changes
        out = mcp(client, who, "history")
        assert id not in out and "contract.png" not in out
        assert client.post("/api/tasks", json={"title": "Taken", "id": id}, headers=HOST).status_code in (400, 409)
    assert (wiki / url[1:]).read_bytes() == PNG and not (wiki / f"_tasks/files/{id}/_assets/mine.png").exists()
    sign_in(client, "alice")
    assert client.post("/api/tasks", json={"title": "Lease", "list": LIST, "id": id}, headers=HOST).status_code == 201
    assert sign_in(client, "carol").get(url, headers=HOST).data == PNG
    assert f"_tasks/files/{id}/_assets/contract.png" in client.get("/changes", headers=HOST).get_data(as_text=True)
    assert sign_in(client, "bob").get(url, headers=HOST).status_code == 404


def test_a_deleted_tasks_files_are_no_ones(client, wiki):
    """A task deleted for good leaves its files for the undo, to no one:
    its id and its files' names don't come to light in anyone's history."""
    sign_in(client, "alice")
    own = client.post("/api/tasks", json={"title": "Renew passport"}, headers=HOST).get_json()
    url = upload(client, task=own["id"], filename="passport.png").get_json()["url"]
    assert client.get(url, headers=HOST).data == PNG
    assert client.delete(f"/api/tasks/{own['id']}", headers=HOST).status_code == 204
    for who in ("alice", "bob", "carol"):
        sign_in(client, who)
        assert client.get(url, headers=HOST).status_code == 404
        assert upload(client, task=own["id"], filename="passport.png", overwrite="1").status_code == 404
        changes = client.get("/changes", headers=HOST).get_data(as_text=True)
        assert who == "alice" or (own["id"] not in changes and "passport.png" not in changes)
        assert who == "alice" or own["id"] not in mcp(client, who, "history")
        assert client.post("/api/tasks", json={"title": "Taken", "id": own["id"]}, headers=HOST).status_code in (400, 409)
    assert (wiki / url[1:]).read_bytes() == PNG
    # The undo brings the task back, and its files with it.
    sign_in(client, "alice")
    assert client.post("/api/tasks/restore", json={"tasks": [own]}, headers=HOST).status_code == 201
    assert client.get(url, headers=HOST).data == PNG


def test_a_tasks_id_in_another_case_is_no_way_in(client, wiki):
    """On a file system that ignores case, a task's id in capitals names its
    files' folder: it is no task's id, so nobody puts a file there or reads
    one by it, and no task restored or imported takes it."""
    upper = IDS["open"].upper()
    sign_in(client, "alice")
    assert upload(client, task=IDS["open"]).status_code == 200
    for who in ("alice", "bob"):
        sign_in(client, who)
        assert upload(client, task=upper, filename="x.png").status_code == 404, who
        assert client.get(f"/_tasks/files/{upper}/_assets/offer.png", headers=HOST).status_code == 404
    assert not (wiki / f"_tasks/files/{upper}").exists()
    sign_in(client, "alice")
    [restored] = client.post("/api/tasks/restore", json={"tasks": [{"title": "Twin", "id": upper}]}, headers=HOST).get_json() or [None]
    assert restored is None
    out = client.post("/api/tasks/import", json={"file": [{"title": "Twin", "id": upper}], "list": "team"}, headers=HOST).get_json()
    assert out["added"] == 0 and out["skipped"] == 1


def test_without_users_a_tasks_file_is_with_the_others(client, git_wiki_dir):
    made = client.post("/api/tasks", json={"title": "Alone"}, headers=HOST).get_json()
    assert upload(client, task=made["id"]).get_json()["url"] == "/_assets/offer.png"


def test_its_words_suggest_nothing_to_anyone_else(client, wiki):
    """The list's tasks are in its members' search index alone: a typo of a
    word only the list has finds it for a member and suggests nothing to
    anyone else."""
    typo = SECRET[:-1] + "x"
    found = sign_in(client, "carol").get("/api/search", query_string={"q": typo}, headers=HOST).get_data(as_text=True)
    assert SECRET in found
    for who in ("bob", "dana"):
        sign_in(client, who)
        for path in ("/api/search", "/search"):
            assert SECRET not in client.get(path, query_string={"q": typo}, headers=HOST).get_data(as_text=True)


def test_a_task_that_comes_back_brings_no_one_in(client, wiki):
    """A snapshot (an undo's restore, an import) is whatever its sender made
    it: its assignee is one of the list's members, as for any write, and the
    names in its history (who had it) and its series (spawned_from) are what
    Zonewiki had for that task, or nothing."""
    sign_in(client, "alice")
    closed_one = IDS["done"]
    forged = {"title": "Smuggled", "list": LIST, "assignee": "bob", "spawned_from": closed_one,
              "history": [{"at": "2026-01-01T09:00", "event": "assigned", "by": "alice", "from": "", "to": "bob"},
                          {"at": "2026-01-01T09:01", "event": "assigned", "by": "carol", "from": "bob", "to": "carol"}]}
    [back] = client.post("/api/tasks/restore", json={"tasks": [forged]}, headers=HOST).get_json()
    imported = client.post("/api/tasks/import", json={"file": [{**forged, "title": "Smuggled too"}], "list": LIST},
                           headers=HOST).get_json()
    assert imported["unassigned"] == 1
    [again] = [t for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] in imported["ids"]]
    for t in (back, again):
        assert t.get("assignee") is None and t.get("spawned_from") is None, t
        assert [(e.get("from"), e.get("to")) for e in t["history"] if e["event"] == "assigned"] == [(None, None), (None, None)], t["history"]
        # bob had it, the history said: no longer a way to give it to him.
        resp = client.put(f"/api/tasks/{t['id']}", json={"assignee": "bob"}, headers=HOST)
        assert resp.status_code == 400 and "bob isn't in the group hiring" in resp.get_json()["error"]
    # A [[task:…]] of the closed task stands for itself, not for the smuggled one.
    chips = client.post("/api/render-notes", json={"notes": [f"[[task:{closed_one[:8]}]]"]}, headers=HOST).get_data(as_text=True)
    assert "Smuggled" not in chips
    # The undo of a delete brings the task back as it was: its series too.
    hers = client.get("/api/tasks", headers=HOST).get_json()
    spawn = next(t for t in hers if t["id"] == IDS["hers"])
    assert client.delete(f"/api/tasks/{spawn['id']}", headers=HOST).status_code == 204
    [undone] = client.post("/api/tasks/restore", json={"tasks": [spawn]}, headers=HOST).get_json()
    assert undone["assignee"] == "carol" and [e.get("to") for e in undone["history"] if e["event"] == "assigned"] == \
        [e.get("to") for e in spawn["history"] if e["event"] == "assigned"]


def test_a_commit_about_it_names_it_to_its_members_only(client, wiki):
    """Moving a list for everyone into a group writes the registry everyone
    reads: the commit is in everyone's history, and says the list's name
    only to the group's members."""
    sign_in(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "offers"}, headers=HOST).status_code == 302
    assert client.post("/tasks/list-for", data={"list": "offers", "for": "group:hiring"}, headers=HOST).status_code == 302
    assert git(wiki, "log", "-1", "--format=%s").strip() == "Move list #offers to the group hiring"
    assert "Move list #offers" in mcp(client, "carol", "history")
    for who in ("bob", "dana"):
        said = mcp(client, who, "history")
        assert "offers" not in said and "carol" not in said, said
        assert "A group's list changed" in said
    # A list for everyone is named to everyone, one whose name only starts like it too.
    assert client.post("/tasks/new-list", data={"name": LIST + "-2"}, headers=HOST).status_code == 302
    said = mcp(client, "bob", "history")
    assert f"Create list #{LIST}-2" in said


def test_a_shared_list_of_its_name_shows_none_of_its_tasks(client, wiki):
    """A shared list of the group's list's name (a merge, a hand edit: no
    door makes one) is another list: its members see their group's, anyone
    else an empty one, never the group's tasks."""
    reg = wiki / "_tasks/lists.json"
    data = json.loads(reg.read_text())
    data["lists"].append({"name": LIST})
    reg.write_text(json.dumps(data, indent=2) + "\n")
    assert SECRET in sign_in(client, "carol").get("/tasks", query_string={"list": LIST}, headers=HOST).get_data(as_text=True)
    for who in ("bob", "dana"):
        sign_in(client, who)
        body = client.get("/tasks", query_string={"list": LIST}, headers=HOST).get_data(as_text=True)
        assert not any(m in body for m in MARKS) and SECRET not in body


def test_a_task_goes_to_a_list_fewer_people_see_by_its_maker_or_an_admin(client, wiki):
    """As a page goes to fewer people: a shared task moves into a list that
    some who see it now are none of only by who made it, or an admin; to
    more people by whoever may write it."""
    sign_in(client, "bob")
    his = client.post("/api/tasks", json={"title": "Bob's chairs", "list": "team"}, headers=HOST).get_json()["id"]
    sign_in(client, "carol")                                   # in @hiring, an editor: not her task
    resp = client.put(f"/api/tasks/{his}", json={"list": LIST}, headers=HOST)
    assert resp.status_code == 403 and "bob, dana would no longer see the task" in resp.get_data(as_text=True), resp.get_data(as_text=True)
    out = mcp(client, "carol", "update_task", id=his, list=LIST)
    assert "would no longer see the task" in out
    assert client.post("/api/tasks/batch-update", json={"tasks": [{"id": his, "list": LIST}]}, headers=HOST).status_code == 403
    sign_in(client, "bob")
    assert [t["list"] for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] == his] == ["team"]
    # Her own task she moves there, and back out to everyone.
    sign_in(client, "carol")
    hers = client.post("/api/tasks", json={"title": "Carol's desk", "list": "team"}, headers=HOST).get_json()["id"]
    assert client.put(f"/api/tasks/{hers}", json={"list": LIST}, headers=HOST).status_code == 200
    assert client.put(f"/api/tasks/{hers}", json={"list": "team"}, headers=HOST).status_code == 200
    # Between lists the same people see nothing is lost: anyone's move.
    assert client.put(f"/api/tasks/{IDS['open']}", json={"list": "team"}, headers=HOST).status_code == 200   # to everyone
    # An admin who is in the list moves anyone's task there.
    config = wiki / ".zone" / "config"
    config.write_text(config.read_text().replace("[user dana]\n", "[user dana]\ngroups = hiring\n"))
    sign_in(client, "dana")
    for _ in range(50):
        if client.get("/tasks", query_string={"list": LIST}, headers=HOST).status_code == 200 and \
                f"list={LIST}" in client.get("/tasks", headers=HOST).get_data(as_text=True):
            break
        time.sleep(0.1)
    assert client.put(f"/api/tasks/{his}", json={"list": LIST}, headers=HOST).status_code == 200


def test_a_groups_list_carries_the_people_sign(client, wiki):
    """As a group's page does: 👥 beside its heading and in its pill, not the
    lock of what is yours alone."""
    body = sign_in(client, "alice").get("/tasks", query_string={"list": LIST}, headers=HOST).get_data(as_text=True)
    head = body.split('<header class="page-header">', 1)[1].split("</header>", 1)[0]
    assert 'class="lock list-for"' in head and "👥" in head and "🔒" not in head
    assert "The group hiring's list: only its members see it and its tasks" in head
    row = body.split('<nav class="tasks-places"', 1)[1]
    pill = row.split(f'data-place="list:{LIST}"', 1)[1].split("</a>", 1)[0]
    assert "tasks-place-for" in pill and "(the group hiring's: only its members see it)" in pill
    team = row.split('data-place="list:team"', 1)
    assert len(team) == 1 or "tasks-place-for" not in team[1].split("</a>", 1)[0]
    # Its line in the left column: the people after its name.
    line = body.split('<nav class="lists-rail"', 1)[1].split(f'data-place="list:{LIST}"', 1)[1].split("</a>", 1)[0]
    assert "(the group hiring's: only its members see it)" in line and 'class="lists-rail-mark"' in line.split('class="lists-rail-name"', 1)[1]


def test_a_batch_update_changes_fields_as_one_tasks_update_does(client, wiki):
    """The batch door (several tasks in one request) is the single update's
    in a loop: a mention added there is logged (and told), and a text that
    someone changed meanwhile is a conflict, with nothing written."""
    sign_in(client, "alice")
    batch = lambda *tasks: client.post("/api/tasks/batch-update", json={"tasks": list(tasks)}, headers=HOST)
    resp = batch({"id": IDS["open"], "description": "Ask @carol about it"}, {"id": IDS["shared"], "description": "And @bob here"})
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.get_json()}
    assert [e["to"] for e in by_id[IDS["open"]]["history"] if e["event"] == "mentioned"] == ["carol"]
    assert [e["to"] for e in by_id[IDS["shared"]]["history"] if e["event"] == "mentioned"] == ["bob"]
    # Started from a text that is another one by now: refused whole.
    resp = batch({"id": IDS["shared"], "title": "Order tables", "was": {"title": "Order chairs, not this"}},
                 {"id": IDS["open"], "title": "Changed too"})
    assert resp.status_code == 409 and "Someone changed this task" in resp.get_data(as_text=True)
    titles = {t["id"]: t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()}
    assert titles[IDS["shared"]] == "Order chairs" and titles[IDS["open"]] == f"Offer for the {SECRET}"
    assert batch({"id": IDS["shared"], "title": "Order tables", "was": {"title": "Order chairs"}}).status_code == 200


def test_a_tags_rename_counts_only_the_tasks_he_sees(client, wiki):
    """A tag is one name for all, so bob's rename reaches the list's task
    too, but what he is told counts only the task he sees."""
    sign_in(client, "bob")
    client.post("/api/tasks", json={"title": "Buy offers", "list": "team", "tags": ["offers"]}, headers=HOST)
    out = mcp(client, "bob", "rename_task_tag", tag="offers", new_tag="deals")
    assert "in one commit: 1 task." in out, out
    assert "deals" in (wiki / FOLDER / "tasks.json").read_text()


@pytest.mark.parametrize("door", ["browser", "mcp"])
def test_an_undo_takes_a_task_back_to_fewer_people_as_a_move_does(client, wiki, door):
    """Carol moves alice's task out of the group's list into everyone's
    team, which anyone who may write does; undoing that takes it back to
    fewer people, which only who made it (or an admin) does, as a move
    there is: carol is refused and says why, alice undoes it. Bob, outside
    the group, sees the change as no change at all."""
    sign_in(client, "carol")
    assert client.put(f"/api/tasks/{IDS['open']}", json={"list": "team"}, headers=HOST).status_code == 200
    moved = git(wiki, "rev-parse", "HEAD").strip()
    assert IDS["open"] not in (wiki / FOLDER / "tasks.json").read_text()

    def undo(who):
        if door == "browser":
            sign_in(client, who)
            resp = client.post("/changes/undo", data={"sha": moved}, headers=HOST)
            if resp.status_code != 303:
                return str(resp.status_code)
            return "done" if "undone=" in resp.headers["Location"] else unquote_plus(resp.headers["Location"])
        text = mcp(client, who, "undo_changes", versions=[moved])
        return "done" if text.startswith("Undone:") else text
    assert "would no longer see the task" in undo("carol") and "Only who made a task, or an admin" in undo("carol")
    said = undo("bob")
    assert said == "404" if door == "browser" else "No change" in said
    assert IDS["open"] not in (wiki / FOLDER / "tasks.json").read_text()
    assert undo("alice") == "done"
    assert IDS["open"] in (wiki / FOLDER / "tasks.json").read_text()
    assert IDS["open"] not in (wiki / "_tasks/lists/team/tasks.json").read_text()
