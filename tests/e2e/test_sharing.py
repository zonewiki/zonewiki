"""Sharing mode: for sharing the screen, everything that shows other pages
around the one on screen is hidden; the page itself stays."""

import re

from playwright.sync_api import expect


def sharing_on(page):
    """Switch sharing mode on (S) and wait for the page, reloaded for it.
    Waiting for html.sharing is not enough: the server renders it before
    leaf.js has loaded, and a key pressed then is lost."""
    with page.expect_navigation():
        page.keyboard.press("Shift+S")
    expect(page.locator("html.sharing")).to_have_count(1)


def _wiki(wiki):
    wiki.write("release", "# Release\n\nSee [[Rollout]].\n\n## Plan\n\nText.\n")
    wiki.write("rollout", "# Rollout\n\nBack to [[Release]].\n")
    wiki.write("q4", "# Q4\n\n[[Release]] in Q4.\n")


def test_sharing_mode_hides_the_surroundings(new_page, wiki):
    _wiki(wiki)
    page = new_page(viewport={"width": 1600, "height": 900})
    page.request.post("/api/deck", data={"title": "Ship [[Release#Plan]]"})
    for url in ("/page/q4", "/page/rollout", "/page/release"):
        page.goto(url)
    hidden = (".trail-link", ".rail-context .backlinks", ".section-tasks")
    expect(page.locator(".trail-link")).to_have_count(2)
    for sel in hidden:
        expect(page.locator(sel).first).to_be_visible()
    expect(page.locator("html.sharing")).to_have_count(0)
    sharing_on(page)
    expect(page.locator(".top-sharing")).to_have_attribute("aria-pressed", "true")
    for sel in hidden:
        expect(page.locator(sel).first).to_be_hidden()
    # The page itself stays: title, text, its own table of contents.
    expect(page.locator(".page-header h1")).to_have_text("Release")
    expect(page.locator(".page-content")).to_contain_text("See Rollout.")
    # A thin bar across the top says it is on.
    bar = page.evaluate("getComputedStyle(document.body, '::before').height")
    assert bar == "3px"
    # Remembered: a reload (and another page) keeps it, from the first paint.
    page.reload()
    expect(page.locator("html.sharing")).to_have_count(1)
    expect(page.locator(".trail-link").first).to_be_hidden()
    # The eye switches it off again.
    page.locator(".top-sharing").click()
    expect(page.locator("html.sharing")).to_have_count(0)
    expect(page.locator(".trail-link").first).to_be_visible()


def test_sharing_mode_search_lists_nothing_until_typed(new_page, wiki):
    _wiki(wiki)
    page = new_page(viewport={"width": 1600, "height": 900})
    page.goto("/page/release")
    sharing_on(page)
    page.keyboard.press("Control+p")
    expect(page.locator("#search-modal")).to_be_visible()
    expect(page.locator("#modal-page-list .page-list-item")).to_have_count(0)
    page.keyboard.press("ArrowDown")
    expect(page.locator("#modal-preview .search-preview-title")).to_have_count(0)
    page.locator("#modal-filter").fill("roll")                  # typed: found as usual
    expect(page.locator("#modal-page-list .page-list-item").first).to_contain_text("Rollout")


def test_sharing_mode_editor_page_list_waits_for_letters(new_page, wiki):
    _wiki(wiki)
    page = new_page(viewport={"width": 1600, "height": 900})
    page.goto("/page/release")
    sharing_on(page)
    page.goto("/edit/release")
    page.locator("#editor-textarea").press("Control+End")
    page.keyboard.type("\n[[")
    expect(page.locator(".wikilink-ac")).to_be_hidden()
    page.keyboard.type("ro")
    expect(page.locator(".wikilink-ac")).to_contain_text("rollout")


def test_no_sharing_switch_in_the_way_on_phones(new_page, wiki):
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    expect(phone.locator(".top-sharing")).to_be_hidden()


def test_sharing_mode_hides_the_backlinks_chip_and_page_end(new_page, wiki):
    # Below the side columns' width the links are at the page's end, and the
    # breadcrumb has the backlinks chip.
    _wiki(wiki)
    page = new_page(viewport={"width": 1100, "height": 900})
    page.goto("/page/release")
    expect(page.locator(".bl-count")).to_be_visible()
    expect(page.locator(".page-foot .backlinks").first).to_be_visible()
    sharing_on(page)
    expect(page.locator(".bl-count")).to_be_hidden()
    expect(page.locator(".page-foot")).to_be_hidden()


def test_noshare_pages_hidden_in_sharing_mode(new_page, wiki):
    # Pages tagged #noshare (LEAF_NOSHARE's default) are left out: their
    # page shows "Hidden page", links to them are plain words, search and
    # the editor's completion don't offer them.
    wiki.write("bob", "---\ntags: noshare\n---\n# Bob Private\n\nNotes about Bob.\n")
    wiki.write("team", "# Team\n\nTalk to [[Bob]] about notes.\n")
    page = new_page(viewport={"width": 1400, "height": 900})
    page.goto("/page/team")
    expect(page.locator(".page-content a", has_text="Bob")).to_have_count(1)
    sharing_on(page)
    expect(page.locator(".page-content a", has_text="Bob")).to_have_count(0)
    expect(page.locator(".page-content .wikilink-hidden")).to_have_text("Bob")
    page.goto("/page/bob")
    expect(page.locator("h1")).to_have_text("Hidden page")
    expect(page.locator("body")).not_to_contain_text("Bob Private")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("notes")
    expect(page.locator(".search-result-card").first).to_contain_text("Team")
    expect(page.locator(".search-result-card", has_text="Bob Private")).to_have_count(0)
    # Off again: everything is back.
    page.keyboard.press("Escape")
    page.locator(".top-sharing").click()
    expect(page.locator("h1")).to_have_text("Bob Private")
