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


def test_clearing_the_filter_drops_a_pending_content_search(page, wiki):
    # Regression: a /api/search still in flight when the filter was cleared
    # brought its "Content matches" back under the empty filter.
    seed(wiki)
    held = []
    page.route("**/api/search*", lambda route: held.append(route))
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes")
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "content search never started"
    page.locator("#modal-filter").fill("")
    held[0].continue_()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(200)
    expect(page.locator("#modal-content-matches .search-result-card")).to_have_count(0)
    expect(page.locator("#modal-content-matches")).to_be_hidden()


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


def test_content_matches_show_folder_and_date(page, wiki):
    wiki.write("conversations/alex/prep", "# Alex Debrief\n\nquokka notes\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("quokka")
    meta = page.locator("#modal-content-matches .search-result-card .result-meta")
    expect(meta).to_have_count(1)
    expect(meta.first).to_have_text(re.compile(r"^conversations/alex · edited \d{4}-\d\d-\d\d$"))


def test_all_results_link_when_there_are_more(page, wiki):
    for i in range(17):
        wiki.write(f"walrus-{i}", f"# Walrus {i}\n\nwalrus notes\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("walrus")
    expect(page.locator("#modal-content-matches .search-result-card")).to_have_count(15)
    page.locator("#modal-content-matches .search-more").click()
    page.wait_for_url("**/search?q=walrus")
    expect(page.locator(".search-results .search-result-card")).to_have_count(17)
    expect(page.locator(".search-count")).to_have_text("17 results")


def test_no_all_results_link_when_everything_is_shown(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes")
    expect(page.locator("#modal-content-matches .search-result-card")).to_have_count(2)
    expect(page.locator("#modal-content-matches .search-more")).to_have_count(0)


def test_date_filters_stay_out_of_the_title_list(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes after:2000")
    expect(page.locator("#modal-page-list .page-list-item")).to_have_count(0)   # no title has "tomatoes"
    expect(page.locator("#modal-content-matches .search-result-card")).to_have_count(2)
    page.locator("#modal-filter").fill("garage after:2000")
    expect(page.locator("#modal-page-list .page-list-item[data-slug='garage']")).to_have_count(1)


def test_unlinked_only_filters_content_matches(page, wiki):
    # Regression: the checkbox narrowed the title list but not the content
    # matches below it.
    seed(wiki)
    wiki.write("hub", "# Hub\n\n[[gardening]]\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes")
    cards = page.locator("#modal-content-matches .search-result-card")
    expect(cards).to_have_count(2)  # gardening and recipes
    page.locator("#modal-unlinked").check()
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text("Recipes")
    page.locator("#modal-unlinked").uncheck()
    expect(cards).to_have_count(2)


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
    phone.locator(".action-search").tap()
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


def test_query_syntax_in_the_modal(page, wiki):
    # Titles match the query's words only; content matches get the whole
    # query, operators and all, which the server applies.
    wiki.write("tomato-notes", "---\ntags: veg\n---\n# Tomato notes\n\nTomatoes need sun.\n")
    wiki.write("tomato-sauce", "# Tomato sauce\n\nTomatoes and garlic.\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomato -garlic")
    titles = page.locator("#modal-page-list .page-list-item")
    expect(titles).to_have_count(2)  # both titles match "tomato"
    cards = page.locator("#modal-content-matches .search-result-card")
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text("Tomato notes")
    page.locator("#modal-filter").fill("#veg")
    expect(titles).to_have_count(0)  # nothing to match titles against
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text("Tomato notes")


def test_equal_title_scores_prefer_the_main_page(page, wiki):
    # Regression: every title starting with "alex" scored the same, and the
    # alphabetical order put a deep conversation note before the person's
    # page. Ties now go to non-scratch pages, then fewer folders, then the
    # shorter name.
    wiki.write("conversations/situations/alex-1on1/prep", "# Alex 1:1 Kickoff\n\nx\n")
    wiki.write("conversations/people/alex", "# Alex — People Profile\n\nx\n")
    wiki.write("2026-07-09_06-12-09_alex_prep", "notes\n")
    wiki.write("alex-morgan", "# Alex Morgan\n\nx\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("alex")
    slugs = page.locator("#modal-page-list .page-list-item")
    expect(slugs).to_have_count(4)
    assert slugs.evaluate_all("els => els.map(e => e.dataset.slug)") == [
        "alex-morgan", "conversations/people/alex",
        "conversations/situations/alex-1on1/prep", "2026-07-09_06-12-09_alex_prep"]


def test_file_name_counts_like_the_title(page, wiki):
    # A file name starting with the query ("team/people/alex") beats a match
    # only in a folder name ("alex-notes/prep"), even though that page sits
    # in fewer folders; the file name part is what gets highlighted.
    wiki.write("alex-notes/prep", "# Prep for the talk\n\nx\n")
    wiki.write("team/people/alex", "# Profile\n\nx\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("alex")
    items = page.locator("#modal-page-list .page-list-item")
    expect(items).to_have_count(2)
    assert items.evaluate_all("els => els.map(e => e.dataset.slug)") == ["team/people/alex", "alex-notes/prep"]
    expect(items.first.locator(".page-slug mark")).to_have_text("alex")


def test_several_words_match_word_by_word(page, wiki):
    # Regression: "alex again" fuzzy-matched its letters spread over a long
    # title ("Add alert exporter tag handling") and listed it first, above the
    # note that says "Alex again". Each word now matches within one word.
    wiki.write("bug-10001", "# BUG-10001 - Add alert exporter tag handling\n\nx\n")
    wiki.write("situation", "# Debrief Sam\n\nThen Alex again pushed back.\n")
    wiki.write("alex-morgan", "# Alex Morgan\n\nx\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("alex again")
    expect(page.locator("#modal-content-matches .search-result-card").first).to_contain_text("Debrief Sam")
    expect(page.locator("#modal-page-list .page-list-item")).to_have_count(0)
    page.locator("#modal-filter").fill("alex morg")
    items = page.locator("#modal-page-list .page-list-item")
    expect(items).to_have_count(1)
    expect(items.first).to_have_attribute("data-slug", "alex-morgan")
    expect(items.first.locator("a mark")).to_have_text(["Alex", "Morg"])


# ── Highlights on a page opened from search ────────────────────────────────


def test_highlights_step_and_clear(page, wiki):
    wiki.write("notes", "# Notes\n\nThen Alex   again. She runs; it reruns.\n\n"
                        "```\nruns in code\n```\n\nRUNS at the end.\n")
    page.goto("/page/notes?hl=Alex+again&hl=runs")
    marks = page.locator("article mark.search-hl")
    expect(marks).to_have_text(["Alex   again", "runs", "RUNS"])   # not "reruns", not in code
    bar = page.locator(".search-hl-bar")
    expect(bar).to_contain_text("1 of 3")
    expect(marks.nth(0)).to_have_class("search-hl current")
    page.keyboard.press("n")
    expect(bar).to_contain_text("2 of 3")
    page.keyboard.press("Shift+N")
    page.keyboard.press("Shift+N")
    expect(bar).to_contain_text("3 of 3")
    page.locator(".search-hl-bar button[aria-label='Next match']").click()
    expect(bar).to_contain_text("1 of 3")
    page.keyboard.press("Escape")
    expect(marks).to_have_count(0)
    expect(bar).to_have_count(0)
    assert page.url.endswith("/page/notes")
    expect(page.locator("article")).to_contain_text("Then Alex again.")


def test_highlights_start_in_the_linked_section(page, wiki):
    wiki.write("long", "# Long\n\n## One\n\nzebra here.\n\n## Two\n\nAnother zebra.\n")
    page.goto("/page/long?hl=zebra#two")
    expect(page.locator(".search-hl-bar")).to_contain_text("2 of 2")


def test_n_does_not_reach_the_deck_while_highlighting(page, wiki):
    wiki.write("tasks", "# Tasks\n\nzebra notes.\n\n[[deck]]\n")
    page.goto("/page/tasks?hl=zebra")
    expect(page.locator(".search-hl-bar")).to_contain_text("1 of 1")
    page.locator(".tasks-app").wait_for()
    page.keyboard.press("n")
    expect(page.locator("dialog[open]")).to_have_count(0)


def test_search_result_opens_highlighted(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes")
    page.locator("#modal-content-matches .search-result-card", has_text="Recipes").click()
    page.wait_for_url("**/page/recipes?hl=*")
    expect(page.locator("article mark.search-hl")).to_have_text(["tomatoes"])
