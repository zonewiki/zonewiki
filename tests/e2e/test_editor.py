"""Editor: auto-save, the unsaved indicator, Done, and save-on-navigate."""

import time

from playwright.sync_api import expect

import server


def test_autosave_writes_file_without_commit(page, wiki):
    wiki.write("notes", "# Notes\n\n")
    wiki.git("add", "-A")
    wiki.git("commit", "-qm", "seed")
    head = wiki.head()

    page.goto("/edit/notes")
    textarea = page.locator("#editor-textarea")
    expect(textarea).to_be_focused()
    dot = page.locator("#save-dot")
    expect(dot).not_to_have_class("save-dot unsaved")

    page.keyboard.press("Control+End")
    page.keyboard.type("typed in the browser")
    expect(dot).to_have_class("save-dot unsaved")

    # Debounced ~2s after the last keystroke, then the dot clears.
    expect(dot).not_to_have_class("save-dot unsaved", timeout=5000)
    assert wiki.read("notes").endswith("typed in the browser")
    assert wiki.head() == head, "auto-save must not commit"


def test_live_preview_renders_markdown(page, wiki):
    page.goto("/edit/preview-me")
    page.keyboard.press("Control+End")
    page.keyboard.type("**bold words**")
    preview = page.locator("#editor-preview")
    expect(preview.locator("strong")).to_have_text("bold words")
    page.keyboard.press("F2")
    expect(preview).to_be_hidden()
    page.keyboard.press("F2")
    expect(preview).to_be_visible()


def test_done_commits_and_shows_page(page, wiki):
    page.goto("/edit/fresh")
    page.keyboard.press("Control+End")
    page.keyboard.type("hello from done")
    page.get_by_role("button", name="Done").click()
    page.wait_for_url("**/page/fresh")
    expect(page.locator("article.page-content")).to_contain_text("hello from done")
    assert wiki.git("log", "-1", "--format=%s").strip() == "Update fresh"


def test_ctrl_s_is_done(page, wiki):
    page.goto("/edit/keyed")
    page.keyboard.press("Control+End")
    page.keyboard.type("saved by shortcut")
    page.keyboard.press("Control+s")
    page.wait_for_url("**/page/keyed")
    assert "saved by shortcut" in wiki.read("keyed")


def test_navigating_away_saves_pending_edit(page, wiki):
    wiki.write("draft", "# Draft\n\n")
    page.goto("/edit/draft")
    page.keyboard.press("Control+End")
    page.keyboard.type("not yet auto-saved")
    # Leave well inside the 2s debounce: the pagehide beacon must catch it.
    page.goto("/page/index")
    page.wait_for_timeout(300)
    assert wiki.read("draft").endswith("not yet auto-saved")


def _slow_saves(monkeypatch, delay=2.5):
    """Auto-saves write the file at once but answer *delay* seconds later,
    as over a slow connection."""
    orig = server.app.view_functions["api_save"]

    def slow(*args, **kwargs):
        res = orig(*args, **kwargs)
        time.sleep(delay)
        return res
    monkeypatch.setitem(server.app.view_functions, "api_save", slow)


def test_overlapping_autosaves_are_no_conflict(page, wiki, monkeypatch):
    # The second auto-save starts while the first one's answer is still out.
    # It used to send the hash from before the first save and be refused:
    # "File was modified on disk while you were editing."
    wiki.write("notes", "# Notes\n\n")
    page.goto("/edit/notes")
    _slow_saves(monkeypatch)
    page.keyboard.press("Control+End")
    page.keyboard.type("one")
    page.wait_for_timeout(2300)
    page.keyboard.type(" two")
    expect(page.locator("#save-dot")).not_to_have_class("save-dot unsaved", timeout=10000)
    expect(page.locator(".conflict-banner")).to_have_count(0)
    assert wiki.read("notes").endswith("one two")


