"""Private pages and tasks don't leak (docs/spec/users.md): with alice's
private page, upload, draft and tasks in the wiki, bob, signed in or
through his MCP token, must not see a word of them anywhere, nor change
them.

Every route Zone registers is either probed here or listed as exempt
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
from tests.conftest import HOST, content_hash

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
COMMENT_ID = "c0ffee01"


def private_task(id, title, completed_at=None):
    return {"id": id, "title": f"{title} {SECRET} {QUERY}", "description": f"{QUERY} {SECRET} for [[plans]]\n\n- [ ] Step",
            "tags": [f"{QUERY}-{SECRET}", "secretlist"], "start_date": None, "due_date": "2026-01-01",
            "created_at": "2026-01-01T10:00:00", "completed_at": completed_at, "wont_do": False, "keep": False,
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
    (w / "plans.md").write_text(f"# Plans\n\nSee [[{DIARY}]] and [[{DIARY}|my diary]].\n\n- [[task:{TASK_ID[:8]}]]\n")
    # Her tasks: her own home and a list of hers, and her tasks.
    (w / "private/alice/deck.md").write_text(f"# My tasks {SECRET}\n\n[[tasks: pin=1 list=secretlist]]\n")
    tasks = w / "private/alice/_tasks"
    tasks.mkdir(parents=True)
    this_year = __import__("datetime").date.today().year
    (tasks / "tasks.json").write_text(json.dumps([private_task(TASK_ID, "Task")], indent=2) + "\n")
    (tasks / "completed.json").write_text(
        json.dumps([private_task(DONE_ID, "Done", f"{this_year}-01-05T10:00:00")], indent=2) + "\n")
    (tasks / "completed-2001.json").write_text(
        json.dumps([private_task(OLD_ID, "Old", "2001-01-05T10:00:00")], indent=2) + "\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "Alice's pages")
    git(w, "rm", "-q", "private/alice/old.md")
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
    ("GET /page/{slug...}", "GET", "/page/private/alice/old", {}),   # a deleted page offers restore
    ("GET /uncommitted", "GET", "/uncommitted", {}),
    ("GET /api/uncommitted", "GET", "/api/uncommitted", {}),
    ("GET /new", "GET", "/new", {}),
    ("GET /_assets/{path...}", "GET", "/_assets/private/alice/photo.png", {}),
    ("GET /private/{user}/_assets/{file}", "GET", "/private/alice/_assets/photo.png", {}),
    ("GET /tasks", "GET", "/tasks", {}),
    ("GET /api/tasks", "GET", "/api/tasks", {}),
    ("GET /api/tasks/completed", "GET", "/api/tasks/completed", {}),
    ("GET /api/tasks/completed", "GET", "/api/tasks/completed", {"query_string": {"year": "2001"}}),
    ("GET /api/tasks/completed-count", "GET", "/api/tasks/completed-count", {}),
    ("GET /api/tasks/completed-years", "GET", "/api/tasks/completed-years", {}),
    ("GET /tasks", "GET", "/tasks", {"query_string": {"list": "secretlist"}}),
    ("GET /api/tasks/due-count", "GET", "/api/tasks/due-count", {}),
    ("GET /api/tasks/headless", "GET", "/api/tasks/headless", {}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {}),
    ("GET /api/tasks/export", "GET", "/api/tasks/export", {"query_string": {"list": "secretlist"}}),
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
    assert not any(id in body and id not in str(kwargs) for id in (TASK_ID, DONE_ID, OLD_ID)), "alice's task"
    assert "secretlist" not in body or "secretlist" in str(kwargs), "alice's list"
    assert "2001" not in body or "2001" in str(kwargs), "the year of alice's archive"
    if path.startswith(f"/page/{DIARY}") or "/private/alice/" in path:
        assert resp.status_code == 404


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
    assert "2001" in client.get("/api/tasks/completed-years", headers=HOST).get_json()["years"]
    # Her Tasks home is hers: the Tasks link goes to its list.
    assert 'href="/tasks?list=secretlist"' in client.get("/page/index", headers=HOST).data.decode()


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
    private/alice would make a shared page private, so it is refused and
    nothing moves."""
    note = "2026-01-01_10-00-00_old"
    (wiki / f"{note}.md").write_text("# Old\n")
    (wiki / "log.md").write_text(f"# Log\n\nSee [[{note}]].\n")
    resp = alice.post(f"/promote/{note}", data={"folder": "private/alice", "name": "old"}, headers=HOST)
    assert "promote_error=" in resp.headers["Location"] and "stays shared" in unquote(resp.headers["Location"])
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
    assert upload(bob, DIARY, "b.png").get_json()["url"] == "/_assets/b.png"   # not into alice's folder


