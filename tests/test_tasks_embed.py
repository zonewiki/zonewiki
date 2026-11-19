"""Tests for the [[tasks:]] embed (its HTML, data attributes) and the page
field tasks no longer have. Its params' parsing has a Go test."""

import json
import re
from datetime import date

from tests.conftest import HOST, create_task, list_page, make_list, preview, write_page


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

    def test_the_dialog_has_a_list_field_and_no_show_lists_checkbox(self, client, wiki_dir):
        html = preview(client, "[[tasks:]]\n[[tasks:]]\n")
        assert "task-show-lists" not in html and "task-edit-page" not in html
        assert 'id="task-edit-list"' in html and 'id="task-edit-list-1"' in html and "task-edit-private" not in html

    def test_no_density_button(self, client, wiki_dir):
        """One line per task is the only way a list shows: the toolbar has no button to switch."""
        html = preview(client, "[[tasks:]]\n")
        assert "density" not in html

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
        for name in ("today", "open", "closed"):
            html = preview(client, f"[[tasks: view={name.upper()}]]\n")
            assert f'data-init-view="{name}"' in html, name

    def test_view_tabs_are_a_segmented_control_in_order(self, client, wiki_dir):
        html = preview(client, "[[tasks:]]\n")
        assert 'class="view-tabs" id="tasks-tabs" role="tablist"' in html
        tabs = re.findall(r'<button type="button" role="tab" data-view="(\w+)"[^>]*>([^<]+)</button>', html)
        assert tabs == [("open", "Open"), ("focus", "Today")]
        # Completed is a link under the list, not a tab.
        assert 'id="task-closed-link"' in html

    def test_embed_unknown_view_is_the_default(self, client, wiki_dir):
        """A name that isn't a view (also the tabs' old names, and `filter=`,
        the old name of `view=`) opens the default view."""
        for directive in ("view=bogus", "view=due", "view=backlog", "view=focus", "view=plan",
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
        """[[tasks: tag=work list=proj view=open group=person]] passes all data attrs."""
        make_list(client, "proj", headers=HOST)
        (wiki_dir / "my-page.md").write_text(
            "# My Page\n\n[[tasks: tag=work list=proj view=open group=person]]\n", encoding="utf-8"
        )
        resp = client.get("/page/my-page")
        body = resp.get_data(as_text=True)
        assert 'data-init-tag="work"' in body
        assert 'data-init-list="proj"' in body
        assert 'data-init-view="open"' in body and 'data-init-group="person"' in body   # the page's way to look at the list

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


def git_log(wiki_dir):
    """The wiki's commits' subjects, the latest first."""
    import subprocess
    return subprocess.run(["git", "-C", str(wiki_dir), "log", "--format=%s"], capture_output=True, text=True, check=True).stdout.splitlines()


def places(html):
    """The list switcher's pills (My tasks and the pinned lists): (key, label,
    current) each, [] without a switcher."""
    if 'class="tasks-places"' not in html:
        return []
    nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0].split("<details", 1)[0]
    return [(k, label, cur == ' aria-current="page"') for k, cur, label in re.findall(
        r'data-place="([^"]+)"[^>]*?( aria-current="page")?>.*?<span class="tasks-place-label">([^<]*)</span>', nav, re.S)]


def more(html):
    """The switcher's More: (its pill's label, whether it is the current
    one, the links in its panel as (href, name, current)); None without a
    switcher."""
    if 'class="tasks-places"' not in html:
        return None
    details = html.split('<details class="lists-more">', 1)[1].split("</details>", 1)[0]
    summary, panel = details.split("</summary>", 1)
    label = re.search(r'<span class="tasks-place-label">([^<]*)</span>', summary).group(1)
    links = [(href, name, cur == ' aria-current="page"') for href, cur, name in re.findall(
        r'<a href="([^"]+)" class="lists-more-item[^>]*?( aria-current="page")?>.*?<span class="lists-more-name">([^<]*)</span>', panel, re.S)]
    return label, 'aria-current="page"' in summary, links


