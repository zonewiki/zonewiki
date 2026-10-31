"""Tasks filter field, commit link, phone editor menu, card swipes, checkbox undo."""

import re
from datetime import date, timedelta

from playwright.sync_api import expect

from conftest import TOUCH_PHONE


def card(page, title):
    return page.locator(".task-item").filter(has=page.get_by_text(title, exact=True))


def titles(page):
    return page.locator(".task-item .task-title").all_text_contents()


def task(page, title):
    return next(t for t in page.request.get("/api/deck").json() if t["title"] == title)


def wait_until(page, predicate, timeout_ms=5000):
    for _ in range(timeout_ms // 100):
        if predicate():
            return
        page.wait_for_timeout(100)
    assert predicate()


def seed_tasks(page, wiki, embed="[[deck]]"):
    for title, tags in (("Fix bug", "work"), ("Buy milk", "home"), ("Fix fence", "home,work")):
        page.request.post("/api/deck", data={"title": title, "tags": tags})
    wiki.write("tasks", f"# Tasks\n\n{embed}\n")
    page.goto("/page/tasks?view=open")


# ── 4. One filter field: text plus #tags ─────────────────────────────────────

def filter_chips(page):
    """The filter field's chips (completed tag words), as their labels."""
    return page.locator(".tasks-toolbar .tag-chip-label")


def tag_chips(page):
    """The edit modal Tags field's chips."""
    return page.locator("#task-edit-modal .tag-chip-label")

def test_filter_field_text_and_tags(page, wiki):
    seed_tasks(page, wiki)
    field = page.locator("#task-filter-search")
    expect(page.locator("#task-filter-tag")).to_have_count(0)
    field.fill("#work")
    expect(page.locator(".task-item")).to_have_count(2)
    field.fill("#work #home")
    expect(page.locator(".task-item .task-title")).to_have_text(["Fix fence"])
    field.fill("#work|home milk")
    expect(page.locator(".task-item .task-title")).to_have_text(["Buy milk"])


def test_filter_field_tag_autocomplete(page, wiki):
    seed_tasks(page, wiki)
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("fix #ho")
    ac = page.locator(".tag-ac-dropdown")
    expect(ac.locator(".tag-ac-item")).to_have_text(["#home"])
    field.press("Enter")   # the first (best) match is preselected
    expect(filter_chips(page)).to_have_text(["#home"])   # a completed tag becomes a chip
    expect(field).to_have_value("fix ")
    expect(page.locator(".task-item .task-title")).to_have_text(["Fix fence"])
    # Tags already in the filter are not suggested again.
    field.press_sequentially("#")
    expect(ac.locator(".tag-ac-item")).to_have_text(["#work"])


def test_tag_suggestions_after_free_text(page, wiki):
    # Regression: suggestions came only from tasks matching the whole rest of
    # the field, text included, so "some text #ho" (text matching no task yet)
    # suggested nothing. Free text no longer narrows the suggestions.
    seed_tasks(page, wiki)
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("some text #ho")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item")).to_have_text(["#home"])
    field.press("Enter")
    expect(filter_chips(page)).to_have_text(["#home"])
    expect(field).to_have_value("some text ")
    # Other tag words still narrow them: with #home set, only #work remains
    # (it co-occurs with home), and home itself isn't offered twice.
    field.press_sequentially("#")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item")).to_have_text(["#work"])


def test_hash_key_starts_a_tag_word(page, wiki):
    seed_tasks(page, wiki)
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("#")
    field = page.locator("#task-filter-search")
    expect(field).to_be_focused()
    expect(field).to_have_value("#")


def test_embed_tag_param_lands_in_filter_field(page, wiki):
    seed_tasks(page, wiki, "[[deck tag=home,work]]")
    expect(filter_chips(page)).to_have_text(["#home", "#work"])
    expect(page.locator("#task-filter-search")).to_have_value("")
    expect(page.locator(".task-item .task-title")).to_have_text(["Fix fence"])


def test_new_task_takes_tags_from_filter(page, wiki):
    seed_tasks(page, wiki)
    page.locator("#task-filter-search").fill("#work|home #urgent text")
    page.get_by_role("button", name="New task").click()
    # Only unambiguous (single-tag) words become default tags.
    expect(tag_chips(page)).to_have_text(["#urgent"])
    expect(page.locator("#task-edit-tags")).to_have_value("")


# ── 5. Commit as a word on the meta line ─────────────────────────────────────

def test_commit_link_shares_the_meta_line_on_phone(new_page, wiki):
    wiki.write("draft", "# Draft\n\nNot committed.\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/draft")
    meta = phone.locator(".page-meta").bounding_box()
    link = phone.locator(".commit-link").bounding_box()
    assert abs((link["y"] + link["height"] / 2) - (meta["y"] + meta["height"] / 2)) < 6


# ── 6. Phone editor: Rename/Delete behind ⋯ ──────────────────────────────────

def test_phone_editor_toolbar_fits_with_more_menu(new_page, wiki):
    wiki.write("notes", "# Notes\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/edit/notes")
    bar = phone.locator(".editor-toolbar")
    assert bar.evaluate("el => el.scrollWidth <= el.clientWidth")
    expect(phone.locator("#delete-btn")).to_be_hidden()
    more = phone.locator("#editor-more-btn")
    more.tap()
    expect(more).to_have_attribute("aria-expanded", "true")
    expect(phone.locator("#delete-btn")).to_be_visible()
    phone.locator("#rename-toggle").tap()
    expect(phone.locator("#rename-bar")).to_be_visible()
    expect(phone.locator("#delete-btn")).to_be_hidden()   # the menu closed


def test_desktop_editor_keeps_inline_buttons(page, wiki):
    wiki.write("notes", "# Notes\n")
    page.goto("/edit/notes")
    expect(page.locator("#editor-more-btn")).to_be_hidden()
    expect(page.locator("#delete-btn")).to_be_visible()
    expect(page.locator("#rename-toggle")).to_be_visible()


# ── 7a. Swipe a card: in and out of Focus ─────────────────────────────────────

def swipe(page, locator, dx, dy=0, steps=8, release=True):
    cdp = page.context.new_cdp_session(page)
    b = locator.bounding_box()
    x0, y0 = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def send(kind, points):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    send("touchStart", [{"x": x0, "y": y0}])
    for i in range(1, steps + 1):
        send("touchMove", [{"x": x0 + dx * i / steps, "y": y0 + dy * i / steps}])
        page.wait_for_timeout(16)
    if release:
        send("touchEnd", [])
    return send


def phone_tasks(new_page, wiki, view="open", **fields):
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/deck", data={"title": "Water plants", **fields})
    wiki.write("tasks", "# Tasks\n\n[[deck]]\n")
    phone.goto(f"/deck?view={view}")
    return phone


TODAY = date.today().isoformat()
NEXT_WEEK = (date.today() + timedelta(days=7)).isoformat()


def test_swipe_right_on_open_starts_today(new_page, wiki):
    phone = phone_tasks(new_page, wiki)
    c = card(phone, "Water plants")
    send = swipe(phone, c, 200, release=False)
    expect(c).to_have_class(re.compile(r"\btask-swipe-armed\b"))
    assert c.get_attribute("data-swipe-label") == "📅 Start today"
    send("touchEnd", [])
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] == TODAY)
    expect(phone.locator(".task-undo-toast")).to_contain_text("Starts")
    expect(phone.locator("#task-edit-modal")).to_be_hidden()   # not also a tap


