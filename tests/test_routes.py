"""Tests for Zonewiki HTTP routes — page view/edit/save, search, upload, auth,
rename with reference updates, conflict detection, and API endpoints."""

import gzip
import html
import http.client
import io
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import settings
from tests.conftest import (HOST, backlinks, content_hash, create_task, list_page, make_list, preview, section_fields,
                            task_backlinks, write_page)
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
        # full-screen tasks editor kept its height and its Save/Cancel bar sat
        # behind the keyboard.
        write_page(wiki_dir, "my-page", "# My Page\n")
        text = client.get("/page/my-page", headers=HOST).data.decode()
        assert ('<meta name="viewport" content="width=device-width, initial-scale=1, '
                'interactive-widget=resizes-content">') in text

    def test_views_name_their_module(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        quicknote = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
        for url, module in (("/page/my-page", "wiki"), ("/page/index", "wiki"),
                            ("/tasks", "tasks"), ("/tasks?list=shop", "tasks"),
                            ("/notes", "notes"), (f"/page/{quicknote}", "notes"),
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
        for url in ("/page/my-page", "/tasks", "/notes"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            crumb = body.split('<div class="breadcrumb"', 1)[1].split("</nav>", 1)[0]
            assert crumb.index('href="/"') < crumb.index('href="/tasks"') < crumb.index('href="/notes"'), url
            assert '<span class="module-label">Wiki</span>' in crumb

    @staticmethod
    def _tasks_links(body):
        return re.findall(r'<a href="([^"]*)" class="(?:action-module )?module-link" data-module="tasks"', body)

    def test_tasks_links_go_to_tasks_without_a_home(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n\n[[tasks: list=task]]\n")
        body = client.get("/page/my-page", headers=HOST).get_data(as_text=True)
        assert self._tasks_links(body) == ["/tasks", "/tasks"]   # switcher, bar
        assert '<body data-module="wiki">' in body

    def test_my_tasks_is_where_the_tasks_links_go(self, client, wiki_dir):
        # With a list in the wiki the Tasks module links go to My tasks. New
        # task from anywhere goes into the Inbox, whatever is pinned: the
        # page says nothing of a list for it, and the views whose New it
        # clicks are My tasks and the Inbox. A page that shows a list is a
        # page like any other.
        list_page(client, wiki_dir, "tasks", "# Tasks\n\n[[tasks: list=task view=today pin=1]]\n")
        write_page(wiki_dir, "other", "# Other\n")
        body = client.get("/page/other", headers=HOST).get_data(as_text=True)
        assert self._tasks_links(body) == ["/tasks?mine=1", "/tasks?mine=1"]
        assert "data-pinned-pages" not in body
        lv = client.get("/tasks?list=task", headers=HOST).get_data(as_text=True)
        assert 'data-init-view="today"' in lv and '<body data-module="tasks">' in lv
        assert 'data-home="1"' not in lv          # the first pinned list takes no new task
        home = client.get("/page/tasks", headers=HOST).get_data(as_text=True)
        assert '<body data-module="wiki">' in home and 'data-home="1"' not in home
        for url in ("/tasks?mine=1", "/tasks?list=inbox"):
            view = client.get(url, headers=HOST).get_data(as_text=True)
            assert '<body data-module="tasks">' in view and 'data-home="1"' in view, url
        # /tasks stays the view of every task, in the Tasks module.
        assert '<body data-module="tasks">' in client.get("/tasks", headers=HOST).get_data(as_text=True)

    def test_the_tasks_links_go_to_my_tasks_however_many_are_pinned(self, client, wiki_dir):
        list_page(client, wiki_dir, "zzz", "# Z\n\n[[tasks: list=zed pin=1]]\n")
        list_page(client, wiki_dir, "mmm", "# M\n\n[[tasks: list=em pin=1]]\n")
        write_page(wiki_dir, "aaa", "# A\n\n[[tasks: tag=x pin=1]]\n")   # a pin in a page pins nothing
        links = lambda: self._tasks_links(client.get("/page/aaa", headers=HOST).get_data(as_text=True))[0]
        assert links() == "/tasks?mine=1"
        for name in ("em", "zed"):
            assert client.post("/tasks/pin-list", data={"list": name, "pin": "0"}, headers=HOST).status_code == 302
            assert links() == "/tasks?mine=1"   # one pinned, none pinned: My tasks still
        # ?pinned=1, its address until 2026-11, is no view: every task, as any unknown word.
        old = client.get("/tasks?pinned=1", headers=HOST).get_data(as_text=True)
        assert "<h1>My tasks</h1>" not in old and "data-init-mine" not in old

    def test_the_tasks_links_go_back_to_the_view_shown_last(self, client, wiki_dir):
        # Tasks → a list → the wiki → Tasks: back on that list, not on My
        # tasks. The view /tasks showed last is remembered in a cookie of the
        # browser's session, its own links name it at once.
        list_page(client, wiki_dir, "work", "# Work\n\n[[tasks: list=work]]\n")
        list_page(client, wiki_dir, "home", "# Home\n\n[[tasks: list=home]]\n")
        links = lambda url="/page/work": self._tasks_links(client.get(url, headers=HOST).get_data(as_text=True))
        assert links() == ["/tasks?mine=1"] * 2
        for url, href in (("/tasks?list=home", "/tasks?list=home"), ("/tasks?list=inbox", "/tasks?list=inbox"),
                          ("/tasks", "/tasks"), ("/tasks?list=work&view=closed", "/tasks?list=work"),
                          ("/tasks?mine=1", "/tasks?mine=1"), ("/tasks?list=home", "/tasks?list=home")):
            assert links(url) == [href] * 2, url   # the view's own switcher and tab bar
            assert links() == [href] * 2, url      # any page after it
        # A list that isn't there is no view to come back to: the last one stays.
        links("/tasks?list=nosuch")
        assert links() == ["/tasks?list=home"] * 2
        # One left out in sharing mode, or deleted: My tasks again.
        assert client.post("/api/tasks/settings", json={"list": "home", "set": {"noshare": "1"}},
                           headers=HOST).status_code == 200
        client.set_cookie("zone-sharing", "1")
        assert links() == ["/tasks?mine=1"] * 2
        client.delete_cookie("zone-sharing")
        assert links() == ["/tasks?list=home"] * 2
        assert client.post("/tasks/delete-list", data={"list": "home", "to": ""}, headers=HOST).status_code == 302
        assert links() == ["/tasks?mine=1"] * 2
        # Whatever the cookie says, only a view the user sees is linked.
        client.set_cookie("zone-tasks-place", "list%3Asomeone-elses")
        assert links() == ["/tasks?mine=1"] * 2

    def test_bottom_bar_lists_the_modules(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        bar = client.get("/page/my-page", headers=HOST).get_data(as_text=True).split(
            '<nav class="action-buttons"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'class="action-label"[^>]*>(\w+)<', bar) == ["Wiki", "Tasks", "Notes", "Note", "Task", "Search"]
        # Navigation, a divider, then the actions.
        assert bar.index('data-module="tasks"') < bar.index('class="action-sep"') < bar.index('class="action-new-note"')
        # The Wiki tab's icon is the logo's drawing, the open frame with its small square.
        wiki = bar.split('data-module="wiki"', 1)[1].split("</a>", 1)[0]
        assert '<path d="M12 7.25v6.25H2.5V4h6.25"' in wiki and '<rect x="10.5" y="2.5" width="3" height="3"' in wiki

    def test_phone_action_buttons_on_views_not_in_editor(self, client, wiki_dir):
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/tasks"):
            body = client.get(url, headers=HOST).data.decode()
            assert all(f'class="action-{a}"' in body for a in ("new-note", "task", "search"))
        # The editor has its own toolbar and the keyboard owns the bottom edge.
        assert 'class="action-buttons"' not in client.get("/edit/my-page", headers=HOST).data.decode()

    def test_desktop_top_actions_on_views_not_in_editor(self, client, wiki_dir):
        # Search, at the start of <main> so the CSS can put it at the top
        # right, in line with the breadcrumb; while sharing mode is on, the
        # Sharing chip before it, which turns it off (off, it takes no room).
        write_page(wiki_dir, "my-page", "# My Page\n")

        def top_buttons(url):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            top = body.split("<main", 1)[1].split('<div class="top-actions">', 1)
            # Before the page itself; only the status badges, which share
            # the row, come first.
            assert len(top) == 2 and '<div class="breadcrumb"' not in top[0] and '<header' not in top[0], url
            assert top[0].count('class="top-badges"') == 1, url
            return re.findall(r'<button type="button" class="top-([\w-]+)"(?: aria-pressed="true")? aria-label="([^"]+)" title="([^"]+)"',
                              top[1].split("</div>", 1)[0])

        for url in ("/page/my-page", "/tasks", "/notes"):
            assert top_buttons(url) == [("search", "Search", "Search (Ctrl+P)")], url
        client.set_cookie("zone-sharing", "1")
        for url in ("/page/my-page", "/tasks", "/notes"):
            assert top_buttons(url) == [
                ("sharing", "Sharing mode is on: turn it off", "Sharing mode is on: click to show everything again (S)"),
                ("search", "Search", "Search (Ctrl+P)")], url
        assert 'class="top-actions"' not in client.get("/edit/my-page", headers=HOST).data.decode()

    def test_sharing_mode_is_rendered_from_the_cookie(self, client, wiki_dir):
        # The server renders html.sharing from the cookie: from the first
        # paint, nothing sharing mode hides flashes on a shared screen.
        write_page(wiki_dir, "my-page", "# My Page\n")
        for url in ("/page/my-page", "/edit/my-page", "/tasks"):
            assert '<html lang="en">' in client.get(url, headers=HOST).get_data(as_text=True), url
        client.set_cookie("zone-sharing", "1")
        for url in ("/page/my-page", "/edit/my-page", "/tasks"):
            assert '<html lang="en" class="sharing">' in client.get(url, headers=HOST).get_data(as_text=True), url

    def test_no_page_says_where_a_new_task_goes(self, client, wiki_dir):
        # c t and the New task button put a task in the Inbox, in no list:
        # <body> names no list, tags or keep for it, whatever is pinned first
        # and whatever that list gives its new tasks.
        write_page(wiki_dir, "my-page", "# My Page\n")
        list_page(client, wiki_dir, "tasks", "# Tasks\n\n[[tasks: list=Task new=work,home pin=1 keep=1]]\n")
        for url in ("/page/my-page", "/edit/my-page", "/tasks", "/tasks?mine=1", "/tasks?list=task"):
            tag = client.get(url, headers=HOST).get_data(as_text=True).split("<body", 1)[1].split(">", 1)[0]
            assert "data-tasks-" not in tag, url
        # The list's own view gives its new tasks what it says; My tasks doesn't take them over.
        own = client.get("/tasks?list=task", headers=HOST).get_data(as_text=True)
        assert 'data-init-keep="1"' in own and 'data-new-tags="work,home"' in own
        mine = client.get("/tasks?mine=1", headers=HOST).get_data(as_text=True)
        assert 'data-init-keep=""' in mine and "data-new-tags" not in mine

    def test_headless_tasks_for_pages_without_one(self, client, wiki_dir):
        # c t on a page without a task list app loads this hidden one (scripts
        # left out: the page loads them).
        body = client.get("/api/tasks/headless", headers=HOST).get_data(as_text=True)
        assert 'data-headless="1"' in body and 'id="task-edit-modal"' in body
        assert "<script" not in body

    def test_pages_load_nothing_from_other_hosts(self, client, wiki_dir):
        # No CDN: highlight.js, KaTeX and Mermaid are vendored and served from /public/.
        write_page(wiki_dir, "my-page", "# My Page\n\n```py\nx = 1\n```\n")
        for url in ("/page/my-page", "/edit/my-page", "/tasks", "/search?q=page"):
            head = client.get(url, headers=HOST).data.decode()
            assert not re.search(r'<(?:script|link)\b[^>]*(?:src|href)="(?:https?:)?//', head), url
        for f in ("highlight/highlight.min.js", "highlight/github.min.css", "highlight/github-dark.min.css",
                  "katex/katex.min.js", "katex/katex.min.css", "katex/fonts/KaTeX_Main-Regular.woff2",
                  "mermaid/mermaid.min.js"):
            r = client.get(f"/public/vendor/{f}", headers=HOST)
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
        assert "<title>About Us — Zonewiki</title>" in resp.data.decode()

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

    def test_a_name_windows_reads_as_another_is_no_page_and_says_why(self, client, wiki_dir):
        # A device's name or a ':' (a wiki's files are copied and cloned to
        # other systems): no page opens, is made or is listed, and the answer
        # says why, where "Bad Request" would leave a person guessing.
        write_page(wiki_dir, "notes/10:30", "# Time\n")
        write_page(wiki_dir, "nul", "# Nul\n")
        for path, word in (("/page/aux", "device"), ("/page/notes/NUL", "device"), ("/edit/com1", "device"),
                           ("/page/notes/10:30", "':'"), ("/edit/a:b", "':'")):
            resp = client.get(path, headers=HOST)
            assert resp.status_code == 400 and word in resp.data.decode() and "Pick another name" in resp.data.decode(), path
        resp = client.post("/edit/aux", data={"content": "# Aux\n"}, headers=HOST)
        assert resp.status_code == 400 and "device" in resp.data.decode()
        assert not (wiki_dir / "aux.md").exists()
        listed = client.get("/api/pages", headers=HOST).data.decode()
        assert "10:30" not in listed and '"nul"' not in listed
        # A name that only looks like one is a page as ever.
        assert client.get("/page/console", headers=HOST).status_code == 302
        assert client.get("/page/../etc/passwd", headers=HOST).data.decode().strip() == "Bad Request"

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
        # row (same line as "Changed …"), and appear there exactly once —
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
        client.post("/api/tasks", json={"title": "Weed the [[garden]]"})
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

    def test_side_toc_is_made_from_the_parsed_headings(self, client, wiki_dir):
        # Regression: the column cut each heading out of the rendered article
        # and took its links out with a pattern, which joined a heading's raw
        # HTML anew past defuseHTML: "<<a>form" a <form> posting to Zonewiki, a
        # <meta> refresh to another site, an iframe's srcdoc, a <link>. Its
        # entries are the parsed headings now: escaped text, code, math,
        # emphasis; a heading of raw HTML isn't one.
        tid = create_task(client, title="Order <b>seeds</b>").get_json()["id"]
        write_page(wiki_dir, "garden", (
            "# Garden\n\n"
            '<h2 id="raw"><<a>form method="post" action="/delete/index"><<a>button>x</h2>\n'
            '<h3 id="meta"><<a>meta http-equiv="refresh" content="0;url=https://evil.example/"></h3>\n'
            '<h3 id="frame"><iframe src<a>doc="&lt;form&gt;"></iframe><<a>link rel="dns-prefetch" href="//evil.example"></h3>\n\n'
            "## Beds <b>bold</b> <<a>form> and `<form>`\n\n"
            "## *Soil* ~~sand~~ $x^2$ [link](/page/x) ![img](/i.png) <https://a.example>\n\n"
            f"### Plan [[task:{tid}]]\n"))
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        left = body.split('<aside class="rail rail-left">', 1)[1].split("</aside>", 1)[0]
        for live in ("<form", "<meta", "<link", "<iframe", "<button", "srcdoc", "<b>"):
            assert live not in left, live
        assert re.findall(r'href="(#[^"]+)"', left) == ["#beds-bold-aform-and-form", "#soil-sand-x2-link", "#plan-0"]
        assert ('<li><a href="#beds-bold-aform-and-form">Beds bold &lt;&lt;a&gt;form&gt; and '
                '<code>&lt;form&gt;</code></a></li>') in left
        assert ('<li><a href="#soil-sand-x2-link"><em>Soil</em> <del>sand</del> '
                '<span class="math math-inline">x^2</span> link  https://a.example</a></li>') in left
        assert '<li class="toc-h3"><a href="#plan-0">Plan Order &lt;b&gt;seeds&lt;/b&gt;</a></li>' in left

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

    def test_files_lists_the_uploads_the_page_links_or_shows(self, client, wiki_dir):
        # Files: the uploads the page's text links to or shows, in its
        # order, each once, with their sizes; in the column and at the page's
        # end. Not a file that isn't there, one on another site, one in
        # code, one in a folder under _assets/, nor a link to a page.
        (wiki_dir / "_assets/My Deck (v2).pdf").write_bytes(b"%PDF" + b"x" * 2044)
        (wiki_dir / "_assets/photo.png").write_bytes(b"\x89PNG")
        (wiki_dir / "_assets/notes.txt").write_bytes(b"hi")
        (wiki_dir / "_assets/sub").mkdir()
        (wiki_dir / "_assets/sub/deep.png").write_bytes(b"x")
        write_page(wiki_dir, "garden", (
            "# Garden\n\n![Beds](/_assets/photo.png)\n\n"
            "The [deck](/_assets/My%20Deck%20(v2).pdf), [again](/_assets/My Deck (v2).pdf) and ![](/_assets/photo.png).\n\n"
            "[Notes](http://localhost:5000/_assets/notes.txt), [elsewhere](https://other.example/_assets/photo.png), "
            "[gone](/_assets/gone.pdf), [deep](/_assets/sub/deep.png), [[index]].\n\n"
            "```\n[code](/_assets/notes.txt)\n```\n"))
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        files = ('<h3>Files</h3><ul><li><a href="/_assets/photo.png">[image]photo.png</a> <span class="file-size">4 bytes</span></li>'
                 '<li><a href="/_assets/My%20Deck%20%28v2%29.pdf">[doc]My Deck (v2).pdf</a> <span class="file-size">2 KB</span></li>'
                 '<li><a href="/_assets/notes.txt">[doc]notes.txt</a> <span class="file-size">2 bytes</span></li></ul>')
        # Each name has its kind's icon before it (drawn, here its kind).
        icons = lambda html: re.sub(r'<svg class="file-icon" data-kind="(\w+)"[^>]*>.*?</svg>', r"[\1]", html)
        right = body.split('<aside class="rail rail-context" aria-label="Links">', 1)[1].split("</aside>", 1)[0]
        assert '<section class="backlinks page-files" data-id="files">' + files in icons(right)
        foot = body.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        assert '<section class="backlinks page-files" id="files">' + files in icons(foot)
        # The kinds by extension, whatever its case; any other is a file.
        kinds = {"a.XLSX": "sheet", "b.csv": "sheet", "c.mp4": "media", "d.mp3": "media", "e.zip": "archive",
                 "f.docx": "doc", "g.SVG": "image", "h.bin": "file", "noext": "file"}
        for name in kinds:
            (wiki_dir / "_assets" / name).write_bytes(b"x")
        write_page(wiki_dir, "kinds", "# Kinds\n\n" + " ".join(f"[{n}](/_assets/{n})" for n in kinds) + "\n")
        body = client.get("/page/kinds", headers=HOST).get_data(as_text=True)
        foot = body.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'data-kind="(\w+)"[^>]*>.*?</svg>([^<]+)</a>', foot) == [(k, n) for n, k in kinds.items()]
        # A page without uploads has no Files.
        write_page(wiki_dir, "paths", "# Paths\n\n[A page](garden), [gone](/_assets/gone.pdf).\n")
        assert "page-files" not in client.get("/page/paths", headers=HOST).get_data(as_text=True)

    def test_side_column_without_toc_is_empty(self, client, wiki_dir):
        # Under three headings: no left column content (a [[toc]] line no
        # longer asks for one); the column stays, so the page keeps its place.
        write_page(wiki_dir, "garden", "# Garden\n\n[[toc]]\n\n## Beds\n\n## Paths\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert '<aside class="rail rail-left"><a class="rail-top" href="#" hidden>↑ Back to top</a></aside>' in body
        right = body.split('<aside class="rail rail-context" aria-label="Links">', 1)[1].split("</aside>", 1)[0]
        assert "<section" not in right

    def test_page_actions_menu(self, client, wiki_dir):
        # The header has Edit and a ⋯ menu (Present, History with git,
        # Print…, Rename…, Delete…), and only the header: the right column has no copy.
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n")
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        header = body.split('<div class="page-header-actions">', 1)[1].split("</div>", 1)[0]
        assert '<a href="/edit/garden" class="btn">Edit</a>' in header
        assert body.count('class="btn page-menu-btn"') == 1
        menu = body.split('<div class="page-menu" id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'role="menuitem"[^>]*>([^<]+)', menu) == ["Present"] + ["History"] * settings.GIT_ENABLED + ["Print…", "Rename…", "Delete…"]
        assert 'id="page-rename-dialog"' in body and 'id="page-delete-dialog"' in body
        # The home page can't be renamed or deleted: Present and Print… are left.
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        menu = body.split('id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'role="menuitem"[^>]*>([^<]+)', menu) == ["Present"] + ["History"] * settings.GIT_ENABLED + ["Print…"]
        assert 'id="page-rename-dialog"' not in body

    def test_only_page_views_and_the_tasks_are_wide(self, client, wiki_dir):
        # The Tasks page has its lists in the left column (docs/spec/tasks.md).
        write_page(wiki_dir, "garden", "# Garden\n")
        assert 'class="wide"' in client.get("/tasks", headers=HOST).get_data(as_text=True)
        for url in ("/notes", "/search?q=garden", "/edit/garden"):
            assert 'class="wide"' not in client.get(url, headers=HOST).get_data(as_text=True), url


class TestPagePreview:
    """/api/page-preview: a page for the search modal's preview pane."""

    def test_rendered_without_ids_and_taskss(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n\nSoil.\n\n[[tasks: list=garden]]\n\n```\n[[tasks:]]\n```\n")
        data = client.get("/api/page-preview/garden", headers=HOST).get_json()
        assert data["title"] == "Garden"
        assert '<h2 data-pid="beds">Beds</h2>' in data["html"] and ' id="' not in data["html"]
        assert "tasks-app" not in data["html"] and "<em>(tasks)</em>" in data["html"]
        assert "[[tasks:]]" in data["html"]    # code stays as written
        assert "<h1" not in data["html"]

    def test_sources_heading_has_no_id(self, client, wiki_dir):
        # The preview sits over a page with its own ids, #sources among them.
        write_page(wiki_dir, "cited", "---\nsources: [[index]]\n---\n# Cited\n\n## Sources\n\nText.\n")
        html = client.get("/api/page-preview/cited", headers=HOST).get_json()["html"]
        assert ' id="' not in html
        assert '<h2 data-pid="sources">Sources</h2>' in html and '<h2 data-pid="sources-1">Sources</h2>' in html
        page = client.get("/page/cited", headers=HOST).get_data(as_text=True)
        assert 'id="sources-1">Sources</h2>' in page

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

    def test_new_quicknote_puts_cursor_after_title(self, client):
        slug = client.post("/api/notes", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
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


class TestMergeOnSave:
    """Two editors on one page: a save onto a file that changed since its
    editor loaded it is merged when the lines don't clash (the editor says
    it takes a merged answer, merge=1); else 409, as before."""

    PAGE = "# Plans\n\n## One\n\nFirst.\n\n## Two\n\nSecond.\n"

    @pytest.fixture()
    def page(self, client, git_wiki_dir):
        write_page(git_wiki_dir, "plans", self.PAGE)
        client.get("/edit/plans", headers=HOST)   # both editors load it: its text is a base
        return git_wiki_dir / "plans.md"

    def save(self, client, content, file_hash=None, **fields):
        return client.post("/api/save/plans", data={"content": content, "merge": "1",
                                                    "file_hash": file_hash or content_hash(self.PAGE), **fields},
                           headers=HOST)

    def test_changes_in_different_lines_both_land(self, client, page):
        first = self.PAGE.replace("First.", "First, A.")
        assert self.save(client, first).status_code == 200
        resp = self.save(client, self.PAGE.replace("Second.", "Second, B."))
        both = self.PAGE.replace("First.", "First, A.").replace("Second.", "Second, B.")
        assert resp.status_code == 200 and page.read_text() == both
        mine = self.PAGE.replace("Second.", "Second, B.")
        assert resp.get_json() == {"file_hash": content_hash(both), "merged": True, "by": "", "content": both,
                                   "sent_hash": content_hash(mine)}
        # The merged text is a base too: the next save from it is no conflict.
        assert self.save(client, both + "\nMore.\n", content_hash(both)).status_code == 200

    def test_the_same_lines_are_a_conflict(self, client, page):
        self.save(client, self.PAGE.replace("First.", "First, A."))
        resp = self.save(client, self.PAGE.replace("First.", "First, B."))
        assert resp.status_code == 409 and resp.get_json()["error"] == "file_changed"
        assert "First, A." in page.read_text()

    @pytest.mark.parametrize("why", ["no merge field", "unknown base", "no git"])
    def test_no_merge_is_the_conflict_as_before(self, client, page, why, wiki_dir, monkeypatch):
        if why == "no git":
            monkeypatch.setattr(settings, "GIT_ENABLED", False)
            client.get("/edit/plans", headers=HOST)
        self.save(client, self.PAGE.replace("First.", "First, A."))
        mine = self.PAGE.replace("Second.", "Second, B.")
        fields = {"merge": ""} if why == "no merge field" else {}
        hash_ = content_hash(mine + "x") if why == "unknown base" else None
        assert self.save(client, mine, hash_, **fields).status_code == 409

    def test_a_section_merges_and_gets_its_new_range(self, client, page):
        fields = section_fields(client, "plans", 2)                  # "## Two", from the page as it was
        self.save(client, self.PAGE.replace("# Plans\n", "# Plans\n\nIntro.\n"))
        resp = client.post("/api/save/plans", data={**fields, "content": "## Two\n\nSecond, B.\n", "merge": "1"},
                           headers=HOST)
        text = page.read_text()
        assert resp.status_code == 200 and text == "# Plans\n\nIntro.\n\n## One\n\nFirst.\n\n## Two\n\nSecond, B.\n"
        data = resp.get_json()
        assert data["merged"] and data["content"] == "## Two\n\nSecond, B."
        assert text[data["start_char"]:data["end_char"]].startswith("## Two") and data["file_hash"] == content_hash(text)
        # The next save from it splices at the new range.
        client.post("/api/save/plans", data={"file_hash": data["file_hash"], "start_char": data["start_char"],
                                             "end_char": data["end_char"], "content": "## Two\n\nAgain.\n"},
                    headers=HOST)
        assert page.read_text().endswith("## Two\n\nAgain.\n") and "Intro." in page.read_text()

    def test_a_section_whose_heading_changed_is_a_conflict(self, client, page):
        fields = section_fields(client, "plans", 2)
        self.save(client, self.PAGE.replace("## Two", "## Zwei"))
        resp = client.post("/api/save/plans", data={**fields, "content": "## Two\n\nSecond, B.\n", "merge": "1"},
                           headers=HOST)
        assert resp.status_code == 409 and "Second, B." not in page.read_text()

    def test_a_conflict_gives_the_lines_with_markers(self, client, page):
        self.save(client, self.PAGE.replace("First.", "First, A."))
        data = self.save(client, self.PAGE.replace("First.", "First, B.")).get_json()
        assert "<<<<<<< yours\nFirst, B.\n=======\nFirst, A.\n>>>>>>> theirs\n" in data["conflict"]
        assert data["file_hash"] == content_hash(page.read_text())
        # Merge by hand saves from that hash: no conflict.
        resolved = self.PAGE.replace("First.", "First, A and B.")
        assert self.save(client, resolved, data["file_hash"]).status_code == 200
        assert page.read_text() == resolved

    def test_a_section_conflict_has_no_markers(self, client, page):
        fields = section_fields(client, "plans", 1)
        self.save(client, self.PAGE.replace("First.", "First, A."))
        resp = client.post("/api/save/plans", data={**fields, "content": "## One\n\nFirst, B.\n", "merge": "1"},
                           headers=HOST)
        assert resp.status_code == 409 and "conflict" not in resp.get_json()

    def test_typed_on_meanwhile_saves_from_what_was_sent(self, client, page):
        self.save(client, self.PAGE.replace("Second.", "Second, B."))
        sent = self.PAGE.replace("First.", "First, A.")
        data = self.save(client, sent).get_json()
        assert data["merged"] and data["sent_hash"] == content_hash(sent)
        # The editor kept typing: its next save goes from the text it sent, and merges again.
        more = sent.replace("First, A.", "First, A, more.")
        data = self.save(client, more, data["sent_hash"]).get_json()
        assert data["merged"] and page.read_text() == more.replace("Second.", "Second, B.")

    def test_reload_theirs_sets_mine_aside_in_git(self, client, page):
        theirs = self.PAGE.replace("First.", "First, A.")
        self.save(client, theirs)
        resp = client.post("/api/save/plans", data={"content": self.PAGE.replace("First.", "First, B."),
                                                    "set_aside": "1"}, headers=HOST)
        assert resp.get_json() == {"set_aside": True} and page.read_text() == theirs
        log = subprocess.run(["git", "log", "--format=%s", "-3"], cwd=page.parent, capture_output=True,
                             text=True).stdout.splitlines()
        assert log == ["Reload plans", "Set aside an edit of plans", "Save plans"]
        shown = subprocess.run(["git", "show", "HEAD~1:plans.md"], cwd=page.parent, capture_output=True, text=True)
        assert "First, B." in shown.stdout

    def test_keep_mine_commits_what_was_never_in_git_first(self, client, page):
        theirs = self.PAGE.replace("First.", "First, edited on disk.")
        page.write_text(theirs)
        assert client.post("/api/save/plans", data={"content": self.PAGE.replace("First.", "First, mine."),
                                                    "overwrite": "1"}, headers=HOST).status_code == 200
        shown = subprocess.run(["git", "show", "HEAD:plans.md"], cwd=page.parent, capture_output=True, text=True)
        assert shown.stdout == theirs and "First, mine." in page.read_text()

    def test_leaving_in_conflict_keeps_the_text_for_the_next_visit(self, client, page, git_wiki_dir):
        """The save on leaving the page (leaving=1) clashes: its text is kept
        for the user, not in the page or git, and offered when they come back."""
        self.save(client, self.PAGE.replace("First.", "First, A."))
        mine = self.PAGE.replace("First.", "First, B.")
        assert self.save(client, mine, leaving="1").status_code == 409
        kept = git_wiki_dir / ".zone/drafts/_/plans.json"
        assert json.loads(kept.read_text())["content"] == mine and "First, B." not in page.read_text()
        body = client.get("/edit/plans", headers=HOST).get_data(as_text=True)
        assert 'id="left-draft"' in body and "First, B." in body and "&lt;&lt;&lt;&lt;&lt;&lt;&lt; yours" in body
        # A section editor sends you to the whole page for it.
        resp = client.get("/edit/plans?section=1", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/edit/plans"
        # Saving the page is choosing: it goes.
        client.post("/api/save/plans", data={"content": mine}, headers=HOST)
        assert not kept.exists() and 'id="left-draft"' not in client.get("/edit/plans", headers=HOST).get_data(as_text=True)

    def test_a_left_section_is_kept_as_a_whole_page(self, client, page, git_wiki_dir):
        fields = section_fields(client, "plans", 2)
        self.save(client, self.PAGE.replace("Second.", "Second, A."))
        client.post("/api/save/plans", data={**fields, "content": "## Two\n\nSecond, B.\n", "merge": "1",
                                             "leaving": "1", "section_idx": "2"}, headers=HOST)
        kept = json.loads((git_wiki_dir / ".zone/drafts/_/plans.json").read_text())
        assert kept["content"] == self.PAGE.replace("Second.", "Second, B.") and kept["base"] == self.PAGE

    def test_reload_theirs_keeps_a_section_whose_heading_is_gone(self, client, page):
        theirs = self.PAGE.replace("## Two", "## Renamed")
        self.save(client, theirs)
        resp = client.post("/api/save/plans", data={"content": "## Two\n\nSecond, mine.", "set_aside": "1",
                                                    "force_section": "2", "section_heading": "## Two"}, headers=HOST)
        assert resp.get_json() == {"set_aside": True} and page.read_text() == theirs
        shown = subprocess.run(["git", "show", "HEAD~1:plans.md"], cwd=page.parent, capture_output=True, text=True)
        assert shown.stdout == theirs.rstrip("\n") + "\n\n## Two\n\nSecond, mine.\n"

    def test_left_drafts_go_after_30_days(self, client, page, git_wiki_dir):
        kept = git_wiki_dir / ".zone/drafts/_/plans.json"
        kept.parent.mkdir(parents=True)
        kept.write_text(json.dumps({"content": "Old.\n", "base": "", "at": "2026-01-01T10:00:00Z"}))
        assert 'id="left-draft"' not in client.get("/edit/plans", headers=HOST).get_data(as_text=True)
        assert not kept.exists()

    def test_a_new_pages_auto_saves_commit_nothing(self, client, git_wiki_dir):
        """Its first save has no hash to send; the answer gives one, and no
        save of it commits (only Keep mine's overwrite=1 commits first)."""
        log = lambda: subprocess.run(["git", "log", "--format=%s"], cwd=git_wiki_dir, capture_output=True,
                                     text=True).stdout
        before = log()
        data = client.post("/api/save/fresh", data={"content": "# Fresh\n\nOne.\n"}, headers=HOST).get_json()
        assert data == {"file_hash": content_hash("# Fresh\n\nOne.\n")}
        client.post("/api/save/fresh", data={"content": "# Fresh\n\nOne. Two.\n", "file_hash": data["file_hash"]},
                    headers=HOST)
        client.post("/api/save/fresh", data={"content": "# Fresh\n\nOne. Two. Three.\n"}, headers=HOST)
        assert log() == before and (git_wiki_dir / "fresh.md").read_text().endswith("Three.\n")

    def test_done_merges_and_commits(self, client, page):
        self.save(client, self.PAGE.replace("First.", "First, A."))
        resp = client.post("/edit/plans", data={"content": self.PAGE.replace("Second.", "Second, B."),
                                                "file_hash": content_hash(self.PAGE)}, headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/page/plans"
        assert "First, A." in page.read_text() and "Second, B." in page.read_text()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=page.parent, capture_output=True, text=True)
        assert status.stdout == ""
        resp = client.post("/edit/plans", data={"content": self.PAGE.replace("First.", "First, C."),
                                                "file_hash": content_hash(self.PAGE)}, headers=HOST)
        assert "conflict=1" in resp.headers["Location"]


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

    @pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example",
                                        "/\t/evil.example", "/\n/evil.example", "/ /evil.example", "/x\\..\\evil"])
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
        assert resp.headers["Location"].startswith("/page/page-a?") and "rename_error=" in resp.headers["Location"]
        body = client.get(resp.headers["Location"], headers=HOST).get_data(as_text=True)
        assert '<dialog id="page-rename-dialog" class="modal" data-open>' in body
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
        assert "/notes" in slugs and "/tasks" in slugs
        assert "_daily" not in slugs   # daily notes were removed

    def test_built_in_pages_are_marked(self, client, wiki_dir):
        # Tasks and Notes are modules (the module switcher links to them), so
        # only Changes and Deleted Pages are pinned (`special`, git only); all
        # four are `builtin`, which the empty-query list and "Unlinked only" leave out.
        write_page(wiki_dir, "aaa", "# AAA\n")
        pages = client.get("/api/pages-list", headers=HOST).get_json()
        assert not [p for p in pages if p.get("special") and p["title"] not in ("Changes", "Deleted Pages")]
        builtin = {p["title"]: p for p in pages if p.get("builtin")}
        assert {"Tasks", "Notes"} <= builtin.keys()
        assert builtin["Tasks"]["href"] == "/tasks"
        assert not next(p for p in pages if p["slug"] == "aaa").get("builtin")


# ===========================================================================
# search
# ===========================================================================

class TestIterPages:
    @pytest.mark.parametrize("slug", [".leaf/index/n", ".zone/index/n", ".git/n", "_tasks/n", "a/_drafts/n",
                                      "a/.hidden/n", "a/../.leaf/n", "a/../.zone/n"])
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
        client.post("/api/tasks", json={"title": "Fix date picker", "tags": ["leaf"]}, headers=HOST)
        client.post("/api/tasks", json={"title": "Date picker elsewhere"}, headers=HOST)
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
        client.post("/api/tasks", json={"title": "Zebra task"}, headers=HOST)
        all_ = {r["title"] for r in client.get("/api/search?q=zebra", headers=HOST).get_json()}
        assert all_ == {"Hub", "Linked", "Orphan", "[Task] Zebra task"}
        only = [r["slug"] for r in client.get("/api/search?q=zebra&unlinked=1", headers=HOST).get_json()]
        assert sorted(only) == ["hub", "orphan"]   # nothing links to hub either; no tasks

    def test_result_meta_tells_alike_titles_apart(self, client, wiki_dir):
        import os
        path = write_page(wiki_dir, "conversations/2026-07-08_alex/prep", "# Alex Debrief\n\nquokka\n")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        write_page(wiki_dir, "quokka", "# Quokka\n\nquokka\n")
        open_ = client.post("/api/tasks", json={"title": "quokka open"}, headers=HOST).get_json()
        make_list(client, "zoo", headers=HOST)
        done = client.post("/api/tasks", json={"title": "quokka done", "list": "zoo"}, headers=HOST).get_json()
        client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
        meta = {r["title"]: r["meta"] for r in client.get("/api/search?q=quokka", headers=HOST).get_json()}
        assert meta["Alex Debrief"] == "conversations/2026-07-08_alex · edited 2001-09-09"
        assert meta["Quokka"].startswith("edited 20")
        assert meta["[Task] quokka open"] == "Tasks · open"
        assert re.fullmatch(r"Tasks › zoo · done \d{4}-\d\d-\d\d", meta["[Task] quokka done"])
        assert open_["id"]
        page = client.get("/search?q=quokka", headers=HOST).get_data(as_text=True)
        assert '<span class="result-meta">conversations/2026-07-08_alex · edited 2001-09-09</span>' in page

    def test_tag_pill_lists_exactly_the_tagged(self, client, wiki_dir):
        # Regression: a pill searched the tag as a word, so it listed every
        # page mentioning "ops", tagged or not.
        write_page(wiki_dir, "tagged", "---\ntags: [ops]\n---\n# Runbook\n\nRestart steps.\n")
        write_page(wiki_dir, "mentions", "# Team chat\n\nAsk the ops team.\n")
        client.post("/api/tasks", json={"title": "Rotate keys", "tags": ["ops"]}, headers=HOST)
        page = client.get("/page/tagged", headers=HOST).get_data(as_text=True)
        href = re.search(r'class="task-tag page-tag" href="([^"]+)"', page).group(1)
        assert href == "/search?q=%23ops"
        results = client.get(href, headers=HOST).get_data(as_text=True)
        assert "Runbook" in results and "Rotate keys" in results and "Team chat" not in results

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
        task = client.post("/api/tasks", json={"title": "yak shaving"}, headers=HOST).get_json()
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

    def test_a_query_is_a_few_words(self, client, wiki_dir):
        """A query is cut at 500 characters (each word that matches nothing
        is looked for among all the index's words as a typo, so a query as
        long as a URL may be kept a processor busy for hours)."""
        write_page(wiki_dir, "zoo", "# Zoo\n\nThe zebracorn lives here.\n")
        assert client.get("/api/search?q=zebracorn", headers=HOST).get_json()
        long = "word " * 120 + "zebracorn"
        assert client.get("/api/search", query_string={"q": long}, headers=HOST).get_json() == []
        assert 'href="/page/zoo' not in client.get("/search", query_string={"q": long}, headers=HOST).get_data(as_text=True)

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
        client.post("/api/tasks", json={"title": "Read", "description": "[doc](https://zzqhost.example)"})
        results = client.get("/api/search?q=zzqhost", headers=HOST).get_json()
        assert results == []

    def test_task_found_by_tag(self, client, wiki_dir):
        # Regression: tasks were indexed by title and notes only, so a page's
        # tag pill (/search?q=<tag>) never listed the tasks with that tag.
        client.post("/api/tasks", json={"title": "Call Bob", "tags": ["zzqtag"]})
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
        client.post("/api/tasks", json={"title": "zzqtask"})
        assert client.get("/api/search?q=zzqtask", headers=HOST).get_json()
        write_page(wiki_dir, "later", "# Later\n\nzzqpage\n")
        assert client.get("/api/search?q=zzqpage", headers=HOST).get_json()

    def test_search_includes_task_results(self, client, wiki_dir):
        client.post("/api/tasks", json={"title": "unique_task_token_abc"})
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
        # A task result must open the tasks (/tasks) at that task (by id) — not the
        # bare _tasks storage slug, and not a title search.
        tid = client.post("/api/tasks", json={"title": "buymilk urgent chore"}).get_json()["id"]
        resp = client.get("/search?q=buymilk+urgent+chore", headers=HOST)
        body = resp.get_data(as_text=True)
        assert "/page/_tasks" not in body
        assert f'href="/tasks?task={tid}&amp;view=open"' in body

    def test_search_page_completed_task_links_to_completed_tab(self, client, wiki_dir):
        r = client.post("/api/tasks", json={"title": "finishreport tuvwxyz"})
        tid = r.get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        resp = client.get("/search?q=finishreport+tuvwxyz", headers=HOST)
        body = resp.get_data(as_text=True)
        assert f'href="/tasks?task={tid}&amp;view=closed"' in body


# ===========================================================================
# api_toggle_checkbox
# ===========================================================================

class TestPreviewMarks:
    """The previews say where the boxes they show are in the text (as the
    browser counts its characters), so an editor ticks the box a reader
    clicked: a "- [ ]" the render shows no box for counts for nothing."""

    TEXT = "---\ntags: [a]\n---\n# Ä 🎉\n\n<!--\n- [ ] hidden\n-->\n\n- [ ] one\n- [x] two\n"

    @staticmethod
    def units(text, char_index):
        return len(text[:char_index].encode("utf-16-le")) // 2

    def test_the_editors_preview(self, client):
        resp = client.post("/api/preview", data=self.TEXT.encode("utf-8"), headers=HOST)
        assert resp.get_data(as_text=True).count('class="task-list-item-checkbox"') == 2
        marks = [int(n) for n in resp.headers["Zone-Task-Marks"].split(",")]
        want = [self.units(self.TEXT, self.TEXT.index("- [ ] one") + 3), self.units(self.TEXT, self.TEXT.index("- [x] two") + 3)]
        assert marks == want
        assert client.post("/api/preview", data=b"no boxes", headers=HOST).headers["Zone-Task-Marks"] == ""

    def test_a_tasks_notes(self, client):
        notes = "<!--\n- [ ] hidden\n-->\n\n- [ ] ü\n"
        got = client.post("/api/render-notes", json={"notes": [notes, "x"], "comments": ["- [ ] c"]}, headers=HOST).get_json()
        assert got["marks"] == [[notes.index("- [ ] ü") + 3], []]


class TestApiToggleCheckbox:

    def test_toggles_unchecked_to_checked(self, client, wiki_dir):
        path = write_page(wiki_dir, "checkbox-page", "# CB\n\n- [ ] task one\n- [ ] task two\n")
        resp = client.post("/api/toggle-checkbox/checkbox-page", data={"idx": "0"}, headers=HOST)
        text = path.read_text(encoding="utf-8")
        assert resp.status_code == 200 and resp.get_json() == {"file_hash": content_hash(text)}
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

    @staticmethod
    def view(client, slug):
        """The page view's checkbox keys, as its article has them."""
        body = client.get(f"/page/{slug}", headers=HOST).get_data(as_text=True)
        return re.search(r'<article class="page-content" data-task-keys="([^"]*)"', body)[1].split(",")

    def test_ticks_after_one_found_elsewhere_go_by_their_own_lines(self, client, wiki_dir):
        """The view had [A, B]; Z came in above. Ticking A finds it below Z;
        ticking B then, and undoing A, go to B and A, not by their places."""
        path = write_page(wiki_dir, "moved", "# CB\n\n- [ ] A\n- [ ] B\n")
        keys = self.view(client, "moved")
        path.write_text("# CB\n\n- [ ] Z\n- [ ] A\n- [ ] B\n")
        tick = lambda idx: client.post("/api/toggle-checkbox/moved", data={"idx": str(idx), "key": keys[idx]},
                                       headers=HOST).status_code
        assert tick(0) == 200 and tick(1) == 200
        text = path.read_text()
        assert "- [ ] Z" in text and re.search(r"- \[x\] A", text) and re.search(r"- \[x\] B", text)
        assert tick(0) == 200                        # undo A
        text = path.read_text()
        assert "- [ ] Z" in text and "- [ ] A\n" in text and re.search(r"- \[x\] B", text)

    def test_a_tick_from_an_older_view_finds_its_item(self, client, wiki_dir):
        """Someone added an item above since the view loaded: the tick goes to
        the item the view showed, by its line, not by its place."""
        path = write_page(wiki_dir, "moved", "# CB\n\n- [ ] Call Sam\n- [ ] Buy milk\n")
        keys = self.view(client, "moved")
        path.write_text("# CB\n\n- [ ] New one\n- [ ] Call Sam\n- [ ] Buy milk\n")
        resp = client.post("/api/toggle-checkbox/moved", data={"idx": "1", "key": keys[1]},
                           headers=HOST)
        assert resp.status_code == 200
        text = path.read_text()
        assert "- [ ] New one" in text and "- [ ] Call Sam" in text and re.search(r"- \[x\] Buy milk", text)
        # Undo from the same view: the same item, by its line again.
        client.post("/api/toggle-checkbox/moved", data={"idx": "1", "key": keys[1]},
                    headers=HOST)
        assert "- [ ] Buy milk" in path.read_text() and "<!--d:" not in path.read_text()

    @pytest.mark.parametrize("change", ["gone", "twice"])
    def test_a_tick_whose_line_isnt_there_once_is_refused(self, client, wiki_dir, change):
        path = write_page(wiki_dir, "moved", "# CB\n\n- [ ] Call Sam\n")
        keys = self.view(client, "moved")
        after = ("# CB\n\n- [ ] Call Sam today\n" if change == "gone"
                 else "# CB\n\n- [ ] New\n- [ ] Call Sam\n- [ ] Call Sam\n")
        path.write_text(after)
        resp = client.post("/api/toggle-checkbox/moved", data={"idx": "0", "key": keys[0]},
                           headers=HOST)
        assert resp.status_code == 409 and path.read_text() == after

    @pytest.mark.parametrize("idx", ["abc", "", "1.5"])
    def test_returns_400_for_non_integer_idx(self, client, wiki_dir, idx):
        # Regression: int() on the raw form value raised → 500.
        write_page(wiki_dir, "bad-idx", "# CB\n\n- [ ] task\n")
        resp = client.post("/api/toggle-checkbox/bad-idx", data={"idx": idx}, headers=HOST)
        assert resp.status_code == 400

    @pytest.mark.parametrize("hidden", [
        "<!--\n- [ ] hidden\n-->\n",           # in an HTML comment
        "Text.\n\n    - [ ] hidden\n",          # an indented code block
        "> ```\n> - [ ] hidden\n> ```\n",       # a fence in a quote
        "<div>\n- [ ] hidden\n</div>\n",       # raw HTML
        "- a\n\n      - [ ] hidden\n",          # code in a list item
    ])
    def test_a_tick_goes_to_the_box_the_page_shows(self, client, wiki_dir, hidden):
        """The page counts the boxes the markdown renders; the server counts
        them the same way, by the parser: a "- [ ]" line that is no box
        (code, a comment, raw HTML) shifts no number, so ticking the box
        shown ticks it, not the hidden line before it."""
        path = write_page(wiki_dir, "hidden", "# CB\n\n" + hidden + "\n- [ ] shown\n")
        body = client.get("/page/hidden", headers=HOST).get_data(as_text=True)
        assert body.count('class="task-list-item-checkbox"') == 1
        keys = self.view(client, "hidden")
        assert len(keys) == 1
        resp = client.post("/api/toggle-checkbox/hidden", data={"idx": "0", "key": keys[0]}, headers=HOST)
        assert resp.status_code == 200
        text = path.read_text()
        assert "- [ ] hidden" in text and re.search(r"- \[x\] shown <!--d:", text)
        # Without a key (a view from before keys) the number alone does the same.
        client.post("/api/toggle-checkbox/hidden", data={"idx": "0"}, headers=HOST)
        assert path.read_text() == "# CB\n\n" + hidden + "\n- [ ] shown\n"


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

    def test_a_name_with_spaces_gets_a_url_a_link_can_hold(self, client, wiki_dir):
        # Regression: the URL had the name as it is, and "[Deck](/_assets/My Deck (v2).pdf)"
        # is no link in markdown: a space ends its target.
        from io import BytesIO
        name = "My Deck (v2) für Jörg.pdf"
        resp = client.post("/api/upload", data={"file": (BytesIO(b"%PDF-1.4"), name)},
                           content_type="multipart/form-data", headers=HOST)
        url = resp.get_json()["url"]
        assert url == "/_assets/My%20Deck%20%28v2%29%20f%C3%BCr%20J%C3%B6rg.pdf"
        assert (wiki_dir / "_assets" / name).read_bytes() == b"%PDF-1.4"      # the file keeps its name
        got = client.get(url, headers=HOST)
        assert got.status_code == 200 and got.get_data() == b"%PDF-1.4"
        # As the editor writes it, it is a link that opens the file.
        write_page(wiki_dir, "tel/promotions/journey", f"# Journey\n\n[the deck]({url})\n")
        body = client.get("/page/tel/promotions/journey", headers=HOST).get_data(as_text=True)
        assert f'<a href="{url}">the deck</a>' in body

    def test_links_written_with_the_spaces_in_them_work_too(self, client, wiki_dir):
        # What the editor wrote before: such pages needn't be edited.
        (wiki_dir / "_assets" / "My Deck (v2).pdf").write_bytes(b"%PDF-1.4")
        (wiki_dir / "_assets" / "plain.pdf").write_bytes(b"%PDF-1.4")
        text = ("# Journey\n\n[the deck](/_assets/My Deck (v2).pdf) and ![shot](/_assets/My Shot 1.png), "
                "[plain](/_assets/plain.pdf) (see b c.pdf), [half](/_assets/Half%20done file.pdf)\n\n"
                "`[code](/_assets/My Deck (v2).pdf)`\n")
        path = write_page(wiki_dir, "journey", text)
        body = client.get("/page/journey", headers=HOST).get_data(as_text=True)
        assert '<a href="/_assets/My%20Deck%20%28v2%29.pdf">the deck</a>' in body
        assert client.get("/_assets/My%20Deck%20%28v2%29.pdf", headers=HOST).status_code == 200
        assert 'src="/_assets/My%20Shot%201.png"' in body
        assert '<a href="/_assets/plain.pdf">plain</a> (see b c.pdf)' in body
        assert '<a href="/_assets/Half%20done%20file.pdf">half</a>' in body
        assert "<code>[code](/_assets/My Deck (v2).pdf)</code>" in body      # code stays as written
        assert path.read_text(encoding="utf-8") == text                        # and so does the file
        # In a task's notes and comments too.
        html = client.post("/api/render-notes", json={"notes": ["[d](/_assets/My Deck (v2).pdf)"]},
                           headers=HOST).get_json()["notes"][0]
        assert '<a href="/_assets/My%20Deck%20%28v2%29.pdf">d</a>' in html

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
    """Uploads must not run scripts with Zonewiki's origin (stored XSS)."""

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
        # script-src 'self' trusts Zonewiki's origin, so an uploaded .js served as
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

    @pytest.mark.parametrize("name, inline", [
        ("pic.png", "image/png"), ("pic.JPG", "image/jpeg"), ("anim.gif", "image/gif"), ("pic.webp", "image/webp"),
        ("draw.svg", "image/svg+xml"), ("song.mp3", "audio/mpeg"), ("clip.mp4", "video/mp4"),
        ("doc.pdf", "application/pdf"), ("notes.txt", "text/plain; charset=utf-8"),
        # Not on the list, whatever the host's mime table calls them (the
        # tools image's says image/svg+xml-compressed, image/x-xbitmap …):
        # a download.
        ("draw.svgz", None), ("pic.xbm", None), ("page.xhtml", None), ("data.xml", None), ("notes.md", None)])
    def test_inline_only_as_a_type_on_the_list(self, client, wiki_dir, name, inline):
        resp = client.get(self.put(wiki_dir, name, b"<svg xmlns='http://www.w3.org/2000/svg'/>"), headers=HOST)
        if inline:
            assert resp.headers["Content-Type"] == inline
            assert "Content-Disposition" not in resp.headers
        else:
            assert resp.headers["Content-Type"] == "application/octet-stream"
            assert resp.headers["Content-Disposition"].startswith("attachment")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_upload_rejects_dot_names(self, client, wiki_dir):
        from io import BytesIO
        for name in ("..", ".git", ".hidden"):
            data = {"file": (BytesIO(b"x"), "a.png"), "filename": name, "overwrite": "1"}
            resp = client.post("/api/upload", data=data,
                               content_type="multipart/form-data", headers=HOST)
            assert resp.status_code == 400, name

# ===========================================================================
# Authentication (the wiki's password)
# ===========================================================================

class TestAuth:

    @pytest.fixture()
    def auth_client(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "testpassword")
        return client

    def test_login_page_has_no_action_buttons(self, auth_client):
        assert 'class="action-buttons"' not in auth_client.get("/login", headers=HOST).data.decode()

    def test_sign_out_only_when_signed_in(self, auth_client):
        assert "signout-form" not in auth_client.get("/login", headers=HOST).data.decode()
        auth_client.post("/login", data={"password": "testpassword"}, headers=HOST)
        body = auth_client.get("/page/index", headers=HOST).data.decode()
        assert 'action="/logout" class="menu-signout"' in body and "signout-form" not in body   # in the menu
        # A view without the menu (the editor) has the button itself.
        assert '<form method="post" action="/logout" class="signout-form">' in auth_client.get("/edit/index", headers=HOST).data.decode()

    def test_login_page_shows_remote_badge_in_its_form(self, auth_client, monkeypatch):
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", True)
        body = auth_client.get("/login", headers=HOST).data.decode()
        assert body.count('class="remote-badge"') == 1
        assert re.search(r'<h2>Sign in to Zonewiki<span class="remote-badge"[^>]*>🌐</span></h2>', body)
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
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

    @pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example",
                                        "/\t/evil.example", "/\n/evil.example", "/ /evil.example", "/x\\..\\evil"])
    def test_login_next_rejects_offsite_redirect(self, auth_client, target):
        # Regression: open redirect via protocol-relative next URL, and one a
        # browser makes of it: it drops tabs and line breaks ("/\t/evil" is
        # "//evil") and reads a backslash as a slash.
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
        assert data["name"] == "Zonewiki" and data["start_url"] == "/"
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

    def test_quicknote_index_page_loads(self, client):
        resp = client.get("/notes", headers=HOST)
        assert resp.status_code == 200
        assert b"Notes" in resp.data

    def test_api_quicknote_creates_note(self, client, wiki_dir):
        resp = client.post("/api/notes", data={"title": "My Note"}, headers=HOST)
        assert resp.status_code == 200
        slug = resp.get_json()["slug"]
        assert (wiki_dir / f"{slug}.md").exists()

    def test_api_quicknote_slash_in_title_makes_no_folder(self, client, wiki_dir):
        # A / used to make a folder, whose note was then no note.
        slug = client.post("/api/notes", data={"title": "A/B test"}, headers=HOST).get_json()["slug"]
        assert slug.endswith("_a-b-test") and "/" not in slug
        assert (wiki_dir / f"{slug}.md").read_text() == "# A/B test\n\n"

    def test_api_quicknote_same_title_same_second_makes_two_notes(self, client, wiki_dir):
        slugs = {client.post("/api/notes", data={"title": "Twin"}, headers=HOST).get_json()["slug"]
                 for _ in range(3)}
        assert len(slugs) == 3

    def test_search_can_keep_only_quicknotes(self, client, wiki_dir):
        # The Notes page's filter: the same search, notes only.
        write_page(wiki_dir, "2026-09-01_10-00-00_bob-call", "# Bob call\n\nRoof repair quote.\n")
        write_page(wiki_dir, "2026-09-02_10-00-00_groceries", "# Groceries\n\nMilk, eggs.\n")
        write_page(wiki_dir, "roof", "# Roof\n\nThe roof repair plan.\n")
        client.post("/api/tasks", json={"title": "Roof repair"}, headers=HOST)
        get = lambda q, **kw: client.get("/api/search", query_string={"q": q, **kw}, headers=HOST).get_json()
        assert {r["slug"] for r in get("roof repair")} >= {"roof", "2026-09-01_10-00-00_bob-call"}
        hits = get("roof repair", notes="1")
        assert [r["slug"] for r in hits] == ["2026-09-01_10-00-00_bob-call"]
        assert "<mark>" in hits[0]["excerpt"] and "hl=" in hits[0]["href"]
        # The query syntax works as everywhere.
        assert [r["slug"] for r in get('"milk, eggs"', notes="1")] == ["2026-09-02_10-00-00_groceries"]
        assert [r["slug"] for r in get("roof -quote", notes="1")] == []
        # Every match, not only the search modal's 100.
        for i in range(120):
            write_page(wiki_dir, f"2026-08-01_10-00-{i // 60:02d}_n{i}", f"# Note {i}\n\nzebra\n")
        assert len(get("zebra", notes="1", limit="1000")) == 120
        assert len(get("zebra", limit="1000")) == 100

    def test_quicknote_page_has_a_filter(self, client, wiki_dir):
        write_page(wiki_dir, "2026-09-01_10-00-00_bob-call", "# Bob call\n")
        body = client.get("/notes?q=bob", headers=HOST).get_data(as_text=True)
        assert 'id="notes-filter"' in body and 'value="bob"' in body
        assert 'data-slug="2026-09-01_10-00-00_bob-call"' in body
        # No notes, nothing to filter.
        for f in wiki_dir.glob("2026-*.md"):
            f.unlink()
        assert 'id="notes-filter"' not in client.get("/notes", headers=HOST).get_data(as_text=True)

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
        old_allow_remote = settings.ZONE_ALLOW_REMOTE
        settings.ZONE_ALLOW_REMOTE = False
        try:
            resp = client.get("/page/index", headers={"Host": "evil.com"})
            assert resp.status_code == 400
        finally:
            settings.ZONE_ALLOW_REMOTE = old_allow_remote

    def test_allow_remote_without_hosts_lets_in_addresses_not_names(self, client, monkeypatch):
        # LAN access by IP needs nothing set up; a name could be an
        # attacker's, rebound to the wiki's address (DNS rebinding).
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", True)
        for host in ("192.168.1.10:5000", "10.0.0.7", "[fe80::1]:5000", "[2001:db8::5]", "localhost:5000",
                     "host.containers.internal:5000", "host.docker.internal"):   # .internal: never anyone's
            assert client.get("/page/index", headers={"Host": host}).status_code == 200, host
        for host in ("evil.example:5000", "nas.local", "192.168.1.10.evil.example", "0x7f.1", "[evil]"):
            resp = client.get("/page/index", headers={"Host": host})
            assert resp.status_code == 400, host
        # The refusal says how a name of the wiki's gets in.
        body = client.get("/page/index", headers={"Host": "nas.local:5000"}).get_data(as_text=True)
        assert '"nas.local"' in body and "hosts" in body

    def test_localhost_at_any_port(self, client, monkeypatch):
        # zonewiki --addr :8080: the browser sends localhost:8080.
        monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
        for host in ("localhost:8080", "127.0.0.1:5000", "[::1]:8080", "LOCALHOST", "localhost"):
            assert client.get("/page/index", headers={"Host": host}).status_code == 200, host
        for host in ("localhost.evil.example:5000", "127.0.0.2:5000", "evil.example:5000"):
            assert client.get("/page/index", headers={"Host": host}).status_code == 400, host


    def test_leaf_hosts_names_who_may_come_in(self, client, monkeypatch):
        monkeypatch.setattr(settings, "HOSTS", frozenset({"wiki.example.org", "[::1]"}))
        for allow_remote in (False, True):   # set, the check stays on even with ZONE_ALLOW_REMOTE
            monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", allow_remote)
            for host in ("wiki.example.org", "Wiki.Example.org:8443", "localhost:5000", "[::1]:5000"):
                assert client.get("/page/index", headers={"Host": host}).status_code == 200, host
            for host in ("evil.example", "wiki.example.org.evil.example", "192.168.1.5:5000"):
                assert client.get("/page/index", headers={"Host": host}).status_code == 400, host

# ===========================================================================
# Request bodies: at most 16 MB, an upload 100 MB
# ===========================================================================

class TestBodyLimits:

    BIG = b"x" * (16 * 1024 * 1024 + 1)

    def test_too_large_is_refused(self, client, wiki_dir):
        # Refused on its length, before a byte of it is read.
        for path in ("/api/preview", "/api/save/index", "/api/tasks", "/mcp", "/new"):
            status, text = self._raw(client, path, {"Content-Length": str(len(self.BIG))})
            assert status == 413 and "16 MB" in text, path

    @staticmethod
    def _raw(client, path, headers, body=None, chunked=False):
        """A request the test client can't send: chunked, or a length it doesn't send."""
        server = client.live.ensure()
        conn = http.client.HTTPConnection(server.host, server.port, timeout=60)
        try:
            conn.request("POST", path, body=body, headers={"Host": "localhost:5000", **headers},
                         encode_chunked=chunked)
            resp = conn.getresponse()
            return resp.status, resp.read().decode()
        finally:
            conn.close()

    def test_without_a_length_cut_at_the_limit(self, client, wiki_dir):
        # Chunked, so the server can't refuse before reading.
        chunks = (b"x" * (1024 * 1024) for _ in range(17))
        status, _ = self._raw(client, "/api/preview", {"Transfer-Encoding": "chunked"}, chunks, chunked=True)
        assert status == 413

    def test_upload_up_to_its_own_limit(self, client, wiki_dir):
        resp = client.post("/api/upload", headers=HOST, content_type="multipart/form-data",
                           data={"file": (io.BytesIO(self.BIG), "big.bin")})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        status, text = self._raw(client, "/api/upload", {"Content-Length": str(100 * 1024 * 1024 + 1)})
        assert status == 413 and "100 MB" in text

    def test_a_page_just_below_saves(self, client, wiki_dir):
        text = "# Big\n\n" + "word " * 200_000
        resp = client.post("/api/save/big", data={"content": text}, headers=HOST)
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_upload_max_sets_an_uploads_limit(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "upload-max = 1KB\n")
        resp = client.post("/api/upload", headers=HOST, content_type="multipart/form-data",
                           data={"file": (io.BytesIO(b"x" * 2000), "big.bin")})
        assert resp.status_code == 413 and "at most 1 KB" in resp.get_data(as_text=True)
        assert not (wiki_dir / "_assets" / "big.bin").exists()
        # Go reads a small body before it answers: sent, though refused on its length.
        status, text = self._raw(client, "/api/upload", {"Content-Length": "2000"}, b"x" * 2000)
        assert status == 413 and "at most 1 KB" in text
        resp = client.post("/api/upload", headers=HOST, content_type="multipart/form-data",
                           data={"file": (io.BytesIO(b"x" * 500), "small.bin")})
        assert resp.status_code == 200
        # The other requests keep theirs.
        assert client.post("/api/preview", data={"content": "x" * 5000}, headers=HOST).status_code == 200


# ===========================================================================
# storage-max: how much the wiki may hold (go/storage.go)
# ===========================================================================

class TestStorageMax:
    """The browser's own ways to make something new (both doors' page
    writes: tests/test_page_writes.py)."""

    @pytest.fixture()
    def full(self, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "storage-max = 10KB\n")
        # 9.3 KB of the 10 KB: index.md, p and this.
        (wiki_dir / "_assets" / "big.bin").write_bytes(b"x" * 9500)
        write_page(wiki_dir, "p", "# P\n")
        return wiki_dir

    @staticmethod
    def upload(client, data, name, **form):
        return client.post("/api/upload", headers=HOST, content_type="multipart/form-data",
                           data={"file": (io.BytesIO(data), name), **form})

    def test_an_upload_that_doesnt_fit(self, client, full):
        resp = self.upload(client, b"x" * 2000, "photo.png")
        assert resp.status_code == 507
        assert "The wiki is full:" in resp.get_data(as_text=True)
        assert not (full / "_assets" / "photo.png").exists()
        assert self.upload(client, b"x" * 500, "small.png").status_code == 200
        # Replacing a file adds only what it grows by.
        assert self.upload(client, b"y" * 9600, "big.bin", overwrite="1").status_code == 200
        assert (full / "_assets" / "big.bin").read_bytes() == b"y" * 9600

    def test_a_smaller_file_replaces_one_in_a_full_wiki(self, client, full):
        (full / "_assets" / "more.bin").write_bytes(b"x" * 1000)
        assert self.upload(client, b"x" * 10, "photo.png").status_code == 507
        assert self.upload(client, b"y" * 100, "more.bin", overwrite="1").status_code == 200
        assert (full / "_assets" / "more.bin").read_bytes() == b"y" * 100

    def test_uploads_in_a_row_count(self, client, full):
        """Each counts at once, not at the next walk."""
        assert self.upload(client, b"x" * 300, "a.png").status_code == 200
        assert self.upload(client, b"x" * 300, "b.png").status_code == 200
        assert self.upload(client, b"x" * 300, "c.png").status_code == 507

    def test_no_editor_for_a_new_page(self, client, full):
        (full / "_assets" / "more.bin").write_bytes(b"x" * 1000)
        resp = client.get("/edit/new-page", headers=HOST)
        assert resp.status_code == 507 and "The wiki is full:" in resp.get_data(as_text=True)
        assert client.get("/edit/p", headers=HOST).status_code == 200

    def test_no_auto_save_makes_a_page(self, client, full):
        (full / "_assets" / "more.bin").write_bytes(b"x" * 1000)
        assert client.post("/api/save/new-page", data={"content": "# New\n"}, headers=HOST).status_code == 507
        assert not (full / "new-page.md").exists()
        resp = client.post("/api/save/p", data={"content": "# P\n\n" + "word " * 1000}, headers=HOST)
        assert resp.status_code == 200

    def test_git_and_zones_folder_dont_count(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "storage-max = 10KB\n")
        (wiki_dir / ".git").mkdir(exist_ok=True)
        (wiki_dir / ".git" / "big.pack").write_bytes(b"x" * 50_000)
        (wiki_dir / ".zone").mkdir(exist_ok=True)
        (wiki_dir / ".zone" / "cache").write_bytes(b"x" * 50_000)
        assert self.upload(client, b"x" * 2000, "photo.png").status_code == 200

    def test_unset_no_limit(self, client, wiki_dir):
        (wiki_dir / "_assets" / "big.bin").write_bytes(b"x" * 200_000)
        assert self.upload(client, b"x" * 2000, "photo.png").status_code == 200
        assert client.get("/edit/new-page", headers=HOST).status_code == 200


# ===========================================================================
# Behind an HTTPS proxy: the login cookie, throttling guesses
# ===========================================================================

class TestProduction:

    def test_no_framing_by_other_sites_or_by_zone(self, client, wiki_dir):
        # A page's raw HTML may hold an <iframe>: of Zonewiki's views none is
        # framed, by another site or by Zonewiki; an upload may be (a PDF in a page).
        for path in ("/page/index", "/tasks", "/api/version", "/login", "/changes", "/public/style.css", "/nope"):
            resp = client.get(path, headers=HOST)
            assert resp.headers["Content-Security-Policy"].endswith("; frame-ancestors 'none'"), path
            assert resp.headers["X-Frame-Options"] == "DENY", path
        (wiki_dir / "_assets" / "a.pdf").write_bytes(b"%PDF-1.4\n")
        resp = client.get("/_assets/a.pdf", headers=HOST)
        assert resp.headers["Content-Security-Policy"].endswith("; frame-ancestors 'self'")
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"

    def test_only_zones_own_scripts_run(self, client, wiki_dir):
        # A page's raw HTML (written by an AI client after a prompt injection,
        # say) must not run a script: script-src 'self' without
        # 'unsafe-inline' blocks <script>, onerror= and javascript: links.
        write_page(wiki_dir, "evil", "# Evil\n\n<script>window.x=1</script><img src=x onerror=alert(1)>\n")
        for path in ("/page/evil", "/edit/evil", "/tasks", "/api/tasks", "/login", "/public/zonewiki.js"):
            resp = client.get(path, headers=HOST)
            csp = resp.headers["Content-Security-Policy"]
            assert csp == client.get("/page/index", headers=HOST).headers["Content-Security-Policy"], path
            rules = dict(rule.strip().split(" ", 1) for rule in csp.split(";"))
            assert rules["script-src"] == "'self'" and "unsafe-eval" not in csp
            for rule in ("object-src 'none'", "base-uri 'self'", "form-action 'self'"):
                assert rule in csp, (path, rule)
            assert resp.headers["X-Content-Type-Options"] == "nosniff", path

    def test_a_page_loads_nothing_from_other_hosts(self, client, wiki_dir):
        # An image or a frame elsewhere is a request the page's writer
        # addresses: a way to send the wiki away without a script.
        resp = client.get("/page/index", headers=HOST)
        rules = dict(rule.strip().split(" ", 1) for rule in resp.headers["Content-Security-Policy"].split(";"))
        assert rules["default-src"] == "'self'" and rules["connect-src"] == "'self'"
        assert rules["img-src"] == rules["media-src"] == "'self' data: blob:"
        assert rules["frame-src"] == "'self'" and rules["font-src"] == "'self' data:"
        assert rules["style-src"] == "'self' 'unsafe-inline'"
        assert resp.headers["Referrer-Policy"] == "same-origin"
        # Nor does a browser look up the host names a page links to before
        # they are followed (Chromium may on plain http): the name asked
        # for can carry what the page's writer wants sent.
        for path in ("/page/index", "/tasks", "/api/tasks", "/login", "/public/zonewiki.js", "/_assets/nope.png"):
            assert client.get(path, headers=HOST).headers["X-DNS-Prefetch-Control"] == "off", path
        # A markdown image elsewhere is a link to it; Zonewiki's own and pasted ones stay images.
        html = preview(client, "![A cat|200](https://elsewhere.example/cat.png?q=1) ![](//elsewhere.example/x.png) "
                               "![mine](/_assets/x.png) ![rel](pic.png) ![pasted](data:image/png;base64,AA)")
        assert '<a href="https://elsewhere.example/cat.png?q=1"' in html and 'class="external-image"' in html
        assert ">A cat</a>" in html and ">//elsewhere.example/x.png</a>" in html
        assert html.count("<img ") == 3 and "elsewhere.example" not in "".join(re.findall(r"<img [^>]*>", html))
        # No <link> in a page: the browser would look its host up.
        assert "&lt;link rel" in preview(client, '<link rel="dns-prefetch" href="//secret.elsewhere.example">')

    def test_external_content_names_the_hosts_a_page_may_load_from(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "CONFIG_FILE", "external-content = upload.wikimedia.org, *.example.org\n")
        csp = client.get("/page/index", headers=HOST).headers["Content-Security-Policy"]
        rules = dict(rule.strip().split(" ", 1) for rule in csp.split(";"))
        for rule in ("img-src", "media-src", "frame-src"):
            assert rules[rule].endswith(" upload.wikimedia.org *.example.org"), rule
        assert rules["script-src"] == "'self'" and rules["style-src"] == "'self' 'unsafe-inline'"
        html = preview(client, "![a](https://upload.wikimedia.org/a.png) ![b](https://img.example.org/b.png) "
                               "![c](https://example.org/c.png) ![d](https://elsewhere.example/d.png)")
        assert html.count("<img ") == 2 and html.count('class="external-image"') == 2
        monkeypatch.setattr(settings, "CONFIG_FILE", "external-content = *\n")
        assert "<img " in preview(client, "![d](https://elsewhere.example/d.png)")
        assert "img-src 'self' data: blob: *;" in client.get("/page/index", headers=HOST).headers["Content-Security-Policy"]

    def test_framing_rule_joins_an_existing_policy(self, client, wiki_dir):
        (wiki_dir / "_assets" / "x.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        csp = client.get("/_assets/x.svg", headers=HOST).headers["Content-Security-Policy"]
        assert csp.startswith("sandbox; default-src 'none'") and csp.endswith("; frame-ancestors 'self'")

class TestHttpsAndThrottle:

    @pytest.fixture()
    def auth_client(self, client, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "pässwört")
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
        client.post("/api/tasks", json={"title": "running shoes"}, headers=HOST)
        results = client.get("/api/search?q=running", headers=HOST).get_json()
        page = next(r for r in results if r["slug"] == "run")
        assert page["href"] == "/page/run?hl=runs"
        task = next(r for r in results if r.get("type") == "task")
        assert "hl=" not in task["href"]
        phrase = client.get("/api/search?q=runs+again", headers=HOST).get_json()[0]
        assert phrase["href"] == "/page/run?hl=RUNS+again"

    def test_task_result_href_opens_tasks_at_the_task(self, client, wiki_dir):
        # Regression: the href prefilled the title into the task app's filter
        # field, where a "#word" in the title reads as a tag filter.
        tid = client.post("/api/tasks", json={"title": "Water the ferns #3"},
                          headers=HOST).get_json()["id"]
        results = client.get("/api/search?q=ferns", headers=HOST).get_json()
        task_hits = [r for r in results if r.get("type") == "task"]
        assert task_hits, "expected a task hit"
        assert task_hits[0]["href"] == f"/tasks?task={tid}&view=open"


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
# Linked tasks (tasks that wikilink to a page)
# ===========================================================================

class TestTaskBacklinks:

    def test_task_linking_a_section_names_it_and_marks_the_heading(self, client, wiki_dir):
        # [[Page#Section]] in a task: it is still a linked task of the page,
        # names the section (linking to the heading), and the heading gets a
        # chip listing the section's tasks. A /page/<slug>#anchor link too.
        write_page(wiki_dir, "garden", "# Garden\n\n## Beds\n\nSoil.\n\n## Garden paths\n\nGravel.\n")
        weed = client.post("/api/tasks", json={"title": "Weed [[Garden#Beds]]"}, headers=HOST).get_json()["id"]
        client.post("/api/tasks", json={"title": "Rake", "description": "[the paths](/page/garden#garden-paths)"}, headers=HOST)
        client.post("/api/tasks", json={"title": "Water [[garden]]"}, headers=HOST)
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
        assert f'href="/tasks?task={weed}&amp;view=open"' in beds
        paths = html.split('<h2 id="garden-paths">', 1)[1].split("</div>", 1)[0]
        assert "☑ 1" in paths and "Rake" in paths
        # The chip sits after the heading: the TOC and anchors are unchanged.
        assert '<h2 id="beds">Beds</h2>' in html

    def test_task_with_wikilink_listed_on_page(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": "Water [[Garden]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' in html
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        assert "Water Garden" in section

    def test_linked_task_links_to_the_task_by_id(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        tid = client.post("/api/tasks", json={"title": "Water [[Garden]]"},
                          headers=HOST).get_json()["id"]
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        assert f'href="/tasks?task={tid}&amp;view=open"' in section

    def test_linked_task_entry_is_a_single_link(self, client, wiki_dir):
        # Regression: the task title's own wikilinks were rendered as <a>
        # inside the outer tasks <a> — nested anchors, which browsers split so
        # half the entry went to the tasks and half to the page.
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": "Water [[Garden]] and [x](/page/garden)"},
                    headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        section = html.split('id="task-backlinks"')[1].split("</section>")[0]
        items = re.findall(r"<li>(.*?)</li>", section)
        assert len(items) == 1
        assert items[0].count("<a ") == 1
        assert "Water Garden and x" in items[0]

    def test_wikilink_in_description_and_labelled_link(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": "Chores",
                                       "description": "see [[garden|the beds]]"}, headers=HOST)
        assert task_backlinks(client, "garden") == ["Chores"]

    def test_markdown_page_link_counts(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": "Read [notes](/page/garden)"}, headers=HOST)
        assert len(task_backlinks(client, "garden")) == 1

    def test_completed_and_unrelated_tasks_excluded(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        t = client.post("/api/tasks", json={"title": "Done [[garden]]"}, headers=HOST).get_json()
        client.put(f"/api/tasks/{t['id']}", json={"completed": True}, headers=HOST)
        client.post("/api/tasks", json={"title": "About [[kitchen]]"}, headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' not in html

    def test_task_title_is_escaped(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": "<img src=x onerror=alert(1)> [[garden]]"},
                    headers=HOST)
        html = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert "<img src=x" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


class TestPlainTaskTitle:

    def test_search_shows_a_task_title_plain(self, client):
        client.post("/api/tasks", json={"title": "Read [the RFC](https://e.x/rfc) on [[Garden]]"}, headers=HOST)
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
    """Backlinks count markdown links, and tasks link pages too — a
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

    def test_tasks_tasks_follow_rename(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        linked = client.post("/api/tasks", json={
            "title": "Do [[old]] and [[Old|label]]", "description": "see [x](/page/old)",
            }, headers=HOST).get_json()
        done = client.post("/api/tasks", json={"title": "Done [[old]]"},
                           headers=HOST).get_json()
        client.put(f"/api/tasks/{done['id']}", json={"completed": True}, headers=HOST)
        other = client.post("/api/tasks", json={"title": "[[older]]"},
                            headers=HOST).get_json()
        self.rename(client, "old", "new")
        tasks = {t["id"]: t for t in client.get("/api/tasks", headers=HOST).get_json()}
        assert tasks[linked["id"]]["title"] == "Do [[new]] and [[new|label]]"
        assert tasks[linked["id"]]["description"] == "see [x](/page/new)"
        assert tasks[other["id"]]["title"] == "[[older]]"
        completed = client.get("/api/tasks/closed", headers=HOST).get_json()
        assert completed[0]["title"] == "Done [[new]]"
        html = client.get("/page/new", headers=HOST).get_data(as_text=True)
        assert 'id="task-backlinks"' in html

    def test_rename_without_tasks_files_does_not_create_them(self, client, wiki_dir):
        write_page(wiki_dir, "old", "# Old\n")
        self.rename(client, "old", "new")
        assert not (wiki_dir / "_tasks" / "tasks.json").exists()


# ===========================================================================
# /tasks — find the page that embeds the tasks
# ===========================================================================

class TestTasksPage:
    """/tasks is a built-in page with one full main tasks. It used to redirect
    to whichever page embedded [[tasks:]], so where the due badge and task links
    landed depended on the wiki's pages (and, before that, /page/tasks opened
    the editor in a wiki without tasks.md)."""

    def get(self, client, qs=""):
        resp = client.get("/tasks" + qs, headers=HOST)
        assert resp.status_code == 200
        return resp.get_data(as_text=True)

    def test_renders_one_main_tasks(self, client, wiki_dir):
        body = self.get(client)
        assert body.count('class="tasks-app"') == 1
        assert 'data-init-list=""' in body
        assert 'data-init-tag=""' in body
        assert 'data-init-view=""' in body   # ?view= / session / Today decide
        assert "<h1>All tasks</h1>" in body

    def test_does_not_depend_on_pages(self, client, wiki_dir):
        # Embeds elsewhere (even a task list.md) are ordinary pages now.
        write_page(wiki_dir, "tasks", "# Desk tasks\n\n[[tasks: tag=work]]\n\n[[tasks: view=open group=day]]\n")
        body = self.get(client, "?task=abc&view=today")
        assert body.count('class="tasks-app"') == 1
        assert "Desk tasks" not in body   # the page's title: not the view My tasks
        assert 'data-init-tag=""' in body

    def test_list(self, client, wiki_dir):
        make_list(client, "groceries", headers=HOST)
        body = self.get(client, "?list=Groceries&task=abc")
        assert 'data-init-list="groceries"' in body
        assert '<a href="/tasks">Tasks</a>' in body
        assert "<h1>List: groceries</h1>" in body

    def test_list_name_escaped(self, client, wiki_dir):
        body = self.get(client, "?list=<b>x")
        assert "<b>x" not in body

    def test_in_search_modal_list(self, client, wiki_dir):
        pages = client.get("/api/pages-list", headers=HOST).get_json()
        tasks = [p for p in pages if p.get("href") == "/tasks"]
        assert len(tasks) == 1 and tasks[0]["title"] == "Tasks"

    def test_linked_task_in_list_links_to_list(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        make_list(client, "shop", headers=HOST)
        tid = client.post("/api/tasks", json={"title": "Seeds for [[Garden]]", "list": "shop"},
                          headers=HOST).get_json()["id"]
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert f'href="/tasks?task={tid}&amp;view=open&amp;list=shop"' in body

    def test_due_count_is_my_tasks_whatever_is_pinned(self, client, wiki_dir):
        from datetime import date
        today = date.today().isoformat()
        for name in ("task", "shop"):
            make_list(client, name, headers=HOST)
        for title, fields in (("Pay rent", {"list": "task"}), ("Milk", {"list": "shop"}),
                              ("Call Bob", {"list": "task", "tags": ["waiting"]}), ("Book trip", {})):
            client.post("/api/tasks", json={"title": title, "due_date": today, **fields}, headers=HOST)
        count = lambda: client.get("/api/tasks/due-count", headers=HOST).get_json()["count"]
        pin = lambda name, how: client.post("/tasks/pin-list", data={"list": name, "pin": how}, headers=HOST)
        home = lambda name, value: client.post("/api/tasks/settings", json={"list": name, "set": {"home": value}}, headers=HOST)
        assert count() == 4   # without users every task, pinned or not
        for name, how in (("task", "1"), ("shop", "first"), ("task", "0"), ("shop", "0")):
            pin(name, how)
            assert count() == 4, (name, how)   # a pin is a pill
        # But a list left out of My tasks, pinned or not.
        assert home("task", "off").status_code == 200
        assert count() == 2
        pin("task", "1")
        assert count() == 2
        assert home("task", "").status_code == 200
        assert count() == 4

    def test_task_counts_are_rendered_into_the_page(self, client, wiki_dir):
        # In the HTML, not only fetched later: the Tasks link must not change
        # width after the page has painted.
        from datetime import date, timedelta
        today = date.today()
        for data in ({"title": "Late", "due_date": (today - timedelta(days=1)).isoformat()},
                     {"title": "Started", "start_date": today.isoformat()},
                     {"title": "Later", "start_date": (today + timedelta(days=3)).isoformat()},
                     {"title": "Someday"}):
            client.post("/api/tasks", json=data, headers=HOST)
        assert client.get("/api/tasks/due-count", headers=HOST).get_json() == {
            "count": 1, "overdue": 1, "today": 0, "focus": 2}
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        counts = ('<span class="module-counts"><span class="module-due">1</span>'
                  '<span class="module-focus">2</span><span class="module-new" hidden></span></span>')
        assert body.count(counts) == 2   # switcher and tab bar
        assert body.count('title="Tasks: 2 for today · 1 overdue"') == 2
        # Zero is hidden, not "0".
        client.post("/api/tasks/batch-delete", json={"ids": [t["id"] for t in client.get(
            "/api/tasks", headers=HOST).get_json()]}, headers=HOST)
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert ('<span class="module-due" hidden></span><span class="module-focus" hidden></span>'
                '<span class="module-new" hidden></span>') in body

    def test_due_badge_and_task_links_use_it(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": "Water [[Garden]]"}, headers=HOST)
        body = client.get("/page/garden", headers=HOST).get_data(as_text=True)
        assert 'href="/tasks" class="module-link" data-module="tasks"' in body
        assert "/page/tasks" not in body


# ===========================================================================
# Cross-site request forgery
# ===========================================================================

class TestCrossSiteWrites:
    """Regression: any website could post a form to localhost:5000 and delete
    or overwrite pages (or tasks); the Host check alone let it through."""

    EVIL = {**HOST, "Origin": "https://evil.example"}
    SAME = {**HOST, "Origin": "http://localhost:5000"}

    def test_forged_delete_and_edit_are_refused(self, client, wiki_dir):
        write_page(wiki_dir, "victim", "# Victim\n\nkeep me\n")
        assert client.post("/delete/victim", headers=self.EVIL).status_code == 403
        resp = client.post("/edit/victim", data={"content": "<script>x</script>"}, headers=self.EVIL)
        assert resp.status_code == 403
        assert (wiki_dir / "victim.md").read_text(encoding="utf-8") == "# Victim\n\nkeep me\n"

    def test_forged_tasks_writes_are_refused(self, client, wiki_dir):
        # text/plain: a cross-site fetch/form can send it without a preflight.
        tid = client.post("/api/tasks", json={"title": "Keep"}, headers=HOST).get_json()["id"]
        resp = client.post("/api/tasks", data='{"title": "Spam"}', content_type="text/plain", headers=self.EVIL)
        assert resp.status_code == 403
        assert client.delete(f"/api/tasks/{tid}", headers=self.EVIL).status_code == 403
        assert client.put(f"/api/tasks/{tid}", json={"title": "x"}, headers=self.EVIL).status_code == 403
        assert [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()] == ["Keep"]

    def test_null_origin_is_refused(self, client, wiki_dir):
        # A sandboxed iframe on an attacker's page posts with Origin: null.
        write_page(wiki_dir, "victim", "# Victim\n")
        assert client.post("/delete/victim", headers={**HOST, "Origin": "null"}).status_code == 403
        assert (wiki_dir / "victim.md").exists()

    def test_a_links_ping_is_refused(self, client, wiki_dir):
        # A followed <a ping=…> posts same-origin with the session's cookie:
        # the render renames the attribute, and the checks refuse a ping
        # that got past it, by its headers or its type.
        write_page(wiki_dir, "victim", "# Victim\n")
        for headers, kind in (({"Ping-To": "http://localhost:5000/page/index"}, None),
                              ({"Ping-From": "http://localhost:5000/page/x"}, None),
                              ({}, "text/ping"), ({}, "Text/Ping; charset=x")):
            resp = client.post("/delete/victim", headers={**self.SAME, **headers}, data=b"PING",
                               content_type=kind or "application/octet-stream")
            assert resp.status_code == 403 and b"ping" in resp.data, (headers, kind)
        assert (wiki_dir / "victim.md").exists()

    def test_same_origin_and_originless_writes_work(self, client, wiki_dir):
        write_page(wiki_dir, "a", "# A\n")
        write_page(wiki_dir, "b", "# B\n")
        assert client.post("/delete/a", headers=self.SAME).status_code == 302
        assert client.post("/delete/b", headers=HOST).status_code == 302   # curl, scripts
        assert client.post("/api/tasks", json={"title": "Ok"}, headers=self.SAME).status_code == 201

    def test_reads_are_not_affected(self, client, wiki_dir):
        write_page(wiki_dir, "r", "# R\n")
        assert client.get("/page/r", headers=self.EVIL).status_code == 200


# ── Zonewiki version ──────────────────────────────────────────────────────────────


class TestVersion:
    def test_shown_in_shortcuts_modal_and_api(self, client, wiki_dir, monkeypatch):
        v = client.get("/api/version", headers=HOST).get_json()["version"]
        monkeypatch.setattr(settings, "ZONE_BUILD", "abc1234 2026-09-26 12:00")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f'<p class="zone-version">Zonewiki {v} · abc1234 2026-09-26 12:00</p>' in body
        assert f'<meta name="generator" content="Zonewiki {v}">' in body
        assert client.get("/api/version", headers=HOST).get_json() == {
            "version": v, "build": "abc1234 2026-09-26 12:00"}

    def test_unknown_build_shows_version_only(self, client, wiki_dir, monkeypatch):
        v = client.get("/api/version", headers=HOST).get_json()["version"]
        monkeypatch.setattr(settings, "ZONE_BUILD", "unknown")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f'<p class="zone-version">Zonewiki {v}</p>' in body

    def test_shortcuts_link_to_the_source(self, client, wiki_dir):
        # The AGPL's offer of the source to everyone using Zonewiki over a network.
        for path in ("/page/index", "/tasks", "/edit/index"):
            body = client.get(path, headers=HOST).get_data(as_text=True)
            assert ('<p class="zone-version"><a href="https://github.com/zonewiki/zonewiki">Get the source code</a>'
                    ' · AGPL-3.0-or-later</p>') in body, path

    def test_bottom_left_shows_version_and_short_hash(self, client, wiki_dir, monkeypatch):
        v = client.get("/api/version", headers=HOST).get_json()["version"]
        monkeypatch.setattr(settings, "ZONE_BUILD", "abc1234 2026-09-26 12:00")
        for path in ("/page/index", "/tasks", "/edit/index"):
            body = client.get(path, headers=HOST).get_data(as_text=True)
            assert ('<div class="zone-build" title="Build abc1234 2026-09-26 12:00">'
                    f'Zonewiki {v} · abc1234</div>') in body, path
        monkeypatch.setattr(settings, "ZONE_BUILD", "abc1234 2026-09-26 12:00 +dirty")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f">Zonewiki {v} · abc1234+dirty</div>" in body
        monkeypatch.setattr(settings, "ZONE_BUILD", "unknown")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert f'<div class="zone-build">Zonewiki {v}</div>' in body

    def test_build_escaped(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "ZONE_BUILD", "<b>")
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert "· &lt;b&gt;</p>" in body

    @staticmethod
    def _start(wiki: Path | str | None, home: Path | None = None, *args: str,
               **more_env: str) -> subprocess.CompletedProcess:
        """Start Zonewiki with ZONE_DATA=wiki (None: unset), HOME=home if given,
        the flags args and the environment more_env, until it listens."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        if wiki is not None:
            env["ZONE_DATA"] = str(wiki)
        if home is not None:
            env["HOME"] = str(home)
        env.update(more_env)
        proc = subprocess.Popen([str(binary()), "--addr", "127.0.0.1:0", *args], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = proc.stdout.readline()
        if " listening on " in out:
            proc.terminate()
        # Read through the same buffer as readline (communicate would skip
        # what it read ahead: the lines after the first).
        with proc.stdout, proc.stderr:
            out += proc.stdout.read()
            err = proc.stderr.read()
        proc.wait(timeout=60)
        return subprocess.CompletedProcess(proc.args, 0 if " listening on " in out else proc.returncode,
                                           out, err)

    @pytest.mark.parametrize("wiki", [None, ""])
    def test_without_zone_data_the_wiki_is_in_the_home_folder(self, wiki, tmp_path):
        # A binary started with nothing set up is a wiki at once: ~/zonewiki,
        # made and said so, and where to open it.
        home = Path(os.path.realpath(tmp_path))
        r = self._start(wiki, home)
        assert r.returncode == 0, r.stderr
        made = home / "zonewiki"
        assert f"ZONE_DATA isn't set: made a new wiki at {made}" in r.stderr
        assert f"made {made} a git repository" in r.stderr and "found a git repository" not in r.stderr
        assert (made / "index.md").exists() and (made / "_assets").is_dir()
        assert re.search(rf"^Open http://localhost:\d+ in a browser\. The wiki is {re.escape(str(made))}$", r.stdout, re.M)
        (made / "notes.md").write_text("# Notes\n", encoding="utf-8")
        r = self._start(wiki, home)     # the next start: the same wiki, as it is
        assert r.returncode == 0, r.stderr
        assert "made a new wiki" not in r.stderr and (made / "notes.md").exists()
        assert f"found a git repository in {made}" in r.stderr and f"made {made} a git repository" not in r.stderr

    def test_says_when_there_is_no_git(self, tmp_path):
        # Without git on PATH the wiki runs with no history: the log says so.
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        env.update(ZONE_DATA=str(wiki), PATH=str(tmp_path / "empty"))
        proc = subprocess.Popen([str(binary()), "--addr", "127.0.0.1:0"], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert " listening on " in proc.stdout.readline()
        proc.terminate()
        with proc.stdout, proc.stderr:
            err = proc.stderr.read()
        proc.wait(timeout=60)
        assert "git isn't installed (not on PATH): the wiki keeps no history" in err
        assert not (wiki / ".git").exists()

    def test_refuses_a_missing_wiki_folder(self, tmp_path):
        # Zonewiki never makes a wiki that ZONE_DATA names, so a mistyped one
        # can't start an empty wiki somewhere else (the default is made).
        r = self._start(tmp_path / "nowhere", home=tmp_path)
        assert r.returncode != 0
        assert f"no wiki at {tmp_path / 'nowhere'}" in r.stderr
        assert not (tmp_path / "nowhere").exists() and not (tmp_path / "zonewiki").exists()

    def test_refuses_a_folder_inside_another_repository_untouched(self, tmp_path):
        # A ZONE_DATA naming a folder of a checkout: Zonewiki doesn't start, and
        # leaves the folder as it was (no .zone/, _assets/ or .git).
        outer = Path(os.path.realpath(tmp_path))
        subprocess.run(["git", "init", "-q", str(outer)], check=True)
        docs = outer / "docs"
        docs.mkdir()
        (docs / "readme.md").write_text("# Docs\n", encoding="utf-8")
        r = self._start(docs, home=tmp_path)
        assert r.returncode != 0
        assert f"{docs} is inside the git repository {outer}, which doesn't ignore it" in r.stderr
        assert sorted(p.name for p in docs.iterdir()) == ["readme.md"]

    def test_a_folder_with_files_is_made_a_wiki_only_when_asked(self, tmp_path):
        # A folder that holds files and isn't a wiki yet (a mistyped
        # ZONE_DATA: the home folder) is refused, untouched, and the message
        # names what is in it and how to go on.
        wiki = Path(os.path.realpath(tmp_path)) / "notes"
        (wiki / "projects").mkdir(parents=True)
        for name in ("a.md", "b.md", "c.md"):
            (wiki / name).write_text("# Note\n", encoding="utf-8")
        r = self._start(wiki, home=tmp_path)
        assert r.returncode != 0
        assert f"{wiki} holds files (a.md, b.md, c.md and 1 more) but isn't a wiki yet" in r.stderr
        assert "--init" in r.stderr and "ZONE_INIT=1" in r.stderr
        assert sorted(p.name for p in wiki.iterdir()) == ["a.md", "b.md", "c.md", "projects"]

        # --init: its files are the wiki's, in its first commit; the next
        # start needs nothing.
        r = self._start(wiki, tmp_path, "--init")
        assert r.returncode == 0, r.stderr
        assert f"made {wiki} a git repository" in r.stderr
        listed = subprocess.run(["git", "ls-files"], cwd=wiki, capture_output=True, text=True, check=True).stdout
        assert "a.md" in listed.split() and (wiki / ".zone-format").exists()
        r = self._start(wiki, home=tmp_path)
        assert r.returncode == 0, r.stderr

    def test_zone_init_makes_a_folder_with_files_a_wiki(self, tmp_path):
        (tmp_path / "a.md").write_text("# Note\n", encoding="utf-8")
        r = self._start(tmp_path, tmp_path, ZONE_INIT="1")
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "index.md").exists()

    def test_a_repository_with_files_is_no_wiki_yet(self, tmp_path):
        # A .git doesn't make a folder a wiki: a project's checkout or a
        # home folder kept in git has one, and Zonewiki would commit into it.
        # Refused untouched, as any folder with files; --init once, for
        # notes kept in git, whose history then goes on.
        wiki = Path(os.path.realpath(tmp_path))
        subprocess.run(["git", "init", "-q", str(wiki)], check=True)
        (wiki / "a.md").write_text("# Note\n", encoding="utf-8")
        r = self._start(wiki, home=tmp_path)
        assert r.returncode != 0
        assert f"{wiki} holds files (a.md) but isn't a wiki yet" in r.stderr
        assert "commits its changes to the git repository there" in r.stderr
        assert sorted(p.name for p in wiki.iterdir()) == [".git", "a.md"]
        r = self._start(wiki, tmp_path, "--init")
        assert r.returncode == 0, r.stderr
        assert f"found a git repository in {wiki}" in r.stderr and (wiki / ".zone-format").exists()
        r = self._start(wiki, home=tmp_path)
        assert r.returncode == 0, r.stderr

    def test_an_empty_repository_needs_no_init(self, tmp_path):
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        r = self._start(tmp_path, home=tmp_path)
        assert r.returncode == 0, r.stderr

    def test_a_wiki_from_before_the_format_file_needs_no_init(self, tmp_path):
        # A wiki an older Zonewiki served has no .zone-format yet but its
        # session key, which only a start writes: it starts as it did, and
        # gets the file.
        wiki = Path(os.path.realpath(tmp_path))
        subprocess.run(["git", "init", "-q", str(wiki)], check=True)
        (wiki / "index.md").write_text("# Home\n", encoding="utf-8")
        (wiki / "notes.md").write_text("# Notes\n", encoding="utf-8")
        (wiki / ".zone").mkdir()
        (wiki / ".zone" / "secret_key").write_text("k" * 64, encoding="utf-8")
        r = self._start(wiki, home=tmp_path)
        assert r.returncode == 0, r.stderr
        assert (wiki / ".zone-format").exists()

    def test_settings_written_before_the_first_start_need_no_init(self, tmp_path):
        # zonewiki user add before the first start writes .zone/config:
        # the folder holds no files of its own yet.
        (tmp_path / ".zone").mkdir()
        (tmp_path / ".zone" / "config").write_text("", encoding="utf-8")
        (tmp_path / "_assets").mkdir()
        r = self._start(tmp_path, home=tmp_path)
        assert r.returncode == 0, r.stderr

    def test_starts_with_an_empty_wiki_folder(self, tmp_path):
        r = self._start(tmp_path)
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "index.md").exists() and (tmp_path / "_assets").is_dir()


class TestUsage:
    """zonewiki --help names every command, the environment and the flags;
    each command answers --help with its own usage, without a wiki."""

    @staticmethod
    def _run(*args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        return subprocess.run([str(binary()), *args], env=env, capture_output=True, text=True,
                              timeout=60)

    @pytest.mark.parametrize("arg", ["--help", "-h", "help"])
    def test_zonewiki(self, arg):
        r = self._run(arg)
        assert r.returncode == 0, r.stderr
        out = r.stdout + r.stderr
        for word in ("Usage: zonewiki", "user COMMAND", "config COMMAND", "eval ", "health ", "licenses ", "version ",
                     "export [--output FILE]", "import FILE",
                     "ZONE_DATA", "ZONE_ALLOW_REMOTE", "ZONE_SEMANTIC_SEARCH",
                     "--addr ADDRESS", "--public FOLDER", "--docs FOLDER", "--semantic-test JSON",
                     "(default :5080)"):
            assert word in out, word
        assert not re.search(r"^ +-[a-z]", out, re.M)   # flags shown with two dashes

    @pytest.mark.parametrize("command", ["user", "config", "eval", "health", "licenses", "export", "import"])
    @pytest.mark.parametrize("arg", ["--help", "-h"])
    def test_command(self, command, arg):
        r = self._run(command, arg)
        assert r.returncode == 0, r.stderr
        assert f"zonewiki {command}" in r.stdout + r.stderr
        assert "ZONE_DATA isn't set" not in r.stderr
        assert not re.search(r"^ +-[a-z]", r.stdout + r.stderr, re.M)

    @pytest.mark.parametrize("arg", ["version", "--version", "-version"])
    def test_version(self, arg):
        # The version and the build, as the start-up line names them, without a wiki.
        r = self._run(arg)
        assert r.returncode == 0, r.stderr
        assert re.fullmatch(r"Zonewiki \d+\.\d+\.\d+(-dev)? \(.+\)\n", r.stdout), r.stdout
        assert r.stderr == ""

    def test_licenses(self):
        # The binary carries the licenses of what it holds, also when passed on alone.
        r = self._run("licenses")
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith("==> LICENSE <==\n\n                    GNU AFFERO GENERAL PUBLIC LICENSE")
        for name in ("THIRD_PARTY_LICENSES", "public/vendor/katex/LICENSE", "public/vendor/mermaid/COPYING.GPL-3.0"):
            assert f"==> {name} <==" in r.stdout, name


class TestTheBinaryAlone:
    """The binary is all a machine needs: with no public/ and no docs beside it
    (the tests' binary is built into a folder of its own), it serves the browser
    code and Help from the copies built into it (files.go)."""

    def test_serves_its_own_browser_code_and_help(self, tmp_path):
        assert not (binary().parent / "public").exists() and not (binary().parent.parent / "public").exists()
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        env["ZONE_DATA"] = str(tmp_path)
        proc = subprocess.Popen([str(binary()), "--addr", "127.0.0.1:0"], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            line = proc.stdout.readline()
            assert " listening on " in line, line
            host, port = line.rsplit(" ", 1)[1].strip().rsplit(":", 1)
            conn = http.client.HTTPConnection(host, int(port), timeout=30)

            def get(path, **headers):
                conn.request("GET", path, headers={"Host": "localhost", **headers})
                resp = conn.getresponse()
                return resp, resp.read()

            public = settings.REPO / "public"
            resp, body = get("/public/style.css")
            assert resp.status == 200 and resp.getheader("Content-Type").startswith("text/css")
            assert body == (public / "style.css").read_bytes()
            resp, body = get("/public/vendor/katex/katex.min.css", **{"Accept-Encoding": "gzip"})
            assert resp.status == 200 and resp.getheader("Content-Encoding") == "gzip"
            assert gzip.decompress(body) == (public / "vendor" / "katex" / "katex.min.css").read_bytes()
            assert get("/public/vendor/")[0].status == 404
            resp, body = get("/help")
            assert resp.status == 200 and b"Zonewiki&#39;s manual" in body
            resp, body = get("/help/screenshots/page.png")
            assert resp.status == 200 and resp.getheader("Content-Type") == "image/png"
            resp, body = get("/page/index")
            assert resp.status == 200 and b'href="/public/style.css"' in body
        finally:
            proc.terminate()
            proc.communicate(timeout=30)


class TestLocale:
    """locale: a BCP 47 tag the browser formats dates with (data-locale)."""

    def test_on_the_body_when_set(self, client, monkeypatch):
        body = client.get("/page/index", headers=HOST).get_data(as_text=True)
        assert "data-locale" not in body and "data-date-format" not in body
        monkeypatch.setattr(settings, "LOCALE", "de-AT")
        monkeypatch.setattr(settings, "DATE_FORMAT", "iso")
        for url in ("/page/index", "/tasks", "/edit/index"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            assert 'data-locale="de-AT"' in body and 'data-date-format="iso"' in body, url


class TestNoshare:
    """Sharing mode leaves out the pages noshare names (#tags, folders/)."""

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
        client.set_cookie("zone-sharing", "1")
        for url in ("/page/bob", "/page/personal/diary", "/edit/bob", "/history/bob"):
            body = client.get(url, headers=HOST).get_data(as_text=True)
            assert "Hidden page" in body and "Secret" not in body, url
        assert client.get("/api/page-preview/bob", headers=HOST).status_code == 404
        assert "Garden" in client.get("/page/garden", headers=HOST).get_data(as_text=True)

    def test_left_out_of_search_and_lists(self, client, wiki_dir):
        self._wiki(wiki_dir)
        client.set_cookie("zone-sharing", "1")
        slugs = {r["slug"] for r in client.get("/api/search?q=garden", headers=HOST).get_json()}
        assert slugs == {"garden", "home2"}
        assert "Secret" not in client.get("/search?q=secret", headers=HOST).get_data(as_text=True)
        assert set(client.get("/api/pages", headers=HOST).get_json()) >= {"garden", "home2"}
        assert not {"bob", "personal/diary"} & set(client.get("/api/pages", headers=HOST).get_json())
        assert not {"bob", "personal/diary"} & {p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()}

    def test_links_to_them_become_plain_text(self, client, wiki_dir):
        self._wiki(wiki_dir)
        client.set_cookie("zone-sharing", "1")
        body = client.get("/page/home2", headers=HOST).get_data(as_text=True)
        assert '<span class="wikilink-hidden">Bob</span>' in body
        assert '<span class="wikilink-hidden">my diary</span>' in body
        assert 'href="/page/garden"' in body and 'href="/page/bob"' not in body
        # Nor do they show among the page's backlinks or links.
        assert "Bob Secret" not in body and "Diary Secret" not in body

    def test_quicknotes_too(self, client, wiki_dir):
        write_page(wiki_dir, "2026-10-01_09-00-00_salary", "---\ntags: noshare\n---\n# Salary talk\n")
        write_page(wiki_dir, "2026-10-01_10-00-00_lunch", "# Lunch\n")
        client.set_cookie("zone-sharing", "1")
        body = client.get("/notes", headers=HOST).get_data(as_text=True)
        assert "Lunch" in body and "Salary" not in body


# ===========================================================================
# Move a note to the wiki (/promote/<slug>)
# ===========================================================================

QUICKNOTE = "2026-10-08_14-03-11_meeting-notes"


class TestPromotePage:

    def test_quicknote_menu_has_move_to_wiki(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "# Meeting with Alex\n\nnotes\n")
        write_page(wiki_dir, "conversations/2026-07-08_debrief", "# Debrief\n")
        body = client.get(f"/page/{QUICKNOTE}", headers=HOST).get_data(as_text=True)
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
        write_page(wiki_dir, QUICKNOTE, "# Meeting with Alex\n\nnotes\n")
        ref = write_page(wiki_dir, "ref", f"# Ref\n\nSee [[{QUICKNOTE}]] and [x](/page/{QUICKNOTE}#a).\n")
        resp = client.post(f"/promote/{QUICKNOTE}", headers=HOST, data={
            "folder": "conversations", "name": "Alex sync", "keep_date": "1"})
        assert resp.headers["Location"] == "/page/conversations/2026-10-08_alex-sync"
        assert not (wiki_dir / f"{QUICKNOTE}.md").exists()
        moved = wiki_dir / "conversations" / "2026-10-08_alex-sync.md"
        assert moved.read_text(encoding="utf-8") == "# Meeting with Alex\n\nnotes\n"
        text = ref.read_text(encoding="utf-8")
        assert "[[conversations/2026-10-08_alex-sync]]" in text
        assert "(/page/conversations/2026-10-08_alex-sync#a)" in text
        # It left Notes list.
        body = client.get("/notes", headers=HOST).get_data(as_text=True)
        assert "Meeting with Alex" not in body

    def test_promote_refuses_and_says_why(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "# Notes\n")
        write_page(wiki_dir, "taken", "# Taken\n")
        for data, error in [({"name": "Taken"}, "already exists"),
                            ({"name": " "}, "Give the page a name"),
                            ({"name": "2026-01-01_10-00-00_x"}, "still be a note")]:
            resp = client.post(f"/promote/{QUICKNOTE}", data=data, headers=HOST)
            loc = resp.headers["Location"]
            assert loc.startswith(f"/page/{QUICKNOTE}?promote_error=")
            body = client.get(loc, headers=HOST).get_data(as_text=True)
            assert '<dialog id="page-promote-dialog" class="modal promote-modal" data-title="Notes" data-open>' in body
            assert error in body
        assert (wiki_dir / f"{QUICKNOTE}.md").exists()

    def test_promote_opens_the_page_to_link_from(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "# Alex sync\n")
        index = write_page(wiki_dir, "conversations/index", "# Conversations\n\n- [[conversations/old]]\n")
        body = client.get(f"/page/{QUICKNOTE}", headers=HOST).get_data(as_text=True)
        assert 'id="promote-link-from" list="wiki-pages"' in body
        assert '<option value="conversations/index">Conversations</option>' in body
        resp = client.post(f"/promote/{QUICKNOTE}", headers=HOST, data={
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
        write_page(wiki_dir, QUICKNOTE, "# Notes\n")
        hub = write_page(wiki_dir, "hub", f"# Hub\n\nSee [[{QUICKNOTE}]].\n")
        resp = client.post(f"/promote/{QUICKNOTE}", data={"name": "Notes", "link_from": "hub"}, headers=HOST)
        assert resp.headers["Location"] == "/page/notes"
        assert hub.read_text(encoding="utf-8") == "# Hub\n\nSee [[notes]].\n"

    def test_promote_refuses_an_unknown_link_page(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "# Notes\n")
        loc = client.post(f"/promote/{QUICKNOTE}", data={"name": "Notes", "link_from": "nowhere"},
                          headers=HOST).headers["Location"]
        assert loc.startswith(f"/page/{QUICKNOTE}?promote_error=")
        assert "There is no page “nowhere” to link from" in client.get(loc, headers=HOST).get_data(as_text=True)
        assert (wiki_dir / f"{QUICKNOTE}.md").exists() and not (wiki_dir / "notes.md").exists()

    def test_promote_only_quicknotes(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        assert client.post("/promote/garden", data={"name": "x"}, headers=HOST).status_code == 400
        assert client.post(f"/promote/{QUICKNOTE}", data={"name": "x"}, headers=HOST).status_code == 404


# ===========================================================================
# Append a note to a page (/append/<slug>)
# ===========================================================================


class TestAppendToPage:

    def test_menu_and_dialog(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "# Alex 1:1\n\nnotes\n")
        write_page(wiki_dir, "people/alex", "# Alex Morgan\n")
        body = client.get(f"/page/{QUICKNOTE}", headers=HOST).get_data(as_text=True)
        menu = body.split('id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert "Append to page…" in re.findall(r'role="menuitem"[^>]*>([^<]+)', menu)
        dialog = body.split('<dialog id="page-append-dialog"', 1)[1].split("</dialog>", 1)[0]
        pages = body.split('<datalist id="wiki-pages">', 1)[1].split("</datalist>", 1)[0]
        assert '<option value="people/alex">Alex Morgan</option>' in pages
        assert QUICKNOTE not in pages                                 # no notes offered
        assert 'list="wiki-pages"' in dialog
        assert 'id="append-heading" value="Alex 1:1 (2026-10-08)"' in dialog

    def test_append_moves_note_into_the_page(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "---\ntags: sync\n---\n# Alex 1:1\n\nnotes\n\n## Next\n\nsteps\n")
        page = write_page(wiki_dir, "people/alex", "# Alex\n\nAbout Alex.\n")
        ref = write_page(wiki_dir, "ref", f"See [[{QUICKNOTE}]], [[{QUICKNOTE}#Next|next]] "
                                          f"and [it](/page/{QUICKNOTE}).\n")
        resp = client.post(f"/append/{QUICKNOTE}", headers=HOST,
                           data={"page": "/page/people/alex", "heading": "Alex 1:1 (2026-10-08)"})
        assert resp.headers["Location"] == "/page/people/alex#alex-11-2026-10-08"
        assert not (wiki_dir / f"{QUICKNOTE}.md").exists()
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
        write_page(wiki_dir, QUICKNOTE, "# Notes\n\nx\n")
        write_page(wiki_dir, "garden", "# Garden\n\n## Notes (2026-10-08)\n")
        for data, error in [({"page": "nowhere", "heading": "H"}, "There is no page"),
                            ({"page": "", "heading": "H"}, "Choose a page"),
                            ({"page": QUICKNOTE, "heading": "H"}, "another page than this note"),
                            ({"page": "garden", "heading": " | "}, "Give the section a heading"),
                            ({"page": "garden", "heading": "Notes (2026-10-08)"}, "already has a section")]:
            loc = client.post(f"/append/{QUICKNOTE}", data=data, headers=HOST).headers["Location"]
            assert loc.startswith(f"/page/{QUICKNOTE}?append_error=")
            body = client.get(loc, headers=HOST).get_data(as_text=True)
            assert '<dialog id="page-append-dialog" class="modal promote-modal" data-open>' in body
            assert error in body
        assert (wiki_dir / f"{QUICKNOTE}.md").exists()
        assert (wiki_dir / "garden.md").read_text(encoding="utf-8") == "# Garden\n\n## Notes (2026-10-08)\n"

    def test_append_refuses_a_heading_with_code_it_has(self, client, wiki_dir):
        # The page's heading counts with its code as written, not masked.
        write_page(wiki_dir, QUICKNOTE, "# Notes\n\nx\n")
        write_page(wiki_dir, "garden", "# Garden\n\n## Run `make` now\n")
        loc = client.post(f"/append/{QUICKNOTE}", data={"page": "garden", "heading": "Run `make` now"},
                          headers=HOST).headers["Location"]
        assert "already has a section" in client.get(loc, headers=HOST).get_data(as_text=True)
        assert (wiki_dir / "garden.md").read_text(encoding="utf-8") == "# Garden\n\n## Run `make` now\n"

    def test_append_only_quicknotes(self, client, wiki_dir):
        write_page(wiki_dir, "garden", "# Garden\n")
        write_page(wiki_dir, "other", "# Other\n")
        assert client.post("/append/garden", data={"page": "other", "heading": "H"},
                           headers=HOST).status_code == 400

    def test_append_points_tasks_links_at_the_section(self, client, wiki_dir):
        write_page(wiki_dir, QUICKNOTE, "# Notes\n")
        write_page(wiki_dir, "garden", "# Garden\n")
        client.post("/api/tasks", json={"title": f"Follow up [[{QUICKNOTE}]]"}, headers=HOST)
        client.post(f"/append/{QUICKNOTE}", data={"page": "garden", "heading": "Notes"}, headers=HOST)
        titles = [t["title"] for t in client.get("/api/tasks", headers=HOST).get_json()]
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


def test_vendored_libraries_are_cached_and_gzipped(client, wiki_dir):
    path = "/public/vendor/mermaid/mermaid.min.js"
    raw = (Path(__file__).parent.parent / "public/vendor/mermaid/mermaid.min.js").read_bytes()
    r = client.get(path, headers={**HOST, "Accept-Encoding": "br, gzip;q=0.8"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert r.headers["Content-Type"].startswith("text/javascript")
    assert "max-age=86400" in r.headers["Cache-Control"] and r.headers["Vary"] == "Accept-Encoding"
    assert len(r.data) < len(raw) / 2 and gzip.decompress(r.data) == raw
    plain = client.get(path, headers={**HOST, "Accept-Encoding": "gzip;q=0"})
    assert "Content-Encoding" not in plain.headers and plain.data == raw
    # Zonewiki's own code isn't kept: it changes with every edit.
    own = client.get("/public/js/figures.js", headers={**HOST, "Accept-Encoding": "gzip"})
    assert own.status_code == 200 and "Cache-Control" not in own.headers
    assert client.get("/public/vendor/nope.js", headers={**HOST, "Accept-Encoding": "gzip"}).status_code == 404


def test_links_in_code_and_math_are_no_backlinks(client, wiki_dir):
    # They show as text, so they count as no link (and a rename leaves them).
    write_page(wiki_dir, "target", "# Target\n")
    write_page(wiki_dir, "code", "# Code\n\n`[[target]]` and\n\n```\n[See](target)\n```\n")
    write_page(wiki_dir, "maths", "# Maths\n\nThe interval $[[target]]$.\n")
    write_page(wiki_dir, "linker", "# Linker\n\nSee [[target]].\n")
    assert backlinks(client, "target") == ["linker"]

