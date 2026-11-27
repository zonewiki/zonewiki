"""A group's pages in the browser (docs/spec/users.md, Group pages): Share…
in the page menu."""

import re

import pytest
from playwright.sync_api import expect

from conftest import box_of, wait_ready
from tests import settings


@pytest.fixture()
def alice(page, wiki, monkeypatch):
    """The browser, signed in as alice, who is in dev with carol; bob is in no group."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2"), "carol": ("pw", "t3")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}, "carol": {"groups": "dev"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    return page


def test_share_a_private_page_with_a_group(alice, wiki):
    wiki.write("private/alice/ideas", "# Ideas\n\nOne.\n")
    wiki.write("plans", "# Plans\n\nSee [[private/alice/ideas]].\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    alice.goto("/page/private/alice/ideas")
    alice.locator(".page-menu-btn").first.click()
    expect(alice.get_by_role("menuitem", name="Make shared…")).to_have_count(0)
    alice.get_by_role("menuitem", name="Share…").click()
    dialog = alice.locator("#page-privacy-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".share-choice")).to_have_count(2)
    expect(dialog.locator(".share-choice").first).to_contain_text("@dev: alice, carol")
    expect(dialog.locator(".share-choice").last).to_contain_text("Everyone who uses this wiki")
    expect(dialog.get_by_role("radio").first).to_be_checked()      # the smaller step first
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()
    assert (wiki.root / "private/alice/ideas.md").exists()
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Share…").click()
    dialog.get_by_role("button", name="Share").click()
    alice.wait_for_url("**/page/groups/dev/ideas")
    # plans is everyone's, the page now @dev's: the link keeps its words.
    assert "[[groups/dev/ideas|private/alice/ideas]]" in wiki.read("plans")
    # From the group's folder it can still go on to everyone.
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Share…").click()
    expect(dialog.locator(".share-choice")).to_have_count(1)
    expect(dialog).to_contain_text("the folder of @dev")
    dialog.get_by_role("button", name="Share").click()
    alice.wait_for_url("**/page/ideas")
    alice.locator(".page-menu-btn").first.click()
    expect(alice.get_by_role("menuitem", name="Share…")).to_have_count(0)


def test_rename_to_other_readers_asks_first(alice, wiki):
    """Rename… into another place changes who sees the page: the dialog comes
    back saying who will, and the second click is the yes."""
    wiki.write("groups/dev/roadmap", "# Roadmap\n\nNext.\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    alice.goto("/page/groups/dev/roadmap")
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Rename…").click()
    dialog = alice.locator("#page-rename-dialog")
    dialog.locator("#page-rename-input").fill("roadmap")
    dialog.get_by_role("button", name="Rename").click()
    alice.wait_for_url("**/page/groups/dev/roadmap?**")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".rename-ask")).to_contain_text("will be seen and changed by everyone who uses this wiki")
    expect(dialog.locator(".rename-ask")).to_contain_text("Now @dev (alice, carol) sees it")
    assert (wiki.root / "groups/dev/roadmap.md").exists()
    dialog.get_by_role("button", name="Yes, move it").click()
    alice.wait_for_url("**/page/roadmap")
    assert (wiki.root / "roadmap.md").exists()
    # Back is to fewer people: hers to take back, as she shared it and nobody changed it since; asked first.
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Rename…").click()
    dialog.locator("#page-rename-input").fill("groups/dev/roadmap")
    dialog.get_by_role("button", name="Rename").click()
    expect(dialog).to_be_visible()
    expect(dialog.locator(".rename-ask")).to_contain_text("bob will no longer see “roadmap”")
    dialog.get_by_role("button", name="Yes, move it").click()
    alice.wait_for_url("**/page/groups/dev/roadmap")


# ── [[ completion ────────────────────────────────────────────────────────────

def test_completion_offers_what_every_reader_of_the_page_sees(alice, wiki):
    """On a group's page: a new page in the group's folder, the shared pages
    and the group's; not her private ones, nor another group's. On a shared
    page nothing of a group's."""
    wiki.write("secretplans", "# Shared\n")
    wiki.write("groups/dev/secretroadmap", "# Roadmap\n")
    wiki.write("groups/dev/standup", "# Standup\n\n")
    wiki.write("private/alice/secretdiary", "# Diary\n\n")
    wiki.write("notes", "# Notes\n\n")
    items = alice.locator(".wikilink-ac-item")
    alice.goto("/edit/groups/dev/standup")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secret")
    expect(items).to_have_text([re.compile("^New page for @dev: secret"), "👥 groups/dev/secretroadmap", "secretplans"])
    alice.keyboard.press("Enter")
    expect(alice.locator("#editor-textarea")).to_have_value(re.compile(r"\[\[groups/dev/secret\|secret\]\]"))
    alice.goto("/edit/notes")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secret")
    expect(items).to_have_text(["secretplans"])
    alice.goto("/edit/private/alice/secretdiary")       # hers alone: all she sees
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secretr")
    expect(items).to_have_text([re.compile("^New private page: secretr"), "👥 groups/dev/secretroadmap"])


