"""Editor formatting bar: buttons and Ctrl+B/I/K turn the selection into markdown."""

import re

from playwright.sync_api import expect

from conftest import TOUCH_PHONE, box_of, make_list
from tests import settings


def _edit(page, wiki, text, sel_start, sel_end=None):
    """Open an editor on `text` (after the H1) with that part selected.

    A test's later editors get pages of their own (fmt-2, fmt-3 …): the
    editor before may still be saving what the test did in it (auto-save
    two seconds after an edit, a save on leaving), and that save landing
    after this write would bring its text back.
    """
    page.fmt_editors = getattr(page, "fmt_editors", 0) + 1
    slug = "fmt" if page.fmt_editors == 1 else f"fmt-{page.fmt_editors}"
    wiki.write(slug, "# Fmt\n\n" + text)
    page.goto(f"/edit/{slug}")
    textarea = page.locator("#editor-textarea")
    off = len("# Fmt\n\n")
    end = sel_start if sel_end is None else sel_end
    textarea.evaluate(f"ta => ta.setSelectionRange({off + sel_start}, {off + end})")
    return textarea


def _body(textarea) -> str:
    return textarea.input_value().removeprefix("# Fmt\n\n")


def _click(page, fmt):
    page.locator(f'#format-bar .fmt-btn[data-fmt="{fmt}"]').click()


def test_bold_wraps_unwraps_and_undoes(page, wiki):
    textarea = _edit(page, wiki, "make this loud", 10, 14)
    _click(page, "bold")
    assert _body(textarea) == "make this **loud**"
    # The word stays selected and the textarea keeps the focus.
    expect(textarea).to_be_focused()
    assert textarea.evaluate("ta => ta.value.slice(ta.selectionStart, ta.selectionEnd)") == "loud"
    _click(page, "bold")
    assert _body(textarea) == "make this loud"
    _click(page, "bold")
    page.keyboard.press("Control+z")
    assert _body(textarea) == "make this loud"


def test_wrap_leaves_surrounding_spaces_outside(page, wiki):
    textarea = _edit(page, wiki, "a word here", 1, 7)   # " word "
    _click(page, "italic")
    assert _body(textarea) == "a _word_ here"


def test_shortcuts_without_selection_put_the_cursor_inside(page, wiki):
    textarea = _edit(page, wiki, "", 0)
    page.keyboard.press("Control+b")
    page.keyboard.type("bold")
    page.keyboard.press("End")
    page.keyboard.type(" ")
    page.keyboard.press("Control+i")
    page.keyboard.type("it")
    page.keyboard.press("End")
    page.keyboard.type(" ")
    page.keyboard.press("Control+k")
    page.keyboard.type("label")
    assert _body(textarea) == "**bold** _it_ [label]()"


def test_strike_and_inline_code(page, wiki):
    textarea = _edit(page, wiki, "old cmd", 0, 3)
    _click(page, "strike")
    textarea.evaluate("ta => ta.setSelectionRange(ta.value.length - 3, ta.value.length)")
    _click(page, "code")
    assert _body(textarea) == "~~old~~ `cmd`"


def test_code_toggles_off_span_content(page, wiki):
    # Selecting the content inside a code span (marks just outside the selection)
    # and clicking Code must remove the wrapping backticks.
    textarea = _edit(page, wiki, "`hello`", 1, 6)   # select 'hello' inside the span
    _click(page, "code")
    assert _body(textarea) == "hello"


def test_code_wraps_between_adjacent_spans(page, wiki):
    # Selecting text between two adjacent code spans must wrap it in backticks,
    # not eat the neighbouring spans' backtick markers.
    # Regression: check 1 in toggleWrap fired whenever both neighbours were
    # backticks, even when they belonged to two different spans.
    textarea = _edit(page, wiki, "`foo`bar`baz`", 5, 8)   # select 'bar'
    _click(page, "code")
    assert _body(textarea) == "`foo``bar``baz`"