def test_assets_in_other_folders_are_not_served(alice, wiki):
    (wiki / "_assets/sub").mkdir()
    (wiki / "_assets/sub/a.png").write_bytes(b"x")
    (wiki / "private/alice/_assets/deeper").mkdir(parents=True)
    (wiki / "private/alice/_assets/deeper/a.png").write_bytes(b"x")
    (wiki / "private/alice/_assets/a.png").write_bytes(b"x")
    for path in ("/_assets/sub/a.png", "/_assets/private/alice/a.png", "/private/alice/_assets/deeper/a.png",
                 "/private/alice/_assets/"):
        assert alice.get(path, headers=HOST).status_code == 404, path


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
    alice.post(f"/rename/{DIARY}", data={"new_name": "diary"}, headers=HOST)
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
    for name in ("p*/alice/ol*", ":(glob)private/alice/*"):
        resp = bob.get(f"/page/{name}", headers=HOST)
        assert resp.status_code == 302 and "/edit/" in resp.headers["Location"], name


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


def test_a_new_task_in_a_private_place_is_private(alice, wiki):
    """An embed on a page in her folder, and New task / c t with her tasks
    home there, start a new task private; /tasks doesn't."""
    assert 'data-init-private="1"' in alice.get("/page/private/alice/deck", headers=HOST).data.decode()
    index = alice.get("/page/index", headers=HOST).data.decode()
    assert 'data-tasks-private="1"' in index
    tasks = alice.get("/tasks", headers=HOST).data.decode()
    assert 'id="task-edit-private"' in tasks and "data-init-private" not in tasks
    assert 'data-private-folder="private/alice"' in tasks
    assert 'data-init-private="1"' in alice.get("/api/tasks/headless", headers=HOST).data.decode()
    sign_in(alice, "bob")
    bob = alice
    index = bob.get("/page/index", headers=HOST).data.decode()
    assert "data-tasks-private" not in index and 'id="task-edit-private"' in bob.get("/tasks", headers=HOST).data.decode()


def test_no_private_box_without_users(client, wiki_dir):
    tasks = client.get("/tasks", headers=HOST).data.decode()
    assert "task-edit-private" not in tasks and "data-private-folder" not in tasks
    assert "private" not in client.post("/api/tasks", json={"title": "x"}, headers=HOST).get_json()
    resp = client.post("/api/tasks", json={"title": "y", "private": True}, headers=HOST)
    assert resp.status_code == 400


def test_a_task_moves_between_her_files_and_the_shared_ones(alice, wiki):
    task = alice.post("/api/tasks", json={"title": "Gift", "private": True}, headers=HOST).get_json()
    assert task["private"] is True
    mine, shared = wiki / "private/alice/_tasks/tasks.json", wiki / "_tasks/tasks.json"
    assert task["id"] in mine.read_text() and task["id"] not in shared.read_text()
    assert alice.put(f"/api/tasks/{task['id']}", json={"private": False}, headers=HOST).get_json()["private"] is False
    assert task["id"] in shared.read_text() and task["id"] not in mine.read_text()
    # One commit, both files, in her name.
    assert git(wiki, "show", "--name-only", "--format=%an", "HEAD").split() == [
        "alice", "_tasks/tasks.json", "private/alice/_tasks/tasks.json"]
    sign_in(alice, "bob")
    assert task["id"] in [t["id"] for t in alice.get("/api/tasks", headers=HOST).get_json()]


def test_a_tag_does_not_make_a_task_private(alice, wiki):
    """secretlist is declared in her folder, but where a task is kept is
    not decided by its tags: it is private only when the request says so."""
    assert "data-private-lists" not in alice.get("/tasks", headers=HOST).data.decode()
    assert alice.post("/api/tasks", json={"title": "A", "tags": ["SecretList"]}, headers=HOST).get_json()["private"] is False
    own = alice.post("/api/tasks", json={"title": "B", "tags": ["secretlist"], "private": True}, headers=HOST)
    assert own.get_json()["private"] is True
    c = alice.post("/api/tasks", json={"title": "C", "private": False}, headers=HOST).get_json()
    tagged = alice.post("/api/tasks/batch-tag", json={"ids": [c["id"]], "add": ["secretlist"]}, headers=HOST)
    assert tagged.get_json()[0]["private"] is False


