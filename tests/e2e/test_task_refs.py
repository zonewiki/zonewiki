"""Task references in the browser: the chip's checkbox completes the task in
the tasks (with undo), the editor completes `[[task:` from open tasks, and
"Make task" turns a line or selection into a task and its reference."""

import json
import re
from datetime import date, timedelta

from playwright.sync_api import expect

from conftest import make_list
from test_usability import wait_until


def _task(page, **fields):
    return page.request.post("/api/tasks", data=fields).json()


def _tasks_task(wiki, task_id):
    """The task as stored, open or completed (None if neither)."""
    for name in ("tasks.json", "closed.json"):
        path = wiki.root / "_tasks" / name
        if path.exists():
            for t in json.loads(path.read_text()):
                if t["id"] == task_id:
                    return t
    return None


def test_chip_checkbox_completes_and_undo_reopens(page, wiki):
    t = _task(page, title="Call the plumber")
    wiki.write("notes", f"# Notes\n\nToday: [[task:{t['id'][:8]}]]\n")
    page.goto("/page/notes")
    chip = page.locator(f'.task-ref[data-task-id="{t["id"]}"]')
    expect(chip).to_contain_text("Call the plumber")
    chip.locator(".task-ref-check").check()
    expect(page.locator(".task-undo-toast")).to_contain_text("Done “Call the plumber”")
    wait_until(page, lambda: (_tasks_task(wiki, t["id"]) or {}).get("closed_at"))
    expect(page.locator(f'.task-ref-done[data-task-id="{t["id"]}"]')).to_be_visible()

    page.locator(".task-undo-toast").get_by_role("button", name="Undo").click()
    wait_until(page, lambda: not _tasks_task(wiki, t["id"])["closed_at"])
    reopened = page.locator(f'.task-ref[data-task-id="{t["id"]}"]')
    expect(reopened).not_to_have_class("task-ref task-ref-done")
    expect(reopened.locator(".task-ref-check")).not_to_be_checked()
    # Undo leaves no trace in the task's history.
    assert [e["event"] for e in _tasks_task(wiki, t["id"])["history"]] == ["created"]


def test_keep_task_chip_comes_back_and_undo_restores_its_dates(page, wiki):
    today = date.today().isoformat()
    t = _task(page, title="Milk", keep=True, start_date=today)
    wiki.write("notes", f"# Shopping\n\n- [[task:{t['id'][:8]}]]\n")
    page.goto("/page/notes")
    page.locator(".task-ref-check").check()
    expect(page.locator(".task-ref")).to_contain_text("⟲ ✓")      # done today, still open
    expect(page.locator(".task-ref-check")).not_to_be_checked()
    assert _tasks_task(wiki, t["id"])["start_date"] is None
    page.locator(".task-undo-toast").get_by_role("button", name="Undo").click()
    wait_until(page, lambda: _tasks_task(wiki, t["id"])["start_date"] == today)
    assert _tasks_task(wiki, t["id"])["last_done"] is None


def test_ticked_chip_keeps_linking_to_the_tasks_list(page, wiki):
    # A task in a list, on a page that shows another list (of the name of
    # one of its tags). The chip links to the task's list, re-rendered after
    # ticking too.
    for name in ("presentideas", "task"):
        make_list(page, name)
    t = _task(page, title="Lego", keep=True, list="presentideas", tags=["task"])
    wiki.write("todo", f"# Todo\n\n[[tasks: list=task]]\n\n[[task:{t['id'][:8]}]]\n")
    page.goto("/page/todo")
    link = page.locator(".page-content .task-ref-title")
    expect(link).to_have_attribute("href", re.compile(r"list=presentideas$"))
    page.locator(".page-content .task-ref-check").check()
    expect(page.locator(".page-content .task-ref")).to_contain_text("⟲ ✓")   # re-rendered
    expect(link).to_have_attribute("href", re.compile(r"list=presentideas$"))


def test_editor_completes_task_references(page, wiki):
    _task(page, title="Book flights")
    t = _task(page, title="Renew passport", due_date="2030-01-02")
    wiki.write("notes", "# Notes\n\n")
    page.goto("/edit/notes")
    page.keyboard.press("Control+End")
    page.keyboard.type("[[task:pass")
    item = page.locator(".wikilink-ac-item")
    expect(item).to_have_count(1)
    expect(item).to_contain_text("Renew passport")
    expect(item).to_contain_text("⏰ 2030-01-02")
    page.keyboard.press("Enter")
    expect(page.locator("#editor-textarea")).to_have_value(f"# Notes\n\n[[task:{t['id'][:8]}]]")


