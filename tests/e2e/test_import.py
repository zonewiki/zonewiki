"""Import pages in a browser: from the New Page form, a vault's zip
uploaded, its report read, Import, the folder's index opened, and the
whole import undone from Changes."""

from playwright.sync_api import expect

from tests.conftest import notes_zip
from tests.e2e.conftest import SCREENSHOT_DIR


def test_upload_report_import_and_undo(page, wiki):
    page.goto("/new")
    page.get_by_role("button", name="Import pages…").click()
    page.wait_for_url("**/import?**")
    expect(page.get_by_role("heading", name="Import pages")).to_be_visible()
    page.get_by_label("Zip file").set_input_files({"name": "My Vault.zip", "mimeType": "application/zip",
                                                    "buffer": notes_zip(NOTES)})
    page.get_by_role("button", name="Upload").click()
    expect(page.get_by_role("heading", name="Import “My Vault”")).to_be_visible()
    expect(page.locator(".import-summary")).to_contain_text("2 pages and 1 file will go into the new folder my-vault/")
    expect(page.get_by_role("heading", name="Links to nothing")).to_be_visible()
    expect(page.locator(".import-lines")).to_contain_text("[[Nowhere]]")
    page.screenshot(path=str(SCREENSHOT_DIR / "import-report.png"))
    assert not (wiki.root / "my-vault").exists()
    page.get_by_role("button", name="Import", exact=True).click()
    page.wait_for_url("**/page/my-vault/index")
    expect(page.locator("article h2", has_text="Links to nothing")).to_be_visible()
    page.locator("article a", has_text="Garden").first.click()
    page.wait_for_url("**/page/my-vault/garden")
    expect(page.locator("article img")).to_have_attribute("src", "/_assets/pic.png")
    # Undone from Changes, whole: the pages, the file, the home's line.
    page.goto("/changes")
    row = page.locator(".changes-row").first
    expect(row).to_contain_text("Import 2 pages and 1 file from My Vault")
    row.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_url("**/changes?undone=**")
    assert not (wiki.root / "my-vault/garden.md").exists() and not (wiki.root / "_assets/pic.png").exists()
    assert "my-vault" not in wiki.read("index")


NOTES = {"My Vault/Garden.md": "# Garden\n\nSee [[Roses]], [[Nowhere]] and ![](pic.png).\n",
         "My Vault/Roses.md": "Red ones.\n", "My Vault/pic.png": b"\x89PNG\r\n\x1a\n"}
