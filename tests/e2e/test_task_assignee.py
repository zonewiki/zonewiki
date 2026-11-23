"""A task's assignee in the browser (docs/spec/users.md, Assigning tasks):
the modal's Assignee field, @name in a new task's title, the name on the
card, and the filter's @name, @me and @none."""

import json
import re
from datetime import date

import pytest
from playwright.sync_api import expect

from conftest import box_of, make_list, more_menu, wait_until
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    """The browser, signed in as alice; bob can be assigned too, carol is a
    viewer and dan disabled. A task for someone is shared work, and with
    users a shared task is in a list: there is one, team (not pinned)."""
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob", "carol", "dan")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"},
                                                    "dan": {"disabled": "yes"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    return page


def new(page, title, **fields):
    """A task in the shared list team (list=None: in the user's Inbox)."""
    fields.setdefault("list", "team")
    resp = page.request.post("/api/tasks", data={"title": title, **{k: v for k, v in fields.items() if v is not None}})
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
    alice.goto("/tasks?view=open&group=none")
    mine = card(alice, "Mine").locator(".task-assignee")
    expect(mine).to_have_text("@alice")
    expect(mine).to_have_class(re.compile(r"\btask-assignee-me\b"))
    expect(mine).to_have_attribute("title", "Assigned to you")
    bobs = card(alice, "Bob's").locator(".task-assignee")
    expect(bobs).to_have_text("@bob")
    expect(bobs).not_to_have_class(re.compile(r"task-assignee-(me|former)"))
    expect(card(alice, "No one's").locator(".task-assignee")).to_have_count(0)
    # After the list and the tags.
    expect(card(alice, "Mine").locator(".task-tags-right > *")).to_have_text(["team", "#work", "@alice"])


def test_the_modal_assigns_with_completion(alice):
    new(alice, "Call Sam")
    alice.goto("/tasks?view=open&group=none")
    modal = open_edit(alice, "Call Sam")
    field = modal.locator("#task-edit-assignee")
    # A shared task always shows who has it: no one, and nothing to ask for.
    expect(field).to_be_visible()
    expect(field).to_have_attribute("placeholder", "No one")
    expect(modal.locator("[data-more=assignee]")).to_be_hidden()
    field.click()
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
    alice.goto("/tasks?view=open&group=none")
    modal = open_edit(alice, "Call Sam")
    field = modal.locator("#task-edit-assignee")
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
    """@name in a new task's title: the task is for that user, and the
    Assignee field shows it. A new task starts in the Inbox, which is alice's
    alone, so the dialog asks for a shared list and offers them; in a list of
    her own the task is hers, the field says so, and @name is text."""
    make_list(alice, "own", private=True)
    alice.goto("/tasks?view=open&group=none")
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    title, lists = modal.locator("#task-edit-title"), modal.locator("#task-edit-list")
    expect(lists).to_have_value("")
    title.type("Call Sam @b")
    expect(modal.locator(".tag-ac-item")).to_have_text(["@bob"])
    alice.keyboard.press("Tab")
    expect(title).to_have_value("Call Sam @bob ")
    expect(modal.locator("#task-edit-quick")).to_have_text("@bob")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@bob"])   # the field follows the title
    expect(lists).to_have_value("")                # nothing chosen for her
    target, note = modal.locator("#task-edit-target"), modal.locator("#task-edit-list-note")
    expect(target.locator(".task-edit-target-list")).to_have_text(["team"])
    target.locator(".task-edit-target-list").click()
    expect(lists).to_have_value("team")
    expect(target).to_have_text("Adds to the list team: everyone sees it.")
    expect(note).to_be_hidden()                  # a new task: the line under the title says who sees it
    expect(modal.locator("#task-edit-assignee")).to_be_visible()   # in a shared list: who has it shows
    # A task in a private list she chose is hers: the field says so, and @bob is text.
    lists.select_option("own")
    expect(target).to_have_text("Adds to the list own: only you see it.")
    expect(note).to_be_hidden()
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@alice"])
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(0)     # hers: not to be taken off
    expect(title).to_have_value("Call Sam @bob ")  # text now, and still there
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    # The same in the Inbox, once she chose it herself.
    lists.select_option("")
    expect(lists).to_have_value("")
    expect(target).to_have_text("Adds to your Inbox: only you see it.")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@alice"])
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    lists.select_option("team")
    expect(target).to_have_text("Adds to the list team: everyone sees it.")
    expect(modal.locator("#task-edit-assignee")).to_be_visible()
    expect(modal.locator("#task-edit-quick")).to_have_text("@bob")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Call Sam", "bob")
    assert task(alice, "Call Sam")["list"] == "team" and task(alice, "Call Sam")["private"] is False
    # A name that is no user stays in the title, and the task in her Inbox.
    alice.locator("#task-add-btn").click()
    title.fill("Mail bob@example.org @zed")
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    expect(lists).to_have_value("")
    modal.get_by_role("button", name="Save").click()
    wait_until(alice, lambda: any(t["title"] == "Mail bob@example.org @zed" for t in alice.request.get("/api/tasks").json()))
    made = task(alice, "Mail bob@example.org @zed")
    assert "assignee" not in made and "list" not in made and made["private"] is True


def test_the_name_list_stays_when_the_filter_is_left_and_entered_again(alice, wiki):
    # Regression: leaving the field closes its list 150 ms later, and that
    # also closed the list of a field entered again meanwhile: Tab then
    # completed nothing (on a busy machine, in the test below).
    new(alice, "Bob's", assignee="bob")
    alice.goto("/tasks?view=open&group=none")
    search = alice.locator("#task-filter-search")
    search.focus()
    search.blur()
    search.type("@b")
    expect(alice.locator(".tag-ac-item")).to_have_text(["@bob"])
    alice.wait_for_timeout(300)                      # past the blur's 150 ms
    expect(alice.locator(".tag-ac-item")).to_have_text(["@bob"])


def test_the_filter_takes_names_me_and_none(alice, wiki):
    new(alice, "Mine", assignee="alice")
    new(alice, "Bob's", assignee="bob")
    new(alice, "Bob's too", assignee="bob")
    new(alice, "No one's")
    alice.goto("/tasks?view=open&group=none")
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
    # Tab takes what the list offers: once it offers @bob alone, not before.
    expect(alice.locator(".tag-ac-item")).to_have_text(["@bob"])
    alice.keyboard.press("Tab")
    expect(alice.locator(".tasks-toolbar .tag-chip-label")).to_have_text(["@bob"])
    expect(alice.locator(".task-item")).to_have_count(2)
    # A new task here starts with the filter's assignee.
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@bob"])
    modal.locator("#task-edit-title").fill("Another for Bob")
    modal.locator(".task-edit-target-list", has_text="team").click()   # a task for someone: which shared list
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(alice, "Another for Bob", "bob")
    assert task(alice, "Another for Bob")["list"] == "team"      # out of her Inbox: a task for someone is shared
    expect(alice.locator(".task-item")).to_have_count(3)


def test_a_former_assignee_is_struck_through_and_counts_as_no_one(alice, monkeypatch):
    new(alice, "Bob's", assignee="bob")
    new(alice, "Mine", assignee="alice")
    monkeypatch.setattr(settings, "USER_SETTINGS", {**settings.USER_SETTINGS, "bob": {"disabled": "yes"}})
    alice.goto("/tasks?view=open&group=none")
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
    page.goto("/tasks?view=open&group=none")
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
    """[[tasks: assignee=me]]: a locked @me chip; a new task there is yours:
    in your Inbox, where it needs no name and no shared list."""
    new(alice, "Alice's", assignee="alice")
    new(alice, "Bob's", assignee="bob")
    new(alice, "No one's")
    wiki.write("mine", "# Mine\n\n[[tasks: assignee=me view=open group=none]]\n")
    alice.goto("/page/mine")
    expect(alice.locator(".task-item .task-title")).to_have_text(["Alice's"])
    chip = alice.locator(".tasks-toolbar .tag-chip")
    expect(chip.locator(".tag-chip-label")).to_have_text(["@me"])
    expect(chip.locator(".tag-chip-x")).to_have_count(0)            # set by the page
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@alice"])
    modal.locator("#task-edit-title").fill("Another of mine")
    expect(modal.locator("#task-edit-target")).to_have_text("Adds to your Inbox: only you see it.")   # no question
    modal.get_by_role("button", name="Save").click()
    expect(alice.locator(".task-item")).to_have_count(2)
    made = task(alice, "Another of mine")
    assert made["private"] is True and "assignee" not in made and "list" not in made
    # Bob, on the same page.
    alice.request.post("/logout")
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    alice.goto("/page/mine")
    expect(alice.locator(".task-item .task-title")).to_have_text(["Bob's"])
    # ?assignee= on /tasks gives an ordinary chip.
    alice.goto("/tasks?view=open&group=none&assignee=none")
    expect(alice.locator(".task-item .task-title")).to_have_text(["No one's"])
    expect(alice.locator(".tasks-toolbar .tag-chip .tag-chip-x")).to_have_count(1)


def test_the_settings_dialog_sets_whose_tasks_a_list_shows(alice, wiki):
    new(alice, "Bob's", assignee="bob", tags=["work"])
    new(alice, "Alice's", assignee="alice", tags=["work"])
    wiki.write("work", "# Work\n\n[[tasks: tag=work view=open group=none]]\n")
    alice.goto("/page/work")
    expect(alice.locator(".task-item")).to_have_count(2)
    more_menu(alice)
    alice.get_by_role("menuitem", name="List settings…").click()
    dialog = alice.locator("#task-settings-modal")
    expect(dialog.locator("[name=tag]")).to_be_visible()   # shown: the list has that filter
    dialog.locator(".task-add-field", has_text="Filter by person").click()
    dialog.locator("[name=assignee]").fill("@Bob")
    with alice.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert wiki.read("work") == "# Work\n\n[[tasks: tag=work view=open group=none assignee=bob]]\n"
    expect(alice.locator(".task-item .task-title")).to_have_text(["Bob's"])
    # It opens with what the page says, More open since it is set.
    more_menu(alice)
    alice.get_by_role("menuitem", name="List settings…").click()
    expect(dialog.locator("[name=assignee]")).to_have_value("bob")


def sign_in(page, name):
    page.request.post("/logout")
    page.request.post("/login", form={"username": name, "password": "pw"})


def test_whoever_sees_a_list_exports_it(alice, wiki):
    # Export tasks is no list setting: a member who doesn't manage the list
    # has it, and Import…; a viewer has Export alone.
    new(alice, "Ship it")
    sign_in(alice, "bob")
    alice.goto("/tasks?list=team")
    more_menu(alice)
    expect(alice.locator("#page-menu [role=menuitem]:visible")).to_have_text(["New list…", "Pin", "Export tasks", "Import tasks…"])
    alice.keyboard.press("Escape")
    sign_in(alice, "carol")
    alice.goto("/tasks?list=team")
    expect(card(alice, "Ship it")).to_be_visible()
    more_menu(alice)
    expect(alice.locator("#page-menu [role=menuitem]:visible")).to_have_text(["Export tasks"])
    with alice.expect_download() as download:
        alice.locator("#page-menu").get_by_role("menuitem", name="Export tasks").click()
    assert download.value.suggested_filename.startswith("zone-tasks-team-")
    assert [t["title"] for t in json.loads(open(download.value.path()).read())["tasks"]] == ["Ship it"]
    # On a page, the embed's ⋯ the same.
    wiki.write("team-page", "# Team\n\n[[tasks: list=team]]\n")
    alice.goto("/page/team-page")
    more_menu(alice)
    expect(alice.get_by_role("menuitem")).to_have_text(["Export tasks"])


def test_several_selected_tasks_are_assigned_at_once(alice):
    """Assign… on a selection (the bar's button, the a key): to a user or
    to no one, with one undo."""
    for title in ("Three", "Two", "One"):
        new(alice, title)
    new(alice, "Bob's already", assignee="bob")
    alice.goto("/tasks?view=open&group=none")
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


def test_my_tasks_holds_what_is_mine(alice, wiki):
    """My tasks, where the Tasks link goes: her own tasks (her Inbox, her
    private lists unless one is left out) and the shared ones assigned to
    her, wherever they are; a pin decides nothing. A task given to someone
    else leaves it; its settings are her own."""
    make_list(alice, "side")
    make_list(alice, "own", private=True)
    make_list(alice, "someday", private=True)
    alice.request.post("/tasks/pin-list", form={"list": "team", "pin": "1"}, max_redirects=0)   # no visit to the list
    assert alice.request.post("/api/tasks/settings", data={"list": "someday", "set": {"home": "off"}}).ok
    today = date.today().isoformat()
    new(alice, "Alice's", assignee="alice", due_date=today)
    new(alice, "Bob's", assignee="bob", due_date=today)
    new(alice, "Nobody's", due_date=today)                   # in a pinned list, but no one's: the list's
    new(alice, "Hers elsewhere", assignee="alice", list="side", due_date=today)
    new(alice, "Nobody's elsewhere", list="side")
    new(alice, "Loose", list=None, due_date=today)           # her Inbox
    new(alice, "Own one", list="own", due_date=today)
    new(alice, "Some day", list="someday", due_date=today)   # left out
    alice.goto("/page/index")
    link = alice.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(link.locator(".module-due")).to_have_text("4")    # what My tasks shows
    link.click()
    alice.wait_for_url("**/tasks?mine=1")
    expect(alice.locator("h1")).to_have_text("My tasks")
    line = alice.locator(".tasks-mine-line")
    expect(line).to_have_text("Your own tasks and what is assigned to you, but the lists left out of it (#someday). Given to others: 1")
    expect(line.locator("a").first).to_have_attribute("href", "/tasks?list=someday")
    alice.goto("/tasks?mine=1&view=open&group=none")
    titles = alice.locator(".task-item .task-title")
    expect(titles).to_have_count(4)
    assert sorted(titles.all_text_contents()) == ["Alice's", "Hers elsewhere", "Loose", "Own one"]
    expect(alice.locator(".tasks-toolbar .tag-chip")).to_have_count(0)        # no filter: the view is the rule
    expect(card(alice, "Hers elsewhere").locator(".task-tag-list")).to_have_text("side")
    expect(card(alice, "Loose").locator(".task-tag-list")).to_have_count(0)
    expect(card(alice, "Loose").locator(".task-private")).to_be_visible()
    # The list itself shows everyone's.
    alice.goto("/tasks?list=team&view=open&group=none")
    expect(alice.locator(".task-item")).to_have_count(3)
    # Given to bob, a task leaves My tasks and its count; undone, it is back.
    alice.goto("/tasks?mine=1&view=open&group=none")
    card(alice, "Alice's").locator(".task-title").click()
    alice.keyboard.press("a")
    alice.locator(".bulk-assign-modal .bulk-list-row", has_text="@bob").click()
    expect(card(alice, "Alice's")).to_have_count(0)
    wait_for_assignee(alice, "Alice's", "bob")
    expect(titles).to_have_count(3)
    expect(link.locator(".module-due")).to_have_text("3")
    alice.get_by_role("button", name="Undo").click()
    expect(card(alice, "Alice's")).to_be_visible()
    wait_for_assignee(alice, "Alice's", "alice")
    # Taken from the list's backlog, a task is in My tasks.
    alice.goto("/tasks?list=team&view=open&group=none")
    card(alice, "Nobody's").locator(".task-title").click()
    alice.keyboard.press("a")
    alice.locator(".bulk-assign-modal .bulk-list-row", has_text="@alice").click()
    wait_for_assignee(alice, "Nobody's", "alice")
    alice.goto("/tasks?mine=1&view=open&group=none")
    expect(card(alice, "Nobody's")).to_be_visible()
    expect(titles).to_have_count(5)
    # Its settings: her own, and what a new task gets is not among them.
    alice.goto("/tasks?mine=1")
    more_menu(alice)
    alice.get_by_role("menuitem", name="List settings…").click()
    dialog = alice.locator("#task-settings-modal")
    expect(dialog.locator(".task-settings-where")).to_have_text("For My tasks: yours alone.")
    expect(dialog.locator(".task-settings-pin")).to_be_hidden()
    expect(dialog.locator(".task-add-field:visible")).to_have_text(["WIP limit", "Tab names"])
    dialog.locator("[name=view]").select_option("open")
    with alice.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '"mine": {"view": "open"}' in (wiki.root / "private/alice/_tasks/lists.json").read_text()
    expect(alice.locator(".task-item .task-title")).to_have_count(5)        # it opens on Open now


def test_a_task_someone_gave_you_is_marked_new(alice):
    # bob gives alice a task: its card says "new" until she does something
    # with it. What she took herself isn't new.
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    new(alice, "From bob", assignee="alice")
    alice.request.post("/login", form={"username": "alice", "password": "pw"})
    new(alice, "Her own", assignee="alice")
    alice.goto("/tasks?view=open&group=none")
    mark = card(alice, "From bob").locator(".task-new")
    expect(mark).to_have_text("new")
    expect(mark).to_have_attribute("title", re.compile(r"^bob gave you this task on "))
    expect(card(alice, "Her own").locator(".task-new")).to_have_count(0)
    # A date she sets is hers: the mark goes.
    alice.request.put(f"/api/tasks/{task(alice, 'From bob')['id']}", data={"due_date": "2030-01-01"})
    alice.reload()
    expect(card(alice, "From bob")).to_be_visible()
    expect(card(alice, "From bob").locator(".task-new")).to_have_count(0)


def test_my_tasks_settings_turn_the_mails_on(page, wiki, monkeypatch):
    # Where Zonewiki could mail her, My tasks' settings have a box for each kind
    # of mail, none ticked: mail is for who asks for it.
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"email": "alice@example.org"}})
    monkeypatch.setattr(settings, "ENV", {"ZONE_SMTP_HOST": "127.0.0.1", "ZONE_SMTP_PORT": "9", "ZONE_SMTP_TLS": "none",
                                          "ZONE_MAIL_FROM": "wiki@example.org"})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team", pin=True)
    page.goto("/tasks?mine=1")
    more_menu(page)
    page.get_by_role("menuitem", name="List settings…").click()
    dialog = page.locator("#task-settings-modal")
    box = dialog.locator("[name=mail_assigned]")
    expect(dialog.locator(".task-settings-mail label")).to_have_text(
        ["a task is given to me", "someone mentions me (@name)", "someone comments on a task I have, gave or follow"])
    for name in ("mail_assigned", "mail_mentioned", "mail_comments"):
        expect(dialog.locator(f"[name={name}]")).not_to_be_checked()
    box.check()
    dialog.locator("[name=mail_comments]").check()
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '"mine": {"mail": "assigned,comments"}' in (wiki.root / "private/alice/_tasks/lists.json").read_text()
    more_menu(page)
    page.get_by_role("menuitem", name="List settings…").click()
    expect(box).to_be_checked()
    expect(dialog.locator("[name=mail_mentioned]")).not_to_be_checked()
    box.uncheck()
    dialog.locator("[name=mail_comments]").uncheck()
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '"mail"' not in (wiki.root / "private/alice/_tasks/lists.json").read_text()
    # bob has no address: no box.
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    page.goto("/tasks?mine=1")
    more_menu(page)
    page.get_by_role("menuitem", name="List settings…").click()
    expect(dialog).to_be_visible()
    expect(box).to_be_hidden()


