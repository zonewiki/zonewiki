"""Deck: Plan board, start/due dates in the edit modal, views, sections and task links."""

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from conftest import open_tab, real_density


def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def task_by_title(page, title: str) -> dict:
    tasks = page.request.get("/api/deck").json()
    return next(t for t in tasks if t["title"] == title)


def wait_for_field(page, title: str, field: str, expected, timeout_ms: int = 5000):
    """Poll the API until the task's field reaches the expected value."""
    for _ in range(timeout_ms // 100):
        if task_by_title(page, title)[field] == expected:
            return
        page.wait_for_timeout(100)
    assert task_by_title(page, title)[field] == expected


def open_board(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    open_tab(page, "plan")
    expect(page.locator(".board-day-divider").first).to_be_visible()


def card(page, title: str):
    # Exact title match — has_text is a substring match ("Planned one" would
    # also hit "Unplanned one").
    return page.locator(".task-item").filter(
        has=page.get_by_text(title, exact=True))


def test_drag_to_tomorrow_plans_task(page, wiki):
    page.request.post("/api/deck", data={"title": "Draggable"})
    open_board(page, wiki)
    expect(card(page, "Draggable")).to_be_visible()

    target = page.locator(".board-day-divider[data-drop-day]", has_text="Tomorrow")
    card(page, "Draggable").drag_to(target)

    wait_for_field(page, "Draggable", "start_date", tomorrow())
    # The card re-renders under the Tomorrow section. Looked up afresh on each
    # poll: the board may re-render again (count refreshes), detaching a card
    # found earlier.
    page.wait_for_function(
        """day => [...document.querySelectorAll('.task-item')].some(el => {
             if (el.querySelector('.task-title')?.textContent.trim() !== 'Draggable') return false;
             let li = el;
             while (li && !li.classList.contains('board-day-divider')) li = li.previousElementSibling;
             return li?.dataset.dropDay === day;
           })""", arg=tomorrow(), timeout=5000)


def test_drag_to_unplanned_clears_start(page, wiki):
    page.request.post("/api/deck", data={"title": "Planned", "start_date": tomorrow()})
    # Unplanned is the first section; Playwright's HTML5 drag can't auto-scroll
    # mid-drag, so make both ends fit on screen.
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    target = page.locator(".board-day-divider[data-drop-bucket='unplanned']")
    card(page, "Planned").drag_to(target)
    wait_for_field(page, "Planned", "start_date", None)
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-count")).to_have_text("1")


def test_view_tabs_segmented_control(page, wiki):
    page.request.post("/api/deck", data={"title": "Some task"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    tabs = page.locator("#tasks-tabs [role='tab']")
    expect(tabs).to_have_text(["Open", "Plan", "Focus"])
    # Focus is the default view and the only one marked active.
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Focus"])
    tabs.nth(0).click()

    tabs.nth(1).click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])
    expect(page.locator(".board-day-divider").first).to_be_visible()

    # Tab / Shift+Tab cycle through the views in on-screen order, wrapping;
    # keyboard focus follows the active view.
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])
    expect(page.locator("#tasks-tabs .active")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])

    # The active view survives a reload within the browser tab.
    page.reload()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])


def test_embed_filter_marks_its_tab_active(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck view=plan]]\n")
    page.goto("/page/deck")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])


def test_view_url_param_picks_the_tab(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=plan")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])
    # Not a view's name (here the Plan tab's old one, and ?filter=, the old
    # name of ?view=): the default, Focus, ...
    page.goto("/page/deck?view=due")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])
    # ... or the tab last clicked in this browser tab.
    open_tab(page, "open")
    page.goto("/page/deck?filter=plan")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])


def test_focus_shows_everything_whose_start_date_has_arrived(page, wiki):
    # The plan date is a *start* date: from that day on the task is available.
    # A task that started days ago is simply part of Focus — not "carried over"
    # or stale, and not singled out for a decision.
    for title, start in [("Started last week", -7), ("Starts today", 0), ("Starts tomorrow", 1)]:
        page.request.post("/api/deck", data={"title": title, "start_date":
                                              (date.today() + timedelta(days=start)).isoformat()})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    open_tab(page, "focus")
    expect(card(page, "Started last week")).to_be_visible()
    expect(card(page, "Starts today")).to_be_visible()
    expect(card(page, "Starts tomorrow")).to_have_count(0)
    expect(page.locator(".review-panel")).to_have_count(0)


def test_deck_link_counts_the_due_tasks_and_opens_focus(page, wiki):
    # The Deck link counts deadlines (overdue + due today); the deck opens on Focus.
    # There is no Due section: the counted tasks sit in Focus's order like
    # any other, marked by their deadline colour.
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    page.request.post("/api/deck", data={"title": "Overdue", "due_date": d(-2)})
    page.request.post("/api/deck", data={"title": "Due today", "due_date": d(0)})
    page.request.post("/api/deck", data={"title": "Started", "start_date": d(-1)})
    page.request.post("/api/deck", data={"title": "Started, due later",
                                         "start_date": d(0), "due_date": d(5)})
    # No deck.md: the Deck module link goes to the built-in /deck page,
    # which opens on Focus.
    page.goto("/page/index")
    deck = page.locator(".breadcrumb .module-link[data-module=deck]")
    expect(deck.locator(".module-due")).to_have_text("2")
    expect(deck.locator(".module-focus")).to_have_text("4")
    expect(deck).to_have_attribute("title", "Deck: 4 in Focus · 1 overdue · 1 due today")
    deck.click()
    expect(page).to_have_url(re.compile(r"/deck$"))
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])
    expect(page.locator(".task-inbox-divider--only")).to_have_count(0)   # no Due section
    # All in Unsorted, in Open's order (newest first).
    expect(_focus_titles(page)).to_have_text(["Started, due later", "Started", "Due today", "Overdue"])
    expect(card(page, "Overdue")).to_have_class(re.compile(r"\btask-urg-overdue\b"))
    expect(card(page, "Due today")).to_have_class(re.compile(r"\btask-urg-today\b"))


def test_deck_page_opens_linked_task_and_lists(page, wiki):
    # /deck is one built-in main deck, whatever embeds the wiki's pages have.
    wiki.write("deck", "# Deck\n\n[[deck tag=work]]\n\n[[deck view=plan]]\n")
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    tid = page.request.post("/api/deck", data={"title": "Untagged"}).json()["id"]
    page.request.post("/api/deck", data={"title": "Milk", "tags": "groceries"})
    page.goto(f"/deck?task={tid}&view=open")
    expect(page.locator(".tasks-app")).to_have_count(1)
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    expect(card(page, "Untagged")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(card(page, "Milk")).to_be_visible()     # list tasks too: it's every task

    page.goto("/deck?list=groceries&view=open")
    expect(page.locator("h1")).to_have_text("groceries")
    expect(card(page, "Milk")).to_be_visible()
    expect(card(page, "Untagged")).to_have_count(0)


def test_search_modal_lists_the_deck(page, wiki):
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("deck")
    expect(page.locator(".page-list-item").first).to_contain_text("Deck")
    page.locator("#modal-filter").press("Enter")
    expect(page).to_have_url(re.compile(r"/deck$"))
    expect(page.locator(".tasks-app")).to_have_count(1)


def test_empty_focus_offers_planning(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    open_tab(page, "focus")
    expect(page.locator(".tasks-empty")).to_contain_text("Nothing planned for today")
    page.get_by_role("button", name="Plan your day").click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])


def test_past_start_date_reads_since(page, wiki):
    # The plan date is a start date, so a past one reads "since Sep 21" —
    # not "planned 5d ago", and not styled as stale/overdue.
    start = date.today() - timedelta(days=5)
    long_ago = date(date.today().year - 1, 3, 4)
    page.request.post("/api/deck", data={"title": "Started", "start_date": start.isoformat()})
    page.request.post("/api/deck", data={"title": "Started last year", "start_date": long_ago.isoformat()})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    open_tab(page, "focus")
    chip = card(page, "Started").locator(".task-focus-info")
    expect(chip).to_have_text(f"📅 since {start.strftime('%b')} {start.day}")
    expect(card(page, "Started last year").locator(".task-focus-info")).to_have_text(
        f"📅 since Mar 4, {long_ago.year}")
    color = chip.evaluate("el => getComputedStyle(el).color")
    muted = chip.evaluate("""el => { const p = document.createElement('span');
        p.style.color = 'var(--muted)'; el.parentNode.appendChild(p);
        const c = getComputedStyle(p).color; p.remove(); return c; }""")
    assert color == muted, (color, muted)   # plain muted meta colour, no stale red


def section_of(page, title: str) -> str:
    """The drop-bucket key of the board section a card sits in ('' if display-only)."""
    return card(page, title).evaluate(
        "el => { let li = el; while (li && !li.classList.contains('board-day-divider'))"
        " li = li.previousElementSibling; return li ? (li.dataset.dropBucket || li.textContent) : null; }")


def test_board_groups_by_start_date(page, wiki):
    today = date.today()
    page.request.post("/api/deck", data={"title": "Started earlier",
                                         "start_date": (today - timedelta(days=4)).isoformat()})
    page.request.post("/api/deck", data={"title": "Deadline only",
                                         "due_date": (today + timedelta(days=2)).isoformat()})
    page.request.post("/api/deck", data={"title": "Missed",
                                         "due_date": (today - timedelta(days=1)).isoformat()})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    # A past start date means "available since": not Overdue, but not
    # today's plan either — Carried over (display-only, so its label).
    assert section_of(page, "Started earlier").startswith("Carried over")
    # A deadline alone is not a plan: the task waits in Unplanned, not on its due day.
    assert section_of(page, "Deadline only") == "unplanned"
    # A missed deadline has no section of its own: without a start date the
    # task heads Unplanned, counted red in its header.
    assert section_of(page, "Missed") == "unplanned"
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-overdue")).to_have_text("1 overdue")


def test_drop_carried_over_on_today_commits_again(page, wiki):
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Slipped",
                                         "start_date": (date.today() - timedelta(days=3)).isoformat()})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    assert section_of(page, "Slipped").startswith("Carried over")
    card(page, "Slipped").drag_to(page.locator(f".board-day-divider[data-drop-day='{today}']"))
    wait_for_field(page, "Slipped", "start_date", today)
    expect(page.locator(".board-day-divider", has_text="Carried over")).to_have_count(0)


def test_day_header_counts_deadlines_and_lists_them(page, wiki):
    day = (date.today() + timedelta(days=3)).isoformat()
    page.request.post("/api/deck", data={"title": "Report", "due_date": day})
    page.request.post("/api/deck", data={"title": "Slides", "due_date": day,
                                         "start_date": date.today().isoformat()})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    header = page.locator(f".board-day-divider[data-drop-day='{day}']")
    due = header.locator(".board-day-due")
    expect(due).to_have_text("2 due")
    expect(header.locator(".board-day-count")).to_have_text("0")   # cards stay put
    expect(page.locator(".board-day-divider[data-drop-day] .board-day-due")).to_have_count(1)
    due.click()
    menu = page.locator(".task-menu")
    expect(menu.locator(".task-menu-title")).to_contain_text("Due")
    expect(menu.get_by_role("menuitem")).to_have_count(2)
    menu.get_by_role("menuitem", name="Report").click()
    expect(card(page, "Report")).to_have_class(re.compile(r"\btask-selected\b"))


def fold(page, bucket: str):
    page.locator(f".board-day-divider[data-drop-bucket='{bucket}'] .board-day-fold").click()


def test_folded_section_hides_its_cards_and_is_remembered(page, wiki):
    page.request.post("/api/deck", data={"title": "Loose one"})
    page.request.post("/api/deck", data={"title": "Loose two"})
    page.request.post("/api/deck", data={"title": "Today one", "start_date": date.today().isoformat()})
    open_board(page, wiki)
    header = page.locator(".board-day-divider[data-drop-bucket='unplanned']")
    fold(page, "unplanned")
    expect(card(page, "Loose one")).to_have_count(0)
    expect(header).to_have_class(re.compile(r"\bboard-day--folded\b"))
    expect(header.locator(".board-day-count")).to_have_text("2")
    expect(header.locator(".board-day-fold")).to_have_attribute("aria-expanded", "false")
    # j skips the folded section: the first card it selects is Today's.
    page.keyboard.press("j")
    expect(card(page, "Today one")).to_have_class(re.compile(r"\btask-selected\b"))
    page.goto("/page/deck?view=plan")   # the same deck: folding is remembered per deck
    expect(card(page, "Today one")).to_be_visible()
    expect(card(page, "Loose one")).to_have_count(0)
    fold(page, "unplanned")
    expect(card(page, "Loose two")).to_be_visible()


def test_whole_header_folds_but_due_count_opens_its_list(page, wiki):
    day = (date.today() + timedelta(days=2)).isoformat()
    page.request.post("/api/deck", data={"title": "On the day", "start_date": day, "due_date": day})
    open_board(page, wiki)
    header = page.locator(f".board-day-divider[data-drop-day='{day}']")
    header.locator(".board-day-due").click()
    expect(page.locator(".task-menu")).to_be_visible()
    page.keyboard.press("Escape")
    expect(card(page, "On the day")).to_be_visible()       # the due count didn't fold
    header.locator(".board-day-count").click()              # far from the label
    expect(card(page, "On the day")).to_have_count(0)
    header.click()
    expect(card(page, "On the day")).to_be_visible()


@pytest.mark.parametrize("embed", ["list=task", "tag=task"])
def test_list_and_tag_decks_fold_too(page, wiki, embed):
    # Regression: the embed's locked filter chip (#task) counted as the
    # viewer filtering, which shows every section, so nothing could fold.
    page.request.post("/api/deck", data={"title": "Loose", "tags": ["task"]})
    wiki.write("deck", f"# Deck\n\n[[deck {embed}]]\n")
    page.goto("/page/deck?view=plan")
    expect(card(page, "Loose")).to_be_visible()
    fold(page, "unplanned")
    expect(card(page, "Loose")).to_have_count(0)
    page.locator("#task-filter-search").fill("loo")         # the viewer's own filter
    expect(card(page, "Loose")).to_be_visible()


def test_folded_section_still_takes_drops(page, wiki):
    page.request.post("/api/deck", data={"title": "Loose"})
    page.request.post("/api/deck", data={"title": "Planned", "start_date": tomorrow()})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    fold(page, "unplanned")
    card(page, "Planned").drag_to(page.locator(".board-day-divider[data-drop-bucket='unplanned']"))
    wait_for_field(page, "Planned", "start_date", None)
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-count")).to_have_text("2")
    expect(card(page, "Planned")).to_have_count(0)


def test_filter_shows_folded_sections(page, wiki):
    page.request.post("/api/deck", data={"title": "Hidden match"})
    open_board(page, wiki)
    fold(page, "unplanned")
    expect(card(page, "Hidden match")).to_have_count(0)
    page.locator("#task-filter-search").fill("match")
    expect(card(page, "Hidden match")).to_be_visible()
    expect(page.locator(".board-day-fold")).to_have_count(0)


def test_task_link_unfolds_its_section(page, wiki):
    tid = page.request.post("/api/deck", data={"title": "Linked"}).json()["id"]
    open_board(page, wiki)
    fold(page, "unplanned")
    expect(card(page, "Linked")).to_have_count(0)
    page.goto(f"/deck?view=plan&task={tid}")
    expect(card(page, "Linked")).to_have_class(re.compile(r"\btask-selected\b"))


def test_new_task_unfolds_its_section(page, wiki):
    page.request.post("/api/deck", data={"title": "Loose"})
    open_board(page, wiki)
    fold(page, "unplanned")
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Fresh")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(card(page, "Fresh")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(card(page, "Loose")).to_be_visible()


def test_number_keys_start_selected_tasks_on_that_day(page, wiki):
    page.request.post("/api/deck", data={"title": "First"})
    page.request.post("/api/deck", data={"title": "Second"})
    page.request.post("/api/deck", data={"title": "Third"})
    open_board(page, wiki)
    card(page, "First").locator(".task-title").click()
    page.keyboard.press("3")
    wait_for_field(page, "First", "start_date", (date.today() + timedelta(days=2)).isoformat())
    # Several at once, with one undo.
    card(page, "Second").locator(".task-title").click()
    card(page, "Third").locator(".task-title").click(modifiers=["Shift"])
    page.keyboard.press("2")
    wait_for_field(page, "Second", "start_date", tomorrow())
    wait_for_field(page, "Third", "start_date", tomorrow())
    expect(page.locator(".task-undo-toast")).to_contain_text("2 tasks start tomorrow")
    page.keyboard.press("0")
    wait_for_field(page, "Second", "start_date", None)
    wait_for_field(page, "Third", "start_date", None)
    page.keyboard.press("Control+z")
    wait_for_field(page, "Second", "start_date", tomorrow())


