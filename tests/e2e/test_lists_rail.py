"""The task lists in the left column on big screens (docs/spec/tasks.md,
The lists in the left column): every list a line, the row of pills hidden
there; their counts, the pins dragged up and down, the find field and the
lists new to this browser. The row itself is in test_tasks.py."""

import re
from datetime import date

from playwright.sync_api import expect

from conftest import DESKTOP, SCREENSHOT_DIR, make_list

BIG = {"width": 1600, "height": 900}


def rail_names(page):
    return page.locator(".lists-rail .lists-rail-item .lists-rail-name")


def test_the_lists_are_the_left_column_on_a_big_screen(new_page, wiki):
    page = new_page(viewport=BIG)
    make_list(page, "shop", pin=True)
    make_list(page, "work")
    make_list(page, "garden")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop", "due_date": date.today().isoformat()})
    page.goto("/tasks?list=shop")
    rail = page.locator(".lists-rail")
    expect(rail).to_be_visible()
    expect(page.locator(".tasks-places")).to_be_hidden()
    # My tasks and the Inbox, the pins, every other list A–Z, All tasks.
    expect(rail_names(page)).to_have_text(["My tasks", "Inbox", "shop", "garden", "work", "All tasks"])
    expect(rail.locator(".lists-rail-head")).to_have_text(["Lists"])
    shop = rail.locator('[data-place="list:shop"]')
    expect(shop).to_have_attribute("aria-current", "page")
    expect(shop.locator(".tasks-place-pin")).to_have_count(1)
    expect(shop.locator(".module-due")).to_have_text("1")
    expect(rail.locator('[data-place="list:work"] .module-counts')).to_have_count(0)   # no counts on the others
    # Left of the sheet, which sits where a page's does.
    r, sheet = rail.bounding_box(), page.locator(".sheet").bounding_box()
    assert r["x"] + r["width"] < sheet["x"]
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.screenshot(path=str(SCREENSHOT_DIR / f"lists-rail-{scheme}.png"))
    # Its counts are kept with the Tasks link's.
    page.request.post("/api/tasks", data={"title": "Bread", "list": "shop", "due_date": date.today().isoformat()})
    page.evaluate("import('/public/js/chrome.js').then(m => m.refreshDueBadge())")
    expect(shop.locator(".module-due")).to_have_text("2")
    # A line goes to its list.
    rail.locator('[data-place="list:work"]').click()
    page.wait_for_url(re.compile(r"/tasks\?list=work$"))
    expect(page.locator('.lists-rail [aria-current]')).to_have_attribute("data-place", "list:work")
    page.locator(".lists-rail-all").click()
    page.wait_for_url(re.compile(r"/tasks$"))
    expect(page.locator(".lists-rail-all")).to_have_attribute("aria-current", "page")
    # Narrower: the row of pills again, no column.
    page.set_viewport_size(DESKTOP)
    expect(rail).to_be_hidden()
    expect(page.locator(".tasks-places")).to_be_visible()


def test_your_lists_and_other_lists(new_page, wiki, monkeypatch):
    from tests import settings
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", ""), "bob": ("pw", "")})
    page = new_page(viewport=BIG)
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    make_list(page, "mine", private=True)
    page.goto("/tasks")
    rail = page.locator(".lists-rail")
    expect(rail.locator(".lists-rail-head")).to_have_text(["Your lists", "Other lists"])
    # Hers (a private list) first, then the lists for everyone she has nothing in.
    rest = rail.locator(".lists-rail-rest .lists-rail-item, .lists-rail-rest .lists-rail-head").all_text_contents()
    assert rest.index("mine") < rest.index("Other lists") < rest.index("team"), rest
    expect(rail.locator('[data-place="list:mine"] .tasks-place-lock')).to_have_count(1)


def test_dragging_a_pin_in_the_column_reorders_the_pins(new_page, wiki):
    page = new_page(viewport=BIG)
    for name in ("a", "b", "c"):
        make_list(page, name, pin=True)
    page.goto("/tasks")
    line = lambda name: page.locator(f'.lists-rail [data-place="list:{name}"]')
    expect(line("a")).to_have_attribute("draggable", "true")
    expect(page.locator('.lists-rail [data-place="mine"]')).not_to_have_attribute("draggable", "true")
    with page.expect_response("**/tasks/pin-order") as saved:
        line("c").drag_to(line("a"), target_position={"x": 20, "y": 2})     # onto a's top half: above it
    assert saved.value.ok
    expect(rail_names(page)).to_have_text(["My tasks", "Inbox", "c", "a", "b", "All tasks"])
    assert '"pinned": ["c", "a", "b"]' in (wiki.root / "_tasks/lists.json").read_text()


def test_the_column_finds_a_list_and_marks_new_ones(new_page, wiki):
    page = new_page(viewport=BIG)
    for i in range(8):
        make_list(page, f"list{i}")
    page.goto("/tasks")
    expect(page.locator(".lists-rail .lists-more-new")).to_have_count(0)    # a first visit knows them all
    field = page.locator(".lists-rail-filter")
    field.fill("list3")
    expect(page.locator(".lists-rail-rest .lists-rail-item:visible")).to_have_text(["list3"])
    expect(page.locator(".lists-rail-all")).to_be_visible()
    field.press("Enter")
    page.wait_for_url(re.compile(r"/tasks\?list=list3$"))
    # Made by someone else since: new beside it, and seen once the column showed it.
    make_list(page, "garden")
    page.goto("/tasks")
    expect(page.locator('.lists-rail [data-place="list:garden"] .lists-more-new')).to_have_text("new")
    expect(page.locator(".lists-more-btn")).not_to_have_class(re.compile("has-new"))
    page.reload()
    expect(page.locator(".lists-rail .lists-more-new")).to_have_count(0)
