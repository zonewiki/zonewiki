"""Search modal: open/close, fuzzy page filter, content matches, keyboard nav."""

import re

from playwright.sync_api import expect

from conftest import TOUCH_PHONE, box_of, wait_until
from tests import settings


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
    wait_until(page, lambda: held, settled=False)   # the content search has started
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


def test_empty_query_pins_deleted_pages_and_leaves_out_modules(page, wiki):
    # Tasks and Notes are modules, in the module switcher: the empty-query
    # list leaves them out, typing finds them. Changes and Deleted Pages stay pinned.
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    items = page.locator("#modal-page-list .page-list-item")
    expect(items.nth(0).locator("a")).to_have_text("Changes")         # git is on
    expect(items.nth(1).locator("a")).to_have_text("Deleted Pages")
    expect(items.nth(2)).not_to_have_class(re.compile(r"page-list-special"))
    expect(items.locator("a", has_text=re.compile(r"^(Tasks|Notes)$"))).to_have_count(0)
    # Whatever the sort.
    page.locator("#modal-sort").select_option("title-desc")
    expect(items.nth(0).locator("a")).to_have_text("Changes")
    expect(items.nth(1).locator("a")).to_have_text("Deleted Pages")
    # Typing matches the built-in pages like any other entry; "Unlinked
    # only" (orphan pages) leaves them out.
    page.locator("#modal-filter").fill("notes")
    expect(items.first.locator("a")).to_have_text("Notes")
    expect(items.first.locator("a")).to_have_attribute("href", "/notes")
    page.locator("#modal-filter").fill("tasks")
    expect(items.first.locator("a")).to_have_attribute("href", "/tasks")
    page.locator("#modal-filter").fill("")
    page.locator("#modal-unlinked").check()
    expect(page.locator("#modal-page-list .page-list-special")).to_have_count(0)
    expect(items.locator("a", has_text=re.compile(r"^(Tasks|Notes)$"))).to_have_count(0)


