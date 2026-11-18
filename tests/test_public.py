"""A public wiki (ZONE_PUBLIC in the deployment's settings;
docs/spec/security.md, Public wikis): whoever isn't signed in, a visitor,
reads the shared pages (view) or edits them too (edit), and nothing else: no
task, no note, no private page, no upload, rename or delete.

Every route Zone registers is asked here without a session
(test_every_route_is_a_visitors_or_closed): one that answers a visitor must
be on the lists below, which must be the server's own (visitorRoutes,
visitorEdits), so a route opens to visitors only by a change to both."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import HOST

REPO = Path(__file__).resolve().parent.parent

# What no answer to a visitor may hold: alice's private page, and the tasks.
SECRET = "quokkaflute"          # in alice's private page and her private task
TASKWORD = "zebracorn"          # in the shared task: its title, description, comment, tag
TASK_ID = "5ba7ed00-0000-4000-8000-000000000001"
MINE_ID = "a11ce000-0000-4000-8000-000000000001"

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "")}

# What a visitor reaches, as go/server.go lists it (test_the_lists_are_the_servers).
VISITOR = {
    "GET /{$}", "GET /page/{slug...}", "GET /api/page-preview/{slug...}",
    "GET /pages", "GET /api/pages-list", "GET /api/pages",
    "GET /search", "GET /api/search",
    "GET /history/{slug...}", "GET /revision/{rest...}", "GET /diff/{rest...}",
    "GET /_assets/{path...}", "GET /api/version",
}
# And besides, where visitors edit.
EDITS = {
    "GET /edit/{slug...}", "POST /edit/{slug...}", "POST /api/save/{slug...}",
    "GET /new", "POST /new", "POST /api/preview", "POST /api/toggle-checkbox/{slug...}",
}
# What needs no login in any wiki (authExempt): the login's own pages, the
# browser code, the healthcheck, /mcp and OAuth's calls, which want a token.
NO_LOGIN = {
    "GET /login", "POST /login", "POST /logout", "GET /setup", "POST /setup", "GET /signup", "POST /signup",
    "GET /welcome/{token}", "POST /welcome/{token}", "GET /api/health", "GET /public/{path...}", "/mcp",
    "GET /.well-known/oauth-protected-resource", "GET /.well-known/oauth-protected-resource/mcp",
    "GET /.well-known/oauth-authorization-server", "POST /oauth/register", "POST /oauth/token",
}


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout.strip()


def task(id, title, tags):
    return {"id": id, "title": title, "description": f"{title} for [[garden]]\n\n- [ ] Step", "tags": tags,
            "start_date": None, "due_date": "2026-01-01", "created_at": "2026-01-01T10:00:00", "closed_at": None,
            "wont_do": False, "keep": False, "last_done": None, "sort_order": None, "recurrence": None,
            "spawned_from": None, "history": [],
            "comments": [{"id": "c0ffee01", "at": "2026-01-02T10:00", "by": "alice", "text": title}]}


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    """A wiki with users: shared pages (one with a task list and a task
    reference, an old top-level note), an upload, a shared task and a list;
    alice's private page and private task."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    w = git_wiki_dir
    (w / "index.md").write_text("# Home\n\nWelcome. See [[garden]].\n")
    (w / "garden.md").write_text(
        f"# Garden\n\nBeds and paths.\n\n- [ ] water\n\n[[tasks: list=shop pin=1]]\n\nSee [[task:{TASK_ID[:8]}]] and "
        "[[private/alice/diary]].\n\n## Later\n\nMore.\n")
    (w / "2026-01-01_10-00-00_old-note.md").write_text("# Old note\n\nFrom before users.\n")
    (w / "wiki-guide.md").write_text("# Guide\n\nHow this wiki is kept.\n")
    (w / "_assets").mkdir(exist_ok=True)
    (w / "_assets/plan.png").write_bytes(b"png")
    (w / "private/alice").mkdir(parents=True)
    (w / "private/alice/diary.md").write_text(f"# Diary {SECRET}\n\n{SECRET} about the [[garden]].\n")
    (w / "private/alice/_tasks").mkdir()
    (w / "private/alice/_tasks/tasks.json").write_text(json.dumps([task(MINE_ID, f"Mine {SECRET}", ["shop"])]) + "\n")
    (w / "_tasks/tasks.json").write_text(json.dumps([task(TASK_ID, f"Buy {TASKWORD}", ["shop", TASKWORD])]) + "\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "A wiki")
    return w


@pytest.fixture(params=["view", "edit"])
def mode(request, wiki, monkeypatch):
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": request.param})
    return request.param


@pytest.fixture()
def view(wiki, monkeypatch):
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "view"})


