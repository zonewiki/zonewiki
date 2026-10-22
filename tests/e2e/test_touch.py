"""Deck on a touch phone: tap to edit, the ⋯ menu as a bottom sheet."""

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from conftest import TOUCH_PHONE, open_tab


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def pick_day(picker, d: str):
    """Tap date `d` in an open date picker. The picker opens on the current
    month, so near a month's end the target is a page further (tests picking
    today + N used to fail on a month's last days)."""
    if d[:7] != day(0)[:7]:
        picker.locator(".dp-nav[data-nav='1']").tap()
    picker.locator(f".dp-day[data-date='{d}']").tap()


def tasks_by_title(page) -> dict:
    return {t["title"]: t for t in page.request.get("/api/deck").json()}


def wait_until(page, predicate, timeout_ms=5000):
    for _ in range(timeout_ms // 100):
        if predicate():
            return
        page.wait_for_timeout(100)
    assert predicate()


def card(page, title):
    return page.locator(".task-item").filter(has=page.get_by_text(title, exact=True))


@pytest.fixture()
def phone(new_page, wiki):
    pg = new_page(**TOUCH_PHONE)
    pg.request.post("/api/deck", data={"title": "Water plants", "start_date": day(0)})
    pg.request.post("/api/deck", data={"title": "Weekly review",
                                       "rec_type": "weekly", "due_date": day(2)})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck?view=open")
    return pg


def open_menu(page, title):
    card(page, title).locator(".task-menu-btn").tap()
    menu = page.locator(".task-menu")
    expect(menu).to_be_visible()
    return menu


def test_first_tap_selects_second_opens_editor(phone):
    water = card(phone, "Water plants")
    water.locator(".task-title").tap()
    expect(water).to_contain_class("task-selected")
    expect(phone.locator("#task-edit-modal")).to_be_hidden()
    card(phone, "Weekly review").locator(".task-title").tap()          # another card: selects it
    expect(phone.locator("#task-edit-modal")).to_be_hidden()
    expect(water).not_to_contain_class("task-selected")
    water.locator(".task-title").tap()
    expect(phone.locator("#task-edit-modal")).to_be_hidden()
    water.locator(".task-title").tap()                                  # the selected one: opens
    expect(phone.locator("#task-edit-modal")).to_be_visible()
    # A double tap is just these two taps: one modal, nothing else.
    expect(phone.locator("#task-edit-title")).to_have_value("Water plants")


def test_tapping_checkbox_completes_not_edits(phone):
    card(phone, "Water plants").locator("input[type=checkbox]").tap()
    expect(phone.locator("#task-edit-modal")).to_be_hidden()
    expect(card(phone, "Water plants")).to_contain_class("task-completing")


def test_hover_icons_hidden_menu_button_is_a_real_target(phone):
    expect(card(phone, "Weekly review").locator(".task-wontdo-btn")).to_have_count(0)
    expect(card(phone, "Weekly review").locator(".task-skip-btn")).to_be_hidden()
    box = card(phone, "Weekly review").locator(".task-menu-btn").bounding_box()
    assert box["width"] >= 40 and box["height"] >= 40


def test_menu_is_a_bottom_sheet_with_context_items(phone):
    menu = open_menu(phone, "Weekly review")
    items = menu.get_by_role("menuitem")
    expect(items).to_have_text(["Edit…", "Start today", "Start tomorrow", "Pick start date…",
                                "Skip this occurrence", "Won't do", "Copy link", "Select several", "Delete…"])
    box = menu.bounding_box()
    assert box["x"] == 0 and abs(box["y"] + box["height"] - 844) < 1, box
    assert box["width"] == 390
    # Tapping outside (the backdrop) closes it without doing anything.
    phone.touchscreen.tap(200, 100)
    expect(menu).to_have_count(0)
    expect(phone.locator("#task-edit-modal")).to_be_hidden()


def test_menu_plan_and_unplan(phone):
    open_menu(phone, "Weekly review").get_by_role("menuitem", name="Start tomorrow").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Weekly review"]["start_date"] == day(1))
    menu = open_menu(phone, "Weekly review")
    menu.get_by_role("menuitem", name="Unplan").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Weekly review"]["start_date"] is None)
    phone.locator(".task-undo-btn").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Weekly review"]["start_date"] == day(1))


def test_menu_pick_date_opens_picker(phone):
    open_menu(phone, "Water plants").get_by_role("menuitem", name="Pick start date…").tap()
    picker = phone.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    pick_day(picker, day(3))
    wait_until(phone, lambda: tasks_by_title(phone)["Water plants"]["start_date"] == day(3))


def test_menu_skip_and_wont_do(phone):
    open_menu(phone, "Weekly review").get_by_role("menuitem", name="Skip this occurrence").tap()
    # The series continues: a new instance with a later due date replaces it.
    wait_until(phone, lambda: tasks_by_title(phone).get("Weekly review", {}).get("due_date") == day(9))
    open_menu(phone, "Water plants").get_by_role("menuitem", name="Won't do").tap()
    wait_until(phone, lambda: "Water plants" not in tasks_by_title(phone))


def test_menu_delete_confirms_and_undoes(phone):
    open_menu(phone, "Water plants").get_by_role("menuitem", name="Delete…").tap()
    phone.locator("#task-delete-confirm").tap()
    wait_until(phone, lambda: "Water plants" not in tasks_by_title(phone))
    phone.locator(".task-undo-btn").tap()
    wait_until(phone, lambda: "Water plants" in tasks_by_title(phone))


def test_completed_task_menu(phone):
    t = tasks_by_title(phone)["Water plants"]
    phone.request.put(f"/api/deck/{t['id']}", data={"completed": True})
    phone.reload()
    open_tab(phone, "completed")
    menu = open_menu(phone, "Water plants")
    expect(menu.get_by_role("menuitem")).to_have_text(["Edit…", "Reopen", "Copy link", "Select several", "Delete…"])
    menu.get_by_role("menuitem", name="Reopen").tap()
    wait_until(phone, lambda: "Water plants" in tasks_by_title(phone))



# ── Selection mode (⋯ → Select several) ──────────────────────────────────────


@pytest.fixture()
def selecting(new_page, wiki):
    """A phone deck on Open (newest first: Saw, Glue, Nuts, Bolts) in
    selection mode, started from Saw's ⋯ menu."""
    pg = new_page(**TOUCH_PHONE)
    for title in ("Bolts", "Nuts", "Glue", "Saw"):
        pg.request.post("/api/deck", data={"title": title})
    pg.goto("/deck?view=open")
    open_menu(pg, "Saw").get_by_role("menuitem", name="Select several").tap()
    expect(pg.locator(".tasks-app")).to_contain_class("deck-selecting")
    return pg


def selected(page):
    return page.locator(".task-item.task-selected .task-title").all_inner_texts()


def test_select_mode_taps_toggle_cards(selecting):
    pg = selecting
    bar = pg.locator("#task-selbar")
    expect(bar.locator(".deck-selbar-count")).to_have_text("1 selected")
    expect(pg.locator(".action-buttons")).to_be_hidden()        # the bar takes the tab bar's place
    box = bar.bounding_box()
    assert box["x"] == 0 and box["width"] == 390 and abs(box["y"] + box["height"] - 844) < 1, box
    card(pg, "Nuts").locator(".task-title").tap()
    card(pg, "Bolts").locator(".task-checkbox").tap()            # the checkbox selects, not completes
    assert selected(pg) == ["Saw", "Nuts", "Bolts"]
    expect(bar.locator(".deck-selbar-count")).to_have_text("3 selected")
    card(pg, "Saw").locator(".task-title").tap()                 # a second tap drops it, no editor
    assert selected(pg) == ["Nuts", "Bolts"]
    expect(pg.locator("#task-edit-modal")).to_be_hidden()
    assert all(t["completed_at"] is None for t in tasks_by_title(pg).values())
    card(pg, "Nuts").locator(".task-title").tap()
    card(pg, "Bolts").locator(".task-title").tap()
    expect(bar.locator(".deck-selbar-count")).to_have_text("Tap tasks to select")
    expect(bar.locator("[data-sel='tag']")).to_be_disabled()
    bar.locator("[data-sel='all']").tap()
    expect(bar.locator(".deck-selbar-count")).to_have_text("4 selected")


def test_select_mode_has_no_swipes_or_drags(selecting):
    pg = selecting
    touch_drag(pg, card(pg, "Bolts"), card(pg, "Saw"))
    pg.wait_for_timeout(200)
    expect(pg.locator(".task-touch-ghost")).to_have_count(0)
    expect(pg.locator(".task-item .task-title")).to_have_text(["Saw", "Glue", "Nuts", "Bolts"])
    assert all(t["sort_order"] is None for t in tasks_by_title(pg).values())


def test_select_mode_tag_keeps_the_mode(selecting):
    pg = selecting
    card(pg, "Glue").locator(".task-title").tap()
    pg.locator("#task-selbar [data-sel='tag']").tap()
    inp = pg.locator("#bulk-tag-input")
    expect(inp).to_be_focused()                  # no rows to tick: the field takes the keyboard
    inp.fill("shop")
    pg.locator(".bulk-tag-modal").get_by_role("button", name="Apply").tap()
    wait_until(pg, lambda: tasks_by_title(pg)["Glue"]["tags"] == ["shop"])
    assert tasks_by_title(pg)["Saw"]["tags"] == ["shop"]
    expect(pg.locator(".tasks-app")).to_contain_class("deck-selecting")
    assert selected(pg) == ["Saw", "Glue"]


def test_select_mode_done_ends_it(selecting):
    pg = selecting
    card(pg, "Glue").locator(".task-title").tap()
    pg.locator("#task-selbar [data-sel='done']").tap()
    expect(pg.locator(".task-undo-toast")).to_contain_text("2 tasks completed")
    expect(pg.locator(".tasks-app")).not_to_contain_class("deck-selecting")
    expect(pg.locator("#task-selbar")).to_be_hidden()
    expect(pg.locator(".action-buttons")).to_be_visible()
    expect(pg.locator(".task-item .task-title")).to_have_text(["Nuts", "Bolts"])
    # Taps are taps again: the first selects, the second opens the editor.
    card(pg, "Nuts").locator(".task-title").tap()
    card(pg, "Nuts").locator(".task-title").tap()
    expect(pg.locator("#task-edit-modal")).to_be_visible()


@pytest.mark.parametrize("how", ["close", "back", "tab"])
def test_select_mode_ends(selecting, how):
    pg = selecting
    card(pg, "Glue").locator(".task-title").tap()
    if how == "close":
        pg.locator("#task-selbar [data-sel='clear']").tap()
    elif how == "back":
        pg.go_back()                             # a phone's back gesture: leaves the mode, not the page
    else:
        open_tab(pg, "focus")
    expect(pg.locator(".tasks-app")).not_to_contain_class("deck-selecting")
    expect(pg.locator("#task-selbar")).to_be_hidden()
    assert pg.url.endswith("/deck?view=open")
    assert selected(pg) == []
    # The mode's history entry goes with it: back is back to the page before.
    wait_until(pg, lambda: pg.evaluate("history.state?.deckSelect === undefined"))


def test_desktop_click_selects_does_not_edit(page, wiki):
    page.request.post("/api/deck", data={"title": "Water plants"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    card(page, "Water plants").locator(".task-content").click()
    expect(card(page, "Water plants")).to_contain_class("task-selected")
    expect(page.locator("#task-edit-modal")).to_be_hidden()


def test_desktop_menu_popover_and_keyboard(page, wiki):
    # With a mouse ⋯ is always shown; right-click (or the menu key, or
    # Shift+F10 on the selected card) opens the menu as a popover too.
    page.request.post("/api/deck", data={"title": "Water plants"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    page.mouse.move(0, 0)
    expect(card(page, "Water plants").locator(".task-menu-btn")).to_be_visible()
    title = card(page, "Water plants").locator(".task-title")
    tb = title.bounding_box()
    page.mouse.click(tb["x"] + 5, tb["y"] + 5, button="right")
    menu = page.locator(".task-menu")
    # A popover at the pointer, not a bottom sheet.
    mb = menu.bounding_box()
    assert abs(mb["x"] - (tb["x"] + 5)) < 6 and mb["width"] < 400
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem", name="Start today")).to_be_focused()
    page.keyboard.press("Enter")
    expect(menu).to_have_count(0)
    expect(page.locator("#task-edit-modal")).to_be_hidden()   # deck keys stayed out
    wait_until(page, lambda: tasks_by_title(page)["Water plants"]["start_date"] == day(0))
    # Keyboard: Shift+F10 on the selected card; Esc closes.
    expect(card(page, "Water plants")).to_have_class(re.compile(r"\btask-selected\b"))
    page.keyboard.press("Shift+F10")
    expect(menu.locator(".task-menu-title")).to_have_text("Water plants")
    page.keyboard.press("Escape")
    expect(page.locator(".task-menu")).to_have_count(0)
    page.keyboard.press("ContextMenu")
    expect(menu).to_be_visible()


# ── Edit modal on small screens ──────────────────────────────────────────────
# Regression: the modal was centred with a transform and had overflow:visible,
# so on a phone (content ~1270px, screen 844px) everything below the fold —
# including Save — was unreachable: the fixed dialog could not be scrolled.

def open_editor(page, title="Weekly review"):
    card(page, title).locator(".task-title").tap()     # selects
    card(page, title).locator(".task-title").tap()     # opens
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    return modal


def in_viewport(page, locator) -> bool:
    box = locator.bounding_box()
    vp = page.viewport_size
    return box is not None and box["y"] >= 0 and box["y"] + box["height"] <= vp["height"] + 0.5


def test_edit_modal_save_reachable_on_phone(new_page, wiki):
    # A short window (e.g. phone with the keyboard open): the form must scroll.
    phone = new_page(viewport={"width": 360, "height": 460}, has_touch=True, is_mobile=True)
    phone.request.post("/api/deck", data={"title": "Weekly review", "rec_type": "weekly"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    phone.goto("/page/deck?view=open")
    modal = open_editor(phone)
    save = modal.get_by_role("button", name="Save")
    # The action bar sticks to the bottom: Save is on screen without scrolling.
    assert in_viewport(phone, save)
    # And the rest of the form scrolls into reach inside the dialog.
    assert modal.evaluate("d => d.scrollHeight > d.clientHeight && getComputedStyle(d).overflowY === 'auto'")
    last_field = modal.locator("#task-edit-rec-interval")
    last_field.scroll_into_view_if_needed()
    assert in_viewport(phone, last_field)
    assert in_viewport(phone, save)
    # Saving from the bottom works.
    modal.locator("#task-edit-title").fill("Weekly review (edited)")
    save.tap()
    expect(modal).to_be_hidden()
    wait_until(phone, lambda: "Weekly review (edited)" in tasks_by_title(phone))


def test_edit_modal_is_full_screen_on_phone(phone):
    box = open_editor(phone).bounding_box()
    assert box["x"] == 0 and box["y"] == 0 and box["width"] == 390 and abs(box["height"] - 844) < 1


def test_edit_modal_inputs_do_not_trigger_ios_zoom(phone):
    # iOS Safari zooms the page when focusing an input with font-size < 16px.
    modal = open_editor(phone)
    sizes = modal.evaluate("""d => [...d.querySelectorAll('input:not([type=checkbox]):not([type=hidden]), textarea, select')]
        .filter(el => el.offsetParent).map(el => parseFloat(getComputedStyle(el).fontSize))""")
    assert sizes and min(sizes) >= 16, sizes


def test_date_picker_in_modal_stays_on_screen(phone):
    modal = open_editor(phone)
    trigger = modal.locator(".date-trigger").first
    trigger.scroll_into_view_if_needed()
    trigger.tap()
    picker = phone.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    assert in_viewport(phone, picker)
    tb, pb = trigger.bounding_box(), picker.bounding_box()
    # Right next to its trigger (below it, or above when there's no room).
    assert abs(pb["y"] - (tb["y"] + tb["height"] + 4)) < 2 or abs(pb["y"] + pb["height"] + 4 - tb["y"]) < 2


def test_edit_modal_scrolls_on_short_desktop_window(new_page, wiki):
    pg = new_page(viewport={"width": 1280, "height": 560})
    pg.request.post("/api/deck", data={"title": "Weekly review", "rec_type": "weekly"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck?view=open")
    c = card(pg, "Weekly review")
    c.dblclick(position={"x": c.bounding_box()["width"] * 0.6, "y": 12})   # empty space in the card
    modal = pg.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    assert in_viewport(pg, modal.get_by_role("button", name="Save"))
    box = modal.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 560
    # Centred horizontally, not full-screen.
    assert abs(box["x"] + box["width"] / 2 - 640) < 2 and box["width"] < 1280


# Regression: the ⏰/📅 chips sit at the right edge of a card, and the picker
# opened left-aligned at the chip, so on a phone most of it was off-screen.
@pytest.mark.parametrize("field", ["due_date", "start_date"])
def test_card_date_picker_fits_on_phone(phone, field):
    chip = card(phone, "Water plants").locator(f".task-clickable-date[data-field='{field}']")
    chip.tap()
    picker = phone.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    pb, cb = picker.bounding_box(), chip.bounding_box()
    assert pb["x"] >= 0 and pb["x"] + pb["width"] <= 390, pb
    assert pb["y"] >= 0 and pb["y"] + pb["height"] <= 844, pb
    assert pb["y"] >= cb["y"] + cb["height"] or pb["y"] + pb["height"] <= cb["y"]  # not covering the chip
    pick_day(picker, day(4))
    wait_until(phone, lambda: tasks_by_title(phone)["Water plants"][field] == day(4))


def test_edit_modal_fields_have_no_dead_space_on_phone(phone):
    # Regression: stacked in a column, each field kept a 7rem flex-basis as its
    # height, leaving large empty gaps between the fields.
    modal = open_editor(phone)
    slack = modal.evaluate("""d => [...d.querySelectorAll('.task-field-row > *')].filter(f => f.offsetParent).map(f => {
        const kids = [...f.children].filter(k => k.offsetParent).map(k => k.getBoundingClientRect());
        const content = Math.max(...kids.map(k => k.bottom)) - Math.min(...kids.map(k => k.top));
        return [f.textContent.trim().slice(0, 20), f.getBoundingClientRect().height - content];
    })""")
    assert slack and all(extra <= 2 for _, extra in slack), slack


# Regression: the picker's size was measured before the calendar was drawn
# (height ~0), so the "no room below → open above" flip never happened and a
# chip near the bottom of the screen opened a picker running off the bottom.
def _open_chip_picker_at_bottom(page, field="start_date"):
    c = card(page, "Water plants")
    # Push the list down until the card sits at the bottom of the screen.
    c.evaluate("el => { const gap = innerHeight - el.getBoundingClientRect().bottom - 8;"
               " document.querySelector('.tasks-app').style.marginTop = gap + 'px'; }")
    chip = c.locator(f".task-clickable-date[data-field='{field}']")
    # Not tap(): Playwright would scroll the page (content follows the card,
    # e.g. the Sorted heading) and the card would leave the bottom edge.
    chip.dispatch_event("click")
    picker = page.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    return chip, picker


def test_card_date_picker_opens_upward_near_bottom(phone):
    chip, picker = _open_chip_picker_at_bottom(phone)
    cb, pb = chip.bounding_box(), picker.bounding_box()
    assert cb["y"] > 844 - 300, cb          # the chip really is near the bottom
    assert pb["y"] + pb["height"] <= cb["y"], (pb, cb)   # opened above it
    assert in_viewport(phone, picker)
    # Paging through months (5- vs 6-week months) keeps it on screen.
    for _ in range(6):
        picker.locator(".dp-nav").last.tap()
        assert in_viewport(phone, picker)


def test_date_picker_pinned_on_screen_when_it_fits_nowhere(new_page, wiki):
    # Landscape phone: 390px tall — no room above or below a mid-screen chip.
    pg = new_page(viewport={"width": 844, "height": 390}, has_touch=True, is_mobile=True)
    pg.request.post("/api/deck", data={"title": "Water plants", "start_date": day(0)})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck?view=open")
    c = card(pg, "Water plants")
    c.evaluate("el => { document.body.style.paddingBottom = '100vh'; el.scrollIntoView({block: 'center'}); }")
    c.locator(".task-clickable-date[data-field='start_date']").tap()
    picker = pg.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    assert in_viewport(pg, picker), picker.bounding_box()


def test_edit_modal_is_compact_on_phone(phone):
    # Small fields share lines on a phone instead of one full-width row each,
    # so the whole form of a recurring task fits on a 390×844 screen.
    modal = open_editor(phone)
    assert modal.evaluate("d => d.scrollHeight <= d.clientHeight"), \
        modal.evaluate("d => [d.scrollHeight, d.clientHeight]")
    def top(sel):
        return modal.locator(sel).bounding_box()["y"]
    assert abs(top("[data-date-trigger='task-edit-due']") - top("[data-date-trigger='task-edit-start']")) <= 1.5
    assert abs(top("#task-edit-rec-type") - top("#task-edit-rec-interval")) <= 1.5
    # The action bar sits at the bottom edge, within thumb reach.
    save = modal.get_by_role("button", name="Save").bounding_box()
    assert save["y"] + save["height"] > 844 - 60, save


# ── Touch drag ───────────────────────────────────────────────────────────────
# Phones never fire HTML5 drag events; a long press picks the card up instead.
# Playwright has no touch-move API, so the gestures go through CDP.

def touch_drag(page, source, target, *, hold_ms=450, steps=8, release=True, at=0.5):
    """Long-press the centre of `source`, move to `target` (`at`: how far down
    it, 0.25 its upper half, 0.75 its lower one), lift."""
    cdp = page.context.new_cdp_session(page)
    s, t = source.bounding_box(), target.bounding_box()
    x0, y0 = s["x"] + s["width"] / 2, s["y"] + s["height"] / 2
    x1, y1 = t["x"] + t["width"] / 2, t["y"] + t["height"] * at

    def send(kind, points):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    send("touchStart", [{"x": x0, "y": y0}])
    page.wait_for_timeout(hold_ms)
    for i in range(1, steps + 1):
        send("touchMove", [{"x": x0 + (x1 - x0) * i / steps, "y": y0 + (y1 - y0) * i / steps}])
        page.wait_for_timeout(16)
    if release:
        send("touchEnd", [])
    return send


def test_touch_drag_on_board_sets_start_date(phone):
    open_tab(phone, "plan")
    target = phone.locator(".board-day-divider[data-drop-day]", has_text="Tomorrow")
    touch_drag(phone, card(phone, "Water plants"), target)
    wait_until(phone, lambda: tasks_by_title(phone)["Water plants"]["start_date"] == day(1))
    expect(phone.locator(".task-touch-ghost")).to_have_count(0)
    # The drop is not also a tap: the editor stays closed.
    expect(phone.locator("#task-edit-modal")).to_be_hidden()


def test_touch_drop_on_a_header_does_not_fold_it(new_page, wiki):
    # The whole header folds its section on a tap; a drop on it is no tap.
    tall = new_page(viewport={"width": 390, "height": 1400}, has_touch=True, is_mobile=True)
    tall.request.post("/api/deck", data={"title": "Water plants", "start_date": day(0)})
    tall.request.post("/api/deck", data={"title": "Loose"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    tall.goto("/page/deck?view=open")
    open_tab(tall, "plan")
    target = tall.locator(".board-day-divider[data-drop-bucket='unplanned']")
    touch_drag(tall, card(tall, "Water plants"), target)
    wait_until(tall, lambda: tasks_by_title(tall)["Water plants"]["start_date"] is None)
    expect(target).not_to_have_class(re.compile(r"\bboard-day--folded\b"))
    expect(card(tall, "Loose")).to_be_visible()


def test_touch_drag_to_unplanned_clears_start(new_page, wiki):
    tall = new_page(viewport={"width": 390, "height": 1400}, has_touch=True, is_mobile=True)
    tall.request.post("/api/deck", data={"title": "Water plants", "start_date": day(0)})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    tall.goto("/page/deck?view=open")
    open_tab(tall, "plan")
    target = tall.locator(".board-day-divider[data-drop-bucket='unplanned']")
    touch_drag(tall, card(tall, "Water plants"), target)
    wait_until(tall, lambda: tasks_by_title(tall)["Water plants"]["start_date"] is None)


def test_touch_drag_reorders_all_tab(new_page, wiki):
    pg = new_page(**TOUCH_PHONE)
    ids = [pg.request.post("/api/deck", data={"title": t}).json()["id"] for t in ("One", "Two", "Three")]
    pg.request.post("/api/deck/reorder", data={"order": ids})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck?view=open")
    open_tab(pg, "open")
    send = touch_drag(pg, card(pg, "Three"), card(pg, "One"), release=False, at=0.25)
    # Mid-drag: a floating copy follows the finger, a line marks where it
    # lands (above One: the finger is on its upper half).
    expect(pg.locator(".task-touch-ghost")).to_have_count(1)
    expect(card(pg, "One")).to_have_class(re.compile(r"\btask-drop-before\b"))
    send("touchEnd", [])
    expect(pg.locator(".task-item .task-title")).to_have_text(["Three", "One", "Two"])
    wait_until(pg, lambda: [t["title"] for t in sorted(
        pg.request.get("/api/deck").json(), key=lambda t: t["sort_order"])] == ["Three", "One", "Two"])


def test_touch_drag_reorders_a_plan_day(new_page, wiki):
    pg = new_page(**TOUCH_PHONE)
    ids = [pg.request.post("/api/deck", data={"title": t, "start_date": day(0)}).json()["id"]
           for t in ("One", "Two", "Three")]
    pg.request.post("/api/deck/reorder", data={"order": ids})
    pg.goto("/deck?view=plan")
    send = touch_drag(pg, card(pg, "Three"), card(pg, "One"), release=False, at=0.25)
    expect(card(pg, "One")).to_have_class(re.compile(r"\btask-drop-before\b"))
    send("touchEnd", [])
    wait_until(pg, lambda: [t["title"] for t in sorted(
        pg.request.get("/api/deck").json(), key=lambda t: t["sort_order"])] == ["Three", "One", "Two"])


def test_quick_swipe_scrolls_instead_of_dragging(phone):
    # Moving before the long press completes is a scroll, not a pick-up.
    open_tab(phone, "plan")
    target = phone.locator(".board-day-divider[data-drop-day]", has_text="Tomorrow")
    touch_drag(phone, card(phone, "Water plants"), target, hold_ms=0)
    phone.wait_for_timeout(600)
    expect(phone.locator(".task-touch-ghost")).to_have_count(0)
    assert tasks_by_title(phone)["Water plants"]["start_date"] == day(0)


def test_long_press_in_place_starts_selection_mode(phone):
    open_tab(phone, "plan")
    c = card(phone, "Water plants")
    touch_drag(phone, c, c)
    expect(phone.locator(".tasks-app")).to_contain_class("deck-selecting")
    expect(phone.locator("#task-selbar .deck-selbar-count")).to_have_text("1 selected")
    expect(phone.locator(".task-touch-ghost")).to_have_count(0)
    expect(phone.locator("#task-edit-modal")).to_be_hidden()     # not a tap either
    assert tasks_by_title(phone)["Water plants"]["start_date"] == day(0)
    assert selected(phone) == ["Water plants"]
    # The release was no tap: the card stays selected, and the next tap drops it.
    card(phone, "Water plants").locator(".task-title").tap()
    assert selected(phone) == []


def complete_one(page, title="Old chore"):
    t = page.request.post("/api/deck", data={"title": title}).json()
    page.request.put(f"/api/deck/{t['id']}", data={"completed": True})
    page.reload()


def test_long_press_where_nothing_drags_starts_selection_mode(phone):
    complete_one(phone)
    open_tab(phone, "completed")
    c = card(phone, "Old chore")
    touch_drag(phone, c, c)
    expect(phone.locator(".tasks-app")).to_contain_class("deck-selecting")
    assert selected(phone) == ["Old chore"]


def test_long_press_then_move_where_nothing_drags_does_nothing(phone):
    complete_one(phone)
    open_tab(phone, "completed")
    touch_drag(phone, card(phone, "Old chore"), phone.locator(".deck-header"))
    phone.wait_for_timeout(200)
    expect(phone.locator(".tasks-app")).not_to_contain_class("deck-selecting")
    expect(phone.locator("#task-selbar")).to_be_hidden()


def test_touch_press_turns_off_native_drag(new_page, wiki):
    # Phone browsers start their own HTML5 drag on a long press of a
    # draggable="true" element, which cancelled the touch drag mid-gesture:
    # the first drop of an unselected card was lost. Under a finger a card is
    # not natively draggable; a mouse press makes it draggable again.
    pg = new_page(**TOUCH_PHONE)
    ids = [pg.request.post("/api/deck", data={"title": t}).json()["id"] for t in ("One", "Two")]
    pg.request.post("/api/deck/reorder", data={"order": ids})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck?view=open")
    open_tab(pg, "open")
    one = card(pg, "One")
    assert one.evaluate("el => el.draggable")
    send = touch_drag(pg, card(pg, "Two"), one, release=False, at=0.25)
    assert not pg.locator("#tasks-list .task-dragging").evaluate("el => el.draggable")
    # Picking a card up selects it (visible feedback, as with a mouse press);
    # the carried copy shows the selection too.
    expect(pg.locator("#tasks-list .task-dragging")).to_have_class(re.compile(r"\btask-selected\b"))
    expect(pg.locator(".task-touch-ghost")).to_have_class(re.compile(r"\btask-selected\b"))
    send("touchEnd", [])
    expect(pg.locator(".task-item .task-title")).to_have_text(["Two", "One"])
    card(pg, "Two").dispatch_event("pointerdown", {"pointerType": "mouse", "bubbles": True})
    assert card(pg, "Two").evaluate("el => el.draggable")


def test_touch_drag_across_focus_sections(new_page, wiki):
    pg = new_page(**TOUCH_PHONE)
    for t in ("Alpha", "Bravo"):
        pg.request.post("/api/deck", data={"title": t, "start_date": day(0)})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/deck?view=focus")
    sorted_ = pg.locator(".list-section[data-section='sorted']")
    unsorted = pg.locator(".list-section[data-section='unsorted']")
    touch_drag(pg, card(pg, "Bravo"), sorted_)
    wait_until(pg, lambda: tasks_by_title(pg)["Bravo"]["sort_order"] == 0)
    assert tasks_by_title(pg)["Alpha"]["sort_order"] is None
    touch_drag(pg, card(pg, "Bravo"), unsorted)
    wait_until(pg, lambda: tasks_by_title(pg)["Bravo"]["sort_order"] is None)


def test_tap_outside_only_closes_the_card_date_picker(phone):
    # Regression: the picker closed on mousedown, which touch fires only with
    # the click, so the same tap also opened the card under the finger.
    card(phone, "Water plants").locator(".task-clickable-date[data-field='start_date']").tap()
    picker = phone.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    card(phone, "Weekly review").locator(".task-title").tap()
    expect(picker).to_have_count(0)
    expect(phone.locator("#task-edit-modal")).to_be_hidden()
    # The next taps work normally again: select, then open.
    card(phone, "Weekly review").locator(".task-title").tap()
    expect(card(phone, "Weekly review")).to_contain_class("task-selected")
    card(phone, "Weekly review").locator(".task-title").tap()
    expect(phone.locator("#task-edit-modal")).to_be_visible()


def test_click_outside_only_closes_the_date_picker(page, wiki):
    # With a mouse too, the click that closes the picker does nothing else:
    # it neither selects another card nor ticks its checkbox.
    page.request.post("/api/deck", data={"title": "Water plants", "start_date": day(0)})
    page.request.post("/api/deck", data={"title": "Weekly review"})
    page.goto("/deck?view=open")
    chip = card(page, "Water plants").locator(".task-clickable-date[data-field='start_date']")
    picker = page.locator(".deck-datepicker")
    chip.click()
    expect(picker).to_be_visible()
    other = card(page, "Weekly review")
    other.locator(".task-title").click()
    expect(picker).to_have_count(0)
    expect(other).not_to_have_class(re.compile(r"\btask-selected\b"))
    chip.click()
    expect(picker).to_be_visible()
    other.locator(".task-checkbox input").click()
    expect(picker).to_have_count(0)
    page.wait_for_timeout(300)
    expect(other.locator(".task-checkbox input")).not_to_be_checked()
    assert tasks_by_title(page)["Weekly review"]["completed_at"] is None
    # The next click acts normally again.
    other.locator(".task-title").click()
    expect(other).to_have_class(re.compile(r"\btask-selected\b"))
