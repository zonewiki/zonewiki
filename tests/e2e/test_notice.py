"""The notice setting's line in a browser (docs/spec/users.md): across the
top, and what is fixed at the top moves down by its height, the editor
and the badges too, so it covers nothing."""

from playwright.sync_api import expect

from tests import settings
from tests.e2e.conftest import box_of

NOTICE = "This demo is put back at every full hour"


def bottom(locator):
    box = box_of(locator)
    return box["y"] + box["height"]


def test_it_covers_nothing_on_a_page_or_in_the_editor(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {NOTICE}\n")
    wiki.write("garden", "# Garden\n\nBeds.\n")
    page.goto("/page/garden")
    notice = page.locator(".wiki-notice")
    expect(notice).to_have_text(NOTICE)
    box = box_of(notice)
    assert box["y"] == 0 and box["width"] == page.viewport_size["width"]
    assert box_of(page.locator("h1"))["y"] >= bottom(notice)
    assert box_of(page.locator(".breadcrumb"))["y"] >= bottom(notice)
    page.goto("/edit/garden")
    expect(page.locator(".wiki-notice")).to_have_text(NOTICE)
    wrap = box_of(page.locator(".editor-wrap"))
    assert abs(wrap["y"] - bottom(page.locator(".wiki-notice"))) < 1
    assert wrap["y"] + wrap["height"] <= page.viewport_size["height"] + 1   # still fills the screen, no more
    assert box_of(page.locator("#editor-textarea"))["y"] >= bottom(page.locator(".wiki-notice"))


def test_the_badges_sit_under_it(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {NOTICE}\n")
    monkeypatch.setattr(settings, "USERS", {"demo": ("demo-pw", "")})
    page.request.post("/login", form={"username": "demo", "password": "demo-pw"})
    page.set_viewport_size({"width": 390, "height": 780})
    page.goto("/page/index")
    notice = page.locator(".wiki-notice")
    expect(notice).to_be_visible()
    assert box_of(page.locator(".top-badges"))["y"] >= bottom(notice)


def test_a_long_one_stays_one_line(page, wiki, monkeypatch):
    long = "Maintenance tonight from 22:00 to 23:00 UTC: save your work before then, " * 2
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {long.strip()}\n")
    page.set_viewport_size({"width": 390, "height": 780})
    page.goto("/page/index")
    notice = page.locator(".wiki-notice")
    assert box_of(notice)["height"] < 40
    expect(notice).to_have_attribute("title", long.strip())


def test_without_one_nothing_moves(page, wiki):
    page.goto("/edit/index")
    expect(page.locator(".wiki-notice")).to_have_count(0)
    assert box_of(page.locator(".editor-wrap"))["y"] == 0
