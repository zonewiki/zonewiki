"""Private pages and tasks don't leak (docs/spec/users.md): with alice's
private page, upload, draft and tasks in the wiki, bob, signed in or
through his MCP token, must not see a word of them anywhere, nor change
them.

Every route Zonewiki registers is either probed here or listed as exempt
(test_every_route_is_covered), and every MCP tool
(test_every_tool_is_covered), so a new one gets checked too."""

import datetime
import json
import re
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

import pytest

from tests import settings
from tests.conftest import HOST, content_hash, git_setup, notes_zip

REPO = Path(__file__).resolve().parent.parent

# In alice's private pages only: no response to bob may contain it. The
# requests search for QUERY, which a response may echo.
SECRET = "quokkaflute"
QUERY = "zebracorn"
DIARY = "private/alice/diary"
SNEAK = "private/bob/../alice/diary"   # the diary, by way of bob's own folder
NOTE = "private/alice/2026-10-27_09-00-00_idea"   # her note

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token")}


# alice's private tasks: open (linking to the shared page plans, which
# refers to it), completed this year, archived.
TASK_ID = "a11ce000-0000-4000-8000-000000000001"
DONE_ID = "a11ce000-0000-4000-8000-000000000002"
OLD_ID = "a11ce000-0000-4000-8000-000000000003"
INBOX_ID = "a11ce000-0000-4000-8000-000000000004"      # in no list: in her Inbox
HER_TASKS = (TASK_ID, DONE_ID, OLD_ID, INBOX_ID)
COMMENT_ID = "c0ffee01"


def private_task(id, title, closed_at=None):
    return {"id": id, "title": f"{title} {SECRET} {QUERY}", "description": f"{QUERY} {SECRET} for [[plans]]\n\n- [ ] Step",
            "tags": [f"{QUERY}-{SECRET}", "secretlist"], "list": "secretlist", "start_date": None, "due_date": "2026-01-01",
            "created_at": "2026-01-01T10:00:00", "closed_at": closed_at, "wont_do": False, "keep": False,
            "last_done": None, "sort_order": None, "recurrence": {"type": "weekly", "interval": 1, "from": "due"},
            "spawned_from": None, "history": [],
            "comments": [{"id": COMMENT_ID, "at": "2026-01-02T10:00", "by": "alice", "text": SECRET}]}


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout.strip()


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    """alice's private pages, committed (the diary, a note; an old
    page, deleted) and not (a draft); a shared page linking to the diary,
    which links back."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    w = git_wiki_dir
    (w / "private" / "alice").mkdir(parents=True)
    (w / "private/alice/_assets").mkdir(parents=True)
    (w / f"{DIARY}.md").write_text(
        f"---\ntags: [{QUERY}-{SECRET}]\n---\n# Diary {SECRET}\n\n{QUERY} {SECRET} with [[plans]].\n\n- [ ] Call\n")
    (w / "private/alice/old.md").write_text(f"# Old {SECRET}\n\n{QUERY} {SECRET}\n")
    (w / f"{NOTE}.md").write_text(f"# Idea {SECRET}\n\n{QUERY} {SECRET}\n")
    (w / "private/alice/_assets/photo.png").write_bytes(SECRET.encode())
    # Names git quotes where it lists files without -z (a letter outside
    # ASCII, a space): read as a path, such a quoted name named no private
    # folder, and Changes showed it to everyone.
    (w / f"private/alice/_assets/{SECRET} März.pdf").write_bytes(b"%PDF-1.4\n")
    (w / f"private/alice/{SECRET}-geständnis.md").write_text(f"# Geständnis {SECRET}\n")
    # A file of one of her tasks: kept by the task, seen by who sees it.
    (w / f"_tasks/files/{TASK_ID}/_assets").mkdir(parents=True)
    (w / f"_tasks/files/{TASK_ID}/_assets/photo.png").write_bytes(SECRET.encode())
    (w / "plans.md").write_text(f"# Plans\n\nSee [[{DIARY}]] and [[{DIARY}|my diary]].\n\n- [[task:{TASK_ID[:8]}]]\n")
    # Her tasks: a private list of hers, pinned, a page that shows it and
    # one of her tags, her tasks in that list, and one in her Inbox (in no
    # list). A shared list, team, for the tasks everyone sees. The registries
    # are this Zonewiki's (version 2): a start changes and commits nothing.
    (w / "private/alice/deck.md").write_text(
        f"# My tasks {SECRET}\n\n[[tasks: list=secretlist]]\n\n[[tasks: tag=secretlist]]\n")
    tasks = w / "private/alice/_tasks"
    tasks.mkdir(parents=True)
    (tasks / "lists.json").write_text(
        '{\n  "lists": [\n    {"name": "secretlist"}\n  ],\n'
        '  "pinned": ["secretlist"]\n}\n')
    (w / "_tasks").mkdir(exist_ok=True)
    (w / "_tasks/lists.json").write_text('{\n  "lists": [\n    {"name": "team"}\n  ]\n}\n')
    # Her closed tasks as Zonewiki files them: this month's, and an earlier month's.
    this_month = datetime.date.today().strftime("%Y-%m")
    inbox = {k: v for k, v in private_task(INBOX_ID, "Inbox").items() if k != "list"}
    # Each list's tasks in its folder, those in no list (her Inbox) beside the registry.
    (tasks / "tasks.json").write_text(json.dumps([inbox], indent=2) + "\n")
    (tasks / "closed.json").write_text("[]\n")
    hers = tasks / "lists/secretlist"
    hers.mkdir(parents=True)
    (hers / "tasks.json").write_text(json.dumps([private_task(TASK_ID, "Task")], indent=2) + "\n")
    (hers / "closed.json").write_text(
        json.dumps([private_task(DONE_ID, "Done", f"{this_month}-01T10:00:00")], indent=2) + "\n")
    (hers / "closed-2001-01.json").write_text(
        json.dumps([private_task(OLD_ID, "Old", "2001-01-05T10:00:00")], indent=2) + "\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "Alice's pages")
    git(w, "rm", "-q", "private/alice/old.md", f"private/alice/{SECRET}-geständnis.md")
    git(w, "commit", "-q", "-m", "Delete private/alice/old")
    (w / "private/alice/draft.md").write_text(f"# Draft {SECRET}\n\n{QUERY} {SECRET}\n")
    # Changes she left on the shared page in conflict (the editor's save on leaving).
    (w / ".zone/drafts/alice").mkdir(parents=True)
    (w / ".zone/drafts/alice/plans.json").write_text(json.dumps(
        {"content": f"# Plans {SECRET}\n", "base": "# Plans\n", "at": datetime.datetime.now().isoformat() + "Z"}))
    return w


@pytest.fixture()
def bob(client, wiki):
    client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
    return client


def shared_tasks(wiki):
    """The shared open tasks' files as text ("" while there is none): the
    Inbox's and each list's."""
    return open_tasks(wiki / "_tasks")


def open_tasks(folder):
    """The open tasks' files of a _tasks folder as text: those of no list
    and each list's (lists/<name>/tasks.json)."""
    return "".join(p.read_text() for p in sorted(folder.rglob("tasks.json")))


def her_tasks(wiki):
    """alice's open tasks' files as text."""
    return open_tasks(wiki / "private/alice/_tasks")


def sha(wiki):
    return git(wiki, "log", "-n1", "--format=%H", "--", f"{DIARY}.md")


def fill(path, wiki):
    """A probe's path with the diary's commit ({sha}) and the commit that
    still had the deleted page ({old})."""
    return path.replace("{sha}", sha(wiki)).replace("{old}", git(wiki, "rev-parse", "HEAD~1"))


# ── Reads ────────────────────────────────────────────────────────────────────

# (route pattern, method, path, request kwargs[, marks]); "{sha}" in a path
# is the diary's commit.
READS = [
    ("GET /page/{slug...}", "GET", f"/page/{DIARY}", {}),
    ("GET /page/{slug...}", "GET", "/page/plans", {}),       # links to the diary
    ("GET /page/{slug...}", "GET", "/page/index", {}),
    ("GET /edit/{slug...}", "GET", f"/edit/{DIARY}", {}),
    ("GET /edit/{slug...}", "GET", "/edit/plans", {}),
    ("GET /history/{slug...}", "GET", f"/history/{DIARY}", {}),
    ("GET /revision/{rest...}", "GET", f"/revision/{DIARY}/{{sha}}", {}),
    ("GET /diff/{rest...}", "GET", f"/diff/{DIARY}/{{sha}}", {}),
    ("GET /diff-uncommitted/{slug...}", "GET", "/diff-uncommitted/private/alice/draft", {}),
    ("GET /api/page-preview/{slug...}", "GET", f"/api/page-preview/{DIARY}", {}),
    ("GET /api/search", "GET", "/api/search", {"query_string": {"q": QUERY}}),
    ("GET /search", "GET", "/search", {"query_string": {"q": QUERY}}),
    ("GET /api/pages", "GET", "/api/pages", {}),
    ("GET /api/pages-list", "GET", "/api/pages-list", {}),
    ("GET /pages", "GET", "/pages", {}),
    ("GET /notes", "GET", "/notes", {}),
    ("GET /page/_deleted", "GET", "/page/_deleted", {}),
    ("GET /changes", "GET", "/changes", {}),
    ("GET /changes", "GET", "/changes", {"query_string": {"under": "private/alice"}}),
    ("GET /changes", "GET", "/changes", {"query_string": {"user": "alice", "n": "500"}}),
    ("GET /changes", "GET", "/changes", {"query_string": {"watched": "1"}}),
    ("GET /page/{slug...}", "GET", "/page/private/alice/old", {}),   # a deleted page offers restore
    ("GET /uncommitted", "GET", "/uncommitted", {}),
    ("GET /api/uncommitted", "GET", "/api/uncommitted", {}),
    ("GET /new", "GET", "/new", {}),
    ("GET /import", "GET", "/import", {}),
    ("GET /import", "GET", "/import", {"query_string": {"for": "private"}}),
    ("GET /_assets/{path...}", "GET", "/_assets/private/alice/photo.png", {}),
    ("GET /private/{user}/_assets/{file}", "GET", "/private/alice/_assets/photo.png", {}),
    ("GET /groups/{group}/_assets/{file}", "GET", "/groups/alice/_assets/photo.png", {}),   # no group's folder (test_group_pages.py)
    ("GET /_tasks/files/{task}/_assets/{file}", "GET", f"/_tasks/files/{TASK_ID}/_assets/photo.png", {}),
    ("GET /groups", "GET", "/groups", {}),                       # the groups bob is in: none
    ("GET /groups/{group}", "GET", "/groups/alice", {}),         # a group's home: no group of his (test_group_pages.py)
    ("GET /tasks", "GET", "/tasks", {}),
    ("GET /api/tasks", "GET", "/api/tasks", {}),
    ("GET /api/tasks/closed", "GET", "/api/tasks/closed", {}),
    ("GET /api/tasks/closed", "GET", "/api/tasks/closed", {"query_string": {"year": "2001"}}),
    ("GET /api/tasks/closed-count", "GET", "/api/tasks/closed-count", {}),
    ("GET /api/tasks/closed-years", "GET", "/api/tasks/closed-years", {}),
    ("GET /tasks", "GET", "/tasks", {"query_string": {"list": "secretlist"}}),
    ("GET /tasks", "GET", "/tasks", {"query_string": {"mine": "1"}}),
    ("GET /tasks", "GET", "/tasks", {"query_string": {"list": "inbox"}}),          # his Inbox, not hers
    ("GET /tasks", "GET", "/tasks", {"query_string": {"list": "none"}}),
    ("GET /tasks", "GET", "/tasks", {"query_string": {"list": "team"}}),
    ("GET /api/tasks/closed-count", "GET", "/api/tasks/closed-count", {"query_string": {"list": "inbox"}}),
    ("GET /api/tasks/due-count", "GET", "/api/tasks/due-count", {"query_string": {"places": "1"}}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {"query_string": {"list": "inbox"}}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {"query_string": {"list": "none"}}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {"query_string": {"assignee": "none"}}),
    ("GET /api/tasks/closed-count", "GET", "/api/tasks/closed-count", {"query_string": {"mine": "1"}}),
    ("GET /api/tasks/closed-years", "GET", "/api/tasks/closed-years", {"query_string": {"mine": "1"}}),
    ("GET /api/tasks/due-count", "GET", "/api/tasks/due-count", {}),
    ("GET /api/tasks/headless", "GET", "/api/tasks/headless", {}),
    ("GET /api/tasks/assignable", "GET", "/api/tasks/assignable", {}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {"query_string": {"list": "secretlist"}}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {"query_string": {"mine": "1"}}),
    ("POST /api/preview", "POST", "/api/preview", {"data": f"[[{DIARY}]] [[private/alice/old]] [[private/alice/draft]]"}),
    ("POST /api/render-notes", "POST", "/api/render-notes", {"json": {"notes": [f"[[{DIARY}]]"], "comments": []}}),
    ("POST /api/render-notes", "POST", "/api/render-notes",
     {"json": {"notes": [f"[[task:{TASK_ID[:8]}]]"], "comments": [f"[[task:{DONE_ID}]]"]}}),
    ("POST /api/preview", "POST", "/api/preview", {"data": f"[[task:{TASK_ID[:8]}]] [[task:{OLD_ID}]]"}),
]


def _read_params():
    out = []
    for entry in READS:
        route, method, path, kwargs, *marks = entry
        out.append(pytest.param(method, path, kwargs, id=f"{method} {path}", marks=marks))
    return out


@pytest.mark.parametrize("method,path,kwargs", _read_params())
def test_bob_reads_nothing_of_alices(bob, wiki, method, path, kwargs):
    resp = bob.open(fill(path, wiki), method=method, headers=HOST, **kwargs)
    assert resp.status_code != 302 or "/login" not in resp.headers["Location"], "not signed in"
    body = resp.data.decode(errors="replace")
    assert SECRET not in body
    assert not re.search(r'href="[^"]*private/alice', body), "a link to alice's page"
    assert not any(id in body and id not in str(kwargs) for id in HER_TASKS), "alice's task"
    assert "secretlist" not in body or "secretlist" in str(kwargs), "alice's list"
    assert "2001" not in body or "2001" in str(kwargs), "the year of alice's archive"
    if path.startswith(f"/page/{DIARY}") or "/private/alice/" in path:
        assert resp.status_code == 404



