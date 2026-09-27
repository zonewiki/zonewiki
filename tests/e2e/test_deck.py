"""Deck: Plan board, start/due dates in the edit modal, and the Open tab's "Unplanned only" toggle."""

import re
from datetime import date, timedelta

from playwright.sync_api import expect

from conftest import open_tab


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
    open_tab(page, "due")
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
    # The card re-renders under the Tomorrow section.
    expect(card(page, "Draggable")).to_be_visible()
    section = card(page, "Draggable").evaluate(
        "el => { let li = el; while (li && !li.classList.contains('board-day-divider'))"
        " li = li.previousElementSibling; return li && li.dataset.dropDay; }")
    assert section == tomorrow()


def test_drag_to_unplanned_clears_start(page, wiki):
    page.request.post("/api/deck", data={"title": "Planned", "start_date": tomorrow()})
    # Unplanned is the last section; Playwright's HTML5 drag can't auto-scroll
    # mid-drag, so make both ends fit on screen.
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    target = page.locator(".board-day-divider[data-drop-bucket='unplanned']")
    card(page, "Planned").drag_to(target)
    wait_for_field(page, "Planned", "start_date", None)
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-count")).to_have_text("1")


def test_all_tab_unplanned_toggle(page, wiki):
    page.request.post("/api/deck", data={"title": "Unplanned one"})
    page.request.post("/api/deck", data={"title": "Planned one", "start_date": tomorrow()})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    open_tab(page, "open")
    toggle = page.locator("#task-unplanned")
    expect(toggle).not_to_be_checked()  # off by default: Open shows every open task
    expect(card(page, "Unplanned one")).to_be_visible()
    expect(card(page, "Planned one")).to_be_visible()

    toggle.check()
    expect(card(page, "Planned one")).to_have_count(0)
    expect(card(page, "Unplanned one")).to_be_visible()

    # A standing preference: survives a reload.
    page.reload()
    open_tab(page, "open")
    expect(page.locator("#task-unplanned")).to_be_checked()
    expect(card(page, "Planned one")).to_have_count(0)


def test_old_stored_default_does_not_keep_toggle_on(page, wiki):
    # v2 of the preference defaulted on; browsers that stored it must now see
    # the new default (off) rather than a leftover "1".
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    page.evaluate("k => localStorage.setItem(k, '1')", "leaf-deck-unplanned-v2:/page/deck#0")
    page.reload()
    open_tab(page, "open")
    expect(page.locator("#task-unplanned")).not_to_be_checked()


def test_embed_pin_turns_toggle_on(page, wiki):
    page.request.post("/api/deck", data={"title": "Planned one", "start_date": tomorrow()})
    wiki.write("deck", "# Deck\n\n[[deck view=all unplanned=1]]\n")
    page.goto("/page/deck")
    expect(page.locator("#task-unplanned")).to_be_checked()
    expect(card(page, "Planned one")).to_have_count(0)


def test_view_tabs_segmented_control(page, wiki):
    page.request.post("/api/deck", data={"title": "Some task"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    tabs = page.locator("#tasks-tabs [role='tab']")
    expect(tabs).to_have_text(["Open", "Plan", "Focus"])
    # Focus is the default view and the only one marked active.
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Focus"])
    expect(page.locator("#task-unplanned-wrap")).to_be_hidden()
    tabs.nth(0).click()
    expect(page.locator("#task-unplanned-wrap")).to_be_visible()

    tabs.nth(1).click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])
    expect(page.locator(".board-day-divider").first).to_be_visible()
    expect(page.locator("#task-unplanned-wrap")).to_be_hidden()

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


def test_legacy_filter_url_param_still_works(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?filter=plan")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])
    page.goto("/deck?view=today")   # the Focus tab's label for a while
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])


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


def test_due_badge_count_matches_focus_due_section(page, wiki):
    # The badge counts deadlines (overdue + due today) and links to Focus,
    # which also lists started tasks; its Due section shows exactly the
    # counted ones, so the number is visible where the link lands.
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    page.request.post("/api/deck", data={"title": "Overdue", "due_date": d(-2)})
    page.request.post("/api/deck", data={"title": "Due today", "due_date": d(0)})
    page.request.post("/api/deck", data={"title": "Started", "start_date": d(-1)})
    page.request.post("/api/deck", data={"title": "Started, due later",
                                         "start_date": d(0), "due_date": d(5)})
    # No deck.md: the badge goes to the built-in /deck page.
    page.goto("/page/index")
    badge = page.locator("#deck-due-badge")
    expect(page.locator("#deck-due-count")).to_have_text("2")
    expect(badge).to_have_attribute("title", "1 overdue · 1 due today")
    badge.click()
    expect(page).to_have_url(re.compile(r"/deck\?view=focus$"))
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Focus"])
    pinned = page.locator(".task-item[data-due-pinned]")
    expect(pinned).to_have_count(2)
    expect(pinned.locator(".task-title")).to_have_text(["Overdue", "Due today"])
    expect(page.locator(".task-inbox-divider--only").first).to_have_text("Due")
    expect(card(page, "Started")).to_be_visible()
    expect(card(page, "Started, due later")).to_be_visible()


