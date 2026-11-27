"""The admin page in the browser (docs/spec/users.md, Managing users):
Remove… asks in a dialog, and your own row offers no Disable or Remove."""

import re

import pytest
from playwright.sync_api import expect

from conftest import PHONE, TOUCH_PHONE, box_of
from tests import settings


@pytest.fixture()
def admin(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    page.goto("/admin/users")
    return page


def test_remove_asks_in_a_dialog(admin, wiki):
    dialog = admin.locator("#user-remove-dialog")
    admin.locator("tr", has_text="bob").locator("summary").click()
    assert dialog.is_visible()
    assert "Remove bob from this wiki?" in dialog.inner_text()
    assert admin.locator("tr", has_text="bob").locator("details").evaluate("d => d.open") is False
    dialog.locator("#user-remove-cancel").click()
    assert not dialog.is_visible()
    assert "[user bob]" in (wiki.root / ".zone" / "config").read_text()

    admin.locator("tr", has_text="bob").locator("summary").click()
    dialog.get_by_role("button", name="Remove bob").click()
    admin.wait_for_url("**/admin/users?done=remove**")
    assert "Removed bob." in admin.inner_text("main")
    assert "[user bob]" not in (wiki.root / ".zone" / "config").read_text()


def test_your_own_row_has_no_disable_or_remove(admin):
    own = admin.locator("tr", has_text="(you)")
    assert own.get_by_role("button", name="Reset password").is_visible()
    assert own.get_by_role("button", name="Disable").count() == 0 and own.locator("summary").count() == 0


def test_a_reset_link_works_in_a_browser(admin, wiki):
    """The welcome page's form is sent by a browser, with its Origin (docs/spec/users.md)."""
    body = admin.request.post("/admin/users/bob/reset").text()
    link = re.search(r"(/welcome/[0-9a-f]{64})</code>", body).group(1)
    admin.request.post("/logout")
    admin.goto(link)
    admin.locator("input[name=password]").fill("a new password")
    admin.locator("input[name=again]").fill("a new password")
    admin.get_by_role("button", name="Set the password").click()
    expect(admin.locator(".top-user")).to_have_attribute("title", "bob")


def test_save_shows_once_a_field_is_edited(admin, wiki):
    """An untouched row has no buttons beside its role and its groups; an
    edit shows the one beside it, and taking the edit back hides it again.
    The groups are pills, of the groups there are."""
    admin.request.post("/groups", form={"name": "dev", "people": ""})     # alice makes dev on Groups
    admin.goto("/admin/users")
    row = admin.locator("tr", has_text="bob")
    change, set_ = row.locator('button[aria-label="Save the role of bob"]'), row.locator('button[aria-label="Save the groups of bob"]')
    expect(change).to_have_text("Save")
    expect(set_).to_have_text("Save")
    expect(change).to_be_hidden()
    expect(set_).to_be_hidden()
    before = box_of(row)
    row.locator("select").select_option("viewer")
    expect(change).to_be_visible()
    expect(set_).to_be_hidden()
    assert box_of(row) == before                      # the button had its place: nothing moves
    assert "role" not in (wiki.root / ".zone" / "config").read_text().split("[user bob]")[1]   # picking saved nothing
    row.locator("select").select_option("editor")
    expect(change).to_be_hidden()
    groups = row.get_by_role("combobox", name="Groups of bob")
    groups.fill("de")
    expect(row.locator(".pick-option")).to_have_text(["👥 dev"])
    groups.press("Enter")
    expect(row.locator(".pick-pill")).to_have_attribute("data-name", "dev")
    expect(set_).to_be_visible()
    row.get_by_role("button", name="Take out dev").click()
    expect(row.locator(".pick-pill")).to_have_count(0)
    expect(set_).to_be_hidden()
    groups.fill("dev")
    groups.press("Enter")
    expect(set_).to_be_visible()
    groups.press("Enter")                                     # with nothing typed, Enter saves, as the button does
    admin.wait_for_url("**/admin/users?done=groups**")
    assert "groups = dev" in (wiki.root / ".zone" / "config").read_text().split("[user bob]")[1]
    row = admin.locator("tr", has_text="bob").first
    expect(row.locator('button[aria-label="Save the groups of bob"]')).to_be_hidden()
    row.locator("select").select_option("viewer")
    row.locator('button[aria-label="Save the role of bob"]').click()
    admin.wait_for_url("**/admin/users?done=role**")
    assert "role = viewer" in (wiki.root / ".zone" / "config").read_text()
    # Groups are made and changed on Groups, which the page links to.
    expect(admin.get_by_role("button", name="Make a group of groups")).to_have_count(0)
    admin.locator('.sheet a[href="/groups"]').click()
    admin.wait_for_url("**/groups")
    expect(admin.get_by_role("button", name="Make a group of groups")).to_be_visible()


def test_a_phone_shows_a_user_in_a_few_lines(new_page, wiki, monkeypatch):
    """On a phone a user is a short card (docs/spec/users.md, The Users tab):
    the role beside the name, the groups on one line under it, no "Active",
    and a Save only once there is something to save."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "t1"), "bob": ("pw", "t2"), "dave": ("pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "dave": {"disabled": "yes"}})
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"username": "alice", "password": "pw"})
    phone.request.post("/groups", form={"name": "dev", "people": "bob"})
    phone.request.post("/admin/users/invite", form={"name": "erin", "role": "editor"})
    phone.goto("/admin/users")
    row = phone.locator(".users-list tr", has_text="bob")
    name, role, groups = box_of(row.locator(".user-name")), box_of(row.locator("select")), box_of(row.locator(".pick"))
    actions = box_of(row.locator(".user-actions"))
    assert role["y"] < name["y"] + name["height"] and name["y"] < role["y"] + role["height"]   # one line
    assert role["x"] + role["width"] > PHONE["width"] - 30                                      # the role at its right end
    assert groups["y"] >= role["y"] + role["height"] and groups["width"] > 250                  # the groups under them, wide
    assert actions["y"] >= groups["y"] + groups["height"]
    assert actions["y"] + actions["height"] - name["y"] < 150                                   # three lines, not five
    expect(row.locator(".user-status")).to_be_hidden()                                          # Active isn't said
    off = phone.locator(".users-list tr", has_text="dave").locator(".user-status")
    expect(off).to_have_text("Disabled")
    expect(off).to_be_visible()
    # A Save takes no room until there is something to save, then saves.
    save = row.locator('button[aria-label="Save the role of bob"]')
    expect(save).to_be_hidden()
    row.locator("select").select_option("viewer")
    expect(save).to_be_visible()
    assert box_of(save)["x"] + box_of(save)["width"] <= PHONE["width"]
    save.click()
    phone.wait_for_url("**/admin/users?done=role**")
    assert "role = viewer" in (wiki.root / ".zone" / "config").read_text()
    # An open link: Revoke beside the name, what it is and until when under it.
    link = phone.locator(".users-links tr", has_text="erin")
    who, revoke = box_of(link.locator(".user-name")), box_of(link.get_by_role("button", name="Revoke"))
    assert revoke["y"] < who["y"] + who["height"] and revoke["x"] > who["x"]
    assert link.locator(".link-until").evaluate("e => getComputedStyle(e, '::before').content") == '"Works until "'
    assert phone.evaluate("document.documentElement.scrollWidth <= innerWidth")                 # nothing wider than the phone