def test_a_link_in_the_wiki_is_no_page(bob, client, wiki):
    """A symlink made on the host is no page (safePath, iterPages): a shared
    name that links to alice's diary, a folder that links to her folder and
    a link to Zonewiki's settings file lead to nothing by any door, search
    reads none of them, and nothing is written through them."""
    (wiki / "leak.md").symlink_to(wiki / f"{DIARY}.md")
    (wiki / "peek").symlink_to(wiki / "private/alice")
    (wiki / "settings.md").symlink_to(wiki / ".zone/config")
    for path in ("/page/leak", "/page/peek/diary", "/page/settings", "/edit/leak", "/edit/peek/diary",
                 "/history/leak", "/api/page-preview/leak", "/api/page-preview/peek/diary"):
        resp = bob.get(path, headers=HOST)
        body = resp.get_data(as_text=True)
        assert resp.status_code in (400, 404) and SECRET not in body and "[user " not in body, (path, resp.status_code)
    for path, kwargs in (("/api/pages", {}), ("/api/pages-list", {}), ("/api/search", {"query_string": {"q": QUERY}}),
                         ("/search", {"query_string": {"q": QUERY}})):
        body = bob.get(path, headers=HOST, **kwargs).get_data(as_text=True)
        assert SECRET not in body and "leak" not in body and "peek" not in body, path
    # The links are files git doesn't have yet, listed by their own names, not by what they lead to.
    assert SECRET not in bob.get("/uncommitted", headers=HOST).get_data(as_text=True)
    for tool, args in (("read_page", {"slug": "leak"}), ("read_page", {"slug": "peek/diary"}),
                       ("search_wiki", {"query": QUERY}), ("list_pages", {})):
        text = mcp(client, tool, **args)
        assert SECRET not in text and "peek/diary" not in text.replace(args.get("slug", "\0"), ""), (tool, text)
    diary = (wiki / f"{DIARY}.md").read_text()
    for slug in ("leak", "peek/diary", "peek/new"):
        assert bob.post(f"/api/save/{slug}", data={"content": "# Planted\n"}, headers=HOST).status_code == 400, slug
    assert (wiki / f"{DIARY}.md").read_text() == diary and not (wiki / "private/alice/new.md").exists()


def test_her_archives_by_month_are_hers(client, wiki, tmp_path_factory):
    """A month's file of hers outside its list's folder (put there by hand),
    and a shared one: a start files both by list in one commit of Zonewiki's.
    Bob sees the shared year and nothing of hers: not her tasks, not her
    year, not her files' names in the history; she sees both."""
    write = lambda rel, tasks: (wiki / rel).write_text(json.dumps(tasks, indent=2) + "\n")  # noqa: E731
    hers = "a11ce000-0000-4000-8000-000000001999"
    write("private/alice/_tasks/closed-1999-03.json", [private_task(hers, "Long ago", "1999-03-05T10:00:00")])
    write("_tasks/closed-1998-07.json", [{"id": "5ba4ed00-0000-4000-8000-000000001998", "title": "Shared long ago", "list": "team",
                                       "closed_at": "1998-07-05T10:00:00"}])
    git(wiki, "add", "-A")
    git(wiki, "commit", "-q", "-m", "Archives outside their lists' folders")
    client.live.stop()     # the fixture's server, which started before these files were there
    git_setup(tmp_path_factory)
    assert not (wiki / "private/alice/_tasks/closed-1999-03.json").exists() and not (wiki / "_tasks/closed-1998-07.json").exists()
    assert hers in (wiki / "private/alice/_tasks/lists/secretlist/closed-1999-03.json").read_text()
    assert "Shared long ago" in (wiki / "_tasks/lists/team/closed-1998-07.json").read_text()
    assert hers not in "".join(p.read_text() for p in (wiki / "_tasks").rglob("*.json")), "her task in the shared files"
    assert git(wiki, "log", "-n1", "--format=%s") == "Archive closed tasks"   # into her list's archive
    files = git(wiki, "show", "--no-renames", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["_tasks/closed-1998-07.json", "_tasks/lists/team/closed-1998-07.json",          # one commit has both
                             "private/alice/_tasks/closed-1999-03.json", "private/alice/_tasks/lists/secretlist/closed-1999-03.json"]
    sign_in(client, "bob")
    assert client.get("/api/tasks/closed-years", headers=HOST).get_json()["years"][1:] == ["1998"]
    assert client.get("/api/tasks/closed?year=1999", headers=HOST).get_json() == []
    assert [t["title"] for t in client.get("/api/tasks/closed?year=1998", headers=HOST).get_json()] == ["Shared long ago"]
    for path in ("/api/tasks/export", "/api/tasks/closed?year=2001", "/api/search?q=long+ago", "/uncommitted", "/api/uncommitted",
                 "/page/_deleted", "/history/index", "/tasks?view=closed&year=1999"):
        body = client.get(path, headers=HOST).get_data(as_text=True)
        assert hers not in body and "Long ago" not in body and "private/alice" not in body and "1999" not in body, path
    for text in (mcp(client, "history"), mcp(client, "history", limit=50), mcp(client, "search_tasks", status="closed"),
                 mcp(client, "search_wiki", query="long ago")):
        assert "private/alice" not in text and "closed-1999" not in text and hers not in text and "Long ago" not in text
    # A write of his files nothing of hers and commits none of her files: a
    # task of hers in closed.json whose month has ended waits for her.
    (wiki / "private/alice/_tasks/closed.json").write_text(json.dumps([private_task(hers[:-4] + "1997", "Older", "1997-03-05T10:00:00")]))
    git(wiki, "add", "-A")
    git(wiki, "commit", "-q", "-m", "An unfiled task of hers")
    made = client.post("/api/tasks", json={"title": "Bob's", "list": "team"}, headers=HOST).get_json()
    assert client.put(f"/api/tasks/{made['id']}", json={"completed": True}, headers=HOST).status_code == 200
    assert "1997" in (wiki / "private/alice/_tasks/closed.json").read_text()
    assert "private/alice" not in git(wiki, "show", "--stat", "--format=", "HEAD")
    assert "1997" not in client.get("/api/tasks/closed-years", headers=HOST).get_data(as_text=True)
    # Hers: both years, her task, and her own write files her unfiled task.
    sign_in(client, "alice")
    years = client.get("/api/tasks/closed-years", headers=HOST).get_json()["years"]
    assert years[1:] == ["2001", "1999", "1998", "1997"]
    assert [t["id"] for t in client.get("/api/tasks/closed?year=1999", headers=HOST).get_json()] == [hers]
    assert client.put(f"/api/tasks/{hers}", json={"title": "Long ago, edited"}, headers=HOST).status_code == 200
    hers_dir = wiki / "private/alice/_tasks/lists/secretlist"
    assert (hers_dir / "closed-1997-03.json").exists() and "1997" not in (wiki / "private/alice/_tasks/closed.json").read_text()
    assert "Long ago, edited" in (hers_dir / "closed-1999-03.json").read_text()
    files = git(wiki, "show", "--no-renames", "--name-only", "--format=%an", "HEAD").split()
    assert files[0] == "alice" and sorted(files[1:]) == [
        "private/alice/_tasks/closed.json", "private/alice/_tasks/lists/secretlist/closed-1997-03.json",
        "private/alice/_tasks/lists/secretlist/closed-1999-03.json"]
    sign_in(client, "bob")
    assert "private/alice" not in mcp(client, "history") and "1997" not in mcp(client, "history")

def test_changes_name_only_the_tasks_bob_sees(client, wiki):
    """A commit that changed a shared task and one of alice's own names both
    (its Task trailers), and bob sees the commit for the shared one: Changes
    and the tool history give him that task's title, and nothing of hers,
    not her title, her id, nor that there was one more."""
    sign_in(client, "alice")
    shared = client.post("/api/tasks", json={"title": "Order chairs", "list": "team"}, headers=HOST).get_json()
    resp = client.post("/api/tasks/batch-tag", json={"ids": [shared["id"], TASK_ID], "add": ["both"]}, headers=HOST)
    assert resp.status_code == 200
    message = git(wiki, "log", "-n1", "--format=%B")
    assert f"Task: {TASK_ID}" in message and f"Task: {shared['id']}" in message
    assert "Order chairs" in client.get("/changes", headers=HOST).get_data(as_text=True)
    assert SECRET in client.get("/changes", headers=HOST).get_data(as_text=True)          # hers, to her
    sign_in(client, "bob")
    body = client.get("/changes", headers=HOST).get_data(as_text=True)
    row = re.findall(r'<li class="hist-row changes-row">(.*?)</li>', body, re.S)[0]
    assert "Tag tasks" in row and f"task={shared['id']}" in row and "Order chairs" in row
    assert SECRET not in body and TASK_ID not in body and "other tasks" not in row and ">Tasks<" not in row
    text = mcp(client, "history")
    assert '"Order chairs" (task ' in text and SECRET not in text and TASK_ID[:8] not in text and "other tasks" not in text


def test_alice_is_offered_the_changes_she_left(alice, wiki):
    body = alice.get("/edit/plans", headers=HOST).get_data(as_text=True)
    assert 'id="left-draft"' in body and SECRET in body


def test_alice_sees_her_own(client, wiki):
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    assert SECRET in client.get(f"/page/{DIARY}", headers=HOST).data.decode()
    assert SECRET in client.get("/api/search", query_string={"q": QUERY}, headers=HOST).data.decode()
    plans = client.get("/page/plans", headers=HOST).data.decode()
    assert 'href="/page/private/alice/diary"' in plans
    assert f"Task {SECRET}" in plans                                 # the [[task:…]] chip
    tasks = client.get("/api/tasks", headers=HOST).get_json()
    assert [t["private"] for t in tasks if t["id"] == TASK_ID] == [True]
    # The tasks /tasks brings along are hers too (not bob's: the probes above).
    assert TASK_ID in client.get("/tasks", headers=HOST).data.decode().split('class="tasks-initial">', 1)[1].split("</script>", 1)[0]
    assert "2001" in client.get("/api/tasks/closed-years", headers=HOST).get_json()["years"]
    # Her Tasks home is hers: the Tasks link goes to My tasks (in a new
    # browser session: /tasks above made it come back there), which has
    # her pinned private list's task.
    client.delete_cookie("zone-tasks-place")
    assert 'href="/tasks?mine=1"' in client.get("/page/index", headers=HOST).data.decode()
    mine = client.get("/api/tasks/export?mine=1", headers=HOST).get_json()["tasks"]
    assert {TASK_ID, INBOX_ID} <= {t["id"] for t in mine}
    # Her Inbox is hers: its view and its export have its task, private.
    inbox = client.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]
    assert [(t["id"], t["private"]) for t in inbox] == [(INBOX_ID, True)]
    page = client.get("/tasks?list=inbox", headers=HOST).data.decode()
    assert INBOX_ID in page.split('class="tasks-initial">', 1)[1].split("</script>", 1)[0]
    assert "<h1>Inbox" in page and "Your Inbox: only you see its tasks" in page
    assert client.get("/api/tasks/due-count", headers=HOST).get_json()["count"] == 2      # both are overdue
    assert client.get("/api/tasks/due-count?places=1", headers=HOST).get_json()["places"]["inbox"]["count"] == 1


def test_alice_sees_her_own_names_whole(client, wiki):
    """Her files named with letters outside ASCII and spaces are hers, by
    their own names: in Changes, in her assistant's history, among her
    deleted pages (which left such a page out)."""
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    for text in (client.get("/changes", headers=HOST).get_data(as_text=True), mcp(client, "history", token="alice-token")):
        assert f"private/alice/_assets/{SECRET} März.pdf" in text and "\\303" not in text
    deleted = client.get("/page/_deleted", headers=HOST).get_data(as_text=True)
    assert f"private/alice/{SECRET}-geständnis" in deleted and "Geständnis" in deleted


def test_bobs_inbox_is_his_own(bob, wiki):
    """Each user's Inbox is their own: bob's view of it is empty while he
    has no task there, counts nothing of alice's, and shows his capture
    only to him."""
    assert bob.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"] == []
    counts = bob.get("/api/tasks/due-count?places=1", headers=HOST).get_json()
    assert counts["count"] == 0 and counts["places"]["inbox"]["count"] == 0
    made = bob.post("/api/tasks", json={"title": "His capture", "due_date": "2020-01-01"}, headers=HOST).get_json()
    assert made["private"] is True and "list" not in made
    assert [t["title"] for t in bob.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]] == ["His capture"]
    assert bob.get("/api/tasks/due-count?places=1", headers=HOST).get_json()["places"]["inbox"]["count"] == 1
    sign_in(bob, "alice")
    alice = bob
    assert "His capture" not in alice.get("/api/tasks", headers=HOST).get_data(as_text=True)
    assert "His capture" not in alice.get("/tasks?list=inbox", headers=HOST).get_data(as_text=True)
    assert [t["id"] for t in alice.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]] == [INBOX_ID]
    assert alice.get("/api/tasks/due-count?places=1", headers=HOST).get_json()["places"]["inbox"]["count"] == 1
    assert alice.put(f"/api/tasks/{made['id']}", json={"title": "Hers now"}, headers=HOST).status_code == 404


def test_a_private_page_of_no_one_is_seen_by_no_one(client, wiki):
    (wiki / "private" / "carol").mkdir()
    (wiki / "private/carol/x.md").write_text(f"# X {SECRET}\n")
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    assert client.get("/page/private/carol/x", headers=HOST).status_code == 404
    assert SECRET not in client.get("/api/search", query_string={"q": "X"}, headers=HOST).data.decode()


def test_stray_private_content_is_logged(client, wiki):
    (wiki / "private" / "carol").mkdir()
    client.live.stop()
    client.get("/page/index", headers=HOST)   # starts the server again
    assert "private/carol is in private/ but no user's folder there" in client.live.log.read_text()


# ── What alice does with her own ─────────────────────────────────────────────

