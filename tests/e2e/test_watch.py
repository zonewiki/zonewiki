"""Watching a page in the browser (docs/spec/navigation.md, Watching pages):
Watch in the page's menu, the dot on the user button and the count in its
menu when someone else changed the page, the row marked new in Changes."""

import pytest
from playwright.sync_api import expect

from conftest import wait_ready
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    return page


def bobs_change(wiki, slug, text):
    wiki.write(slug, text)
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", f"Update {slug}", "--author", "bob <bob@example.org>")


def test_watch_a_page_and_see_what_changed(alice, wiki):
    wiki.write("roadmap", "# Roadmap\n\nQ1.\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    alice.goto("/page/roadmap")
    dot = alice.locator(".top-user-new")
    expect(dot).to_be_hidden()
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Watch", exact=True).click()
    alice.wait_for_url("**/page/roadmap")
    wait_ready(alice)
    alice.locator(".page-menu-btn").first.click()
    expect(alice.get_by_role("menuitem", name="Unwatch")).to_be_visible()
    alice.keyboard.press("Escape")
    # bob changes it: on another page the button has a dot, its menu the count.
    bobs_change(wiki, "roadmap", "# Roadmap\n\nQ1 and Q2.\n")
    alice.goto("/page/index")
    expect(dot).to_be_visible()
    alice.locator(".top-user").click()
    entry = alice.get_by_role("menuitem", name="Watched pages")
    expect(entry.locator(".menu-count")).to_have_text("1")
    entry.click()
    alice.wait_for_url("**/changes?watched=1")
    wait_ready(alice)
    row = alice.locator(".changes-row").first
    expect(row).to_contain_text("Update roadmap")
    expect(row.locator(".changes-new")).to_have_text("new")
    expect(alice.locator(".changes-watching")).to_contain_text("You watch 1 thing")
    # What the change did reads it: no dot, no mark.
    row.get_by_role("link", name="roadmap").click()
    alice.wait_for_url("**/diff/roadmap/**")
    wait_ready(alice)
    expect(dot).to_be_hidden()
    alice.goto("/changes?watched=1")
    expect(alice.locator(".changes-new")).to_have_count(1)        # the word in the intro only
    expect(alice.locator(".changes-row .changes-new")).to_have_count(0)
    # The count is kept current without a reload (the minute's asking, here by hand).
    bobs_change(wiki, "roadmap", "# Roadmap\n\nQ1 to Q3.\n")
    alice.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(dot).to_be_visible()