def test_number_key_past_the_deadline_asks(page, wiki):
    page.request.post("/api/deck", data={"title": "Due soon", "due_date": tomorrow()})
    open_board(page, wiki)
    card(page, "Due soon").locator(".task-title").click()
    page.keyboard.press("5")
    expect(page.locator("#task-deadline-modal")).to_be_visible()
    page.locator("#task-deadline-modal").get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(200)
    assert task_by_title(page, "Due soon")["start_date"] is None


def open_edit(page, title: str):
    card(page, title).locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    return modal


def test_modal_days_before_due_sets_start_date(page, wiki):
    page.request.post("/api/deck", data={"title": "Report", "due_date": "2099-01-10"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    modal = open_edit(page, "Report")
    lead = modal.locator("#task-edit-lead")
    expect(lead).to_have_value("")
    lead.fill("3")
    expect(modal.locator("#task-edit-start")).to_have_value("2099-01-07")
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Report", "start_date", "2099-01-07")
    assert "defer_days" not in task_by_title(page, "Report")

    # Reopening shows the gap between the two stored dates.
    open_edit(page, "Report")
    expect(lead).to_have_value("3")


def test_modal_lead_disabled_without_due_date(page, wiki):
    page.request.post("/api/deck", data={"title": "Undated"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    open_edit(page, "Undated")
    expect(page.locator("#task-edit-lead")).to_be_disabled()


# ── Stale-tab refresh ────────────────────────────────────────────────────────
# A deck tab can stay open for days; coming back to it reloads the tasks.

def _deck_with_one_task(page, wiki):
    page.request.post("/api/deck", data={"title": "Loaded"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    expect(card(page, "Loaded")).to_be_visible()
    # Changed elsewhere (another device) while this tab sat in the background.
    page.request.post("/api/deck", data={"title": "Added elsewhere"})


def test_deck_refreshes_when_tab_becomes_visible(page, wiki):
    _deck_with_one_task(page, wiki)
    page.wait_for_timeout(300)
    expect(card(page, "Added elsewhere")).to_have_count(0)   # no polling
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(card(page, "Added elsewhere")).to_be_visible()


def test_deck_refreshes_when_restored_from_bfcache(page, wiki):
    _deck_with_one_task(page, wiki)
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(card(page, "Added elsewhere")).to_be_visible()


def test_deck_refresh_waits_while_editing(page, wiki):
    _deck_with_one_task(page, wiki)
    card(page, "Loaded").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    expect(page.locator("#task-edit-modal")).to_be_visible()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    expect(card(page, "Added elsewhere")).to_have_count(0)   # the editor is open: no re-render


# ?task=<id> — search results and a page's "Linked tasks" point at one task.

def _task_link_deck(page, wiki, deck="[[deck]]"):
    wiki.write("deck", f"# Deck\n\n{deck}\n")
    ids = [page.request.post("/api/deck", data={"title": t}).json()["id"]
           for t in ("Same title", "Same title", "Fix #3 now")]
    return ids


def test_task_link_selects_that_task_by_id(page, wiki):
    ids = _task_link_deck(page, wiki)
    page.goto(f"/page/deck?task={ids[1]}&view=open")
    target = page.locator(f".task-item[data-id='{ids[1]}']")
    expect(target).to_have_class(re.compile(r"\btask-selected\b"))
    expect(target).to_have_class(re.compile(r"\btask-flash\b"))
    # Nothing is filtered away: the twin and the #word task are still listed.
    expect(page.locator(f".task-item[data-id='{ids[0]}']")).to_be_visible()
    expect(page.locator("#task-filter-search")).to_have_value("")


def test_task_link_to_title_with_hashword(page, wiki):
    # Regression: the old ?search=<title> link read "#3" as a tag filter and
    # showed nothing.
    ids = _task_link_deck(page, wiki)
    page.goto(f"/page/deck?task={ids[2]}&view=open")
    expect(page.locator(f".task-item[data-id='{ids[2]}']")).to_have_class(
        re.compile(r"\btask-selected\b"))


def test_task_link_to_completed_task(page, wiki):
    ids = _task_link_deck(page, wiki)
    page.request.put(f"/api/deck/{ids[0]}", data={"completed": True})
    page.goto(f"/page/deck?task={ids[0]}&view=completed")
    expect(page.locator(f".task-item[data-id='{ids[0]}']")).to_have_class(
        re.compile(r"\btask-selected\b"))


def test_task_link_to_missing_task_says_so(page, wiki):
    _task_link_deck(page, wiki)
    page.goto("/page/deck?task=no-such-id&view=open")
    expect(page.get_by_text("That task is no longer in the deck.")).to_be_visible()


# Swapping a list's tag in a list view moves the task into the other list.

def test_list_tag_moves_task_to_another_list(page, wiki):
    page.request.post("/api/deck", data={"title": "Prune", "tags": "chores"})
    wiki.write("garden", "# Garden\n\n[[deck list=garden]]\n")
    wiki.write("home", "# Home\n\n[[deck list=chores]]\n")
    page.goto("/page/home?view=open")
    modal = open_edit(page, "Prune")
    expect(modal.locator("#task-edit-list")).to_have_count(0)   # no List field any more
    modal.locator("#task-edit-tags").click()
    page.keyboard.press("Backspace")      # drops #chores
    modal.locator("#task-edit-tags").fill("garden,")
    modal.get_by_role("button", name="Save").click()
    # It leaves this list and says where it went.
    expect(card(page, "Prune")).to_have_count(0)
    expect(page.get_by_text("Saved to list “garden”.")).to_be_visible()
    wait_for_field(page, "Prune", "tags", ["garden"])


def test_list_tag_in_main_deck_keeps_the_task(page, wiki):
    # On the main deck a list's tag is just a tag: the task stays.
    page.request.post("/api/deck", data={"title": "Prune"})
    wiki.write("garden", "# Garden\n\n[[deck list=garden]]\n")
    page.goto("/deck?view=open")
    modal = open_edit(page, "Prune")
    modal.locator("#task-edit-tags").fill("garden,")
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Prune", "tags", ["garden"])
    expect(card(page, "Prune")).to_be_visible()
    expect(card(page, "Prune").locator(".task-tag-list")).to_have_text("#garden")
    expect(page.get_by_text("Saved to list “garden”.")).to_have_count(0)


# Completed: a link under the list instead of a tab.

def test_completed_link_opens_completed_and_goes_back(page, wiki):
    for title in ("Open one", "Done one", "Done two"):
        tid = page.request.post("/api/deck", data={"title": title}).json()["id"]
        if title.startswith("Done"):
            page.request.put(f"/api/deck/{tid}", data={"completed": True})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    open_tab(page, "plan")
    link = page.locator("#task-completed-link")
    expect(link).to_have_text("✓ Completed (2) →")

    link.click()
    expect(page.locator("#tasks-tabs .active")).to_have_count(0)
    expect(page.locator(".task-inbox-divider--only")).to_have_text("Completed")
    expect(card(page, "Done one")).to_be_visible()
    expect(card(page, "Open one")).to_have_count(0)
    expect(link).to_have_text("← Back to Plan")   # names the tab it returns to

    # Back returns to the tab it came from.
    link.click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])

    # Completed survives a reload, like any view; Tab from there goes to the
    # first tab.
    link.click()
    page.reload()
    expect(card(page, "Done two")).to_be_visible()
    page.locator("body").click()
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])


def test_completed_count_follows_completion(page, wiki):
    page.request.post("/api/deck", data={"title": "Finish me"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    expect(page.locator("#task-completed-link")).to_have_text("✓ Completed (0) →")
    card(page, "Finish me").locator("input[type=checkbox]").check()
    # After the checkbox's ~5 s grace period.
    expect(page.locator("#task-completed-link")).to_have_text("✓ Completed (1) →", timeout=10000)


def test_embed_filter_completed_still_opens_completed(page, wiki):
    tid = page.request.post("/api/deck", data={"title": "Old work"}).json()["id"]
    page.request.put(f"/api/deck/{tid}", data={"completed": True})
    wiki.write("log", "# Log\n\n[[deck view=completed]]\n")
    page.goto("/page/log")
    expect(card(page, "Old work")).to_be_visible()
    expect(page.locator("#task-completed-link")).to_have_text("← Back to Focus")


def test_filter_points_to_matches_in_completed(page, wiki):
    page.request.post("/api/deck", data={"title": "Buy milk"})
    for title in ("Buy bread", "Fix bike"):
        tid = page.request.post("/api/deck", data={"title": title}).json()["id"]
        page.request.put(f"/api/deck/{tid}", data={"completed": True})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    link = page.locator("#task-completed-link")
    search = page.locator("#task-filter-search")

    search.fill("bread")
    expect(page.locator(".tasks-empty")).to_be_visible()   # nothing open matches…
    expect(link).to_have_text("✓ 1 match in Completed →")  # …but it was done
    expect(link).to_have_class(re.compile(r"\bhas-matches\b"))
    # Subtle: the muted colour of the plain link, not the link blue.
    match_colour = link.evaluate("el => getComputedStyle(el).color")
    search.fill("buy")
    expect(link).to_have_text("✓ 1 match in Completed →")
    search.fill("b")
    expect(link).to_have_text("✓ 2 matches in Completed →")
    search.fill("milk")   # matches only the open task: no unfiltered total
    expect(link).to_have_text("✓ No matches in Completed →")
    expect(link).not_to_have_class(re.compile(r"\bhas-matches\b"))
    page.mouse.move(0, 0)  # no hover colour
    assert link.evaluate("el => getComputedStyle(el).color") == match_colour
    search.fill("#nosuchtag")
    expect(link).to_have_text("✓ No matches in Completed →")

    search.fill("bread")
    link.click()   # the filter carries over into Completed
    expect(card(page, "Buy bread")).to_be_visible()
    expect(card(page, "Fix bike")).to_have_count(0)

    link.click()
    search.fill("")
    expect(link).to_have_text("✓ Completed (2) →")
    expect(link).not_to_have_class(re.compile(r"\bhas-matches\b"))


def test_new_task_on_default_focus_starts_today(page, wiki):
    # A plain deck opens on Focus; a task added there starts today, so it
    # stays in the view instead of vanishing on save.
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Water plants")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(card(page, "Water plants")).to_be_visible()
    assert task_by_title(page, "Water plants")["start_date"] == date.today().isoformat()


# Quick-add shorthand in a new task's title.

def test_quick_add_shorthand_fills_fields(page, wiki):
    from datetime import date as _date
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    title = modal.locator("#task-edit-title")
    preview = modal.locator("#task-edit-quick")
    expect(preview).to_be_hidden()
    title.fill("Call Bob tomorrow #work due:+3")
    expect(preview).to_have_text(re.compile(r"^📅 start Tomorrow · ⏰ due .+ · #work$"))
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Call Bob")).to_be_visible()
    t = task_by_title(page, "Call Bob")
    today = _date.today()
    assert t["start_date"] == (today + timedelta(days=1)).isoformat()
    assert t["due_date"] == (today + timedelta(days=3)).isoformat()
    assert t["tags"] == ["work"]


def test_quick_add_is_not_applied_when_editing(page, wiki):
    page.request.post("/api/deck", data={"title": "Old task"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    modal = open_edit(page, "Old task")
    modal.locator("#task-edit-title").fill("Old task tomorrow")
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Old task tomorrow")).to_be_visible()   # saved, title kept whole
    assert task_by_title(page, "Old task tomorrow")["start_date"] is None


# ── Separate lists ([[deck list=…]]) ──────────────────────────────────────────


def test_list_embed_shows_only_its_tasks(page, wiki):
    page.request.post("/api/deck", data={"title": "Main task"})
    page.request.post("/api/deck", data={"title": "Oat milk", "tags": "groceries"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")

    # The main deck shows every task, list tasks included.
    page.goto("/page/deck?view=open")
    expect(card(page, "Main task")).to_be_visible()
    expect(card(page, "Oat milk")).to_be_visible()
    open_tab(page, "plan")
    expect(card(page, "Main task")).to_be_visible()
    expect(card(page, "Oat milk")).to_be_visible()

    page.goto("/page/shop?view=open")
    expect(card(page, "Oat milk")).to_be_visible()
    expect(card(page, "Main task")).to_have_count(0)


def test_tag_embed_shows_list_tasks(page, wiki):
    # A tag view shows list tasks like any other.
    page.request.post("/api/deck", data={"title": "Oat milk", "tags": "groceries,sam"})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    wiki.write("sam", "# Sam\n\n[[deck tag=sam]]\n")
    page.goto("/page/sam?view=open")
    expect(card(page, "Oat milk")).to_be_visible()


def test_tag_embed_filter_is_locked_and_says_when_a_task_leaves(page, wiki):
    # tag= chips are locked like a list's, but a task's tags are not: taking
    # #sam off a task on Sam's page is a normal edit, and it says so.
    page.request.post("/api/deck", data={"title": "Review PR", "tags": "task,sam"})
    wiki.write("sam", "# Sam\n\n[[deck tag=sam]]\n")
    page.goto("/page/sam?view=open")
    filt = page.locator(".tasks-toolbar .tag-chip")
    expect(filt).to_have_text(["#sam"])
    expect(filt.locator(".tag-chip-x")).to_have_count(0)
    page.locator("#task-filter-search").click()
    page.keyboard.press("Backspace")
    expect(filt).to_have_text(["#sam"])
    modal = open_edit(page, "Review PR")
    expect(modal.locator(".tag-chip .tag-chip-x")).to_have_count(2)   # #task and #sam removable
    modal.locator("#task-edit-tags").click()
    page.keyboard.press("Backspace")      # drops #sam
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Review PR")).to_have_count(0)
    expect(page.get_by_text("Saved; it no longer shows here.")).to_be_visible()
    wait_for_field(page, "Review PR", "tags", ["task"])


def test_url_tag_chip_stays_removable(page, wiki):
    page.goto("/deck?tag=task&view=open")
    filt = page.locator(".tasks-toolbar .tag-chip")
    expect(filt.locator(".tag-chip-label")).to_have_text(["#task"])
    expect(filt.locator(".tag-chip-x")).to_have_count(1)


def test_undo_a_move_to_another_list(page, wiki):
    # Undo restores the whole save, not just the tags, and leaves no trace.
    page.request.post("/api/deck", data={"title": "Screws", "tags": "groceries",
                                         "due_date": "2099-01-10"})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    wiki.write("diy", "# DIY\n\n[[deck list=hardware]]\n")
    page.goto("/page/shop?view=open")
    modal = open_edit(page, "Screws")
    modal.locator("#task-edit-lead").fill("3")       # a start date too (logged)
    modal.locator("#task-edit-tags").click()
    page.keyboard.press("Backspace")
    page.keyboard.type("hardware,")
    modal.get_by_role("button", name="Save").click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("Saved to list “hardware”.")
    expect(card(page, "Screws")).to_have_count(0)
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Screws")).to_be_visible()
    wait_for_field(page, "Screws", "tags", ["groceries"])
    t = task_by_title(page, "Screws")
    assert t["start_date"] is None
    assert [e["event"] for e in t["history"]] == ["created"]


def test_undo_removing_the_tag_on_a_tag_page(page, wiki):
    page.request.post("/api/deck", data={"title": "Review PR", "tags": "task,sam"})
    wiki.write("sam", "# Sam\n\n[[deck tag=sam]]\n")
    page.goto("/page/sam?view=open")
    modal = open_edit(page, "Review PR")
    modal.locator("#task-edit-tags").click()
    page.keyboard.press("Backspace")      # drops #sam
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Review PR")).to_have_count(0)
    page.locator(".task-undo-btn").click()
    expect(card(page, "Review PR")).to_be_visible()
    wait_for_field(page, "Review PR", "tags", ["task", "sam"])


def test_undo_a_bulk_retag(page, wiki):
    # Bulk tag (T) taking tasks out of a list into another one they carry:
    # one toast, one undo for all.
    for title in ("Nails", "Glue"):
        page.request.post("/api/deck", data={"title": title, "tags": "misc,hardware"})
    wiki.write("diy", "# DIY\n\n[[deck list=hardware]]\n")
    wiki.write("misc", "# Misc\n\n[[deck list=misc]]\n")
    page.goto("/page/misc?view=open")
    card(page, "Glue").locator(".task-title").click()     # newest first: Glue, Nails
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("Shift+T")
    page.locator("#bulk-tag-input").fill("misc")
    page.keyboard.press("Enter")
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks saved to list “hardware”.")
    expect(card(page, "Nails")).to_have_count(0)
    expect(card(page, "Glue")).to_have_count(0)
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Nails")).to_be_visible()
    expect(card(page, "Glue")).to_be_visible()
    wait_for_field(page, "Nails", "tags", ["misc", "hardware"])
    wait_for_field(page, "Glue", "tags", ["misc", "hardware"])


def select_by_keys(page, first: str, count: int):
    """Select `count` cards from `first` down (a click, then Shift+↓)."""
    card(page, first).locator(".task-title").click()
    for _ in range(count - 1):
        page.keyboard.press("Shift+ArrowDown")


def test_bulk_tag_dialog_three_states(page, wiki):
    # The selection's tags as rows: on both (✓), on one (–, "1 of 2").
    # A click cycles all → none → some; typed tags are added; one request,
    # one undo that puts every task's tags back.
    page.request.post("/api/deck", data={"title": "Bolts", "tags": "x,y"})
    page.request.post("/api/deck", data={"title": "Nuts", "tags": "x"})
    page.goto("/deck?view=open")
    select_by_keys(page, "Nuts", 2)                   # newest first: Nuts, Bolts
    page.keyboard.press("t")
    dlg = page.locator(".bulk-tag-modal")
    expect(dlg.locator(".scratch-label")).to_have_text("Tags of 2 tasks")
    rows = dlg.locator(".bulk-tag-row")
    expect(rows).to_have_count(2)
    x, y = rows.filter(has_text="#x"), rows.filter(has_text="#y")
    expect(x).to_have_attribute("aria-checked", "true")
    expect(y).to_have_attribute("aria-checked", "mixed")
    expect(y.locator(".bulk-tag-count")).to_have_text("1 of 2")
    expect(page.locator("#bulk-tag-input")).to_be_focused()
    y.click()
    expect(y).to_have_attribute("aria-checked", "true")
    x.click()
    expect(x).to_have_attribute("aria-checked", "false")
    y.click()
    y.click()                                         # none, then back to some
    expect(y).to_have_attribute("aria-checked", "mixed")
    y.click()                                         # all again
    inp = page.locator("#bulk-tag-input")
    inp.click()
    inp.press_sequentially("new,more")
    expect(dlg.locator(".tag-chip-label")).to_have_text(["#new"])
    dlg.get_by_role("button", name="Apply").click()   # takes the half-typed "more"
    expect(dlg).to_have_count(0)
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("Tags changed on 2 tasks")
    wait_for_field(page, "Bolts", "tags", ["y", "new", "more"])
    wait_for_field(page, "Nuts", "tags", ["y", "new", "more"])
    expect(card(page, "Nuts").locator(".task-tag")).to_have_text(["#y", "#new", "#more"])
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Bolts", "tags", ["x", "y"])
    wait_for_field(page, "Nuts", "tags", ["x"])
    expect(card(page, "Nuts").locator(".task-tag")).to_have_text(["#x"])


def test_bulk_tag_one_tag_says_so_and_skips_unchanged(page, wiki):
    page.request.post("/api/deck", data={"title": "Bolts", "tags": "work"})
    page.request.post("/api/deck", data={"title": "Nuts"})
    page.goto("/deck?view=open")
    select_by_keys(page, "Nuts", 2)
    page.keyboard.press("t")
    page.locator("#bulk-tag-input").fill("work")
    page.keyboard.press("Enter")
    toast = page.locator(".task-undo-toast")
    # Bolts had it already: one task changed.
    expect(toast).to_contain_text("#work added to 1 task")
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Nuts", "tags", [])
    assert task_by_title(page, "Bolts")["tags"] == ["work"]


def test_bulk_tag_nothing_changed_sends_nothing(page, wiki):
    page.request.post("/api/deck", data={"title": "Bolts", "tags": "work"})
    page.goto("/deck?view=open")
    card(page, "Bolts").locator(".task-title").click()
    page.keyboard.press("t")
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "batch-tag" in r.url else None)
    page.locator(".bulk-tag-modal").get_by_role("button", name="Apply").click()
    expect(page.locator(".bulk-tag-modal")).to_have_count(0)
    page.wait_for_timeout(200)
    assert requests == []
    expect(page.locator(".task-undo-toast")).to_have_count(0)


def seed_four(page):
    # Open shows them newest first: Saw, Glue, Nuts, Bolts.
    for title in ("Bolts", "Nuts", "Glue", "Saw"):
        page.request.post("/api/deck", data={"title": title})
    page.goto("/deck?view=open")


def selected_titles(page):
    return page.locator(".task-item.task-selected .task-title").all_inner_texts()


def test_shift_and_ctrl_click_select(page, wiki):
    seed_four(page)
    bar = page.locator("#task-selbar")
    card(page, "Saw").locator(".task-title").click()
    expect(bar).to_be_hidden()                          # one task: no bar
    card(page, "Nuts").locator(".task-title").click(modifiers=["Shift"])
    assert selected_titles(page) == ["Saw", "Glue", "Nuts"]
    expect(bar.locator(".deck-selbar-count")).to_have_text("3 selected")
    card(page, "Glue").locator(".task-title").click(modifiers=["Control"])
    assert selected_titles(page) == ["Saw", "Nuts"]
    card(page, "Bolts").locator(".task-title").click(modifiers=["Control"])
    expect(bar.locator(".deck-selbar-count")).to_have_text("3 selected")
    # Shift+click extends from the last Ctrl+clicked card.
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    assert selected_titles(page) == ["Glue", "Nuts", "Bolts"]
    # A plain click selects just that one again.
    card(page, "Saw").locator(".task-title").click()
    assert selected_titles(page) == ["Saw"]
    expect(bar).to_be_hidden()


def test_ctrl_a_selects_what_the_filter_shows(page, wiki):
    for title, tags in (("Bolts", "hw"), ("Nuts", "hw"), ("Glue", "")):
        page.request.post("/api/deck", data={"title": title, "tags": tags})
    page.goto("/deck?view=open&tag=hw")
    card(page, "Nuts").locator(".task-title").click()
    page.keyboard.press("Control+a")
    assert sorted(selected_titles(page)) == ["Bolts", "Nuts"]
    bar = page.locator("#task-selbar")
    expect(bar.locator(".deck-selbar-count")).to_have_text("2 selected")
    expect(bar.locator("[data-sel='all']")).to_be_disabled()
    page.keyboard.press("Escape")
    expect(bar).to_be_hidden()
    assert selected_titles(page) == []


def test_selection_bar_tag_opens_the_dialog(page, wiki):
    seed_four(page)
    card(page, "Saw").locator(".task-title").click()
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    page.locator("#task-selbar [data-sel='tag']").click()
    expect(page.locator(".bulk-tag-modal .scratch-label")).to_have_text("Tags of 2 tasks")
    page.locator("#bulk-tag-input").fill("shop")
    page.keyboard.press("Enter")
    wait_for_field(page, "Saw", "tags", ["shop"])
    wait_for_field(page, "Glue", "tags", ["shop"])
    assert task_by_title(page, "Nuts")["tags"] == []
    # The selection stays for the next action.
    assert selected_titles(page) == ["Saw", "Glue"]


def test_selection_bar_today_with_undo(page, wiki):
    seed_four(page)
    later = (date.today() + timedelta(days=5)).isoformat()
    page.request.put(f"/api/deck/{task_by_title(page, 'Glue')['id']}", data={"start_date": later})
    page.reload()
    card(page, "Saw").locator(".task-title").click()
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    page.locator("#task-selbar [data-sel='today']").click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks start today")
    today = date.today().isoformat()
    wait_for_field(page, "Saw", "start_date", today)
    wait_for_field(page, "Glue", "start_date", today)
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Saw", "start_date", None)
    wait_for_field(page, "Glue", "start_date", later)
    assert [e["event"] for e in task_by_title(page, "Glue")["history"]] == ["created", "start"]


def test_selection_bar_done_and_toast_above_it(page, wiki):
    seed_four(page)
    card(page, "Saw").locator(".task-title").click()
    card(page, "Nuts").locator(".task-title").click(modifiers=["Control"])
    page.locator("#task-selbar [data-sel='done']").click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks completed")
    expect(card(page, "Saw")).to_have_count(0)
    expect(page.locator("#task-selbar")).to_be_hidden()
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Saw")).to_be_visible()
    expect(card(page, "Nuts")).to_be_visible()


def test_menu_on_a_selected_card_acts_on_the_selection(page, wiki):
    seed_four(page)
    card(page, "Saw").locator(".task-title").click()
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    card(page, "Glue").locator(".task-title").click(button="right")
    menu = page.locator(".task-menu")
    expect(menu.locator(".task-menu-title")).to_have_text("2 tasks")
    assert selected_titles(page) == ["Saw", "Glue"]       # the right-click kept it
    menu.get_by_role("menuitem", name="Delete…").click()
    expect(page.locator("#task-delete-modal .scratch-label")).to_have_text("Delete 2 tasks?")
    page.locator("#task-delete-confirm").click()
    expect(card(page, "Saw")).to_have_count(0)
    expect(card(page, "Glue")).to_have_count(0)
    expect(page.locator("#task-selbar")).to_be_hidden()
    # The ⋯ button of a card not in a multi-selection is that card's own menu.
    card(page, "Nuts").locator(".task-title").click()
    card(page, "Bolts").locator(".task-menu-btn").click()
    expect(page.locator(".task-menu .task-menu-title")).to_have_text("Bolts")


def test_main_deck_counts_list_tasks_as_completed(page, wiki):
    page.request.post("/api/deck", data={"title": "Oat milk", "tags": "groceries"})
    done = page.request.post("/api/deck", data={"title": "Bread", "tags": "groceries"}).json()
    page.request.put(f"/api/deck/{done['id']}", data={"completed": True})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    page.goto("/deck?view=open")
    expect(card(page, "Oat milk")).to_be_visible()
    expect(page.locator("#task-completed-link")).to_have_text("✓ Completed (1) →")
    expect(page.locator("#task-show-lists")).to_have_count(0)


def test_new_task_in_list_embed_gets_the_locked_tag(page, wiki):
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    page.goto("/page/shop?view=open")
    filt = page.locator(".tasks-toolbar .tag-chip")
    expect(filt).to_have_text(["#groceries"])
    expect(filt.locator(".tag-chip-x")).to_have_count(0)
    page.locator("#task-filter-search").click()
    page.keyboard.press("Backspace")      # a locked chip stays
    expect(filt).to_have_text(["#groceries"])
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    # The task's tags aren't locked: it starts with the list's, removable.
    expect(modal.locator(".tag-chip .tag-chip-label")).to_have_text(["#groceries"])
    expect(modal.locator(".tag-chip .tag-chip-x")).to_have_count(1)
    modal.locator("#task-edit-title").fill("Bread")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Bread")).to_be_visible()
    wait_for_field(page, "Bread", "tags", ["groceries"])


def test_list_tag_comes_first_and_marked(page, wiki):
    page.request.post("/api/deck", data={"title": "Oat milk", "tags": "dairy,groceries"})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    page.goto("/page/shop?view=open")
    tags = card(page, "Oat milk").locator(".task-tag")      # the card too
    expect(tags).to_have_text(["#groceries", "#dairy"])
    expect(tags.first).to_have_class(re.compile(r"\btask-tag-list\b"))
    modal = open_edit(page, "Oat milk")
    chips = modal.locator(".tag-chip")
    expect(chips.locator(".tag-chip-label")).to_have_text(["#groceries", "#dairy"])
    expect(chips.first).to_have_class(re.compile(r"\btag-chip-list\b"))
    expect(chips.nth(1)).not_to_have_class(re.compile(r"\btag-chip-list\b"))
    # Icon, tint and a tooltip naming the list's page, on the chip and the card.
    for el, plain in ((chips.first, chips.nth(1)), (tags.first, tags.nth(1))):
        assert "svg" in el.evaluate("e => getComputedStyle(e, '::before').maskImage")
        assert plain.evaluate("e => getComputedStyle(e, '::before').content") in ("none", "normal")
        bg = "e => getComputedStyle(e).backgroundColor"
        assert el.evaluate(bg) != plain.evaluate(bg)
        assert el.get_attribute("title") == "List: Shop"
        assert not plain.get_attribute("title")
    # A list tag typed in later moves to the front too.
    wiki.write("diy", "# DIY\n\n[[deck list=hardware]]\n")
    page.goto("/page/shop?view=open")
    modal = open_edit(page, "Oat milk")
    modal.locator("#task-edit-tags").click()
    page.keyboard.type("hardware,")
    expect(modal.locator(".tag-chip .tag-chip-label")).to_have_text(["#groceries", "#hardware", "#dairy"])


def test_filter_marks_only_list_tags(page, wiki):
    page.request.post("/api/deck", data={"title": "Screws", "tags": "groceries,weekly,hardware,dairy"})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries tag=weekly]]\n")
    wiki.write("diy", "# DIY\n\n[[deck list=hardware]]\n")
    page.goto("/page/shop?view=open")
    filt = page.locator("#task-filter-search")
    filt.click()
    for partial in ("#hard", "#dai"):           # chips come from completion
        page.keyboard.type(partial)
        page.keyboard.press("Enter")
    chips = page.locator(".tasks-toolbar .tag-chip")
    expect(chips.locator(".tag-chip-label")).to_have_text(
        ["#groceries", "#hardware", "#weekly", "#dairy"])
    bgs = chips.evaluate_all("cs => cs.map(c => getComputedStyle(c).backgroundColor)")
    assert bgs[0] == bgs[1] and bgs[2] == bgs[3] and bgs[0] != bgs[2]   # lists tinted only
    titles = chips.evaluate_all("cs => cs.map(c => c.title)")
    assert titles[0] == "List: Shop"
    assert titles[1] == "List: DIY"
    assert titles[2] == "Set by this page" and titles[3] == ""


def test_task_moves_to_another_list(page, wiki):
    page.request.post("/api/deck", data={"title": "Screws", "tags": "groceries"})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    wiki.write("diy", "# DIY\n\n[[deck list=hardware]]\n")
    page.goto("/page/shop?view=open")
    modal = open_edit(page, "Screws")
    modal.locator("#task-edit-tags").click()
    page.keyboard.press("Backspace")      # drops #groceries
    page.keyboard.type("hardware,")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Screws")).to_have_count(0)
    expect(page.get_by_text("Saved to list “hardware”.")).to_be_visible()
    wait_for_field(page, "Screws", "tags", ["hardware"])
    page.goto("/page/diy?view=open")
    expect(card(page, "Screws")).to_be_visible()


def _focus_titles(page):
    return page.locator(".task-item .task-title")


def _by_order(page, field):
    tasks = [t for t in page.request.get("/api/deck").json() if t[field] is not None]
    return [t["title"] for t in sorted(tasks, key=lambda t: t[field])]


def test_focus_is_open_filtered(page, wiki):
    today = date.today().isoformat()
    later = (date.today() + timedelta(days=3)).isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Later", later), ("Bravo", today), ("Charlie", today))]
    page.request.post("/api/deck/reorder", data={"order": list(reversed(ids))})
    page.request.post("/api/deck", data={"title": "Unordered", "start_date": today})
    page.goto("/deck?view=focus")
    # Open's sections and order, without the task planned later.
    expect(_section(page, "unsorted")).to_have_text(re.compile("Unsorted"))
    expect(_section(page, "sorted")).to_have_text(re.compile("Sorted"))
    expect(_section(page, "lineup")).to_have_count(0)
    expect(_section(page, "others")).to_have_count(0)
    assert _open_titles(page, "unsorted") == ["Unordered"]
    assert _open_titles(page, "sorted") == ["Charlie", "Bravo", "Alpha"]


def _section(page, zone):
    return page.locator(f".list-section[data-section='{zone}']")


def test_focus_drag_sorts_into_open_order(page, wiki):
    # A shopping list: Open is the shop's order, Focus today's list. A new item
    # added on Focus is sorted in among today's items, and so lands in the
    # shop's order right after the item above it; items not on today's list
    # keep their places.
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Apples", today), ("Bread", None), ("Cheese", today),
                        ("Dates", None), ("Eggs", today))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Butter")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(_focus_titles(page)).to_have_text(["Butter", "Apples", "Cheese", "Eggs"])
    assert task_by_title(page, "Butter")["start_date"] == today

    # Onto Apples' lower half: after it, so right after Apples in the shop,
    # before Bread, which Focus doesn't show.
    _drop(card(page, "Butter"), card(page, "Apples"), "after")
    expect(_focus_titles(page)).to_have_text(["Apples", "Butter", "Cheese", "Eggs"])
    wait_until(lambda: _by_order(page, "sort_order") ==
               ["Apples", "Butter", "Bread", "Cheese", "Dates", "Eggs"])

    # Moving to the top: Apples' upper half, right before it; hidden ones stay put.
    _drop(card(page, "Eggs"), card(page, "Apples"), "before")
    expect(_focus_titles(page)).to_have_text(["Eggs", "Apples", "Butter", "Cheese"])
    wait_until(lambda: _by_order(page, "sort_order") ==
               ["Eggs", "Apples", "Butter", "Bread", "Cheese", "Dates"])

    # Onto the Unsorted heading: out of the order again.
    card(page, "Butter").drag_to(_section(page, "unsorted"))
    wait_for_field(page, "Butter", "sort_order", None)
    expect(_focus_titles(page)).to_have_text(["Butter", "Eggs", "Apples", "Cheese"])

    # Open shows the same order.
    open_tab(page, "open")
    assert _open_titles(page, "sorted") == ["Eggs", "Apples", "Bread", "Cheese", "Dates"]


def _day_titles(page, day):
    """Titles in one Plan-board day section, top to bottom."""
    return page.evaluate("""day => {
        const out = [];
        let li = document.querySelector(`.board-day-divider[data-drop-day="${day}"]`)?.nextElementSibling;
        for (; li && !li.classList.contains('board-day-divider'); li = li.nextElementSibling)
          if (li.classList.contains('task-item')) out.push(li.querySelector('.task-title').textContent.trim());
        return out;
    }""", day)


def test_board_reorders_a_day_in_open_order(page, wiki):
    # A day shows Open's order, and a drop on one of its cards edits it:
    # the task lands where the pointer is, hidden tasks keep their place.
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Elsewhere", tomorrow()), ("Bravo", today), ("Charlie", today))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.request.post("/api/deck", data={"title": "Newbie", "start_date": today})
    page.goto("/deck?view=plan")
    expect(card(page, "Charlie")).to_be_visible()
    assert _day_titles(page, today) == ["Newbie", "Alpha", "Bravo", "Charlie"]

    _drop(card(page, "Charlie"), card(page, "Alpha"), "before")
    wait_until(lambda: _by_order(page, "sort_order") == ["Charlie", "Alpha", "Elsewhere", "Bravo"])
    assert _day_titles(page, today) == ["Newbie", "Charlie", "Alpha", "Bravo"]
    assert task_by_title(page, "Newbie")["sort_order"] is None   # above it: stays Unsorted

    # Above an Unsorted task: that one is sorted too, so the day keeps
    # the order shown.
    _drop(card(page, "Bravo"), card(page, "Newbie"), "before")
    wait_until(lambda: _by_order(page, "sort_order") == ["Bravo", "Newbie", "Charlie", "Alpha", "Elsewhere"])
    assert _day_titles(page, today) == ["Bravo", "Newbie", "Charlie", "Alpha"]


def test_board_drop_on_a_card_of_another_day_starts_and_places_it(page, wiki):
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Bravo", today), ("Mover", tomorrow()))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=plan")
    _drop(card(page, "Mover"), card(page, "Bravo"), "before")
    wait_for_field(page, "Mover", "start_date", today)
    wait_until(lambda: _by_order(page, "sort_order") == ["Alpha", "Mover", "Bravo"])
    assert _day_titles(page, today) == ["Alpha", "Mover", "Bravo"]


def test_board_keyboard_reorders_within_a_day_only(page, wiki):
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Bravo", today), ("Next", tomorrow()))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=plan")
    card(page, "Bravo").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_until(lambda: _by_order(page, "sort_order") == ["Bravo", "Alpha", "Next"])
    # First of its day: it doesn't leave it, upwards or (as last) downwards.
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)
    page.keyboard.press("Control+Shift+ArrowUp")
    card(page, "Alpha").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    page.wait_for_timeout(300)
    assert writes == []
    assert task_by_title(page, "Alpha")["start_date"] == today


def test_board_unplanned_has_no_manual_order(page, wiki):
    # Unplanned is by deadline: a drop on one of its own cards writes nothing.
    page.set_viewport_size({"width": 1280, "height": 1400})
    for t in ("Loose one", "Loose two"):
        page.request.post("/api/deck", data={"title": t})
    page.goto("/deck?view=plan")
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)
    _drop(card(page, "Loose two"), card(page, "Loose one"), "after")
    page.wait_for_timeout(300)
    assert writes == []
    expect(card(page, "Loose one")).not_to_have_class(re.compile(r"task-drop-"))