def sign_in(client, name):
    client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST)


@pytest.fixture()
def alice(client, wiki):
    sign_in(client, "alice")
    return client


def quicknote_titles(client):
    links = re.findall(r'<a href="/page/([^"]+)"', client.get("/notes", headers=HOST).data.decode())
    return links


def test_quicknotes_are_private(alice, wiki):
    (wiki / "2026-01-01_10-00-00_shared.md").write_text("# Shared\n")   # from before users: a shared page, in no one's Notes
    slug = alice.post("/api/notes", data={"title": "Mine"}, headers=HOST).get_json()["slug"]
    assert re.fullmatch(r"private/alice/\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_mine", slug)
    assert (wiki / f"{slug}.md").exists()
    notes = quicknote_titles(alice)
    assert slug in notes and NOTE in notes and "2026-01-01_10-00-00_shared" not in notes
    assert 'aria-label="Private">🔒</span></h1>' in alice.get("/notes", headers=HOST).data.decode()
    sign_in(alice, "bob")
    bob = alice
    assert quicknote_titles(bob) == []
    assert alice.get("/page/2026-01-01_10-00-00_shared", headers=HOST).status_code == 200   # still a page


def test_move_to_wiki_makes_a_private_note_shared(alice, wiki):
    resp = alice.post(f"/promote/{NOTE}", data={"folder": "ideas", "name": "Idea"}, headers=HOST)
    assert resp.headers["Location"] == "/page/ideas/idea"
    assert (wiki / "ideas/idea.md").exists() and not (wiki / f"{NOTE}.md").exists()


def test_an_old_shared_note_cannot_be_moved_into_private(alice, wiki):
    """A note from before users is a shared page: Move to wiki into
    private/alice would take it from everyone else, which isn't an editor's
    to do: refused, and nothing moves."""
    note = "2026-01-01_10-00-00_old"
    (wiki / f"{note}.md").write_text("# Old\n")
    (wiki / "log.md").write_text(f"# Log\n\nSee [[{note}]].\n")
    resp = alice.post(f"/promote/{note}", data={"folder": "private/alice", "name": "old"}, headers=HOST)
    assert "promote_error=" in resp.headers["Location"] and "Ask an admin" in unquote(resp.headers["Location"])
    assert (wiki / f"{note}.md").exists() and f"[[{note}]]" in (wiki / "log.md").read_text()
    # Into a shared folder it goes, links following.
    alice.post(f"/promote/{note}", data={"folder": "notes", "name": "old"}, headers=HOST)
    assert (wiki / "notes/old.md").exists() and "[[notes/old]]" in (wiki / "log.md").read_text()


def test_append_moves_a_private_note_out(alice, wiki):
    resp = alice.post(f"/append/{NOTE}", data={"page": "plans", "heading": "Idea"}, headers=HOST)
    assert resp.headers["Location"] == "/page/plans#idea"
    assert SECRET in (wiki / "plans.md").read_text() and not (wiki / f"{NOTE}.md").exists()


def upload(client, page="", name="pic.png", **extra):
    return client.post("/api/upload", data={"file": (BytesIO(b"\x89PNG"), name), "page": page, **extra},
                       content_type="multipart/form-data", headers=HOST)


def test_an_upload_for_a_private_page_is_private(alice, wiki):
    resp = upload(alice, DIARY)
    assert resp.get_json()["url"] == "/private/alice/_assets/pic.png"
    assert (wiki / "private/alice/_assets/pic.png").read_bytes() == b"\x89PNG"
    assert upload(alice, DIARY).status_code == 409                      # the same name there again
    assert upload(alice, "plans").get_json()["url"] == "/_assets/pic.png"   # a shared page's: shared
    assert alice.get("/private/alice/_assets/pic.png", headers=HOST).status_code == 200
    assert "Upload private/alice/_assets/pic.png" in git(wiki, "log", "--format=%s")
    sign_in(alice, "bob")
    bob = alice
    assert bob.get("/private/alice/_assets/pic.png", headers=HOST).status_code == 404
    assert bob.get("/_assets/pic.png", headers=HOST).status_code == 200
    # Not into alice's folder, nor with everyone's, which his file for her page didn't mean.
    resp = upload(bob, DIARY, "b.png")
    assert resp.status_code == 404 and "only your own folder private/bob/" in resp.get_data(as_text=True)
    assert not (wiki / "_assets/b.png").exists() and not (wiki / "private/alice/_assets/b.png").exists()
    # The same for a page of hers that isn't there: the place alone decides.
    none = upload(bob, "private/alice/none", "c.png")
    assert none.status_code == 404 and none.get_data(as_text=True) == resp.get_data(as_text=True).replace("diary", "none")


def test_assets_in_other_folders_are_not_served(alice, wiki):
    (wiki / "_assets/sub").mkdir()
    (wiki / "_assets/sub/a.png").write_bytes(b"x")
    (wiki / "private/alice/_assets/deeper").mkdir(parents=True)
    (wiki / "private/alice/_assets/deeper/a.png").write_bytes(b"x")
    (wiki / "private/alice/_assets/a.png").write_bytes(b"x")
    for path in ("/_assets/sub/a.png", "/_assets/private/alice/a.png", "/private/alice/_assets/deeper/a.png",
                 "/private/alice/_assets/"):
        assert alice.get(path, headers=HOST).status_code == 404, path


def test_a_link_among_the_uploads_is_no_upload(alice, client, wiki):
    """A symlink made on the host among the uploads leads nowhere (uploadPath,
    as safePath for pages): a shared upload that links to alice's private
    one, bob's uploads folder that links to hers. Nothing is served or read
    through them, nothing written through them, and a name with a '\\'
    (out of the folder on Windows) is no upload's name."""
    (wiki / "_assets/leak.png").symlink_to(wiki / "private/alice/_assets/photo.png")
    (wiki / "private/bob").mkdir(parents=True, exist_ok=True)
    (wiki / "private/bob/_assets").symlink_to(wiki / "private/alice/_assets")
    sign_in(alice, "bob")
    bob = alice
    for path in ("/_assets/leak.png", "/private/bob/_assets/photo.png", "/private/bob/_assets/..%5Calice%5C_assets%5Cphoto.png"):
        resp = bob.get(path, headers=HOST)
        assert resp.status_code == 404 and SECRET.encode() not in resp.data, path
    for url in ("/_assets/leak.png", "/private/bob/_assets/photo.png"):
        assert SECRET not in mcp(client, "read_file", url=url)
    # Nor written through: an upload by that name, replacing it, or one into the linked folder.
    assert upload(bob, "", "leak.png", overwrite="1").status_code == 400
    assert upload(bob, "private/bob/x", "photo.png", overwrite="1").status_code == 400
    assert upload(bob, "", r"a\..\..\private\alice\_assets\photo.png", overwrite="1").status_code == 400
    assert (wiki / "private/alice/_assets/photo.png").read_bytes() == SECRET.encode()


def test_files_name_only_the_uploads_the_reader_may_open(alice, wiki):
    """A shared page's Files (pageFiles) list the uploads it links to that
    the reader may open: alice's private file and her private task's to
    her, not to bob, for whom they are left out as a file that isn't there
    is; a symlink among the uploads to nobody."""
    (wiki / "_assets/map.png").write_bytes(b"map")
    (wiki / "_assets/leak.png").symlink_to(wiki / "private/alice/_assets/photo.png")
    (wiki / "shelf.md").write_text(
        "# Shelf\n\n![](/_assets/map.png) ![](/private/alice/_assets/photo.png) "
        f"[file](/_tasks/files/{TASK_ID}/_assets/photo.png) ![](/_assets/leak.png) ![](/private/alice/_assets/none.png)\n")

    def files(client):
        body = client.get("/page/shelf", headers=HOST).get_data(as_text=True)
        foot = body.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        return re.findall(r'<li><a href="([^"]+)">', foot.split('id="files"', 1)[1]) if 'id="files"' in foot else []

    assert files(alice) == ["/_assets/map.png", "/private/alice/_assets/photo.png", f"/_tasks/files/{TASK_ID}/_assets/photo.png"]
    sign_in(alice, "bob")
    assert files(alice) == ["/_assets/map.png"]


def test_new_private_page(alice, wiki):
    assert 'name="private"' in alice.get("/new", headers=HOST).data.decode()
    resp = alice.post("/new", data={"slug": "Gift ideas", "private": "1"}, headers=HOST)
    assert resp.headers["Location"] == "/edit/private/alice/gift-ideas"
    assert (wiki / "private/alice/gift-ideas.md").exists()
    alice.post("/new", data={"slug": "private/alice/two", "private": "1"}, headers=HOST)
    assert (wiki / "private/alice/two.md").exists()


def test_no_private_checkbox_without_users(client, wiki_dir):
    assert 'name="private"' not in client.get("/new", headers=HOST).data.decode()
    client.post("/new", data={"slug": "x", "private": "1"}, headers=HOST)
    assert (wiki_dir / "x.md").exists()


def test_rename_into_another_users_folder_is_refused(bob, wiki):
    resp = bob.post("/rename/plans", data={"new_name": "private/alice/plans"}, headers=HOST)
    assert "rename_error" in resp.headers["Location"]
    assert (wiki / "plans.md").exists() and not (wiki / "private/alice/plans.md").exists()


def test_history_leaves_out_versions_from_before_a_page_was_shared(alice, wiki):
    alice.post(f"/rename/{DIARY}", data={"new_name": "diary", "confirmed": "diary"}, headers=HOST)
    shas = git(wiki, "log", "--follow", "--format=%h", "--", "diary.md").split()
    assert len(shas) == 2   # the rename, and alice's commit before it
    sign_in(alice, "bob")
    bob = alice
    body = bob.get("/history/diary", headers=HOST).data.decode()
    assert shas[0] in body and shas[1] not in body
    assert bob.get(f"/revision/diary/{shas[1]}", headers=HOST).status_code == 404


# Names git would read as patterns (its pathspecs: wildcards, ":(magic)")
# that match alice's files.
PATTERNS = ["*", "p*", "private*/diary", "*/alice/*", ":(glob)private/**/diary", ":(glob)**/*", ":/private/alice/diary"]


@pytest.mark.parametrize("name", PATTERNS)
def test_a_page_named_like_a_pattern_is_only_itself(bob, wiki, name):
    # A page may be called anything, "*" too: its history, versions and
    # changes are that file's, not those of every file the name matches.
    diary = sha(wiki)
    for path in (f"/diff/{name}/{diary}", f"/revision/{name}/{diary}", f"/history/{name}", f"/page/{name}",
                 f"/diff-uncommitted/{name}"):
        body = bob.get(path, headers=HOST).get_data(as_text=True)
        assert SECRET not in body and "private/alice" not in unquote(body).replace(unquote(name), ""), path
    assert SECRET not in mcp(bob, "history", slug=name, version=diary[:7])


def test_a_page_named_star_shows_no_one_elses_uncommitted_text(bob, wiki):
    # bob's page "*", committed; alice's diary edited since its commit.
    (wiki / "*.md").write_text("# Star\n")
    git(wiki, "add", "--", "*.md")
    git(wiki, "commit", "-q", "-m", "Create *", "--", "*.md")
    (wiki / f"{DIARY}.md").write_text(f"# Diary\n\nnot committed: {SECRET}\n")
    resp = bob.get("/diff-uncommitted/*", headers=HOST)
    assert resp.status_code == 200 and SECRET not in resp.get_data(as_text=True)
    assert "no changes" in resp.get_data(as_text=True)
    # The page itself still has its history and its changes.
    assert "Created" in bob.get("/history/*", headers=HOST).get_data(as_text=True)
    (wiki / "*.md").write_text("# Star\n\nbob's own line\n")
    assert "bob&#39;s own line" in bob.get("/diff-uncommitted/*", headers=HOST).get_data(as_text=True)


def test_a_pattern_tells_nothing_of_a_deleted_private_page(bob, wiki):
    # private/alice/old was deleted: a name matching it is a page that
    # isn't there (the editor for a new one), not a deleted page to restore.
    resp = bob.get("/page/p*/alice/ol*", headers=HOST)
    assert resp.status_code == 302 and "/edit/" in resp.headers["Location"]
    # Git's own pattern syntax has a ':', which no page's name has: refused
    # for the name alone, the same whatever was deleted.
    resp = bob.get("/page/:(glob)private/alice/*", headers=HOST)
    assert resp.status_code == 400 and "':'" in resp.get_data(as_text=True)
    assert resp.get_data(as_text=True) == bob.get("/page/:(glob)private/nobody/*", headers=HOST).get_data(as_text=True)


def test_commit_all_commits_only_what_you_see(bob, wiki):
    (wiki / "notes.md").write_text("# Bob's\n")
    assert bob.post("/api/commit-all", headers=HOST).get_json()["files"] == ["notes.md"]
    assert "private/alice/draft.md" in git(wiki, "status", "--porcelain")
    sign_in(bob, "alice")
    assert bob.post("/api/commit-all", headers=HOST).get_json()["files"] == ["private/alice/draft.md"]


def test_links_from_invisible_pages_dont_count(alice, wiki):
    def links(client):
        return {p["slug"]: p["links"] for p in client.get("/api/pages-list", headers=HOST).get_json()}
    assert links(alice)["plans"] == 1   # her diary links there
    sign_in(alice, "bob")
    bob = alice
    assert links(bob)["plans"] == 0