def test_lists_switch_kind_number_and_toggle_off(page, wiki):
    text = "eggs\nmilk\n\nbread"
    textarea = _edit(page, wiki, text, 0, len(text))
    _click(page, "ul")
    assert _body(textarea) == "- eggs\n- milk\n\n- bread"
    _click(page, "ol")
    assert _body(textarea) == "1. eggs\n2. milk\n\n3. bread"
    _click(page, "task")
    assert _body(textarea) == "- [ ] eggs\n- [ ] milk\n\n- [ ] bread"
    _click(page, "task")
    assert _body(textarea) == text


def test_list_on_an_empty_line_continues_on_enter(page, wiki):
    textarea = _edit(page, wiki, "", 0)
    _click(page, "task")
    page.keyboard.type("first")
    page.keyboard.press("Enter")
    page.keyboard.type("second")
    assert _body(textarea) == "- [ ] first\n- [ ] second"


def test_heading_cycles(page, wiki):
    textarea = _edit(page, wiki, "Intro\nbody", 2)
    _click(page, "heading")
    assert _body(textarea) == "## Intro\nbody"
    _click(page, "heading")
    assert _body(textarea) == "### Intro\nbody"
    _click(page, "heading")
    assert _body(textarea) == "Intro\nbody"


def test_quote_toggles(page, wiki):
    text = "said this\n\nand that"
    textarea = _edit(page, wiki, text, 0, len(text))
    _click(page, "quote")
    assert _body(textarea) == "> said this\n>\n> and that"
    _click(page, "quote")
    assert _body(textarea) == text


def test_link_from_text_offers_pages(page, wiki):
    wiki.write("target-page", "# Target\n")
    textarea = _edit(page, wiki, "see the docs", 8, 12)
    _click(page, "link")
    assert _body(textarea) == "see the [docs]()"
    item = page.locator(".wikilink-ac-item", has_text="target-page")
    expect(item).to_be_visible()
    item.click()
    assert _body(textarea) == "see the [docs](/page/target-page)"


def test_link_from_url_asks_for_the_text(page, wiki):
    text = "https://example.com/x"
    textarea = _edit(page, wiki, text, 0, len(text))
    _click(page, "link")
    page.keyboard.type("Example")
    assert _body(textarea) == "[Example](https://example.com/x)"


def test_wikilink_button(page, wiki):
    wiki.write("target-page", "# Target\n")
    textarea = _edit(page, wiki, "Target Page", 0, 11)
    _click(page, "wikilink")
    assert _body(textarea) == "[[Target Page]]"
    page.keyboard.press("Enter")
    _click(page, "wikilink")
    page.keyboard.type("targ")   # narrows the autocomplete to the one page
    expect(page.locator(".wikilink-ac-item")).to_have_text(["target-page"])
    page.keyboard.press("Enter")
    assert _body(textarea) == "[[Target Page]]\n[[target-page]]"


def test_code_block_and_table(page, wiki):
    textarea = _edit(page, wiki, "ls -l\nText", 0, 5)
    _click(page, "codeblock")
    assert _body(textarea) == "```\nls -l\n```\nText"
    page.keyboard.press("Control+End")
    _click(page, "table")
    page.keyboard.type("Name")
    assert _body(textarea) == ("```\nls -l\n```\nText\n\n"
                               "| Name | Column 2 |\n| --- | --- |\n|  |  |")


def test_math_inline_in_text_and_a_block_on_lines(page, wiki):
    textarea = _edit(page, wiki, "Area x^2 here", 5, 8)
    _click(page, "math")
    assert _body(textarea) == "Area $x^2$ here"
    _click(page, "math")   # a second click unwraps
    assert _body(textarea) == "Area x^2 here"
    textarea = _edit(page, wiki, "Text ", 5)
    _click(page, "math")
    page.keyboard.type("a+b")
    assert _body(textarea) == "Text $a+b$"
    textarea = _edit(page, wiki, "a = b\\\\\nc = d\nText", 0, 13)
    _click(page, "math")
    assert _body(textarea) == "$$\na = b\\\\\nc = d\n$$\nText"
    textarea = _edit(page, wiki, "Text\n\nMore", 5)
    _click(page, "math")
    page.keyboard.type("E = mc^2")
    assert _body(textarea) == "Text\n$$\nE = mc^2\n$$\nMore"
    expect(page.locator("#editor-preview .math-display .katex")).to_have_count(1)