def test_board_drop_on_own_heading_changes_nothing_except_later(page, wiki):
    # A drop on the task's own section heading sends nothing (a heading
    # sets the day); Later's heading still asks for another date.
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Today task", "start_date": today})
    page.request.post("/api/deck", data={"title": "Loose task"})
    far = (date.today() + timedelta(days=30)).isoformat()
    page.request.post("/api/deck", data={"title": "Later task", "start_date": far})
    page.goto("/deck?view=plan")
    expect(card(page, "Later task")).to_be_visible()
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)

    card(page, "Today task").drag_to(page.locator(f".board-day-divider[data-drop-day='{today}']"))
    card(page, "Loose task").drag_to(page.locator(".board-day-divider[data-drop-bucket='unplanned']"))
    page.wait_for_timeout(300)
    assert writes == []
    expect(page.locator(".deck-datepicker")).to_have_count(0)

    card(page, "Later task").drag_to(page.locator(".board-day-divider[data-drop-bucket='later']"))
    expect(page.locator(".deck-datepicker")).to_be_visible()


def test_focus_follows_open_order(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_text(["Alpha", "Bravo"])
    open_tab(page, "open")
    card(page, "Bravo").drag_to(card(page, "Alpha"))
    open_tab(page, "focus")
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])


def test_focus_keyboard_crosses_the_sections(page, wiki):
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Deadline", "due_date": today})
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": t2}).json()["id"]
           for t, t2 in (("Bravo", today), ("Hidden", None), ("Alpha", today))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    # Unsorted (the due task) above Sorted.
    expect(_focus_titles(page)).to_have_text(["Deadline", "Bravo", "Alpha"])
    # Unsorted can't be reordered: ↑ does nothing, ↓ sorts the task in at the
    # top of Sorted.
    card(page, "Deadline").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    expect(_focus_titles(page)).to_have_text(["Deadline", "Bravo", "Alpha"])
    page.keyboard.press("Control+Shift+ArrowDown")
    wait_until(lambda: _by_order(page, "sort_order") == ["Deadline", "Bravo", "Hidden", "Alpha"])
    expect(_section(page, "unsorted")).to_have_class(re.compile(r"\bboard-day--empty\b"))
    # ↓ within Sorted: after Alpha, past the hidden task.
    card(page, "Bravo").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    expect(_focus_titles(page)).to_have_text(["Deadline", "Alpha", "Bravo"])
    wait_until(lambda: _by_order(page, "sort_order") == ["Deadline", "Hidden", "Alpha", "Bravo"])
    # ↑ on the first sorted task takes it back to Unsorted.
    card(page, "Deadline").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_for_field(page, "Deadline", "sort_order", None)


