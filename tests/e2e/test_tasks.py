"""Tasks: Plan board, start/due dates in the edit modal, views, sections and task links."""

import json
import re
import uuid
from datetime import date, datetime, timedelta

import pytest
from playwright.sync_api import expect

from conftest import list_page, make_list, more_menu, open_tab, pick_date, settle, still, wait_until
from tests import settings


def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def task_by_title(page, title: str) -> dict:
    tasks = page.request.get("/api/tasks").json()
    return next(t for t in tasks if t["title"] == title)


def wait_for_field(page, title: str, field: str, expected, timeout_ms: int = 15000):
    """Poll the API until the task exists and its field reaches the expected
    value: a dialog's submit returns before its request is through, so a new
    task may not be there yet."""
    def there():
        task = next((t for t in page.request.get("/api/tasks").json() if t["title"] == title), None)
        return task is not None and task.get(field) == expected
    wait_until(page, there, timeout_ms)


def open_board(page, wiki):
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    open_tab(page, "plan")
    expect(page.locator(".board-day-divider").first).to_be_visible()


def open_details(page, title: str):
    """Open a card's description and comments under its line."""
    item = card(page, title)
    if "task-details" not in item.get_attribute("class"):
        item.locator(".task-expand-btn").click()
    expect(item).to_have_class(re.compile(r"\btask-details\b"))
    return item


def card(page, title: str):
    # Exact title match — has_text is a substring match ("Planned one" would
    # also hit "Unplanned one").
    return page.locator(".task-item").filter(
        has=page.get_by_text(title, exact=True))


def test_drag_to_tomorrow_plans_task(page, wiki):
    page.request.post("/api/tasks", data={"title": "Draggable"})
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
    page.request.post("/api/tasks", data={"title": "Planned", "start_date": tomorrow()})
    # Unplanned is the first section; Playwright's HTML5 drag can't auto-scroll
    # mid-drag, so make both ends fit on screen.
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    target = page.locator(".board-day-divider[data-drop-bucket='unplanned']")
    card(page, "Planned").drag_to(target)
    wait_for_field(page, "Planned", "start_date", None)
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-count")).to_have_text("1")


def test_view_tabs_segmented_control(page, wiki):
    page.request.post("/api/tasks", data={"title": "Some task"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks")
    tabs = page.locator("#tasks-tabs [role='tab']")
    expect(tabs).to_have_text(["Open", "Today"])
    # Focus is the default view and the only one marked active.
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Today"])
    plan = page.locator(".tasks-cut [data-group=day]")
    as_list = page.locator(".tasks-cut [data-group=none]")
    expect(plan).to_be_hidden()

    # Open has Plan on at first: the board.
    tabs.nth(0).click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    expect(plan).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".board-day-divider[data-drop-day]").first).to_be_visible()
    # List: one list, Unsorted · Sorted; d goes back and forth.
    as_list.click()
    expect(plan).to_have_attribute("aria-pressed", "false")
    expect(page.locator(".list-section[data-section='unsorted']")).to_be_visible()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    page.keyboard.press("d")
    expect(plan).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("d")
    expect(plan).to_have_attribute("aria-pressed", "false")

    # Tab / Shift+Tab cycle through the tabs in on-screen order, wrapping;
    # keyboard focus follows the active view (from a tab), and Open comes
    # back as it was left.
    tabs.nth(0).click()
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])
    expect(page.locator("#tasks-tabs .active")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    expect(plan).to_have_attribute("aria-pressed", "false")
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])

    # The active view survives a reload within the browser tab; the
    # grouping is remembered for the tasks.
    page.reload()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])
    tabs.nth(0).click()
    expect(plan).to_have_attribute("aria-pressed", "false")


def test_embed_filter_marks_its_tab_active(page, wiki):
    wiki.write("tasks", "# Tasks\n\n[[tasks: view=open group=day]]\n")
    page.goto("/page/tasks")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])


def test_view_url_param_picks_the_tab(page, wiki):
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=day")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    # Not a view's name (here the Plan tab's old one, and ?filter=, the old
    # name of ?view=): the default, Focus, ...
    page.goto("/page/tasks?view=due")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])
    # ... or the tab last clicked in this browser tab.
    open_tab(page, "open")
    page.goto("/page/tasks?filter=plan")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])


def test_focus_shows_everything_whose_start_date_has_arrived(page, wiki):
    # The plan date is a *start* date: from that day on the task is available.
    # A task that started days ago is simply part of Focus — not "carried over"
    # or stale, and not singled out for a decision.
    for title, start in [("Started last week", -7), ("Starts today", 0), ("Starts tomorrow", 1)]:
        page.request.post("/api/tasks", data={"title": title, "start_date":
                                              (date.today() + timedelta(days=start)).isoformat()})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    open_tab(page, "focus")
    expect(card(page, "Started last week")).to_be_visible()
    expect(card(page, "Starts today")).to_be_visible()
    expect(card(page, "Starts tomorrow")).to_have_count(0)
    expect(page.locator(".review-panel")).to_have_count(0)


def test_tasks_link_counts_the_due_tasks_and_opens_focus(page, wiki):
    # The Tasks link counts deadlines (overdue + due today); the tasks opens on Focus.
    # There is no Due section: the counted tasks sit for today's order like
    # any other, marked by their deadline colour.
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    page.request.post("/api/tasks", data={"title": "Overdue", "due_date": d(-2)})
    page.request.post("/api/tasks", data={"title": "Due today", "due_date": d(0)})
    page.request.post("/api/tasks", data={"title": "Started", "start_date": d(-1)})
    page.request.post("/api/tasks", data={"title": "Started, due later",
                                         "start_date": d(0), "due_date": d(5)})
    # No tasks.md: the Tasks module link goes to the built-in /tasks page,
    # which opens on Focus.
    page.goto("/page/index")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks.locator(".module-due")).to_have_text("2")
    expect(tasks.locator(".module-focus")).to_have_text("4")
    expect(tasks).to_have_attribute("title", "Tasks: 4 for today · 1 overdue · 1 due today")
    tasks.click()
    expect(page).to_have_url(re.compile(r"/tasks$"))
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])
    expect(page.locator(".task-inbox-divider--only")).to_have_count(0)   # no Due section
    # All in Unsorted, in Open's order (newest first).
    expect(_focus_titles(page)).to_have_text(["Started, due later", "Started", "Due today", "Overdue"])
    expect(card(page, "Overdue")).to_have_class(re.compile(r"\btask-urg-overdue\b"))
    expect(card(page, "Due today")).to_have_class(re.compile(r"\btask-urg-today\b"))


def test_list_switcher_looks_as_the_module_switcher_does(page, wiki):
    # One capsule with a border holding pills without one, the current pill
    # shaded: what the module switcher at the top of the page is. More, the
    # last pill, is one of them.
    make_list(page, "shop", pin=True)
    make_list(page, "work")
    page.goto("/tasks?list=shop")
    style = lambda sel, *props: page.locator(sel).first.evaluate(
        "(el, props) => props.map(p => getComputedStyle(el)[p])", list(props))
    capsule = ("borderTopWidth", "borderTopStyle", "borderTopColor", "backgroundColor", "paddingTop", "paddingLeft")
    assert style(".tasks-places-group", *capsule) == style(".module-group", *capsule)
    pill = ("height", "fontSize", "fontWeight", "color", "backgroundColor", "borderTopWidth", "paddingLeft",
            "borderTopLeftRadius")
    assert style(".tasks-place[aria-current]", *pill) == style(".module-switch .module-link[aria-current]", *pill)
    assert style(".tasks-place:not([aria-current])", *pill) == style(".module-switch .module-link:not([aria-current])", *pill)
    # The pills are inside the capsule, on one line.
    group = page.locator(".tasks-places-group").bounding_box()
    for b in (p.bounding_box() for p in page.locator(".tasks-place").all()):
        assert group["x"] < b["x"] and b["x"] + b["width"] < group["x"] + group["width"]
        assert group["y"] < b["y"] and b["y"] + b["height"] < group["y"] + group["height"]


def test_list_switcher_goes_between_the_lists_and_keeps_its_counts(page, wiki):
    # /tasks opens with the switcher above the heading: the lists by name
    # pinned lists as pills, then More with the others and All tasks; its
    # counts follow the task app's writes.
    list_page(page, wiki, "tasks", "# My tasks\n\n[[tasks: list=task view=today pin=1]]\n")
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=shop view=today pin=1]]\n")
    make_list(page, "garden")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop", "start_date": date.today().isoformat()})
    page.goto("/tasks?list=shop")
    places = page.locator(".tasks-places .tasks-place")
    expect(places.locator(".tasks-place-label")).to_have_text(["My tasks", "Inbox", "task", "shop", "More"])
    shop = places.and_(page.locator("[data-place='list:shop']"))
    expect(shop).to_have_attribute("aria-current", "page")
    expect(shop.locator(".module-focus")).to_be_hidden()      # a pill has one number: what is due
    expect(page.locator("h1")).to_have_text("List: shop")
    expect(page.locator(".page-foot #backlinks a")).to_have_text(["Shop"])
    # One row, above the heading.
    boxes = [b.bounding_box() for b in places.all()]
    assert all(abs(b["y"] - boxes[0]["y"]) < 1 for b in boxes)
    assert boxes[0]["y"] + boxes[0]["height"] <= page.locator("h1").bounding_box()["y"]
    card(page, "Milk").locator("input[type=checkbox]").check()
    # After the checkbox's ~5 s grace period.
    expect(card(page, "Milk")).to_have_count(0, timeout=10000)
    page.locator(".tasks-place[data-place='list:task']").click()
    page.wait_for_url(re.compile(r"/tasks\?list=task$"))
    expect(page.locator(".tasks-place[aria-current]")).to_have_attribute("data-place", "list:task")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])
    # More: the lists that aren't pinned, and All tasks. It opens under its
    # pill, closes on Escape and on a click elsewhere, and names the view
    # the page shows when that is one of its own.
    more = page.locator(".lists-more")
    panel = more.locator(".lists-more-panel")
    expect(panel).to_be_hidden()
    more.locator("summary").click()
    expect(panel.locator(".lists-more-item")).to_have_text(["garden", "All tasks"])
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(more.locator("summary")).to_be_focused()
    more.locator("summary").click()
    page.locator("h1").click()
    expect(panel).to_be_hidden()
    more.locator("summary").click()
    panel.get_by_role("link", name="All tasks").click()
    page.wait_for_url(re.compile(r"/tasks$"))
    expect(page.locator(".tasks-place[aria-current]")).to_have_text("All tasks▾")
    expect(page.locator("h1")).to_have_text("All tasks")
    page.locator(".lists-more summary").click()
    expect(page.locator(".lists-more-item[aria-current]")).to_have_text("All tasks")
    page.locator(".lists-more-item", has_text="garden").click()
    page.wait_for_url(re.compile(r"/tasks\?list=garden$"))
    expect(page.locator(".tasks-place[aria-current]")).to_have_text("garden▾")
    expect(page.locator("h1")).to_have_text("List: garden")
    # A page embedding a list shows the list, no switcher.
    page.goto("/page/shop")
    expect(card(page, "Milk")).to_have_count(0)
    expect(page.locator(".tasks-places")).to_have_count(0)


def test_tasks_link_shows_my_tasks(page, wiki):
    # The Tasks link opens My tasks, one task app of every task but those of
    # a list left out of it, and counts what it shows; a pin decides nothing
    # about it, the switcher marks the pinned lists with one. A new task
    # there goes to the Inbox, and the dialog says so.
    list_page(page, wiki, "tasks", "# My tasks\n\n[[tasks: list=task view=today pin=default]]\n")
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=shop view=today pin=1]]\n")
    wiki.write("garden", "# Garden\n")
    today = date.today().isoformat()
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop", "due_date": today})
    page.request.post("/api/tasks", data={"title": "Report", "list": "task", "due_date": today})
    page.request.post("/api/tasks", data={"title": "Loose", "due_date": today})      # in the Inbox: among them
    make_list(page, "cellar")
    page.request.post("/api/tasks", data={"title": "Shelves", "list": "cellar", "due_date": today})   # a list not pinned: among them
    make_list(page, "attic", pin=True)
    assert page.request.post("/api/tasks/settings", data={"list": "attic", "set": {"home": "off"}}).ok
    page.request.post("/api/tasks", data={"title": "Boxes", "list": "attic", "due_date": today})   # a list left out, though pinned: not among them
    page.goto("/page/garden")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks.locator(".module-due")).to_have_text("4")
    tasks.click()
    page.wait_for_url(re.compile(r"/tasks\?mine=1$"))
    expect(tasks).to_have_attribute("aria-current", "page")
    expect(page.locator("h1")).to_have_text("My tasks")
    line = page.locator(".tasks-mine-line")
    expect(line).to_have_text("Every open task, but the lists left out of it (#attic).")
    expect(line.locator("a")).to_have_attribute("href", "/tasks?list=attic")
    expect(page.locator(".tasks-pinned-lists")).to_have_count(0)
    expect(page.locator(".tasks-app")).to_have_count(1)
    expect(card(page, "Report")).to_be_visible()
    expect(card(page, "Milk")).to_be_visible()
    expect(card(page, "Loose")).to_be_visible()
    expect(card(page, "Shelves")).to_be_visible()
    expect(card(page, "Boxes")).to_have_count(0)
    # Each card says which list it is in; one in the Inbox says nothing.
    expect(card(page, "Milk").locator(".task-tag-list")).to_have_text("shop")
    expect(card(page, "Loose").locator(".task-tag-list")).to_have_count(0)
    expect(page.locator(".tasks-place:has(.tasks-place-pin) .tasks-place-label")).to_have_text(["task", "shop", "attic"])
    expect(page.locator(".tasks-place-first")).to_have_count(0)        # no pill is where new tasks go
    expect(page.locator(".tasks-place[aria-current]")).to_have_attribute("data-place", "mine")
    # Filed into the list that is left out, a task leaves, and the notice says where it went.
    card(page, "Loose").dblclick()
    edit = page.locator(".task-edit-modal[open]")
    edit.locator("select[id^=task-edit-list]").select_option("attic")
    edit.get_by_role("button", name="Save").click()
    expect(card(page, "Loose")).to_have_count(0)
    expect(page.get_by_text("Saved to list “attic”.")).to_be_visible()
    expect(tasks.locator(".module-due")).to_have_text("3")
    page.locator("#task-add-btn").click()
    modal = page.locator(".task-edit-modal[open]")
    target = modal.locator(".task-edit-target")
    lists = modal.locator("select[id^=task-edit-list]")
    expect(lists).to_have_value("")
    expect(target).to_have_text("Adds to the Inbox.")
    expect(target).not_to_have_class(re.compile("task-edit-target-warn"))
    # The target follows the List field, and a #list in the title.
    lists.select_option("task")
    expect(target).to_have_text("Adds to the list task.")
    lists.select_option("shop")
    expect(target).to_have_text("Adds to the list shop.")
    lists.select_option("")
    expect(target).to_have_text("Adds to the Inbox.")
    modal.locator("[id^=task-edit-title]").fill("Eggs #shop #weekly")
    expect(target).to_have_text("Adds to the list shop.")
    expect(modal.locator("[id^=task-edit-quick]")).to_have_text("list shop · #weekly")
    modal.locator("[id^=task-edit-title]").press("Enter")
    expect(card(page, "Eggs")).to_be_visible()
    eggs = next(t for t in page.request.get("/api/tasks").json() if t["title"] == "Eggs")
    assert (eggs["list"], eggs["tags"]) == ("shop", ["weekly"])
    # Editing a task: no target line.
    card(page, "Eggs").dblclick()
    expect(page.locator(".task-edit-modal[open] .task-edit-target")).to_be_hidden()


def test_tasks_page_opens_linked_task_and_lists(page, wiki):
    # /tasks is one built-in main tasks, whatever embeds the wiki's pages have.
    wiki.write("tasks", "# Tasks\n\n[[tasks: tag=work]]\n\n[[tasks: view=open group=day]]\n")
    make_list(page, "groceries")
    tid = page.request.post("/api/tasks", data={"title": "Untagged"}).json()["id"]
    page.request.post("/api/tasks", data={"title": "Milk", "list": "groceries"})
    page.goto(f"/tasks?task={tid}&view=open")
    expect(page.locator(".tasks-app")).to_have_count(1)
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])
    expect(card(page, "Untagged")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(card(page, "Milk")).to_be_visible()     # list tasks too: it's every task

    page.goto("/tasks?list=groceries&view=open&group=none")
    expect(page.locator("h1")).to_have_text("List: groceries")
    expect(card(page, "Milk")).to_be_visible()
    expect(card(page, "Untagged")).to_have_count(0)


def test_search_modal_lists_the_tasks(page, wiki):
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-filter").fill("tasks")
    expect(page.locator(".page-list-item").first).to_contain_text("Tasks")
    page.locator("#modal-filter").press("Enter")
    expect(page).to_have_url(re.compile(r"/tasks$"))
    expect(page.locator(".tasks-app")).to_have_count(1)


def test_empty_focus_offers_planning(page, wiki):
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    open_tab(page, "focus")
    expect(page.locator(".tasks-empty")).to_contain_text("Nothing planned for today")
    page.get_by_role("button", name="Plan your day").click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])


def test_past_start_date_reads_since(page, wiki):
    # The plan date is a start date, so a past one reads "since Sep 21" —
    # not "planned 5d ago", and not styled as stale/overdue.
    start = date.today() - timedelta(days=5)
    long_ago = date(date.today().year - 1, 3, 4)
    page.request.post("/api/tasks", data={"title": "Started", "start_date": start.isoformat()})
    page.request.post("/api/tasks", data={"title": "Started last year", "start_date": long_ago.isoformat()})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    open_tab(page, "focus")
    chip = card(page, "Started").locator(".task-start-info")
    expect(chip).to_have_text(f"📅 since {start.strftime('%b')} {start.day}")
    expect(card(page, "Started last year").locator(".task-start-info")).to_have_text(
        f"📅 since Mar 4, {long_ago.year}")
    # Every date of a card is written that way, never ISO: a later start, a
    # deadline, and the day a closed task was done.
    later, due = date.today() + timedelta(days=40), date.today() + timedelta(days=45)
    short = lambda d: f"{d.strftime('%b')} {d.day}" + ("" if d.year == date.today().year else f", {d.year}")
    page.request.post("/api/tasks", data={"title": "Later", "start_date": later.isoformat(), "due_date": due.isoformat()})
    page.request.post("/api/tasks", data={"title": "Done one"})
    page.request.put(f"/api/tasks/{task_by_title(page, 'Done one')['id']}", data={"completed": True})
    page.reload()
    open_tab(page, "open")
    expect(card(page, "Later").locator(".task-start-info")).to_have_text(f"📅 {short(later)}")
    expect(card(page, "Later").locator(".task-due")).to_have_text(f"⏰ {short(due)}")
    page.locator("#task-closed-link").click()
    done = card(page, "Done one").locator(".task-date-info", has_text="done:")
    expect(done).to_have_text(f"done: {short(date.today())}")
    assert done.get_attribute("title") == date.today().isoformat()
    page.locator("#task-closed-link").click()
    open_tab(page, "focus")
    chip = card(page, "Started").locator(".task-start-info")
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
    page.request.post("/api/tasks", data={"title": "Started earlier",
                                         "start_date": (today - timedelta(days=4)).isoformat()})
    page.request.post("/api/tasks", data={"title": "Deadline only",
                                         "due_date": (today + timedelta(days=2)).isoformat()})
    page.request.post("/api/tasks", data={"title": "Missed",
                                         "due_date": (today - timedelta(days=1)).isoformat()})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    # A past start date means "available since": not Overdue, but today's
    # load (it is for today) — on Today, carried over, its badge amber.
    assert section_of(page, "Started earlier") == today.isoformat()
    expect(card(page, "Started earlier")).to_have_class(re.compile(r"\btask-carried\b"))
    expect(page.locator(".board-day-divider", has_text="Carried over")).to_have_count(0)
    # A deadline alone is not a plan: the task waits in Unplanned, not on its due day.
    assert section_of(page, "Deadline only") == "unplanned"
    # A missed deadline has no section of its own: without a start date the
    # task heads Unplanned, counted red in its header.
    assert section_of(page, "Missed") == "unplanned"
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-overdue")).to_have_text("1 overdue")


def test_today_counts_its_carried_over_tasks_and_a_drop_commits_again(page, wiki):
    today = date.today().isoformat()
    for title, days in (("Slipped", 3), ("Slipped long", 9)):
        page.request.post("/api/tasks", data={"title": title,
                                             "start_date": (date.today() - timedelta(days=days)).isoformat()})
    page.request.post("/api/tasks", data={"title": "Fresh today", "start_date": today})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    header = page.locator(f".board-day-divider[data-drop-day='{today}']")
    # Today's count is its whole load, carried-over tasks included.
    expect(header.locator(".board-day-count")).to_have_text("3")
    carried = header.locator(".board-day-carried")
    expect(carried).to_have_text("2 carried")
    carried.click()
    menu = page.locator(".task-menu")
    expect(menu.locator(".task-menu-title")).to_contain_text("Carried over")
    expect(menu.get_by_role("menuitem")).to_have_text(["Slipped long", "Slipped"])
    page.keyboard.press("Escape")
    expect(card(page, "Fresh today")).not_to_have_class(re.compile(r"\btask-carried\b"))
    # Dropped on Today's own heading, a carried-over task starts today again.
    card(page, "Slipped").drag_to(header)
    wait_for_field(page, "Slipped", "start_date", today)
    expect(carried).to_have_text("1 carried")
    expect(card(page, "Slipped")).not_to_have_class(re.compile(r"\btask-carried\b"))


def test_day_header_counts_deadlines_and_lists_them(page, wiki):
    day = (date.today() + timedelta(days=3)).isoformat()
    page.request.post("/api/tasks", data={"title": "Report", "due_date": day})
    page.request.post("/api/tasks", data={"title": "Slides", "due_date": day,
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
    page.request.post("/api/tasks", data={"title": "Loose one"})
    page.request.post("/api/tasks", data={"title": "Loose two"})
    page.request.post("/api/tasks", data={"title": "Today one", "start_date": date.today().isoformat()})
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
    page.goto("/page/tasks?view=open&group=day")   # the same tasks: folding is remembered per task list
    expect(card(page, "Today one")).to_be_visible()
    expect(card(page, "Loose one")).to_have_count(0)
    fold(page, "unplanned")
    expect(card(page, "Loose two")).to_be_visible()


def test_whole_header_folds_but_due_count_opens_its_list(page, wiki):
    day = (date.today() + timedelta(days=2)).isoformat()
    page.request.post("/api/tasks", data={"title": "On the day", "start_date": day, "due_date": day})
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
def test_list_and_tag_taskss_fold_too(page, wiki, embed):
    # Regression: the embed's locked filter chip (the list's, #task) counted
    # as the viewer filtering, which shows every section, so nothing could fold.
    make_list(page, "task")
    page.request.post("/api/tasks", data={"title": "Loose", "list": "task", "tags": ["task"]})
    wiki.write("tasks", f"# Tasks\n\n[[tasks: {embed}]]\n")
    page.goto("/page/tasks?view=open&group=day")
    expect(card(page, "Loose")).to_be_visible()
    fold(page, "unplanned")
    expect(card(page, "Loose")).to_have_count(0)
    page.locator("#task-filter-search").fill("loo")         # the viewer's own filter
    expect(card(page, "Loose")).to_be_visible()


def test_folded_section_still_takes_drops(page, wiki):
    page.request.post("/api/tasks", data={"title": "Loose"})
    page.request.post("/api/tasks", data={"title": "Planned", "start_date": tomorrow()})
    page.set_viewport_size({"width": 1280, "height": 1400})
    open_board(page, wiki)
    fold(page, "unplanned")
    card(page, "Planned").drag_to(page.locator(".board-day-divider[data-drop-bucket='unplanned']"))
    wait_for_field(page, "Planned", "start_date", None)
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned'] .board-day-count")).to_have_text("2")
    expect(card(page, "Planned")).to_have_count(0)


def test_filter_shows_folded_sections(page, wiki):
    page.request.post("/api/tasks", data={"title": "Hidden match"})
    open_board(page, wiki)
    fold(page, "unplanned")
    expect(card(page, "Hidden match")).to_have_count(0)
    page.locator("#task-filter-search").fill("match")
    expect(card(page, "Hidden match")).to_be_visible()
    expect(page.locator(".board-day-fold")).to_have_count(0)


def test_task_link_unfolds_its_section(page, wiki):
    tid = page.request.post("/api/tasks", data={"title": "Linked"}).json()["id"]
    open_board(page, wiki)
    fold(page, "unplanned")
    expect(card(page, "Linked")).to_have_count(0)
    page.goto(f"/tasks?view=open&group=day&task={tid}")
    expect(card(page, "Linked")).to_have_class(re.compile(r"\btask-selected\b"))


def test_new_task_unfolds_its_section(page, wiki):
    page.request.post("/api/tasks", data={"title": "Loose"})
    open_board(page, wiki)
    fold(page, "unplanned")
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Fresh")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(card(page, "Fresh")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(card(page, "Loose")).to_be_visible()


def test_number_keys_start_selected_tasks_on_that_day(page, wiki):
    page.request.post("/api/tasks", data={"title": "First"})
    page.request.post("/api/tasks", data={"title": "Second"})
    page.request.post("/api/tasks", data={"title": "Third"})
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
    # The undo is armed once the write's response is in, a moment after the
    # server has it: Ctrl+Z before that would undo the step before.
    expect(page.locator(".task-undo-toast")).to_contain_text("2 tasks unplanned")
    # In the filter field Ctrl+Z is the field's own: the task stays as it is.
    page.locator("#task-filter-search").focus()
    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    assert task_by_title(page, "Second")["start_date"] is None
    page.locator("#task-filter-search").blur()
    page.keyboard.press("Control+z")
    wait_for_field(page, "Second", "start_date", tomorrow())


def test_every_quick_start_is_one_rule(page, wiki):
    # f, F, the card's 📅 and a drop on a day set a start date as 1–7 do
    # (startOn): for the selection, with one undo, and nothing toggles.
    # (f and F were one task's, cleared the date when pressed again and had
    # no undo; the picker and the drop had none either.)
    for title in ("First", "Second", "Third"):
        page.request.post("/api/tasks", data={"title": title})
    open_board(page, wiki)
    toast = page.locator(".task-undo-toast")
    today = date.today().isoformat()

    # f: today, with an undo; again changes nothing.
    card(page, "First").locator(".task-title").click()
    page.keyboard.press("f")
    wait_for_field(page, "First", "start_date", today)
    expect(toast).to_contain_text("Starts Today")
    page.keyboard.press("f")
    page.wait_for_timeout(300)
    assert task_by_title(page, "First")["start_date"] == today      # no toggle
    page.keyboard.press("Control+z")
    wait_for_field(page, "First", "start_date", None)

    # F on several: all of them tomorrow, one undo.
    card(page, "Second").locator(".task-title").click()
    card(page, "Third").locator(".task-title").click(modifiers=["Shift"])
    page.keyboard.press("F")
    wait_for_field(page, "Second", "start_date", tomorrow())
    wait_for_field(page, "Third", "start_date", tomorrow())
    expect(toast).to_contain_text("2 tasks start tomorrow")
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Second", "start_date", None)
    wait_for_field(page, "Third", "start_date", None)

    # A drop on a day: an undo too.
    card(page, "First").drag_to(page.locator(".board-day-divider[data-drop-day]", has_text="Tomorrow"))
    wait_for_field(page, "First", "start_date", tomorrow())
    expect(toast).to_contain_text("Starts Tomorrow")
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "First", "start_date", None)

    # The card's 📅: the same.
    page.keyboard.press("Escape")
    card(page, "First").locator(".task-title").click()
    page.keyboard.press("f")
    wait_for_field(page, "First", "start_date", today)
    card(page, "First").locator(".task-start-info").click()
    pick_date(page.locator(".tasks-datepicker"), _day(2))
    wait_for_field(page, "First", "start_date", _day(2))
    expect(toast).to_contain_text("Starts")
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "First", "start_date", today)


def test_undo_waits_for_the_write_it_belongs_to(page, wiki):
    # A bulk start writes first and arms its undo after the response: Ctrl+Z
    # in between does nothing, instead of undoing the step before.
    page.request.post("/api/tasks", data={"title": "Second"})
    page.request.post("/api/tasks", data={"title": "Third"})
    open_board(page, wiki)
    card(page, "Second").locator(".task-title").click()
    card(page, "Third").locator(".task-title").click(modifiers=["Shift"])
    page.keyboard.press("2")
    expect(page.locator(".task-undo-toast")).to_contain_text("2 tasks start tomorrow")
    held = []
    page.route("**/api/tasks/batch-update", lambda route: held.append(route))
    page.keyboard.press("0")
    wait_until(page, lambda: held, settled=False)
    assert len(held) == 1
    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    assert len(held) == 1  # no undo request went out
    held[0].continue_()
    page.unroute("**/api/tasks/batch-update")
    expect(page.locator(".task-undo-toast")).to_contain_text("2 tasks unplanned")
    wait_for_field(page, "Second", "start_date", None)
    page.keyboard.press("Control+z")
    wait_for_field(page, "Second", "start_date", tomorrow())
    wait_for_field(page, "Third", "start_date", tomorrow())