def test_a_new_task_goes_to_her_inbox(alice, wiki):
    """An embed of her private list shows it; New task / c t (the headless
    app) start a new task in no list, which with users is her Inbox, so
    private, whatever she pinned; the List field names her lists, hers
    marked. Bob has no such list."""
    html = __import__("html")
    assert 'data-init-list="secretlist"' in alice.get("/page/private/alice/deck", headers=HOST).data.decode()
    index = alice.get("/page/index", headers=HOST).data.decode()
    assert "data-tasks-list" not in index           # nothing on the page says where a new task goes
    tasks = html.unescape(alice.get("/tasks", headers=HOST).data.decode())
    assert 'id="task-edit-list"' in tasks and "task-edit-private" not in tasks
    assert 'data-inbox-private="1"' in tasks and "data-default-list" not in tasks
    assert '{"ref":"secretlist","private":true}' in tasks and '{"ref":"team","private":false}' in tasks
    assert 'data-private-folder="private/alice"' in tasks
    headless = alice.get("/api/tasks/headless", headers=HOST).data.decode()
    assert 'data-inbox-private="1"' in headless and "data-default-list" not in headless
    # The task itself: hers alone, in her files, though her pinned list is another.
    made = alice.post("/api/tasks", json={"title": "Capture"}, headers=HOST).get_json()
    assert made["private"] is True and "list" not in made
    assert made["id"] in (wiki / "private/alice/_tasks/tasks.json").read_text()      # her Inbox's file
    assert made["id"] not in shared_tasks(wiki)
    sign_in(alice, "bob")
    bob = alice
    page = bob.get("/tasks", headers=HOST).data.decode()
    assert 'data-inbox-private="1"' in page and "secretlist" not in page and "Capture" not in page


def test_nothing_private_without_users(client, wiki_dir):
    tasks = client.get("/tasks", headers=HOST).data.decode()
    assert "list-new-private" not in tasks and "data-private-folder" not in tasks
    assert "private" not in client.post("/api/tasks", json={"title": "x"}, headers=HOST).get_json()
    assert "data-inbox-private" not in tasks        # the Inbox is the wiki's: the tasks in no list
    # private is no field of a write, and no list is private.
    resp = client.post("/api/tasks", json={"title": "y", "private": True}, headers=HOST)
    assert resp.status_code == 400 and "list" in resp.get_json()["error"]
    made = client.post("/tasks/new-list", data={"name": "mine", "private": "1"}, headers=HOST)
    assert "newlist_error" in made.headers["Location"] and not (wiki_dir / "_tasks/lists.json").exists()


def test_a_task_moves_between_her_files_and_the_shared_ones(alice, wiki):
    task = alice.post("/api/tasks", json={"title": "Gift", "list": "secretlist"}, headers=HOST).get_json()
    assert task["private"] is True and task["list"] == "secretlist"
    class mine:            # her open tasks, whichever of her folders holds them
        read_text = staticmethod(lambda: her_tasks(wiki))
    assert task["id"] in mine.read_text() and task["id"] not in shared_tasks(wiki)
    # Out of her private list it is in her Inbox: still hers alone, share or not.
    for body in ({"list": ""}, {"list": "secretlist"}, {"list": "inbox", "share": True}, {"list": "secretlist"}, {"list": None}):
        moved = alice.put(f"/api/tasks/{task['id']}", json=body, headers=HOST)
        assert moved.status_code == 200 and moved.get_json()["private"] is True, body
        assert moved.get_json().get("list") == (body["list"] if body["list"] == "secretlist" else None)
        assert task["id"] in mine.read_text() and task["id"] not in shared_tasks(wiki)
        assert "shared" not in [e["event"] for e in moved.get_json()["history"]]
    # Into a shared list it is shared: only when the write says so.
    refused = alice.put(f"/api/tasks/{task['id']}", json={"list": "team"}, headers=HOST)
    assert refused.status_code == 400 and "share" in refused.get_json()["error"] and task["id"] in mine.read_text()
    assert task["id"] not in shared_tasks(wiki)
    moved = alice.put(f"/api/tasks/{task['id']}", json={"list": "team", "share": True}, headers=HOST).get_json()
    assert moved["private"] is False and moved["list"] == "team"
    assert task["id"] in shared_tasks(wiki) and task["id"] not in mine.read_text()
    # One commit, both files, in her name.
    assert git(wiki, "show", "--name-only", "--format=%an", "HEAD").split() == [
        "alice", "_tasks/lists/team/tasks.json", "private/alice/_tasks/tasks.json"]
    sign_in(alice, "bob")
    assert task["id"] in [t["id"] for t in alice.get("/api/tasks", headers=HOST).get_json()]


def test_a_tag_does_not_make_a_task_private(alice, wiki):
    """secretlist is her private list, but a tag of that name is a tag:
    a task is in a list, and so private, only by its list."""
    a = alice.post("/api/tasks", json={"title": "A", "list": "team", "tags": ["SecretList"]}, headers=HOST).get_json()
    assert a["private"] is False and a["list"] == "team"
    own = alice.post("/api/tasks", json={"title": "B", "list": "SecretList"}, headers=HOST).get_json()
    assert own["private"] is True and own["list"] == "secretlist" and own["tags"] == []
    c = alice.post("/api/tasks", json={"title": "C", "list": "team"}, headers=HOST).get_json()
    tagged = alice.post("/api/tasks/batch-tag", json={"ids": [c["id"]], "add": ["secretlist"]}, headers=HOST)
    assert tagged.get_json()[0]["private"] is False and tagged.get_json()[0]["list"] == "team"
    # Nor does a tag put a task into the list: one in her Inbox stays there.
    d = alice.post("/api/tasks", json={"title": "D", "tags": ["secretlist", "team"]}, headers=HOST).get_json()
    assert d["private"] is True and "list" not in d


def test_a_shared_task_cannot_be_made_private(alice, wiki):
    """Others have seen it: shared stays shared, by every route and tool:
    it goes neither into a private list nor into an Inbox (with users a
    shared task is always in a list). A private task, though, can be shared
    (and not back)."""
    sign_in(alice, "bob")
    bobs = alice.post("/api/tasks", json={"title": "Bob's", "list": "team"}, headers=HOST).get_json()
    assert bobs["private"] is False
    sign_in(alice, "alice")
    class shared_file:
        read_text = staticmethod(lambda: shared_tasks(wiki))

    class her_file:
        read_text = staticmethod(lambda: her_tasks(wiki))
    for where, why in (("secretlist", "stays shared"), ("", "always in a list"), ("inbox", "always in a list"),
                       ("none", "always in a list"), (None, "always in a list")):
        for resp in (alice.put(f"/api/tasks/{bobs['id']}", json={"list": where}, headers=HOST),
                     alice.put(f"/api/tasks/{bobs['id']}", json={"list": where, "share": True}, headers=HOST),
                     alice.post("/api/tasks/batch-update", json={"tasks": [{"id": bobs["id"], "list": where}]}, headers=HOST)):
            assert resp.status_code == 400 and why in resp.get_json()["error"], where
        if where is not None:
            assert why in mcp(alice, "update_task", token="alice-token", id=bobs["id"], list=where)
        assert bobs["id"] in shared_file.read_text() and bobs["id"] not in her_file.read_text()
    # Whoever made it: bob can't take it back into his Inbox either.
    sign_in(alice, "bob")
    assert alice.put(f"/api/tasks/{bobs['id']}", json={"list": ""}, headers=HOST).status_code == 400
    assert not (wiki / "private/bob/_tasks/tasks.json").exists() or bobs["id"] not in (wiki / "private/bob/_tasks/tasks.json").read_text()
    sign_in(alice, "alice")
    # Her own private task goes the other way; unchanged (the list it is in) is no error.
    mine = alice.post("/api/tasks", json={"title": "Mine", "list": "secretlist"}, headers=HOST).get_json()
    assert alice.put(f"/api/tasks/{bobs['id']}", json={"list": "team"}, headers=HOST).status_code == 200
    assert alice.put(f"/api/tasks/{mine['id']}", json={"list": "team", "share": True}, headers=HOST).get_json()["private"] is False
    assert alice.put(f"/api/tasks/{mine['id']}", json={"list": "secretlist"}, headers=HOST).status_code == 400
    assert alice.put(f"/api/tasks/{mine['id']}", json={"list": ""}, headers=HOST).status_code == 400
    assert mine["id"] in shared_file.read_text() and mine["id"] not in her_file.read_text()
    # private is no field of a write: a task is private by its list.
    assert alice.put(f"/api/tasks/{mine['id']}", json={"private": True}, headers=HOST).status_code == 400


def test_a_comment_is_its_authors_to_change(alice, wiki):
    """A shared task is everyone's; a comment on it is its author's: alice
    can't edit or delete bob's, and his own edit keeps him as its author."""
    task = alice.post("/api/tasks", json={"title": "Shared", "list": "team"}, headers=HOST).get_json()
    url = f"/api/tasks/{task['id']}/comments"
    sign_in(alice, "bob")
    his = alice.post(url, json={"text": "His"}, headers=HOST).get_json()["comments"][-1]
    sign_in(alice, "alice")
    assert his["by"] == "bob"
    assert alice.put(f"{url}/{his['id']}", json={"text": "Changed"}, headers=HOST).status_code == 403
    assert alice.delete(f"{url}/{his['id']}", headers=HOST).status_code == 403
    sign_in(alice, "bob")
    edited = alice.put(f"{url}/{his['id']}", json={"text": "Changed"}, headers=HOST).get_json()
    assert [(c["text"], c["by"]) for c in edited["comments"]] == [("Changed", "bob")]
    assert alice.delete(f"{url}/{his['id']}", headers=HOST).get_json()["comments"] == []


def test_sharing_a_task_is_in_its_history(alice, wiki):
    """Making a private task shared is an event in its History, by who did it."""
    task = alice.post("/api/tasks", json={"title": "Own", "list": "secretlist"}, headers=HOST).get_json()
    url = f"/api/tasks/{task['id']}"
    # Into her Inbox nothing is shared: no event.
    inbox = alice.put(url, json={"list": "", "share": True}, headers=HOST).get_json()
    assert inbox["private"] is True and "shared" not in [e["event"] for e in inbox["history"]]
    last, given = alice.put(url, json={"list": "team", "share": True}, headers=HOST).get_json()["history"][-2:]
    assert (given["event"], given["to"]) == ("assigned", "alice")              # and it stays hers to do
    assert (last["event"], last["by"]) == ("shared", "alice")
    # Unchanged is no event, and a new private task has none to start with.
    again = alice.put(url, json={"list": "team"}, headers=HOST).get_json()["history"]
    assert [e["event"] for e in again].count("shared") == 1
    for fields in ({"list": "secretlist"}, {}):
        new = alice.post("/api/tasks", json={"title": "New", **fields}, headers=HOST).get_json()
        assert [e["event"] for e in new["history"]] == ["created"]


def test_undoing_a_delete_keeps_a_task_private(alice, wiki):
    snap = next(t for t in alice.get("/api/tasks", headers=HOST).get_json() if t["id"] == TASK_ID)
    assert alice.delete(f"/api/tasks/{TASK_ID}", headers=HOST).status_code == 204
    restored = alice.post("/api/tasks/restore", json={"tasks": [snap]}, headers=HOST).get_json()
    assert [t["private"] for t in restored] == [True]
    assert TASK_ID in her_tasks(wiki)


def test_alices_assistant_makes_private_tasks(client, wiki):
    """create_task without a list puts a task into her Inbox, so private,
    whatever she pinned, as c t does; told a list, into that."""
    text = mcp(client, "create_task", token="alice-token", title="Call mum")
    assert "· private" in text and "list:" not in text
    for none in ("", "inbox", "none"):
        text = mcp(client, "create_task", token="alice-token", title="Loose lunch", list=none)
        assert "· private" in text and "list:" not in text, none
    text = mcp(client, "create_task", token="alice-token", title="Wrap the gift", list="secretlist")
    assert "· private ·" in text and "list: secretlist" in text
    text = mcp(client, "create_task", token="alice-token", title="Team lunch", list="team")
    assert "private" not in text.split("\n")[3] and "list: team" in text
    assert "stays" not in mcp(client, "create_task", token="alice-token", title="x", private=True)   # told to give list
    assert "list" in mcp(client, "create_task", token="alice-token", title="x", private=True)
    her_file = her_tasks(wiki)
    assert all(t in her_file for t in ("Call mum", "Loose lunch", "Wrap the gift")) and "Team lunch" not in her_file
    shared = shared_tasks(wiki)
    assert "Team lunch" in shared and not any(t in shared for t in ("Call mum", "Loose lunch", "Wrap the gift"))
    # Her Inbox, by its name, is hers: bob's has none of it.
    assert "Call mum" in mcp(client, "search_tasks", token="alice-token", list="inbox")
    assert "Wrap the gift" not in mcp(client, "search_tasks", token="alice-token", list="inbox")
    assert "Team lunch" in mcp(client, "search_tasks", query="lunch")
    assert "Loose lunch" not in mcp(client, "search_tasks", query="lunch")
    assert "Call mum" not in mcp(client, "search_tasks", query="mum")
    for name in ("inbox", "none"):
        text = mcp(client, "search_tasks", list=name)
        assert "Call mum" not in text and "Loose lunch" not in text and SECRET not in text


# ── MCP ──────────────────────────────────────────────────────────────────────

def rpc(client, method, params, token="bob-token"):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert resp.status_code == 200
    return resp.get_json()["result"]


def mcp(client, tool, token="bob-token", **args):
    return rpc(client, "tools/call", {"name": tool, "arguments": args}, token)["content"][0]["text"]


MCP_READS = [
    ("read_page", {"slug": DIARY}),
    ("read_page", {"slug": "plans"}),                            # links to the diary
    ("read_page", {"slug": SNEAK}),
    ("history", {"slug": SNEAK}),
    ("read_guides", {"slug": SNEAK}),
    ("search_wiki", {"query": QUERY}),
    ("search_wiki", {"query": QUERY, "under": "private"}),
    ("search_wiki", {"query": QUERY, "notes": True}),
    ("grep_wiki", {"pattern": QUERY}),
    ("list_pages", {}),
    ("list_pages", {"under": "private"}),
    ("list_pages", {"notes": True}),
    ("check_links", {}),
    ("list_tags", {}),
    ("history", {"slug": DIARY}),
    ("history", {"slug": "private/alice/old"}),                  # deleted
    ("history", {}),                                             # recent changes
    ("history", {"under": "private"}),
    ("history", {"watched": True}),
    ("search_tasks", {"query": QUERY}),
    ("read_guides", {"slug": DIARY}),
    ("read_guides", {"slug": "private/alice/"}),
    ("search_tasks", {"query": QUERY, "status": "all"}),
    ("search_tasks", {"tag": "secretlist", "status": "all"}),
    ("search_tasks", {"list": "inbox", "status": "all"}),        # his Inbox, not hers
    ("search_tasks", {"list": "none"}),
    ("search_tasks", {"assignee": "none", "status": "all"}),
    ("replace_in_pages", {"find": QUERY, "replace": "x"}),       # a dry run
    ("read_file", {"url": "/private/alice/_assets/photo.png"}),
    ("read_file", {"url": f"/_tasks/files/{TASK_ID}/_assets/photo.png"}),
    ("read_file", {"url": "/private/bob/../alice/_assets/photo.png"}),
    ("read_file", {"url": "/_assets/../private/alice/_assets/photo.png"}),
    ("read_file", {"url": "/_assets/..%2Fprivate%2Falice%2Fdiary.md"}),
]


