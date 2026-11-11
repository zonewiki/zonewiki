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
    tasks (open with a comment, completed), a note and a private task of
    carol's from when she was an editor; signed in as carol, a viewer."""
    monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    wiki = git_wiki_dir
    (wiki / "plans.md").write_text("# Plans\n\n- [ ] one\n\nshared words\n")
    (wiki / "folder").mkdir()
    (wiki / "folder" / "a.md").write_text("# A\n")
    git(wiki, "add", "-A")
    git(wiki, "commit", "-qm", "Plans")
    login(client, "alice")
    task = client.post("/api/tasks", json={"title": "Shared task", "tags": ["work"]}, headers=HOST).get_json()
    client.post(f"/api/tasks/{task['id']}/comments", json={"text": "hi"}, headers=HOST)
    done = client.post("/api/tasks", json={"title": "Done task"}, headers=HOST).get_json()
    client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
    comment = client.get("/api/tasks", headers=HOST).get_json()
    comment = next(t for t in comment if t["id"] == task["id"])["comments"][0]["id"]
    login(client, "carol")
    note = client.post("/api/notes", data={"title": "Carols"}, headers=HOST).get_json()["slug"]
    mine = client.post("/api/tasks", json={"title": "Mine", "private": True}, headers=HOST).get_json()
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"}})
    login(client, "carol")
    (wiki / "plans.md").write_text("# Plans\n\n- [ ] one\n\nshared words, uncommitted\n")
    IDS.clear()
    IDS.update({"task": task["id"], "done": done["id"], "comment": comment, "note": note, "mine": mine["id"]})
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
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "renamed"}}),
    ("POST /rename/{slug...}", "POST", "/rename/plans", {"data": {"new_name": "private/carol/plans"}}),
    ("POST /delete/{slug...}", "POST", "/delete/plans", {}),
    ("POST /restore/{rest...}", "POST", "/restore/plans/HEAD", {}),
    ("POST /promote/{slug...}", "POST", "/promote/{note}", {"data": {"folder": "", "name": "carols"}}),
    ("POST /promote/{slug...}", "POST", "/promote/{note}", {"data": {"folder": "folder", "name": "carols"}}),
    ("POST /append/{slug...}", "POST", "/append/{note}", {"data": {"page": "plans", "heading": "Carol's"}}),
    ("POST /api/toggle-checkbox/{slug...}", "POST", "/api/toggle-checkbox/plans", {"data": {"idx": "0"}}),
    ("POST /api/commit-file", "POST", "/api/commit-file", {"json": {"filepath": "plans.md"}}),
    ("POST /api/commit-all", "POST", "/api/commit-all", {}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"c"), "c.png"), "page": "plans"}}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"c"), "c.png")}}),
    ("POST /tasks/rename", "POST", "/tasks/rename", {"data": {"tag": "work", "new_name": "job"}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"all": True, "was": "", "set": {"view": "open"}}}),
    ("POST /api/tasks/settings", "POST", "/api/tasks/settings", {"json": {"page": "plans", "index": 0, "was": "", "set": {"view": "open"}}}),
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Carol's", "private": False}}),
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
    ("POST /api/scratch", "POST", "/api/scratch", {"data": {"title": "New note"}}),
    ("POST /new", "POST", "/new", {"data": {"slug": "diary", "private": "1"}}),
    ("POST /api/save/{slug...}", "POST", "/api/save/{note}", {"data": {"content": "# Mine\n"}}),
    ("POST /delete/{slug...}", "POST", "/delete/{note}", {}),
    ("POST /api/tasks", "POST", "/api/tasks", {"json": {"title": "Mine too", "private": True}}),
    ("PUT /api/tasks/{id}", "PUT", "/api/tasks/{mine}", {"json": {"completed": True}}),
    ("POST /api/upload", "POST", "/api/upload", {"data": lambda: {"file": (BytesIO(b"c"), "c.png"), "page": "{note}"}}),
    ("/api/deck", "POST", "/api/deck", {"json": {"title": "Carol's", "private": False}}),
    ("/api/deck/{rest...}", "PUT", "/api/deck/{task}", {"json": {"title": "Carol was here"}}),
    ("POST /admin/users/{name}/role", "POST", "/admin/users/carol/role", {"data": {"role": "admin"}}),
    ("POST /admin/users/invite", "POST", "/admin/users/invite", {"data": {"name": "dave", "role": "admin"}}),
]

# Writes that change nothing shared, or are someone else's to check.
EXEMPT = {
    "/mcp",                                             # its tools: MCP_WRITES
    "POST /login", "POST /logout", "POST /welcome/{token}",
    "POST /api/preview", "POST /api/render-notes",      # render only
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
    ("file_note", {"slug": "{note}", "name": "carols"}),
    ("file_note", {"slug": "{note}", "page": "plans"}),
    ("rename_task_tag", {"tag": "work", "new_tag": "job"}),
    ("create_task", {"title": "Carol's", "private": False}),
    ("update_task", {"id": "{task}", "title": "Carol was here"}),
    ("update_task", {"id": "{task}", "status": "deleted"}),
    ("update_task", {"id": "{done}", "status": "open", "comment": "Carol's"}),
    ("update_task", {"ids": ["{task}"], "add_tags": ["c"]}),
    ("write_page", {"slug": "private/carol/x", "content": "# X\n"}),
    ("write_page", {"note": True, "slug": "Mine", "content": "Mine\n"}),
    ("create_task", {"title": "Mine", "private": True}),
    ("update_task", {"id": "{mine}", "status": "done"}),
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
             "history", "zone_help", "read_guides"}
    assert tools - reads - {t for t, _ in MCP_WRITES} == set(), "write tools not probed"


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
