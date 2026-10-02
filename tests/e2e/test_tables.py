"""Sortable tables: clicking a column header sorts the rows (view only)."""

from playwright.sync_api import expect

TABLE = """# Prices

| Item | Price | Note |
|------|-------|------|
| banana | 10 | |
| Apple | 9.5 | ripe |
| cherry | -2 | sour |
| item 10 | 1,200 | |
| item 9 | | x |
"""


def column(page, n):
    return page.locator(f".page-content tbody tr td:nth-child({n})")


def test_header_click_cycles_asc_desc_original(page, wiki):
    wiki.write("prices", TABLE)
    page.goto("/page/prices")
    item = page.locator(".page-content thead th", has_text="Item")
    original = ["banana", "Apple", "cherry", "item 10", "item 9"]
    expect(column(page, 1)).to_have_text(original)

    item.click()
    # Case-insensitive, and "item 9" before "item 10"
    expect(column(page, 1)).to_have_text(["Apple", "banana", "cherry", "item 9", "item 10"])
    expect(item).to_have_attribute("aria-sort", "ascending")
    item.click()
    expect(column(page, 1)).to_have_text(["item 10", "item 9", "cherry", "banana", "Apple"])
    item.click()
    expect(column(page, 1)).to_have_text(original)
    expect(item).to_have_attribute("aria-sort", "none")
    # The file is untouched
    assert wiki.read("prices") == TABLE


def test_numeric_column_and_empty_cells_last(page, wiki):
    wiki.write("prices", TABLE)
    page.goto("/page/prices")
    price = page.locator(".page-content thead th", has_text="Price")
    price.click()
    expect(column(page, 2)).to_have_text(["-2", "9.5", "10", "1,200", ""])
    price.click()
    expect(column(page, 2)).to_have_text(["1,200", "10", "9.5", "-2", ""])
    # Sorting another column resets the first one's arrow
    page.locator(".page-content thead th", has_text="Note").click()
    expect(price).to_have_attribute("aria-sort", "none")
    expect(column(page, 3)).to_have_text(["ripe", "sour", "x", "", ""])


def test_keyboard_sorts(page, wiki):
    wiki.write("prices", TABLE)
    page.goto("/page/prices")
    page.locator(".page-content thead th", has_text="Item").focus()
    page.keyboard.press("Enter")
    expect(column(page, 1).first).to_have_text("Apple")


def test_editor_preview_tables_not_sortable(page, wiki):
    wiki.write("prices", TABLE)
    page.goto("/edit/prices")
    expect(page.locator("#editor-preview table")).to_be_attached()
    expect(page.locator(".sortable-table")).to_have_count(0)
