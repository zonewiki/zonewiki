"""A group's pages in the browser (docs/spec/users.md, Group pages): Share…
in the page menu."""

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
