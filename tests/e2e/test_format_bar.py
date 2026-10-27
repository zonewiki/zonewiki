"""Editor formatting bar: buttons and Ctrl+B/I/K turn the selection into markdown."""

from playwright.sync_api import expect

from conftest import TOUCH_PHONE
from tests import settings


def _edit(page, wiki, text, sel_start, sel_end=None):
    """Open an editor on `text` (after the H1) with that part selected."""
    wiki.write("fmt", "# Fmt\n\n" + text)
    page.goto("/edit/fmt")
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


def test_attach_to_a_private_page_keeps_the_file_private(page, wiki, monkeypatch):
    monkeypatch.setattr(settings, "LEAF_USERS", {"alice": ("pw", "token")})
    page.request.post("/login", form={"username": "alice", "password": "pw"})
    wiki.write("private/alice/fmt", "# Fmt\n\n")
    page.goto("/edit/private/alice/fmt")
    page.locator("#fmt-attach-input").set_input_files(
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"})
    expect(page.locator("#editor-textarea")).to_have_value(
        "# Fmt\n\n[notes.txt](/_assets/private/alice/notes.txt)")
    assert (wiki.root / "_assets/private/alice/notes.txt").read_bytes() == b"hello"


def test_phone_bar_is_finger_sized_and_scrolls_sideways(new_page, wiki):
    wiki.write("fmt", "# Fmt\n")
    phone = new_page(**TOUCH_PHONE)
    phone.goto("/edit/fmt")
    bar = phone.locator("#format-bar")
    expect(bar).to_be_visible()
    assert phone.locator("#format-bar .fmt-btn").first.bounding_box()["height"] >= 40
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
    bar = page.locator("#format-bar .fmt-btn").first.bounding_box()
    ta = page.locator("#editor-textarea").bounding_box()
    assert 0 <= ta["y"] - (bar["y"] + bar["height"]) <= 6
