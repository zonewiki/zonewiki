"""Tests for the MCP server: the protocol handler (src/mcp_server.py), the
/mcp endpoint with its auth, and the tools (search_wiki, search_tasks,
read_page, list_pages) backed by section-level retrieval in server.py.
"""

import os
import re

import pytest

from tests.conftest import HOST, write_page
import mcp_server
from tests.test_semantic import make_index
import config
import retrieval


TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def mcp_token(monkeypatch):
    """/mcp is off without LEAF_MCP_TOKEN: every test here has one."""
    monkeypatch.setattr(config, "LEAF_MCP_TOKEN", TOKEN)


def rpc(client, method, params=None, id_=1, headers=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return client.post("/mcp", json=msg, headers={**HOST, **AUTH, **(headers or {})})


def call(client, name, **args):
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
        assert [t["name"] for t in tools] == [
            "search_wiki", "search_tasks", "read_page", "list_pages",
            "write_page", "edit_page", "append_to_page", "rename_page", "delete_page",
            "create_task", "update_task", "comment_task", "delete_task"]
        reading = {"search_wiki", "search_tasks", "read_page", "list_pages"}
        assert all(t["annotations"]["readOnlyHint"] == (t["name"] in reading) for t in tools)
        adding = {"append_to_page", "create_task", "comment_task"}
        assert all(t["annotations"]["destructiveHint"] == (t["name"] not in adding)
                   for t in tools if t["name"] not in reading)
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
    def test_initialize_over_http(self, client):
        resp = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "0"}})
        assert resp.status_code == 200
        assert resp.get_json()["result"]["serverInfo"]["name"] == "leaf"

    def test_notification_is_202(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                           headers={**HOST, **AUTH})
        assert resp.status_code == 202 and resp.data == b""

    def test_batch(self, client):
        resp = client.post("/mcp", headers={**HOST, **AUTH}, json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ])
        assert resp.get_json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]

    def test_parse_error(self, client):
        resp = client.post("/mcp", data="{nope", content_type="application/json", headers={**HOST, **AUTH})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == -32700

    def test_get_and_delete_not_offered(self, client):
        assert client.get("/mcp", headers={**HOST, **AUTH}).status_code == 405
        assert client.delete("/mcp", headers={**HOST, **AUTH}).status_code == 405

    def test_cross_site_post_refused(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers={**HOST, **AUTH, "Origin": "https://evil.example"})
        assert resp.status_code == 403