def test_diagram_starts_a_flowchart_or_fences_the_selection(page, wiki):
    textarea = _edit(page, wiki, "Text", 4)
    _click(page, "diagram")
    page.keyboard.type("X --> Y")   # the example's edge is selected
    assert _body(textarea) == "Text\n\n```mermaid\nflowchart LR\n    X --> Y\n```"
    expect(page.locator("#editor-preview .mermaid-diagram svg")).to_have_count(1)
    textarea = _edit(page, wiki, "graph TD\n  A --> B\nText", 0, 18)
    _click(page, "diagram")
    assert _body(textarea) == "```mermaid\ngraph TD\n  A --> B\n```\nText"


def _list_dialog(page):
    """The list settings dialog as the editor shows it, and a field of it by name."""
    dialog = page.locator("#task-settings-modal-editor")
    return dialog, lambda name: dialog.locator(f"#task-settings-{name}-editor")


def test_task_list_asks_what_the_list_shows_and_writes_the_embed(page, wiki):
    # The Task list button shows the dialog a list's ⋯ menu shows, with which
    # list the embed shows, and writes the line it makes. (A pin is no
    # param: a list is pinned on the Tasks page, by whoever pins it.)
    make_list(page, "shop")
    textarea = _edit(page, wiki, "Shopping", 8)
    _click(page, "tasklist")
    dialog, field = _list_dialog(page)
    expect(dialog).to_be_visible()
    expect(dialog.locator(".modal-label")).to_have_text("Task list")
    expect(field("list")).to_be_focused()
    # The lists there are, to choose from; nothing set embeds all tasks.
    assert "shop" in dialog.locator("datalist option").evaluate_all("os => os.map(o => o.value)")
    expect(field("where")).to_have_text("[[tasks:]]")
    more = lambda name: dialog.locator(".task-add-field", has_text=name)
    # A page says which tasks and how it looks at them (the tab, the
    # grouping, the filter); what they are (a WIP limit, tab names, keep, a
    # new task's tags) is set on the list, My tasks or All tasks, never here.
    for name in ("WIP limit", "Tab names", "Keep tasks", "Tags of a new task"):
        expect(more(name)).to_be_hidden()
    expect(more("Filter by tag")).to_be_visible()
    field("list").fill("#Garden work")                                # cleaned as a list's name is
    for name in ("WIP limit", "Tab names", "Keep tasks", "Tags of a new task"):
        expect(more(name)).to_be_hidden()
    field("view").select_option("open")
    expect(field("where")).to_have_text("[[tasks: list=garden-work view=open]]")   # Open, with Plan
    field("group").select_option("none")
    expect(dialog.locator(".task-settings-pin")).to_have_count(0)
    more("Filter by tag").click()
    field("tag").fill("urgent, -later")
    expect(field("where")).to_have_text("[[tasks: list=garden-work view=open group=none tag=urgent,-later]]")
    # Without the list it is All tasks, and still only the page's.
    field("list").fill("")
    for name in ("WIP limit", "Tab names", "Keep tasks", "Tags of a new task"):
        expect(more(name)).to_be_hidden()
    line = "[[tasks: view=open group=none tag=urgent,-later]]"
    expect(field("where")).to_have_text(line)
    dialog.get_by_role("button", name="Insert").click()
    expect(dialog).to_be_hidden()
    assert _body(textarea) == "Shopping\n\n" + line
    expect(textarea).to_be_focused()
    page.keyboard.press("Control+z")                                  # one step back
    assert _body(textarea) == "Shopping"