def test_the_tasks_link_says_that_something_is_new_for_you(alice):
    # bob gives alice a task with no date: the Tasks link counts it as new,
    # on any page, and no longer once she has touched it.
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    new(alice, "From bob", assignee="alice")
    alice.request.post("/login", form={"username": "alice", "password": "pw"})
    alice.goto("/")
    badge = alice.locator('.module-link[data-module="tasks"] .module-new').first
    expect(badge).to_have_text("1")
    expect(alice.locator('.module-link[data-module="tasks"]').first).to_have_attribute("title", "Tasks: 1 update for you")
    # On the Tasks page Updates' pill has it (My tasks' counts what is due
    # alone), and it goes when she sets a date, without a reload.
    alice.goto("/tasks?mine=1&view=open&group=none")
    pill = alice.locator('.tasks-place[data-place="updates"] .module-new')
    expect(pill).to_have_text("1")
    expect(alice.locator('.tasks-place[data-place="mine"] .module-new')).to_be_hidden()
    card(alice, "From bob").locator(".task-title").click()
    alice.keyboard.press("f")                        # starts today: hers now
    expect(pill).to_be_hidden()
    expect(alice.locator('.module-link[data-module="tasks"] .module-new').first).to_be_hidden()


def test_opening_a_task_given_to_you_takes_the_new_away(alice):
    # bob gives alice two tasks. Opening one (its dialog, or its details)
    # takes its "new" away, on the card and on the Tasks link, and its
    # History says that she saw it.
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    new(alice, "With notes", assignee="alice", description="Read **this**")
    new(alice, "Plain", assignee="alice")
    alice.request.post("/login", form={"username": "alice", "password": "pw"})
    alice.goto("/tasks?mine=1&view=open&group=none")
    badge = alice.locator('.module-link[data-module="tasks"] .module-new').first
    expect(badge).to_have_text("2")
    # Its details opened: seen.
    card(alice, "With notes").locator(".task-has-notes").click()
    expect(card(alice, "With notes").locator(".task-desc strong")).to_be_visible()
    expect(card(alice, "With notes").locator(".task-new")).to_have_count(0)
    expect(card(alice, "Plain").locator(".task-new")).to_have_count(1)
    expect(badge).to_have_text("1")
    # Its dialog opened: seen, and the History says so.
    modal = open_edit(alice, "Plain")
    expect(card(alice, "Plain").locator(".task-new")).to_have_count(0)
    expect(badge).to_be_hidden()
    modal.locator("#task-edit-history summary").click()
    expect(modal.locator("#task-edit-history")).to_contain_text("Seen")
    alice.keyboard.press("Escape")
    events = [(e["event"], e.get("by")) for e in task(alice, "Plain")["history"]]
    assert events == [("created", "bob"), ("seen", "alice")]
    # bob, who gave them, opens one: nothing is logged for him.
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    alice.goto("/tasks?list=team&view=open&group=none")
    open_edit(alice, "Plain")
    alice.wait_for_timeout(300)
    assert [e["event"] for e in task(alice, "Plain")["history"]] == ["created", "seen"]