class TestAuth:
    def test_off_without_a_token(self, client, wiki_dir, monkeypatch):
        # The tools write, so /mcp is never open: not without a password either.
        monkeypatch.setattr(config, "LEAF_MCP_TOKEN", "")
        for headers in ({}, {"Authorization": "Bearer "}, AUTH):
            resp = rpc(client, "ping", headers=headers)
            assert resp.status_code == 403
            assert "set LEAF_MCP_TOKEN" in resp.get_json()["error"]
        resp = client.post("/mcp", headers=HOST, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "write_page", "arguments": {"slug": "x", "content": "# X"}}})
        assert resp.status_code == 403 and not (wiki_dir / "x.md").exists()

    def test_token_required(self, client):
        assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers=HOST).status_code == 401
        assert rpc(client, "ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert rpc(client, "ping", headers={"Authorization": TOKEN}).status_code == 401
        assert rpc(client, "ping").status_code == 200

    def test_token_works_with_password(self, client, monkeypatch):
        monkeypatch.setattr(config, "LEAF_PASSWORD", "pw")
        assert rpc(client, "ping").status_code == 200
        # and the rest of the wiki still wants a login
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_get_also_needs_auth(self, client):
        assert client.get("/mcp", headers=HOST).status_code == 401

    def test_wrong_tokens_are_throttled(self, client):
        for _ in range(5):
            assert rpc(client, "ping", headers={"Authorization": "Bearer guess"}).status_code == 401
        resp = rpc(client, "ping")
        assert resp.status_code == 429 and "Retry-After" in resp.headers
        assert "Try again in 1 minute" in resp.get_json()["error"]


# ── Tools ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def pages(wiki_dir):
    write_page(wiki_dir, "git-remote",
               "# Git remote\n\n## Setup\n\nSet LEAF_GIT_REMOTE in .env.\n\n## Pushing\n\nRun git push to back up.\n")
    write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n")
    return wiki_dir


class TestSearchWiki:
    def test_returns_sections_with_full_text_and_links(self, client, pages):
        text, err = call(client, "search_wiki", query="git push")
        assert not err
        first = text.split("\n\n---\n\n")[0]
        assert first.startswith("[1] Git remote › Pushing")
        assert "slug: git-remote" in first
        assert "url: http://localhost:5000/page/git-remote#pushing" in first
        assert first.endswith("Run git push to back up.")

    def test_last_edit_date(self, client, pages):
        os.utime(pages / "git-remote.md", (1_000_000_000, 1_000_000_000))
        first = call(client, "search_wiki", query="git push")[0].split("\n\n---\n\n")[0]
        assert "\nslug: git-remote\nedited: 2001-09-0" in first

    def test_several_sections_of_one_page(self, client, pages):
        text, _ = call(client, "search_wiki", query="git")
        assert "Git remote › Setup" in text and "Git remote › Pushing" in text

    def test_limit(self, client, pages):
        text, _ = call(client, "search_wiki", query="git", limit=1)
        assert "[1]" in text and "[2]" not in text

    def test_stopwords_dont_rank(self, client, pages):
        write_page(pages, "filler", "# Filler\n\nThe the the is is to to the.\n")
        text, _ = call(client, "search_wiki", query="when is the git push")
        assert "Filler" not in text

    def test_query_syntax(self, client, pages):
        text, _ = call(client, "search_wiki", query='"git push" -remote')
        assert text.startswith("No matching")      # the git-remote page has "remote"
        write_page(pages, "tagged", "---\ntags: ops\n---\n# Ops\n\nRun git push to back up.\n")
        task(client, title="git push the backup", tags=["ops"])
        text, _ = call(client, "search_wiki", query="git push #ops")
        assert "slug: tagged" in text and "Task: git push the backup" in text
        assert "slug: git-remote" not in text

    def test_exact_phrase_ranks_first(self, client, pages):
        write_page(pages, "prep", "# Zebra prep\n\nZebra, zebra, zebra. Then crossing, crossing.\n")
        write_page(pages, "exact", "# Notes\n\nAt the zebra   crossing.\n")
        assert call(client, "search_wiki", query="zebra crossing")[0].startswith("[1] Notes\nslug: exact")

    def test_date_filter(self, client, pages):
        os.utime(pages / "golf.md", (1_000_000_000, 1_000_000_000))   # 2001-09-09
        text, _ = call(client, "search_wiki", query="werkstatt edited:2001")
        assert "slug: golf" in text
        assert call(client, "search_wiki", query="werkstatt after:2002")[0].startswith("No matching")

    def test_found_by_file_name(self, client, pages):
        write_page(pages, "messages/2026-06-18_update-alex-robin", "# Roof status\n\nRevised timeline.\n")
        assert "slug: messages/2026-06-18_update-alex-robin" in call(client, "search_wiki", query="robin")[0]

    def test_no_match(self, client, pages):
        text, err = call(client, "search_wiki", query="xqzt")
        assert not err and text.startswith("No matching sections")

    def test_bad_arguments(self, client, pages):
        assert call(client, "search_wiki", query="  ")[1] is True
        assert call(client, "search_wiki")[1] is True
        assert call(client, "search_wiki", query="git", limit="many")[1] is True

    def test_sees_edits(self, client, pages):
        call(client, "search_wiki", query="git")
        path = pages / "golf.md"
        path.write_text("# Golf\n\nNow about git too.\n", encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        assert "Now about git too." in call(client, "search_wiki", query="git")[0]

    def test_finds_by_meaning_with_semantic_on(self, client, pages, monkeypatch):
        idx = make_index(pages)
        idx.sync()
        monkeypatch.setattr(retrieval, "SEMANTIC", idx)
        text, _ = call(client, "search_wiki", query="car repair")
        assert text.startswith("[1] Golf › Werkstatt")
        assert "#werkstatt" in text

    def test_keywords_only_without_semantic(self, client, pages, monkeypatch):
        monkeypatch.setattr(retrieval, "SEMANTIC", None)
        assert call(client, "search_wiki", query="car repair")[0].startswith("No matching")


def task(client, **fields):
    resp = client.post("/api/deck", json=fields, headers=HOST)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def close(client, task_id, **fields):
    resp = client.put(f"/api/deck/{task_id}", json=fields, headers=HOST)
    assert resp.status_code == 200, resp.get_json()


def entries(text):
    """A search_tasks/search_wiki answer split into its entries' first lines."""
    return [part.split("\n")[0] for part in text.split("\n\n---\n\n")]


class TestFolders:
    @pytest.fixture()
    def tree(self, pages):
        write_page(pages, "tickets/BUG-1_push", "# BUG-1\n\nThe git push failed on the server.\n")
        write_page(pages, "tickets/old/BUG-0", "# BUG-0\n\nAn old git issue.\n")
        write_page(pages, "ticketsarchive", "# Archive\n\nMore git notes.\n")
        return pages

    def test_search_under_a_folder(self, client, tree):
        task(client, title="git chores")
        text, err = call(client, "search_wiki", query="git", under="tickets")
        assert not err
        slugs = [line[6:] for line in text.splitlines() if line.startswith("slug: ")]
        assert set(slugs) == {"tickets/BUG-1_push", "tickets/old/BUG-0"}  # not ticketsarchive
        assert "Task:" not in text

    @pytest.mark.parametrize("under", ["tickets/", "/page/tickets", "TICKETS", "tickets/old"])
    def test_folder_spellings(self, client, tree, under):
        text, err = call(client, "search_wiki", query="git", under=under)
        assert not err and "slug: tickets/old/BUG-0" in text

    def test_unknown_folder_is_an_error(self, client, tree):
        text, err = call(client, "search_wiki", query="git", under="nope")
        assert err and "list_pages" in text

    def test_empty_under_means_everything(self, client, tree):
        assert "slug: ticketsarchive" in call(client, "search_wiki", query="git", under="")[0]

    def test_list_pages_under(self, client, tree):
        text, _ = call(client, "list_pages", under="tickets")
        assert text.splitlines()[0].startswith("Pages under tickets/, most recently edited first (all 2)")
        assert call(client, "list_pages", under="nope")[0] == "No pages under 'nope'."

    def test_bad_type(self, client, tree):
        assert call(client, "search_wiki", query="git", under=3)[1] is True


class TestTasksInSearchWiki:
    def test_task_is_a_result_with_its_facts(self, client, pages):
        t = task(client, title="Fix the leaf importer", tags=["leaf"], due_date="2026-10-10",
                 description="Crashes on empty files.")
        text, err = call(client, "search_wiki", query="importer")
        assert not err
        first = text.split("\n\n---\n\n")[0]
        assert first.startswith("[1] Task: Fix the leaf importer\nstatus: open · tags: #leaf · due: 2026-10-10\n")
        assert f"url: http://localhost:5000/deck?task={t['id']}&view=open" in first
        assert first.endswith("Crashes on empty files.")

    def test_tasks_and_sections_together(self, client, pages):
        task(client, title="Push the git remote again")
        text, _ = call(client, "search_wiki", query="git push")
        assert "Task: Push the git remote again" in text and "Git remote › Pushing" in text

    def test_done_task_links_to_completed(self, client, pages):
        t = task(client, title="Renew passport")
        close(client, t["id"], completed=True)
        text, _ = call(client, "search_wiki", query="passport")
        assert "status: done 20" in text and "view=completed" in text

    def test_with_semantic_on_tasks_still_found(self, client, pages, monkeypatch):
        idx = make_index(pages)
        idx.sync()
        monkeypatch.setattr(retrieval, "SEMANTIC", idx)
        task(client, title="Call the mechaniker")
        text, _ = call(client, "search_wiki", query="mechaniker")
        assert "Task: Call the mechaniker" in text and "Golf › Werkstatt" in text


class TestTasksByMeaning:
    @pytest.fixture()
    def semantic_on(self, pages, monkeypatch):
        idx = make_index(pages)
        monkeypatch.setattr(retrieval, "SEMANTIC", idx)
        return idx

    def test_search_wiki_finds_task_by_meaning(self, client, semantic_on):
        task(client, title="Book the Werkstatt")
        semantic_on.sync()
        assert "Task: Book the Werkstatt" in call(client, "search_wiki", query="car repair")[0]

    def test_not_under_a_folder(self, client, semantic_on, pages):
        write_page(pages, "tickets/car", "# Car\n\nMechaniker.\n")
        task(client, title="Book the Werkstatt")
        semantic_on.sync()
        assert "Task:" not in call(client, "search_wiki", query="car repair", under="tickets")[0]

    def test_search_tasks_query_by_meaning_within_the_filter(self, client, semantic_on):
        task(client, title="Book the Werkstatt", tags=["home"])
        task(client, title="Mechaniker for the van", tags=["work"])
        semantic_on.sync()
        text, _ = call(client, "search_tasks", query="car repair", tag="home")
        assert "1 matching tasks" in text and "Book the Werkstatt" in text


class TestSearchTasks:
    def test_open_by_tag_sorted_by_deadline(self, client, wiki_dir):
        task(client, title="Later", tags=["leaf"], due_date="2026-12-01")
        task(client, title="Soon", tags=["leaf", "bug"], due_date="2026-10-05")
        task(client, title="Undated", tags=["Leaf"])
        task(client, title="Other project", tags=["kb"], due_date="2026-10-01")
        text, err = call(client, "search_tasks", tag="leaf")
        assert not err
        assert text.startswith("Today is ")
        assert "3 matching tasks:" in text
        assert entries(text.split(":\n\n", 1)[1]) == ["Task: Soon", "Task: Later", "Task: Undated"]

    def test_several_tags_must_all_match(self, client, wiki_dir):
        task(client, title="A", tags=["leaf", "bug"])
        task(client, title="B", tags=["leaf"])
        text, _ = call(client, "search_tasks", tag="#leaf, bug")
        assert "1 matching tasks" in text and "Task: A" in text and "Task: B" not in text

    def test_status(self, client, wiki_dir):
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

    def test_query_ranks_within_the_filter(self, client, wiki_dir):
        task(client, title="Write the importer", tags=["leaf"])
        task(client, title="Importer docs", tags=["kb"])
        text, _ = call(client, "search_tasks", query="importer", tag="leaf")
        assert "1 matching tasks" in text and "Write the importer" in text

    def test_list_label_and_link(self, client, wiki_dir):
        (wiki_dir / "shop.md").write_text("# Shop\n\n[[deck list=groceries]]\n", encoding="utf-8")
        task(client, title="Milk", tags=["groceries"])
        task(client, title="Main deck task")
        text, _ = call(client, "search_tasks", tag="groceries")
        assert "Task: Milk" in text and "list: groceries" in text and "Main deck" not in text
        assert "list=groceries" in text

    def test_repeat_keep_and_start(self, client, wiki_dir):
        task(client, title="Water plants", start_date="2026-10-04", due_date="2026-10-06",
             rec_type="weekly", rec_interval=2)
        task(client, title="Coffee", keep=True)
        text, _ = call(client, "search_tasks")
        assert "start: 2026-10-04 · due: 2026-10-06 · repeats every 2 weeks" in text
        assert "keep task (back to the list when done)" in text

    def test_query_syntax(self, client, wiki_dir):
        task(client, title="Write the importer docs")
        task(client, title="Write the importer")
        text, _ = call(client, "search_tasks", query="importer -docs")
        assert "1 matching tasks" in text and "Task: Write the importer\n" in text

    def test_limit_and_none(self, client, wiki_dir):
        for i in range(3):
            task(client, title=f"T{i}")
        text, _ = call(client, "search_tasks", limit=2)
        assert "3 matching tasks, the first 2 shown" in text
        assert call(client, "search_tasks", tag="nope")[0].endswith("No matching tasks.")

    def test_bad_arguments(self, client, wiki_dir):
        assert call(client, "search_tasks", status="later")[1] is True
        assert call(client, "search_tasks", tag=["leaf"])[1] is True


class TestBaseUrl:
    def test_links_use_request_address_by_default(self, client, pages, monkeypatch):
        monkeypatch.setattr(config, "LEAF_BASE_URL", "")
        resp = client.post("/mcp", headers={"Host": "host.containers.internal:5000", **AUTH}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "read_page", "arguments": {"slug": "golf"}}})
        assert "url: http://host.containers.internal:5000/page/golf\n" in \
            resp.get_json()["result"]["content"][0]["text"]

    def test_base_url_wins(self, client, pages, monkeypatch):
        monkeypatch.setattr(config, "LEAF_BASE_URL", "http://wiki.lan:5000")
        assert "url: http://wiki.lan:5000/page/golf\n" in call(client, "read_page", slug="golf")[0]
        assert "url: http://wiki.lan:5000/page/git-remote#pushing" in \
            call(client, "search_wiki", query="git push")[0]


