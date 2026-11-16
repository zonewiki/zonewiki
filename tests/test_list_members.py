"""Lists for some (docs/spec/tasks.md): a shared list can say whom it is
for, groups and users. Its members see it, change its tasks and are who its
tasks are given to; to anyone else it is no list, as another user's private
list: no pill, no task in any view, count, search, history or assistant's
answer. An admin is none by being admin.

alice made the list hiring for the group @hiring and herself; carol is in
the group; bob is an editor and dana an admin, neither of them a member."""

import json
import re
import subprocess

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {name: (f"{name}-pw", f"{name}-token") for name in ("alice", "bob", "carol", "dana")}
SECRET = "zebrafinch"          # a word only the list's tasks have
MARKS = ("Offer for the", "Interview the", "Call the", "About the")   # what its tasks and comment say
LIST = "hiring"


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
    """The list hiring for @hiring and alice, with an open task (commented),
    a closed one and one assigned to carol; the list team for everyone."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"dana": {"role": "admin"}, "carol": {"groups": "hiring"}})
    sign_in(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    resp = client.post("/tasks/new-list", data={"name": LIST, "for": "@hiring, alice"}, headers=HOST)
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


def test_its_entry_is_in_its_folder_not_in_the_registry(wiki):
    assert LIST not in (wiki / "_tasks/lists.json").read_text()
    entry = json.loads((wiki / f"_tasks/lists/{LIST}/list.json").read_text())
    assert entry == {"name": LIST, "for": ["@hiring", "alice"], "owners": ["alice"]}


@pytest.mark.parametrize("member", ["alice", "carol"])
def test_its_members_see_it(client, wiki, member):
    sign_in(client, member)
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert f"<h1>List: {LIST}" in page and "For @hiring, alice: only they see this list" in page and "(alice, carol)" in page
    assert f'class="tasks-place" data-place="list:{LIST}"' in client.get("/tasks", headers=HOST).get_data(as_text=True)   # a pill
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
    words = lambda html: re.sub(r'<datalist id="list-for-words".*?</datalist>', "", html)   # the groups there are: one is called hiring
    assert words(page).replace(LIST, "nosuch") == words(none) and 'id="list-rename-btn"' not in page
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
    ("POST", "/api/tasks/batch-update", {"json": {"tasks": [{"id": "{shared}", "list": LIST}]}}),
    ("POST", "/tasks/rename-list", {"data": {"list": LIST, "new_name": "taken"}}),
    ("POST", "/tasks/delete-list", {"data": {"list": LIST, "to": ""}}),
    ("POST", "/tasks/list-for", {"data": {"list": LIST, "for": ""}}),
    ("POST", "/tasks/pin-list", {"data": {"list": LIST, "pin": "1"}}),
    ("POST", "/api/tasks/settings", {"json": {"list": LIST, "set": {"limit": "3"}}}),
    ("POST", "/api/tasks/import", {"json": {"zone_tasks": 1, "list": LIST, "tasks": [{"title": "Imported"}]}}),
]


def _its_files(wiki):
    folder = wiki / f"_tasks/lists/{LIST}"
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
    log = git(wiki, "log", "--format=%H %s", "--", f"_tasks/lists/{LIST}").splitlines()
    assert log, "the list's files are in git"
    for line in log:
        sha = line.split()[0]
        for path in (f"/diff/_tasks/lists/{LIST}/tasks.json/{sha}", f"/revision/_tasks/lists/{LIST}/tasks.json/{sha}"):
            resp = client.get(path, headers=HOST)
            assert SECRET not in resp.get_data(as_text=True)


def test_a_task_there_is_given_only_to_a_member(client, wiki):
    sign_in(client, "alice")
    resp = client.put(f"/api/tasks/{IDS['open']}", json={"assignee": "bob"}, headers=HOST)
    assert resp.status_code == 400 and "isn't one of those the list #hiring is for" in resp.get_json()["error"]
    assert client.put(f"/api/tasks/{IDS['open']}", json={"assignee": "carol"}, headers=HOST).status_code == 200
    # A mention of someone who is none of them is text: no news for them.
    client.post(f"/api/tasks/{IDS['open']}/comments", json={"text": "@bob and @carol should know"}, headers=HOST)
    task = next(t for t in client.get("/api/tasks", headers=HOST).get_json() if t["id"] == IDS["open"])
    assert [e["to"] for e in task["history"] if e["event"] == "mentioned"] == ["carol"]
    # The page's lists say who its tasks can be given to.
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert "&#34;members&#34;:[&#34;alice&#34;,&#34;carol&#34;]" in page


def test_who_it_is_for_is_its_owners_to_say(client, wiki):
    # carol is a member, not an owner.
    sign_in(client, "carol")
    assert 'id="list-for-btn"' not in client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert client.post("/tasks/list-for", data={"list": LIST, "for": ""}, headers=HOST).status_code == 403
    sign_in(client, "alice")
    assert 'id="list-for-btn"' in client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    # She has to stay one of them, and what she names has to be there.
    for words, why in (("@hiring", "You wouldn&#39;t see the list yourself"), ("alice, zed", "There is no user"),
                       ("alice, @Bad_Group", "is no group&#39;s name")):
        resp = client.post("/tasks/list-for", data={"list": LIST, "for": words}, headers=HOST)
        assert resp.status_code == 302 and "for_error=" in resp.headers["Location"]
        assert why in client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True), words
    # Widened: for everyone, its entry in the registry, its file gone, one commit.
    assert client.post("/tasks/list-for", data={"list": LIST, "for": "everyone"}, headers=HOST).headers["Location"] == f"/tasks?list={LIST}"
    assert not (wiki / f"_tasks/lists/{LIST}/list.json").exists()
    assert {"name": LIST, "owners": ["alice"]} in json.loads((wiki / "_tasks/lists.json").read_text())["lists"]
    assert git(wiki, "log", "-1", "--format=%s").strip() == f"List #{LIST} is for everyone"
    sign_in(client, "bob")
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    # Narrowed again: gone for bob at once, its tasks as they were.
    sign_in(client, "alice")
    assert client.post("/tasks/list-for", data={"list": LIST, "for": "alice carol"}, headers=HOST).status_code == 302
    assert json.loads((wiki / f"_tasks/lists/{LIST}/list.json").read_text())["for"] == ["alice", "carol"]
    assert LIST not in (wiki / "_tasks/lists.json").read_text()
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


def test_renamed_and_deleted_its_folders_stay_its_members(client, wiki):
    sign_in(client, "alice")
    assert client.post("/tasks/rename-list", data={"list": LIST, "new_name": "recruiting"}, headers=HOST).headers["Location"] == "/tasks?list=recruiting"
    assert json.loads((wiki / "_tasks/lists/recruiting/list.json").read_text())["for"] == ["@hiring", "alice"]
    assert json.loads((wiki / f"_tasks/lists/{LIST}/list.json").read_text()) == {"name": LIST, "for": ["@hiring", "alice"], "gone": True}
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    sign_in(client, "bob")
    assert SECRET not in client.get("/api/tasks", headers=HOST).get_data(as_text=True)
    for line in git(wiki, "log", "--format=%H", "--", f"_tasks/lists/{LIST}").splitlines():
        resp = client.get(f"/diff/_tasks/lists/{LIST}/tasks.json/{line}", headers=HOST)
        assert SECRET not in resp.get_data(as_text=True)
    # Deleted, its tasks to the list for everyone (which she says by moving them there).
    sign_in(client, "alice")
    assert client.post("/tasks/delete-list", data={"list": "recruiting", "to": "team"}, headers=HOST).headers["Location"] == "/tasks?list=team"
    assert json.loads((wiki / "_tasks/lists/recruiting/list.json").read_text())["gone"] is True
    sign_in(client, "bob")
    assert SECRET in client.get("/api/tasks", headers=HOST).get_data(as_text=True)      # in team now: everyone's


def test_a_list_for_some_needs_users_and_is_no_private_list(client, git_wiki_dir, monkeypatch):
    resp = client.post("/tasks/new-list", data={"name": "x", "for": "anna"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    sign_in(client, "alice")
    resp = client.post("/tasks/new-list", data={"name": "x", "private": "1", "for": "alice"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]
    # An assistant makes one with for.
    out = mcp(client, "alice", "create_task_list", name="deals", **{"for": "alice, bob"}, summary="x")
    assert "for alice, bob: only they see it" in out
    assert json.loads((git_wiki_dir / "_tasks/lists/deals/list.json").read_text())["for"] == ["alice", "bob"]
    # Its name is taken for everyone, though not everyone sees it: two lists can't have one folder.
    sign_in(client, "carol")
    resp = client.post("/tasks/new-list", data={"name": "deals"}, headers=HOST)
    assert "newlist_error=" in resp.headers["Location"]


def places(client):
    """The switcher as the Tasks page has it: (the pills' keys, More's heads and lists in order)."""
    html = client.get("/tasks", headers=HOST).get_data(as_text=True)
    pills = re.findall(r'class="tasks-place" data-place="([^"]+)"', html)
    more = re.findall(r'class="lists-more-head">([^<]+)<|class="lists-more-item" data-list="([^"]+)"', html)
    return pills, [head or name for head, name in more]


def test_a_list_you_are_one_of_is_a_pill_until_you_unpin_it(client, wiki):
    # carol pinned nothing: the list made for her group is in her row all the
    # same (under More nobody would find it), until she takes it out.
    sign_in(client, "carol")
    pills, more = places(client)
    assert pills == ["mine", "inbox", f"list:{LIST}"] and more == ["team"]
    page = client.get(f"/tasks?list={LIST}", headers=HOST).get_data(as_text=True)
    assert 'id="list-unpin-btn"' in page and 'id="list-pin-btn"' not in page
    assert client.post("/tasks/pin-list", data={"list": LIST, "pin": "0"}, headers=HOST).status_code == 302
    own = json.loads((wiki / "private/carol/_tasks/lists.json").read_text())
    assert own["unpinned"] == [LIST] and own["pinned"] == []
    pills, more = places(client)
    assert pills == ["mine", "inbox"] and more == ["Your lists", LIST, "Other lists", "team"]
    # Pinned again, it is a pin like any other.
    assert client.post("/tasks/pin-list", data={"list": LIST, "pin": "1"}, headers=HOST).status_code == 302
    own = json.loads((wiki / "private/carol/_tasks/lists.json").read_text())
    assert "unpinned" not in own and own["pinned"] == [LIST]
    assert places(client)[0] == ["mine", "inbox", f"list:{LIST}"]
    # Renamed, what she unpinned stays unpinned under its new name.
    client.post("/tasks/pin-list", data={"list": LIST, "pin": "0"}, headers=HOST)
    sign_in(client, "alice")
    assert client.post("/tasks/rename-list", data={"list": LIST, "new_name": "recruiting"}, headers=HOST).status_code == 302
    sign_in(client, "carol")
    assert json.loads((wiki / "private/carol/_tasks/lists.json").read_text())["unpinned"] == ["recruiting"]
    assert places(client)[0] == ["mine", "inbox"]


def test_more_has_your_lists_before_the_others(client, wiki):
    sign_in(client, "alice")
    for name in ("archive", "misc"):
        assert client.post("/tasks/new-list", data={"name": name, "pin": "0"}, headers=HOST).status_code == 302
    client.post("/api/tasks", json={"title": "Bob's", "list": "misc", "assignee": "bob"}, headers=HOST)
    assert client.post("/tasks/new-list", data={"name": "bobs", "for": "alice, bob", "pin": "0"}, headers=HOST).status_code == 302
    sign_in(client, "bob")
    client.post("/tasks/new-list", data={"name": "own", "private": "1", "pin": "0"}, headers=HOST)
    client.post("/tasks/pin-list", data={"list": "bobs", "pin": "0"}, headers=HOST)
    # His: the private one, the one for him, the one with a task of his; the rest are other lists.
    pills, more = places(client)
    assert pills == ["mine", "inbox"]
    assert more == ["Your lists", "bobs", "misc", "own", "Other lists", "archive", "team"]
    # Without the others there is no word over his.
    sign_in(client, "alice")
    for name in ("archive", "team"):
        client.post("/tasks/list-for", data={"list": name, "for": "alice", "confirm": "1"}, headers=HOST)
    sign_in(client, "bob")
    assert places(client)[1] == ["bobs", "misc", "own"]


def test_pin_for_everyone_sets_the_wikis_pins(client, wiki):
    # What a user who has pinned nothing has: an owner's to set, in the list's menu.
    sign_in(client, "bob")
    assert places(client)[0] == ["mine", "inbox"]
    assert 'id="list-pin-all-btn"' not in client.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
    before = (wiki / "_tasks/lists.json").read_text()
    assert client.post("/tasks/pin-list", data={"list": "team", "pin": "1", "everyone": "1"}, headers=HOST).status_code == 403
    assert (wiki / "_tasks/lists.json").read_text() == before
    sign_in(client, "alice")
    assert 'id="list-pin-all-btn"' in client.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
    assert client.post("/tasks/pin-list", data={"list": "team", "pin": "1", "everyone": "1"}, headers=HOST).status_code == 302
    assert json.loads((wiki / "_tasks/lists.json").read_text())["pinned"] == ["team"]
    assert git(wiki, "log", "-1", "--format=%s").strip() == "Pin list #team for everyone"
    assert 'id="list-unpin-all-btn"' in client.get("/tasks?list=team", headers=HOST).get_data(as_text=True)
    sign_in(client, "bob")
    assert places(client)[0] == ["mine", "inbox", "list:team"]
    # And taken back.
    sign_in(client, "alice")
    assert client.post("/tasks/pin-list", data={"list": "team", "pin": "0", "everyone": "1"}, headers=HOST).status_code == 302
    assert "pinned" not in json.loads((wiki / "_tasks/lists.json").read_text()) or json.loads((wiki / "_tasks/lists.json").read_text())["pinned"] == []
    sign_in(client, "bob")
    assert places(client)[0] == ["mine", "inbox"]


def test_narrowing_says_whose_tasks_leave_their_sight(client, wiki):
    sign_in(client, "alice")
    for title, who in (("One", "bob"), ("Two", "bob"), ("Three", "dana"), ("Hers", "alice")):
        assert client.post("/api/tasks", json={"title": title, "list": "team", "assignee": who}, headers=HOST).status_code == 201
    resp = client.post("/tasks/list-for", data={"list": "team", "for": "alice, carol"}, headers=HOST)
    assert resp.status_code == 302 and "for_confirm=1" in resp.headers["Location"]
    assert not (wiki / "_tasks/lists/team/list.json").exists()                 # asked, not done
    page = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
    assert "3 open tasks here are assigned to bob, dana, who will no longer see the list." in page
    assert '<input type="hidden" name="confirm" value="1">' in page and 'value="alice, carol"' in page
    # Save again: narrowed, and nobody unassigned.
    resp = client.post("/tasks/list-for", data={"list": "team", "for": "alice, carol", "confirm": "1"}, headers=HOST)
    assert resp.headers["Location"] == "/tasks?list=team"
    assert json.loads((wiki / "_tasks/lists/team/list.json").read_text())["for"] == ["alice", "carol"]
    who = {t["title"]: t.get("assignee") for t in client.get("/api/tasks", query_string={"list": "team"}, headers=HOST).get_json()}
    assert who["One"] == "bob" and who["Three"] == "dana"
    sign_in(client, "bob")
    assert client.get("/api/tasks", query_string={"mine": "1"}, headers=HOST).get_json() == []
    # Nothing to say when nobody loses a task: no question.
    sign_in(client, "alice")
    resp = client.post("/tasks/list-for", data={"list": "team", "for": "alice, bob, carol, dana"}, headers=HOST)
    assert resp.headers["Location"] == "/tasks?list=team"


def test_anyone_elses_word_stays_when_the_list_is_written(client, wiki):
    # The word a browser asks for every minute, to load its lists again when
    # it is another one: a write to a list for some is none of bob's.
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
