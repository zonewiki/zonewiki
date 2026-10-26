"""Tests for Leaf HTTP routes — page view/edit/save, search, upload, auth,
rename with reference updates, conflict detection, and API endpoints."""

import html
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import HOST, backlinks, section_fields, task_backlinks, write_page
from tests.goserver import binary


# ===========================================================================
# Index redirect
# ===========================================================================

class TestIndex:

    def test_root_redirects_to_index_page(self, client):
        resp = client.get("/", headers=HOST)
        assert resp.status_code in (301, 302)
        assert "/page/index" in resp.headers["Location"]


# ===========================================================================
# view_page
# ===========================================================================

class TestViewPage:

    def test_odd_file_names_get_working_escaped_links(self, client, wiki_dir):
        # Regression: slugs went into href attributes as they were, so a
        # file named on disk with a quote broke out of the attribute, and
        # one with "#" or a space linked to the wrong page.
        write_page(wiki_dir, "target", "# Target\n")
        write_page(wiki_dir, 'odd "name" #1', "# Odd\n\nSee [[target]].\n")
        body = client.get("/page/target", headers=HOST).get_data(as_text=True)
        href = "/page/odd%20%22name%22%20%231"
        assert f'<a href="{href}">Odd</a>' in body and 'odd "name"' not in body
        page = client.get(href, headers=HOST).get_data(as_text=True)
        assert "See" in page and 'href="/edit/odd%20%22name%22%20%231"' in page

    def test_existing_page_returns_200(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n\nHello world.\n")
        resp = client.get("/page/my-page", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "Hello world." in text
        assert "My Page" in text

    def test_viewport_resizes_with_on_screen_keyboard(self, client, wiki_dir):
        # Regression: without interactive-widget=resizes-content, Android
        # Chrome's keyboard only shrinks the visual viewport, so the
        # full-screen deck editor kept its height and its Save/Cancel bar sat
        # behind the keyboard.
        write_page(wiki_dir, "my-page", "# My Page\n")
        text = client.get("/page/my-page", headers=HOST).data.decode()
        assert ('<meta name="viewport" content="width=device-width, initial-scale=1, '
                'interactive-widget=resizes-content">') in text

    def test_views_name_their_module(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        scratch = client.post("/api/scratch", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        for url, module in (("/page/my-page", "wiki"), ("/page/index", "wiki"),
                            ("/deck", "deck"), ("/deck?list=shop", "deck"),
                            ("/page/_scratch", "scratch"), (f"/page/{scratch}", "scratch"),
                            ("/history/my-page", "wiki")):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            assert f'<body data-module="{module}">' in body, url
            # The current module's links (switcher, bar) are marked by the
            # server, so the highlight is there at first paint.
            assert len(re.findall(rf'data-module="{module}"[^>]*aria-current="page"', body)) == 2, url
            assert body.count('aria-current="page"') == 2, url
        # Search results belong to no module; the editor has none either.
        assert "<body>" in client.get("/search?q=page", headers=HOST).get_data(as_text=True)
        assert 'data-module=' not in client.get("/edit/my-page", headers=HOST).get_data(
            as_text=True).split("<body", 1)[1].split(">", 1)[0]

    def test_breadcrumb_root_is_the_module_switcher(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/deck", "/page/_scratch"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            crumb = body.split('<div class="breadcrumb"', 1)[1].split("</nav>", 1)[0]
            assert crumb.index('href="/"') < crumb.index('href="/page/_scratch"') < crumb.index('href="/deck"'), url
            assert '<span class="module-label">Wiki</span>' in crumb

    @staticmethod
    def _deck_links(body):
        return re.findall(r'<a href="([^"]*)" class="(?:action-module )?module-link" data-module="deck"', body)

    def test_deck_links_go_to_deck_without_a_home(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n\n[[deck list=task]]\n")
        body = client.get("/page/my-page", headers=HOST).get_data(as_text=True)
        assert self._deck_links(body) == ["/deck", "/deck"]   # switcher, bar
        assert '<body data-module="wiki">' in body

    def test_deck_home_page_is_where_the_deck_links_go(self, client, wiki_dir):
        # [[deck … home=1]] makes that page your own deck: the Deck module
        # links go there, and the page belongs to the Deck module.
        write_page(wiki_dir, "deck", "# Deck\n\n[[deck list=task view=focus home=1]]\n")
        write_page(wiki_dir, "other", "# Other\n")
        body = client.get("/page/other", headers=HOST).get_data(as_text=True)
        assert self._deck_links(body) == ["/page/deck", "/page/deck"]
        home = client.get("/page/deck", headers=HOST).get_data(as_text=True)
        assert '<body data-module="deck" data-deck-tags="task" data-deck-today="1">' in home
        assert 'data-home="1"' in home   # the embed New clicks
        # /deck stays the view of every task, in the Deck module.
        assert '<body data-module="deck" data-deck-tags="task" data-deck-today="1">' in client.get("/deck", headers=HOST).get_data(as_text=True)

    def test_deck_home_ignores_code_and_first_page_wins(self, client, wiki_dir):
        write_page(wiki_dir, "aaa", "# A\n\n```\n[[deck home=1]]\n```\n")
        write_page(wiki_dir, "zzz", "# Z\n\n[[deck tag=x home=yes]]\n")
        write_page(wiki_dir, "mmm", "# M\n\n[[deck home=true]]\n")
        body = client.get("/page/aaa", headers=HOST).get_data(as_text=True)
        assert self._deck_links(body)[0] == "/page/mmm"
        # Removing the declaration ends it at once.
        write_page(wiki_dir, "mmm", "# M\n\n[[deck]]\n")
        os.utime(wiki_dir / "mmm.md", (2_000_000_000, 2_000_000_000))
        body = client.get("/page/aaa", headers=HOST).get_data(as_text=True)
        assert self._deck_links(body)[0] == "/page/zzz"

    def test_bottom_bar_lists_the_modules(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        bar = client.get("/page/my-page", headers=HOST).get_data(as_text=True).split(
            '<nav class="action-buttons"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'class="action-label"[^>]*>(\w+)<', bar) == ["Wiki", "Scratch", "Deck", "Note", "Task", "Search"]
        # Navigation, a divider, then the actions.
        assert bar.index('data-module="deck"') < bar.index('class="action-sep"') < bar.index('class="action-scratch"')

    def test_phone_action_buttons_on_views_not_in_editor(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/deck"):
            body = client.get(url, headers=HOST).data.decode()
            assert all(f'class="action-{a}"' in body for a in ("scratch", "task", "search"))
        # The editor has its own toolbar and the keyboard owns the bottom edge.
        assert 'class="action-buttons"' not in client.get("/edit/my-page", headers=HOST).data.decode()

    def test_desktop_top_actions_on_views_not_in_editor(self, client, wiki_dir):
        # New scratch note, New task and Search, at the start of <main> so the
        # CSS can put them at the top right, in line with the breadcrumb.
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/deck", "/page/_scratch"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            top = body.split("<main", 1)[1].split('<div class="top-actions">', 1)
            # Before the page itself; only the status badges, which share
            # the row, come first.
            assert len(top) == 2 and '<div class="breadcrumb"' not in top[0] and '<header' not in top[0], url
            assert top[0].count('class="top-badges"') == 1, url
            buttons = re.findall(r'<button type="button" class="top-(\w+)"(?: aria-pressed="false")? aria-label="([^"]+)" title="([^"]+)"',
                                 top[1].split("</div>", 1)[0])
            assert buttons == [("sharing", "Sharing mode", "Sharing mode: hide what is around the page (S)"),
                               ("scratch", "New scratch note", "New scratch note (c s)"),
                               ("task", "Add a task to your deck", "Add a task to your deck (c t)"),
                               ("search", "Search", "Search (Ctrl+P)")], url
        assert 'class="top-actions"' not in client.get("/edit/my-page", headers=HOST).data.decode()

    def test_sharing_mode_is_rendered_from_the_cookie(self, client, wiki_dir):
        # The server renders html.sharing from the cookie: from the first
        # paint, nothing sharing mode hides flashes on a shared screen.
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/edit/my-page", "/deck"):
            assert '<html lang="en">' in client.get(url, headers=HOST).get_data(as_text=True), url
        client.set_cookie("leaf-sharing", "1")
        for url in ("/page/my-page", "/edit/my-page", "/deck"):
            assert '<html lang="en" class="sharing">' in client.get(url, headers=HOST).get_data(as_text=True), url

    def test_new_tasks_get_the_deck_homes_tags(self, client, wiki_dir):
        # c t and the New task button tag a task as the deck's home does:
        # leaf.js reads the tags (and keep) from <body>, on every view.
        write_page(wiki_dir, "my-page", "# My Page\n")
        body = client.get("/page/my-page", headers=HOST).get_data(as_text=True)
        assert "data-deck-tags" not in body and "data-deck-keep" not in body   # no home: no tags
        write_page(wiki_dir, "deck", "# Deck\n\n[[deck list=Task tag=work,-mary home=1 keep=1]]\n")
        for url in ("/page/my-page", "/edit/my-page", "/deck"):
            tag = client.get(url, headers=HOST).get_data(as_text=True).split("<body", 1)[1].split(">", 1)[0]
            assert 'data-deck-tags="task,work"' in tag and 'data-deck-keep="1"' in tag, url
            assert 'data-deck-today="1"' in tag, url   # the default view, Focus: start today
        write_page(wiki_dir, "deck", "# Deck\n\n[[deck list=task view=open home=1]]\n")
        tag = client.get("/page/my-page", headers=HOST).get_data(as_text=True).split("<body", 1)[1].split(">", 1)[0]
        assert 'data-deck-tags="task"' in tag and "data-deck-today" not in tag and "data-deck-keep" not in tag

    def test_headless_deck_for_pages_without_one(self, client, wiki_dir):
        # c t on a page without a deck app loads this hidden one (scripts
        # left out: the page loads them).
        body = client.get("/api/deck/headless", headers=HOST).get_data(as_text=True)
        assert 'data-headless="1"' in body and 'id="task-edit-modal"' in body
        assert "<script" not in body

    def test_pages_load_nothing_from_other_hosts(self, client, wiki_dir):
        # No CDN: highlight.js is vendored and served from /public/.
        write_page(wiki_dir, "my-page", "# My Page\n\n```py\nx = 1\n```\n")
        for url in ("/page/my-page", "/edit/my-page", "/deck", "/search?q=page"):
            head = client.get(url, headers=HOST).data.decode()
            assert not re.search(r'<(?:script|link)\b[^>]*(?:src|href)="(?:https?:)?//', head), url
        for f in ("highlight.min.js", "github.min.css", "github-dark.min.css"):
            r = client.get(f"/public/vendor/highlight/{f}", headers=HOST)
            assert r.status_code == 200 and len(r.data) > 1000, f

    def test_editor_has_formatting_bar_page_view_not(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        body = client.get("/edit/my-page", headers=HOST).data.decode()
        assert 'id="format-bar" role="toolbar"' in body
        for fmt in ("bold", "italic", "ul", "ol", "task", "link", "wikilink", "table", "attach"):
            assert f'data-fmt="{fmt}"' in body
        # Icon-only buttons need a name for screen readers.
        assert body.count('class="fmt-btn"') == body.count('class="fmt-btn" data-fmt="') \
            == len(re.findall(r'class="fmt-btn" data-fmt="\w+" title="[^"]+" aria-label="[^"]+"', body))
        assert 'id="format-bar"' not in client.get("/page/my-page", headers=HOST).data.decode()

    def test_missing_page_redirects_to_editor(self, client):
        resp = client.get("/page/does-not-exist", headers=HOST)
        assert resp.status_code in (301, 302)
        assert "/edit/does-not-exist" in resp.headers["Location"]

    def test_slug_with_md_extension_stripped(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n\nContent.\n")
        resp = client.get("/page/my-page.md", headers=HOST)
        assert resp.status_code == 200
        assert "Content." in resp.data.decode()

    def test_page_title_in_head(self, client, wiki_dir):
        write_page(wiki_dir, "about", "# About Us\n\nInfo.\n")
        resp = client.get("/page/about", headers=HOST)
        assert "<title>About Us — Leaf</title>" in resp.data.decode()

    def test_edit_link_present(self, client, wiki_dir):
        write_page(wiki_dir, "editable", "# Editable\n\ncontent\n")
        resp = client.get("/page/editable", headers=HOST)
        assert b"/edit/editable" in resp.data

    def test_backlinks_section_appears(self, client, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n\ncontent\n")
        write_page(wiki_dir, "source", "# Source\n\nSee [[target]].\n")
        resp = client.get("/page/target", headers=HOST)
        text = resp.data.decode()
        assert "Backlinks" in text
        assert "source" in text.lower()

    def test_no_backlinks_section_when_empty(self, client, wiki_dir):
        write_page(wiki_dir, "lonely", "# Lonely\n\ncontent\n")
        resp = client.get("/page/lonely", headers=HOST)
        text = resp.data.decode()
        # Backlinks section should not appear
        assert "Backlinks" not in text

    def test_unsafe_slug_returns_400(self, client):
        resp = client.get("/page/../etc/passwd", headers=HOST)
        assert resp.status_code == 400

    def test_frontmatter_rendered_as_meta_not_raw_yaml(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged",
            "---\ntags: work, urgent\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/page/tagged", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "tags: work" not in text
        assert '<p class="page-tldr">Short summary.</p>' in text
        assert 'class="task-tag page-tag"' in text
        assert "Body." in text
        assert "Tagged" in text  # title still derived correctly past the frontmatter

    def test_tags_and_tldr_shown_once_in_meta_row_not_in_body(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged2",
            "---\ntags: work, urgent\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/page/tagged2", headers=HOST)
        text = resp.data.decode()
        # Tags and the collapsed tldr toggle both live in the page-meta-wrapper
        # row (same line as "Last changed"), and appear there exactly once —
        # not duplicated in the article body.
        meta_start = text.index('class="page-meta-wrapper"')
        article_start = text.index('class="page-content"')
        assert 'class="task-tag page-tag"' in text[meta_start:article_start]
        assert text.count('class="task-tag page-tag"') == 2  # "work" + "urgent"
        assert text.count('<p class="page-tldr">') == 1
        assert '<p class="page-tldr">' in text[meta_start:article_start]
        assert '<p class="page-tldr">' not in text[article_start:]
        # Tags and the tldr toggle label sit together in the left group, "Last
        # changed" in the right group.
        left_start = text.index('class="page-meta-left"')
        right_start = text.index('class="page-meta-right"')
        assert left_start < right_start
        assert 'class="task-tag page-tag"' in text[left_start:right_start]
        assert 'class="tldr-label"' in text[left_start:right_start]

    def test_tldr_toggle_is_sibling_of_row_and_text(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged3",
            "---\ntags: work\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/page/tagged3", headers=HOST)
        text = resp.data.decode()
        # The CSS-only reveal needs checkbox → row → text as siblings in that
        # order, so `.tldr-toggle:checked ~ .page-tldr` can match.
        cb = text.index('class="tldr-toggle"')
        row = text.index('class="page-meta-wrapper"')
        para = text.index('<p class="page-tldr">')
        assert cb < row < para
        # The label points at the checkbox by id.
        assert 'id="tldr-toggle"' in text
        assert 'for="tldr-toggle"' in text

    def test_no_tldr_toggle_when_no_tldr(self, client, wiki_dir):
        write_page(wiki_dir, "tagsonly", "---\ntags: work\n---\n\n# T\n\nBody.\n")
        resp = client.get("/page/tagsonly", headers=HOST)
        text = resp.data.decode()
        assert 'class="tldr-toggle"' not in text
        assert 'class="tldr-label"' not in text
        assert 'class="task-tag page-tag"' in text

    # ── Side columns (big screens; the CSS decides when they show) ──────────

    def test_page_view_has_side_columns(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n\nSoil.\n\n### Roses\n\nRed.\n\n## Paths\n")
        write_page(wiki_dir, "plans", "# Plans\n\nSee [[Garden]].\n")
        client.post("/api/deck", json={"title": "Weed the [[garden]]"})
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert '<main class="wide">' in body
        grid = body.split('<div class="page-grid">', 1)[1]
        left, rest = grid.split('<div class="page-col">', 1)
        # Left: the headings as "On this page".
        assert '<aside class="rail rail-left"><nav class="rail-toc" aria-label="On this page">' in left
        assert re.findall(r'href="(#[^"]+)"', left) == ["#beds", "#roses", "#paths"]
        assert '<li class="toc-h3"><a href="#roses">' in left
        # Right: a copy of the sections at the end; the anchors stay with them.
        col, right = rest.split('<aside class="rail rail-context" aria-label="Links">', 1)
        foot = col.split('<div class="page-foot">', 1)[1]
        for sid in ("backlinks", "task-backlinks"):
            assert f'<section class="backlinks" id="{sid}">' in foot
            assert f'<section class="backlinks" data-id="{sid}">' in right
        assert ' id="' not in right.split("</aside>", 1)[0]
        assert "Plans" in right and "Weed the garden" in right

    def test_side_toc_from_three_headings(self, client, wiki_dir):
        # A long page gets "On this page": its ## and ### headings, links in
        # them flattened (no link inside a link). The text stays as written.
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n\n### [[Roses]] and `tulips`\n\n```\n## not a heading\n```\n\n## Paths\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        left = body.split('<aside class="rail rail-left">', 1)[1].split("</aside>", 1)[0]
        assert re.findall(r'href="(#[^"]+)"', left) == ["#beds", "#roses-and-tulips", "#paths"]
        assert '<li class="toc-h3"><a href="#roses-and-tulips">Roses and <code>tulips</code></a></li>' in left
        assert '<nav class="toc">' not in body

    def test_side_column_lists_the_pages_linked_to(self, client, wiki_dir):
        # "Links to": existing pages only, A–Z by title, not the page itself;
        # only in the column (smaller screens have the links in the text).
        write_page(wiki_dir, "garden", "# Garden\n\nSee [[Zinnias]], [[roses]], [[Missing]], [[Garden]] and [beds](beds).\n")
        write_page(wiki_dir, "roses", "# Roses\n")
        write_page(wiki_dir, "zinnias", "# Zinnias\n")
        write_page(wiki_dir, "beds", "# Beds\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        right = body.split('<aside class="rail rail-context" aria-label="Links">', 1)[1].split("</aside>", 1)[0]
        out = right.split('<section class="backlinks" data-id="outlinks"><h3>Links to</h3>', 1)[1]
        assert re.findall(r'<a href="/page/([^"]+)">([^<]+)</a>', out) == [
            ("beds", "Beds"), ("roses", "Roses"), ("zinnias", "Zinnias")]
        assert body.count("Links to") == 1

    def test_side_column_without_toc_is_empty(self, client, wiki_dir):
        # Under three headings: no left column content (a [[toc]] line no
        # longer asks for one); the column stays, so the page keeps its place.
        write_page(wiki_dir, "garden", "# Garden\n\n[[toc]]\n\n## Beds\n\n## Paths\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert '<aside class="rail rail-left"><a class="rail-top" href="#" hidden>↑ Back to top</a></aside>' in body
        right = body.split('<aside class="rail rail-context" aria-label="Links">', 1)[1].split("</aside>", 1)[0]
        assert right.startswith('<div class="rail-actions" hidden><a href="/edit/garden" class="btn">Edit</a>')
        assert "<section" not in right

    def test_page_actions_menu(self, client, wiki_dir):
        # The header has Edit and a ⋯ menu (Present, History with git,
        # Rename…, Delete…); the right column the same two.
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        header = body.split('<div class="page-header-actions">', 1)[1].split("</div>", 1)[0]
        assert '<a href="/edit/garden" class="btn">Edit</a>' in header
        assert body.count('class="btn page-menu-btn"') == 2
        menu = body.split('<div class="page-menu" id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'role="menuitem"[^>]*>([^<]+)', menu) == ["Present"] + ["History"] * settings.GIT_ENABLED + ["Rename…", "Delete…"]
        assert 'id="page-rename-dialog"' in body and 'id="page-delete-dialog"' in body
        # The home page can't be renamed or deleted: only Present is left.
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        menu = body.split('id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'role="menuitem"[^>]*>([^<]+)', menu) == ["Present"] + ["History"] * settings.GIT_ENABLED
        assert 'id="page-rename-dialog"' not in body

    def test_only_page_views_are_wide(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        for url in ("/deck", "/page/_scratch", "/search?q=garden", "/edit/garden"):
            assert 'class="wide"' not in client.get(url, headers=HOST).get_data(as_text=True), url


class TestPagePreview:
    """/api/page-preview: a page for the search modal's preview pane."""

    def test_rendered_without_ids_and_decks(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n\nSoil.\n\n[[deck list=garden]]\n\n```\n[[deck]]\n```\n")
        data = client.get("/api/page-preview/garden", headers=HOST).get_json()
        assert data["title"] == "Garden"
        assert '<h2 data-pid="beds">Beds</h2>' in data["html"] and ' id="' not in data["html"]
        assert "tasks-app" not in data["html"] and "<em>(deck)</em>" in data["html"]
        assert "[[deck]]" in data["html"]    # code stays as written
        assert "<h1" not in data["html"]

    def test_missing_page(self, client, wiki_dir):
        assert client.get("/api/page-preview/nowhere", headers=HOST).status_code == 404
        assert client.get("/api/page-preview/../etc/passwd", headers=HOST).status_code in (400, 404)


# ===========================================================================
# edit_page GET
# ===========================================================================

class TestEditPageGet:

    def test_edit_existing_page(self, client, wiki_dir):
        write_page(wiki_dir, "test-edit", "# Test\n\nContent here.\n")
        resp = client.get("/edit/test-edit", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "Content here." in text
        assert 'name="content"' in text

    def test_edit_shows_raw_frontmatter_for_editing(self, client, wiki_dir):
        write_page(
            wiki_dir, "tagged",
            "---\ntags: work, urgent\ntldr: Short summary.\n---\n\n# Tagged\n\nBody.\n",
        )
        resp = client.get("/edit/tagged", headers=HOST)
        text = resp.data.decode()
        # The textarea holds the raw file content, frontmatter included verbatim,
        # so it can be edited as plain text like everything else.
        assert "tags: work, urgent" in text
        assert "tldr: Short summary." in text

    def test_edit_new_page_puts_cursor_after_title(self, client):
        resp = client.get("/edit/brand-new-page", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert 'name="content"' in text and 'data-cursor="end"' in text

    def test_new_scratch_note_puts_cursor_after_title(self, client):
        slug = client.post("/api/scratch", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        assert 'data-cursor="end"' in client.get(f"/edit/{slug}", headers=HOST).data.decode()

    def test_existing_page_keeps_cursor_at_start(self, client, wiki_dir):
        write_page(wiki_dir, "test-edit", "# Test\n\nContent here.\n")
        resp = client.get("/edit/test-edit", headers=HOST)
        assert 'data-cursor="end"' not in resp.data.decode()

    def test_edit_section_loads_section_content(self, client, wiki_dir):
        content = "# Title\n\npreamble\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
        write_page(wiki_dir, "sectioned", content)
        resp = client.get("/edit/sectioned?section=1", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "alpha body" in text
        assert "beta body" not in text

    def test_edit_unsafe_slug_returns_400(self, client):
        resp = client.get("/edit/../etc/passwd", headers=HOST)
        assert resp.status_code == 400

    def test_editor_usable_on_mobile(self, client, wiki_dir):
        """The editor must be served on small screens — no 'not available'
        message, and the toolbar carries a mobile Preview toggle."""
        write_page(wiki_dir, "test-edit", "# Test\n\nContent here.\n")
        resp = client.get("/edit/test-edit", headers=HOST)
        assert resp.status_code == 200
        text = resp.data.decode()
        assert "not available on small screens" not in text
        assert "editor-mobile-msg" not in text
        assert 'id="mobile-preview-btn"' in text


# ===========================================================================
# save_page POST (/edit/<slug>)
# ===========================================================================

class TestSavePage:

    def test_save_creates_file_and_redirects(self, client, wiki_dir):
        resp = client.post("/edit/new-page", data={"content": "# New\n\nbody\n"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert (wiki_dir / "new-page.md").exists()

    def test_save_updates_existing_file(self, client, wiki_dir):
        path = write_page(wiki_dir, "update-me", "# Old\n\nold body\n")
        client.post("/edit/update-me", data={"content": "# New\n\nnew body\n"}, headers=HOST)
        assert "new body" in path.read_text(encoding="utf-8")

    def test_save_conflict_redirects_to_editor(self, client, wiki_dir):
        content = "# Title\n\noriginal\n"
        write_page(wiki_dir, "conflict-page", content)
        resp = client.post(
            "/edit/conflict-page",
            data={"content": "# Title\n\nupdated\n", "file_hash": "wrong_hash_value"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302)
        assert "conflict" in resp.headers["Location"]

    def test_save_normalizes_crlf(self, client, wiki_dir):
        resp = client.post(
            "/edit/crlf-page",
            data={"content": "line1\r\nline2\r\n"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302)
        path = wiki_dir / "crlf-page.md"
        assert path.exists()
        assert "\r\n" not in path.read_text(encoding="utf-8")


# ===========================================================================
# api_save POST (/api/save/<slug>)
# ===========================================================================

class TestApiSave:

    def test_saves_new_file(self, client, wiki_dir):
        resp = client.post("/api/save/autosave-page", data={"content": "# Auto\n\nbody\n"}, headers=HOST)
        assert resp.status_code in (200, 204)
        assert (wiki_dir / "autosave-page.md").exists()

    def test_saves_existing_file(self, client, wiki_dir):
        path = write_page(wiki_dir, "auto-existing", "# Old\n\nold\n")
        resp = client.post("/api/save/auto-existing", data={"content": "# New\n\nnew\n"}, headers=HOST)
        assert resp.status_code in (200, 204)
        assert "new" in path.read_text(encoding="utf-8")

    def test_section_save_returns_json(self, client, wiki_dir):
        content = "# T\n\npre\n\n## S\n\nbody\n"
        write_page(wiki_dir, "api-section", content)
        fields = section_fields(client, "api-section", 1)
        resp = client.post("/api/save/api-section", data={**fields, "content": "## S\n\nupdated body\n"}, headers=HOST)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "file_hash" in data
        assert "start_char" in data
        assert "end_char" in data

    def test_section_conflict_returns_409(self, client, wiki_dir):
        content = "# T\n\npre\n\n## S\n\nbody\n"
        write_page(wiki_dir, "conflict-section", content)
        fields = section_fields(client, "conflict-section", 1)
        resp = client.post("/api/save/conflict-section", data={**fields, "content": "## S\n\nnew\n",
                                                                "file_hash": "wrong_hash"}, headers=HOST)
        assert resp.status_code == 409

    def test_full_file_hash_conflict_returns_409(self, client, wiki_dir):
        write_page(wiki_dir, "hash-conflict", "# T\n\nbody\n")
        resp = client.post("/api/save/hash-conflict", data={
            "content": "# T\n\nupdated\n",
            "file_hash": "bad_hash",
        }, headers=HOST)
        assert resp.status_code == 409

    def test_unchanged_content_returns_204_or_json(self, client, wiki_dir):
        content = "# T\n\nbody\n"
        write_page(wiki_dir, "unchanged", content)
        resp = client.post("/api/save/unchanged", data={"content": content}, headers=HOST)
        assert resp.status_code in (200, 204)


# ===========================================================================
# create_page POST (/new)
# ===========================================================================

class TestCreatePage:

    def test_create_page_redirects_to_editor(self, client, wiki_dir):
        resp = client.post("/new", data={"slug": "Brand New Page"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert "brand-new-page" in resp.headers["Location"]

    def test_create_page_writes_file(self, client, wiki_dir):
        client.post("/new", data={"slug": "fresh-page"}, headers=HOST)
        assert (wiki_dir / "fresh-page.md").exists()

    def test_create_page_empty_slug_returns_400(self, client):
        resp = client.post("/new", data={"slug": ""}, headers=HOST)
        assert resp.status_code == 400

    def test_create_existing_page_redirects_to_editor(self, client, wiki_dir):
        write_page(wiki_dir, "existing-page", "# Existing\n\ncontent\n")
        resp = client.post("/new", data={"slug": "Existing Page"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert "existing-page" in resp.headers["Location"]


# ===========================================================================
# delete_page POST (/delete/<slug>)
# ===========================================================================

class TestDeletePage:

    def test_delete_page_removes_file(self, client, wiki_dir):
        path = write_page(wiki_dir, "to-delete", "# Delete Me\n\ncontent\n")
        resp = client.post("/delete/to-delete", headers=HOST)
        assert resp.status_code in (301, 302)
        assert not path.exists()

    def test_delete_redirects_to_next(self, client, wiki_dir):
        write_page(wiki_dir, "gone", "# Gone\n")
        resp = client.post("/delete/gone", data={"next": "/page/other"}, headers=HOST)
        assert resp.headers["Location"] == "/page/other"

    def test_delete_next_pointing_at_deleted_page_falls_back(self, client, wiki_dir):
        write_page(wiki_dir, "gone", "# Gone\n")
        resp = client.post("/delete/gone", data={"next": "/page/gone"}, headers=HOST)
        assert resp.headers["Location"] == "/page/index"

    @pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example"])
    def test_delete_next_rejects_offsite_redirect(self, client, wiki_dir, target):
        # Regression: "//host" and "/\\host" start with "/" but browsers
        # resolve them to another origin (open redirect).
        write_page(wiki_dir, "gone", "# Gone\n")
        resp = client.post("/delete/gone", data={"next": target}, headers=HOST)
        assert resp.headers["Location"] == "/page/index"

    def test_delete_index_returns_400(self, client):
        resp = client.post("/delete/index", headers=HOST)
        assert resp.status_code == 400

    def test_delete_nonexistent_page_returns_404(self, client):
        resp = client.post("/delete/ghost-page", headers=HOST)
        assert resp.status_code == 404


# ===========================================================================
# rename_page POST (/rename/<slug>)
# ===========================================================================

class TestRenamePage:

    def test_rename_moves_file(self, client, wiki_dir):
        write_page(wiki_dir, "old-name", "# Old Name\n\ncontent\n")
        resp = client.post("/rename/old-name", data={"new_name": "new-name"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert (wiki_dir / "new-name.md").exists()
        assert not (wiki_dir / "old-name.md").exists()

    def test_rename_updates_wikilinks_in_other_pages(self, client, wiki_dir):
        write_page(wiki_dir, "old-slug", "# Old Slug\n\ncontent\n")
        ref = write_page(wiki_dir, "referencing", "# Ref\n\nSee [[old-slug]].\n")
        client.post("/rename/old-slug", data={"new_name": "new-slug"}, headers=HOST)
        assert "[[new-slug]]" in ref.read_text(encoding="utf-8")
        assert "[[old-slug]]" not in ref.read_text(encoding="utf-8")

    def test_rename_index_returns_400(self, client):
        resp = client.post("/rename/index", data={"new_name": "homepage"}, headers=HOST)
        assert resp.status_code == 400

    def test_rename_to_existing_slug_returns_redirect_with_error(self, client, wiki_dir):
        write_page(wiki_dir, "page-a", "# A\n\ncontent\n")
        write_page(wiki_dir, "page-b", "# B\n\ncontent\n")
        resp = client.post("/rename/page-a", data={"new_name": "page-b"}, headers=HOST)
        assert resp.status_code in (301, 302)
        assert "rename_error" in resp.headers["Location"]

    def test_rename_from_page_view_returns_to_the_page(self, client, wiki_dir):
        # The page view's ⋯ menu renames through a dialog (from=page): an
        # error, or no change, goes back to the page view, not the editor.
        write_page(wiki_dir, "page-a", "# A\n")
        write_page(wiki_dir, "page-b", "# B\n")
        resp = client.post("/rename/page-a", data={"new_name": "page-b", "from": "page"}, headers=HOST)
        assert resp.headers["Location"].startswith("/page/page-a?rename_error=")
        body = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
        assert '<dialog id="page-rename-dialog" class="scratch-modal" data-open>' in body
        assert 'A page named "page-b" already exists' in html.unescape(body)
        resp = client.post("/rename/page-a", data={"new_name": "page-a", "from": "page"}, headers=HOST)
        assert resp.headers["Location"] == "/page/page-a"
        resp = client.post("/rename/page-a", data={"new_name": "page-c", "from": "page"}, headers=HOST)
        assert resp.headers["Location"] == "/page/page-c"

    def test_rename_updates_pipe_alias_wikilinks(self, client, wiki_dir):
        write_page(wiki_dir, "old-page", "# Old\n\ncontent\n")
        ref = write_page(wiki_dir, "source", "# Src\n\nSee [[old-page|My Label]].\n")
        client.post("/rename/old-page", data={"new_name": "new-page"}, headers=HOST)
        text = ref.read_text(encoding="utf-8")
        assert "[[new-page|My Label]]" in text


# ===========================================================================
# api_preview POST
# ===========================================================================

class TestApiPreview:

    def test_preview_returns_rendered_html(self, client):
        resp = client.post(
            "/api/preview",
            data="**bold** text",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        assert b"<strong>bold</strong>" in resp.data

    def test_preview_renders_wikilinks(self, client, wiki_dir):
        write_page(wiki_dir, "linked", "# Linked\n")
        resp = client.post(
            "/api/preview",
            data="[[linked]]",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        assert b'href="/page/linked"' in resp.data

    def test_preview_renders_frontmatter_live(self, client):
        resp = client.post(
            "/api/preview",
            data="---\ntags: draft\ntldr: In progress.\n---\n\n# Title\n\nBody.\n",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        text = resp.data.decode()
        assert "tags: draft" not in text
        assert '<p class="page-tldr">In progress.</p>' in text
        assert 'class="task-tag page-tag"' in text

    def test_preview_empty_body(self, client):
        resp = client.post(
            "/api/preview",
            data="",
            headers={**HOST, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 200


# ===========================================================================
# api_pages GET
# ===========================================================================

class TestApiPages:

    def test_returns_list_of_slugs(self, client, wiki_dir):
        write_page(wiki_dir, "page-one", "# Page One\n")
        write_page(wiki_dir, "page-two", "# Page Two\n")
        resp = client.get("/api/pages", headers=HOST)
        assert resp.status_code == 200
        slugs = resp.get_json()
        assert "page-one" in slugs
        assert "page-two" in slugs

    def test_returns_json_list(self, client):
        resp = client.get("/api/pages", headers=HOST)
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)


# ===========================================================================
# api_pages_list GET
# ===========================================================================

class TestApiPagesList:

    def test_includes_title_and_mtime(self, client, wiki_dir):
        write_page(wiki_dir, "listable", "# Listable Page\n\nbody\n")
        resp = client.get("/api/pages-list", headers=HOST)
        assert resp.status_code == 200
        pages = resp.get_json()
        slugs = {p["slug"] for p in pages}
        assert "listable" in slugs
        page = next(p for p in pages if p["slug"] == "listable")
        assert page["title"] == "Listable Page"
        assert "mtime" in page

    def test_includes_virtual_special_pages(self, client):
        resp = client.get("/api/pages-list", headers=HOST)
        pages = resp.get_json()
        slugs = {p["slug"] for p in pages}
        assert "_scratch" in slugs
        assert "_daily" not in slugs   # daily notes were removed

    def test_built_in_pages_are_marked(self, client, wiki_dir):
        # Deck and Scratch are modules (the module switcher links to them), so
        # only Deleted Pages is pinned (`special`, git only); all three are
        # `builtin`, which the empty-query list and "Unlinked only" leave out.
        write_page(wiki_dir, "aaa", "# AAA\n")
        pages = client.get("/api/pages-list", headers=HOST).get_json()
        assert not [p for p in pages if p.get("special") and p["title"] != "Deleted Pages"]
        builtin = {p["title"]: p for p in pages if p.get("builtin")}
        assert {"Deck", "Scratch"} <= builtin.keys()
        assert builtin["Deck"]["href"] == "/deck"
        assert not next(p for p in pages if p["slug"] == "aaa").get("builtin")


# ===========================================================================
# search
# ===========================================================================

class TestIterPages:
    @pytest.mark.parametrize("slug", [".leaf/index/n", ".git/n", "_deck/n", "a/_drafts/n", "a/.hidden/n",
                                      "a/../.leaf/n"])
    def test_pages_in_skipped_folders_are_not_served(self, client, wiki_dir, slug):
        """What iter_pages skips no route reads or writes, even by direct URL."""
        path = wiki_dir / (slug.replace("a/../", "") + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# secret\n\nhidden_token_q\n", encoding="utf-8")
        for url in (f"/page/{slug}", f"/edit/{slug}", f"/api/page-preview/{slug}"):
            resp = client.get(url, headers=HOST)
            assert b"hidden_token_q" not in resp.data and resp.status_code in (400, 404), url
        resp = client.post(f"/api/save/{slug}", json={"content": "# x\n"}, headers=HOST)
        assert resp.status_code in (400, 404)
        assert path.read_text(encoding="utf-8") == "# secret\n\nhidden_token_q\n"

class TestSearch:

    def test_api_search_returns_results(self, client, wiki_dir):
        write_page(wiki_dir, "searchable", "# Searchable\n\nunique_token_xyz\n")
        resp = client.get("/api/search?q=unique_token_xyz", headers=HOST)
        assert resp.status_code == 200
        results = resp.get_json()
        assert any(r["slug"] == "searchable" for r in results)

    def test_result_links_to_the_matching_section(self, client, wiki_dir):
        write_page(wiki_dir, "long", "# Long notes\n\nIntro.\n\n## Setup\n\nInstall it.\n\n"
                                     "## Backing **up** the [[data]]\n\nRun git push nightly.\n")
        r = client.get("/api/search?q=nightly", headers=HOST).get_json()[0]
        assert r["href"] == "/page/long?hl=nightly#backing-up-the-data"
        assert "<mark>nightly</mark>" in r["excerpt"] and r["excerpt"].startswith("Backing")

    def test_best_section_wins(self, client, wiki_dir):
        write_page(wiki_dir, "multi", "# Multi\n\n## One\n\ngit once, and lots of other words here.\n\n"
                                      "## Two\n\ngit git git.\n")
        assert client.get("/api/search?q=git", headers=HOST).get_json()[0]["href"] == "/page/multi?hl=git#two"

    def test_title_only_match_opens_the_top(self, client, wiki_dir):
        write_page(wiki_dir, "zebra", "# Zebra\n\n## Food\n\nGrass.\n\n## Home\n\nAfrica.\n")
        assert client.get("/api/search?q=zebra", headers=HOST).get_json()[0]["href"] == "/page/zebra?hl=Zebra"

    def test_match_before_the_first_heading_opens_the_top(self, client, wiki_dir):
        write_page(wiki_dir, "intro", "# Intro\n\nThe quokka lives here.\n\n## Later\n\nNothing.\n")
        assert client.get("/api/search?q=quokka", headers=HOST).get_json()[0]["href"] == "/page/intro?hl=quokka"

    def test_no_anchor_for_headings_without_an_id(self, client, wiki_dir):
        # Only ## and ### headings get ids (anchors_plugin max_level=3).
        write_page(wiki_dir, "deep", "# Deep\n\n## Top\n\nx\n\n#### Fourth\n\nThe narwhal.\n")
        r = client.get("/api/search?q=narwhal", headers=HOST).get_json()[0]
        assert r["href"] == "/page/deep?hl=narwhal" and r["excerpt"].startswith("Fourth")

    def test_search_page_links_to_the_section(self, client, wiki_dir):
        write_page(wiki_dir, "long", "# Long\n\n## Setup\n\nInstall the wombat.\n")
        assert b'href="/page/long?hl=wombat#setup"' in client.get("/search?q=wombat", headers=HOST).data

    def test_exact_phrase(self, client, wiki_dir):
        write_page(wiki_dir, "near", "# Near\n\nThe push to git happened.\n")
        write_page(wiki_dir, "exact", "# Exact\n\nWe git push daily.\n")
        slugs = [r["slug"] for r in client.get('/api/search?q="git push"', headers=HOST).get_json()]
        assert slugs == ["exact"]

    def test_phrase_marked_in_excerpt(self, client, wiki_dir):
        write_page(wiki_dir, "trip", "# Trip\n\nTwo weeks of holiday in Italy.\n")
        r = client.get('/api/search?q="holiday in italy"', headers=HOST).get_json()[0]
        assert "<mark>holiday in Italy</mark>" in r["excerpt"]

    def test_exclude_word(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n\ngit and docker.\n")
        write_page(wiki_dir, "b", "# B\n\ngit only.\n")
        slugs = [r["slug"] for r in client.get("/api/search?q=git+-dockers", headers=HOST).get_json()]
        assert slugs == ["b"]

    def test_tag_filter_pages_and_tasks(self, client, wiki_dir):
        write_page(wiki_dir, "tagged", "---\ntags: [Leaf, ui]\n---\n# Tagged\n\nThe date picker.\n")
        write_page(wiki_dir, "untagged", "# Untagged\n\nThe date picker again.\n")
        client.post("/api/deck", json={"title": "Fix date picker", "tags": ["leaf"]}, headers=HOST)
        client.post("/api/deck", json={"title": "Date picker elsewhere"}, headers=HOST)
        results = client.get("/api/search?q=date+picker+%23leaf", headers=HOST).get_json()
        assert {r["title"] for r in results} == {"Tagged", "[Task] Fix date picker"}
        results = client.get("/api/search?q=date+picker+-%23leaf", headers=HOST).get_json()
        assert {r["title"] for r in results} == {"Untagged", "[Task] Date picker elsewhere"}

    def test_tag_only_query(self, client, wiki_dir):
        write_page(wiki_dir, "tagged", "---\ntags: leaf\n---\n# Tagged\n\nNotes.\n")
        write_page(wiki_dir, "mentions", "# Mentions\n\nA leaf fell.\n")
        assert [r["slug"] for r in client.get("/api/search?q=%23leaf", headers=HOST).get_json()] == ["tagged"]

    def test_only_exclusions_find_nothing(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n\ngit.\n")
        assert client.get("/api/search?q=-git", headers=HOST).get_json() == []

    def test_exact_phrase_ranks_first(self, client, wiki_dir):
        # Regression: "alex again" put a note with both words apart (and
        # more often) above the one saying "Alex again".
        write_page(wiki_dir, "prep", "# Alex prep\n\nAlex, Alex and Alex. Ask again, again.\n")
        write_page(wiki_dir, "situation", "# Situation\n\n## Context\n\nCalm.\n\n"
                                          "## The ask\n\nThen Alex   again pushed back.\n")
        results = client.get("/api/search?q=alex+again", headers=HOST).get_json()
        assert [r["slug"] for r in results] == ["situation", "prep"]
        assert results[0]["href"] == "/page/situation?hl=Alex+again#the-ask"
        assert "<mark>Alex   again</mark>" in results[0]["excerpt"]

    def test_single_word_has_no_phrase_boost(self, client, wiki_dir):
        write_page(wiki_dir, "once", "# Once\n\nzebra.\n")
        write_page(wiki_dir, "often", "# Zebra\n\nzebra zebra.\n")
        assert client.get("/api/search?q=zebra", headers=HOST).get_json()[0]["slug"] == "often"

    def test_unlinked_only(self, client, wiki_dir):
        write_page(wiki_dir, "hub", "# Hub\n\nSee [[linked]]. Zebra.\n")
        write_page(wiki_dir, "linked", "# Linked\n\nZebra.\n")
        write_page(wiki_dir, "orphan", "# Orphan\n\nZebra.\n")
        client.post("/api/deck", json={"title": "Zebra task"}, headers=HOST)
        all_ = {r["title"] for r in client.get("/api/search?q=zebra", headers=HOST).get_json()}
        assert all_ == {"Hub", "Linked", "Orphan", "[Task] Zebra task"}
        only = [r["slug"] for r in client.get("/api/search?q=zebra&unlinked=1", headers=HOST).get_json()]
        assert sorted(only) == ["hub", "orphan"]   # nothing links to hub either; no tasks

    def test_result_meta_tells_alike_titles_apart(self, client, wiki_dir):
        import os
        path = write_page(wiki_dir, "conversations/2026-07-08_alex/prep", "# Alex Debrief\n\nquokka\n")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        write_page(wiki_dir, "quokka", "# Quokka\n\nquokka\n")
        open_ = client.post("/api/deck", json={"title": "quokka open"}, headers=HOST).get_json()
        write_page(wiki_dir, "zoo", "# Zoo\n\n[[deck list=zoo]]\n")
        done = client.post("/api/deck", json={"title": "quokka done", "tags": ["zoo"]}, headers=HOST).get_json()
        client.put(f"/api/deck/{done['id']}", json={"completed": True}, headers=HOST)
        meta = {r["title"]: r["meta"] for r in client.get("/api/search?q=quokka", headers=HOST).get_json()}
        assert meta["Alex Debrief"] == "conversations/2026-07-08_alex · edited 2001-09-09"
        assert meta["Quokka"].startswith("edited 20")
        assert meta["[Task] quokka open"] == "Deck · open"
        assert re.fullmatch(r"Deck › zoo · done \d{4}-\d\d-\d\d", meta["[Task] quokka done"])
        assert open_["id"]
        page = client.get("/search?q=quokka", headers=HOST).get_data(as_text=True)
        assert '<span class="result-meta">conversations/2026-07-08_alex · edited 2001-09-09</span>' in page

    def test_tag_pill_lists_exactly_the_tagged(self, client, wiki_dir):
        # Regression: a pill searched the tag as a word, so it listed every
        # page mentioning "ops", tagged or not.
        write_page(wiki_dir, "tagged", "---\ntags: [ops]\n---\n# Runbook\n\nRestart steps.\n")
        write_page(wiki_dir, "mentions", "# Notes\n\nAsk the ops team.\n")
        client.post("/api/deck", json={"title": "Rotate keys", "tags": ["ops"]}, headers=HOST)
        page = client.get("/page/tagged", headers=HOST).get_data(as_text=True)
        href = re.search(r'class="task-tag page-tag" href="([^"]+)"', page).group(1)
        assert href == "/search?q=%23ops"
        results = client.get(href, headers=HOST).get_data(as_text=True)
        assert "Runbook" in results and "Rotate keys" in results and "Notes" not in results

    def test_limit(self, client, wiki_dir):
        for i in range(20):
            write_page(wiki_dir, f"walrus-{i}", f"# Walrus {i}\n\nwalrus\n")
        get = lambda qs: client.get(f"/api/search?q=walrus{qs}", headers=HOST).get_json()  # noqa: E731
        assert len(get("")) == 15
        assert len(get("&limit=16")) == 16
        assert len(get("&limit=1000")) == 20      # clamped to 100, only 20 exist
        assert len(get("&limit=0")) == 1          # at least one
        assert len(get("&limit=lots")) == 15      # not a number: the default

    def test_search_page_shows_more_with_a_count(self, client, wiki_dir):
        for i in range(20):
            write_page(wiki_dir, f"walrus-{i}", f"# Walrus {i}\n\nwalrus\n")
        page = client.get("/search?q=walrus", headers=HOST).get_data(as_text=True)
        assert page.count('class="search-result-card"') == 20
        assert '<p class="search-count">20 results</p>' in page
        for i in range(20, 60):
            write_page(wiki_dir, f"walrus-{i}", f"# Walrus {i}\n\nwalrus\n")
        page = client.get("/search?q=walrus&unlinked=1", headers=HOST).get_data(as_text=True)
        assert page.count('class="search-result-card"') == 50
        assert "The first 50 results · unlinked pages only" in page

    def test_date_filters(self, client, wiki_dir):
        import os
        for slug, day in [("july", 1_783_000_000), ("sept", 1_789_000_000), ("oct", 1_791_000_000)]:
            os.utime(write_page(wiki_dir, slug, f"# {slug}\n\nyak notes\n"), (day, day))
        # july: 2026-07-02, sept: 2026-09-10, oct: 2026-10-02 (UTC; any zone keeps the months)
        slugs = lambda q: sorted(r["slug"] for r in client.get(  # noqa: E731
            "/api/search", query_string={"q": q}, headers=HOST).get_json())
        assert slugs("yak edited:2026-09") == ["sept"]
        assert slugs("yak after:2026-09") == ["oct", "sept"]
        assert slugs("yak before:2026-09") == ["july"]
        assert slugs("yak -edited:2026-09") == ["july", "oct"]

    def test_only_dates_list_newest_first(self, client, wiki_dir):
        import os
        for slug, day in [("july", 1_783_000_000), ("sept", 1_789_000_000), ("oct", 1_791_000_000)]:
            os.utime(write_page(wiki_dir, slug, f"# {slug}\n\nnotes\n"), (day, day))
        os.utime(wiki_dir / "index.md", (1_000_000_000, 1_000_000_000))  # the fixture's, 2001
        results = client.get("/api/search", query_string={"q": "after:2026-07"}, headers=HOST).get_json()
        assert [r["slug"] for r in results] == ["oct", "sept", "july"]

    def test_tasks_dated_by_last_activity(self, client, wiki_dir):
        from datetime import date
        task = client.post("/api/deck", json={"title": "yak shaving"}, headers=HOST).get_json()
        today = date.today().isoformat()
        hits = client.get("/api/search", query_string={"q": f"yak edited:{today}"}, headers=HOST).get_json()
        assert [r.get("task_id") for r in hits] == [task["id"]]
        assert client.get("/api/search", query_string={"q": "yak before:2000"}, headers=HOST).get_json() == []

    def test_found_by_file_and_folder_names(self, client, wiki_dir):
        write_page(wiki_dir, "messages/2026-06-18_roof-update-alex-robin",
                   "# Roof repair status\n\nRevised timeline.\n")
        slugs = [r["slug"] for r in client.get("/api/search?q=robin", headers=HOST).get_json()]
        assert slugs == ["messages/2026-06-18_roof-update-alex-robin"]
        assert client.get("/api/search?q=messages", headers=HOST).get_json()  # folder name

    def test_file_name_does_not_outrank_content(self, client, wiki_dir):
        write_page(wiki_dir, "zebra-notes", "# Notes\n\nNothing here.\n")
        write_page(wiki_dir, "savanna", "# Savanna\n\nThe zebra grazes. A zebra runs.\n")
        slugs = [r["slug"] for r in client.get("/api/search?q=zebra", headers=HOST).get_json()]
        assert slugs == ["savanna", "zebra-notes"]

    def test_api_search_empty_query(self, client):
        resp = client.get("/api/search?q=", headers=HOST)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_api_search_title_match_ranked_higher(self, client, wiki_dir):
        write_page(wiki_dir, "title-match", "# uniquetoken\n\nbody\n")
        write_page(wiki_dir, "body-only", "# Other\n\nuniquetoken appears once\n")
        resp = client.get("/api/search?q=uniquetoken", headers=HOST)
        results = resp.get_json()
        # title match should come first
        assert results[0]["slug"] == "title-match"

    def test_search_page_returns_200(self, client, wiki_dir):
        write_page(wiki_dir, "findable", "# Findable\n\nfindme_content\n")
        resp = client.get("/search?q=findme_content", headers=HOST)
        assert resp.status_code == 200
        assert b"findme_content" in resp.data

    def test_api_search_excerpt_has_mark(self, client, wiki_dir):
        write_page(wiki_dir, "marked", "# Marked\n\nsome highlighted word\n")
        resp = client.get("/api/search?q=highlighted", headers=HOST)
        results = resp.get_json()
        assert results
        assert "<mark>" in results[0]["excerpt"]

    def test_link_targets_are_not_indexed(self, client, wiki_dir):
        # Regression: every page with a /page/… link matched "page".
        write_page(wiki_dir, "linker", "# Linker\n\nSee [the notes](/page/zzqtarget).\n")
        results = client.get("/api/search?q=zzqtarget", headers=HOST).get_json()
        assert not any(r["slug"] == "linker" for r in results)
        results = client.get("/api/search?q=notes", headers=HOST).get_json()
        assert any(r["slug"] == "linker" for r in results)

    def test_bare_urls_stay_searchable(self, client, wiki_dir):
        write_page(wiki_dir, "bare", "# Bare\n\nsee https://zzqdomain.example/x\n")
        results = client.get("/api/search?q=zzqdomain", headers=HOST).get_json()
        assert any(r["slug"] == "bare" for r in results)

    def test_task_link_targets_are_not_indexed(self, client, wiki_dir):
        client.post("/api/deck", json={"title": "Read", "description": "[doc](https://zzqhost.example)"})
        results = client.get("/api/search?q=zzqhost", headers=HOST).get_json()
        assert results == []

    def test_task_found_by_tag(self, client, wiki_dir):
        # Regression: tasks were indexed by title and notes only, so a page's
        # tag pill (/search?q=<tag>) never listed the tasks with that tag.
        client.post("/api/deck", json={"title": "Call Bob", "tags": ["zzqtag"]})
        results = client.get("/api/search?q=zzqtag", headers=HOST).get_json()
        assert [r["title"] for r in results] == ["[Task] Call Bob"]
        assert "<mark>zzqtag</mark>" in results[0]["excerpt"]

    def test_non_utf8_page_does_not_break_search(self, client, wiki_dir):
        # Regression: one undecodable file made every search a 500.
        (wiki_dir / "latin1.md").write_bytes("# Latin\n\nzzqword Gr\xf6\xdfe\n".encode("latin-1"))
        write_page(wiki_dir, "fine", "# Fine\n\nzzqword here\n")
        resp = client.get("/api/search?q=zzqword", headers=HOST)
        assert resp.status_code == 200
        assert {r["slug"] for r in resp.get_json()} == {"latin1", "fine"}

    def test_windows_line_ends_read_as_newlines(self, client, wiki_dir):
        # A page saved with CRLF line ends reads as Python's read_text reads
        # it: no "\r" in excerpts, sections found as with "\n".
        (wiki_dir / "crlf.md").write_bytes(b"# Crlf\r\n\r\n## Part\r\n\r\nzzqword one\r\ntwo\r\n")
        [r] = client.get("/api/search?q=zzqword", headers=HOST).get_json()
        assert r["excerpt"] == "Part <mark>zzqword</mark> one two"
        assert r["href"].endswith("#part")

    def test_index_is_reused_until_something_changes(self, client, wiki_dir):
        path = write_page(wiki_dir, "cached", "# Cached\n\nzzqold\n")
        assert [r["slug"] for r in client.get("/api/search?q=zzqold", headers=HOST).get_json()] == ["cached"]
        path.write_text("# Cached\n\nzzqnew\n", encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        assert [r["slug"] for r in client.get("/api/search?q=zzqnew", headers=HOST).get_json()] == ["cached"]
        assert client.get("/api/search?q=zzqold", headers=HOST).get_json() == []

    def test_new_task_and_new_page_reach_the_cached_index(self, client, wiki_dir):
        client.get("/api/search?q=warm", headers=HOST)
        client.post("/api/deck", json={"title": "zzqtask"})
        assert client.get("/api/search?q=zzqtask", headers=HOST).get_json()
        write_page(wiki_dir, "later", "# Later\n\nzzqpage\n")
        assert client.get("/api/search?q=zzqpage", headers=HOST).get_json()

    def test_search_includes_task_results(self, client, wiki_dir):
        client.post("/api/deck", json={"title": "unique_task_token_abc"})
        resp = client.get("/api/search?q=unique_task_token_abc", headers=HOST)
        results = resp.get_json()
        assert any(r.get("type") == "task" for r in results)

    def test_multi_word_query_matches_scattered_terms(self, client, wiki_dir):
        # Both words present but not as a phrase — old substring search scored 0.
        write_page(wiki_dir, "scattered",
                   "# Scattered\n\nthe vector lives here, database is elsewhere\n")
        resp = client.get("/api/search?q=vector+database", headers=HOST)
        results = resp.get_json()
        assert any(r["slug"] == "scattered" for r in results)

    def test_typo_still_finds_page(self, client, wiki_dir):
        write_page(wiki_dir, "dbnotes", "# Database notes\n\nabout the database engine\n")
        resp = client.get("/api/search?q=databse", headers=HOST)  # transposed typo
        results = resp.get_json()
        assert any(r["slug"] == "dbnotes" for r in results)

    def test_word_variation_matches_via_stem(self, client, wiki_dir):
        write_page(wiki_dir, "runlog", "# Run log\n\nI was running in the park today\n")
        resp = client.get("/api/search?q=run", headers=HOST)
        results = resp.get_json()
        assert any(r["slug"] == "runlog" for r in results)

    def test_rarer_term_ranked_higher(self, client, wiki_dir):
        # "notes" is common across pages; "quasar" is rare — the quasar page wins.
        for i in range(4):
            write_page(wiki_dir, f"common{i}", f"# Notes {i}\n\nordinary notes about notes\n")
        write_page(wiki_dir, "rare", "# Notes rare\n\nnotes about the quasar phenomenon\n")
        resp = client.get("/api/search?q=notes+quasar", headers=HOST)
        results = resp.get_json()
        assert results[0]["slug"] == "rare"

    def test_search_page_task_links_to_the_task_by_id(self, client, wiki_dir):
        # A task result must open the deck (/deck) at that task (by id) — not the
        # bare _deck storage slug, and not a title search.
        tid = client.post("/api/deck", json={"title": "buymilk urgent chore"}).get_json()["id"]
        resp = client.get("/search?q=buymilk+urgent+chore", headers=HOST)
        body = resp.get_data(as_text=True)
        assert "/page/_deck" not in body
        assert f'href="/deck?task={tid}&amp;view=open"' in body

    def test_search_page_completed_task_links_to_completed_tab(self, client, wiki_dir):
        r = client.post("/api/deck", json={"title": "finishreport tuvwxyz"})
        tid = r.get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        resp = client.get("/search?q=finishreport+tuvwxyz", headers=HOST)
        body = resp.get_data(as_text=True)
        assert f'href="/deck?task={tid}&amp;view=completed"' in body


# ===========================================================================
# api_toggle_checkbox
# ===========================================================================

class TestApiToggleCheckbox:

    def test_toggles_unchecked_to_checked(self, client, wiki_dir):
        path = write_page(wiki_dir, "checkbox-page", "# CB\n\n- [ ] task one\n- [ ] task two\n")
        resp = client.post("/api/toggle-checkbox/checkbox-page", data={"idx": "0"}, headers=HOST)
        assert resp.status_code == 204
        text = path.read_text(encoding="utf-8")
        assert "- [x] task one" in text
        assert "- [ ] task two" in text

    def test_toggles_checked_to_unchecked(self, client, wiki_dir):
        path = write_page(wiki_dir, "toggle-back", "# CB\n\n- [x] done task\n")
        client.post("/api/toggle-checkbox/toggle-back", data={"idx": "0"}, headers=HOST)
        assert "- [ ] done task" in path.read_text(encoding="utf-8")

    def test_returns_404_for_missing_page(self, client):
        resp = client.post("/api/toggle-checkbox/no-such-page", data={"idx": "0"}, headers=HOST)
        assert resp.status_code == 404

    def test_returns_400_for_negative_idx(self, client, wiki_dir):
        write_page(wiki_dir, "bad-idx", "# CB\n\n- [ ] task\n")
        resp = client.post("/api/toggle-checkbox/bad-idx", data={"idx": "-1"}, headers=HOST)
        assert resp.status_code == 400

    @pytest.mark.parametrize("idx", ["abc", "", "1.5"])
    def test_returns_400_for_non_integer_idx(self, client, wiki_dir, idx):
        # Regression: int() on the raw form value raised → 500.
        write_page(wiki_dir, "bad-idx", "# CB\n\n- [ ] task\n")
        resp = client.post("/api/toggle-checkbox/bad-idx", data={"idx": idx}, headers=HOST)
        assert resp.status_code == 400


# ===========================================================================
# api_upload
# ===========================================================================

class TestApiUpload:

    def test_upload_file(self, client, wiki_dir):
        from io import BytesIO
        data = {"file": (BytesIO(b"fake image data"), "test.png")}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 200
        url = resp.get_json()["url"]
        assert url.startswith("/_assets/")
        assert (wiki_dir / "_assets" / "test.png").exists()

    def test_upload_conflict_returns_409(self, client, wiki_dir):
        from io import BytesIO
        (wiki_dir / "_assets" / "existing.png").write_bytes(b"original")
        data = {"file": (BytesIO(b"new data"), "existing.png")}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 409
        assert resp.get_json()["conflict"] is True

    def test_upload_overwrite(self, client, wiki_dir):
        from io import BytesIO
        (wiki_dir / "_assets" / "overwrite.txt").write_bytes(b"old")
        data = {"file": (BytesIO(b"new"), "overwrite.txt"), "overwrite": "1"}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 200

    def test_upload_unique_flag_generates_uuid_name(self, client, wiki_dir):
        from io import BytesIO
        data = {"file": (BytesIO(b"img"), "photo.png"), "unique": "1"}
        resp = client.post("/api/upload", data=data, content_type="multipart/form-data", headers=HOST)
        assert resp.status_code == 200
        url = resp.get_json()["url"]
        # UUID filenames don't match the original name
        assert "photo.png" not in url



class TestServeUpload:
    """Uploads must not run scripts with Leaf's origin (stored XSS)."""

    def put(self, wiki_dir, name, data=b"x"):
        (wiki_dir / "_assets" / name).write_bytes(data)
        return f"/_assets/{name}"

    def test_image_is_inline(self, client, wiki_dir):
        resp = client.get(self.put(wiki_dir, "pic.png"), headers=HOST)
        assert resp.status_code == 200
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_pdf_is_inline(self, client, wiki_dir):
        resp = client.get(self.put(wiki_dir, "doc.pdf"), headers=HOST)
        assert "attachment" not in resp.headers.get("Content-Disposition", "")

    def test_html_is_a_download(self, client, wiki_dir):
        url = self.put(wiki_dir, "evil.html", b"<script>alert(1)</script>")
        resp = client.get(url, headers=HOST)
        assert resp.headers["Content-Disposition"].startswith("attachment")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_script_upload_is_not_served_as_a_script(self, client, wiki_dir):
        # script-src 'self' trusts Leaf's origin, so an uploaded .js served as
        # text/javascript would run from <script src="/_assets/…">.
        resp = client.get(self.put(wiki_dir, "x.js", b"window.x=1"), headers=HOST)
        assert resp.headers["Content-Type"] == "application/octet-stream"
        assert resp.headers["Content-Disposition"].startswith("attachment")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_inline_upload_keeps_its_type(self, client, wiki_dir):
        assert client.get(self.put(wiki_dir, "doc.pdf"), headers=HOST).headers["Content-Type"] == "application/pdf"

    def test_unknown_type_is_a_download(self, client, wiki_dir):
        resp = client.get(self.put(wiki_dir, "blob.xyzzy"), headers=HOST)
        assert resp.headers["Content-Disposition"].startswith("attachment")

    def test_svg_is_inline_but_sandboxed(self, client, wiki_dir):
        url = self.put(wiki_dir, "draw.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
        resp = client.get(url, headers=HOST)
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.headers["Content-Security-Policy"].startswith("sandbox")

    def test_upload_rejects_dot_names(self, client, wiki_dir):
        from io import BytesIO
        for name in ("..", ".git", ".hidden"):
            data = {"file": (BytesIO(b"x"), "a.png"), "filename": name, "overwrite": "1"}
            resp = client.post("/api/upload", data=data,
                               content_type="multipart/form-data", headers=HOST)
            assert resp.status_code == 400, name

# ===========================================================================
# Authentication (LEAF_PASSWORD)
# ===========================================================================

class TestAuth:

    @pytest.fixture()
    def auth_client(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "testpassword")
        return client

    def test_login_page_has_no_action_buttons(self, auth_client):
        assert 'class="action-buttons"' not in auth_client.get("/login", headers=HOST).data.decode()

    def test_sign_out_only_when_signed_in(self, auth_client):
        assert "signout-form" not in auth_client.get("/login", headers=HOST).data.decode()
        auth_client.post("/login", data={"password": "testpassword"}, headers=HOST)
        assert "signout-form" in auth_client.get("/page/index", headers=HOST).data.decode()

    def test_login_page_shows_remote_badge_in_its_form(self, auth_client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_ALLOW_REMOTE", True)
        body = auth_client.get("/login", headers=HOST).data.decode()
        assert body.count('class="remote-badge"') == 1
        assert re.search(r'<h2>Sign in to Leaf<span class="remote-badge"[^>]*>🌐</span></h2>', body)
        monkeypatch.setattr(settings, "LEAF_ALLOW_REMOTE", False)
        assert "remote-badge" not in auth_client.get("/login", headers=HOST).data.decode()

    def test_unauthenticated_redirects_to_login(self, auth_client):
        resp = auth_client.get("/page/index", headers=HOST)
        assert resp.status_code in (301, 302)
        assert "/login" in resp.headers["Location"]

    def test_login_with_correct_password(self, auth_client):
        resp = auth_client.post(
            "/login",
            data={"password": "testpassword"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302)
        assert "login" not in resp.headers["Location"]

    def test_login_redirects_to_next(self, auth_client):
        resp = auth_client.post("/login?next=/page/foo",
                                data={"password": "testpassword"}, headers=HOST)
        assert resp.headers["Location"] == "/page/foo"

    @pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example"])
    def test_login_next_rejects_offsite_redirect(self, auth_client, target):
        # Regression: open redirect via protocol-relative next URL.
        resp = auth_client.post("/login", query_string={"next": target},
                                data={"password": "testpassword"}, headers=HOST)
        assert resp.headers["Location"] == "/"

    def test_login_without_password_configured_redirects_home(self, client):
        resp = client.get("/login", headers=HOST)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

    def test_public_assets_served_without_auth(self, auth_client):
        resp = auth_client.get("/public/style.css", headers=HOST)
        assert resp.status_code == 200

    def test_home_screen_app_files_served_without_auth(self, auth_client):
        # Browsers may fetch the manifest and icons without the session cookie.
        manifest = auth_client.get("/public/manifest.json", headers=HOST)
        assert manifest.status_code == 200
        data = json.loads(manifest.data)
        assert data["name"] == "Leaf" and data["start_url"] == "/"
        for icon in data["icons"]:
            resp = auth_client.get(icon["src"], headers=HOST)
            assert resp.status_code == 200, icon["src"]
            assert resp.mimetype == icon["type"]
        assert auth_client.get("/public/apple-touch-icon.png", headers=HOST).status_code == 200

    def test_pages_link_the_manifest_and_touch_icon(self, auth_client):
        body = auth_client.get("/login", headers=HOST).get_data(as_text=True)
        assert '<link rel="manifest" href="/public/manifest.json">' in body
        assert '<link rel="apple-touch-icon" href="/public/apple-touch-icon.png">' in body

    def test_login_with_wrong_password_shows_error(self, auth_client):
        resp = auth_client.post(
            "/login",
            data={"password": "wrongpassword"},
            headers=HOST,
        )
        assert resp.status_code == 200
        assert b"Incorrect" in resp.data

    def test_login_page_accessible_without_auth(self, auth_client):
        resp = auth_client.get("/login", headers=HOST)
        assert resp.status_code == 200

    def test_logout_clears_session(self, auth_client):
        auth_client.post("/login", data={"password": "testpassword"}, headers=HOST)
        resp = auth_client.post("/logout", headers=HOST)
        assert resp.status_code in (301, 302)
        # After logout, protected page should redirect to login
        follow = auth_client.get("/page/index", headers=HOST)
        assert follow.status_code in (301, 302)
        assert "login" in follow.headers["Location"]


# ===========================================================================
# API pages — special virtual pages
# ===========================================================================

class TestSpecialPages:

    def test_scratch_index_page_loads(self, client):
        resp = client.get("/page/_scratch", headers=HOST)
        assert resp.status_code == 200
        assert b"Scratch" in resp.data

    def test_api_scratch_creates_note(self, client, wiki_dir):
        resp = client.post("/api/scratch", data={"title": "My Note"}, headers=HOST)
        assert resp.status_code == 200
        slug = resp.get_json()["slug"]
        assert (wiki_dir / f"{slug}.md").exists()

    def test_api_scratch_slash_in_title_makes_no_folder(self, client, wiki_dir):
        # A / used to make a folder, whose note was then no scratch note.
        slug = client.post("/api/scratch", data={"title": "A/B test"}, headers=HOST).get_json()["slug"]
        assert slug.endswith("_a-b-test") and "/" not in slug
        assert (wiki_dir / f"{slug}.md").read_text() == "# A/B test\n\n"

    def test_api_scratch_same_title_same_second_makes_two_notes(self, client, wiki_dir):
        slugs = {client.post("/api/scratch", data={"title": "Twin"}, headers=HOST).get_json()["slug"]
                 for _ in range(3)}
        assert len(slugs) == 3

    def test_search_can_keep_only_scratch_notes(self, client, wiki_dir):
        # The Scratch page's filter: the same search, scratch notes only.
        write_page(wiki_dir, "2026-09-01_10-00-00_bob-call", "# Bob call\n\nRoof repair quote.\n")
        write_page(wiki_dir, "2026-09-02_10-00-00_groceries", "# Groceries\n\nMilk, eggs.\n")
        write_page(wiki_dir, "roof", "# Roof\n\nThe roof repair plan.\n")
        client.post("/api/deck", json={"title": "Roof repair"}, headers=HOST)
        get = lambda q, **kw: client.get("/api/search", query_string={"q": q, **kw}, headers=HOST).get_json()
        assert {r["slug"] for r in get("roof repair")} >= {"roof", "2026-09-01_10-00-00_bob-call"}
        hits = get("roof repair", scratch="1")
        assert [r["slug"] for r in hits] == ["2026-09-01_10-00-00_bob-call"]
        assert "<mark>" in hits[0]["excerpt"] and "hl=" in hits[0]["href"]
        # The query syntax works as everywhere.
        assert [r["slug"] for r in get('"milk, eggs"', scratch="1")] == ["2026-09-02_10-00-00_groceries"]
        assert [r["slug"] for r in get("roof -quote", scratch="1")] == []
        # Every match, not only the search modal's 100.
        for i in range(120):
            write_page(wiki_dir, f"2026-08-01_10-00-{i // 60:02d}_n{i}", f"# Note {i}\n\nzebra\n")
        assert len(get("zebra", scratch="1", limit="1000")) == 120
        assert len(get("zebra", limit="1000")) == 100

    def test_scratch_page_has_a_filter(self, client, wiki_dir):
        write_page(wiki_dir, "2026-09-01_10-00-00_bob-call", "# Bob call\n")
        body = client.get("/page/_scratch?q=bob", headers=HOST).get_data(as_text=True)
        assert 'id="scratch-filter"' in body and 'value="bob"' in body
        assert 'data-slug="2026-09-01_10-00-00_bob-call"' in body
        # No notes, nothing to filter.
        for f in wiki_dir.glob("2026-*.md"):
            f.unlink()
        assert 'id="scratch-filter"' not in client.get("/page/_scratch", headers=HOST).get_data(as_text=True)

    def test_daily_notes_are_gone(self, client, wiki_dir):
        # The feature was removed: no endpoint, no shortcut, no index. An old
        # daily note on disk is just an ordinary page.
        assert client.post("/api/daily", headers=HOST).status_code in (404, 405)
        (wiki_dir / "2026-01-01_daily.md").write_text("# 2026-01-01\n\nold\n", encoding="utf-8")
        resp = client.get("/page/2026-01-01_daily", headers=HOST)
        assert resp.status_code == 200 and b"old" in resp.data
        # The shortcuts help (in every page's shell) no longer offers it.
        assert "daily" not in resp.get_data(as_text=True).lower().replace("2026-01-01_daily", "")


# ===========================================================================
# Host header check
# ===========================================================================

class TestHostHeader:

    def test_bad_host_returns_400(self, client):
        old_allow_remote = settings.LEAF_ALLOW_REMOTE
        settings.LEAF_ALLOW_REMOTE = False
        try:
            resp = client.get("/page/index", headers={"Host": "evil.com"})
            assert resp.status_code == 400
        finally:
            settings.LEAF_ALLOW_REMOTE = old_allow_remote

    def test_allow_remote_skips_check(self, client):
        old = settings.LEAF_ALLOW_REMOTE
        settings.LEAF_ALLOW_REMOTE = True
        try:
            resp = client.get("/page/index", headers={"Host": "any.host.example"})
            assert resp.status_code != 400
        finally:
            settings.LEAF_ALLOW_REMOTE = old


    def test_leaf_hosts_names_who_may_come_in(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_HOSTS", frozenset({"wiki.example.org", "[::1]"}))
        for allow_remote in (False, True):   # set, the check stays on even with LEAF_ALLOW_REMOTE
            monkeypatch.setattr(settings, "LEAF_ALLOW_REMOTE", allow_remote)
            for host in ("wiki.example.org", "Wiki.Example.org:8443", "localhost:5000", "[::1]:5000"):
                assert client.get("/page/index", headers={"Host": host}).status_code == 200, host
            for host in ("evil.example", "wiki.example.org.evil.example", "192.168.1.5:5000"):
                assert client.get("/page/index", headers={"Host": host}).status_code == 400, host

# ===========================================================================
# Behind an HTTPS proxy: the login cookie, throttling guesses
# ===========================================================================

class TestProduction:

    def test_no_framing_by_other_sites(self, client, wiki_dir):
        for path in ("/page/index", "/deck", "/api/version", "/login"):
            resp = client.get(path, headers=HOST)
            assert "frame-ancestors 'self'" in resp.headers["Content-Security-Policy"], path
            assert resp.headers["X-Frame-Options"] == "SAMEORIGIN", path

    def test_only_leafs_own_scripts_run(self, client, wiki_dir):
        # A page's raw HTML (written by an AI client after a prompt injection,
        # say) must not run a script: script-src 'self' without
        # 'unsafe-inline' blocks <script>, onerror= and javascript: links.
        write_page(wiki_dir, "evil", "# Evil\n\n<script>window.x=1</script><img src=x onerror=alert(1)>\n")
        for path in ("/page/evil", "/edit/evil", "/deck", "/api/deck", "/login", "/public/leaf.js"):
            resp = client.get(path, headers=HOST)
            csp = resp.headers["Content-Security-Policy"]
            assert csp == client.get("/page/index", headers=HOST).headers["Content-Security-Policy"], path
            assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
            for rule in ("object-src 'none'", "base-uri 'self'", "form-action 'self'"):
                assert rule in csp, (path, rule)
            assert resp.headers["X-Content-Type-Options"] == "nosniff", path

    def test_framing_rule_joins_an_existing_policy(self, client, wiki_dir):
        (wiki_dir / "_assets" / "x.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        csp = client.get("/_assets/x.svg", headers=HOST).headers["Content-Security-Policy"]
        assert csp.startswith("sandbox; default-src 'none'") and csp.endswith("; frame-ancestors 'self'")

class TestHttpsAndThrottle:

    @pytest.fixture()
    def auth_client(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "pässwört")
        return client

    def login(self, client, password, **headers):
        return client.post("/login", data={"password": password}, headers={**HOST, **headers})

    def test_cookie_is_secure_only_over_https(self, auth_client):
        plain = self.login(auth_client, "pässwört").headers["Set-Cookie"]
        assert "HttpOnly" in plain and "Secure" not in plain
        proxied = self.login(auth_client, "pässwört", **{"X-Forwarded-Proto": "https"}).headers["Set-Cookie"]
        assert "; Secure" in proxied

    def test_non_ascii_password(self, auth_client):
        assert self.login(auth_client, "pässwört").status_code == 302
        assert self.login(auth_client, "passwort").status_code == 200

    def test_each_client_counts_alone(self, auth_client, monkeypatch):
        # Behind a proxy (requests from 127.0.0.1) the client is the address
        # the proxy appended last to X-Forwarded-For.
        for _ in range(5):
            self.login(auth_client, "nope", **{"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
        assert self.login(auth_client, "pässwört", **{"X-Forwarded-For": "203.0.113.9"}).status_code == 429
        assert self.login(auth_client, "pässwört", **{"X-Forwarded-For": "198.51.100.7"}).status_code == 302
        assert self.login(auth_client, "pässwört").status_code == 302    # the proxy host itself

# ===========================================================================
# Search result hrefs (server-computed destination URLs)
# ===========================================================================

class TestSearchResultHref:

    def test_page_result_has_href(self, client, wiki_dir):
        write_page(wiki_dir, "gardening", "# Gardening\n\nTomatoes and peppers.\n")
        results = client.get("/api/search?q=tomatoes", headers=HOST).get_json()
        assert results, "expected a search hit"
        assert results[0]["href"] == f"/page/{results[0]['slug']}?hl=Tomatoes"

    def test_page_result_carries_the_matched_words(self, client, wiki_dir):
        # The page highlights what matched, as written there: stems, typos
        # and phrases included, each once, tasks without.
        write_page(wiki_dir, "run", "# Notes\n\nShe runs. He ran. RUNS again.\n")
        client.post("/api/deck", json={"title": "running shoes"}, headers=HOST)
        results = client.get("/api/search?q=running", headers=HOST).get_json()
        page = next(r for r in results if r["slug"] == "run")
        assert page["href"] == "/page/run?hl=runs"
        task = next(r for r in results if r.get("type") == "task")
        assert "hl=" not in task["href"]
        phrase = client.get("/api/search?q=runs+again", headers=HOST).get_json()[0]
        assert phrase["href"] == "/page/run?hl=RUNS+again"

    def test_task_result_href_opens_deck_at_the_task(self, client, wiki_dir):
        # Regression: the href prefilled the title into the deck's filter
        # field, where a "#word" in the title reads as a tag filter.
        tid = client.post("/api/deck", json={"title": "Water the ferns #3"},
                          headers=HOST).get_json()["id"]
        results = client.get("/api/search?q=ferns", headers=HOST).get_json()
        task_hits = [r for r in results if r.get("type") == "task"]
        assert task_hits, "expected a task hit"
        assert task_hits[0]["href"] == f"/deck?task={tid}&view=open"


class TestBacklinkCacheInvalidation:

    def test_backlinks_reflect_on_disk_edits(self, client, wiki_dir):
        write_page(wiki_dir, "target", "# Target\n\nbody\n")
        write_page(wiki_dir, "source", "# Source\n\nno links yet\n")
        resp = client.get("/page/target", headers=HOST)
        assert "No pages link here" in resp.data.decode() or "Source" not in resp.data.decode()
        # Edit on disk (new mtime) — the cached link targets must be re-derived
        import time
        time.sleep(0.01)
        write_page(wiki_dir, "source", "# Source\n\nSee [[Target]].\n")
        resp = client.get("/page/target", headers=HOST)
        assert "Source" in resp.data.decode()


# ===========================================================================
# Linked tasks (deck tasks that wikilink to a page)
# ===========================================================================

class TestTaskBacklinks:

    def test_task_linking_a_section_names_it_and_marks_the_heading(self, client, wiki_dir):
        # [[Page#Section]] in a task: it is still a linked task of the page,
        # names the section (linking to the heading), and the heading gets a
        # chip listing the section's tasks. A /page/<slug>#anchor link too.
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n\nSoil.\n\n## Garden paths\n\nGravel.\n")
        weed = client.post("/api/deck", json={"title": "Weed [[Garden#Beds]]"}, headers=HOST).get_json()["id"]
        client.post("/api/deck", json={"title": "Rake", "description": "[the paths](/page/garden#garden-paths)"}, headers=HOST)
        client.post("/api/deck", json={"title": "Water [[garden]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        items = re.findall(r"<li>(.*?)</li>", section)
        assert len(items) == 3
        weed_item = next(i for i in items if "Weed" in i)
        assert "Weed Garden › Beds</a>" in weed_item
        assert '<a class="task-bl-section" href="#beds">§ Beds</a>' in weed_item
        assert '<a class="task-bl-section" href="#garden-paths">§ Garden paths</a>' in next(i for i in items if "Rake" in i)
        assert "task-bl-section" not in next(i for i in items if "Water" in i)
        beds = html.split('<h2 id="beds">', 1)[1].split("</div>", 1)[0]
        assert '<details class="section-tasks"><summary title="1 linked task">☑ 1</summary>' in beds
        assert f'href="/deck?task={weed}&amp;view=open"' in beds
        paths = html.split('<h2 id="garden-paths">', 1)[1].split("</div>", 1)[0]
        assert "☑ 1" in paths and "Rake" in paths
        # The chip sits after the heading: the TOC and anchors are unchanged.
        assert '<h2 id="beds">Beds</h2>' in html

    def test_task_with_wikilink_listed_on_page(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Water [[Garden]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' in html
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        assert "Water Garden" in section

    def test_linked_task_links_to_the_task_by_id(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        tid = client.post("/api/deck", json={"title": "Water [[Garden]]"},
                          headers=HOST).get_json()["id"]
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        assert f'href="/deck?task={tid}&amp;view=open"' in section

    def test_linked_task_entry_is_a_single_link(self, client, wiki_dir):
        # Regression: the task title's own wikilinks were rendered as <a>
        # inside the outer deck <a> — nested anchors, which browsers split so
        # half the entry went to the deck and half to the page.
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Water [[Garden]] and [x](/page/garden)"},
                    headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        items = re.findall(r"<li>(.*?)</li>", section)
        assert len(items) == 1
        assert items[0].count("<a ") == 1
        assert "Water Garden and x" in items[0]

    def test_wikilink_in_description_and_labelled_link(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Chores",
                                       "description": "see [[garden|the beds]]"}, headers=HOST)
        assert task_backlinks(client, "garden") == ["Chores"]

    def test_markdown_page_link_counts(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Read [notes](/page/garden)"}, headers=HOST)
        assert len(task_backlinks(client, "garden")) == 1

    def test_completed_and_unrelated_tasks_excluded(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        t = client.post("/api/deck", json={"title": "Done [[garden]]"}, headers=HOST).get_json()
        client.put(f"/api/deck/{t['id']}", json={"completed": True}, headers=HOST)
        client.post("/api/deck", json={"title": "About [[kitchen]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' not in html

    def test_task_title_is_escaped(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "<img src=x onerror=alert(1)> [[garden]]"},
                    headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert "<img src=x" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


class TestPlainTaskTitle:

    def test_search_shows_a_task_title_plain(self, client):
        client.post("/api/deck", json={"title": "Read [the RFC](https://e.x/rfc) on [[Garden]]"}, headers=HOST)
        results = client.get("/api/search?q=rfc", headers=HOST).get_json()
        assert [r["title"] for r in results] == ["[Task] Read the RFC on Garden"]


# ===========================================================================
# The _raw inbox is gone
# ===========================================================================

def test_raw_inbox_is_gone(client, wiki_dir):
    # Removed: an old _raw.md is an ordinary page now, nothing is created,
    # and the paste endpoints are gone.
    assert client.post("/api/raw/append-url", data={"url": "https://e.x"}, headers=HOST).status_code in (404, 405)
    assert client.post("/api/raw/create-page", data={"text": "x"}, headers=HOST).status_code in (404, 405)
    assert client.get("/page/_raw", headers=HOST).status_code == 302   # to the editor, like any missing page
    assert not (wiki_dir / "_raw.md").exists()
    (wiki_dir / "_raw.md").write_text("# Raw\n\n- [Ex](https://example.com)\n", encoding="utf-8")
    body = client.get("/page/_raw", headers=HOST).get_data(as_text=True)
    assert 'href="https://example.com"' in body


# ===========================================================================
# Rename keeps every kind of reference working
# ===========================================================================

class TestRenameUpdatesAllReferences:
    """Backlinks count markdown links, and deck tasks link pages too — a
    rename used to update only [[wikilinks]] in pages, so every other link to
    the page broke. It also rewrote wikilinks inside code examples."""

    def rename(self, client, old, new):
        resp = client.post(f"/rename/{old}", data={"new_name": new}, headers=HOST)
        assert resp.status_code == 302

    def test_markdown_links_rewritten(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        ref = write_page(wiki_dir, "ref", (
            "[bare](old) [ext](old.md) [abs](/page/old) [anchor](old#part)\n"
            "[other](older) [web](https://example.com/old) [img](/_assets/old.png)\n"))
        self.rename(client, "old", "new")
        assert ref.read_text() == (
            "[bare](new) [ext](new.md) [abs](/page/new) [anchor](new#part)\n"
            "[other](older) [web](https://example.com/old) [img](/_assets/old.png)\n")

    def test_relative_links_across_directories(self, client, wiki_dir):
        write_page(wiki_dir, "notes/old", "# Old\n")
        sibling = write_page(wiki_dir, "notes/sib", "[s](old) [s2](./old.md)\n")
        cousin = write_page(wiki_dir, "journal/day", "[c](../notes/old)\n")
        self.rename(client, "notes/old", "archive/new")
        assert sibling.read_text() == "[s](../archive/new) [s2](../archive/new.md)\n"
        assert cousin.read_text() == "[c](../archive/new)\n"
        assert len(backlinks(client, "archive/new")) == 2

    def test_backlinks_survive_rename(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        write_page(wiki_dir, "ref", "# Ref\n\n[x](old.md) and [[old]]\n")
        assert backlinks(client, "old") == ["ref"]
        self.rename(client, "old", "new")
        assert backlinks(client, "new") == ["ref"]

    def test_links_inside_code_untouched(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        ref = write_page(wiki_dir, "ref", (
            "[[old]] and `[[old]]` and [x](old)\n\n"
            "```\n[[old]] [x](old)\n```\n"))
        self.rename(client, "old", "new")
        assert ref.read_text() == (
            "[[new]] and `[[old]]` and [x](new)\n\n"
            "```\n[[old]] [x](old)\n```\n")

    def test_deck_tasks_follow_rename(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        linked = client.post("/api/deck", json={
            "title": "Do [[old]] and [[Old|label]]", "description": "see [x](/page/old)",
            }, headers=HOST).get_json()
        done = client.post("/api/deck", json={"title": "Done [[old]]"},
                           headers=HOST).get_json()
        client.put(f"/api/deck/{done['id']}", json={"completed": True}, headers=HOST)
        other = client.post("/api/deck", json={"title": "[[older]]"},
                            headers=HOST).get_json()
        self.rename(client, "old", "new")
        tasks = {t["id"]: t for t in client.get("/api/deck", headers=HOST).get_json()}
        assert tasks[linked["id"]]["title"] == "Do [[new]] and [[new|label]]"
        assert tasks[linked["id"]]["description"] == "see [x](/page/new)"
        assert tasks[other["id"]]["title"] == "[[older]]"
        completed = client.get("/api/deck/completed", headers=HOST).get_json()
        assert completed[0]["title"] == "Done [[new]]"
        html = client.get("/page/new", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' in html

    def test_rename_without_deck_files_does_not_create_them(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        self.rename(client, "old", "new")
        assert not (wiki_dir / "_deck" / "tasks.json").exists()


# ===========================================================================
# /deck — find the page that embeds the deck
# ===========================================================================

class TestDeckPage:
    """/deck is a built-in page with one full main deck. It used to redirect
    to whichever page embedded [[deck]], so where the due badge and task links
    landed depended on the wiki's pages (and, before that, /page/deck opened
    the editor in a wiki without deck.md)."""

    def get(self, client, qs=""):
        resp = client.get("/deck" + qs, headers=HOST)
        assert resp.status_code == 200
        return resp.get_data(as_text=True)

    def test_renders_one_main_deck(self, client, wiki_dir):
        body = self.get(client)
        assert body.count('class="tasks-app"') == 1
        assert 'data-init-list=""' in body
        assert 'data-init-tag=""' in body
        assert 'data-init-view=""' in body   # ?view= / session / Today decide
        assert "<h1>Deck</h1>" in body

    def test_does_not_depend_on_pages(self, client, wiki_dir):
        # Embeds elsewhere (even a deck.md) are ordinary pages now.
        write_page(wiki_dir, "deck", "# My deck\n\n[[deck tag=work]]\n\n[[deck view=plan]]\n")
        body = self.get(client, "?task=abc&view=today")
        assert body.count('class="tasks-app"') == 1
        assert "My deck" not in body
        assert 'data-init-tag=""' in body

    def test_list(self, client, wiki_dir):
        body = self.get(client, "?list=Groceries&task=abc")
        assert 'data-init-list="groceries"' in body
        assert '<a href="/deck">Deck</a>' in body
        assert "<h1>groceries</h1>" in body

    def test_list_name_escaped(self, client, wiki_dir):
        body = self.get(client, "?list=<b>x")
        assert "<b>x" not in body

    def test_in_search_modal_list(self, client, wiki_dir):
        pages = client.get("/api/pages-list", headers=HOST).get_json()
        deck = [p for p in pages if p.get("href") == "/deck"]
        assert len(deck) == 1 and deck[0]["title"] == "Deck"

    def test_linked_task_in_list_links_to_list(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        write_page(wiki_dir, "shopping", "# Shopping\n\n[[deck list=shop]]\n")
        tid = client.post("/api/deck", json={"title": "Seeds for [[Garden]]", "tags": ["shop"]},
                          headers=HOST).get_json()["id"]
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert f'href="/deck?task={tid}&amp;view=open&amp;list=shop"' in body

    def test_due_count_follows_the_deck_home(self, client, wiki_dir):
        from datetime import date
        today = date.today().isoformat()
        for title, tags in (("Pay rent", ["task"]), ("Milk", ["shop"]),
                            ("Call Bob", ["task", "waiting"]), ("Book trip", [])):
            client.post("/api/deck", json={"title": title, "tags": tags, "due_date": today}, headers=HOST)
        count = lambda: client.get("/api/deck/due-count", headers=HOST).get_json()["count"]
        assert count() == 4   # no home: every task
        write_page(wiki_dir, "deck", "# Deck\n\n[[deck list=task home=1]]\n")
        assert count() == 2   # the home's list
        write_page(wiki_dir, "deck", "# Deck\n\n[[deck list=task tag=-waiting home=1]]\n")
        os.utime(wiki_dir / "deck.md", (2_000_000_000, 2_000_000_000))
        assert count() == 1   # and its tag filter

    def test_deck_counts_are_rendered_into_the_page(self, client, wiki_dir):
        # In the HTML, not only fetched later: the Deck link must not change
        # width after the page has painted.
        from datetime import date, timedelta
        today = date.today()
        for data in ({"title": "Late", "due_date": (today - timedelta(days=1)).isoformat()},
                     {"title": "Started", "start_date": today.isoformat()},
                     {"title": "Later", "start_date": (today + timedelta(days=3)).isoformat()},
                     {"title": "Someday"}):
            client.post("/api/deck", json=data, headers=HOST)
        assert client.get("/api/deck/due-count", headers=HOST).get_json() == {
            "count": 1, "overdue": 1, "today": 0, "focus": 2}
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        counts = ('<span class="module-counts"><span class="module-due">1</span>'
                  '<span class="module-focus">2</span></span>')
        assert body.count(counts) == 2   # switcher and tab bar
        assert body.count('title="Deck: 2 in Focus · 1 overdue"') == 2
        # Zero is hidden, not "0".
        client.post("/api/deck/batch-delete", json={"ids": [t["id"] for t in client.get(
            "/api/deck", headers=HOST).get_json()]}, headers=HOST)
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert '<span class="module-due" hidden></span><span class="module-focus" hidden></span>' in body

    def test_due_badge_and_task_links_use_it(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": "Water [[Garden]]"}, headers=HOST)
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'href="/deck" class="module-link" data-module="deck"' in body
        assert "/page/deck" not in body


# ===========================================================================
# Cross-site request forgery
# ===========================================================================

class TestCrossSiteWrites:
    """Regression: any website could post a form to localhost:5000 and delete
    or overwrite pages (or deck tasks); the Host check alone let it through."""

    EVIL = {**HOST, "Origin": "https://evil.example"}
    SAME = {**HOST, "Origin": "http://localhost:5000"}

    def test_forged_delete_and_edit_are_refused(self, client, wiki_dir):
        write_page(wiki_dir, "victim", "# Victim\n\nkeep me\n")
        assert client.post("/delete/victim", headers=self.EVIL).status_code == 403
        resp = client.post("/edit/victim", data={"content": "<script>x</script>"}, headers=self.EVIL)
        assert resp.status_code == 403
        assert (wiki_dir / "victim.md").read_text(encoding="utf-8") == "# Victim\n\nkeep me\n"

    def test_forged_deck_writes_are_refused(self, client, wiki_dir):
        # text/plain: a cross-site fetch/form can send it without a preflight.
        tid = client.post("/api/deck", json={"title": "Keep"}, headers=HOST).get_json()["id"]
        resp = client.post("/api/deck", data='{"title": "Spam"}', content_type="text/plain", headers=self.EVIL)
        assert resp.status_code == 403
        assert client.delete(f"/api/deck/{tid}", headers=self.EVIL).status_code == 403
        assert client.put(f"/api/deck/{tid}", json={"title": "x"}, headers=self.EVIL).status_code == 403
        assert [t["title"] for t in client.get("/api/deck", headers=HOST).get_json()] == ["Keep"]

    def test_null_origin_is_refused(self, client, wiki_dir):
        # A sandboxed iframe on an attacker's page posts with Origin: null.
        write_page(wiki_dir, "victim", "# Victim\n")
        assert client.post("/delete/victim", headers={**HOST, "Origin": "null"}).status_code == 403
        assert (wiki_dir / "victim.md").exists()

    def test_same_origin_and_originless_writes_work(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n")
        write_page(wiki_dir, "b", "# B\n")
        assert client.post("/delete/a", headers=self.SAME).status_code == 302
        assert client.post("/delete/b", headers=HOST).status_code == 302   # curl, scripts
        assert client.post("/api/deck", json={"title": "Ok"}, headers=self.SAME).status_code == 201

    def test_reads_are_not_affected(self, client, wiki_dir):
        write_page(wiki_dir, "r", "# R\n")
        assert client.get("/page/r", headers=self.EVIL).status_code == 200


# ── Leaf version ──────────────────────────────────────────────────────────────


class TestVersion:
    def test_shown_in_shortcuts_modal_and_api(self, client, wiki_dir, monkeypatch):
        v = client.get("/api/version", headers=HOST).get_json()["version"]
        monkeypatch.setattr(settings, "LEAF_BUILD", "abc1234 2026-09-26 12:00")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f'<p class="leaf-version">Leaf {v} · abc1234 2026-09-26 12:00</p>' in body
        assert f'<meta name="generator" content="Leaf {v}">' in body
        assert client.get("/api/version", headers=HOST).get_json() == {
            "version": v, "build": "abc1234 2026-09-26 12:00"}

    def test_unknown_build_shows_version_only(self, client, wiki_dir, monkeypatch):
        v = client.get("/api/version", headers=HOST).get_json()["version"]
        monkeypatch.setattr(settings, "LEAF_BUILD", "unknown")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f'<p class="leaf-version">Leaf {v}</p>' in body

    def test_bottom_left_shows_version_and_short_hash(self, client, wiki_dir, monkeypatch):
        v = client.get("/api/version", headers=HOST).get_json()["version"]
        monkeypatch.setattr(settings, "LEAF_BUILD", "abc1234 2026-09-26 12:00")
        for path in ("/page/index", "/deck", "/edit/index"):
            body = client.get(path, headers=HOST).get_data(as_text=True)
            assert ('<div class="leaf-build" title="Build abc1234 2026-09-26 12:00">'
                    f'Leaf {v} · abc1234</div>') in body, path
        monkeypatch.setattr(settings, "LEAF_BUILD", "abc1234 2026-09-26 12:00 +dirty")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f">Leaf {v} · abc1234+dirty</div>" in body
        monkeypatch.setattr(settings, "LEAF_BUILD", "unknown")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f'<div class="leaf-build">Leaf {v}</div>' in body

    def test_build_escaped(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_BUILD", "<b>")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert "· &lt;b&gt;</p>" in body

    @staticmethod
    def _start(wiki: Path | str | None) -> subprocess.CompletedProcess:
        """Start Leaf with LEAF_DATA=wiki (None: unset) until it listens."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("LEAF_")}
        if wiki is not None:
            env["LEAF_DATA"] = str(wiki)
        proc = subprocess.Popen([str(binary()), "-addr", "127.0.0.1:0"], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = proc.stdout.readline()
        if " listening on " in out:
            proc.terminate()
        _out, err = proc.communicate(timeout=60)
        return subprocess.CompletedProcess(proc.args, 0 if " listening on " in out else proc.returncode,
                                           out, err)

    @pytest.mark.parametrize("wiki", [None, ""])
    def test_refuses_to_start_without_leaf_data(self, wiki):
        # No default wiki (there was src/data, a folder in the checkout).
        r = self._start(wiki)
        assert r.returncode != 0
        assert "LEAF_DATA isn't set" in r.stderr

    def test_refuses_a_missing_wiki_folder(self, tmp_path):
        # Leaf never makes the wiki itself, so a mistyped LEAF_DATA can't
        # start an empty wiki somewhere else.
        r = self._start(tmp_path / "nowhere")
        assert r.returncode != 0
        assert f"no wiki at {tmp_path / 'nowhere'}" in r.stderr
        assert not (tmp_path / "nowhere").exists()

    def test_starts_with_an_empty_wiki_folder(self, tmp_path):
        r = self._start(tmp_path)
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "index.md").exists() and (tmp_path / "_assets").is_dir()

class TestLocale:
    """LEAF_LOCALE: a BCP 47 tag the browser formats dates with (data-locale)."""

    def test_on_the_body_when_set(self, client, monkeypatch):
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert "data-locale" not in body and "data-date-format" not in body
        monkeypatch.setattr(settings, "LEAF_LOCALE", "de-AT")
        monkeypatch.setattr(settings, "LEAF_DATE_FORMAT", "iso")
        for url in ("/page/index", "/deck", "/edit/index"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            assert 'data-locale="de-AT"' in body and 'data-date-format="iso"' in body, url


class TestNoshare:
    """Sharing mode leaves out the pages LEAF_NOSHARE names (#tags, folders/)."""

    @pytest.fixture(autouse=True)
    def _config(self, monkeypatch):
        monkeypatch.setattr(settings, "NOSHARE_TAGS", frozenset({"noshare", "people"}))
        monkeypatch.setattr(settings, "NOSHARE_FOLDERS", ("personal/",))

    def _wiki(self, wiki_dir):
        write_page(wiki_dir, "home2", "# Home2\n\n[[Bob]], [[personal/diary|my diary]] and [[Garden]].\n")
        write_page(wiki_dir, "bob", "---\ntags: people\n---\n# Bob Secret\n\nAbout [[Home2]]. Garden too.\n")
        write_page(wiki_dir, "personal/diary", "# Diary Secret\n\nGarden thoughts, see [[Home2]].\n")
        write_page(wiki_dir, "garden", "# Garden\n\nBeds. See [[Home2]].\n")

    def test_off_everything_shows(self, client, wiki_dir):
        self._wiki(wiki_dir)
        assert "Bob Secret" in client.get("/page/bob", headers=HOST).get_data(as_text=True)
        assert any(r["slug"] == "bob" for r in client.get("/api/search?q=about", headers=HOST).get_json())

    def test_pages_hidden_in_sharing_mode(self, client, wiki_dir):
        self._wiki(wiki_dir)
        client.set_cookie("leaf-sharing", "1")
        for url in ("/page/bob", "/page/personal/diary", "/edit/bob", "/history/bob"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            assert "Hidden page" in body and "Secret" not in body, url
        assert client.get("/api/page-preview/bob", headers=HOST).status_code == 404
        assert "Garden" in client.get("/page/garden", headers=HOST).get_data(as_text=True)

    def test_left_out_of_search_and_lists(self, client, wiki_dir):
        self._wiki(wiki_dir)
        client.set_cookie("leaf-sharing", "1")
        slugs = {r["slug"] for r in client.get("/api/search?q=garden", headers=HOST).get_json()}
        assert slugs == {"garden", "home2"}
        assert "Secret" not in client.get("/search?q=secret", headers=HOST).get_data(as_text=True)
        assert set(client.get("/api/pages", headers=HOST).get_json()) >= {"garden", "home2"}
        assert not {"bob", "personal/diary"} & set(client.get("/api/pages", headers=HOST).get_json())
        assert not {"bob", "personal/diary"} & {p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()}

    def test_links_to_them_become_plain_text(self, client, wiki_dir):
        self._wiki(wiki_dir)
        client.set_cookie("leaf-sharing", "1")
        body = client.get("/page/home2", headers=HOST).get_data(as_text=True)
        assert '<span class="wikilink-hidden">Bob</span>' in body
        assert '<span class="wikilink-hidden">my diary</span>' in body
        assert 'href="/page/garden"' in body and 'href="/page/bob"' not in body
        # Nor do they show among the page's backlinks or links.
        assert "Bob Secret" not in body and "Diary Secret" not in body

    def test_scratch_notes_too(self, client, wiki_dir):
        write_page(wiki_dir, "2026-10-01_09-00-00_salary", "---\ntags: noshare\n---\n# Salary talk\n")
        write_page(wiki_dir, "2026-10-01_10-00-00_lunch", "# Lunch\n")
        client.set_cookie("leaf-sharing", "1")
        body = client.get("/page/_scratch", headers=HOST).get_data(as_text=True)
        assert "Lunch" in body and "Salary" not in body


# ===========================================================================
# Move a scratch note to the wiki (/promote/<slug>)
# ===========================================================================

SCRATCH = "2026-10-08_14-03-11_meeting-notes"


class TestPromotePage:

    def test_scratch_note_menu_has_move_to_wiki(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Meeting with Alex\n\nnotes\n")
        write_page(wiki_dir, "conversations/2026-07-08_debrief", "# Debrief\n")
        body = client.get(f"/page/{SCRATCH}", headers=HOST).get_data(as_text=True)
        menu = body.split('id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert "Move to wiki…" in re.findall(r'role="menuitem"[^>]*>([^<]+)', menu)
        dialog = body.split('<dialog id="page-promote-dialog"', 1)[1].split("</dialog>", 1)[0]
        # The name is the note's title, not its timestamped slug; the date
        # can be kept; existing folders are suggested.
        assert 'id="promote-name" value="Meeting with Alex"' in dialog
        assert 'value="2026-10-08"> Keep the date (2026-10-08)' in dialog
        assert '<option value="conversations/">' in dialog
        # An ordinary page has no such item.
        write_page(wiki_dir, "garden", "# Garden\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert "Move to wiki" not in body

    def test_promote_moves_the_note_and_its_links(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Meeting with Alex\n\nnotes\n")
        ref = write_page(wiki_dir, "ref", f"# Ref\n\nSee [[{SCRATCH}]] and [x](/page/{SCRATCH}#a).\n")
        resp = client.post(f"/promote/{SCRATCH}", headers=HOST, data={
            "folder": "conversations", "name": "Alex sync", "keep_date": "1"})
        assert resp.headers["Location"] == "/page/conversations/2026-10-08_alex-sync"
        assert not (wiki_dir / f"{SCRATCH}.md").exists()
        moved = wiki_dir / "conversations" / "2026-10-08_alex-sync.md"
        assert moved.read_text(encoding="utf-8") == "# Meeting with Alex\n\nnotes\n"
        text = ref.read_text(encoding="utf-8")
        assert "[[conversations/2026-10-08_alex-sync]]" in text
        assert "(/page/conversations/2026-10-08_alex-sync#a)" in text
        # It left the Scratch list.
        body = client.get("/page/_scratch", headers=HOST).get_data(as_text=True)
        assert "Meeting with Alex" not in body

    def test_promote_refuses_and_says_why(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Notes\n")
        write_page(wiki_dir, "taken", "# Taken\n")
        for data, error in [({"name": "Taken"}, "already exists"),
                            ({"name": " "}, "Give the page a name"),
                            ({"name": "2026-01-01_10-00-00_x"}, "still be a scratch note")]:
            resp = client.post(f"/promote/{SCRATCH}", data=data, headers=HOST)
            loc = resp.headers["Location"]
            assert loc.startswith(f"/page/{SCRATCH}?promote_error=")
            body = client.get(loc, headers=HOST).get_data(as_text=True)
            assert '<dialog id="page-promote-dialog" class="scratch-modal promote-modal" data-title="Notes" data-open>' in body
            assert error in body
        assert (wiki_dir / f"{SCRATCH}.md").exists()

    def test_promote_opens_the_page_to_link_from(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Alex sync\n")
        index = write_page(wiki_dir, "conversations/index", "# Conversations\n\n- [[conversations/old]]\n")
        body = client.get(f"/page/{SCRATCH}", headers=HOST).get_data(as_text=True)
        assert 'id="promote-link-from" list="wiki-pages"' in body
        assert '<option value="conversations/index">Conversations</option>' in body
        resp = client.post(f"/promote/{SCRATCH}", headers=HOST, data={
            "folder": "conversations", "name": "Alex sync", "link_from": "/page/conversations/index",
            "copied": "1"})
        # The editor opens on that page, the link to paste; the page itself
        # is left as it was.
        loc = resp.headers["Location"]
        assert loc == "/edit/conversations/index?paste=conversations/alex-sync&copied=1"
        assert index.read_text(encoding="utf-8") == "# Conversations\n\n- [[conversations/old]]\n"
        body = client.get(loc, headers=HOST).get_data(as_text=True)
        hint = body.split('<div class="paste-hint"', 1)[1].split("</div>", 1)[0]
        assert 'data-link="[[conversations/alex-sync|Alex sync]]"' in hint
        assert "Link copied." in hint
        # Without the clipboard (copied missing) the hint says to copy it.
        body = client.get("/edit/conversations/index?paste=conversations/alex-sync",
                          headers=HOST).get_data(as_text=True)
        assert "Copy its link and paste it where it belongs." in body and ">Copy link</button>" in body
        # Nothing for an unknown page.
        body = client.get("/edit/conversations/index?paste=nowhere", headers=HOST).get_data(as_text=True)
        assert "paste-hint" not in body

    def test_promote_from_a_page_already_linking_opens_the_page(self, client, wiki_dir):
        # The page linked to the note, so it links to the new page now:
        # nothing to paste.
        write_page(wiki_dir, SCRATCH, "# Notes\n")
        hub = write_page(wiki_dir, "hub", f"# Hub\n\nSee [[{SCRATCH}]].\n")
        resp = client.post(f"/promote/{SCRATCH}", data={"name": "Notes", "link_from": "hub"}, headers=HOST)
        assert resp.headers["Location"] == "/page/notes"
        assert hub.read_text(encoding="utf-8") == "# Hub\n\nSee [[notes]].\n"

    def test_promote_refuses_an_unknown_link_page(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Notes\n")
        loc = client.post(f"/promote/{SCRATCH}", data={"name": "Notes", "link_from": "nowhere"},
                          headers=HOST).headers["Location"]
        assert loc.startswith(f"/page/{SCRATCH}?promote_error=")
        assert "There is no page “nowhere” to link from" in client.get(loc, headers=HOST).get_data(as_text=True)
        assert (wiki_dir / f"{SCRATCH}.md").exists() and not (wiki_dir / "notes.md").exists()

    def test_promote_only_scratch_notes(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        assert client.post("/promote/garden", data={"name": "x"}, headers=HOST).status_code == 400
        assert client.post(f"/promote/{SCRATCH}", data={"name": "x"}, headers=HOST).status_code == 404


# ===========================================================================
# Append a scratch note to a page (/append/<slug>)
# ===========================================================================


class TestAppendToPage:

    def test_menu_and_dialog(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Alex 1:1\n\nnotes\n")
        write_page(wiki_dir, "people/alex", "# Alex Morgan\n")
        body = client.get(f"/page/{SCRATCH}", headers=HOST).get_data(as_text=True)
        menu = body.split('id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert "Append to page…" in re.findall(r'role="menuitem"[^>]*>([^<]+)', menu)
        dialog = body.split('<dialog id="page-append-dialog"', 1)[1].split("</dialog>", 1)[0]
        pages = body.split('<datalist id="wiki-pages">', 1)[1].split("</datalist>", 1)[0]
        assert '<option value="people/alex">Alex Morgan</option>' in pages
        assert SCRATCH not in pages                                 # no scratch notes offered
        assert 'list="wiki-pages"' in dialog
        assert 'id="append-heading" value="Alex 1:1 (2026-10-08)"' in dialog

    def test_append_moves_note_into_the_page(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "---\ntags: sync\n---\n# Alex 1:1\n\nnotes\n\n## Next\n\nsteps\n")
        page = write_page(wiki_dir, "people/alex", "# Alex\n\nAbout Alex.\n")
        ref = write_page(wiki_dir, "ref", f"See [[{SCRATCH}]], [[{SCRATCH}#Next|next]] "
                                          f"and [it](/page/{SCRATCH}).\n")
        resp = client.post(f"/append/{SCRATCH}", headers=HOST,
                           data={"page": "/page/people/alex", "heading": "Alex 1:1 (2026-10-08)"})
        assert resp.headers["Location"] == "/page/people/alex#alex-11-2026-10-08"
        assert not (wiki_dir / f"{SCRATCH}.md").exists()
        assert page.read_text(encoding="utf-8") == (
            "---\ntags: sync\n---\n# Alex\n\nAbout Alex.\n\n"
            "## Alex 1:1 (2026-10-08)\n\nnotes\n\n### Next\n\nsteps\n")
        # Links follow to the section; one with its own anchor keeps it.
        assert ref.read_text(encoding="utf-8") == (
            "See [[people/alex#Alex 1:1 (2026-10-08)]], [[people/alex#Next|next]] "
            "and [it](/page/people/alex#alex-11-2026-10-08).\n")
        body = client.get("/page/people/alex", headers=HOST).get_data(as_text=True)
        assert 'id="alex-11-2026-10-08"' in body

    def test_append_refuses_and_says_why(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Notes\n\nx\n")
        write_page(wiki_dir, "garden", "# Garden\n\n## Notes (2026-10-08)\n")
        for data, error in [({"page": "nowhere", "heading": "H"}, "There is no page"),
                            ({"page": "", "heading": "H"}, "Choose a page"),
                            ({"page": SCRATCH, "heading": "H"}, "another page than this note"),
                            ({"page": "garden", "heading": " | "}, "Give the section a heading"),
                            ({"page": "garden", "heading": "Notes (2026-10-08)"}, "already has a section")]:
            loc = client.post(f"/append/{SCRATCH}", data=data, headers=HOST).headers["Location"]
            assert loc.startswith(f"/page/{SCRATCH}?append_error=")
            body = client.get(loc, headers=HOST).get_data(as_text=True)
            assert '<dialog id="page-append-dialog" class="scratch-modal promote-modal" data-open>' in body
            assert error in body
        assert (wiki_dir / f"{SCRATCH}.md").exists()
        assert (wiki_dir / "garden.md").read_text(encoding="utf-8") == "# Garden\n\n## Notes (2026-10-08)\n"

    def test_append_only_scratch_notes(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        write_page(wiki_dir, "other", "# Other\n")
        assert client.post("/append/garden", data={"page": "other", "heading": "H"},
                           headers=HOST).status_code == 400

    def test_append_points_deck_links_at_the_section(self, client, wiki_dir):
        write_page(wiki_dir, SCRATCH, "# Notes\n")
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/deck", json={"title": f"Follow up [[{SCRATCH}]]"}, headers=HOST)
        client.post(f"/append/{SCRATCH}", data={"page": "garden", "heading": "Notes"}, headers=HOST)
        titles = [t["title"] for t in client.get("/api/deck", headers=HOST).get_json()]
        assert titles == ["Follow up [[garden#Notes]]"]


# ===========================================================================
# Bug: rename_page crashes when new slug is in a new subdirectory
# ===========================================================================

class TestRenamePageMkdir:

    def test_rename_to_existing_flat_slug_works(self, client, wiki_dir):
        write_page(wiki_dir, "flat-old", "# Old\n\ncontent\n")
        resp = client.post(
            "/rename/flat-old",
            data={"new_name": "flat-new"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        assert (wiki_dir / "flat-new.md").exists()
        assert not (wiki_dir / "flat-old.md").exists()

    def test_rename_to_new_subdirectory_does_not_crash(self, client, wiki_dir):
        """Renaming my-page to journal/my-page must create the journal/ dir."""
        write_page(wiki_dir, "my-page", "# My Page\n\ncontent\n")
        resp = client.post(
            "/rename/my-page",
            data={"new_name": "journal/my-page"},
            headers=HOST,
        )
        # Must redirect, not 500
        assert resp.status_code in (301, 302, 303)
        new_file = wiki_dir / "journal" / "my-page.md"
        assert new_file.exists(), "File was not moved to the new subdirectory"
        assert not (wiki_dir / "my-page.md").exists()

    def test_rename_to_deeply_nested_subdirectory(self, client, wiki_dir):
        write_page(wiki_dir, "src", "# Src\n\ncontent\n")
        resp = client.post(
            "/rename/src",
            data={"new_name": "a/b/c/src"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        assert (wiki_dir / "a" / "b" / "c" / "src.md").exists()

    def test_rename_within_same_subdirectory(self, client, wiki_dir):
        write_page(wiki_dir, "notes/old-note", "# Old\n\ncontent\n")
        resp = client.post(
            "/rename/notes/old-note",
            data={"new_name": "notes/new-note"},
            headers=HOST,
        )
        assert resp.status_code in (301, 302, 303)
        assert (wiki_dir / "notes" / "new-note.md").exists()
        assert not (wiki_dir / "notes" / "old-note.md").exists()

    def test_rename_preserves_content(self, client, wiki_dir):
        write_page(wiki_dir, "preserve", "# Title\n\nUnique content 12345.\n")
        client.post(
            "/rename/preserve",
            data={"new_name": "subdir/preserve"},
            headers=HOST,
        )
        new_file = wiki_dir / "subdir" / "preserve.md"
        assert new_file.exists()
        assert "Unique content 12345." in new_file.read_text(encoding="utf-8")


# ===========================================================================
# Bug: backlinks not detected when link is a bare slug (no .md extension)
# e.g. [Ivo](ivo-petrenko) — not a wikilink, not a .md link
# ===========================================================================

