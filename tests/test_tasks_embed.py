"""Tests for [[deck]] embed directive and page field on tasks."""

import re

import decksyntax
from tests.conftest import create_task
import render


# ── Param parsing ───────────────────────────────────────────────────────────────


class TestParseParams:
    def test_empty(self):
        assert decksyntax.parse_deck_params("") == {}

    def test_single(self):
        result = decksyntax.parse_deck_params("tag=work")
        assert result == {"tag": "work"}

    def test_multiple(self):
        result = decksyntax.parse_deck_params("tag=work view=plan limit=5 sort=due_date")
        assert result == {"tag": "work", "view": "plan", "limit": "5", "sort": "due_date"}

    def test_list(self):
        result = decksyntax.parse_deck_params("list=groceries")
        assert result == {"list": "groceries"}

    def test_case_insensitive_keys(self):
        result = decksyntax.parse_deck_params("Tag=work VIEW=plan")
        assert result == {"tag": "work", "view": "plan"}


# ── Rendering ───────────────────────────────────────────────────────────────────


class TestTasksLinkRender:
    def test_basic_embed_no_tasks(self, wiki_dir):
        """[[deck]] renders the full deck app embed."""
        html = render.render_md("[[deck]]\n")
        assert "tasks-app" in html
        assert "tasks-list" in html

    def test_eleven_embeds_each_render_their_own(self, wiki_dir):
        # Regression: placeholder _1 is a prefix of _10, so the 11th embed
        # was replaced by (part of) the 2nd one's HTML.
        html = render.render_md("\n\n".join(f"[[deck tag=t{i}]]" for i in range(11)) + "\n")
        assert "LEAFDECKPLACEHOLDER" not in html
        for i in range(11):
            sfx = f"-{i}" if i else ""
            assert html.count(f'id="tasks-app{sfx}"') == 1
            assert f'id="tasks-app{sfx}" data-init-tag="t{i}"' in html

    def test_no_list_field_or_show_lists_checkbox(self, wiki_dir):
        html = render.render_md("[[deck]]\n[[deck]]\n")
        assert "task-show-lists" not in html
        assert 'id="task-edit-list' not in html and "task-edit-page" not in html

    def test_density_button(self, wiki_dir):
        """Every deck has the switch between one line per task and full cards."""
        html = render.render_md("[[deck]]\n")
        assert 'class="btn deck-density-btn" id="task-density-btn"' in html and 'title="One line per task (v)"' in html

    def test_embed_with_tag_filter(self, client, wiki_dir):
        """[[deck tag=work]] passes tag as data attribute."""
        create_task(client, title="Write code", tags=["work"])
        html = render.render_md("[[deck tag=work]]\n")
        assert 'data-init-tag="work"' in html
        assert "tasks-app" in html

    def test_embed_with_multi_tag_filter(self, client, wiki_dir):
        """[[deck tag=a,b]] passes comma-separated tags as data attribute."""
        html = render.render_md("[[deck tag=q3-launch,BUG-10002]]\n")
        assert 'data-init-tag="q3-launch,BUG-10002"' in html

    def test_embed_view_param(self, wiki_dir):
        """[[deck view=…]] passes the view as a data attribute, any case."""
        for name in ("focus", "open", "plan", "completed"):
            html = render.render_md(f"[[deck view={name.upper()}]]\n")
            assert f'data-init-view="{name}"' in html, name

    def test_view_tabs_are_a_segmented_control_in_order(self, wiki_dir):
        html = render.render_md("[[deck]]\n")
        assert 'class="view-tabs" id="tasks-tabs" role="tablist"' in html
        tabs = re.findall(r'<button type="button" role="tab" data-view="(\w+)"[^>]*>([^<]+)</button>', html)
        assert tabs == [("open", "Open"), ("plan", "Plan"), ("focus", "Focus")]
        # Completed is a link under the list, not a tab.
        assert 'id="task-completed-link"' in html

    def test_embed_unknown_view_is_the_default(self, wiki_dir):
        """A name that isn't a view (also the tabs' old names, and `filter=`,
        the old name of `view=`) opens the default view."""
        for directive in ("view=bogus", "view=due", "view=backlog", "view=today",
                          "view=all", "view=upcoming", "view=done", "filter=plan"):
            html = render.render_md(f"[[deck {directive}]]\n")
            assert 'data-init-view=""' in html, directive

    def test_multiple_embeds_only_first(self, client, wiki_dir):
        """Multiple [[deck]] on one page: all render with unique instance IDs."""
        create_task(client, title="A", tags=["alpha"])
        md = "## Alpha\n\n[[deck tag=alpha]]\n\n## Beta\n\n[[deck tag=beta]]\n"
        html = render.render_md(md)
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
        html = render.render_md(md)
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
        html = render.render_md("[[deck]]\n")
        assert "<p>" not in html.split('id="tasks-app"')[0].split('\n')[-1]

    def test_embed_case_insensitive(self, client, wiki_dir):
        """[[Deck]] and [[DECK]] work too."""
        for variant in ["[[Deck]]\n", "[[DECK]]\n", "[[deck]]\n"]:
            html = render.render_md(variant)
            assert "tasks-app" in html

    def test_embed_works_inline(self):
        """[[deck]] inline is also replaced (renders embed)."""
        html = render.render_md("Here are the [[deck]] for today.\n")
        assert "tasks-app" in html

    def test_embed_works_in_list(self):
        """[[deck]] inside a list item renders embed."""
        html = render.render_md("- [[deck tag=work]]\n")
        assert "tasks-app" in html
        assert 'data-init-tag="work"' in html

    def test_embed_page_param_is_ignored(self, wiki_dir):
        """page= was removed: it no longer narrows the embed."""
        html = render.render_md("[[deck page=my-project]]\n")
        assert "data-init-page" not in html


# ── No page field (a wikilink in the notes links a page) ─────────────────────


class TestNoPageField:
    def test_page_in_request_is_ignored(self, client, wiki_dir):
        data = create_task(client, title="T", page="my-project").get_json()
        assert "page" not in data
        resp = client.put(f"/api/deck/{data['id']}", json={"page": "x"})
        assert resp.status_code == 200 and "page" not in resp.get_json()

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

    def test_deck_embed_with_params(self, client, wiki_dir):
        """[[deck tag=work list=proj view=plan]] passes all data attrs."""
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\n[[deck tag=work list=proj view=plan]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        body = resp.get_data(as_text=True)
        assert 'data-init-tag="work"' in body
        assert 'data-init-list="proj"' in body
        assert 'data-init-view="plan"' in body

    def test_embed_has_single_filter_field(self, wiki_dir):
        html = render.render_md("[[deck]]\n")
        assert 'id="task-filter-search"' in html
        assert "task-filter-tag" not in html

    def test_embed_contains_start_date_field(self, wiki_dir):
        """Edit modal has a Start (start_date) field and an unstored
        days-before-due helper; the old lead/anchor fields are gone."""
        html = render.render_md("[[deck]]\n")
        assert 'id="task-edit-start"' in html
        assert 'name="start_date"' in html
        assert 'data-date-trigger="task-edit-start"' in html
        assert 'id="task-edit-lead"' in html
        assert "defer_days" not in html and "focus_date" not in html
        assert "rec-anchor" not in html