def test_focus_limit_is_off_unless_set(page, wiki):
    today = date.today().isoformat()
    for i in range(5):
        page.request.post("/api/deck", data={"title": f"Task {i}", "start_date": today})
    # No limit by default: no Focus warning, no flagged day on Plan.
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_count(5)
    expect(page.locator(".focus-over-limit")).to_have_count(0)
    open_tab(page, "plan")
    expect(page.locator(".board-day-divider", has_text="Today")).to_be_visible()
    expect(page.locator(".board-day--over")).to_have_count(0)
    # ?limit= (the /deck page) turns it on; the count includes Unsorted.
    page.goto("/deck?view=focus&limit=5")
    expect(page.locator(".focus-over-limit")).to_contain_text("5 tasks in Focus (limit: 5)")
    open_tab(page, "plan")
    expect(page.locator(".board-day--over")).to_have_count(1)
    # [[deck limit=N]] in an embed.
    wiki.write("board", "# Board\n\n[[deck view=focus limit=3]]\n")
    page.goto("/page/board")
    expect(page.locator(".focus-over-limit")).to_contain_text("(limit: 3)")


def test_task_added_on_focus_lands_in_unsorted(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Added now")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(_focus_titles(page)).to_have_text(["Added now", "Alpha", "Bravo"])
    page.reload()
    assert _open_titles(page, "unsorted") == ["Added now"]


def test_new_task_is_selected_and_scrolled_into_view(page, wiki):
    today = date.today().isoformat()
    for i in range(30):
        page.request.post("/api/deck", data={"title": f"Filler {i}", "start_date": today})
    page.set_viewport_size({"width": 1000, "height": 600})
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_count(30)
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Added now")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    new = card(page, "Added now")
    expect(new).to_have_class(re.compile(r"\btask-selected\b"))
    expect(new).to_be_in_viewport()
    expect(page.locator(".task-item.task-selected")).to_have_count(1)


def wait_until(predicate, timeout_s=5):
    import time
    end = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < end, "condition not met in time"
        time.sleep(0.05)


def _open_titles(page, zone):
    """Titles of the tasks in one Open section, in order."""
    page.wait_for_selector(f".list-section[data-section='{zone}']")
    return page.evaluate(f"""() => {{
      const out = []; let el = document.querySelector(".list-section[data-section='{zone}']");
      while ((el = el.nextElementSibling) && el.classList.contains('task-item'))
        out.push(el.querySelector('.task-title').textContent);
      return out; }}""")


def test_open_has_unsorted_and_sorted_sections(page, wiki):
    ids = [page.request.post("/api/deck", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    for t in ("Old new", "Fresh"):
        page.request.post("/api/deck", data={"title": t})
    page.request.post("/api/deck", data={"title": "Overdue", "due_date": "2020-01-01"})
    page.goto("/deck?view=open")
    expect(_section(page, "unsorted")).to_have_text(re.compile("Unsorted"))
    expect(_section(page, "sorted")).to_have_text(re.compile("Sorted"))
    # Unsorted: newest first. No Due section on Open: a due task sits in its section.
    assert _open_titles(page, "unsorted") == ["Overdue", "Fresh", "Old new"]
    assert _open_titles(page, "sorted") == ["Alpha", "Bravo"]
    # No "Move to New" checkbox.
    card(page, "Alpha").dblclick()
    expect(page.locator("#task-edit-modal")).to_be_visible()
    expect(page.locator("#task-edit-inbox")).to_have_count(0)


def test_double_click_on_title_or_notes_opens_modal(page, wiki):
    """The title and the notes open the modal too; nothing is edited in place."""
    page.request.post("/api/deck", data={"title": "Paint fence", "description": "Two coats."})
    page.goto("/deck?view=open")
    modal = page.locator("#task-edit-modal")
    for part in (".task-title", ".task-desc"):
        card(page, "Paint fence").locator(part).dblclick()
        expect(modal).to_be_visible()
        expect(page.locator("#task-edit-title")).to_have_value("Paint fence")
        page.keyboard.press("Escape")
        expect(modal).to_be_hidden()
    expect(card(page, "Paint fence").locator("input[type=text]")).to_have_count(0)


def _drop(source, target, side):
    """Drag source onto target's upper (side "before") or lower half ("after")."""
    box = target.bounding_box()
    source.drag_to(target, target_position={"x": box["width"] / 2,
                                            "y": box["height"] * (0.25 if side == "before" else 0.75)})


def test_open_drag_between_unsorted_and_sorted(page, wiki):
    page.set_viewport_size({"width": 1000, "height": 1200})
    ids = [page.request.post("/api/deck", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    for t in ("One", "Two"):
        page.request.post("/api/deck", data={"title": t})
    page.goto("/deck?view=open")
    assert _open_titles(page, "unsorted") == ["Two", "One"]
    # Within Unsorted nothing moves (newest first).
    card(page, "Two").drag_to(card(page, "One"))
    assert _open_titles(page, "unsorted") == ["Two", "One"]
    # Onto a sorted task: where the pointer is, here its lower half, so after it.
    _drop(card(page, "One"), card(page, "Alpha"), "after")
    wait_until(lambda: _open_titles(page, "sorted") == ["Alpha", "One", "Bravo"])
    wait_for_field(page, "One", "sort_order", 1)
    assert task_by_title(page, "Two")["sort_order"] is None
    # Onto the Sorted heading: first in Sorted.
    card(page, "Two").drag_to(_section(page, "sorted"))
    wait_for_field(page, "Two", "sort_order", 0)
    expect(_section(page, "unsorted")).to_have_class(re.compile(r"\bboard-day--empty\b"))
    # A sorted task onto the Sorted heading (from below) or the Unsorted
    # heading goes back to Unsorted.
    card(page, "Bravo").drag_to(_section(page, "sorted"))
    wait_for_field(page, "Bravo", "sort_order", None)
    card(page, "Alpha").drag_to(_section(page, "unsorted"))
    wait_for_field(page, "Alpha", "sort_order", None)
    assert _open_titles(page, "sorted") == ["Two", "One"]
    assert [t["title"] for t in sorted(
        (t for t in page.request.get("/api/deck").json() if t["sort_order"] is not None),
        key=lambda t: t["sort_order"])] == ["Two", "One"]


def test_drop_lands_where_the_pointer_is_from_anywhere(page, wiki):
    # Regression: a task from Unsorted landed after the task dropped on,
    # one from within Sorted (moving up) before it. Now the pointer decides,
    # the same from either section: upper half before, lower half after.
    page.set_viewport_size({"width": 1000, "height": 1200})
    ids = [page.request.post("/api/deck", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo", "Charlie")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.request.post("/api/deck", data={"title": "New"})
    page.goto("/deck?view=open")
    _drop(card(page, "New"), card(page, "Bravo"), "before")          # from Unsorted
    wait_until(lambda: _open_titles(page, "sorted") == ["Alpha", "New", "Bravo", "Charlie"])
    _drop(card(page, "Charlie"), card(page, "Alpha"), "before")      # within Sorted, up
    wait_until(lambda: _open_titles(page, "sorted") == ["Charlie", "Alpha", "New", "Bravo"])
    _drop(card(page, "Charlie"), card(page, "New"), "after")         # within Sorted, down
    wait_until(lambda: _open_titles(page, "sorted") == ["Alpha", "New", "Charlie", "Bravo"])
    _drop(card(page, "Bravo"), card(page, "Alpha"), "after")         # within Sorted, up
    wait_until(lambda: _open_titles(page, "sorted") == ["Alpha", "Bravo", "New", "Charlie"])
    wait_until(lambda: _by_order(page, "sort_order") == ["Alpha", "Bravo", "New", "Charlie"])


def test_open_keyboard_crosses_the_sections(page, wiki):
    ids = [page.request.post("/api/deck", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    for t in ("One", "Two"):
        page.request.post("/api/deck", data={"title": t})
    page.goto("/deck?view=open")
    # ↓ on any unsorted task (not just the last) puts it first in Sorted.
    card(page, "Two").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    wait_for_field(page, "Two", "sort_order", 0)
    assert _open_titles(page, "sorted") == ["Two", "Alpha", "Bravo"]
    # ↑ on the first sorted task sends it back to Unsorted.
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_for_field(page, "Two", "sort_order", None)
    # ↑ within Unsorted does nothing.
    card(page, "One").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    assert _open_titles(page, "unsorted") == ["Two", "One"]


def test_open_reorder_under_a_filter_keeps_hidden_tasks_in_place(page, wiki):
    ids = [page.request.post("/api/deck", data={"title": t, "tags": tags}).json()["id"]
           for t, tags in (("A1", "a"), ("B1", "b"), ("A2", "a"), ("B2", "b"))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=open&tag=a")
    assert _open_titles(page, "sorted") == ["A1", "A2"]
    card(page, "A2").drag_to(card(page, "A1"))
    wait_until(lambda: _open_titles(page, "sorted") == ["A2", "A1"])
    # Only the dragged task moves (right before A1); A1 doesn't jump past B1.
    wait_until(lambda: _by_order(page, "sort_order") == ["A2", "A1", "B1", "B2"])


def test_completed_matches_survive_the_load_race(page, wiki):
    # Regression: with a filter at load, the completed tasks were fetched in
    # parallel with the open ones. When they arrived first, the open tasks
    # replaced the list and the completed ones were lost for good: the link
    # said "No matches" on some reloads and "1 match" on others.
    t = page.request.post("/api/deck", data={"title": "Buy milk"}).json()
    page.request.put(f"/api/deck/{t['id']}", data={"completed": True})
    page.request.post("/api/deck", data={"title": "Something else"})

    def slow_open_tasks(route):
        page.wait_for_timeout(400)  # the completed tasks arrive first
        route.continue_()
    page.route("**/api/deck", slow_open_tasks)
    # Only once the (slow) open tasks are in: before that the link already
    # showed the right count, and the bug only struck when they replaced it.
    with page.expect_response("**/api/deck"):
        page.goto("/deck?view=open&search=milk")
    expect(card(page, "Something else")).to_have_count(0)  # filtered out
    page.wait_for_timeout(300)
    expect(page.locator("#task-completed-link")).to_have_text("✓ 1 match in Completed →")


def test_keep_task_goes_back_to_open_with_undo(page, wiki):
    # Ticking off a keep task (a staple on a shopping list) takes it off
    # Focus and back to Open, shows when it was last done, and can be undone.
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Milk", "keep": True, "start_date": today})
    page.clock.install()
    page.goto("/deck?view=focus")
    expect(card(page, "Milk").locator(".task-keep")).to_be_visible()
    card(page, "Milk").locator("input[type=checkbox]").check()
    page.clock.run_for(5500)   # completion grace period + fade-out
    expect(card(page, "Milk")).to_have_count(0)
    wait_for_field(page, "Milk", "start_date", None)
    assert task_by_title(page, "Milk")["completed_at"] is None
    expect(page.locator(".task-undo-toast")).to_contain_text("“Milk” done")

    page.locator(".task-undo-btn").click()
    wait_for_field(page, "Milk", "start_date", today)
    expect(card(page, "Milk")).to_be_visible()
    assert task_by_title(page, "Milk")["last_done"] is None

    # Done again, it waits on Open with the day it was last done.
    card(page, "Milk").locator("input[type=checkbox]").check()
    page.clock.run_for(5500)
    wait_for_field(page, "Milk", "last_done", today)
    open_tab(page, "open")
    expect(card(page, "Milk").locator(".task-last-done")).to_be_visible()


def test_keep_checkbox_in_modal(page, wiki):
    # keep=1 makes new tasks keep tasks; a keep task can't repeat.
    wiki.write("shop", "# Shop\n\n[[deck list=groceries keep=1 view=open]]\n")
    page.goto("/page/shop")
    page.locator("#task-add-btn").click()
    keep = page.locator("#task-edit-keep")
    expect(keep).to_be_checked()
    page.locator("#task-edit-title").fill("Bread")
    page.locator("#task-edit-form button[type=submit]").click()
    expect(card(page, "Bread")).to_be_visible()
    wait_for_field(page, "Bread", "keep", True)

    expect(card(page, "Bread")).to_have_class(re.compile(r"\btask-selected\b"))  # a new task is selected
    page.keyboard.press("Enter")
    expect(page.locator("#task-edit-modal")).to_be_visible()
    expect(keep).to_be_checked()
    expect(page.locator("#task-edit-rec-type")).to_be_disabled()
    keep.uncheck()
    page.locator("#task-edit-form button[type=submit]").click()
    wait_for_field(page, "Bread", "keep", False)


def test_checkboxes_in_task_notes(page, wiki):
    # "- [ ] item" lines in the notes are real checkboxes, as on a page;
    # ticking one edits the notes (recording when, shown as a date chip),
    # leaves the task itself alone, and can be undone.
    page.request.post("/api/deck", data={"title": "Bake", "description": "Need:\n- [ ] eggs\n- [x] flour"})
    page.goto("/deck?view=open")
    checks = card(page, "Bake").locator(".task-desc input[type=checkbox]")
    expect(checks).to_have_count(2)
    expect(checks.nth(0)).not_to_be_checked()
    expect(checks.nth(1)).to_be_checked()
    expect(card(page, "Bake").locator(".task-desc")).not_to_contain_text("[ ]")

    checks.nth(0).check()
    for _ in range(50):
        if re.fullmatch(r"Need:\n- \[x\] eggs <!--d:\S+-->\n- \[x\] flour",
                        task_by_title(page, "Bake")["description"]):
            break
        page.wait_for_timeout(100)
    assert re.fullmatch(r"Need:\n- \[x\] eggs <!--d:\S+-->\n- \[x\] flour",
                        task_by_title(page, "Bake")["description"])
    expect(card(page, "Bake").locator(".task-desc .task-date-done")).to_have_text(date.today().isoformat())
    expect(page.locator("#task-edit-modal")).to_be_hidden()
    assert task_by_title(page, "Bake")["completed_at"] is None
    expect(card(page, "Bake").locator(".task-checkbox input")).not_to_be_checked()
    # Undo unticks it again, and the date goes.
    page.locator(".task-undo-toast button", has_text="Undo").click()
    wait_for_field(page, "Bake", "description", "Need:\n- [ ] eggs\n- [x] flour")
    expect(checks.nth(0)).not_to_be_checked()


def test_notes_and_comments_render_as_on_a_page(page, wiki):
    # The page renderer draws the notes: headings, tables, highlighted code,
    # _italic_ (the formatting bar's), one paragraph for lines in a row.
    # Comments too, inline.
    tid = page.request.post("/api/deck", data={"title": "Fence", "description":
        "## Steps\nsand\nprime\n\n| coat | days |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```\n\n_soft_ wood"}).json()["id"]
    page.request.post(f"/api/deck/{tid}/comments", data={"text": "**primed** today"})
    page.goto("/deck?view=open")
    desc = card(page, "Fence").locator(".task-desc")
    expect(desc.locator("h2")).to_have_text("Steps")
    expect(desc.locator("p").first).to_have_text("sand prime")
    expect(desc.locator("table td").first).to_have_text("1")
    expect(desc.locator("pre code.hljs")).to_have_count(1)
    expect(desc.locator("em")).to_have_text("soft")
    expect(card(page, "Fence").locator(".task-card-comments strong")).to_have_text("primed")
    card(page, "Fence").dblclick()
    expect(page.locator("#task-edit-comment-list strong")).to_have_text("primed")


PASTE = """(url) => {
    const dt = new DataTransfer();
    dt.setData('text/plain', url);
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}"""


def test_title_links(page, wiki):
    # Pasting a URL into the title asks for its name, as in the notes; the
    # card shows [name](url), [[wikilinks]] and bare URLs as links, menus
    # and toasts the plain words. A javascript: link stays text.
    page.request.post("/api/deck", data={"title": "See [[Garden]] and https://e.x/a or [bad](javascript:alert(1))"})
    page.goto("/deck?view=open")
    page.locator("#task-add-btn").click()
    title = page.locator("#task-edit-title")
    title.press_sequentially("Read ")
    page.evaluate(PASTE, "https://example.com/rfc")
    page.locator("#url-name-input").fill("the RFC")
    page.locator("#url-name-input").press("Enter")
    expect(title).to_have_value("Read [the RFC](https://example.com/rfc)")
    expect(title).to_be_focused()
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    link = card(page, "Read the RFC").locator(".task-title a")
    expect(link).to_have_text("the RFC")
    expect(link).to_have_attribute("href", "https://example.com/rfc")
    expect(link).to_have_attribute("target", "_blank")
    other = page.locator(".task-item", has_text="See Garden").locator(".task-title")
    expect(other.locator("a")).to_have_count(2)
    expect(other.locator("a.wikilink")).to_have_attribute("href", "/page/garden")
    expect(other).to_contain_text("[bad](javascript:alert(1))")
    card(page, "Read the RFC").click(button="right")
    expect(page.locator(".task-menu")).to_contain_text("Read the RFC")
    expect(page.locator(".task-menu")).not_to_contain_text("](")


def test_note_checklist_starts_flush_with_the_text(page, wiki):
    # A checklist's boxes sit in the bullet column: the first starts where
    # the notes' text does, an item's text starts where a bullet item's
    # does, and a nested one is indented. The same in the modal's preview.
    page.request.post("/api/deck", data={"title": "Pack", "description":
        "Before:\n\n- [ ] tent\n  - [ ] pegs\n  - poles\n- stove"})
    page.goto("/deck?view=open")
    desc = card(page, "Pack").locator(".task-desc")
    text_x = desc.locator("p").bounding_box()["x"]
    checks = desc.locator(".task-list-item-checkbox")
    assert abs(checks.nth(0).bounding_box()["x"] - text_x) <= 1
    assert checks.nth(1).bounding_box()["x"] > text_x + 10
    # Where each item's first letter starts: tent, pegs, poles, stove.
    starts = desc.evaluate("""d => [...d.querySelectorAll('li')].map(li => {
        const t = [...li.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
        const r = document.createRange(), i = t.textContent.search(/\\S/);
        r.setStart(t, i); r.setEnd(t, i + 1);
        return r.getBoundingClientRect().x; })""")
    assert abs(starts[0] - starts[3]) <= 1        # a box item and a bullet item
    assert abs(starts[1] - starts[2]) <= 1        # nested, both kinds
    # The bullets are drawn in the boxes' column, as wide as a box.
    box = checks.nth(0).bounding_box()
    bullet = desc.evaluate("""d => { const li = d.querySelectorAll('li')[3], st = getComputedStyle(li, '::before');
        return { content: st.content, x: li.getBoundingClientRect().x + parseFloat(st.left), w: parseFloat(st.width) }; }""")
    assert bullet["content"] == '"•"'
    assert abs(bullet["x"] - box["x"]) <= 0.5 and abs(bullet["w"] - box["width"]) <= 0.5
    card(page, "Pack").dblclick()
    page.locator("#task-edit-preview-btn").click()
    preview = page.locator("#task-edit-preview")
    expect(preview.locator(".task-list-item-checkbox")).to_have_count(2)
    assert abs(preview.locator(".task-list-item-checkbox").first.bounding_box()["x"]
               - preview.locator("p").bounding_box()["x"]) <= 1


def test_notes_editor_has_the_bar_and_a_preview(page, wiki):
    page.goto("/deck?view=open")
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    notes = page.locator("#task-edit-desc")
    bar = modal.locator("#task-edit-format")
    expect(bar.locator('[data-fmt="maketask"]')).to_have_count(0)     # the editor's own
    notes.click()
    page.keyboard.type("make it loud")
    notes.evaluate("ta => ta.setSelectionRange(8, 12)")
    bar.locator('[data-fmt="bold"]').click()
    expect(notes).to_have_value("make it **loud**")
    expect(notes).to_be_focused()
    # Snippets expand on Tab; otherwise Tab moves on to the next field.
    notes.evaluate("ta => ta.setSelectionRange(ta.value.length, ta.value.length)")
    page.keyboard.type("\n/date")
    page.keyboard.press("Tab")
    expect(notes).to_have_value(re.compile(r"make it \*\*loud\*\*\n\d{4}-\d\d-\d\d$"))
    expect(notes).to_be_focused()
    page.keyboard.press("Tab")
    expect(notes).not_to_be_focused()
    # Preview takes the field's place, at its height: the notes as the card
    # will show them; Edit (or F2) goes back.
    preview = modal.locator("#task-edit-preview")
    expect(preview).to_be_hidden()
    comments_y = modal.locator("label", has_text="Tags").bounding_box()["y"]
    modal.locator("#task-edit-preview-btn").click()
    expect(preview.locator("strong")).to_have_text("loud")
    expect(notes).to_be_hidden()
    expect(bar).to_be_hidden()
    assert abs(modal.locator("label", has_text="Tags").bounding_box()["y"] - comments_y) <= 1   # nothing moved
    # Labelled, so it is never taken for the text field.
    assert preview.evaluate("el => getComputedStyle(el, '::before').content") == '"Preview"'
    modal.locator("#task-edit-notes-edit-btn").click()
    expect(notes).to_be_focused()
    page.keyboard.press("Control+End")
    page.keyboard.type("\n\n- [ ] eggs")
    page.keyboard.press("F2")
    expect(notes).to_be_hidden()
    expect(preview).to_be_focused()                     # so F2 and Esc still reach the modal
    page.keyboard.press("F2")
    expect(notes).to_be_focused()
    page.keyboard.press("F2")
    expect(preview.locator(".task-list-item-checkbox")).to_have_count(1)
    # Ticking a box in the preview ticks it in the text with the date, as
    # on the card, and the preview shows its chip; unticking takes it out.
    preview.locator(".task-list-item-checkbox").check()
    expect(preview.locator(".task-date-done")).to_have_text(date.today().isoformat())
    expect(preview).to_be_focused()
    preview.locator(".task-list-item-checkbox").uncheck()
    expect(preview.locator(".task-date-done")).to_have_count(0)
    preview.locator(".task-list-item-checkbox").check()
    expect(preview.locator(".task-date-done")).to_have_count(1)
    page.keyboard.press("F2")
    expect(notes).to_be_visible()
    expect(notes).to_have_value(re.compile(r"- \[x\] eggs <!--d:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-->$"))
    # The modal always opens in Edit.
    page.keyboard.press("F2")
    page.keyboard.press("Escape")
    page.locator("#task-add-btn").click()
    expect(notes).to_be_visible()
    expect(preview).to_be_hidden()


def test_page_and_deck_checkboxes_stay_apart(page, wiki):
    # A page with its own checklist and a deck: ticking one never ticks
    # the other, though both render the same checkboxes.
    page.request.post("/api/deck", data={"title": "Pack", "description": "- [ ] tent"})
    wiki.write("trip", "# Trip\n\n[[deck view=open]]\n\n- [ ] book train\n")
    page.goto("/page/trip")
    deck_box = card(page, "Pack").locator(".task-desc .task-list-item-checkbox")
    deck_box.check()
    for _ in range(50):
        if task_by_title(page, "Pack")["description"].startswith("- [x] tent"):
            break
        page.wait_for_timeout(100)
    assert task_by_title(page, "Pack")["description"].startswith("- [x] tent")
    assert "- [ ] book train" in wiki.read("trip")
    page.locator("article > ul .task-list-item-checkbox").check()
    for _ in range(50):
        if "- [x] book train" in wiki.read("trip"):
            break
        page.wait_for_timeout(100)
    assert "- [x] book train" in wiki.read("trip")
    assert task_by_title(page, "Pack")["description"].startswith("- [x] tent")


def test_notes_continue_checkbox_list_on_enter(page, wiki):
    # As in the page editor: Enter after a checkbox item starts the next one,
    # Enter on an empty item ends the list.
    page.goto("/deck?view=open")
    page.locator("#task-add-btn").click()
    notes = page.locator("#task-edit-desc")
    notes.click()
    page.keyboard.type("- [x] eggs")
    page.keyboard.press("Enter")
    expect(notes).to_have_value("- [x] eggs\n- [ ] ")
    page.keyboard.type("flour")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    expect(notes).to_have_value("- [x] eggs\n- [ ] flour\n\n")
    expect(page.locator("#task-edit-modal")).to_be_visible()   # Enter never submits from the notes


def test_embed_labels_rename_the_tabs(page, wiki):
    # labels= changes only the words: tabs, the back link, tooltips. Views
    # and links keep their names.
    page.request.post("/api/deck", data={"title": "Milk", "tags": "groceries", "keep": True})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries keep=1 labels=Catalogue,,Shopping_list]]\n")
    page.goto("/page/shop?view=open")
    expect(page.locator("#tasks-tabs [role='tab']")).to_have_text(["Catalogue", "Plan", "Shopping list"])
    expect(page.locator("#tasks-tabs .active")).to_have_text("Catalogue")
    expect(card(page, "Milk").locator(".task-keep")).to_have_attribute(
        "title", "Keep: back to Catalogue when ticked off")
    open_tab(page, "completed")
    expect(page.locator("#task-completed-link")).to_have_text("← Back to Catalogue")


def test_filter_excludes_tags(page, wiki):
    # -#tag hides tasks with that tag; its suggestions work like #tag's.
    for title, tags in (("Plan trip", "tags,gernot"), ("Call vet", "tags,mary"), ("Untagged", "")):
        page.request.post("/api/deck", data={"title": title, "tags": tags})
    page.goto("/deck?view=open")
    field = page.locator("#task-filter-search")
    field.click()
    page.keyboard.type("-#ma")
    expect(page.locator(".tag-ac-item").first).to_have_text("#mary")
    page.keyboard.press("Enter")
    expect(page.locator(".tasks-toolbar .tag-chip-label")).to_have_text(["-#mary"])
    expect(field).to_have_value("")
    expect(card(page, "Call vet")).to_have_count(0)
    expect(card(page, "Plan trip")).to_be_visible()
    expect(card(page, "Untagged")).to_be_visible()

    page.keyboard.type("#tags")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)   # the filter is debounced
    expect(card(page, "Untagged")).to_have_count(0)
    expect(card(page, "Plan trip")).to_be_visible()


def test_history_in_edit_modal_and_undo_leaves_no_trace(page, wiki):
    tid = page.request.post("/api/deck", data={"title": "Report"}).json()["id"]
    page.request.put(f"/api/deck/{tid}", data={"due_date": tomorrow()})
    page.goto("/deck?view=open")

    # Start today from the ⋯ menu; its undo puts the old log back.
    card(page, "Report").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Start today").click()
    wait_for_field(page, "Report", "start_date", date.today().isoformat())
    page.locator(".task-undo-btn").click()
    wait_for_field(page, "Report", "start_date", None)
    assert [e["event"] for e in task_by_title(page, "Report")["history"]] == ["created", "due"]

    card(page, "Report").click()
    page.keyboard.press("Enter")
    expect(page.locator("#task-edit-modal")).to_be_visible()
    history = page.locator("#task-edit-history")
    expect(history).to_be_visible()
    expect(history.locator("summary")).to_contain_text("2")
    history.locator("summary").click()
    expect(history.locator("li")).to_have_count(2)
    expect(history.locator("li").first).to_contain_text("Due — →")   # newest first
    expect(history.locator("li").last).to_contain_text("Created")


def test_history_of_a_task_from_before_the_log(page, wiki):
    # No history field yet: the modal still shows when it was created.
    import json
    (wiki.root / "_deck" / "tasks.json").write_text(json.dumps([{
        "id": "old-1", "title": "Ancient", "created_at": "2025-03-04T09:15:00"}]))
    page.goto("/deck?view=open")
    card(page, "Ancient").click()
    page.keyboard.press("Enter")
    history = page.locator("#task-edit-history")
    expect(history.locator("summary")).to_contain_text("1")
    history.locator("summary").click()
    expect(history.locator("li")).to_have_text("Mar 4, 2025 09:15 Created")


def test_planned_tasks_are_marked_on_every_tab(page, wiki):
    # A planned task gets a coloured left edge, the same on every tab: amber
    # when it is in Focus already (start today or earlier, also "since" a past
    # day), blue when planned later. Unplanned tasks get none.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    in_week = (date.today() + timedelta(days=4)).isoformat()
    for title, start in (("Since yesterday", yesterday), ("Thursday-ish", in_week), ("Undecided", None)):
        page.request.post("/api/deck", data={"title": title, "start_date": start})
    page.goto("/deck?view=open")
    expect(card(page, "Since yesterday")).to_have_class(re.compile(r"\btask-planned-now\b"))
    expect(card(page, "Thursday-ish")).to_have_class(re.compile(r"\btask-planned-later\b"))
    expect(card(page, "Undecided")).not_to_have_class(re.compile(r"task-planned"))
    edge = lambda title: card(page, title).evaluate("el => getComputedStyle(el).borderLeftColor")
    assert edge("Since yesterday") != edge("Thursday-ish") != edge("Undecided")
    # The 📅 date: blue for later; a past start ("since …") stays plain.
    expect(card(page, "Thursday-ish").locator(".task-focus-info")).to_have_class(re.compile(r"\bfocus-later\b"))
    expect(card(page, "Since yesterday").locator(".task-focus-info")).not_to_have_class(re.compile(r"\bfocus-(now|later)\b"))

    open_tab(page, "focus")
    expect(card(page, "Since yesterday")).to_have_class(re.compile(r"\btask-planned-now\b"))
    open_tab(page, "plan")
    expect(card(page, "Thursday-ish")).to_have_class(re.compile(r"\btask-planned-later\b"))


def test_due_colour_is_the_same_on_every_tab(page, wiki):
    # Regression: Open's Unsorted cards had a grey background that painted
    # over the deadline colour, so a task due today looked different on Open
    # than on Focus.
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Due now", "due_date": today, "start_date": today})
    page.goto("/deck?view=open")
    looks = lambda: card(page, "Due now").evaluate(
        "el => { const s = getComputedStyle(el); return [s.backgroundColor, s.borderTopColor]; }")
    expect(card(page, "Due now")).to_have_class(re.compile(r"\btask-inbox\b"))   # in Unsorted
    on_open = looks()
    for tab in ("focus", "plan"):
        open_tab(page, tab)
        expect(card(page, "Due now")).to_be_visible()
        assert looks() == on_open, tab


def test_due_date_does_not_affect_order(page, wiki):
    # A deadline marks a task (its colour), it never moves it: Open's Unsorted
    # stays newest first, Sorted stays in its order, on Open and on Focus.
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Old, due", "due_date": "2020-01-01", "start_date": today})
    page.request.post("/api/deck", data={"title": "New", "start_date": today})
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today,
                                                "due_date": due}).json()["id"]
           for t, due in (("Ranked first", None), ("Ranked second, due", today))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=open")
    assert _open_titles(page, "unsorted") == ["New", "Old, due"]
    assert _open_titles(page, "sorted") == ["Ranked first", "Ranked second, due"]
    open_tab(page, "focus")
    expect(_focus_titles(page)).to_have_text(["New", "Old, due", "Ranked first", "Ranked second, due"])


def test_history_of_a_repeating_task_continues(page, wiki):
    today = date.today().isoformat()
    tid = page.request.post("/api/deck", data={"title": "Water plants", "rec_type": "daily",
                                               "start_date": today}).json()["id"]
    page.request.put(f"/api/deck/{tid}", data={"completed": True})
    page.goto("/deck?view=open")
    card(page, "Water plants").click()
    page.keyboard.press("Enter")
    history = page.locator("#task-edit-history")
    history.locator("summary").click()
    expect(history.locator("li")).to_have_count(3)
    expect(history.locator("li").nth(0)).to_contain_text("Repeats: next")   # newest first
    expect(history.locator("li").nth(1)).to_contain_text("Done")
    expect(history.locator("li").nth(2)).to_contain_text("Created")


def test_board_drop_past_deadline_moves_deadline_too(page, wiki):
    """Regression: dropping an overdue task on a day set its start date but
    left the card in Overdue (a section the deadline decided, since gone),
    and the task started after it was due. Now it asks to move the deadline
    along; Cancel changes nothing."""
    page.set_viewport_size({"width": 1280, "height": 1400})
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=5)).isoformat()
    page.request.post("/api/deck", data={"title": "Late task", "due_date": yesterday})
    page.request.post("/api/deck", data={"title": "Due today", "due_date": today})
    page.request.post("/api/deck", data={"title": "Due soon", "due_date": soon})
    page.goto("/deck?view=plan")
    tomorrow_div = page.locator(f".board-day-divider[data-drop-day='{tomorrow()}']")
    dialog = page.locator("#task-deadline-modal")

    # Before the deadline: no question, just planned.
    card(page, "Due soon").drag_to(tomorrow_div)
    wait_for_field(page, "Due soon", "start_date", tomorrow())
    expect(dialog).not_to_be_visible()

    # Cancel: nothing is written.
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)
    card(page, "Due today").drag_to(tomorrow_div)
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("“Due today” is due today. Starting it tomorrow")
    page.locator("#task-deadline-cancel").click()
    expect(dialog).not_to_be_visible()
    page.wait_for_timeout(300)
    assert writes == []

    # Move deadline: both dates move, the card goes to Tomorrow.
    card(page, "Late task").drag_to(tomorrow_div)
    expect(dialog).to_contain_text("is due yesterday")
    page.locator("#task-deadline-confirm").click()
    wait_for_field(page, "Late task", "due_date", tomorrow())
    assert task_by_title(page, "Late task")["start_date"] == tomorrow()
    expect(page.locator(".task-undo-toast")).to_contain_text("Starts and due Tomorrow")
    day = card(page, "Late task").evaluate(
        "el => { let li = el; while (li && !li.classList.contains('board-day-divider'))"
        " li = li.previousElementSibling; return li && li.dataset.dropDay; }")
    assert day == tomorrow()

    # Undo restores both dates.
    page.locator(".task-undo-btn").click()
    wait_for_field(page, "Late task", "due_date", yesterday)
    assert task_by_title(page, "Late task")["start_date"] is None


def test_overdue_dropped_on_today_starts_without_asking(page, wiki):
    # Catching up on a missed deadline: no question, as everywhere else.
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Late task",
                                         "due_date": (date.today() - timedelta(days=2)).isoformat()})
    page.goto("/deck?view=plan")
    card(page, "Late task").drag_to(page.locator(f".board-day-divider[data-drop-day='{today}']"))
    wait_for_field(page, "Late task", "start_date", today)
    expect(page.locator("#task-deadline-modal")).to_be_hidden()
    expect(page.locator(".board-day-overdue")).to_have_count(0)


