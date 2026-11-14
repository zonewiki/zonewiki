"""A task's assignee in the browser (docs/spec/users.md, Assigning tasks):
the modal's Assignee field, @name in a new task's title, the name on the
card, and the filter's @name, @me and @none."""

import re

import pytest
from playwright.sync_api import expect

from conftest import wait_until
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    """The browser, signed in as alice; bob can be assigned too, carol is a
    viewer and dan disabled."""
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob", "carol", "dan")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"},
                                                    "dan": {"disabled": "yes"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    return page


def new(page, title, **fields):
    resp = page.request.post("/api/tasks", data={"title": title, "private": False, **fields})
    assert resp.ok, resp.text()
    return resp.json()


def task(page, title):
    return next(t for t in page.request.get("/api/tasks").json() if t["title"] == title)


def wait_for_assignee(page, title, name):
    def there():
        found = [t for t in page.request.get("/api/tasks").json() if t["title"] == title]
        return bool(found) and found[0].get("assignee") == name
    wait_until(page, there)


def card(page, title):
    return page.locator(".task-item").filter(has=page.get_by_text(title, exact=True))


def open_edit(page, title):
    card(page, title).locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    return modal



def test_the_card_names_who_has_it(alice):
    new(alice, "Mine", assignee="alice", tags=["work"])
    new(alice, "Bob's", assignee="bob")
    new(alice, "No one's")
    alice.goto("/tasks?view=open")
    mine = card(alice, "Mine").locator(".task-assignee")
    expect(mine).to_have_text("@alice")
    expect(mine).to_have_class(re.compile(r"\btask-assignee-me\b"))
    expect(mine).to_have_attribute("title", "Assigned to you")
    bobs = card(alice, "Bob's").locator(".task-assignee")
    expect(bobs).to_have_text("@bob")
    expect(bobs).not_to_have_class(re.compile(r"task-assignee-(me|former)"))
    expect(card(alice, "No one's").locator(".task-assignee")).to_have_count(0)
    # After the tags.
    expect(card(alice, "Mine").locator(".task-tags-right > *")).to_have_text(["#work", "@alice"])


def test_the_modal_assigns_with_completion(alice):
    new(alice, "Call Sam")
    alice.goto("/tasks?view=open")
    modal = open_edit(alice, "Call Sam")
    field = modal.locator("#task-edit-assignee")
    # No one has it: the field shows once asked for, and has the focus.
    expect(field).to_be_hidden()
    modal.locator("[data-more=assignee]").click()
    expect(field).to_be_focused()
    expect(modal.locator("[data-more=assignee]")).to_be_hidden()
    expect(modal.locator(".tag-ac-item")).to_have_count(0)     # nothing offered until something is typed
    field.type("@")
    expect(modal.locator(".tag-ac-item")).to_have_text(["@alice", "@bob"])   # yourself first; no viewer, no one disabled
    field.type("b")
    expect(modal.locator(".tag-ac-item")).to_have_text(["@bob"])
    alice.keyboard.press("Tab")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@bob"])
    expect(field).to_have_value("")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Call Sam", "bob")
    expect(card(alice, "Call Sam").locator(".task-assignee")).to_have_text("@bob")
    # Another name replaces it; the × takes it off.
    modal = open_edit(alice, "Call Sam")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@bob"])
    modal.locator("#task-edit-assignee").fill("me")
    alice.keyboard.press("Enter")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@alice"])
    modal.locator(".task-field-assignee .tag-chip-x").click()
    expect(modal.locator(".task-field-assignee .tag-chip")).to_have_count(0)
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Call Sam", None)
    expect(card(alice, "Call Sam").locator(".task-assignee")).to_have_count(0)
    # The History says what happened.
    modal = open_edit(alice, "Call Sam")
    modal.locator("#task-edit-history summary").click()
    expect(modal.locator("#task-edit-history-list li").first).to_contain_text("Unassigned (was bob)")
    expect(modal.locator("#task-edit-history-list li").nth(1)).to_contain_text("Assigned to bob")


def test_a_name_that_is_no_user_keeps_the_modal_open(alice):
    new(alice, "Call Sam")
    alice.goto("/tasks?view=open")
    modal = open_edit(alice, "Call Sam")
    field = modal.locator("#task-edit-assignee")
    modal.locator("[data-more=assignee]").click()
    for name in ("carol", "dan", "zed"):
        field.fill(name)
        modal.get_by_role("button", name="Save").click()
        expect(modal).to_be_visible()
        assert "is no user a task can be assigned to" in field.evaluate("el => el.validationMessage")
    field.fill("bob")
    modal.get_by_role("button", name="Save").click()
    expect(modal).to_be_hidden()
    wait_for_assignee(alice, "Call Sam", "bob")


def test_a_new_task_s_title_assigns(alice):
    alice.goto("/tasks?view=open")
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    title = modal.locator("#task-edit-title")
    title.type("Call Sam @b")
    expect(modal.locator(".tag-ac-item")).to_have_text(["@bob"])
    alice.keyboard.press("Tab")
    expect(title).to_have_value("Call Sam @bob ")
    expect(modal.locator("#task-edit-quick")).to_have_text("@bob")
    # A private task has no assignee: it isn't offered, and @bob is text.
    expect(modal.locator("[data-more=assignee]")).to_be_visible()
    modal.locator("[data-more=private]").click()
    expect(modal.locator("#task-edit-private")).to_be_checked()
    expect(modal.locator("[data-more=assignee]")).to_be_hidden()
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    modal.locator("#task-edit-private").uncheck()
    expect(modal.locator("[data-more=assignee]")).to_be_visible()
    expect(modal.locator("#task-edit-quick")).to_have_text("@bob")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Call Sam", "bob")
    # A name that is no user stays in the title.
    alice.locator("#task-add-btn").click()
    title.fill("Mail bob@example.org @zed")
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    modal.get_by_role("button", name="Save").click()
    wait_until(alice, lambda: any(t["title"] == "Mail bob@example.org @zed" for t in alice.request.get("/api/tasks").json()))
    assert "assignee" not in task(alice, "Mail bob@example.org @zed")


def test_the_filter_takes_names_me_and_none(alice, wiki):
    new(alice, "Mine", assignee="alice")
    new(alice, "Bob's", assignee="bob")
    new(alice, "Bob's too", assignee="bob")
    new(alice, "No one's")
    alice.goto("/tasks?view=open")
    expect(card(alice, "Mine")).to_be_visible()
    search = alice.locator("#task-filter-search")
    assert "@name" in search.get_attribute("title") and "@me" in search.get_attribute("title")   # the tooltip says so
    search.fill("@me")
    expect(alice.locator(".task-item")).to_have_count(1)
    expect(alice.locator(".task-item .task-title")).to_have_text(["Mine"])
    search.fill("@none")
    expect(alice.locator(".task-item")).to_have_count(1)
    expect(alice.locator(".task-item .task-title")).to_have_text(["No one's"])
    search.fill("-@me")
    expect(alice.locator(".task-item")).to_have_count(3)
    search.fill("@alice|bob")
    expect(alice.locator(".task-item")).to_have_count(3)
    # '@' from the list starts a name; me and none come first, then who has tasks.
    search.fill("")
    search.blur()
    alice.keyboard.press("@")
    expect(search).to_be_focused()
    expect(search).to_have_value("@")
    expect(alice.locator(".tag-ac-item")).to_have_text(["@me", "@none", "@bob", "@alice"])
    search.type("b")
    alice.keyboard.press("Tab")
    expect(alice.locator(".tasks-toolbar .tag-chip-label")).to_have_text(["@bob"])
    expect(alice.locator(".task-item")).to_have_count(2)
    # A new task here starts with the filter's assignee.
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@bob"])
    modal.locator("#task-edit-title").fill("Another for Bob")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Another for Bob", "bob")
    expect(alice.locator(".task-item")).to_have_count(3)


def test_a_former_assignee_is_struck_through_and_counts_as_no_one(alice, monkeypatch):
    new(alice, "Bob's", assignee="bob")
    new(alice, "Mine", assignee="alice")
    monkeypatch.setattr(settings, "USER_SETTINGS", {**settings.USER_SETTINGS, "bob": {"disabled": "yes"}})
    alice.goto("/tasks?view=open")
    name = card(alice, "Bob's").locator(".task-assignee")
    expect(name).to_have_text("@bob")
    expect(name).to_have_class(re.compile(r"\btask-assignee-former\b"))
    search = alice.locator("#task-filter-search")
    search.fill("@none")
    expect(alice.locator(".task-item")).to_have_count(1)
    expect(alice.locator(".task-item .task-title")).to_have_text(["Bob's"])
    search.fill("@bob")                       # and is still found by the name
    expect(alice.locator(".task-item")).to_have_count(1)
    search.fill("")
    # The modal keeps the name through an edit, struck through.
    modal = open_edit(alice, "Bob's")
    chip = modal.locator(".task-field-assignee .tag-chip")
    expect(chip).to_have_class(re.compile(r"\btag-chip-former\b"))
    modal.locator("#task-edit-title").fill("Bob's old one")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Bob's old one", "bob")
    # Handed on, and the undo of a save that took it out of the view puts bob back.
    search.fill("@bob")
    expect(alice.locator(".task-item")).to_have_count(1)
    modal = open_edit(alice, "Bob's old one")
    modal.locator(".task-field-assignee .tag-chip-x").click()
    modal.locator("#task-edit-assignee").fill("alice")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Bob's old one", "alice")
    alice.get_by_role("button", name="Undo").click()
    wait_for_assignee(alice, "Bob's old one", "bob")
    assert [e["event"] for e in task(alice, "Bob's old one")["history"]] == ["created"]


def test_without_users_there_is_no_assignee(page, wiki):
    page.request.post("/api/tasks", data={"title": "Call Sam"})
    page.request.post("/api/tasks", data={"title": "Mail @bob"})
    page.goto("/tasks?view=open")
    expect(card(page, "Call Sam")).to_be_visible()
    search = page.locator("#task-filter-search")
    search.fill("@bob")                       # text to find, as any word
    expect(page.locator(".task-item")).to_have_count(1)
    search.fill("")
    modal = open_edit(page, "Call Sam")
    expect(modal.locator("#task-edit-assignee")).to_have_count(0)
    modal.get_by_role("button", name="Cancel").click()
    page.locator("#task-add-btn").click()
    modal.locator("#task-edit-title").fill("Shop @me")
    expect(modal.locator("#task-edit-quick")).to_be_hidden()


def test_one_page_shows_each_user_their_own_tasks(alice, wiki):
    """[[tasks: assignee=me]]: a locked @me chip; a new task there is yours."""
    new(alice, "Alice's", assignee="alice")
    new(alice, "Bob's", assignee="bob")
    new(alice, "No one's")
    wiki.write("mine", "# Mine\n\n[[tasks: assignee=me view=open]]\n")
    alice.goto("/page/mine")
    expect(alice.locator(".task-item .task-title")).to_have_text(["Alice's"])
    chip = alice.locator(".tasks-toolbar .tag-chip")
    expect(chip.locator(".tag-chip-label")).to_have_text(["@me"])
    expect(chip.locator(".tag-chip-x")).to_have_count(0)            # set by the page
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@alice"])
    modal.locator("#task-edit-title").fill("Another of mine")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Another of mine", "alice")
    expect(alice.locator(".task-item")).to_have_count(2)
    # Bob, on the same page.
    alice.request.post("/logout")
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    alice.goto("/page/mine")
    expect(alice.locator(".task-item .task-title")).to_have_text(["Bob's"])
    # ?assignee= on /tasks gives an ordinary chip.
    alice.goto("/tasks?view=open&assignee=none")
    expect(alice.locator(".task-item .task-title")).to_have_text(["No one's"])
    expect(alice.locator(".tasks-toolbar .tag-chip .tag-chip-x")).to_have_count(1)


def test_the_settings_dialog_sets_whose_tasks_a_list_shows(alice, wiki):
    new(alice, "Bob's", assignee="bob", tags=["work"])
    new(alice, "Alice's", assignee="alice", tags=["work"])
    wiki.write("work", "# Work\n\n[[tasks: tag=work view=open]]\n")
    alice.goto("/page/work")
    expect(alice.locator(".task-item")).to_have_count(2)
    alice.locator("#task-more-btn").click()
    alice.get_by_role("menuitem", name="List settings…").click()
    dialog = alice.locator("#task-settings-modal")
    dialog.locator("[name=assignee]").fill("@Bob")        # More is open: the list has a filter
    with alice.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert wiki.read("work") == "# Work\n\n[[tasks: tag=work view=open assignee=bob]]\n"
    expect(alice.locator(".task-item .task-title")).to_have_text(["Bob's"])
    # It opens with what the page says, More open since it is set.
    alice.locator("#task-more-btn").click()
    alice.get_by_role("menuitem", name="List settings…").click()
    expect(dialog.locator("[name=assignee]")).to_have_value("bob")


def test_several_selected_tasks_are_assigned_at_once(alice):
    """Assign… on a selection (the bar's button, the a key): to a user or
    to no one, with one undo."""
    for title in ("Three", "Two", "One"):
        new(alice, title)
    new(alice, "Bob's already", assignee="bob")
    alice.goto("/tasks?view=open")
    card(alice, "Three").locator(".task-title").click()
    card(alice, "Bob's already").locator(".task-title").click(modifiers=["Shift"])
    expect(alice.locator(".task-item.task-selected")).to_have_count(4)
    alice.locator("#task-selbar [data-sel=assign]").click()
    dlg = alice.locator(".bulk-assign-modal")
    expect(dlg.locator(".modal-label")).to_have_text("Assign 4 tasks")
    expect(dlg.locator(".bulk-list-row")).to_have_text(["@alice", re.compile(r"@bob\s*1 of 4"), re.compile(r"No one\s*3 of 4")])
    dlg.locator(".bulk-list-row", has_text="@bob").click()
    expect(alice.get_by_text("3 tasks assigned to bob")).to_be_visible()
    for title in ("One", "Two", "Three"):
        wait_for_assignee(alice, title, "bob")
        expect(card(alice, title).locator(".task-assignee")).to_have_text("@bob")
    alice.get_by_role("button", name="Undo").click()
    for title in ("One", "Two", "Three"):
        wait_for_assignee(alice, title, None)
    assert [e["event"] for e in task(alice, "One")["history"]] == ["created"]      # no trace
    wait_for_assignee(alice, "Bob's already", "bob")
    # The a key, and to no one.
    card(alice, "Bob's already").locator(".task-title").click()
    alice.keyboard.press("a")
    expect(dlg.locator(".bulk-list-row[aria-current=true]")).to_have_text(re.compile("@bob"))
    dlg.locator(".bulk-list-row", has_text="No one").click()
    expect(alice.get_by_text("1 task assigned to no one")).to_be_visible()
    wait_for_assignee(alice, "Bob's already", None)
