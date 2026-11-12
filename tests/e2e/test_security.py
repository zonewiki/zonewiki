"""The Content-Security-Policy in a real browser: a page's raw HTML (which an
AI client may have written after a prompt injection) runs no script, while
Zone's own scripts keep working."""

from playwright.sync_api import expect

ATTACKS = (
    "<script>window.pwned = 'script'</script>\n\n"
    "<img id=\"bad-img\" src=\"/nope.png\" onerror=\"window.pwned = 'onerror'\">\n\n"
    "<a id=\"bad-link\" href=\"javascript:window.pwned = 'link'\">click me</a>\n\n"
)


def test_raw_html_in_a_page_runs_no_script(page, wiki):
    wiki.write("evil", "# Evil\n\n" + ATTACKS + "```python\nx = 1\n```\n")
    page.goto("/page/evil")
    # Zone's own scripts still run: the code block is highlighted.
    expect(page.locator("article.page-content pre code.hljs")).to_have_count(1)
    expect(page.locator("#bad-img")).to_have_count(1)
    page.locator("#bad-link").click()
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None
    # The browser blocked them, which the harness would count against Zone.
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
    # script-src 'self' trusts Zone's origin, so an upload must not be
    # servable as a script: it comes as application/octet-stream with nosniff.
    (wiki.root / "_assets" / "x.js").write_text("window.pwned = 'upload'\n")
    wiki.write("loader", '# Loader\n\n<script src="/_assets/x.js"></script>\n')
    page.goto("/page/loader")
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None


def test_a_page_frames_no_form(page, wiki):
    # An iframe's srcdoc would be a document of Zone's own origin, where a
    # form posts to Zone like one of its own: however the attribute is
    # written, the frame stays empty.
    form = "&lt;form method=post action=/delete/index&gt;&lt;button id=go&gt;Go&lt;/button&gt;&lt;/form&gt;"
    wiki.write("framed", "# Framed\n\n"
               f'<iframe id="f1" srcdoc="{form}"></iframe>\n\n'
               f"<iframe id=\"f2\" a=b' srcdoc=\"{form}\"></iframe>\n\n"
               f'<!-- <a title=" --><iframe id="f3" srcdoc="{form}"></iframe><!-- " -->\n')
    page.goto("/page/framed")
    expect(page.locator("article.page-content iframe")).to_have_count(3)
    for frame in ("f1", "f2", "f3"):
        assert page.locator(f"#{frame}").get_attribute("srcdoc") is None
        expect(page.frame_locator(f"#{frame}").locator("form")).to_have_count(0)


def test_a_tasks_id_is_text_on_its_card(page, wiki):
    # A tasks file written by hand (or by an older Zone) may hold any id:
    # the card shows the task, and the id makes no element of its own.
    import json
    evil = 'x"></span><form id="evil" method="post" action="/delete/index"><button>ok</button></form><span data-x="'
    (wiki.root / "_tasks").mkdir(exist_ok=True)
    (wiki.root / "_tasks" / "tasks.json").write_text(json.dumps([{
        "id": evil, "title": "Odd id", "created_at": "2026-01-01T10:00:00"}, {
        "id": "done-1", "title": "Odd day", "created_at": "2026-01-01T10:00:00",
        "due_date": "<i id=evil2>", "start_date": "<i id=evil3>"}]))
    page.goto("/tasks?view=open")
    expect(page.locator(".task-item")).to_have_count(2)
    expect(page.locator("#evil, #evil2, #evil3")).to_have_count(0)
    assert page.locator(".task-item").first.get_attribute("data-id") in (evil, "done-1")
