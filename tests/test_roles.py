"""Viewers (role = viewer, docs/spec/users.md, Viewers): carol reads
everything shared and changes nothing, not even a private page or note of
her own. Every write route and MCP write tool is probed as carol: no file
may change, nor any commit be made (test_every_write_route_is_probed fails
for a new write route without a probe, as test_privacy.py does for
reads)."""

import json
import re
import subprocess
from io import BytesIO
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import HOST

REPO = Path(__file__).resolve().parent.parent
USERS = {"alice": ("alice-pw", "alice-token"), "carol": ("carol-pw", "carol-token")}
READ_ONLY = "You can read this wiki but not change it"
IDS: dict[str, str] = {}   # the wiki fixture's task ids and note


def login(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]},
                       headers=HOST).status_code == 302


def mcp(client, tool, token="carol-token", **args):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    return "".join(c["text"] for c in resp.get_json()["result"]["content"])


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


@pytest.fixture()
def wiki(client, git_wiki_dir, monkeypatch):
    """A shared page with a checkbox, an uncommitted shared change, shared
    tasks in the shared list team (open with a comment, completed), a note,
    a private task of carol's in a list of hers and one in her Inbox, from
    when she was an editor; signed in as carol, a viewer."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    wiki = git_wiki_dir
    (wiki / "plans.md").write_text("# Plans\n\n- [ ] one\n\nshared words\n")
    (wiki / "folder").mkdir()
    (wiki / "folder" / "a.md").write_text("# A\n")
    git(wiki, "add", "-A")
    git(wiki, "commit", "-qm", "Plans")
    login(client, "alice")
    assert client.post("/tasks/new-list", data={"name": "team"}, headers=HOST).status_code == 302
    task = client.post("/api/tasks", json={"title": "Shared task", "list": "team", "tags": ["work"]}, headers=HOST).get_json()
    assert task["private"] is False        # with users a task without a list would be alice's own
    client.post(f"/api/tasks/{task['id']}/comments", json={"text": "hi"}, headers=HOST)
    done = client.post("/api/tasks", json={"title": "Done task", "list": "team"}, headers=HOST).get_json()
    client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
    comment = client.get("/api/tasks", headers=HOST).get_json()
    comment = next(t for t in comment if t["id"] == task["id"])["comments"][0]["id"]
    login(client, "carol")
    note = client.post("/api/notes", data={"title": "Carols"}, headers=HOST).get_json()["slug"]
    client.post("/tasks/new-list", data={"name": "carols-own", "private": "1"}, headers=HOST)
    mine = client.post("/api/tasks", json={"title": "Mine", "list": "carols-own"}, headers=HOST).get_json()
    inbox = client.post("/api/tasks", json={"title": "Captured"}, headers=HOST).get_json()
    assert inbox["private"] is True and "list" not in inbox
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"}})
    login(client, "carol")
    (wiki / "plans.md").write_text("# Plans\n\n- [ ] one\n\nshared words, uncommitted\n")
    IDS.clear()
    IDS.update({"task": task["id"], "done": done["id"], "comment": comment, "note": note, "mine": mine["id"],
                "inbox": inbox["id"]})
    return wiki


def shared_state(wiki):
    """Every file of the wiki, hers too, but Zone's state and git's."""
    return {p.relative_to(wiki).as_posix(): p.read_bytes() for p in wiki.rglob("*")
            if p.is_file() and not p.relative_to(wiki).as_posix().startswith((".git/", ".zone/"))}


def fill(value, wiki):
    if isinstance(value, str):
        return value.format(**IDS)
    if isinstance(value, dict):
        return {k: fill(v, wiki) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, wiki) for v in value]
    return value


# Route, method, path, request: carol on what is shared.
WRITES = [
    ("POST /edit/{slug...}", "POST", "/edit/plans", {"data": {"content": "# Carol\n"}}),
    ("POST /api/save/{slug...}", "POST", "/api/save/plans", {"data": {"content": "# Carol\n"}}),
    ("POST /new", "POST", "/new", {"data": {"slug": "carols-shared"}}),
    ("POST /link-new", "POST", "/link-new", {"data": {"slug": "garden", "on": "plans"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "renamed"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "private/carol/plans"}}),
    ("POST /delete/{slug...}", "POST", "/delete/plans", {}),
    ("POST /restore/{rest...}", "POST", "/restore/plans/HEAD", {}),
    ("POST /changes/undo", "POST", "/changes/undo?sha=abcdef1", {}),
    ("POST /promote/{slug...}", "POST", "/promote/{note}", {"data": {"folder": "", "name": "carols"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{note}", {"data": {"folder": "folder", "name": "carols"}}),
    ("POST /append/{slug...}", "POST", "/append/{note}", {"data": {"page": "plans", "heading": "Carol's"}}),
    ("POST /api/toggle-checkbox/{slug...}", "POST", "/api/toggle-checkbox/plans", {"data": {"idx": "0"}}),
    ("POST /api/commit-file", "POST", "/api/commit-file", {"json": {"filepath": "plans.md"}}),
    ("POST /api/commit-all", "POST", "/api/commit-all", {}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"c"), "c.png"), "page": "plans"}}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"c"), "c.png")}}),
    ("POST /tasks/rename", "POST", "/tasks/rename", {"data": {"tag": "work", "new_name": "job"}}),
    ("POST /tasks/new-list", "POST", "/tasks/new-list", {"data": {"name": "carols"}}),
    ("POST /tasks/new-list", "POST", "/tasks/new-list", {"data": {"name": "carols", "private": "1"}}),
    ("POST /api/tasks/lists", "POST", "/api/tasks/lists", {"json": {"name": "carols"}}),
    ("POST /api/tasks/lists", "POST", "/api/tasks/lists", {"json": {"name": "carols", "private": True}}),
    ("POST /tasks/rename-list", "POST", "/tasks/rename-list", {"data": {"list": "team", "new_name": "carols-team"}}),
    ("POST /tasks/rename-list", "POST", "/tasks/rename-list", {"data": {"list": "carols-own", "new_name": "hers"}}),
    ("POST /tasks/delete-list", "POST", "/tasks/delete-list", {"data": {"list": "team", "to": ""}}),
    ("POST /tasks/delete-list", "POST", "/tasks/delete-list", {"data": {"list": "carols-own", "to": "", "share": "1"}}),
    ("POST /tasks/share-list", "POST", "/tasks/share-list", {"data": {"list": "carols-own"}}),
    ("POST /tasks/list-for", "POST", "/tasks/list-for", {"data": {"list": "team", "for": "carol"}}),
    ("POST /tasks/pin-list", "POST", "/tasks/pin-list", {"data": {"list": "team", "pin": "1"}}),
    ("POST /tasks/pin-list", "POST", "/tasks/pin-list", {"data": {"mine": "1"}}),
    ("POST /tasks/pin-order", "POST", "/tasks/pin-order", {"data": {"list": "team"}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"list": "team", "set": {"limit": "3"}}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"all": True, "was": "", "set": {"view": "open"}}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"page": "plans", "index": 0, "was": "", "set": {"view": "open"}}}),
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Carol's", "list": "team"}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{task}", {"json": {"list": "team"}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{task}", {"json": {"title": "Carol was here"}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{task}", {"json": {"completed": True}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{done}", {"json": {"completed": False}}),
    ("DELETE /api/tasks/{id}", "DELETE", "/api/tasks/{task}", {}),
    ("POST /api/tasks/{id}/toggle-check", "POST", "/api/tasks/{task}/toggle-check", {"json": {"idx": 0}}),
    ("POST /api/tasks/{id}/comments", "POST", "/api/tasks/{task}/comments", {"json": {"text": "Carol's"}}),
    ("PUT /api/tasks/{id}/comments/{cid}", "PUT", "/api/tasks/{task}/comments/{comment}", {"json": {"text": "C"}}),
    ("DELETE /api/tasks/{id}/comments/{cid}", "DELETE", "/api/tasks/{task}/comments/{comment}", {}),
    ("POST /api/tasks/batch-complete", "POST", "/api/tasks/batch-complete", {"json": {"ids": ["{task}"]}}),
    ("POST /api/tasks/batch-delete", "POST", "/api/tasks/batch-delete", {"json": {"ids": ["{task}", "{done}"]}}),
    ("POST /api/tasks/batch-tag", "POST", "/api/tasks/batch-tag", {"json": {"ids": ["{task}"], "add": ["c"]}}),
    ("POST /api/tasks/batch-update", "POST", "/api/tasks/batch-update", {"json": {"tasks": [{"id": "{task}", "title": "C"}]}}),
    ("POST /api/tasks/reorder", "POST", "/api/tasks/reorder", {"json": {"order": ["{task}"], "updates": [{"id": "{task}", "sort_order": 9}]}}),
    ("POST /api/tasks/import", "POST", "/api/tasks/import", {"json": {"file": {"tasks": [{"title": "Imported"}]}}}),
    ("POST /api/tasks/restore", "POST", "/api/tasks/restore", {"json": {"tasks": [{"id": "new-id-1", "title": "Restored"}]}}),
    # Her own things, from when she was an editor, and new ones: refused too.
    ("POST /api/notes", "POST", "/api/notes", {"data": {"title": "New note"}}),
    ("POST /new", "POST", "/new", {"data": {"slug": "diary", "private": "1"}}),
    ("POST /api/save/{slug...}", "POST", "/api/save/{note}", {"data": {"content": "# Mine\n"}}),
    ("POST /delete/{slug...}", "POST", "/delete/{note}", {}),
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Mine too", "list": "carols-own"}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{mine}", {"json": {"list": "team", "share": True}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{mine}", {"json": {"completed": True}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{mine}", {"json": {"list": ""}}),
    # Her Inbox: no new task into it, and the one there stays as it is.
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "A capture"}}),
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "A capture", "list": "inbox"}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{inbox}", {"json": {"title": "Carol was here"}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{inbox}", {"json": {"list": "team", "share": True}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{inbox}", {"json": {"list": "carols-own"}}),
    ("DELETE /api/tasks/{id}", "DELETE", "/api/tasks/{inbox}", {}),
    ("POST /tasks/delete-list", "POST", "/tasks/delete-list", {"data": {"list": "carols-own", "to": ""}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"list": "carols-own", "set": {"home": "off"}}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"list": "team", "set": {"home": "off"}}}),
    ("POST /api/tasks/import", "POST", "/api/tasks/import", {"json": {"file": {"tasks": [{"title": "Imported"}]}, "list": "inbox"}}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"c"), "c.png"), "page": "{note}"}}),
    ("POST /admin/users/{name}/role", "POST", "/admin/users/carol/role", {"data": {"role": "admin"}}),
    ("POST /admin/users/{name}/groups", "POST", "/admin/users/carol/groups", {"data": {"groups": "dev"}}),
    ("POST /groups/includes", "POST", "/groups/includes", {"data": {"group": "product", "includes": "dev"}}),
    ("POST /admin/users/invite", "POST", "/admin/users/invite", {"data": {"name": "dave", "role": "admin"}}),
    ("POST /groups", "POST", "/groups", {"data": {"name": "carols", "people": "alice"}}),
    ("POST /groups/{group}/people", "POST", "/groups/dev/people", {"data": {"people": "carol", "owners": "carol"}}),
    ("POST /groups/{group}/rename", "POST", "/groups/dev/rename", {"data": {"name": "carols"}}),
    ("POST /groups/{group}/leave", "POST", "/groups/dev/leave", {}),
    ("POST /groups/{group}/remove", "POST", "/groups/dev/remove", {"data": {"pages": "delete"}}),
]

# Writes that change nothing shared, or are someone else's to check.
EXEMPT = {
    "/mcp",                                             # its tools: MCP_WRITES
    "POST /login", "POST /logout", "POST /welcome/{token}",
    "POST /setup",                                      # only while the wiki has no login (test_setup.py)
    "POST /signup",                                     # signed out only: a viewer is sent home (test_signup.py)
    "POST /api/preview", "POST /api/render-notes",      # render only
    "POST /assistant/token", "POST /assistant/revoke",  # their own MCP token (test_assistant.py)
    "POST /password",                                   # their own password (test_password.py)
    # OAuth (test_oauth.py): a connection of their own, read-only as their token; a client's calls carry no session
    "POST /oauth/authorize", "POST /oauth/token", "POST /oauth/register", "POST /assistant/connections/{id}/revoke",
    "POST /assistant/connections/{id}/read-only",
    # admins only (test_admin.py); two are probed above
    "POST /admin/users/{name}/disable", "POST /admin/users/{name}/enable", "POST /admin/users/{name}/remove",
    "POST /admin/users/{name}/reset", "POST /admin/users/{name}/revoke",
}


@pytest.mark.parametrize("route,method,path,kwargs",
                         [pytest.param(*w, id=f"{w[1]} {w[2]} {i}") for i, w in enumerate(WRITES)])
def test_a_viewer_changes_nothing_shared(client, wiki, route, method, path, kwargs):
    kwargs = {k: (v() if callable(v) else fill(v, wiki)) for k, v in kwargs.items()}
    before, head = shared_state(wiki), git(wiki, "rev-parse", "HEAD").strip()
    resp = client.open(fill(path, wiki), method=method, headers=HOST, **kwargs)
    assert resp.status_code in (403, 404), resp.data.decode()[:300]
    assert shared_state(wiki) == before, resp.data.decode()[:300]
    assert git(wiki, "rev-parse", "HEAD").strip() == head


MCP_WRITES = [
    ("write_page", {"slug": "plans", "content": "# Carol\n", "overwrite": True}),
    ("write_page", {"slug": "carols-shared", "content": "# Carol\n"}),
    ("edit_page", {"slug": "plans", "old_string": "shared words", "new_string": "carol"}),
    ("append_to_page", {"slug": "plans", "content": "Carol was here"}),
    ("replace_in_pages", {"find": "shared", "replace": "carol", "dry_run": False}),
    ("rename_page", {"slug": "plans", "new_slug": "renamed"}),
    ("rename_page", {"slug": "folder/", "new_slug": "moved"}),
    ("delete_page", {"slug": "plans"}),
    ("restore_page", {"slug": "plans", "version": "HEAD"}),
    ("undo_changes", {"versions": "abcdef1"}),
    ("file_note", {"slug": "{note}", "name": "carols"}),
    ("file_note", {"slug": "{note}", "page": "plans"}),
    ("rename_task_tag", {"tag": "work", "new_tag": "job"}),
    ("create_task", {"title": "Carol's", "list": "team"}),
    ("create_task_list", {"name": "carols"}),
    ("create_task_list", {"name": "carols", "private": True}),
    ("rename_task_list", {"list": "team", "new_name": "carols"}),
    ("update_task", {"id": "{task}", "list": "team"}),
    ("update_task", {"id": "{task}", "title": "Carol was here"}),
    ("update_task", {"id": "{task}", "status": "wont_do"}),
    ("update_task", {"id": "{done}", "status": "open", "comment": "Carol's"}),
    ("update_task", {"ids": ["{task}"], "add_tags": ["c"]}),
    ("write_page", {"slug": "private/carol/x", "content": "# X\n"}),
    ("write_page", {"note": True, "slug": "Mine", "content": "Mine\n"}),
    ("create_task", {"title": "Mine", "list": "carols-own"}),
    ("update_task", {"id": "{mine}", "list": "team", "share": True}),
    ("update_task", {"id": "{mine}", "status": "done"}),
    ("update_task", {"id": "{mine}", "list": ""}),
    ("create_task", {"title": "A capture"}),
    ("create_task", {"title": "A capture", "list": "inbox"}),
    ("create_task", {"title": "For alice", "assignee": "alice"}),
    ("update_task", {"id": "{inbox}", "title": "Carol was here"}),
    ("update_task", {"id": "{inbox}", "list": "team", "share": True}),
    ("upload_file", {"filename": "carols.txt", "text": "Carol was here"}),
]


@pytest.mark.parametrize("tool,args", [pytest.param(t, a, id=f"{t} {i}") for i, (t, a) in enumerate(MCP_WRITES)])
def test_a_viewers_assistant_changes_nothing_shared(client, wiki, tool, args):
    before = shared_state(wiki)
    text = mcp(client, tool, **fill(args, wiki))
    assert READ_ONLY in text and shared_state(wiki) == before, text


def test_every_write_route_is_probed():
    source = "".join(p.read_text() for p in (REPO / "go").glob("*.go") if not p.name.endswith("_test.go"))
    routes = {r for r in re.findall(r'HandleFunc\("([^"]+)"', source)
              if not r.startswith(("GET ", "HEAD "))}
    covered = {w[0] for w in WRITES} | EXEMPT
    assert routes - covered == set(), "write routes neither probed nor exempt"
    assert covered - routes == set(), "probes of routes that don't exist"


def test_every_write_tool_is_probed():
    tools = {t["name"] for t in json.loads((REPO / "go/mcp_tools.json").read_text())}
    reads = {"search_wiki", "search_tasks", "grep_wiki", "read_page", "list_pages", "check_links", "list_tags",
             "history", "zone_help", "read_guides", "read_file"}
    assert tools - reads - {t for t, _ in MCP_WRITES} == set(), "write tools not probed"


def test_a_viewers_token_uploads_nothing_and_reads_files(client, wiki):
    """The file routes with her MCP token (docs/spec/mcp.md, Files): reading yes, uploading no."""
    from io import BytesIO
    token = {**HOST, "Authorization": "Bearer carol-token"}
    before = shared_state(wiki)
    resp = client.post("/api/upload", data={"file": (BytesIO(b"x"), "carols.txt")},
                       content_type="multipart/form-data", headers=token)
    assert resp.status_code == 403 and shared_state(wiki) == before
    (wiki / "_assets").mkdir(exist_ok=True)
    (wiki / "_assets/seen.txt").write_text("shared words")
    assert client.get("/_assets/seen.txt", headers=token).data == b"shared words"
    assert "shared words" in mcp(client, "read_file", url="/_assets/seen.txt")


class TestWhatTheyAreTold:

    def test_a_shared_page_says_read_only(self, client, wiki):
        resp = client.post("/api/save/plans", data={"content": "# C\n"}, headers=HOST)
        assert resp.status_code == 403 and READ_ONLY in resp.data.decode()
        assert client.post("/tasks/rename", data={"tag": "work", "new_name": "x"}, headers=HOST).status_code == 403

    def test_a_shared_task_says_read_only(self, client, wiki):
        resp = client.put(f"/api/tasks/{IDS['task']}", json={"title": "C"}, headers=HOST)
        assert resp.status_code == 403 and READ_ONLY in resp.data.decode()

    def test_the_assistant_is_told_too(self, client, wiki):
        assert READ_ONLY in mcp(client, "write_page", slug="plans", content="# C\n", overwrite=True)
        assert READ_ONLY in mcp(client, "update_task", id=IDS["task"], title="C")

    def test_the_refusal_tells_nothing_of_another_users_folder(self, client, wiki):
        """The gate refuses before the route looks: alice's folder answers as anywhere."""
        a = client.post("/api/save/private/alice/x", data={"content": "# C\n"}, headers=HOST)
        b = client.post("/api/save/nothing-here", data={"content": "# C\n"}, headers=HOST)
        assert (a.status_code, a.data) == (b.status_code, b.data) == (403, b.data)
        assert client.get("/page/private/alice/x", headers=HOST).status_code == 404

    def test_she_reads_everything_shared(self, client, wiki):
        assert "shared words" in client.get("/page/plans", headers=HOST).data.decode()
        assert any(t["id"] == IDS["task"] for t in client.get("/api/tasks", headers=HOST).get_json())

    def test_she_has_no_inbox_to_fill(self, client, wiki):
        """A viewer makes no task, so the switcher offers her no Inbox; what
        she captured as an editor is still hers, in her My tasks."""
        page = client.get("/tasks", headers=HOST).data.decode()
        assert 'data-place="mine"' in page and 'data-place="inbox"' not in page
        assert "<h1>Inbox" not in client.get("/tasks?list=inbox", headers=HOST).data.decode()
        mine = {t["id"] for t in client.get("/api/tasks/export?mine=1", headers=HOST).get_json()["tasks"]}
        assert {IDS["inbox"], IDS["mine"], IDS["task"]} <= mine
        login(client, "alice")
        page = client.get("/tasks", headers=HOST).data.decode()
        assert 'data-place="inbox"' in page and "Captured" not in page