@pytest.mark.parametrize("tool,args", MCP_READS)
def test_bobs_assistant_reads_nothing_of_alices(client, wiki, tool, args):
    text = mcp(client, tool, **args)
    # The slug asked for, and the shared page's own links, may name the diary.
    named = text.replace(args.get("slug", "\0"), "").replace(f"[[{DIARY}", "")
    assert SECRET not in text and "private/alice" not in named
    assert TASK_ID[:8] not in text.replace(f"[[task:{TASK_ID[:8]}]]", "") and "secretlist" not in text
    assert sha(wiki)[:7] not in text or tool == "history" and not args, "the diary's versions"


def test_her_private_upload_is_hers_over_mcp_and_with_her_token(client, wiki):
    """read_file and the file routes with a token give alice her private upload, bob none of it."""
    url = "/private/alice/_assets/photo.png"
    mine = rpc(client, "tools/call", {"name": "read_file", "arguments": {"url": url}}, "alice-token")
    assert not mine["isError"] and "photo.png (image/png" in mine["content"][0]["text"]
    assert mine["content"][1]["type"] == "image"
    assert rpc(client, "tools/call", {"name": "read_file", "arguments": {"url": url}})["isError"]
    assert client.get(url, headers={**HOST, "Authorization": "Bearer alice-token"}).data == SECRET.encode()
    assert client.get(url, headers={**HOST, "Authorization": "Bearer bob-token"}).status_code == 404
    # A file for her private page goes to her folder; bob's for the same page doesn't.
    text = mcp(client, "upload_file", token="alice-token", filename="plan.txt", text="hers", page=DIARY)
    assert "[plan.txt](/private/alice/_assets/plan.txt)" in text and "only alice can open it" in text
    assert (wiki / "private/alice/_assets/plan.txt").read_text() == "hers"
    refused = rpc(client, "tools/call", {"name": "upload_file", "arguments": {"filename": "bobs.txt", "text": "his", "page": DIARY}})
    assert refused["isError"] and "Nothing uploaded" in refused["content"][0]["text"]
    assert not (wiki / "_assets/bobs.txt").exists()
    sent = client.post("/api/upload", data={"file": (BytesIO(b"hers too"), "plan.txt"), "page": DIARY},
                       content_type="multipart/form-data", headers={**HOST, "Authorization": "Bearer alice-token"})
    assert sent.get_json() == {"url": "/private/alice/_assets/plan-2.txt"}


def test_alices_assistant_sees_her_own(client, wiki):
    assert SECRET in mcp(client, "read_page", token="alice-token", slug=DIARY)
    assert DIARY in mcp(client, "list_pages", token="alice-token")
    assert DIARY in mcp(client, "search_wiki", token="alice-token", query=QUERY)
    assert DIARY in mcp(client, "grep_wiki", token="alice-token", pattern=QUERY)
    assert "Delete private/alice/old" in mcp(client, "history", token="alice-token")
    assert f"Task {SECRET}" in mcp(client, "search_tasks", token="alice-token", query=QUERY)
    assert "private" in mcp(client, "search_tasks", token="alice-token", query=QUERY).split("\n")[3]


def gives_up_on(word):
    """A regex that gives up on a line holding word (PCRE's match limit:
    the line's characters taken two ways each) and finds nothing on any
    other line: where grep_wiki's engine reads a line with word, the answer
    is an error, so a word in a file bob may not see must never make one."""
    return rf"^(?=.*{word})(?:.|.)*(*FAIL)"


def grep_fails(client, word, token="bob-token"):
    return rpc(client, "tools/call", {"name": "grep_wiki", "arguments": {"pattern": gives_up_on(word)}}, token)["isError"]


LONG = " and some more words, so that the line is long enough to give up on\n"


def test_grep_wiki_reads_no_file_he_may_not_see(client, wiki):
    """grep_wiki's regex runs over what bob sees and nothing else: her
    pages, her uploads, her task's files, a folder of hers made by hand in
    capitals and Zonewiki's own folders stay unread, so no error tells a word
    of them."""
    (wiki / "private/alice/_assets/keys.md").write_text(f"{SECRET}upload{LONG}")
    (wiki / f"_tasks/files/{TASK_ID}/_assets/keys.md").write_text(f"{SECRET}taskfile{LONG}")
    (wiki / "Private/Alice").mkdir(parents=True)
    (wiki / "Private/Alice/by-hand.md").write_text(f"{SECRET}byhand{LONG}")
    (wiki / ".zone/drafts/alice/left.md").write_text(f"{SECRET}draft{LONG}")
    (wiki / "private/alice/long.md").write_text(f"{SECRET}page{LONG}")
    (wiki / "notes").mkdir()
    (wiki / "notes/shared.md").write_text(f"bobseesthis{LONG}")
    assert grep_fails(client, "bobseesthis"), "the pattern gives up where it reads the word"
    for word in ("page", "upload", "taskfile", "byhand", "draft"):
        assert not grep_fails(client, SECRET + word), word
    # She sees her own, by hand in capitals too (placeOf judges in lower case).
    assert grep_fails(client, SECRET + "page", "alice-token") and grep_fails(client, SECRET + "byhand", "alice-token")
    # A pattern git can't read is said; the regex giving up only that it did.
    text = mcp(client, "grep_wiki", pattern="(")
    assert "missing closing parenthesis" in text or "Unmatched (" in text
    assert "pcre" not in mcp(client, "grep_wiki", pattern=gives_up_on("bobseesthis")).lower()


def test_read_guides_tells_no_folder_he_may_not_see(client, wiki):
    """Whether a folder of hers is there, or a folder outside the wiki,
    read_guides answers bob the same: a folder is one by the pages he sees
    in it, never by the disk."""
    (wiki / "private/alice/jobsearch").mkdir()
    (wiki / "private/alice/jobsearch/acme.md").write_text("# Acme\n")
    same = lambda there, none: mcp(client, "read_guides", slug=there).replace(there, "X") == \
        mcp(client, "read_guides", slug=none).replace(none, "X")
    assert same("private/alice/jobsearch", "private/alice/nothere")
    assert same("private/alice/jobsearch/", "private/alice/nothere/")
    up = "../" * 12
    assert same(up + "etc", up + "nonexistent-folder")
    assert "is no page or folder of this wiki" in mcp(client, "read_guides", slug=up + "etc")
    assert "is no page or folder of this wiki" in mcp(client, "read_guides", slug="_tasks")
    # Hers to her, as a folder.
    (wiki / "private/alice/jobsearch/AGENTS.md").write_text("# How I keep my job search\n")
    assert "guide private/alice/jobsearch/AGENTS" in mcp(client, "read_guides", token="alice-token",
                                                          slug="private/alice/jobsearch")


def test_his_writes_count_none_of_her_files(client, wiki):
    """A rename re-points the links on her pages and in her tasks too, a
    replace changes his own private pages with shared ones: what bob is told,
    and what the history says to her and anyone, counts only what each sees."""
    text = mcp(client, "rename_page", slug="plans", new_slug="plans-2")
    assert "Renamed page plans to plans-2." in text and "updated in" not in text
    assert "[[plans-2]]" in (wiki / f"{DIARY}.md").read_text()    # hers follow all the same
    (wiki / "notes.md").write_text("# Notes\n\n[[plans-2]]\n")
    assert "Links to it were updated in 1 files." in mcp(client, "rename_page", slug="plans-2", new_slug="plans")
    (wiki / "projects").mkdir()
    (wiki / "projects/roadmap.md").write_text("# Roadmap\n")
    (wiki / f"{DIARY}.md").write_text((wiki / f"{DIARY}.md").read_text() + "\n[[projects/roadmap]]\n")
    text = mcp(client, "rename_page", slug="projects/", new_slug="work")
    assert "Moved folder projects/ to work/: 1 pages." in text and "updated in" not in text
    assert "[[work/roadmap]]" in (wiki / f"{DIARY}.md").read_text()
    # His replace over his private pages and a shared one, not in git yet:
    # neither commit says a count.
    (wiki / "private/bob").mkdir(parents=True)
    for slug in ("private/bob/a", "private/bob/b", "shed"):
        (wiki / f"{slug}.md").write_text("# X\n\nfoobarbaz\n")
    assert "on 3 pages" in mcp(client, "replace_in_pages", find="foobarbaz", replace="quux", dry_run=False)
    assert git(wiki, "log", "-2", "--format=%s").splitlines() == [
        'Replace "foobarbaz" (MCP)', 'Save pages before replacing "foobarbaz"']
    assert "3 pages" not in mcp(client, "history", token="alice-token")


def test_guides_in_private_folders_stay_private(client, wiki):
    """A guide in alice's folder, and a shared one importing her diary:
    bob's assistant gets neither her guide nor the diary's text."""
    (wiki / "private/alice/CLAUDE.md").write_text(f"# Alice's rules {SECRET}\n")
    (wiki / "notes").mkdir()
    (wiki / "notes/AGENTS.md").write_text(f"# Notes guide\n\n@../{DIARY}.md\n")
    tools = rpc(client, "tools/list", {})["tools"]
    guides = next(t for t in tools if t["name"] == "read_guides")["description"]
    assert "notes/" in guides and "private/alice" not in guides
    text = mcp(client, "read_guides", slug="notes/")
    assert "Notes guide" in text and SECRET not in text
    refusal = mcp(client, "write_page", slug="notes/x", content="# X\n")
    assert "Notes guide" in refusal and SECRET not in refusal
    alices = rpc(client, "tools/list", {}, token="alice-token")["tools"]
    assert "private/alice/" in next(t for t in alices if t["name"] == "read_guides")["description"]
    assert SECRET in mcp(client, "read_guides", token="alice-token", slug="notes/")


def test_history_names_no_private_page(client, wiki):
    """A commit that moved alice's note into the wiki is in the shared
    page's history, but its message doesn't name the note for bob."""
    alice = "alice-token"
    mcp(client, "file_note", token=alice, slug=NOTE, name="Idea")
    assert NOTE in mcp(client, "history", token=alice, slug="idea")
    for text in (mcp(client, "history", slug="idea"), mcp(client, "history")):
        assert "Promote a private page to idea" in text and "private/alice" not in text


def test_recent_changes_fill_the_limit_with_visible_commits(client, wiki):
    for i in range(3):
        (wiki / f"private/alice/p{i}.md").write_text("# P\n")
        git(wiki, "add", "-A")
        git(wiki, "commit", "-q", "-m", f"Create private/alice/p{i}")
    text = mcp(client, "history", limit=2)
    assert "(2, the limit)" in text and "private/alice" not in text


def test_bobs_assistant_writes_quicknotes_in_his_folder(client, wiki):
    text = mcp(client, "write_page", note=True, slug="Thought", content="Hm\n")
    assert "Created note private/bob/" in text
    assert "private/bob/" in mcp(client, "list_pages", notes=True)
    assert "private/bob/" not in mcp(client, "list_pages", token="alice-token", notes=True)


def test_a_private_new_list_is_its_makers_alone(alice, client, wiki):
    """New list… with Private: the list is in alice's registry, so bob has
    no such list; a shared one by that name is his to make, and she then
    has both, her own by the plain name."""
    resp = alice.post("/tasks/new-list", data={"name": "Gifts", "private": "1"}, headers=HOST)
    assert resp.headers["Location"] == "/tasks?list=gifts"
    assert '{"name": "gifts"}' in (wiki / "private/alice/_tasks/lists.json").read_text()
    assert not (wiki / "private/alice/lists").exists() and "gifts" not in (wiki / "_tasks/lists.json").read_text()
    page = alice.get("/tasks", headers=HOST).get_data(as_text=True)
    assert 'data-place="list:gifts"' in page and '<option value="private">Only you</option>' in page
    assert alice.post("/tasks/new-list", data={"name": "gifts"}, headers=HOST).headers["Location"].startswith("/tasks?newlist_name=")
    sign_in(client, "bob")
    assert 'data-place="list:gifts"' not in client.get("/tasks", headers=HOST).get_data(as_text=True)
    assert client.post("/tasks/new-list", data={"name": "gifts"}, headers=HOST).headers["Location"] == "/tasks?list=gifts"
    assert '{"name": "gifts", "owners": ["bob"]}' in (wiki / "_tasks/lists.json").read_text()
    sign_in(client, "alice")
    page = client.get("/tasks", headers=HOST).get_data(as_text=True)
    assert 'data-place="list:gifts"' in page and 'data-place="list:shared/gifts"' in page


def test_a_private_list_made_where_the_page_stays_is_its_makers_alone(alice, client, wiki):
    """New list… in the dialog that moves tasks, with Private."""
    resp = alice.post("/api/tasks/lists", json={"name": "Gifts", "private": True}, headers=HOST)
    assert resp.status_code == 201 and resp.get_json()["ref"] == "gifts" and resp.get_json()["private"] is True
    assert '{"name": "gifts"}' in (wiki / "private/alice/_tasks/lists.json").read_text()
    assert "gifts" not in (wiki / "_tasks/lists.json").read_text()
    assert alice.post("/api/tasks/lists", json={"name": "gifts"}, headers=HOST).status_code == 400
    sign_in(client, "bob")
    assert 'data-place="list:gifts"' not in client.get("/tasks", headers=HOST).get_data(as_text=True)
    assert client.post("/api/tasks/lists", json={"name": "gifts"}, headers=HOST).get_json()["ref"] == "gifts"


# ── Writes ───────────────────────────────────────────────────────────────────

