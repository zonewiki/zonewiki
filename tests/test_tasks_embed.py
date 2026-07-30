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
import deck as deck_module


@pytest.fixture()
def wiki_dir(tmp_path):
    orig_wiki = server.WIKI_DIR
    orig_pages = server.PAGES_DIR
    orig_deck = deck_module.DECK_FILE
    server.WIKI_DIR = tmp_path
    server.PAGES_DIR = tmp_path
    deck_module.DECK_FILE = tmp_path / "_deck.json"
    server.DECK_FILE = tmp_path / "_deck.json"
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    yield tmp_path
    server.WIKI_DIR = orig_wiki
    server.PAGES_DIR = orig_pages
    deck_module.DECK_FILE = orig_deck
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
        assert deck_module._parse_tasks_params("") == {}

    def test_single(self):
        result = deck_module._parse_tasks_params("tag=work")
        assert result == {"tag": "work"}

    def test_multiple(self):
        result = deck_module._parse_tasks_params("tag=work filter=due limit=5 sort=due_date")
        assert result == {"tag": "work", "filter": "due", "limit": "5", "sort": "due_date"}

    def test_page_self(self):
        result = deck_module._parse_tasks_params("page=self")
        assert result == {"page": "self"}

    def test_case_insensitive_keys(self):
        result = deck_module._parse_tasks_params("Tag=work FILTER=due")
        assert result == {"tag": "work", "filter": "due"}


# ── Rendering ───────────────────────────────────────────────────────────────────