def test_format_bar_button_starts_a_task_reference(page, wiki):
    t = _task(page, title="Water plants")
    wiki.write("notes", "# Notes\n\n")
    page.goto("/edit/notes")
    page.keyboard.press("Control+End")
    page.locator('#format-bar .fmt-btn[data-fmt="taskref"]').click()
    expect(page.locator(".wikilink-ac-item")).to_contain_text("Water plants")
    page.locator(".wikilink-ac-item", has_text="Water plants").click()
    expect(page.locator("#editor-textarea")).to_have_value(f"# Notes\n\n[[task:{t['id'][:8]}]]")


# ── Make task (from the editor) ──────────────────────────────────────────────

def _open_tasks(wiki):
    path = wiki.root / "_tasks" / "tasks.json"
    return json.loads(path.read_text()) if path.exists() else []


def _edit_with_cursor(page, wiki, text, at, end=None):
    wiki.write("notes", "# Notes\n\n" + text)
    page.goto("/edit/notes")
    off = len("# Notes\n\n")
    page.locator("#editor-textarea").evaluate(
        f"ta => ta.setSelectionRange({off + at}, {off + (at if end is None else end)})")
    page.locator(".tasks-app[data-ready]").wait_for(state="attached")   # the hidden tasks has started


def _save_modal(page):
    page.locator("#task-edit-modal").get_by_role("button", name="Save").click()


def test_make_task_from_the_current_line_opens_the_tasks_modal(page, wiki):
    _edit_with_cursor(page, wiki, "Action items:\n- [ ] Call Bob tomorrow #work\nend", 20)
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    modal = page.locator("#task-edit-modal")
    expect(modal).to_be_visible()
    expect(page.locator("#task-edit-title")).to_have_value("Call Bob tomorrow #work")
    expect(page.locator("#task-edit-title")).to_be_focused()
    expect(page.locator("#task-edit-desc")).to_have_value("From [[notes]]")
    # The modal's quick-add preview reads the shorthand, as in the tasks.
    expect(page.locator("#task-edit-quick")).to_be_visible()
    page.keyboard.press("Enter")                     # Enter saves, as in the tasks
    expect(modal).to_be_hidden()
    expect(page.locator(".task-undo-toast")).to_contain_text("Task “Call Bob” created")
    [t] = _open_tasks(wiki)
    assert t["title"] == "Call Bob" and t["tags"] == ["work"]
    assert t["start_date"] == (date.today() + timedelta(days=1)).isoformat()
    assert t["description"] == "From [[notes]]"
    expect(page.locator("#editor-textarea")).to_have_value(
        f"# Notes\n\nAction items:\n- [[task:{t['id'][:8]}]]\nend")


def test_make_tasks_from_selected_lines_shares_the_modal_fields(page, wiki):
    existing = _task(page, title="Already a task")
    text = (f"## Action items\n- [ ] First thing\n\n1. Second thing tomorrow #home\n"
            f"- [[task:{existing['id'][:8]}]]\nPlain line")
    _edit_with_cursor(page, wiki, text, 0, len(text))
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    batch = page.locator("#task-edit-batch")
    expect(batch).to_be_visible()
    expect(page.locator("#task-edit-title")).to_be_hidden()
    expect(page.locator("#task-edit-batch-label")).to_have_text("3 tasks")
    expect(page.locator("#task-edit-batch-list li")).to_have_count(3)
    expect(page.locator("#task-edit-batch-list li").nth(1)).to_contain_text("Second thing")
    expect(page.locator("#task-edit-batch-list li").nth(1)).to_contain_text("#home")
    expect(page.locator("#task-edit-tags")).to_be_focused()
    page.keyboard.type("work,")                   # one tag for all of them
    _save_modal(page)
    expect(page.locator(".task-undo-toast")).to_contain_text("3 tasks created")
    by_title = {t["title"]: t for t in _open_tasks(wiki)}
    assert set(by_title) == {"Already a task", "First thing", "Second thing", "Plain line"}
    assert by_title["First thing"]["tags"] == ["work"]
    assert by_title["Plain line"]["tags"] == ["work"]
    assert sorted(by_title["Second thing"]["tags"]) == ["home", "work"]
    assert by_title["Second thing"]["start_date"] == (date.today() + timedelta(days=1)).isoformat()
    assert by_title["First thing"]["start_date"] is None
    assert all(by_title[t]["description"] == "From [[notes]]" for t in ("First thing", "Plain line"))
    ref = lambda title: f"[[task:{by_title[title]['id'][:8]}]]"
    expect(page.locator("#editor-textarea")).to_have_value(
        f"# Notes\n\n## Action items\n- {ref('First thing')}\n\n1. {ref('Second thing')}\n"
        f"- [[task:{existing['id'][:8]}]]\n{ref('Plain line')}")


