"""Phone search button, status badges on phones, and the compact Plan board."""

import re
from datetime import date

from playwright.sync_api import expect

from conftest import TOUCH_PHONE, open_tab
from test_touch import touch_drag


def _overlap(a, b) -> bool:
    return not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"] or
                a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


# ── Phone search button ──────────────────────────────────────────────────────
# Search is otherwise Ctrl+P, which a phone doesn't have.

def test_search_button_only_on_phones(page, new_page, wiki):
    page.goto("/page/index")
    expect(page.locator(".mobile-search")).to_be_hidden()
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    btn = phone.locator(".mobile-search")
    expect(btn).to_be_visible()
    box = btn.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert box["x"] + box["width"] > 390 - 40 and box["y"] + box["height"] > 844 - 40  # bottom right


def test_search_button_opens_search_and_steps_aside_while_typing(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    phone.locator(".mobile-search").tap()
    expect(phone.locator("#search-modal")).to_be_visible()
    # The search field has focus (keyboard up), so the button steps aside.
    expect(phone.locator(".mobile-search")).to_be_hidden()


def test_no_action_buttons_in_editor(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/edit/index")
    expect(phone.locator(".mobile-actions")).to_have_count(0)


def test_page_end_not_hidden_behind_search_button(new_page, wiki):
    wiki.write("long", "# Long\n\n" + "Filler.\n\n" * 60 + "The last line.\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/long")
    phone.evaluate("scrollTo(0, document.body.scrollHeight)")
    last = phone.get_by_text("The last line.").bounding_box()
    assert last["y"] + last["height"] <= phone.locator(".mobile-search").bounding_box()["y"]


# ── Phone new-note button ────────────────────────────────────────────────────
# Next to search: `c s` (a new scratch note) for a phone.

def test_new_note_button_sits_left_of_search_on_phones(page, new_page, wiki):
    page.goto("/page/index")
    expect(page.locator(".mobile-note")).to_be_hidden()
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    note, search = phone.locator(".mobile-note"), phone.locator(".mobile-search")
    expect(note).to_be_visible()
    nb, sb = note.bounding_box(), search.bounding_box()
    assert nb["width"] >= 44 and nb["height"] >= 44
    assert nb["x"] + nb["width"] < sb["x"] and abs(nb["y"] - sb["y"]) < 1


def test_new_note_button_creates_a_scratch_note(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/deck")
    phone.locator(".mobile-note").tap()
    expect(phone.locator("#scratch-modal")).to_be_visible()
    # The title field has focus (keyboard up), so the buttons step aside.
    expect(phone.locator(".mobile-actions")).to_be_hidden()
    phone.locator("#scratch-title").fill("Shopping idea")
    phone.locator("#scratch-title").press("Enter")
    expect(phone).to_have_url(re.compile(r"/edit/\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_shopping-idea$"))


# ── Status badges on phones ──────────────────────────────────────────────────

def test_badges_do_not_cover_breadcrumb_on_phone(new_page, wiki):
    # Regression: the fixed badges sat on top of "Leaf ›" on narrow screens.
    wiki.write("draft", "# Draft\n\nNot committed yet.\n")   # → uncommitted badge
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/draft")
    badge = phone.locator("#uncommitted-badge")
    expect(badge).to_be_visible()
    assert not _overlap(badge.bounding_box(), phone.locator(".breadcrumb").bounding_box())


# ── Compact Plan board ───────────────────────────────────────────────────────

def test_empty_sections_are_single_rows(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.request.post("/api/deck", data={"title": "Today's", "start_date": date.today().isoformat()})
    page.goto("/page/deck?view=open")
    open_tab(page, "due")
    empties = page.locator(".board-day-divider.board-day--empty")
    expect(empties).to_have_count(8)   # Tomorrow + five more days + Later + Unplanned
    for row in empties.all():
        h = row.bounding_box()["height"]
        assert 28 <= h <= 36, h
    # Today is the only section with a task, so it is the only non-empty one.
    expect(page.locator(".board-day-divider:not(.board-day--empty)")).to_have_count(1)
    # A week of empty days fits in well under 300px.
    first, last = empties.first.bounding_box(), empties.last.bounding_box()
    assert last["y"] + last["height"] - first["y"] < 300


def test_empty_sections_are_finger_sized_on_touch(new_page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/deck?view=open")
    open_tab(phone, "due")
    assert all(r.bounding_box()["height"] >= 40
               for r in phone.locator(".board-day--empty").all())


def test_board_layout_does_not_change_when_a_drag_starts(new_page, wiki):
    # Nothing may grow or shift when a card is picked up: the section you
    # aim at has to stay where it was.
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/deck", data={"title": "Water plants",
                                          "start_date": date.today().isoformat()})
    phone.goto("/page/deck?view=open")
    open_tab(phone, "due")
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
    # and Search buttons.
    import server
    monkeypatch.setattr(server, "LEAF_PASSWORD", "pw")
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"password": "pw"})
    phone.goto("/page/index")
    signout = phone.get_by_role("button", name="Sign out")
    expect(signout).to_be_visible()
    s, a = signout.bounding_box(), phone.locator(".mobile-actions").bounding_box()
    assert s["x"] + s["width"] <= a["x"] or s["y"] + s["height"] <= a["y"], (s, a)
    assert s["x"] >= 0 and s["y"] + s["height"] <= 844