def test_number_key_past_the_deadline_asks(page, wiki):
    page.request.post("/api/tasks", data={"title": "Due soon", "due_date": tomorrow()})
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
    page.request.post("/api/tasks", data={"title": "Report", "due_date": "2099-01-10"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    modal = open_edit(page, "Report")
    lead = modal.locator("#task-edit-lead")
    expect(lead).to_be_hidden()                       # shown once asked for
    modal.locator("#task-edit-more-lead").click()
    expect(lead).to_be_focused()
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
    page.request.post("/api/tasks", data={"title": "Undated"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    modal = open_edit(page, "Undated")
    expect(page.locator("#task-edit-lead")).to_be_disabled()
    # Nor is it offered: the line of more fields has no "Days before due".
    expect(modal.locator("#task-edit-more-keep")).to_be_visible()
    expect(modal.locator("#task-edit-more-lead")).to_be_hidden()


# ── Stale-tab refresh ────────────────────────────────────────────────────────
# A task list tab can stay open for days; coming back to it reloads the tasks.

def _tasks_with_one_task(page, wiki):
    page.request.post("/api/tasks", data={"title": "Loaded"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    expect(card(page, "Loaded")).to_be_visible()
    # Changed elsewhere (another device) while this tab sat in the background.
    page.request.post("/api/tasks", data={"title": "Added elsewhere"})


def test_tasks_refreshes_when_tab_becomes_visible(page, wiki):
    _tasks_with_one_task(page, wiki)
    page.wait_for_timeout(300)
    expect(card(page, "Added elsewhere")).to_have_count(0)   # no polling
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(card(page, "Added elsewhere")).to_be_visible()


def test_tasks_show_what_someone_else_changed_within_a_minute(page, wiki):
    """The page asks for the due count every minute while it is used; its
    Zone-Tasks word is another one after a write elsewhere, and the lists
    load again. A list that is busy (its dialog open) is told the next
    minute."""
    page.clock.install()
    page.request.post("/api/tasks", data={"title": "Loaded"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    expect(card(page, "Loaded")).to_be_visible()
    # Nothing changed: a minute later the list is as it was (no reload).
    page.mouse.move(10, 10)
    with page.expect_response(lambda r: "/api/tasks/due-count" in r.url):
        page.clock.fast_forward(61_000)
    page.request.post("/api/tasks", data={"title": "Added elsewhere"})
    # Busy: the edit dialog is open, so the list waits …
    card(page, "Loaded").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    expect(page.locator("#task-edit-modal")).to_be_visible()
    page.mouse.move(20, 20)
    with page.expect_response(lambda r: "/api/tasks/due-count" in r.url):
        page.clock.fast_forward(61_000)
    expect(card(page, "Added elsewhere")).to_have_count(0)
    # … and is told again the minute after it is closed.
    page.keyboard.press("Escape")
    expect(page.locator("#task-edit-modal")).to_be_hidden()
    page.mouse.move(30, 30)
    page.clock.fast_forward(61_000)
    expect(card(page, "Added elsewhere")).to_be_visible()


def test_a_long_list_of_every_task_loads_again_every_five_minutes(page, wiki):
    """All tasks of a team is thousands of tasks, and some task changes every
    minute: such a list is told of a change once and loads again when five
    minutes have passed since it last did."""
    page.clock.install()
    _many_tasks(wiki, 160)
    page.goto("/tasks?view=open&group=none")
    expect(card(page, "Task 000")).to_be_visible()
    page.request.post("/api/tasks", data={"title": "Added elsewhere"})
    page.mouse.move(10, 10)
    with page.expect_response(lambda r: "/api/tasks/due-count" in r.url):
        page.clock.fast_forward(61_000)
    page.clock.fast_forward(61_000)
    expect(card(page, "Added elsewhere")).to_have_count(0)
    with page.expect_response(lambda r: r.url.endswith("/api/tasks")):
        page.clock.fast_forward(200_000)
    expect(card(page, "Added elsewhere")).to_be_visible()


def test_a_board_section_of_only_unsorted_tasks_has_no_labels(page, wiki):
    """Unsorted and Sorted are labelled in a section that holds both; where
    nothing is ranked yet (a team's list nobody ordered) the label would say
    nothing, in every section. The cards are hollow all the same."""
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"] for t in ("One", "Two")]
    page.goto("/tasks?view=open&group=day")
    expect(card(page, "Two")).to_be_visible()
    expect(page.locator(".plan-subdivider")).to_have_count(0)
    expect(card(page, "One")).to_have_class(re.compile(r"\btask-inbox\b"))
    page.request.post("/api/tasks/reorder", data={"order": ids[:1]})
    page.reload()
    expect(card(page, "Two")).to_be_visible()
    expect(page.locator(".plan-subdivider")).to_have_text(["Unsorted1", "Sorted1"])
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.reload()
    expect(card(page, "Two")).to_be_visible()
    expect(page.locator(".plan-subdivider")).to_have_count(0)


def test_a_closed_task_is_deleted_for_good_from_its_menu(page, wiki):
    """Delete for good… in a closed task's menu asks first and then takes
    the task out of the wiki; an open task has no such entry (Won't do
    closes it)."""
    done = page.request.post("/api/tasks", data={"title": "Made by mistake"}).json()
    page.request.put(f"/api/tasks/{done['id']}", data={"wont_do": True})
    page.request.post("/api/tasks", data={"title": "Still open"})
    page.goto("/tasks?view=open&group=none")
    card(page, "Still open").locator(".task-title").click(button="right")
    expect(page.get_by_role("menuitem", name="Won't do")).to_be_visible()
    expect(page.get_by_role("menuitem", name="Delete for good…")).to_have_count(0)
    page.keyboard.press("Escape")
    page.goto("/tasks?view=closed")
    card(page, "Made by mistake").locator(".task-title").click(button="right")
    page.once("dialog", lambda d: d.dismiss())
    page.get_by_role("menuitem", name="Delete for good…").click()
    expect(card(page, "Made by mistake")).to_be_visible()
    card(page, "Made by mistake").locator(".task-title").click(button="right")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("menuitem", name="Delete for good…").click()
    expect(card(page, "Made by mistake")).to_have_count(0)
    assert page.request.get("/api/tasks/closed").json() == []


def test_a_series_occurrence_links_to_the_one_before(page, wiki):
    """Comments stay on the occurrence they were written on; the next one's
    History ends with a link to it."""
    first = page.request.post("/api/tasks", data={"title": "Water the plants", "rec_type": "daily", "due_date": date.today().isoformat()}).json()
    page.request.post(f"/api/tasks/{first['id']}/comments", data={"text": "The fern needs more"})
    page.request.put(f"/api/tasks/{first['id']}", data={"completed": True})
    page.goto("/tasks?view=open&group=none")
    card(page, "Water the plants").locator(".task-title").dblclick()
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    expect(modal.locator(".task-comment-body")).to_have_count(0)
    modal.locator("#task-edit-history summary").click()
    link = modal.locator(".task-history-before")
    expect(link).to_have_text("The occurrence before →")
    assert first["id"] in link.get_attribute("href") and "view=closed" in link.get_attribute("href")


def test_tab_is_the_browsers_on_a_page_that_embeds_a_list(page, wiki):
    """Tab goes through the tabs on the Tasks page, and on any page while a
    task is selected or the focus is in the list; on a page that only embeds
    a list it stays what it is everywhere, the way to the next link or
    field."""
    page.request.post("/api/tasks", data={"title": "One"})
    wiki.write("notes", "# Notes\n\n[A link](/page/index)\n\n[[tasks: view=open group=none]]\n")
    page.goto("/page/notes")
    expect(card(page, "One")).to_be_visible()
    active = page.locator("#tasks-tabs .active")
    expect(active).to_have_text(["Open"])
    page.keyboard.press("Tab")
    expect(active).to_have_text(["Open"])                 # the page's Tab
    card(page, "One").locator(".task-title").click()
    page.keyboard.press("Tab")
    expect(active).to_have_text(["Today"])


def test_a_link_names_the_tab_and_how_open_shows_its_tasks(page, wiki):
    """?view= says the tab (today, open), ?group= how the Open tab shows its
    tasks; view=open alone opens it as the view's own group says (the board
    by day). The names of before 2026-11 (focus, plan) are no view's: such
    a link opens the default tab."""
    page.request.post("/api/tasks", data={"title": "One", "start_date": date.today().isoformat()})
    active = page.locator("#tasks-tabs .active")
    pressed = page.locator(".tasks-cut [aria-pressed='true']")
    page.goto("/tasks?view=open")
    expect(active).to_have_text(["Open"])
    expect(pressed).to_have_text(["Plan"])
    page.goto("/tasks?view=open&group=none")
    expect(active).to_have_text(["Open"])
    expect(pressed).to_have_text(["List"])
    expect(page.locator(".list-section[data-section]")).to_have_count(2)
    page.goto("/tasks?view=today")
    expect(active).to_have_text(["Today"])
    for old in ("focus", "plan", "week"):
        page.goto("/tasks?view=open&group=none")
        expect(active).to_have_text(["Open"])
        page.evaluate("sessionStorage.clear()")
        page.goto(f"/tasks?view={old}")
        expect(card(page, "One")).to_be_visible()
        expect(active).to_have_text(["Today"])


def test_today_of_one_person_without_lists_has_no_grouping_to_choose(page, wiki):
    """Today offers one list where there is nobody to group by and no list;
    the control shows on the Open tab, which has List and Plan."""
    page.request.post("/api/tasks", data={"title": "One", "start_date": date.today().isoformat()})
    page.goto("/tasks?view=today")
    expect(card(page, "One")).to_be_visible()
    expect(page.locator(".tasks-cut")).to_be_hidden()
    page.locator('#tasks-tabs [data-view="open"]').click()
    expect(page.locator(".tasks-cut button:visible")).to_have_text(["List", "Plan"])


def test_save_writes_only_what_was_changed_in_the_dialog(page, wiki):
    """Two people on one task: Save sends the fields changed in this dialog,
    so what someone else changed meanwhile stays; a text both changed is
    refused, and the dialog comes back with what was typed."""
    task = page.request.post("/api/tasks", data={"title": "Shared work", "description": "first"}).json()
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    card(page, "Shared work").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    # Meanwhile, elsewhere: a deadline and a tag.
    page.request.put(f"/api/tasks/{task['id']}", data={"due_date": "2031-05-06", "tags": ["elsewhere"]})
    modal.locator("#task-edit-title").fill("Shared work, renamed")
    with page.expect_request(lambda r: r.method == "PUT" and r.url.endswith(task["id"])) as sent:
        modal.get_by_role("button", name="Save").click()
    assert sent.value.post_data_json == {"title": "Shared work, renamed", "was": {"title": "Shared work"}}
    expect(modal).to_be_hidden()
    wait_until(page, lambda: page.request.get("/api/tasks").json()[0]["title"] == "Shared work, renamed")
    got = page.request.get("/api/tasks").json()[0]
    assert got["due_date"] == "2031-05-06" and got["tags"] == ["elsewhere"] and got["description"] == "first"
    # Nothing changed: nothing is sent.
    card(page, "Shared work, renamed").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    puts = []
    page.on("request", lambda r: puts.append(r) if r.method == "PUT" else None)
    modal.get_by_role("button", name="Save").click()
    expect(modal).to_be_hidden()
    page.wait_for_timeout(200)
    assert not puts
    # The description changed by both: refused, and the dialog is back with what was typed.
    card(page, "Shared work, renamed").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    page.request.put(f"/api/tasks/{task['id']}", data={"description": "theirs"})
    modal.locator("#task-edit-desc").fill("mine")
    modal.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Someone changed this task's description")).to_be_visible()
    expect(modal).to_be_visible()
    expect(modal.locator("#task-edit-desc")).to_have_value("mine")
    assert page.request.get("/api/tasks").json()[0]["description"] == "theirs"


def test_tasks_refreshes_when_restored_from_bfcache(page, wiki):
    _tasks_with_one_task(page, wiki)
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(card(page, "Added elsewhere")).to_be_visible()


def test_tasks_refresh_waits_while_editing(page, wiki):
    _tasks_with_one_task(page, wiki)
    card(page, "Loaded").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    expect(page.locator("#task-edit-modal")).to_be_visible()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    expect(card(page, "Added elsewhere")).to_have_count(0)   # the editor is open: no re-render


# ?task=<id> — search results and a page's "Linked tasks" point at one task.

def _task_link_tasks(page, wiki, tasks="[[tasks:]]"):
    wiki.write("tasks", f"# Tasks\n\n{tasks}\n")
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"]
           for t in ("Same title", "Same title", "Fix #3 now")]
    return ids


def test_task_link_selects_that_task_by_id(page, wiki):
    ids = _task_link_tasks(page, wiki)
    page.goto(f"/page/tasks?task={ids[1]}&view=open")
    target = page.locator(f".task-item[data-id='{ids[1]}']")
    expect(target).to_have_class(re.compile(r"\btask-selected\b"))
    expect(target).to_have_class(re.compile(r"\btask-flash\b"))
    # Nothing is filtered away: the twin and the #word task are still listed.
    expect(page.locator(f".task-item[data-id='{ids[0]}']")).to_be_visible()
    expect(page.locator("#task-filter-search")).to_have_value("")


def test_task_link_to_title_with_hashword(page, wiki):
    # Regression: the old ?search=<title> link read "#3" as a tag filter and
    # showed nothing.
    ids = _task_link_tasks(page, wiki)
    page.goto(f"/page/tasks?task={ids[2]}&view=open")
    expect(page.locator(f".task-item[data-id='{ids[2]}']")).to_have_class(
        re.compile(r"\btask-selected\b"))


def test_task_link_to_completed_task(page, wiki):
    ids = _task_link_tasks(page, wiki)
    page.request.put(f"/api/tasks/{ids[0]}", data={"completed": True})
    page.goto(f"/page/tasks?task={ids[0]}&view=closed")
    expect(page.locator(f".task-item[data-id='{ids[0]}']")).to_have_class(
        re.compile(r"\btask-selected\b"))


def test_task_link_to_missing_task_says_so(page, wiki):
    _task_link_tasks(page, wiki)
    page.goto("/page/tasks?task=no-such-id&view=open")
    expect(page.get_by_text("That task is no longer in your tasks.")).to_be_visible()


# The List field of a task's dialog moves the task into another list.

def test_the_list_field_moves_a_task_to_another_list(page, wiki):
    for name in ("chores", "garden"):
        make_list(page, name)
    page.request.post("/api/tasks", data={"title": "Prune", "list": "chores", "tags": "weekly"})
    wiki.write("home", "# Home\n\n[[tasks: list=chores]]\n")
    page.goto("/page/home?view=open&group=none")
    # In a view of its list a card doesn't say the list; its tags are tags.
    expect(card(page, "Prune").locator(".task-tag-list")).to_have_count(0)
    modal = open_edit(page, "Prune")
    lists = modal.locator("#task-edit-list")
    expect(lists).to_have_value("chores")
    expect(lists.locator("option")).to_have_text(["Inbox", "chores", "garden", "New list…"])
    expect(modal.locator(".tag-chip")).to_have_text(["#weekly×"])      # the list is no tag
    lists.select_option("garden")
    modal.get_by_role("button", name="Save").click()
    # It leaves this list and says where it went.
    expect(card(page, "Prune")).to_have_count(0)
    expect(page.get_by_text("Saved to list “garden”.")).to_be_visible()
    wait_for_field(page, "Prune", "list", "garden")
    assert task_by_title(page, "Prune")["tags"] == ["weekly"]


def test_the_list_field_makes_a_new_list(page, wiki):
    make_list(page, "chores")
    page.request.post("/api/tasks", data={"title": "Rake"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Rake")
    lists = modal.locator("#task-edit-list")
    lists.select_option(label="New list…")
    dlg = page.locator(".list-new-modal")
    name = dlg.locator("#task-new-list-name")
    expect(name).to_be_focused()
    expect(lists).to_have_value("")                                   # New list… is no choice: the field is as it was
    expect(dlg.locator("#task-new-list-private")).to_have_count(0)    # no users: no private lists
    # Escape leaves only this dialog; the edit modal stays.
    page.keyboard.press("Escape")
    expect(dlg).to_have_count(0)
    expect(modal).to_be_visible()
    expect(lists).to_be_focused()
    # A name there is: said in the dialog. Then the list is made and the field names it.
    lists.select_option(label="New list…")
    name.fill("chores")
    name.press("Enter")
    expect(dlg.locator(".rename-error")).to_have_text("There is a list #chores already")
    expect(modal).to_be_visible()
    name.fill("Garden")
    name.press("Enter")
    expect(dlg).to_have_count(0)
    expect(lists).to_have_value("garden")
    expect(lists.locator("option")).to_have_text(["Inbox", "chores", "garden", "New list…"])
    assert "list" not in task_by_title(page, "Rake")                  # the task moves with Save
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Rake", "list", "garden")
    expect(card(page, "Rake").locator(".task-tag-list")).to_have_text("garden")
    # A new task: the line under its title follows the list just made.
    page.keyboard.press("Escape")
    page.locator("#task-add-btn").click()
    lists.select_option(label="New list…")
    name.fill("yard")
    dlg.get_by_role("button", name="Create").click()
    expect(page.locator("#task-edit-target")).to_have_text("Adds to the list yard.")


def test_a_new_list_from_the_list_field_is_as_private_as_the_task(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    make_list(page, "team")
    page.request.post("/api/tasks", data={"title": "Bob's errand", "list": "team"})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "own", private=True)
    page.request.post("/api/tasks", data={"title": "Alice's errand", "list": "own"})
    page.goto("/tasks?view=open&group=none")
    lists, note = page.locator("#task-edit-list"), page.locator("#task-edit-list-note")
    box = page.locator("#task-new-list-private")
    # A shared task stays shared: its new list can't be private.
    open_edit(page, "Bob's errand")
    lists.select_option(label="New list…")
    expect(box).to_be_disabled()
    expect(box).not_to_be_checked()
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    # A private task: its new list starts private, and the task stays so.
    modal = open_edit(page, "Alice's errand")
    lists.select_option(label="New list…")
    expect(box).to_be_checked()
    page.locator("#task-new-list-name").fill("secret")
    page.locator("#task-new-list-create").click()
    expect(lists).to_have_value("secret")
    expect(lists.locator("option")).to_have_text(["🔒 Inbox", "🔒 own", "🔒 secret", "team", "New list…"])
    expect(note).to_have_text("Private: only you see this task.")
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Alice's errand", "list", "secret")
    assert task_by_title(page, "Alice's errand")["private"] is True


def test_a_tag_named_as_a_list_is_a_tag(page, wiki):
    # A list is no tag: tagging a task with a list's name leaves it where it
    # is. On the main task list a task that goes into a list stays in view,
    # with its list as a chip.
    page.request.post("/api/tasks", data={"title": "Prune"})
    make_list(page, "garden")
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Prune")
    modal.locator("#task-edit-tags").fill("garden,")
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Prune", "tags", ["garden"])
    expect(card(page, "Prune")).to_be_visible()
    expect(card(page, "Prune").locator(".task-tag-list")).to_have_count(0)
    assert "list" not in task_by_title(page, "Prune")
    modal = open_edit(page, "Prune")
    modal.locator("#task-edit-list").select_option("garden")
    modal.get_by_role("button", name="Save").click()
    wait_for_field(page, "Prune", "list", "garden")
    expect(card(page, "Prune").locator(".task-tag-list")).to_have_text("garden")
    expect(card(page, "Prune").locator(".task-tag")).to_have_text(["garden", "#garden"])   # its list first, then its tags
    expect(page.get_by_text("Saved to list “garden”.")).to_have_count(0)


# Completed: a link under the list instead of a tab.

def test_completed_link_opens_completed_and_goes_back(page, wiki):
    for title in ("Open one", "Done one", "Done two"):
        tid = page.request.post("/api/tasks", data={"title": title}).json()["id"]
        if title.startswith("Done"):
            page.request.put(f"/api/tasks/{tid}", data={"completed": True})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks")
    open_tab(page, "plan")
    link = page.locator("#task-closed-link")
    expect(link).to_have_text("Closed (2) →")

    link.click()
    expect(page.locator("#tasks-tabs .active")).to_have_count(0)
    expect(page.locator(".task-inbox-divider--only")).to_have_text("Closed")
    expect(card(page, "Done one")).to_be_visible()
    expect(card(page, "Open one")).to_have_count(0)
    expect(link).to_have_text("← Back to Open")   # names the tab it returns to

    # Back returns to the tab it came from.
    link.click()
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])

    # Completed survives a reload, like any view; Tab from there (a task
    # selected: on a page that embeds a list Tab is otherwise the page's)
    # goes to the first tab.
    link.click()
    page.reload()
    expect(card(page, "Done two")).to_be_visible()
    card(page, "Done two").locator(".task-title").click()
    page.keyboard.press("Tab")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Open"])


def test_completed_count_follows_completion(page, wiki):
    page.request.post("/api/tasks", data={"title": "Finish me"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    expect(page.locator("#task-closed-link")).to_have_text("Closed (0) →")
    card(page, "Finish me").locator("input[type=checkbox]").check()
    # After the checkbox's ~5 s grace period.
    expect(page.locator("#task-closed-link")).to_have_text("Closed (1) →", timeout=10000)


def test_embed_filter_completed_still_opens_completed(page, wiki):
    tid = page.request.post("/api/tasks", data={"title": "Old work"}).json()["id"]
    page.request.put(f"/api/tasks/{tid}", data={"completed": True})
    wiki.write("log", "# Log\n\n[[tasks: view=closed]]\n")
    page.goto("/page/log")
    expect(card(page, "Old work")).to_be_visible()
    expect(page.locator("#task-closed-link")).to_have_text("← Back to Today")


def test_a_filter_does_not_load_the_closed_tasks(page, wiki):
    # While a filter is on, the link has no number (the total would read as
    # what the filter leaves) and the closed tasks aren't counted: that
    # loaded all of them behind every filtered list, and drew it again.
    page.request.post("/api/tasks", data={"title": "Buy milk", "tags": ["shop"]})
    for title in ("Buy bread", "Fix bike"):
        tid = page.request.post("/api/tasks", data={"title": title, "tags": ["shop"]}).json()["id"]
        page.request.put(f"/api/tasks/{tid}", data={"completed": True})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n\n[[tasks: tag=shop view=open group=none]]\n")
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/tasks/closed" in r.url and "closed-count" not in r.url else None)
    page.goto("/page/tasks?view=open&group=none")
    link = page.locator("#task-closed-link")
    search = page.locator("#task-filter-search")
    expect(link).to_have_text("Closed (2) →")
    expect(page.locator("#task-closed-link-1")).to_have_text("Closed →")   # the embed's tag is a filter

    search.fill("bread")
    expect(page.locator("#tasks-list .tasks-empty")).to_be_visible()   # nothing open matches
    expect(link).to_have_text("Closed →")
    page.wait_for_load_state("networkidle")
    assert asked == []

    link.click()   # the filter carries over into Closed, which loads them
    expect(card(page, "Buy bread")).to_be_visible()
    expect(page.locator("#tasks-list").get_by_text("Fix bike")).to_have_count(0)
    assert asked

    link.click()
    search.fill("")
    expect(link).to_have_text("Closed (2) →")
    # Any word of the filter takes the number away: ! and is:updated too
    # (they once left it, and it read as what the filter leaves).
    for words in ("!", "!low", "is:updated", "list:shop", "-list:shop"):
        search.fill(words)
        expect(link).to_have_text("Closed →")
    search.fill("")
    expect(link).to_have_text("Closed (2) →")


def test_new_task_on_default_focus_starts_today(page, wiki):
    # A plain tasks opens on Focus; a task added there starts today, so it
    # stays in the view instead of vanishing on save.
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks")
    expect(page.locator("#tasks-tabs .active")).to_have_text(["Today"])
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Water plants")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(card(page, "Water plants")).to_be_visible()
    assert task_by_title(page, "Water plants")["start_date"] == date.today().isoformat()


# Quick-add shorthand in a new task's title.

def test_quick_add_shorthand_fills_fields(page, wiki):
    from datetime import date as _date
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
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
    page.request.post("/api/tasks", data={"title": "Old task"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    modal = open_edit(page, "Old task")
    modal.locator("#task-edit-title").fill("Old task tomorrow")
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Old task tomorrow")).to_be_visible()   # saved, title kept whole
    assert task_by_title(page, "Old task tomorrow")["start_date"] is None


# ── Separate lists ([[tasks: list=…]]) ──────────────────────────────────────────


def test_list_embed_shows_only_its_tasks(page, wiki):
    make_list(page, "groceries")
    page.request.post("/api/tasks", data={"title": "Main task", "tags": "groceries"})      # tagged so: not in the list
    page.request.post("/api/tasks", data={"title": "Oat milk", "list": "groceries"})
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    wiki.write("shop", "# Shop\n\n[[tasks: list=groceries]]\n")

    # The main tasks shows every task, list tasks included.
    page.goto("/page/tasks?view=open&group=none")
    expect(card(page, "Main task")).to_be_visible()
    expect(card(page, "Oat milk")).to_be_visible()
    open_tab(page, "plan")
    expect(card(page, "Main task")).to_be_visible()
    expect(card(page, "Oat milk")).to_be_visible()

    page.goto("/page/shop?view=open&group=none")
    expect(card(page, "Oat milk")).to_be_visible()
    expect(card(page, "Main task")).to_have_count(0)


def test_the_inbox_has_a_pill_and_a_view_of_its_own(page, wiki):
    # /tasks?list=inbox: the tasks in no list, the second pill of the
    # switcher (they are in My tasks too, which counts them). A task added
    # there is in no list, and one filed into a list leaves.
    make_list(page, "groceries", pin=True)
    page.request.post("/api/tasks", data={"title": "Loose end", "due_date": date.today().isoformat()})
    page.request.post("/api/tasks", data={"title": "Oat milk", "list": "groceries"})
    page.goto("/tasks?list=groceries")
    more = page.locator(".lists-more")
    expect(more.locator("summary .module-due")).to_have_count(0)       # More has no number
    more.locator("summary").click()
    expect(more.locator(".lists-more-item")).to_have_text(["All tasks"])   # no entry for the tasks in no list there
    expect(page.locator(".lists-more-none")).to_have_count(0)
    page.keyboard.press("Escape")
    mine = page.locator('.tasks-place[data-place="mine"]')
    expect(mine.locator(".module-due")).to_have_text("1")
    inbox = page.locator('.tasks-place[data-place="inbox"]')
    expect(page.locator(".tasks-place .tasks-place-label")).to_have_text(["My tasks", "Inbox", "groceries", "More"])
    expect(inbox.locator(".module-due")).to_have_text("1")
    expect(inbox.locator(".tasks-place-lock")).to_have_count(0)        # no users: it is no one's alone
    expect(inbox.locator(".tasks-place-pin")).to_have_count(0)
    expect(inbox).to_have_attribute("title", "Inbox: new tasks land here")
    inbox.click()
    page.wait_for_url(re.compile(r"/tasks\?list=inbox$"))
    expect(page.locator("h1")).to_have_text("Inbox")
    expect(page.locator("h1 .lock")).to_have_count(0)
    expect(inbox).to_have_attribute("aria-current", "page")
    expect(more.locator("summary .tasks-place-label")).to_have_text("More")
    expect(page.locator(".tasks-inbox-line")).to_have_text("New tasks land here, in no list. Move one to a list to file it.")
    open_tab(page, "open")
    expect(card(page, "Loose end")).to_be_visible()
    expect(card(page, "Oat milk")).to_have_count(0)
    chip = page.locator(".tasks-toolbar .tag-chip")
    expect(chip).to_have_text(["Inbox"])
    expect(chip.locator(".tag-chip-x")).to_have_count(0)               # the view's own: locked
    # A new task here goes into the Inbox.
    page.locator("#task-add-btn").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal.locator(".task-edit-target")).to_have_text("Adds to the Inbox.")
    modal.locator("[id^=task-edit-title]").fill("Another loose end")
    modal.locator("[id^=task-edit-title]").press("Enter")
    expect(card(page, "Another loose end")).to_be_visible()
    assert not task_by_title(page, "Another loose end").get("list")
    # Filed into a list, a task leaves the Inbox, and the notice says where it went.
    card(page, "Another loose end").dblclick()
    modal.locator("select[id^=task-edit-list]").select_option("groceries")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Another loose end")).to_have_count(0)
    expect(page.get_by_text("Saved to list “groceries”.")).to_be_visible()
    wait_for_field(page, "Another loose end", "list", "groceries")
    # Done: the due counts on the Inbox and on My tasks go.
    card(page, "Loose end").locator("input[type=checkbox]").check()
    expect(inbox.locator(".module-due")).to_be_hidden(timeout=10000)
    expect(mine.locator(".module-due")).to_be_hidden()
    # ?list=none, its name until the Inbox, is the same view; the name is no list's.
    page.goto("/tasks?list=none")
    expect(page.locator("h1")).to_have_text("Inbox")
    expect(page.locator('.tasks-place[data-place="inbox"]')).to_have_attribute("aria-current", "page")
    for name in ("inbox", "Inbox", "none"):
        resp = page.request.post("/api/tasks/lists", data={"name": name})
        assert resp.status == 400, (name, resp.status)


def test_tag_embed_shows_list_tasks(page, wiki):
    # A tag view shows list tasks like any other.
    make_list(page, "groceries")
    page.request.post("/api/tasks", data={"title": "Oat milk", "list": "groceries", "tags": "sam"})
    wiki.write("sam", "# Sam\n\n[[tasks: tag=sam]]\n")
    page.goto("/page/sam?view=open&group=none")
    expect(card(page, "Oat milk")).to_be_visible()


def test_tag_embed_filter_is_locked_and_says_when_a_task_leaves(page, wiki):
    # tag= chips are locked like a list's, but a task's tags are not: taking
    # #sam off a task on Sam's page is a normal edit, and it says so.
    page.request.post("/api/tasks", data={"title": "Review PR", "tags": "task,sam"})
    wiki.write("sam", "# Sam\n\n[[tasks: tag=sam]]\n")
    page.goto("/page/sam?view=open&group=none")
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
    page.goto("/tasks?tag=task&view=open&group=none")
    filt = page.locator(".tasks-toolbar .tag-chip")
    expect(filt.locator(".tag-chip-label")).to_have_text(["#task"])
    expect(filt.locator(".tag-chip-x")).to_have_count(1)


def test_undo_a_move_to_another_list(page, wiki):
    # Undo restores the whole save, not just the list, and leaves no trace.
    for name in ("groceries", "hardware"):
        make_list(page, name)
    page.request.post("/api/tasks", data={"title": "Screws", "list": "groceries",
                                         "due_date": "2099-01-10"})
    wiki.write("shop", "# Shop\n\n[[tasks: list=groceries]]\n")
    page.goto("/page/shop?view=open&group=none")
    modal = open_edit(page, "Screws")
    modal.locator("#task-edit-more-lead").click()
    modal.locator("#task-edit-lead").fill("3")       # a start date too (logged)
    modal.locator("#task-edit-list").select_option("hardware")
    modal.get_by_role("button", name="Save").click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("Saved to list “hardware”.")
    expect(card(page, "Screws")).to_have_count(0)
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Screws")).to_be_visible()
    wait_for_field(page, "Screws", "list", "groceries")
    t = task_by_title(page, "Screws")
    assert t["start_date"] is None
    assert [e["event"] for e in t["history"]] == ["created"]


def test_undo_removing_the_tag_on_a_tag_page(page, wiki):
    page.request.post("/api/tasks", data={"title": "Review PR", "tags": "task,sam"})
    wiki.write("sam", "# Sam\n\n[[tasks: tag=sam]]\n")
    page.goto("/page/sam?view=open&group=none")
    modal = open_edit(page, "Review PR")
    modal.locator("#task-edit-tags").click()
    page.keyboard.press("Backspace")      # drops #sam
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Review PR")).to_have_count(0)
    page.locator(".task-undo-btn").click()
    expect(card(page, "Review PR")).to_be_visible()
    wait_for_field(page, "Review PR", "tags", ["task", "sam"])


def test_undo_a_bulk_untag_on_a_tag_page(page, wiki):
    # Bulk tag (T) taking tasks out of a tag's view: one toast, one undo for all.
    for title in ("Nails", "Glue"):
        page.request.post("/api/tasks", data={"title": title, "tags": "misc,hardware"})
    wiki.write("misc", "# Misc\n\n[[tasks: tag=misc]]\n")
    page.goto("/page/misc?view=open&group=none")
    card(page, "Glue").locator(".task-title").click()     # newest first: Glue, Nails
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("Shift+T")
    page.locator("#bulk-tag-input").fill("misc")
    page.keyboard.press("Enter")
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks no longer show here.")
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
    page.request.post("/api/tasks", data={"title": "Bolts", "tags": "x,y"})
    page.request.post("/api/tasks", data={"title": "Nuts", "tags": "x"})
    page.goto("/tasks?view=open&group=none")
    select_by_keys(page, "Nuts", 2)                   # newest first: Nuts, Bolts
    page.keyboard.press("t")
    dlg = page.locator(".bulk-tag-modal")
    expect(dlg.locator(".modal-label")).to_have_text("Tags of 2 tasks")
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
    page.request.post("/api/tasks", data={"title": "Bolts", "tags": "work"})
    page.request.post("/api/tasks", data={"title": "Nuts"})
    page.goto("/tasks?view=open&group=none")
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
    page.request.post("/api/tasks", data={"title": "Bolts", "tags": "work"})
    page.goto("/tasks?view=open&group=none")
    card(page, "Bolts").locator(".task-title").click()
    page.keyboard.press("t")
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "batch-tag" in r.url else None)
    page.locator(".bulk-tag-modal").get_by_role("button", name="Apply").click()
    expect(page.locator(".bulk-tag-modal")).to_have_count(0)
    page.wait_for_timeout(200)
    assert requests == []
    expect(page.locator(".task-undo-toast")).to_have_count(0)


def test_a_reload_keeps_the_tab(page, wiki):
    # A link opens its tab; F5 after going elsewhere stays there.
    task = page.request.post("/api/tasks", data={"title": "Linked", "start_date": date.today().isoformat()}).json()
    active = page.locator("#tasks-tabs .active")
    page.goto(f"/tasks?task={task['id']}&view=open&group=none")
    expect(active).to_have_text(["Open"])
    expect(card(page, "Linked")).to_have_class(re.compile(r"\btask-selected\b"))
    page.reload()                                     # nothing chosen yet: still the link's tab
    expect(active).to_have_text(["Open"])
    open_tab(page, "focus")
    page.reload()
    expect(active).to_have_text(["Today"])
    expect(card(page, "Linked")).to_be_visible()
    expect(card(page, "Linked")).not_to_have_class(re.compile(r"\btask-selected\b"))  # the link's task isn't picked again
    page.reload()
    expect(active).to_have_text(["Today"])
    # Opening the link again follows it.
    page.goto(f"/tasks?task={task['id']}&view=open&group=none")
    expect(active).to_have_text(["Open"])
    # An embed that names its tab: the same. Opened, its tab; reloaded, the one shown.
    wiki.write("board", "# Board\n\n[[tasks: view=open group=none]]\n")
    page.goto("/page/board")
    expect(active).to_have_text(["Open"])
    open_tab(page, "focus")
    page.reload()
    expect(active).to_have_text(["Today"])
    page.goto("/page/board")
    expect(active).to_have_text(["Open"])


def open_list_settings(page):
    """List settings…, the first item of the toolbar's ⋯ menu."""
    more_menu(page)
    page.get_by_role("menuitem", name="List settings…").click()


def seed_four(page):
    # Open shows them newest first: Saw, Glue, Nuts, Bolts.
    for title in ("Bolts", "Nuts", "Glue", "Saw"):
        page.request.post("/api/tasks", data={"title": title})
    page.goto("/tasks?view=open&group=none")


def selected_titles(page):
    return page.locator(".task-item.task-selected .task-title").all_inner_texts()


def test_shift_and_ctrl_click_select(page, wiki):
    seed_four(page)
    bar = page.locator("#task-selbar")
    card(page, "Saw").locator(".task-title").click()
    expect(bar).to_be_hidden()                          # one task: no bar
    card(page, "Nuts").locator(".task-title").click(modifiers=["Shift"])
    assert selected_titles(page) == ["Saw", "Glue", "Nuts"]
    expect(bar.locator(".tasks-selbar-count")).to_have_text("3 selected")
    card(page, "Glue").locator(".task-title").click(modifiers=["Control"])
    assert selected_titles(page) == ["Saw", "Nuts"]
    card(page, "Bolts").locator(".task-title").click(modifiers=["Control"])
    expect(bar.locator(".tasks-selbar-count")).to_have_text("3 selected")
    # Shift+click extends from the last Ctrl+clicked card.
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    assert selected_titles(page) == ["Glue", "Nuts", "Bolts"]
    # A plain click selects just that one again.
    card(page, "Saw").locator(".task-title").click()
    assert selected_titles(page) == ["Saw"]
    expect(bar).to_be_hidden()


def test_ctrl_a_selects_what_the_filter_shows(page, wiki):
    for title, tags in (("Bolts", "hw"), ("Nuts", "hw"), ("Glue", "")):
        page.request.post("/api/tasks", data={"title": title, "tags": tags})
    page.goto("/tasks?view=open&group=none&tag=hw")
    card(page, "Nuts").locator(".task-title").click()
    page.keyboard.press("Control+a")
    assert sorted(selected_titles(page)) == ["Bolts", "Nuts"]
    bar = page.locator("#task-selbar")
    expect(bar.locator(".tasks-selbar-count")).to_have_text("2 selected")
    expect(bar.locator("[data-sel='all']")).to_be_disabled()
    page.keyboard.press("Escape")
    expect(bar).to_be_hidden()
    assert selected_titles(page) == []


def test_selection_bar_tag_opens_the_dialog(page, wiki):
    seed_four(page)
    card(page, "Saw").locator(".task-title").click()
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    page.locator("#task-selbar [data-sel='tag']").click()
    expect(page.locator(".bulk-tag-modal .modal-label")).to_have_text("Tags of 2 tasks")
    page.locator("#bulk-tag-input").fill("shop")
    page.keyboard.press("Enter")
    wait_for_field(page, "Saw", "tags", ["shop"])
    wait_for_field(page, "Glue", "tags", ["shop"])
    assert task_by_title(page, "Nuts")["tags"] == []
    # The selection stays for the next action.
    assert selected_titles(page) == ["Saw", "Glue"]


def test_selection_bar_moves_tasks_to_a_list(page, wiki):
    seed_four(page)
    for name in ("shop", "work"):
        make_list(page, name)
    for title, fields in (("Saw", {"list": "Shop", "tags": ["tools"]}), ("Glue", {"list": "work"})):
        page.request.put(f"/api/tasks/{task_by_title(page, title)['id']}", data=fields)
    page.reload()
    card(page, "Saw").locator(".task-title").click()
    for title in ("Glue", "Nuts"):          # by name: tasks made in one second have no order across lists
        card(page, title).locator(".task-title").click(modifiers=["Control"])
    page.locator("#task-selbar [data-sel='list']").click()
    dlg = page.locator(".bulk-list-modal")
    expect(dlg.locator(".modal-label")).to_have_text("Move 3 tasks to a list")
    rows = dlg.locator(".bulk-list-row")
    expect(rows).to_have_text(["Inbox1 of 3", "shop1 of 3", "work1 of 3", "+New list…"])
    dlg.get_by_role("button", name="work").click()
    expect(dlg).to_have_count(0)
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks moved to list “work”")      # Glue was there already
    # Their list changes, their tags stay; the selection is kept.
    wait_for_field(page, "Saw", "list", "work")
    wait_for_field(page, "Nuts", "list", "work")
    assert task_by_title(page, "Saw")["tags"] == ["tools"] and task_by_title(page, "Glue")["list"] == "work"
    assert "list" not in task_by_title(page, "Bolts")
    assert sorted(selected_titles(page)) == ["Glue", "Nuts", "Saw"]
    # Now all three are in work: its row is ticked.
    page.locator("#task-selbar [data-sel='list']").click()
    expect(dlg.locator(".bulk-list-row[aria-current='true']")).to_have_text("✓work")
    expect(dlg.locator(".bulk-list-row[aria-current='true']")).to_be_focused()
    page.keyboard.press("Escape")
    # One undo puts every task back in its list.
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Saw", "list", "shop")
    wait_for_field(page, "Nuts", "list", None)
    assert task_by_title(page, "Glue")["list"] == "work"
    expect(card(page, "Nuts").locator(".task-tag-list")).to_have_count(0)   # the page has them again: the move counts what it holds
    # The Inbox: out of every list. The menu of a selected card offers the move too.
    card(page, "Glue").locator(".task-title").click(button="right")
    page.locator(".task-menu").get_by_role("menuitem", name="Move to list…").click()
    expect(dlg.locator(".bulk-list-row", has_text="Inbox")).to_be_enabled()      # no users: the Inbox is no one's alone
    dlg.get_by_role("button", name="Inbox").click()
    expect(toast).to_contain_text("2 tasks moved to the Inbox")
    wait_for_field(page, "Saw", "list", None)
    wait_for_field(page, "Glue", "list", None)
    assert task_by_title(page, "Saw")["tags"] == ["tools"]
    # One task alone: Move to list… is in its card's menu too.
    page.keyboard.press("Escape")
    card(page, "Bolts").locator(".task-title").click(button="right")
    page.locator(".task-menu").get_by_role("menuitem", name="Move to list…").click()
    expect(dlg.locator(".modal-label")).to_have_text("Move 1 task to a list")
    dlg.get_by_role("button", name="shop").click()
    wait_for_field(page, "Bolts", "list", "shop")


def test_the_move_dialog_makes_a_new_list(page, wiki):
    # No list yet: the dialog still opens, with the Inbox (where they are) and New list….
    seed_four(page)
    card(page, "Saw").locator(".task-title").click()
    card(page, "Glue").locator(".task-title").click(modifiers=["Shift"])
    page.locator("#task-selbar [data-sel='list']").click()
    dlg = page.locator(".bulk-list-modal")
    expect(dlg.locator(".bulk-list-row")).to_have_text(["✓Inbox", "+New list…"])
    dlg.get_by_role("button", name="New list…").click()
    expect(dlg.locator(".modal-label")).to_have_text("Move 2 tasks to a new list")
    expect(dlg.locator(".bulk-tag-list")).to_be_hidden()
    name = dlg.locator("#bulk-list-name")
    expect(name).to_be_focused()
    expect(dlg.locator("#bulk-list-private")).to_have_count(0)        # no users: no private lists
    # A name that won't do: the dialog stays and says why.
    name.fill("a,b")
    name.press("Enter")
    expect(dlg.locator(".rename-error")).to_be_visible()
    # Back shows the rows again; then the list is made and the tasks move.
    dlg.get_by_role("button", name="Back").click()
    expect(dlg.locator(".bulk-tag-list")).to_be_visible()
    expect(dlg.locator(".bulk-list-form")).to_be_hidden()
    dlg.get_by_role("button", name="New list…").click()
    expect(dlg.locator(".rename-error")).to_be_hidden()
    name.fill("Shop")
    name.press("Enter")
    expect(dlg).to_have_count(0)
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks moved to the new list “shop”")
    wait_for_field(page, "Saw", "list", "shop")
    wait_for_field(page, "Glue", "list", "shop")
    expect(card(page, "Saw").locator(".task-tag-list")).to_have_text("shop")   # the page knows the list
    assert selected_titles(page) == ["Saw", "Glue"]
    # The row of lists is there without a reload (the wiki had no list, and so no row):
    # a list made anywhere is pinned, a pill after the Inbox.
    expect(page.locator(".tasks-places .tasks-place .tasks-place-label")).to_have_text(["My tasks", "Inbox", "shop", "All tasks"])
    expect(page.locator("a.tasks-place[data-place='list:shop'] .tasks-place-pin")).to_have_count(1)
    expect(page.locator(".lists-more-item[data-place='list:shop']")).to_have_count(0)
    expect(page.locator(".lists-more-item .lists-more-new")).to_have_count(0)
    # It is a row now, ticked; a second list of that name is refused.
    page.keyboard.press("m")
    expect(dlg.locator(".bulk-list-row")).to_have_text(["Inbox", "✓shop", "+New list…"])
    dlg.get_by_role("button", name="New list…").click()
    name.fill("shop")
    name.press("Enter")
    expect(dlg.locator(".rename-error")).to_have_text("There is a list #shop already")
    page.keyboard.press("Escape")
    # Undo takes the tasks back out; the list stays.
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Saw", "list", None)
    wait_for_field(page, "Glue", "list", None)
    page.keyboard.press("Escape")
    open_edit(page, "Bolts")
    expect(page.locator("#task-edit-list option")).to_have_text(["Inbox", "shop", "New list…"])


def test_a_list_made_in_the_move_dialog_is_a_pill_beside_the_pinned(page, wiki):
    make_list(page, "work", pin=True)
    page.request.post("/api/tasks", data={"title": "Bolts"})
    page.goto("/tasks?view=open&group=none")
    card(page, "Bolts").locator(".task-title").click(button="right")
    page.locator(".task-menu").get_by_role("menuitem", name="Move to list…").click()
    dlg = page.locator(".bulk-list-modal")
    dlg.get_by_role("button", name="New list…").click()
    dlg.locator("#bulk-list-name").fill("home")
    dlg.get_by_role("button", name="Create and move").click()
    wait_for_field(page, "Bolts", "list", "home")
    pills = page.locator("a.tasks-place[data-place^='list:']")
    expect(pills).to_have_count(2)
    assert pills.evaluate_all("els => els.map(a => a.dataset.place)") == ["list:work", "list:home"]


def test_moving_tasks_out_of_the_list_shown(page, wiki):
    for name in ("shop", "work"):
        make_list(page, name)
    for title in ("Bolts", "Nuts", "Glue"):
        page.request.post("/api/tasks", data={"title": title, "list": "shop"})
    page.goto("/tasks?list=shop&view=open&group=none")
    card(page, "Glue").locator(".task-title").click()
    card(page, "Nuts").locator(".task-title").click(modifiers=["Shift"])
    page.keyboard.press("m")                                         # as the selection bar's List…
    expect(page.locator(".bulk-list-modal .modal-label")).to_have_text("Move 2 tasks to a list")
    page.locator(".bulk-list-modal").get_by_role("button", name="work").click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks moved to list “work”")
    expect(card(page, "Glue")).to_have_count(0)
    expect(card(page, "Nuts")).to_have_count(0)
    expect(card(page, "Bolts")).to_be_visible()
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Glue")).to_be_visible()
    expect(card(page, "Nuts")).to_be_visible()


def test_selection_bar_today_with_undo(page, wiki):
    seed_four(page)
    later = (date.today() + timedelta(days=5)).isoformat()
    page.request.put(f"/api/tasks/{task_by_title(page, 'Glue')['id']}", data={"start_date": later})
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


def test_selection_bar_unplan_with_undo(page, wiki):
    seed_four(page)
    later = (date.today() + timedelta(days=5)).isoformat()
    today = date.today().isoformat()
    page.request.put(f"/api/tasks/{task_by_title(page, 'Glue')['id']}", data={"start_date": later})
    page.request.put(f"/api/tasks/{task_by_title(page, 'Nuts')['id']}", data={"start_date": today})
    page.reload()
    unplan = page.locator("#task-selbar [data-sel='unplan']")
    # Nothing planned among the selected: nothing to unplan.
    card(page, "Saw").locator(".task-title").click()
    card(page, "Bolts").locator(".task-title").click(modifiers=["Control"])
    expect(page.locator("#task-selbar")).to_be_visible()
    expect(unplan).to_be_hidden()
    card(page, "Saw").locator(".task-title").click()
    card(page, "Nuts").locator(".task-title").click(modifiers=["Shift"])
    expect(unplan).to_be_visible()
    unplan.click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks unplanned")
    wait_for_field(page, "Glue", "start_date", None)
    wait_for_field(page, "Nuts", "start_date", None)
    assert selected_titles(page) == ["Saw", "Glue", "Nuts"]
    expect(unplan).to_be_hidden()
    toast.locator(".task-undo-btn").click()
    wait_for_field(page, "Glue", "start_date", later)
    wait_for_field(page, "Nuts", "start_date", today)
    expect(unplan).to_be_visible()   # the page has loaded them again: its menu goes by what it holds
    # The menu of a selected card offers it too.
    card(page, "Glue").locator(".task-title").click(button="right")
    page.locator(".task-menu").get_by_role("menuitem", name="Unplan").click()
    wait_for_field(page, "Glue", "start_date", None)
    wait_for_field(page, "Nuts", "start_date", None)


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
    # A task is never deleted: the menu closes them undone.
    expect(menu.get_by_role("menuitem")).to_have_text(["Tag…", "Move to list…", "Start today", "Done", "Won't do"])
    menu.get_by_role("menuitem", name="Won't do").click()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("2 tasks marked won't do")
    expect(card(page, "Saw")).to_have_count(0)
    expect(card(page, "Glue")).to_have_count(0)
    expect(page.locator("#task-selbar")).to_be_hidden()
    wait_until(page, lambda: sorted(t["title"] for t in page.request.get("/api/tasks/closed").json()
                                    if t["wont_do"]) == ["Glue", "Saw"])
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Saw")).to_be_visible()
    expect(card(page, "Glue")).to_be_visible()
    assert page.request.get("/api/tasks/closed").json() == []
    # The ⋯ button of a card not in a multi-selection is that card's own menu.
    card(page, "Nuts").locator(".task-title").click()
    card(page, "Bolts").locator(".task-menu-btn").click()
    expect(page.locator(".task-menu .task-menu-title")).to_have_text("Bolts")


def closed_titles(page):
    return sorted(t["title"] for t in page.request.get("/api/tasks/closed").json() if t["wont_do"])


def test_a_task_is_closed_undone_never_deleted(page, wiki):
    seed_four(page)
    # Nothing offers Delete: not the menu, the selection bar or the edit modal.
    card(page, "Bolts").locator(".task-menu-btn").click()
    assert "Delete…" not in page.locator(".task-menu").get_by_role("menuitem").all_inner_texts()
    page.keyboard.press("Escape")
    expect(page.locator("#task-selbar [data-sel='delete']")).to_have_count(0)
    expect(page.locator("#task-delete-modal")).to_have_count(0)
    # The Delete key closes the selected task undone, with an undo.
    card(page, "Saw").locator(".task-title").click()
    page.keyboard.press("Delete")
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("Marked won't do")
    expect(card(page, "Saw")).to_have_count(0)
    assert selected_titles(page) == ["Glue"]              # the selection moved on
    wait_until(page, lambda: closed_titles(page) == ["Saw"])
    toast.locator(".task-undo-btn").click()
    expect(card(page, "Saw")).to_be_visible()
    wait_until(page, lambda: closed_titles(page) == [])
    # Several: the selection bar's Won't do, one undo for all.
    card(page, "Glue").locator(".task-title").click()
    card(page, "Nuts").locator(".task-title").click(modifiers=["Shift"])
    page.locator("#task-selbar [data-sel='wontdo']").click()
    expect(toast).to_contain_text("2 tasks marked won't do")
    wait_until(page, lambda: closed_titles(page) == ["Glue", "Nuts"])
    # The edit modal's button, for an open task.
    modal = open_edit(page, "Bolts")
    modal.get_by_role("button", name="Won't do").click()
    expect(modal).to_be_hidden()
    wait_until(page, lambda: closed_titles(page) == ["Bolts", "Glue", "Nuts"])
    # In Closed they are still there, and can't be closed again or deleted.
    open_tab(page, "closed")
    expect(card(page, "Bolts")).to_be_visible()
    card(page, "Bolts").locator(".task-title").click()
    page.keyboard.press("Delete")
    expect(card(page, "Bolts")).to_be_visible()
    modal = open_edit(page, "Bolts")
    expect(modal.get_by_role("button", name="Won't do")).to_be_hidden()
    assert closed_titles(page) == ["Bolts", "Glue", "Nuts"]


def test_ctrl_delete_is_the_pages_not_the_selected_tasks(page, wiki):
    # Delete closes the selected tasks as won't do; Ctrl+Delete is the
    # page's Delete page, and only that: it once did both.
    page.request.post("/api/tasks", data={"title": "Keep me"})
    wiki.write("tasks", "# Tasks\n\n[[tasks: view=open group=none]]\n")
    page.goto("/page/tasks")
    card(page, "Keep me").locator(".task-title").click()
    assert selected_titles(page) == ["Keep me"]
    page.keyboard.press("Control+Delete")
    dialog = page.locator("#page-delete-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    settle(page)
    expect(card(page, "Keep me")).to_be_visible()
    expect(page.get_by_text("Marked won't do")).to_have_count(0)
    assert closed_titles(page) == []


def test_main_task_counts_list_tasks_as_completed(page, wiki):
    page.request.post("/api/tasks", data={"title": "Oat milk", "tags": "groceries"})
    done = page.request.post("/api/tasks", data={"title": "Bread", "tags": "groceries"}).json()
    page.request.put(f"/api/tasks/{done['id']}", data={"completed": True})
    wiki.write("shop", "# Shop\n\n[[tasks: list=groceries]]\n")
    page.goto("/tasks?view=open&group=none")
    expect(card(page, "Oat milk")).to_be_visible()
    expect(page.locator("#task-closed-link")).to_have_text("Closed (1) →")
    expect(page.locator("#task-show-lists")).to_have_count(0)


def test_new_task_in_a_list_embed_goes_into_the_list(page, wiki):
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=groceries]]\n")
    page.goto("/page/shop?view=open&group=none")
    # The filter says which list this is: a chip of the list's, with no ×.
    filt = page.locator(".tasks-toolbar .tag-chip")
    expect(filt).to_have_text(["groceries"])
    expect(filt).to_have_class(re.compile(r"\btag-chip-list\b"))
    expect(filt.locator(".tag-chip-x")).to_have_count(0)
    page.locator("#task-filter-search").click()
    page.keyboard.press("Backspace")      # a locked chip stays
    expect(filt).to_have_text(["groceries"])
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    # The task starts in the list: the List field says so, and it has no tag for it.
    expect(modal.locator("#task-edit-list")).to_have_value("groceries")
    expect(modal.locator(".task-edit-target")).to_have_text("Adds to the list groceries.")
    expect(modal.locator(".tag-chip")).to_have_count(0)
    modal.locator("#task-edit-title").fill("Bread")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Bread")).to_be_visible()
    wait_for_field(page, "Bread", "list", "groceries")
    assert task_by_title(page, "Bread")["tags"] == []


def test_a_cards_list_comes_first_and_marked(page, wiki):
    make_list(page, "groceries")
    page.request.post("/api/tasks", data={"title": "Oat milk", "list": "groceries", "tags": "dairy"})
    page.goto("/tasks?view=open&group=none")
    tags = card(page, "Oat milk").locator(".task-tag")
    expect(tags).to_have_text(["groceries", "#dairy"])
    expect(tags.first).to_have_class(re.compile(r"\btask-tag-list\b"))
    # Icon, tint and a tooltip on the list's chip; a tag is plain.
    el, plain = tags.first, tags.nth(1)
    assert "svg" in el.evaluate("e => getComputedStyle(e, '::before').maskImage")
    assert plain.evaluate("e => getComputedStyle(e, '::before').content") in ("none", "normal")
    bg = "e => getComputedStyle(e).backgroundColor"
    assert el.evaluate(bg) != plain.evaluate(bg)
    assert el.get_attribute("title") == "List"
    assert not plain.get_attribute("title")
    # The dialog's Tags are its tags alone: the list has its own field.
    modal = open_edit(page, "Oat milk")
    chips = modal.locator(".tag-chip")
    expect(chips.locator(".tag-chip-label")).to_have_text(["#dairy"])
    expect(chips.first).not_to_have_class(re.compile(r"\btag-chip-list\b"))
    expect(modal.locator("#task-edit-list")).to_have_value("groceries")
    page.keyboard.press("Escape")
    # In a view of that one list the cards don't repeat it.
    wiki.write("shop", "# Shop\n\n[[tasks: list=groceries]]\n")
    page.goto("/page/shop?view=open&group=none")
    expect(card(page, "Oat milk").locator(".task-tag")).to_have_text(["#dairy"])


def test_filter_marks_the_list_and_takes_list_words(page, wiki):
    for name in ("groceries", "hardware"):
        make_list(page, name)
    page.request.post("/api/tasks", data={"title": "Screws", "list": "groceries", "tags": "weekly,dairy"})
    wiki.write("shop", "# Shop\n\n[[tasks: list=groceries tag=weekly]]\n")
    page.goto("/page/shop?view=open&group=none")
    filt = page.locator("#task-filter-search")
    filt.click()
    page.keyboard.type("#dai")                   # chips come from completion
    page.keyboard.press("Enter")
    chips = page.locator(".tasks-toolbar .tag-chip")
    expect(chips.locator(".tag-chip-label")).to_have_text(["groceries", "#weekly", "#dairy"])
    bgs = chips.evaluate_all("cs => cs.map(c => getComputedStyle(c).backgroundColor)")
    assert bgs[1] == bgs[2] and bgs[0] != bgs[1]   # the list tinted only
    titles = chips.evaluate_all("cs => cs.map(c => c.title)")
    assert titles == ["List", "Set by this page", ""]
    expect(card(page, "Screws")).to_be_visible()
    # A tag named as a list finds nothing of the list; list: does, on any view.
    page.goto("/tasks?view=open&group=none")
    filt.fill("#groceries")
    expect(card(page, "Screws")).to_have_count(0)
    filt.fill("list:groceries")
    expect(card(page, "Screws")).to_be_visible()
    filt.fill("list:hardware")
    expect(card(page, "Screws")).to_have_count(0)
    filt.fill("-list:hardware #weekly")
    expect(card(page, "Screws")).to_be_visible()


def test_task_moves_to_another_list(page, wiki):
    for name in ("groceries", "hardware"):
        make_list(page, name)
    page.request.post("/api/tasks", data={"title": "Screws", "list": "groceries"})
    wiki.write("shop", "# Shop\n\n[[tasks: list=groceries]]\n")
    wiki.write("diy", "# DIY\n\n[[tasks: list=hardware]]\n")
    page.goto("/page/shop?view=open&group=none")
    modal = open_edit(page, "Screws")
    modal.locator("#task-edit-list").select_option("hardware")
    modal.get_by_role("button", name="Save").click()
    expect(card(page, "Screws")).to_have_count(0)
    expect(page.get_by_text("Saved to list “hardware”.")).to_be_visible()
    wait_for_field(page, "Screws", "list", "hardware")
    page.goto("/page/diy?view=open&group=none")
    expect(card(page, "Screws")).to_be_visible()


def _focus_titles(page):
    return page.locator(".task-item .task-title")


def _by_order(page, field):
    tasks = [t for t in page.request.get("/api/tasks").json() if t[field] is not None]
    return [t["title"] for t in sorted(tasks, key=lambda t: t[field])]


def test_focus_is_open_filtered(page, wiki):
    today = date.today().isoformat()
    later = (date.today() + timedelta(days=3)).isoformat()
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Later", later), ("Bravo", today), ("Charlie", today))]
    page.request.post("/api/tasks/reorder", data={"order": list(reversed(ids))})
    page.request.post("/api/tasks", data={"title": "Unordered", "start_date": today})
    page.goto("/tasks?view=today")
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
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Apples", today), ("Bread", None), ("Cheese", today),
                        ("Dates", None), ("Eggs", today))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=today")
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Butter")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(_focus_titles(page)).to_have_text(["Butter", "Apples", "Cheese", "Eggs"])
    assert task_by_title(page, "Butter")["start_date"] == today

    # Onto Apples' lower half: after it, so right after Apples in the shop,
    # before Bread, which Focus doesn't show.
    _drop(card(page, "Butter"), card(page, "Apples"), "after")
    expect(_focus_titles(page)).to_have_text(["Apples", "Butter", "Cheese", "Eggs"])
    wait_until(page, lambda: _by_order(page, "sort_order") ==
               ["Apples", "Butter", "Bread", "Cheese", "Dates", "Eggs"])

    # Moving to the top: Apples' upper half, right before it; hidden ones stay put.
    _drop(card(page, "Eggs"), card(page, "Apples"), "before")
    expect(_focus_titles(page)).to_have_text(["Eggs", "Apples", "Butter", "Cheese"])
    wait_until(page, lambda: _by_order(page, "sort_order") ==
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
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Elsewhere", tomorrow()), ("Bravo", today), ("Charlie", today))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.request.post("/api/tasks", data={"title": "Newbie", "start_date": today})
    page.goto("/tasks?view=open&group=day")
    expect(card(page, "Charlie")).to_be_visible()
    assert _day_titles(page, today) == ["Newbie", "Alpha", "Bravo", "Charlie"]

    _drop(card(page, "Charlie"), card(page, "Alpha"), "before")
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Charlie", "Alpha", "Elsewhere", "Bravo"])
    assert _day_titles(page, today) == ["Newbie", "Charlie", "Alpha", "Bravo"]
    assert task_by_title(page, "Newbie")["sort_order"] is None   # above it: stays Unsorted

    # As on Open: dropped among the Unsorted tasks, a task leaves the order
    # (Unsorted is newest first), and no other task is ranked by it.
    _drop(card(page, "Bravo"), card(page, "Newbie"), "before")
    wait_until(page, lambda: task_by_title(page, "Bravo")["sort_order"] is None)
    assert _by_order(page, "sort_order") == ["Charlie", "Alpha", "Elsewhere"]
    assert task_by_title(page, "Newbie")["sort_order"] is None
    assert _day_titles(page, today) == ["Newbie", "Bravo", "Charlie", "Alpha"]
    # Dropped on the Sorted label: first among the sorted ones.
    card(page, "Newbie").drag_to(page.locator(f'.board-day-divider[data-drop-day="{today}"] ~ .plan-subdivider[data-sub="sorted"]').first)
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Newbie", "Charlie", "Alpha", "Elsewhere"])
    assert _day_titles(page, today) == ["Bravo", "Newbie", "Charlie", "Alpha"]


def test_board_drop_on_a_card_of_another_day_starts_and_places_it(page, wiki):
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Bravo", today), ("Mover", tomorrow()))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=open&group=day")
    _drop(card(page, "Mover"), card(page, "Bravo"), "before")
    wait_for_field(page, "Mover", "start_date", today)
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Alpha", "Mover", "Bravo"])
    assert _day_titles(page, today) == ["Alpha", "Mover", "Bravo"]


def test_board_keyboard_reorders_within_a_day_only(page, wiki):
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": s}).json()["id"]
           for t, s in (("Alpha", today), ("Bravo", today), ("Next", tomorrow()))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=open&group=day")
    card(page, "Bravo").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Bravo", "Alpha", "Next"])
    # Last of its day: it doesn't leave it downwards.
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)
    card(page, "Alpha").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    page.wait_for_timeout(300)
    assert writes == []
    assert task_by_title(page, "Alpha")["start_date"] == today
    # First of its day, up: into Unsorted, as on Open, still on its day.
    card(page, "Bravo").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_until(page, lambda: task_by_title(page, "Bravo")["sort_order"] is None)
    assert task_by_title(page, "Bravo")["start_date"] == today
    assert _day_titles(page, today) == ["Bravo", "Alpha"]


def _unplanned_rows(page):
    """Unplanned's labels ('# Unsorted') and card titles, top to bottom."""
    return page.evaluate("""() => {
        const out = [];
        let li = document.querySelector('.board-day-divider[data-drop-bucket="unplanned"]')?.nextElementSibling;
        for (; li && !li.classList.contains('board-day-divider'); li = li.nextElementSibling)
          out.push(li.classList.contains('task-item') ? li.querySelector('.task-title').textContent.trim()
                   : '# ' + li.firstElementChild.textContent.trim());
        return out;
    }""")


def test_board_labels_unsorted_in_every_section_that_has_some(page, wiki):
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    ranked = page.request.post("/api/tasks", data={"title": "Ranked", "start_date": today}).json()["id"]
    page.request.post("/api/tasks/reorder", data={"order": [ranked]})
    page.request.post("/api/tasks", data={"title": "Added today", "start_date": today})
    other = page.request.post("/api/tasks", data={"title": "Tomorrow ranked", "start_date": tomorrow}).json()["id"]
    page.request.post("/api/tasks/reorder", data={"order": [ranked, other]})
    page.set_viewport_size({"width": 1280, "height": 1400})
    page.goto("/tasks?view=open&group=day")
    expect(card(page, "Added today")).to_be_visible()
    rows = lambda day: page.evaluate("""day => {
        const out = [];
        let li = document.querySelector(`.board-day-divider[data-drop-day="${day}"]`)?.nextElementSibling;
        for (; li && !li.classList.contains('board-day-divider'); li = li.nextElementSibling)
          out.push(li.classList.contains('task-item') ? li.querySelector('.task-title').textContent.trim()
                   : '# ' + li.firstElementChild.textContent.trim());
        return out;
    }""", day)
    assert rows(today) == ["# Unsorted", "Added today", "# Sorted", "Ranked"]
    # Nothing unsorted: no labels.
    assert rows(tomorrow) == ["Tomorrow ranked"]


def test_board_unplanned_shows_unsorted_apart_and_reorders(page, wiki):
    page.set_viewport_size({"width": 1280, "height": 1400})
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.request.post("/api/tasks", data={"title": "Fresh"})
    page.goto("/tasks?view=open&group=day")
    expect(card(page, "Fresh")).to_be_visible()
    assert _unplanned_rows(page) == ["# Unsorted", "Fresh", "# Sorted", "Alpha", "Bravo"]
    # Unsorted cards are hollow, as on Open and Focus; Sorted ones aren't.
    expect(card(page, "Fresh")).to_have_class(re.compile(r"\btask-inbox\b"))
    expect(card(page, "Alpha")).not_to_have_class(re.compile(r"\btask-inbox\b"))
    # A drop within Sorted reorders it.
    _drop(card(page, "Bravo"), card(page, "Alpha"), "before")
    wait_until(page, lambda: _unplanned_rows(page) == ["# Unsorted", "Fresh", "# Sorted", "Bravo", "Alpha"])
    # An Unsorted task dropped among the sorted ones is sorted there; no
    # Unsorted left, no labels.
    _drop(card(page, "Fresh"), card(page, "Bravo"), "after")
    wait_until(page, lambda: _unplanned_rows(page) == ["Bravo", "Fresh", "Alpha"])
    page.reload()
    expect(card(page, "Fresh")).to_be_visible()
    assert _unplanned_rows(page) == ["Bravo", "Fresh", "Alpha"]
    expect(card(page, "Fresh")).not_to_have_class(re.compile(r"\btask-inbox\b"))
    # Sorted by deadline (or creation), no labels and no reordering.
    page.locator("select.tasks-sort").select_option("due")
    expect(page.locator(".plan-subdivider")).to_have_count(0)
    expect(page.locator('.board-day-divider[data-drop-bucket="unplanned"][data-ordered]')).to_have_count(0)


def test_board_drop_on_own_heading_changes_nothing_except_later(page, wiki):
    # A drop on the task's own section heading sends nothing (a heading
    # sets the day); Later's heading still asks for another date.
    page.set_viewport_size({"width": 1280, "height": 1400})
    today = date.today().isoformat()
    page.request.post("/api/tasks", data={"title": "Today task", "start_date": today})
    page.request.post("/api/tasks", data={"title": "Loose task"})
    far = (date.today() + timedelta(days=30)).isoformat()
    page.request.post("/api/tasks", data={"title": "Later task", "start_date": far})
    page.goto("/tasks?view=open&group=day")
    expect(card(page, "Later task")).to_be_visible()
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method != "GET" else None)

    card(page, "Today task").drag_to(page.locator(f".board-day-divider[data-drop-day='{today}']"))
    card(page, "Loose task").drag_to(page.locator(".board-day-divider[data-drop-bucket='unplanned']"))
    page.wait_for_timeout(300)
    assert writes == []
    expect(page.locator(".tasks-datepicker")).to_have_count(0)

    card(page, "Later task").drag_to(page.locator(".board-day-divider[data-drop-bucket='later']"))
    expect(page.locator(".tasks-datepicker")).to_be_visible()


def test_focus_follows_open_order(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=today")
    expect(_focus_titles(page)).to_have_text(["Alpha", "Bravo"])
    open_tab(page, "open")
    card(page, "Bravo").drag_to(card(page, "Alpha"))
    open_tab(page, "focus")
    expect(_focus_titles(page)).to_have_text(["Bravo", "Alpha"])


def test_focus_keyboard_crosses_the_sections(page, wiki):
    today = date.today().isoformat()
    page.request.post("/api/tasks", data={"title": "Deadline", "due_date": today})
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": t2}).json()["id"]
           for t, t2 in (("Bravo", today), ("Hidden", None), ("Alpha", today))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=today")
    # Unsorted (the due task) above Sorted.
    expect(_focus_titles(page)).to_have_text(["Deadline", "Bravo", "Alpha"])
    # Unsorted can't be reordered: ↑ does nothing, ↓ sorts the task in at the
    # top of Sorted.
    card(page, "Deadline").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    expect(_focus_titles(page)).to_have_text(["Deadline", "Bravo", "Alpha"])
    page.keyboard.press("Control+Shift+ArrowDown")
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Deadline", "Bravo", "Hidden", "Alpha"])
    expect(_section(page, "unsorted")).to_have_class(re.compile(r"\bboard-day--empty\b"))
    # ↓ within Sorted: after Alpha, past the hidden task.
    card(page, "Bravo").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    expect(_focus_titles(page)).to_have_text(["Deadline", "Alpha", "Bravo"])
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Deadline", "Hidden", "Alpha", "Bravo"])
    # ↑ on the first sorted task takes it back to Unsorted.
    card(page, "Deadline").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_for_field(page, "Deadline", "sort_order", None)


def test_focus_limit_is_off_unless_set(page, wiki):
    today = date.today().isoformat()
    for i in range(5):
        page.request.post("/api/tasks", data={"title": f"Task {i}", "start_date": today})
    # No limit by default: no Focus warning, no flagged day on Plan.
    page.goto("/tasks?view=today")
    expect(_focus_titles(page)).to_have_count(5)
    expect(page.locator(".today-over-limit")).to_have_count(0)
    open_tab(page, "plan")
    expect(page.locator(".board-day-divider", has_text="Today")).to_be_visible()
    expect(page.locator(".board-day--over")).to_have_count(0)
    # All tasks' limit turns it on (no ?limit= in the address); the count
    # includes Unsorted.
    page.goto("/tasks?view=today&limit=2")
    expect(page.locator(".today-over-limit")).to_have_count(0)
    assert page.request.post("/api/tasks/settings", data={"all": True, "was": "", "set": {"limit": "5"}}).ok
    page.goto("/tasks?view=today")
    expect(page.locator(".today-over-limit")).to_contain_text("5 tasks in Today (limit: 5)")
    open_tab(page, "plan")
    expect(page.locator(".board-day--over")).to_have_count(1)
    # An embed of every task has All tasks' limit; its own limit= is ignored.
    wiki.write("board", "# Board\n\n[[tasks: view=today limit=3]]\n")
    page.goto("/page/board")
    expect(page.locator(".today-over-limit")).to_contain_text("(limit: 5)")


def test_task_added_on_focus_lands_in_unsorted(page, wiki):
    today = date.today().isoformat()
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": today}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=today")
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Added now")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    expect(_focus_titles(page)).to_have_text(["Added now", "Alpha", "Bravo"])
    page.reload()
    assert _open_titles(page, "unsorted") == ["Added now"]


def test_new_task_is_selected_and_scrolled_into_view(page, wiki):
    today = date.today().isoformat()
    for i in range(30):
        page.request.post("/api/tasks", data={"title": f"Filler {i}", "start_date": today})
    page.set_viewport_size({"width": 1000, "height": 600})
    page.goto("/tasks?view=today")
    expect(_focus_titles(page)).to_have_count(30)
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Added now")
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()
    new = card(page, "Added now")
    expect(new).to_have_class(re.compile(r"\btask-selected\b"))
    expect(new).to_be_in_viewport()
    expect(page.locator(".task-item.task-selected")).to_have_count(1)


def _open_titles(page, zone):
    """Titles of the tasks in one Open section, in order."""
    page.wait_for_selector(f".list-section[data-section='{zone}']")
    return page.evaluate(f"""() => {{
      const out = []; let el = document.querySelector(".list-section[data-section='{zone}']");
      while ((el = el.nextElementSibling) && el.classList.contains('task-item'))
        out.push(el.querySelector('.task-title').textContent);
      return out; }}""")


def test_open_has_unsorted_and_sorted_sections(page, wiki):
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    for t in ("Old new", "Fresh"):
        page.request.post("/api/tasks", data={"title": t})
    page.request.post("/api/tasks", data={"title": "Overdue", "due_date": "2020-01-01"})
    page.goto("/tasks?view=open&group=none")
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
    page.request.post("/api/tasks", data={"title": "Paint fence", "description": "Two coats."})
    page.goto("/tasks?view=open&group=none")
    modal = page.locator("#task-edit-modal")
    for part in (".task-title", ".task-desc"):
        if part == ".task-desc":
            open_details(page, "Paint fence")
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
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    for t in ("One", "Two"):
        page.request.post("/api/tasks", data={"title": t})
    page.goto("/tasks?view=open&group=none")
    assert _open_titles(page, "unsorted") == ["Two", "One"]
    # Within Unsorted nothing moves (newest first).
    card(page, "Two").drag_to(card(page, "One"))
    assert _open_titles(page, "unsorted") == ["Two", "One"]
    # Onto a sorted task: where the pointer is, here its lower half, so after it.
    _drop(card(page, "One"), card(page, "Alpha"), "after")
    wait_until(page, lambda: _open_titles(page, "sorted") == ["Alpha", "One", "Bravo"])
    # (The numbers are whatever places them so: a move writes the moved task alone.)
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Alpha", "One", "Bravo"])
    assert task_by_title(page, "Two")["sort_order"] is None
    # Onto the Sorted heading: first in Sorted.
    card(page, "Two").drag_to(_section(page, "sorted"))
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Two", "Alpha", "One", "Bravo"])
    expect(_section(page, "unsorted")).to_have_class(re.compile(r"\bboard-day--empty\b"))
    # A sorted task onto the Sorted heading (from below) or the Unsorted
    # heading goes back to Unsorted.
    card(page, "Bravo").drag_to(_section(page, "sorted"))
    wait_for_field(page, "Bravo", "sort_order", None)
    card(page, "Alpha").drag_to(_section(page, "unsorted"))
    wait_for_field(page, "Alpha", "sort_order", None)
    assert _open_titles(page, "sorted") == ["Two", "One"]
    assert [t["title"] for t in sorted(
        (t for t in page.request.get("/api/tasks").json() if t["sort_order"] is not None),
        key=lambda t: t["sort_order"])] == ["Two", "One"]


def test_drop_lands_where_the_pointer_is_from_anywhere(page, wiki):
    # Regression: a task from Unsorted landed after the task dropped on,
    # one from within Sorted (moving up) before it. Now the pointer decides,
    # the same from either section: upper half before, lower half after.
    page.set_viewport_size({"width": 1000, "height": 1200})
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo", "Charlie")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.request.post("/api/tasks", data={"title": "New"})
    page.goto("/tasks?view=open&group=none")
    _drop(card(page, "New"), card(page, "Bravo"), "before")          # from Unsorted
    wait_until(page, lambda: _open_titles(page, "sorted") == ["Alpha", "New", "Bravo", "Charlie"])
    _drop(card(page, "Charlie"), card(page, "Alpha"), "before")      # within Sorted, up
    wait_until(page, lambda: _open_titles(page, "sorted") == ["Charlie", "Alpha", "New", "Bravo"])
    _drop(card(page, "Charlie"), card(page, "New"), "after")         # within Sorted, down
    wait_until(page, lambda: _open_titles(page, "sorted") == ["Alpha", "New", "Charlie", "Bravo"])
    _drop(card(page, "Bravo"), card(page, "Alpha"), "after")         # within Sorted, up
    wait_until(page, lambda: _open_titles(page, "sorted") == ["Alpha", "Bravo", "New", "Charlie"])
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Alpha", "Bravo", "New", "Charlie"])


def test_open_keyboard_crosses_the_sections(page, wiki):
    ids = [page.request.post("/api/tasks", data={"title": t}).json()["id"] for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    for t in ("One", "Two"):
        page.request.post("/api/tasks", data={"title": t})
    page.goto("/tasks?view=open&group=none")
    # ↓ on any unsorted task (not just the last) puts it first in Sorted.
    card(page, "Two").click()
    page.keyboard.press("Control+Shift+ArrowDown")
    wait_until(page, lambda: _by_order(page, "sort_order") == ["Two", "Alpha", "Bravo"])
    assert _open_titles(page, "sorted") == ["Two", "Alpha", "Bravo"]
    # ↑ on the first sorted task sends it back to Unsorted.
    page.keyboard.press("Control+Shift+ArrowUp")
    wait_for_field(page, "Two", "sort_order", None)
    # ↑ within Unsorted does nothing.
    card(page, "One").click()
    page.keyboard.press("Control+Shift+ArrowUp")
    assert _open_titles(page, "unsorted") == ["Two", "One"]


def test_open_reorder_under_a_filter_keeps_hidden_tasks_in_place(page, wiki):
    ids = [page.request.post("/api/tasks", data={"title": t, "tags": tags}).json()["id"]
           for t, tags in (("A1", "a"), ("B1", "b"), ("A2", "a"), ("B2", "b"))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=open&group=none&tag=a")
    assert _open_titles(page, "sorted") == ["A1", "A2"]
    card(page, "A2").drag_to(card(page, "A1"))
    wait_until(page, lambda: _open_titles(page, "sorted") == ["A2", "A1"])
    # Only the dragged task moves (right before A1); A1 doesn't jump past B1.
    wait_until(page, lambda: _by_order(page, "sort_order") == ["A2", "A1", "B1", "B2"])


def test_closed_tasks_survive_the_load_race(page, wiki):
    # Regression: the closed tasks were fetched in parallel with the open
    # ones. When they arrived first, the open tasks replaced the list and the
    # closed ones were lost for good. (It showed in the link's count of
    # matches in Closed, which is gone; Closed opened while the list reloads
    # is the same race.)
    t = page.request.post("/api/tasks", data={"title": "Buy milk"}).json()
    page.request.put(f"/api/tasks/{t['id']}", data={"completed": True})
    page.request.post("/api/tasks", data={"title": "Something else"})

    def slow_open_tasks(route):
        page.wait_for_timeout(400)  # the closed tasks arrive first
        route.continue_()
    page.route("**/api/tasks", slow_open_tasks)
    page.goto("/tasks?view=open&group=none")
    expect(card(page, "Something else")).to_be_visible()
    # Only once the (slow) open tasks are in: before that Closed already
    # showed the task, and the bug only struck when they replaced the list.
    with page.expect_response("**/api/tasks"):
        page.evaluate("() => { document.dispatchEvent(new Event('visibilitychange')); }")  # the list reloads
        page.locator("#task-closed-link").click()
        expect(card(page, "Buy milk")).to_be_visible()
    page.wait_for_timeout(300)
    expect(card(page, "Buy milk")).to_be_visible()
    expect(card(page, "Something else")).to_have_count(0)


def test_ticked_tasks_stay_ticked_when_the_first_one_goes(page, wiki):
    # Regression: ticking several tasks in a row, the first one's completion
    # re-rendered the list and the others lost their tick (yet still completed).
    for title in ("Alpha", "Beta", "Gamma"):
        page.request.post("/api/tasks", data={"title": title})
    page.clock.install()
    page.goto("/tasks?view=open&group=none")
    card(page, "Alpha").locator("input[type=checkbox]").check()
    page.clock.run_for(2000)
    card(page, "Beta").locator("input[type=checkbox]").check()
    card(page, "Gamma").locator("input[type=checkbox]").check()
    page.clock.run_for(3500)   # Alpha's grace period + fade-out
    expect(card(page, "Alpha")).to_have_count(0)
    for title in ("Beta", "Gamma"):
        expect(card(page, title).locator("input[type=checkbox]")).to_be_checked()
        expect(card(page, title)).to_have_class(re.compile(r"\btask-completing\b"))
    # Unticking one still cancels it; the other goes as planned.
    card(page, "Gamma").locator("input[type=checkbox]").uncheck()
    page.clock.run_for(3000)
    expect(card(page, "Beta")).to_have_count(0)
    expect(card(page, "Gamma")).to_be_visible()
    wait_until(page, lambda: [t["title"] for t in page.request.get("/api/tasks").json()] == ["Gamma"])


def test_copy_link_to_a_task(page, wiki):
    # ⋯ → Copy link puts the task's /tasks?task= link in the clipboard (a
    # done one's on Completed); opening it selects the task.
    tid = page.request.post("/api/tasks", data={"title": "Call Bob"}).json()["id"]
    done = page.request.post("/api/tasks", data={"title": "Old one"}).json()["id"]
    page.request.put(f"/api/tasks/{done}", data={"completed": True})
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.goto("/tasks?view=open&group=none")
    card(page, "Call Bob").locator(".task-menu-btn").click()
    page.locator(".task-menu").get_by_role("menuitem", name="Copy link").click()
    expect(page.locator(".task-undo-toast")).to_contain_text("Link copied")
    base = page.url.split("/tasks")[0]
    link = page.evaluate("navigator.clipboard.readText()")
    assert link == f"{base}/tasks?task={tid}&view=open"
    page.goto("/")
    page.goto(link)
    expect(card(page, "Call Bob")).to_have_class(re.compile(r"\btask-selected\b"))

    open_tab(page, "closed")
    card(page, "Old one").locator(".task-menu-btn").click()
    page.locator(".task-menu").get_by_role("menuitem", name="Copy link").click()
    expect(page.locator(".task-undo-toast")).to_contain_text("Link copied")
    assert page.evaluate("navigator.clipboard.readText()") == f"{base}/tasks?task={done}&view=closed"


def test_click_on_checkbox_label_ticks_without_selecting(page, wiki):
    # A press on the checkbox's label (on touch it reaches beside the box)
    # ticks it like one on the box: it used to select the card as well.
    page.request.post("/api/tasks", data={"title": "Water plants"})
    page.goto("/tasks?view=open&group=none")
    c = card(page, "Water plants")
    label = c.locator(".task-checkbox")
    label.dispatch_event("mousedown", {"button": 0})
    label.dispatch_event("click")
    expect(c).to_contain_class("task-completing")
    expect(c).not_to_contain_class("task-selected")


def test_keep_task_goes_back_to_open_with_undo(page, wiki):
    # Ticking off a keep task (a staple on a shopping list) takes it off
    # Focus and back to Open, shows when it was last done, and can be undone.
    today = date.today().isoformat()
    page.request.post("/api/tasks", data={"title": "Milk", "keep": True, "start_date": today})
    page.clock.install()
    page.goto("/tasks?view=today")
    expect(card(page, "Milk").locator(".task-keep")).to_be_visible()
    card(page, "Milk").locator("input[type=checkbox]").check()
    page.clock.run_for(5500)   # completion grace period + fade-out
    expect(card(page, "Milk")).to_have_count(0)
    wait_for_field(page, "Milk", "start_date", None)
    assert task_by_title(page, "Milk")["closed_at"] is None
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
    # A list whose new tasks are keep tasks (its setting); a keep task can't repeat.
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=groceries keep=1 view=open group=none]]\n")
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


def test_a_task_in_a_private_list_is_private(page, wiki, monkeypatch):
    # With users: a task made in a private list of yours is private (🔒 on
    # the card, in your own task files), and stays so in your Inbox; moved
    # into a shared list, it is shared, which the dialog says before Save.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    list_page(page, wiki, "private/alice/todo", "# Todo\n\n[[tasks: list=mine view=open group=none]]\n")
    page.goto("/page/private/alice/todo")
    page.locator("#task-add-btn").click()
    lists, note = page.locator("#task-edit-list"), page.locator("#task-edit-list-note")
    expect(lists).to_have_value("mine")
    expect(lists.locator("option")).to_have_text(["🔒 Inbox", "🔒 mine", "team", "New list…"])
    expect(page.locator(".task-edit-target")).to_have_text("Adds to the list mine: only you see it.")
    expect(note).to_be_hidden()                                      # a new task: the line under the title says it
    expect(page.locator("#task-edit-private")).to_have_count(0)      # no Private box: the list says it
    page.locator("#task-edit-title").fill("Gift for Bob")
    page.locator("#task-edit-form button[type=submit]").click()
    expect(card(page, "Gift for Bob").locator(".task-private")).to_be_visible()
    wait_for_field(page, "Gift for Bob", "private", True)
    assert "Gift for Bob" in (wiki.root / "private/alice/_tasks/lists/mine/tasks.json").read_text()

    page.keyboard.press("Enter")   # the new task is selected: edit it
    expect(lists).to_have_value("mine")
    lists.select_option("")        # her Inbox: as private as her list
    expect(note).to_have_text("Private: only you see this task.")
    lists.select_option("team")
    expect(note).to_have_text("Everyone who uses the wiki will see and change this task. It can’t be made private again.")
    lists.select_option("mine")
    expect(note).to_have_text("Private: only you see this task.")
    lists.select_option("team")
    page.locator("#task-edit-form button[type=submit]").click()
    wait_for_field(page, "Gift for Bob", "private", False)
    assert task_by_title(page, "Gift for Bob")["list"] == "team"
    # It left the list this page shows; All tasks has it, without the lock.
    expect(card(page, "Gift for Bob")).to_have_count(0)
    page.goto("/tasks?view=open&group=none")
    expect(card(page, "Gift for Bob")).to_be_visible()
    expect(card(page, "Gift for Bob").locator(".task-private")).to_have_count(0)
    assert "Gift for Bob" in (wiki.root / "_tasks/lists/team/tasks.json").read_text()
    # No undo of a share: the toast that says where it went has none to take it back.
    assert not (wiki.root / "private/alice/_tasks/lists/mine/tasks.json").exists()
    # A shared task doesn't go back: neither her list nor her Inbox is offered.
    open_edit(page, "Gift for Bob")
    expect(lists.locator("option[value=mine]")).to_have_attribute("disabled", "")
    expect(lists.locator("option").first).to_have_attribute("disabled", "")


def test_a_tag_named_as_a_private_list_keeps_a_task_shared(page, wiki, monkeypatch):
    # gifts is alice's private list, but a tag of that name is a tag: the
    # task stays where its list says. In the title, #gifts is the list.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "gifts", private=True)
    make_list(page, "team")
    page.goto("/tasks?list=team&view=open&group=none")
    page.locator("#task-add-btn").click()
    lists = page.locator("#task-edit-list")
    expect(lists).to_have_value("team")         # a shared list: the tag doesn't change that
    page.locator("#task-edit-title").fill("Book")
    page.locator("#task-edit-tags").fill("gifts ")
    expect(lists).to_have_value("team")
    expect(page.locator("#task-edit-list-note")).to_be_hidden()
    expect(page.locator(".task-edit-target")).to_have_text("Adds to the list team: everyone sees it.")
    page.locator("#task-edit-form button[type=submit]").click()
    wait_for_field(page, "Book", "private", False)
    assert task_by_title(page, "Book")["tags"] == ["gifts"] and task_by_title(page, "Book")["list"] == "team"
    # The title's shorthand names the list: the new task goes into it, private.
    page.locator("#task-add-btn").click()
    page.locator("#task-edit-title").fill("Ring #gifts")
    expect(page.locator(".task-edit-target")).to_have_text("Adds to the list gifts: only you see it.")
    page.locator("#task-edit-form button[type=submit]").click()
    wait_for_field(page, "Ring", "private", True)
    assert task_by_title(page, "Ring")["list"] == "gifts" and task_by_title(page, "Ring")["tags"] == []


def test_c_t_makes_a_private_task_in_the_users_inbox(page, wiki, monkeypatch):
    # With users, c t starts in the user's Inbox, whatever she pinned: a task
    # nobody chose a place for is not everyone's, and the dialog says who
    # sees it. It is in her My tasks and on her Inbox's pill; bob sees nothing of it.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team", pin=True)
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    page.keyboard.press("c")
    page.keyboard.press("t")
    modal = page.locator(".task-edit-modal[open]")
    lists = modal.locator("select[id^=task-edit-list]")
    target = modal.locator(".task-edit-target")
    expect(lists).to_have_value("")
    expect(lists.locator("option")).to_have_text(["🔒 Inbox", "team", "New list…"])
    expect(target).to_have_text("Adds to your Inbox: only you see it.")
    expect(modal.locator("[id^=task-edit-start]")).to_have_value(date.today().isoformat())
    lists.select_option("team")
    expect(target).to_have_text("Adds to the list team: everyone sees it.")
    lists.select_option("")
    expect(target).to_have_text("Adds to your Inbox: only you see it.")
    modal.locator("[id^=task-edit-title]").fill("Call the bank")
    modal.locator("button[type=submit]").click()
    expect(modal).to_be_hidden()
    wait_for_field(page, "Call the bank", "private", True)
    made = task_by_title(page, "Call the bank")
    assert "list" not in made and "assignee" not in made
    assert "Call the bank" in (wiki.root / "private/alice/_tasks/tasks.json").read_text()
    assert "Call the bank" not in (wiki.root / "_tasks/tasks.json").read_text()
    # The Tasks link counts it; My tasks and her Inbox show it, with a lock.
    link = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(link.locator(".module-focus")).to_have_text("1")
    link.click()
    page.wait_for_url(re.compile(r"/tasks\?mine=1$"))
    expect(card(page, "Call the bank").locator(".task-private")).to_be_visible()
    inbox = page.locator('.tasks-place[data-place="inbox"]')
    expect(inbox.locator(".module-focus")).to_have_text("1")
    expect(inbox.locator(".tasks-place-lock")).to_have_count(1)
    expect(inbox).to_have_attribute("title", "Inbox: new tasks land here (only you see it)")
    inbox.click()
    page.wait_for_url(re.compile(r"/tasks\?list=inbox$"))
    expect(page.locator("h1")).to_have_text("Inbox 🔒")
    expect(page.locator(".tasks-inbox-line")).to_have_text(
        "New tasks land here, and only you see them. Move one to a list to file it; in a shared list everyone sees it.")
    expect(card(page, "Call the bank")).to_be_visible()
    # bob doesn't see it: not in the API, not in his Inbox, not on his Tasks link.
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    assert "Call the bank" not in [t["title"] for t in page.request.get("/api/tasks").json()]
    page.goto("/tasks?list=inbox")
    expect(page.locator("h1")).to_have_text("Inbox 🔒")
    expect(page.locator(".task-item")).to_have_count(0)
    expect(page.locator(".breadcrumb .module-link[data-module=tasks] .module-focus")).to_be_hidden()


def test_a_name_on_a_new_inbox_task_asks_for_a_shared_list(page, wiki, monkeypatch):
    # The Inbox is its user's alone and a task for someone is shared work:
    # naming someone on a new task nobody chose a list for asks for a shared
    # list and offers them, the one this browser last made a task in first.
    # None is chosen for the user (the List field moved by itself once, and
    # a task went where nobody had looked). With none to offer, the dialog
    # says so and Save asks for a list.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "own", private=True, pin=True)        # a private list, pinned first: no place for it either
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    modal = page.locator(".task-edit-modal[open]")
    lists = modal.locator("select[id^=task-edit-list]")
    target = modal.locator(".task-edit-target")
    offered = target.locator(".task-edit-target-list")
    title = modal.locator("[id^=task-edit-title]")
    warn = re.compile(r"\btask-edit-target-warn\b")

    def c_t():
        page.keyboard.press("c")
        page.keyboard.press("t")
        expect(lists).to_have_value("")

    # No shared list: the line under the title says what is missing, and Save refuses.
    c_t()
    title.fill("Ask about the loan @bob")
    expect(lists).to_have_value("")
    expect(target).to_have_text("A task for someone else needs a shared list: choose one in List.")
    expect(target).to_have_class(warn)
    expect(offered).to_have_count(0)
    assert target.evaluate("el => getComputedStyle(el).color") == "rgb(180, 83, 9)"     # a warning, not the muted line
    expect(lists).to_be_visible()                         # the field it speaks of is there
    modal.locator("button[type=submit]").click()
    expect(modal).to_be_visible()
    assert lists.evaluate("el => el.validationMessage") == "A task for someone else needs a shared list"
    assert page.request.get("/api/tasks").json() == []
    title.fill("Ask about the loan")                      # the name gone: her Inbox will do
    expect(target).to_have_text("Adds to your Inbox: only you see it.")
    expect(target).not_to_have_class(warn)
    # The same for a name in the Assignee field.
    modal.locator("[data-more=assignee]").click()
    modal.locator("input[name=assignee]").fill("bob")
    expect(target).to_have_text("A task for someone else needs a shared list: choose one in List.")
    modal.locator("input[name=assignee]").fill("")
    expect(target).to_have_text("Adds to your Inbox: only you see it.")
    page.keyboard.press("Escape")

    # One shared list: it is offered, not taken; Save without it still asks.
    make_list(page, "team")
    page.goto("/page/garden")
    c_t()
    title.fill("Ask about the loan @bob")
    expect(offered).to_have_text(["team"])
    expect(lists).to_have_value("")                       # nothing chosen for her
    expect(target).to_have_class(warn)
    modal.locator("button[type=submit]").click()
    expect(modal).to_be_visible()
    assert lists.evaluate("el => el.validationMessage") == "A task for someone else needs a shared list"
    title.fill("Ask about the loan")                      # without the name there is nothing to ask
    expect(offered).to_have_count(0)
    expect(target).to_have_text("Adds to your Inbox: only you see it.")
    title.fill("Ask about the loan @bob")
    offered.first.click()                                 # choosing it is choosing it in the List field
    expect(lists).to_have_value("team")
    expect(target).to_have_text("Adds to the list team: everyone sees it.")
    expect(target).not_to_have_class(warn)
    expect(title).to_be_focused()
    title.fill("Ask about the loan")                      # chosen by hand: it stays, whatever the title says
    expect(lists).to_have_value("team")
    title.fill("Ask about the loan @bob")
    modal.locator("button[type=submit]").click()
    expect(modal).to_be_hidden()
    wait_for_field(page, "Ask about the loan", "assignee", "bob")
    task = task_by_title(page, "Ask about the loan")
    assert task["private"] is False and task["list"] == "team"

    # Several shared lists: the one the last task went to comes first, then A–Z.
    make_list(page, "board")
    make_list(page, "zoo")
    page.goto("/page/garden")
    assert page.evaluate("localStorage.getItem('zone-tasks-shared-list')") == "team"
    c_t()
    title.fill("Send the minutes @bob")
    expect(offered).to_have_text(["team", "board", "zoo"])
    expect(lists).to_have_value("")
    offered.nth(1).click()
    expect(lists).to_have_value("board")
    modal.locator("button[type=submit]").click()
    expect(modal).to_be_hidden()
    wait_for_field(page, "Send the minutes", "list", "board")
    c_t()
    title.fill("Book the room @bob")
    expect(offered).to_have_text(["board", "team", "zoo"])   # remembered
    page.keyboard.press("Escape")
    page.evaluate("localStorage.removeItem('zone-tasks-shared-list')")
    page.reload()
    c_t()
    title.fill("Book the room @bob")
    expect(offered).to_have_text(["board", "team", "zoo"])   # none remembered: A–Z
    lists.select_option("team")                           # the List field does the same
    expect(target).to_have_text("Adds to the list team: everyone sees it.")
    modal.locator("button[type=submit]").click()
    wait_for_field(page, "Book the room", "assignee", "bob")
    assert task_by_title(page, "Book the room")["list"] == "team"


def test_a_new_tasks_fields_follow_its_title(page, wiki, monkeypatch):
    # What the title's words say shows in the fields as it is typed, and what
    # they showed before once the word is gone; a field changed by hand takes
    # its word out of the title. So the dialog never says two things.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "website")
    make_list(page, "team")
    page.goto("/tasks?list=team&view=open")
    page.locator("[id^=task-add-btn]").click()
    modal = page.locator(".task-edit-modal[open]")
    title = modal.locator("[id^=task-edit-title]")
    lists = modal.locator("select[id^=task-edit-list]")
    start, due = modal.locator("input[id^=task-edit-start]"), modal.locator("input[id^=task-edit-due]")
    importance = modal.locator("select[id^=task-edit-importance]")
    chip = modal.locator("[id^=task-edit-assignee-field] .tag-chip-label")
    expect(lists).to_have_value("team")
    expect(start).to_have_value("")

    title.fill("Fix footer @bob #website tomorrow due:+3 !")
    expect(lists).to_have_value("website")
    expect(start).to_have_value(_day(1))
    expect(due).to_have_value(_day(3))
    expect(importance).to_have_value("high")
    expect(chip).to_have_text(["@bob"])
    expect(modal.locator("[data-date-trigger^=task-edit-start]")).to_have_text("Tomorrow")

    # A word gone: its field is what it was before.
    title.fill("Fix footer @bob tomorrow")
    expect(lists).to_have_value("team")
    expect(due).to_have_value("")
    expect(importance).to_have_value("")
    expect(start).to_have_value(_day(1))
    title.fill("Fix footer")
    expect(start).to_have_value("")
    expect(chip).to_have_count(0)

    # A field changed by hand takes its word out of the title, and keeps the hand's.
    title.fill("Fix footer @bob #website tomorrow !low")
    lists.select_option("team")
    expect(title).to_have_value("Fix footer @bob tomorrow !low")
    importance.select_option("")
    expect(title).to_have_value("Fix footer @bob tomorrow")
    modal.locator("[data-date-trigger^=task-edit-start]").click()
    pick_date(modal.locator(".tasks-datepicker"), _day(2))
    expect(title).to_have_value("Fix footer @bob")
    expect(start).to_have_value(_day(2))
    modal.locator("[id^=task-edit-assignee-field] .tag-chip-x").click()
    expect(title).to_have_value("Fix footer")
    expect(chip).to_have_count(0)
    title.fill("Fix footer !")                            # typed on: the other fields keep the hand's
    expect(importance).to_have_value("high")
    expect(lists).to_have_value("team")
    expect(start).to_have_value(_day(2))

    modal.locator("button[type=submit]").click()
    expect(modal).to_be_hidden()
    wait_for_field(page, "Fix footer", "importance", "high")
    task = task_by_title(page, "Fix footer")
    assert (task["list"], task["start_date"], task["due_date"], task.get("assignee")) == ("team", _day(2), None, None)


def test_a_name_typed_before_the_users_are_known_still_counts(page, wiki, monkeypatch):
    # Regression: c t on a page without tasks loads the task app, and with
    # it who a task can be given to. A title typed before that answer came
    # kept its @name as text and the task in the Inbox, at random.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    wiki.write("garden", "# Garden\n")
    held = []
    page.route("**/api/tasks/assignable", lambda route: held.append(route))
    page.goto("/page/garden")
    page.keyboard.press("c")
    page.keyboard.press("t")
    modal = page.locator(".task-edit-modal[open]")
    lists = modal.locator("select[id^=task-edit-list]")
    title = modal.locator("[id^=task-edit-title]")
    title.fill("Ask about the loan @bob")
    wait_until(page, lambda: len(held) > 0, settled=False)
    expect(lists).to_have_value("")                       # bob is no one yet
    expect(modal.locator("[id^=task-edit-quick]")).to_be_hidden()
    for route in held:
        route.continue_()
    expect(modal.locator("[id^=task-edit-quick]")).to_have_text("@bob")
    expect(modal.locator("[id^=task-edit-assignee-field] .tag-chip-label")).to_have_text(["@bob"])
    modal.locator(".task-edit-target-list", has_text="team").click()
    expect(lists).to_have_value("team")
    expect(modal.locator(".task-edit-target")).to_have_text("Adds to the list team: everyone sees it.")
    modal.locator("button[type=submit]").click()
    wait_for_field(page, "Ask about the loan", "assignee", "bob")


def test_a_private_and_a_shared_list_of_one_name(page, wiki, monkeypatch):
    # alice has a private list task, and bob made a shared one of that name
    # (he doesn't see hers): her own is "task" to her, the shared one
    # "shared/task". A task made in each is as private as its list.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "task", private=True, pin=True)
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    make_list(page, "task")
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    assert page.request.post("/tasks/pin-list", form={"list": "shared/task", "pin": "first"}).ok
    page.goto("/tasks?list=shared/task&view=open&group=none")
    expect(page.locator("h1")).to_have_text("List: task")
    page.locator("#task-add-btn").click()
    modal = page.locator(".task-edit-modal[open]")
    lists = modal.locator("select[id^=task-edit-list]")
    expect(lists).to_have_value("shared/task")
    expect(lists.locator("option")).to_have_text(["🔒 Inbox", "task (shared)", "🔒 task", "New list…"])
    expect(modal.locator("[id^=task-edit-list-note]")).to_be_hidden()
    expect(modal.locator(".task-edit-target")).to_have_text("Adds to the list task: everyone sees it.")
    modal.locator("[id^=task-edit-title]").fill("Call the bank")
    modal.locator("button[type=submit]").click()
    expect(modal).to_be_hidden()
    wait_for_field(page, "Call the bank", "private", False)   # the dialog closes before its request is through
    assert task_by_title(page, "Call the bank")["list"] == "task"
    # Her own list of that name: private.
    page.goto("/tasks?list=task&view=open&group=none")
    page.locator("#task-add-btn").click()
    expect(lists).to_have_value("task")
    expect(modal.locator("[id^=task-edit-list-note]")).to_be_hidden()
    expect(modal.locator(".task-edit-target")).to_have_text("Adds to the list task: only you see it.")
    modal.locator("[id^=task-edit-title]").fill("Buy a ring")
    modal.locator("button[type=submit]").click()
    wait_for_field(page, "Buy a ring", "private", True)
    # The switcher has both, hers with a lock, each with its own tasks.
    page.goto("/tasks?list=shared/task&view=open&group=none")
    expect(card(page, "Call the bank")).to_be_visible()
    expect(card(page, "Buy a ring")).to_have_count(0)
    expect(page.locator(".tasks-place[data-place='list:task'] .tasks-place-lock")).to_have_count(1)
    expect(page.locator(".tasks-place[data-place='list:shared/task'] .tasks-place-lock")).to_have_count(0)
    page.goto("/tasks?list=task&view=open&group=none")
    expect(card(page, "Buy a ring")).to_be_visible()
    expect(card(page, "Call the bank")).to_have_count(0)


def test_a_shared_task_cant_go_into_a_private_list(page, wiki, monkeypatch):
    # bob made it in a shared list: it is shared for good, so alice's private
    # list and her Inbox are off for it. Her own private task can leave its
    # list, and the modal says what that does.
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    for name in ("team", "board"):
        make_list(page, name)
    page.request.post("/api/tasks", data={"title": "Bob's errand", "list": "board"})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "own", private=True)
    page.request.post("/api/tasks", data={"title": "Alice's errand", "list": "own"})
    page.request.post("/api/tasks", data={"title": "Alice's note"})        # her Inbox
    page.goto("/tasks?view=open&group=none")
    open_edit(page, "Bob's errand")
    lists, note = page.locator("#task-edit-list"), page.locator("#task-edit-list-note")
    expect(lists.locator("option")).to_have_text(["🔒 Inbox", "board", "🔒 own", "team", "New list…"])
    expect(lists.locator("option[value=own]")).to_have_attribute("disabled", "")
    inbox = lists.locator("option").first
    expect(inbox).to_have_attribute("disabled", "")
    expect(inbox).to_have_attribute("title", "A shared task stays shared")
    expect(lists.locator("option[value=team]")).not_to_have_attribute("disabled", "")
    page.keyboard.press("Escape")
    # (The server says the same to a client that asks anyway.)
    bobs = task_by_title(page, "Bob's errand")
    assert page.request.put(f"/api/tasks/{bobs['id']}", data={"list": ""}).status == 400
    for title in ("Alice's errand", "Alice's note"):
        open_edit(page, title)
        expect(lists.locator("option[value=own]")).not_to_have_attribute("disabled", "")
        expect(lists.locator("option").first).not_to_have_attribute("disabled", "")
        expect(note).to_have_text("Private: only you see this task.")
        lists.select_option("team")
        expect(note).to_have_text("Everyone who uses the wiki will see and change this task. It can’t be made private again.")
        lists.select_option("own")
        expect(note).to_have_text("Private: only you see this task.")
        page.keyboard.press("Escape")
    # Moving several: the private list and the Inbox are off while a shared task is among them.
    card(page, "Alice's errand").locator(".task-title").click()
    card(page, "Bob's errand").locator(".task-title").click(modifiers=["Control"])
    page.locator("#task-selbar [data-sel='list']").click()
    dlg = page.locator(".bulk-list-modal")
    expect(dlg.locator(".bulk-list-row[data-list=own]")).to_be_disabled()
    expect(dlg.locator(".bulk-list-row[data-list=own]")).to_contain_text("a shared task stays shared")
    inbox_row = dlg.locator(".bulk-list-row[data-list='']")
    expect(inbox_row).to_be_disabled()
    expect(inbox_row).to_have_text(re.compile(r"🔒 Inbox\s*a shared task stays shared"))
    expect(dlg.locator(".bulk-list-row[data-list=team]")).to_contain_text("shares it with everyone")
    dlg.locator(".bulk-list-row[data-list=team]").click()
    wait_for_field(page, "Alice's errand", "private", False)
    wait_for_field(page, "Bob's errand", "list", "team")
    assert task_by_title(page, "Alice's errand")["list"] == "team"
    # What was hers alone is assigned to her, so it stays in her My tasks; bob's is whose it was.
    assert task_by_title(page, "Alice's errand")["assignee"] == "alice"
    assert "assignee" not in task_by_title(page, "Bob's errand")


def test_a_new_list_from_the_move_dialog_is_as_private_as_its_tasks(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    make_list(page, "team")
    page.request.post("/api/tasks", data={"title": "Bob's errand", "list": "team"})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "own", private=True)
    page.request.post("/api/tasks", data={"title": "Alice's errand", "list": "own"})
    page.goto("/tasks?view=open&group=none")
    dlg = page.locator(".bulk-list-modal")
    box, note = dlg.locator("#bulk-list-private"), dlg.locator(".bulk-list-note")

    def new_list_for(title):
        card(page, title).locator(".task-title").click(button="right")
        page.locator(".task-menu").get_by_role("menuitem", name="Move to list…").click()
        dlg.get_by_role("button", name="New list…").click()

    # A shared task stays shared: the new list can't be private.
    new_list_for("Bob's errand")
    expect(box).to_be_disabled()
    expect(box).not_to_be_checked()
    expect(note).to_be_hidden()
    page.keyboard.press("Escape")
    # A private task: the new list starts private, and unticking says what that does.
    new_list_for("Alice's errand")
    expect(box).to_be_checked()
    expect(note).to_be_hidden()
    box.uncheck()
    expect(note).to_have_text("Everyone who uses the wiki will see and change this task. It can’t be made private again.")
    box.check()
    dlg.locator("#bulk-list-name").fill("secret")
    dlg.get_by_role("button", name="Create and move").click()
    wait_for_field(page, "Alice's errand", "list", "secret")
    assert task_by_title(page, "Alice's errand")["private"] is True
    expect(card(page, "Alice's errand").locator(".task-tag-list")).to_have_text("secret")


def test_a_comment_can_be_edited_by_its_author(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    make_list(page, "team")                     # a task both see is in a shared list
    task = page.request.post("/api/tasks", data={"title": "Errand", "list": "team"}).json()
    page.request.post(f"/api/tasks/{task['id']}/comments", data={"text": "From Bob"})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    page.request.post(f"/api/tasks/{task['id']}/comments", data={"text": "From Alice"})
    page.goto("/tasks?view=open&group=none")
    open_edit(page, "Errand")
    items = page.locator("#task-edit-comment-list li")
    expect(items.filter(has_text="From Alice").locator("[data-comment-action]")).to_have_count(2)
    expect(items.filter(has_text="From Bob").locator("[data-comment-action]")).to_have_count(0)   # bob's: his alone to change


def test_no_private_checkbox_without_users(page, wiki):
    page.goto("/tasks")
    page.locator("#task-add-btn").click()
    expect(page.locator("#task-edit-title")).to_be_visible()
    expect(page.locator("#task-edit-private")).to_have_count(0)


def test_subtasks_show_their_count_and_ask_before_done(page, wiki):
    # Checklist items in the notes are the task's subtasks: the card says how
    # many are done, and Done with one still open asks.
    notes = "Before the trip:\n\n- [x] passport\n- [ ] tickets\n- [ ] hotel\n\n```\n- [ ] not one\n```"
    page.request.post("/api/tasks", data={"title": "Plan trip", "description": notes})
    page.request.post("/api/tasks", data={"title": "Only notes", "description": "no list here"})
    page.request.post("/api/tasks", data={"title": "All ticked", "description": "- [x] one"})
    page.goto("/tasks?view=open&group=none")
    badge = card(page, "Plan trip").locator(".task-subtasks")
    expect(badge).to_have_text("☑ 1/3")
    expect(badge).to_have_attribute("title", "1 of 3 subtasks done")
    expect(card(page, "Only notes").locator(".task-has-notes")).to_have_text("📝")
    expect(card(page, "Only notes").locator(".task-subtasks")).to_have_count(0)
    expect(card(page, "All ticked").locator(".task-subtasks-done")).to_have_text("☑ 1/1")
    # On the line it is what tells: the notes are hidden. A click opens them.
    expect(card(page, "Plan trip").locator(".task-desc")).to_be_hidden()
    expect(badge).to_be_visible()
    badge.click()
    # Ticking a subtask counts at once.
    card(page, "Plan trip").locator(".task-desc .task-list-item-checkbox").nth(1).click()
    expect(badge).to_have_text("☑ 2/3")
    # Done with a subtask open: asks, and Cancel leaves the task as it was.
    dialog = page.locator("#task-subtasks-modal")
    box = card(page, "Plan trip").locator(".task-checkbox input")
    box.click()
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("“Plan trip” has 1 open subtask.")
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    expect(box).not_to_be_checked()
    expect(card(page, "Plan trip")).not_to_have_class(re.compile("task-completing"))
    # Complete anyway: it completes as ever (its grace period, here cut short).
    box.click()
    dialog.get_by_role("button", name="Complete anyway").click()
    expect(card(page, "Plan trip")).to_have_class(re.compile("task-completing"))
    # No question without open subtasks.
    card(page, "All ticked").locator(".task-checkbox input").click()
    expect(dialog).to_be_hidden()
    expect(card(page, "All ticked")).to_have_class(re.compile("task-completing"))


def test_space_and_bulk_done_ask_about_open_subtasks(page, wiki):
    page.request.post("/api/tasks", data={"title": "Plan trip", "description": "- [ ] tickets\n- [ ] hotel"})
    page.request.post("/api/tasks", data={"title": "Water plants"})
    page.goto("/tasks?view=open&group=none")
    dialog = page.locator("#task-subtasks-modal")
    card(page, "Plan trip").locator(".task-title").click()
    page.keyboard.press("Space")
    expect(dialog).to_contain_text("“Plan trip” has 2 open subtasks.")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(card(page, "Plan trip")).not_to_have_class(re.compile("task-completing"))
    # Several selected: one question for them all; Cancel keeps the selection.
    card(page, "Plan trip").locator(".task-title").click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Space")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("1 of these 2 tasks have open subtasks.")
    dialog.get_by_role("button", name="Cancel").click()
    assert sorted(selected_titles(page)) == ["Plan trip", "Water plants"]
    assert len(page.request.get("/api/tasks").json()) == 2
    page.keyboard.press("Space")
    dialog.get_by_role("button", name="Complete anyway").click()
    expect(card(page, "Plan trip")).to_have_count(0)
    expect(card(page, "Water plants")).to_have_count(0)


def test_checkboxes_in_task_notes(page, wiki):
    # "- [ ] item" lines in the notes are real checkboxes, as on a page;
    # ticking one edits the notes (recording when, shown as a date chip),
    # leaves the task itself alone, and can be undone.
    page.request.post("/api/tasks", data={"title": "Bake", "description": "Need:\n- [ ] eggs\n- [x] flour"})
    page.goto("/tasks?view=open&group=none")
    checks = open_details(page, "Bake").locator(".task-desc input[type=checkbox]")
    expect(checks).to_have_count(2)
    expect(checks.nth(0)).not_to_be_checked()
    expect(checks.nth(1)).to_be_checked()
    expect(card(page, "Bake").locator(".task-desc")).not_to_contain_text("[ ]")

    checks.nth(0).check()
    wait_until(page, lambda: re.fullmatch(r"Need:\n- \[x\] eggs <!--d:\S+-->\n- \[x\] flour",
                                          task_by_title(page, "Bake")["description"]))
    expect(card(page, "Bake").locator(".task-desc .task-date-done")).to_have_text(date.today().isoformat())
    expect(page.locator("#task-edit-modal")).to_be_hidden()
    assert task_by_title(page, "Bake")["closed_at"] is None
    expect(card(page, "Bake").locator(".task-checkbox input")).not_to_be_checked()
    # Undo unticks it again, and the date goes.
    page.locator(".task-undo-toast button", has_text="Undo").click()
    wait_for_field(page, "Bake", "description", "Need:\n- [ ] eggs\n- [x] flour")
    expect(checks.nth(0)).not_to_be_checked()


def test_notes_and_comments_render_as_on_a_page(page, wiki):
    # The page renderer draws the notes: headings, tables, highlighted code,
    # _italic_ (the formatting bar's), one paragraph for lines in a row.
    # Comments too.
    tid = page.request.post("/api/tasks", data={"title": "Fence", "description":
        "## Steps\nsand\nprime\n\n| coat | days |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```\n\n_soft_ wood"}).json()["id"]
    page.request.post(f"/api/tasks/{tid}/comments", data={"text": "**primed** today\n\n```js\nlet y = 2\n```"})
    page.goto("/tasks?view=open&group=none")
    desc = card(page, "Fence").locator(".task-desc")
    expect(desc.locator("h2")).to_have_text("Steps")
    expect(desc.locator("p").first).to_have_text("sand prime")
    expect(desc.locator("table td").first).to_have_text("1")
    expect(desc.locator("pre code.hljs")).to_have_count(1)
    expect(desc.locator("em")).to_have_text("soft")
    expect(card(page, "Fence").locator(".task-card-comments strong")).to_have_text("primed")
    # Highlighted where it is drawn (js/tasktexts.js highlightIn), again
    # after the list is drawn anew.
    page.locator("#task-filter-search").fill("fence")
    expect(card(page, "Fence").locator(".task-desc pre code.hljs .hljs-number")).to_have_text("1")
    page.locator("#task-filter-search").fill("")
    card(page, "Fence").dblclick()
    expect(page.locator("#task-edit-comment-list strong")).to_have_text("primed")
    expect(page.locator("#task-edit-comment-list pre code.hljs .hljs-number")).to_have_text("2")


def test_a_comment_keeps_its_lines(page, wiki):
    # A comment typed on several lines shows on several lines, on the card
    # (under its time, not on the time's line) and in the dialog.
    tid = page.request.post("/api/tasks", data={"title": "Fence"}).json()["id"]
    page.request.post(f"/api/tasks/{tid}/comments", data={"text": "sanded\nprimed"})
    page.goto("/tasks?view=open&group=none")
    expect(card(page, "Fence").locator("div.task-card-comment .task-comment-body br")).to_have_count(1)
    card(page, "Fence").dblclick()
    body = page.locator("#task-edit-comment-list .task-comment-body")
    expect(body.locator("br")).to_have_count(1)
    assert body.inner_text().strip() == "sanded\nprimed"


def test_a_page_draws_its_lists_without_asking_for_the_tasks(page, wiki):
    # The page brings the open tasks and their texts' HTML along: its lists
    # draw from them (each its own), with no request for either. What comes
    # after (a write, the other list's reload) asks the server.
    wiki.write("garden", "# Garden\n")
    tid = page.request.post("/api/tasks", data={"title": "Fence", "tags": ["yard"], "description": "see [[garden]]"}).json()["id"]
    page.request.post(f"/api/tasks/{tid}/comments", data={"text": "**primed** today"})
    page.request.post("/api/tasks", data={"title": "Taxes"})
    wiki.write("lists", "# Lists\n\n[[tasks: tag=yard view=open group=none]]\n\n[[tasks: view=open group=none]]\n")
    asked = []
    page.on("request", lambda r: asked.append(r.url.split("?")[0].rsplit("/api/", 1)[-1])
            if r.method == "GET" and r.url.endswith("/api/tasks") or "/api/render-notes" in r.url else None)
    page.goto("/page/lists")
    yard, everything = page.locator("#tasks-list"), page.locator("#tasks-list-1")
    expect(yard.locator(".task-item")).to_have_count(1)
    expect(everything.locator(".task-item")).to_have_count(2)
    expect(yard.locator(".task-desc a.wikilink")).to_have_attribute("href", "/page/garden")
    expect(yard.locator(".task-card-comments strong")).to_have_text("primed")
    page.wait_for_load_state("networkidle")
    assert asked == []
    page.locator("#task-add-btn-1").click()
    page.locator("#task-edit-title-1").fill("Fresh #yard")
    page.locator("#task-edit-modal-1").get_by_role("button", name="Save").click()
    expect(everything.locator(".task-item")).to_have_count(3)
    expect(yard.locator(".task-item")).to_have_count(2)
    assert "tasks" in asked


PASTE = """(url) => {
    const dt = new DataTransfer();
    dt.setData('text/plain', url);
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}"""


def test_title_links(page, wiki):
    # Pasting a URL into the title asks for its name, as in the notes; the
    # card shows [name](url), [[wikilinks]] and bare URLs as links, menus
    # and toasts the plain words. A javascript: link stays text.
    page.request.post("/api/tasks", data={"title": "See [[Garden]] and https://e.x/a or [bad](javascript:alert(1))"})
    page.goto("/tasks?view=open&group=none")
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
    page.request.post("/api/tasks", data={"title": "Pack", "description":
        "Before:\n\n- [ ] tent\n  - [ ] pegs\n  - poles\n- stove"})
    page.goto("/tasks?view=open&group=none")
    desc = open_details(page, "Pack").locator(".task-desc")
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
    page.goto("/tasks?view=open&group=none")
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    notes = page.locator("#task-edit-desc")
    bar = modal.locator("#task-edit-format")
    expect(bar.locator('[data-fmt="maketask"]')).to_have_count(0)     # the editor's own
    expect(bar.locator('[data-fmt="tasklist"]')).to_have_count(0)     # notes show no embed
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
    # Preview takes the field's place, no shorter than it: the notes as the
    # card will show them; Edit (or F2) goes back.
    preview = modal.locator("#task-edit-preview")
    expect(preview).to_be_hidden()
    field_height = notes.bounding_box()["height"]
    modal.locator("#task-edit-preview-btn").click()
    expect(preview.locator("strong")).to_have_text("loud")
    expect(notes).to_be_hidden()
    expect(bar).to_be_hidden()
    assert preview.bounding_box()["height"] >= field_height - 1
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


def test_page_and_tasks_checkboxes_stay_apart(page, wiki):
    # A page with its own checklist and a task list: ticking one never ticks
    # the other, though both render the same checkboxes.
    page.request.post("/api/tasks", data={"title": "Pack", "description": "- [ ] tent"})
    wiki.write("trip", "# Trip\n\n[[tasks: view=open group=none]]\n\n- [ ] book train\n")
    page.goto("/page/trip")
    tasks_box = open_details(page, "Pack").locator(".task-desc .task-list-item-checkbox")
    tasks_box.check()
    wait_until(page, lambda: task_by_title(page, "Pack")["description"].startswith("- [x] tent"))
    assert "- [ ] book train" in wiki.read("trip")
    page.locator("article > ul .task-list-item-checkbox").check()
    wait_until(page, lambda: "- [x] book train" in wiki.read("trip"))
    assert task_by_title(page, "Pack")["description"].startswith("- [x] tent")


def test_notes_continue_checkbox_list_on_enter(page, wiki):
    # As in the page editor: Enter after a checkbox item starts the next one,
    # Enter on an empty item ends the list.
    page.goto("/tasks?view=open&group=none")
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
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=groceries keep=1 labels=Catalogue,Shopping_list]]\n")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "groceries", "keep": True})
    page.goto("/page/shop?view=open&group=none")
    expect(page.locator("#tasks-tabs [role='tab']")).to_have_text(["Catalogue", "Shopping list"])
    expect(page.locator("#tasks-tabs .active")).to_have_text("Catalogue")
    expect(card(page, "Milk").locator(".task-keep")).to_have_attribute(
        "title", "Keep: back to Catalogue when ticked off")
    open_tab(page, "closed")
    expect(page.locator("#task-closed-link")).to_have_text("← Back to Catalogue")


def test_filter_excludes_tags(page, wiki):
    # -#tag hides tasks with that tag; its suggestions work like #tag's.
    for title, tags in (("Plan trip", "tags,gernot"), ("Call vet", "tags,mary"), ("Untagged", "")):
        page.request.post("/api/tasks", data={"title": title, "tags": tags})
    page.goto("/tasks?view=open&group=none")
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
    expect(card(page, "Untagged")).to_have_count(0)   # once the debounced filter has run
    expect(card(page, "Plan trip")).to_be_visible()


def test_history_in_edit_modal_and_undo_leaves_no_trace(page, wiki):
    tid = page.request.post("/api/tasks", data={"title": "Report"}).json()["id"]
    page.request.put(f"/api/tasks/{tid}", data={"due_date": tomorrow()})
    page.goto("/tasks?view=open&group=none")

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
    (wiki.root / "_tasks" / "tasks.json").write_text(json.dumps([{
        "id": "old-1", "title": "Ancient", "created_at": "2025-03-04T09:15:00"}]))
    page.goto("/tasks?view=open&group=none")
    card(page, "Ancient").click()
    page.keyboard.press("Enter")
    history = page.locator("#task-edit-history")
    expect(history.locator("summary")).to_contain_text("1")
    history.locator("summary").click()
    expect(history.locator("li")).to_have_text("Mar 4, 2025 09:15 Created")


def test_planned_tasks_are_marked_on_every_tab(page, wiki):
    # A planned task gets a coloured left edge, the same on every tab: amber
    # when it is for today already (start today or earlier, also "since" a past
    # day), blue when planned later. Unplanned tasks get none.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    in_week = (date.today() + timedelta(days=4)).isoformat()
    for title, start in (("Since yesterday", yesterday), ("Thursday-ish", in_week), ("Undecided", None)):
        page.request.post("/api/tasks", data={"title": title, "start_date": start})
    page.goto("/tasks?view=open&group=none")
    expect(card(page, "Since yesterday")).to_have_class(re.compile(r"\btask-planned-now\b"))
    expect(card(page, "Thursday-ish")).to_have_class(re.compile(r"\btask-planned-later\b"))
    expect(card(page, "Undecided")).not_to_have_class(re.compile(r"task-planned"))
    edge = lambda title: card(page, title).evaluate("el => getComputedStyle(el).borderLeftColor")
    assert edge("Since yesterday") != edge("Thursday-ish") != edge("Undecided")
    # The 📅 date: blue for later; a past start ("since …") stays plain.
    expect(card(page, "Thursday-ish").locator(".task-start-info")).to_have_class(re.compile(r"\bstart-later\b"))
    expect(card(page, "Since yesterday").locator(".task-start-info")).not_to_have_class(re.compile(r"\bstart-(now|later)\b"))

    open_tab(page, "focus")
    expect(card(page, "Since yesterday")).to_have_class(re.compile(r"\btask-planned-now\b"))
    open_tab(page, "plan")
    expect(card(page, "Thursday-ish")).to_have_class(re.compile(r"\btask-planned-later\b"))


def test_due_colour_is_the_same_on_every_tab(page, wiki):
    # Regression: Open's Unsorted cards had a grey background that painted
    # over the deadline colour, so a task due today looked different on Open
    # than on Focus.
    today = date.today().isoformat()
    page.request.post("/api/tasks", data={"title": "Due now", "due_date": today, "start_date": today})
    page.goto("/tasks?view=open&group=none")
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
    page.request.post("/api/tasks", data={"title": "Old, due", "due_date": "2020-01-01", "start_date": today})
    page.request.post("/api/tasks", data={"title": "New", "start_date": today})
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": today,
                                                "due_date": due}).json()["id"]
           for t, due in (("Ranked first", None), ("Ranked second, due", today))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=open&group=none")
    assert _open_titles(page, "unsorted") == ["New", "Old, due"]
    assert _open_titles(page, "sorted") == ["Ranked first", "Ranked second, due"]
    open_tab(page, "focus")
    expect(_focus_titles(page)).to_have_text(["New", "Old, due", "Ranked first", "Ranked second, due"])


def test_history_of_a_repeating_task_continues(page, wiki):
    today = date.today().isoformat()
    tid = page.request.post("/api/tasks", data={"title": "Water plants", "rec_type": "daily",
                                               "start_date": today}).json()["id"]
    page.request.put(f"/api/tasks/{tid}", data={"completed": True})
    page.goto("/tasks?view=open&group=none")
    card(page, "Water plants").click()
    page.keyboard.press("Enter")
    history = page.locator("#task-edit-history")
    history.locator("summary").click()
    expect(history.locator("li")).to_have_count(4)             # and the link to the occurrence before
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
    page.request.post("/api/tasks", data={"title": "Late task", "due_date": yesterday})
    page.request.post("/api/tasks", data={"title": "Due today", "due_date": today})
    page.request.post("/api/tasks", data={"title": "Due soon", "due_date": soon})
    page.goto("/tasks?view=open&group=day")
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
    page.request.post("/api/tasks", data={"title": "Late task",
                                         "due_date": (date.today() - timedelta(days=2)).isoformat()})
    page.goto("/tasks?view=open&group=day")
    card(page, "Late task").drag_to(page.locator(f".board-day-divider[data-drop-day='{today}']"))
    wait_for_field(page, "Late task", "start_date", today)
    expect(page.locator("#task-deadline-modal")).to_be_hidden()
    expect(page.locator(".board-day-overdue")).to_have_count(0)


def test_unplanned_overdue_count_lists_them_even_folded(page, wiki):
    page.request.post("/api/tasks", data={"title": "Late", "due_date": "2020-01-01"})
    page.request.post("/api/tasks", data={"title": "Loose"})
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
    (wiki.root / "_tasks" / f"closed-{year-1}.json").write_text(
        '[{"id": "old", "title": "Old done", "closed_at": "%d-05-01T10:00:00"}]' % (year - 1),
        encoding="utf-8")
    tid = page.request.post("/api/tasks", data={"title": "New done"}).json()["id"]
    page.request.put(f"/api/tasks/{tid}", data={"completed": True})

    page.goto("/tasks?view=today")
    page.locator("#task-closed-link").click()
    years = page.locator(".closed-years button")
    expect(years).to_have_text([str(year), str(year - 1)])
    expect(years.first).to_have_attribute("aria-pressed", "true")
    expect(card(page, "New done")).to_be_visible()
    expect(card(page, "Old done")).to_have_count(0)

    years.nth(1).click()
    expect(card(page, "Old done")).to_be_visible()
    expect(card(page, "New done")).to_have_count(0)

    # Back to a tab and to Completed again: this year.
    page.locator("#task-closed-link").click()
    page.locator("#task-closed-link").click()
    expect(card(page, "New done")).to_be_visible()
    expect(card(page, "Old done")).to_have_count(0)

    # The search result's link opens the task in its year.
    href = next(r["href"] for r in page.request.get("/api/search?q=old+done").json()
                if r.get("task_id") == "old")
    page.goto(href)
    expect(page.locator(".task-item[data-id='old']")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(page.get_by_text("That task is no longer in your tasks.")).to_have_count(0)


# ── Locale ───────────────────────────────────────────────────────────────────

GERMAN_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                 "August", "September", "Oktober", "November", "Dezember"]


def test_locale_formats_dates_and_the_calendar(page, wiki, monkeypatch):
    # locale: dates in German, a calendar with German heads from Monday,
    # and the task chips the server writes in English rewritten in the page.
    monkeypatch.setattr(settings, "LOCALE", "de-DE")
    long_ago = date(date.today().year - 1, 3, 4)
    t = page.request.post("/api/tasks", data={"title": "Started last year", "start_date": long_ago.isoformat(),
                                              "due_date": (date.today() + timedelta(days=400)).isoformat()}).json()
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    open_tab(page, "focus")
    expect(card(page, "Started last year").locator(".task-start-info")).to_have_text(
        f"📅 since 4. März {long_ago.year}")

    modal = open_edit(page, "Started last year")
    modal.locator("[data-date-trigger='task-edit-due']").click()
    picker = page.locator(".tasks-datepicker")
    expect(picker.locator(".dp-weekdays span")).to_have_text(["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"])
    expect(picker.locator(".dp-title")).to_have_text(re.compile(rf"^({'|'.join(GERMAN_MONTHS)}) \d{{4}}$"))
    page.keyboard.press("Escape")

    wiki.write("notes", f"# Notes\n\n[[task:{t['id'][:8]}]]\n")
    page.goto("/page/notes")
    expect(page.locator(".task-ref-due time")).to_have_text(re.compile(r"^\d+\. \S+ \d{4}$"))


def test_iso_date_format(page, wiki, monkeypatch):
    # date-format = iso: YYYY-MM-DD on cards, the board and in chips; the
    # locale still names the weekdays and starts the week.
    monkeypatch.setattr(settings, "LOCALE", "en-US")
    monkeypatch.setattr(settings, "DATE_FORMAT", "iso")
    start = date.today() - timedelta(days=5)
    t = page.request.post("/api/tasks", data={"title": "Started", "start_date": start.isoformat(),
                                              "due_date": (date.today() + timedelta(days=40)).isoformat()}).json()
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.goto("/page/tasks?view=open&group=none")
    open_tab(page, "focus")
    expect(card(page, "Started").locator(".task-start-info")).to_have_text(f"📅 since {start.isoformat()}")
    in3 = date.today() + timedelta(days=3)
    open_tab(page, "plan")
    expect(page.locator(".board-day-label", has_text=in3.isoformat())).to_have_count(1)

    modal = open_edit(page, "Started")
    modal.locator("[data-date-trigger='task-edit-due']").click()
    expect(page.locator(".tasks-datepicker .dp-weekdays span").first).to_have_text("Su")
    page.keyboard.press("Escape")

    wiki.write("notes", f"# Notes\n\n[[task:{t['id'][:8]}]]\n")
    page.goto("/page/notes")
    expect(page.locator(".task-ref-due time")).to_have_text(
        (date.today() + timedelta(days=40)).isoformat())


# ── Start after the deadline ─────────────────────────────────────────────────

def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _pick(page, d: str):
    """Click date `d` in the open date picker (conftest.pick_date)."""
    pick_date(page.locator(".tasks-datepicker"), d)


def test_modal_warns_when_start_is_after_due_and_offers_a_fix(page, wiki):
    page.request.post("/api/tasks", data={"title": "Report", "due_date": _day(2)})
    page.goto("/tasks?view=open&group=none")
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
    page.request.post("/api/tasks", data={"title": "Due today", "due_date": _day(0)})
    page.request.post("/api/tasks", data={"title": "Late task", "due_date": _day(-1)})
    page.goto("/tasks?view=open&group=none")
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


def test_comment_paste_of_a_url_asks_for_its_name(page, wiki):
    page.request.post("/api/tasks", data={"title": "Link in comment"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Link in comment")
    field = modal.locator("#task-edit-comment")
    modal.get_by_role("button", name="Write a comment…").click()
    field.press_sequentially("See ")
    page.evaluate(PASTE, "https://example.com/rfc")
    page.locator("#url-name-input").fill("the RFC")
    page.locator("#url-name-input").press("Enter")
    expect(field).to_have_value("See [the RFC](https://example.com/rfc)")
    expect(field).to_be_focused()
    field.press("Control+Enter")               # saves it
    expect(modal.locator(".task-comment-list li .task-comment-text a")).to_have_text(["the RFC"])


def test_comments_in_edit_modal(page, wiki):
    page.request.post("/api/tasks", data={"title": "Plan on-call"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Plan on-call")
    field = modal.locator("#task-edit-comment")
    add = modal.get_by_role("button", name="Write a comment…")
    # The field shows only once it is asked for.
    expect(field).to_be_hidden()
    add.click()
    expect(field).to_be_focused()
    expect(add).to_be_hidden()
    field.fill("asked Alex")
    field.press("Control+Enter")               # saves the comment, doesn't submit the form
    expect(modal).to_be_visible()
    items = modal.locator(".task-comment-list li")
    expect(items.locator(".task-comment-text")).to_have_text(["asked Alex"])
    expect(field).to_be_hidden()               # put away again
    expect(add).to_be_focused()
    add.click()
    expect(field).to_have_value("")
    field.fill("6 people")
    modal.get_by_role("button", name="Add", exact=True).click()
    expect(items.locator(".task-comment-text")).to_have_text(["6 people", "asked Alex"])   # newest first
    # Esc (or its Cancel) puts the field away with what it held, not the modal.
    add.click()
    field.fill("never mind")
    field.press("Escape")
    expect(field).to_be_hidden()
    expect(modal).to_be_visible()
    add.click()
    expect(field).to_have_value("")
    modal.locator("#task-edit-comment-cancel").click()
    expect(field).to_be_hidden()
    expect(items).to_have_count(2)
    # Saved at once: Cancel keeps them, and the card shows the count.
    modal.locator(".task-edit-actions").get_by_role("button", name="Cancel").click()
    wait_for_field(page, "Plan on-call", "comments",
                   task_by_title(page, "Plan on-call")["comments"])
    assert [c["text"] for c in task_by_title(page, "Plan on-call")["comments"]] == ["asked Alex", "6 people"]
    badge = card(page, "Plan on-call").locator(".task-comments-btn")
    expect(badge).to_have_text("💬 2")
    assert badge.get_attribute("title").endswith(": 6 people")
    # The badge opens them under the card's line, newest first; the modal
    # stays closed.
    badge.click()
    expect(card(page, "Plan on-call").locator(".task-card-comment")).to_have_text(
        [re.compile("6 people"), re.compile("asked Alex")])
    expect(modal).to_be_hidden()
    modal = open_edit(page, "Plan on-call")
    # Edit in place: the field takes the comment's place, with its text.
    # Esc cancels without closing the modal, Ctrl+Enter (or Save) saves.
    items.first.hover()
    items.first.get_by_role("button", name="Edit comment").click()
    edit = items.first.locator("#task-edit-comment")
    expect(edit).to_have_value("6 people")
    expect(edit).to_be_focused()
    expect(items.first.locator(".task-comment-text")).to_be_hidden()
    edit.fill("nope")
    edit.press("Escape")
    expect(modal).to_be_visible()
    expect(items.first.locator(".task-comment-text")).to_have_text("6 people")
    items.first.hover()
    items.first.get_by_role("button", name="Edit comment").click()
    edit.fill("6 people, maybe 7")
    edit.press("Control+Enter")
    expect(items.first.locator(".task-comment-text")).to_have_text("6 people, maybe 7")
    items.last.hover()
    items.last.get_by_role("button", name="Edit comment").click()
    items.last.locator("#task-edit-comment").fill("asked Alex twice")
    expect(modal.locator("#task-edit-comment-add")).to_have_text("Save")
    modal.locator("#task-edit-comment-add").click()
    expect(items.last.locator(".task-comment-text")).to_have_text("asked Alex twice")
    items.last.hover()
    items.last.get_by_role("button", name="Edit comment").click()
    items.last.locator("#task-edit-comment").fill("asked Alex")
    items.last.locator("#task-edit-comment").press("Control+Enter")
    expect(items.last.locator(".task-comment-text")).to_have_text("asked Alex")
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
    modal.locator(".task-edit-actions").get_by_role("button", name="Cancel").click()
    expect(modal).to_be_hidden()
    page.locator(".task-undo-btn").click()
    expect(card(page, "Plan on-call").locator(".task-comments-btn")).to_have_text("💬 2")
    assert len(task_by_title(page, "Plan on-call")["comments"]) == 2


def test_comment_of_several_lines(page, wiki):
    # A comment is markdown, as the notes are: paragraphs, lists, code.
    page.request.post("/api/tasks", data={"title": "Paint the fence"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Paint the fence")
    modal.get_by_role("button", name="Write a comment…").click()
    field = modal.locator("#task-edit-comment")
    field.press_sequentially("Bought:")
    field.press("Enter")                       # a new line, not a save
    field.press("Enter")
    field.press_sequentially("- **two** brushes")
    field.press("Enter")                       # the list goes on, as in the editor
    field.press_sequentially("primer")
    expect(field).to_have_value("Bought:\n\n- **two** brushes\n- primer")
    expect(modal.locator(".task-comment-list li")).to_have_count(0)
    field.press("Control+Enter")
    body = modal.locator(".task-comment-list li .task-comment-text")
    expect(body.locator("p")).to_have_text("Bought:")
    expect(body.locator("li")).to_have_text(["two brushes", "primer"])
    expect(body.locator("strong")).to_have_text("two")
    assert task_by_title(page, "Paint the fence")["comments"][0]["text"] == "Bought:\n\n- **two** brushes\n- primer"
    # The card: its time on a line, the text under it; a one-line comment
    # stays on the line of its time.
    modal.get_by_role("button", name="Write a comment…").click()
    field.fill("done")
    modal.get_by_role("button", name="Add", exact=True).click()
    expect(modal.locator(".task-comment-list > li")).to_have_count(2)
    # A line between two comments, none above the first.
    listed = modal.locator(".task-comment-list > li")
    expect(listed.first).to_have_css("border-top-width", "0px")
    expect(listed.last).to_have_css("border-top-width", "1px")
    expect(listed.last).to_have_css("border-top-style", "solid")
    modal.locator(".task-edit-actions").get_by_role("button", name="Cancel").click()
    shown = open_details(page, "Paint the fence").locator(".task-card-comment")
    expect(shown).to_have_count(2)
    # On the card: a bar down each comment's left, a gap between two.
    for one in (shown.first, shown.last):
        expect(one).to_have_css("border-left-width", "3px")
        expect(one).to_have_css("border-top-width", "0px")
    first, last = shown.first.bounding_box(), shown.last.bounding_box()
    assert last["y"] - (first["y"] + first["height"]) >= 5
    expect(shown.first).to_have_js_property("tagName", "P")
    expect(shown.last.locator(".task-comment-body li")).to_have_text(["two brushes", "primer"])
    when, text = shown.last.locator(".task-comment-when"), shown.last.locator(".task-comment-body")
    assert text.bounding_box()["y"] >= when.bounding_box()["y"] + when.bounding_box()["height"] - 1


def test_comment_in_its_field_is_saved_with_the_task(page, wiki):
    # Save with a comment still in its field: the comment is not lost.
    page.request.post("/api/tasks", data={"title": "Call Bob"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Call Bob")
    modal.get_by_role("button", name="Write a comment…").click()
    modal.locator("#task-edit-comment").fill("left a voicemail")
    modal.locator("#task-edit-title").fill("Call Bob again")
    modal.locator(".task-edit-actions").get_by_role("button", name="Save").click()
    expect(modal).to_be_hidden()
    task = task_by_title(page, "Call Bob again")
    assert [c["text"] for c in task["comments"]] == ["left a voicemail"]


def _tab_stops(page, modal, limit=30):
    """What Tab focuses in the dialog (its id, or what names it), from where the
    focus is until it is back there. Between the last and the first the focus
    may leave the fields (the dialog itself, the browser): no stop."""
    name = """() => { const el = document.activeElement;
        return el.closest('form') ? el.id || el.dataset.dateTrigger || el.name || el.textContent.trim() : ''; }"""
    stops = [page.evaluate(name)]
    for _ in range(limit):
        page.keyboard.press("Tab")
        at = page.evaluate(name)
        if at == stops[0]:
            break
        if at:
            stops.append(at)
    return stops


def test_tab_in_the_task_dialog_goes_from_field_to_field(page, wiki):
    # Not through Edit | Preview, the formatting bar, Write a comment…, a
    # comment's ✎ and ×, links in comments, or History.
    tid = page.request.post("/api/tasks", data={"title": "Plan trip", "description": "notes",
                                                "due_date": "2026-12-24"}).json()["id"]
    page.request.post(f"/api/tasks/{tid}/comments", data={"text": "see [the site](https://example.com)\n\n- [ ] one"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Plan trip")
    expect(modal.locator("#task-edit-title")).to_be_focused()
    # The fields the task has, then the line that offers the others.
    fields = ["task-edit-title", "task-edit-desc", "task-edit-tags", "task-edit-due", "task-edit-start", "task-edit-importance",
              "task-edit-more-repeat", "task-edit-more-lead", "task-edit-more-keep", "Save", "task-edit-cancel", "task-edit-wontdo"]
    stops = _tab_stops(page, modal)
    assert stops == fields, stops
    # And back the same way.
    modal.locator("#task-edit-tags").focus()
    page.keyboard.press("Shift+Tab")
    expect(modal.locator("#task-edit-desc")).to_be_focused()
    # The comment field, once asked for (Alt+C), is a field: the last one, before Save.
    modal.locator("#task-edit-desc").focus()
    page.keyboard.press("Alt+c")
    comment = modal.locator("#task-edit-comment")
    save = modal.get_by_role("button", name="Save", exact=True)
    expect(comment).to_be_focused()
    page.keyboard.press("Tab")
    expect(save).to_be_focused()                                     # nothing written: Add is no stop
    page.keyboard.press("Shift+Tab")
    expect(comment).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(modal.locator("#task-edit-more-keep")).to_be_focused()
    comment.focus()
    page.keyboard.type("called Bob")
    page.keyboard.press("Tab")
    expect(modal.locator("#task-edit-comment-add")).to_be_focused()  # with a comment to add, it is
    page.keyboard.press("Enter")
    expect(modal.locator(".task-comment-list > li")).to_have_count(2)
    page.keyboard.press("Tab")
    expect(save).to_be_focused()
    # A new task: the same, without Won't do.
    modal.locator("#task-edit-cancel").click()
    page.locator(".tasks-app").get_by_role("button", name="New task").click()
    stops = _tab_stops(page, modal)
    # Without a due date nothing can start before it: Days before due isn't offered.
    off = ("task-edit-more-lead", "task-edit-wontdo")
    assert stops == [f for f in fields if f not in off], stops
    page.keyboard.press("Alt+c")                                     # no comments on a task not yet made
    expect(comment).to_be_hidden()


def test_repeat_stays_where_it_is_when_a_repeat_is_chosen(page, wiki):
    # Repeat has a line of its own under the dates: choosing a repeat adds
    # Every and From done beside it and moves nothing (it used to open on the
    # dates' line and jump to the next with its two fields).
    page.request.post("/api/tasks", data={"title": "Water plants", "due_date": "2099-01-10"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Water plants")
    modal.locator("#task-edit-more-repeat").click()
    rec = modal.locator("#task-edit-rec-type")
    box = lambda loc: loc.locator("xpath=ancestor::label").bounding_box()
    due = modal.locator("[data-date-trigger=task-edit-due]").locator("xpath=ancestor::label").bounding_box()
    before = box(rec)
    assert before["x"] == due["x"] and before["y"] >= due["y"] + due["height"], (before, due)
    rec.select_option("weekly")
    every = modal.locator("#task-edit-rec-interval")
    expect(every).to_be_visible()
    after = box(rec)
    # (Within a pixel: the line is a hair taller with a number field on it.)
    assert after["x"] == before["x"] and abs(after["y"] - before["y"]) < 1, (before, after)
    assert abs(box(every)["y"] - after["y"]) < 1 and box(every)["x"] > after["x"]   # beside it, on its line


def test_description_is_close_to_the_title(page, wiki):
    # "Description" is about as far under the title's field (and, on a new
    # task, the line saying where it goes) as "Tags" is under the description.
    page.goto("/tasks?view=open&group=none")
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    bottom = lambda sel: (lambda b: b["y"] + b["height"])(modal.locator(sel).bounding_box())
    top = lambda sel: modal.locator(sel).bounding_box()["y"]
    expect(modal.locator("#task-edit-target")).to_be_visible()
    tags_gap = top("label[for=task-edit-tags]") - bottom("#task-edit-desc")
    assert 0 < top("label[for=task-edit-desc]") - bottom("#task-edit-target") <= tags_gap + 6
    modal.locator("#task-edit-cancel").click()
    page.request.post("/api/tasks", data={"title": "Plain"})
    page.reload()
    open_edit(page, "Plain")                           # editing: no such line
    expect(modal.locator("#task-edit-target")).to_be_hidden()
    assert 0 < top("label[for=task-edit-desc]") - bottom("#task-edit-title") <= tags_gap + 6


def test_the_dialog_shows_rare_fields_when_set_or_asked_for(page, wiki):
    # Repeat, Days before due and Keep after done are no part of a plain
    # task's dialog: the line under the fields offers them.
    page.request.post("/api/tasks", data={"title": "Water plants", "due_date": "2099-01-10"})
    page.request.post("/api/tasks", data={"title": "Milk", "keep": True})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Water plants")
    rec, every, from_done = (modal.locator(f"#task-edit-rec-{f}") for f in ("type", "interval", "from"))
    keep, more = modal.locator("#task-edit-keep"), lambda f: modal.locator(f"#task-edit-more-{f}")
    for field in (rec, every, from_done, keep, modal.locator("#task-edit-lead")):
        expect(field).to_be_hidden()
    expect(modal.locator(".task-add-field:visible")).to_have_text(["Repeat", "Days before due", "Keep after done"])
    # Asked for, Repeat shows and has the focus; Every and From done come with a type.
    more("repeat").click()
    expect(rec).to_be_focused()
    expect(more("repeat")).to_be_hidden()
    expect(every).to_be_hidden()
    rec.select_option("weekly")
    expect(every).to_be_visible()
    expect(from_done).to_be_visible()
    expect(modal.locator("#task-edit-rec-unit")).to_have_text("week")
    every.fill("2")
    expect(modal.locator("#task-edit-rec-unit")).to_have_text("weeks")
    # Emptied again, the field stays while the dialog is open.
    rec.select_option("")
    expect(rec).to_be_visible()
    expect(every).to_be_hidden()
    rec.select_option("weekly")
    modal.locator(".task-edit-actions").get_by_role("button", name="Save").click()
    wait_until(page, lambda: (task_by_title(page, "Water plants").get("recurrence") or {}).get("interval") == 2)
    # A task that repeats opens with it; one that doesn't, without.
    modal = open_edit(page, "Water plants")
    expect(rec).to_have_value("weekly")
    expect(every).to_have_value("2")
    expect(more("repeat")).to_be_hidden()
    # Keep after done: asked for, it is ticked, and Repeat is off, with the reason.
    more("keep").click()
    expect(keep).to_be_checked()
    expect(keep).to_be_focused()
    expect(rec).to_be_disabled()
    expect(modal.locator("#task-edit-rec-note")).to_have_text("A keep task does not repeat.")
    page.keyboard.press("Escape")
    # A keep task shows its box, and is offered no Repeat.
    modal = open_edit(page, "Milk")
    expect(keep).to_be_visible()
    expect(keep).to_be_checked()
    expect(modal.locator(".task-add-field:visible")).to_have_count(0)
    expect(modal.locator("#task-edit-more")).to_be_hidden()
    keep.uncheck()
    expect(modal.locator(".task-add-field:visible")).to_have_text(["Repeat"])
    # Without a date Repeat shows, off, and says what it needs.
    more("repeat").click()
    expect(rec).to_be_disabled()
    expect(modal.locator("#task-edit-rec-note")).to_have_text("To repeat, a task needs a due or start date.")


def test_the_description_grows_and_has_its_bar_while_written_in(page, wiki):
    page.goto("/tasks?view=open&group=none")
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    notes, bar = modal.locator("#task-edit-desc"), modal.locator("#task-edit-format")
    label, mode = modal.locator("label[for=task-edit-desc]"), modal.locator(".task-notes-mode")
    # Empty and not written in: its label, no bar, nothing to preview.
    expect(bar).to_be_hidden()
    expect(mode).to_be_hidden()
    empty = notes.bounding_box()
    assert empty["height"] < 70, empty
    # With the focus the bar takes the label's place, whole, and nothing moves.
    notes.click()
    expect(bar).to_be_visible()
    expect(mode).to_be_visible()
    assert label.bounding_box()["width"] <= 1                          # there for screen readers
    assert bar.evaluate("b => b.scrollWidth <= b.clientWidth")
    assert notes.bounding_box() == empty
    # As tall as its text, without a scrollbar of its own.
    page.keyboard.type("one\ntwo\nthree\nfour\nfive\nsix")
    grown = notes.bounding_box()["height"]
    assert grown > empty["height"] + 50, grown
    assert notes.evaluate("ta => ta.scrollHeight <= ta.clientHeight")
    # Up to a point: then it scrolls, and the dialog's buttons stay in reach.
    notes.fill("\n".join(f"line {i}" for i in range(80)))
    tall = notes.bounding_box()["height"]
    assert grown < tall <= page.viewport_size["height"] * 0.45 + 1, tall
    assert notes.evaluate("ta => ta.scrollHeight > ta.clientHeight")
    # Text taken out again: as small as it was.
    notes.fill("")
    assert notes.bounding_box()["height"] == empty["height"]
    # Left with text in it: the label is back, and Preview stays.
    notes.fill("**done**")
    modal.locator("#task-edit-title").focus()
    expect(bar).to_be_hidden()
    expect(mode).to_be_visible()
    assert label.bounding_box()["width"] > 20
    # A task with a long description opens with its field that tall.
    page.keyboard.press("Escape")
    page.request.post("/api/tasks", data={"title": "Long", "description": "a\n\nb\n\nc\n\nd"})
    page.reload()
    modal = open_edit(page, "Long")
    assert notes.evaluate("ta => ta.scrollHeight <= ta.clientHeight")
    assert notes.bounding_box()["height"] > empty["height"] + 50


def test_importance_shows_before_the_title_and_is_set_four_ways(page, wiki):
    page.request.post("/api/tasks", data={"title": "Tax return", "importance": "high"})
    page.request.post("/api/tasks", data={"title": "Sort photos", "importance": "low"})
    page.request.post("/api/tasks", data={"title": "Water plants"})
    page.goto("/tasks?view=open&group=none")
    mark = lambda title: card(page, title).locator(".task-importance")
    expect(mark("Tax return")).to_have_text("!")
    expect(mark("Tax return")).to_have_attribute("title", "High importance")
    expect(mark("Sort photos")).to_have_text("↓")
    expect(mark("Water plants")).to_have_count(0)                     # normal says nothing
    # Before the title, on its line.
    box, title = mark("Tax return").bounding_box(), card(page, "Tax return").locator(".task-title").bounding_box()
    assert box["x"] < title["x"] and abs(box["y"] - title["y"]) < 6
    expect(mark("Tax return")).to_be_visible()
    # The dialog's field.
    modal = open_edit(page, "Water plants")
    expect(modal.locator("#task-edit-importance")).to_have_value("")
    modal.locator("#task-edit-importance").select_option("high")
    modal.locator(".task-edit-actions").get_by_role("button", name="Save").click()
    expect(mark("Water plants")).to_have_text("!")
    modal = open_edit(page, "Tax return")
    expect(modal.locator("#task-edit-importance")).to_have_value("high")
    modal.locator("#task-edit-importance").select_option("")
    modal.locator(".task-edit-actions").get_by_role("button", name="Save").click()
    expect(mark("Tax return")).to_have_count(0)
    assert "importance" not in task_by_title(page, "Tax return")
    # The ⋯ menu: Importance…, then the level; the task's own is ticked.
    card(page, "Sort photos").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Importance…").click()
    expect(page.locator(".task-menu").get_by_role("menuitem")).to_have_text(["High", "Normal", "Low ✓"])
    page.get_by_role("menuitem", name="High").click()
    expect(mark("Sort photos")).to_have_text("!")
    # The key: i, in turn high, low, normal; several selected take the first one's next.
    card(page, "Tax return").locator(".task-title").click()
    page.keyboard.press("i")
    expect(mark("Tax return")).to_have_text("!")
    page.keyboard.press("i")
    expect(mark("Tax return")).to_have_text("↓")
    page.keyboard.press("i")
    expect(mark("Tax return")).to_have_count(0)
    page.keyboard.press("Control+a")
    page.keyboard.press("i")
    expect(page.locator(".task-importance-high")).to_have_count(3)
    assert {t["title"]: t.get("importance") for t in page.request.get("/api/tasks").json()} == {
        "Tax return": "high", "Sort photos": "high", "Water plants": "high"}


def test_importance_in_quick_add_and_the_filter(page, wiki):
    page.request.post("/api/tasks", data={"title": "Plain one"})
    page.goto("/tasks?view=open&group=none")
    page.locator(".tasks-app").get_by_role("button", name="New task").click()
    modal = page.locator("#task-edit-modal")
    modal.locator("#task-edit-title").fill("Call the bank ! #money")
    expect(modal.locator("#task-edit-quick")).to_contain_text("high importance")
    modal.locator("#task-edit-title").press("Enter")
    expect(card(page, "Call the bank").locator(".task-importance-high")).to_have_count(1)
    assert task_by_title(page, "Call the bank")["importance"] == "high"
    page.locator(".tasks-app").get_by_role("button", name="New task").click()
    modal.locator("#task-edit-title").fill("Tidy desk !low")
    modal.locator("#task-edit-title").press("Enter")
    expect(card(page, "Tidy desk").locator(".task-importance-low")).to_have_count(1)
    # The filter field: !high (or !), !low.
    search = page.locator("#task-filter-search")
    search.fill("!high")
    expect(page.locator(".task-item .task-title")).to_have_text(["Call the bank"])
    search.fill("!low")
    expect(page.locator(".task-item .task-title")).to_have_text(["Tidy desk"])
    search.fill("")
    expect(page.locator(".task-item")).to_have_count(3)


def test_new_task_has_no_comments_field(page, wiki):
    page.goto("/tasks?view=open&group=none")
    page.locator(".tasks-app").get_by_role("button", name="New task").click()
    expect(page.locator("#task-edit-comments")).to_be_hidden()


def test_date_picker_shortcuts_move_the_start_date(page, wiki):
    page.request.post("/api/tasks", data={"title": "Shift me", "start_date": date.today().isoformat()})
    page.goto("/tasks?view=open&group=none")
    chip = card(page, "Shift me").locator(".task-start-info")
    for label, days in (("Tomorrow", 1), ("In 2 days", 2), ("In a week", 7), ("Today", 0),
                        ("Next Monday", (7 - date.today().weekday()) or 7)):
        before = chip.inner_text()
        chip.click()
        page.locator(".tasks-datepicker").get_by_role("button", name=label, exact=True).click()
        expect(page.locator(".tasks-datepicker")).to_have_count(0)
        wait_for_field(page, "Shift me", "start_date", (date.today() + timedelta(days=days)).isoformat())
        # The page's copy of the task (which the picker marks the current
        # value from) changes after the server's: wait for the card to show
        # it, or under load the next click opens the picker on the old date.
        expect(chip).not_to_have_text(before)
    # The current value is marked, and the tooltip names the day.
    chip.click()
    btn = page.locator(".tasks-datepicker").get_by_role("button", name="Next Monday")
    expect(btn).to_have_class(re.compile(r"\bdp-selected\b"))
    assert btn.get_attribute("title").startswith("Next Monday: Mon")
    # All five fit on one row, also at finger size (test_touch covers the phone).
    quick = page.locator(".dp-quick-btn")
    expect(quick).to_have_text(["Today", "Tmrw", "+2d", "Mon", "+1w"])
    assert quick.evaluate_all("bs => bs.every(b => b.scrollWidth <= b.clientWidth)")
    assert len(set(quick.evaluate_all("bs => bs.map(b => Math.round(b.getBoundingClientRect().top))"))) == 1



def test_new_param_opens_one_modal_on_a_page_with_several_taskss(page, wiki):
    # Regression: every embed took ?new=1. One does: the Inbox's when the
    # page shows it (as New task and c t do), else the first one.
    list_page(page, wiki, "lists", "# Lists\n\n[[tasks: list=shop]]\n\n[[tasks: list=inbox]]\n")
    page.goto("/page/lists?new=1")
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_have_count(1)
    expect(modal.locator("select[id^=task-edit-list]")).to_have_value("")
    expect(modal.locator(".task-edit-target")).to_have_text("Adds to the Inbox.")
    page.keyboard.press("Escape")
    wiki.write("lists", "# Lists\n\n[[tasks: list=shop]]\n\n[[tasks: tag=later]]\n")
    page.goto("/page/lists?new=1")
    expect(modal).to_have_count(1)
    expect(modal.locator("select[id^=task-edit-list]")).to_have_value("shop")


def test_section_wikilinks_in_task_notes(page, wiki):
    # tasks.js renders [[Page#Section]] like the server: the heading's anchor,
    # "Page › Section" as label; [[Page#Section|label]] keeps the label.
    wiki.write("garden", "# Garden\n\n## Raised beds\n")
    page.request.post("/api/tasks", data={"title": "Weed", "description": "See [[Garden#Raised beds]] and [[Garden#Raised beds|there]]."})
    page.goto("/tasks?view=open&group=none")
    expect(page.locator(".task-item a[href='/page/garden#raised-beds']")).to_have_text(["Garden › Raised beds", "there"])


def test_external_links_in_tasks_open_a_new_tab(page, wiki):
    page.request.post("/api/tasks", data={"title": "Read https://example.com/a",
                                          "description": "[docs](https://example.org) and https://example.net/x and [[Home]]"})
    page.goto("/tasks?view=open&group=none")
    item = page.locator(".task-item").first
    for href in ("https://example.com/a", "https://example.org", "https://example.net/x"):
        link = item.locator(f'a[href="{href}"]')
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(item.locator('a[href="/page/home"]')).not_to_have_attribute("target", "_blank")


# ── One line per task ────────────────────────────────────────────────────────

def _line_tasks(page):
    page.request.post("/api/tasks", data={"title": "Renew passport", "tags": "personal,admin,travel",
                                          "due_date": "2026-11-15", "description": "Photos first.\nBring the old one."})
    page.request.post("/api/tasks", data={"title": "Plain"})


def test_one_line_per_task(page, wiki):
    # Every tab shows one line per task, and nothing switches to full cards:
    # not v, which once did, nor a choice an earlier version remembered.
    _line_tasks(page)
    page.goto("/tasks?view=open&group=none")
    page.evaluate("""() => { for (const view of ['focus', 'open', 'plan', 'closed'])
        localStorage.setItem('zone-tasks-compact:/tasks|||0|' + view, '0'); }""")
    page.reload()
    passport = card(page, "Renew passport")
    for view in ("focus", "plan", "closed", "open"):
        open_tab(page, view)
        if not passport.count():
            continue
        # One line: title, tags, the notes marker and the deadline side by side.
        box = passport.bounding_box()
        assert box["height"] < 45, view
        expect(passport.locator(".task-desc")).to_be_hidden()
        for sel in (".task-title", ".task-tags-right .task-tag >> nth=0", ".task-due"):
            b = passport.locator(sel).bounding_box()
            assert box["y"] <= b["y"] and b["y"] + b["height"] <= box["y"] + box["height"], sel
    page.locator("body").click(position={"x": 5, "y": 300})
    page.keyboard.press("v")
    expect(passport.locator(".task-desc")).to_be_hidden()
    assert passport.bounding_box()["height"] < 45
    expect(page.locator("#shortcuts-modal, .shortcuts-modal").get_by_text("full cards")).to_have_count(0)


def test_phone_cards_show_every_tag_never_clip_dates(new_page, wiki):
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _line_tasks(phone)
    phone.request.post("/api/tasks", data={"title": "Pack for the trip", "due_date": "2026-11-15",
                                          "tags": "admin,travel,family,urgent,paperwork,portugal"})
    phone.goto("/tasks?view=open&group=none")
    tid = task_by_title(phone, "Renew passport")["id"]
    phone.request.put(f"/api/tasks/{tid}", data={"rec_type": "yearly", "rec_interval": 10})
    phone.request.post(f"/api/tasks/{tid}/comments", data={"text": "Shop closes at 6"})
    phone.request.post("/api/tasks", data={"title": "Review the canary dashboards carefully", "start_date": tomorrow()})
    phone.reload()
    expect(card(phone, "Pack for the trip")).to_be_visible()
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
    phone.request.post("/api/tasks", data={"title": "Pay rent", "tags": "home,money", "due_date": "2026-11-15"})
    phone.request.post("/api/tasks", data={"title": "Plain"})
    phone.goto("/tasks?view=open&group=none")
    expect(card(phone, "Pay rent")).to_be_visible()
    rent = card(phone, "Pay rent")
    title, menu = rent.locator(".task-content").bounding_box(), rent.locator(".task-menu-btn").bounding_box()
    meta = rent.locator(".task-meta").bounding_box()
    assert abs((menu["y"] + menu["height"] / 2) - (title["y"] + title["height"] / 2)) < 2, (menu, title)
    assert menu["width"] >= 40 and menu["height"] >= 40
    assert meta["y"] >= title["y"] + title["height"] - 1
    assert meta["x"] + meta["width"] > menu["x"]                    # the line runs under ⋯
    # A line with nothing but a title stays one finger-sized line.
    assert card(phone, "Plain").bounding_box()["height"] < 56


def test_every_card_shows_every_tag(page, wiki):
    # Short of room, the tags wrap onto more lines (the dates stay at the
    # first line's end): a line gets two lines rather than drop a tag. A long title doesn't push
    # them out, nor do they squeeze the title: both get their share.
    page.set_viewport_size({"width": 800, "height": 800})
    page.request.post("/api/tasks", data={"title": "Call the insurance about the broken window in the kitchen and the hallway",
                                         "tags": "home,insurance,money,calls,waiting,landlord,repairs",
                                         "due_date": tomorrow()})
    page.request.post("/api/tasks", data={"title": "Plain"})
    page.goto("/tasks?view=open&group=none")
    item = card(page, "Call the insurance about the broken window in the kitchen and the hallway")
    tags = item.locator(".task-tag")
    expect(tags).to_have_count(7)

    def check():
        box, due = item.bounding_box(), item.locator(".task-due").bounding_box()
        title = item.locator(".task-content").bounding_box()
        for tag in tags.all():
            b = tag.bounding_box()
            assert box["x"] <= b["x"] and b["x"] + b["width"] <= due["x"]     # whole, left of the dates
            assert b["x"] >= title["x"] + title["width"]                      # right of the title
            assert b["y"] + b["height"] <= box["y"] + box["height"]           # inside the card
        first = tags.first.bounding_box()
        assert tags.last.bounding_box()["y"] > first["y"]                    # wrapped
        assert abs((due["y"] + due["height"] / 2) - (first["y"] + first["height"] / 2)) < 2
        assert title["width"] > 200                                           # the title keeps its share
        return box

    page.mouse.move(0, 0)
    line = check()
    assert item.locator(".task-content").bounding_box()["height"] < 30    # the title is one line, cut off
    assert card(page, "Plain").bounding_box()["height"] < 40              # a line, as ever
    # Hovering shows the empty 📅 in room kept for it: nothing rewraps.
    item.locator(".task-content").hover()
    expect(item.locator(".task-date-empty").first).to_be_visible()
    assert item.bounding_box()["height"] == line["height"]


# ── Expand a task in place ───────────────────────────────────────────────────

LONG = ("Renew the passport before the trip to Lisbon in spring next year, with new photos "
        "from the shop on Main Street and the filled-in form")
EXPANDED = re.compile(r"\btask-expanded\b")
DETAILS = re.compile(r"\btask-details\b")


def _expand_setup(page):
    page.request.post("/api/tasks", data={"title": LONG, "tags": "admin", "due_date": "2026-11-15",
                                          "description": "At the shop.\n- [ ] old passport"})
    page.request.post("/api/tasks", data={"title": "Short with notes", "description": "Some notes."})
    page.request.post("/api/tasks", data={"title": "Plain"})
    page.goto("/tasks?view=open&group=none")


def test_line_selecting_shows_the_title_details_on_request(page, wiki):
    # Selecting a task shows its whole title (wrapped), not its
    # notes; → (or 📝) opens them, ← closes them; Enter opens the modal.
    _expand_setup(page)
    passport = card(page, LONG)
    one_line = passport.bounding_box()["height"]
    passport.locator(".task-content").click()                      # select: the whole title
    expect(passport).to_have_class(EXPANDED)
    expect(passport).not_to_have_class(DETAILS)
    expect(passport.locator(".task-desc")).to_be_hidden()
    titled = passport.bounding_box()["height"]
    assert titled > one_line
    expect(page.locator(".task-edit-modal[open]")).to_have_count(0)
    page.keyboard.press("ArrowRight")                               # the details
    expect(passport).to_have_class(DETAILS)
    expect(passport.locator(".task-desc")).to_be_visible()
    assert passport.bounding_box()["height"] > titled + 20
    # The notes' checkboxes work; the save re-renders, the details stay open.
    passport.locator(".task-desc .task-list-item-checkbox").check()
    expect(card(page, LONG).locator(".task-desc .task-date-done")).to_be_visible()
    assert task_by_title(page, LONG)["description"].startswith("At the shop.\n- [x] old passport <!--d:")
    expect(card(page, LONG)).to_have_class(DETAILS)
    # The tasks ignores its keys while an input has focus: the ticked box
    # keeps it until the save re-renders the card.
    expect(page.locator(".task-list-item-checkbox:focus")).to_have_count(0)
    page.keyboard.press("ArrowLeft")                                # closes them, the title stays whole
    expect(passport).not_to_have_class(DETAILS)
    expect(passport).to_have_class(EXPANDED)
    expect(passport.locator(".task-desc")).to_be_hidden()
    # 📝 toggles them too.
    passport.locator(".task-has-notes").click()
    expect(passport.locator(".task-desc")).to_be_visible()
    passport.locator(".task-has-notes").click()
    expect(passport.locator(".task-desc")).to_be_hidden()
    page.keyboard.press("Enter")                                    # the modal
    expect(page.locator(".task-edit-modal[open] [id^=task-edit-title]")).to_have_value(LONG)
    page.keyboard.press("Escape")
    # Esc collapses (the selection stays); Enter still opens the modal.
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Escape")
    expect(passport).not_to_have_class(EXPANDED)
    expect(passport).not_to_have_class(DETAILS)
    expect(passport).to_have_class(re.compile(r"\btask-selected\b"))
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()
    page.keyboard.press("Escape")
    # Moving the selection expands the newly selected task when its title
    # is cut off; a short title with notes stays a line until →.
    # (Newest first: "Short with notes" is above.)
    page.keyboard.press("k")
    short = card(page, "Short with notes")
    expect(short).to_have_class(re.compile(r"\btask-selected\b"))
    expect(page.locator(".task-expanded")).to_have_count(0)
    expect(short.locator(".task-desc")).to_be_hidden()
    page.keyboard.press("ArrowRight")
    expect(short.locator(".task-desc")).to_be_visible()
    # Open details stay open on the way: the next selected task shows its
    # own (the previous one's close), until they are closed.
    page.keyboard.press("j")
    expect(short.locator(".task-desc")).to_be_hidden()
    expect(passport).to_have_class(DETAILS)
    expect(passport.locator(".task-desc")).to_be_visible()
    expect(page.locator(".task-expanded.task-selected")).to_have_count(1)
    page.keyboard.press("j")                                            # Plain: nothing to show
    expect(page.locator(".task-details")).to_have_count(0)
    page.keyboard.press("k")                                            # still on
    expect(passport.locator(".task-desc")).to_be_visible()
    page.keyboard.press("h")                                            # off
    page.keyboard.press("k")
    expect(short.locator(".task-desc")).to_be_hidden()
    expect(page.locator(".task-details")).to_have_count(0)
    # Esc turns it off too.
    page.keyboard.press("l")
    expect(short.locator(".task-desc")).to_be_visible()
    page.keyboard.press("Escape")
    page.keyboard.press("j")
    expect(passport.locator(".task-desc")).to_be_hidden()


def test_line_expand_button_and_hl_keys(page, wiki):
    # A line with notes or comments has an expand button (a
    # chevron, left of ⋯): it opens and closes them; l and h do the same
    # from the keyboard, as → and ←. Lines without keep its room, so the
    # dates end at the same place.
    _expand_setup(page)
    short, plain = card(page, "Short with notes"), card(page, "Plain")
    btn = short.locator(".task-expand-btn")
    expect(btn).to_be_visible()
    expect(btn).to_have_attribute("aria-expanded", "false")
    expect(plain.locator(".task-expand-btn")).to_have_count(0)
    p_menu, s_menu = plain.locator(".task-menu-btn").bounding_box(), short.locator(".task-menu-btn").bounding_box()
    assert abs(p_menu["x"] - s_menu["x"]) < 1
    b = btn.bounding_box()
    assert b["x"] + b["width"] <= s_menu["x"] + 1                        # left of ⋯
    btn.click()
    expect(short.locator(".task-desc")).to_be_visible()
    expect(short).to_have_class(re.compile(r"\btask-selected\b"))
    expect(btn).to_have_attribute("aria-expanded", "true")
    btn.click()
    expect(short.locator(".task-desc")).to_be_hidden()
    expect(btn).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("l")
    expect(short.locator(".task-desc")).to_be_visible()
    expect(btn).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("h")
    expect(short.locator(".task-desc")).to_be_hidden()
    expect(short).to_have_class(re.compile(r"\btask-selected\b"))
    page.keyboard.press("j")                                             # hjkl: j moves on
    expect(short).not_to_have_class(re.compile(r"\btask-selected\b"))


def test_line_double_click_below_expanded_opens_that_card(page, wiki):
    # A press on the card below the open one doesn't close that one at
    # once (which would pull the card up under the pointer): the
    # double-click opens the card it started on, the other closes after.
    page.request.post("/api/tasks", data={"title": "Below", "description": "Some notes."})
    page.request.post("/api/tasks", data={"title": "Long notes", "description": "\n\n".join(f"Line {i}" for i in range(12))})
    # The open card closes 400 ms after the first press (COLLAPSE_DELAY_MS
    # in js/taskview.js), and this test looks twice in between: on a busy
    # machine that took longer, and the card closed under the double-click.
    # The page's clock stands still while the presses are looked at.
    page.clock.install()
    page.goto("/tasks?view=open&group=none")
    long_notes, below = card(page, "Long notes"), card(page, "Below")

    def open_details(item):
        item.locator(".task-content").click()
        page.keyboard.press("ArrowRight")
        expect(item).to_have_class(DETAILS)

    open_details(long_notes)
    for _ in range(2):                       # every time, not just the first
        still(page)
        box = below.locator(".task-content").bounding_box()
        page.mouse.move(box["x"] + 10, box["y"] + box["height"] / 2)
        # Each press of the double-click leaves the card where it was.
        top = box["y"]
        page.clock.pause_at(datetime.fromtimestamp(page.evaluate("() => Date.now()") / 1000 + 0.001))
        page.mouse.down()
        page.mouse.up()
        assert below.locator(".task-content").bounding_box()["y"] == top
        assert "task-details" in long_notes.get_attribute("class")
        page.mouse.down(click_count=2)
        assert below.locator(".task-content").bounding_box()["y"] == top
        assert "task-details" in long_notes.get_attribute("class")
        page.mouse.up(click_count=2)
        page.clock.resume()
        expect(page.locator(".task-edit-modal[open] [id^=task-edit-title]")).to_have_value("Below")
        page.keyboard.press("Escape")
        expect(long_notes).not_to_have_class(DETAILS)
        # The card closes with a 150ms height animation (finishCollapse): the
        # one below moves up as it runs, not with the class. Read at once,
        # under load, it hadn't moved yet.
        wait_until(page, lambda: below.locator(".task-content").bounding_box()["y"] < top)
        open_details(long_notes)
        page.wait_for_timeout(500)
    # A single click: the card stays put for the double-click window.
    top = below.bounding_box()["y"]
    below.locator(".task-content").click()
    assert below.bounding_box()["y"] == top
    expect(long_notes).not_to_have_class(DETAILS)
    # The card below closes the same way when the one above is clicked.
    page.keyboard.press("ArrowRight")
    expect(below).to_have_class(DETAILS)
    page.wait_for_timeout(500)
    long_notes.locator(".task-content").click()
    assert "task-details" in below.get_attribute("class")
    expect(below).not_to_have_class(DETAILS)


def test_line_task_with_nothing_more_stays_a_line(page, wiki):
    # A title that fits: selecting it changes nothing (no "expanded" card a
    # few pixels taller), notes or not; Enter opens it.
    _expand_setup(page)
    for title in ("Plain", "Short with notes"):
        item = card(page, title)
        before = item.bounding_box()
        item.locator(".task-content").click()
        expect(item).to_have_class(re.compile(r"\btask-selected\b"))
        expect(item).not_to_have_class(EXPANDED)
        assert item.bounding_box()["height"] == before["height"]
    plain = card(page, "Plain")
    plain.locator(".task-content").click()
    page.keyboard.press("ArrowRight")
    expect(plain).not_to_have_class(DETAILS)
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open] [id^=task-edit-title]")).to_have_value("Plain")


def test_line_tap_on_nothing_more_selects_then_opens(new_page, wiki):
    # A task whose title fits stays a line: the first tap only selects it,
    # the second opens the modal.
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _expand_setup(phone)
    plain = card(phone, "Plain")
    plain.locator(".task-content").tap()
    expect(plain).to_have_class(re.compile(r"\btask-selected\b"))
    expect(plain).not_to_have_class(EXPANDED)
    expect(phone.locator(".task-edit-modal[open]")).to_have_count(0)
    plain.locator(".task-content").tap()
    expect(phone.locator(".task-edit-modal[open]")).to_be_visible()


def test_full_cards_enter_opens_the_modal_at_once(page, wiki):
    page.request.post("/api/tasks", data={"title": "Plain"})
    page.goto("/tasks?view=open&group=none")
    card(page, "Plain").locator(".task-content").click()
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()


def test_line_tap_expands_then_opens(new_page, wiki):
    # On touch: the first tap shows the whole title, a tap on 📝 the notes
    # (and again hides them), a tap on the selected task opens the modal.
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _expand_setup(phone)
    passport = card(phone, LONG)
    passport.locator(".task-content").tap()
    expect(passport).to_have_class(EXPANDED)
    expect(passport.locator(".task-desc")).to_be_hidden()
    expect(phone.locator(".task-edit-modal[open]")).to_have_count(0)
    expand = passport.locator(".task-expand-btn")
    b = expand.bounding_box()
    assert b["width"] >= 40 and b["height"] >= 40
    expand.tap()
    expect(passport.locator(".task-desc")).to_be_visible()
    expand.tap()
    expect(passport.locator(".task-desc")).to_be_hidden()
    notes = passport.locator(".task-has-notes")
    b = notes.bounding_box()
    assert b["width"] >= 24 and b["height"] >= 24                       # a finger's target
    notes.tap()
    expect(passport.locator(".task-desc")).to_be_visible()
    expect(phone.locator(".task-edit-modal[open]")).to_have_count(0)
    notes.tap()
    expect(passport.locator(".task-desc")).to_be_hidden()
    passport.locator(".task-content").tap()
    expect(phone.locator(".task-edit-modal[open]")).to_be_visible()


def test_tasks_keys_work_right_after_the_modal_closes(page, wiki):
    # Regression: focus stayed on the closed modal's title field, so j/k,
    # Enter and Esc did nothing until a click somewhere.
    for t in ("Alpha", "Bravo"):
        page.request.post("/api/tasks", data={"title": t})
    page.goto("/tasks?view=open&group=none")
    card(page, "Bravo").locator(".task-content").click()
    page.keyboard.press("Enter")
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".task-edit-modal[open]")).to_have_count(0)
    page.keyboard.press("j")
    expect(card(page, "Alpha")).to_have_class(re.compile(r"\btask-selected\b"))


def test_line_tags_follow_the_title(page, wiki):
    # In the line the tags (pills) come right after the
    # title; dates and markers sit at the right end.
    _line_tasks(page)
    page.goto("/tasks?view=open&group=none")
    passport = card(page, "Renew passport")
    expect(passport.locator(".task-tag").first).to_have_text("#personal")
    title, first, due = (passport.locator(sel).first.bounding_box() for sel in (".task-title", ".task-tag", ".task-due"))
    assert first["x"] - (title["x"] + title["width"]) < 30
    row = passport.bounding_box()
    assert due["x"] + due["width"] > row["x"] + row["width"] - 90


# ── Card actions with a mouse ────────────────────────────────────────────────

def test_card_actions_on_hover_and_right_click_menu(page, wiki):
    # ⋯ is always shown; ⏭ on hover and on the selected card.
    page.request.post("/api/tasks", data={"title": "Water plants", "description": "See https://example.com",
                                          "due_date": "2026-11-12", "rec_type": "weekly", "rec_interval": 1})
    page.request.post("/api/tasks", data={"title": "Other"})
    page.goto("/tasks?view=open&group=none")
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
    open_details(page, "Water plants")
    water.locator("a[href='https://example.com']").click(button="right")
    expect(page.locator(".task-menu")).to_have_count(0)


def test_dates_end_at_the_right_edge(page, wiki):
    # What is always shown ends at the same place on every card, just left
    # of ⋯ at the right end, a recurring card's (which has ⏭) too. Hover
    # adds ⏭ and the empty 📅 left of it, so nothing on the card moves.
    page.request.post("/api/tasks", data={"title": "Weekly review", "due_date": "2026-11-12",
                                          "rec_type": "weekly", "rec_interval": 1, "tags": "work"})
    page.request.post("/api/tasks", data={"title": "Call the plumber", "due_date": "2026-11-14"})
    page.goto("/tasks?view=open&group=none")
    page.mouse.move(0, 0)
    ends = []
    for title in ("Weekly review", "Call the plumber"):
        menu, due = card(page, title).locator(".task-menu-btn").bounding_box(), card(page, title).locator(".task-due").bounding_box()
        ends.append(menu["x"] - (due["x"] + due["width"]))
    assert abs(ends[0] - ends[1]) < 1 and 0 <= ends[0] < 45     # the expand button's room is between
    weekly = card(page, "Weekly review")
    shown = (".task-tag", ".task-recurring", ".task-due", ".task-title", ".task-menu-btn")
    before = {sel: weekly.locator(sel).bounding_box() for sel in shown}
    weekly.locator(".task-title").hover()
    expect(weekly.locator(".task-skip-btn")).to_be_visible()
    for sel, box in before.items():
        after = weekly.locator(sel).bounding_box()
        assert abs(after["x"] - box["x"]) < 1 and abs(after["y"] - box["y"]) < 1, sel
    boxes = [weekly.locator(sel).bounding_box() for sel in
             (".task-skip-btn", ".task-start-info.task-date-empty", ".task-recurring", ".task-due", ".task-menu-btn")]
    assert all(l["x"] + l["width"] <= r["x"] + 1 for l, r in zip(boxes, boxes[1:]))    # left to right
    dots, title = weekly.locator(".task-menu-btn svg").bounding_box(), weekly.locator(".task-title").bounding_box()
    assert abs((dots["y"] + dots["height"] / 2) - (title["y"] + title["height"] / 2)) < 1.5   # ⋯ on the line's centre


def test_line_marks_notes(page, wiki):
    # 📝 says a task has notes, which its line hides; it stays when they are
    # open, so expanding a line changes nothing on it.
    page.request.post("/api/tasks", data={"title": "Has notes", "description": "Some details"})
    page.request.post("/api/tasks", data={"title": "No notes"})
    page.goto("/tasks?view=open&group=none")
    marker = card(page, "Has notes").locator(".task-has-notes")
    expect(marker).to_be_visible()
    expect(marker).to_have_attribute("title", "Has a description")
    expect(card(page, "No notes").locator(".task-has-notes")).to_have_count(0)
    expect(card(page, "Has notes").locator(".task-desc")).to_be_hidden()
    marker.click()                                                       # opens them
    expect(card(page, "Has notes").locator(".task-desc")).to_be_visible()
    expect(card(page, "Has notes")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(marker).to_be_visible()


def test_expanded_task_keeps_its_line(page, wiki):
    # Expanding keeps the first line as it is (tags and dates don't move);
    # the title wraps instead of being cut off, the notes open below.
    page.request.post("/api/tasks", data={"title": "Renew the passport before the trip to Lisbon in spring next year, with new photos from the shop on Main Street and the form",
                                          "tags": "admin", "due_date": "2026-11-15", "description": "At the shop."})
    page.set_viewport_size({"width": 800, "height": 800})
    page.goto("/tasks?view=open&group=none")
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
    expect(item.locator(".task-desc")).to_be_hidden()                    # not the notes
    page.keyboard.press("ArrowRight")                                    # they open below
    for sel, box in before.items():
        after = item.locator(sel).first.bounding_box()
        assert abs(after["x"] - box["x"]) < 1 and abs(after["y"] - box["y"]) < 3, sel
    desc = item.locator(".task-desc").bounding_box()
    assert desc["y"] > title.bounding_box()["y"] + title.bounding_box()["height"] - 1


def test_open_task_sets_its_details_apart(page, wiki):
    # An open task's description is plain, each comment hangs on a bar down
    # its left, "Comments" says where the description ends, and the card ends with room
    # before the next task.
    page.request.post("/api/tasks", data={"title": "Below"})          # the newest is on top
    tid = page.request.post("/api/tasks", data={"title": "Renew passport", "description": "Photos first."}).json()["id"]
    page.request.post(f"/api/tasks/{tid}/comments", data={"text": "Form printed"})
    page.request.post("/api/tasks", data={"title": "Above"})
    page.goto("/tasks?view=open&group=none")
    passport, after = card(page, "Renew passport"), card(page, "Below")
    gap = lambda: after.bounding_box()["y"] - (passport.bounding_box()["y"] + passport.bounding_box()["height"])
    closed_gap = gap()
    open_details(page, "Renew passport")
    desc, comments = passport.locator(".task-desc"), passport.locator(".task-card-comments")
    # The description is plain; only a comment has a bar.
    for part in (desc, comments):
        expect(part).to_have_css("border-left-width", "0px")
        expect(part).to_have_css("border-top-width", "0px")
    expect(comments.locator(".task-card-comment")).to_have_css("border-left-width", "3px")
    label = comments.locator(".task-card-comments-label")
    expect(label).to_have_text("Comments")
    d, l, c = desc.bounding_box(), label.bounding_box(), comments.locator(".task-card-comment").bounding_box()
    assert d["y"] + d["height"] < l["y"] < c["y"]
    assert gap() >= closed_gap + 8


def test_comments_are_the_edit_modals_last_section(page, wiki):
    # After every field of the task, under a line; Save and Cancel stay at
    # the dialog's bottom edge however many comments there are.
    tid = page.request.post("/api/tasks", data={"title": "Renew passport", "description": "Photos first."}).json()["id"]
    for i in range(30):
        page.request.post(f"/api/tasks/{tid}/comments", data={"text": f"Step {i}"})
    page.goto("/tasks?view=open&group=none")
    modal = open_edit(page, "Renew passport")
    order = modal.evaluate("""d => [...d.querySelectorAll('[name=description], [name=tags], [name=rec_type], [name=keep], .task-comments, .task-edit-actions')]
        .map(el => el.name || el.className.split(' ').pop())""")
    assert order == ["description", "tags", "keep", "rec_type", "task-comments", "task-edit-actions"]
    section = modal.locator(".task-comments")
    expect(section).to_have_css("border-top-width", "1px")
    # The line to write one looks like a field and opens the real one.
    line = modal.get_by_role("button", name="Write a comment…")
    box, desc = line.bounding_box(), modal.locator("[name=description]").bounding_box()
    assert abs(box["width"] - desc["width"]) < 2
    line.click()
    expect(modal.locator("#task-edit-comment")).to_be_focused()
    modal.locator("#task-edit-comment").press("Escape")
    # Every comment is in the dialog's own scroll, and Save stays in sight.
    expect(modal.locator(".task-comment-list > li")).to_have_count(30)
    assert modal.locator(".task-comment-list").evaluate("ul => ul.scrollHeight <= ul.clientHeight + 1")
    modal.locator(".task-comment-list > li").last.scroll_into_view_if_needed()
    save = modal.get_by_role("button", name="Save", exact=True)
    expect(save).to_be_in_viewport()
    modal.evaluate("d => d.scrollTo(0, 0)")
    expect(save).to_be_in_viewport()


def test_expanded_task_shows_its_latest_comments(page, wiki):
    tid = page.request.post("/api/tasks", data={"title": "Renew passport"}).json()["id"]
    for text in ("Photos done", "Form printed", "Appointment Tue", "Paid the fee"):
        page.request.post(f"/api/tasks/{tid}/comments", data={"text": text})
    page.goto("/tasks?view=open&group=none")
    passport = card(page, "Renew passport")
    expect(passport.locator(".task-card-comments")).to_be_hidden()   # the line: hidden too
    passport.locator(".task-comments-btn").click()                  # 💬 opens them on the line
    expect(page.locator(".task-edit-modal[open]")).to_have_count(0)
    comments = passport.locator(".task-card-comments p")
    expect(comments).to_have_count(3)                                # the latest 3, newest first
    expect(comments.first).to_contain_text("Paid the fee")
    expect(comments.last).to_contain_text("Form printed")
    passport.get_by_role("button", name="1 earlier…").click()       # the rest: in the modal
    expect(page.locator(".task-edit-modal[open] #task-edit-comment")).to_be_focused()


def test_line_on_a_phone_is_two_lines(new_page, wiki):
    # A phone's line is too short for title and dates: the title has the
    # first line (nearly the full width), tags and dates the second; a task
    # with neither stays one line. Rows stay finger-sized.
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    _line_tasks(phone)
    phone.goto("/tasks?view=open&group=none")
    passport, plain = card(phone, "Renew passport"), card(phone, "Plain")
    row, title, due = (passport.locator(sel).first.bounding_box() for sel in (".task-content", ".task-content", ".task-due"))
    row = passport.bounding_box()
    assert title["width"] > row["width"] * 0.6
    assert due["y"] >= title["y"] + title["height"] - 1                  # dates on the second line
    assert 44 <= row["height"] < 64
    assert 44 <= plain.bounding_box()["height"] < 52                     # nothing for a second line


def test_empty_dates_show_on_hover_in_lines_too(page, wiki):
    # With a mouse the empty ⏰ / 📅 placeholders (to set a date) show on
    # hover.
    page.request.post("/api/tasks", data={"title": "No dates yet"})
    page.goto("/tasks?view=open&group=none")
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
        : '# ' + li.querySelector('span')?.textContent.trim())""")


def test_open_sorts_by_deadline_then_created_and_remembers_it(page, wiki):
    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=9)).isoformat()
    ids = [page.request.post("/api/tasks", data={"title": t, "due_date": d}).json()["id"]
           for t, d in (("Plain", None), ("Later due", later), ("Soon due", soon))]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.request.post("/api/tasks", data={"title": "Fresh"})
    page.goto("/tasks?view=open&group=none")
    sel = page.locator("select.tasks-sort")
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
    ids = [page.request.post("/api/tasks", data={"title": t, "due_date": soon}).json()["id"]
           for t in ("Alpha", "Bravo")]
    page.request.post("/api/tasks/reorder", data={"order": ids})
    page.goto("/tasks?view=open&group=none")
    page.locator("select.tasks-sort").select_option("due")
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
    ids = [page.request.post("/api/tasks", data={"title": t, "start_date": today, "due_date": d}).json()["id"]
           for t, d in (("Old", later), ("Mid", None), ("New", soon))]
    page.request.post("/api/tasks/reorder", data={"order": [ids[1], ids[0], ids[2]]})
    page.goto("/tasks?view=open&group=day")
    expect(card(page, "New")).to_be_visible()
    sel = page.locator("select.tasks-sort")
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


def test_new_list_from_the_tasks_pages_menu(page, wiki):
    # The Tasks page's ⋯ menu: New list… asks for a name, makes the list and
    # opens it; a name taken comes back saying so.
    page.request.post("/api/tasks", data={"title": "Milk", "tags": "shop"})
    page.goto("/tasks?view=open&group=none")
    page.locator(".page-header .page-menu-btn").click()
    menu = page.locator("#page-menu")
    # The page's one ⋯ menu: the view's settings, export and import, and New list…; a list's own actions aren't All tasks'.
    expect(menu.get_by_role("menuitem")).to_have_text(["List settings…", "New list…", "Export tasks", "Import tasks…"])
    menu.get_by_role("menuitem", name="New list…").click()
    dialog = page.locator("#list-new-dialog")
    expect(dialog).to_be_visible()
    name = page.locator("#list-new-input")
    expect(name).to_be_focused()
    name.fill("#Shop")
    expect(page.locator("#list-new-private")).to_have_count(0)        # no users: nothing private
    name.press("Enter")
    # The new list opens, pinned: a pill at once, after the Inbox. A pin is
    # a pill and no more: new tasks go to the Inbox as before, and the page
    # has nothing to say about it.
    page.wait_for_url(re.compile(r"/tasks\?list=shop$"))
    expect(page.locator(".page-header h1")).to_have_text("List: shop")
    expect(page.locator(".tasks-place .tasks-place-label")).to_have_text(["My tasks", "Inbox", "shop", "More"])
    expect(page.locator('.tasks-place[data-place="list:shop"]')).to_have_attribute("aria-current", "page")
    expect(page.locator('.tasks-place[data-place="list:shop"] .tasks-place-pin')).to_have_count(1)
    expect(page.locator(".tasks-made-first")).to_have_count(0)
    # The Tasks link comes back to the view you are on: the new list.
    expect(page.locator(".breadcrumb .module-link[data-module=tasks]")).to_have_attribute("href", "/tasks?list=shop")
    page.goto("/tasks?list=shop&view=open&group=none")
    expect(page.locator(".task-item")).to_have_count(0)                # a tag of that name is a tag: the list starts empty
    assert '{"name": "shop"}' in (wiki.root / "_tasks/lists.json").read_text()
    assert not (wiki.root / "lists").exists()                          # a list is no page
    # On a list the menu has its actions too; the same name again is refused in the dialog.
    page.locator(".page-header .page-menu-btn").click()
    expect(menu.get_by_role("menuitem")).to_have_text(["List settings…", "New list…", "Rename…", "Unpin", "Delete…", "Export tasks", "Import tasks…"])
    menu.get_by_role("menuitem", name="New list…").click()
    name.fill("shop")
    page.locator("#list-new-submit").click()
    expect(dialog).to_be_visible()
    expect(dialog.locator(".rename-error")).to_contain_text("There is a list #shop already")
    expect(name).to_have_value("shop")
    assert "newlist_error" not in page.url                            # gone from the address
    page.locator("#list-new-cancel").click()
    expect(dialog).to_be_hidden()


def test_rename_and_delete_list_dialogs(page, wiki):
    """Rename… on a list: its name everywhere; a name of another list is
    refused in the dialog. Delete…: its tasks go where the dialog says."""
    list_page(page, wiki, "shop", "# Shop\n\n[[tasks: list=shop]]\n")
    make_list(page, "hardware")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop", "tags": ["shop"]})
    page.request.post("/api/tasks", data={"title": "Nails", "list": "hardware"})
    page.goto("/tasks?list=shop")
    dialog = page.locator("#list-rename-dialog")
    expect(dialog).to_be_hidden()
    page.locator(".page-header .page-menu-btn").click()             # the page's ⋯ menu
    page.locator("#list-rename-btn").click()
    expect(dialog).to_be_visible()
    expect(page.locator("#list-rename-input")).to_have_value("shop")
    expect(page.locator("#list-rename-input")).to_be_focused()

    page.locator("#list-rename-input").fill("hardware")
    page.locator("#list-rename-submit").click()
    expect(dialog).to_be_visible()   # back, saying why
    expect(dialog).to_contain_text("There is a list #hardware already")
    expect(page.locator("#list-rename-input")).to_have_value("hardware")
    expect(page).not_to_have_url(re.compile("rename_error"))
    page.locator("#list-rename-input").fill("food")
    page.locator("#list-rename-submit").click()
    page.wait_for_url("**/tasks?list=food")
    expect(page.locator(".page-header h1")).to_have_text("List: food")
    open_tab(page, "open")
    expect(card(page, "Milk")).to_be_visible()
    assert "list=food" in (wiki.root / "shop.md").read_text()
    assert task_by_title(page, "Milk")["tags"] == ["shop"]            # the tag of the old name is a tag

    # Delete…: the list goes, its tasks into the list chosen.
    page.locator(".page-header .page-menu-btn").click()
    page.locator("#list-delete-btn").click()
    delete = page.locator("#list-delete-dialog")
    expect(delete).to_be_visible()
    expect(delete.locator("#list-delete-to option")).to_have_text(["The Inbox (no list)", "#hardware"])
    expect(delete.locator("label", has=page.locator("#list-delete-close"))).to_have_text("Close its open task as won't do")
    expect(delete.locator("#list-delete-close")).not_to_be_checked()
    delete.locator("#list-delete-to").select_option("hardware")
    delete.locator("#list-delete-submit").click()
    page.wait_for_url("**/tasks?list=hardware")
    open_tab(page, "open")
    expect(card(page, "Milk")).to_be_visible()
    expect(card(page, "Nails")).to_be_visible()
    expect(page.locator('.tasks-place[data-place="list:food"]')).to_have_count(0)
    # The page that showed it says so, and offers to make it again.
    page.goto("/page/shop")
    expect(page.locator(".tasks-no-list")).to_contain_text("There is no list #food.")
    expect(page.locator(".tasks-app")).to_have_count(0)
    page.locator(".tasks-no-list button").click()
    page.wait_for_url(re.compile(r"/tasks\?list=food$"))            # made, and pinned
    expect(page.locator('.tasks-place[data-place="list:food"] .tasks-place-pin')).to_have_count(1)
    page.goto("/page/shop")
    expect(page.locator(".tasks-app")).to_have_count(1)


def test_pin_and_move_first_from_a_lists_menu(page, wiki):
    """A list's ⋯ menu pins it for you, moves its pill first, unpins it. A
    pin is a pill and no more: it says nothing about where a new task goes."""
    for name in ("shop", "work"):
        make_list(page, name)
    page.goto("/tasks?list=shop")
    menu = page.locator("#page-menu")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks).to_have_attribute("href", "/tasks?list=shop")   # the view shown, pinned or not
    page.locator(".page-header .page-menu-btn").click()
    menu.get_by_role("menuitem", name="Pin").click()
    page.wait_for_url("**/tasks?list=shop")
    expect(page.locator('.tasks-place[data-place="list:shop"] .tasks-place-pin')).to_have_count(1)
    expect(tasks).to_have_attribute("href", "/tasks?list=shop")
    page.goto("/tasks?list=work")
    page.locator(".page-header .page-menu-btn").click()
    menu.get_by_role("menuitem", name="Pin").click()
    page.wait_for_url("**/tasks?list=work")
    labels = page.locator(".tasks-place .tasks-place-label")
    expect(labels).to_have_text(["My tasks", "Inbox", "shop", "work", "More"])
    expect(page.locator(".tasks-place-first")).to_have_count(0)                    # no pill is marked as the first
    expect(page.locator('.tasks-place[data-place="list:shop"]')).to_have_attribute("title", "Pinned list #shop")
    for attr in ("data-tasks-list", "data-tasks-tags"):
        assert page.locator("body").get_attribute(attr) is None
    # The menu has no Move first: the pills are put in order by dragging them
    # (the list switcher), which the route does here.
    page.locator(".page-header .page-menu-btn").click()
    expect(menu.get_by_role("menuitem")).to_have_text(["List settings…", "New list…", "Rename…", "Unpin", "Delete…", "Export tasks", "Import tasks…"])
    page.keyboard.press("Escape")
    assert page.request.post("/tasks/pin-list", form={"list": "work", "pin": "first"}).ok
    page.goto("/tasks?list=work")
    expect(labels).to_have_text(["My tasks", "Inbox", "work", "shop", "More"])
    # A new task still goes to the Inbox.
    page.keyboard.press("c")
    page.keyboard.press("t")
    expect(page.locator(".task-edit-modal[open] select[id^=task-edit-list]")).to_have_value("")
    page.keyboard.press("Escape")
    page.locator(".page-header .page-menu-btn").click()
    menu.get_by_role("menuitem", name="Unpin").click()
    page.wait_for_url("**/tasks?list=work")
    expect(labels).to_have_text(["My tasks", "Inbox", "shop", "work"])   # work is under More, which names it
    # The last pin goes as any other.
    page.goto("/tasks?list=shop")
    page.locator(".page-header .page-menu-btn").click()
    menu.get_by_role("menuitem", name="Unpin").click()
    page.wait_for_url("**/tasks?list=shop")
    expect(page.locator(".tasks-place-pin")).to_have_count(0)
    expect(labels).to_have_text(["My tasks", "Inbox", "shop"])


def test_dragging_a_pill_reorders_the_pinned_lists(page, wiki):
    """A pinned list's pill dragged to another place in the row: the pins in
    that order, kept without the page loading again (the order is all a pin
    has)."""
    for name in ("a", "b", "c"):
        make_list(page, name, pin=True)
    make_list(page, "quiet")
    page.goto("/tasks")
    labels = page.locator(".tasks-places a.tasks-place .tasks-place-label")
    expect(labels).to_have_text(["My tasks", "Inbox", "a", "b", "c"])
    pill = lambda name: page.locator(f'.tasks-place[data-place="list:{name}"]')
    expect(pill("c")).to_have_attribute("draggable", "true")
    for fixed in ("mine", "inbox"):
        expect(page.locator(f'.tasks-place[data-place="{fixed}"]')).not_to_have_attribute("draggable", "true")
    loads = []
    page.on("framenavigated", lambda frame: loads.append(frame.url))
    with page.expect_response("**/tasks/pin-order") as saved:
        pill("c").drag_to(pill("a"), target_position={"x": 3, "y": 5})    # onto a's left half: before it
    assert saved.value.ok
    expect(labels).to_have_text(["My tasks", "Inbox", "c", "a", "b"])
    assert '"pinned": ["c", "a", "b"]' in (wiki.root / "_tasks/lists.json").read_text()
    page.wait_for_timeout(300)
    assert loads == []                                                    # the page stayed
    # Onto the Inbox: no place for a list; the pills stay behind it.
    pill("b").drag_to(page.locator('.tasks-place[data-place="inbox"]'), target_position={"x": 3, "y": 5})
    expect(labels.nth(0)).to_have_text("My tasks")
    expect(labels.nth(1)).to_have_text("Inbox")
    page.reload()
    expect(labels.nth(1)).to_have_text("Inbox")
    # In sharing mode nobody reorders.
    page.context.add_cookies([{"name": "zone-sharing", "value": "1", "url": page.url}])
    page.reload()
    expect(pill("c")).not_to_have_attribute("draggable", "true")


def test_more_finds_a_list_by_name_and_marks_new_ones(page, wiki):
    """With many lists More has a field that filters them by name; a list
    this browser hasn't shown before is marked new until More is opened."""
    for i in range(8):
        make_list(page, f"list{i}")
    make_list(page, "garden")
    page.goto("/tasks")
    more = page.locator(".lists-more")
    summary = more.locator("summary")
    expect(summary).not_to_have_class(re.compile("has-new"))            # a first visit knows them all
    summary.click()
    field = more.locator(".lists-more-filter")
    expect(field).to_be_focused()
    expect(more.locator(".lists-more-item:visible")).to_have_count(10)
    field.fill("GAR")
    expect(more.locator(".lists-more-item:visible")).to_have_text(["garden", "All tasks"])
    field.press("Enter")
    page.wait_for_url(re.compile(r"/tasks\?list=garden$"))
    # A list made since (by someone else, or an assistant: one made here is a pill).
    make_list(page, "fresh")
    page.reload()
    summary = page.locator(".lists-more summary")
    expect(summary).to_have_class(re.compile("has-new"))
    summary.click()
    expect(page.locator(".lists-more-item:has(.lists-more-new) .lists-more-name")).to_have_text(["fresh"])
    expect(summary).not_to_have_class(re.compile("has-new"))
    page.reload()
    expect(page.locator(".lists-more summary")).not_to_have_class(re.compile("has-new"))
    expect(page.locator(".lists-more-new")).to_have_count(0)


def test_rename_tag_dialog(page, wiki):
    """Rename tag… on a tag's view: the tag everywhere; a name in use offers the merge, until edited."""
    wiki.write("shop", "# Shop\n\n[[tasks: tag=shop]]\n")
    page.request.post("/api/tasks", data={"title": "Milk", "tags": ["shop"]})
    page.request.post("/api/tasks", data={"title": "Nails", "tags": ["hardware"]})
    page.goto("/tasks?tag=shop")
    dialog = page.locator("#tag-rename-dialog")
    expect(dialog).to_be_hidden()
    page.locator(".page-header .page-menu-btn").click()
    page.locator("#tag-rename-btn").click()
    expect(dialog).to_be_visible()
    expect(page.locator("#tag-rename-input")).to_have_value("shop")

    page.locator("#tag-rename-input").fill("hardware")
    page.locator("#tag-rename-submit").click()
    expect(dialog).to_be_visible()   # back, asking
    expect(dialog).to_contain_text("already in use")
    expect(page.locator("#tag-rename-submit")).to_have_text("Merge")
    expect(page).not_to_have_url(re.compile("rename_error"))
    page.locator("#tag-rename-input").fill("food")
    expect(page.locator("#tag-rename-submit")).to_have_text("Rename")   # another name: no merge

    page.locator("#tag-rename-submit").click()
    page.wait_for_url("**/tasks?tag=food")
    open_tab(page, "open")
    expect(card(page, "Milk")).to_be_visible()
    expect(card(page, "Nails")).to_have_count(0)
    assert "tag=food" in (wiki.root / "shop.md").read_text()


def test_settings_dialog_changes_the_lists_settings_and_the_pages(page, wiki):
    """List settings… in the toolbar's ⋯ menu: the tabs' names, the limit
    and the tags are the list's own, kept with the list; the tab and the
    grouping a page opens it with are the page's, written into its
    [[tasks: …]]; the pin is yours."""
    list_page(page, wiki, "shopping", "# Shopping\n\nBefore.\n\n[[tasks: list=shop keep=1]]\n\nAfter.\n")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop"})
    registry = wiki.root / "_tasks/lists.json"
    page.goto("/page/shopping")
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Today"])
    open_list_settings(page)
    dialog = page.locator("#task-settings-modal")
    expect(dialog).to_be_visible()
    # It opens with what the list and the page say.
    expect(dialog.locator("[name=view]")).to_have_value("today")
    expect(dialog.locator("[name=group]")).to_have_value("day")
    expect(dialog.locator("[name=keep]")).to_be_checked()   # shown: the list makes keep tasks
    expect(dialog.locator("[name=list]")).to_have_count(0)   # which list it shows is the page's to say
    expect(dialog.locator(".task-settings-pin")).to_have_count(0)      # a list is pinned in its ⋯ menu, not here
    expect(dialog.locator(".task-settings-where")).to_contain_text("The list’s settings hold wherever it shows")
    # What is rarely changed shows only when set, so the line under the
    # fields offers each.
    for name in ("label_open", "tag", "limit", "new", "noshare", "homeout"):
        expect(dialog.locator(f"[name={name}]")).to_be_hidden()
    more = dialog.locator(".task-add-field:visible")
    expect(more).to_have_text(["WIP limit", "Tab names", "Tags of a new task", "Filter by tag", "Hide when sharing", "Leave out of My tasks"])
    more.filter(has_text="Tab names").click()
    expect(dialog.locator("[name=label_open]")).to_be_focused()
    more.filter(has_text="WIP limit").click()
    more.filter(has_text="Tags of a new task").click()
    expect(dialog.locator(".task-add-field:visible")).to_have_text(["Filter by tag", "Hide when sharing", "Leave out of My tasks"])
    # The app's shortcuts leave the dialog's typing alone.
    dialog.locator("[name=label_open]").fill("To buy")
    dialog.locator("[name=label_focus]").fill("Cart")
    dialog.locator("[name=view]").select_option("open")
    dialog.locator("[name=group]").select_option("none")
    dialog.locator("[name=limit]").fill("6")
    dialog.locator("[name=new]").fill("weekly, fresh")
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    # The list's own to the registry; the tab and the grouping, the page's, into its embed.
    assert '{"name": "shop", "limit": "6", "keep": "1", "labels": "To_buy,Cart", "new": "weekly,fresh"}' in registry.read_text()
    assert wiki.read("shopping") == "# Shopping\n\nBefore.\n\n[[tasks: list=shop keep=1 view=open group=none]]\n\nAfter.\n"
    # The page opens as set: the Open tab, by its new name, as one list.
    expect(page.locator("#tasks-tabs [role='tab']")).to_have_text(["To buy", "Cart"])
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["To buy"])
    expect(page.locator(".tasks-cut [data-group=day]")).to_have_attribute("aria-pressed", "false")

    # Saving without a change writes nothing; one change leaves the rest.
    open_list_settings(page)
    expect(dialog.locator("[name=view] option")).to_have_text(["To buy", "Cart"])
    expect(dialog.locator("[name=label_open]")).to_be_visible()   # shown: names are set
    expect(dialog.locator(".task-add-field:visible")).to_have_text(["Filter by tag", "Hide when sharing", "Leave out of My tasks"])
    expect(dialog.locator("[name=label_open]")).to_have_value("To buy")
    expect(dialog.locator("[name=limit]")).to_have_value("6")
    before = registry.read_text(), wiki.read("shopping")
    dialog.get_by_role("button", name="Save").click()
    expect(dialog).to_be_hidden()
    assert (registry.read_text(), wiki.read("shopping")) == before
    open_list_settings(page)
    dialog.locator("[name=group]").select_option("day")
    # (The list is pinned meanwhile, as its ⋯ menu's Pin does: no setting of this dialog.)
    assert page.request.post("/tasks/pin-list", form={"list": "shop", "pin": "1"}, max_redirects=0).status == 302   # no visit
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    text = registry.read_text()
    assert '"group"' not in text and '"pinned": ["shop"]' in text and "default" not in text
    assert wiki.read("shopping") == "# Shopping\n\nBefore.\n\n[[tasks: list=shop keep=1 view=open group=day]]\n\nAfter.\n"   # no pin=: the pin is no param of a page
    before = registry.read_text(), wiki.read("shopping")
    expect(page.locator(".tasks-cut [data-group=day]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".breadcrumb .module-link[data-module=tasks]")).to_have_attribute("href", "/tasks?mine=1")

    # The list on /tasks has the same settings; there the tab is the list's own.
    page.goto("/tasks?list=shop")
    open_list_settings(page)
    expect(dialog.locator(".task-settings-where")).to_contain_text("For this list, for everyone")
    expect(dialog.locator(".task-add-field:visible")).to_have_text(["Hide when sharing", "Leave out of My tasks"])    # no page here: no filter
    expect(dialog.locator("[name=view]")).to_have_value("today")    # the list's own tab, not the page's
    dialog.locator("[name=view]").select_option("open")
    assert page.request.post("/tasks/pin-list", form={"list": "shop", "pin": "0"}).ok     # unpinned
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    text = registry.read_text()
    assert '{"name": "shop", "view": "open", "limit": "6", "keep": "1"' in text and '"pinned": []' in text
    assert wiki.read("shopping") == before[1]                        # the page keeps its own tab
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["To buy"])
    expect(page.locator(".tasks-place-pin")).to_have_count(0)


def test_plan_default_and_all_tasks_settings(page, wiki):
    """group=none: Open is one list on a fresh load; a flip of the switch lasts
    for the browser tab. All tasks keeps its settings in the lists' registry."""
    page.request.post("/api/tasks", data={"title": "Some task"})
    wiki.write("tasks", "# Tasks\n\n[[tasks: group=none]]\n")
    page.goto("/page/tasks")
    plan = page.locator(".tasks-cut [data-group=day]")
    as_list = page.locator(".tasks-cut [data-group=none]")
    page.locator("#tasks-tabs [data-view='open']").click()
    expect(plan).to_have_attribute("aria-pressed", "false")
    plan.click()
    expect(plan).to_have_attribute("aria-pressed", "true")
    page.locator("#tasks-tabs [data-view='focus']").click()
    page.locator("#tasks-tabs [data-view='open']").click()
    expect(plan).to_have_attribute("aria-pressed", "true")
    # A new browser tab starts at the default again.
    other = page.context.new_page()
    other.goto("/page/tasks")
    other.locator("#tasks-tabs [data-view='open']").click()
    expect(other.locator(".tasks-cut [data-group=day]")).to_have_attribute("aria-pressed", "false")
    other.close()

    page.goto("/tasks")
    open_list_settings(page)
    dialog = page.locator("#task-settings-modal")
    # No pin and no filter there, nor offered.
    expect(dialog.locator(".task-settings-pin")).to_be_hidden()
    expect(dialog.locator(".task-add-field:visible")).to_have_text(["WIP limit", "Tab names", "Tags of a new task"])
    dialog.locator(".task-add-field", has_text="Tags of a new task").click()
    expect(dialog.locator("[name=new]")).to_be_visible()
    expect(dialog.locator("[name=tag]")).to_be_hidden()
    dialog.locator("[name=view]").select_option("open")
    dialog.locator("[name=group]").select_option("none")
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '"all": {"view": "open", "group": "none"}' in (wiki.root / "_tasks" / "lists.json").read_text()
    expect(page.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Open"])
    expect(plan).to_have_attribute("aria-pressed", "false")


def test_settings_are_in_the_more_menu_on_a_phone(new_page, wiki):
    """List settings… is the first item of the ⋯ menu, on a phone as
    anywhere: the toolbar has no button of its own for it."""
    from conftest import TOUCH_PHONE
    phone = new_page(**TOUCH_PHONE)
    list_page(phone, wiki, "shopping", "# Shopping\n\n[[tasks: list=shop]]\n")
    phone.goto("/page/shopping")
    expect(phone.locator("#task-settings-btn")).to_have_count(0)
    more_menu(phone)
    expect(phone.get_by_role("menuitem")).to_have_text(["List settings…", "Export tasks", "Import tasks…"])
    phone.get_by_role("menuitem", name="List settings…").click()
    dialog = phone.locator("#task-settings-modal")
    expect(dialog).to_be_visible()
    dialog.locator(".task-add-field", has_text="WIP limit").tap()
    dialog.locator("[name=limit]").fill("4")
    with phone.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '{"name": "shop", "limit": "4"}' in (wiki.root / "_tasks/lists.json").read_text()
    assert "[[tasks: list=shop]]" in wiki.read("shopping")


def test_export_and_import_a_lists_tasks(page, wiki, tmp_path):
    """The list's ⋯ menu: Export saves the list's tasks as a
    file, Import… adds a file's tasks to the list it is opened in, asking for
    a tag of your own once the file is picked."""
    list_page(page, wiki, "shopping", "# Shopping\n\n[[tasks: list=shop view=open group=none]]\n")
    list_page(page, wiki, "food", "# Food\n\n[[tasks: list=food view=open group=none]]\n")
    page.request.post("/api/tasks", data={"title": "Milk", "list": "shop"})
    page.request.post("/api/tasks", data={"title": "Report", "tags": ["shop"]})     # a tag of that name: not the list's
    page.goto("/page/shopping")
    more = page.locator("#task-more-btn")
    dialog = page.locator("#task-import-modal")

    def pick(file):
        more.click()
        with page.expect_file_chooser() as chooser:
            page.get_by_role("menuitem", name="Import tasks…").click()
        chooser.value.set_files(file)

    more.click()
    expect(page.get_by_role("menuitem")).to_have_text(["List settings…", "Export tasks", "Import tasks…"])
    with page.expect_download() as got:
        page.get_by_role("menuitem", name="Export tasks").click()
    assert re.fullmatch(r"zone-tasks-shop-\d{4}-\d\d-\d\d\.json", got.value.suggested_filename)
    saved = tmp_path / "shop.json"
    got.value.save_as(saved)
    assert '"Milk"' in saved.read_text() and "Report" not in saved.read_text()
    # The settings dialog has none of it.
    open_list_settings(page)
    expect(page.locator("#task-settings-modal")).not_to_contain_text("Export")
    page.keyboard.press("Escape")

    # Here already: nothing to import.
    pick(saved)
    expect(dialog).to_contain_text("Import 1 task from “shop.json”")
    dialog.get_by_role("button", name="Import").click()
    expect(page.locator(".task-undo-toast")).to_have_text("Nothing imported: the tasks are already here.")
    expect(dialog).to_be_hidden()
    # Into another list, once the task is gone from this one: it goes into that list.
    page.request.delete(f"/api/tasks/{task_by_title(page, 'Milk')['id']}")
    page.goto("/page/food")
    expect(page.locator("#tasks-list .task-item")).to_have_count(0)
    pick(saved)
    # Cancel imports nothing.
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert "Milk" not in [t["title"] for t in page.request.get("/api/tasks").json()]
    pick(saved)
    page.locator("#task-import-tag").fill("from anna")
    page.keyboard.press("Enter")
    expect(page.locator(".task-undo-toast")).to_contain_text("Imported 1 task.")
    expect(dialog).to_be_hidden()
    expect(page.locator("#tasks-list .task-item")).to_have_count(1)
    assert (task_by_title(page, "Milk")["list"], task_by_title(page, "Milk")["tags"]) == ("food", ["from-anna"])
    # Undo takes the imported tasks out again; then without a tag of its own.
    page.locator(".task-undo-btn").click()
    expect(page.locator("#tasks-list .task-item")).to_have_count(0)
    assert "Milk" not in [t["title"] for t in page.request.get("/api/tasks").json()]
    pick(saved)
    expect(page.locator("#task-import-tag")).to_have_value("")
    dialog.get_by_role("button", name="Import").click()
    expect(page.locator("#tasks-list .task-item")).to_have_count(1)
    assert (task_by_title(page, "Milk")["list"], task_by_title(page, "Milk")["tags"]) == ("food", [])

    # A file that is no export says so, without asking for a tag.
    other = tmp_path / "notes.json"
    other.write_text('{"pages": []}')
    pick(other)
    expect(page.locator(".task-undo-toast").last).to_contain_text("exported from Zone")
    expect(dialog).to_be_hidden()
    # One the server refuses says why in the dialog.
    other.write_text('[{"notes": "no title"}]')
    pick(other)
    dialog.get_by_role("button", name="Import").click()
    expect(page.locator("#task-import-error")).to_be_visible()
    expect(dialog).to_be_visible()




def _many_tasks(wiki, n):
    """n open tasks in the wiki's file, Task 000 the oldest; Task 000 and
    Task 220 have a description in markdown."""
    tasks = [{"id": str(uuid.UUID(int=i + 1, version=4)), "title": f"Task {i:03d}",
              "description": "Some **bold** words" if i in (0, 220) else "",
              "created_at": f"2026-01-01T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}"} for i in range(n)]
    (wiki.root / "_tasks").mkdir(exist_ok=True)
    (wiki.root / "_tasks" / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    return tasks


def test_a_long_section_draws_its_top_and_shows_the_rest_on_request(page, wiki):
    """All tasks of a large wiki: a section draws its first 200 cards and a
    row that shows the rest; its count is of all, the filter searches all,
    and a task a link names is drawn."""
    tasks = _many_tasks(wiki, 230)
    page.goto("/tasks?view=open&group=none")
    expect(page.locator(".task-item")).to_have_count(200)
    expect(page.locator(".list-section[data-section=unsorted] .board-day-count")).to_have_text("230")
    more = page.locator("[data-draw-more]")
    expect(more).to_have_text("Show 30 more")
    expect(card(page, "Task 229")).to_be_visible()          # newest first
    expect(card(page, "Task 000")).to_have_count(0)
    # The filter finds what isn't drawn.
    page.locator("#task-filter-search").fill("Task 000")
    expect(card(page, "Task 000")).to_be_visible()
    expect(page.locator(".task-item")).to_have_count(1)
    page.locator("#task-filter-search").fill("")
    expect(page.locator(".task-item")).to_have_count(200)
    # The rest on request, and it stays through a redraw.
    more.click()
    expect(page.locator(".task-item")).to_have_count(230)
    expect(page.locator("[data-draw-more]")).to_have_count(0)
    page.locator("#task-filter-search").fill("x")
    page.locator("#task-filter-search").fill("")
    expect(page.locator(".task-item")).to_have_count(230)
    # A link to a task past the window: its section shows whole, the task selected.
    page.goto(f"/tasks?view=open&group=none&task={tasks[0]['id']}")
    expect(card(page, "Task 000")).to_be_visible()
    expect(card(page, "Task 000")).to_have_class(re.compile(r"\btask-selected\b|\bselected\b"))
    # The Plan board's sections too.
    page.goto("/tasks?view=open&group=day")
    expect(page.locator(".task-item")).to_have_count(200)
    expect(page.locator("[data-draw-more]")).to_have_text("Show 30 more")


def test_a_long_list_renders_a_description_when_it_is_opened(page, wiki):
    """Over 150 tasks, the page doesn't bring every description's HTML, and
    the list doesn't ask for them: a card's are fetched when its details are
    opened, and no card shows the markdown as plain text."""
    _many_tasks(wiki, 230)
    asked = []
    page.on("request", lambda r: asked.append(r.post_data_json) if r.url.endswith("/api/render-notes") else None)
    page.goto("/tasks?view=open&group=none")
    assert "tasks" not in json.loads(page.locator("script.tasks-initial").inner_text())    # the list asks for them
    row = card(page, "Task 220")
    expect(row).to_be_visible()
    expect(row.locator(".task-desc[data-lazy]")).to_have_count(1)
    assert not asked
    # Opened: fetched, and shown as a page shows it.
    row.locator(".task-has-notes").click()
    expect(row.locator(".task-desc strong")).to_have_text("bold")
    expect(row.locator(".task-desc")).to_be_visible()
    assert asked == [{"notes": ["Some **bold** words"], "comments": []}]
    expect(page.get_by_text("**bold**")).to_have_count(0)
    # Closed and opened again: nothing asked twice.
    row.locator(".task-has-notes").click()
    row.locator(".task-has-notes").click()
    expect(row.locator(".task-desc strong")).to_be_visible()
    assert len(asked) == 1
    # A short list has its descriptions with it, as always.
    _many_tasks(wiki, 3)
    page.goto("/tasks?view=open&group=none")
    assert json.loads(page.locator("script.tasks-initial").inner_text())["notes"] == ["<p>Some <strong>bold</strong> words</p>\n"]
    expect(card(page, "Task 000").locator(".task-desc strong")).to_have_count(1)