def test_swipe_left_on_open_does_nothing(new_page, wiki):
    phone = phone_tasks(new_page, wiki, start_date=NEXT_WEEK)
    c = card(phone, "Water plants")
    send = swipe(phone, c, -200, release=False)
    assert c.evaluate("el => el.style.transform") in ("", "translateX(0px)")
    expect(c).not_to_have_class(re.compile(r"\btask-swipe-armed\b"))
    send("touchEnd", [])
    phone.wait_for_timeout(300)
    assert task(phone, "Water plants")["start_date"] == NEXT_WEEK


def test_swipe_right_on_open_task_already_in_focus_does_nothing(new_page, wiki):
    phone = phone_tasks(new_page, wiki, start_date=(date.today() - timedelta(days=3)).isoformat())
    c = card(phone, "Water plants")
    send = swipe(phone, c, 200, release=False)
    expect(c).not_to_have_class(re.compile(r"\btask-swipe-armed\b"))
    send("touchEnd", [])
    phone.wait_for_timeout(300)
    assert task(phone, "Water plants")["start_date"] != TODAY


def test_swipe_right_on_plan_commits_carried_over_task_again(new_page, wiki):
    phone = phone_tasks(new_page, wiki, view="plan",
                       start_date=(date.today() - timedelta(days=3)).isoformat())
    c = card(phone, "Water plants")
    send = swipe(phone, c, 200, release=False)
    expect(c).to_have_class(re.compile(r"\btask-swipe-armed\b"))
    send("touchEnd", [])
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] == TODAY)


