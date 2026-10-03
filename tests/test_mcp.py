"""Tests for the MCP server: the protocol handler (src/mcp_server.py), the
/mcp endpoint with its auth, and the tools (search_wiki, search_tasks,
read_page, list_pages) backed by section-level retrieval in server.py.

Run with:
    pytest tests/test_mcp.py -v
"""

import os

import pytest

from tests.test_routes import HOST, client, wiki_dir, write_page  # noqa: F401  (fixtures; sets sys.path)
import mcp_server
import server
from tests.test_semantic import make_index


def rpc(client, method, params=None, id_=1, headers=None):  # noqa: F811
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return client.post("/mcp", json=msg, headers={**HOST, **(headers or {})})


def call(client, name, **args):  # noqa: F811
    resp = rpc(client, "tools/call", {"name": name, "arguments": args})
    assert resp.status_code == 200
    result = resp.get_json()["result"]
    return result["content"][0]["text"], result["isError"]


# ── Protocol ─────────────────────────────────────────────────────────────────

def _no_tools(name, args):
    raise AssertionError("not called")


class TestHandle:
    def test_initialize_echoes_a_known_version(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18"}}, _no_tools, "1.0.0")
        res = r["result"]
        assert res["protocolVersion"] == "2025-06-18"
        assert res["capabilities"] == {"tools": {"listChanged": False}}
        assert res["serverInfo"]["name"] == "leaf" and res["serverInfo"]["version"] == "1.0.0"
        assert "search_wiki" in res["instructions"]

    def test_initialize_unknown_version_gets_newest(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "1999-01-01"}}, _no_tools, "x")
        assert r["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSIONS[0]

    def test_notification_and_response_get_nothing(self):
        assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                 _no_tools, "x") is None
        assert mcp_server.handle({"jsonrpc": "2.0", "id": 5, "result": {}}, _no_tools, "x") is None

    def test_ping(self):
        assert mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"},
                                 _no_tools, "x") == {"jsonrpc": "2.0", "id": 2, "result": {}}

    def test_tools_list(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, _no_tools, "x")
        tools = r["result"]["tools"]
        assert [t["name"] for t in tools] == ["search_wiki", "search_tasks", "read_page", "list_pages"]
        assert all(t["annotations"]["readOnlyHint"] for t in tools)
        assert all(t["inputSchema"]["type"] == "object" for t in tools)

    def test_unknown_method(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, _no_tools, "x")
        assert r["error"]["code"] == -32601

    def test_invalid_request(self):
        assert mcp_server.handle({"id": 1, "method": "ping"}, _no_tools, "x")["error"]["code"] == -32600
        assert mcp_server.handle([1], _no_tools, "x")["error"]["code"] == -32600

    def test_unknown_tool_and_bad_arguments(self):
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "delete_everything"}}, _no_tools, "x")
        assert r["error"]["code"] == -32602
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "read_page", "arguments": [1]}}, _no_tools, "x")
        assert r["error"]["code"] == -32602

    def test_tool_error_is_a_result(self):
        def fail(name, args):
            raise mcp_server.ToolError("No page 'x'")
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "read_page", "arguments": {"slug": "x"}}}, fail, "x")
        assert r["result"] == {"content": [{"type": "text", "text": "No page 'x'"}], "isError": True}


# ── HTTP transport and auth ──────────────────────────────────────────────────

class TestEndpoint:
    def test_initialize_over_http(self, client):  # noqa: F811
        resp = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "0"}})
        assert resp.status_code == 200
        assert resp.get_json()["result"]["serverInfo"]["name"] == "leaf"

    def test_notification_is_202(self, client):  # noqa: F811
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                           headers=HOST)
        assert resp.status_code == 202 and resp.data == b""

    def test_batch(self, client):  # noqa: F811
        resp = client.post("/mcp", headers=HOST, json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ])
        assert resp.get_json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]

    def test_parse_error(self, client):  # noqa: F811
        resp = client.post("/mcp", data="{nope", content_type="application/json", headers=HOST)
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == -32700

    def test_get_and_delete_not_offered(self, client):  # noqa: F811
        assert client.get("/mcp", headers=HOST).status_code == 405
        assert client.delete("/mcp", headers=HOST).status_code == 405

    def test_cross_site_post_refused(self, client):  # noqa: F811
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers={**HOST, "Origin": "https://evil.example"})
        assert resp.status_code == 403


