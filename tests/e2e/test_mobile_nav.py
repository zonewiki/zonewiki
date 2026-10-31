"""The modules (switcher and phone tab bar), action buttons (phone and desktop),
status badges on phones, and the compact Plan board."""

import re
from datetime import date

from playwright.sync_api import expect

from conftest import TOUCH_PHONE, open_tab
from test_touch import touch_drag
from tests import settings


def _overlap(a, b) -> bool:
    return not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"] or
                a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


# ── Phone search button ──────────────────────────────────────────────────────
# Search is otherwise Ctrl+P, which a phone doesn't have.

def test_search_button_on_phones(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    btn = phone.locator(".action-search")
    expect(btn).to_be_visible()
    box = btn.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert box["x"] + box["width"] > 390 - 40 and box["y"] + box["height"] > 844 - 40  # bottom right


def test_search_button_opens_search_and_steps_aside_while_typing(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    phone.locator(".action-search").tap()
    expect(phone.locator("#search-modal")).to_be_visible()
    # The search field has focus (keyboard up), so the button steps aside.
    expect(phone.locator(".action-search")).to_be_hidden()


def test_no_action_buttons_in_editor(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/edit/index")
    expect(phone.locator(".action-buttons")).to_have_count(0)


def test_page_end_not_hidden_behind_search_button(new_page, wiki):
    wiki.write("long", "# Long\n\n" + "Filler.\n\n" * 60 + "The last line.\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/long")
    phone.evaluate("scrollTo(0, document.body.scrollHeight)")
    last = phone.get_by_text("The last line.").bounding_box()
    assert last["y"] + last["height"] <= phone.locator(".action-search").bounding_box()["y"]


# ── Phone tab bar ────────────────────────────────────────────────────────────
# The phone's way to the modules: Wiki, Notes, Tasks, then a divider and
# the desktop's actions: New note, New task and Search.

def test_phone_tab_bar_spans_the_bottom_edge(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    bar = phone.locator(".action-buttons")
    b = bar.bounding_box()
    assert b["x"] == 0 and b["width"] == 390 and abs(b["y"] + b["height"] - 844) < 1
    items = bar.locator(".action-module, button")
    expect(items).to_have_text(["Wiki", "Tasks", "Notes", "Note", "Task", "Search"])
    boxes = [i.bounding_box() for i in items.all()]
    assert all(x["width"] >= 44 and x["height"] >= 44 for x in boxes)
    assert all(abs(x["y"] - boxes[0]["y"]) < 1 for x in boxes)   # one row
    # Dividers set apart navigation (the modules), the "new" actions and Search.
    seps = [s.bounding_box() for s in bar.locator(".action-sep").all()]
    assert len(seps) == 2
    assert all(s["width"] <= 1.5 and s["height"] >= 20 for s in seps), seps
    assert boxes[2]["x"] + boxes[2]["width"] <= seps[0]["x"] <= boxes[3]["x"]
    assert boxes[4]["x"] + boxes[4]["width"] <= seps[1]["x"] <= boxes[5]["x"]
    # The actions say what they make, with the desktop's icons.
    expect(bar.locator(".action-new-note")).to_have_attribute("aria-label", "New note")
    expect(bar.locator(".action-task")).to_have_attribute("aria-label", "New task")
    for a in ("new-note", "task"):
        assert (bar.locator(f".action-{a} svg").inner_html()
                == phone.locator(f".top-{a} svg").inner_html())
    # The breadcrumb's switcher shrinks to the logo: the bar has the rest.
    expect(phone.locator(".breadcrumb .module-link[data-module=wiki] svg")).to_be_visible()
    expect(phone.locator(".breadcrumb .module-link[data-module=tasks]")).to_be_hidden()
    for label in phone.locator(".breadcrumb .module-label").all():
        expect(label).to_be_hidden()


def test_phone_tab_bar_marks_and_opens_modules(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    tab = lambda m: phone.locator(f".action-buttons .action-module[data-module={m}]")
    expect(tab("wiki")).to_have_attribute("aria-current", "page")
    tab("tasks").tap()
    phone.wait_for_url("**/tasks")
    expect(tab("tasks")).to_have_attribute("aria-current", "page")
    expect(tab("wiki")).not_to_have_attribute("aria-current", "page")
    tab("notes").tap()
    phone.wait_for_url("**/notes")
    expect(tab("notes")).to_have_attribute("aria-current", "page")


def test_note_creates_a_quicknote(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    # In every module, the wiki too: Note always means a note.
    phone.goto("/page/index")
    phone.locator(".action-new-note").tap()
    expect(phone.locator("#modal")).to_be_visible()
    # The title field has focus (keyboard up), so the bar steps aside.
    expect(phone.locator(".action-buttons")).to_be_hidden()
    phone.locator("#new-note-title").fill("Shopping idea")
    phone.locator("#new-note-title").press("Enter")
    expect(phone).to_have_url(re.compile(r"/edit/\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_shopping-idea$"))
    # The cursor waits under the title, so typing starts the body.
    ta = phone.locator("#editor-textarea")
    expect(ta).to_be_focused()
    assert ta.evaluate("t => [t.selectionStart, t.value.length]") == [len("# Shopping idea\n\n")] * 2
    phone.keyboard.type("milk")
    expect(ta).to_have_value("# Shopping idea\n\nmilk")


def test_task_adds_a_task_to_your_tasks_from_the_wiki(new_page, wiki):
    wiki.write("tasks", "# My tasks\n\n[[tasks: list=task view=focus home=1]]\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    phone.locator(".action-task").tap()
    modal = phone.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip", has_text="task")).to_have_count(1)
    expect(phone).to_have_url(re.compile(r"/page/index$"))   # stays on the page


def test_task_creates_a_task_in_the_tasks(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/tasks")
    phone.locator(".action-task").tap()
    expect(phone.locator(".task-edit-modal[open]")).to_be_visible()


def test_tasks_tab_counts_stand_beside_the_icon(new_page, wiki):
    # Regression: two two-digit counts hung past the narrow Tasks tab, over
    # its neighbour; moved in, they covered the icon. They stand right of the
    # icon, due above Focus, clear of the label and of the next tab's icon.
    phone = new_page(**{**TOUCH_PHONE, "viewport": {"width": 320, "height": 640}})
    for i in range(12):
        phone.request.post("/api/tasks", data={
            "title": f"Task {i}", "due_date": "2020-01-01", "start_date": date.today().isoformat()})
    phone.goto("/page/index")
    bar = phone.locator(".action-buttons")
    expect(bar.locator(".module-focus")).to_have_text("12")
    tasks = bar.locator(".action-module[data-module=tasks]")
    tab, icon = tasks.bounding_box(), tasks.locator("svg").bounding_box()
    label = tasks.locator(".action-label").bounding_box()
    nxt = bar.locator(".action-module[data-module=tasks] ~ .action-module svg").first.bounding_box()
    due, focus = bar.locator(".module-due").bounding_box(), bar.locator(".module-focus").bounding_box()
    assert due["y"] + due["height"] <= focus["y"]                       # due on top
    for b in (due, focus):
        assert b["x"] >= icon["x"] + icon["width"], (b, icon)            # beside the icon
        assert b["x"] + b["width"] < nxt["x"], (b, nxt)                  # clear of the next tab's icon
        assert not _overlap(b, label), (b, label)
    # The icons stay evenly spaced: the counts take no room from a wider Tasks tab.
    xs = [bar.locator(f".action-module[data-module={m}] svg").bounding_box() for m in ("wiki", "tasks", "notes")]
    centres = [x["x"] + x["width"] / 2 for x in xs]
    assert abs((centres[1] - centres[0]) - (centres[2] - centres[1])) < 1, centres


def test_tasks_tab_shows_the_due_count(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/tasks", data={"title": "Pay rent", "due_date": date.today().isoformat()})
    phone.goto("/page/index")
    due = phone.locator(".action-buttons .module-due")
    expect(due).to_have_text("1")
    expect(due).to_be_visible()
    d, tab = due.bounding_box(), phone.locator(".action-buttons [data-module=tasks]").bounding_box()
    assert tab["x"] <= d["x"] and d["x"] + d["width"] <= tab["x"] + tab["width"]


def _count_trusted_clicks(phone):
    """From now on, count the clicks the browser itself dispatches
    (isTrusted), wherever they land; kept across a navigation."""
    phone.evaluate("""() => { sessionStorage.trusted = 0;
        document.addEventListener('click', e => { if (e.isTrusted) sessionStorage.trusted++; }, true); }""")


def _touch_tap(phone, locator, below=0):
    """A tap with real touch events, near the bottom edge of `locator`, or
    `below` px under it."""
    b = locator.bounding_box()
    y = b["y"] + b["height"] + below if below else b["y"] + b["height"] - 4
    cdp = phone.context.new_cdp_session(phone)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [
        {"x": b["x"] + b["width"] / 2, "y": y}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


def test_tab_bar_tap_does_not_leave_its_click_to_the_browser(new_page, wiki):
    # Regression: Firefox for Android (installed app, toolbar scrolled away)
    # sent the click of a tap on the bar to the tasks card under it, which got
    # selected and scrolled into view. The bar acts on the touch and cancels
    # the browser's click, so where the browser would send it doesn't matter.
    phone = new_page(**TOUCH_PHONE)
    for i in range(30):
        phone.request.post("/api/tasks", data={"title": f"Task {i}"})
    phone.goto("/tasks")
    open_tab(phone, "open")
    phone.evaluate("scrollTo(0, 400)")
    _count_trusted_clicks(phone)
    with phone.expect_navigation():
        _touch_tap(phone, phone.locator(".action-module[data-module=notes]"))
    assert phone.url.endswith("/notes")
    assert phone.evaluate("sessionStorage.trusted") == "0"
    phone.goto("/tasks")
    _count_trusted_clicks(phone)
    _touch_tap(phone, phone.locator(".action-new-note"))
    expect(phone.locator("#modal")).to_be_visible()      # opened once, by the touch
    assert phone.evaluate("sessionStorage.trusted") == "0"
    expect(phone.locator(".task-item.task-selected")).to_have_count(0)


def test_tab_bar_takes_taps_below_its_box(new_page, wiki):
    # Regression: Firefox for Android (installed app, toolbar scrolled away)
    # draws the bar lower than it lays it out, so a tap on the strip under
    # the labels landed below the bar's box, on a task list card, which got
    # selected. Here the bar's box is lifted to leave that strip over the
    # cards, as Firefox has it: the tap still goes to the tab above.
    phone = new_page(**TOUCH_PHONE)
    for i in range(30):
        phone.request.post("/api/tasks", data={"title": f"Task {i}"})
    phone.goto("/tasks")
    open_tab(phone, "open")
    phone.evaluate("scrollTo(0, 400)")
    phone.evaluate("document.querySelector('.action-buttons').style.bottom = '40px'")
    tab = phone.locator(".action-module[data-module=notes]")
    b = tab.bounding_box()
    under = phone.evaluate(f"document.elementFromPoint({b['x'] + b['width'] / 2}, {b['y'] + b['height'] + 20}).closest('.task-item') !== null")
    assert under   # a card is what the browser finds there
    with phone.expect_navigation():
        _touch_tap(phone, tab, below=20)
    assert phone.url.endswith("/notes")


# ── Action buttons on desktop ────────────────────────────────────────────────
# At the top right, in line with the breadcrumb: New note, Add a task
# and Search, as icons. The bottom bar is the phone's.

def test_top_actions_on_desktop(page, wiki):
    page.goto("/page/index")
    expect(page.locator(".action-buttons")).to_be_hidden()
    buttons = page.locator(".top-actions button")
    expect(buttons).to_have_count(4)
    names = [b.get_attribute("aria-label") for b in buttons.all()]
    assert names == ["Sharing mode", "New note", "Add a task", "Search"]
    boxes = [b.bounding_box() for b in buttons.all()]
    crumb = page.locator("main > .breadcrumb").bounding_box()
    vw = page.viewport_size["width"]
    main = page.locator("main").bounding_box()
    for b in boxes:
        assert abs((b["y"] + b["height"] / 2) - (crumb["y"] + crumb["height"] / 2)) < 2   # in line
    for b in boxes[:3]:
        assert 32 <= b["width"] <= 40 and b["width"] == b["height"]           # icon buttons, not tiny
    # Search, the one used most, is a labelled pill with its shortcut.
    assert boxes[3]["width"] > 100
    expect(buttons.nth(3)).to_contain_text("Search")
    expect(buttons.nth(3).locator("kbd")).to_have_text("Ctrl P")
    # Groups: the eye set apart by a divider; the two "new" buttons side by
    # side; a wider gap before search.
    sep = page.locator(".top-actions .top-sep").bounding_box()
    assert boxes[0]["x"] + boxes[0]["width"] < sep["x"] < boxes[1]["x"]
    gap = lambda a, b: b["x"] - (a["x"] + a["width"])
    assert gap(boxes[2], boxes[3]) > gap(boxes[1], boxes[2])
    assert all(a["x"] < b["x"] for a, b in zip(boxes, boxes[1:]))
    assert boxes[-1]["x"] + boxes[-1]["width"] > main["x"] + main["width"] - 40 and boxes[-1]["x"] < vw
    # The breadcrumb's own right end (the backlinks chip) keeps clear of them.
    assert crumb["x"] + crumb["width"] - float(page.locator("main > .breadcrumb").evaluate(
        "el => getComputedStyle(el).paddingRight").rstrip("px")) <= boxes[0]["x"]


def test_top_actions_hidden_on_phones(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    expect(phone.locator(".top-actions")).to_be_hidden()
    expect(phone.locator(".action-buttons")).to_be_visible()


def test_top_actions_open_quicknote_and_search(page, wiki):
    page.goto("/page/index")
    page.locator(".top-search").click()
    expect(page.locator("#search-modal")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#search-modal")).to_be_hidden()
    page.locator(".top-new-note").click()
    expect(page.locator("#modal")).to_be_visible()


# ── New task: the Tasks home decides ────────────────────────────────────────
# The New task button and c t give a new task the Tasks home's tags.

def _home(wiki, embed="[[tasks: list=task view=focus home=1]]"):
    wiki.write("tasks", f"# My tasks\n\n{embed}\n")
    wiki.write("garden", "# Garden\n\nBeds.\n")


def test_new_task_button_stays_on_the_page(page, wiki):
    # A page without a task list app loads a hidden one: the modal opens right
    # there, with the home's tags and start (Focus: today); a toast says
    # where the task went.
    _home(wiki)
    page.goto("/page/garden")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    assert page.url.endswith("/page/garden")
    expect(modal.locator(".tag-chip", has_text="task")).to_have_count(1)
    expect(modal.locator("[id^=task-edit-start]")).to_have_value(date.today().isoformat())
    modal.locator("[id^=task-edit-title]").fill("Order seeds")
    page.keyboard.press("Enter")
    expect(page.locator(".task-undo-toast")).to_contain_text("Added “Order seeds” to your tasks")
    tasks = page.request.get("/api/tasks").json()
    assert [(t["title"], t["tags"], t["start_date"]) for t in tasks] == [("Order seeds", ["task"], date.today().isoformat())]
    # A second time doesn't load the tasks again.
    page.keyboard.press("c")
    page.keyboard.press("t")
    expect(modal).to_be_visible()
    expect(page.locator(".tasks-app[data-headless]")).to_have_count(1)


def test_new_task_start_follows_the_homes_view(page, wiki):
    _home(wiki, "[[tasks: list=task view=open home=1]]")
    page.goto("/page/garden")
    page.locator(".top-task").click()
    expect(page.locator(".task-edit-modal[open] [id^=task-edit-start]")).to_have_value("")


def test_new_task_falls_back_to_the_home_page(page, wiki):
    # Should the hidden tasks fail to load, the home page opens with the modal.
    _home(wiki)
    page.route("**/api/tasks/headless", lambda route: route.fulfill(status=500, body="no"))
    page.goto("/page/garden")
    page.locator(".top-task").click()
    page.wait_for_url(re.compile(r"/page/tasks(\?.*)?$"))
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()


def test_new_task_button_on_the_home_page(page, wiki):
    _home(wiki)
    page.goto("/page/tasks")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip", has_text="task")).to_have_count(1)
    assert page.url.endswith("/page/tasks")


def test_new_task_on_another_tasks_gets_the_homes_tags(page, wiki):
    # A page with another task list (here /tasks, every task): the modal opens
    # there, but with the home's tags and keep, not that task list's filter.
    _home(wiki, "[[tasks: list=shop keep=1 home=1]]")
    page.goto("/tasks?tag=garden")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip")).to_have_count(1)
    expect(modal.locator(".tag-chip")).to_contain_text("shop")
    expect(modal.locator("[id^=task-edit-keep]")).to_be_checked()
    assert "/tasks" in page.url


def test_new_task_without_a_home_has_no_tags(page, wiki):
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    page.keyboard.press("c")
    page.keyboard.press("t")
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    assert page.url.endswith("/page/garden")
    expect(modal.locator(".tag-chip")).to_have_count(0)


def test_c_t_in_the_editor_tags_like_the_home(page, wiki):
    # The editor has a hidden tasks: the modal opens right there. (Not the
    # old fixed #task: c t once always tagged that.)
    _home(wiki, "[[tasks: list=work home=1]]")
    page.goto("/edit/garden")
    page.locator("#editor-textarea").evaluate("el => el.blur()")
    page.keyboard.press("c")
    page.keyboard.press("t")
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip")).to_have_count(1)
    expect(modal.locator(".tag-chip")).to_contain_text("work")
    assert "/edit/garden" in page.url


def test_g_t_goes_to_the_home_tasks(page, wiki):
    _home(wiki)
    page.goto("/page/garden")
    page.keyboard.press("g")
    page.keyboard.press("t")
    page.wait_for_url("**/page/tasks")
    wiki.write("tasks", "# My tasks\n\n[[tasks: list=task]]\n")   # no home any more
    page.goto("/page/garden")
    page.keyboard.press("g")
    page.keyboard.press("t")
    page.wait_for_url(re.compile(r"/tasks$"))



def test_c_n_opens_a_new_note_and_g_n_g_a_go_to_notes_and_all_tasks(page, wiki):
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    page.keyboard.press("c")
    page.keyboard.press("n")
    expect(page.locator("#modal")).to_be_visible()
    page.goto("/page/garden")
    page.keyboard.press("g")
    page.keyboard.press("n")
    page.wait_for_url("**/notes")
    page.keyboard.press("g")
    page.keyboard.press("a")
    page.wait_for_url(re.compile(r"/tasks$"))


def test_the_keys_from_before_the_rename_still_work(page, wiki):
    # c s, g s and g d were the keys while Notes was called Scratch and Tasks Deck.
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    page.keyboard.press("c")
    page.keyboard.press("s")
    expect(page.locator("#modal")).to_be_visible()
    page.goto("/page/garden")
    page.keyboard.press("g")
    page.keyboard.press("s")
    page.wait_for_url("**/notes")
    page.keyboard.press("g")
    page.keyboard.press("d")
    page.wait_for_url(re.compile(r"/tasks$"))

# ── Module switcher on desktop ───────────────────────────────────────────────
# The breadcrumb starts with the modules: Wiki (with the logo), Tasks, Notes
# (with users two pills: Wiki · Tasks and Private · Notes).

def test_desktop_breadcrumb_switches_modules(page, wiki):
    wiki.write("garden", "# Garden\n\nBeds.\n")
    page.goto("/page/garden")
    switch = page.locator(".breadcrumb .module-switch .module-link")
    expect(switch.locator(".module-label")).to_have_text(["Wiki", "Tasks", "Notes"])
    expect(switch.nth(0)).to_have_attribute("aria-current", "page")
    # One line with the page title after it.
    boxes = [m.bounding_box() for m in switch.all()]
    title = page.locator(".breadcrumb > span", has_text="Garden").bounding_box()
    assert all(abs(b["y"] - boxes[0]["y"]) < 3 for b in boxes)
    assert boxes[-1]["x"] + boxes[-1]["width"] < title["x"]
    page.locator(".breadcrumb .module-link[data-module=tasks]").click()
    page.wait_for_url("**/tasks")
    expect(page.locator(".breadcrumb .module-link[data-module=tasks]")).to_have_attribute("aria-current", "page")
    page.locator(".breadcrumb .module-link[data-module=notes]").click()
    page.wait_for_url("**/notes")
    # The navigation trail keeps the page, but not the modules' own pages.
    expect(page.locator(".breadcrumb > a")).to_have_text(["Garden"])


def test_desktop_switcher_is_set_apart_from_the_trail(page, wiki):
    # The switcher is a control, not a crumb: a bordered pill group, then a
    # thin vertical divider instead of "›" before the pages.
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    switch = page.locator(".breadcrumb .module-switch")
    assert switch.locator(".module-group").evaluate("el => getComputedStyle(el).borderTopWidth") == "1px"
    current = switch.locator(".module-link[aria-current]")
    assert current.evaluate("el => getComputedStyle(el).backgroundColor") != "rgba(0, 0, 0, 0)"
    divider = page.locator(".breadcrumb .module-switch + .breadcrumb-sep")
    d = divider.bounding_box()
    assert d["width"] <= 1.5 and d["height"] >= 10, d
    s, t = switch.bounding_box(), page.locator(".breadcrumb > span", has_text="Garden").bounding_box()
    assert s["x"] + s["width"] < d["x"] < t["x"]


def test_desktop_breadcrumb_tasks_link_shows_the_due_count(page, wiki):
    page.request.post("/api/tasks", data={"title": "Overdue", "due_date": "2020-01-01"})
    page.request.post("/api/tasks", data={"title": "Due today", "due_date": date.today().isoformat()})
    page.goto("/page/index")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks.locator(".module-due")).to_have_text("2")
    expect(tasks.locator(".module-focus")).to_have_text("2")
    expect(tasks).to_have_attribute("title", "Tasks: 2 in Focus · 1 overdue · 1 due today")


def test_task_counts_are_in_the_page_before_any_script(page, wiki):
    # Regression: the counts came from a fetch after the page had painted, so
    # the Tasks link grew a moment later on every page load (flicker). The
    # server renders them; with scripts off they are there all the same.
    page.request.post("/api/tasks", data={"title": "Pay rent", "due_date": date.today().isoformat()})
    page.request.post("/api/tasks", data={"title": "Water plants", "start_date": date.today().isoformat()})
    page.route("**/public/zonewiki.js", lambda route: route.abort())
    page.route("**/api/tasks/due-count", lambda route: route.abort())
    page.goto("/page/index")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks.locator(".module-due")).to_have_text("1")
    expect(tasks.locator(".module-focus")).to_have_text("2")
    expect(tasks).to_have_attribute("title", "Tasks: 2 in Focus · 1 due today")


def test_task_counts_follow_tasks_changes(page, wiki):
    # A task started from the tasks shows up in the Focus count at once.
    page.request.post("/api/tasks", data={"title": "Water plants"})
    page.goto("/tasks?view=open")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks.locator(".module-focus")).to_be_hidden()
    page.locator(".task-item").first.click()
    page.keyboard.press("f")   # start today
    expect(tasks.locator(".module-focus")).to_have_text("1")


def test_tasks_home_page_is_the_tasks_module(page, wiki):
    # [[tasks: … home=1]]: your own tasks. The Tasks links go there, it counts
    # as the Tasks module, New task adds one in its embed (with its list tag),
    # and it is no stop in the navigation trail.
    wiki.write("tasks", "# My tasks\n\n[[tasks: list=task view=focus home=1]]\n")
    wiki.write("garden", "# Garden\n\nBeds.\n")
    page.request.post("/api/tasks", data={"title": "Milk", "tags": "shop", "due_date": date.today().isoformat()})
    page.request.post("/api/tasks", data={"title": "Pay rent", "tags": "task", "due_date": date.today().isoformat()})
    page.goto("/page/garden")
    tasks = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(tasks.locator(".module-due")).to_have_text("1")   # the #task list's only
    tasks.click()
    page.wait_for_url("**/page/tasks")
    expect(tasks).to_have_attribute("aria-current", "page")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip", has_text="task")).to_have_count(1)
    page.keyboard.press("Escape")
    page.goto("/page/garden")
    expect(page.locator(".breadcrumb > a")).to_have_count(0)   # no "My tasks" stop


def test_editor_breadcrumb_keeps_only_the_logo(page, wiki):
    page.goto("/edit/index")
    expect(page.locator(".breadcrumb .module-link[data-module=wiki] svg")).to_be_visible()
    expect(page.locator(".breadcrumb .module-link[data-module=tasks]")).to_be_hidden()
    for label in page.locator(".breadcrumb .module-label").all():
        expect(label).to_be_hidden()


def test_desktop_sign_out_clear_of_the_buttons(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")
    page.request.post("/login", form={"password": "pw"})
    page.goto("/page/index")
    signout = page.get_by_role("button", name="Sign out")
    expect(signout).to_be_visible()
    assert not _overlap(signout.bounding_box(), page.locator(".top-actions").bounding_box())


def test_desktop_toast_clear_of_the_buttons(page, wiki):
    wiki.write("todo", "# Todo\n\n- [ ] Call the plumber\n")
    page.goto("/page/todo")
    page.locator(".task-list-item-checkbox").check()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_be_visible()
    assert not _overlap(toast.bounding_box(), page.locator(".top-actions").bounding_box())


def test_no_action_buttons_in_desktop_editor(page, wiki):
    page.goto("/edit/index")
    expect(page.locator(".action-buttons")).to_have_count(0)


# ── Status badges on phones ──────────────────────────────────────────────────

def test_badges_do_not_cover_breadcrumb_on_phone(new_page, wiki):
    # Regression: the fixed badges sat on top of "Zone ›" on narrow screens.
    wiki.write("draft", "# Draft\n\nNot committed yet.\n")   # → uncommitted badge
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/draft")
    badge = phone.locator("#uncommitted-badge")
    expect(badge).to_be_visible()
    assert not _overlap(badge.bounding_box(), phone.locator(".breadcrumb").bounding_box())


# ── Compact Plan board ───────────────────────────────────────────────────────

def test_empty_sections_are_single_rows(page, wiki):
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    page.request.post("/api/tasks", data={"title": "Today's", "start_date": date.today().isoformat()})
    page.goto("/page/tasks?view=open")
    open_tab(page, "plan")
    empties = page.locator(".board-day-divider.board-day--empty")
    expect(empties).to_have_count(8)   # Tomorrow + five more days + Later + Unplanned
    for row in empties.all():
        h = row.bounding_box()["height"]
        assert 28 <= h <= 36, h
    # Today is the only section with a task, so it is the only non-empty one.
    expect(page.locator(".board-day-divider:not(.board-day--empty)")).to_have_count(1)
    # A week of empty days fits in well under 300px (Unplanned, above Today,
    # is left out: the days and Later run below Today).
    below = page.locator(".board-day-divider.board-day--empty:not([data-drop-bucket='unplanned'])")
    first, last = below.first.bounding_box(), below.last.bounding_box()
    assert last["y"] + last["height"] - first["y"] < 300


def test_empty_sections_are_finger_sized_on_touch(new_page, wiki):
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/tasks?view=open")
    open_tab(phone, "plan")
    assert all(r.bounding_box()["height"] >= 40
               for r in phone.locator(".board-day--empty").all())


def test_board_layout_does_not_change_when_a_drag_starts(new_page, wiki):
    # Nothing may grow or shift when a card is picked up: the section you
    # aim at has to stay where it was.
    wiki.write("tasks", "# Tasks\n\n[[tasks:]]\n")
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/tasks", data={"title": "Water plants",
                                          "start_date": date.today().isoformat()})
    phone.goto("/page/tasks?view=open")
    open_tab(phone, "plan")
    rows = phone.locator(".board-day-divider")
    before = [r.bounding_box() for r in rows.all()]
    card = phone.locator(".task-item").filter(has=phone.get_by_text("Water plants", exact=True))
    send = touch_drag(phone, card, rows.nth(3), release=False)
    expect(phone.locator(".task-touch-ghost")).to_have_count(1)
    assert [r.bounding_box() for r in rows.all()] == before
    send("touchEnd", [])


# ── Sign out on phones ───────────────────────────────────────────────────────

def test_sign_out_does_not_cover_the_phone_buttons(new_page, wiki, monkeypatch):
    # Regression: Sign out sat in the bottom-right corner, under the New note
    # and Search buttons. With the tab bar along the bottom edge it sits at
    # the top, in the row of status badges.
    monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"password": "pw"})
    phone.goto("/page/index")
    signout = phone.get_by_role("button", name="Sign out")
    expect(signout).to_be_visible()
    s, a = signout.bounding_box(), phone.locator(".action-buttons").bounding_box()
    assert s["y"] + s["height"] <= a["y"], (s, a)
    assert s["x"] + s["width"] <= 390 and s["y"] < 100
    assert not _overlap(s, phone.locator(".breadcrumb").bounding_box())


def test_breadcrumb_crumbs_wait_for_the_trail_so_they_dont_jump(page, wiki):
    # Regression: the trail comes from sessionStorage and js/chrome.js inserts it
    # after the first paint, so the page's title crumb jumped right on every
    # page change. Until the script has run, the crumbs after the switcher are
    # invisible (they keep their room); the script marks the breadcrumb ready.
    wiki.write("garden", "# Garden\n")
    wiki.write("beds", "# Beds\n")
    page.goto("/page/garden")
    page.goto("/page/beds")
    title = page.locator(".breadcrumb > span", has_text="Beds")
    expect(page.locator(".breadcrumb")).to_have_class(re.compile(r"\btrail-ready\b"))
    expect(title).to_be_visible()
    expect(page.locator(".breadcrumb .trail-link")).to_have_text(["Garden"])
    page.route("**/public/zonewiki.js", lambda route: route.abort())
    page.goto("/page/beds")
    expect(page.locator(".breadcrumb")).not_to_have_class(re.compile(r"\btrail-ready\b"))
    assert title.evaluate("el => getComputedStyle(el).visibility") == "hidden"
    expect(title).to_be_visible(timeout=3000)   # the fallback shows it after a second