def test_swipe_left_on_focus_moves_back_to_open(new_page, wiki):
    phone = phone_tasks(new_page, wiki, view="focus", start_date=TODAY)
    c = card(phone, "Water plants")
    send = swipe(phone, c, -200, release=False)
    expect(c).to_have_class(re.compile(r"\btask-swipe-armed\b"))
    assert c.get_attribute("data-swipe-label") == "↩ Unplan"
    send("touchEnd", [])
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] is None)
    expect(c).to_have_count(0)   # left Focus
    expect(phone.locator(".task-undo-toast")).to_contain_text("Start date cleared")


def test_swipe_right_on_focus_does_nothing(new_page, wiki):
    phone = phone_tasks(new_page, wiki, view="focus", start_date=TODAY)
    c = card(phone, "Water plants")
    swipe(phone, c, 200)
    phone.wait_for_timeout(300)
    expect(c).not_to_have_class(re.compile(r"\btask-completing\b"))   # no swipe-to-done
    t = task(phone, "Water plants")
    assert t["start_date"] == TODAY and not t["completed_at"]


def test_due_task_cannot_be_swiped_out_of_focus(new_page, wiki):
    # Clearing the start date would leave it in Focus: the deadline keeps it.
    phone = phone_tasks(new_page, wiki, view="focus", start_date=TODAY, due_date=TODAY)
    c = card(phone, "Water plants")
    send = swipe(phone, c, -200, release=False)
    expect(c).not_to_have_class(re.compile(r"\btask-swipe-armed\b"))
    send("touchEnd", [])
    phone.wait_for_timeout(300)
    assert task(phone, "Water plants")["start_date"] == TODAY


def test_swipes_on_plan_go_both_ways(new_page, wiki):
    phone = phone_tasks(new_page, wiki, view="plan", start_date=NEXT_WEEK)
    swipe(phone, card(phone, "Water plants"), 200)
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] == TODAY)
    swipe(phone, card(phone, "Water plants"), -200)
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] is None)


def test_short_swipe_does_nothing(new_page, wiki):
    phone = phone_tasks(new_page, wiki)
    c = card(phone, "Water plants")
    swipe(phone, c, 40)
    phone.wait_for_timeout(300)
    assert task(phone, "Water plants")["start_date"] is None
    assert c.evaluate("el => el.style.transform") == ""


def test_vertical_move_is_not_a_swipe(new_page, wiki):
    phone = phone_tasks(new_page, wiki)
    c = card(phone, "Water plants")
    swipe(phone, c, 30, dy=120)
    phone.wait_for_timeout(300)
    assert task(phone, "Water plants")["start_date"] is None


