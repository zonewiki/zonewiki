"""The Inbox and My tasks with users, in the browser (docs/spec/tasks.md,
Lists): filing an Inbox task into a shared list (the dialog, the move
dialog), a shared list's unassigned tasks, Leave out of My tasks in List
settings, and where Delete… sends a list's tasks.

The Inbox's pill and view, c t and the line under a new task's title are in
test_tasks.py (test_the_inbox_has_a_pill_and_a_view_of_its_own,
test_c_t_makes_a_private_task_in_the_users_inbox,
test_a_name_on_a_new_inbox_task_moves_it_to_a_shared_list); what My tasks
holds with users in test_task_assignee.py."""

import re
from datetime import date

import pytest
from playwright.sync_api import expect

from conftest import make_list, more_menu, wait_until
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    """The browser, signed in as alice, in a wiki with bob and a shared list
    team (not pinned)."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", ""), "bob": ("pw", "")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    make_list(page, "team")
    return page


def new(page, title, **fields):
    resp = page.request.post("/api/tasks", data={"title": title, **fields})
    assert resp.ok, resp.text()
    return resp.json()


def task(page, title):
    return next(t for t in page.request.get("/api/tasks").json() if t["title"] == title)


def wait_for(page, title, **fields):
    """Wait until the task has these fields (None: not set)."""
    def there():
        found = [t for t in page.request.get("/api/tasks").json() if t["title"] == title]
        return bool(found) and all(found[0].get(k) == v for k, v in fields.items())
    wait_until(page, there)


def card(page, title):
    return page.locator(".task-item").filter(has=page.get_by_text(title, exact=True))


def open_edit(page, title):
    card(page, title).locator(".task-title").click(button="right")
    page.get_by_role("menuitem", name="Edit…").click()
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    return modal


def open_list_settings(page):
    more_menu(page)
    page.get_by_role("menuitem", name="List settings…").click()
    dialog = page.locator("#task-settings-modal")
    expect(dialog).to_be_visible()
    return dialog


SHARES = "Everyone who uses the wiki will see and change this task. It can’t be made private again."


def test_filing_an_inbox_task_through_the_dialog_shares_it_assigned_to_you(alice):
    """The List field takes an Inbox task into a shared list: the dialog
    says that this shares it, and assigns it to its user, so what was hers
    stays in her My tasks; she can take her name off."""
    for title in ("Idea", "Hand over", "Second thoughts"):
        new(alice, title)
    alice.goto("/tasks?list=inbox&view=open&group=none")
    modal = open_edit(alice, "Idea")
    lists, note = modal.locator("#task-edit-list"), modal.locator("#task-edit-list-note")
    chips = modal.locator(".task-field-assignee .tag-chip-label")
    expect(lists).to_have_value("")
    expect(note).to_have_text("Private: only you see this task.")
    expect(chips).to_have_text(["@alice"])                           # hers: the field says so, with no ×
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(0)
    lists.select_option("team")
    expect(note).to_have_text(SHARES)
    expect(chips).to_have_text(["@alice"])
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(1)     # a name the task keeps now: it can go
    modal.get_by_role("button", name="Save").click()
    wait_for(alice, "Idea", private=False, list="team", assignee="alice")
    # It left the Inbox, and the notice says where it went; My tasks still has it.
    expect(card(alice, "Idea")).to_have_count(0)
    expect(alice.get_by_text("Saved to list “team”.")).to_be_visible()
    assert {"shared", "assigned"} <= {e["event"] for e in task(alice, "Idea")["history"]}

    # Her name taken off: the task is the list's, no one's.
    modal = open_edit(alice, "Hand over")
    lists.select_option("team")
    expect(chips).to_have_text(["@alice"])
    modal.locator(".task-field-assignee .tag-chip-x").click()
    expect(chips).to_have_count(0)
    lists.select_option("")                    # back and forth: the name isn't put back a second time
    expect(chips).to_have_text(["@alice"])     # (in her Inbox it is hers, whatever she took off)
    lists.select_option("team")
    expect(chips).to_have_count(0)
    modal.get_by_role("button", name="Save").click()
    wait_for(alice, "Hand over", private=False, list="team", assignee=None)

    # Back in the Inbox before Save: nothing is shared, no one assigned.
    modal = open_edit(alice, "Second thoughts")
    lists.select_option("team")
    lists.select_option("")
    expect(note).to_have_text("Private: only you see this task.")
    expect(chips).to_have_text(["@alice"])
    expect(modal.locator(".task-field-assignee .tag-chip-x")).to_have_count(0)
    modal.locator("#task-edit-title").fill("Second thoughts, kept")
    modal.get_by_role("button", name="Save").click()
    wait_for(alice, "Second thoughts, kept", private=True, list=None, assignee=None)

    alice.goto("/tasks?mine=1&view=open&group=none")
    titles = alice.locator(".task-item .task-title")
    expect(titles).to_have_count(2)
    assert sorted(titles.all_text_contents()) == ["Idea", "Second thoughts, kept"]
    expect(card(alice, "Idea").locator(".task-assignee")).to_have_count(0)   # yours, as all of My tasks: the card doesn't say it
    assert task(alice, "Idea")["assignee"] == "alice"
    expect(card(alice, "Idea").locator(".task-private")).to_have_count(0)
    # bob sees what was filed, not what stayed.
    alice.request.post("/login", form={"username": "bob", "password": "pw"})
    assert sorted(t["title"] for t in alice.request.get("/api/tasks").json()) == ["Hand over", "Idea"]


def test_the_move_dialog_files_inbox_tasks_assigned_to_you(alice):
    """Move to list… on Inbox tasks: a shared list's row says that it shares
    them; moved, they are assigned to their user, and no undo takes a share
    back. The Inbox's row is where they are."""
    make_list(alice, "own", private=True)
    for title in ("Two", "One"):
        new(alice, title)
    new(alice, "Bob's", list="team", assignee="bob")
    alice.goto("/tasks?list=inbox&view=open&group=none")
    card(alice, "One").locator(".task-title").click()
    card(alice, "Two").locator(".task-title").click(modifiers=["Shift"])
    alice.keyboard.press("m")
    dlg = alice.locator(".bulk-list-modal")
    rows = dlg.locator(".bulk-list-row")
    expect(rows).to_have_text(["✓🔒 Inbox", "🔒 own", re.compile(r"team\s*shares it with everyone, assigned to you"), "+New list…"])
    expect(dlg.locator(".bulk-list-row[data-list='']")).to_have_attribute("aria-current", "true")
    dlg.locator(".bulk-list-row[data-list=team]").click()
    toast = alice.get_by_text("2 tasks moved to list “team”")
    expect(toast).to_be_visible()
    for title in ("One", "Two"):
        wait_for(alice, title, private=False, list="team", assignee="alice")
        expect(card(alice, title)).to_have_count(0)                 # out of the Inbox
    expect(alice.get_by_role("button", name="Undo")).to_have_count(0)
    assert task(alice, "Bob's")["assignee"] == "bob"
    # In her My tasks still, by her name.
    alice.goto("/tasks?mine=1&view=open&group=none")
    titles = alice.locator(".task-item .task-title")
    expect(titles).to_have_count(2)
    assert sorted(titles.all_text_contents()) == ["One", "Two"]
    # Into her own list a task stays private, with no name on it, and comes back with Undo.
    new(alice, "Three")
    alice.goto("/tasks?list=inbox&view=open&group=none")
    card(alice, "Three").locator(".task-title").click(button="right")
    alice.locator(".task-menu").get_by_role("menuitem", name="Move to list…").click()
    dlg.locator(".bulk-list-row[data-list=own]").click()
    wait_for(alice, "Three", private=True, list="own", assignee=None)
    alice.get_by_role("button", name="Undo").click()
    wait_for(alice, "Three", private=True, list=None)
    expect(card(alice, "Three")).to_be_visible()