def section(page, key):
    return page.locator(f".board-day-divider[data-drop-bucket='{key}']")


def cut(page):
    """The choices of the Open tab's control that show, by their names."""
    return page.locator(".tasks-cut button:visible").all_inner_texts()


def test_by_person_shows_who_has_what_and_a_drop_assigns(alice):
    """The Open tab By person (docs/spec/tasks.md, Views): a section per
    assignee, Unassigned and your own first, and a card dropped on a
    section is that user's, with an undo."""
    new(alice, "Bob's", assignee="bob")
    new(alice, "Mine", assignee="alice")
    new(alice, "No one's")
    alice.goto("/tasks?list=team&view=open&group=none")
    assert cut(alice) == ["List", "Plan", "By person"]
    alice.locator(".tasks-cut [data-group=person]").click()
    expect(alice.locator(".board-day-label")).to_have_text(["Unassigned", "You", "@bob"])
    expect(section(alice, "p:bob").locator(".board-day-count")).to_have_text("1")
    card(alice, "No one's").drag_to(section(alice, "p:bob"))
    wait_for_assignee(alice, "No one's", "bob")
    expect(section(alice, "p:bob").locator(".board-day-count")).to_have_text("2")
    expect(alice.get_by_text("1 task assigned to bob")).to_be_visible()
    alice.keyboard.press("Control+z")
    wait_for_assignee(alice, "No one's", None)
    # On Unassigned a task is let go; on your own it is taken.
    card(alice, "Bob's").drag_to(section(alice, "p:"))
    wait_for_assignee(alice, "Bob's", None)
    card(alice, "Bob's").drag_to(section(alice, "p:alice"))
    wait_for_assignee(alice, "Bob's", "alice")
    # d goes through the choices; a reload stays By person.
    alice.reload()
    expect(alice.locator(".tasks-cut [data-group=person]")).to_have_attribute("aria-pressed", "true")
    card(alice, "Mine").locator(".task-title").click()
    alice.keyboard.press("d")
    expect(alice.locator(".tasks-cut [data-group=none]")).to_have_attribute("aria-pressed", "true")