def test_swipe_does_not_scroll_page_sideways(new_page, wiki):
    phone = phone_tasks(new_page, wiki)
    swipe(phone, card(phone, "Water plants"), 250, release=False)
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390


# ── 7b. Page-view checkbox: undo toast ───────────────────────────────────────

def test_checkbox_toggle_offers_undo(page, wiki):
    wiki.write("todo", "# Todo\n\n- [ ] Call the plumber\n")
    page.goto("/page/todo")
    page.locator(".task-list-item-checkbox").check()
    toast = page.locator(".task-undo-toast")
    expect(toast).to_contain_text("Checked “Call the plumber”")
    wait_until(page, lambda: "- [x] Call the plumber" in wiki.read("todo"))
    toast.get_by_role("button", name="Undo").click()
    expect(page.locator(".task-list-item-checkbox")).not_to_be_checked()
    wait_until(page, lambda: "- [ ] Call the plumber" in wiki.read("todo"))


def test_quick_undo_never_overtakes_the_toggle(page, wiki):
    # Undo straight after the tap: the requests are chained, so the file ends
    # up where it started, not double-toggled out of order.
    wiki.write("todo", "# Todo\n\n- [ ] Call the plumber\n")
    page.goto("/page/todo")
    page.locator(".task-list-item-checkbox").check()
    page.locator(".task-undo-toast").get_by_role("button", name="Undo").click()
    page.wait_for_timeout(500)
    assert "- [ ] Call the plumber" in wiki.read("todo")


def test_a_tick_on_a_page_changed_since_finds_its_item(page, wiki):
    # Someone added an item above after the page was loaded: the tick goes to
    # the one clicked, by its line; one no longer there once says so.
    wiki.write("todo", "# Todo\n\n- [ ] Call the plumber\n- [ ] Buy milk\n")
    page.goto("/page/todo")
    wiki.write("todo", "# Todo\n\n- [ ] New one\n- [ ] Call the plumber\n- [ ] Buy milk\n")
    page.locator(".task-list-item-checkbox").nth(1).check()
    wait_until(page, lambda: "- [x] Buy milk" in wiki.read("todo"))
    assert "- [ ] New one" in wiki.read("todo")
    wiki.write("todo", "# Todo\n\n- [ ] Call the plumber today\n")
    page.locator(".task-list-item-checkbox").nth(0).check()
    toast = page.locator(".task-undo-toast", has_text="The page changed")
    expect(toast).to_be_visible()
    expect(page.locator(".task-list-item-checkbox").nth(0)).not_to_be_checked()
    toast.get_by_role("button", name="Reload").click()
    expect(page.locator(".task-list-item")).to_have_text(["Call the plumber today"])


# ── Tag completion: the best match is preselected everywhere ─────────────────

def seed_tags(page, wiki):
    """work on 3 tasks, workshop and wood on 1 each, home on 1."""
    for title, tags in (("A", "work"), ("B", "work"), ("C", "work,workshop"),
                        ("D", "wood"), ("E", "home")):
        page.request.post("/api/deck", data={"title": title, "tags": tags})
    wiki.write("tasks", "# Tasks\n\n[[deck]]\n")
    page.goto("/page/tasks?view=open")


def test_filter_tab_completes_to_best_match(page, wiki):
    seed_tags(page, wiki)
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("#wo")
    items = page.locator(".tag-ac-dropdown .tag-ac-item")
    # Prefix matches, most-used first; the first is highlighted from the start.
    expect(items).to_have_text(["#work", "#wood", "#workshop"])
    expect(items.first).to_have_class(re.compile(r"\bselected\b"))
    field.press("Tab")
    expect(filter_chips(page)).to_have_text(["#work"])
    expect(field).to_have_value("")
    expect(field).to_be_focused()


