"""Tests for the [[tasks:]] embed (its HTML, data attributes) and the page
field tasks no longer have. Its params' parsing has a Go test."""

import re
from datetime import date

from tests.conftest import HOST, create_task, preview, write_page


# ── Rendering ───────────────────────────────────────────────────────────────────


class TestTasksLinkRender:
    def test_basic_embed_no_tasks(self, client, wiki_dir):
        """[[tasks:]] renders the full task app embed."""
        html = preview(client, "[[tasks:]]\n")
        assert "tasks-app" in html
        assert "tasks-list" in html

    def test_eleven_embeds_each_render_their_own(self, client, wiki_dir):
        # Regression: placeholder _1 is a prefix of _10, so the 11th embed
        # was replaced by (part of) the 2nd one's HTML.
        html = preview(client, "\n\n".join(f"[[tasks: tag=t{i}]]" for i in range(11)) + "\n")
        assert "LEAFTASKSPLACEHOLDER" not in html
        for i in range(11):
            sfx = f"-{i}" if i else ""
            assert html.count(f'id="tasks-app{sfx}"') == 1
            assert f'id="tasks-app{sfx}" data-init-tag="t{i}"' in html

    def test_no_list_field_or_show_lists_checkbox(self, client, wiki_dir):
        html = preview(client, "[[tasks:]]\n[[tasks:]]\n")
        assert "task-show-lists" not in html
        assert 'id="task-edit-list' not in html and "task-edit-page" not in html

    def test_density_button(self, client, wiki_dir):
        """Every task list has the switch between one line per task and full cards."""
        html = preview(client, "[[tasks:]]\n")
        assert 'class="btn tasks-density-btn" id="task-density-btn"' in html and 'title="One line per task (v)"' in html

    def test_embed_with_tag_filter(self, client, wiki_dir):
        """[[tasks: tag=work]] passes tag as data attribute."""
        create_task(client, title="Write code", tags=["work"])
        html = preview(client, "[[tasks: tag=work]]\n")
        assert 'data-init-tag="work"' in html
        assert "tasks-app" in html

    def test_embed_with_multi_tag_filter(self, client, wiki_dir):
        """[[tasks: tag=a,b]] passes comma-separated tags as data attribute."""
        html = preview(client, "[[tasks: tag=q3-launch,BUG-10002]]\n")
        assert 'data-init-tag="q3-launch,BUG-10002"' in html

    def test_embed_view_param(self, client, wiki_dir):
        """[[tasks: view=…]] passes the view as a data attribute, any case."""
        for name in ("focus", "open", "plan", "completed"):
            html = preview(client, f"[[tasks: view={name.upper()}]]\n")
            assert f'data-init-view="{name}"' in html, name

    def test_view_tabs_are_a_segmented_control_in_order(self, client, wiki_dir):
        html = preview(client, "[[tasks:]]\n")
        assert 'class="view-tabs" id="tasks-tabs" role="tablist"' in html
        tabs = re.findall(r'<button type="button" role="tab" data-view="(\w+)"[^>]*>([^<]+)</button>', html)
        assert tabs == [("open", "Open"), ("focus", "Focus")]
        # Completed is a link under the list, not a tab.
        assert 'id="task-completed-link"' in html

    def test_embed_unknown_view_is_the_default(self, client, wiki_dir):
        """A name that isn't a view (also the tabs' old names, and `filter=`,
        the old name of `view=`) opens the default view."""
        for directive in ("view=bogus", "view=due", "view=backlog", "view=today",
                          "view=all", "view=upcoming", "view=done", "filter=plan"):
            html = preview(client, f"[[tasks: {directive}]]\n")
            assert 'data-init-view=""' in html, directive

    def test_multiple_embeds_only_first(self, client, wiki_dir):
        """Multiple [[tasks:]] on one page: all render with unique instance IDs."""
        create_task(client, title="A", tags=["alpha"])
        md = "## Alpha\n\n[[tasks: tag=alpha]]\n\n## Beta\n\n[[tasks: tag=beta]]\n"
        html = preview(client, md)
        # Both embeds should be present with unique IDs
        assert 'id="tasks-app"' in html
        assert 'id="tasks-app-1"' in html
        assert 'data-init-tag="alpha"' in html
        assert 'data-init-tag="beta"' in html

    def test_multiple_embeds_have_independent_element_ids(self, client, wiki_dir):
        """Each embed must expose its own suffixed list/tabs/modal IDs so the
        tasks.js instance registry can address and refresh each independently
        (cross-instance refresh relies on this)."""
        create_task(client, title="A", tags=["alpha"])
        md = "[[tasks: tag=alpha]]\n\n[[tasks: tag=beta]]\n"
        html = preview(client, md)
        # Instance 0 uses no suffix; instance 1 uses "-1".
        for base in ("tasks-list", "tasks-tabs", "task-edit-modal"):
            assert f'id="{base}"' in html, f"missing {base} for instance 0"
            assert f'id="{base}-1"' in html, f"missing {base}-1 for instance 1"
        # Distinct instance markers the registry keys on.
        assert 'data-instance="0"' in html
        assert 'data-instance="1"' in html

    def test_embed_not_inside_paragraph(self, client, wiki_dir):
        """[[tasks:]] on its own line should NOT be wrapped in <p>."""
        create_task(client, title="T1")
        html = preview(client, "[[tasks:]]\n")
        assert "<p>" not in html.split('id="tasks-app"')[0].split('\n')[-1]

    def test_embed_case_insensitive(self, client, wiki_dir):
        """[[Tasks:]] and [[TASKS:]] work too, and so does no space after the colon."""
        for variant in ["[[Tasks:]]\n", "[[TASKS: tag=work]]\n", "[[tasks:tag=work]]\n"]:
            html = preview(client, variant)
            assert "tasks-app" in html, variant
        assert 'data-init-tag="work"' in preview(client, "[[tasks:tag=work]]\n")

    def test_the_old_deck_syntax_still_embeds(self, client, wiki_dir):
        """[[deck …]] is how pages wrote it before Deck was called Tasks."""
        for variant in ["[[deck]]\n", "[[Deck]]\n", "[[DECK]]\n", "[[deck list=shop view=focus]]\n"]:
            assert "tasks-app" in preview(client, variant), variant
        html = preview(client, "[[deck list=shop view=focus]]\n")
        assert 'data-init-list="shop"' in html and 'data-init-view="focus"' in html

    def test_a_link_to_a_page_called_tasks_stays_a_link(self, client, wiki_dir):
        """The colon is what makes the embed: [[Tasks]] links to the page Tasks."""
        write_page(wiki_dir, "tasks", "# Tasks\n")
        for variant in ["[[Tasks]]\n", "[[tasks]]\n", "[[Tasks|my list]]\n", "[[decks]]\n", "[[deckchair]]\n"]:
            html = preview(client, variant)
            assert "tasks-app" not in html and "wikilink" in html, variant

    def test_embed_works_inline(self, client):
        """[[tasks:]] inline is also replaced (renders embed)."""
        html = preview(client, "Here are the [[tasks:]] for today.\n")
        assert "tasks-app" in html

    def test_embed_works_in_list(self, client):
        """[[tasks:]] inside a list item renders embed."""
        html = preview(client, "- [[tasks: tag=work]]\n")
        assert "tasks-app" in html
        assert 'data-init-tag="work"' in html

    def test_embed_page_param_is_ignored(self, client, wiki_dir):
        """page= was removed: it no longer narrows the embed."""
        html = preview(client, "[[tasks: page=my-project]]\n")
        assert "data-init-page" not in html