class TestListSwitcher:
    """/tasks opens with the list switcher, above its heading: My tasks, the
    Inbox and the pinned lists as pills, the other lists A–Z and All tasks
    under More; pages that embed a list show none."""

    @staticmethod
    def wiki(client, wiki_dir):
        list_page(client, wiki_dir, "tasks", "# My tasks\n\n[[tasks: list=task view=today pin=1]]\n")
        list_page(client, wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n")
        list_page(client, wiki_dir, "gifts", "# Gifts\n\n[[tasks: list=gifts]]\n")

    def get(self, client, url):
        return client.get(url, headers=HOST).get_data(as_text=True)

    def current(self, client, url):
        return [k for k, _, cur in places(self.get(client, url)) if cur]

    def test_nothing_to_switch_between_shows_no_switcher(self, client, wiki_dir):
        write_page(wiki_dir, "work", "# Work\n\n[[tasks: tag=work]]\n")
        html = self.get(client, "/tasks")
        assert places(html) == [] and more(html) is None and "lists-more" not in html
        # The left column is there (empty), for the list a first one makes.
        assert '<aside class="rail rail-left rail-lists"><a class="rail-top"' in html and 'class="lists-rail"' not in html

    def test_the_lists_in_the_left_column(self, client, wiki_dir):
        # On big screens the left column has every list, a line each, in
        # groups: the views (My tasks and the Inbox with their counts, All
        # tasks), Pinned (with counts), Lists (none); the same entries as
        # the row. Names start in one line: no pin, marks after the name.
        self.wiki(client, wiki_dir)
        create_task(client, title="Report", list="task", due_date=date.today().isoformat())
        html = self.get(client, "/tasks?list=shop")
        assert '<main class="wide">' in html
        rail = html.split('<nav class="lists-rail"', 1)[1].split("</nav>", 1)[0]
        lines = re.findall(r'<a href="([^"]+)" class="lists-rail-item[^"]*"(?: data-list="[^"]*")?(?: data-place="([^"]+)")?[^>]*?( aria-current="page")?><span class="lists-rail-name">([^<]*)</span>', rail)
        assert [(href, key, name, cur != "") for href, key, cur, name in lines] == [
            ("/tasks?mine=1", "mine", "My tasks", False), ("/tasks?list=inbox", "inbox", "Inbox", False),
            ("/tasks", "", "All tasks", False),
            ("/tasks?list=task", "list:task", "task", False),
            ("/tasks?list=gifts", "list:gifts", "gifts", False), ("/tasks?list=shop", "list:shop", "shop", True)]
        heads = re.findall(r'<p class="lists-rail-head">([^<]*)</p>', rail)
        assert heads == ["Pinned", "Lists"]
        pins = rail.split('<div class="lists-rail-group lists-rail-pins">', 1)[1].split("</div>", 1)[0]
        assert re.search(r'class="module-due"[^>]*>1<', pins) and "<svg" not in pins   # no pin: the heading says it
        rest = rail.split('<div class="lists-rail-group lists-rail-rest">', 1)[1]
        assert "module-counts" not in rest
        assert "lists-rail-filter" not in rail          # a find field from 8 lists
        assert "data-reorder" in html.split('<nav class="lists-rail"', 1)[1].split(">", 1)[0]
        # All tasks is the current line on /tasks; no pins, no Pinned.
        client.post("/tasks/pin-list", data={"list": "task", "pin": "0"}, headers=HOST)
        rail = self.get(client, "/tasks").split('<nav class="lists-rail"', 1)[1].split("</nav>", 1)[0]
        assert "lists-rail-pins" not in rail and ">Pinned<" not in rail
        assert re.search(r'class="lists-rail-item lists-rail-all"[^>]*aria-current="page"', rail)

    def test_the_lists_and_all_above_the_heading(self, client, wiki_dir):
        self.wiki(client, wiki_dir)
        html = self.get(client, "/tasks?list=shop")
        # My tasks, the Inbox and the pinned list are pills; the others, by
        # their names A–Z, and All tasks are under More, which names the view
        # the page shows: shop.
        assert places(html) == [("mine", "My tasks", False), ("inbox", "Inbox", False), ("list:task", "task", False)]
        assert more(html) == ("shop", True, [("/tasks?list=gifts", "gifts", False), ("/tasks?list=shop", "shop", True), ("/tasks", "All tasks", False)])
        nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'href="([^"]+)"', nav) == ["/tasks?mine=1", "/tasks?list=inbox", "/tasks?list=task", "/tasks?list=gifts", "/tasks?list=shop", "/tasks"]
        assert 'title="List #shop"' in nav and 'title="Pinned list #task"' in nav
        assert 'title="Inbox: new tasks land here"' in nav and 'title="Every task but those of the lists left out of My tasks"' in nav
        # The pinned list marked with a pin; My tasks, the Inbox and the others not. No pill has a plus.
        assert re.findall(r'data-place="([^"]+)"[^>]*>(?:<span class="tasks-place-pin")', nav) == ["list:task"]
        assert "tasks-place-first" not in nav
        assert "tasks-place-lock" not in nav   # without users nothing is private
        assert "lists-more-filter" not in nav  # a few lists: no field to find one
        assert " data-reorder>" in html.split('<nav class="tasks-places"', 1)[1].split("<div", 1)[0]
        # On the pinned list More is just More.
        assert more(self.get(client, "/tasks?list=task"))[:2] == ("More", False)
        # Above the heading, which names the list.
        assert html.count('class="tasks-places"') == 1
        assert html.index('class="tasks-places"') < html.index("<h1>List: shop</h1>") < html.index('class="tasks-app"')
        assert "<title>List: shop" in html

    def test_all_tasks_and_the_pinned_list(self, client, wiki_dir):
        self.wiki(client, wiki_dir)
        html = self.get(client, "/tasks")
        assert self.current(client, "/tasks") == []   # no pill is: All tasks is under More
        assert more(html)[:2] == ("All tasks", True) and more(html)[2][-1] == ("/tasks", "All tasks", True)
        assert "<h1>All tasks</h1>" in html
        # A tag's view is All tasks with a filter.
        assert more(self.get(client, "/tasks?tag=x"))[:2] == ("All tasks", True)
        assert self.current(client, "/tasks?list=task") == ["list:task"]
        # A name no list has: the switcher, nothing current, and a line that
        # says so, with a button that makes the list.
        html = self.get(client, "/tasks?list=nothing")
        assert places(html) and self.current(client, "/tasks?list=nothing") == [] and more(html)[:2] == ("More", False)
        assert "<h1>List: nothing</h1>" in html and "There is no list <code>#nothing</code>" in html
        assert 'class="tasks-app"' not in html and 'action="/tasks/new-list"><input type="hidden" name="name" value="nothing">' in html

    def test_pages_show_no_switcher(self, client, wiki_dir):
        # A page that embeds a list shows the list; the switcher is /tasks's.
        self.wiki(client, wiki_dir)
        for slug in ("tasks", "shop"):
            assert places(self.get(client, f"/page/{slug}")) == [], slug
        html = client.post("/api/preview?page=shop", data=b"[[tasks: list=shop]]\n", headers=HOST).get_data(as_text=True)
        assert places(html) == []

    def test_a_pinned_list_declared_elsewhere_too_is_one_place(self, client, wiki_dir):
        # Another page showing the pinned list (here first A–Z): still one
        # pill, named by the list, with the list's own view; no page title
        # on /tasks?list=.
        self.wiki(client, wiki_dir)
        write_page(wiki_dir, "aaa-scratch", "# Scratch\n\n[[tasks: list=task]]\n")
        html = self.get(client, "/tasks?list=task")
        assert [k for k, _, _ in places(html)] == ["mine", "inbox", "list:task"]
        assert self.current(client, "/tasks?list=task") == ["list:task"]
        assert 'data-init-view="today"' in html   # the list's view=today
        assert "A separate list" not in html and "tasks-list-home" not in html
        foot = html.split('<div class="page-foot">', 1)[1].split("</div>", 1)[0]
        assert re.findall(r'<li><a href="([^"]+)">', foot) == ["/page/tasks", "/page/aaa-scratch"]

    def test_a_pin_in_a_page_pins_nothing(self, client, wiki_dir):
        # Pins are of lists, and the user's own: pin= in an embed means nothing.
        write_page(wiki_dir, "tasks", "# My tasks\n\n[[tasks: tag=me pin=1]]\n")
        write_page(wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop pin=1]]\n")
        make_list(client, "shop", headers=HOST)
        html = self.get(client, "/tasks")
        # Nothing pinned: the row is My tasks, the Inbox and More, which says All tasks.
        assert places(html) == [("mine", "My tasks", False), ("inbox", "Inbox", False)]
        assert more(html) == ("All tasks", True, [("/tasks?list=shop", "shop", False), ("/tasks", "All tasks", True)])
        # The Tasks link goes where it would without the pin: the view shown.
        assert "tasks-place-pin" not in html and 'href="/tasks" class="module-link" data-module="tasks"' in html
        # My tasks is every task all the same: a pin is a pill.
        line = self.get(client, "/tasks?mine=1").split('class="tasks-list-home tasks-mine-line">', 1)[1].split("</p>", 1)[0]
        assert line == "Every open task."

    def test_my_tasks_is_one_view(self, client, wiki_dir):
        # Two pinned lists: My tasks first (the Tasks link's target), the
        # Inbox, then both lists with a pin, in their order; /tasks?mine=1
        # shows every task but those of a list left out, as one task app,
        # whose New puts a task in the Inbox. The Tasks link counts what it
        # shows. The pins and their order change nothing of it.
        self.wiki(client, wiki_dir)
        list_page(client, wiki_dir, "tasks", "# My tasks\n\n[[tasks: list=task view=today pin=default]]\n")
        list_page(client, wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1 pin=1]]\n")
        assert client.post("/api/tasks/settings", json={"list": "gifts", "set": {"home": "off"}}, headers=HOST).status_code == 200
        today = date.today().isoformat()
        create_task(client, title="Milk", list="shop", due_date=today)
        create_task(client, title="Report", list="task", start_date=today)
        create_task(client, title="Also", list="task", tags=["shop"], due_date=today)   # a tag is no list
        create_task(client, title="Gift", list="gifts", due_date=today)   # its list is left out: not in it
        create_task(client, title="Loose", due_date=today)                # in the Inbox
        html = self.get(client, "/tasks?mine=1")
        assert places(html) == [("mine", "My tasks", True), ("inbox", "Inbox", False), ("list:task", "task", False),
                                ("list:shop", "shop", False)]
        assert more(html) == ("More", False, [("/tasks?list=gifts", "gifts", False), ("/tasks", "All tasks", False)])
        nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        assert re.findall(r'data-place="([^"]+)"[^>]*>(?:<span class="tasks-place-pin")', nav) == ["list:task", "list:shop"]
        assert "<h1>My tasks</h1>" in html and "<title>My tasks" in html
        # Under the heading: what it holds, the lists left out each a link to the list.
        line = html.split('class="tasks-list-home tasks-mine-line">', 1)[1].split("</p>", 1)[0]
        assert re.sub(r"<[^>]+>", "", line) == "Every open task, but the lists left out of it (#gifts)."
        assert re.findall(r'<a href="([^"]+)">([^<]*)</a>', line) == [("/tasks?list=gifts", "#gifts")]
        assert "tasks-pinned-lists" not in html and 'id="backlinks"' not in html     # no line of pins, no Backlinks
        # One task app: its own settings (none yet: not a list's view=today
        # or keep), new tasks into the Inbox.
        assert html.count('class="tasks-app"') == 1
        assert 'data-init-tag="" data-init-list="" data-init-mine="lists" data-init-out="gifts"' in html
        assert "data-init-lists" not in html
        assert 'data-init-view=""' in html and 'data-init-keep=""' in html and "data-new-tags" not in html
        assert "data-default-list" not in html and 'data-home="1"' in html and "data-inbox-private" not in html
        # The Tasks link goes there and counts what it shows.
        body = self.get(client, "/page/index")
        assert 'href="/tasks?mine=1" class="module-link" data-module="tasks"' in body
        assert "data-tasks-list" not in body          # c t: the Inbox, no list
        data = client.get("/api/tasks/due-count?places=1").get_json()
        assert (data["count"], data["focus"]) == (3, 4)
        assert {k: (v["count"], v["focus"]) for k, v in data["places"].items()} == {
            "mine": (3, 4), "inbox": (1, 1), "list:task": (1, 2), "list:shop": (1, 1)}   # the pills
        titles = lambda url: sorted(t["title"] for t in client.get(url, headers=HOST).get_json()["tasks"])
        assert titles("/api/tasks/export?mine=1") == ["Also", "Loose", "Milk", "Report"]
        # The order of the pills is the user's to choose: shop, moved to the
        # front. It is a pill like the others (no plus), and takes no new task.
        assert client.post("/tasks/pin-list", data={"list": "shop", "pin": "first"}, headers=HOST).status_code == 302
        html = self.get(client, "/tasks")
        assert [k for k, _, _ in places(html)][:4] == ["mine", "inbox", "list:shop", "list:task"]
        assert "data-tasks-list" not in html and "tasks-place-first" not in html and "new tasks go here" not in html
        assert 'data-init-keep=""' in self.get(client, "/tasks?mine=1")       # shop's keep is shop's
        # "default", the word for it until 2026-11, is refused.
        assert client.post("/tasks/pin-list", data={"list": "task", "pin": "default"}, headers=HOST).status_code == 400
        # Unpinned, a list's tasks are in My tasks still; pinned, a list left out isn't.
        assert client.post("/tasks/pin-list", data={"list": "shop", "pin": "0"}, headers=HOST).status_code == 302
        assert client.post("/tasks/pin-list", data={"list": "gifts", "pin": "1"}, headers=HOST).status_code == 302
        html = self.get(client, "/tasks?mine=1")
        assert "<h1>My tasks</h1>" in html and 'data-init-mine="lists" data-init-out="gifts"' in html
        assert [k for k, _, _ in places(html)] == ["mine", "inbox", "list:task", "list:gifts"]
        assert titles("/api/tasks/export?mine=1") == ["Also", "Loose", "Milk", "Report"]
        assert client.get("/api/tasks/due-count").get_json()["count"] == 3

    def test_my_tasks_has_settings_of_its_own(self, client, wiki_dir):
        # The tab, Plan, the limit and the tabs' names, in the registry's
        # "mine"; what a new task gets is its list's and can't be set here.
        self.wiki(client, wiki_dir)
        html = self.get(client, "/tasks?mine=1")
        assert 'data-settings-mine="1"' in html and 'data-settings-raw=""' in html
        save = lambda was, **set: client.post("/api/tasks/settings", json={"mine": True, "was": was, "set": set}, headers=HOST)
        assert save("", view="open", limit="4").status_code == 200
        assert '"mine": {"view": "open", "limit": "4"}' in (wiki_dir / "_tasks/lists.json").read_text()
        assert git_log(wiki_dir)[0] == "Change the settings of My tasks"
        html = self.get(client, "/tasks?mine=1")
        assert 'data-init-view="open"' in html and 'data-init-limit="4"' in html
        assert 'data-settings-raw="view=open limit=4"' in html
        # A list keeps its own, and so does All tasks.
        assert 'data-init-view="today"' in self.get(client, "/tasks?list=task")
        assert 'data-init-limit=""' in self.get(client, "/tasks")
        assert save("", limit="9").status_code == 409            # from a dialog opened before
        for bad in ({"new": "x"}, {"keep": "1"}, {"tag": "x"}, {"limit": "0"}):
            assert save("view=open limit=4", **bad).status_code == 400, bad

    def test_my_tasks_on_a_page(self, client, wiki_dir):
        # [[tasks: mine=1]]: My tasks for whoever looks, with the embed's own
        # view; its settings dialog writes the embed, not the user's My tasks.
        self.wiki(client, wiki_dir)
        write_page(wiki_dir, "desk", "# Desk\n\n[[tasks: mine=1 view=open]]\n")
        html = self.get(client, "/page/desk")
        assert 'data-init-mine="lists" data-init-out=""' in html and 'data-init-view="open"' in html
        assert "data-init-lists" not in html
        assert 'data-home="1"' in html                       # the phone's Task tab clicks its + New
        assert 'data-settings-embed="1"' in html and "data-settings-mine" not in html

    def test_without_lists_my_tasks_is_all_tasks(self, client, wiki_dir):
        resp = client.get("/tasks?mine=1", headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == "/tasks"
        assert 'href="/tasks" class="module-link" data-module="tasks"' in self.get(client, "/page/index")

    def test_each_place_has_its_counts(self, client, wiki_dir):
        self.wiki(client, wiki_dir)
        assert client.post("/tasks/pin-list", data={"list": "shop", "pin": "1"}, headers=HOST).status_code == 302
        today = date.today().isoformat()
        create_task(client, title="Milk", list="shop", start_date=today)
        create_task(client, title="Bread", list="shop", due_date=today)
        create_task(client, title="Report", list="task", due_date=today)
        create_task(client, title="Elsewhere", start_date=today)
        nav = self.get(client, "/tasks").split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        entries = dict(re.findall(r'<a [^>]*data-place="([^"]+)"[^>]*>(.*?)</a>', nav, re.S))
        counts = {k: v for k, v in entries.items() if "module-counts" in v}
        due = lambda k: re.search(r'class="module-due"[^>]*>(\d*)<', counts[k]).group(1)
        focus = lambda k: re.search(r'class="module-focus"[^>]*>(\d*)<', counts[k]).group(1)
        assert (due("list:shop"), focus("list:shop")) == ("1", "2")
        assert (due("list:task"), focus("list:task")) == ("1", "1")
        assert (due("mine"), focus("mine")) == ("2", "4")   # every task: the lists' and the Inbox's
        assert (due("inbox"), focus("inbox")) == ("", "1")  # Elsewhere, started
        # More's lists and All tasks carry none.
        assert set(counts) == {"mine", "inbox", "list:task", "list:shop"}
        # js/chrome.js refreshes them from the due count's places.
        data = client.get("/api/tasks/due-count?places=1").get_json()
        assert data["count"] == 2 and data["focus"] == 4   # My tasks'
        assert {k: (v["count"], v["focus"]) for k, v in data["places"].items()} == {
            "mine": (2, 4), "inbox": (0, 1), "list:task": (1, 1), "list:shop": (1, 2)}
        assert "places" not in client.get("/api/tasks/due-count").get_json()

    def test_the_inbox_is_a_pill_and_a_view(self, client, wiki_dir):
        # The tasks in no list are the Inbox: a pill after My tasks, always
        # there, with its counts, and a view of them alone. More has no entry
        # for them, and its pill no number.
        self.wiki(client, wiki_dir)
        today = date.today().isoformat()
        html = self.get(client, "/tasks?list=task")
        assert [k for k, _, _ in places(html)] == ["mine", "inbox", "list:task"]     # there while it is empty too
        assert more(html) == ("More", False, [("/tasks?list=gifts", "gifts", False), ("/tasks?list=shop", "shop", False), ("/tasks", "All tasks", False)])
        assert "module-counts" not in html.split('<details class="lists-more">', 1)[1].split("</details>", 1)[0]
        create_task(client, title="Loose", due_date=today)
        create_task(client, title="Loose too")
        create_task(client, title="Filed", list="task", due_date=today)
        create_task(client, title="Bread", list="shop", start_date=today)
        html = self.get(client, "/tasks?list=task")
        assert more(html) == ("More", False, [("/tasks?list=gifts", "gifts", False), ("/tasks?list=shop", "shop", False), ("/tasks", "All tasks", False)])
        assert "No list" not in html and "lists-more-none" not in html and 'data-place="none"' not in html
        summary = html.split('<details class="lists-more">', 1)[1].split("</details>", 1)[0].split("</summary>", 1)[0]
        assert "data-place" not in summary and "module-" not in summary
        nav = html.split('<nav class="tasks-places"', 1)[1].split("</nav>", 1)[0]
        pill = nav.split('data-place="inbox"', 1)[1].split("</a>", 1)[0]
        assert re.search(r'class="module-due"[^>]*>1<', pill) and re.search(r'class="module-focus"[^>]*>1<', pill)
        assert "tasks-place-lock" not in pill and "only you see it" not in pill      # without users it is the wiki's
        # The Tasks link counts every task; the refresh has the Inbox's too.
        data = client.get("/api/tasks/due-count?places=1").get_json()
        assert (data["count"], data["focus"]) == (2, 3)
        assert {k: (v["count"], v["focus"]) for k, v in data["places"].items()} == {"mine": (2, 3), "inbox": (1, 1), "list:task": (1, 1)}
        # Its view: the tasks in no list; its pill is the current one, More is More.
        for url in ("/tasks?list=inbox", "/tasks?list=Inbox", "/tasks?list=none"):     # "none" named it until 2026-11
            html = self.get(client, url)
            assert more(html)[:2] == ("More", False), url
            assert self.current(client, url) == ["inbox"], url
            assert "<h1>Inbox</h1>" in html and "<title>Inbox" in html and "There is no list" not in html, url
            assert html.count('class="tasks-app"') == 1 and 'data-init-list="inbox"' in html, url
            assert "data-settings-" not in html, url   # nothing of its own to set
            assert 'data-home="1"' in html, url      # the phone's Task tab clicks its + New
        line = html.split('class="tasks-list-home tasks-inbox-line">', 1)[1].split("</p>", 1)[0]
        assert line == "New tasks land here, in no list. Move one to a list to file it."
        # It is no list: no Rename…, pin, Delete… in the menu, no Backlinks.
        menu = html.split('id="page-menu"', 1)[1].split("</div>", 1)[0]
        assert "list-new-btn" in menu and "list-rename-btn" not in menu and "list-delete-btn" not in menu and "list-pin-btn" not in menu
        assert 'id="backlinks"' not in html
        for op, data in (("rename-list", {"list": "inbox", "new_name": "x"}), ("delete-list", {"list": "inbox", "to": "task"}),
                         ("pin-list", {"list": "inbox", "pin": "1"}), ("share-list", {"list": "inbox"})):
            assert client.post(f"/tasks/{op}", data=data, headers=HOST).status_code in (400, 404), op
        assert client.post("/api/tasks/settings", json={"list": "inbox", "set": {"limit": "3"}}, headers=HOST).status_code in (400, 404)
        # The API's scopes take the name too.
        for name in ("inbox", "none"):
            assert [t["title"] for t in client.get(f"/api/tasks/export?list={name}", headers=HOST).get_json()["tasks"]] == ["Loose", "Loose too"]
        # On a page: an embed of the Inbox, with the embed's own params.
        for name in ("inbox", "none"):
            write_page(wiki_dir, "loose", f"# Loose ends\n\n[[tasks: list={name} view=open]]\n")
            page = self.get(client, "/page/loose")
            assert 'data-init-list="inbox"' in page and 'data-init-view="open"' in page and "There is no list" not in page, name
        # No list can be called so.
        for name in ("None", "Inbox", "#inbox"):
            made = client.post("/tasks/new-list", data={"name": name}, headers=HOST)
            assert "newlist_error" in made.headers["Location"], name
        registry = (wiki_dir / "_tasks/lists.json").read_text()
        assert '"none"' not in registry and '"inbox"' not in registry
        renamed = client.post("/tasks/rename-list", data={"list": "gifts", "new_name": "inbox"}, headers=HOST)
        assert "rename_error" in renamed.headers["Location"] and '"gifts"' in (wiki_dir / "_tasks/lists.json").read_text()
        # A task filed into a list leaves it; one taken out of its list is back in it.
        tasks = {t["title"]: t for t in client.get("/api/tasks").get_json()}
        assert client.put(f"/api/tasks/{tasks['Loose']['id']}", json={"list": "gifts"}).status_code == 200
        back = client.put(f"/api/tasks/{tasks['Filed']['id']}", json={"list": "inbox"})
        assert back.status_code == 200 and "list" not in back.get_json()
        assert [t["title"] for t in client.get("/api/tasks/export?list=inbox", headers=HOST).get_json()["tasks"]] == ["Loose too", "Filed"]
        # Once every task is in a list the pill stays, with no number.
        for t in client.get("/api/tasks").get_json():
            if not t.get("list"):
                assert client.put(f"/api/tasks/{t['id']}", json={"list": "gifts"}).status_code == 200
        html = self.get(client, "/tasks?list=task")
        assert [k for k, _, _ in places(html)] == ["mine", "inbox", "list:task"]
        assert client.get("/api/tasks/due-count?places=1").get_json()["places"]["inbox"] == {"count": 0, "overdue": 0, "today": 0, "focus": 0}

    def test_sharing_mode_keeps_the_wikis_inbox(self, client, wiki_dir):
        # Without users the Inbox is the wiki's tasks in no list: shown in sharing mode as they are in All tasks.
        self.wiki(client, wiki_dir)
        client.set_cookie("zone-sharing", "1")
        assert [k for k, _, _ in places(self.get(client, "/tasks"))] == ["mine", "inbox", "list:task"]

    def test_sharing_mode_leaves_out_a_list_not_for_sharing(self, client, wiki_dir):
        # A list says so itself (List settings…: Hide in sharing mode).
        self.wiki(client, wiki_dir)
        assert client.post("/api/tasks/settings", json={"list": "gifts", "set": {"noshare": "1"}}, headers=HOST).status_code == 200
        names = lambda url: [n for _, n, _ in more(self.get(client, url))[2]]
        assert names("/tasks") == ["gifts", "shop", "All tasks"]
        client.set_cookie("zone-sharing", "1")
        assert names("/tasks") == ["shop", "All tasks"]
        # More is there in sharing mode (the ⋯ menu isn't), and nobody reorders the pills.
        html = self.get(client, "/tasks")
        assert "page-menu-btn" not in html and "data-reorder" not in html


    def test_a_new_list_is_pinned_for_who_made_it(self, client, wiki_dir):
        # New list… pins the list it makes, last: it is a pill at once, the
        # first one too. That changes nothing else: new tasks go to the
        # Inbox as before, and the page has nothing to say about it.
        create_task(client, title="Loose")
        create_task(client, title="Loose too")
        made = client.post("/tasks/new-list", data={"name": "home"}, headers=HOST)
        assert made.headers["Location"] == "/tasks?list=home"
        html = self.get(client, "/tasks?list=home&made=1")      # ?made=1, the first list's note until 2026-11, means nothing
        assert places(html) == [("mine", "My tasks", False), ("inbox", "Inbox", False), ("list:home", "home", True)]
        assert more(html)[:2] == ("More", False)
        assert "tasks-made-first" not in html and "New tasks go into this list" not in html
        # The Tasks link comes back to the view shown (not a list for new tasks).
        assert 'href="/tasks?list=home" class="module-link" data-module="tasks"' in html and "data-tasks-list" not in html
        assert "list" not in create_task(client, title="After").get_json()
        # The tasks made before it stay where the Tasks link goes.
        assert client.get("/api/tasks/due-count?places=1").get_json()["places"]["mine"]["focus"] == 0
        assert len(client.get("/api/tasks/export?mine=1", headers=HOST).get_json()["tasks"]) == 3
        # The next is pinned after it.
        made = client.post("/tasks/new-list", data={"name": "work"}, headers=HOST)
        assert made.headers["Location"] == "/tasks?list=work"
        html = self.get(client, "/tasks?list=work")
        assert places(html) == [("mine", "My tasks", False), ("inbox", "Inbox", False), ("list:home", "home", False),
                                ("list:work", "work", True)]
        text = (wiki_dir / "_tasks/lists.json").read_text()
        assert '"pinned": ["home", "work"]' in text
        assert git_log(wiki_dir)[:3] == ["Create list #work", "Create task", "Create list #home"]    # the pin is in the list's commit
        # A list made where the page stays (the dialog that moves tasks) too.
        resp = client.post("/api/tasks/lists", json={"name": "later"}, headers=HOST)
        assert resp.get_json() == {"ref": "later", "private": False, "pinned": True}
        assert '"pinned": ["home", "work", "later"]' in (wiki_dir / "_tasks/lists.json").read_text()

    def test_the_pills_in_another_order(self, client, wiki_dir):
        # Dragging the pills posts their order: the pinned lists named first,
        # as named. Nothing is pinned by it, and the first takes no new task.
        for name in ("a", "b", "c"):
            client.post("/tasks/new-list", data={"name": name}, headers=HOST)
        make_list(client, "quiet", headers=HOST)
        order = lambda *refs: client.post("/tasks/pin-order", data={"list": list(refs)}, headers=HOST).status_code
        assert order("c", "quiet", "a") == 204
        html = self.get(client, "/tasks")
        assert [k for k, _, _ in places(html)] == ["mine", "inbox", "list:c", "list:a", "list:b"]
        assert "data-tasks-list" not in html and "tasks-place-first" not in html
        # My tasks and the Inbox are no pins: they stay in front, and aren't in the order.
        assert order("inbox", "mine", "b") == 204
        assert [k for k, _, _ in places(self.get(client, "/tasks"))] == ["mine", "inbox", "list:b", "list:c", "list:a"]
        assert order("c", "a") == 204
        assert '"pinned": ["c", "a", "b"]' in (wiki_dir / "_tasks/lists.json").read_text()
        before = git_log(wiki_dir)
        assert before[0] == "Reorder the pinned lists"
        assert order("c", "a", "b") == 204 and order() == 204 and git_log(wiki_dir) == before   # the same order: no commit

    def test_more_has_a_filter_field_with_many_lists(self, client, wiki_dir):
        for i in range(7):
            make_list(client, f"list{i}", headers=HOST)
        assert "lists-more-filter" not in self.get(client, "/tasks")
        make_list(client, "list7", headers=HOST)
        html = self.get(client, "/tasks")
        assert '<input type="search" class="lists-more-filter"' in html and len(more(html)[2]) == 9


class TestListBacklinks:
    """/tasks?list= lists the pages that embed the list as its Backlinks."""

    def test_the_pages_embedding_a_list(self, client, wiki_dir):
        list_page(client, wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop keep=1]]\n")
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

    def test_my_tasks_and_the_inbox_have_none(self, client, wiki_dir):
        # My tasks is no list's view: it names no pinned lists and has no
        # Backlinks, however many pages embed the lists whose tasks it shows.
        # Nor has the Inbox, which is no list a page could embed by a name of its own.
        list_page(client, wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop pin=1]]\n")
        list_page(client, wiki_dir, "kitchen", "# Kitchen\n\n[[tasks: list=shop view=open]]\n\n[[tasks: list=work]]\n")
        write_page(wiki_dir, "desk", "# Desk\n\n[[tasks: mine=1]]\n\n[[tasks: list=inbox]]\n")
        for url in ("/tasks?mine=1", "/tasks?list=inbox"):
            html = client.get(url, headers=HOST).get_data(as_text=True)
            assert 'id="backlinks"' not in html and "bl-count" not in html, url
            assert '<aside class="rail rail-context" aria-label="Links"></aside>' in html, url
            assert "tasks-pinned-lists" not in html, url
        # A list's own view has its Backlinks, and no line of My tasks.
        html = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        assert 'id="backlinks"' in html and "tasks-mine-line" not in html

    def test_no_list_no_backlinks(self, client, wiki_dir):
        list_page(client, wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop]]\n")
        for url in ("/tasks", "/tasks?list=nothing"):
            html = client.get(url, headers=HOST).get_data(as_text=True)
            assert 'id="backlinks"' not in html and "bl-count" not in html, url

    def test_sharing_mode_leaves_out_a_page_not_for_sharing(self, client, wiki_dir):
        list_page(client, wiki_dir, "shop", "# Shopping\n\n[[tasks: list=shop]]\n")
        write_page(wiki_dir, "zz-secret", "---\ntags: noshare\n---\n# Secret\n\n[[tasks: list=shop]]\n")
        assert "/page/zz-secret" in client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        client.set_cookie("zone-sharing", "1")
        html = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        assert "/page/zz-secret" not in html and "/page/shop" in html