def test_unplanned_overdue_count_lists_them_even_folded(page, wiki):
    page.request.post("/api/deck", data={"title": "Late", "due_date": "2020-01-01"})
    page.request.post("/api/deck", data={"title": "Loose"})
    open_board(page, wiki)
    fold(page, "unplanned")
    count = page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-overdue")
    expect(count).to_have_text("1 overdue")
    count.click()
    menu = page.locator(".task-menu")
    expect(menu.locator(".task-menu-title")).to_have_text("Overdue")
    menu.get_by_role("menuitem", name="Late").click()
    expect(card(page, "Late")).to_have_class(re.compile(r"\btask-selected\b"))   # unfolded


def test_completed_shows_earlier_years(page, wiki):
    """Earlier years' completed tasks (the yearly archives) are one click
    away in Completed, and a search link to one opens its year."""
    year = date.today().year
    (wiki.root / "_deck" / f"completed-{year-1}.json").write_text(
        '[{"id": "old", "title": "Old done", "completed_at": "%d-05-01T10:00:00"}]' % (year - 1),
        encoding="utf-8")
    tid = page.request.post("/api/deck", data={"title": "New done"}).json()["id"]
    page.request.put(f"/api/deck/{tid}", data={"completed": True})

    page.goto("/deck?view=focus")
    page.locator("#task-completed-link").click()
    years = page.locator(".completed-years button")
    expect(years).to_have_text([str(year), str(year - 1)])
    expect(years.first).to_have_attribute("aria-pressed", "true")
    expect(card(page, "New done")).to_be_visible()
    expect(card(page, "Old done")).to_have_count(0)

    years.nth(1).click()
    expect(card(page, "Old done")).to_be_visible()
    expect(card(page, "New done")).to_have_count(0)

    # Back to a tab and to Completed again: this year.
    page.locator("#task-completed-link").click()
    page.locator("#task-completed-link").click()
    expect(card(page, "New done")).to_be_visible()
    expect(card(page, "Old done")).to_have_count(0)

    # The search result's link opens the task in its year.
    href = next(r["href"] for r in page.request.get("/api/search?q=old+done").json()
                if r.get("task_id") == "old")
    page.goto(href)
    expect(page.locator(".task-item[data-id='old']")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(page.get_by_text("That task is no longer in the deck.")).to_have_count(0)


# ── Start after the deadline ─────────────────────────────────────────────────

def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _pick(page, d: str):
    """Click date `d` in the open date picker, paging forward if needed."""
    picker = page.locator(".deck-datepicker")
    for _ in range(3):
        cell = picker.locator(f".dp-day[data-date='{d}']")
        if cell.count():
            cell.click()
            return
        picker.locator(".dp-nav[data-nav='1']").click()
    raise AssertionError(f"{d} not in the picker")


def test_modal_warns_when_start_is_after_due_and_offers_a_fix(page, wiki):
    page.request.post("/api/deck", data={"title": "Report", "due_date": _day(2)})
    page.goto("/deck?view=open")
    modal = open_edit(page, "Report")
    warn = modal.locator("#task-edit-date-warn")
    expect(warn).to_be_hidden()

    # Start after due: the hint offers to move the deadline (the other date).
    modal.locator("[data-date-trigger='task-edit-start']").click()
    _pick(page, _day(5))
    expect(warn).to_be_visible()
    expect(warn).to_contain_text("Starts after its deadline")
    warn.get_by_role("button", name=re.compile("^Move deadline to")).click()
    expect(modal.locator("#task-edit-due")).to_have_value(_day(5))
    expect(warn).to_be_hidden()

    # Due moved before start: the fix moves the start instead.
    modal.locator("[data-date-trigger='task-edit-due']").click()
    _pick(page, _day(3))
    expect(warn.get_by_role("button", name=re.compile("^Move start to"))).to_be_visible()
    warn.get_by_role("button", name=re.compile("^Move start to")).click()
    expect(modal.locator("#task-edit-start")).to_have_value(_day(3))

    # Only a hint: a late task may start after its deadline on purpose.
    modal.locator("[data-date-trigger='task-edit-start']").click()
    _pick(page, _day(6))
    expect(warn).to_be_visible()
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Report", "start_date", _day(6))
    assert task_by_title(page, "Report")["due_date"] == _day(3)


def test_quick_start_after_the_deadline_asks(page, wiki):
    page.request.post("/api/deck", data={"title": "Due today", "due_date": _day(0)})
    page.request.post("/api/deck", data={"title": "Late task", "due_date": _day(-1)})
    page.goto("/deck?view=open")
    dialog = page.locator("#task-deadline-modal")

    # ⋯ Start tomorrow on a task due today: asks; Cancel changes nothing.
    card(page, "Due today").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Start tomorrow").click()
    expect(dialog).to_be_visible()
    page.locator("#task-deadline-cancel").click()
    page.wait_for_timeout(300)
    assert task_by_title(page, "Due today")["start_date"] is None

    # F (start tomorrow) on the selected card: Move deadline moves both.
    card(page, "Due today").locator(".task-title").click()
    page.keyboard.press("F")
    expect(dialog).to_be_visible()
    page.locator("#task-deadline-confirm").click()
    wait_for_field(page, "Due today", "due_date", _day(1))
    assert task_by_title(page, "Due today")["start_date"] == _day(1)

    # An overdue task started today: catching up, no question.
    card(page, "Late task").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Start today").click()
    wait_for_field(page, "Late task", "start_date", _day(0))
    expect(dialog).to_be_hidden()
    assert task_by_title(page, "Late task")["due_date"] == _day(-1)


def test_comments_in_edit_modal(page, wiki):
    page.request.post("/api/deck", data={"title": "Plan on-call"})
    page.goto("/deck?view=open")
    modal = open_edit(page, "Plan on-call")
    field = modal.locator("#task-edit-comment")
    field.fill("asked Alex")
    field.press("Enter")                       # adds, doesn't submit the form
    expect(modal).to_be_visible()
    items = modal.locator(".task-comment-list li")
    expect(items.locator(".task-comment-text")).to_have_text(["asked Alex"])
    expect(field).to_have_value("")
    field.fill("6 people")
    modal.get_by_role("button", name="Add").click()
    expect(items.locator(".task-comment-text")).to_have_text(["6 people", "asked Alex"])   # newest first
    # Saved at once: Cancel keeps them, and the card shows the count.
    modal.get_by_role("button", name="Cancel").click()
    wait_for_field(page, "Plan on-call", "comments",
                   task_by_title(page, "Plan on-call")["comments"])
    assert [c["text"] for c in task_by_title(page, "Plan on-call")["comments"]] == ["asked Alex", "6 people"]
    badge = card(page, "Plan on-call").locator(".task-comments-btn")
    expect(badge).to_have_text("💬 2")
    assert badge.get_attribute("title").endswith(": 6 people")
    # The badge opens the modal with the comment field focused.
    badge.click()
    expect(field).to_be_focused()
    # Edit in place: Esc cancels without closing the modal, Enter saves.
    items.first.hover()
    items.first.get_by_role("button", name="Edit comment").click()
    edit = items.first.locator(".task-comment-edit")
    edit.fill("nope")
    edit.press("Escape")
    expect(modal).to_be_visible()
    expect(items.first.locator(".task-comment-text")).to_have_text("6 people")
    items.first.hover()
    items.first.get_by_role("button", name="Edit comment").click()
    edit.fill("6 people, maybe 7")
    edit.press("Enter")
    expect(items.first.locator(".task-comment-text")).to_have_text("6 people, maybe 7")
    # Delete, then undo.
    items.first.hover()
    items.first.get_by_role("button", name="Delete comment").click()
    expect(items.locator(".task-comment-text")).to_have_text(["asked Alex"])
    page.locator(".task-undo-btn").click()
    expect(items.locator(".task-comment-text")).to_have_text(["6 people, maybe 7", "asked Alex"])
    # The toast sits in the modal (else the dialog covers it) and stays,
    # clickable, once the modal is closed.
    items.first.hover()
    items.first.get_by_role("button", name="Delete comment").click()
    modal.get_by_role("button", name="Cancel").click()
    expect(modal).to_be_hidden()
    page.locator(".task-undo-btn").click()
    expect(card(page, "Plan on-call").locator(".task-comments-btn")).to_have_text("💬 2")
    assert len(task_by_title(page, "Plan on-call")["comments"]) == 2


def test_new_task_has_no_comments_field(page, wiki):
    page.goto("/deck?view=open")
    page.locator(".tasks-app").get_by_role("button", name="New task").click()
    expect(page.locator("#task-edit-comments")).to_be_hidden()


def test_date_picker_shortcuts_move_the_start_date(page, wiki):
    page.request.post("/api/deck", data={"title": "Shift me", "start_date": date.today().isoformat()})
    page.goto("/deck?view=open")
    chip = card(page, "Shift me").locator(".task-focus-info")
    for label, days in (("Tomorrow", 1), ("In 2 days", 2), ("In a week", 7), ("Today", 0),
                        ("Next Monday", (7 - date.today().weekday()) or 7)):
        chip.click()
        page.locator(".deck-datepicker").get_by_role("button", name=label, exact=True).click()
        expect(page.locator(".deck-datepicker")).to_have_count(0)
        wait_for_field(page, "Shift me", "start_date", (date.today() + timedelta(days=days)).isoformat())
    # The current value is marked, and the tooltip names the day.
    chip.click()
    btn = page.locator(".deck-datepicker").get_by_role("button", name="Next Monday")
    expect(btn).to_have_class(re.compile(r"\bdp-selected\b"))
    assert btn.get_attribute("title").startswith("Next Monday: Mon")
    # All five fit on one row, also at finger size (test_touch covers the phone).
    quick = page.locator(".dp-quick-btn")
    expect(quick).to_have_text(["Today", "Tmrw", "+2d", "Mon", "+1w"])
    assert quick.evaluate_all("bs => bs.every(b => b.scrollWidth <= b.clientWidth)")
    assert len(set(quick.evaluate_all("bs => bs.map(b => Math.round(b.getBoundingClientRect().top))"))) == 1



def test_new_param_opens_one_modal_on_a_page_with_several_decks(page, wiki):
    # Regression: the first embed always took ?new=1 (and removed it from
    # the URL), even on a page with the deck's home further down. The home
    # embed opens it, as the New buttons do, else the first one.
    wiki.write("decks", "# Decks\n\n[[deck list=shop]]\n\n[[deck list=task home=1]]\n")
    page.goto("/page/decks?new=1")
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_have_count(1)
    expect(modal.locator(".tag-chip")).to_contain_text("task")
    page.keyboard.press("Escape")
    wiki.write("decks", "# Decks\n\n[[deck list=shop]]\n\n[[deck list=task]]\n")
    page.goto("/page/decks?new=1")
    expect(modal).to_have_count(1)
    expect(modal.locator(".tag-chip")).to_contain_text("shop")


def test_section_wikilinks_in_task_notes(page, wiki):
    # deck.js renders [[Page#Section]] like the server: the heading's anchor,
    # "Page › Section" as label; [[Page#Section|label]] keeps the label.
    wiki.write("garden", "# Garden\n\n## Raised beds\n")
    page.request.post("/api/deck", data={"title": "Weed", "description": "See [[Garden#Raised beds]] and [[Garden#Raised beds|there]]."})
    page.goto("/deck?view=open")
    expect(page.locator(".task-item a[href='/page/garden#raised-beds']")).to_have_text(["Garden › Raised beds", "there"])


def test_external_links_in_tasks_open_a_new_tab(page, wiki):
    page.request.post("/api/deck", data={"title": "Read https://example.com/a",
                                          "description": "[docs](https://example.org) and https://example.net/x and [[Home]]"})
    page.goto("/deck?view=open")
    item = page.locator(".task-item").first
    for href in ("https://example.com/a", "https://example.org", "https://example.net/x"):
        link = item.locator(f'a[href="{href}"]')
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(item.locator('a[href="/page/home"]')).not_to_have_attribute("target", "_blank")


# ── Compact (one line per task) ──────────────────────────────────────────────

def _compact_tasks(page):
    page.request.post("/api/deck", data={"title": "Renew passport", "tags": "personal,admin,travel",
                                          "due_date": "2026-11-15", "description": "Photos first.\nBring the old one."})
    page.request.post("/api/deck", data={"title": "Plain"})


def test_compact_switch_one_line_per_task(page, wiki):
    _compact_tasks(page)
    page.goto("/deck?view=open")
    passport = card(page, "Renew passport")
    full = passport.bounding_box()["height"]
    expect(passport.locator(".task-desc")).to_be_visible()
    page.locator(".deck-density-btn").click()
    expect(page.locator(".tasks-app")).to_have_class(re.compile(r"\bdeck-compact\b"))
    expect(page.locator(".deck-density-btn")).to_have_attribute("aria-pressed", "true")
    # One line: title, tags, the notes marker and the deadline side by side.
    box = passport.bounding_box()
    assert box["height"] < 45 < full
    expect(passport.locator(".task-desc")).to_be_hidden()
    for sel in (".task-title", ".task-tags-right .task-tag >> nth=0", ".task-due"):
        b = passport.locator(sel).bounding_box()
        assert box["y"] <= b["y"] and b["y"] + b["height"] <= box["y"] + box["height"], sel
    # Remembered for this deck; v switches back.
    page.reload()
    expect(page.locator(".tasks-app")).to_have_class(re.compile(r"\bdeck-compact\b"))
    page.locator("body").click(position={"x": 5, "y": 300})
    page.keyboard.press("v")
    expect(page.locator(".tasks-app")).not_to_have_class(re.compile(r"\bdeck-compact\b"))
    expect(passport.locator(".task-desc")).to_be_visible()


COMPACT = re.compile(r"\bdeck-compact\b")


def test_compact_defaults_per_tab(page, wiki):
    # Every tab starts with one line per task. A switch is remembered per
    # deck and tab, across reloads.
    real_density(page)
    _compact_tasks(page)
    app = page.locator(".tasks-app")
    page.goto("/deck?view=open")
    expect(app).to_have_class(COMPACT)
    for view in ("focus", "plan", "completed"):
        open_tab(page, view)
        expect(app).to_have_class(COMPACT)
    # Full cards on Open; Focus stays compact: each tab keeps its own.
    open_tab(page, "open")
    page.locator(".deck-density-btn").click()
    expect(app).not_to_have_class(COMPACT)
    open_tab(page, "focus")
    expect(app).to_have_class(COMPACT)
    page.reload()                                       # the URL says Open
    expect(app).not_to_have_class(COMPACT)              # Open, as left
    open_tab(page, "focus")
    expect(app).to_have_class(COMPACT)                  # Focus, as left


def test_compact_choice_is_per_deck(page, wiki):
    # Every deck starts compact; a choice is per deck.
    real_density(page)
    wiki.write("shop", "# Shop\n\n[[deck view=focus]]\n")
    _compact_tasks(page)
    page.goto("/page/shop")
    expect(page.locator(".tasks-app")).to_have_class(COMPACT)
    page.locator(".deck-density-btn").click()             # Focus there: full
    page.goto("/deck?view=focus")
    expect(page.locator(".tasks-app")).to_have_class(COMPACT)       # /deck: still the default
    page.goto("/page/shop")
    expect(page.locator(".tasks-app")).not_to_have_class(COMPACT)


def test_phone_cards_show_every_tag_never_clip_dates(new_page, wiki):
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _compact_tasks(phone)
    phone.request.post("/api/deck", data={"title": "Pack for the trip", "due_date": "2026-11-15",
                                          "tags": "admin,travel,family,urgent,paperwork,portugal"})
    phone.goto("/deck?view=open")
    tid = task_by_title(phone, "Renew passport")["id"]
    phone.request.put(f"/api/deck/{tid}", data={"rec_type": "yearly", "rec_interval": 10})
    phone.request.post(f"/api/deck/{tid}/comments", data={"text": "Shop closes at 6"})
    phone.request.post("/api/deck", data={"title": "Review the canary dashboards carefully", "start_date": tomorrow()})
    phone.reload()
    expect(card(phone, "Pack for the trip")).to_be_visible()
    for switch in (False, True):                       # both densities
        if switch:
            phone.locator(".deck-density-btn").tap()
        assert phone.evaluate("document.documentElement.scrollWidth") <= 390
        for item in phone.locator(".task-item").all():
            box = item.bounding_box()
            assert box["height"] >= 44                                   # finger-sized
            # Regression: with markers (↻ 💬) or a "📅 Tomorrow" the dates
            # were cut at the line's end; every line's meta fits.
            assert item.evaluate("el => { const m = el.querySelector('.task-meta'); return !m || m.scrollWidth <= m.clientWidth + 1; }"), item.inner_text()
            due = item.locator(".task-due:not(.task-date-empty)")
            if due.count():
                d = due.bounding_box()
                assert due.evaluate("el => el.scrollWidth <= el.clientWidth + 1") and d["x"] + d["width"] <= box["x"] + box["width"]
            # Every tag shows, whole: tags wrap onto more lines rather than
            # drop out when the line is short.
            for tag in item.locator(".task-tag").all():
                expect(tag).to_be_visible()
                b = tag.bounding_box()
                assert tag.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), tag.inner_text()
                assert box["x"] <= b["x"] and b["x"] + b["width"] <= box["x"] + box["width"]
                assert b["y"] + b["height"] <= box["y"] + box["height"]
        trip = card(phone, "Pack for the trip")
        tags = [t.bounding_box() for t in trip.locator(".task-tag").all()]
        assert len(tags) == 6 and tags[-1]["y"] > tags[0]["y"]          # on two lines