class TestHostFile:
    def test_wiki_path_without_host_data(self, client, pages, monkeypatch):
        monkeypatch.setattr(config, "LEAF_HOST_DATA", "")
        first = call(client, "search_wiki", query="git push")[0].split("\n\n---\n\n")[0]
        assert f"#pushing\nfile: {pages.resolve()}/git-remote.md\n\nRun git push" in first

    def test_host_data_wins(self, client, pages, monkeypatch):
        monkeypatch.setattr(config, "LEAF_HOST_DATA", "/home/me/leaf")
        assert "\nfile: /home/me/leaf/git-remote.md\n" in call(client, "search_wiki", query="git push")[0]
        assert "\nfile: /home/me/leaf/golf.md\n" in call(client, "read_page", slug="golf")[0]

    def test_subfolder_and_odd_names_kept(self, client, pages, monkeypatch):
        monkeypatch.setattr(config, "LEAF_HOST_DATA", "/home/me/leaf")
        write_page(pages, "notes/My Notes", "# My Notes\n\n## Part\n\nZebrafish facts.\n")
        assert "\nfile: /home/me/leaf/notes/My Notes.md\n" in \
            call(client, "search_wiki", query="zebrafish")[0]
        assert "\nfile: /home/me/leaf/notes/My Notes.md\n" in \
            call(client, "read_page", slug="notes/My Notes")[0]

    def test_tasks_have_no_file_line(self, client, pages):
        task(client, title="Buy zebrafish food")
        text = call(client, "search_wiki", query="zebrafish food")[0]
        assert "Task: Buy zebrafish food" in text and "file:" not in text