def test_done_waits_for_autosave_in_flight(page, wiki, monkeypatch):
    wiki.write("notes", "# Notes\n\n")
    page.goto("/edit/notes")
    _slow_saves(monkeypatch)
    page.keyboard.press("Control+End")
    page.keyboard.type("one")
    page.wait_for_timeout(2300)
    page.keyboard.type(" two")
    page.keyboard.press("Control+s")
    page.wait_for_url("**/page/notes", timeout=10000)
    assert wiki.read("notes").endswith("one two")
    assert wiki.git("log", "-1", "--format=%s").strip() == "Update notes"


def test_list_continuation(page, wiki):
    page.goto("/edit/lists")
    page.keyboard.press("Control+End")
    page.keyboard.type("- one")
    page.keyboard.press("Enter")
    page.keyboard.type("two")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")  # empty item exits the list
    page.keyboard.type("after")
    expect(page.locator("#editor-textarea")).to_have_value("# Lists\n\n- one\n- two\n\nafter")


# Prose brackets, a fenced example and a second bracket on a task line: the
# old client/server counting disagreed with the rendered checkboxes on all of
# these, so a click toggled the wrong line.
TRICKY_TASKS = (
    "# Tasks\n\n"
    "Press [x] to close.\n\n"
    "```\n- [ ] not a real task\n```\n\n"
    "- [ ] first [x] note\n"
    "- [ ] second\n"
)


def test_preview_checkbox_toggles_matching_line(page, wiki):
    wiki.write("tasks", TRICKY_TASKS)
    page.goto("/edit/tasks")
    boxes = page.locator("#editor-preview .task-list-item-checkbox")
    expect(boxes).to_have_count(2)
    boxes.nth(1).click()
    textarea = page.locator("#editor-textarea")
    expect(textarea).to_have_value(TRICKY_TASKS.replace("- [ ] second", "- [x] second"))
    # A real edit: marks unsaved, auto-saves, and is undoable.
    expect(page.locator("#save-dot")).to_have_class("save-dot unsaved")
    expect(page.locator("#save-dot")).not_to_have_class("save-dot unsaved", timeout=5000)
    assert "- [x] second" in wiki.read("tasks")
    textarea.focus()
    page.keyboard.press("Control+z")
    expect(textarea).to_have_value(TRICKY_TASKS)


def test_page_checkbox_click_persists_matching_line(page, wiki):
    wiki.write("tasks", TRICKY_TASKS)
    page.goto("/page/tasks")
    boxes = page.locator("article .task-list-item-checkbox")
    expect(boxes).to_have_count(2)
    with page.expect_response("**/api/toggle-checkbox/tasks"):
        boxes.nth(0).click()
    content = wiki.read("tasks")
    assert "Press [x] to close." in content
    assert "- [ ] not a real task" in content
    assert "- [x] first [x] note" in content
    assert "- [ ] second" in content


# Pasting a URL makes a markdown link, and Ctrl+Z undoes it (regression: the
# insert assigned textarea.value, which wiped the browser's undo history).

PASTE = """(url) => {
    const dt = new DataTransfer();
    dt.setData('text/plain', url);
    document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}"""


def test_url_paste_over_selection_wraps_it_and_undoes(page, wiki):
    wiki.write("links", "# Links\n\n")
    page.goto("/edit/links")
    textarea = page.locator("#editor-textarea")
    page.keyboard.press("Control+End")
    page.keyboard.type("see docs")
    before = textarea.input_value()
    textarea.evaluate("ta => ta.setSelectionRange(ta.value.length - 4, ta.value.length)")
    page.evaluate(PASTE, "https://example.com/d")
    expect(textarea).to_have_value(before[:-4] + "[docs](https://example.com/d)")
    page.keyboard.press("Control+z")
    expect(textarea).to_have_value(before)
    # The typing before the paste is still on the undo stack too.
    page.keyboard.press("Control+z")
    expect(textarea).not_to_have_value(before)


def test_url_paste_asks_for_a_name_and_undoes(page, wiki):
    wiki.write("links", "# Links\n\n")
    page.goto("/edit/links")
    textarea = page.locator("#editor-textarea")
    page.keyboard.press("Control+End")
    before = textarea.input_value()
    page.evaluate(PASTE, "https://example.com")
    name = page.locator("#url-name-input")
    expect(name).to_be_focused()
    name.fill("Example")
    name.press("Enter")
    expect(textarea).to_have_value(before + "[Example](https://example.com)")
    expect(textarea).to_be_focused()
    page.keyboard.press("Control+z")
    expect(textarea).to_have_value(before)