@pytest.fixture()
def edit(wiki, monkeypatch):
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "edit"})


def to_login(resp):
    return resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=")


def text(resp):
    return resp.get_data(as_text=True)


# ── Every route ──────────────────────────────────────────────────────────────

def routes():
    source = "".join(p.read_text() for p in (REPO / "go").glob("*.go") if not p.name.endswith("_test.go"))
    return set(re.findall(r'HandleFunc\("([^"]+)"', source))


def request_for(route, wiki):
    """A request that reaches the route: its method and a path with every
    part filled in."""
    method, _, path = route.rpartition(" ")
    sha = git(wiki, "rev-parse", "HEAD")
    for part, value in (("{$}", ""), ("{slug...}", "garden"), ("{rest...}", f"garden/{sha}"), ("{path...}", "plan.png"),
                        ("{id}", TASK_ID), ("{cid}", "c0ffee01"), ("{name}", "bob"), ("{token}", "nosuchtoken"),
                        ("{user}", "alice"), ("{group}", "dev"), ("{file}", "photo.png"), ("{task}", TASK_ID)):
        path = path.replace(part, value)
    assert "{" not in path, route
    return method or "POST", path


def test_the_lists_are_the_servers():
    """VISITOR and EDITS are go/server.go's visitorRoutes and visitorEdits,
    and every route on a list exists."""
    source = (REPO / "go/server.go").read_text()
    for name, expected in (("visitorRoutes", VISITOR), ("visitorEdits", EDITS)):
        block = re.search(r"var %s = map\[string\]bool\{(.*?)\n\}" % name, source, re.S).group(1)
        assert set(re.findall(r'"([^"]+)": true', block)) == expected, name
    assert (VISITOR | EDITS | NO_LOGIN) - routes() == set(), "listed routes that don't exist"
    assert VISITOR & EDITS == set() and (VISITOR | EDITS) & NO_LOGIN == set()


def test_every_route_is_a_visitors_or_closed(client, wiki, mode):
    """Without a session, a route answers only if it is a visitor's (in this
    mode) or needs no login anywhere; every other sends to the login page."""
    open_now = VISITOR | (EDITS if mode == "edit" else set())
    for route in sorted(routes() - NO_LOGIN):
        method, path = request_for(route, wiki)
        resp = client.open(path, method=method, headers=HOST)
        if route in open_now:
            assert not to_login(resp), f"{route}: a visitor's, but it sends to the login page"
        else:
            assert to_login(resp), f"{route} answers a visitor with {resp.status_code}: list it, or close it"


def test_nothing_changes_for_a_visitor_of_a_wiki_to_read(client, wiki, view):
    """Every route asked as a visitor with a write's method: the wiki's
    files and its history stay as they were."""
    def state():
        # A task file as what it holds: Zone writes it in its own layout when it starts.
        return {p.relative_to(wiki).as_posix(): json.loads(p.read_bytes()) if p.suffix == ".json" else p.read_bytes()
                for p in wiki.rglob("*") if p.is_file() and not p.relative_to(wiki).as_posix().startswith((".git/", ".zone/"))}
    client.get("/page/index", headers=HOST)
    before, head = state(), git(wiki, "rev-parse", "HEAD")
    for route in sorted(routes() - NO_LOGIN):
        method, path = request_for(route, wiki)
        if method == "GET":
            continue
        for kwargs in ({"data": {"content": "# Changed\n", "slug": "made", "name": "made", "new_name": "moved", "idx": "0"}},
                       {"json": {"title": "Made", "ids": [TASK_ID], "text": "x", "completed": True}}):
            client.open(path, method=method, headers=HOST, **kwargs)
    assert state() == before and git(wiki, "rev-parse", "HEAD") == head


# ── What a visitor reads ─────────────────────────────────────────────────────