def test_exact_match_comes_first(page, wiki):
    seed_tags(page, wiki)
    for title in ("F", "G", "H", "I"):   # workshop now more used than work
        page.request.post("/api/deck", data={"title": title, "tags": "workshop"})
    page.reload()
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("#work")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item").first).to_have_text("#work")


def test_hover_moves_the_highlight(page, wiki):
    seed_tags(page, wiki)
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("#wo")
    items = page.locator(".tag-ac-dropdown .tag-ac-item")
    items.nth(1).hover()
    expect(items.nth(1)).to_have_class(re.compile(r"\bselected\b"))
    expect(items.first).not_to_have_class(re.compile(r"\bselected\b"))


def test_edit_modal_tags_complete_and_chain(page, wiki):
    seed_tags(page, wiki)
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    modal.locator("#task-edit-title").fill("Chained")
    tags = modal.locator("#task-edit-tags")
    tags.click()
    tags.press_sequentially("wo")
    tags.press("Enter")                      # completes to a chip; does not save
    expect(tag_chips(page)).to_have_text(["#work"])
    expect(tags).to_have_value("")           # no ", " after it
    expect(modal).to_be_visible()
    tags.press_sequentially("ho")
    tags.press("Tab")
    expect(tag_chips(page)).to_have_text(["#work", "#home"])
    # Esc with the dropdown open closes the dropdown, not the dialog.
    tags.press_sequentially("wo")
    tags.press("Escape")
    expect(page.locator(".tag-ac-dropdown")).to_have_count(0)
    expect(modal).to_be_visible()
    tags.fill("")
    modal.get_by_role("button", name="Save").click()
    expect(page.locator(".task-item").filter(has_text="Chained")).to_be_visible()
    t = next(t for t in page.request.get("/api/deck").json() if t["title"] == "Chained")
    assert t["tags"] == ["work", "home"]


def test_title_shorthand_tag_is_suggested(page, wiki):
    seed_tags(page, wiki)
    page.locator("#task-add-btn").click()
    title = page.locator("#task-edit-title")
    title.click()
    title.press_sequentially("Call Bob #wo")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item").first).to_have_text("#work")
    title.press("Tab")
    expect(title).to_have_value("Call Bob #work ")
    expect(page.locator("#task-edit-quick")).to_have_text("#work")


def test_bulk_remove_suggests_only_tags_of_selected_tasks(page, wiki):
    seed_tags(page, wiki)
    card = page.locator(".task-item").filter(has=page.get_by_text("D", exact=True))
    card.locator(".task-title").click()          # select the "wood" task
    page.keyboard.press("Shift+T")               # remove-tag dialog
    inp = page.locator("#bulk-tag-input")
    expect(inp).to_be_focused()
    inp.press_sequentially("w")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item")).to_have_text(["#wood"])
    inp.press("Enter")                           # completes and applies
    expect(inp).to_have_count(0)
    # The dialog closes before the request finishes: wait for the card.
    expect(card.locator(".task-tag")).to_have_count(0)
    t = next(t for t in page.request.get("/api/deck").json() if t["title"] == "D")
    assert t["tags"] == []


def test_tag_with_space_is_findable_in_filter(page, wiki):
    # Regression: a tag saved as "locallm xxx" never matched "#locallm xxx",
    # since the filter splits on spaces. Tags are now one word each.
    page.request.post("/api/deck", data={"title": "Try models", "tags": "locallm xxx"})
    page.request.post("/api/deck", data={"title": "Other"})
    wiki.write("tasks", "# Tasks\n\n[[deck]]\n")
    page.goto("/page/tasks?view=open")
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("#locallm")
    field.press("Tab")
    expect(filter_chips(page)).to_have_text(["#locallm-xxx"])
    expect(page.locator(".task-item .task-title")).to_have_text(["Try models"])