class TestTheInterface:

    def test_a_page_offers_nothing_to_change(self, client, wiki):
        body = client.get("/page/plans", headers=HOST).data.decode()
        assert 'class="page-col read-only"' in body and 'href="/edit/plans"' not in body
        assert 'data-action="rename"' not in body and 'data-action="delete"' not in body
        assert client.get("/edit/plans", headers=HOST).status_code == 403
        assert client.get("/edit/new-page", headers=HOST).status_code == 403

    def test_nothing_to_make(self, client, wiki):
        body = client.get("/page/plans", headers=HOST).data.decode()
        assert 'data-viewer="1"' in body
        assert 'data-module="private"' not in body and 'data-module="notes"' not in body
        assert client.get("/private", headers=HOST).status_code == 404

    def test_her_assistant_isnt_offered_the_writing_tools(self, client, wiki):
        resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer carol-token"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in resp.get_json()["result"]["tools"]}
        assert "read_page" in names and "search_wiki" in names
        assert not names & {"write_page", "edit_page", "create_task", "update_task", "file_note"}

    def test_an_editor_sees_it_all(self, client, wiki):
        login(client, "alice")
        body = client.get("/page/plans", headers=HOST).data.decode()
        assert "read-only" not in body and 'href="/edit/plans"' in body and "data-viewer" not in body


