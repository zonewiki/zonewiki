"""Tests for [[deck]] embed directive and page field on tasks."""

import sys
import os
import re
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
    deck_module.DECK_FILE = tmp_path / "_deck" / "tasks.json"
    (tmp_path / "_deck").mkdir(exist_ok=True)   # tests write deck files directly
    server.DECK_FILE = tmp_path / "_deck" / "tasks.json"
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
        result = deck_module._parse_tasks_params("tag=work view=due limit=5 sort=due_date")
        assert result == {"tag": "work", "view": "due", "limit": "5", "sort": "due_date"}

    def test_list(self):
        result = deck_module._parse_tasks_params("list=groceries")
        assert result == {"list": "groceries"}

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

    def test_edit_modal_has_list_field(self, wiki_dir):
        html = server.render_md("[[deck]]\n[[deck]]\n")
        assert 'id="task-edit-list"' in html and 'list="task-edit-lists"' in html
        assert 'id="task-edit-list-1"' in html and 'list="task-edit-lists-1"' in html
        assert "task-edit-page" not in html

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
        """[[deck view=due]] passes filter as data attribute."""
        html = server.render_md("[[deck view=due]]\n")
        assert 'data-init-view="due"' in html

    def test_embed_filter_completed(self, client, wiki_dir):
        """[[deck view=completed]] passes filter as data attribute."""
        html = server.render_md("[[deck view=completed]]\n")
        assert 'data-init-view="completed"' in html

    def test_embed_filter_label_aliases(self, wiki_dir):
        """Friendly tab-label names map to the client data-filter values."""
        cases = {
            "today": "focus",   # the Focus tab's label for a while
            "focus": "focus",
            "all": "open",
            "backlog": "open",  # former label of the Open tab
            "plan": "due",
            "upcoming": "due",  # legacy alias for the renamed Plan tab
            "completed": "completed",
        }
        for label, expected in cases.items():
            html = server.render_md(f"[[deck view={label}]]\n")
            assert f'data-init-view="{expected}"' in html, label

    def test_view_tabs_are_a_segmented_control_in_order(self, wiki_dir):
        html = server.render_md("[[deck]]\n")
        assert 'class="view-tabs" id="tasks-tabs" role="tablist"' in html
        tabs = re.findall(r'<button type="button" role="tab" data-filter="(\w+)"[^>]*>([^<]+)</button>', html)
        assert tabs == [("open", "Open"), ("due", "Plan"), ("focus", "Focus")]
        # Completed is a link under the list, not a tab.
        assert 'id="task-completed-link"' in html

    def test_embed_filter_alias_case_insensitive(self, wiki_dir):
        """Filter label matching is case-insensitive."""
        html = server.render_md("[[deck view=All]]\n")
        assert 'data-init-view="open"' in html

    def test_embed_filter_internal_names_still_work(self, wiki_dir):
        """Internal names (open/due) remain accepted for back-compat."""
        for name in ("open", "due"):
            html = server.render_md(f"[[deck view={name}]]\n")
            assert f'data-init-view="{name}"' in html

    def test_embed_no_deferred_tab(self, wiki_dir):
        """The Deferred tab is gone — nothing in the deck hides tasks now."""
        html = server.render_md("[[deck]]\n")
        assert 'value="hidden"' not in html
        assert "Deferred" not in html

    def test_embed_has_no_unplanned_toggle(self, wiki_dir):
        """The Open tab's "Unplanned only" toggle is gone: unplanned tasks are
        the Plan board's Unplanned section. A leftover `unplanned=` param in an
        embed is ignored."""
        html = server.render_md("[[deck view=open unplanned=1]]\n")
        assert 'id="task-unplanned' not in html
        assert "data-init-unplanned" not in html
        assert 'data-init-view="open"' in html

    def test_embed_legacy_filter_param(self, wiki_dir):
        """`filter=` is the old name of `view=` and still works; view wins."""
        assert 'data-init-view="due"' in server.render_md("[[deck filter=plan]]\n")
        html = server.render_md("[[deck view=focus filter=plan]]\n")
        assert 'data-init-view="focus"' in html

    def test_embed_filter_unknown_passthrough(self, wiki_dir):
        """An unrecognized filter value passes through unchanged (client ignores it)."""
        html = server.render_md("[[deck view=bogus]]\n")
        assert 'data-init-view="bogus"' in html

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

    def test_embed_page_param_is_ignored(self, wiki_dir):
        """page= was removed: it no longer narrows the embed."""
        html = server.render_md("[[deck page=my-project]]\n")
        assert "data-init-page" not in html