def test_a_new_shared_page_from_a_groups_page_says_so(alice, wiki):
    """A link on a group's page to a page there isn't opens the editor of a
    shared page, which says so and makes it the group's with one click: the
    link is pointed at the page in the group's folder."""
    wiki.write("groups/dev/standup", "# Standup\n\nSee [[Garden]] and [[Orchard]].\n")
    alice.goto("/page/groups/dev/standup")
    link = alice.locator("a.wikilink-shared-new").first
    expect(link).to_have_attribute("title", "Creates a shared page")
    link.click()
    alice.wait_for_url("**/edit/garden?from=group:dev&on=groups%2Fdev%2Fstandup")
    banner = alice.locator("#shared-new-banner")
    expect(banner).to_contain_text("This new page is shared")
    banner.get_by_role("button", name="Make it a page of @dev").click()
    alice.wait_for_url("**/edit/groups/dev/garden")
    expect(alice.locator("#shared-new-banner")).to_have_count(0)
    assert wiki.read("groups/dev/standup") == "# Standup\n\nSee [[groups/dev/garden|Garden]] and [[Orchard]].\n"
    # Keep shared: the note goes, the page is everyone's.
    alice.goto("/page/groups/dev/standup")
    alice.locator("a.wikilink-shared-new").click()
    alice.wait_for_url("**/edit/orchard?**")
    banner = alice.locator("#shared-new-banner")
    banner.get_by_role("button", name="Keep shared").click()
    expect(banner).to_have_count(0)


def test_new_page_on_a_groups_home_starts_a_page_of_the_group(alice, wiki):
    alice.goto("/groups/dev")
    alice.get_by_role("link", name="New page", exact=True).click()
    alice.wait_for_url("**/new?for=group**")
    expect(alice.locator("#page-for")).to_have_value("group:dev")
    alice.locator("#page-name").fill("Road Map")
    alice.get_by_role("button", name="Create").click()
    alice.wait_for_url("**/edit/groups/dev/road-map")


# ── The way in ───────────────────────────────────────────────────────────────

