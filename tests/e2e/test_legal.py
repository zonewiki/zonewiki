"""The imprint and privacy links in a browser (docs/spec/users.md): under
the login form, on a phone too, and under a page of a wiki without a login."""

from playwright.sync_api import expect

from tests import settings
from tests.e2e.conftest import PHONE, box_of
from tests.e2e.test_layout import horizontal_overflow

BOTH = "imprint = https://example.org/imprint.html\nprivacy = /page/privacy\n"


def test_under_the_login_form_on_a_phone(new_page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "")})
    monkeypatch.setattr(settings, "CONFIG_FILE", BOTH)
    pg = new_page(viewport=PHONE)
    pg.goto("/login")
    imprint, privacy = pg.get_by_role("link", name="Imprint"), pg.get_by_role("link", name="Privacy")
    expect(imprint).to_have_attribute("href", "https://example.org/imprint.html")
    expect(privacy).to_have_attribute("href", "/page/privacy")
    form, a, b = box_of(pg.locator(".login-form")), box_of(imprint), box_of(privacy)
    assert a["y"] >= form["y"] + form["height"] and abs(a["y"] - b["y"]) < 2 and a["x"] + a["width"] < b["x"]
    assert horizontal_overflow(pg) <= 0
    # Signed in, they are gone.
    pg.fill("[name=username]", "alice")
    pg.fill("[name=password]", "pw")
    pg.get_by_role("button", name="Sign in").click()
    expect(pg.locator("h1")).to_be_visible()
    expect(pg.locator(".wiki-legal")).to_have_count(0)


def test_under_a_page_of_a_wiki_without_a_login(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", BOTH)
    wiki.write("garden", "# Garden\n\nBeds.\n")
    page.goto("/page/garden")
    text, legal = box_of(page.locator("article").first), box_of(page.locator(".wiki-legal"))
    assert legal["y"] >= text["y"] + text["height"]
    page.goto("/edit/garden")
    expect(page.locator(".editor-wrap")).to_be_visible()
    expect(page.locator(".wiki-legal")).to_have_count(0)
