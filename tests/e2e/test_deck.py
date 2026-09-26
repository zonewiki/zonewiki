"""Deck: Plan board, start/due dates in the edit modal, and the All tab's "Unplanned only" toggle."""

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
    page.goto("/page/deck")
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
    page.goto("/page/deck")
    open_tab(page, "open")
    toggle = page.locator("#task-unplanned")
    expect(toggle).not_to_be_checked()  # off by default: All shows every open task
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
    page.goto("/page/deck")
    page.evaluate("k => localStorage.setItem(k, '1')", "leaf-deck-unplanned-v2:/page/deck#0")
    page.reload()
    open_tab(page, "open")
    expect(page.locator("#task-unplanned")).not_to_be_checked()


def test_embed_pin_turns_toggle_on(page, wiki):
    page.request.post("/api/deck", data={"title": "Planned one", "start_date": tomorrow()})
    wiki.write("deck", "# Deck\n\n[[deck filter=all unplanned=1]]\n")
    page.goto("/page/deck")
    expect(page.locator("#task-unplanned")).to_be_checked()
    expect(card(page, "Planned one")).to_have_count(0)


def test_view_tabs_segmented_control(page, wiki):
    page.request.post("/api/deck", data={"title": "Some task"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    tabs = page.locator("#tasks-tabs [role='tab']")
    expect(tabs).to_have_text(["All", "Plan", "Focus", "Completed"])
    # All is the default view and the only one marked active.
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["All"])
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
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["All"])
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Completed"])

    # The active view survives a reload within the browser tab.
    page.reload()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Completed"])


def test_embed_filter_marks_its_tab_active(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck filter=plan]]\n")
    page.goto("/page/deck")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Plan"])


def test_focus_shows_everything_whose_start_date_has_arrived(page, wiki):
    # The plan date is a *start* date: from that day on the task is available.
    # A task that started days ago is simply part of Focus — not "carried over"
    # or stale, and not singled out for a decision.
    for title, start in [("Started last week", -7), ("Starts today", 0), ("Starts tomorrow", 1)]:
        page.request.post("/api/deck", data={"title": title, "start_date":
                                              (date.today() + timedelta(days=start)).isoformat()})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    open_tab(page, "focus")
    expect(card(page, "Started last week")).to_be_visible()
    expect(card(page, "Starts today")).to_be_visible()
    expect(card(page, "Starts tomorrow")).to_have_count(0)
    expect(page.locator(".review-panel")).to_have_count(0)


def test_empty_focus_offers_planning(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
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
    page.goto("/page/deck")
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
    page.goto("/page/deck")
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
    page.goto("/page/deck")
    open_edit(page, "Undated")
    expect(page.locator("#task-edit-lead")).to_be_disabled()


# ── Stale-tab refresh ────────────────────────────────────────────────────────
# A deck tab can stay open for days; coming back to it reloads the tasks.

def _deck_with_one_task(page, wiki):
    page.request.post("/api/deck", data={"title": "Loaded"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
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
