"""Deck on a touch phone: tap to edit, the ⋯ menu as a bottom sheet."""

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from conftest import TOUCH_PHONE, open_tab


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


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
    pg.request.post("/api/deck", data={"title": "Water plants", "focus_date": day(0)})
    pg.request.post("/api/deck", data={"title": "Weekly review",
                                       "rec_type": "weekly", "due_date": day(2)})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck")
    return pg


def open_menu(page, title):
    card(page, title).locator(".task-menu-btn").tap()
    menu = page.locator(".task-menu")
    expect(menu).to_be_visible()
    return menu


def test_tap_card_opens_editor(phone):
    card(phone, "Water plants").locator(".task-title").tap()
    expect(phone.locator("#task-edit-modal")).to_be_visible()
    expect(phone.locator("#task-edit-title")).to_have_value("Water plants")


def test_tapping_checkbox_completes_not_edits(phone):
    card(phone, "Water plants").locator("input[type=checkbox]").tap()
    expect(phone.locator("#task-edit-modal")).to_be_hidden()
    expect(card(phone, "Water plants")).to_contain_class("task-completing")


def test_hover_icons_hidden_menu_button_is_a_real_target(phone):
    expect(card(phone, "Weekly review").locator(".task-wontdo-btn")).to_be_hidden()
    expect(card(phone, "Weekly review").locator(".task-skip-btn")).to_be_hidden()
    box = card(phone, "Weekly review").locator(".task-menu-btn").bounding_box()
    assert box["width"] >= 40 and box["height"] >= 40


def test_menu_is_a_bottom_sheet_with_context_items(phone):
    menu = open_menu(phone, "Weekly review")
    items = menu.get_by_role("menuitem")
    expect(items).to_have_text(["Edit…", "Plan for today", "Plan for tomorrow", "Pick plan date…",
                                "Skip this occurrence", "Won't do", "Delete…"])
    box = menu.bounding_box()
    assert box["x"] == 0 and abs(box["y"] + box["height"] - 844) < 1, box
    assert box["width"] == 390
    # Tapping outside (the backdrop) closes it without doing anything.
    phone.touchscreen.tap(200, 100)
    expect(menu).to_have_count(0)
    expect(phone.locator("#task-edit-modal")).to_be_hidden()


def test_menu_plan_and_unplan(phone):
    open_menu(phone, "Weekly review").get_by_role("menuitem", name="Plan for tomorrow").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Weekly review"]["focus_date"] == day(1))
    menu = open_menu(phone, "Weekly review")
    menu.get_by_role("menuitem", name="Unplan").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Weekly review"]["focus_date"] is None)
    phone.locator(".task-undo-btn").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Weekly review"]["focus_date"] == day(1))


def test_menu_pick_date_opens_picker(phone):
    open_menu(phone, "Water plants").get_by_role("menuitem", name="Pick plan date…").tap()
    picker = phone.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    picker.locator(f".dp-day[data-date='{day(3)}']").tap()
    wait_until(phone, lambda: tasks_by_title(phone)["Water plants"]["focus_date"] == day(3))


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
    expect(menu.get_by_role("menuitem")).to_have_text(["Edit…", "Reopen", "Delete…"])
    menu.get_by_role("menuitem", name="Reopen").tap()
    wait_until(phone, lambda: "Water plants" in tasks_by_title(phone))



def test_desktop_click_selects_does_not_edit(page, wiki):
    page.request.post("/api/deck", data={"title": "Water plants"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    card(page, "Water plants").locator(".task-content").click()
    expect(card(page, "Water plants")).to_contain_class("task-selected")
    expect(page.locator("#task-edit-modal")).to_be_hidden()


def test_desktop_menu_popover_and_keyboard(page, wiki):
    page.request.post("/api/deck", data={"title": "Water plants"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck")
    btn = card(page, "Water plants").locator(".task-menu-btn")
    btn.click()
    menu = page.locator(".task-menu")
    # Popover next to the button, not a bottom sheet.
    mb, bb = menu.bounding_box(), btn.bounding_box()
    assert abs(mb["x"] + mb["width"] - (bb["x"] + bb["width"])) < 2 and mb["y"] > bb["y"]
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem", name="Plan for today")).to_be_focused()
    page.keyboard.press("Enter")
    expect(menu).to_have_count(0)
    expect(page.locator("#task-edit-modal")).to_be_hidden()   # deck keys stayed out
    wait_until(page, lambda: tasks_by_title(page)["Water plants"]["focus_date"] == day(0))
    btn = card(page, "Water plants").locator(".task-menu-btn")
    btn.click()
    page.keyboard.press("Escape")
    expect(page.locator(".task-menu")).to_have_count(0)
    expect(btn).to_be_focused()


# ── Edit modal on small screens ──────────────────────────────────────────────
# Regression: the modal was centred with a transform and had overflow:visible,
# so on a phone (content ~1270px, screen 844px) everything below the fold —
# including Save — was unreachable: the fixed dialog could not be scrolled.

def open_editor(page, title="Weekly review"):
    card(page, title).locator(".task-title").tap()
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
    phone.goto("/page/deck")
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
    pg.goto("/page/deck")
    card(pg, "Weekly review").locator(".task-content").dblclick()
    modal = pg.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    assert in_viewport(pg, modal.get_by_role("button", name="Save"))
    box = modal.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 560
    # Centred horizontally, not full-screen.
    assert abs(box["x"] + box["width"] / 2 - 640) < 2 and box["width"] < 1280


# Regression: the ⏰/📅 chips sit at the right edge of a card, and the picker
# opened left-aligned at the chip, so on a phone most of it was off-screen.
@pytest.mark.parametrize("field", ["due_date", "focus_date"])
def test_card_date_picker_fits_on_phone(phone, field):
    chip = card(phone, "Water plants").locator(f".task-clickable-date[data-field='{field}']")
    chip.tap()
    picker = phone.locator(".deck-datepicker")
    expect(picker).to_be_visible()
    pb, cb = picker.bounding_box(), chip.bounding_box()
    assert pb["x"] >= 0 and pb["x"] + pb["width"] <= 390, pb
    assert pb["y"] >= 0 and pb["y"] + pb["height"] <= 844, pb
    assert pb["y"] >= cb["y"] + cb["height"] or pb["y"] + pb["height"] <= cb["y"]  # not covering the chip
    picker.locator(f".dp-day[data-date='{day(4)}']").tap()
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
def _open_chip_picker_at_bottom(page, field="focus_date"):
    c = card(page, "Water plants")
    # Push the list down until the card sits at the bottom of the screen.
    c.evaluate("el => { const gap = innerHeight - el.getBoundingClientRect().bottom - 8;"
               " document.querySelector('.tasks-app').style.marginTop = gap + 'px'; }")
    chip = c.locator(f".task-clickable-date[data-field='{field}']")
    chip.tap()
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
    pg.request.post("/api/deck", data={"title": "Water plants", "focus_date": day(0)})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    pg.goto("/page/deck")
    c = card(pg, "Water plants")
    c.evaluate("el => { document.body.style.paddingBottom = '100vh'; el.scrollIntoView({block: 'center'}); }")
    c.locator(".task-clickable-date[data-field='focus_date']").tap()
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
    assert abs(top("[data-date-trigger='task-edit-due']") - top("[data-date-trigger='task-edit-focus']")) <= 1.5
    assert abs(top("#task-edit-rec-type") - top("#task-edit-rec-interval")) <= 1.5
    # The action bar sits at the bottom edge, within thumb reach.
    save = modal.get_by_role("button", name="Save").bounding_box()
    assert save["y"] + save["height"] > 844 - 60, save