WRITES = [
    # Watching (test_watch.py asks what a watched folder tells): her page isn't watched, and a folder's watch is bob's own.
    ("POST /watch", "POST", "/watch", {"data": {"page": DIARY}}),
    ("POST /watch", "POST", "/watch", {"data": {"page": SNEAK}}),
    ("POST /watch", "POST", "/watch", {"data": {"folder": "private/alice", "back": "/changes?watched=1"}}),
    ("POST /watch/read", "POST", "/watch/read", {}),
    ("POST /edit/{slug...}", "POST", f"/edit/{DIARY}", {"data": {"content": "# Bob was here\n"}}),
    ("POST /api/save/{slug...}", "POST", f"/api/save/{DIARY}", {"data": {"content": "# Bob was here\n"}}),
    ("POST /new", "POST", "/new", {"data": {"slug": "private/alice/bobs"}}),
    ("POST /link-new", "POST", "/link-new", {"data": {"slug": "garden", "on": DIARY}}),     # a link on her page pointed elsewhere
    ("POST /relink", "POST", "/relink", {"data": {"slug": "garden", "on": DIARY, "target": "x/garden"}}),   # likewise
    ("POST /rename/{slug...}", "POST", f"/rename/{DIARY}", {"data": {"new_name": "stolen"}}),
    ("POST /tasks/rename", "POST", "/tasks/rename", {"data": {"tag": "secretlist", "new_name": "stolen"}}),
    # New list…: a list named as alice's private one, and one aimed at her folder.
    ("POST /tasks/new-list", "POST", "/tasks/new-list", {"data": {"name": "secretlist"}}),
    ("POST /tasks/new-list", "POST", "/tasks/new-list", {"data": {"name": "../alice/x", "private": "1"}}),
    ("POST /api/tasks/lists", "POST", "/api/tasks/lists", {"json": {"name": "secretlist"}}),
    ("POST /api/tasks/lists", "POST", "/api/tasks/lists", {"json": {"name": "../alice/x", "private": True}}),
    # Her private list, by its name: no list for bob.
    ("POST /tasks/rename-list", "POST", "/tasks/rename-list", {"data": {"list": "secretlist", "new_name": "stolen"}}),
    ("POST /tasks/delete-list", "POST", "/tasks/delete-list", {"data": {"list": "secretlist", "to": "", "share": "1"}}),
    ("POST /tasks/share-list", "POST", "/tasks/share-list", {"data": {"list": "secretlist"}}),
    ("POST /tasks/list-for", "POST", "/tasks/list-for", {"data": {"list": "secretlist", "for": "bob"}}),
    ("POST /tasks/pin-list", "POST", "/tasks/pin-list", {"data": {"list": "secretlist", "pin": "1"}}),
    ("POST /tasks/pin-order", "POST", "/tasks/pin-order", {"data": {"list": "secretlist"}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"list": "secretlist", "set": {"limit": "3"}}}),
    ("POST /tasks/rename", "POST", "/tasks/rename", {"data": {"tag": f"{QUERY}-{SECRET}", "new_name": "stolen"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "private/alice/plans"}}),
    ("POST /delete/{slug...}", "POST", f"/delete/{DIARY}", {}),
    ("POST /restore/{rest...}", "POST", "/restore/private/alice/old/{old}", {}),
    ("POST /changes/undo", "POST", "/changes/undo?sha={old}", {}),          # the commit that deleted her page
    ("POST /changes/undo", "POST", "/changes/undo?sha={sha}", {}),          # one that changed her pages and a shared one
    ("POST /promote/{slug...}", "POST", f"/promote/{NOTE}", {"data": {"folder": "", "name": "stolen"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "private/alice", "name": "x"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "", "name": "x", "link_from": DIARY}}),
    ("POST /append/{slug...}", "POST", f"/append/{NOTE}", {"data": {"page": "plans", "heading": "Stolen"}}),
    ("POST /append/{slug...}", "POST", "/append/{bobs}", {"data": {"page": DIARY, "heading": "Bob's"}}),
    ("POST /append/{slug...}", "POST", "/append/{bobs}", {"data": {"page": SNEAK, "heading": "Bob's"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "", "name": "x", "link_from": SNEAK}}),
    ("POST /api/toggle-checkbox/{slug...}", "POST", f"/api/toggle-checkbox/{DIARY}", {"data": {"idx": "0"}}),
    ("POST /api/commit-file", "POST", "/api/commit-file", {"json": {"filepath": "private/alice/draft.md"}}),
    # Import pages: a zip into her folder, by its path and by way of his; her waiting import (test_import.py), by no id.
    ("POST /import", "POST", "/import", {"data": lambda: {"file": (BytesIO(notes_zip()), "vault.zip"), "folder": "private/alice/vault"}}),
    ("POST /import", "POST", "/import", {"data": lambda: {"file": (BytesIO(notes_zip()), "vault.zip"), "folder": SNEAK + "/x"}}),
    ("POST /import/apply", "POST", "/import/apply", {"data": {"id": "0" * 32}}),
    ("POST /import/cancel", "POST", "/import/cancel", {"data": {"id": "0" * 32}}),
    ("POST /api/commit-all", "POST", "/api/commit-all", {}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"bob"), "photo.png"),
                                                                  "page": DIARY, "overwrite": "1"}}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"bob"), "private/alice/photo.png"),
                                                                  "overwrite": "1"}}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"bob"), "x.png"),
                                                                  "filename": "../private/alice/photo.png",
                                                                  "overwrite": "1"}}),
    # The settings dialog: the [[tasks: …]] on alice's page.
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings",
     {"json": {"page": "private/alice/deck", "index": 0, "was": "list=secretlist", "set": {"view": "open"}}}),
    # alice's tasks, by id; a task of bob's for her list is refused (he has no such list).
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Bob's", "list": "secretlist"}}),
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{TASK_ID}", {"json": {"list": "", "share": True}}),
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{TASK_ID}", {"json": {"list": "team", "share": True}}),
    # Her Inbox's task: filed into a shared list, taken into his Inbox, changed, deleted.
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{INBOX_ID}", {"json": {"list": "team", "share": True}}),
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{INBOX_ID}", {"json": {"list": "inbox"}}),
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{INBOX_ID}", {"json": {"title": "Bob was here", "assignee": "bob"}}),
    ("DELETE /api/tasks/{id}", "DELETE", f"/api/tasks/{INBOX_ID}", {}),
    ("POST /api/tasks/batch-update", "POST", "/api/tasks/batch-update", {"json": {"tasks": [{"id": INBOX_ID, "list": "team", "share": True}]}}),
    ("POST /api/tasks/batch-complete", "POST", "/api/tasks/batch-complete", {"json": {"ids": [INBOX_ID]}}),
    # A list's settings: her Inbox has none to set, by either name.
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"list": "inbox", "set": {"home": "off"}}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"list": "secretlist", "set": {"home": "off"}}}),
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{TASK_ID}", {"json": {"title": "Bob was here"}}),
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{DONE_ID}", {"json": {"completed": False}}),
    ("DELETE /api/tasks/{id}", "DELETE", f"/api/tasks/{TASK_ID}", {}),
    ("POST /api/tasks/{id}/toggle-check", "POST", f"/api/tasks/{TASK_ID}/toggle-check", {"json": {"idx": 0}}),
    ("POST /api/tasks/{id}/comments", "POST", f"/api/tasks/{TASK_ID}/comments", {"json": {"text": "Bob's"}}),
    ("PUT /api/tasks/{id}/comments/{cid}", "PUT", f"/api/tasks/{TASK_ID}/comments/{COMMENT_ID}", {"json": {"text": "Bob's"}}),
    ("DELETE /api/tasks/{id}/comments/{cid}", "DELETE", f"/api/tasks/{TASK_ID}/comments/{COMMENT_ID}", {}),
    ("POST /api/tasks/batch-complete", "POST", "/api/tasks/batch-complete", {"json": {"ids": [TASK_ID]}}),
    ("POST /api/tasks/batch-complete", "POST", "/api/tasks/batch-complete", {"json": {"ids": [DONE_ID], "completed": False}}),
    ("POST /api/tasks/batch-delete", "POST", "/api/tasks/batch-delete", {"json": {"ids": [TASK_ID, DONE_ID, OLD_ID]}}),
    ("POST /api/tasks/batch-tag", "POST", "/api/tasks/batch-tag", {"json": {"ids": [TASK_ID], "add": ["bob"]}}),
    ("POST /api/tasks/batch-update", "POST", "/api/tasks/batch-update", {"json": {"tasks": [{"id": TASK_ID, "title": "Bob"}]}}),
    ("POST /api/tasks/reorder", "POST", "/api/tasks/reorder", {"json": {"mine": True, "moves": [{"id": TASK_ID, "after": INBOX_ID, "before": ""}]}}),
    ("POST /api/tasks/reorder", "POST", "/api/tasks/reorder", {"json": {"moves": [{"id": TASK_ID, "after": INBOX_ID, "before": ""}]}}),
    # A position in the manual order: hers, and his by hers.
    ("PUT /api/tasks/{id}", "PUT", f"/api/tasks/{TASK_ID}", {"json": {"position": "top"}}),
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Bob's", "list": "team", "position": {"after": TASK_ID}}}),
    ("POST /api/tasks/restore", "POST", "/api/tasks/restore", {"json": {"tasks": [{"id": TASK_ID, "title": "Bob's"}]}}),
    ("POST /api/tasks/import", "POST", "/api/tasks/import", {"json": {"file": {"tasks": [{"id": TASK_ID, "title": "Bob's"}]}}}),
]


def _write_params():
    return [pytest.param(method, path, kwargs, id=f"{method} {path} {i}")
            for i, (route, method, path, kwargs) in enumerate(WRITES)]


def _alices_files(wiki):
    """alice's private files, and whether a shared tasks file holds one of
    her task ids (a restore of a snapshot with her id)."""
    files = {p.relative_to(wiki).as_posix(): p.read_bytes()
             for folder in ("private",)
             for p in (wiki / folder).rglob("*") if p.is_file()
             and "private/bob/" not in p.relative_to(wiki).as_posix() + "/"}   # bob's own, bob may change
    shared = "".join(p.read_text() for p in (wiki / "_tasks").rglob("*.json"))
    files["shared tasks has her ids"] = str(any(id in shared for id in HER_TASKS))
    return files


