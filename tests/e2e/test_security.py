"""The Content-Security-Policy in a real browser: a page's raw HTML (which an
AI client may have written after a prompt injection) runs no script, while
Leaf's own scripts keep working."""

from playwright.sync_api import expect

ATTACKS = (
    "<script>window.pwned = 'script'</script>\n\n"
    "<img id=\"bad-img\" src=\"/nope.png\" onerror=\"window.pwned = 'onerror'\">\n\n"
    "<a id=\"bad-link\" href=\"javascript:window.pwned = 'link'\">click me</a>\n\n"
)


def test_raw_html_in_a_page_runs_no_script(page, wiki):
    wiki.write("evil", "# Evil\n\n" + ATTACKS + "```python\nx = 1\n```\n")
    page.goto("/page/evil")
    # Leaf's own scripts still run: the code block is highlighted.
    expect(page.locator("article.page-content pre code.hljs")).to_have_count(1)
    expect(page.locator("#bad-img")).to_have_count(1)
    page.locator("#bad-link").click()
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None
    # The browser blocked them, which the harness would count against Leaf.
    assert page.csp_violations
    page.csp_violations.clear()


def test_editor_preview_runs_no_handler(page, wiki):
    # The live preview puts rendered HTML into the page with innerHTML, where
    # an onerror= handler would otherwise run.
    page.goto("/edit/preview-evil")
    page.keyboard.press("Control+End")
    page.keyboard.type('<img id="bad-img" src="/nope.png" onerror="window.pwned = 1">')
    expect(page.locator("#editor-preview #bad-img")).to_have_count(1)
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None
    assert page.csp_violations
    page.csp_violations.clear()


def test_uploaded_script_does_not_run(page, wiki):
    # script-src 'self' trusts Leaf's origin, so an upload must not be
    # servable as a script: it comes as application/octet-stream with nosniff.
    (wiki.root / "_assets" / "x.js").write_text("window.pwned = 'upload'\n")
    wiki.write("loader", '# Loader\n\n<script src="/_assets/x.js"></script>\n')
    page.goto("/page/loader")
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None
