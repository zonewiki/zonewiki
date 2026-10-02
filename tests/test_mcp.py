"""Tests for the MCP server: the protocol handler (src/mcp_server.py), the
/mcp endpoint with its auth, and the tools (search_wiki, read_page,
list_pages) backed by section-level retrieval in server.py.

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
        assert [t["name"] for t in tools] == ["search_wiki", "read_page", "list_pages"]
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