@pytest.mark.parametrize("method,path,kwargs", _write_params())
def test_bob_changes_nothing_of_alices(bob, wiki, method, path, kwargs):
    if "{bobs}" in path:   # bob's own note, aiming at alice's pages from there
        path = path.replace("{bobs}", bob.post("/api/notes", data={"title": "Bobs"}, headers=HOST).get_json()["slug"])
    kwargs = {k: v() if callable(v) else v for k, v in kwargs.items()}
    before, log = _alices_files(wiki), git(wiki, "log", "--format=%H")
    resp = bob.open(fill(path, wiki), method=method, headers=HOST, **kwargs)
    assert _alices_files(wiki) == before
    assert SECRET not in resp.data.decode(errors="replace")
    if f"/api/tasks/{TASK_ID}/" in path or method == "PUT" and "/api/tasks/" in path:
        assert resp.status_code == 404   # as for no such task
    new = git(wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split() if log else []
    for commit in new:
        assert "private/alice" not in git(wiki, "show", "--stat", "--format=", commit)


# What bob's assistant tries on alice's pages; "{bobs}" is a note of
# his, "{old}" the version that still had her deleted page.
MCP_WRITES = [
    ("watch_page", {"slug": DIARY}),
    ("watch_page", {"slug": SNEAK}),
    ("watch_page", {"slug": "private/alice/"}),
    ("write_page", {"slug": DIARY, "content": "# Bob was here\n", "overwrite": True}),
    ("write_page", {"slug": "private/alice/bobs", "content": "# Bob was here\n"}),
    ("edit_page", {"slug": DIARY, "old_string": QUERY, "new_string": "bob"}),
    ("edit_page", {"slug": SNEAK, "old_string": QUERY, "new_string": "bob"}),
    ("append_to_page", {"slug": SNEAK, "content": "Bob was here"}),
    ("delete_page", {"slug": SNEAK}),
    ("rename_task_tag", {"tag": "secretlist", "new_tag": "stolen"}),
    ("rename_page", {"slug": SNEAK, "new_slug": "stolen"}),
    ("file_note", {"slug": "{bobs}", "page": SNEAK}),
    ("append_to_page", {"slug": DIARY, "content": "Bob was here"}),
    ("replace_in_pages", {"find": QUERY, "replace": "bob", "dry_run": False}),
    ("rename_page", {"slug": DIARY, "new_slug": "stolen"}),
    ("rename_page", {"slug": "plans", "new_slug": "private/alice/plans"}),
    ("rename_page", {"slug": "private/", "new_slug": "stolen"}),
    ("rename_page", {"slug": "private/alice/", "new_slug": "stolen"}),
    ("delete_page", {"slug": DIARY}),
    ("restore_page", {"slug": "private/alice/old", "version": "{old}"}),
    ("restore_page", {"slug": DIARY, "version": "{sha}"}),
    ("undo_changes", {"versions": "{old}"}),
    ("undo_changes", {"versions": "{sha}"}),
    ("file_note", {"slug": NOTE, "name": "stolen"}),
    ("file_note", {"slug": "{bobs}", "page": DIARY}),
    ("file_note", {"slug": "{bobs}", "name": "x", "folder": "private/alice"}),
    ("create_task", {"title": "Bob's", "list": "secretlist"}),
    ("create_task_list", {"name": "secretlist", "private": True}),   # his own list of that name
    ("rename_task_list", {"list": "secretlist", "new_name": "bobs"}),          # her private list, by its name: no list for bob
    ("update_task", {"id": TASK_ID, "list": "", "share": True}),
    ("update_task", {"id": TASK_ID, "list": "team", "share": True}),
    ("update_task", {"id": INBOX_ID, "list": "team", "share": True}),
    ("update_task", {"id": INBOX_ID[:8], "title": "Bob was here"}),
    ("update_task", {"ids": [INBOX_ID], "status": "done"}),
    ("update_task", {"id": TASK_ID[:8], "title": "Bob was here"}),
    ("update_task", {"id": TASK_ID, "status": "wont_do"}),
    ("update_task", {"id": DONE_ID, "status": "open", "comment": "Bob's"}),
    ("update_task", {"ids": [TASK_ID, OLD_ID], "add_tags": ["bob"]}),
    ("update_task", {"id": TASK_ID, "position": "top"}),
    ("update_task", {"ids": [INBOX_ID, TASK_ID], "position": "bottom"}),
    ("create_task", {"title": "Bob's", "list": "team", "after": TASK_ID}),
    ("create_task", {"title": "Bob's", "list": "team", "before": INBOX_ID[:8]}),
    ("upload_file", {"filename": "bobs.txt", "text": "Bob was here", "page": DIARY}),          # not into her folder
    ("upload_file", {"filename": "../private/alice/_assets/photo.png", "text": "Bob was here"}),
]


@pytest.mark.parametrize("tool,args", [pytest.param(t, a, id=f"{t} {i}") for i, (t, a) in enumerate(MCP_WRITES)])
def test_bobs_assistant_changes_nothing_of_alices(client, wiki, tool, args):
    if "{bobs}" in args.values():
        bobs = mcp(client, "write_page", note=True, slug="Bobs", content="Bob's\n")
        args = {k: re.search(r"note (\S+)", bobs)[1] if v == "{bobs}" else v for k, v in args.items()}
    args = {k: fill(v, wiki) if isinstance(v, str) else v for k, v in args.items()}
    before, log = _alices_files(wiki), git(wiki, "log", "--format=%H")
    text = mcp(client, tool, **args)
    assert _alices_files(wiki) == before, text
    for commit in git(wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split():
        assert "private/alice" not in git(wiki, "show", "--stat", "--format=", commit)
    assert SECRET not in text


def _private_tags(wiki, owner="alice"):
    return json.loads((wiki / f"private/{owner}/_tasks/tasks.json").read_text())[0]["tags"]


def test_a_shared_tags_rename_reaches_private_files_and_tells_nothing(bob, wiki):
    """secretlist on a shared task too is one tag for all: bob's rename goes on in
    alice's private tasks and embeds, but he is told only of what he sees."""
    made = bob.post("/api/tasks", json={"title": "Shared", "list": "team", "tags": ["secretlist"]}, headers=HOST)
    assert made.status_code < 300 and made.get_json()["private"] is False
    out = mcp(bob, "rename_task_tag", tag="secretlist", new_tag="renamed")
    assert out == "Renamed tag #secretlist to #renamed, in one commit: 1 task."
    assert "renamed" in _private_tags(wiki) and "secretlist" not in _private_tags(wiki)
    # Her embed that filters by the tag follows; her list of that name is no tag, and stays.
    deck = (wiki / "private/alice/deck.md").read_text()
    assert "tag=renamed" in deck and "list=secretlist" in deck
    assert '{"name": "secretlist"}' in (wiki / "private/alice/_tasks/lists.json").read_text()


def test_a_tag_only_she_sees_stays_hers(alice, wiki):
    """A tag on nothing shared is hers: bob's private tag of that name is another tag,
    and nothing about it shows in the history bob reads."""
    bobs = wiki / "private/bob/_tasks"
    bobs.mkdir(parents=True)
    (bobs / "tasks.json").write_text(json.dumps([{**private_task(TASK_ID[:-1] + "9", "Bob's"), "tags": ["secretlist"], "list": ""}]))
    out = mcp(alice, "rename_task_tag", token="alice-token", tag="secretlist", new_tag="lumenword")
    assert "Renamed tag" in out and "private/alice/deck" in out, out
    assert "lumenword" in _private_tags(wiki)
    assert _private_tags(wiki, "bob") == ["secretlist"]
    assert "lumenword" not in mcp(alice, "history", token="bob-token")


def test_a_name_she_uses_is_not_taken_for_bob(bob, wiki):
    """Renaming to a name only alice uses is no merge for bob: the refusal would tell him it exists."""
    bob.post("/api/tasks", json={"title": "Bob's", "tags": ["errands"]}, headers=HOST)
    out = mcp(bob, "rename_task_tag", tag="errands", new_tag="secretlist")
    assert "Renamed tag" in out, out


def test_a_path_through_his_own_folder_stays_in_it(bob, wiki):
    """private/bob/../alice/… is judged as it is opened, alice's: what bob
    writes by such a name lands in his own folder, if anywhere."""
    def hers():
        return {p.relative_to(wiki).as_posix(): p.read_bytes() for p in (wiki / "private/alice").rglob("*") if p.is_file()}
    before = hers()
    mcp(bob, "write_page", slug=SNEAK, content="# Bob was here\n", overwrite=True)
    note = bob.post("/api/notes", data={"title": "Bobs"}, headers=HOST).get_json()["slug"]
    bob.post(f"/promote/{note}", data={"folder": "private/bob/../alice", "name": "x"}, headers=HOST)
    assert hers() == before


def test_an_assistant_cannot_make_a_shared_page_private(alice, wiki):
    """A shared page isn't an editor's to take from the others: renaming it,
    or a folder of shared pages, into alice's folder is refused and nothing
    moves, linked or not."""
    (wiki / "toy.md").write_text("# Toy\n")
    (wiki / "games.md").write_text("# Games\n\nSee [[toy]].\n")
    out = mcp(alice, "rename_page", token="alice-token", slug="toy", new_slug="private/alice/toy")
    assert "Not renamed" in out and "Ask an admin" in out
    assert (wiki / "toy.md").exists() and "[[toy]]" in (wiki / "games.md").read_text()
    (wiki / "stuff").mkdir()
    (wiki / "stuff/a.md").write_text("# A\n")
    out = mcp(alice, "rename_page", token="alice-token", slug="stuff/", new_slug="private/alice/stuff")
    assert "Nothing was moved" in out and "Ask an admin" in out and (wiki / "stuff/a.md").exists()
    # Her own private page may go out, and back within her folder.
    assert "Renamed page" in mcp(alice, "rename_page", token="alice-token", slug=DIARY, new_slug="private/alice/journal")


def test_moving_the_private_folder_is_refused(client, wiki):
    """With a note of bob's in private/, private/ is a folder he sees, but
    it holds alice's too: nothing moves."""
    mcp(client, "write_page", note=True, slug="Bobs", content="Bob's\n")
    before = _alices_files(wiki)
    assert "Nothing was moved" in mcp(client, "rename_page", slug="private/", new_slug="stolen")
    assert _alices_files(wiki) == before and not (wiki / "stolen").exists()


def test_a_position_by_her_task_is_told_as_by_no_task(bob, wiki):
    """bob's task put after alice's private one: refused in the words a
    task that isn't there gets, through the API and his assistant alike,
    and nothing is made."""
    nosuch = "deadbeef-0000-4000-8000-000000000000"
    said = [bob.post("/api/tasks", json={"title": "Bob's", "list": "team", "position": {"after": id}}, headers=HOST)
            for id in (TASK_ID, nosuch)]
    assert [r.status_code for r in said] == [400, 400] and said[0].get_json() == said[1].get_json()
    told = [mcp(bob, "create_task", title="Bob's", list="team", after=id) for id in (TASK_ID, nosuch)]
    assert told[0].replace(TASK_ID, "X") == told[1].replace(nosuch, "X") and "No task with id" in told[0]
    assert "Bob's" not in shared_tasks(wiki)


def test_every_tool_is_covered():
    tools = {t["name"] for t in json.loads((REPO / "go/mcp_tools.json").read_text())}
    covered = {t[0] for t in MCP_READS} | {t[0] for t in MCP_WRITES} | {"zone_help"}
    assert tools - covered == set(), "tools neither probed nor exempt"
    assert covered - tools == set(), "probes of tools that don't exist"


# ── Every route is covered ───────────────────────────────────────────────────

# Routes that show or change no page: nothing of alice's to leak.
EXEMPT = {
    "/mcp",                         # its tools: MCP_READS
    "GET /{$}", "GET /login", "POST /login", "POST /logout", "GET /public/{path...}", "GET /api/version",
    "GET /api/health",              # ok, or the settings file's error
    "POST /api/notes",              # makes bob's own note
    "GET /private",                 # redirects to the caller's own private home (test_users.py)
    # The admin page: admins only, names and roles, no page (test_admin.py)
    "GET /admin/wiki", "POST /admin/wiki/timezone",   # the wiki's timezone, an admin's (test_timezone.py)
    "GET /admin", "GET /admin/users", "POST /admin/users/{name}/role", "POST /admin/users/{name}/groups", "POST /admin/users/{name}/disable", "POST /admin/users/{name}/enable",
    "POST /admin/users/{name}/remove", "POST /admin/users/invite", "POST /admin/users/{name}/reset", "POST /admin/users/{name}/revoke",
    "GET /welcome/{token}", "POST /welcome/{token}",   # a link's own page: sets one password (test_admin.py)
    "GET /signup", "POST /signup",  # asks for an account by mail, signed out (test_signup.py)
    # The caller's groups: making one, and who is in one they own. A rename moves a group's pages: its own
    # tests ask it as someone who doesn't manage the group, and as an admin who isn't in it (test_groups_own.py)
    "POST /groups", "POST /groups/{group}/people", "POST /groups/{group}/rename", "POST /groups/{group}/leave",
    "POST /groups/{group}/remove",  # ends a group and moves or deletes its pages: asked as who doesn't manage it (test_groups_own.py)
    "POST /groups/includes",        # an admin's: what a group is made of, names only, no page (test_groups_own.py)
    "GET /assistant", "POST /assistant/token", "POST /assistant/revoke",   # the caller's own MCP token, no page (test_assistant.py)
    "GET /password", "POST /password",   # the caller's own password, no page (test_password.py)
    # the caller's own mail address, no page; an admin's: a user's address (test_email.py)
    "GET /notifications", "POST /notifications", "POST /email", "POST /email/resend", "GET /email/confirm/{token}", "POST /email/confirm/{token}",
    "POST /admin/users/{name}/email",
    "GET /updates",                 # the caller's own news: its tasks are the task app's, asked of /api/tasks?updates=1 (above)
    "GET /usage",                   # counts of the wiki's use, for admins: numbers and user names, no page (test_usage.py)
    # OAuth: the caller's own connections, no page (test_oauth.py)
    "GET /.well-known/oauth-protected-resource", "GET /.well-known/oauth-protected-resource/mcp",
    "GET /.well-known/oauth-authorization-server", "POST /oauth/register", "GET /oauth/authorize",
    "POST /oauth/authorize", "POST /oauth/token", "POST /oauth/revoke", "POST /assistant/connections/{id}/revoke",
    "POST /assistant/connections/{id}/change",
    "GET /setup", "POST /setup",    # the first-run page: only while the wiki has no login (test_setup.py)
    "GET /help", "GET /help/{name...}",   # Zonewiki's manual and spec, from docs/: nothing of the wiki's (test_help.py)
}


def test_every_route_is_covered():
    source = "".join(p.read_text() for p in (REPO / "go").glob("*.go") if not p.name.endswith("_test.go"))
    routes = set(re.findall(r'HandleFunc\("([^"]+)"', source))
    covered = {r[0] for r in READS} | {w[0] for w in WRITES} | EXEMPT
    assert routes - covered == set(), "routes neither probed nor exempt"
    assert covered - routes == set(), "probes of routes that don't exist"


def test_link_warnings_dont_tell_which_private_pages_exist(client, wiki):
    """A link into alice's folder is never reported broken, whether or not the page is
    there (else "no such page" would tell bob which exist); his own folder still is."""
    links = f"[[{DIARY}]] [[private/alice/nonexistent]] [[private/bob/nope]] [[nope-shared]]"
    for out in (
        mcp(client, "write_page", slug="probe", content=f"# Probe\n\n{links}\n"),
        mcp(client, "read_page", slug="probe"),
        mcp(client, "check_links"),
    ):
        assert "private/alice" not in out.split("\n---\n")[-1].replace(DIARY, "")
        assert "private/bob/nope" in out and "nope-shared" in out
    assert "alice" not in mcp(client, "edit_page", slug="probe", old="nope-shared", new="private/alice/zzz")


def test_link_guesses_are_only_pages_the_asker_sees(client, wiki):
    """check_links guesses the one page with a broken link's last path part: never alice's
    diary for bob's [[diary]], and her diary doesn't make his shared one of that name one of
    several (which would tell him hers exists)."""
    (wiki / "probe.md").write_text("# Probe\n\n[[diary]]\n")
    out = mcp(client, "check_links")
    assert "[[diary]]" in out and "maybe" not in out and "alice" not in out
    (wiki / "journal").mkdir()
    (wiki / "journal/diary.md").write_text("# Diary\n")
    out = mcp(client, "check_links")
    assert "probe: [[diary]] (maybe [[journal/diary]]?)" in out and "alice" not in out
    # alice sees both: for her, the name is no longer one page's.
    assert "\nprobe: [[diary]]\n" in mcp(client, "check_links", token="alice-token") + "\n"


def test_search_statistics_ignore_private_pages(client, wiki):
    """Fuzzy matching only runs for a word no page has. If alice's pages counted,
    bob's typo "quokkaflutte" would stop finding his page the moment her
    "quokkaflute" exists, which tells him her pages have that word."""
    (wiki / "bobs.md").write_text("# Bobs\n\nquokkaflutte is how he spells it\n")
    assert "bobs" in mcp(client, "search_wiki", query=SECRET)


# ── A shared rename rewrites links on private pages: not her draft too ─────────

def _authors(wiki):
    out = git(wiki, "log", "--format=%an|%s").splitlines()
    return [tuple(line.split("|", 1)) for line in out]


def _added(wiki):
    """The lines the newest commit adds."""
    return [l for l in git(wiki, "show", "HEAD", "--format=").splitlines() if l.startswith("+") and not l.startswith("+++")]


def _rename_as_bob(bob, wiki):
    assert bob.post("/rename/plans", data={"new_name": "plans-2"}, headers=HOST).status_code in (200, 302)
    return _authors(wiki), git(wiki, "show", "HEAD", "--format=", "--stat")


DRAFT = f"# Diary {SECRET}\n\n{QUERY} {SECRET} with [[plans]].\n\nA line alice wrote and has not committed.\n"


def test_a_rename_leaves_her_saved_draft_to_her(client, wiki):
    """Alice auto-saved her private page, which links to plans; bob renames plans.
    Her text goes into git as hers, not into bob's rename."""
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    base = (wiki / f"{DIARY}.md").read_text()
    assert client.post(f"/api/save/{DIARY}", data={"content": DRAFT, "file_hash": content_hash(base)},
                       headers=HOST).status_code == 200
    client.post("/logout", headers=HOST)
    client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
    log, stat = _rename_as_bob(client, wiki)
    assert log[0][0] == "bob" and "Rename" in log[0][1]
    assert not any("has not committed" in l for l in _added(wiki))
    assert any(a == "alice" and s.startswith("Save ") for a, s in log)   # her files, in her name
    assert "has not committed" in git(wiki, "show", f"HEAD~1:{DIARY}.md")
    assert "[[plans-2]]" in (wiki / f"{DIARY}.md").read_text()


def test_a_rename_leaves_a_draft_leaf_doesnt_know_to_its_owner(bob, wiki):
    """The same for text on disk that Zonewiki never saw (written by hand, or before a
    restart): in a private folder the owner is known from the path."""
    (wiki / f"{DIARY}.md").write_text(DRAFT)
    log, stat = _rename_as_bob(bob, wiki)
    assert log[0][0] == "bob"
    assert not any("has not committed" in l for l in _added(wiki))
    assert any(a == "alice" and s.startswith("Save ") for a, s in log)   # her files, in her name
    assert "has not committed" in git(wiki, "show", f"HEAD~1:{DIARY}.md")


def test_many_unsaved_private_files_are_one_commit_of_theirs(bob, wiki):
    for i in range(5):
        (wiki / f"private/alice/wip-{i}.md").write_text(f"# Wip {i}\n\n[[plans]]\n")
    before = len(_authors(wiki))
    log, stat = _rename_as_bob(bob, wiki)
    assert [a for a, s in log[:2]] == ["bob", "alice"] and len(log) == before + 2
    assert log[1][1].startswith("Save ") and "files in private/alice" in log[1][1]
    assert git(wiki, "status", "--porcelain", "--", "private/alice/wip-0.md") == ""


def test_an_exported_private_task_stays_private_when_imported(alice, wiki):
    """alice's export has her private tasks; imported again, on a shared
    page too, they are hers alone."""
    file = alice.get("/api/tasks/export", headers=HOST).get_json()
    mine = [t for t in file["tasks"] if t["id"] == TASK_ID]
    assert mine and mine[0]["private"] is True
    alice.delete(f"/api/tasks/{TASK_ID}", headers=HOST)
    shared_before = (wiki / "_tasks/tasks.json").read_text() if (wiki / "_tasks/tasks.json").exists() else ""
    alice.post("/tasks/new-list", data={"name": "shared"}, headers=HOST)
    out = alice.post("/api/tasks/import", json={"file": {"tasks": mine}, "list": "shared"}, headers=HOST).get_json()
    assert out == {"added": 1, "skipped": 0, "ids": [TASK_ID]}
    # Into a shared list: it stays hers, in her Inbox; no list is made for it.
    back = next(t for t in json.loads((wiki / "private/alice/_tasks/tasks.json").read_text()) if t["id"] == TASK_ID)
    assert "list" not in back and '"private"' not in (wiki / "private/alice/_tasks/lists.json").read_text()
    assert ((wiki / "_tasks/tasks.json").read_text() if (wiki / "_tasks/tasks.json").exists() else "") == shared_before


# ── A new task nobody chose a place for is its user's own: the Inbox ────────────


@pytest.mark.parametrize("named", [{}, {"list": ""}, {"list": None}, {"list": "inbox"}, {"list": "None"}])
def test_a_new_task_that_names_no_list_goes_to_its_makers_inbox(bob, wiki, named):
    """With users a new task that names no list is in its maker's Inbox:
    theirs alone, in their files, with no list made for it. A private task
    can be shared later, a shared one never made private."""
    assert not (wiki / "private/bob/_tasks").exists()
    # Nothing on a page says where c t puts a task; the task app is told
    # that no list means private.
    body = bob.get("/page/plans", headers=HOST).get_data(as_text=True)
    assert "data-tasks-list" not in body
    app = bob.get("/tasks", headers=HOST).get_data(as_text=True)
    assert 'data-inbox-private="1"' in app and "data-default-list" not in app
    choices = json.loads(re.search(r'data-lists="([^"]*)"', app).group(1).replace("&#34;", '"'))
    assert choices == [{"ref": "team", "private": False}]
    assert not (wiki / "private/bob/_tasks").exists()      # looking makes nothing
    made = bob.post("/api/tasks", json={"title": "Call the bank", **named}, headers=HOST)
    assert made.status_code == 201 and made.get_json()["private"] is True and "list" not in made.get_json()
    assert "Call the bank" in (wiki / "private/bob/_tasks/tasks.json").read_text()
    assert "Call the bank" not in shared_tasks(wiki)
    assert not (wiki / "private/bob/_tasks/lists.json").exists()      # no list "private" is made any more
    changed = git(wiki, "show", "--name-only", "--format=", "HEAD").split()
    assert "private/bob/_tasks/tasks.json" in changed and not any(f.endswith("lists.json") for f in changed)
    # An assistant's create_task goes there too.
    text = mcp(bob, "create_task", title="Renew the passport", **{k: v for k, v in named.items() if v is not None})
    assert "· private" in text and "list:" not in text
    assert "Renew the passport" in (wiki / "private/bob/_tasks/tasks.json").read_text(), text
    assert '"id"' not in shared_tasks(wiki)
    # Nobody else sees them, whatever they ask for.
    sign_in(bob, "alice")
    for url in ("/api/tasks", "/api/tasks/export?list=inbox", "/api/tasks/export?mine=1", "/tasks?list=inbox", "/tasks",
                "/api/tasks?updates=1", "/updates"):
        seen = bob.get(url, headers=HOST).get_data(as_text=True)
        assert "Call the bank" not in seen and "Renew the passport" not in seen, url
    assert "Call the bank" not in mcp(bob, "search_tasks", token="alice-token", query="bank passport")


def test_the_inbox_is_a_pill_and_a_view(bob, wiki):
    """The switcher has the Inbox after My tasks and Updates, with its counts; its view
    is headed Inbox, marked private, and has its tasks; a task filed into a
    shared list leaves it."""
    made = bob.post("/api/tasks", json={"title": "Call the bank", "due_date": "2020-01-01"}, headers=HOST).get_json()
    page = bob.get("/tasks?list=inbox", headers=HOST).get_data(as_text=True)
    places = re.findall(r'data-place="([^"]+)"', page)
    assert places[:3] == ["mine", "updates", "inbox"]
    pill = page.split('data-place="inbox"', 1)[1].split("</a>", 1)[0]
    assert 'aria-current="page"' in pill and "(only you see it)" in pill and ">Inbox<" in pill
    assert '<a href="/tasks?list=inbox" class="tasks-place tasks-place-view" data-place="inbox"' in page
    assert "<h1>Inbox" in page and "Your Inbox: only you see its tasks" in page
    assert "New tasks land here, and only you see them." in page
    assert 'data-init-list="inbox"' in page
    assert made["id"] in page.split('class="tasks-initial">', 1)[1].split("</script>", 1)[0]
    # ?list=none is the same view.
    assert "<h1>Inbox" in bob.get("/tasks?list=none", headers=HOST).get_data(as_text=True)
    counts = lambda: bob.get("/api/tasks/due-count?places=1", headers=HOST).get_json()
    assert counts()["count"] == 1 and counts()["places"]["inbox"]["count"] == 1
    # Filed (share) as no one's: it is the list's, and in no Inbox.
    filed = bob.put(f"/api/tasks/{made['id']}", json={"list": "team", "share": True, "assignee": None}, headers=HOST)
    assert filed.status_code == 200 and filed.get_json()["private"] is False
    assert counts()["count"] == 0 and counts()["places"]["inbox"]["count"] == 0
    assert bob.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"] == []
    sign_in(bob, "alice")
    assert [t["id"] for t in bob.get("/api/tasks/export?list=team", headers=HOST).get_json()["tasks"]] == [made["id"]]
    assert made["id"] not in [t["id"] for t in bob.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]]


def test_sharing_mode_shows_no_inbox_of_a_user(alice, wiki):
    """Sharing mode (a screen others look at) shows nothing of what is
    private in the switcher: with users the Inbox has no pill there, and no
    view by its name."""
    assert 'data-place="inbox"' in alice.get("/tasks", headers=HOST).get_data(as_text=True)
    alice.set_cookie("zone-sharing", "1")
    page = alice.get("/tasks", headers=HOST).get_data(as_text=True)
    assert 'data-place="mine"' in page and 'data-place="inbox"' not in page
    for name in ("inbox", "none"):
        view = alice.get(f"/tasks?list={name}", headers=HOST).get_data(as_text=True)
        assert "<h1>Inbox" not in view and "tasks-inbox-line" not in view and 'class="tasks-app"' not in view, name


def test_a_list_called_private_is_an_ordinary_list(bob, wiki):
    """Until 2026-11 a user's list "private" took their new tasks and
    stayed pinned while nothing else was. It is a list like any other now:
    it pins and unpins, and a new task doesn't go into it."""
    assert bob.post("/tasks/new-list", data={"name": "private", "private": "1"}, headers=HOST).status_code == 302
    assert "list-unpin-btn" in bob.get("/tasks?list=private", headers=HOST).get_data(as_text=True)
    assert bob.post("/tasks/pin-list", data={"list": "private", "pin": "0"}, headers=HOST).status_code == 302
    page = bob.get("/tasks?list=private", headers=HOST).get_data(as_text=True)
    assert "list-unpin-btn" not in page and "list-pin-btn" in page
    assert 'data-place="list:private"' not in page.split('<nav class="tasks-places"', 1)[1].split('class="lists-more"', 1)[0]
    made = bob.post("/api/tasks", json={"title": "Call the bank"}, headers=HOST).get_json()
    assert made["private"] is True and "list" not in made
    # Unpinned, its tasks are in his My tasks all the same: a pin is a pill.
    listed = bob.post("/api/tasks", json={"title": "Listed", "list": "private"}, headers=HOST).get_json()
    mine = [t["id"] for t in bob.get("/api/tasks/export?mine=1", headers=HOST).get_json()["tasks"]]
    assert sorted(mine) == sorted([made["id"], listed["id"]])


def test_an_assistants_task_for_someone_is_not_made_private(bob, wiki):
    """create_task without a list goes into the user's Inbox, which only
    they see: with an assignee that would be a task its assignee never
    hears of, so it is refused, and the answer names the shared lists (not
    a private one, the asker's own or anyone's)."""
    for token, other in (("bob-token", "alice"), ("alice-token", "bob")):
        text = mcp(bob, "create_task", title="Check the invoice", assignee=other, token=token)
        assert "has to be in a shared list" in text and text.rstrip().endswith("Pass list: team"), text
        assert "secretlist" not in text
    assert '"id"' not in shared_tasks(wiki) and not (wiki / "private/bob/_tasks").exists()
    assert "Check the invoice" not in (wiki / "private/alice/_tasks/tasks.json").read_text()
    # In a shared list it is made, and assigned.
    text = mcp(bob, "create_task", title="Sign the form", assignee="alice", list="team")
    shared = {t["title"]: t for t in json.loads((wiki / "_tasks/lists/team/tasks.json").read_text())}
    assert shared["Sign the form"]["list"] == "team" and shared["Sign the form"]["assignee"] == "alice", text
    # Without an assignee it is in the asker's Inbox.
    mcp(bob, "create_task", title="Wrap the gift", token="alice-token")
    assert "Wrap the gift" in (wiki / "private/alice/_tasks/tasks.json").read_text()
    assert "Wrap the gift" not in (wiki / "_tasks/tasks.json").read_text()


def test_deleting_a_private_list_keeps_its_tasks_private(alice, wiki):
    """A deleted private list's tasks go to their user's Inbox when no list
    is named: private still, with or without share; into a shared list only
    with share."""
    her_file = wiki / "private/alice/_tasks/tasks.json"
    refused = alice.post("/tasks/delete-list", data={"list": "secretlist", "to": "team"}, headers=HOST)
    assert "delete_error" in refused.headers["Location"] and "secretlist" in (wiki / "private/alice/_tasks/lists.json").read_text()
    gone = alice.post("/tasks/delete-list", data={"list": "secretlist", "to": ""}, headers=HOST)
    assert gone.status_code == 302 and "delete_error" not in gone.headers["Location"]
    assert "secretlist" not in (wiki / "private/alice/_tasks/lists.json").read_text()
    tasks = {t["id"]: t for t in json.loads(her_file.read_text())}
    assert "list" not in tasks[TASK_ID] and '"id"' not in shared_tasks(wiki)
    closed = json.loads((wiki / "private/alice/_tasks/closed.json").read_text())
    assert "list" not in closed[0] and closed[0]["id"] == DONE_ID
    mine = alice.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]
    assert sorted(t["id"] for t in mine) == sorted(HER_TASKS) and all(t["private"] for t in mine)    # open and closed
    sign_in(alice, "bob")
    assert SECRET not in alice.get("/api/tasks", headers=HOST).get_data(as_text=True)
    assert SECRET not in alice.get("/tasks?list=inbox", headers=HOST).get_data(as_text=True)


def test_no_private_inbox_without_users(client, wiki_dir):
    body = client.get("/page/index", headers=HOST).get_data(as_text=True)
    assert "data-tasks-list=" not in body
    page = client.get("/tasks?list=inbox", headers=HOST).get_data(as_text=True)
    assert "data-inbox-private" not in page and "data-default-list" not in page
    assert "<h1>Inbox</h1>" in page and "New tasks land here, in no list." in page and "only you" not in page
    made = client.post("/api/tasks", json={"title": "Loose"}, headers=HOST).get_json()
    assert "private" not in made and "list" not in made
    assert "Loose" in (wiki_dir / "_tasks/tasks.json").read_text() and not (wiki_dir / "private").exists()
