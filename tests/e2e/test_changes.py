"""Changes (/changes) in a browser: found from the search list, its filter
form, a page's link to what the commit changed, and its fit on a phone."""

from playwright.sync_api import expect

from tests.e2e.conftest import PHONE, SCREENSHOT_DIR
from tests.e2e.test_layout import horizontal_overflow


def _commits(wiki):
    """Pages committed as Zone commits a save, one of them as an assistant's."""
    for slug, message in (("garden", "Update garden"), ("trips/rome", "Create trips/rome: from the mail (MCP)"),
                          ("a-page-with-a-rather-long-name/and-a-folder-above-it", "Update the long one")):
        wiki.write(slug, f"# {slug}\n\nText of {slug}.\n")
        wiki.git("add", "-A")
        wiki.git("commit", "-q", "-m", message)


def test_found_filtered_and_opened(page, wiki):
    _commits(wiki)
    page.goto("/page/index")
    page.keyboard.press("Control+p")
    page.locator("#modal-page-list a", has_text="Changes").first.click()
    page.wait_for_url("**/changes")
    rows = page.locator(".changes-row")
    expect(rows.first).to_contain_text("Update the long one")
    expect(rows.nth(1).locator(".changes-assistant")).to_be_visible()
    page.get_by_label("Only by an assistant").check()
    page.get_by_role("button", name="Show").click()
    page.wait_for_url("**/changes?assistant=1**")
    expect(rows).to_have_count(1)
    page.screenshot(path=str(SCREENSHOT_DIR / "changes.png"))
    rows.first.get_by_role("link", name="trips/rome").click()
    page.wait_for_url("**/diff/trips/rome/**")
    expect(page.locator(".diff-view")).to_contain_text("Text of trips/rome.")


def test_fits_a_phone(new_page, wiki):
    _commits(wiki)
    pg = new_page(viewport=PHONE)
    pg.goto("/changes")
    expect(pg.locator(".changes-row")).to_have_count(4)   # and the wiki's first commit
    assert horizontal_overflow(pg) <= 0
    pg.screenshot(path=str(SCREENSHOT_DIR / "changes-phone.png"))
