"""A public wiki in the browser (ZONE_PUBLIC; docs/spec/security.md, Public
wikis): what a visitor, who isn't signed in, sees and can do. The routes
and what they answer are tests/test_public.py's."""

import pytest
from playwright.sync_api import expect

from conftest import box_of, list_page
from tests import settings


@pytest.fixture()
def visitor(page, wiki, monkeypatch):
    """The browser, not signed in, on a wiki with users: a page with a task
    list and a shared task that links to it. Collects what goes wrong in the
    page: failed requests and script errors."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    list_page(page, wiki, "index", "# Home\n\nWelcome. See [[Garden]].\n\n- [ ] a box\n\n[[tasks: list=shop pin=1]]\n\n## More\n\ntext\n")
    wiki.write("garden", "# Garden\n\nBeds.\n")
    assert page.request.post("/api/tasks", data={"title": "Quokka task for [[garden]]", "list": "shop"}).ok
    page.request.post("/logout")
    page.problems = []
    page.on("pageerror", lambda e: page.problems.append(f"error: {e}"))
    # A request refused, or sent to the login page: something on the page asked for what isn't a visitor's.
    page.on("response", lambda r: page.problems.append(f"{r.status} {r.request.method} {r.url}")
            if r.status >= 400 or "/login" in r.headers.get("location", "") else None)
    return page


def public(monkeypatch, mode):
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": mode})


def test_a_visitor_reads_the_wiki(visitor, wiki, monkeypatch):
    public(monkeypatch, "view")
    page = visitor
    page.goto("/page/index")
    expect(page.locator(".page-header h1")).to_have_text("Home")
    expect(page.locator("article.page-content")).to_contain_text("Welcome.")
    # The wiki alone: no Tasks or Notes, no task list, nothing to make or edit.
    expect(page.locator(".module-switch .module-link")).to_have_text(["Wiki"])
    expect(page.locator(".tasks-app")).to_have_count(0)
    expect(page.get_by_role("link", name="Edit", exact=True)).to_have_count(0)
    expect(page.locator("#uncommitted-badge")).to_have_count(0)
    # Sign in, once, at the top right; it comes back to this page.
    signin = page.locator("a:visible", has_text="Sign in")
    expect(signin).to_have_count(1)
    expect(signin).to_have_attribute("href", "/login?next=%2Fpage%2Findex")
    # The ⋯ menu: what reads, nothing that moves the page.
    page.locator(".page-menu-btn").click()
    items = page.locator("#page-menu [role=menuitem]:visible")
    expect(items.filter(has_text="History")).to_have_count(1)
    expect(items.filter(has_text="Rename")).to_have_count(0)
    expect(items.filter(has_text="Delete")).to_have_count(0)
    page.keyboard.press("Escape")
    # Search finds pages, and no task.
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("Garden")
    expect(page.locator("#modal-page-list li", has_text="Garden")).to_have_count(1)
    page.locator("#modal-filter").fill("Quokka")
    expect(page.locator("#modal-page-list li")).to_have_count(0)
    expect(page.locator("#modal-content-matches")).not_to_contain_text("Quokka")
    page.keyboard.press("Escape")
    # A link goes on; the page a task links to shows no Linked tasks.
    page.get_by_role("link", name="Garden").first.click()
    expect(page.locator(".page-header h1")).to_have_text("Garden")
    expect(page.locator("body")).not_to_contain_text("Quokka")
    assert page.problems == [], page.problems
    # (From here on the test asks for such things itself.)
    # Tasks want an account.
    page.goto("/tasks")
    assert "/login?next=%2Ftasks" in page.url
    # Signing in comes back, as a user: the tasks are there.
    page.goto("/page/index")
    page.locator("a:visible", has_text="Sign in").click()
    page.locator("input[name=username]").fill("alice")
    page.locator("input[name=password]").fill("pw")
    page.locator("input[name=password]").press("Enter")
    page.wait_for_url("**/page/index")
    expect(page.locator(".tasks-app")).to_have_count(1)
    expect(page.locator(".module-switch .module-link", has_text="Tasks")).to_have_count(1)


def test_a_visitor_on_a_phone_signs_in_from_the_top_row(visitor, wiki, monkeypatch):
    public(monkeypatch, "view")
    page = visitor
    page.set_viewport_size({"width": 400, "height": 700})
    page.goto("/page/garden")
    expect(page.locator("a:visible", has_text="Sign in")).to_have_count(1)
    # On the breadcrumb's row, at its right end, clear of the breadcrumb: no row of its own.
    signin, row = box_of(page.locator(".top-signin")), box_of(page.locator(".breadcrumb"))
    assert row["y"] <= signin["y"] and signin["y"] + signin["height"] <= row["y"] + row["height"] + 1, (signin, row)
    assert abs(signin["x"] + signin["width"] - (400 - 16)) <= 1 and row["y"] <= 14, (signin, row)
    for crumb in page.locator(".breadcrumb > *").all():
        if crumb.is_visible():
            c = box_of(crumb)
            assert c["x"] + c["width"] <= signin["x"], (c, signin)
    expect(page.locator(".action-buttons .action-module")).to_have_count(1)        # the wiki alone
    expect(page.locator(".action-buttons .action-new-note")).to_be_hidden()
    expect(page.locator(".action-buttons .action-task")).to_be_hidden()
    assert page.problems == [], page.problems


def test_a_visitor_edits_a_page(visitor, wiki, monkeypatch):
    public(monkeypatch, "edit")
    page = visitor
    page.goto("/page/garden")
    page.get_by_role("link", name="Edit", exact=True).click()
    box = page.locator("#editor-textarea")
    expect(box).to_have_value("# Garden\n\nBeds.\n")
    # The bar has nothing of tasks or uploads; no Rename or Delete.
    for fmt in ("taskref", "tasklist", "maketask", "attach"):
        expect(page.locator(f'#format-bar .fmt-btn[data-fmt="{fmt}"]')).to_be_hidden()
    expect(page.locator('#format-bar .fmt-btn[data-fmt="bold"]')).to_be_visible()
    expect(page.locator("#rename-toggle, #delete-btn")).to_have_count(0)
    box.press("Control+End")
    box.press_sequentially("\nAnd a pond.")
    expect(page.locator("#editor-preview")).to_contain_text("And a pond.")
    page.get_by_role("button", name="Done").click()
    page.wait_for_url("**/page/garden")
    expect(page.locator("article.page-content")).to_contain_text("And a pond.")
    assert wiki.read("garden").rstrip() == "# Garden\n\nBeds.\n\nAnd a pond."
    # Its history says who: no one in particular.
    page.goto("/history/garden")
    expect(page.locator("body")).to_contain_text("anonymous")
    # A box ticks.
    page.goto("/page/index")
    page.locator("article .task-list-item-checkbox").first.check()
    expect(page.locator("article .task-list-item-checkbox").first).to_be_checked()
    page.reload()
    expect(page.locator("article .task-list-item-checkbox").first).to_be_checked()
    assert page.problems == [], page.problems
