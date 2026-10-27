"""Private pages don't leak (docs/spec/users.md): with alice's private page,
upload and draft in the wiki, bob, signed in or through his MCP token, must
not see a word of them anywhere, nor change them.

Every route Leaf registers is either probed here or listed as exempt
(test_every_route_is_covered), so a new route gets checked too. Probes for
what a later phase of docs/plans/multi-user.md closes are marked xfail with
that phase, strictly: once it passes, the mark must go."""

import re
import subprocess
from io import BytesIO
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import HOST

REPO = Path(__file__).resolve().parent.parent

# In alice's private pages only: no response to bob may contain it. The
# requests search for QUERY, which a response may echo.
SECRET = "quokkaflute"
QUERY = "zebracorn"
DIARY = "private/alice/diary"
NOTE = "private/alice/2026-10-27_09-00-00_idea"   # her scratch note

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token")}


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout.strip()


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    """alice's private pages, committed (the diary, a scratch note; an old
    page, deleted) and not (a draft); a shared page linking to the diary,
    which links back."""
    monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))
    w = git_wiki_dir
    (w / "private" / "alice").mkdir(parents=True)
    (w / "_assets" / "private" / "alice").mkdir(parents=True)
    (w / f"{DIARY}.md").write_text(
        f"---\ntags: [{QUERY}-{SECRET}]\n---\n# Diary {SECRET}\n\n{QUERY} {SECRET} with [[plans]].\n\n- [ ] Call\n")
    (w / "private/alice/old.md").write_text(f"# Old {SECRET}\n\n{QUERY} {SECRET}\n")
    (w / f"{NOTE}.md").write_text(f"# Idea {SECRET}\n\n{QUERY} {SECRET}\n")
    (w / "_assets/private/alice/photo.png").write_bytes(SECRET.encode())
    (w / "plans.md").write_text(f"# Plans\n\nSee [[{DIARY}]] and [[{DIARY}|my diary]].\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "Alice's pages")
    git(w, "rm", "-q", "private/alice/old.md")
    git(w, "commit", "-q", "-m", "Delete private/alice/old")
    (w / "private/alice/draft.md").write_text(f"# Draft {SECRET}\n\n{QUERY} {SECRET}\n")
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


def later(phase, why=""):
    return pytest.mark.xfail(strict=True, reason=f"phase {phase} of docs/plans/multi-user.md {why}".strip())


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
    ("GET /page/_scratch", "GET", "/page/_scratch", {}),
    ("GET /page/_deleted", "GET", "/page/_deleted", {}),
    ("GET /page/{slug...}", "GET", "/page/private/alice/old", {}),   # a deleted page offers restore
    ("GET /uncommitted", "GET", "/uncommitted", {}),
    ("GET /api/uncommitted", "GET", "/api/uncommitted", {}),
    ("GET /new", "GET", "/new", {}),
    ("GET /_assets/{path...}", "GET", "/_assets/private/alice/photo.png", {}),
    ("GET /deck", "GET", "/deck", {}),
    ("GET /api/deck", "GET", "/api/deck", {}),
    ("GET /api/deck/completed", "GET", "/api/deck/completed", {}),
    ("GET /api/deck/completed-count", "GET", "/api/deck/completed-count", {}),
    ("GET /api/deck/completed-years", "GET", "/api/deck/completed-years", {}),
    ("GET /api/deck/due-count", "GET", "/api/deck/due-count", {}),
    ("GET /api/deck/headless", "GET", "/api/deck/headless", {}),
    ("POST /api/preview", "POST", "/api/preview", {"data": f"[[{DIARY}]] [[private/alice/old]] [[private/alice/draft]]"}),
    ("POST /api/render-notes", "POST", "/api/render-notes", {"json": {"notes": [f"[[{DIARY}]]"], "comments": []}}),
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
    if path.startswith(f"/page/{DIARY}") or "/private/alice/" in path:
        assert resp.status_code == 404


def test_alice_sees_her_own(client, wiki):
    client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
    assert SECRET in client.get(f"/page/{DIARY}", headers=HOST).data.decode()
    assert SECRET in client.get("/api/search", query_string={"q": QUERY}, headers=HOST).data.decode()
    assert 'href="/page/private/alice/diary"' in client.get("/page/plans", headers=HOST).data.decode()


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


def scratch_titles(client):
    return re.findall(r'<a href="/page/([^"]+)"', client.get("/page/_scratch", headers=HOST).data.decode())


def test_scratch_notes_are_private(alice, wiki):
    (wiki / "2026-01-01_10-00-00_shared.md").write_text("# Shared\n")   # from before users: stays shared
    slug = alice.post("/api/scratch", data={"title": "Mine"}, headers=HOST).get_json()["slug"]
    assert re.fullmatch(r"private/alice/\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_mine", slug)
    assert (wiki / f"{slug}.md").exists()
    notes = scratch_titles(alice)
    assert slug in notes and NOTE in notes and "2026-01-01_10-00-00_shared" in notes
    sign_in(alice, "bob")
    bob = alice
    notes = scratch_titles(bob)
    assert "2026-01-01_10-00-00_shared" in notes and not any("alice" in n for n in notes)


def test_move_to_wiki_makes_a_private_note_shared(alice, wiki):
    resp = alice.post(f"/promote/{NOTE}", data={"folder": "ideas", "name": "Idea"}, headers=HOST)
    assert resp.headers["Location"] == "/page/ideas/idea"
    assert (wiki / "ideas/idea.md").exists() and not (wiki / f"{NOTE}.md").exists()


def test_append_moves_a_private_note_out(alice, wiki):
    resp = alice.post(f"/append/{NOTE}", data={"page": "plans", "heading": "Idea"}, headers=HOST)
    assert resp.headers["Location"] == "/page/plans#idea"
    assert SECRET in (wiki / "plans.md").read_text() and not (wiki / f"{NOTE}.md").exists()


def upload(client, page="", name="pic.png", **extra):
    return client.post("/api/upload", data={"file": (BytesIO(b"\x89PNG"), name), "page": page, **extra},
                       content_type="multipart/form-data", headers=HOST)


def test_an_upload_for_a_private_page_is_private(alice, wiki):
    resp = upload(alice, DIARY)
    assert resp.get_json()["url"] == "/_assets/private/alice/pic.png"
    assert (wiki / "_assets/private/alice/pic.png").read_bytes() == b"\x89PNG"
    assert upload(alice, DIARY).status_code == 409                      # the same name there again
    assert upload(alice, "plans").get_json()["url"] == "/_assets/pic.png"   # a shared page's: shared
    assert alice.get("/_assets/private/alice/pic.png", headers=HOST).status_code == 200
    assert "Upload private/alice/pic.png" in git(wiki, "log", "--format=%s")
    sign_in(alice, "bob")
    bob = alice
    assert bob.get("/_assets/private/alice/pic.png", headers=HOST).status_code == 404
    assert bob.get("/_assets/pic.png", headers=HOST).status_code == 200
    assert upload(bob, DIARY, "b.png").get_json()["url"] == "/_assets/b.png"   # not into alice's folder


def test_assets_in_other_folders_are_not_served(alice, wiki):
    (wiki / "_assets/sub").mkdir()
    (wiki / "_assets/sub/a.png").write_bytes(b"x")
    (wiki / "_assets/private/alice/deeper").mkdir()
    (wiki / "_assets/private/alice/deeper/a.png").write_bytes(b"x")
    for path in ("/_assets/sub/a.png", "/_assets/private/alice/deeper/a.png", "/_assets/private/alice/"):
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


# ── MCP ──────────────────────────────────────────────────────────────────────

def mcp(client, tool, **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer bob-token"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert resp.status_code == 200
    return resp.get_json()["result"]["content"][0]["text"]


MCP_READS = [
    pytest.param("read_page", {"slug": DIARY}, marks=later(4)),
    pytest.param("search_wiki", {"query": QUERY}, marks=later(4)),
    pytest.param("grep_wiki", {"pattern": QUERY}, marks=later(4)),
    pytest.param("list_pages", {}, marks=later(4)),
    pytest.param("list_pages", {"under": "private"}, marks=later(4)),
    ("check_links", {}),
    pytest.param("list_tags", {}, marks=later(4)),
    pytest.param("history", {"slug": DIARY}, marks=later(4)),
    pytest.param("history", {}, marks=later(4)),                 # recent changes
    ("search_tasks", {"query": QUERY}),
    ("read_guides", {"slug": DIARY}),
]


@pytest.mark.parametrize("tool,args", MCP_READS)
def test_bobs_assistant_reads_nothing_of_alices(client, wiki, tool, args):
    text = mcp(client, tool, **args)
    assert SECRET not in text and "private/alice" not in text.replace(args.get("slug", "\0"), "")
    assert sha(wiki)[:7] not in text or tool == "history" and not args, "the diary's versions"


# ── Writes ───────────────────────────────────────────────────────────────────

WRITES = [
    ("POST /edit/{slug...}", "POST", f"/edit/{DIARY}", {"data": {"content": "# Bob was here\n"}}),
    ("POST /api/save/{slug...}", "POST", f"/api/save/{DIARY}", {"data": {"content": "# Bob was here\n"}}),
    ("POST /new", "POST", "/new", {"data": {"slug": "private/alice/bobs"}}),
    ("POST /rename/{slug...}", "POST", f"/rename/{DIARY}", {"data": {"new_name": "stolen"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "private/alice/plans"}}),
    ("POST /delete/{slug...}", "POST", f"/delete/{DIARY}", {}),
    ("POST /restore/{rest...}", "POST", "/restore/private/alice/old/{old}", {}),
    ("POST /promote/{slug...}", "POST", f"/promote/{NOTE}", {"data": {"folder": "", "name": "stolen"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "private/alice", "name": "x"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{bobs}", {"data": {"folder": "", "name": "x", "link_from": DIARY}}),
    ("POST /append/{slug...}", "POST", f"/append/{NOTE}", {"data": {"page": "plans", "heading": "Stolen"}}),
    ("POST /append/{slug...}", "POST", "/append/{bobs}", {"data": {"page": DIARY, "heading": "Bob's"}}),
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
]


def _write_params():
    return [pytest.param(method, path, kwargs, id=f"{method} {path} {i}")
            for i, (route, method, path, kwargs) in enumerate(WRITES)]


def _alices_files(wiki):
    return {p.relative_to(wiki).as_posix(): p.read_bytes()
            for folder in ("private", "_assets/private") for p in (wiki / folder).rglob("*") if p.is_file()}


@pytest.mark.parametrize("method,path,kwargs", _write_params())
def test_bob_changes_nothing_of_alices(bob, wiki, method, path, kwargs):
    if "{bobs}" in path:   # bob's own scratch note, aiming at alice's pages from there
        path = path.replace("{bobs}", bob.post("/api/scratch", data={"title": "Bobs"}, headers=HOST).get_json()["slug"])
    kwargs = {k: v() if callable(v) else v for k, v in kwargs.items()}
    before, log = _alices_files(wiki), git(wiki, "log", "--format=%H")
    bob.open(fill(path, wiki), method=method, headers=HOST, **kwargs)
    assert _alices_files(wiki) == before
    new = git(wiki, "log", "--format=%H", f"{log.split()[0]}..HEAD").split() if log else []
    for commit in new:
        assert "private/alice" not in git(wiki, "show", "--stat", "--format=", commit)


# ── Every route is covered ───────────────────────────────────────────────────

# Routes that show or change no page: nothing of alice's to leak.
EXEMPT = {
    "/mcp",                         # its tools: MCP_READS
    "GET /{$}", "GET /login", "POST /login", "POST /logout", "GET /public/{path...}", "GET /api/version",
    "POST /api/scratch",            # makes bob's own note
}
# The deck's writes take task ids: private tasks come in phase 5, with their probes.
DECK_WRITES = {
    "POST /api/deck", "PUT /api/deck/{id}", "DELETE /api/deck/{id}", "POST /api/deck/{id}/toggle-check",
    "POST /api/deck/{id}/comments", "PUT /api/deck/{id}/comments/{cid}", "DELETE /api/deck/{id}/comments/{cid}",
    "POST /api/deck/batch-complete", "POST /api/deck/batch-delete", "POST /api/deck/batch-tag",
    "POST /api/deck/batch-update", "POST /api/deck/reorder", "POST /api/deck/restore",
}


def test_every_route_is_covered():
    source = "".join(p.read_text() for p in (REPO / "go").glob("*.go") if not p.name.endswith("_test.go"))
    routes = set(re.findall(r'HandleFunc\("([^"]+)"', source))
    covered = {r[0] for r in READS} | {w[0] for w in WRITES} | EXEMPT | DECK_WRITES
    assert routes - covered == set(), "routes neither probed nor exempt"
    assert covered - routes == set(), "probes of routes that don't exist"