# ── Page field removed (now a wikilink in the notes) ───────────────────────────


class TestPageFieldRemoved:
    def test_page_in_request_is_ignored(self, client, wiki_dir):
        data = create_task(client, title="T", page="my-project").get_json()
        assert "page" not in data
        resp = client.put(f"/api/deck/{data['id']}", json={"page": "x"})
        assert resp.status_code == 200 and "page" not in resp.get_json()

    def test_stored_page_becomes_wikilink(self, client, wiki_dir):
        (wiki_dir / "_deck" / "tasks.json").write_text(json.dumps([
            {"id": "a", "title": "Prune", "description": "Roses", "page": "garden"},
            {"id": "b", "title": "Bare", "page": "garden"},
            {"id": "c", "title": "Water [[Garden]]", "page": "garden"},
            {"id": "d", "title": "Seeds", "description": "[see](/page/garden)", "page": "garden"},
            {"id": "e", "title": "None", "page": None},
        ]), encoding="utf-8")
        tasks = {t["id"]: t for t in client.get("/api/deck").get_json()}
        assert all("page" not in t for t in tasks.values())
        assert tasks["a"]["description"] == "Roses\n\n[[garden]]"
        assert tasks["b"]["description"] == "[[garden]]"
        assert tasks["c"].get("description", "") == ""     # title already links it
        assert tasks["d"]["description"] == "[see](/page/garden)"
        assert "description" not in tasks["e"] or tasks["e"]["description"] == ""

    def test_migrated_task_is_linked_task(self, client, wiki_dir):
        (wiki_dir / "garden.md").write_text("# Garden\n", encoding="utf-8")
        (wiki_dir / "_deck" / "tasks.json").write_text(json.dumps([
            {"id": "a", "title": "Prune", "page": "garden"}]), encoding="utf-8")
        body = client.get("/page/garden").get_data(as_text=True)
        assert 'id="task-backlinks"' in body and "Prune" in body

    def test_startup_migration_rewrites_file(self, wiki_dir):
        path = wiki_dir / "_deck" / "tasks.json"
        path.write_text(json.dumps([{"id": "a", "title": "P", "page": "garden"}]), encoding="utf-8")
        deck_module._migrate_deck_files()
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored == [{"id": "a", "title": "P", "description": "[[garden]]"}]

    def test_restore_snapshot_with_page(self, client, wiki_dir):
        snap = {"id": "old", "title": "Old", "description": "Notes", "page": "garden"}
        restored = client.post("/api/deck/restore", json={"tasks": [snap]}).get_json()
        assert restored[0]["description"] == "Notes\n\n[[garden]]"
        assert "page" not in restored[0]


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
            "/api/preview",
            data="[[deck]]\n",
            content_type="text/plain"
        )
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body
        assert "deck.js" in body

    def test_deck_embed_with_params(self, client, wiki_dir):
        """[[deck tag=work list=proj view=due]] passes all data attrs."""
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\n[[deck tag=work list=proj view=due]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        body = resp.get_data(as_text=True)
        assert 'data-init-tag="work"' in body
        assert 'data-init-list="proj"' in body
        assert 'data-init-view="due"' in body

    def test_embed_has_single_filter_field(self, wiki_dir):
        html = server.render_md("[[deck]]\n")
        assert 'id="task-filter-search"' in html
        assert "task-filter-tag" not in html

    def test_embed_contains_start_date_field(self, wiki_dir):
        """Edit modal has a Start (start_date) field and an unstored
        days-before-due helper; the old lead/anchor fields are gone."""
        html = server.render_md("[[deck]]\n")
        assert 'id="task-edit-start"' in html
        assert 'name="start_date"' in html
        assert 'data-date-trigger="task-edit-start"' in html
        assert 'id="task-edit-lead"' in html
        assert "defer_days" not in html and "focus_date" not in html
        assert "rec-anchor" not in html
