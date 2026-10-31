"""Tests for the MCP server: the /mcp endpoint with its auth, the tool
definitions, and the tools (search_wiki, search_tasks, read_page,
list_pages, grep_wiki, check_links, the writing tools). The protocol's
handling and the limits Zone cuts long answers at have Go tests
(go/mcp_test.go).
"""

import json
from datetime import date, timedelta
import os
import re

import pytest

from tests import settings
from tests.conftest import HOST, content_hash, write_page
from tests.semantic import make_index


TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def mcp_token(monkeypatch):
    """/mcp is off without LEAF_MCP_TOKEN: every test here has one."""
    monkeypatch.setattr(settings, "LEAF_MCP_TOKEN", TOKEN)


def rpc(client, method, params=None, id_=1, headers=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return client.post("/mcp", json=msg, headers={**HOST, **AUTH, **(headers or {})})


def call(client, tool, /, **args):
    resp = rpc(client, "tools/call", {"name": tool, "arguments": args})
    assert resp.status_code == 200
    result = resp.get_json()["result"]
    return result["content"][0]["text"], result["isError"]


# ── Protocol ─────────────────────────────────────────────────────────────────

class TestToolDefinitions:
    """tools/list: what every client session loads."""

    def tools(self, client):
        return rpc(client, "tools/list").get_json()["result"]["tools"]

    def test_names_and_hints(self, client):
        tools = self.tools(client)
        assert [t["name"] for t in tools] == [
            "search_wiki", "search_tasks", "grep_wiki", "read_page", "list_pages", "check_links",
            "list_tags", "history", "leaf_help", "read_guides",
            "write_page", "edit_page", "replace_in_pages", "append_to_page", "file_scratch_note", "rename_page",
            "delete_page", "restore_page", "create_task", "update_task"]
        reading = {"search_wiki", "search_tasks", "grep_wiki", "read_page", "list_pages", "check_links",
                   "list_tags", "history", "leaf_help", "read_guides"}
        assert all(t["annotations"]["readOnlyHint"] == (t["name"] in reading) for t in tools)
        adding = {"append_to_page", "create_task"}
        assert all(t["annotations"]["destructiveHint"] == (t["name"] not in adding)
                   for t in tools if t["name"] not in reading)
        assert all(t["inputSchema"]["type"] == "object" for t in tools)

    def test_they_stay_small(self, client):
        # Every session loads them all. Parameters many tools share keep
        # short descriptions; read_guides explains the gate once.
        tools = {t["name"]: t for t in self.tools(client)}
        assert len(json.dumps(list(tools.values()))) < 25000
        props = tools["write_page"]["inputSchema"]["properties"]
        assert len(props["summary"]["description"]) < 100 and len(props["guides"]["description"]) < 100
        assert "refused until its id" in tools["read_guides"]["description"]
        assert "as create_task takes it" in tools["update_task"]["inputSchema"]["properties"]["due"]["description"]

    def test_the_writes_take_guides(self, client):
        writes = {"write_page", "edit_page", "replace_in_pages", "append_to_page", "rename_page",
                  "delete_page", "restore_page", "read_guides", "file_scratch_note"}
        for tool in self.tools(client):
            assert ("guides" in tool["inputSchema"]["properties"]) == (tool["name"] in writes), tool["name"]

    def test_a_plain_date_is_the_deadline(self, client):
        fields = next(t for t in self.tools(client) if t["name"] == "create_task")["inputSchema"]["properties"]
        assert "'tomorrow'" in fields["due"]["description"] and "not in start" in fields["due"]["description"]
        assert "a plain date is the deadline (due)" in fields["start"]["description"]


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
        monkeypatch.setattr(settings, "LEAF_MCP_TOKEN", "")
        for headers in ({}, {"Authorization": "Bearer "}, AUTH):
            resp = rpc(client, "ping", headers=headers)
            assert resp.status_code == 403
            assert "make a token with ./run.sh config token" in resp.get_json()["error"]
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
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")
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

    def test_a_nested_section_shows_and_is_found_by_its_parent_headings(self, client, pages):
        write_page(pages, "rollout", "# Rollout\n\n## Problems\n\n### Windows\n\nCrashes at startup.\n")
        first = call(client, "search_wiki", query="rollout problems windows")[0].split("\n\n---\n\n")[0]
        assert first.startswith("[1] Rollout › Problems › Windows")
        assert "url: http://localhost:5000/page/rollout#windows" in first
        assert first.endswith("Crashes at startup.")

    def test_queries_are_logged_only_when_asked(self, client, pages, monkeypatch):
        log = pages / ".leaf" / "queries.jsonl"
        call(client, "search_wiki", query="git push")
        assert not log.exists()
        monkeypatch.setattr(settings, "LEAF_QUERY_LOG", True)
        call(client, "search_wiki", query="git push")
        call(client, "search_wiki", query="Mechaniker", under="golf")
        first, second = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        assert first["q"] == "git push" and "under" not in first
        assert first["results"][0] == "git-remote#pushing"
        assert second["under"] == "golf" and second["results"] == ["golf#werkstatt"]
        assert first["at"][:2] == "20"

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
        idx = make_index()
        idx.sync()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
        text, _ = call(client, "search_wiki", query="car repair")
        assert text.startswith("[1] Golf › Werkstatt")
        assert "#werkstatt" in text

    def test_keywords_only_without_semantic(self, client, pages, monkeypatch):
        monkeypatch.setattr(settings, "SEMANTIC", None)
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
        assert call(client, "list_pages", under="nope")[0] == 'No pages under "nope".'

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
        idx = make_index()
        idx.sync()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
        task(client, title="Call the mechaniker")
        text, _ = call(client, "search_wiki", query="mechaniker")
        assert "Task: Call the mechaniker" in text and "Golf › Werkstatt" in text


class TestTasksByMeaning:
    @pytest.fixture()
    def semantic_on(self, pages, monkeypatch):
        idx = make_index()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
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
        monkeypatch.setattr(settings, "LEAF_BASE_URL", "")
        resp = client.post("/mcp", headers={"Host": "host.containers.internal:5000", **AUTH}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "read_page", "arguments": {"slug": "golf"}}})
        assert "url: http://host.containers.internal:5000/page/golf\n" in \
            resp.get_json()["result"]["content"][0]["text"]

    def test_base_url_wins(self, client, pages, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_BASE_URL", "http://wiki.example.org:5000")
        assert "url: http://wiki.example.org:5000/page/golf\n" in call(client, "read_page", slug="golf")[0]
        assert "url: http://wiki.example.org:5000/page/git-remote#pushing" in \
            call(client, "search_wiki", query="git push")[0]


class TestHostFile:
    def test_wiki_path_without_host_data(self, client, pages, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_HOST_DATA", "")
        first = call(client, "search_wiki", query="git push")[0].split("\n\n---\n\n")[0]
        assert f"#pushing\nfile: {pages.resolve()}/git-remote.md\n\nRun git push" in first

    def test_host_data_wins(self, client, pages, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_HOST_DATA", "/home/me/leaf")
        assert "\nfile: /home/me/leaf/git-remote.md\n" in call(client, "search_wiki", query="git push")[0]
        assert "\nfile: /home/me/leaf/golf.md\n" in call(client, "read_page", slug="golf")[0]

    def test_subfolder_and_odd_names_kept(self, client, pages, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_HOST_DATA", "/home/me/leaf")
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
        golf = (pages / "golf.md").read_text()
        assert text.startswith("slug: golf\nurl: http://localhost:5000/page/golf\n"
                               f"file: {pages.resolve()}/golf.md\nhash: {content_hash(golf)}\n\n# Golf\n")

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

    def test_counts_pages_linking_to_each(self, client, pages):
        write_page(pages, "a", "# A\n\n[[golf]] and [[Git Remote]].\n")
        write_page(pages, "b", "# B\n\nSee [Golf](golf.md), and [[b]] itself.\n")
        lines = call(client, "list_pages")[0].splitlines()
        assert lines[0].endswith("slug | title | last edit | pages linking to it:")
        rows = {line.split(" | ")[0]: line.rsplit(" | ", 1)[1] for line in lines[1:]}
        assert rows == {"golf": "2", "git-remote": "1", "a": "0", "b": "0", "index": "0"}

    def test_orphans(self, client, pages):
        write_page(pages, "a", "# A\n\n[[golf]]\n")
        write_page(pages, "2026-10-08_14-03-11_idea", "# Idea\n")       # a scratch note
        write_page(pages, "tasks", "# Tasks\n\n[[deck home=1]]\n")     # the deck's home
        text, err = call(client, "list_pages", orphans=True)
        assert not err and text.startswith("Pages no other page links to")
        assert sorted(line.split(" | ")[0] for line in text.splitlines()[1:]) == ["a", "git-remote"]
        assert call(client, "list_pages", orphans=True, under="golf")[0] == \
            "Every page under golf/ is linked from another page."
        assert call(client, "list_pages", orphans="yes")[1]


class TestGuide:
    """The wiki-guide page: part of the instructions a client gets when it
    connects."""

    def instructions(self, client):
        return rpc(client, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]["instructions"]

    def test_what_every_session_needs_fits_before_a_clients_cut(self, client, wiki_dir):
        # Claude Code cuts a server's instructions at about 2 KB.
        text = self.instructions(client)
        assert len(text.encode()) < 1900
        for needed in ("search_wiki", "leaf_help", "read_guides", "offer to add it",
                       "Write only when the user agrees"):
            assert needed in text
        write_page(wiki_dir, "wiki-guide", "# Wiki guide\n\n" + "x" * 5000)
        assert len(self.instructions(client).split("# Wiki guide")[0].encode()) < 1900

    def test_without_a_guide_a_nudge_to_start_one(self, client, wiki_dir):
        text = self.instructions(client)
        assert text.startswith("Zone is the user's personal wiki")
        assert text.endswith("it is shown to you at the start of every session.")
        assert "This wiki has no guide yet" in text

    def test_the_guide_follows_the_instructions(self, client, wiki_dir):
        base = self.instructions(client).split("This wiki has no guide yet")[0]
        write_page(wiki_dir, "wiki-guide", "# Wiki guide\n\n- Tickets go in tickets/<id>.\n")
        text = self.instructions(client)
        assert text.startswith(base)
        assert "The user's guide for this wiki (the page 'wiki-guide'; read_guides gives it too" in text
        assert text.endswith("\n\n# Wiki guide\n\n- Tickets go in tickets/<id>.")
        # Read when a client connects: an edit shows in the next session.
        write_page(wiki_dir, "wiki-guide", "# Wiki guide\n\n- People notes stay private.\n")
        assert self.instructions(client).endswith("- People notes stay private.")

    def test_not_an_orphan(self, client, wiki_dir):
        write_page(wiki_dir, "wiki-guide", "# Wiki guide\n")
        assert call(client, "list_pages", orphans=True)[0] == "Every page is linked from another page."


def guide_ids(text):
    """The ids a guide refusal tells the model to pass."""
    return re.findall(r"\(id ([^)]+)\)", text)


class TestFolderGuides:
    """Guides (wiki-guide, a folder's CLAUDE.md or AGENTS.md): a page write
    under one is refused until the model passes its id."""

    @pytest.fixture()
    def guided(self, wiki_dir):
        write_page(wiki_dir, "tickets/CLAUDE", "# Tickets\n\nOne folder per ticket, summary.md in it.\n")
        write_page(wiki_dir, "tickets/bug-1", "# BUG-1\n\nOld.\n")
        return wiki_dir

    def test_a_write_without_guides_is_refused_with_the_guide(self, client, guided):
        text, err = call(client, "write_page", slug="tickets/bug-2", content="# BUG-2\n")
        assert err
        assert text.startswith("Nothing was written.")
        assert "=== guide tickets/CLAUDE (id tickets/CLAUDE:" in text
        assert "One folder per ticket, summary.md in it." in text
        assert not (guided / "tickets" / "bug-2.md").exists()

    def test_with_the_id_it_goes_through(self, client, guided):
        ids = guide_ids(call(client, "write_page", slug="tickets/bug-2", content="# BUG-2\n")[0])
        text, err = call(client, "write_page", slug="tickets/bug-2", content="# BUG-2\n", guides=ids)
        assert not err and text.startswith("Created page tickets/bug-2.")
        # Every page write is gated, and the same id keeps working.
        for name, args in [("edit_page", {"slug": "tickets/bug-1", "old_string": "Old.", "new_string": "New."}),
                           ("append_to_page", {"slug": "tickets/bug-1", "content": "More."}),
                           ("rename_page", {"slug": "tickets/bug-2", "new_slug": "tickets/bug-3"}),
                           ("delete_page", {"slug": "tickets/bug-3"})]:
            assert call(client, name, **args)[0].startswith("Nothing was written."), name
            text, err = call(client, name, **args, guides=ids)
            assert not err, (name, text)

    def test_ids_as_a_comma_separated_string(self, client, guided):
        ids = guide_ids(call(client, "append_to_page", slug="tickets/bug-1", content="x")[0])
        assert not call(client, "append_to_page", slug="tickets/bug-1", content="x", guides=", ".join(ids))[1]
        assert call(client, "append_to_page", slug="tickets/bug-1", content="x", guides=5)[1]

    def test_pages_elsewhere_are_not_gated(self, client, guided):
        assert not call(client, "write_page", slug="notes", content="# Notes\n")[1]
        assert not call(client, "write_page", slug="other/page", content="# Page\n")[1]

    def test_the_wiki_guide_and_parent_folders_apply(self, client, guided):
        write_page(guided, "wiki-guide", "# Wiki guide\n\nTags in lowercase.\n")
        write_page(guided, "tickets/bug-1/AGENTS", "# BUG-1\n\nOnly summary.md here.\n")
        text = call(client, "write_page", slug="tickets/bug-1/summary", content="# BUG-1\n")[0]
        ids = guide_ids(text)
        assert [i.split(":")[0] for i in ids] == ["wiki-guide", "tickets/CLAUDE", "tickets/bug-1/AGENTS"]
        # The ids known so far are passed: only the missing guide is shown.
        text, err = call(client, "write_page", slug="tickets/bug-1/summary", content="# BUG-1\n", guides=ids[:2])
        assert err and guide_ids(text) == ids[2:] and "Tags in lowercase" not in text
        assert not call(client, "write_page", slug="tickets/bug-1/summary", content="# BUG-1\n", guides=ids)[1]
        # The wiki's guide applies everywhere.
        assert guide_ids(call(client, "write_page", slug="notes", content="# Notes\n")[0]) == ids[:1]

    def test_a_changed_guide_needs_its_new_id(self, client, guided):
        ids = guide_ids(call(client, "append_to_page", slug="tickets/bug-1", content="x")[0])
        write_page(guided, "tickets/CLAUDE", "# Tickets\n\nNew rule: a TL;DR first.\n")
        text, err = call(client, "append_to_page", slug="tickets/bug-1", content="x", guides=ids)
        assert err and "New rule: a TL;DR first." in text and guide_ids(text) != ids

    def test_writing_a_guide_gives_its_new_id(self, client, guided):
        ids = guide_ids(call(client, "append_to_page", slug="tickets/CLAUDE", content="x")[0])
        text, err = call(client, "append_to_page", slug="tickets/CLAUDE", content="- A rule.", guides=ids)
        new = re.search(r"its id is now (\S+):", text).group(1)
        assert not err and new != ids[0]
        assert not call(client, "append_to_page", slug="tickets/bug-1", content="x", guides=[new])[1]

    def test_imports_are_resolved_once(self, client, guided):
        write_page(guided, "wiki-guide", "# Wiki guide\n\nTags in lowercase.\n")
        write_page(guided, "CLAUDE", "@wiki-guide.md\n")       # only the import: no second guide
        write_page(guided, "messages/shared", "# Shared\n\nSign as G.\n")
        write_page(guided, "messages/CLAUDE", "# Messages\n\n@shared.md\n@../outside.md\n@missing.md\n")
        text = call(client, "write_page", slug="messages/m1", content="# M1\n")[0]
        assert [i.split(":")[0] for i in guide_ids(text)] == ["wiki-guide", "messages/CLAUDE"]
        assert "# Messages\n\n# Shared\n\nSign as G.\n@../outside.md\n@missing.md" in text

    def test_replace_in_pages_is_gated_on_writing_only(self, client, guided):
        assert not call(client, "replace_in_pages", find="Old", replace="New")[1]     # a dry run
        text, err = call(client, "replace_in_pages", find="Old", replace="New", dry_run=False)
        assert err and guide_ids(text)
        assert not call(client, "replace_in_pages", find="Old", replace="New", dry_run=False,
                        guides=guide_ids(text))[1]
        assert "New." in (guided / "tickets" / "bug-1.md").read_text()

    def test_moving_into_a_guided_folder(self, client, guided):
        write_page(guided, "loose", "# Loose\n")
        assert guide_ids(call(client, "rename_page", slug="loose", new_slug="tickets/loose")[0])
        write_page(guided, "old/a", "# A\n")
        assert guide_ids(call(client, "rename_page", slug="old/", new_slug="tickets/old")[0])

class TestReadGuides:
    """read_guides: a page's or folder's guides before drafting, their ids
    good for the writes; the folders that have guides in its description."""

    @pytest.fixture()
    def guided(self, wiki_dir):
        write_page(wiki_dir, "wiki-guide", "# Wiki guide\n\nTags in lowercase.\n")
        write_page(wiki_dir, "messages/CLAUDE", "# Messages — Project Instructions\n\nSign as G.\n")
        write_page(wiki_dir, "messages/old", "# Old\n")
        return wiki_dir

    def tool_list(self, client):
        tools = rpc(client, "tools/list").get_json()["result"]["tools"]
        return {t["name"]: t for t in tools}

    def test_for_a_new_page(self, client, guided):
        text, err = call(client, "read_guides", slug="messages/2026-10-11_reply")
        assert not err
        assert text.startswith("Guides for messages/2026-10-11_reply, the whole wiki's first.")
        assert text.index("Tags in lowercase.") < text.index("Sign as G.")
        ids = guide_ids(text)
        assert [i.split(":")[0] for i in ids] == ["wiki-guide", "messages/CLAUDE"]
        # Reading them is enough: the write goes through at once.
        assert not call(client, "write_page", slug="messages/2026-10-11_reply", content="# R\n", guides=ids)[1]

    def test_for_a_folder_an_existing_page_and_the_wiki(self, client, guided):
        for slug in ("messages/", "messages", "/page/messages/old", "messages/old.md"):
            assert len(guide_ids(call(client, "read_guides", slug=slug)[0])) == 2, slug
        assert call(client, "read_guides", slug="messages")[0].startswith("Guides for messages/,")
        assert [i.split(":")[0] for i in guide_ids(call(client, "read_guides")[0])] == ["wiki-guide"]

    def test_known_guides_are_named_not_repeated(self, client, guided):
        ids = guide_ids(call(client, "read_guides")[0])
        text = call(client, "read_guides", slug="messages/", guides=ids)[0]
        assert f"=== guide wiki-guide (id {ids[0]}): you have it ===" in text
        assert "Tags in lowercase." not in text and "Sign as G." in text

    def test_no_guides(self, client, wiki_dir):
        assert call(client, "read_guides", slug="notes/x")[0] == \
            "No guides apply to notes/x: write as the conversation and the wiki suggest."

    def test_the_description_lists_the_guided_folders(self, client, guided):
        write_page(guided, "tickets/bug-1/AGENTS", "Only summary.md here.\n")
        write_page(guided, "_deck/CLAUDE", "# Not a page\n")
        desc = self.tool_list(client)["read_guides"]["description"]
        assert desc.endswith(" Guides in this wiki:\n- the whole wiki: Wiki guide\n"
                             "- messages/: Messages — Project Instructions\n- tickets/bug-1/: tickets/bug-1/AGENTS")
        # Other tools as they are.
        defined = json.loads((settings.REPO / "go" / "mcp_tools.json").read_text(encoding="utf-8"))
        assert self.tool_list(client)["read_page"] == next(t for t in defined if t["name"] == "read_page")

    def test_read_page_of_a_guide_gives_its_id(self, client, guided):
        text = call(client, "read_page", slug="messages/CLAUDE")[0]
        gid = re.search(r"guide id: (\S+) \(pass it as guides with writes under messages\)", text).group(1)
        root = re.search(r"guide id: (\S+)", call(client, "read_page", slug="wiki-guide")[0]).group(1)
        assert not call(client, "write_page", slug="messages/x", content="# X\n", guides=[root, gid])[1]
        assert "\nguide" not in call(client, "read_page", slug="messages/old")[0].split("\n\n")[0]
        write_page(guided, "messages/CLAUDE", "# Messages\n\n@old.md\n")
        assert "guide: yes; it imports other pages" in call(client, "read_page", slug="messages/CLAUDE")[0]


class TestReadSection:
    @pytest.fixture()
    def long(self, pages):
        write_page(pages, "long", "# Long\n\nIntro.\n\n## Setup\n\nSteps.\n\n### Detail\n\nMore.\n"
                                  "\n```\n## not a heading\n```\n\n## Usage\n\nUse [[Nowhere]].\n")
        return pages

    def test_one_section_with_its_subsections(self, client, long):
        text, err = call(client, "read_page", slug="long", heading="setup")
        assert not err
        head, _, rest = text.partition("\n\n")
        assert head.endswith("\nsection: lines 5–16 of 19")
        assert rest.startswith("## Setup\n\nSteps.\n\n### Detail\n\nMore.\n\n```\n## not a heading\n```\n")
        assert "## Usage" not in rest and "Broken links" not in rest

    def test_heading_with_hashes_and_its_links(self, client, long):
        text = call(client, "read_page", slug="long", heading="## Usage")[0]
        assert "\n\n## Usage\n\nUse [[Nowhere]].\n" in text
        assert text.endswith("Broken links (no such page): [[Nowhere]]")

    def test_missing_heading_lists_headings(self, client, long):
        text, err = call(client, "read_page", slug="long", heading="Install")
        assert err and '"Setup", "Detail", "Usage"' in text


class TestListTags:
    def test_pages_and_tasks_most_used_first(self, client, pages):
        write_page(pages, "a", "---\ntags: [Backend, infra]\n---\n# A\n")
        write_page(pages, "b", "---\ntags: backend\n---\n# B\n")
        write_page(pages, "shop", "# Shop\n\n[[deck list=shopping]]\n")
        task(client, title="t1", tags=["backend"])
        close(client, task(client, title="t2", tags=["backend", "infra"])["id"], completed=True)
        task(client, title="milk", tags=["shopping"])
        text, err = call(client, "list_tags")
        assert not err
        assert text.splitlines()[1:] == ["backend | 2 | 1 | 1", "infra | 1 | 0 | 1",
                                         "shopping (a list) | 0 | 1 | 0"]

    def test_none(self, client, pages):
        assert call(client, "list_tags")[0].startswith("No tags yet")


class TestRelatedByMeaning:
    """With semantic search: read_page names pages close in meaning that
    aren't linked, and a new page names the pages closest in content."""

    @pytest.fixture()
    def semantic_on(self, pages, monkeypatch):
        write_page(pages, "brakes", "# Brakes\n\nThe bremsen need the mechaniker.\n")
        write_page(pages, "budget", "# Budget\n\nRent and savings.\n")
        idx = make_index()
        idx.sync()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
        return idx

    def test_read_page_lists_related_not_linked(self, client, pages, semantic_on):
        assert call(client, "read_page", slug="golf")[0].endswith(
            "\nRelated, not linked (close in meaning): brakes")
        write_page(pages, "golf", "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, [[brakes]].\n")
        semantic_on.sync()
        assert "Related, not linked" not in call(client, "read_page", slug="golf")[0]

    def test_new_page_names_pages_close_in_content(self, client, semantic_on):
        text, err = call(client, "write_page", slug="car-trouble",
                         content="# Car trouble\n\nThe car needs a repair, bremsen again.")
        assert not err
        # Both score the same (ties come in the wiki's order: the listing's, or A–Z in Go).
        note = re.search(r"\nNote: the pages closest to it in content: (.*?)\. If one already covers", text)
        assert note and sorted(note.group(1).split(", ")) == ["brakes", "golf"]
        assert "car-trouble" not in text.split("closest")[1]          # not itself
        text = call(client, "write_page", slug="sleep", content="# Sleep\n\nTired every evening.")[0]
        assert "closest to it in content" not in text                 # nothing close enough

    def test_linked_pages_left_out(self, client, semantic_on):
        text = call(client, "write_page", slug="car-trouble",
                    content="# Car trouble\n\nThe car needs a repair, see [[golf]].")[0]
        assert "closest to it in content: brakes." in text

    def test_without_semantic_nothing(self, client, pages):
        assert "Related" not in call(client, "read_page", slug="golf")[0]
        assert "closest" not in call(client, "write_page", slug="x", content="# X\n\ncar repair")[0]


class TestReadPageLinks:
    def test_linked_from_and_broken_links(self, client, pages):
        write_page(pages, "a", "# A\n\n[[golf]], [[Nowhere]], [[golf#Werkstatt|the garage]]\n"
                               "\n`[[In Code]]`\n\n[[deck]]\n")
        write_page(pages, "b", "# B\n\n[Golf](golf.md)\n")
        text = call(client, "read_page", slug="golf")[0]
        assert text.endswith("\n\n---\nLinked from (2): a, b")
        text = call(client, "read_page", slug="a")[0]
        assert text.endswith("\n\n---\nLinked from (0): no other page\n"
                             "Broken links (no such page): [[Nowhere]]")

class TestCheckLinks:
    def test_reports_broken_links_with_a_guess(self, client, pages):
        write_page(pages, "a", "# A\n\n[[Golff]] and [[Nowhere At All]] and [[golf]]\n")
        write_page(pages, "tickets/t", "# T\n\n[[Missing]]\n\n```\n[[In Code]]\n```\n")
        text, err = call(client, "check_links")
        assert not err
        assert text.splitlines() == [
            "3 links point to pages that don't exist, on 2 pages (fix the link, or create the page):",
            "a: [[Golff]] (maybe [[golf]]?), [[Nowhere At All]]",
            "tickets/t: [[Missing]]"]
        assert call(client, "check_links", under="tickets")[0].endswith("\ntickets/t: [[Missing]]")

    def test_relative_markdown_links_are_checked_too(self, client, pages):
        # Relative to the page's folder, absolute /page/ links too; files, folders, anchors and external links are not pages.
        write_page(pages, "notes/index", "# Notes\n")
        write_page(pages, "knowledge/nginx/b", "# B\n")
        write_page(pages, "knowledge/nginx/a", (
            "# A\n\n[ok](b.md) [up](../../notes/index.md) [gone](../../notes/nope.md) [bare](b) "
            "[gone2](/page/nowhere) [file](_assets/x.pdf) [folder](../../notes/) "
            "[web](https://example.com/z.md) [anchor](#top) [raw](../../_raw/x.md) [daily](/page/_daily)\n"))
        text, err = call(client, "check_links")
        assert not err
        assert text.splitlines() == [
            "2 links point to pages that don't exist, on 1 pages (fix the link, or create the page):",
            "knowledge/nginx/a: [gone2](/page/nowhere), [gone](../../notes/nope.md)"]

    def test_a_write_notes_the_new_broken_links(self, client, pages):
        write_page(pages, "knowledge/nginx/b", "# B\n")
        text, err = call(client, "write_page", slug="knowledge/nginx/c",
                         content="# C\n\n[b](b.md) [gone](../notes/nope.md)\n")
        assert not err
        assert text.endswith(
            "Note: these new links point to pages that don't exist (yet): [gone](../notes/nope.md)")

    def test_none(self, client, pages):
        assert call(client, "check_links")[0] == "No broken links."
        assert call(client, "check_links", under="golf")[0] == "No broken links under golf/."


class TestGrepWiki:
    @pytest.fixture()
    def notes(self, pages):
        write_page(pages, "tickets/bug-1", "# BUG-1\n\nSee BUG-12345 and bug-777.\n- [ ] follow up\n")
        write_page(pages, "log", "# Log\n\nBUG-99 is old, BUG-12345 again.\n")
        write_page(pages, "_assets/notes", "BUG-55555 in an asset folder\n")
        return pages

    def test_regex_lines_by_page(self, client, notes):
        text, err = call(client, "grep_wiki", pattern=r"BUG-\d{4,}")
        assert not err
        assert text == ("2 matching lines on 2 pages:\n"
                        "log\n  3: BUG-99 is old, BUG-12345 again.\n\n"
                        "tickets/bug-1\n  3: See BUG-12345 and bug-777.")

    def test_ignore_case_and_under(self, client, notes):
        text = call(client, "grep_wiki", pattern=r"\bbug-\d+", ignore_case=True, under="tickets")[0]
        assert text.startswith("2 matching lines on 1 pages under tickets/:")
        assert "log" not in text
        assert call(client, "grep_wiki", pattern=r"^- \[ \]")[0].endswith("  4: - [ ] follow up")

    def test_limit_says_how_many_more(self, client, notes):
        text = call(client, "grep_wiki", pattern="BUG", limit=2)[0]
        assert text.startswith("3 matching lines on 2 pages, the first 2 shown")
        assert len([line for line in text.splitlines() if line.startswith("  ")]) == 2

    def test_long_lines_are_cut(self, client, pages):
        write_page(pages, "long", "x" * 500 + " needle\n")
        assert call(client, "grep_wiki", pattern="x+")[0].endswith("x" * 300 + " …")

    def test_no_match_and_bad_pattern(self, client, notes):
        assert call(client, "grep_wiki", pattern="zebra")[0] == 'No line matches "zebra".'
        text, err = call(client, "grep_wiki", pattern="(unclosed")
        assert err and text.startswith("grep_wiki: ")
        assert call(client, "grep_wiki", pattern=" ")[1]
        assert call(client, "grep_wiki", pattern="a", ignore_case="no")[1]

    def test_finds_uncommitted_pages(self, client, git_wiki_dir):
        write_page(git_wiki_dir, "fresh", "# Fresh\n\nnot committed yet\n")
        assert "fresh\n  3: not committed yet" in call(client, "grep_wiki", pattern="committed")[0]

    def test_without_git_plain_text(self, client, notes, monkeypatch):
        monkeypatch.setattr(settings, "GIT_ENABLED", False)
        text = call(client, "grep_wiki", pattern="bug-12345", ignore_case=True)[0]
        assert text.startswith("2 matching lines on 2 pages; git isn't installed")
        assert call(client, "grep_wiki", pattern=r"BUG-\d+")[0] == r'No line matches "BUG-\\d+".'


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

    PLANS = "# Plans\n\n## One\n\nFirst.\n\n## Two\n\nSecond.\n"

    def read_hash(self, client, wiki_dir):
        write_page(wiki_dir, "plans", self.PLANS)
        text, _ = call(client, "read_page", slug="plans")
        found = re.search(r"^hash: (\w+)$", text, re.M)
        assert found and found[1] == content_hash(self.PLANS)
        return found[1]

    def test_overwrite_from_an_older_read_merges(self, client, git_wiki_dir):
        """The page changed since read_page (the user edited another line):
        the overwrite with base_hash keeps that change."""
        hash_ = self.read_hash(client, git_wiki_dir)
        (git_wiki_dir / "plans.md").write_text(self.PLANS.replace("Second.", "Second, by the user."))
        text, err = call(client, "write_page", slug="plans", overwrite=True, base_hash=hash_,
                         content=self.PLANS.replace("First.", "First, by the model."))
        assert not err and "were merged" in text
        assert (git_wiki_dir / "plans.md").read_text() == \
            self.PLANS.replace("First.", "First, by the model.").replace("Second.", "Second, by the user.")

    def test_overwrite_clashing_with_a_change_writes_nothing(self, client, git_wiki_dir):
        hash_ = self.read_hash(client, git_wiki_dir)
        theirs = self.PLANS.replace("First.", "First, by the user.")
        (git_wiki_dir / "plans.md").write_text(theirs)
        text, err = call(client, "write_page", slug="plans", overwrite=True, base_hash=hash_,
                         content=self.PLANS.replace("First.", "First, by the model."))
        assert err and "Nothing written" in text
        assert "<<<<<<< yours\nFirst, by the model.\n=======\nFirst, by the user.\n>>>>>>> theirs" in text
        assert "## Two" not in text                    # only where they clash
        assert (git_wiki_dir / "plans.md").read_text() == theirs

    def test_overwrite_from_a_version_leaf_doesnt_have_writes_nothing(self, client, git_wiki_dir):
        self.read_hash(client, git_wiki_dir)
        text, err = call(client, "write_page", slug="plans", overwrite=True, base_hash="0123456789abcdef",
                         content="# Plans\n\nNew.\n")
        assert err and "no longer has that version" in text
        assert (git_wiki_dir / "plans.md").read_text() == self.PLANS

    def test_overwrite_with_the_current_hash_just_writes(self, client, git_wiki_dir):
        hash_ = self.read_hash(client, git_wiki_dir)
        text, err = call(client, "write_page", slug="plans", overwrite=True, base_hash=hash_, content="# Plans\n\nNew.")
        assert not err and "merged" not in text and (git_wiki_dir / "plans.md").read_text() == "# Plans\n\nNew.\n"

    def test_names_pages_with_similar_names(self, client, pages):
        text, err = call(client, "write_page", slug="notes/git-remotes", content="# Git Remotes\n")
        assert not err
        assert "pages with similar names exist" in text and "- git-remote | Git remote" in text
        text, _ = call(client, "write_page", slug="cooking", content="# Cooking\n")
        assert "similar names" not in text

    def test_warns_about_wikilinks_to_missing_pages(self, client, pages):
        text, _ = call(client, "write_page", slug="hub",
                       content="# Hub\n\n[[Golf]] [[Nowhere]] [[task:abcd1234]] `[[Code]]`")
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

    def test_several_edits_all_or_nothing(self, client, pages):
        text, err = call(client, "edit_page", slug="golf", edits=[
            {"old_string": "Werkstatt", "new_string": "Garage"},
            {"old_string": "## Garage", "new_string": "## Garage (Mai)"},   # sees the edit before it
            {"old_string": "quietschen", "new_string": "neu"}])
        assert not err and text.startswith("Edited page golf: applied 3 edits, 3 occurrences.")
        assert (pages / "golf.md").read_text() == "# Golf\n\n## Garage (Mai)\n\nTermin beim Mechaniker, Bremsen neu.\n"
        text, err = call(client, "edit_page", slug="golf", edits=[
            {"old_string": "Golf", "new_string": "Polo"}, {"old_string": "Reifen", "new_string": "x"}])
        assert err and text.startswith("Edit 2: old_string was not found")
        assert "Polo" not in (pages / "golf.md").read_text()

    def test_edits_arguments(self, client, pages):
        assert call(client, "edit_page", slug="golf", edits=[])[1]
        assert call(client, "edit_page", slug="golf", edits=["x"])[1]
        assert "not both" in call(client, "edit_page", slug="golf", old_string="Golf", new_string="x",
                                  edits=[{"old_string": "a", "new_string": "b"}])[0]
        assert call(client, "edit_page", slug="golf")[1]

    def test_crlf_in_arguments_matches_lf_files(self, client, pages):
        _, err = call(client, "edit_page", slug="git-remote", old_string="## Setup\r\n\r\nSet",
                      new_string="## Setup\r\n\r\nPut")
        assert not err and "## Setup\n\nPut LEAF_GIT_REMOTE" in (pages / "git-remote.md").read_text()

    def test_unknown_page_and_no_op(self, client, pages):
        text, err = call(client, "edit_page", slug="nope", old_string="a", new_string="b")
        assert err and 'No page "nope"' in text
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
        assert err and '"Setup", "Pushing"' in text


SCRATCH_NOTE = "2026-10-15_09-30-00_alex-prep"


def git(wiki, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


def subjects(wiki):
    import subprocess
    return subprocess.run(["git", "log", "--format=%s"], cwd=wiki, capture_output=True,
                          text=True, check=True, env=settings.GIT_ENV).stdout.splitlines()


class TestScratchNotes:
    """Scratch notes over MCP: write_page's scratch, list_pages' and
    search_wiki's scratch filter, file_scratch_note (scratch.py)."""

    def test_write_page_makes_a_scratch_note(self, client, git_wiki_dir):
        text, err = call(client, "write_page", slug="Alex prep", content="Ask about the roadmap.",
                         scratch=True, summary="for Friday")
        assert not err
        slug = re.search(r"Created scratch note (\S+) ", text).group(1)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_alex-prep", slug)
        assert "file_scratch_note" in text
        # No title in the content: the name becomes it.
        assert (git_wiki_dir / f"{slug}.md").read_text() == "# Alex prep\n\nAsk about the roadmap.\n"
        assert subjects(git_wiki_dir)[0] == f"Create {slug}: for Friday (MCP)"

    def test_the_contents_title_names_it_and_frontmatter_stays_first(self, client, wiki_dir):
        text, _ = call(client, "write_page", slug="", content="# Idea: a/b test\n\nbody", scratch=True)
        slug = re.search(r"Created scratch note (\S+) ", text).group(1)
        assert slug.endswith("_idea-a-b-test") and "/" not in slug
        text, _ = call(client, "write_page", slug="Tagged", content="---\ntags: [x]\n---\nbody", scratch=True)
        slug = re.search(r"Created scratch note (\S+) ", text).group(1)
        assert (wiki_dir / f"{slug}.md").read_text() == "---\ntags: [x]\n---\n# Tagged\n\nbody\n"

    def test_same_name_in_the_same_second_makes_two_notes(self, client, wiki_dir):
        slugs = {re.search(r"Created scratch note (\S+) ", call(client, "write_page", slug="Twin",
                                                                  content="x", scratch=True)[0]).group(1)
                 for _ in range(3)}
        assert len(slugs) == 3

    def test_scratch_refusals(self, client, wiki_dir):
        text, err = call(client, "write_page", slug="", content="no title", scratch=True)
        assert err and "title" in text
        text, err = call(client, "write_page", slug="x", content="# X", scratch=True, overwrite=True)
        assert err and "always a new one" in text

    def test_list_and_search_only_scratch_notes(self, client, pages):
        write_page(pages, SCRATCH_NOTE, "# Alex prep\n\nBremsen beim Golf ansprechen.\n")
        text, _ = call(client, "list_pages", scratch=True)
        assert text.startswith("Notes") and SCRATCH_NOTE in text and "golf |" not in text
        text, _ = call(client, "search_wiki", query="Bremsen", scratch=True)
        assert f"slug: {SCRATCH_NOTE}" in text and "slug: golf" not in text
        assert f"slug: golf" in call(client, "search_wiki", query="Bremsen")[0]
        (pages / f"{SCRATCH_NOTE}.md").unlink()
        assert call(client, "list_pages", scratch=True)[0] == "No scratch notes."

    def test_move_to_the_wiki(self, client, git_wiki_dir):
        write_page(git_wiki_dir, SCRATCH_NOTE, "# Alex prep\n\nnotes\n")
        write_page(git_wiki_dir, "hub", f"# Hub\n\n[[{SCRATCH_NOTE}]]\n")
        text, err = call(client, "file_scratch_note", slug=SCRATCH_NOTE, name="Alex Sam debrief",
                         folder="conversations", keep_date=True)
        assert not err
        new = "conversations/2026-10-15_alex-sam-debrief"
        assert text.startswith(f"Moved note {SCRATCH_NOTE} into the wiki as {new};")
        assert (git_wiki_dir / f"{new}.md").exists() and not (git_wiki_dir / f"{SCRATCH_NOTE}.md").exists()
        assert (git_wiki_dir / "hub.md").read_text() == f"# Hub\n\n[[{new}]]\n"
        assert subjects(git_wiki_dir)[0] == f"Promote {SCRATCH_NOTE} to {new} (MCP)"

    def test_append_to_a_page(self, client, git_wiki_dir):
        write_page(git_wiki_dir, SCRATCH_NOTE, "---\ntags: [people]\n---\n# Alex prep\n\n## Topics\n\nRoadmap\n")
        write_page(git_wiki_dir, "people/alex", "# Alex\n\nLead.\n")
        write_page(git_wiki_dir, "hub", f"# Hub\n\n[[{SCRATCH_NOTE}]]\n")
        text, err = call(client, "file_scratch_note", slug=SCRATCH_NOTE, page="people/alex", summary="1:1")
        assert not err
        assert "as the section “Alex prep (2026-10-15)”" in text
        assert "url: http://localhost:5000/page/people/alex#alex-prep-2026-10-15" in text
        page = (git_wiki_dir / "people" / "alex.md").read_text()
        assert page.startswith("---\ntags: people\n---\n# Alex\n")
        assert page.endswith("## Alex prep (2026-10-15)\n\n### Topics\n\nRoadmap\n")
        assert not (git_wiki_dir / f"{SCRATCH_NOTE}.md").exists()
        assert "[[people/alex#Alex prep (2026-10-15)" in (git_wiki_dir / "hub.md").read_text()
        assert subjects(git_wiki_dir)[0] == f"Append {SCRATCH_NOTE} to people/alex: 1:1 (MCP)"

    def test_file_refusals(self, client, pages):
        write_page(pages, SCRATCH_NOTE, "# Alex prep\n")
        for args, words in [({"slug": "golf", "name": "x"}, "not a note"),
                            ({"slug": SCRATCH_NOTE}, "either name"),
                            ({"slug": SCRATCH_NOTE, "name": "x", "page": "golf"}, "either name"),
                            ({"slug": SCRATCH_NOTE, "page": "nowhere"}, 'No page "nowhere"'),
                            ({"slug": SCRATCH_NOTE, "name": "golf"}, "already exists"),
                            ({"slug": SCRATCH_NOTE, "name": "2026-01-01_10-00-00_x"}, "still be a note"),
                            ({"slug": SCRATCH_NOTE, "page": "golf", "heading": "Werkstatt"}, "already has a section")]:
            text, err = call(client, "file_scratch_note", **args)
            assert err and words in text, (args, text)
        assert (pages / f"{SCRATCH_NOTE}.md").exists()


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

    def test_a_moved_page_keeps_its_own_relative_links(self, client, pages):
        write_page(pages, "golf", "# Golf\n\nSee [hub](hub.md), [bug](tickets/bug-1) and [[hub]].\n")
        write_page(pages, "hub", "# Hub\n")
        write_page(pages, "tickets/bug-1", "# Bug 1\n")
        text, err = call(client, "rename_page", slug="golf", new_slug="cars/golf")
        assert not err
        assert (pages / "cars" / "golf.md").read_text() == (
            "# Golf\n\nSee [hub](../hub.md), [bug](../tickets/bug-1) and [[hub]].\n")

    def test_refusals(self, client, pages):
        assert call(client, "rename_page", slug="index", new_slug="home")[1]
        text, err = call(client, "rename_page", slug="golf", new_slug="git-remote")
        assert err and "already exists" in text
        assert call(client, "rename_page", slug="golf", new_slug="golf")[1]


class TestRenameFolder:
    @pytest.fixture()
    def tree(self, wiki_dir):
        write_page(wiki_dir, "tickets/bug-1", "# Bug 1\n\nSee [[tickets/sub/bug-2]].\n")
        write_page(wiki_dir, "tickets/sub/bug-2", "# Bug 2\n")
        write_page(wiki_dir, "tickets-old", "# Not in the folder\n")
        write_page(wiki_dir, "hub", "# Hub\n\n[[tickets/bug-1]] and [two](/page/tickets/sub/bug-2)\n")
        (wiki_dir / "tickets" / "shot.png").write_bytes(b"png")
        return wiki_dir

    def test_moves_every_page_and_their_links(self, client, tree):
        tid = client.post("/api/deck", json={"title": "Fix [[tickets/bug-1]]"}, headers=HOST).get_json()["id"]
        text, err = call(client, "rename_page", slug="tickets/", new_slug="work/tickets")
        assert not err and text.startswith("Moved folder tickets/ to work/tickets/: 2 pages.")
        assert "- tickets/sub/bug-2 → work/tickets/sub/bug-2" in text
        assert (tree / "work/tickets/bug-1.md").read_text() == "# Bug 1\n\nSee [[work/tickets/sub/bug-2]].\n"
        assert (tree / "work/tickets/sub/bug-2.md").exists()
        assert (tree / "hub.md").read_text() == (
            "# Hub\n\n[[work/tickets/bug-1]] and [two](/page/work/tickets/sub/bug-2)\n")
        assert (tree / "tickets-old.md").exists()
        assert call(client, "search_tasks", query="Fix")[0].count("[[work/tickets/bug-1]]") == 1 and tid
        # Other files stay, so the folder does; emptied subfolders go.
        assert (tree / "tickets" / "shot.png").exists() and not (tree / "tickets" / "sub").exists()

    def test_moved_pages_relative_links_still_reach_their_targets(self, client, tree):
        write_page(tree, "tickets/bug-3", "# Bug 3\n\n[up](../hub.md) and [sib](sub/bug-2.md)\n")
        text, err = call(client, "rename_page", slug="tickets/", new_slug="work/tickets")
        assert not err
        assert (tree / "work/tickets/bug-3.md").read_text() == (
            "# Bug 3\n\n[up](../../hub.md) and [sib](sub/bug-2.md)\n")

    def test_a_folder_without_the_slash_when_no_page_has_its_name(self, client, tree):
        text, err = call(client, "rename_page", slug="tickets", new_slug="Archive/Tickets")
        assert not err and "to archive/tickets/" in text
        assert (tree / "archive/tickets/bug-1.md").exists()

    def test_a_page_of_the_folders_name_wins_without_the_slash(self, client, tree):
        write_page(tree, "tickets", "# Tickets\n")
        text, err = call(client, "rename_page", slug="tickets", new_slug="ticket-list")
        assert not err and text.startswith("Renamed page tickets to ticket-list.")
        assert (tree / "tickets/bug-1.md").exists()

    def test_refusals(self, client, tree):
        write_page(tree, "done/bug-1", "# Taken\n")
        text, err = call(client, "rename_page", slug="tickets/", new_slug="done")
        assert err and "done/bug-1" in text and (tree / "tickets/sub/bug-2.md").exists()
        assert not (tree / "done/sub").exists()
        text, err = call(client, "rename_page", slug="tickets/", new_slug="tickets/inner")
        assert err and "inside itself" in text
        assert call(client, "rename_page", slug="tickets/", new_slug="tickets")[1]
        assert call(client, "rename_page", slug="tickets/", new_slug="_hidden")[1]
        assert call(client, "rename_page", slug="nothing/", new_slug="x")[1]


class TestReplaceInPages:
    @pytest.fixture()
    def notes(self, wiki_dir):
        write_page(wiki_dir, "a", "# A\n\nBUG-12 and bug-13 here.\n\n```\nBUG-12 in a block\n```\n\nand `BUG-12` inline\n")
        write_page(wiki_dir, "tickets/b", "# B\n\nFixed BUG-99.\n")
        write_page(wiki_dir, "c", "# C\n\nnothing\n")
        return wiki_dir

    def test_dry_run_shows_and_writes_nothing(self, client, notes):
        before = (notes / "a.md").read_text()
        text, err = call(client, "replace_in_pages", find="BUG-", replace="ONE-")
        assert not err and text.startswith("Dry run, nothing written: would replace 2 occurrences on 2 pages outside code.")
        assert "\na\n  3: - BUG-12 and bug-13 here.\n  3: + ONE-12 and bug-13 here." in text
        assert "tickets/b\n  3: - Fixed BUG-99." in text
        assert (notes / "a.md").read_text() == before

    def test_writes_outside_code(self, client, notes):
        text, err = call(client, "replace_in_pages", find="BUG-", replace="ONE-", ignore_case=True, dry_run=False)
        assert not err and text.startswith("Replaced 3 occurrences on 2 pages outside code, in one commit: a, tickets/b")
        assert (notes / "a.md").read_text() == (
            "# A\n\nONE-12 and ONE-13 here.\n\n```\nBUG-12 in a block\n```\n\nand `BUG-12` inline\n")
        assert (notes / "tickets/b.md").read_text() == "# B\n\nFixed ONE-99.\n"

    def test_include_code(self, client, notes):
        call(client, "replace_in_pages", find="BUG-12", replace="X", include_code=True, dry_run=False)
        assert (notes / "a.md").read_text().count("X") == 3

    def test_regex_groups_and_under(self, client, notes):
        text, err = call(client, "replace_in_pages", find=r"BUG-(\d+)", replace=r"[[tickets/bug-\1]]",
                         regex=True, under="tickets", dry_run=False)
        assert not err and (notes / "tickets/b.md").read_text() == "# B\n\nFixed [[tickets/bug-99]].\n"
        assert "BUG-12 and" in (notes / "a.md").read_text()
        assert "don't exist (yet): [[tickets/bug-99]]" in text

    def test_max_pages_guards_the_write(self, client, notes):
        text, err = call(client, "replace_in_pages", find="BUG-", replace="X", max_pages=1)
        assert not err and "more pages than max_pages (1)" in text
        text, err = call(client, "replace_in_pages", find="BUG-", replace="X", max_pages=1, dry_run=False)
        assert err and "2 pages, more than max_pages (1)" in text
        assert "BUG-99" in (notes / "tickets/b.md").read_text()

    def test_dry_run_limit(self, client, notes):
        text, _ = call(client, "replace_in_pages", find="#", replace="##", limit=1)
        assert "the first 1 of 4 changed lines shown" in text and "tickets/b" not in text

    def test_no_match_and_errors(self, client, notes):
        assert call(client, "replace_in_pages", find="zzz", replace="y")[0] == 'Nothing matches "zzz" outside code.'
        assert call(client, "replace_in_pages", find="a\nb", replace="y")[1]
        assert call(client, "replace_in_pages", find="(", replace="y", regex=True)[1]
        text, err = call(client, "replace_in_pages", find="BUG-(\\d+)", replace=r"\2", regex=True)
        assert err and "replace:" in text
        assert call(client, "replace_in_pages", find="OA", replace="y", under="nope")[1]
        assert call(client, "replace_in_pages", find="OA", replace="y", dry_run="no")[1]

    def test_a_regex_re2_cant_take_is_refused(self, client, notes):
        # Lookarounds and backreferences can backtrack without end: RE2 has none.
        for find in ("BUG(?=-12)", r"(BUG)-\1"):
            text, err = call(client, "replace_in_pages", find=find, replace="y", regex=True)
            assert err and "no lookarounds or backreferences" in text, text
        assert "BUG-12" in (notes / "a.md").read_text()

    def test_literal_is_not_a_regex(self, client, notes):
        write_page(notes, "d", "# D\n\ncost $5 (approx.)\n")
        call(client, "replace_in_pages", find="$5 (approx.)", replace=r"\1 €5", dry_run=False)
        assert (notes / "d.md").read_text() == "# D\n\ncost \\1 €5\n"


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


@pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")
class TestWritesCommit:
    def test_each_write_is_a_commit_marked_mcp(self, client, git_wiki_dir, monkeypatch):
        import subprocess
        def subjects():
            return subprocess.run(["git", "log", "--format=%s"], cwd=git_wiki_dir, capture_output=True,
                                  text=True, check=True, env=settings.GIT_ENV).stdout.splitlines()

        call(client, "write_page", slug="mcp-notes", content="# MCP notes\n\nfirst", summary="start notes")
        call(client, "append_to_page", slug="mcp-notes", content="second")
        call(client, "edit_page", slug="mcp-notes", old_string="first", new_string="1st",
             summary="  shorten\n wording ")
        call(client, "rename_page", slug="mcp-notes", new_slug="notes/mcp")
        tid = call(client, "create_task", title="Ship it", summary="from chat")[0]
        short = re.search(r"\nid: (\w+)", tid).group(1)
        call(client, "update_task", id=short, status="done")
        call(client, "replace_in_pages", find="1st", replace="first", dry_run=False, summary="spell out")
        call(client, "rename_page", slug="notes/", new_slug="old-notes")
        call(client, "delete_page", slug="old-notes/mcp")
        assert subjects()[:9] == [
            "Delete old-notes/mcp (MCP)",
            "Move notes/ to old-notes/ (MCP)",
            'Replace "1st" on notes/mcp: spell out (MCP)',
            "Complete task (MCP)",
            "Create task: from chat (MCP)",
            "Rename mcp-notes to notes/mcp (MCP)",
            "Update mcp-notes: shorten wording (MCP)",
            "Append to mcp-notes (MCP)",
            "Create mcp-notes: start notes (MCP)",
        ]
        status = subprocess.run(["git", "status", "--porcelain"], cwd=git_wiki_dir, capture_output=True,
                                text=True, env=settings.GIT_ENV).stdout
        assert status == ""


@pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")
class TestOpenInTheEditor:
    """The editor auto-saves without committing. A model's write must neither
    take that draft into its own commit nor lose it."""

    @pytest.fixture()
    def draft(self, client, git_wiki_dir):
        call(client, "write_page", slug="p", content="# P\n\nline one")
        resp = client.post("/api/save/p", data={"content": "# P\n\nline one\nMY DRAFT\n"}, headers=HOST)
        assert resp.status_code == 200
        return git_wiki_dir

    def log(self, wiki):
        import subprocess
        return subprocess.run(["git", "log", "--format=%s"], cwd=wiki, capture_output=True, text=True,
                              env=settings.GIT_ENV).stdout.splitlines()

    def test_draft_committed_as_the_users_first(self, client, draft):
        text, err = call(client, "edit_page", slug="p", old_string="line one", new_string="line ONE")
        assert not err and 'committed first, as "Save p", and kept' in text
        assert self.log(draft)[:2] == ["Update p (MCP)", "Save p"]
        diff = git(draft, "show", "--format=", "HEAD", "--", "p.md")
        assert "+line ONE" in diff and "+MY DRAFT" not in diff

    def test_overwrite_keeps_the_draft_in_history(self, client, draft):
        call(client, "write_page", slug="p", content="# P\n\nreplaced", overwrite=True)
        assert self.log(draft)[1] == "Save p"
        assert "MY DRAFT" in git(draft, "show", "HEAD~1:p.md")

    def test_delete_keeps_the_draft_in_history(self, client, draft):
        call(client, "delete_page", slug="p")
        assert self.log(draft)[:2] == ["Delete p (MCP)", "Save p"]

    def test_editor_then_sees_a_conflict(self, client, draft):
        hash_before = content_hash("# P\n\nline one\nMY DRAFT\n")
        call(client, "append_to_page", slug="p", content="from the model")
        resp = client.post("/api/save/p", data={"content": "# P\n\nmine\n", "file_hash": hash_before},
                           headers=HOST)
        assert resp.status_code == 409          # the editor shows "File was modified" instead of overwriting
        assert "from the model" in (draft / "p.md").read_text()

    def test_nothing_pending_no_note(self, client, git_wiki_dir):
        call(client, "write_page", slug="q", content="# Q")
        text, _ = call(client, "edit_page", slug="q", old_string="# Q", new_string="# Q2")
        assert "Note" not in text and self.log(git_wiki_dir)[0] == "Update q (MCP)"


@pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")
class TestHistory:
    @pytest.fixture()
    def versions(self, client, git_wiki_dir):
        call(client, "write_page", slug="notes/p", content="# P\n\nv1", summary="first")
        call(client, "edit_page", slug="notes/p", old_string="v1", new_string="v2")
        call(client, "write_page", slug="other", content="# Other")
        call(client, "create_task", title="A task")
        return git_wiki_dir

    def test_recent_changes(self, client, versions):
        text, err = call(client, "history")
        assert not err
        rows = text.splitlines()
        assert rows[0].startswith("Changes, newest first (")
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d \| [0-9a-f]{7} \| Create task \(MCP\) \| tasks", rows[1])
        assert rows[2].endswith("| Create other (MCP) | other")
        assert rows[4].endswith("| Create notes/p: first (MCP) | notes/p")
        assert call(client, "history", under="notes")[0].count("\n") == 2
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert call(client, "history", since=tomorrow)[0] == f"No changes since {tomorrow}."
        assert call(client, "history", since="yesterday")[1]

    def test_lists_uncommitted(self, client, versions):
        (versions / "other.md").write_text("# Other\n\nedited on disk\n")
        text = call(client, "history")[0]
        assert text.startswith("Not committed yet (edited in the browser or on disk): other (")

    def test_page_versions_and_a_diff(self, client, versions):
        text, err = call(client, "history", slug="notes/p")
        assert not err
        rows = text.splitlines()
        assert rows[0].startswith("Versions of notes/p, newest first (all 2)")
        assert rows[1].endswith("| +1 -1 | Update notes/p (MCP)")
        sha = rows[2].split(" | ")[0]
        diff = call(client, "history", slug="notes/p", version=sha)[0]
        assert diff.startswith(f"notes/p, version {sha} of ") and "+v1" in diff
        assert call(client, "history", slug="notes/p", version="deadbeef")[1]
        assert call(client, "history", version=sha)[1]
        assert call(client, "history", slug="nope")[1]

    def test_restore_a_version(self, client, versions):
        sha = call(client, "history", slug="notes/p")[0].splitlines()[2].split(" | ")[0]
        text, err = call(client, "restore_page", slug="notes/p", version=sha, summary="undo")
        assert not err and text.startswith(f"Restored notes/p as of version {sha}")
        assert (versions / "notes" / "p.md").read_text() == "# P\n\nv1\n"
        assert self_log(versions)[0] == f"Restore notes/p to {sha}: undo (MCP)"
        assert "already reads as" in call(client, "restore_page", slug="notes/p", version=sha)[0]

    def test_bring_back_a_deleted_page(self, client, versions):
        call(client, "delete_page", slug="other")
        rows = call(client, "history", slug="other")[0].splitlines()
        assert "(deleted" in rows[0]
        deleted_sha, created_sha = rows[1].split(" | ")[0], rows[2].split(" | ")[0]
        assert "pick the version before it" in call(client, "restore_page", slug="other", version=deleted_sha)[0]
        text, err = call(client, "restore_page", slug="other", version=created_sha)
        assert not err and text.startswith("Brought back the deleted page other")
        assert (versions / "other.md").read_text() == "# Other\n"

    def test_without_git(self, client, pages, monkeypatch):
        monkeypatch.setattr(settings, "GIT_ENABLED", False)
        assert "git isn't installed" in call(client, "history")[0]


def self_log(wiki):
    import subprocess
    return subprocess.run(["git", "log", "--format=%s"], cwd=wiki, capture_output=True, text=True,
                          env=settings.GIT_ENV).stdout.splitlines()


# ── Writing tasks ────────────────────────────────────────────────────────────

def _task_id(text: str) -> str:
    return re.search(r"\nid: (\w+)", text).group(1)


class TestCreateTask:
    def test_suggests_the_tags_of_similar_tasks(self, client, wiki_dir):
        task(client, title="Call the plumber about the boiler", tags=["home", "calls"])
        done = task(client, title="Boiler service appointment", tags=["home"])
        close(client, done["id"], completed=True)
        task(client, title="Quarterly report", tags=["work"])
        text, err = call(client, "create_task", title="Boiler pressure check", tags=["calls"])
        assert not err
        hint = text.split("Tags on similar tasks")[1]
        assert "#home (2)" in hint and "#work" not in hint and "#calls" not in hint
        assert "New tags" not in text
        # Nothing alike: no suggestion.
        assert "similar tasks" not in call(client, "create_task", title="Zebra crossing")[0]

    def test_names_tags_used_nowhere_else(self, client, wiki_dir):
        write_page(wiki_dir, "notes", "---\ntags: [meetings]\n---\n# Notes\n")
        task(client, title="Standup", tags=["work"])
        text, _ = call(client, "create_task", title="Sync", tags=["meeting", "work"])
        assert "New tags, used nowhere else yet: #meeting." in text

    def test_creates_with_fields(self, client, wiki_dir):
        text, err = call(client, "create_task", title="Renew passport", notes="at the Bürgeramt",
                         tags=["admin", "#home"], due="2026-11-01", start="2026-10-20")
        assert not err and text.startswith(f"Created (today is {date.today()}).\n\nTask: Renew passport\n")
        assert "status: open · tags: #admin #home · start: 2026-10-20 · due: 2026-11-01" in text
        assert re.search(r"\nid: [0-9a-f]{8}\n", text)
        assert "at the Bürgeramt" in text

    def test_repeat_and_keep(self, client, wiki_dir):
        text, _ = call(client, "create_task", title="Water plants", due="2026-10-10",
                       repeat="weekly", repeat_every=2, repeat_from="completion")
        assert "repeats every 2 weeks after completion" in text
        text, _ = call(client, "create_task", title="Milk", keep=True, repeat="daily")
        assert "keep task" in text and "repeats" not in text

    def test_date_words_use_leafs_today(self, client, wiki_dir):
        # "tomorrow" is resolved by Zone, so a client without the date (or
        # with a wrong one) still gets it right.
        today = date.today()
        text, err = call(client, "create_task", title="Call Bob", due="tomorrow", start="today")
        assert not err
        assert f"start: {today} · due: {today + timedelta(days=1)}" in text
        tid = _task_id(text)
        text, _ = call(client, "update_task", id=tid, due="+2w")
        assert f"due: {today + timedelta(weeks=2)}" in text
        friday = today + timedelta(days=(4 - today.weekday() + 6) % 7 + 1)
        assert f"due: {friday}" in call(client, "update_task", id=tid, due="Fri")[0]

    def test_new_tasks_get_the_deck_homes_tags(self, client, wiki_dir):
        # The home deck shows list=task: a task without the tag wasn't in it.
        write_page(wiki_dir, "my-deck", "# My deck\n\n[[deck list=task tag=work view=focus home=1]]\n")
        write_page(wiki_dir, "shop", "[[deck list=groceries]]\n")
        assert "tags: #task #work" in call(client, "create_task", title="Report")[0]
        assert "tags: #task #work #urgent" in call(client, "create_task", title="Fix", tags=["urgent"])[0]
        assert "tags: #task #work #x" in call(client, "create_task", title="Y", tags="x")[0]
        # Filed in another list: just its tags.
        text = call(client, "create_task", title="Milk", tags=["Groceries"])[0]
        assert "tags: #Groceries" in text and "#task" not in text

    def test_bad_input_is_a_tool_error(self, client, wiki_dir):
        text, err = call(client, "create_task", title="x", due="next week")
        assert err and "YYYY-MM-DD, today, tomorrow" in text
        assert call(client, "create_task", title="x", due="2026-02-30")[1]
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
        # Due 3 days ago: the next week's date is still to come (a date
        # already past would be skipped, see deck.md Completing).
        due = (date.today() - timedelta(days=3)).isoformat()
        nxt = (date.today() + timedelta(days=4)).isoformat()
        tid = _task_id(call(client, "create_task", title="Backup", due=due, repeat="weekly")[0])
        text, err = call(client, "update_task", id=tid, status="done")
        assert not err and "status: done" in text
        assert f"Next occurrence:\n\nTask: Backup\nstatus: open · due: {nxt}" in text
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


class TestTagTasks:
    """update_task with ids: tags on several tasks in one commit."""
    def _tasks(self, client):
        return {t["title"]: t for t in client.get("/api/deck", headers=HOST).get_json()}

    def test_adds_and_removes_on_several_in_one_commit(self, client, git_wiki_dir):
        a = _task_id(call(client, "create_task", title="Nails", tags=["old", "Work"])[0])
        b = _task_id(call(client, "create_task", title="Glue")[0])
        c = _task_id(call(client, "create_task", title="Saw")[0])
        call(client, "update_task", id=c, status="done")          # completed tasks too
        before = len(self_log(git_wiki_dir))
        text, err = call(client, "update_task", ids=[a, f"[[task:{b}]]", c],
                         add_tags=["#work", "diy"], remove_tags=["OLD"], summary="sort the shed")
        assert not err
        assert text.startswith("Tags changed on 3 of 3 tasks.\n\n")
        assert f"- Nails ({a}): #Work #diy" in text and f"- Glue ({b}): #work #diy" in text
        assert self._tasks(client)["Nails"]["tags"] == ["Work", "diy"]
        assert self._tasks(client)["Glue"]["tags"] == ["work", "diy"]
        done = client.get("/api/deck/completed", headers=HOST).get_json()
        assert done[0]["tags"] == ["work", "diy"]
        log = self_log(git_wiki_dir)
        assert len(log) == before + 1 and log[0] == "Tag tasks: sort the shed (MCP)"

    def test_says_which_had_it_already(self, client, wiki_dir):
        a = _task_id(call(client, "create_task", title="A", tags=["x"])[0])
        b = _task_id(call(client, "create_task", title="B")[0])
        text, _ = call(client, "update_task", ids=[a, b], add_tags="x")
        assert text.startswith("Tags changed on 1 of 2 tasks (the others had them so already).")

    def test_errors_change_nothing(self, client, wiki_dir):
        a = _task_id(call(client, "create_task", title="A")[0])
        text, err = call(client, "update_task", ids=[a, "deadbeef"], add_tags=["x"])
        assert err and "No task with id" in text
        assert self._tasks(client)["A"]["tags"] == []
        assert "Give add_tags or remove_tags" in call(client, "update_task", ids=[a])[0]
        assert "ids must be" in call(client, "update_task", ids=[], add_tags=["x"])[0]
        assert "ids must be" in call(client, "update_task", ids=a, add_tags=["x"])[0]
        assert "add_tags must be" in call(client, "update_task", ids=[a], add_tags=[3])[0]
        assert "At most" in call(client, "update_task", ids=[a] * 201, add_tags=["x"])[0]

    def test_ids_take_only_tags(self, client, wiki_dir):
        a = _task_id(call(client, "create_task", title="A")[0])
        text, err = call(client, "update_task", id=a, ids=[a], add_tags=["x"])
        assert err and "Give id or ids, not both" in text
        text, err = call(client, "update_task", ids=[a], add_tags=["x"], status="done", due="today")
        assert err and "only add_tags and remove_tags work, not due, status" in text
        assert self._tasks(client)["A"]["tags"] == [] and not self._tasks(client)["A"]["due_date"]


class TestCommentAndDeleteTask:
    """update_task's comment and status deleted."""
    def test_comment(self, client, git_wiki_dir):
        tid = _task_id(call(client, "create_task", title="Call Bob")[0])
        text, err = call(client, "update_task", id=tid, comment="left a voicemail", summary="bob")
        assert not err and text.startswith("Comment added.")
        assert re.search(r"Comments:\n- \d{4}-\d{2}-\d{2} \d{2}:\d{2}: left a voicemail", text)
        assert self_log(git_wiki_dir)[0] == "Comment on task: bob (MCP)"
        assert call(client, "update_task", id=tid, comment=" ")[1]

    def test_comment_with_changes(self, client, git_wiki_dir):
        tid = _task_id(call(client, "create_task", title="Call Bob")[0])
        text, err = call(client, "update_task", id=tid, status="done", comment="reached him")
        assert not err and text.startswith("Updated, comment added.")
        assert "status: done" in text and "reached him" in text
        assert self_log(git_wiki_dir)[:2] == ["Comment on task (MCP)", "Complete task (MCP)"]

    def test_bad_comment_changes_nothing(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Call Bob")[0])
        text, err = call(client, "update_task", id=tid, status="done", comment=" ")
        assert err and "comment must be" in text
        assert call(client, "search_tasks", status="open")[0].count("Call Bob") == 1

    def test_delete(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Oops")[0])
        text, err = call(client, "update_task", id=tid, status="deleted")
        assert not err and text == f"Deleted task {tid}: Oops"
        assert call(client, "search_tasks", status="all")[0].endswith("No matching tasks.")
        assert call(client, "update_task", id=tid, status="deleted")[1]

    def test_delete_takes_nothing_else(self, client, wiki_dir):
        tid = _task_id(call(client, "create_task", title="Oops")[0])
        text, err = call(client, "update_task", id=tid, status="deleted", comment="why")
        assert err and "status deleted takes nothing else, not comment" in text
        assert "Oops" in call(client, "search_tasks")[0]

    def test_old_tools_are_gone(self, client, wiki_dir):
        for name in ("tag_tasks", "comment_task", "delete_task"):
            r = rpc(client, "tools/call", {"name": name, "arguments": {}}).get_json()
            assert "Unknown tool" in json.dumps(r)


# ── Zone's documentation ─────────────────────────────────────────────────────

class TestLeafHelp:
    @pytest.fixture
    def docs(self, tmp_path, monkeypatch):
        """A docs root like the checkout's: README.md and docs/spec/."""
        spec = tmp_path / "docs" / "spec"
        spec.mkdir(parents=True)
        (tmp_path / "README.md").write_text("# Zone\n\nA wiki.\n\n## Quick start\n\nRun ./run.sh.\n")
        (spec / "deck.md").write_text(
            "# Deck\n\n## Views\n\nOpen, Plan and Focus.\n\n### Labels\n\n"
            "`labels=Catalogue,Plan,Shopping` gives the tabs other names.\n\n"
            "```\n# not a heading\n```\n\n## Dates\n\nDue and start dates.\n")
        (spec / "testing.md").write_text("# Testing\n\nFixtures and labels.\n")
        monkeypatch.setattr(settings, "DOCS", tmp_path)
        return tmp_path

    def test_instructions_point_to_it(self, client):
        res = rpc(client, "initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]
        assert "leaf_help" in res["instructions"]

    def test_query_finds_passages(self, client, docs):
        text, err = call(client, "leaf_help", query="tab labels")
        assert not err
        assert text.startswith("[1] Deck › Labels\ntopic: deck\nheading: Labels\n\n`labels=Catalogue")
        assert "Testing" not in text  # how Zone is tested is left out
        assert "leaf_help with its topic and heading" in text

    def test_query_limit(self, client, docs):
        text, _ = call(client, "leaf_help", query="dates views labels wiki", limit=1)
        assert "[1]" in text and "[2]" not in text

    def test_nothing_matches(self, client, docs):
        text, err = call(client, "leaf_help", query="xylophone")
        assert not err and text.startswith("Nothing in Zone's documentation matches")

    def test_topic_and_section(self, client, docs):
        text, err = call(client, "leaf_help", topic="deck")
        assert not err and text.startswith("topic: deck\n\n# Deck\n") and "## Dates" in text
        text, err = call(client, "leaf_help", topic="Deck.md", heading="views")
        assert not err
        assert text == ("topic: deck\n\n## Views\n\nOpen, Plan and Focus.\n\n### Labels\n\n"
                        "`labels=Catalogue,Plan,Shopping` gives the tabs other names.\n\n"
                        "```\n# not a heading\n```\n")

    def test_bad_topic_or_heading(self, client, docs):
        text, err = call(client, "leaf_help", topic="testing")
        assert err and text == 'No topic "testing". Topics: readme, deck.'
        text, err = call(client, "leaf_help", topic="deck", heading="Nope")
        assert err and '"Views", "Labels", "Dates"' in text and "not a heading" not in text
        text, err = call(client, "leaf_help", heading="Views")
        assert err and text == "heading needs a topic."

    def test_list_of_topics(self, client, docs):
        text, err = call(client, "leaf_help")
        assert not err
        assert "\nreadme: Zone\n  Quick start\n" in text
        assert "\ndeck: Deck\n  Views\n    Labels\n  Dates" in text

    def test_without_docs(self, client, monkeypatch):
        monkeypatch.setattr(settings, "DOCS", None)
        text, err = call(client, "leaf_help", query="deck")
        assert err and "isn't installed" in text

    def test_the_real_docs(self, client):
        """The checkout's README and spec, every spec file but testing.md."""
        spec = settings.REPO / "docs" / "spec"
        topics = re.findall(r"^(\w[\w-]*): ", call(client, "leaf_help")[0], re.M)
        assert topics == ["readme"] + sorted(p.stem for p in spec.glob("*.md") if p.stem != "testing")
        text, err = call(client, "leaf_help", query="deck tab names labels")
        assert not err and "topic: deck" in text