def test_url_paste_in_task_notes_undoes(page, wiki):
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    page.goto("/page/deck?view=open")
    page.locator("#task-add-btn").click()
    notes = page.locator("#task-edit-desc")
    notes.click()
    notes.press_sequentially("read ")
    page.evaluate(PASTE, "https://example.com")
    page.locator("#url-name-input").fill("this")
    page.locator("#url-name-input").press("Enter")
    expect(notes).to_have_value("read [this](https://example.com)")
    expect(page.locator("#task-edit-modal")).to_be_visible()
    page.keyboard.press("Control+z")
    expect(notes).to_have_value("read ")


def test_preview_grows_on_very_wide_screens(new_page, wiki):
    # From 1800px the preview may be 1100px wide (the writing pane stays
    # 800px); its text keeps the reading width, a wide table uses the rest.
    wide_table = "| " + " | ".join(f"Column {i} with a long header" for i in range(8)) + " |\n|" + "---|" * 8 + "\n"
    wiki.write("wide", "# Wide\n\n" + "Words to fill a line. " * 30 + "\n\n" + wide_table)
    page = new_page(viewport={"width": 2200, "height": 900})
    page.goto("/edit/wide")
    preview = page.locator("#editor-preview")
    expect(preview.locator("table")).to_be_visible()
    assert page.locator(".preview-pane").bounding_box()["width"] == 1100
    assert page.locator(".editor-pane").bounding_box()["width"] == 800
    text = preview.locator("p").first.bounding_box()
    table = preview.locator("table").bounding_box()
    assert abs(text["width"] - 46 * 16) < 1 and table["width"] > text["width"] + 100
    # Below 1800px: both panes as before.
    page.set_viewport_size({"width": 1700, "height": 900})
    assert page.locator(".preview-pane").bounding_box()["width"] <= 800


def test_date_snippets_use_local_time(new_page, wiki):
    # Regression: /date and /datetime took UTC's day and time
    # (toISOString), so shortly after midnight in Europe /date said
    # yesterday, and /datetime disagreed with /time.
    wiki.write("notes", "# Notes\n\n")
    page = new_page(timezone_id="Pacific/Kiritimati")   # UTC+14
    page.clock.set_fixed_time("2026-03-22T23:30:00Z")  # there: 2026-03-23 13:30
    page.goto("/edit/notes")
    textarea = page.locator("#editor-textarea")
    textarea.press("Control+End")
    for snippet in ("/date", " /datetime", " /time"):
        page.keyboard.type(snippet)
        page.keyboard.press("Tab")
    expect(textarea).to_have_value("# Notes\n\n2026-03-23 2026-03-23 13:30 13:30")


def test_task_notes_complete_page_links(page, wiki):
    # The deck's notes and title complete [[ and [text]( like the editor
    # (one implementation, linkAutocomplete in leaf.js).
    wiki.write("deck", "# Deck\n\n[[deck]]\n")
    wiki.write("target-page", "# Target\n")
    wiki.write("tangent", "# Tangent\n")
    page.goto("/page/deck?view=open")
    page.locator("#task-add-btn").click()
    notes = page.locator("#task-edit-desc")
    notes.click()
    page.keyboard.type("see [[ta")
    items = page.locator("#task-edit-modal .wikilink-ac-item")
    expect(items).to_have_text(["tangent", "target-page"])
    page.keyboard.press("ArrowDown")
    page.keyboard.type("r")          # the list shrinks under the highlight
    expect(items).to_have_text(["target-page"])
    page.keyboard.press("Enter")     # still picks it, no new line
    expect(notes).to_have_value("see [[target-page]]")
    page.keyboard.type(" or [docs](tan")
    expect(items).to_have_text(["tangent"])
    page.keyboard.press("Tab")
    expect(notes).to_have_value("see [[target-page]] or [docs](/page/tangent")
    expect(page.locator("#task-edit-modal")).to_be_visible()