def test_task_list_dialog_cancelled_writes_nothing(page, wiki):
    textarea = _edit(page, wiki, "Shopping", 8)
    _click(page, "tasklist")
    dialog, field = _list_dialog(page)
    field("list").fill("shop")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    assert _body(textarea) == "Shopping"
    expect(textarea).to_be_focused()
    _click(page, "tasklist")
    expect(field("list")).to_have_value("")                           # a fresh dialog
    dialog.get_by_role("button", name="Cancel").click()
    assert _body(textarea) == "Shopping"
    # All tasks: the bare embed, on a line of its own under the text.
    _click(page, "tasklist")
    field("list").press("Enter")
    assert _body(textarea) == "Shopping\n\n[[tasks:]]"
    page.keyboard.press("Control+s")   # Done: to the page
    page.wait_for_url("**/page/fmt")
    expect(page.locator(".tasks-app")).to_have_count(1)


def test_task_list_on_an_embeds_line_changes_that_embed(page, wiki):
    # With the cursor on a [[tasks: …]] the dialog shows its params and
    # writes only what was changed: the rest stays as written, also params
    # the dialog has no field for (pin=, labels=, keep=, new= from before:
    # what the tasks are is set where they are).
    text = "Intro\n\n  [[Tasks: labels=Catalogue,Shopping view=closed pin=default keep=1 new=food]]\n\nOutro"
    textarea = _edit(page, wiki, text, 20)
    _click(page, "tasklist")
    dialog, field = _list_dialog(page)
    expect(dialog.locator(".modal-label")).to_have_text("Change the task list")
    expect(field("list")).to_have_value("")
    expect(field("view")).to_have_value("closed")
    for name in ("keep", "label-open", "new", "limit"):
        expect(field(name)).to_be_hidden()
    dialog.locator(".task-add-field", has_text="Filter by tag").click()
    field("tag").fill("weekly")
    dialog.get_by_role("button", name="Change").click()
    assert _body(textarea) == ("Intro\n\n  [[tasks: labels=Catalogue,Shopping view=closed pin=default"
                               " keep=1 new=food tag=weekly]]\n\nOutro")
    # Emptied, a param goes.
    _click(page, "tasklist")
    field("tag").fill("")
    field("view").select_option("open")
    field("save").click()
    assert _body(textarea) == ("Intro\n\n  [[tasks: labels=Catalogue,Shopping view=open pin=default"
                               " keep=1 new=food]]\n\nOutro")


def test_task_list_on_a_lists_embed_changes_what_is_the_pages(page, wiki):
    # An embed of a list: the dialog has the tab, the grouping and the filter.
    # What the line says of the list's own settings (from before they were
    # the list's) has no field, and stays as written.
    text = "[[tasks: list=shop labels=Catalogue,Shopping view=today keep=1 new=food limit=3]]"
    textarea = _edit(page, wiki, text, 5)
    _click(page, "tasklist")
    dialog, field = _list_dialog(page)
    expect(field("list")).to_have_value("shop")
    expect(field("view")).to_have_value("today")
    for name in ("keep", "label-open", "new", "limit"):
        expect(field(name)).to_be_hidden()
    field("view").select_option("open")
    field("group").select_option("none")
    dialog.locator(".task-add-field", has_text="Filter by tag").click()
    field("tag").fill("weekly")
    dialog.get_by_role("button", name="Change").click()
    assert _body(textarea) == "[[tasks: list=shop labels=Catalogue,Shopping view=open keep=1 new=food limit=3 group=none tag=weekly]]"


def test_inserted_block_keeps_a_blank_line_to_the_text_above(page, wiki):
    # On an empty line right under text the block would join the text's paragraph.
    textarea = _edit(page, wiki, "Text\n", 5)
    _click(page, "table")
    assert _body(textarea).startswith("Text\n\n| Column 1 |")