def test_groups_in_the_switcher_leads_to_the_groups_home(alice, wiki):
    """One group: its pill is called by the group and goes to its home, made
    on the first visit; g g does too; the mark beside a page's title names
    the group and leads back."""
    alice.goto("/page/index")
    switch = alice.locator(".module-switch .module-link")
    expect(switch).to_have_text(["Wiki", "dev", "Private", "Tasks", "Notes"])
    expect(alice.locator(".groups-menu")).to_have_count(0)
    switch.nth(1).click()
    alice.wait_for_url("**/page/groups/dev/index")
    expect(alice.locator("h1")).to_contain_text("dev")
    expect(alice.locator('.module-switch .module-link[data-module="groups"]')).to_have_attribute("aria-current", "page")
    expect(alice.locator('.module-switch .module-link[data-module="wiki"]')).not_to_have_attribute("aria-current", "page")
    wiki.write("groups/dev/roadmap", "# Roadmap\n\nQ1.\n")
    alice.goto("/page/groups/dev/roadmap")
    mark = alice.locator("h1 .group-mark")
    expect(mark).to_have_text("👥 dev")
    expect(mark).to_have_attribute("title", "@dev: alice and carol see this page")
    mark.click()
    alice.wait_for_url("**/page/groups/dev/index")
    alice.goto("/page/index")
    alice.keyboard.press("g"); alice.keyboard.press("g")
    alice.wait_for_url("**/page/groups/dev/index")


def test_no_groups_for_who_is_in_none(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}})
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    page.goto("/page/index")
    expect(page.locator(".module-switch .module-link")).to_have_text(["Wiki", "Private", "Tasks", "Notes"])
    page.keyboard.press("g"); page.keyboard.press("g")
    page.wait_for_timeout(300)
    assert page.url.endswith("/page/index")