class TestReadPage:
    @pytest.mark.parametrize("slug", ["golf", "/page/golf", "http://localhost:5000/page/golf#werkstatt",
                                      "golf.md", "Golf"])
    def test_slug_forms(self, client, pages, slug):
        text, err = call(client, "read_page", slug=slug)
        assert not err
        assert text.startswith("slug: golf\nurl: http://localhost:5000/page/golf\n"
                               f"file: {pages.resolve()}/golf.md\n\n# Golf\n")

    def test_page_name_with_spaces(self, client, pages):
        assert call(client, "read_page", slug="Git Remote")[0].startswith("slug: git-remote")

    def test_frontmatter_included(self, client, pages):
        write_page(pages, "fm", "---\ntags: a\n---\n# FM\n\nBody.\n")
        assert "tags: a" in call(client, "read_page", slug="fm")[0]

    def test_missing_page(self, client, pages):
        text, err = call(client, "read_page", slug="nope")
        assert err and "search_wiki" in text

    def test_path_traversal_refused(self, client, pages):
        (pages.parent / "secret.md").write_text("top secret", encoding="utf-8")
        text, err = call(client, "read_page", slug="../secret")
        assert err and "top secret" not in text


class TestListPages:
    def test_newest_first_with_titles(self, client, pages):
        old = pages / "index.md"
        os.utime(old, (1_000_000_000, 1_000_000_000))
        text, err = call(client, "list_pages")
        assert not err
        lines = text.splitlines()
        assert lines[0].startswith("Pages, most recently edited first (all 3)")
        assert lines[-1].startswith("index | Home | edited 2001-09-")
        assert any(line.startswith("golf | Golf | edited ") for line in lines)

    def test_limit(self, client, pages):
        text, _ = call(client, "list_pages", limit=1)
        assert "(1 of 3)" in text and len(text.splitlines()) == 2