# ── The tasks a page brings along ───────────────────────────────────────────────


def initial_tasks(html: str) -> list[dict]:
    """The data blocks of a page's HTML (<script class="tasks-initial">), parsed."""
    return [json.loads(m) for m in re.findall(r'<script type="application/json" class="tasks-initial">(.*?)</script>', html, re.S)]


class TestInitialTasks:
    """A page view with a task list carries the open tasks and their texts'
    HTML, so the list draws without asking for them (js/taskdata.js)."""

    def test_a_page_with_a_list_brings_what_the_api_would_answer(self, client, wiki_dir):
        write_page(wiki_dir, "plans", "# Plans\n")
        a = create_task(client, title="Buy milk", description="see [[plans]]\nsoon\n\n- [ ] oat", tags=["shop"]).get_json()
        create_task(client, title="No notes")
        client.post(f"/api/tasks/{a['id']}/comments", json={"text": "**called** Bob\nno answer"})   # a comment keeps its lines, notes don't
        done = create_task(client, title="Done already").get_json()
        client.put(f"/api/tasks/{done['id']}", json={"completed": True})
        write_page(wiki_dir, "shop", "# Shop\n\n[[tasks: tag=shop]]\n\n[[tasks:]]\n")
        tasks = client.get("/api/tasks", headers=HOST).get_json()
        assert sorted(t["title"] for t in tasks) == ["Buy milk", "No notes"]
        texts = {"notes": [t["description"] for t in tasks if t["description"]],
                 "comments": [c["text"] for t in tasks for c in t["comments"]]}
        html = client.post("/api/render-notes", json=texts, headers=HOST).get_json()
        assert 'href="/page/plans"' in html["notes"][0] and "<strong>called</strong>" in html["comments"][0]
        # Once a page, whatever its lists show, and on /tasks.
        # With them the word of these tasks, as GET /api/tasks answers it.
        word = client.get("/api/tasks", headers=HOST).headers["Zone-Tasks"]
        for url in ("/page/shop", "/tasks", "/tasks?tag=shop"):
            assert initial_tasks(client.get(url, headers=HOST).get_data(as_text=True)) == [{"word": word, "order": [], "tasks": tasks, **html}], url

    def test_a_page_brings_the_tasks_its_lists_show_and_no_others(self, client, wiki_dir):
        """My tasks or one list doesn't carry every open task: the block
        holds what the page's lists show (their scopes), a list of every
        task all of them; GET /api/tasks takes the scope too."""
        for name in ("shop", "work", "someday"):
            make_list(client, name, headers=HOST)
        for title, fields in (("Milk", {"list": "shop"}), ("Report", {"list": "work"}),
                              ("Loose", {}), ("Later", {"list": "someday"})):
            assert create_task(client, title=title, headers=HOST, **fields).status_code == 201
        # Left out of My tasks: someday.
        assert client.post("/api/tasks/settings", json={"list": "someday", "set": {"home": "off"}}, headers=HOST).status_code == 200
        titles = lambda url: sorted(t["title"] for t in initial_tasks(client.get(url, headers=HOST).get_data(as_text=True))[0]["tasks"])
        assert titles("/tasks?list=shop") == ["Milk"]
        assert titles("/tasks?list=inbox") == ["Loose"]
        assert titles("/tasks?mine=1") == ["Loose", "Milk", "Report"]
        assert titles("/tasks") == ["Later", "Loose", "Milk", "Report"]
        assert titles("/tasks?tag=x") == ["Later", "Loose", "Milk", "Report"]      # a tag's view is every task, filtered
        # A page: what any of its lists shows; a list that isn't there adds nothing.
        write_page(wiki_dir, "two", "[[tasks: list=shop]]\n\n[[tasks: list=work]]\n\n[[tasks: list=nope]]\n")
        assert titles("/page/two") == ["Milk", "Report"]
        write_page(wiki_dir, "mixed", "[[tasks: list=shop]]\n\n[[tasks: tag=x]]\n")
        assert titles("/page/mixed") == ["Later", "Loose", "Milk", "Report"]
        # The route a list loads from later.
        api = lambda q: sorted(t["title"] for t in client.get("/api/tasks" + q, headers=HOST).get_json())
        assert api("?list=shop") == ["Milk"] and api("?list=inbox") == ["Loose"] and api("?lists=shop|work") == ["Milk", "Report"]
        assert api("?mine=1") == ["Loose", "Milk", "Report"] and api("") == ["Later", "Loose", "Milk", "Report"]
        assert api("?list=nope") == []
        got = client.get("/api/tasks?mine=1&with=order", headers=HOST).get_json()
        assert sorted(t["title"] for t in got["tasks"]) == ["Loose", "Milk", "Report"] and got["order"] == []

    def test_no_tasks(self, client, wiki_dir):
        write_page(wiki_dir, "shop", "# Shop\n\n[[tasks:]]\n")
        [block] = initial_tasks(client.get("/page/shop", headers=HOST).get_data(as_text=True))
        assert block.pop("word") == client.get("/api/tasks/due-count", headers=HOST).headers["Zone-Tasks"]
        assert block == {"order": [], "tasks": [], "notes": [], "comments": []}

    def test_a_tasks_text_does_not_end_the_block(self, client, wiki_dir):
        evil = '</script><script src="/x.js"></script><!-- & <b>'
        create_task(client, title=evil, description=evil, tags=["t"])
        write_page(wiki_dir, "shop", "# Shop\n\n[[tasks:]]\n")
        html = client.get("/page/shop", headers=HOST).get_data(as_text=True)
        block = html.split('class="tasks-initial">', 1)[1].split("</script>", 1)[0]
        assert "<" not in block and ">" not in block and "&" not in block
        (data,) = initial_tasks(html)
        assert data["tasks"][0]["title"] == evil and data["tasks"] == client.get("/api/tasks", headers=HOST).get_json()

    def test_the_block_is_in_the_first_lists_own_element(self, client, wiki_dir):
        # Found there by the lists (js/taskdata.js), never by an id a page's
        # raw HTML could take; and a page's raw HTML makes no <script>, so a
        # data block of its own before the list shows as text.
        create_task(client, title="Buy milk", tags=["t"])
        write_page(wiki_dir, "shop", "# Shop\n\n"
                   '<script type="application/json" id="tasks-initial">{"tasks": []}</script>\n\n'
                   "No list called nope: [[tasks: list=nope]]\n\n[[tasks: tag=t]]\n\n[[tasks:]]\n")
        html = client.get("/page/shop", headers=HOST).get_data(as_text=True)
        assert html.count("<script") == html.count("</script>") and len(initial_tasks(html)) == 1
        assert re.search(r'<div class="tasks-app" id="tasks-app-1"[^>]*><script type="application/json" '
                         r'class="tasks-initial">', html)
        assert '&lt;script type="application/json" id="tasks-initial">{"tasks": []}&lt;/script>' in html
        page_html = html.split('<article', 1)[1].split("</article>", 1)[0]
        assert len(re.findall(r"<script\b", page_html)) == 1
        tasks = client.get("/tasks", headers=HOST).get_data(as_text=True)
        assert re.search(r'<div class="tasks-app" id="tasks-app"[^>]*><script type="application/json" '
                         r'class="tasks-initial">', tasks)

    def test_only_a_page_view_with_a_list(self, client, wiki_dir):
        create_task(client, title="Buy milk")
        write_page(wiki_dir, "plain", "# Plain\n\nNo list here.\n")
        assert "tasks-initial" not in client.get("/page/plain", headers=HOST).get_data(as_text=True)
        # A preview's list starts later: it asks the server.
        html = preview(client, "[[tasks:]]\n")
        assert "tasks-app" in html and "tasks-initial" not in html
        assert "tasks-initial" not in client.get("/api/tasks/headless", headers=HOST).get_data(as_text=True)
