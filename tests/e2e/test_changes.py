"""Changes (/changes) in a browser: found from the search list, its filter
form, a page's link to what the commit changed, and its fit on a phone."""

from playwright.sync_api import expect

from tests.e2e.conftest import PHONE, SCREENSHOT_DIR, box_of
from tests.e2e.test_layout import horizontal_overflow


def _commits(wiki):
    """Pages committed as Zonewiki commits a save, one of them as an assistant's."""
    for slug, message in (("garden", "Update garden"), ("trips/rome", "Create trips/rome: from the mail (MCP)\n\nAssistant: MCP token"),
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
    # The filters are folded away until asked for.
    expect(page.get_by_label("Only by an assistant")).to_be_hidden()
    page.locator(".changes-filter-box summary").click()
    page.get_by_label("Only by an assistant").check()
    page.get_by_role("button", name="Show").click()
    page.wait_for_url("**/changes?**assistant=1**")
    expect(rows).to_have_count(1)
    # Shut again, and what is set is said beside the button.
    expect(page.get_by_label("Only by an assistant")).to_be_hidden()
    expect(page.locator(".changes-on")).to_have_text("by an assistant")
    page.screenshot(path=str(SCREENSHOT_DIR / "changes.png"))
    rows.first.get_by_role("link", name="trips/rome").click()
    page.wait_for_url("**/diff/trips/rome/**")
    expect(page.locator(".diff-view")).to_contain_text("Text of trips/rome.")


def test_fits_a_phone(new_page, wiki):
    _commits(wiki)
    pg = new_page(viewport=PHONE)
    pg.goto("/changes")
    expect(pg.locator(".changes-row")).to_have_count(5)   # and the wiki's first two (its files, .zone-format)
    assert horizontal_overflow(pg) <= 0
    # The list starts right under the Filter button, and a row reads down:
    # the text, what it changed under it, both beside the time's column.
    expect(pg.locator(".changes-filter")).to_be_hidden()
    row = pg.locator(".changes-row").first
    when, what, items = (box_of(row.locator(s)) for s in (".hist-when", ".changes-what", ".changes-items"))
    assert when["x"] + when["width"] <= what["x"] and abs(items["x"] - what["x"]) < 2
    assert items["y"] >= what["y"] + what["height"] - 1
    pg.screenshot(path=str(SCREENSHOT_DIR / "changes-phone.png"))
    # Open, the filters are one to a line and fit the screen.
    pg.locator(".changes-filter-box summary").click()
    expect(pg.get_by_role("button", name="Show")).to_be_visible()
    assert horizontal_overflow(pg) <= 0


def test_undo_one_change_and_an_assistants_run(page, wiki):
    wiki.write("garden", "# Garden\n\nBeds.\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-q", "-m", "Update garden")
    for text, message in (("# Garden\n\nBeds.\n\nPaths.\n", "Append to garden (MCP)\n\nAssistant: Claude"),
                          ("# Garden\n\nBeds.\n\nPaths.\n\nA pond.\n", "Append to garden: a pond (MCP)\n\nAssistant: Claude")):
        wiki.write("garden", text)
        wiki.git("commit", "-q", "-am", message)
    page.goto("/changes")
    rows = page.locator(".changes-row")
    expect(rows.first.locator(".changes-assistant")).to_have_text("through Claude")
    page.screenshot(path=str(SCREENSHOT_DIR / "changes-undo.png"))
    # The assistant's run together: asked first.
    said = []
    page.once("dialog", lambda d: (said.append(d.message), d.accept()))
    rows.first.get_by_role("button", name="Undo all 2").click()
    page.wait_for_url("**/changes?undone=**")
    assert "the 2 changes Claude made" in said[0]
    expect(page.get_by_role("status")).to_contain_text("Undone: garden as before.")
    assert wiki.read("garden") == "# Garden\n\nBeds.\n"
    # One change, here the undo itself: no question, it is a change that can be undone too.
    expect(rows.first).to_contain_text("Undo 2 changes")
    rows.first.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_url("**/changes?undone=**")
    assert "A pond." in wiki.read("garden")


def test_a_task_named_and_its_change_undone(page, wiki):
    page.goto("/page/index")
    made = page.request.post("/api/tasks", data={"title": "Water the beds"})
    assert made.ok
    page.goto("/changes")
    row = page.locator(".changes-row").first
    expect(row.get_by_role("link", name="Water the beds")).to_have_attribute("href", f"/tasks?task={made.json()['id']}&view=open")
    row.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_url("**/changes?undone=**")
    expect(page.get_by_role("status")).to_contain_text("Undone: the task “Water the beds” deleted again.")
    assert made.json()["id"] not in (wiki.root / "_tasks/tasks.json").read_text()