class TestTaskComments:
    def test_task_text_lists_comments(self, client):
        tid = client.post("/api/deck", json={"title": "Plan on-call", "tags": ["task"],
                                             "description": "the notes"},
                          headers=HOST).get_json()["id"]
        client.post(f"/api/deck/{tid}/comments", json={"text": "asked alex: 6 people"}, headers=HOST)
        text, err = call(client, "search_tasks", tag="task")
        assert not err
        assert "the notes\n\nComments:\n- " in text
        assert re.search(r"- \d{4}-\d{2}-\d{2} \d{2}:\d{2}: asked alex: 6 people", text)


# ── Writing pages ────────────────────────────────────────────────────────────

class TestWritePage:
    def test_creates_a_page(self, client, wiki_dir):
        text, err = call(client, "write_page", slug="Tickets/BUG 1234", content="# BUG-1234\n\nCrash on start.")
        assert not err
        assert text.startswith("Created page tickets/bug-1234.")
        assert "url: http://localhost:5000/page/tickets/bug-1234" in text
        assert f"file: {wiki_dir.resolve()}/tickets/bug-1234.md" in text
        assert (wiki_dir / "tickets" / "bug-1234.md").read_text() == "# BUG-1234\n\nCrash on start.\n"

    def test_refuses_an_existing_page_without_overwrite(self, client, pages):
        text, err = call(client, "write_page", slug="golf", content="# Golf\n\nnew")
        assert err and "already exists" in text and "edit_page" in text
        assert "Werkstatt" in (pages / "golf.md").read_text()

    def test_overwrite_replaces_the_page(self, client, pages):
        text, err = call(client, "write_page", slug="golf", content="# Golf\n\nnew", overwrite=True)
        assert not err and text.startswith("Replaced page golf.")
        assert (pages / "golf.md").read_text() == "# Golf\n\nnew\n"

    def test_names_pages_with_similar_names(self, client, pages):
        text, err = call(client, "write_page", slug="notes/git-remotes", content="# Git Remotes\n")
        assert not err
        assert "pages with similar names exist" in text and "- git-remote | Git remote" in text
        text, _ = call(client, "write_page", slug="cooking", content="# Cooking\n")
        assert "similar names" not in text

    def test_warns_about_wikilinks_to_missing_pages(self, client, pages):
        text, _ = call(client, "write_page", slug="hub",
                       content="# Hub\n\n[[Golf]] [[Nowhere]] [[task:abcd1234]] [[toc]] `[[Code]]`")
        assert "point to pages that don't exist (yet): [[Nowhere]]" in text
        assert "[[Golf]]" not in text.split("Note:")[1] and "Code" not in text

    def test_refuses_folders_leaf_skips_and_stays_inside(self, client, wiki_dir):
        for slug in ("_deck/tasks", "_assets/x", "a/_b/c"):
            text, err = call(client, "write_page", slug=slug, content="# X")
            assert err and "folders starting with '_'" in text, slug
        assert not (wiki_dir / "_deck" / "tasks.md").exists()
        # to_slug drops the dots: the page lands inside the wiki
        text, err = call(client, "write_page", slug="../outside", content="# X")
        assert not err and "Created page outside." in text
        assert not (wiki_dir.parent / "outside.md").exists()

    def test_needs_content(self, client, wiki_dir):
        text, err = call(client, "write_page", slug="empty", content="  ")
        assert err and "content" in text