# ── Guides are an admin's to change ──────────────────────────────────────────

ADMINS_ONLY = "Only an admin of this wiki can change this page"
GUIDE_USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token")}
GUIDES = ["wiki-guide", "tickets/AGENTS", "tickets/style", "tickets/deep/claude"]


@pytest.fixture()
def guides(client, git_wiki_dir, monkeypatch):
    """The wiki's guide, a folder's (which pulls in a page there and one
    not there yet), a deeper one, and one in bob's private folder; alice an
    admin, bob an editor, signed in."""
    monkeypatch.setattr(settings, "USERS", dict(GUIDE_USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    wiki = git_wiki_dir
    for slug, text in {"wiki-guide": "# Guide\n\nkeep it short\n", "plans": "# Plans\n\nkeep it short\n",
                       "tickets/AGENTS": "# Tickets\n\n@style.md\n@later.md\n\nkeep it short\n",
                       "tickets/style": "# Style\n\nkeep it short\n", "tickets/t1": "# T1\n\nkeep it short\n",
                       "tickets/deep/claude": "# Deep\n", "private/bob/AGENTS": "# Bob's own\n"}.items():
        (wiki / f"{slug}.md").parent.mkdir(parents=True, exist_ok=True)
        (wiki / f"{slug}.md").write_text(text)
    git(wiki, "add", "-A")
    git(wiki, "commit", "-qm", "Guides")
    client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
    return wiki


class TestGuidesAreAnAdmins:
    """Every user's assistant follows the guides, so with users only an
    admin writes them (adminsOnly in go/access.go)."""

    @pytest.mark.parametrize("slug", GUIDES)
    def test_an_editor_changes_no_guide(self, client, guides, slug):
        before = shared_state(guides)
        for path, data in [(f"/api/save/{slug}", {"content": "# Mine\n"}), (f"/edit/{slug}", {"content": "# Mine\n"}),
                           (f"/delete/{slug}", {}), (f"/rename/{slug}", {"new_name": "elsewhere"}),
                           (f"/api/toggle-checkbox/{slug}", {"idx": "0"})]:
            resp = client.post(path, data=data, headers=HOST)
            assert resp.status_code == 403 and ADMINS_ONLY in resp.data.decode(), path
        assert client.get(f"/edit/{slug}", headers=HOST).status_code == 403
        body = client.get(f"/page/{slug}", headers=HOST).data.decode()
        assert 'class="page-col read-only"' in body and f'href="/edit/{slug}"' not in body
        for tool, args in [("write_page", {"slug": slug, "content": "# Mine\n", "overwrite": True}),
                           ("edit_page", {"slug": slug, "old_text": "keep it short", "new_text": "mine"}),
                           ("append_to_page", {"slug": slug, "content": "mine"}), ("delete_page", {"slug": slug}),
                           ("rename_page", {"slug": slug, "new_slug": "elsewhere"})]:
            assert ADMINS_ONLY in mcp(client, tool, token="bob-token", **args), tool
        assert shared_state(guides) == before

    def test_an_editor_makes_no_guide(self, client, guides):
        before = shared_state(guides)
        for slug in ("notes/AGENTS", "notes/claude", "tickets/later"):   # later: a guide pulls it in
            assert client.post(f"/api/save/{slug}", data={"content": "# Mine\n"}, headers=HOST).status_code == 403
            assert client.get(f"/edit/{slug}", headers=HOST).status_code == 403
            assert ADMINS_ONLY in mcp(client, "write_page", token="bob-token", slug=slug, content="# Mine\n")
        resp = client.post("/rename/plans", data={"new_name": "notes/agents"}, headers=HOST)
        assert resp.status_code == 403 and (guides / "plans.md").exists()
        assert ADMINS_ONLY in mcp(client, "rename_page", token="bob-token", slug="tickets/t1", new_slug="tickets/claude")
        # A folder with a guide in it moves with an admin only.
        mcp(client, "rename_page", token="bob-token", slug="tickets/", new_slug="issues/")
        assert shared_state(guides) == before

    def test_a_replace_across_pages_leaves_the_guides(self, client, guides):
        args = dict(token="bob-token", find="keep it short", replace="write a lot", dry_run=False)
        refusal = mcp(client, "replace_in_pages", **args)   # the guides first: their ids
        ids = json.loads(re.search(r"guides set to (\[.*?\])", refusal).group(1))
        out = mcp(client, "replace_in_pages", guides=ids, **args)
        assert "plans" in out and "tickets/t1" in out
        for slug in GUIDES[:3]:
            assert "keep it short" in (guides / f"{slug}.md").read_text(), slug
        assert "write a lot" in (guides / "plans.md").read_text()

    def test_an_editor_writes_the_other_pages_and_their_own_guide(self, client, guides):
        assert client.post("/api/save/tickets/t1", data={"content": "# T1\n\nmine\n"}, headers=HOST).status_code == 200
        assert client.post("/api/save/private/bob/AGENTS", data={"content": "# Mine\n"}, headers=HOST).status_code == 200
        assert (guides / "private/bob/AGENTS.md").read_text() == "# Mine\n"

    def test_an_editor_commits_no_guide(self, client, guides):
        """A commit puts a page's text in the history under a name: a guide
        changed on disk is an admin's to commit, by Commit and by Commit all."""
        (guides / "wiki-guide.md").write_text("# Guide\n\nchanged by hand\n")
        (guides / "tickets/t1.md").write_text("# T1\n\nchanged by hand too\n")
        resp = client.post("/api/commit-file", json={"filepath": "wiki-guide.md"}, headers=HOST)
        assert resp.status_code == 403 and "admin" in resp.get_json()["error"]
        assert client.post("/api/commit-all", headers=HOST).get_json()["files"] == ["tickets/t1.md"]
        assert "wiki-guide.md" in git(guides, "status", "--porcelain")
        client.post("/logout", headers=HOST)
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        assert client.post("/api/commit-file", json={"filepath": "wiki-guide.md"}, headers=HOST).status_code == 200
        assert git(guides, "status", "--porcelain") == ""

    def test_an_admin_writes_them(self, client, guides):
        login_as = lambda name: client.post("/login", data={"username": name, "password": f"{name}-pw"}, headers=HOST)
        client.post("/logout", headers=HOST)
        login_as("alice")
        assert client.post("/api/save/wiki-guide", data={"content": "# Guide\n\nnew\n"}, headers=HOST).status_code == 200
        assert client.post("/api/save/tickets/later", data={"content": "# Later\n"}, headers=HOST).status_code == 200
        assert "read-only" not in client.get("/page/wiki-guide", headers=HOST).data.decode()
        out = mcp(client, "edit_page", token="alice-token", slug="tickets/style", old_text="keep it short", new_text="x")
        assert ADMINS_ONLY not in out

    def test_without_users_the_guides_are_the_wikis(self, client, git_wiki_dir):
        (git_wiki_dir / "wiki-guide.md").write_text("# Guide\n")
        assert client.post("/api/save/wiki-guide", data={"content": "# Guide\n\nnew\n"}, headers=HOST).status_code == 200