def test_phone_card_menu_sits_on_the_title_line(new_page, wiki):
    # ⋯ belongs to the first line (the title's), so the second line, with
    # tags and dates, has the card's whole width; the first line doesn't
    # grow for the finger-sized button.
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/deck", data={"title": "Pay rent", "tags": "home,money", "due_date": "2026-11-15"})
    phone.request.post("/api/deck", data={"title": "Plain"})
    phone.goto("/deck?view=open")
    expect(card(phone, "Pay rent")).to_be_visible()
    for switch in (False, True):                       # both densities
        if switch:
            phone.locator(".deck-density-btn").tap()
        compact = "deck-compact" in phone.locator(".tasks-app").get_attribute("class")
        rent = card(phone, "Pay rent")
        title, menu = rent.locator(".task-content").bounding_box(), rent.locator(".task-menu-btn").bounding_box()
        meta = rent.locator(".task-meta").bounding_box()
        assert abs((menu["y"] + menu["height"] / 2) - (title["y"] + title["height"] / 2)) < 2, (menu, title)
        assert menu["width"] >= 40 and menu["height"] >= 40
        assert meta["y"] >= title["y"] + title["height"] - 1
        assert meta["x"] + meta["width"] > menu["x"]                    # the line runs under ⋯
        # A line with nothing but a title stays one finger-sized line (full
        # cards on touch offer the empty ⏰ 📅 in a second line).
        if compact:
            assert card(phone, "Plain").bounding_box()["height"] < 56


