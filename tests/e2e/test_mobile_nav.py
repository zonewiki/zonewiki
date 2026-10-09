"""The modules (switcher and phone tab bar), action buttons (phone and desktop),
status badges on phones, and the compact Plan board."""

import re
from datetime import date

from playwright.sync_api import expect

from conftest import TOUCH_PHONE, open_tab
from test_touch import touch_drag
import config


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
# The phone's way to the modules: Wiki, Scratch, Deck, then New and Search.

def test_phone_tab_bar_spans_the_bottom_edge(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    bar = phone.locator(".action-buttons")
    b = bar.bounding_box()
    assert b["x"] == 0 and b["width"] == 390 and abs(b["y"] + b["height"] - 844) < 1
    items = bar.locator(".action-module, button")
    expect(items).to_have_text(["Wiki", "Scratch", "Deck", "New", "Search"])
    boxes = [i.bounding_box() for i in items.all()]
    assert all(x["width"] >= 44 and x["height"] >= 44 for x in boxes)
    assert all(abs(x["y"] - boxes[0]["y"]) < 1 for x in boxes)   # one row
    # The breadcrumb's switcher shrinks to the logo: the bar has the rest.
    expect(phone.locator(".breadcrumb .module-link[data-module=wiki] svg")).to_be_visible()
    expect(phone.locator(".breadcrumb .module-link[data-module=deck]")).to_be_hidden()
    for label in phone.locator(".breadcrumb .module-label").all():
        expect(label).to_be_hidden()


def test_phone_tab_bar_marks_and_opens_modules(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    tab = lambda m: phone.locator(f".action-buttons .action-module[data-module={m}]")
    expect(tab("wiki")).to_have_attribute("aria-current", "page")
    tab("deck").tap()
    phone.wait_for_url("**/deck")
    expect(tab("deck")).to_have_attribute("aria-current", "page")
    expect(tab("wiki")).not_to_have_attribute("aria-current", "page")
    tab("scratch").tap()
    phone.wait_for_url("**/page/_scratch")
    expect(tab("scratch")).to_have_attribute("aria-current", "page")


def test_new_creates_a_scratch_note_in_scratch(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/_scratch")
    expect(phone.locator(".action-new")).to_have_attribute("aria-label", "New scratch note")
    phone.locator(".action-new").tap()
    expect(phone.locator("#scratch-modal")).to_be_visible()
    # The title field has focus (keyboard up), so the bar steps aside.
    expect(phone.locator(".action-buttons")).to_be_hidden()
    phone.locator("#scratch-title").fill("Shopping idea")
    phone.locator("#scratch-title").press("Enter")
    expect(phone).to_have_url(re.compile(r"/edit/\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_shopping-idea$"))
    # On a scratch note, too.
    phone.goto(phone.url.replace("/edit/", "/page/"))
    expect(phone.locator(".action-new")).to_have_attribute("aria-label", "New scratch note")


def test_new_creates_a_page_in_the_wiki(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/page/index")
    phone.locator(".action-new").tap()
    expect(phone.locator("#new-page-modal")).to_be_visible()
    phone.locator("#new-page-input").fill("Garden plan")
    phone.locator("#new-page-input").press("Enter")
    phone.wait_for_url("**/edit/garden-plan")


def test_new_creates_a_task_in_the_deck(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/deck")
    expect(phone.locator(".action-new")).to_have_attribute("aria-label", "New task")
    phone.locator(".action-new").tap()
    expect(phone.locator(".task-edit-modal[open]")).to_be_visible()


def test_deck_tab_shows_the_due_count(new_page, wiki):
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/api/deck", data={"title": "Pay rent", "due_date": date.today().isoformat()})
    phone.goto("/page/index")
    due = phone.locator(".action-buttons .module-due")
    expect(due).to_have_text("1")
    expect(due).to_be_visible()
    d, tab = due.bounding_box(), phone.locator(".action-buttons [data-module=deck]").bounding_box()
    assert tab["x"] <= d["x"] and d["x"] + d["width"] <= tab["x"] + tab["width"]


# ── Action buttons on desktop ────────────────────────────────────────────────
# At the top right, in line with the breadcrumb: New scratch note, New task
# and Search, as icons. The bottom bar is the phone's.

def test_top_actions_on_desktop(page, wiki):
    page.goto("/page/index")
    expect(page.locator(".action-buttons")).to_be_hidden()
    buttons = page.locator(".top-actions button")
    expect(buttons).to_have_count(4)
    names = [b.get_attribute("aria-label") for b in buttons.all()]
    assert names == ["Sharing mode", "New scratch note", "Add a task to your deck", "Search"]
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


def test_top_actions_open_scratch_and_search(page, wiki):
    page.goto("/page/index")
    page.locator(".top-search").click()
    expect(page.locator("#search-modal")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#search-modal")).to_be_hidden()
    page.locator(".top-scratch").click()
    expect(page.locator("#scratch-modal")).to_be_visible()


# ── New task: the deck's home decides ────────────────────────────────────────
# The New task button and c t give a new task the home deck's tags.

def _home(wiki, embed="[[deck list=task view=focus home=1]]"):
    wiki.write("deck", f"# My deck\n\n{embed}\n")
    wiki.write("garden", "# Garden\n\nBeds.\n")


def test_new_task_button_stays_on_the_page(page, wiki):
    # A page without a deck app loads a hidden one: the modal opens right
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
    expect(page.locator(".task-undo-toast")).to_contain_text("Added “Order seeds” to your deck")
    tasks = page.request.get("/api/deck").json()
    assert [(t["title"], t["tags"], t["start_date"]) for t in tasks] == [("Order seeds", ["task"], date.today().isoformat())]
    # A second time doesn't load the deck again.
    page.keyboard.press("c")
    page.keyboard.press("t")
    expect(modal).to_be_visible()
    expect(page.locator(".tasks-app[data-headless]")).to_have_count(1)


def test_new_task_start_follows_the_homes_view(page, wiki):
    _home(wiki, "[[deck list=task view=open home=1]]")
    page.goto("/page/garden")
    page.locator(".top-task").click()
    expect(page.locator(".task-edit-modal[open] [id^=task-edit-start]")).to_have_value("")


def test_new_task_falls_back_to_the_home_page(page, wiki):
    # Should the hidden deck fail to load, the home page opens with the modal.
    _home(wiki)
    page.route("**/api/deck/headless", lambda route: route.fulfill(status=500, body="no"))
    page.goto("/page/garden")
    page.locator(".top-task").click()
    page.wait_for_url(re.compile(r"/page/deck(\?.*)?$"))
    expect(page.locator(".task-edit-modal[open]")).to_be_visible()


def test_new_task_button_on_the_home_page(page, wiki):
    _home(wiki)
    page.goto("/page/deck")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip", has_text="task")).to_have_count(1)
    assert page.url.endswith("/page/deck")


def test_new_task_on_another_deck_gets_the_homes_tags(page, wiki):
    # A page with another deck (here /deck, every task): the modal opens
    # there, but with the home's tags and keep, not that deck's filter.
    _home(wiki, "[[deck list=shop keep=1 home=1]]")
    page.goto("/deck?tag=garden")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip")).to_have_count(1)
    expect(modal.locator(".tag-chip")).to_contain_text("shop")
    expect(modal.locator("[id^=task-edit-keep]")).to_be_checked()
    assert "/deck" in page.url


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
    # The editor has a hidden deck: the modal opens right there. (Not the
    # old fixed #task: c t once always tagged that.)
    _home(wiki, "[[deck list=work home=1]]")
    page.goto("/edit/garden")
    page.locator("#editor-textarea").evaluate("el => el.blur()")
    page.keyboard.press("c")
    page.keyboard.press("t")
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip")).to_have_count(1)
    expect(modal.locator(".tag-chip")).to_contain_text("work")
    assert "/edit/garden" in page.url


def test_g_t_goes_to_the_home_deck(page, wiki):
    _home(wiki)
    page.goto("/page/garden")
    page.keyboard.press("g")
    page.keyboard.press("t")
    page.wait_for_url("**/page/deck")
    wiki.write("deck", "# My deck\n\n[[deck list=task]]\n")   # no home any more
    page.goto("/page/garden")
    page.keyboard.press("g")
    page.keyboard.press("t")
    page.wait_for_url(re.compile(r"/deck$"))


# ── Module switcher on desktop ───────────────────────────────────────────────
# The breadcrumb starts with the modules: Wiki (with the logo), Scratch, Deck.

def test_desktop_breadcrumb_switches_modules(page, wiki):
    wiki.write("garden", "# Garden\n\nBeds.\n")
    page.goto("/page/garden")
    switch = page.locator(".breadcrumb .module-switch .module-link")
    expect(switch.locator(".module-label")).to_have_text(["Wiki", "Scratch", "Deck"])
    expect(switch.nth(0)).to_have_attribute("aria-current", "page")
    # One line with the page title after it.
    boxes = [m.bounding_box() for m in switch.all()]
    title = page.locator(".breadcrumb > span", has_text="Garden").bounding_box()
    assert all(abs(b["y"] - boxes[0]["y"]) < 3 for b in boxes)
    assert boxes[-1]["x"] + boxes[-1]["width"] < title["x"]
    page.locator(".breadcrumb .module-link[data-module=deck]").click()
    page.wait_for_url("**/deck")
    expect(page.locator(".breadcrumb .module-link[data-module=deck]")).to_have_attribute("aria-current", "page")
    expect(page.locator(".action-new")).to_have_attribute("title", "New task (n)")
    page.locator(".breadcrumb .module-link[data-module=scratch]").click()
    page.wait_for_url("**/page/_scratch")
    # The navigation trail keeps the page, but not the modules' own pages.
    expect(page.locator(".breadcrumb > a")).to_have_text(["Garden"])


def test_desktop_switcher_is_set_apart_from_the_trail(page, wiki):
    # The switcher is a control, not a crumb: a bordered pill group, then a
    # thin vertical divider instead of "›" before the pages.
    wiki.write("garden", "# Garden\n")
    page.goto("/page/garden")
    switch = page.locator(".breadcrumb .module-switch")
    assert switch.evaluate("el => getComputedStyle(el).borderTopWidth") == "1px"
    current = switch.locator(".module-link[aria-current]")
    assert current.evaluate("el => getComputedStyle(el).backgroundColor") != "rgba(0, 0, 0, 0)"
    divider = page.locator(".breadcrumb .module-switch + .breadcrumb-sep")
    d = divider.bounding_box()
    assert d["width"] <= 1.5 and d["height"] >= 10, d
    s, t = switch.bounding_box(), page.locator(".breadcrumb > span", has_text="Garden").bounding_box()
    assert s["x"] + s["width"] < d["x"] < t["x"]


def test_desktop_breadcrumb_deck_link_shows_the_due_count(page, wiki):
    page.request.post("/api/deck", data={"title": "Overdue", "due_date": "2020-01-01"})
    page.request.post("/api/deck", data={"title": "Due today", "due_date": date.today().isoformat()})
    page.goto("/page/index")
    deck = page.locator(".breadcrumb .module-link[data-module=deck]")
    expect(deck.locator(".module-due")).to_have_text("2")
    expect(deck.locator(".module-focus")).to_have_text("2")
    expect(deck).to_have_attribute("title", "Deck: 2 in Focus · 1 overdue · 1 due today")


def test_deck_counts_are_in_the_page_before_any_script(page, wiki):
    # Regression: the counts came from a fetch after the page had painted, so
    # the Deck link grew a moment later on every page load (flicker). The
    # server renders them; with scripts off they are there all the same.
    page.request.post("/api/deck", data={"title": "Pay rent", "due_date": date.today().isoformat()})
    page.request.post("/api/deck", data={"title": "Water plants", "start_date": date.today().isoformat()})
    page.route("**/public/leaf.js", lambda route: route.abort())
    page.route("**/api/deck/due-count", lambda route: route.abort())
    page.goto("/page/index")
    deck = page.locator(".breadcrumb .module-link[data-module=deck]")
    expect(deck.locator(".module-due")).to_have_text("1")
    expect(deck.locator(".module-focus")).to_have_text("2")
    expect(deck).to_have_attribute("title", "Deck: 2 in Focus · 1 due today")


def test_deck_counts_follow_deck_changes(page, wiki):
    # A task started from the deck shows up in the Focus count at once.
    page.request.post("/api/deck", data={"title": "Water plants"})
    page.goto("/deck?view=open")
    deck = page.locator(".breadcrumb .module-link[data-module=deck]")
    expect(deck.locator(".module-focus")).to_be_hidden()
    page.locator(".task-item").first.click()
    page.keyboard.press("f")   # start today
    expect(deck.locator(".module-focus")).to_have_text("1")


def test_deck_home_page_is_the_deck_module(page, wiki):
    # [[deck … home=1]]: your own deck. The Deck links go there, it counts
    # as the Deck module, New adds a task in its embed (with its list tag),
    # and it is no stop in the navigation trail.
    wiki.write("deck", "# My deck\n\n[[deck list=task view=focus home=1]]\n")
    wiki.write("garden", "# Garden\n\nBeds.\n")
    page.request.post("/api/deck", data={"title": "Milk", "tags": "shop", "due_date": date.today().isoformat()})
    page.request.post("/api/deck", data={"title": "Pay rent", "tags": "task", "due_date": date.today().isoformat()})
    page.goto("/page/garden")
    deck = page.locator(".breadcrumb .module-link[data-module=deck]")
    expect(deck.locator(".module-due")).to_have_text("1")   # the #task list's only
    deck.click()
    page.wait_for_url("**/page/deck")
    expect(deck).to_have_attribute("aria-current", "page")
    expect(page.locator(".action-new")).to_have_attribute("title", "New task (n)")
    page.locator(".top-task").click()
    modal = page.locator(".task-edit-modal[open]")
    expect(modal).to_be_visible()
    expect(modal.locator(".tag-chip", has_text="task")).to_have_count(1)
    page.keyboard.press("Escape")
    page.goto("/page/garden")
    expect(page.locator(".breadcrumb > a")).to_have_count(0)   # no "My deck" stop


def test_editor_breadcrumb_keeps_only_the_logo(page, wiki):
    page.goto("/edit/index")
    expect(page.locator(".breadcrumb .module-link[data-module=wiki] svg")).to_be_visible()
    expect(page.locator(".breadcrumb .module-link[data-module=deck]")).to_be_hidden()
    for label in page.locator(".breadcrumb .module-label").all():
        expect(label).to_be_hidden()


def test_desktop_sign_out_clear_of_the_buttons(page, wiki, monkeypatch):
    monkeypatch.setattr(config, "LEAF_PASSWORD", "pw")
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
    # and Search buttons. With the tab bar along the bottom edge it sits at
    # the top, in the row of status badges.
    monkeypatch.setattr(config, "LEAF_PASSWORD", "pw")
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"password": "pw"})
    phone.goto("/page/index")
    signout = phone.get_by_role("button", name="Sign out")
    expect(signout).to_be_visible()
    s, a = signout.bounding_box(), phone.locator(".action-buttons").bounding_box()
    assert s["y"] + s["height"] <= a["y"], (s, a)
    assert s["x"] + s["width"] <= 390 and s["y"] < 100
    assert not _overlap(s, phone.locator(".breadcrumb").bounding_box())