READS = [
    "/page/index", "/page/garden", "/page/2026-01-01_10-00-00_old-note", "/page/private/alice/diary", "/page/nosuchpage",
    "/api/page-preview/garden", "/api/page-preview/private/alice/diary",
    "/pages", "/api/pages", "/api/pages-list",
    f"/search?q={TASKWORD}", f"/search?q={SECRET}", "/search?q=garden", "/search?q=shop",
    f"/api/search?q={TASKWORD}", f"/api/search?q={SECRET}", "/api/search?q=garden", "/api/search?q=buy",
    "/history/garden", "/history/private/alice/diary", "/revision/garden/{sha}", "/diff/garden/{sha}",
    "/revision/private/alice/diary/{sha}", "/diff/private/alice/diary/{sha}",
    "/revision/_tasks/tasks.json/{sha}", "/diff/_tasks/tasks/{sha}", "/history/_tasks/tasks",
    "/_assets/plan.png", "/api/version", "/",
]


@pytest.mark.parametrize("path", READS)
def test_a_visitor_reads_no_task_and_no_private_page(client, wiki, mode, path):
    resp = client.get(path.replace("{sha}", git(wiki, "rev-parse", "HEAD")), headers=HOST)
    body = text(resp)
    # The query a search echoes is the visitor's own word, not the wiki's.
    for word in (SECRET, TASKWORD):
        echoed = f"q={word}" in path
        assert body.count(word) == (body.count(word) if echoed else 0), f"{word} in {path}"
        if echoed:
            assert "Buy " + TASKWORD not in body and "Diary" not in body and "Mine" not in body
    assert TASK_ID not in body and MINE_ID not in body and "Buy" not in body
    if "private/alice" in path or "_tasks" in path:
        assert resp.status_code in (400, 404), path


def test_a_page_without_its_tasks(client, wiki, mode):
    page = text(client.get("/page/garden", headers=HOST))
    assert "Beds and paths." in page and "Later" in page
    assert "tasks-app" not in page and "tasks-initial" not in page and "[[tasks:" not in page   # no task list
    assert "Linked tasks" not in page and 'class="task-ref task-ref-missing"' in page   # the reference names nothing
    assert 'data-visitor="1"' in page
    assert '<a class="btn top-signin" href="/login?next=%2Fpage%2Fgarden">Sign in</a>' in page
    assert 'data-module="wiki"' in page
    for gone in ('data-module="tasks"', 'data-module="notes"', 'data-module="private"', "uncommitted-badge",
                 'action="/logout"', "signout-user", "module-menu", 'data-users="1"', "data-tasks-tags"):
        assert gone not in page, gone
    # The old note is a page like any other: nothing to move it with.
    note = text(client.get("/page/2026-01-01_10-00-00_old-note", headers=HOST))
    assert "From before users." in note and "Move to wiki" not in note
    # The page list has the pages, not the views that want an account.
    slugs = {p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()}
    assert {"index", "garden"} <= slugs and not {"/tasks", "/notes", "_deleted"} & slugs
    assert not any(s.startswith("private/") for s in slugs)


def test_a_wiki_to_read_offers_no_edit(client, wiki, view):
    page = text(client.get("/page/garden", headers=HOST))
    assert 'href="/edit/garden"' not in page and "read-only" in page and 'data-action="rename"' not in page
    assert to_login(client.get("/edit/garden", headers=HOST))
    assert to_login(client.post("/api/save/garden", data={"content": "# Mine now\n"}, headers=HOST))
    assert "Beds and paths." in (wiki / "garden.md").read_text()


# ── What a visitor writes, where visitors edit ───────────────────────────────

def test_a_visitor_edits_and_makes_pages_as_anonymous(client, wiki, edit):
    page = text(client.get("/page/garden", headers=HOST))
    assert 'href="/edit/garden"' in page
    assert 'data-action="rename"' not in page and 'data-action="delete"' not in page
    editor = text(client.get("/edit/garden", headers=HOST))
    assert 'id="editor-textarea"' in editor and "Beds and paths." in editor
    for gone in ('id="rename-toggle"', 'id="delete-btn"', "tasks-app", "task-settings-modal"):
        assert gone not in editor, gone
    # An auto-save, then Done: committed, by anonymous.
    assert client.post("/api/save/garden", data={"content": "# Garden\n\nNew beds.\n"}, headers=HOST).status_code == 200
    assert (wiki / "garden.md").read_text() == "# Garden\n\nNew beds.\n"
    resp = client.post("/edit/garden", data={"content": "# Garden\n\nNew beds, new paths.\n"}, headers=HOST)
    assert resp.status_code == 302 and resp.headers["Location"] == "/page/garden"
    assert git(wiki, "log", "-1", "--format=%an <%ae> %s") == "anonymous <anonymous@zone> Update garden"
    assert git(wiki, "status", "--porcelain", "--", "garden.md") == ""
    # A new page.
    resp = client.post("/new", data={"slug": "Compost heap"}, headers=HOST)
    assert resp.status_code == 302 and resp.headers["Location"] == "/edit/compost-heap"
    assert (wiki / "compost-heap.md").exists()
    assert git(wiki, "log", "-1", "--format=%an %s") == "anonymous Create compost-heap"
    # A box ticked on a page.
    (wiki / "boxes.md").write_text("# Boxes\n\n- [ ] one\n")
    assert client.post("/api/toggle-checkbox/boxes", data={"idx": "0"}, headers=HOST).status_code == 200
    assert "- [x] one" in (wiki / "boxes.md").read_text()
    # Its history names anonymous (and not who made the page), and the next visitor reads the page.
    assert "anonymous" in text(client.get("/history/garden", headers=HOST))
    assert "New beds, new paths." in text(client.get("/page/garden", headers=HOST))