def test_by_list_in_my_tasks_and_a_drop_moves(alice):
    """By list, where a view holds several lists: a section per list, the
    Inbox first; a drop moves the task, a private one into a shared list
    only after asking, and a shared one never into the Inbox."""
    new(alice, "Shared", assignee="alice")
    new(alice, "Private", list=None)
    alice.goto("/tasks?mine=1&view=open&group=none")
    assert cut(alice) == ["List", "Plan", "By list"]      # My tasks are all yours: not By person
    alice.locator(".tasks-cut [data-group=list]").click()
    expect(alice.locator(".board-day-label")).to_have_text(["🔒 Inbox", "team"])
    card(alice, "Shared").drag_to(section(alice, "l:"))
    expect(section(alice, "l:team").locator(".board-day-count")).to_have_text("1")   # a shared task stays shared
    card(alice, "Private").drag_to(section(alice, "l:team"))
    modal = alice.locator("#task-share-modal")
    expect(modal).to_contain_text("everyone who uses the wiki will see and change it")
    modal.locator("#task-share-cancel").click()
    assert task(alice, "Private").get("list") is None
    card(alice, "Private").drag_to(section(alice, "l:team"))
    modal.locator("#task-share-confirm").click()
    wait_until(alice, lambda: task(alice, "Private").get("list") == "team")
    expect(section(alice, "l:team").locator(".board-day-count")).to_have_text("2")


def test_one_person_has_list_and_plan(page, wiki):
    """Without users, and with no list, the control is what the Plan switch
    was: List and Plan."""
    page.request.post("/api/tasks", data={"title": "One"})
    page.goto("/tasks?view=open&group=none")
    assert cut(page) == ["List", "Plan"]
    expect(page.locator(".tasks-cut [data-group=none]")).to_have_attribute("aria-pressed", "true")
    page.locator(".tasks-cut [data-group=day]").click()
    expect(page.locator(".board-day-divider[data-drop-bucket='unplanned']")).to_be_visible()


def test_a_task_given_to_you_is_in_updates_and_stays_there_when_read(alice, new_page):
    """Updates (docs/spec/tasks.md, News on your tasks) holds what someone
    gave you and you haven't opened, whatever its dates: a view of its own,
    with no tabs. Opened, the task is read and its card stays, dimmed, until
    the page is left; it is in My tasks all along, as it is hers to do."""
    bob = new_page()
    bob.request.post("/login", form={"username": "bob", "password": "pw"})
    assert bob.request.post("/api/tasks", data={"title": "From bob", "list": "team", "assignee": "alice"}).ok
    new(alice, "Own, today", assignee="alice", start_date=date.today().isoformat())
    alice.goto("/tasks?updates=1")
    expect(alice.locator(".page-header h1")).to_have_text("Updates")
    expect(alice.locator('.tasks-place[data-place="updates"]')).to_have_attribute("aria-current", "page")
    for gone in ("#tasks-tabs", "#task-add-btn", "#task-closed-link", "#task-cut"):
        expect(alice.locator(gone)).to_be_hidden()
    expect(alice.locator(".task-item .task-title")).to_have_text(["From bob"])     # her own task is no news
    expect(alice.locator(".task-item.task-update[draggable]")).to_have_count(0)    # nothing is ranked there
    expect(card(alice, "From bob").locator(".task-news-what")).to_have_text("bob gave it to you")
    pill = alice.locator('.tasks-place[data-place="updates"] .module-new')
    expect(pill).to_have_text("1")
    # Opened: read, and still there.
    open_edit(alice, "From bob")
    alice.keyboard.press("Escape")
    expect(pill).to_be_hidden()
    expect(alice.locator(".task-item.task-update.task-read .task-title")).to_have_text(["From bob"])
    expect(card(alice, "From bob").locator(".task-news.task-news-read .task-news-what")).to_have_text("bob gave it to you")
    expect(card(alice, "From bob").locator(".task-read-btn")).to_have_count(0)
    # Left and come back to: nothing is new.
    alice.reload()
    expect(alice.locator(".tasks-empty")).to_contain_text("No updates.")
    # It is hers to do: in My tasks, and without a date on Open.
    alice.goto("/tasks?mine=1&view=open&group=none")
    expect(card(alice, "From bob")).to_be_visible()
    expect(alice.locator(".updates-heading")).to_have_count(0)
    # The Tasks links keep going to the view before Updates.
    alice.goto("/tasks?list=team")
    alice.goto("/tasks?updates=1")
    alice.goto("/page/index")
    expect(alice.locator(".breadcrumb .module-link[data-module=tasks]")).to_have_attribute("href", "/tasks?list=team")