class TestAuth:
    def test_password_without_token_closes_mcp(self, client, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "LEAF_PASSWORD", "pw")
        resp = rpc(client, "ping")
        assert resp.status_code == 401
        assert "LEAF_MCP_TOKEN" in resp.get_json()["error"]

    def test_token_required_when_set(self, client, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "LEAF_MCP_TOKEN", "s3cret")
        assert rpc(client, "ping").status_code == 401
        assert rpc(client, "ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert rpc(client, "ping", headers={"Authorization": "Bearer s3cret"}).status_code == 200

    def test_token_works_with_password(self, client, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "LEAF_PASSWORD", "pw")
        monkeypatch.setattr(server, "LEAF_MCP_TOKEN", "s3cret")
        assert rpc(client, "ping", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        # and the rest of the wiki still wants a login
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_get_also_needs_auth(self, client, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "LEAF_MCP_TOKEN", "s3cret")
        assert client.get("/mcp", headers=HOST).status_code == 401


# ── Tools ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def pages(wiki_dir):  # noqa: F811
    write_page(wiki_dir, "git-remote",
               "# Git remote\n\n## Setup\n\nSet GIT_REMOTE in .env.\n\n## Pushing\n\nRun git push to back up.\n")
    write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n")
    return wiki_dir


class TestSearchWiki:
    def test_returns_sections_with_full_text_and_links(self, client, pages):  # noqa: F811
        text, err = call(client, "search_wiki", query="git push")
        assert not err
        first = text.split("\n\n---\n\n")[0]
        assert first.startswith("[1] Git remote › Pushing")
        assert "slug: git-remote" in first
        assert "url: http://localhost:5000/page/git-remote#pushing" in first
        assert first.endswith("Run git push to back up.")

    def test_last_edit_date(self, client, pages):  # noqa: F811
        os.utime(pages / "git-remote.md", (1_000_000_000, 1_000_000_000))
        first = call(client, "search_wiki", query="git push")[0].split("\n\n---\n\n")[0]
        assert "\nslug: git-remote\nedited: 2001-09-0" in first

    def test_several_sections_of_one_page(self, client, pages):  # noqa: F811
        text, _ = call(client, "search_wiki", query="git")
        assert "Git remote › Setup" in text and "Git remote › Pushing" in text

    def test_limit(self, client, pages):  # noqa: F811
        text, _ = call(client, "search_wiki", query="git", limit=1)
        assert "[1]" in text and "[2]" not in text

    def test_stopwords_dont_rank(self, client, pages):  # noqa: F811
        write_page(pages, "filler", "# Filler\n\nThe the the is is to to the.\n")
        text, _ = call(client, "search_wiki", query="when is the git push")
        assert "Filler" not in text

    def test_query_syntax(self, client, pages):  # noqa: F811
        text, _ = call(client, "search_wiki", query='"git push" -remote')
        assert text.startswith("No matching")      # the git-remote page has "remote"
        write_page(pages, "tagged", "---\ntags: ops\n---\n# Ops\n\nRun git push to back up.\n")
        task(client, title="git push the backup", tags=["ops"])
        text, _ = call(client, "search_wiki", query="git push #ops")
        assert "slug: tagged" in text and "Task: git push the backup" in text
        assert "slug: git-remote" not in text

    def test_no_match(self, client, pages):  # noqa: F811
        text, err = call(client, "search_wiki", query="xqzt")
        assert not err and text.startswith("No matching sections")

    def test_bad_arguments(self, client, pages):  # noqa: F811
        assert call(client, "search_wiki", query="  ")[1] is True
        assert call(client, "search_wiki")[1] is True
        assert call(client, "search_wiki", query="git", limit="many")[1] is True

    def test_sees_edits(self, client, pages):  # noqa: F811
        call(client, "search_wiki", query="git")
        path = pages / "golf.md"
        path.write_text("# Golf\n\nNow about git too.\n", encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        assert "Now about git too." in call(client, "search_wiki", query="git")[0]

    def test_finds_by_meaning_with_semantic_on(self, client, pages, monkeypatch):  # noqa: F811
        idx = make_index(pages)
        idx.sync()
        monkeypatch.setattr(server, "SEMANTIC", idx)
        text, _ = call(client, "search_wiki", query="car repair")
        assert text.startswith("[1] Golf › Werkstatt")
        assert "#werkstatt" in text

    def test_keywords_only_without_semantic(self, client, pages, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "SEMANTIC", None)
        assert call(client, "search_wiki", query="car repair")[0].startswith("No matching")


def task(client, **fields):  # noqa: F811
    resp = client.post("/api/deck", json=fields, headers=HOST)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def close(client, task_id, **fields):  # noqa: F811
    resp = client.put(f"/api/deck/{task_id}", json=fields, headers=HOST)
    assert resp.status_code == 200, resp.get_json()


def entries(text):
    """A search_tasks/search_wiki answer split into its entries' first lines."""
    return [part.split("\n")[0] for part in text.split("\n\n---\n\n")]


class TestFolders:
    @pytest.fixture()
    def tree(self, pages):  # noqa: F811
        write_page(pages, "tickets/BUG-1_push", "# BUG-1\n\nThe git push failed on the server.\n")
        write_page(pages, "tickets/old/BUG-0", "# BUG-0\n\nAn old git issue.\n")
        write_page(pages, "ticketsarchive", "# Archive\n\nMore git notes.\n")
        return pages

    def test_search_under_a_folder(self, client, tree):  # noqa: F811
        task(client, title="git chores")
        text, err = call(client, "search_wiki", query="git", under="tickets")
        assert not err
        slugs = [line[6:] for line in text.splitlines() if line.startswith("slug: ")]
        assert set(slugs) == {"tickets/BUG-1_push", "tickets/old/BUG-0"}  # not ticketsarchive
        assert "Task:" not in text

    @pytest.mark.parametrize("under", ["tickets/", "/page/tickets", "TICKETS", "tickets/old"])
    def test_folder_spellings(self, client, tree, under):  # noqa: F811
        text, err = call(client, "search_wiki", query="git", under=under)
        assert not err and "slug: tickets/old/BUG-0" in text

    def test_unknown_folder_is_an_error(self, client, tree):  # noqa: F811
        text, err = call(client, "search_wiki", query="git", under="nope")
        assert err and "list_pages" in text

    def test_empty_under_means_everything(self, client, tree):  # noqa: F811
        assert "slug: ticketsarchive" in call(client, "search_wiki", query="git", under="")[0]

    def test_list_pages_under(self, client, tree):  # noqa: F811
        text, _ = call(client, "list_pages", under="tickets")
        assert text.splitlines()[0].startswith("Pages under tickets/, most recently edited first (all 2)")
        assert call(client, "list_pages", under="nope")[0] == "No pages under 'nope'."

    def test_bad_type(self, client, tree):  # noqa: F811
        assert call(client, "search_wiki", query="git", under=3)[1] is True


class TestTasksInSearchWiki:
    def test_task_is_a_result_with_its_facts(self, client, pages):  # noqa: F811
        t = task(client, title="Fix the leaf importer", tags=["leaf"], due_date="2026-10-10",
                 description="Crashes on empty files.")
        text, err = call(client, "search_wiki", query="importer")
        assert not err
        first = text.split("\n\n---\n\n")[0]
        assert first.startswith("[1] Task: Fix the leaf importer\nstatus: open · tags: #leaf · due: 2026-10-10\n")
        assert f"url: http://localhost:5000/deck?task={t['id']}&view=open" in first
        assert first.endswith("Crashes on empty files.")

    def test_tasks_and_sections_together(self, client, pages):  # noqa: F811
        task(client, title="Push the git remote again")
        text, _ = call(client, "search_wiki", query="git push")
        assert "Task: Push the git remote again" in text and "Git remote › Pushing" in text

    def test_done_task_links_to_completed(self, client, pages):  # noqa: F811
        t = task(client, title="Renew passport")
        close(client, t["id"], completed=True)
        text, _ = call(client, "search_wiki", query="passport")
        assert "status: done 20" in text and "view=completed" in text

    def test_with_semantic_on_tasks_still_found(self, client, pages, monkeypatch):  # noqa: F811
        idx = make_index(pages)
        idx.sync()
        monkeypatch.setattr(server, "SEMANTIC", idx)
        task(client, title="Call the mechaniker")
        text, _ = call(client, "search_wiki", query="mechaniker")
        assert "Task: Call the mechaniker" in text and "Golf › Werkstatt" in text


class TestTasksByMeaning:
    @pytest.fixture()
    def semantic_on(self, pages, monkeypatch):  # noqa: F811
        idx = make_index(pages)
        monkeypatch.setattr(server, "SEMANTIC", idx)
        return idx

    def test_search_wiki_finds_task_by_meaning(self, client, semantic_on):  # noqa: F811
        task(client, title="Book the Werkstatt")
        semantic_on.sync()
        assert "Task: Book the Werkstatt" in call(client, "search_wiki", query="car repair")[0]

    def test_not_under_a_folder(self, client, semantic_on, pages):  # noqa: F811
        write_page(pages, "tickets/car", "# Car\n\nMechaniker.\n")
        task(client, title="Book the Werkstatt")
        semantic_on.sync()
        assert "Task:" not in call(client, "search_wiki", query="car repair", under="tickets")[0]

    def test_search_tasks_query_by_meaning_within_the_filter(self, client, semantic_on):  # noqa: F811
        task(client, title="Book the Werkstatt", tags=["home"])
        task(client, title="Mechaniker for the van", tags=["work"])
        semantic_on.sync()
        text, _ = call(client, "search_tasks", query="car repair", tag="home")
        assert "1 matching tasks" in text and "Book the Werkstatt" in text


class TestSearchTasks:
    def test_open_by_tag_sorted_by_deadline(self, client, wiki_dir):  # noqa: F811
        task(client, title="Later", tags=["leaf"], due_date="2026-12-01")
        task(client, title="Soon", tags=["leaf", "bug"], due_date="2026-10-05")
        task(client, title="Undated", tags=["Leaf"])
        task(client, title="Other project", tags=["kb"], due_date="2026-10-01")
        text, err = call(client, "search_tasks", tag="leaf")
        assert not err
        assert text.startswith("Today is ")
        assert "3 matching tasks:" in text
        assert entries(text.split(":\n\n", 1)[1]) == ["Task: Soon", "Task: Later", "Task: Undated"]

    def test_several_tags_must_all_match(self, client, wiki_dir):  # noqa: F811
        task(client, title="A", tags=["leaf", "bug"])
        task(client, title="B", tags=["leaf"])
        text, _ = call(client, "search_tasks", tag="#leaf, bug")
        assert "1 matching tasks" in text and "Task: A" in text and "Task: B" not in text

    def test_status(self, client, wiki_dir):  # noqa: F811
        open_ = task(client, title="Still open", tags=["x"])
        done = task(client, title="Finished", tags=["x"])
        dropped = task(client, title="Dropped", tags=["x"])
        close(client, done["id"], completed=True)
        close(client, dropped["id"], wont_do=True)
        assert "Finished" not in call(client, "search_tasks", tag="x")[0]
        text = call(client, "search_tasks", tag="x", status="done")[0]
        assert "Still open" not in text and "status: done" in text and "status: won't do" in text
        text = call(client, "search_tasks", tag="x", status="all")[0]
        assert text.index("Still open") < text.index("Finished")
        assert open_["id"]

    def test_query_ranks_within_the_filter(self, client, wiki_dir):  # noqa: F811
        task(client, title="Write the importer", tags=["leaf"])
        task(client, title="Importer docs", tags=["kb"])
        text, _ = call(client, "search_tasks", query="importer", tag="leaf")
        assert "1 matching tasks" in text and "Write the importer" in text

    def test_list_filter_and_label(self, client, wiki_dir):  # noqa: F811
        task(client, title="Milk", list="groceries")
        task(client, title="Main deck task")
        text, _ = call(client, "search_tasks", list="Groceries")
        assert "Task: Milk" in text and "list: groceries" in text and "Main deck" not in text
        assert "list=groceries" in text

    def test_repeat_keep_and_start(self, client, wiki_dir):  # noqa: F811
        task(client, title="Water plants", start_date="2026-10-04", due_date="2026-10-06",
             rec_type="weekly", rec_interval=2)
        task(client, title="Coffee", keep=True)
        text, _ = call(client, "search_tasks")
        assert "start: 2026-10-04 · due: 2026-10-06 · repeats every 2 weeks" in text
        assert "keep task (back to the list when done)" in text

    def test_query_syntax(self, client, wiki_dir):  # noqa: F811
        task(client, title="Write the importer docs")
        task(client, title="Write the importer")
        text, _ = call(client, "search_tasks", query="importer -docs")
        assert "1 matching tasks" in text and "Task: Write the importer\n" in text

    def test_limit_and_none(self, client, wiki_dir):  # noqa: F811
        for i in range(3):
            task(client, title=f"T{i}")
        text, _ = call(client, "search_tasks", limit=2)
        assert "3 matching tasks, the first 2 shown" in text
        assert call(client, "search_tasks", tag="nope")[0].endswith("No matching tasks.")

    def test_bad_arguments(self, client, wiki_dir):  # noqa: F811
        assert call(client, "search_tasks", status="later")[1] is True
        assert call(client, "search_tasks", tag=["leaf"])[1] is True


class TestBaseUrl:
    def test_links_use_request_address_by_default(self, client, pages, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "LEAF_BASE_URL", "")
        resp = client.post("/mcp", headers={"Host": "host.containers.internal:5000"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "read_page", "arguments": {"slug": "golf"}}})
        assert "url: http://host.containers.internal:5000/page/golf\n" in \
            resp.get_json()["result"]["content"][0]["text"]

    def test_base_url_wins(self, client, pages, monkeypatch):  # noqa: F811
        monkeypatch.setattr(server, "LEAF_BASE_URL", "http://wiki.lan:5000")
        assert "url: http://wiki.lan:5000/page/golf\n" in call(client, "read_page", slug="golf")[0]
        assert "url: http://wiki.lan:5000/page/git-remote#pushing" in \
            call(client, "search_wiki", query="git push")[0]


class TestReadPage:
    @pytest.mark.parametrize("slug", ["golf", "/page/golf", "http://localhost:5000/page/golf#werkstatt",
                                      "golf.md", "Golf"])
    def test_slug_forms(self, client, pages, slug):  # noqa: F811
        text, err = call(client, "read_page", slug=slug)
        assert not err
        assert text.startswith("slug: golf\nurl: http://localhost:5000/page/golf\n\n# Golf\n")

    def test_page_name_with_spaces(self, client, pages):  # noqa: F811
        assert call(client, "read_page", slug="Git Remote")[0].startswith("slug: git-remote")

    def test_frontmatter_included(self, client, pages):  # noqa: F811
        write_page(pages, "fm", "---\ntags: a\n---\n# FM\n\nBody.\n")
        assert "tags: a" in call(client, "read_page", slug="fm")[0]

    def test_missing_page(self, client, pages):  # noqa: F811
        text, err = call(client, "read_page", slug="nope")
        assert err and "search_wiki" in text

    def test_path_traversal_refused(self, client, pages):  # noqa: F811
        (pages.parent / "secret.md").write_text("top secret", encoding="utf-8")
        text, err = call(client, "read_page", slug="../secret")
        assert err and "top secret" not in text


class TestListPages:
    def test_newest_first_with_titles(self, client, pages):  # noqa: F811
        old = pages / "index.md"
        os.utime(old, (1_000_000_000, 1_000_000_000))
        text, err = call(client, "list_pages")
        assert not err
        lines = text.splitlines()
        assert lines[0].startswith("Pages, most recently edited first (all 3)")
        assert lines[-1].startswith("index | Home | edited 2001-09-")
        assert any(line.startswith("golf | Golf | edited ") for line in lines)

    def test_limit(self, client, pages):  # noqa: F811
        text, _ = call(client, "list_pages", limit=1)
        assert "(1 of 3)" in text and len(text.splitlines()) == 2