def test_several_groups_are_listed(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev, ops"}, "bob": {"groups": "design"}})
    monkeypatch.setattr(settings, "GROUPS", {"product": "dev, design"})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    page.goto("/page/index")
    # Several groups: the pill says Groups and opens them as a menu.
    pill = page.locator('.module-switch .module-link[data-module="groups"]')
    menu = page.locator(".groups-menu")
    expect(pill).to_have_text("Groups")
    expect(menu).to_be_hidden()
    pill.click()
    expect(menu).to_be_visible()
    expect(pill).to_have_attribute("aria-expanded", "true")
    expect(menu.get_by_role("menuitem")).to_have_text(["👥 dev", "👥 ops", "👥 product", "All your groups"])
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    b, m = box_of(pill), box_of(menu)
    assert m["y"] >= b["y"] + b["height"] and abs(m["x"] - b["x"]) < 4      # under the pill
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem", name="👥 ops")).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(pill).to_be_focused()
    assert page.url.endswith("/page/index")
    pill.click()
    menu.get_by_role("menuitem", name="👥 ops").click()
    page.wait_for_url("**/page/groups/ops/index")
    expect(page.locator(".module-switch .module-link[data-module='groups']")).to_have_attribute("aria-current", "page")
    # The list of them is the menu's last entry (and where g g, a phone's tab and a browser without JavaScript go).
    page.locator('.module-switch .module-link[data-module="groups"]').click()
    menu.get_by_role("menuitem", name="All your groups").click()
    page.wait_for_url("**/groups")
    expect(page.locator(".groups-list .group-name")).to_have_text(["👥 dev", "👥 ops", "👥 product"])
    expect(page.locator(".groups-list li").last).to_contain_text("everyone in design (bob) and dev (alice)")   # by which way
    page.locator(".groups-list li").last.locator("a").first.click()
    page.wait_for_url("**/page/groups/product/index")
    expect(page.locator("h1 .group-mark")).to_have_text("👥 product")


def test_the_phones_bar_has_groups(alice, wiki):
    alice.set_viewport_size({"width": 390, "height": 800})
    alice.goto("/page/index")
    bar = alice.locator(".action-buttons .action-module")
    expect(bar).to_have_count(5)
    expect(bar.nth(1)).to_have_text("Groups")          # the tab's word stays short, whatever the group is called
    box = box_of(alice.locator(".action-buttons"))
    last = box_of(alice.locator(".action-buttons .action-search"))
    assert last["x"] + last["width"] <= box["x"] + box["width"] + 1 and box["width"] <= 391     # nothing pushed off the screen
    alice.locator('.action-buttons .action-module[data-module="groups"]').click()
    alice.wait_for_url("**/page/groups/dev/index")


# ── A group of your own ──────────────────────────────────────────────────────

def test_make_a_group_say_who_is_in_it_and_rename_it(page, wiki, monkeypatch):
    """Someone in no group makes one from the user menu's Groups, is in it
    and owns it; under Manage… they say who else is in it and what it is
    called, and its page goes along with a new name."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2"), "carol": ("pw", "t3")})
    page.request.post("/login", form={"username": "bob", "password": "pw"})
    page.goto("/page/index")
    expect(page.locator('.module-switch .module-link[data-module="groups"]')).to_have_count(0)     # in no group: no pill
    page.locator(".top-user").click()
    page.get_by_role("menuitem", name="Settings").click()   # Groups is a tab of the settings pages
    page.get_by_role("navigation", name="Settings").get_by_role("link", name="Groups").click()
    page.wait_for_url("**/groups")
    expect(page.locator(".groups-none")).to_have_text("You are in no group.")
    page.get_by_label("The group's name").fill("apollo")
    page.get_by_role("combobox", name="Who else is in it").fill("ali")
    page.get_by_role("combobox", name="Who else is in it").press("Enter")              # the user offered: a pill
    expect(page.locator(".groups-new .pick-pill")).to_have_attribute("data-name", "alice")
    page.get_by_role("button", name="Make the group").click()
    page.wait_for_url("**/page/groups/apollo/index")                                   # on to its home, which asks what it is for
    expect(page.locator(".page-content em").first).to_have_text("What is this group for? A team, a project: say it here in a line or two.")
    page.goto("/groups")
    row = page.locator('.groups-list li[data-group="apollo"]')
    expect(row.locator(".group-who")).to_contain_text("alice, bob")
    expect(row.locator(".group-who")).to_contain_text("owned by bob")
    expect(page.locator('.module-switch .module-link[data-module="groups"] .module-label')).to_have_text("apollo")   # the pill, by its name
    # Its home, and a page there.
    row.get_by_role("link", name="apollo").click()
    page.wait_for_url("**/page/groups/apollo/index")
    wiki.write("groups/apollo/roadmap", "# Roadmap\n")
    # Manage…: carol in, alice out; then another name.
    page.goto("/groups")
    row.locator("summary").click()
    people = row.get_by_role("combobox", name="People")
    row.get_by_role("button", name="Take out alice").click()
    people.fill("carol")
    people.press("Enter")
    expect(row.locator("label", has_text="People").locator(".pick-pill")).to_have_count(2)
    row.get_by_role("button", name="Save").first.click()
    # Taking someone out takes the group's pages and lists from them: Save says whom first, and the second click saves.
    sure = row.get_by_role("button", name="Yes, take out alice")
    expect(sure).to_be_visible()
    assert "apollo" in (wiki.root / ".zone" / "config").read_text().split("[user alice]")[1].split("[")[0]
    sure.click()
    expect(page.locator(".users-done")).to_contain_text("No longer in it: alice")
    expect(row.locator(".group-who")).to_contain_text("bob, carol")
    row.locator("summary").click()
    row.get_by_label("Name").fill("artemis")
    row.get_by_role("button", name="Rename").click()
    expect(page.locator(".users-done")).to_contain_text("The group is called artemis now")
    expect(page.locator(".groups-list .group-name")).to_have_text(["👥 artemis"])
    assert (wiki.root / "groups/artemis/roadmap.md").exists() and not (wiki.root / "groups/apollo").exists()
    page.goto("/page/groups/artemis/roadmap")
    expect(page.locator("h1 .group-mark")).to_have_text("👥 artemis")


def test_a_name_field_takes_only_users_and_groups_there_are(page, wiki, monkeypatch):
    """The fields that take users or groups are pills, chosen from those
    there are: typing offers them, what names nobody is marked and the form
    isn't sent with it, Backspace takes the last pill out, a pasted list
    becomes pills; an admin's Everyone in offers groups, not the group
    itself."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2"), "carol": ("pw", "t3")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin", "groups": "dev"}, "bob": {"groups": "design"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    page.goto("/groups")
    form = page.locator(".groups-new").first
    field = form.get_by_role("combobox", name="Who else is in it")
    field.fill("x")
    expect(form.locator(".pick-none")).to_have_text("No user called x")
    field.press("Enter")
    expect(field).to_have_attribute("aria-invalid", "")
    form.get_by_label("The group's name").fill("apollo")
    form.get_by_role("button", name="Make the group").click()
    page.wait_for_timeout(300)
    assert page.url.endswith("/groups") and "[group apollo]" not in (wiki.root / ".zone/config").read_text()   # not sent
    field.fill("")
    field.fill("bob, carol, zed ")                         # a pasted list: who there is becomes a pill
    expect(form.locator(".pick-pill")).to_have_count(2)
    expect(field).to_have_value("zed")
    expect(field).to_have_attribute("aria-invalid", "")
    field.fill("")
    field.press("Backspace")                               # the last pill goes
    expect(form.locator(".pick-pill")).to_have_count(1)
    field.press("ArrowDown")                               # the menu: who is not chosen yet
    expect(form.locator(".pick-option")).to_have_text(["alice", "carol"])
    field.press("ArrowDown")
    field.press("Enter")
    expect(form.locator(".pick-pill")).to_have_count(2)
    assert form.locator('input[name="people"]').input_value() == "bob, carol"
    form.get_by_role("button", name="Make the group").click()
    page.wait_for_url("**/page/groups/apollo/index")
    page.goto("/groups")
    wait_ready(page)   # a new page: its pickers take focus once its code runs
    assert "groups = apollo" in (wiki.root / ".zone/config").read_text().split("[user carol]")[1]
    # An admin's Everyone in: the groups there are, less the group itself.
    row = page.locator('.groups-list li[data-group="dev"]')
    row.locator("summary").click()
    everyone = row.get_by_role("combobox", name="Everyone in")
    everyone.focus()
    expect(row.locator(".pick-option")).to_have_text(["👥 apollo", "👥 design"])
    everyone.fill("des")
    everyone.press("Tab")
    expect(row.locator('form[action="/groups/includes"] .pick-pill')).to_have_attribute("data-name", "design")
    row.locator('form[action="/groups/includes"]').get_by_role("button", name="Save").click()
    expect(page.locator(".users-done")).to_contain_text("dev includes design")
    assert "[group dev]\nincludes = design" in (wiki.root / ".zone/config").read_text()