def test_button_edit_is_auto_saved(page, wiki):
    textarea = _edit(page, wiki, "word", 0, 4)
    _click(page, "bold")
    expect(page.locator("#save-dot")).to_have_class("save-dot unsaved")
    expect(page.locator("#save-dot")).not_to_have_class("save-dot unsaved", timeout=5000)
    assert wiki.read("fmt") == "# Fmt\n\n**word**"
    assert _body(textarea) == "**word**"


def test_attach_uploads_and_inserts_a_link(page, wiki):
    textarea = _edit(page, wiki, "File: ", 6)
    page.locator("#fmt-attach-input").set_input_files(
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"})
    expect(textarea).to_have_value("# Fmt\n\nFile: [notes.txt](/_assets/notes.txt)")
    assert (wiki.root / "_assets" / "notes.txt").read_bytes() == b"hello"


def test_a_refused_upload_says_why(page, wiki, monkeypatch):
    """Over upload-max (413) or with the wiki full (507): the placeholder
    goes and an alert gives the server's message."""
    monkeypatch.setattr(settings, "CONFIG_FILE", "upload-max = 1KB\n")
    textarea = _edit(page, wiki, "File: ", 6)
    with page.expect_event("dialog") as dialog:
        page.locator("#fmt-attach-input").set_input_files(
            {"name": "big.bin", "mimeType": "application/octet-stream", "buffer": b"x" * 2000})
    assert "Too large: at most 1 KB" in dialog.value.message
    dialog.value.accept()
    expect(textarea).to_have_value("# Fmt\n\nFile: ")
    assert not (wiki.root / "_assets" / "big.bin").exists()


def test_attach_to_a_private_page_keeps_the_file_private(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"alice": ("pw", "token")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    wiki.write("private/alice/fmt", "# Fmt\n\n")
    page.goto("/edit/private/alice/fmt")
    page.locator("#fmt-attach-input").set_input_files(
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"})
    expect(page.locator("#editor-textarea")).to_have_value(
        "# Fmt\n\n[notes.txt](/private/alice/_assets/notes.txt)")
    assert (wiki.root / "private/alice/_assets/notes.txt").read_bytes() == b"hello"


def test_phone_bar_is_finger_sized_and_scrolls_sideways(new_page, wiki):
    wiki.write("fmt", "# Fmt\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/edit/fmt")
    bar = phone.locator("#format-bar")
    expect(bar).to_be_visible()
    assert box_of(phone.locator("#format-bar .fmt-btn").first)["height"] >= 40
    # One line that scrolls inside itself, never widening the page.
    assert bar.evaluate("b => b.scrollWidth > b.clientWidth")
    assert phone.evaluate("document.documentElement.scrollWidth") <= 390
    textarea = phone.locator("#editor-textarea")
    textarea.evaluate("ta => ta.setSelectionRange(2, 5)")   # "Fmt"
    phone.locator('#format-bar .fmt-btn[data-fmt="bold"]').tap()
    expect(textarea).to_have_value("# **Fmt**\n")


def test_bar_sits_close_above_the_textarea(page, wiki):
    # Regression: `.editor-pane form`'s padding overrode the smaller gap.
    wiki.write("fmt", "# Fmt\n")
    page.goto("/edit/fmt")
    bar = box_of(page.locator("#format-bar .fmt-btn").first)
    ta = box_of(page.locator("#editor-textarea"))
    assert 0 <= ta["y"] - (bar["y"] + bar["height"]) <= 6


def test_whole_bar_fits_beside_the_preview_on_a_laptop(new_page, wiki):
    # Regression: at 1280px the editor (half the window, the preview beside
    # it) was too narrow for every button; the last ones had to be scrolled to.
    wiki.write("fmt", "# Fmt\n")
    page = new_page(viewport={"width": 1280, "height": 800})
    page.goto("/edit/fmt")
    bar = page.locator("#format-bar")
    assert bar.evaluate("b => b.scrollWidth <= b.clientWidth")
    expect(page.locator('#format-bar [data-fmt="wikilink"]')).to_have_text("[[]]")