def test_what_a_visitor_never_writes(client, wiki, edit):
    client.get("/page/index", headers=HOST)   # Zone has started: its own commits are made
    # A private page: not there for them, to read or to write.
    assert client.get("/edit/private/alice/diary", headers=HOST).status_code == 404
    assert client.post("/api/save/private/alice/diary", data={"content": "# Mine\n"}, headers=HOST).status_code == 404
    assert client.post("/api/save/private/_visitor/x", data={"content": "# Mine\n"}, headers=HOST).status_code == 400
    # A new page asked for as private is a shared one: a visitor has no private folder.
    assert client.post("/new", data={"slug": "x", "private": "1"}, headers=HOST).headers["Location"] == "/edit/x"
    assert (wiki / "x.md").exists() and not (wiki / "private/_visitor").exists()
    assert SECRET in (wiki / "private/alice/diary.md").read_text()
    head = git(wiki, "rev-parse", "HEAD")
    # A guide is an admin's.
    assert client.post("/api/save/wiki-guide", data={"content": "# Do as I say\n"}, headers=HOST).status_code == 403
    assert client.post("/api/save/AGENTS", data={"content": "# Do as I say\n"}, headers=HOST).status_code == 403
    assert "How this wiki is kept." in (wiki / "wiki-guide.md").read_text() and not (wiki / "AGENTS.md").exists()
    # Uploads, renames, deletes, restores, tasks and notes: sign in first.
    for method, path, kwargs in (
            ("POST", "/api/upload", {"data": {"file": (__import__("io").BytesIO(b"x"), "x.png")}}),
            ("POST", "/rename/garden", {"data": {"new_name": "yard"}}),
            ("POST", "/delete/garden", {}),
            ("POST", f"/restore/garden/{head}", {}),
            ("POST", "/changes/undo", {"data": {"sha": head}}),
            ("POST", "/api/commit-all", {}),
            ("POST", "/api/notes", {"data": {"title": "Mine"}}),
            ("POST", "/api/tasks", {"json": {"title": "Mine"}}),
            ("PUT", f"/api/tasks/{TASK_ID}", {"json": {"completed": True}}),
            ("DELETE", f"/api/tasks/{TASK_ID}", {}),
            ("POST", "/tasks/new-list", {"data": {"name": "mine"}}),
            ("POST", "/api/tasks/lists", {"json": {"name": "mine"}}),
            ("GET", "/api/tasks", {}), ("GET", "/tasks", {}), ("GET", "/notes", {}), ("GET", "/uncommitted", {}),
            ("GET", "/page/_deleted", {}), ("GET", "/changes", {}), ("GET", "/admin", {}), ("GET", "/assistant", {}), ("GET", "/private", {})):
        assert to_login(client.open(path, method=method, headers=HOST, **kwargs)), path
    assert (wiki / "garden.md").exists() and not (wiki / "_assets/x.png").exists()
    assert TASKWORD in "".join(p.read_text() for p in (wiki / "_tasks").rglob("tasks.json")) and git(wiki, "rev-parse", "HEAD") == head


