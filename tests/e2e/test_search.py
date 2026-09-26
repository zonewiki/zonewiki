"""Search modal: open/close, fuzzy page filter, content matches, keyboard nav."""

from playwright.sync_api import expect


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