def test_full_cards_show_every_tag_compact_lines_stay_one_line(page, wiki):
    # Short of room, a full card's tags wrap onto more lines (the dates stay
    # at the first line's end); a compact line stays one line, its tags give
    # way.
    page.set_viewport_size({"width": 800, "height": 800})
    page.request.post("/api/deck", data={"title": "Call the insurance about the broken window in the kitchen",
                                         "tags": "home,insurance,money,calls,waiting,landlord,repairs",
                                         "due_date": tomorrow()})
    page.goto("/deck?view=open")
    app, item = page.locator(".tasks-app"), card(page, "Call the insurance about the broken window in the kitchen")
    if "deck-compact" in app.get_attribute("class"):
        page.locator(".deck-density-btn").click()
    expect(app).not_to_have_class(COMPACT)
    box, due = item.bounding_box(), item.locator(".task-due").bounding_box()
    tags = item.locator(".task-tag")
    expect(tags).to_have_count(7)
    for tag in tags.all():
        b = tag.bounding_box()
        assert box["x"] <= b["x"] and b["x"] + b["width"] <= due["x"]     # whole, left of the dates
        assert b["y"] + b["height"] <= box["y"] + box["height"]           # inside the card
    first = tags.first.bounding_box()
    assert tags.last.bounding_box()["y"] > first["y"]                    # wrapped
    assert abs((due["y"] + due["height"] / 2) - (first["y"] + first["height"] / 2)) < 2
    page.locator(".deck-density-btn").click()
    expect(app).to_have_class(COMPACT)
    line = item.bounding_box()
    assert line["height"] < box["height"]
    t = item.locator(".task-tags-right").bounding_box()
    hidden = [b for b in (tag.bounding_box() for tag in tags.all()) if b["y"] >= t["y"] + t["height"]]
    assert hidden                                                       # some gave way


# ── Compact: expand a task in place ──────────────────────────────────────────

def _expand_setup(page):
    page.request.post("/api/deck", data={"title": "Renew passport", "tags": "admin", "due_date": "2026-11-15",
                                          "description": "At the shop.\n- [ ] old passport"})
    page.request.post("/api/deck", data={"title": "Plain"})
    page.goto("/deck?view=open")
    page.locator(".deck-density-btn").click()


def test_compact_selecting_expands_enter_opens(page, wiki):
    # Compact: the selected task is expanded to its full card; Enter opens
    # the modal, as on full cards.
    _expand_setup(page)
    passport = card(page, "Renew passport")
    one_line = passport.bounding_box()["height"]
    expect(passport.locator(".task-desc")).to_be_hidden()
    passport.locator(".task-content").click()                      # select: expands
    expect(passport).to_have_class(re.compile(r"\btask-expanded\b"))
    expect(passport.locator(".task-desc")).to_be_visible()
    assert passport.bounding_box()["height"] > one_line + 30
    expect(page.locator(".task-edit-modal[open]")).to_have_count(0)
    # The notes' checkboxes work; the save re-renders, the task stays open.
    passport.locator(".task-desc .task-list-item-checkbox").check()
    expect(card(page, "Renew passport").locator(".task-desc .task-date-done")).to_be_visible()
    assert task_by_title(page, "Renew passport")["description"].startswith("At the shop.\n- [x] old passport <!--d:")
    expect(card(page, "Renew passport")).to_have_class(re.compile(r"\btask-expanded\b"))
    # The deck ignores its keys while an input has focus: the ticked box
    # keeps it until the save re-renders the card.
    expect(page.locator(".task-list-item-checkbox:focus")).to_have_count(0)
    page.keyboard.press("Enter")                                    # the modal
    expect(page.locator(".task-edit-modal[open] [id^=task-edit-title]")).to_have_value("Renew passport")
    page.keyboard.press("Escape")
    # Esc collapses (the selection stays); Enter still opens the modal.
    page.keyboard.press("Escape")
    expect(card(page, "Renew passport")).not_to_have_class(re.compile(r"\btask-expanded\b"))
    expect(card(page, "Renew passport")).to_have_class(re.compile(r"\btask-selected\b"))
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(card(page, "Renew passport")).not_to_have_class(re.compile(r"\btask-expanded\b"))
    # Moving the selection expands the newly selected task (when it has
    # more to show: "Plain" doesn't), the previous one closes.
    # (Newest first: "Plain" is above.)
    page.keyboard.press("k")
    expect(card(page, "Plain")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(page.locator(".task-expanded")).to_have_count(0)
    page.keyboard.press("j")
    expect(card(page, "Renew passport")).to_have_class(re.compile(r"\btask-expanded\b"))
    expect(page.locator(".task-expanded.task-selected")).to_have_count(1)


def test_compact_double_click_below_expanded_opens_that_card(page, wiki):
    # A press on the card below the expanded one doesn't collapse that one
    # at once (which would pull the card up under the pointer): the
    # double-click opens the card it started on, the other collapses after.
    page.request.post("/api/deck", data={"title": "Below", "description": "Some notes."})
    page.request.post("/api/deck", data={"title": "Long notes", "description": "\n\n".join(f"Line {i}" for i in range(12))})
    page.goto("/deck?view=open")
    page.locator(".deck-density-btn").click()
    long_notes, below = card(page, "Long notes"), card(page, "Below")
    long_notes.locator(".task-content").click()
    expect(long_notes).to_have_class(re.compile(r"\btask-expanded\b"))
    page.wait_for_timeout(500)
    for _ in range(2):                       # every time, not just the first
        box = below.locator(".task-content").bounding_box()
        page.mouse.move(box["x"] + 10, box["y"] + box["height"] / 2)
        # Each press of the double-click leaves the card where it was.
        top = box["y"]
        page.mouse.down()
        page.mouse.up()
        assert below.locator(".task-content").bounding_box()["y"] == top
        assert "task-expanded" in long_notes.get_attribute("class")
        page.mouse.down(click_count=2)
        assert below.locator(".task-content").bounding_box()["y"] == top
        assert "task-expanded" in long_notes.get_attribute("class")
        page.mouse.up(click_count=2)
        expect(page.locator(".task-edit-modal[open] [id^=task-edit-title]")).to_have_value("Below")
        page.keyboard.press("Escape")
        expect(long_notes).not_to_have_class(re.compile(r"\btask-expanded\b"))
        assert below.locator(".task-content").bounding_box()["y"] < top
        long_notes.locator(".task-content").click()
        expect(long_notes).to_have_class(re.compile(r"\btask-expanded\b"))
        page.wait_for_timeout(500)
    # A single click: the card stays put for the double-click window.
    top = below.bounding_box()["y"]
    below.locator(".task-content").click()
    assert below.bounding_box()["y"] == top
    expect(long_notes).not_to_have_class(re.compile(r"\btask-expanded\b"))
    # The card below closes the same way when the one above is clicked.
    expect(below).to_have_class(re.compile(r"\btask-expanded\b"))
    page.wait_for_timeout(500)
    long_notes.locator(".task-content").click()
    assert "task-expanded" in below.get_attribute("class")
    expect(long_notes).to_have_class(re.compile(r"\btask-expanded\b"))
    expect(below).not_to_have_class(re.compile(r"\btask-expanded\b"))


def test_compact_task_with_nothing_more_stays_a_line(page, wiki):
    # No notes, no comments, a title that fits: selecting it changes
    # nothing (no "expanded" card a few pixels taller); Enter opens it.
    _expand_setup(page)
    plain = card(page, "Plain")
    before = plain.bounding_box()
    plain.locator(".task-content").click()
    expect(plain).to_have_class(re.compile(r"\btask-selected\b"))
    expect(plain).not_to_have_class(re.compile(r"\btask-expanded\b"))
    assert plain.bounding_box()["height"] == before["height"]
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open] [id^=task-edit-title]")).to_have_value("Plain")