def test_tags_field_takes_several_tags_at_once(page, wiki):
    # Regression: only a comma made a chip; a space became a hyphen ("a b"
    # one tag "a-b") and Enter on a new tag saved the task. Tags are one
    # word each, so a comma, a space or Enter ends one.
    seed_tags(page, wiki)
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    modal.locator("#task-edit-title").fill("Several")
    tags = modal.locator("#task-edit-tags")
    tags.click()
    tags.press_sequentially("alpha beta,gamma ")
    expect(tag_chips(page)).to_have_text(["#alpha", "#beta", "#gamma"])
    expect(tags).to_have_value("")
    tags.press_sequentially("brandnew")
    expect(page.locator(".tag-ac-dropdown")).to_have_count(0)   # nothing to complete
    tags.press("Enter")                                         # a chip, not Save
    expect(tag_chips(page)).to_have_text(["#alpha", "#beta", "#gamma", "#brandnew"])
    expect(modal).to_be_visible()
    # Paste: "#a #b, c" — the part after the last separator stays to be
    # typed on, and Save takes it too.
    tags.evaluate("el => { el.value = '#a  #b, c'; el.dispatchEvent(new Event('input')); }")
    expect(tag_chips(page)).to_have_text(["#alpha", "#beta", "#gamma", "#brandnew", "#a", "#b"])
    expect(tags).to_have_value("c")
    modal.get_by_role("button", name="Save").click()
    expect(page.locator(".task-item").filter(has_text="Several")).to_be_visible()
    t = next(t for t in page.request.get("/api/deck").json() if t["title"] == "Several")
    assert t["tags"] == ["alpha", "beta", "gamma", "brandnew", "a", "b", "c"]

    # The bulk tag dialog too: a space makes a chip.
    card = page.locator(".task-item").filter(has=page.get_by_text("E", exact=True))
    card.locator(".task-title").click()
    page.keyboard.press("t")
    inp = page.locator("#bulk-tag-input")
    inp.press_sequentially("new tag")
    expect(page.locator(".bulk-tag-modal .tag-chip-label")).to_have_text(["#new"])
    expect(inp).to_have_value("tag")


def test_c_t_with_a_task_selected_opens_only_new_task(page, wiki):
    # The t of the c t shortcut is not the task app's t (tag the selected tasks).
    seed_tags(page, wiki)
    card = page.locator(".task-item").filter(has=page.get_by_text("E", exact=True))
    card.locator(".task-title").click()
    page.keyboard.press("c")
    page.keyboard.press("t")
    expect(page.locator("#task-edit-modal")).to_be_visible()
    expect(page.locator("#bulk-tag-input")).not_to_be_visible()


def test_backspace_in_tags_field(page, wiki):
    # A chip can't be edited: one Backspace at the start of the field removes
    # it whole. Inside the typed text, Backspace deletes characters as usual.
    seed_tags(page, wiki)
    page.locator("#task-add-btn").click()
    tags = page.locator("#task-edit-tags")
    tags.click()
    tags.press_sequentially("zz,yy,")
    expect(tag_chips(page)).to_have_text(["#zz", "#yy"])
    tags.press("Backspace")
    expect(tag_chips(page)).to_have_text(["#zz"])
    tags.press_sequentially("abc")
    for _ in range(2):
        tags.press("Backspace")
    expect(tags).to_have_value("a")
    expect(tag_chips(page)).to_have_text(["#zz"])
    # The × removes a chip too.
    page.locator("#task-edit-modal .tag-chip-x").click()
    expect(tag_chips(page)).to_have_count(0)


def test_swipe_starting_on_a_date(new_page, wiki):
    # The dates sit on a card's first line, where a thumb starts a swipe: a
    # swipe from there works, and doesn't also open the date picker.
    phone = phone_tasks(new_page, wiki, view="plan", start_date=NEXT_WEEK)
    chip = card(phone, "Water plants").locator(".task-clickable-date[data-field='start_date']")
    swipe(phone, chip, 150)
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] == TODAY)
    expect(phone.locator(".tasks-datepicker")).to_have_count(0)