def test_a_shared_task_cannot_be_made_private(alice, wiki):
    """Others have seen it: shared stays shared, by every route and tool.
    A private task, though, can be shared (and not back)."""
    sign_in(alice, "bob")
    bobs = alice.post("/api/tasks", json={"title": "Bob's"}, headers=HOST).get_json()
    sign_in(alice, "alice")
    for resp in (alice.put(f"/api/tasks/{bobs['id']}", json={"private": True}, headers=HOST),
                 alice.post("/api/tasks/batch-update", json={"tasks": [{"id": bobs["id"], "private": True}]}, headers=HOST)):
        assert resp.status_code == 400 and "stays shared" in resp.get_json()["error"]
    assert "stays shared" in mcp(alice, "update_task", token="alice-token", id=bobs["id"], private=True)
    assert bobs["id"] in (wiki / "_tasks/tasks.json").read_text()
    assert not (wiki / "private/alice/_tasks/tasks.json").exists() or bobs["id"] not in (wiki / "private/alice/_tasks/tasks.json").read_text()
    # Her own private task goes the other way; unchanged (false on a shared one) is no error.
    mine = alice.post("/api/tasks", json={"title": "Mine", "private": True}, headers=HOST).get_json()
    assert alice.put(f"/api/tasks/{bobs['id']}", json={"private": False}, headers=HOST).status_code == 200
    assert alice.put(f"/api/tasks/{mine['id']}", json={"private": False}, headers=HOST).get_json()["private"] is False
    assert alice.put(f"/api/tasks/{mine['id']}", json={"private": True}, headers=HOST).status_code == 400


def test_comments_are_everyones_to_change(alice, wiki):
    """A shared task is everyone's, its comments too: alice may edit and
    delete bob's, and an edit keeps the author."""
    task = alice.post("/api/tasks", json={"title": "Shared", "private": False}, headers=HOST).get_json()
    url = f"/api/tasks/{task['id']}/comments"
    sign_in(alice, "bob")
    his = alice.post(url, json={"text": "His"}, headers=HOST).get_json()["comments"][-1]
    sign_in(alice, "alice")
    assert his["by"] == "bob"
    edited = alice.put(f"{url}/{his['id']}", json={"text": "Changed"}, headers=HOST).get_json()
    assert [(c["text"], c["by"]) for c in edited["comments"]] == [("Changed", "bob")]
    assert alice.delete(f"{url}/{his['id']}", headers=HOST).get_json()["comments"] == []


def test_sharing_a_task_is_in_its_history(alice, wiki):
    """Making a private task shared is an event in its History, by who did it."""
    task = alice.post("/api/tasks", json={"title": "Own", "private": True}, headers=HOST).get_json()
    url = f"/api/tasks/{task['id']}"
    last = alice.put(url, json={"private": False}, headers=HOST).get_json()["history"][-1]
    assert (last["event"], last["by"]) == ("shared", "alice")
    # Unchanged is no event, and a new private task has none to start with.
    assert alice.put(url, json={"private": False}, headers=HOST).get_json()["history"][-1]["event"] == "shared"
    new = alice.post("/api/tasks", json={"title": "New", "private": True}, headers=HOST).get_json()
    assert [e["event"] for e in new["history"]] == ["created"]


def test_undoing_a_delete_keeps_a_task_private(alice, wiki):
    snap = next(t for t in alice.get("/api/tasks", headers=HOST).get_json() if t["id"] == TASK_ID)
    assert alice.delete(f"/api/tasks/{TASK_ID}", headers=HOST).status_code == 204
    restored = alice.post("/api/tasks/restore", json={"tasks": [snap]}, headers=HOST).get_json()
    assert [t["private"] for t in restored] == [True]
    assert TASK_ID in (wiki / "private/alice/_tasks/tasks.json").read_text()


