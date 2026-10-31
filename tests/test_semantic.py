"""Semantic search through the server: hybrid ranking in search, the
indicator's state, and the Related section on page view. A fake embedder
stands in for the model (tests/semantic.py). The index itself (chunking,
the cache, floors, the worker, settings, the model's download) has Go
tests (go/embed_test.go)."""

import os
import re

import pytest

from tests import settings
from tests.conftest import HOST, write_page
from tests.semantic import RELATED_MIN_CHARS, make_index


@pytest.fixture()
def semantic(wiki_dir, monkeypatch):
    write_page(wiki_dir, "git-remote", "# Git remote\n\nSet LEAF_GIT_REMOTE, then push to copy every commit.\n")
    write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n")
    write_page(wiki_dir, "evening", "# Evening routine\n\nNo screens late, bed by eleven.\n")
    write_page(wiki_dir, "money", "# Budget\n\nRent and savings.\n")
    idx = make_index()
    idx.sync()
    monkeypatch.setattr(settings, "SEMANTIC", idx)
    return idx


def _bump_mtime(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# ── Server: hybrid search and Related ────────────────────────────────────────

def _search(client, q):
    return client.get("/api/search", query_string={"q": q}, headers=HOST).get_json()


class TestSemanticStatus:
    """The search dialog's indicator: the page renders the state, every
    /api/search says it in X-Zone-Semantic."""

    def _states(self, client):
        page = client.get("/page/index", headers=HOST).get_data(as_text=True)
        rendered = re.search(r'id="search-mode" class="search-mode" data-state="(\w+)"', page).group(1)
        return rendered, client.get("/api/search", query_string={"q": "golf"}, headers=HOST).headers["X-Zone-Semantic"]

    def test_off_without_a_semantic_index(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "SEMANTIC", None)
        assert self._states(client) == ("off", "off")

    def test_indexing_until_ready_then_on(self, client, wiki_dir, monkeypatch):
        write_page(wiki_dir, "golf", "# Golf\n\nMechaniker.\n")
        idx = make_index()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
        assert self._states(client) == ("indexing", "indexing")
        idx.sync()
        assert self._states(client) == ("on", "on")

    def test_failed_when_the_model_fails(self, client, wiki_dir, monkeypatch):
        write_page(wiki_dir, "golf", "# Golf\n\nMechaniker.\n")
        idx = make_index()
        idx.embedder.fail = True
        idx.sync()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
        assert self._states(client) == ("failed", "failed")


class TestHybridSearch:
    def test_semantic_only_hit_with_section_excerpt(self, client, semantic):
        results = _search(client, "car repair")
        assert results[0]["slug"] == "golf"
        assert results[0]["excerpt"].startswith("Werkstatt")
        assert results[0]["href"] == "/page/golf#werkstatt"   # its most similar section

    def test_matching_both_ways_beats_keyword_only(self, client, semantic, wiki_dir):
        # All three contain "golf"; only "sport" isn't about cars, so it is
        # the one page found by keyword alone and ranks last.
        write_page(wiki_dir, "sport", "# Sport\n\nGolf on Sundays, then an evening routine and early bed when tired.\n")
        write_page(wiki_dir, "zz-car", "# Car notes\n\nWerkstatt, Mechaniker, golf.\n")
        semantic.sync()
        slugs = [r["slug"] for r in _search(client, "golf")]
        assert set(slugs) == {"golf", "sport", "zz-car"}
        assert slugs[-1] == "sport"

    def test_nonsense_finds_nothing(self, client, semantic):
        assert _search(client, "xqzt blorf") == []

    def test_tasks_still_found_by_keyword(self, client, semantic):
        client.post("/api/tasks", json={"title": "Call the mechaniker"}, headers=HOST)
        results = _search(client, "mechaniker")
        assert any(r.get("type") == "task" for r in results)
        assert any(r["slug"] == "golf" for r in results)

    def test_deleted_page_not_returned_before_resync(self, client, semantic, wiki_dir):
        (wiki_dir / "golf.md").unlink()
        assert all(r["slug"] != "golf" for r in _search(client, "car repair"))

    def test_without_semantic_keywords_only(self, client, semantic, monkeypatch):
        monkeypatch.setattr(settings, "SEMANTIC", None)
        assert _search(client, "car repair") == []
        assert _search(client, "werkstatt")[0]["slug"] == "golf"

    def test_task_found_by_meaning(self, client, semantic):
        client.post("/api/tasks", json={"title": "Book the Werkstatt"}, headers=HOST)
        semantic.sync()
        results = _search(client, "car repair")
        assert any(r.get("type") == "task" and r["title"] == "[Task] Book the Werkstatt" for r in results)

    def test_keyword_section_beats_the_similar_one(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nMechaniker, Bremsen.\n\n"
                                     "## Papers\n\nThe insurance letter is in the drawer.\n")
        semantic.sync()
        r = next(r for r in _search(client, "drawer") if r["slug"] == "golf")
        assert r["href"] == "/page/golf?hl=drawer#papers"

    def test_filters_apply_to_meaning_hits(self, client, semantic):
        assert _search(client, "car repair")[0]["slug"] == "golf"
        assert _search(client, "car repair -bremsen") == []      # golf mentions Bremsen
        assert _search(client, 'car repair "no such phrase"') == []

    def test_search_page_uses_it_too(self, client, semantic):
        html = client.get("/search", query_string={"q": "car repair"}, headers=HOST).get_data(as_text=True)
        assert "/page/golf" in html


def _related(client, slug):
    html = client.get(f"/page/{slug}", headers=HOST).get_data(as_text=True)
    start = html.find('id="related"')
    if start < 0:
        return None
    section = html[start:html.find("</section>", start)]
    return re.findall(r'href="/page/([^"]+)"', section)


class TestRelated:
    def test_lists_similar_pages(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        assert _related(client, "golf") == ["brakes"]

    def test_title_shown(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brake pads\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        html = client.get("/page/golf", headers=HOST).get_data(as_text=True)
        assert ">Brake pads</a>" in html

    def test_linked_pages_left_out(self, client, semantic, wiki_dir):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        write_page(wiki_dir, "garage", "# Garage\n\nWerkstatt Mechaniker. See [[golf]].\n")
        _bump_mtime(write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nMechaniker, Bremsen. [[brakes]]\n"))
        semantic.sync()
        # brakes is linked from golf, garage links to golf: nothing left.
        assert _related(client, "golf") is None

    def test_absent_when_nothing_similar(self, client, semantic):
        assert _related(client, "evening") is None

    def test_absent_on_a_page_that_is_only_a_tasks_embed(self, client, wiki_dir, monkeypatch):
        long = "Werkstatt beim Mechaniker, Bremsen quietschen, Termin am Dienstag. " * 3
        write_page(wiki_dir, "golf", f"# Golf\n\n{long}\n")
        write_page(wiki_dir, "car-tasks", "# Golf tasks\n\n[[tasks: tag=golf]]\n")
        idx = make_index(related_min_chars=RELATED_MIN_CHARS)
        idx.sync()
        monkeypatch.setattr(settings, "SEMANTIC", idx)
        assert _related(client, "car-tasks") is None
        assert _related(client, "golf") is None  # car-tasks isn't offered either

    def test_absent_without_semantic(self, client, semantic, wiki_dir, monkeypatch):
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker.\n")
        semantic.sync()
        monkeypatch.setattr(settings, "SEMANTIC", None)
        assert _related(client, "golf") is None


class TestRelatedWithUsers:
    def test_fills_the_five_with_pages_the_user_may_see(self, client, semantic, wiki_dir, monkeypatch):
        """Alice's private pages are closer to golf than the shared one, and more than five:
        bob still gets the shared page, since only what he may see is ranked."""
        monkeypatch.setattr(settings, "LEAF_USERS", {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token")})
        for i in range(6):
            write_page(wiki_dir, f"private/alice/brakes-{i}", "# Brakes\n\nBremsen beim Mechaniker, Werkstatt Termin.\n")
        write_page(wiki_dir, "brakes", "# Brakes\n\nBremsen beim Mechaniker. Rent and savings.\n")
        semantic.sync()
        client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
        assert _related(client, "golf") == ["brakes"]
        client.post("/logout", headers=HOST)
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        assert len(_related(client, "golf")) == 5


class TestSearchWithUsers:
    """Alice's private page and task are the closest in meaning to Bob's query
    (no word in common, only the model links them). Every way to search by
    meaning leaves them out for Bob, and shows them to Alice."""

    @pytest.fixture()
    def alice_bob(self, client, semantic, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_USERS", {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "bob-token")})
        write_page(wiki_dir, "private/alice/policy",
                   "# Policy\n\nInsurance papers: the policy renewal is due, rent and savings stay.\n")
        semantic.sync()

    def _mcp(self, client, tool, token, **args):
        resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        return resp.get_json()["result"]["content"][0]["text"]

    def test_search_wiki_by_meaning(self, client, alice_bob):
        assert "private/alice/policy" not in self._mcp(client, "search_wiki", "bob-token", query="budget")
        assert "private/alice/policy" in self._mcp(client, "search_wiki", "alice-token", query="budget")

    def test_the_search_route_by_meaning(self, client, alice_bob):
        client.post("/login", data={"username": "bob", "password": "bob-pw"}, headers=HOST)
        assert "private/alice/policy" not in str(_search(client, "budget"))
        client.post("/logout", headers=HOST)
        client.post("/login", data={"username": "alice", "password": "alice-pw"}, headers=HOST)
        assert "private/alice/policy" in str(_search(client, "budget"))

    def test_tasks_by_meaning(self, client, semantic, alice_bob):
        self._mcp(client, "create_task", "alice-token", title="Renew insurance", private=True)
        semantic.sync()
        assert "Renew insurance" in self._mcp(client, "search_tasks", "alice-token", query="budget")
        assert "Renew insurance" not in self._mcp(client, "search_tasks", "bob-token", query="budget")

    def test_a_new_page_is_not_compared_with_her_private_pages(self, client, alice_bob):
        text = self._mcp(client, "write_page", "bob-token", slug="costs",
                         content="# Costs\n\n" + "Budget: rent and savings, insurance every month. " * 4)
        assert "private/alice" not in text
        text = self._mcp(client, "write_page", "alice-token", slug="private/alice/costs",
                         content="# Costs\n\n" + "Budget: rent and savings, insurance every month. " * 4)
        assert "private/alice/policy" in text   # the note exists, Bob's just had nothing to name