def test_a_shared_list_opens_on_open_and_your_own_on_focus(alice):
    """A team's list opens where its backlog is; a private list, My tasks
    and All tasks on Focus. The list's own setting still wins."""
    make_list(alice, "diary", private=True)
    alice.goto("/tasks?list=team")
    expect(alice.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Open"])
    for url in ("/tasks?list=diary", "/tasks?mine=1"):         # what is yours to do
        alice.goto(url)
        expect(alice.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Today"])
    for url in ("/tasks?list=inbox", "/tasks"):                # a place you look into
        alice.goto(url)
        expect(alice.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Open"])
    assert alice.request.post("/api/tasks/settings", data={"list": "team", "set": {"view": "today"}}).ok
    alice.goto("/tasks?list=team")
    expect(alice.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Today"])


def test_what_you_gave_to_others_is_one_click_from_my_tasks(alice):
    """My tasks links to the open tasks you made that someone else has
    (docs/spec/tasks.md, Lists → My tasks): not yours to do, so not in it."""
    new(alice, "For bob", assignee="bob")
    new(alice, "Mine", assignee="alice")
    new(alice, "No one's")
    assert task(alice, "For bob")["created_by"] == "alice"
    alice.goto("/tasks?mine=1&view=open&group=none")
    expect(card(alice, "For bob")).to_have_count(0)
    link = alice.locator(".tasks-given")
    expect(link).to_have_text("Given to others: 1")
    link.click()
    expect(alice.locator(".task-item .task-title")).to_have_text(["For bob"])
    expect(alice.locator(".board-day-label")).to_have_text(["Unassigned", "You", "@bob"])
    expect(alice.locator(".tasks-cut [data-group=person]")).to_have_attribute("aria-pressed", "true")


def test_a_comment_is_its_authors(alice, new_page):
    """Only its author edits or deletes a comment; an admin deletes any."""
    t = new(alice, "Talk", assignee="bob")
    bob = new_page()
    bob.request.post("/login", form={"username": "bob", "password": "pw"})
    url = f"/api/tasks/{t['id']}/comments"
    cid = bob.request.post(url, data={"text": "bob's word"}).json()["comments"][0]["id"]
    mine = alice.request.post(url, data={"text": "alice's word"}).json()["comments"][1]["id"]
    # bob can't touch alice's; alice (an admin) may delete bob's, not edit it.
    assert bob.request.put(f"{url}/{mine}", data={"text": "changed"}).status == 403
    assert bob.request.delete(f"{url}/{mine}").status == 403
    assert alice.request.put(f"{url}/{cid}", data={"text": "changed"}).status == 403
    # Nor by sending the comments whole, as an undo does.
    whole = [{**c, "text": "changed"} if c["id"] == mine else c for c in task(bob, "Talk")["comments"]]
    assert bob.request.put(f"/api/tasks/{t['id']}", data={"comments": whole}).status == 403
    assert bob.request.put(f"/api/tasks/{t['id']}", data={"comments": [c for c in whole if c["id"] != mine]}).status == 403
    assert [c["text"] for c in task(alice, "Talk")["comments"]] == ["bob's word", "alice's word"]
    # In the dialog: bob's comment has no ✎ for alice, but × (she is an admin); her own has both.
    alice.goto("/tasks?list=team")
    modal = open_edit(alice, "Talk")
    theirs = modal.locator(f"[data-comment='{cid}']")
    expect(theirs.locator("[data-comment-action=edit]")).to_have_count(0)
    expect(theirs.locator("[data-comment-action=delete]")).to_have_count(1)
    expect(modal.locator(f"[data-comment='{mine}'] [data-comment-action=edit]")).to_have_count(1)
    # bob's own: he edits and deletes it, and the delete's undo puts it back.
    assert bob.request.put(f"{url}/{cid}", data={"text": "bob's second word"}).ok
    kept = task(bob, "Talk")["comments"]
    assert bob.request.delete(f"{url}/{cid}").ok
    assert bob.request.put(f"/api/tasks/{t['id']}", data={"comments": kept}).ok
    assert [c["text"] for c in task(alice, "Talk")["comments"]] == ["bob's second word", "alice's word"]


def test_updates_show_what_happened_on_your_tasks(alice, new_page):
    """Updates (docs/spec/tasks.md, News on your tasks): a comment on a task
    you gave away, with what happened in words, though it is not yours to
    do; Mark read sees one without opening it, Mark all read all of them,
    and the cards stay; the Tasks link counts them meanwhile."""
    t = new(alice, "For bob", assignee="bob")
    other = new(alice, "Also for bob", assignee="bob")
    bob = new_page()
    bob.request.post("/login", form={"username": "bob", "password": "pw"})
    assert bob.request.post(f"/api/tasks/{t['id']}/comments", data={"text": "Need the contract first"}).ok
    assert bob.request.put(f"/api/tasks/{other['id']}", data={"completed": True}).ok
    # Not hers to do: My tasks has neither, and a line that leads to Updates.
    alice.goto("/tasks?mine=1")
    expect(alice.locator(".tasks-app[data-loaded]")).to_be_visible()
    expect(alice.locator(".task-item")).to_have_count(0)
    note = alice.locator(".tasks-updates-note")
    expect(note).to_have_text("2 updates for you →")
    note.locator("a").click()
    expect(alice).to_have_url(re.compile(r"/tasks\?updates=1$"))
    expect(alice.locator(".task-item.task-update")).to_have_count(2)
    expect(card(alice, "For bob").locator(".task-news-what")).to_have_text("bob commented")
    expect(card(alice, "Also for bob").locator(".task-news-what")).to_have_text("bob closed it")   # a closed task too
    # The card says what the comment says, so it is read without opening the
    # task; a card whose news is no comment has no such line, and the small
    # mark other views have isn't there beside the line.
    expect(card(alice, "For bob").locator(".task-news-comment")).to_have_text("Need the contract first")
    expect(card(alice, "Also for bob").locator(".task-news-comment")).to_have_count(0)
    expect(alice.locator(".task-item.task-update .task-new")).to_have_count(0)
    # More since she last looked: all of it on the line, the latest comment under it.
    assert bob.request.post(f"/api/tasks/{t['id']}/comments", data={"text": "Got it, *starting* now"}).ok
    alice.reload()
    expect(card(alice, "For bob").locator(".task-news-what")).to_have_text("bob commented twice")
    expect(card(alice, "For bob").locator(".task-news-comment")).to_have_text("Got it, *starting* now")
    expect(card(alice, "For bob").locator(".task-news-when")).to_be_visible()
    dot =alice.locator('.module-link[data-module="tasks"] .module-new').first
    expect(dot).to_have_text("2")
    # Mark read on a card: that one is read, and stays.
    card(alice, "For bob").locator(".task-read-btn").click()
    expect(alice.locator(".task-item.task-read .task-title")).to_have_text(["For bob"])
    expect(dot).to_have_text("1")
    assert [e["event"] for e in task(alice, "For bob")["history"]][-1] == "seen"
    # Mark all read, beside the heading: the rest, and the button goes.
    button = alice.locator("#updates-read-btn")
    button.click()
    expect(alice.locator(".task-item.task-read")).to_have_count(2)
    expect(button).to_be_hidden()
    expect(dot).to_be_hidden()
    expect(alice.locator('.tasks-place[data-place="updates"] .module-new')).to_be_hidden()


def test_the_closed_view_shows_a_page_and_asks_for_more(alice):
    """Closed shows the latest closed tasks and says how many more there
    are; Show more and the filter ask the server again."""
    for i in range(53):
        t = new(alice, f"Done {i:02d}", tags=["odd"] if i % 2 else [])
        assert alice.request.put(f"/api/tasks/{t['id']}", data={"completed": True}).ok
    alice.goto("/tasks?list=team&view=closed")
    cards = alice.locator(".task-item")
    expect(cards).to_have_count(50)
    more = alice.locator("#tasks-show-more-btn")
    expect(more).to_have_text("Show more (3 remaining)")
    more.click()
    expect(cards).to_have_count(53)
    expect(more).to_have_count(0)
    # The filter is the server's too: all of what it matches, counted.
    field = alice.locator("#task-filter-search")
    field.fill("#odd")
    field.press("Enter")
    expect(cards).to_have_count(26)
    field.fill("Done 07")
    expect(cards).to_have_count(1)


def test_a_cards_menu_assigns(alice):
    """Assign… is in a task's own menu too, not only on a selection."""
    new(alice, "Shared work")
    alice.goto("/tasks?view=open&group=none")
    card(alice, "Shared work").locator(".task-title").click(button="right")
    alice.get_by_role("menuitem", name="Assign…").click()
    dialog = alice.locator(".bulk-assign-modal")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".bulk-list-note")).to_have_count(0)      # a shared task: nothing to say of a list
    dialog.locator(".bulk-list-row", has_text="@bob").click()
    wait_for_assignee(alice, "Shared work", "bob")


def test_a_private_task_is_yours_and_assign_gives_it_away_with_a_shared_list(alice):
    """A private task is its user's (docs/spec/users.md, A private task is
    yours): Assign… has their name ticked and no No one, and someone else's
    name asks for a shared list next, which shares the task and gives it to
    them in one write."""
    make_list(alice, "own", private=True)
    new(alice, "My own", list=None)
    new(alice, "Kept", list="own")
    alice.goto("/tasks?mine=1&view=open&group=none")
    card(alice, "My own").locator(".task-title").click(button="right")
    alice.get_by_role("menuitem", name="Assign…").click()
    dialog = alice.locator(".bulk-assign-modal")
    expect(dialog.locator(".modal-label")).to_have_text("Assign 1 task")
    expect(dialog.locator(".bulk-list-note")).to_have_text("This task is private, yours alone: for someone else, you choose a shared list next.")
    expect(dialog.locator(".bulk-list-row")).to_have_text([re.compile("@alice"), "@bob"])      # no No one: it is hers
    expect(dialog.locator(".bulk-list-row[aria-current=true]")).to_have_text(re.compile("@alice"))
    # Her own name: it is hers already, and nothing is written.
    dialog.locator(".bulk-list-row", has_text="@alice").click()
    expect(dialog).to_have_count(0)
    assert task(alice, "My own")["private"] is True and [e["event"] for e in task(alice, "My own")["history"]] == ["created"]
    # Bob: which shared list? Only those, and a new one; Cancel leaves it hers.
    card(alice, "My own").locator(".task-title").click()
    alice.keyboard.press("a")
    dialog.locator(".bulk-list-row", has_text="@bob").click()
    lists = alice.locator(".bulk-list-modal:not(.bulk-assign-modal)")
    expect(lists.locator(".modal-label")).to_have_text("Give 1 task to bob: choose a shared list")
    expect(lists.locator(".bulk-give-note")).to_have_text(
        "This task is private, yours alone. Whoever sees the list will see and change it, and it can’t be made private again.")
    expect(lists.locator(".bulk-list-row")).to_have_text([re.compile(r"team\s*shares it with everyone$"), "+New list…"])
    lists.get_by_role("button", name="Cancel").click()
    assert task(alice, "My own")["private"] is True
    alice.keyboard.press("a")
    dialog.locator(".bulk-list-row", has_text="@bob").click()
    lists.locator(".bulk-list-row", has_text="team").click()
    wait_for_assignee(alice, "My own", "bob")
    given = task(alice, "My own")
    assert given["private"] is False and given["list"] == "team"
    assert [e["event"] for e in given["history"]] == ["created", "shared", "assigned"]      # to bob at once, never to her
    expect(alice.get_by_text("1 task shared in list “team” and assigned to bob")).to_be_visible()
    expect(alice.get_by_role("button", name="Undo")).to_have_count(0)       # shared is shared
    expect(card(alice, "My own")).to_have_count(0)          # bob's: no longer in her My tasks

    # Several, a shared one of hers among them: one write gives them all, and
    # the undo takes back what can be (who has the shared one).
    new(alice, "Shared, mine", assignee="alice")
    alice.goto("/tasks?mine=1&view=open&group=none")
    card(alice, "Kept").locator(".task-title").click()
    card(alice, "Shared, mine").locator(".task-title").click(modifiers=["ControlOrMeta"])
    alice.locator("#task-selbar [data-sel=assign]").click()
    expect(dialog.locator(".modal-label")).to_have_text("Assign 2 tasks")
    expect(dialog.locator(".bulk-list-note")).to_have_text("One of them is private, yours alone: for someone else, you choose a shared list next.")
    expect(dialog.locator(".bulk-list-row")).to_have_text([re.compile("@alice"), "@bob", re.compile("No one")])
    dialog.locator(".bulk-list-row", has_text="@bob").click()
    expect(lists.locator(".modal-label")).to_have_text("Give 1 task to bob: choose a shared list")
    # A new list for it: shared, with no box to make it private.
    lists.locator(".bulk-list-new").click()
    expect(lists.locator(".modal-label")).to_have_text("Give 1 task to bob: a new shared list")
    expect(lists.locator("#bulk-list-private")).to_have_count(0)
    lists.locator("#bulk-list-name").fill("handover")
    lists.get_by_role("button", name="Create and move").click()
    wait_for_assignee(alice, "Kept", "bob")
    wait_for_assignee(alice, "Shared, mine", "bob")
    assert task(alice, "Kept")["list"] == "handover" and task(alice, "Kept")["private"] is False
    expect(alice.get_by_text("2 tasks assigned to bob, 1 of them shared in the new list “handover”")).to_be_visible()
    alice.get_by_role("button", name="Undo").click()
    wait_for_assignee(alice, "Shared, mine", "alice")
    assert task(alice, "Kept")["assignee"] == "bob" and task(alice, "Kept")["private"] is False


def test_the_dialog_of_a_private_task_names_you_and_another_name_asks_for_a_shared_list(page, wiki, monkeypatch):
    """The Assignee field of a private task holds its user's name, with no ×;
    someone else's asks for a shared list under the fields, offering those
    that person sees (not a group's they are none of), and Save waits for
    one."""
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob", "carol")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin", "groups": "hiring"}, "carol": {"groups": "hiring"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    assert page.request.post("/tasks/new-list", form={"name": "hiring", "for": "group:hiring", "pin": "0"}, max_redirects=0).status == 302
    new(page, "Ask Sam", list=None)
    new(page, "Ask Zed", list=None)
    page.goto("/tasks?mine=1&view=open&group=none")
    modal = open_edit(page, "Ask Sam")
    field, lists, note = modal.locator("#task-edit-assignee"), modal.locator("#task-edit-list"), modal.locator("#task-edit-list-note")
    chips = modal.locator(".task-field-assignee .tag-chip-label")
    warn = re.compile(r"\btask-edit-target-warn\b")
    expect(chips).to_have_text(["@alice"])
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(0)
    expect(modal.locator(".task-field-assignee .tag-chip")).to_have_attribute(
        "title", "A private task is yours. Name someone else to give it away: it then goes to a shared list")
    expect(field).to_have_attribute("placeholder", "")
    expect(note).to_have_text("Private: only you see this task.")
    # Her own name is what it is; Backspace takes nothing off.
    field.fill("me")
    page.keyboard.press("Enter")
    expect(chips).to_have_text(["@alice"])
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(0)
    field.press("Backspace")
    expect(chips).to_have_text(["@alice"])
    expect(note).to_have_text("Private: only you see this task.")
    # Bob: the lists he sees are offered, and Save waits for one.
    field.fill("bob")
    page.keyboard.press("Enter")
    expect(chips).to_have_text(["@bob"])
    expect(note).to_have_text("A task for someone else needs a shared list: team")
    expect(note).to_have_class(warn)
    modal.get_by_role("button", name="Save").click()
    expect(modal).to_be_visible()
    assert lists.evaluate("el => el.validationMessage") == "A task for someone else needs a shared list"
    assert task(page, "Ask Sam")["private"] is True
    # Carol is in the group: its list is offered for her too.
    field.fill("carol")
    page.keyboard.press("Enter")
    expect(note.locator(".task-edit-target-list")).to_have_text(["hiring", "team"])
    # The name taken off: hers again, and nothing to ask.
    modal.locator(".task-field-assignee .tag-chip-x").click()
    expect(chips).to_have_text(["@alice"])
    expect(note).to_have_text("Private: only you see this task.")
    expect(note).not_to_have_class(warn)
    # Bob again, and the list: it is shared there and his.
    field.fill("bob")
    page.keyboard.press("Enter")
    note.locator(".task-edit-target-list", has_text="team").click()
    expect(lists).to_have_value("team")
    expect(chips).to_have_text(["@bob"])                    # her name isn't put over his
    expect(note).to_have_text("Everyone who uses the wiki will see and change this task. It can’t be made private again.")
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(page, "Ask Sam", "bob")
    given = task(page, "Ask Sam")
    assert given["private"] is False and given["list"] == "team"
    assert [e["event"] for e in given["history"]] == ["created", "shared", "assigned"]

    # Into a group's list, the note names the group, not everyone.
    modal = open_edit(page, "Ask Zed")
    lists.select_option("hiring")
    expect(note).to_have_text("The members of the group hiring will see and change this task. It can’t be made private again.")
    expect(chips).to_have_text(["@alice"])                  # shared by her: hers, a name now, to be taken off
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(1)
    page.keyboard.press("Escape")
    # And the move dialog's row says whom it shares it with.
    card(page, "Ask Zed").locator(".task-title").click()
    page.keyboard.press("m")
    rows = page.locator(".bulk-list-modal .bulk-list-row")
    expect(rows.filter(has_text="hiring")).to_have_text(re.compile(r"shares it with the group hiring, assigned to you$"))
    expect(rows.filter(has_text="team")).to_have_text(re.compile(r"shares it with everyone, assigned to you$"))


def test_a_private_task_dropped_on_someone_asks_for_a_shared_list(alice):
    """By person, a private task dropped on someone else's section goes to
    them with a shared list, as Assign… does it; on Unassigned it stays: a
    private task is never no one's."""
    new(alice, "Bob's", assignee="bob")
    new(alice, "My own", list=None)
    alice.goto("/tasks?view=open&group=person")
    expect(alice.locator(".board-day-label")).to_have_text(["Unassigned", "You", "@bob"])
    card(alice, "My own").drag_to(section(alice, "p:"))         # not taken: the section doesn't light up for it
    expect(section(alice, "p:").locator(".board-day-count")).to_have_text("0")
    assert task(alice, "My own")["private"] is True
    card(alice, "My own").drag_to(section(alice, "p:bob"))
    lists = alice.locator(".bulk-list-modal")
    expect(lists.locator(".modal-label")).to_have_text("Give 1 task to bob: choose a shared list")
    lists.locator(".bulk-list-row", has_text="team").click()
    wait_for_assignee(alice, "My own", "bob")
    assert task(alice, "My own")["list"] == "team" and task(alice, "My own")["private"] is False
    expect(section(alice, "p:bob").locator(".board-day-count")).to_have_text("2")


def test_closed_hides_the_lines_about_open_tasks(alice):
    """A list's "N unassigned" is of its open tasks: it isn't shown while
    Closed is, and is back with the tab."""
    new(alice, "No one's")
    done = new(alice, "Done", assignee="alice")
    alice.request.put(f"/api/tasks/{done['id']}", data={"completed": True})
    alice.goto("/tasks?list=team")
    line = alice.locator(".tasks-list-facts")
    expect(line).to_contain_text("1 unassigned")
    alice.locator("#task-closed-link").click()
    expect(card(alice, "Done")).to_be_visible()
    expect(line).to_be_hidden()
    alice.locator("#task-closed-link").click()
    expect(line).to_be_visible()


def test_the_tasks_link_has_one_number_and_a_dot(alice):
    """The Tasks link shows what is due and, when there is news, a dot: how
    many updates there are is in its tooltip, and My tasks lists them."""
    new(alice, "Due", assignee="alice", due_date=date.today().isoformat())
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    new(alice, "From bob", assignee="alice")
    alice.request.post("/login", form={"username": "alice", "password": "pw"})
    alice.goto("/page/index")
    link = alice.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(link.locator(".module-due")).to_have_text("1")
    expect(link.locator(".module-focus")).to_be_hidden()
    dot = link.locator(".module-new")
    expect(dot).to_be_visible()
    box = box_of(dot)
    assert box["width"] <= 10 and box["height"] <= 10          # a dot, no number
    expect(link).to_have_attribute("title", re.compile(r"1 update for you"))


def test_my_tasks_says_that_there_are_updates(alice):
    """Updates are listed in Updates; My tasks says that there are some, on
    each of its tabs, and leads there; a list's view has no such line."""
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    new(alice, "From bob", assignee="alice")
    alice.request.post("/login", form={"username": "alice", "password": "pw"})
    for view in ("today", "open&group=none"):
        alice.goto("/tasks?mine=1&view=" + view)
        expect(alice.locator(".tasks-updates-note")).to_have_text("1 update for you →")
    # Read where it is, the line goes without a reload.
    card(alice, "From bob").locator(".task-title").click()
    alice.keyboard.press("f")                        # starts today: hers now
    expect(alice.locator(".tasks-updates-note")).to_be_hidden()
    alice.goto("/tasks?list=team&view=open&group=none")
    expect(card(alice, "From bob")).to_be_visible()
    expect(alice.locator(".tasks-updates-note")).to_have_count(0)      # a list's view has none


def test_today_is_open_with_a_filter_and_groups_as_open_does(alice):
    """The Today tab shows the tasks to do now, drawn as the Open tab draws
    its own: as one list, or in sections by person or by list (not by day:
    they are all today's). Each tab keeps its own choice; a link names it."""
    today = date.today().isoformat()
    new(alice, "Mine today", assignee="alice", start_date=today)
    new(alice, "Bob's today", assignee="bob", start_date=today)
    new(alice, "No one's today", start_date=today)
    new(alice, "Bob's later", assignee="bob")
    alice.goto("/tasks?list=team&view=today")
    active = alice.locator("#tasks-tabs .active")
    expect(active).to_have_text(["Today"])
    cut = alice.locator(".tasks-cut")
    expect(cut.locator("button:visible")).to_have_text(["List", "By person"])
    expect(cut.locator("[aria-pressed='true']")).to_have_text(["List"])
    expect(alice.locator(".task-item")).to_have_count(3)
    cut.get_by_role("button", name="By person").click()
    expect(active).to_have_text(["Today"])
    heads = alice.locator(".board-day-divider .board-day-label")
    expect(heads).to_have_text(["Unassigned", "You", "@bob"])
    expect(alice.locator(".task-item")).to_have_count(3)
    expect(card(alice, "Bob's later")).to_have_count(0)
    # A new task made there starts today, as on Today as one list.
    alice.locator("#task-add-btn").click()
    modal = alice.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    expect(modal.locator("#task-edit-start")).to_have_value(today)
    alice.keyboard.press("Escape")
    # The Open tab keeps its own: the board by day; back on Today, by person still.
    alice.locator('#tasks-tabs [data-view="open"]').click()
    expect(cut.locator("[aria-pressed='true']")).to_have_text(["Plan"])
    expect(card(alice, "Bob's later")).to_be_visible()
    alice.locator('#tasks-tabs [data-view="focus"]').click()
    expect(cut.locator("[aria-pressed='true']")).to_have_text(["By person"])
    expect(heads).to_have_text(["Unassigned", "You", "@bob"])
    # A link says it too.
    alice.evaluate("sessionStorage.clear()")
    alice.goto("/tasks?list=team&view=today&group=person")
    expect(active).to_have_text(["Today"])
    expect(heads).to_have_text(["Unassigned", "You", "@bob"])
    alice.goto("/tasks?list=team&view=today&group=day")            # by day is no choice of Today's
    expect(cut.locator("[aria-pressed='true']")).to_have_text(["List"])


# ── A group's lists ──────────────────────────────────────────────────────────

def test_a_groups_list_is_made_and_its_tasks_go_to_its_members(page, wiki, monkeypatch):
    """New list… makes a group's list (For: a group she is in); the heading
    says it, Who sees it… changes it, and Assign…, the Assignee field and the
    title's @name offer the group's members only."""
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob", "carol", "dave")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin", "groups": "hiring"}, "carol": {"groups": "hiring"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    page.goto("/tasks")
    page.locator(".page-menu-btn").click()
    page.locator("#list-new-btn").click()
    dialog = page.locator("#list-new-dialog")
    dialog.locator("input[name=name]").fill("hiring")
    field = dialog.locator("#list-new-for")
    expect(field.locator("option")).to_have_text(["Everyone who uses this wiki", "@hiring: its members see it", "Only you"])
    field.select_option("group:hiring")
    dialog.locator("#list-new-submit").click()
    page.wait_for_url(re.compile(r"/tasks\?list=hiring$"))
    lock = page.locator("h1 .list-for")
    expect(lock).to_have_attribute("title", "The group hiring's list: only its members see it and its tasks (alice, carol)")

    # A task there: the title's @name and the Assignee field know its members.
    page.locator("#task-add-btn").click()
    modal = page.locator("#task-edit-modal")
    # The line under the title says who sees it: not everyone.
    expect(modal.locator("#task-edit-target")).to_have_text("Adds to the list hiring: only the group hiring sees it.")
    title = modal.locator("#task-edit-title")
    title.type("Call Zed @")
    expect(modal.locator(".tag-ac-item")).to_have_text(["@alice", "@carol"])
    title.fill("Call Zed @bob")                               # not in the group: text
    expect(modal.locator("#task-edit-quick")).to_be_hidden()
    title.fill("Call Zed @carol")
    expect(modal.locator(".task-field-assignee .tag-chip-label")).to_have_text(["@carol"])
    modal.get_by_role("button", name="Save").click()
    wait_for_assignee(page, "Call Zed", "carol")
    # Assign…: its members, and no one.
    card(page, "Call Zed").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Assign…").click()
    rows = page.locator(".bulk-assign-modal .bulk-list-row")
    expect(rows).to_have_text(["@alice", re.compile("@carol"), "No one"])
    rows.first.click()
    wait_for_assignee(page, "Call Zed", "alice")

    # Who sees it…: everyone.
    page.locator(".page-menu-btn").click()
    page.locator("#list-for-btn").click()
    who = page.locator("#list-for-dialog")
    expect(who.locator("#list-for-input")).to_have_value("group:hiring")
    who.locator("#list-for-input").select_option("")
    who.locator("#list-for-submit").click()
    page.wait_for_url(re.compile(r"/tasks\?list=hiring$"))
    expect(page.locator("h1 .list-for")).to_have_count(0)
    card(page, "Call Zed").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Assign…").click()
    expect(page.locator(".bulk-assign-modal .bulk-list-row")).to_have_text([re.compile("@alice"), "@bob", "@carol", "@dave", "No one"])


def test_many_people_are_found_by_typing(page, wiki, monkeypatch):
    """Past twelve people Assign… has a field: what is typed leaves the names
    that hold it, and Enter takes the first."""
    names = ["alice"] + [f"user{i:02d}" for i in range(14)] + ["zoe"]
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in names})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    new(page, "Hand me on")
    page.goto("/tasks?list=team&view=open")
    card(page, "Hand me on").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Assign…").click()
    dlg = page.locator(".bulk-assign-modal")
    find = dlg.locator("#bulk-assign-find")
    expect(find).to_be_focused()
    expect(dlg.locator(".bulk-list-row:visible")).to_have_count(17)
    find.fill("zo")
    expect(dlg.locator(".bulk-list-row:visible")).to_have_text(["@zoe", re.compile("No one")])
    find.press("Enter")
    wait_for_assignee(page, "Hand me on", "zoe")