def test_deck_page_opens_linked_task_and_lists(page, wiki):
    # /deck is one built-in main deck, whatever embeds the wiki's pages have.
    wiki.write("deck", "# Deck\n\n[[deck tag=work]]\n\n[[deck view=plan]]\n")
    tid = page.request.post("/api/deck", data={"title": "Untagged"}).json()["id"]
    page.request.post("/api/deck", data={"title": "Milk", "list": "groceries"})
    page.goto(f"/deck?task={tid}&view=open")
    expect(page.locator(".tasks-app")).to_have_count(1)
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    expect(card(page, "Untagged")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(card(page, "Milk")).to_have_count(0)

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
    # A past start date means "available since" — Today, not Overdue.
    assert section_of(page, "Started earlier") == today.isoformat()
    # A deadline alone is not a plan: the task waits in Unplanned, not on its due day.
    assert section_of(page, "Deadline only") == "unplanned"
    # Only a missed deadline is overdue.
    assert section_of(page, "Missed").startswith("Overdue")


def open_edit(page, title: str):
    card(page, title).locator(".task-menu-btn").click()
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
    card(page, "Loaded").locator(".task-menu-btn").click()
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


def test_task_link_lifts_unplanned_only_for_a_planned_task(page, wiki):
    ids = _task_link_deck(page, wiki, "[[deck view=all unplanned=1]]")
    page.request.put(f"/api/deck/{ids[0]}", data={"start_date": tomorrow()})
    page.goto(f"/page/deck?task={ids[0]}&view=open")
    expect(page.locator(f".task-item[data-id='{ids[0]}']")).to_have_class(
        re.compile(r"\btask-selected\b"))
    expect(page.locator("#task-unplanned")).not_to_be_checked()


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


# The edit modal's List field.

def test_list_field_moves_task_into_list(page, wiki):
    page.request.post("/api/deck", data={"title": "Prune"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    modal = open_edit(page, "Prune")
    # Usually set by the embed, so tucked into a closed section.
    expect(modal.locator("#task-edit-list")).to_be_hidden()
    modal.locator("#task-edit-links summary").click()
    modal.locator("#task-edit-list").fill("Garden")
    expect(modal.locator("#task-edit-links-summary")).to_have_text("Garden")
    modal.get_by_role("button", name="Save").click()
    # It leaves the main deck and says where it went.
    expect(card(page, "Prune")).to_have_count(0)
    expect(page.get_by_text("Saved to list “garden”.")).to_be_visible()
    wait_for_field(page, "Prune", "list", "garden")


# Completed: a link under the list instead of a tab.

def test_completed_link_opens_completed_and_goes_back(page, wiki):
    for title in ("Open one", "Done one", "Done two"):
        tid = page.request.post("/api/deck", data={"title": title}).json()["id"]
        if title.startswith("Done"):
            page.request.put(f"/api/deck/{tid}", data={"completed": True})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    open_tab(page, "due")
    link = page.locator("#task-completed-link")
    expect(link).to_have_text("✓ Completed (2) →")

    link.click()
    expect(page.locator("#tasks-tabs .active")).to_have_count(0)
    expect(page.locator(".task-inbox-divider--only")).to_have_text("Completed")
    expect(card(page, "Done one")).to_be_visible()
    expect(card(page, "Open one")).to_have_count(0)
    expect(link).to_have_text("← Back to open tasks")

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
    expect(page.locator("#task-completed-link")).to_have_text("← Back to open tasks")


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
    page.request.post("/api/deck", data={"title": "Oat milk", "list": "groceries"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")

    page.goto("/page/deck?view=open")
    expect(card(page, "Main task")).to_be_visible()
    expect(card(page, "Oat milk")).to_have_count(0)
    open_tab(page, "due")
    expect(card(page, "Main task")).to_be_visible()
    expect(card(page, "Oat milk")).to_have_count(0)

    page.goto("/page/shop?view=open")
    expect(card(page, "Oat milk")).to_be_visible()
    expect(card(page, "Main task")).to_have_count(0)


def test_new_task_in_list_embed_joins_list(page, wiki):
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    page.goto("/page/shop?view=open")
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    expect(modal.locator("#task-edit-list")).to_have_value("groceries")
    expect(modal.locator("#task-edit-links-summary")).to_have_text("groceries")
    modal.locator("#task-edit-title").fill("Bread")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Bread")).to_be_visible()
    wait_for_field(page, "Bread", "list", "groceries")


def test_moving_task_out_of_list_removes_it(page, wiki):
    page.request.post("/api/deck", data={"title": "Oat milk", "list": "groceries"})
    wiki.write("shop", "# Shop\n\n[[deck list=groceries]]\n")
    page.goto("/page/shop?view=open")
    card(page, "Oat milk").dblclick()
    modal = page.locator("#task-edit-modal")
    modal.locator("#task-edit-links summary").click()
    modal.locator("#task-edit-list").fill("")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Oat milk")).to_have_count(0)
    expect(page.get_by_text("Moved to the main deck.")).to_be_visible()
    wait_for_field(page, "Oat milk", "list", None)


def _focus_titles(page):
    return page.locator(".task-item:not([data-due-pinned]) .task-title")


def _by_order(page, field):
    tasks = [t for t in page.request.get("/api/deck").json() if t[field] is not None]
    return [t["title"] for t in sorted(tasks, key=lambda t: t[field])]


def test_focus_starts_from_open_order_without_new_section(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo", "Charlie")]
    page.request.post("/api/deck/reorder", data={"order": list(reversed(ids))})
    page.request.post("/api/deck", data={"title": "Unordered", "start_date": today})
    page.goto("/deck?view=focus")
    # Open's order (Charlie, Bravo, Alpha), never-ordered tasks after it.
    expect(_focus_titles(page)).to_have_text(["Charlie", "Bravo", "Alpha", "Unordered"])
    expect(page.locator(".task-inbox-divider--only", has_text="New")).to_have_count(0)
    # Nothing arranged yet: an empty Pinned section, everything Others.
    expect(_section(page, "pinned")).to_have_text(re.compile("Pinned"))
    expect(_section(page, "others")).to_have_text(re.compile("Others"))
    expect(_section(page, "pinned")).to_have_class(re.compile(r"\bboard-day--empty\b"))
    expect(_section(page, "others").locator(".board-day-count")).to_have_text("4")


def _section(page, zone):
    return page.locator(f".list-section[data-section='{zone}']")


def _arranged(page):
    return _by_order(page, "focus_order")


def test_focus_drag_between_arranged_and_not_arranged(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo", "Charlie")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_text(["Alpha", "Bravo", "Charlie"])

    # Onto the (empty) Pinned heading: only the dragged task is arranged.
    card(page, "Charlie").drag_to(_section(page, "pinned"))
    expect(_focus_titles(page)).to_have_text(["Charlie", "Alpha", "Bravo"])
    wait_for_field(page, "Charlie", "focus_order", 0)
    assert _arranged(page) == ["Charlie"]

    # Within Others nothing moves: it is always in Open's order.
    card(page, "Bravo").drag_to(card(page, "Alpha"))
    expect(_focus_titles(page)).to_have_text(["Charlie", "Alpha", "Bravo"])
    assert _arranged(page) == ["Charlie"]

    # Onto an arranged task: placed before it, nothing else is arranged.
    card(page, "Alpha").drag_to(card(page, "Charlie"))
    expect(_focus_titles(page)).to_have_text(["Alpha", "Charlie", "Bravo"])
    wait_for_field(page, "Alpha", "focus_order", 0)
    assert _arranged(page) == ["Alpha", "Charlie"]
    assert task_by_title(page, "Bravo")["focus_order"] is None

    # Onto the Others heading from below: appended to Pinned.
    card(page, "Bravo").drag_to(_section(page, "others"))
    wait_for_field(page, "Bravo", "focus_order", 2)
    expect(_section(page, "others")).to_have_class(re.compile(r"\bboard-day--empty\b"))

    # From Pinned onto the Others heading: back to unarranged.
    card(page, "Alpha").drag_to(_section(page, "others"))
    expect(_focus_titles(page)).to_have_text(["Charlie", "Bravo", "Alpha"])
    wait_for_field(page, "Alpha", "focus_order", None)
    assert _arranged(page) == ["Charlie", "Bravo"]
    # ... and onto an unarranged task works the same way.
    card(page, "Bravo").drag_to(card(page, "Alpha"))
    wait_for_field(page, "Bravo", "focus_order", None)
    expect(_focus_titles(page)).to_have_text(["Charlie", "Alpha", "Bravo"])

    # Open's order is never touched.
    assert _by_order(page, "sort_order") == ["Alpha", "Bravo", "Charlie"]


def test_focus_not_arranged_follows_open_order(page, wiki):
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


def test_focus_keyboard_crosses_the_sections_and_skips_due_pinned(page, wiki):
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Deadline", "due_date": today})
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Bravo", "Alpha")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])
    # Others can't be reordered: ↓ does nothing, ↑ pins the task (at the end
    # of Pinned), even when it is not the first of Others.
    card(page, "Alpha").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])
    page.keyboard.press("Control+Shift+ArrowUp")
    expect(_focus_titles(page)).to_have_text(["Alpha", "Bravo"])
    wait_for_field(page, "Alpha", "focus_order", 0)
    page.keyboard.press("Control+Shift+ArrowUp")  # already first below Due
    assert _arranged(page) == ["Alpha"]
    # ↓ on the last pinned task unpins it.
    page.keyboard.press("Control+Shift+ArrowDown")
    wait_for_field(page, "Alpha", "focus_order", None)
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])
    expect(page.locator(".task-item[data-due-pinned] .task-title")).to_have_text(["Deadline"])
    assert task_by_title(page, "Deadline")["focus_order"] is None


def test_focus_limit_is_off_unless_set(page, wiki):
    today = date.today().isoformat()
    for i in range(5):
        page.request.post("/api/deck", data={"title": f"Task {i}", "start_date": today})
    # No limit by default: no Focus warning, no flagged day on Plan.
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_count(5)
    expect(page.locator(".focus-over-capacity")).to_have_count(0)
    open_tab(page, "due")
    expect(page.locator(".board-day-divider", has_text="Today")).to_be_visible()
    expect(page.locator(".board-day--over")).to_have_count(0)
    # ?limit= (the /deck page) turns it on; the count includes Others.
    page.goto("/deck?view=focus&limit=5")
    expect(page.locator(".focus-over-capacity")).to_contain_text("5 tasks in focus (limit: 5)")
    open_tab(page, "due")
    expect(page.locator(".board-day--over")).to_have_count(1)
    # [[deck limit=N]] in an embed.
    wiki.write("board", "# Board\n\n[[deck view=focus limit=3]]\n")
    page.goto("/page/board")
    expect(page.locator(".focus-over-capacity")).to_contain_text("(limit: 3)")


def test_task_added_on_focus_lands_at_the_end(page, wiki):
    today = date.today().isoformat()
    page.request.post("/api/deck", data={"title": "Old unordered", "start_date": today})
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.goto("/deck?view=focus")
    card(page, "Bravo").drag_to(_section(page, "pinned"))  # arrange part of the day
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha", "Old unordered"])
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Added now")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha", "Old unordered", "Added now"])
    page.reload()
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha", "Old unordered", "Added now"])


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


def test_undo_of_start_tomorrow_restores_focus_arrangement(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Bravo", "Alpha")]
    page.request.post("/api/deck/reorder", data={"order": ids, "field": "focus_order"})
    page.goto("/deck?view=focus")
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])
    card(page, "Bravo").locator(".task-menu-btn").click()
    page.locator(".task-menu").get_by_role("menuitem", name="Start tomorrow").click()
    expect(_focus_titles(page)).to_have_text(["Alpha"])
    # Moving to a later day clears its place on Focus ...
    wait_for_field(page, "Bravo", "focus_order", None)
    # ... and undo puts it back where it was, not under "Others".
    page.locator(".task-undo-btn").click()
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])
    wait_for_field(page, "Bravo", "focus_order", 0)


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
    expect(page.locator(".task-item[data-due-pinned]")).to_have_count(0)
    # No sort menu, no "Move to New" checkbox.
    expect(page.locator("#task-sort")).to_have_count(0)
    card(page, "Alpha").dblclick()
    expect(page.locator("#task-edit-modal")).to_be_visible()
    expect(page.locator("#task-edit-inbox")).to_have_count(0)


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
    # Onto a sorted task: lands before it.
    card(page, "One").drag_to(card(page, "Bravo"))
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
    wait_until(lambda: _by_order(page, "sort_order") == ["A2", "B1", "A1", "B2"])


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


def test_focus_pin_all_appends_others_to_pinned(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/deck", data={"title": t, "start_date": today, "tags": tag}).json()["id"]
           for t, tag in (("Alpha", "x"), ("Bravo", "x"), ("Charlie", "x"), ("Hidden", "y"))]
    page.request.post("/api/deck/reorder", data={"order": ids})
    page.request.post("/api/deck/reorder", data={"order": [ids[2]], "field": "focus_order"})
    page.goto("/deck?view=focus&tag=x")
    expect(_focus_titles(page)).to_have_text(["Charlie", "Alpha", "Bravo"])
    pin_all = _section(page, "others").get_by_role("button", name="Pin all")
    pin_all.click()
    # Appended in Others' order; the task hidden by the filter stays in Others.
    wait_until(lambda: _arranged(page) == ["Charlie", "Alpha", "Bravo"])
    assert task_by_title(page, "Hidden")["focus_order"] is None
    expect(_section(page, "others")).to_have_class(re.compile(r"\bboard-day--empty\b"))
    expect(pin_all).to_have_count(0)  # nothing left to pin
