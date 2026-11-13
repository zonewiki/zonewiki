"""Printing a page and Export as PDF… (the page's ⋯ menu): the page alone,
light on paper whatever the screen is."""
from playwright.sync_api import expect

PAGE = """---
tags: [garden]
---
# Garden plan

Intro with a [[Other]] link.

## Beds

```python
x = 1
```

| a | b |
|---|---|
| 1 | 2 |
"""


def test_export_as_pdf_opens_the_print_dialog_named_after_the_page(page, wiki):
    wiki.write("garden", PAGE)
    page.goto("/page/garden")
    # The print dialog can't be driven: note what it would have been given.
    page.evaluate("window.print = () => { window.printedAs = document.title; }")
    page.locator(".page-menu-btn").click()
    page.get_by_role("menuitem", name="Export as PDF…").click()
    page.wait_for_function("window.printedAs !== undefined")
    assert page.evaluate("window.printedAs") == "Garden plan"     # the file's name, without "— Zone"
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    assert page.title().startswith("Garden plan — ")


def test_print_shows_the_page_alone(page, wiki):
    wiki.write("garden", PAGE)
    wiki.write("other", "# Other\n\nSee [[Garden]].\n")
    page.goto("/page/garden")
    expect(page.locator(".breadcrumb")).to_be_visible()
    page.emulate_media(media="print")
    for hidden in (".breadcrumb", ".top-actions", ".page-header-actions", ".page-foot", ".rail-context",
                   ".section-edit-btn", ".zone-build"):
        expect(page.locator(hidden).first).to_be_hidden()
    expect(page.locator(".page-header h1")).to_be_visible()
    expect(page.locator("article table")).to_be_visible()
    expect(page.locator("article pre code")).to_be_visible()
    # The text takes the sheet's width.
    article, main = page.locator("article").bounding_box(), page.locator("main").bounding_box()
    assert article["width"] >= main["width"] - 2
    # A real PDF comes out of it.
    assert page.pdf()[:5] == b"%PDF-"


def test_print_is_light_on_a_dark_screen(new_page, wiki):
    wiki.write("garden", PAGE)
    page = new_page(color_scheme="dark")
    page.goto("/page/garden")
    dark = page.evaluate("getComputedStyle(document.body).color")
    page.emulate_media(media="print", color_scheme="dark")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"
    ink = page.evaluate("getComputedStyle(document.body).color")
    assert ink != dark and ink == "rgb(29, 31, 28)"
    # Code is highlighted for paper too (the light theme's keyword colour).
    light_sheet = page.evaluate("""[...document.querySelectorAll('link[rel=stylesheet]')]
        .filter(l => l.href.includes('highlight')).map(l => [l.href.includes('dark'), matchMedia(l.media).matches])""")
    assert sorted(light_sheet) == [[False, True], [True, False]]