def test_alices_assistant_makes_private_tasks(client, wiki):
    """Her Tasks home is in her folder: create_task makes private tasks
    unless told otherwise, as c t does."""
    text = mcp(client, "create_task", token="alice-token", title="Call mum")
    assert "· private ·" in text
    text = mcp(client, "create_task", token="alice-token", title="Team lunch", private=False)
    assert "private" not in text.split("\n")[3]
    assert "Team lunch" in mcp(client, "search_tasks", query="lunch")
    assert "Call mum" not in mcp(client, "search_tasks", query="mum")


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
    ("search_tasks", {"query": QUERY}),
    ("read_guides", {"slug": DIARY}),
    ("read_guides", {"slug": "private/alice/"}),
    ("search_tasks", {"query": QUERY, "status": "all"}),
    ("search_tasks", {"tag": "secretlist", "status": "all"}),
    ("replace_in_pages", {"find": QUERY, "replace": "x"}),       # a dry run
    ("read_file", {"url": "/private/alice/_assets/photo.png"}),
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
    assert "[bobs.txt](/_assets/bobs.txt)" in mcp(client, "upload_file", filename="bobs.txt", text="his", page=DIARY)
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


# ── Writes ───────────────────────────────────────────────────────────────────

WRITES = [
    ("POST /edit/{slug...}", "POST", f"/edit/{DIARY}", {"data": {"content": "# Bob was here\n"}}),
    ("POST /api/save/{slug...}", "POST", f"/api/save/{DIARY}", {"data": {"content": "# Bob was here\n"}}),
    ("POST /new", "POST", "/new", {"data": {"slug": "private/alice/bobs"}}),
    ("POST /rename/{slug...}", "POST", f"/rename/{DIARY}", {"data": {"new_name": "stolen"}}),
    ("POST /tasks/rename", "POST", "/tasks/rename", {"data": {"tag": "secretlist", "new_name": "stolen"}}),
    ("POST /tasks/rename", "POST", "/tasks/rename", {"data": {"tag": f"{QUERY}-{SECRET}", "new_name": "stolen"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "private/alice/plans"}}),
    ("POST /delete/{slug...}", "POST", f"/delete/{DIARY}", {}),
    ("POST /restore/{rest...}", "POST", "/restore/private/alice/old/{old}", {}),
    ("POST /promote/{slug...}", "POST", f"/promote/{NOTE}", {"data": {"folder": "", "name": "stolen"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "private/alice", "name": "x"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "", "name": "x", "link_from": DIARY}}),
    ("POST /append/{slug...}", "POST", f"/append/{NOTE}", {"data": {"page": "plans", "heading": "Stolen"}}),
    ("POST /append/{slug...}", "POST", "/append/{bobs}", {"data": {"page": DIARY, "heading": "Bob's"}}),
    ("POST /append/{slug...}", "POST", "/append/{bobs}", {"data": {"page": SNEAK, "heading": "Bob's"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "", "name": "x", "link_from": SNEAK}}),
    ("POST /api/toggle-checkbox/{slug...}", "POST", f"/api/toggle-checkbox/{DIARY}", {"data": {"idx": "0"}}),
    ("POST /api/commit-file", "POST", "/api/commit-file", {"json": {"filepath": "private/alice/draft.md"}}),
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
     {"json": {"page": "private/alice/deck", "index": 0, "was": "pin=1 list=secretlist", "set": {"view": "open", "pin": ""}}}),
    # alice's tasks, by id; bob's own private task goes into his files.
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Bob's", "private": True}}),
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
    ("POST /api/tasks/reorder", "POST", "/api/tasks/reorder", {"json": {"order": [TASK_ID], "updates": [{"id": TASK_ID, "sort_order": 1}]}}),
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
    shared = "".join(p.read_text() for p in (wiki / "_tasks").glob("*.json"))
    files["shared tasks has her ids"] = str(TASK_ID in shared or DONE_ID in shared)
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
    ("file_note", {"slug": NOTE, "name": "stolen"}),
    ("file_note", {"slug": "{bobs}", "page": DIARY}),
    ("file_note", {"slug": "{bobs}", "name": "x", "folder": "private/alice"}),
    ("create_task", {"title": "Bob's", "private": True}),
    ("update_task", {"id": TASK_ID[:8], "title": "Bob was here"}),
    ("update_task", {"id": TASK_ID, "status": "wont_do"}),
    ("update_task", {"id": DONE_ID, "status": "open", "comment": "Bob's"}),
    ("update_task", {"ids": [TASK_ID, OLD_ID], "add_tags": ["bob"]}),
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
    assert bob.post("/api/tasks", json={"title": "Shared", "tags": ["secretlist"]}, headers=HOST).status_code < 300
    out = mcp(bob, "rename_task_tag", tag="secretlist", new_tag="renamed")
    assert out == "Renamed tag #secretlist to #renamed, in one commit: 1 task."
    assert "renamed" in _private_tags(wiki) and "secretlist" not in _private_tags(wiki)
    assert "list=renamed" in (wiki / "private/alice/deck.md").read_text()


