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
        assert "ZONETASKSPLACEHOLDER" not in html
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

    def test_a_link_to_a_page_called_tasks_stays_a_link(self, client, wiki_dir):
        """The colon is what makes the embed: [[Tasks]] links to the page Tasks."""
        write_page(wiki_dir, "tasks", "# Tasks\n")
        for variant in ["[[Tasks]]\n", "[[tasks]]\n", "[[Tasks|my list]]\n", "[[tasksheet]]\n"]:
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
    """/tasks opens with the list switcher, above its heading: the Tasks
    pinned lists, the other lists A–Z, All; pages that embed a list show none."""

    @staticmethod
    def wiki(wiki_dir):
        write_page(wiki_dir, "tasks", "# My tasks\n\n[[tasks: list=task view=focus pin=1]]\n")
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n")
        write_page(wiki_dir, "gifts", "# Gifts\n\n[[tasks: list=gifts]]\n")

    def get(self, client, url):
        return client.get(url, headers=HOST).get_data(as_text=True)

    def current(self, client, url):
        return [k for k, _, cur in places(self.get(client, url)) if cur]

    def test_nothing_to_switch_between_shows_no_switcher(self, client, wiki_dir):
        write_page(wiki_dir, "work", "# Work\n\n[[tasks: tag=work]]\n")
        assert places(self.get(client, "/tasks")) == []

    def test_the_lists_and_all_above_the_heading(self, client, wiki_dir):
        self.wiki(wiki_dir)
        html = self.get(client, "/tasks?list=shop")
        # Lists by their names, not their pages': the pinned one first.
        assert places(html) == [("list:task", "task", False), ("list:gifts", "gifts", False),
                                ("list:shop", "shop", True), ("all", "All", False)]
        nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'href="([^"]+)"', nav) == ["/tasks?list=task", "/tasks?list=gifts", "/tasks?list=shop", "/tasks"]
        assert 'title="List #shop"' in nav and 'title="Pinned list #task"' in nav
        # The pinned list marked with a pin; the others not.
        assert re.findall(r'data-place="([^"]+)"[^>]*>(?:<span class="tasks-place-pin")', nav) == ["list:task"]
        assert "tasks-place-lock" not in nav   # without users nothing is private
        # Above the heading, which names the list.
        assert html.count('class="tasks-places"') == 1
        assert html.index('class="tasks-places"') < html.index("<h1>List: shop</h1>") < html.index('class="tasks-app"')
        assert "<title>List: shop" in html

    def test_all_tasks_and_the_pinned_list(self, client, wiki_dir):
        self.wiki(wiki_dir)
        assert self.current(client, "/tasks") == ["all"]
        assert "<h1>All tasks</h1>" in self.get(client, "/tasks")
        assert self.current(client, "/tasks?list=task") == ["list:task"]
        # A tag no page declares: the switcher, nothing current.
        html = self.get(client, "/tasks?list=nothing")
        assert places(html) and self.current(client, "/tasks?list=nothing") == []
        assert "<h1>List: nothing</h1>" in html and "No page declares" in html

    def test_pages_show_no_switcher(self, client, wiki_dir):
        # A page that embeds a list shows the list; the switcher is /tasks's.
        self.wiki(wiki_dir)
        for slug in ("tasks", "shop"):
            assert places(self.get(client, f"/page/{slug}")) == [], slug
        html = client.post("/api/preview?page=shop", data=b"[[tasks: list=shop]]\n", headers=HOST).get_data(as_text=True)
        assert places(html) == []

    def test_a_pinned_list_declared_elsewhere_too_is_one_place(self, client, wiki_dir):
        # Another page declaring the pinned list (here first A–Z, so the
        # list's home): still one pill, named by the list, with the pinned
        # embed's view; no page title on /tasks?list=.
        self.wiki(wiki_dir)
        write_page(wiki_dir, "aaa-scratch", "# Scratch\n\n[[tasks: list=task]]\n")
        html = self.get(client, "/tasks?list=task")
        assert [k for k, _, _ in places(html)] == ["list:task", "list:gifts", "list:shop", "all"]
        assert self.current(client, "/tasks?list=task") == ["list:task"]
        assert 'data-init-view="focus"' in html   # the pinned embed's view=focus
        assert "A separate list" not in html and "tasks-list-home" not in html
        foot = html.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'<li><a href="([^"]+)">', foot) == ["/page/tasks", "/page/aaa-scratch"]

    def test_a_pin_without_a_list_is_pinned(self, client, wiki_dir):
        write_page(wiki_dir, "tasks", "# My tasks\n\n[[tasks: tag=me pin=1]]\n")
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop]]\n")
        html = self.get(client, "/tasks")
        assert places(html) == [("pinned", "Pinned", False), ("list:shop", "shop", False), ("all", "All", True)]
        assert 'href="/page/tasks" class="tasks-place" data-place="pinned" title="Your pinned tasks"' in html

    def test_several_pinned_lists_are_one_view(self, client, wiki_dir):
        # Two pinned lists: Pinned first (the Tasks link's target), then both
        # lists with a pin, the default one (pin=default) first; /tasks?pinned=1
        # shows both as one task app, whose New gives the default list's tags.
        # The Tasks link counts both, a task in both once.
        self.wiki(wiki_dir)
        write_page(wiki_dir, "tasks", "# My tasks\n\n[[tasks: list=task view=focus pin=default]]\n")
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1 pin=1]]\n")
        today = date.today().isoformat()
        create_task(client, title="Milk", tags=["shop"], due_date=today)
        create_task(client, title="Report", tags=["task"], start_date=today)
        create_task(client, title="Both", tags=["task", "shop"], due_date=today)
        create_task(client, title="Gift", tags=["gifts"], due_date=today)
        html = self.get(client, "/tasks?pinned=1")
        assert places(html) == [("pinned", "Pinned", True), ("list:task", "task", False), ("list:shop", "shop", False),
                                ("list:gifts", "gifts", False), ("all", "All", False)]
        nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'data-place="([^"]+)"[^>]*>(?:<span class="tasks-place-pin")', nav) == ["pinned", "list:task", "list:shop"]
        assert "<h1>Pinned</h1>" in html and "<title>Pinned" in html
        # Under the heading: the lists it shows, each a link to the list.
        line = html.split('class="tasks-list-home tasks-pinned-lists">', 1)[1].split("</p>", 1)[0]
        assert re.findall(r'<a href="([^"]+)">([^<]*)</a>', line) == [("/tasks?list=task", "#task"), ("/tasks?list=shop", "#shop")]
        # One task app: either list's tasks, the default list's view, its tags for new tasks.
        assert html.count('class="tasks-app"') == 1
        assert 'data-init-tag="task|shop" data-init-list=""' in html and 'data-init-view="focus"' in html
        assert 'data-pinned="1" data-new-tags="task"' in html
        # The Tasks link goes there and counts the pinned lists, a task in both once.
        body = self.get(client, "/page/index")
        assert 'href="/tasks?pinned=1" class="module-link" data-module="tasks" data-pinned-pages="tasks shop"' in body
        assert 'data-tasks-tags="task"' in body   # c t: the default list
        data = client.get("/api/tasks/due-count?places=1").get_json()
        assert (data["count"], data["focus"]) == (2, 3)
        assert {k: (v["count"], v["focus"]) for k, v in data["places"].items()} == {
            "pinned": (2, 3), "list:task": (1, 2), "list:shop": (2, 2), "list:gifts": (1, 1), "all": (3, 4)}
        # Without pin=default the first by name is the default: shop.
        write_page(wiki_dir, "tasks", "# My tasks\n\n[[tasks: list=task view=focus pin=1]]\n")
        assert [k for k, _, _ in places(self.get(client, "/tasks"))][:3] == ["pinned", "list:shop", "list:task"]
        # With one pin, ?pinned=1 goes where the Tasks link does.
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n")
        resp = client.get("/tasks?pinned=1", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/tasks?list=task"

    def test_each_place_has_its_counts(self, client, wiki_dir):
        self.wiki(wiki_dir)
        today = date.today().isoformat()
        create_task(client, title="Milk", tags=["shop"], start_date=today)
        create_task(client, title="Bread", tags=["shop"], due_date=today)
        create_task(client, title="Report", tags=["task"], due_date=today)
        create_task(client, title="Elsewhere", start_date=today)
        nav = self.get(client, "/tasks").split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        counts = dict(re.findall(r'data-place="([^"]+)".*?<span class="module-counts">(.*?)</span></a>', nav, re.S))
        due = lambda k: re.search(r'class="module-due"[^>]*>(\d*)<', counts[k]).group(1)
        focus = lambda k: re.search(r'class="module-focus"[^>]*>(\d*)<', counts[k]).group(1)
        assert (due("list:shop"), focus("list:shop")) == ("1", "2")
        assert (due("list:task"), focus("list:task")) == ("1", "1")
        assert (due("list:gifts"), focus("list:gifts")) == ("", "")
        assert (due("all"), focus("all")) == ("2", "4")
        # js/chrome.js refreshes them from the due count's places.
        data = client.get("/api/tasks/due-count?places=1").get_json()
        assert data["count"] == 1 and data["focus"] == 1   # the pinned list's, as before
        assert {k: (v["count"], v["focus"]) for k, v in data["places"].items()} == {
            "list:task": (1, 1), "list:shop": (1, 2), "list:gifts": (0, 0), "all": (2, 4)}
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

    def test_pinned_has_the_pages_embedding_its_lists(self, client, wiki_dir):
        # Pinned: the pages that embed any of the pinned lists, once each; a
        # pin without a list is named by its page, which is a backlink too.
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop pin=1]]\n")
        write_page(wiki_dir, "kitchen", "# Kitchen\n\n[[tasks: list=shop view=open]]\n\n[[tasks: list=work]]\n")
        write_page(wiki_dir, "office", "# Work\n\n[[tasks: list=work pin=default]]\n")
        write_page(wiki_dir, "me", "# Mine\n\n[[tasks: tag=me pin=1]]\n")
        write_page(wiki_dir, "gifts", "# Gifts\n\n[[tasks: list=gifts]]\n")
        html = client.get("/tasks?pinned=1", headers=HOST).get_data(as_text=True)
        line = html.split('class="tasks-list-home tasks-pinned-lists">', 1)[1].split("</p>", 1)[0]
        assert re.findall(r'<a href="([^"]+)">([^<]*)</a>', line) == [
            ("/tasks?list=work", "#work"), ("/page/me", "Mine"), ("/tasks?list=shop", "#shop")]
        foot = html.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'<li><a href="([^"]+)">([^<]*)</a>', foot) == [
            ("/page/kitchen", "Kitchen"), ("/page/me", "Mine"), ("/page/shop", "Shopping"), ("/page/office", "Work")]
        assert '<a href="#backlinks" class="bl-count">← 4</a>' in html and '<main class="wide">' in html
        # A list's own view names no lists.
        assert "tasks-pinned-lists" not in client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)

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