class TestEditPage:
    def test_replaces_unique_text(self, client, pages):
        text, err = call(client, "edit_page", slug="golf", old_string="Bremsen quietschen.",
                         new_string="Bremsen getauscht.")
        assert not err and text.startswith("Edited page golf: replaced 1 occurrence.")
        assert "Bremsen getauscht." in (pages / "golf.md").read_text()

    def test_not_found(self, client, pages):
        text, err = call(client, "edit_page", slug="golf", old_string="Reifen", new_string="x")
        assert err and "not found" in text and "read_page" in text

    def test_ambiguous_needs_replace_all(self, client, pages):
        write_page(pages, "twice", "# Twice\n\nfoo and foo\n")
        text, err = call(client, "edit_page", slug="twice", old_string="foo", new_string="bar")
        assert err and "occurs 2 times" in text
        assert (pages / "twice.md").read_text() == "# Twice\n\nfoo and foo\n"
        text, err = call(client, "edit_page", slug="twice", old_string="foo", new_string="bar",
                         replace_all=True)
        assert not err and "replaced 2 occurrences" in text
        assert (pages / "twice.md").read_text() == "# Twice\n\nbar and bar\n"

    def test_empty_new_string_deletes(self, client, pages):
        _, err = call(client, "edit_page", slug="git-remote", old_string="## Setup\n\nSet LEAF_GIT_REMOTE in .env.\n\n",
                      new_string="")
        assert not err
        assert (pages / "git-remote.md").read_text() == "# Git remote\n\n## Pushing\n\nRun git push to back up.\n"

    def test_crlf_in_arguments_matches_lf_files(self, client, pages):
        _, err = call(client, "edit_page", slug="git-remote", old_string="## Setup\r\n\r\nSet",
                      new_string="## Setup\r\n\r\nPut")
        assert not err and "## Setup\n\nPut LEAF_GIT_REMOTE" in (pages / "git-remote.md").read_text()

    def test_unknown_page_and_no_op(self, client, pages):
        text, err = call(client, "edit_page", slug="nope", old_string="a", new_string="b")
        assert err and "No page 'nope'" in text
        text, err = call(client, "edit_page", slug="golf", old_string="Golf", new_string="Golf")
        assert err and "nothing to change" in text

    def test_only_new_broken_links_are_named(self, client, pages):
        write_page(pages, "links", "# Links\n\n[[Old Missing]]\n")
        text, _ = call(client, "edit_page", slug="links", old_string="# Links", new_string="# Links [[New Missing]]")
        assert "[[New Missing]]" in text and "Old Missing" not in text


class TestAppendToPage:
    def test_appends_at_the_end(self, client, pages):
        text, err = call(client, "append_to_page", slug="golf", content="## Reifen\n\nWinterreifen drauf.")
        assert not err and text.startswith("Appended to page golf.")
        assert (pages / "golf.md").read_text().endswith(
            "Bremsen quietschen.\n\n## Reifen\n\nWinterreifen drauf.\n")

    def test_under_heading_goes_to_the_end_of_that_section(self, client, pages):
        _, err = call(client, "append_to_page", slug="git-remote", content="- also: git pull",
                      under_heading="setup")
        assert not err
        assert (pages / "git-remote.md").read_text() == (
            "# Git remote\n\n## Setup\n\nSet LEAF_GIT_REMOTE in .env.\n\n- also: git pull\n\n"
            "## Pushing\n\nRun git push to back up.\n")

    def test_subsections_belong_to_the_section(self, client, wiki_dir):
        write_page(wiki_dir, "log", "# Log\n\n## 2026\n\n### Oct\n\na\n\n```\n## not a heading\n```\n\n## 2025\n\nold\n")
        call(client, "append_to_page", slug="log", content="b", under_heading="## 2026")
        assert (wiki_dir / "log.md").read_text() == (
            "# Log\n\n## 2026\n\n### Oct\n\na\n\n```\n## not a heading\n```\n\nb\n\n## 2025\n\nold\n")

    def test_last_section(self, client, pages):
        call(client, "append_to_page", slug="git-remote", content="more", under_heading="Pushing")
        assert (pages / "git-remote.md").read_text().endswith("Run git push to back up.\n\nmore\n")

    def test_missing_heading_lists_the_headings(self, client, pages):
        text, err = call(client, "append_to_page", slug="git-remote", content="x", under_heading="Nope")
        assert err and "'Setup', 'Pushing'" in text