def test_a_task_leaving_a_groups_list_says_who_will_see_it(page, wiki, monkeypatch):
    """Out of a group's list into one for everyone: the move dialog's row
    and the edit dialog's line say it, and a drop asks, as sharing a private
    task does."""
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob", "carol")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin", "groups": "hiring"}, "carol": {"groups": "hiring"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    assert page.request.post("/tasks/new-list", form={"name": "hiring", "for": "group:hiring"}).ok
    new(page, "Offer", list="hiring")
    new(page, "Other", list="hiring")
    new(page, "Chairs")                                       # in team: By list has its section

    # The move dialog's row says what the move does.
    page.goto("/tasks?list=hiring&view=open")
    card(page, "Offer").locator(".task-title").click()
    page.keyboard.press("m")
    row = page.locator(".bulk-list-modal .bulk-list-row[data-list=team]")
    expect(row.locator(".bulk-tag-count")).to_have_text("everyone will see it")
    page.keyboard.press("Escape")
    # The edit dialog's line too.
    modal = open_edit(page, "Offer")
    note = modal.locator("#task-edit-list-note")
    expect(note).to_be_hidden()
    modal.locator("#task-edit-list").select_option("team")
    expect(note).to_have_text("Everyone who uses the wiki will see and change this task. Now only the members of the group hiring do.")
    modal.locator("#task-edit-list").select_option("hiring")
    expect(note).to_be_hidden()
    page.keyboard.press("Escape")

    # A drop asks.
    page.goto("/tasks?view=open&group=list")
    card(page, "Other").drag_to(section(page, "l:team"))
    ask = page.locator("#task-share-modal")
    expect(ask).to_contain_text("everyone who uses the wiki will see and change it. Now only the members of the group of the list “hiring” do.")
    ask.locator("#task-share-cancel").click()
    assert task(page, "Other")["list"] == "hiring"
    card(page, "Other").drag_to(section(page, "l:team"))
    ask.locator("#task-share-confirm").click()
    wait_until(page, lambda: task(page, "Other")["list"] == "team")


def test_who_no_longer_sees_a_list_no_longer_has_its_tasks(page, wiki, monkeypatch):
    """A list moved to a group: the task of someone not in it keeps the name,
    struck through, and counts as no one's (the filter's @none, By person's
    Unassigned), so it is found and handed on."""
    monkeypatch.setattr(settings, "USERS", {name: ("pw", "") for name in ("alice", "bob", "carol")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin", "groups": "hiring"}, "carol": {"groups": "hiring"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    new(page, "Bob's", assignee="bob")
    new(page, "Carol's", assignee="carol")
    assert page.request.post("/tasks/list-for", form={"list": "team", "for": "group:hiring", "confirm": "1"}).ok
    page.goto("/tasks?list=team&view=open&group=none")
    name = card(page, "Bob's").locator(".task-assignee")
    expect(name).to_have_class(re.compile(r"\btask-assignee-former\b"))
    expect(name).to_have_attribute("title", "Assigned to bob, who doesn’t see this list anymore: it counts as no one’s")
    expect(card(page, "Carol's").locator(".task-assignee")).not_to_have_class(re.compile(r"\btask-assignee-former\b"))
    expect(page.locator(".tasks-unassigned")).to_contain_text("1 unassigned")
    search = page.locator("#task-filter-search")
    search.fill("@none")
    expect(page.locator(".task-item .task-title")).to_have_text(["Bob's"])
    search.fill("@bob")
    expect(page.locator(".task-item .task-title")).to_have_text(["Bob's"])
    search.fill("")
    page.locator(".tasks-cut [data-group=person]").click()
    expect(section(page, "p:").locator("~ .task-item .task-title").first).to_have_text("Bob's")
    # The dialog says why the name is struck, and it is handed on to a member.
    page.locator(".tasks-cut [data-group=none]").click()
    modal = open_edit(page, "Bob's")
    chip = modal.locator(".task-field-assignee .tag-chip")
    expect(chip).to_have_class(re.compile(r"\btag-chip-former\b"))
    expect(chip).to_have_attribute("title", "bob isn’t in the group hiring, whose list it is: the task counts as no one’s")
    page.keyboard.press("Escape")
    card(page, "Bob's").locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Assign…").click()
    page.locator(".bulk-assign-modal .bulk-list-row", has_text="@carol").click()
    wait_for_assignee(page, "Bob's", "carol")
    page.reload()                                             # the list's line is the page's
    expect(page.locator(".tasks-unassigned")).to_have_count(0)


def test_follow_a_task_and_its_news_is_under_updates(alice, new_page):
    """Follow in a shared task's menu (docs/spec/tasks.md, News on your
    tasks): the task is not yours to do, and what happens on it is in
    Updates; the dialog says who follows
    and unfollows; is:following lists what you follow."""
    t = new(alice, "Bob's work", assignee="bob")
    new(alice, "Nobody follows", assignee="bob")
    alice.goto("/tasks?list=team")
    card(alice, "Bob's work").locator(".task-title").click(button="right")
    alice.get_by_role("menuitem", name="Follow", exact=True).click()
    wait_until(alice, lambda: task(alice, "Bob's work").get("followers") == ["alice"])
    # The menu offers the other way now, and the filter's word finds it.
    card(alice, "Bob's work").locator(".task-title").click(button="right")
    expect(alice.get_by_role("menuitem", name="Unfollow", exact=True)).to_be_visible()
    alice.keyboard.press("Escape")
    alice.locator("#task-filter-search").fill("is:following")
    expect(alice.locator(".task-item .task-title")).to_have_text(["Bob's work"])
    # Not hers to do: My tasks has nothing of it, until bob comments.
    alice.goto("/tasks?mine=1")
    expect(card(alice, "Bob's work")).to_have_count(0)
    bob = new_page()
    bob.request.post("/login", form={"username": "bob", "password": "pw"})
    assert bob.request.post(f"/api/tasks/{t['id']}/comments", data={"text": "Half done"}).ok
    alice.goto("/tasks?mine=1")
    expect(alice.locator(".tasks-app[data-loaded]")).to_be_visible()
    expect(card(alice, "Bob's work")).to_have_count(0)                  # still not hers to do
    alice.goto("/tasks?updates=1")
    expect(alice.locator(".task-item.task-update .task-title")).to_have_text(["Bob's work"])
    expect(card(alice, "Bob's work").locator(".task-news-what")).to_have_text("bob commented")
    expect(alice.locator('.module-link[data-module="tasks"] .module-new').first).to_have_text("1")
    # Its dialog says who follows, and unfollows: saved at once.
    modal = open_edit(alice, "Bob's work")
    expect(modal.locator("#task-edit-followers")).to_have_text("👁 Followed by you")
    modal.locator("#task-edit-follow-btn").click()
    expect(modal.locator("#task-edit-follow-btn")).to_have_text("Follow")
    expect(modal.locator("#task-edit-followers")).to_have_text("")
    wait_until(alice, lambda: "followers" not in task(alice, "Bob's work"))
    # A private task has no such line, and no Follow in its menu.
    alice.keyboard.press("Escape")
    new(alice, "My own", list=None)
    alice.goto("/tasks?list=inbox")
    card(alice, "My own").locator(".task-title").click(button="right")
    expect(alice.get_by_role("menuitem", name="Edit…")).to_be_visible()
    expect(alice.get_by_role("menuitem", name="Follow", exact=True)).to_have_count(0)
    alice.get_by_role("menuitem", name="Edit…").click()
    expect(alice.locator("#task-edit-follow")).to_be_hidden()