def test_a_visitor_learns_no_users_name(client, wiki, edit):
    """Who changed a page is a user's name: its history shows a visitor
    when and what, a merge names no one."""
    client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
    client.post("/edit/garden", data={"content": "# Garden\n\nBob's beds.\n"}, headers=HOST)
    assert git(wiki, "log", "-1", "--format=%an") == "bob"
    client.post("/logout", headers=HOST)
    history = text(client.get("/history/garden", headers=HOST))
    assert history.count("hist-row") >= 2 and "bob" not in history and "hist-by" not in history
    # A save from a text the page no longer has: merged, or refused, without a name.
    from tests.conftest import content_hash
    resp = client.post("/api/save/garden", data={"content": "# Garden\n\nMine.\n", "merge": "1",
                                                 "file_hash": content_hash("# Garden\n\nOld.\n")}, headers=HOST)
    assert "bob" not in text(resp)
    # Their own change says anonymous.
    client.post("/edit/garden", data={"content": "# Garden\n\nA visitor's beds.\n"}, headers=HOST)
    assert "anonymous" in text(client.get("/history/garden", headers=HOST))


def test_no_draft_is_kept_for_a_visitor(client, wiki, edit):
    """Every visitor is the same one: a draft left for "them" would be the
    next visitor's, and would fill Zone's folder."""
    for extra in ({"keep_left": "1"}, {"leaving": "1", "file_hash": "0" * 64}):
        client.post("/api/save/garden", data={"content": "# Planted\n" * 50, **extra}, headers=HOST)
    assert not (wiki / ".zone/drafts/_visitor").exists()
    assert "left-draft" not in text(client.get("/edit/garden", headers=HOST))


def test_a_guide_by_another_spelling_is_a_guide(client, wiki, edit):
    assert client.post("/api/save/Wiki-Guide", data={"content": "# Do as I say\n"}, headers=HOST).status_code == 403
    assert not (wiki / "Wiki-Guide.md").exists() and "How this wiki is kept." in (wiki / "wiki-guide.md").read_text()


# ── Around it ────────────────────────────────────────────────────────────────

def test_without_the_setting_everyone_signs_in(client, wiki):
    for path in ("/page/garden", "/search?q=garden", "/api/pages", "/_assets/plan.png", "/"):
        assert to_login(client.get(path, headers=HOST)), path


def test_off_says_so_too(client, wiki, monkeypatch):
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "off"})
    assert to_login(client.get("/page/garden", headers=HOST))


def test_a_wiki_without_users_is_not_public(client, git_wiki_dir, monkeypatch):
    """With one password for the wiki there is nobody to tell a visitor from:
    the setting does nothing."""
    monkeypatch.setattr(settings, "PASSWORD", "pw")
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "edit"})
    assert to_login(client.get("/page/index", headers=HOST))
    assert to_login(client.post("/api/save/index", data={"content": "# Mine\n"}, headers=HOST))


def test_signed_in_nothing_changes(client, wiki, mode):
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    page = text(client.get("/page/garden", headers=HOST))
    assert "tasks-app" in page and 'data-visitor' not in page and 'data-module="tasks"' in page and "top-signin" not in page
    assert SECRET in text(client.get("/page/private/alice/diary", headers=HOST))
    tasks = client.get("/api/tasks", headers=HOST).get_json()
    assert {t["id"] for t in tasks} == {TASK_ID, MINE_ID}
    assert client.post("/rename/garden", data={"new_name": "yard"}, headers=HOST).status_code == 302
    assert (wiki / "yard.md").exists()
    assert git(wiki, "log", "-1", "--format=%an") == "alice"
    # Signed out again: a visitor.
    client.post("/logout", headers=HOST)
    assert 'data-visitor="1"' in text(client.get("/page/yard", headers=HOST))
    assert to_login(client.get("/api/tasks", headers=HOST))


def test_mcp_wants_its_token_as_ever(client, wiki, mode):
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_tasks", "arguments": {}}}
    assert client.post("/mcp", json=call, headers=HOST).status_code == 401
    resp = client.post("/mcp", json=call, headers={**HOST, "Authorization": "Bearer alice-token"})
    assert resp.status_code == 200 and TASKWORD in text(resp)
    # A file by its token still works; without one, an upload is a visitor's: none.
    assert to_login(client.post("/api/upload", data={"file": (__import__("io").BytesIO(b"x"), "y.png")}, headers=HOST))


def test_the_login_page_comes_back_to_the_page(client, wiki, view):
    resp = client.post("/login?next=%2Fpage%2Fgarden", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
    assert resp.status_code == 302 and resp.headers["Location"] == "/page/garden"