def test_remove_a_group_and_move_its_pages_to_the_wiki(page, wiki, monkeypatch):
    """Remove, under Manage… of a group you own: with pages it asks what
    becomes of them, and moved they are in a folder of the wiki for everyone."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    assert page.request.post("/groups", form={"name": "apollo", "people": "bob"}).ok
    page.goto("/groups/apollo")                               # its home, made on the first visit
    page.wait_for_url("**/page/groups/apollo/index")
    page.goto("/groups")
    row = page.locator('.groups-list li[data-group="apollo"]')
    row.locator("summary").click()                            # Manage…
    form = row.locator(".group-remove form")
    expect(form).to_contain_text("1 page will be seen and changed by everyone who uses this wiki")
    form.get_by_role("button", name="Remove apollo").click()   # nothing chosen: the browser keeps the form
    assert "[group apollo]" in (wiki.root / ".zone" / "config").read_text()
    form.get_by_role("radio").first.check()
    form.get_by_label("The folder the pages go to").fill("space")
    form.get_by_role("button", name="Remove apollo").click()
    expect(page.locator(".users-done")).to_contain_text("Removed the group apollo: its pages are in space/ now")
    expect(page.locator('.groups-list li[data-group="apollo"]')).to_have_count(0)
    assert (wiki.root / "space/index.md").exists() and not (wiki.root / "groups/apollo").exists()
