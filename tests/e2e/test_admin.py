"""The admin page in the browser (docs/spec/users.md, Managing users):
Remove… asks in a dialog, and your own row offers no Disable or Remove."""

import re

import pytest
from playwright.sync_api import expect

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
