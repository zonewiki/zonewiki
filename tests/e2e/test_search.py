"""Search modal: open/close, fuzzy page filter, content matches, keyboard nav."""

import re

from playwright.sync_api import expect

from conftest import TOUCH_PHONE


def seed(wiki):
    wiki.write("gardening", "# Gardening\n\nTomatoes need sun.\n")
    wiki.write("garage", "# Garage\n\nBikes and tools.\n")
    wiki.write("recipes", "# Recipes\n\nSauce from fresh tomatoes.\n")


def test_ctrl_p_opens_and_esc_closes(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    modal = page.locator("#search-modal")
    expect(modal).to_be_hidden()
    page.keyboard.press("Control+p")
    expect(modal).to_be_visible()
    expect(page.locator("#modal-filter")).to_be_focused()
    # Lazy-loaded page list: all pages plus the synthetic special pages.
    expect(page.locator("#modal-page-list .page-list-item[data-slug='recipes']")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()


def test_fuzzy_filter_ranks_and_highlights(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("gard")
    items = page.locator("#modal-page-list .page-list-item")
    expect(items.first).to_have_attribute("data-slug", "gardening")
    expect(items.first).to_have_class("page-list-item active")
    expect(items.first.locator("mark").first).to_be_visible()
    expect(page.locator("#modal-page-list .page-list-item[data-slug='recipes']")).to_have_count(0)


def test_content_matches_appear_after_debounce(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes")
    cards = page.locator("#modal-content-matches .search-result-card")
    expect(cards).to_have_count(2)
    expect(cards.first.locator("mark").first).to_have_text("Tomatoes", ignore_case=True)


def test_enter_opens_highlighted_and_arrows_move(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("ga")
    items = page.locator("#modal-page-list .page-list-item")
    expect(items).to_have_count(2)
    second_slug = items.nth(1).get_attribute("data-slug")
    page.keyboard.press("ArrowDown")
    expect(items.nth(1)).to_have_class("page-list-item active")
    page.keyboard.press("Enter")
    page.wait_for_url(f"**/page/{second_slug}")


def test_unlinked_only(page, wiki):
    seed(wiki)
    wiki.write("hub", "# Hub\n\n[[gardening]]\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    expect(page.locator("#modal-page-list .page-list-item[data-slug='gardening']")).to_have_count(1)
    page.locator("#modal-unlinked").check()
    expect(page.locator("#modal-page-list .page-list-item[data-slug='gardening']")).to_have_count(0)
    expect(page.locator("#modal-page-list .page-list-item[data-slug='garage']")).to_have_count(1)


def test_empty_query_lists_recently_edited_first_and_remembers_sort(page, wiki):
    import os
    seed(wiki)
    # garage newest, then recipes, then gardening
    for md in wiki.root.glob("*.md"):   # everything else (index…) is older
        os.utime(md, (1_000_000_000, 1_000_000_000))
    for age, slug in ((300, "gardening"), (200, "recipes"), (100, "garage")):
        t = 1_900_000_000 - age
        os.utime(wiki.root / f"{slug}.md", (t, t))
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    expect(page.locator("#modal-sort")).to_have_value("mtime-desc")
    items = page.locator("#modal-page-list .page-list-item:not(.page-list-special)")
    expect(items.first).to_have_attribute("data-slug", "garage")
    expect(items.nth(1)).to_have_attribute("data-slug", "recipes")

    # A sort picked by hand sticks across page loads.
    page.locator("#modal-sort").select_option("title-asc")
    page.reload()
    page.keyboard.press("Control+p")
    expect(page.locator("#modal-sort")).to_have_value("title-asc")


def test_empty_query_pins_built_in_pages_first(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    items = page.locator("#modal-page-list .page-list-item")
    expect(items.nth(0).locator("a")).to_have_text("Deck")
    expect(items.nth(1).locator("a")).to_have_text("Scratch")
    expect(items.nth(2).locator("a")).to_have_text("Deleted Pages")   # git is on
    expect(items.nth(3)).not_to_have_class(re.compile(r"page-list-special"))
    # Whatever the sort.
    page.locator("#modal-sort").select_option("title-desc")
    expect(items.nth(1).locator("a")).to_have_text("Scratch")
    # Typing matches them like any other entry; "Unlinked only" (orphan
    # pages) leaves them out.
    page.locator("#modal-filter").fill("scra")
    expect(items.first.locator("a")).to_have_text("Scratch")
    page.locator("#modal-filter").fill("")
    page.locator("#modal-unlinked").check()
    expect(page.locator("#modal-page-list .page-list-special")).to_have_count(0)


def test_phone_reaches_scratch_list_in_two_taps(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    phone.locator(".mobile-search").tap()
    phone.locator("#modal-page-list .page-list-special a", has_text="Scratch").tap()
    phone.wait_for_url("**/page/_scratch")


def test_task_result_opens_the_deck_page(page, wiki):
    # Regression: with no deck.md, opening a task from Ctrl+P landed on
    # /edit/deck, an empty new page. It now opens the built-in /deck, whatever
    # pages embed a deck.
    wiki.write("tasks", "# Tasks\n\n[[deck]]\n")
    tid = page.request.post("/api/deck", data={"title": "Renew passport"}).json()["id"]
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("passport")
    card = page.locator("#modal-content-matches .search-result-card").first
    expect(card).to_be_visible()
    card.click()
    page.wait_for_url(f"**/deck?task={tid}&view=open")
    expect(page.locator(f".task-item[data-id='{tid}']")).to_have_class(
        re.compile(r"\btask-selected\b"))