# ── No page field (a wikilink in the notes links a page) ─────────────────────


class TestNoPageField:
    def test_page_in_request_is_ignored(self, client, wiki_dir):
        data = create_task(client, title="T", page="my-project").get_json()
        assert "page" not in data
        resp = client.put(f"/api/tasks/{data['id']}", json={"page": "x"})
        assert resp.status_code == 200 and "page" not in resp.get_json()

# ── Page view rendering ────────────────────────────────────────────────────────


class TestPageViewLink:
    def test_page_with_tasks_embed(self, client, wiki_dir):
        """A wiki page with [[tasks:]] renders the full task app."""
        create_task(client, title="Page task", tags=["test"])
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\nSome text.\n\n[[tasks: tag=test]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body
        assert 'data-init-tag="test"' in body

    def test_preview_with_tasks_embed(self, client, wiki_dir):
        """Preview endpoint renders [[tasks:]] as the full embed."""
        create_task(client, title="Preview task")
        resp = client.post(
            "/api/preview",
            data="[[tasks:]]\n",
            content_type="text/plain"
        )
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body

    def test_tasks_embed_with_params(self, client, wiki_dir):
        """[[tasks: tag=work list=proj view=plan]] passes all data attrs."""
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\n[[tasks: tag=work list=proj view=plan]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        body = resp.get_data(as_text=True)
        assert 'data-init-tag="work"' in body
        assert 'data-init-list="proj"' in body
        assert 'data-init-view="plan"' in body

    def test_embed_has_single_filter_field(self, client, wiki_dir):
        html = preview(client, "[[tasks:]]\n")
        assert 'id="task-filter-search"' in html
        assert "task-filter-tag" not in html

    def test_embed_contains_start_date_field(self, client, wiki_dir):
        """Edit modal has a Start (start_date) field and an unstored
        days-before-due helper; the old lead/anchor fields are gone."""
        html = preview(client, "[[tasks:]]\n")
        assert 'id="task-edit-start"' in html
        assert 'name="start_date"' in html
        assert 'data-date-trigger="task-edit-start"' in html
        assert 'id="task-edit-lead"' in html
        assert "defer_days" not in html and "focus_date" not in html
        assert "rec-anchor" not in html


# ── The list switcher ───────────────────────────────────────────────────────────


def places(html):
    """The list switcher's places: (key, label, current) each, [] without one."""
    if 'class="tasks-places"' not in html:
        return []
    nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
    return [(k, label, cur == ' aria-current="page"') for k, cur, label in re.findall(
        r'data-place="([^"]+)"[^>]*?( aria-current="page")?>.*?<span class="tasks-place-label">([^<]*)</span>', nav, re.S)]


class TestListSwitcher:
    """A place of the tasks (the Tasks home, a list's home, /tasks) opens with
    the list switcher: the home, the lists A–Z, All."""

    @staticmethod
    def wiki(wiki_dir):
        write_page(wiki_dir, "tasks", "# My tasks\n\n[[tasks: list=task view=focus home=1]]\n")
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n")
        write_page(wiki_dir, "gifts", "# Gifts\n\n[[tasks: list=gifts]]\n")

    def get(self, client, url):
        return client.get(url, headers=HOST).get_data(as_text=True)

    def test_nothing_to_switch_between_shows_no_switcher(self, client, wiki_dir):
        write_page(wiki_dir, "work", "# Work\n\n[[tasks: tag=work]]\n")
        assert places(self.get(client, "/tasks")) == []
        assert places(self.get(client, "/page/work")) == []

    def test_the_home_the_lists_and_all(self, client, wiki_dir):
        self.wiki(wiki_dir)
        html = self.get(client, "/page/tasks")
        # The home's own list (task) is the home, not an entry of its own.
        assert places(html) == [("home", "My tasks", True), ("list:gifts", "gifts", False),
                                ("list:shop", "shop", False), ("all", "All", False)]
        nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        # The home opens its page; a list /tasks?list= (its home's view, and
        # the pages that embed it).
        assert 'href="/page/tasks"' in nav and 'href="/tasks?list=shop"' in nav and 'href="/tasks"' in nav
        assert 'title="List: Shopping"' in nav and 'title="Your Tasks home"' in nav
        assert "tasks-place-lock" not in nav   # without users nothing is private
        # It opens the task app: before it, once.
        assert html.count('class="tasks-places"') == 1
        assert html.index('class="tasks-places"') < html.index('class="tasks-app"')

    def test_a_list_home_and_tasks_mark_their_place(self, client, wiki_dir):
        self.wiki(wiki_dir)
        current = lambda url: [k for k, _, cur in places(self.get(client, url)) if cur]
        assert current("/page/shop") == ["list:shop"]
        assert current("/tasks") == ["all"]
        assert current("/tasks?list=gifts") == ["list:gifts"]
        assert current("/tasks?list=task") == ["home"]
        # A tag no page declares: the switcher, nothing current.
        html = self.get(client, "/tasks?list=nothing")
        assert places(html) and current("/tasks?list=nothing") == []

    def test_a_view_on_a_page_is_no_place(self, client, wiki_dir):
        # A list's embed on a page that isn't its home, a tag= view, a second
        # embed of a home: views, not places, so no switcher.
        self.wiki(wiki_dir)
        write_page(wiki_dir, "zz-more", "# More\n\n[[tasks: list=shop]]\n\n[[tasks: tag=urgent]]\n")
        assert places(self.get(client, "/page/zz-more")) == []
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n\n[[tasks: list=shop view=open]]\n")
        assert self.get(client, "/page/shop").count('class="tasks-places"') == 1
        # A page with home=1 that isn't the home (the first A–Z is).
        write_page(wiki_dir, "zz-home", "# Other home\n\n[[tasks: home=1]]\n")
        assert places(self.get(client, "/page/zz-home")) == []

    def test_the_editors_preview_shows_it_as_the_page_will(self, client, wiki_dir):
        self.wiki(wiki_dir)
        html = client.post("/api/preview?page=shop", data=b"[[tasks: list=shop]]\n", headers=HOST).get_data(as_text=True)
        assert [k for k, _, cur in places(html) if cur] == ["list:shop"]
        assert places(preview(client, "[[tasks: list=shop]]\n")) == []   # no page: no place

    def test_each_place_has_its_counts(self, client, wiki_dir):
        self.wiki(wiki_dir)
        today = date.today().isoformat()
        create_task(client, title="Milk", tags=["shop"], start_date=today)
        create_task(client, title="Bread", tags=["shop"], due_date=today)
        create_task(client, title="Report", tags=["task"], due_date=today)
        create_task(client, title="Elsewhere", start_date=today)
        nav = self.get(client, "/page/shop").split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        counts = dict(re.findall(r'data-place="([^"]+)".*?<span class="module-counts">(.*?)</span></a>', nav, re.S))
        due = lambda k: re.search(r'class="module-due"[^>]*>(\d*)<', counts[k]).group(1)
        focus = lambda k: re.search(r'class="module-focus"[^>]*>(\d*)<', counts[k]).group(1)
        assert (due("list:shop"), focus("list:shop")) == ("1", "2")
        assert (due("home"), focus("home")) == ("1", "1")
        assert (due("list:gifts"), focus("list:gifts")) == ("", "")
        assert (due("all"), focus("all")) == ("2", "4")
        # js/chrome.js refreshes them from the due count's places.
        data = client.get("/api/tasks/due-count?places=1").get_json()
        assert data["count"] == 1 and data["focus"] == 1   # the home's, as before
        assert {k: (v["count"], v["focus"]) for k, v in data["places"].items()} == {
            "home": (1, 1), "list:shop": (1, 2), "list:gifts": (0, 0), "all": (2, 4)}
        assert "places" not in client.get("/api/tasks/due-count").get_json()

    def test_sharing_mode_leaves_out_a_list_not_for_sharing(self, client, wiki_dir):
        self.wiki(wiki_dir)
        write_page(wiki_dir, "gifts", "---\ntags: noshare\n---\n# Gifts\n\n[[tasks: list=gifts]]\n")
        assert "list:gifts" in [k for k, _, _ in places(self.get(client, "/tasks"))]
        client.set_cookie("zone-sharing", "1")
        assert "list:gifts" not in [k for k, _, _ in places(self.get(client, "/tasks"))]


class TestListBacklinks:
    """/tasks?list= lists the pages that embed the list as its Backlinks."""

    def test_the_pages_embedding_a_list(self, client, wiki_dir):
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n")
        write_page(wiki_dir, "kitchen", "# Kitchen\n\n[[tasks: list=Shop view=open]]\n")
        write_page(wiki_dir, "code", "# Code\n\n```\n[[tasks: list=shop]]\n```\n")
        write_page(wiki_dir, "work", "# Work\n\n[[tasks: tag=shop]]\n")
        write_page(wiki_dir, "links", "# Links\n\n[[shop]]\n")
        html = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        foot = html.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        assert 'id="backlinks"' in foot
        assert re.findall(r'<li><a href="([^"]+)">([^<]*)</a>', foot) == [("/page/kitchen", "Kitchen"), ("/page/shop", "Shopping")]
        # The right column's copy and the breadcrumb's chip, as on a page.
        assert 'class="rail rail-context"' in html and 'data-id="backlinks"' in html
        assert '<a href="#backlinks" class="bl-count">← 2</a>' in html
        assert '<main class="wide">' in html

    def test_no_list_no_backlinks(self, client, wiki_dir):
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop]]\n")
        for url in ("/tasks", "/tasks?list=nothing"):
            html = client.get(url, headers=HOST).get_data(as_text=True)
            assert 'id="backlinks"' not in html and "bl-count" not in html, url

    def test_sharing_mode_leaves_out_a_page_not_for_sharing(self, client, wiki_dir):
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop]]\n")
        # zz-: not the list's home (the first A–Z), only a page embedding it.
        write_page(wiki_dir, "zz-secret", "---\ntags: noshare\n---\n# Secret\n\n[[tasks: list=shop]]\n")
        assert "/page/zz-secret" in client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        client.set_cookie("zone-sharing", "1")
        html = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        assert "/page/zz-secret" not in html and "/page/shop" in html