class TestRenamePage:
    def test_moves_the_page_and_its_links(self, client, pages):
        write_page(pages, "hub", "# Hub\n\nSee [[golf]] and [car](golf).\n")
        tid = client.post("/api/deck", json={"title": "Fix [[golf]]"}, headers=HOST).get_json()["id"]
        text, err = call(client, "rename_page", slug="golf", new_slug="cars/golf")
        assert not err and text.startswith("Renamed page golf to cars/golf. Links to it were updated in 2 files.")
        assert not (pages / "golf.md").exists() and (pages / "cars" / "golf.md").exists()
        assert (pages / "hub.md").read_text() == "# Hub\n\nSee [[cars/golf]] and [car](cars/golf).\n"
        assert call(client, "search_tasks", query="Fix")[0].count("[[cars/golf]]") == 1
        assert tid

    def test_refusals(self, client, pages):
        assert call(client, "rename_page", slug="index", new_slug="home")[1]
        text, err = call(client, "rename_page", slug="golf", new_slug="git-remote")
        assert err and "already exists" in text
        assert call(client, "rename_page", slug="golf", new_slug="golf")[1]


class TestDeletePage:
    def test_deletes_and_names_pages_still_linking(self, client, pages):
        write_page(pages, "hub", "# Hub\n\n[[Golf]]\n")
        text, err = call(client, "delete_page", slug="golf")
        assert not err and text.startswith("Deleted page golf.")
        assert "still link to it: hub" in text
        assert not (pages / "golf.md").exists()

    def test_home_page_stays(self, client, pages):
        text, err = call(client, "delete_page", slug="index")
        assert err and (pages / "index.md").exists()


@pytest.mark.skipif(not config.GIT_ENABLED, reason="git not installed")
class TestWritesCommit:
    def test_each_write_is_a_commit_marked_mcp(self, client, git_wiki_dir, monkeypatch):
        import subprocess
        import gitstore

        def subjects():
            return subprocess.run(["git", "log", "--format=%s"], cwd=git_wiki_dir, capture_output=True,
                                  text=True, check=True, env=gitstore._GIT_ENV).stdout.splitlines()

        call(client, "write_page", slug="mcp-notes", content="# MCP notes\n\nfirst", summary="start notes")
        call(client, "append_to_page", slug="mcp-notes", content="second")
        call(client, "edit_page", slug="mcp-notes", old_string="first", new_string="1st",
             summary="  shorten\n wording ")
        call(client, "rename_page", slug="mcp-notes", new_slug="notes/mcp")
        tid = call(client, "create_task", title="Ship it", summary="from chat")[0]
        short = re.search(r"\nid: (\w+)", tid).group(1)
        call(client, "update_task", id=short, status="done")
        call(client, "delete_page", slug="notes/mcp")
        assert subjects()[:7] == [
            "Delete notes/mcp (MCP)",
            "Complete deck task (MCP)",
            "Create deck task: from chat (MCP)",
            "Rename mcp-notes to notes/mcp (MCP)",
            "Update mcp-notes: shorten wording (MCP)",
            "Append to mcp-notes (MCP)",
            "Create mcp-notes: start notes (MCP)",
        ]
        status = subprocess.run(["git", "status", "--porcelain"], cwd=git_wiki_dir, capture_output=True,
                                text=True, env=gitstore._GIT_ENV).stdout
        assert status == ""


# ── Writing tasks ────────────────────────────────────────────────────────────

def _task_id(text: str) -> str:
    return re.search(r"\nid: (\w+)", text).group(1)