def test_a_shared_list_says_how_many_of_its_tasks_no_one_has(alice):
    """Under a shared list's heading: "N unassigned", a link to the list's
    Open tab filtered by @none, from Today too (which shows only some of
    them). Not on a private list, whose tasks have no assignee."""
    make_list(alice, "own", private=True)
    new(alice, "Free one", list="team")
    new(alice, "Free two", list="team")
    new(alice, "Bob's", list="team", assignee="bob")
    new(alice, "Hers", list="team", assignee="alice")
    new(alice, "Own one", list="own")
    alice.goto("/tasks?list=team&view=open&group=none")
    facts = alice.locator(".tasks-list-facts")
    expect(facts).to_have_text("2 unassigned.")
    link = facts.locator("a.tasks-unassigned")
    expect(link).to_have_attribute("href", "/tasks?list=team&view=open&assignee=none")
    expect(alice.locator(".task-item")).to_have_count(4)
    alice.locator("#tasks-tabs [data-view='focus']").click()
    expect(alice.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Today"])
    link.click()
    alice.wait_for_url(re.compile(r"/tasks\?list=team&view=open&assignee=none$"))
    expect(alice.locator("h1")).to_have_text("List: team")
    expect(alice.locator("#tasks-tabs [aria-selected='true']")).to_have_text(["Open"])
    titles = alice.locator(".task-item .task-title")
    expect(titles).to_have_count(2)
    assert sorted(titles.all_text_contents()) == ["Free one", "Free two"]
    expect(alice.locator(".tasks-toolbar .tag-chip-label")).to_have_text(["team", "@none"])
    # Taken (a, then her name), a task is no longer counted.
    card(alice, "Free one").locator(".task-title").click()
    alice.keyboard.press("a")
    alice.locator(".bulk-assign-modal .bulk-list-row", has_text="@alice").click()
    wait_for(alice, "Free one", assignee="alice")
    alice.goto("/tasks?list=team")
    expect(facts).to_have_text("1 unassigned.")
    # None left: no line. Nor on a list of her own.
    alice.request.put(f"/api/tasks/{task(alice, 'Free two')['id']}", data={"assignee": "bob"})
    alice.reload()
    expect(facts).to_have_count(0)
    alice.goto("/tasks?list=own")
    expect(alice.locator(".task-item, .tasks-empty").first).to_be_attached()
    expect(facts).to_have_count(0)


def test_leave_out_of_my_tasks_is_a_setting_of_your_own_lists(alice, wiki):
    """List settings… of a list of her own: Leave out of My tasks. Its tasks
    then show in the list alone: not in My tasks, not on the Tasks link. A
    shared list has no such setting where tasks are assigned: what is in My
    tasks of it is what is hers."""
    make_list(alice, "someday", private=True, pin=True)
    today = date.today().isoformat()
    new(alice, "Learn the oboe", list="someday", due_date=today)
    new(alice, "Call Sam", due_date=today)
    link = alice.locator(".breadcrumb .module-link[data-module=tasks]")
    alice.goto("/tasks?list=someday&view=open&group=none")
    expect(link.locator(".module-due")).to_have_text("2")
    expect(alice.locator(".tasks-list-facts")).to_have_count(0)
    dialog = open_list_settings(alice)
    box = dialog.locator("#task-settings-home")
    expect(box).to_be_hidden()
    word = dialog.locator(".task-add-field[data-more=home]")
    expect(word).to_have_text("Leave out of My tasks")
    word.click()
    expect(box).to_be_visible()
    expect(box).to_be_checked()                     # asked for: ticked
    expect(dialog.locator("label", has=alice.locator("#task-settings-home"))).to_have_text("Leave its tasks out of My tasks")
    expect(word).to_be_hidden()
    with alice.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    registry = wiki.root / "private/alice/_tasks/lists.json"
    assert '"home": "off"' in registry.read_text()
    # The list says so, keeps its pill and its tasks; My tasks and the Tasks link are without them.
    facts = alice.locator(".tasks-list-facts")
    expect(facts).to_have_text("Left out of My tasks.")
    expect(facts.locator("a")).to_have_attribute("href", "/tasks?mine=1")
    expect(alice.locator('.tasks-place[data-place="list:someday"] .tasks-place-pin')).to_have_count(1)
    expect(card(alice, "Learn the oboe")).to_be_visible()
    expect(link.locator(".module-due")).to_have_text("1")
    expect(alice.locator('.tasks-place[data-place="mine"] .module-due')).to_have_text("1")
    facts.locator("a").click()
    alice.wait_for_url(re.compile(r"/tasks\?mine=1$"))
    expect(alice.locator(".tasks-mine-line")).to_have_text(
        "Your own tasks and what is assigned to you, but the lists left out of it (#someday).")
    alice.locator("#tasks-tabs [data-view='open']").click()
    expect(alice.locator(".task-item .task-title")).to_have_text(["Call Sam"])
    # A task made in the list while My tasks is open elsewhere doesn't show there either:
    # the page knows the lists left out.
    assert alice.locator(".tasks-app").get_attribute("data-init-out") == "someday"
    # Unticked, they are back.
    alice.goto("/tasks?list=someday")
    dialog = open_list_settings(alice)
    expect(box).to_be_visible()                     # set: shown
    expect(box).to_be_checked()
    box.uncheck()
    with alice.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '"home"' not in registry.read_text()
    expect(alice.locator(".tasks-list-facts")).to_have_count(0)
    expect(link.locator(".module-due")).to_have_text("2")
    alice.goto("/tasks?mine=1")
    expect(alice.locator(".tasks-mine-line")).to_have_text("Your own tasks and what is assigned to you.")
    # A shared list: not hers to leave out. My tasks and the Inbox have no such setting either.
    alice.goto("/tasks?list=team")
    dialog = open_list_settings(alice)
    expect(dialog.locator(".task-add-field:visible")).to_have_text(["WIP limit", "Tab names", "Tags of a new task", "Keep tasks", "Hide when sharing"])
    expect(box).to_be_hidden()
    alice.keyboard.press("Escape")
    alice.goto("/tasks?mine=1")
    dialog = open_list_settings(alice)
    expect(dialog.locator(".task-add-field[data-more=home]")).to_be_hidden()


def test_leave_out_of_my_tasks_without_users_is_any_lists(page, wiki):
    """Without users every list is yours: any can be left out of My tasks."""
    make_list(page, "catalogue")
    today = date.today().isoformat()
    page.request.post("/api/tasks", data={"title": "Part 17", "list": "catalogue", "due_date": today})
    page.request.post("/api/tasks", data={"title": "Call Sam", "due_date": today})
    page.goto("/tasks?list=catalogue")
    link = page.locator(".breadcrumb .module-link[data-module=tasks]")
    expect(link.locator(".module-due")).to_have_text("2")
    dialog = open_list_settings(page)
    dialog.locator(".task-add-field[data-more=home]").click()
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save").click()
    assert '"home": "off"' in (wiki.root / "_tasks/lists.json").read_text()
    expect(page.locator(".tasks-list-facts")).to_have_text("Left out of My tasks.")
    expect(link.locator(".module-due")).to_have_text("1")
    page.goto("/tasks?mine=1&view=open&group=none")
    expect(page.locator(".tasks-mine-line")).to_have_text("Every open task, but the lists left out of it (#catalogue).")
    expect(page.locator(".task-item .task-title")).to_have_text(["Call Sam"])
    # All tasks has every task, as ever.
    page.goto("/tasks?view=open&group=none")
    expect(page.locator(".task-item")).to_have_count(2)


def delete_dialog(page):
    page.locator(".page-header .page-menu-btn").click()
    page.locator("#list-delete-btn").click()
    dialog = page.locator("#list-delete-dialog")
    expect(dialog).to_be_visible()
    return dialog


def test_delete_sends_a_private_lists_tasks_to_your_inbox(alice):
    """Delete… on a private list: its tasks go to her Inbox unless she
    names a list; her own lists come before the shared ones."""
    make_list(alice, "own", private=True)
    make_list(alice, "notes", private=True)
    new(alice, "Own one", list="own")
    alice.goto("/tasks?list=own")
    dialog = delete_dialog(alice)
    expect(dialog.locator("#list-delete-to option")).to_have_text(["Your Inbox", "#notes (private)", "#team (shared)"])
    expect(dialog.locator("#list-delete-to")).to_have_value("")
    expect(dialog.locator("label", has=alice.locator("#list-delete-share"))).to_contain_text(
        "Share its tasks if they go to a shared list")
    dialog.locator("#list-delete-submit").click()
    alice.wait_for_url(re.compile(r"/tasks$"))
    wait_for(alice, "Own one", private=True, list=None)
    alice.goto("/tasks?list=inbox&view=open&group=none")
    expect(card(alice, "Own one")).to_be_visible()
    expect(alice.locator('.tasks-place[data-place="list:own"], .lists-more-item[data-place="list:own"]')).to_have_count(0)


def test_delete_sends_a_shared_lists_tasks_to_general(alice):
    """Delete… on a shared list, with users: a shared task is always in a
    list, so its tasks go to another shared list: #general (made if it
    isn't there) unless one is named. Her private lists and her Inbox are
    not offered."""
    make_list(alice, "board")
    make_list(alice, "own", private=True)
    new(alice, "Agenda", list="team")
    new(alice, "Minutes", list="board")
    alice.goto("/tasks?list=team")
    dialog = delete_dialog(alice)
    expect(dialog.locator("#list-delete-to option")).to_have_text(["#general", "#board"])
    expect(dialog.locator("#list-delete-share")).to_have_count(0)
    dialog.locator("#list-delete-submit").click()
    alice.wait_for_url(re.compile(r"/tasks$"))
    wait_for(alice, "Agenda", private=False, list="general")
    # general is made for them: a list of the switcher (under More: nobody pinned it).
    expect(alice.locator('.lists-more-item[data-place="list:general"]')).to_have_count(1)
    expect(alice.locator('[data-place="list:team"]')).to_have_count(0)
    alice.goto("/tasks?list=general&view=open&group=none")
    expect(card(alice, "Agenda")).to_be_visible()
    # With a general there, it is offered once; a named list takes them instead.
    make_list(alice, "team")
    alice.goto("/tasks?list=board")
    dialog = delete_dialog(alice)
    expect(dialog.locator("#list-delete-to option")).to_have_text(["#general", "#team"])
    dialog.locator("#list-delete-to").select_option("team")
    dialog.locator("#list-delete-submit").click()
    alice.wait_for_url(re.compile(r"/tasks\?list=team$"))
    wait_for(alice, "Minutes", private=False, list="team")
    # general itself goes to another shared list; as the only one, it has none to
    # name, which the dialog says (and the server, when it has tasks).
    alice.goto("/tasks?list=general")
    dialog = delete_dialog(alice)
    expect(dialog.locator("#list-delete-to option")).to_have_text(["#team"])
    alice.keyboard.press("Escape")
    alice.goto("/tasks?list=team")
    dialog = delete_dialog(alice)
    dialog.locator("#list-delete-to").select_option(label="#general")
    dialog.locator("#list-delete-submit").click()
    alice.wait_for_url(re.compile(r"/tasks$"))
    wait_for(alice, "Minutes", list="general")
    alice.goto("/tasks?list=general")
    dialog = delete_dialog(alice)
    expect(dialog.locator("#list-delete-to option")).to_have_text(["No other shared list"])
    dialog.locator("#list-delete-submit").click()
    expect(dialog).to_be_visible()
    expect(dialog.locator(".rename-error")).to_contain_text("With users a shared task is always in a list")
    assert task(alice, "Agenda")["list"] == "general"


def test_ranking_in_my_tasks_leaves_the_lists_order_alone(alice):
    """My tasks has the user's own manual order: a task ranked there is
    ranked for them, in that view; the list's order, which is everyone's,
    stays as it was."""
    page = alice
    for title in ("One", "Two"):
        new(page, title, list="team", assignee="alice")
    titles = page.locator(".tasks-list > .task-item .task-title")
    # On Today, where My tasks opens, the menu is there and says so too.
    page.goto("/tasks?mine=1&view=focus")
    expect(page.locator("select.tasks-sort")).to_be_visible()
    expect(page.locator("select.tasks-sort option").first).to_have_text("My order")
    page.goto("/tasks?mine=1&view=open&group=none")
    expect(titles).to_have_text(["Two", "One"])           # the list's order until ranked here
    # The Order menu says whose order this is.
    order = page.locator("select.tasks-sort")
    expect(order.locator("option").first).to_have_text("My order")
    expect(order).to_have_attribute("title", "Order (o). My order is yours alone: ranking here moves nothing in a list")
    # And the way back from a sort names it.
    order.select_option("due")
    back = page.locator(".tasks-sort-reset")
    expect(back).to_have_attribute("aria-label", "Back to My order")
    back.click()
    expect(order).to_have_value("manual")
    expect(titles).to_have_text(["Two", "One"])
    one, two = task(page, "One")["id"], task(page, "Two")["id"]
    card(page, "One").locator(".task-title").click()
    with page.expect_request(lambda r: r.url.endswith("/api/tasks/reorder")) as sent:
        page.keyboard.press("Control+Shift+ArrowUp")
    assert sent.value.post_data_json == {"mine": True, "moves": [{"id": one, "after": None, "before": two}]}
    wait_until(page, lambda: page.request.get("/api/tasks?with=order").json()["order"] == [one, two])
    # No task was written: the list's order is as it was.
    assert {t["title"]: t["sort_order"] for t in page.request.get("/api/tasks").json()} == {"One": 0, "Two": -1}
    page.goto("/tasks?list=team&view=open&group=none")
    expect(titles).to_have_text(["Two", "One"])
    expect(order.locator("option").first).to_have_text("Manual")
    expect(order).to_have_attribute("title", "Order (o)")
    # Back in My tasks, One is where it was put.
    page.goto("/tasks?mine=1&view=open&group=none")
    expect(titles).to_have_text(["One", "Two"])
    # A change to the task keeps its place (the answer's sort_order is the list's).
    page.request.put(f"/api/tasks/{one}", data={"due_date": "2031-01-01"})
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(card(page, "One").locator(".task-due, [class*=due]").first).to_be_visible()
    expect(titles).to_have_text(["One", "Two"])
    # bob's My tasks has his own order: nothing of alice's.
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    assert page.request.get("/api/tasks?with=order").json()["order"] == []


def test_a_file_attached_in_the_dialog_is_the_tasks(alice, wiki):
    """A file attached to a new task is uploaded for an id the dialog makes
    up, and the task is created with that id: its files are kept by the task
    (_tasks/files/<id>/), so they are seen by who sees it. An existing task's
    go by its id."""
    alice.goto("/tasks?list=team")
    alice.keyboard.press("c")
    alice.keyboard.press("t")
    modal = alice.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    modal.locator("#task-edit-title").fill("With a file")
    modal.locator("#task-edit-attach").set_input_files({"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"})
    notes = modal.locator("#task-edit-desc")
    expect(notes).to_have_value(re.compile(r"^\[notes\.txt\]\(/_tasks/files/[0-9a-f-]{36}/_assets/notes\.txt\)$"))
    id = re.search(r"files/([0-9a-f-]{36})/", notes.input_value()).group(1)
    modal.get_by_role("button", name="Save").click()
    wait_for(alice, "With a file", id=id)
    assert (wiki.root / "_tasks/files" / id / "_assets/notes.txt").read_bytes() == b"hello"
    # An edited task's files go by its id.
    alice.goto("/tasks?view=open&group=none")
    modal = open_edit(alice, "With a file")
    modal.locator("#task-edit-attach").set_input_files({"name": "more.txt", "mimeType": "text/plain", "buffer": b"more"})
    expect(modal.locator("#task-edit-desc")).to_have_value(re.compile(rf"/_tasks/files/{id}/_assets/more\.txt"))