class TestTasksLinkRender:
    def test_basic_embed_no_tasks(self, wiki_dir):
        """[[deck]] renders the full deck app embed."""
        html = server.render_md("[[deck]]\n")
        assert "tasks-app" in html
        assert "tasks-list" in html
        assert "deck.js" in html

    def test_embed_with_tag_filter(self, client, wiki_dir):
        """[[deck tag=work]] passes tag as data attribute."""
        create_task(client, title="Write code", tags=["work"])
        html = server.render_md("[[deck tag=work]]\n")
        assert 'data-init-tag="work"' in html
        assert "tasks-app" in html

    def test_embed_with_multi_tag_filter(self, client, wiki_dir):
        """[[deck tag=a,b]] passes comma-separated tags as data attribute."""
        html = server.render_md("[[deck tag=q3-launch,BUG-10002]]\n")
        assert 'data-init-tag="q3-launch,BUG-10002"' in html

    def test_embed_filter_param(self, client, wiki_dir):
        """[[deck filter=due]] passes filter as data attribute."""
        html = server.render_md("[[deck filter=due]]\n")
        assert 'data-init-filter="due"' in html

    def test_embed_filter_completed(self, client, wiki_dir):
        """[[deck filter=completed]] passes filter as data attribute."""
        html = server.render_md("[[deck filter=completed]]\n")
        assert 'data-init-filter="completed"' in html

    def test_embed_filter_label_aliases(self, wiki_dir):
        """Friendly tab-label names map to the client data-filter values."""
        cases = {
            "focus": "focus",
            "today": "focus",   # legacy alias
            "backlog": "open",
            "plan": "due",
            "upcoming": "due",  # legacy alias for the renamed Plan tab
            "deferred": "hidden",
            "completed": "completed",
        }
        for label, expected in cases.items():
            html = server.render_md(f"[[deck filter={label}]]\n")
            assert f'data-init-filter="{expected}"' in html, label

    def test_embed_filter_alias_case_insensitive(self, wiki_dir):
        """Filter label matching is case-insensitive."""
        html = server.render_md("[[deck filter=Backlog]]\n")
        assert 'data-init-filter="open"' in html

    def test_embed_filter_internal_names_still_work(self, wiki_dir):
        """Internal names (open/due/hidden) remain accepted for back-compat."""
        for name in ("open", "due", "hidden"):
            html = server.render_md(f"[[deck filter={name}]]\n")
            assert f'data-init-filter="{name}"' in html

    def test_embed_filter_unknown_passthrough(self, wiki_dir):
        """An unrecognized filter value passes through unchanged (client ignores it)."""
        html = server.render_md("[[deck filter=bogus]]\n")
        assert 'data-init-filter="bogus"' in html

    def test_multiple_embeds_only_first(self, client, wiki_dir):
        """Multiple [[deck]] on one page: all render with unique instance IDs."""
        create_task(client, title="A", tags=["alpha"])
        md = "## Alpha\n\n[[deck tag=alpha]]\n\n## Beta\n\n[[deck tag=beta]]\n"
        html = server.render_md(md)
        # Both embeds should be present with unique IDs
        assert 'id="tasks-app"' in html
        assert 'id="tasks-app-1"' in html
        assert 'data-init-tag="alpha"' in html
        assert 'data-init-tag="beta"' in html

    def test_multiple_embeds_have_independent_element_ids(self, client, wiki_dir):
        """Each embed must expose its own suffixed list/tabs/modal IDs so the
        deck.js instance registry can address and refresh each independently
        (cross-instance refresh relies on this)."""
        create_task(client, title="A", tags=["alpha"])
        md = "[[deck tag=alpha]]\n\n[[deck tag=beta]]\n"
        html = server.render_md(md)
        # Instance 0 uses no suffix; instance 1 uses "-1".
        for base in ("tasks-list", "tasks-tabs", "task-edit-modal"):
            assert f'id="{base}"' in html, f"missing {base} for instance 0"
            assert f'id="{base}-1"' in html, f"missing {base}-1 for instance 1"
        # Distinct instance markers the registry keys on.
        assert 'data-instance="0"' in html
        assert 'data-instance="1"' in html

    def test_embed_not_inside_paragraph(self, client, wiki_dir):
        """[[deck]] on its own line should NOT be wrapped in <p>."""
        create_task(client, title="T1")
        html = server.render_md("[[deck]]\n")
        assert "<p>" not in html.split('id="tasks-app"')[0].split('\n')[-1]

    def test_embed_case_insensitive(self, client, wiki_dir):
        """[[Deck]] and [[DECK]] work too."""
        for variant in ["[[Deck]]\n", "[[DECK]]\n", "[[deck]]\n"]:
            html = server.render_md(variant)
            assert "tasks-app" in html

    def test_embed_works_inline(self):
        """[[deck]] inline is also replaced (renders embed)."""
        html = server.render_md("Here are the [[deck]] for today.\n")
        assert "tasks-app" in html

    def test_embed_works_in_list(self):
        """[[deck]] inside a list item renders embed."""
        html = server.render_md("- [[deck tag=work]]\n")
        assert "tasks-app" in html
        assert 'data-init-tag="work"' in html

    def test_embed_page_param(self, wiki_dir):
        """[[deck page=my-project]] passes page as data attribute."""
        html = server.render_md("[[deck page=my-project]]\n")
        assert 'data-init-page="my-project"' in html

    def test_embed_page_self(self, client, wiki_dir):
        """[[deck page=self]] resolves to current slug."""
        create_task(client, title="T", page="my-page")
        html = server.render_md("[[deck page=self]]\n", slug="my-page")
        assert 'data-init-page="my-page"' in html


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
        """[[deck page=self]] on slug 'my-project' passes page as data attribute."""
        create_task(client, title="Linked", page="my-project")
        create_task(client, title="Other", page="other-page")
        create_task(client, title="No page")
        html = server.render_md("[[deck page=self]]\n", slug="my-project")
        assert 'data-init-page="my-project"' in html

    def test_page_explicit_filter(self, client, wiki_dir):
        """[[deck page=my-project]] passes page as data attribute."""
        create_task(client, title="Linked", page="my-project")
        create_task(client, title="Unlinked")
        html = server.render_md("[[deck page=my-project]]\n")
        assert 'data-init-page="my-project"' in html

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
    def test_page_with_deck_embed(self, client, wiki_dir):
        """A wiki page with [[deck]] renders the full deck app."""
        create_task(client, title="Page task", tags=["test"])
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\nSome text.\n\n[[deck tag=test]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body
        assert 'data-init-tag="test"' in body
        assert "deck.js" in body

    def test_preview_with_deck_embed(self, client, wiki_dir):
        """Preview endpoint renders [[deck]] as the full embed."""
        create_task(client, title="Preview task")
        resp = client.post(
            "/api/preview?slug=test-page",
            data="[[deck]]\n",
            content_type="text/plain"
        )
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body
        assert "deck.js" in body

    def test_deck_embed_with_params(self, client, wiki_dir):
        """[[deck tag=work page=proj filter=due]] passes all data attrs."""
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\n[[deck tag=work page=proj filter=due]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        body = resp.get_data(as_text=True)
        assert 'data-init-tag="work"' in body
        assert 'data-init-page="proj"' in body
        assert 'data-init-filter="due"' in body

    def test_embed_contains_focus_date_field(self, wiki_dir):
        """Edit modal includes a Plan for (focus_date) date field."""
        html = server.render_md("[[deck]]\n")
        assert 'id="task-edit-focus"' in html
        assert 'name="focus_date"' in html
        assert 'data-date-trigger="task-edit-focus"' in html