def test_batch_modal_then_single_modal_shows_the_title_again(page, wiki):
    text = "one\ntwo"
    _edit_with_cursor(page, wiki, text, 0, len(text))
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    expect(page.locator("#task-edit-batch")).to_be_visible()
    page.keyboard.press("Escape")
    assert _open_tasks(wiki) == []
    expect(page.locator("#editor-textarea")).to_have_value("# Notes\n\none\ntwo")
    page.locator("#editor-textarea").evaluate("ta => ta.setSelectionRange(ta.value.length, ta.value.length)")
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    expect(page.locator("#task-edit-batch")).to_be_hidden()
    expect(page.locator("#task-edit-title")).to_have_value("two")


def test_make_task_from_a_selection_inside_a_sentence(page, wiki):
    _edit_with_cursor(page, wiki, "We agreed: send the slides. Done.", 11, 27)
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    expect(page.locator("#task-edit-title")).to_have_value("send the slides.")
    _save_modal(page)
    expect(page.locator(".task-undo-toast")).to_be_visible()
    [t] = _open_tasks(wiki)
    assert t["title"] == "send the slides."
    expect(page.locator("#editor-textarea")).to_have_value(
        f"# Notes\n\nWe agreed: [[task:{t['id'][:8]}]] Done.")


def test_make_task_undo_deletes_the_task_and_restores_the_text(page, wiki):
    _edit_with_cursor(page, wiki, "- [ ] Water plants", 3)
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    _save_modal(page)
    expect(page.locator(".task-undo-toast")).to_be_visible()
    assert len(_open_tasks(wiki)) == 1
    page.locator(".task-undo-toast").get_by_role("button", name="Undo").click()
    expect(page.locator("#editor-textarea")).to_have_value("# Notes\n\n- [ ] Water plants")
    wait_until(page, lambda: _open_tasks(wiki) == [])


def test_made_task_shows_as_a_chip_after_done(page, wiki):
    _edit_with_cursor(page, wiki, "- [ ] Renew passport", 3)
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    _save_modal(page)
    expect(page.locator(".task-undo-toast")).to_be_visible()
    page.keyboard.press("Control+s")
    expect(page).to_have_url(re.compile(r"/page/notes$"))
    expect(page.locator(".task-ref-item .task-ref")).to_contain_text("Renew passport")
    expect(page.locator("#task-backlinks")).to_have_count(0)   # shown inline, not repeated


def test_make_task_modal_takes_more_fields_and_cancel_changes_nothing(page, wiki):
    _edit_with_cursor(page, wiki, "- [ ] Water plants", 3)
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    page.keyboard.press("Escape")
    expect(page.locator("#task-edit-modal")).to_be_hidden()
    expect(page.locator("#editor-textarea")).to_have_value("# Notes\n\n- [ ] Water plants")
    assert _open_tasks(wiki) == []
    # Again, this time with a field only the modal has.
    page.locator('.fmt-btn[data-fmt="maketask"]').click()
    page.locator("#task-edit-title").fill("Water plants today")
    page.locator("#task-edit-more-keep").click()        # shows the box, ticked
    expect(page.locator("#task-edit-keep")).to_be_checked()
    _save_modal(page)
    expect(page.locator(".task-undo-toast")).to_be_visible()
    [t] = _open_tasks(wiki)
    assert t["title"] == "Water plants" and t["keep"] is True
    assert t["start_date"] == date.today().isoformat()
    expect(page.locator("#editor-textarea")).to_have_value(f"# Notes\n\n- [[task:{t['id'][:8]}]]")


def test_hidden_tasks_does_not_take_the_editors_keys(page, wiki):
    wiki.write("notes", "# Notes\n\n")
    page.goto("/edit/notes")
    page.locator(".tasks-app[data-ready]").wait_for(state="attached")
    page.keyboard.press("Control+End")
    page.keyboard.type("n/#")   # the task app's new task / filter keys
    expect(page.locator("#task-edit-modal")).to_be_hidden()
    expect(page.locator("#editor-textarea")).to_have_value("# Notes\n\nn/#")
    expect(page.locator(".tasks-app")).to_be_hidden()