def test_a_tag_only_she_sees_stays_hers(alice, wiki):
    """A tag on nothing shared is hers: bob's private tag of that name is another tag,
    and nothing about it shows in the history bob reads."""
    bobs = wiki / "private/bob/_tasks"
    bobs.mkdir(parents=True)
    (bobs / "tasks.json").write_text(json.dumps([{**private_task(TASK_ID[:-1] + "9", "Bob's"), "tags": ["secretlist"]}]))
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
    """A shared page stays shared: renaming it, or a folder of shared pages,
    into alice's folder is refused and nothing moves, linked or not."""
    (wiki / "toy.md").write_text("# Toy\n")
    (wiki / "games.md").write_text("# Games\n\nSee [[toy]].\n")
    out = mcp(alice, "rename_page", token="alice-token", slug="toy", new_slug="private/alice/toy")
    assert "Not renamed" in out and "stays shared" in out
    assert (wiki / "toy.md").exists() and "[[toy]]" in (wiki / "games.md").read_text()
    (wiki / "stuff").mkdir()
    (wiki / "stuff/a.md").write_text("# A\n")
    out = mcp(alice, "rename_page", token="alice-token", slug="stuff/", new_slug="private/alice/stuff")
    assert "Nothing was moved" in out and "stays shared" in out and (wiki / "stuff/a.md").exists()
    # Her own private page may go out, and back within her folder.
    assert "Renamed page" in mcp(alice, "rename_page", token="alice-token", slug=DIARY, new_slug="private/alice/journal")


def test_moving_the_private_folder_is_refused(client, wiki):
    """With a note of bob's in private/, private/ is a folder he sees, but
    it holds alice's too: nothing moves."""
    mcp(client, "write_page", note=True, slug="Bobs", content="Bob's\n")
    before = _alices_files(wiki)
    assert "Nothing was moved" in mcp(client, "rename_page", slug="private/", new_slug="stolen")
    assert _alices_files(wiki) == before and not (wiki / "stolen").exists()


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
    "GET /admin", "GET /admin/users", "POST /admin/users/{name}/role", "POST /admin/users/{name}/disable", "POST /admin/users/{name}/enable",
    "POST /admin/users/{name}/remove", "POST /admin/users/invite", "POST /admin/users/{name}/reset", "POST /admin/users/{name}/revoke",
    "GET /welcome/{token}", "POST /welcome/{token}",   # a link's own page: sets one password (test_admin.py)
    "GET /signup", "POST /signup",  # asks for an account by mail, signed out (test_signup.py)
    "GET /assistant", "POST /assistant/token", "POST /assistant/revoke",   # the caller's own MCP token, no page (test_assistant.py)
    "GET /password", "POST /password",   # the caller's own password, no page (test_password.py)
    # OAuth: the caller's own connections, no page (test_oauth.py)
    "GET /.well-known/oauth-protected-resource", "GET /.well-known/oauth-protected-resource/mcp",
    "GET /.well-known/oauth-authorization-server", "POST /oauth/register", "GET /oauth/authorize",
    "POST /oauth/authorize", "POST /oauth/token", "POST /assistant/connections/{id}/revoke",
    "GET /setup", "POST /setup",    # the first-run page: only while the wiki has no login (test_setup.py)
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
    """The same for text on disk that Zone never saw (written by hand, or before a
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
    out = alice.post("/api/tasks/import", json={"file": {"tasks": mine}, "list": "shared"}, headers=HOST).get_json()
    assert out == {"added": 1, "skipped": 0, "ids": [TASK_ID]}
    assert TASK_ID in (wiki / "private/alice/_tasks/tasks.json").read_text()
    assert ((wiki / "_tasks/tasks.json").read_text() if (wiki / "_tasks/tasks.json").exists() else "") == shared_before
