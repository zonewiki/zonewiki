"""Deck filter field, commit link, phone editor menu, card swipes, checkbox undo."""

import re
from datetime import date

from playwright.sync_api import expect

from conftest import TOUCH_PHONE, open_tab


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


def seed_deck(page, wiki, embed="[[deck]]"):
    for title, tags in (("Fix bug", "work"), ("Buy milk", "home"), ("Fix fence", "home,work")):
        page.request.post("/api/deck", data={"title": title, "tags": tags})
    wiki.write("deck", f"# Deck\n\n{embed}\n")
    page.goto("/page/deck")


# ── 4. One filter field: text plus #tags ─────────────────────────────────────

def test_filter_field_text_and_tags(page, wiki):
    seed_deck(page, wiki)
    field = page.locator("#task-filter-search")
    expect(page.locator("#task-filter-tag")).to_have_count(0)
    field.fill("#work")
    expect(page.locator(".task-item")).to_have_count(2)
    field.fill("#work #home")
    expect(page.locator(".task-item .task-title")).to_have_text(["Fix fence"])
    field.fill("#work|home milk")
    expect(page.locator(".task-item .task-title")).to_have_text(["Buy milk"])


def test_filter_field_tag_autocomplete(page, wiki):
    seed_deck(page, wiki)
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("fix #ho")
    ac = page.locator(".tag-ac-dropdown")
    expect(ac.locator(".tag-ac-item")).to_have_text(["#home"])
    field.press("ArrowDown")
    field.press("Enter")
    expect(field).to_have_value("fix #home ")
    expect(page.locator(".task-item .task-title")).to_have_text(["Fix fence"])
    # Tags already in the filter are not suggested again.
    field.press_sequentially("#")
    expect(ac.locator(".tag-ac-item")).to_have_text(["#work"])


def test_tag_suggestions_after_free_text(page, wiki):
    # Regression: suggestions came only from tasks matching the whole rest of
    # the field, text included, so "some text #ho" (text matching no task yet)
    # suggested nothing. Free text no longer narrows the suggestions.
    seed_deck(page, wiki)
    field = page.locator("#task-filter-search")
    field.click()
    field.press_sequentially("some text #ho")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item")).to_have_text(["#home"])
    field.press("ArrowDown")
    field.press("Enter")
    expect(field).to_have_value("some text #home ")
    # Other tag words still narrow them: with #home set, only #work remains
    # (it co-occurs with home), and home itself isn't offered twice.
    field.press_sequentially("#")
    expect(page.locator(".tag-ac-dropdown .tag-ac-item")).to_have_text(["#work"])


def test_hash_key_starts_a_tag_word(page, wiki):
    seed_deck(page, wiki)
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("#")
    field = page.locator("#task-filter-search")
    expect(field).to_be_focused()
    expect(field).to_have_value("#")


def test_embed_tag_param_lands_in_filter_field(page, wiki):
    seed_deck(page, wiki, "[[deck tag=home,work]]")
    expect(page.locator("#task-filter-search")).to_have_value("#home #work")
    expect(page.locator(".task-item .task-title")).to_have_text(["Fix fence"])


def test_new_task_takes_tags_from_filter(page, wiki):
    seed_deck(page, wiki)
    page.locator("#task-filter-search").fill("#work|home #urgent text")
    page.get_by_role("button", name="+ New").click()
    # Only unambiguous (single-tag) words become default tags.
    expect(page.locator("#task-edit-tags")).to_have_value("urgent")


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


# ── 7a. Swipe a card: right = complete, left = start today ───────────────────

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


def phone_deck(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/deck", data={"title": "Water plants"})
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    phone.goto("/page/deck")
    return phone


def test_swipe_right_completes(new_page, wiki):
    phone = phone_deck(new_page, wiki)
    c = card(phone, "Water plants")
    send = swipe(phone, c, 200, release=False)
    expect(c).to_have_class(re.compile(r"\btask-swipe-armed\b"))
    send("touchEnd", [])
    # Same grace period as ticking the checkbox.
    expect(c).to_have_class(re.compile(r"\btask-completing\b"))
    expect(c.locator("input[type=checkbox]")).to_be_checked()
    wait_until(phone, lambda: not any(t["title"] == "Water plants"
                                      for t in phone.request.get("/api/deck").json()), 8000)
    expect(phone.locator("#task-edit-modal")).to_be_hidden()   # not also a tap


def test_swipe_left_starts_today(new_page, wiki):
    phone = phone_deck(new_page, wiki)
    swipe(phone, card(phone, "Water plants"), -200)
    wait_until(phone, lambda: task(phone, "Water plants")["start_date"] == date.today().isoformat())
    expect(phone.locator(".task-undo-toast")).to_contain_text("Starts")


def test_short_swipe_does_nothing(new_page, wiki):
    phone = phone_deck(new_page, wiki)
    c = card(phone, "Water plants")
    swipe(phone, c, 40)
    phone.wait_for_timeout(300)
    expect(c).not_to_have_class(re.compile(r"\btask-completing\b"))
    assert task(phone, "Water plants")["start_date"] is None
    assert c.evaluate("el => el.style.transform") == ""


def test_vertical_move_is_not_a_swipe(new_page, wiki):
    phone = phone_deck(new_page, wiki)
    c = card(phone, "Water plants")
    swipe(phone, c, 30, dy=120)
    phone.wait_for_timeout(300)
    expect(c).not_to_have_class(re.compile(r"\btask-completing\b"))
    assert task(phone, "Water plants")["start_date"] is None


def test_swipe_does_not_scroll_page_sideways(new_page, wiki):
    phone = phone_deck(new_page, wiki)
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
