"""The Usage page in the browser (docs/spec/users.md, Usage): reached from
Settings, and readable on a phone."""

import json
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from conftest import SCREENSHOT_DIR, TOUCH_PHONE
from tests import settings


@pytest.fixture()
def counted(wiki, monkeypatch):
    """A wiki with users whose usage files hold a few weeks."""
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", ""), "bob": ("pw", ""), "carol": ("pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    today = date.today()
    days = {(today - timedelta(days=n)).isoformat(): {
        "logins": 2 + n % 3, "users": 1 + n % 3, "page_views": 140 - 3 * n, "searches": 12 + n, "page_edits": 9 + n % 5,
        "task_edits": 21 - n % 7, "assistant_edits": n % 4, "mcp_calls": 30 + 2 * n} for n in range(1, 40)}
    folder = wiki.root / ".zone" / "usage"
    folder.mkdir(parents=True)
    (folder / "counts.json").write_text(json.dumps({"days": days}))
    (folder / "users.json").write_text(json.dumps({"bob": (today - timedelta(days=3)).isoformat()}))
    return wiki


def test_settings_leads_an_admin_to_usage(page, counted):
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    page.goto("/page/index")
    page.locator(".top-actions .top-user").click()
    page.locator(".module-menu").get_by_role("menuitem", name="Settings").click()
    page.locator(".settings-tabs").get_by_role("link", name="Usage").click()
    page.wait_for_url("**/usage")
    expect(page.get_by_role("heading", name="Usage", level=1)).to_be_visible()
    expect(page.locator(".settings-tabs a[aria-current=page]")).to_have_text("Usage")
    sums = page.locator(".usage-sums tbody tr")
    expect(sums).to_have_count(8)
    expect(sums.first.locator("td")).to_have_text(["2", "2", "2"])    # alice today, bob three days ago
    users = page.locator(".usage-users tbody tr")
    expect(users.nth(0)).to_contain_text("alice")
    expect(users.nth(1)).to_contain_text("bob")
    expect(users.nth(2)).to_have_text("carolNot in the last 8 weeks")
    expect(page.locator(".usage-days tbody tr")).to_have_count(40)   # 39 days from the file, and today
    # On a desk the days fit the sheet: nothing to scroll for the last column.
    assert page.evaluate("(() => { const b = document.querySelector('.usage-scroll'); return b.scrollWidth <= b.clientWidth; })()")
    page.screenshot(path=str(SCREENSHOT_DIR / "usage.png"), full_page=True)


def test_on_a_phone_the_days_scroll_in_their_box(new_page, counted):
    phone = new_page(**TOUCH_PHONE)
    phone.request.post("/login", form={"username": "alice", "password": "pw"})
    phone.goto("/usage")
    expect(phone.locator(".usage-days")).to_be_visible()
    # The page itself is no wider than the screen: the wide table scrolls alone.
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert phone.evaluate("(() => { const b = document.querySelector('.usage-scroll'); return b.scrollWidth > b.clientWidth; })()")
    for table in (".usage-sums", ".usage-users"):
        assert phone.evaluate(f"document.querySelector('{table}').getBoundingClientRect().right <= window.innerWidth")
    phone.screenshot(path=str(SCREENSHOT_DIR / "usage-phone.png"), full_page=True)