class TestCreateTask:
    def test_creates_with_fields(self, client, wiki_dir):
        text, err = call(client, "create_task", title="Renew passport", notes="at the Bürgeramt",
                         tags=["admin", "#home"], due="2026-11-01", start="2026-10-20")
        assert not err and text.startswith("Created.\n\nTask: Renew passport\n")
        assert "status: open · tags: #admin #home · start: 2026-10-20 · due: 2026-11-01" in text
        assert re.search(r"\nid: [0-9a-f]{8}\n", text)
        assert "at the Bürgeramt" in text

    def test_repeat_and_keep(self, client, wiki_dir):
        text, _ = call(client, "create_task", title="Water plants", due="2026-10-10",
                       repeat="weekly", repeat_every=2, repeat_from="completion")
        assert "repeats every 2 weeks after completion" in text
        text, _ = call(client, "create_task", title="Milk", keep=True, repeat="daily")
        assert "keep task" in text and "repeats" not in text

    def test_bad_input_is_a_tool_error(self, client, wiki_dir):
        assert "YYYY-MM-DD" in call(client, "create_task", title="x", due="tomorrow")[0]
        text, err = call(client, "create_task", title="  ")
        assert err and "title" in text
        text, err = call(client, "create_task", title="x", repeat="hourly")
        assert err and "repeat must be" in text
        assert call(client, "search_tasks", status="all")[0].endswith("No matching tasks.")


class TestUpdateTask:
    def test_changes_only_given_fields(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Report", tags=["work"], due="2026-10-15",
                            notes="draft")[0])
        text, err = call(client, "update_task", id=tid, due="2026-10-20", add_tags=["urgent", "WORK"])
        assert not err
        assert "tags: #work #urgent · due: 2026-10-20" in text and "draft" in text
        text, _ = call(client, "update_task", id=tid, remove_tags=["work"], due="", notes="")
        assert "tags: #urgent" in text and "due:" not in text and "draft" not in text

    def test_status_done_spawns_the_next_occurrence(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Backup", due="2026-10-10", repeat="weekly")[0])
        text, err = call(client, "update_task", id=tid, status="done")
        assert not err and "status: done" in text
        assert "Next occurrence:\n\nTask: Backup\nstatus: open · due: 2026-10-17" in text
        text, _ = call(client, "update_task", id=tid, status="open")
        assert "status: open" in text
        assert call(client, "search_tasks")[0].count("Task: Backup") == 1

    def test_wont_do_and_skip(self, client, wiki_dir):
        a = _task_id(call(client, "create_task", title="Maybe")[0])
        assert "status: won't do" in call(client, "update_task", id=a, status="wont_do")[0]
        b = _task_id(call(client, "create_task", title="Gym", due="2026-10-10", repeat="daily")[0])
        text, _ = call(client, "update_task", id=b, status="skip")
        assert "status: won't do" in text and "Next occurrence" in text

    def test_ids_links_and_refs_name_the_task(self, client, wiki_dir):
        full = client.post("/api/deck", json={"title": "By id"}, headers=HOST).get_json()["id"]
        for ref in (full, full[:8], f"[[task:{full[:8]}]]", f"http://localhost:5000/deck?task={full}"):
            text, err = call(client, "update_task", id=ref, notes=ref)
            assert not err, ref

    def test_errors(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="T")[0])
        assert "No task with id" in call(client, "update_task", id="deadbeef", title="x")[0]
        assert "not a task id" in call(client, "update_task", id="hello", title="x")[0]
        text, err = call(client, "update_task", id=tid)
        assert err and "Nothing to change" in text
        assert "status must be" in call(client, "update_task", id=tid, status="finished")[0]
        assert call(client, "update_task", id=tid, title="")[1]

    def test_ambiguous_prefix(self, client, wiki_dir):
        import json
        tasks = [{"id": f"abcd{i}000-0000-0000-0000-000000000000", "title": f"T{i}"} for i in (1, 2)]
        (wiki_dir / "_deck" / "tasks.json").write_text(json.dumps(tasks))
        text, err = call(client, "update_task", id="abcd", title="x")
        assert err and "Several tasks" in text
        assert not call(client, "update_task", id="abcd1", title="x")[1]


class TestCommentAndDeleteTask:
    def test_comment(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Call Bob")[0])
        text, err = call(client, "comment_task", id=tid, text="left a voicemail")
        assert not err and text.startswith("Comment added.")
        assert re.search(r"Comments:\n- \d{4}-\d{2}-\d{2} \d{2}:\d{2}: left a voicemail", text)
        assert call(client, "comment_task", id=tid, text=" ")[1]

    def test_delete(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Oops")[0])
        text, err = call(client, "delete_task", id=tid)
        assert not err and text == f"Deleted task {tid}: Oops"
        assert call(client, "search_tasks", status="all")[0].endswith("No matching tasks.")
        assert call(client, "delete_task", id=tid)[1]
