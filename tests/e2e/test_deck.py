"""Deck: Plan-board drag-to-plan and the Backlog "Unplanned only" toggle."""

from datetime import date, timedelta

from playwright.sync_api import expect


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
    page.locator("#tasks-tabs").select_option("due")
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

    wait_for_field(page, "Draggable", "focus_date", tomorrow())
    # The card re-renders under the Tomorrow section.
    expect(card(page, "Draggable")).to_be_visible()
    section = card(page, "Draggable").evaluate(
        "el => { let li = el; while (li && !li.classList.contains('board-day-divider'))"
        " li = li.previousElementSibling; return li && li.dataset.dropDay; }")
    assert section == tomorrow()


def test_drag_to_someday_clears_plan(page, wiki):
    page.request.post("/api/deck", data={"title": "Planned", "focus_date": tomorrow()})
    # Someday is the last section; Playwright's HTML5 drag can't auto-scroll
    # mid-drag, so make both ends fit on screen.
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    target = page.locator(".board-day-divider[data-drop-bucket='someday']")
    card(page, "Planned").drag_to(target)
    wait_for_field(page, "Planned", "focus_date", None)
    expect(page.locator(".board-day-divider[data-drop-bucket='someday'] .board-day-count")).to_have_text("1")


def test_backlog_unplanned_toggle(page, wiki):
    page.request.post("/api/deck", data={"title": "Unplanned one"})
    page.request.post("/api/deck", data={"title": "Planned one", "focus_date": tomorrow()})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    page.locator("#tasks-tabs").select_option("open")
    toggle = page.locator("#task-unplanned")
    expect(toggle).to_be_checked()  # on by default
    expect(card(page, "Unplanned one")).to_be_visible()
    expect(card(page, "Planned one")).to_have_count(0)

    toggle.uncheck()
    expect(card(page, "Planned one")).to_be_visible()

    # A standing preference: survives a reload.
    page.reload()
    page.locator("#tasks-tabs").select_option("open")
    expect(page.locator("#task-unplanned")).not_to_be_checked()
    expect(card(page, "Planned one")).to_be_visible()