def test_compact_tap_on_nothing_more_selects_then_opens(new_page, wiki):
    # A task with nothing more to show stays a line: the first tap only
    # selects it, the second opens the modal.
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _expand_setup(phone)
    plain = card(phone, "Plain")
    plain.locator(".task-content").tap()
    expect(plain).to_have_class(re.compile(r"\btask-selected\b"))
    expect(plain).not_to_have_class(re.compile(r"\btask-expanded\b"))
    expect(phone.locator(".task-edit-modal[open]")).to_have_count(0)
    plain.locator(".task-content").tap()
    expect(phone.locator(".task-edit-modal[open]")).to_be_visible()


def test_full_cards_enter_opens_the_modal_at_once(page, wiki):
    page.request.post("/api/deck", data={"title": "Plain"})
    page.goto("/deck?view=open")
    card(page, "Plain").locator(".task-content").click()
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()


def test_compact_tap_expands_then_opens(new_page, wiki):
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _expand_setup(phone)
    passport = card(phone, "Renew passport")
    passport.locator(".task-content").tap()
    expect(passport).to_have_class(re.compile(r"\btask-expanded\b"))
    expect(phone.locator(".task-edit-modal[open]")).to_have_count(0)
    passport.locator(".task-content").tap()
    expect(phone.locator(".task-edit-modal[open]")).to_be_visible()


def test_deck_keys_work_right_after_the_modal_closes(page, wiki):
    # Regression: focus stayed on the closed modal's title field, so j/k,
    # Enter and Esc did nothing until a click somewhere.
    for t in ("Alpha", "Bravo"):
        page.request.post("/api/deck", data={"title": t})
    page.goto("/deck?view=open")
    card(page, "Bravo").locator(".task-content").click()
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".task-edit-modal[open]")).to_have_count(0)
    page.keyboard.press("j")
    expect(card(page, "Alpha")).to_have_class(re.compile(r"\btask-selected\b"))


def test_compact_tags_follow_the_title(page, wiki):
    # In the line the tags (pills, as on full cards) come right after the
    # title; dates and markers sit at the right end.
    _compact_tasks(page)
    page.goto("/deck?view=open")
    page.locator(".deck-density-btn").click()
    passport = card(page, "Renew passport")
    expect(passport.locator(".task-tag").first).to_have_text("#personal")
    title, first, due = (passport.locator(sel).first.bounding_box() for sel in (".task-title", ".task-tag", ".task-due"))
    assert first["x"] - (title["x"] + title["width"]) < 30
    row = passport.bounding_box()
    assert due["x"] + due["width"] > row["x"] + row["width"] - 90


# ── Card actions with a mouse ────────────────────────────────────────────────

def test_card_actions_on_hover_and_right_click_menu(page, wiki):
    # ⋯ is always shown; ⏭ on hover and on the selected card.
    page.request.post("/api/deck", data={"title": "Water plants", "description": "See https://example.com",
                                          "due_date": "2026-11-12", "rec_type": "weekly", "rec_interval": 1})
    page.request.post("/api/deck", data={"title": "Other"})
    page.goto("/deck?view=open")
    water = card(page, "Water plants")
    skip = water.locator(".task-skip-btn")
    expect(water.locator(".task-wontdo-btn")).to_have_count(0)        # no ✗ on cards
    page.mouse.move(0, 0)
    expect(water.locator(".task-menu-btn")).to_be_visible()
    expect(skip).to_be_hidden()
    water.hover()
    expect(skip).to_be_visible()
    water.locator(".task-content").click()                             # selected: stays shown
    page.mouse.move(0, 0)
    page.wait_for_timeout(200)
    expect(skip).to_be_visible()
    # Right-click: the same menu, at the pointer; its actions work.
    box = card(page, "Other").locator(".task-title").bounding_box()
    page.mouse.click(box["x"] + 10, box["y"] + 5, button="right")
    menu = page.locator(".task-menu")
    expect(menu.locator(".task-menu-title")).to_have_text("Other")
    m = menu.bounding_box()
    assert abs(m["x"] - (box["x"] + 10)) < 6 and m["y"] >= box["y"]
    expect(card(page, "Other")).to_have_class(re.compile(r"\btask-selected\b"))
    menu.get_by_role("menuitem", name="Won't do").click()
    expect(card(page, "Other")).to_have_count(0)
    # A link in a card keeps the browser's menu.
    water.locator("a[href='https://example.com']").click(button="right")
    expect(page.locator(".task-menu")).to_have_count(0)


def test_dates_end_at_the_right_edge(page, wiki):
    # What is always shown ends at the same place on every card, just left
    # of ⋯ at the right end, a recurring card's (which has ⏭) too. Hover
    # adds ⏭ and the empty 📅 left of it, so nothing on the card moves.
    page.request.post("/api/deck", data={"title": "Weekly review", "due_date": "2026-11-12",
                                          "rec_type": "weekly", "rec_interval": 1, "tags": "work"})
    page.request.post("/api/deck", data={"title": "Call the plumber", "due_date": "2026-11-14"})
    page.goto("/deck?view=open")
    page.mouse.move(0, 0)
    ends = []
    for title in ("Weekly review", "Call the plumber"):
        menu, due = card(page, title).locator(".task-menu-btn").bounding_box(), card(page, title).locator(".task-due").bounding_box()
        ends.append(menu["x"] - (due["x"] + due["width"]))
    assert abs(ends[0] - ends[1]) < 1 and 0 <= ends[0] < 20
    weekly = card(page, "Weekly review")
    shown = (".task-tag", ".task-recurring", ".task-due", ".task-title", ".task-menu-btn")
    before = {sel: weekly.locator(sel).bounding_box() for sel in shown}
    weekly.locator(".task-title").hover()
    expect(weekly.locator(".task-skip-btn")).to_be_visible()
    for sel, box in before.items():
        after = weekly.locator(sel).bounding_box()
        assert abs(after["x"] - box["x"]) < 1 and abs(after["y"] - box["y"]) < 1, sel
    boxes = [weekly.locator(sel).bounding_box() for sel in
             (".task-skip-btn", ".task-focus-info.task-date-empty", ".task-recurring", ".task-due", ".task-menu-btn")]
    assert all(l["x"] + l["width"] <= r["x"] + 1 for l, r in zip(boxes, boxes[1:]))    # left to right
    dots, title = weekly.locator(".task-menu-btn svg").bounding_box(), weekly.locator(".task-title").bounding_box()
    assert abs((dots["y"] + dots["height"] / 2) - (title["y"] + title["height"] / 2)) < 1.5   # ⋯ on the line's centre


def test_compact_line_marks_notes(page, wiki):
    # 📝 says a task has notes, which a compact line hides; every card shows
    # it, so expanding a line changes nothing on it.
    page.request.post("/api/deck", data={"title": "Has notes", "description": "Some details"})
    page.request.post("/api/deck", data={"title": "No notes"})
    page.goto("/deck?view=open")
    marker = card(page, "Has notes").locator(".task-has-notes")
    expect(marker).to_be_visible()                                       # full card
    page.locator(".deck-density-btn").click()
    expect(marker).to_be_visible()
    expect(marker).to_have_attribute("title", "Has notes")
    expect(card(page, "No notes").locator(".task-has-notes")).to_have_count(0)
    card(page, "Has notes").locator(".task-content").click()            # expanded
    expect(card(page, "Has notes").locator(".task-desc")).to_be_visible()
    expect(marker).to_be_visible()


def test_expanded_task_keeps_its_line(page, wiki):
    # Expanding keeps the first line as it is (tags and dates don't move);
    # the title wraps instead of being cut off, the notes open below.
    page.request.post("/api/deck", data={"title": "Renew the passport before the trip to Lisbon in spring next year, with new photos from the shop on Main Street and the form",
                                          "tags": "admin", "due_date": "2026-11-15", "description": "At the shop."})
    page.set_viewport_size({"width": 800, "height": 800})
    page.goto("/deck?view=open")
    page.locator(".deck-density-btn").click()
    item = page.locator(".task-item").first
    item.locator(".task-content").hover()   # as when clicking it: hover shows the empty 📅 too
    before = {sel: item.locator(sel).first.bounding_box() for sel in (".task-tag", ".task-due")}
    title = item.locator(".task-content")
    text_wider = "el => { const r = document.createRange(); r.selectNodeContents(el); return r.getBoundingClientRect().width > el.clientWidth + 1; }"
    assert title.evaluate(text_wider)                                    # cut off in the line
    item.locator(".task-content").click()                            # select: expands
    expect(item).to_have_class(re.compile(r"\btask-expanded\b"))
    for sel, box in before.items():
        after = item.locator(sel).first.bounding_box()
        assert abs(after["x"] - box["x"]) < 1 and abs(after["y"] - box["y"]) < 3, sel
    assert not title.evaluate(text_wider)                                # the whole title, wrapped
    desc = item.locator(".task-desc").bounding_box()
    assert desc["y"] > title.bounding_box()["y"] + title.bounding_box()["height"] - 1


def test_expanded_task_shows_its_latest_comments(page, wiki):
    tid = page.request.post("/api/deck", data={"title": "Renew passport"}).json()["id"]
    for text in ("Photos done", "Form printed", "Appointment Tue", "Paid the fee"):
        page.request.post(f"/api/deck/{tid}/comments", data={"text": text})
    page.goto("/deck?view=open")
    passport = card(page, "Renew passport")
    expect(passport.locator(".task-card-comments p")).to_have_count(3)   # full cards show them
    page.locator(".deck-density-btn").click()
    expect(passport.locator(".task-card-comments")).to_be_hidden()   # the line: hidden too
    passport.locator(".task-content").click()                       # select: expands
    comments = passport.locator(".task-card-comments p")
    expect(comments).to_have_count(3)                                # the latest 3, newest first
    expect(comments.first).to_contain_text("Paid the fee")
    expect(comments.last).to_contain_text("Form printed")
    passport.get_by_role("button", name="1 earlier…").click()       # the rest: in the modal
    expect(page.locator(".task-edit-modal[open] #task-edit-comment")).to_be_focused()


def test_compact_on_a_phone_is_two_lines(new_page, wiki):
    # A phone's line is too short for title and dates: the title has the
    # first line (nearly the full width), tags and dates the second; a task
    # with neither stays one line. Rows stay finger-sized.
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _compact_tasks(phone)
    phone.goto("/deck?view=open")
    phone.locator(".deck-density-btn").tap()
    passport, plain = card(phone, "Renew passport"), card(phone, "Plain")
    row, title, due = (passport.locator(sel).first.bounding_box() for sel in (".task-content", ".task-content", ".task-due"))
    row = passport.bounding_box()
    assert title["width"] > row["width"] * 0.6
    assert due["y"] >= title["y"] + title["height"] - 1                  # dates on the second line
    assert 44 <= row["height"] < 64
    assert 44 <= plain.bounding_box()["height"] < 52                     # nothing for a second line


def test_empty_dates_show_on_hover_in_lines_too(page, wiki):
    # With a mouse the empty ⏰ / 📅 placeholders (to set a date) show on
    # hover, on one-line cards as on full ones.
    page.request.post("/api/deck", data={"title": "No dates yet"})
    real_density(page)                       # Open: one line per task
    page.goto("/deck?view=open")
    expect(page.locator(".tasks-app")).to_have_class(COMPACT)
    empty = card(page, "No dates yet").locator(".task-date-empty")
    page.mouse.move(0, 0)
    expect(empty.first).to_be_hidden()
    card(page, "No dates yet").hover()
    expect(empty).to_have_count(2)
    expect(empty.first).to_be_visible()
    expect(empty.last).to_be_visible()


def _list_rows(page):
    """Headings and task titles on the list, top to bottom."""
    return page.evaluate("""() => [...document.querySelectorAll('.tasks-list > li')].map(li =>
        li.classList.contains('task-item') ? li.querySelector('.task-title').textContent.trim()
        : '# ' + li.querySelector('.board-day-label')?.textContent.trim())""")


def test_open_sorts_by_deadline_then_created_and_remembers_it(page, wiki):
    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=9)).isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "due_date": d}).json()["id"]
           for t, d in (("Plain", None), ("Later due", later), ("Soon due", soon))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.request.post("/api/deck", data={"title": "Fresh"})
    page.goto("/deck?view=open")
    sel = page.locator("select.deck-sort")
    expect(sel).to_have_value("manual")
    sel.select_option("due")
    assert _list_rows(page) == ["# By deadline", "Soon due", "Later due",
                                "# No deadline", "Fresh", "Plain"]
    # Nothing drags in this order.
    expect(page.locator(".task-item[data-drag]")).to_have_count(0)
    sel.select_option("created")
    assert _list_rows(page) == ["# Newest first", "Fresh", "Soon due", "Later due", "Plain"]
    # Remembered on reload; Focus has no sort.
    page.reload()
    expect(card(page, "Fresh")).to_be_visible()
    assert _list_rows(page)[0] == "# Newest first"
    open_tab(page, "focus")
    expect(sel).to_be_hidden()
    open_tab(page, "open")
    # o goes on to the manual order.
    page.keyboard.press("o")
    expect(sel).to_have_value("manual")
    assert _list_rows(page)[0] == "# Unsorted"


def test_open_by_deadline_does_not_reorder(page, wiki):
    soon = (date.today() + timedelta(days=2)).isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "due_date": soon}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=open")
    page.locator("select.deck-sort").select_option("due")
    card(page, "Bravo").click()
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)
    page.keyboard.press("Control+Shift+ArrowUp")
    expect(page.get_by_text("press o until Manual")).to_be_visible()
    page.wait_for_timeout(200)
    assert writes == []


def test_plan_sorts_each_section_and_keeps_its_own_sort(page, wiki):
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=9)).isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today, "due_date": d}).json()["id"]
           for t, d in (("Old", later), ("Mid", None), ("New", soon))]
    page.request.post("/api/deck/reorder", data={"order": [ids[1], ids[0], ids[2]]})
    page.goto("/deck?view=plan")
    expect(card(page, "New")).to_be_visible()
    sel = page.locator("select.deck-sort")
    expect(sel).to_have_value("manual")
    assert _day_titles(page, today) == ["Mid", "Old", "New"]
    page.keyboard.press("o")
    expect(sel).to_have_value("due")
    assert _day_titles(page, today) == ["New", "Old", "Mid"]
    # Sorted, a day is not reorderable: no drop line, and keys only explain.
    expect(page.locator('.board-day-divider[data-ordered]')).to_have_count(0)
    page.keyboard.press("o")
    expect(sel).to_have_value("created")
    assert _day_titles(page, today) == ["New", "Mid", "Old"]
    # Open keeps its own sort, Plan's is remembered.
    open_tab(page, "open")
    expect(sel).to_have_value("manual")
    page.reload()
    open_tab(page, "plan")
    expect(sel).to_have_value("created")
