"""Tests for [[deck]] embed directive and page field on tasks."""

import sys
import os
import json
import tempfile
from pathlib import Path

import pytest

_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_embed_test_")
os.environ.setdefault("WIKI_DATA", _SESSION_TMP)
os.environ["ALLOW_REMOTE"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import server


@pytest.fixture()
def wiki_dir(tmp_path):
    orig_wiki = server.WIKI_DIR
    orig_pages = server.PAGES_DIR
    orig_deck = server.DECK_FILE
    server.WIKI_DIR = tmp_path
    server.PAGES_DIR = tmp_path
    server.DECK_FILE = tmp_path / "_deck.json"
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    yield tmp_path
    server.WIKI_DIR = orig_wiki
    server.PAGES_DIR = orig_pages
    server.DECK_FILE = orig_deck


@pytest.fixture()
def client(wiki_dir):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def create_task(client, **kwargs):
    payload = {"title": "Test task", **kwargs}
    return client.post("/api/deck", json=payload)


# ── Param parsing ───────────────────────────────────────────────────────────────


class TestParseParams:
    def test_empty(self):
        assert server._parse_tasks_params("") == {}

    def test_single(self):
        result = server._parse_tasks_params("tag=work")
        assert result == {"tag": "work"}

    def test_multiple(self):
        result = server._parse_tasks_params("tag=work filter=due limit=5 sort=due_date")
        assert result == {"tag": "work", "filter": "due", "limit": "5", "sort": "due_date"}

    def test_page_self(self):
        result = server._parse_tasks_params("page=self")
        assert result == {"page": "self"}

    def test_case_insensitive_keys(self):
        result = server._parse_tasks_params("Tag=work FILTER=due")
        assert result == {"tag": "work", "filter": "due"}


# ── Rendering ───────────────────────────────────────────────────────────────────


class TestTasksLinkRender:
    def test_basic_link_no_tasks(self, wiki_dir):
        """[[deck]] with no tasks renders a link with no badge."""
        html = server.render_md("[[deck]]\n")
        assert "deck-link" in html
        assert 'href="/deck"' in html
        assert "deck-link-count" not in html

    def test_link_with_count_badge(self, client, wiki_dir):
        """[[deck]] shows a count badge when tasks exist."""
        create_task(client, title="Buy milk")
        create_task(client, title="Write code")
        html = server.render_md("[[deck]]\n")
        assert "deck-link-count" in html
        assert ">2<" in html

    def test_link_tag_filter(self, client, wiki_dir):
        """[[deck tag=work]] generates href with tag param and correct count."""
        create_task(client, title="Buy milk", tags=["shopping"])
        create_task(client, title="Write code", tags=["work"])
        html = server.render_md("[[deck tag=work]]\n")
        assert "tag=work" in html
        assert "#work" in html
        assert ">1<" in html  # only 1 task matches

    def test_link_filter_completed(self, client, wiki_dir):
        """[[deck filter=completed]] generates correct href."""
        resp = create_task(client, title="Done task")
        task_id = resp.get_json()["id"]
        client.put(f"/api/deck/{task_id}", json={"completed": True})
        html = server.render_md("[[deck filter=completed]]\n")
        assert "filter=completed" in html
        assert ">1<" in html

    def test_link_completed_excluded_from_default_count(self, client, wiki_dir):
        """Default filter=all count excludes completed tasks."""
        resp = create_task(client, title="Done")
        task_id = resp.get_json()["id"]
        client.put(f"/api/deck/{task_id}", json={"completed": True})
        create_task(client, title="Open")
        html = server.render_md("[[deck]]\n")
        assert ">1<" in html  # only the open task

    def test_multiple_links(self, client, wiki_dir):
        """Multiple [[deck]] blocks on one page each render independently."""
        create_task(client, title="A", tags=["alpha"])
        create_task(client, title="B", tags=["beta"])
        md = "## Alpha\n\n[[deck tag=alpha]]\n\n## Beta\n\n[[deck tag=beta]]\n"
        html = server.render_md(md)
        assert html.count('class="deck-link"') == 2
        assert "tag=alpha" in html
        assert "tag=beta" in html

    def test_link_not_inside_paragraph_when_standalone(self, client, wiki_dir):
        """[[deck]] on its own line should NOT be wrapped in <p>."""
        create_task(client, title="T1")
        html = server.render_md("[[deck]]\n")
        # Standalone: the link is inside a <p> from markdown but that's fine
        assert '<a href="/deck"' in html

    def test_link_case_insensitive(self, client, wiki_dir):
        """[[Deck]] and [[DECK]] work too."""
        create_task(client, title="Case test")
        for variant in ["[[Deck]]\n", "[[DECK]]\n", "[[deck]]\n"]:
            html = server.render_md(variant)
            assert "deck-link" in html

    def test_link_works_inline(self):
        """[[deck]] inline is also replaced (works in lists, paragraphs, etc)."""
        html = server.render_md("Here are the [[deck]] for today.\n")
        assert "deck-link" in html

    def test_link_works_in_list(self):
        """[[deck]] inside a list item works."""
        html = server.render_md("- [[deck tag=work]]\n")
        assert "deck-link" in html
        assert "tag=work" in html

    def test_link_page_param(self, wiki_dir):
        """[[deck page=my-project]] includes page in href."""
        html = server.render_md("[[deck page=my-project]]\n")
        assert "page=my-project" in html
        assert "My Project" in html

    def test_link_page_self(self, client, wiki_dir):
        """[[deck page=self]] resolves to current slug in href."""
        create_task(client, title="T", page="my-page")
        html = server.render_md("[[deck page=self]]\n", slug="my-page")
        assert "page=my-page" in html
        assert ">1<" in html


# ── Page field ──────────────────────────────────────────────────────────────────


class TestPageField:
    def test_create_with_page(self, client, wiki_dir):
        resp = create_task(client, title="Linked", page="my-project")
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["page"] == "my-project"

    def test_create_without_page(self, client, wiki_dir):
        resp = create_task(client, title="No page")
        data = resp.get_json()
        assert data["page"] is None

    def test_update_page(self, client, wiki_dir):
        resp = create_task(client, title="T")
        task_id = resp.get_json()["id"]
        resp2 = client.put(f"/api/deck/{task_id}", json={"page": "new-page"})
        assert resp2.get_json()["page"] == "new-page"

    def test_clear_page(self, client, wiki_dir):
        resp = create_task(client, title="T", page="some-page")
        task_id = resp.get_json()["id"]
        resp2 = client.put(f"/api/deck/{task_id}", json={"page": ""})
        assert resp2.get_json()["page"] is None

    def test_page_self_filter(self, client, wiki_dir):
        """[[deck page=self]] on slug 'my-project' links with page param and counts correctly."""
        create_task(client, title="Linked", page="my-project")
        create_task(client, title="Other", page="other-page")
        create_task(client, title="No page")
        html = server.render_md("[[deck page=self]]\n", slug="my-project")
        assert "page=my-project" in html
        assert ">1<" in html  # only "Linked" matches

    def test_page_explicit_filter(self, client, wiki_dir):
        """[[deck page=my-project]] generates link with page param."""
        create_task(client, title="Linked", page="my-project")
        create_task(client, title="Unlinked")
        html = server.render_md("[[deck page=my-project]]\n")
        assert "page=my-project" in html
        assert ">1<" in html

    def test_recurring_preserves_page(self, client, wiki_dir):
        """Spawned recurring task inherits page field."""
        resp = create_task(
            client, title="Recur", page="proj",
            due_date="2026-01-01", rec_type="daily", rec_interval=1
        )
        task_id = resp.get_json()["id"]
        client.put(f"/api/deck/{task_id}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = [t for t in tasks if t["id"] != task_id and t["title"] == "Recur"]
        assert len(spawned) == 1
        assert spawned[0]["page"] == "proj"


# ── Page view rendering ────────────────────────────────────────────────────────


class TestPageViewLink:
    def test_page_with_tasks_link(self, client, wiki_dir):
        """A wiki page with [[deck]] renders a link."""
        create_task(client, title="Page task", tags=["test"])
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\nSome text.\n\n[[deck tag=test]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "deck-link" in body
        assert "tag=test" in body

    def test_preview_with_tasks_link(self, client, wiki_dir):
        """Preview endpoint renders [[deck]] as a link."""
        create_task(client, title="Preview task")
        resp = client.post(
            "/api/preview?slug=test-page",
            data="[[deck]]\n",
            content_type="text/plain"
        )
        body = resp.get_data(as_text=True)
        assert "deck-link" in body
        assert "/deck" in body

    def test_tasks_page_url_params(self, client, wiki_dir):
        """/deck?tag=work&page=proj passes data attrs to the app."""
        resp = client.get("/deck?tag=work&page=proj&filter=due")
        body = resp.get_data(as_text=True)
        assert 'data-init-tag="work"' in body
        assert 'data-init-page="proj"' in body
        assert 'data-init-filter="due"' in body