def test_phone_reaches_quicknote_list_in_one_tap(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    phone.locator(".action-buttons [data-module=notes]").tap()
    phone.wait_for_url("**/notes")


def test_task_result_opens_the_tasks_page(page, wiki):
    # Regression: with no tasks.md, opening a task from Ctrl+P landed on
    # an empty new page's editor. It now opens the built-in /tasks, whatever
    # pages embed a task list.
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    tid = page.request.post("/api/tasks", data={"title": "Renew passport"}).json()["id"]
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("passport")
    card = page.locator("#modal-content-matches .search-result-card").first
    expect(card).to_be_visible()
    card.click()
    page.wait_for_url(f"**/tasks?task={tid}&view=open")
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
    # page. Ties now go to pages that are no notes, then fewer folders, then the
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


def test_n_does_not_reach_the_tasks_while_highlighting(page, wiki):
    wiki.write("tasks", "# Tasks\n\nzebra notes.\n\n[[tasks:]]\n")
    page.goto("/page/tasks?hl=zebra")
    expect(page.locator(".search-hl-bar")).to_contain_text("1 of 1")
    page.locator(".tasks-app").wait_for()
    page.keyboard.press("n")
    expect(page.locator("dialog[open]")).to_have_count(0)


def test_first_esc_clears_only_the_highlights(page, wiki):
    # A task list's Esc clears its selection; while highlights show, the first
    # Esc is theirs alone, the second the task list's.
    page.request.post("/api/tasks", data={"title": "Picked"})
    wiki.write("tasks", "# Tasks\n\nzebra notes.\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none&hl=zebra")
    expect(page.locator(".search-hl-bar")).to_contain_text("1 of 1")
    picked = page.locator(".task-item", has_text="Picked")
    picked.wait_for()
    page.keyboard.press("j")
    expect(picked).to_have_class(re.compile(r"\btask-selected\b"))
    page.keyboard.press("Escape")
    expect(page.locator(".search-hl-bar")).to_have_count(0)
    expect(picked).to_have_class(re.compile(r"\btask-selected\b"))
    page.keyboard.press("Escape")
    expect(picked).not_to_have_class(re.compile(r"\btask-selected\b"))


def test_search_result_opens_highlighted(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tomatoes")
    page.locator("#modal-content-matches .search-result-card", has_text="Recipes").click()
    page.wait_for_url("**/page/recipes?hl=*")
    expect(page.locator("article mark.search-hl")).to_have_text(["tomatoes"])


# ── Notes filter ───────────────────────────────────────────────────────────
# The Notes page's filter box: the search, over notes only.

def test_quicknote_filter_uses_the_search(page, wiki):
    wiki.write("2026-09-01_10-00-00_bob-call", "# Bob call\n\n## Quote\n\nRoof repair, running late.\n")
    wiki.write("2026-09-02_10-00-00_groceries", "# Groceries\n\nMilk, eggs.\n")
    wiki.write("2026-09-03_10-00-00_garden", "# Garden\n\nThe roof of the shed.\n")
    wiki.write("roof", "# Roof\n\nThe roof repair plan.\n")   # a page, not a note
    page.goto("/notes")
    items = page.locator(".notes-list li:not([hidden])")
    expect(items).to_have_count(3)
    expect(items.locator("a")).to_have_text(["Garden", "Groceries", "Bob call"])   # newest first

    box = page.locator("#notes-filter")
    box.fill("roof repair")
    # Ranked like the search (stems: "runs" finds "running"), excerpts marked.
    expect(items.locator("a")).to_have_text(["Bob call", "Garden"])
    expect(page.locator("#notes-filter-count")).to_have_text("2 of 3")
    expect(items.first.locator(".note-excerpt mark").first).to_have_text(re.compile("roof", re.I))
    expect(page).to_have_url(re.compile(r"/notes\?q=roof\+repair$"))

    box.fill('"roof repair"')   # the query syntax works
    expect(items.locator("a")).to_have_text(["Bob call"])
    box.fill("zzzqqq")
    expect(items).to_have_count(0)
    expect(page.locator(".notes-filter-empty")).to_be_visible()

    box.press("Escape")   # clears: the full list, newest first, again
    expect(items.locator("a")).to_have_text(["Garden", "Groceries", "Bob call"])
    expect(page.locator(".note-excerpt")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/notes$"))

    # Enter opens the best match at the section that matched, highlighted.
    box.fill("runs late")
    expect(items.locator("a")).to_have_text(["Bob call"])
    box.press("Enter")
    page.wait_for_url(re.compile(r"/page/2026-09-01_10-00-00_bob-call\?hl=.*#quote$"))
    expect(page.locator("mark.search-hl").first).to_be_visible()
    # Back returns to the filtered list.
    page.go_back()
    expect(page.locator("#notes-filter")).to_have_value("runs late")
    expect(items.locator("a")).to_have_text(["Bob call"])


def test_preview_pane_on_wide_screens(new_page, wiki):
    # The highlighted result shows on the right: a page from its section on,
    # the words marked; moving the highlight moves the preview.
    wiki.write("garden", "# Garden\n\n## Beds\n\nSoil.\n\n" + "Filler text. " * 300 + "\n\n## Compost\n\nTurn the compost heap weekly.\n\n" + "More text. " * 300 + "\n")
    wiki.write("kitchen", "# Kitchen\n\nCompost bin under the sink.\n")
    page = new_page(viewport={"width": 1400, "height": 900})
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("compost heap")
    preview = page.locator("#modal-preview")
    expect(preview.locator(".search-preview-title")).to_have_text("Garden")
    expect(preview.locator("mark.search-hl").first).to_be_visible()   # scrolled to the section
    heading = box_of(preview.locator('[data-pid="compost"]'))
    pane = box_of(preview)
    assert pane["y"] <= heading["y"] < pane["y"] + pane["height"] / 2
    assert page.locator("#compost").count() == 0                       # no ids of its own
    page.keyboard.press("ArrowDown")
    expect(preview.locator(".search-preview-title")).not_to_have_text("Garden")


def test_the_filters_sit_above_the_results_on_wide_screens(new_page, wiki):
    # Regression: the wide dialog's grid had a row for the controls and one
    # for the results; the filters' row took the second, and the results and
    # the preview fell into a row of their own below the dialog's height,
    # the content matches running under the chips.
    for i in range(30):
        wiki.write(f"stone-{i}", f"# Stone {i}\n\nA stone wall, number {i}.\n")
    page = new_page(viewport={"width": 1400, "height": 800})
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("stone")
    expect(page.locator("#modal-content-matches .search-result-card").first).to_be_visible()
    # The chips themselves, not their row: the row collapsed to nothing while
    # its chips spilled out of it, under the results.
    dialog, chip = box_of(page.locator("#search-modal")), box_of(page.locator(".search-chip").first)
    results, preview = box_of(page.locator(".modal-results")), box_of(page.locator("#modal-preview"))
    assert box_of(page.locator(".search-filters"))["height"] >= chip["height"]
    assert chip["y"] + chip["height"] <= results["y"] + 1
    assert chip["y"] + chip["height"] <= preview["y"] + 1
    for box in (results, preview):    # inside the dialog, scrolling there
        assert box["y"] + box["height"] <= dialog["y"] + dialog["height"] + 1
    page.screenshot(path="tests/e2e/_screenshots/search-filters-wide.png")


def test_no_preview_pane_on_narrow_screens(new_page, wiki):
    wiki.write("garden", "# Garden\n\nCompost.\n")
    page = new_page(viewport={"width": 1000, "height": 800})
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("compost")
    expect(page.locator(".search-result-card").first).to_be_visible()
    expect(page.locator("#modal-preview")).to_be_hidden()
    assert box_of(page.locator("#search-modal"))["width"] <= 660


def test_preview_of_a_title_match(new_page, wiki):
    # Regression: a page found by its title has no words to mark; the empty
    # term list matched everywhere, scattering empty marks through the
    # preview and scrolling it to the first one.
    wiki.write("garden", "# Garden\n\n## Beds\n\n" + "Soil and more soil. " * 200 + "\n")
    page = new_page(viewport={"width": 1400, "height": 900})
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("gard")
    preview = page.locator("#modal-preview")
    expect(preview.locator(".search-preview-title")).to_have_text("Garden")
    expect(preview.locator("mark")).to_have_count(0)
    assert preview.evaluate("el => el.scrollTop") == 0


def test_the_dialog_says_whether_search_goes_by_meaning(page, wiki):
    seed(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    mode = page.locator("#search-mode")
    expect(mode).to_have_text("Keywords only")   # no semantic index in the tests
    expect(mode).to_have_attribute("title", re.compile("ZONE_SEMANTIC_SEARCH"))

    def as_if_indexed(route):
        resp = route.fetch()
        route.fulfill(response=resp, headers={**resp.headers, "x-zone-semantic": "on"})
    page.route("**/api/search*", as_if_indexed)
    page.locator("#modal-filter").fill("tomatoes")
    expect(mode).to_have_text("Keywords + meaning")
    expect(mode).to_have_attribute("data-state", "on")


def test_the_meaning_indicator_fits_the_dialog_on_a_phone(new_page, wiki):
    seed(wiki)
    page = new_page(**TOUCH_PHONE)
    page.goto("/page/index")
    page.evaluate("document.getElementById('search-modal').showModal()")
    dialog = box_of(page.locator("#search-modal"))
    mode = box_of(page.locator("#search-mode"))
    assert mode["width"] > 0
    assert dialog["x"] <= mode["x"] and mode["x"] + mode["width"] <= dialog["x"] + dialog["width"]
    page.screenshot(path="tests/e2e/_screenshots/search-mode-phone.png")


def test_chips_write_the_kind_into_the_query(page, wiki):
    # A chip writes is:… into the query, replacing the one there; All drops
    # it; typing is:… presses its chip. The title list and content matches
    # follow the query.
    wiki.write("heron", "# Heron\n\nThe heron waits.\n")
    wiki.write("2026-10-27_09-00-00_heron-sighting", "# Heron sighting\n\nA heron again.\n")
    page.request.post("/api/tasks", data={"title": "Photograph the heron"})
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    query = page.locator("#modal-filter")
    query.fill("heron")
    titles = page.locator("#modal-page-list .page-list-item")
    cards = page.locator("#modal-content-matches .search-result-card")
    expect(titles).to_have_count(2)
    expect(cards).to_have_count(3)
    chip = lambda kind: page.locator(f".search-chip[data-kind='{kind}']")
    expect(chip("")).to_have_attribute("aria-pressed", "true")

    chip("note").click()
    expect(query).to_have_value("heron is:note")
    expect(query).to_be_focused()
    expect(chip("note")).to_have_attribute("aria-pressed", "true")
    expect(chip("")).to_have_attribute("aria-pressed", "false")
    expect(titles).to_have_count(1)
    expect(titles.first).to_have_attribute("data-slug", "2026-10-27_09-00-00_heron-sighting")
    expect(cards).to_have_count(1)

    chip("task").click()
    expect(query).to_have_value("heron is:task")
    expect(titles).to_have_count(0)                    # tasks aren't in the title list
    expect(cards).to_have_count(1)
    expect(cards.first).to_contain_text("Photograph the heron")

    chip("").click()
    expect(query).to_have_value("heron")
    expect(cards).to_have_count(3)

    query.fill("heron is:page")
    expect(chip("page")).to_have_attribute("aria-pressed", "true")
    expect(titles).to_have_count(1)
    expect(titles.first).to_have_attribute("data-slug", "heron")

    # A task's status has no chip: it stays when a chip is clicked, and
    # empties the title list (pages have none).
    query.fill("heron is:open")
    expect(chip("")).to_have_attribute("aria-pressed", "true")
    expect(titles).to_have_count(0)
    expect(cards).to_have_count(1)
    chip("task").click()
    expect(query).to_have_value("heron is:open is:task")


def test_the_where_menu_writes_the_place(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    wiki.write("egret", "# Egret\n\nAn egret.\n")
    wiki.write("private/alice/egret-mine", "# My egret\n\nAn egret.\n")
    wiki.write("groups/dev/egret-dev", "# Dev egret\n\nAn egret.\n")
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    query = page.locator("#modal-filter")
    query.fill("egret")
    titles = page.locator("#modal-page-list .page-list-item")
    expect(titles).to_have_count(3)
    where = page.locator("#modal-where")
    expect(where.locator("option")).to_have_text(["Anywhere", "Everyone's", "🔒 Private", "👥 dev"])
    where.select_option("dev")
    expect(query).to_have_value("egret in:dev")
    expect(titles).to_have_count(1)
    expect(titles.first).to_have_attribute("data-slug", "groups/dev/egret-dev")
    expect(page.locator("#modal-content-matches .search-result-card")).to_have_count(1)
    where.select_option("private")
    expect(query).to_have_value("egret in:private")
    expect(titles.first).to_have_attribute("data-slug", "private/alice/egret-mine")
    query.fill("egret in:everyone")
    expect(where).to_have_value("everyone")
    expect(titles).to_have_count(1)
    where.select_option("")
    expect(query).to_have_value("egret")


def test_a_long_file_name_leaves_the_title_its_room(page, wiki):
    # Regression: the slug beside the title never shrank, so a long file
    # name squeezed the title to a few letters. The slug is cut at its
    # start now, the file name's end in sight.
    slug = "projects/2026/archive/" + "a-very-long-file-name-that-goes-on-and-on-" * 3 + "end"
    wiki.write(slug, "# Quarterly planning meeting notes\n\nText.\n")
    page.set_viewport_size({"width": 800, "height": 900})
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("quarterly")
    item = page.locator(f"#modal-page-list .page-list-item[data-slug='{slug}']")
    expect(item).to_be_visible()
    title = item.locator("a")
    # The title whole, on one line.
    assert title.evaluate("a => a.scrollWidth <= a.clientWidth + 1")
    assert box_of(title)["height"] < 30
    slug_box, row = box_of(item.locator(".page-slug")), box_of(item)
    assert slug_box["x"] + slug_box["width"] <= row["x"] + row["width"] + 1    # stays in the row
    assert item.locator(".page-slug").evaluate("s => s.scrollWidth > s.clientWidth")   # cut
    page.screenshot(path="tests/e2e/_screenshots/search-long-slug.png")
