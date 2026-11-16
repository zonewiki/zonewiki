"""A group's pages in the browser (docs/spec/users.md, Group pages): Share…
in the page menu."""

import re

import pytest
from playwright.sync_api import expect

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
    assert "[[groups/dev/ideas]]" in wiki.read("plans")
    # From the group's folder it can still go on to everyone.
    alice.locator(".page-menu-btn").first.click()
    alice.get_by_role("menuitem", name="Share…").click()
    expect(dialog.locator(".share-choice")).to_have_count(1)
    expect(dialog).to_contain_text("the folder of @dev")
    dialog.get_by_role("button", name="Share").click()
    alice.wait_for_url("**/page/ideas")
    alice.locator(".page-menu-btn").first.click()
    expect(alice.get_by_role("menuitem", name="Share…")).to_have_count(0)


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
    expect(items).to_have_text([re.compile("^New page for @dev: secret"), "groups/dev/secretroadmap", "secretplans"])
    alice.keyboard.press("Enter")
    expect(alice.locator("#editor-textarea")).to_have_value(re.compile(r"\[\[groups/dev/secret\|secret\]\]"))
    alice.goto("/edit/notes")
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secret")
    expect(items).to_have_text(["secretplans"])
    alice.goto("/edit/private/alice/secretdiary")       # hers alone: all she sees
    alice.keyboard.press("Control+End")
    alice.keyboard.type("[[secretr")
    expect(items).to_have_text([re.compile("^New private page: secretr"), "groups/dev/secretroadmap"])


def test_a_new_shared_page_from_a_groups_page_says_so(alice, wiki):
    wiki.write("groups/dev/standup", "# Standup\n\nSee [[Garden]].\n")
    alice.goto("/page/groups/dev/standup")
    link = alice.locator("a.wikilink-shared-new")
    expect(link).to_have_attribute("title", "Creates a shared page")
    link.click()
    alice.wait_for_url("**/edit/garden?from=group:dev")
    banner = alice.locator("#shared-new-banner")
    expect(banner).to_contain_text("For a page of @dev, link to groups/dev/garden instead")
    banner.get_by_role("button", name="Keep shared").click()
    expect(banner).to_have_count(0)
