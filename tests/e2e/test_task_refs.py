"""Task references in the browser: the chip's checkbox completes the task in
the deck (with undo), and the editor completes `[[task:` from open tasks."""

import json
from datetime import date

from playwright.sync_api import expect

from test_usability import wait_until


def _task(page, **fields):
    return page.request.post("/api/deck", data=fields).json()


def _deck_task(wiki, task_id):
    """The task as stored, open or completed (None if neither)."""
    for name in ("_deck.json", "_deck_completed.json"):
        path = wiki.root / name
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
    wait_until(page, lambda: (_deck_task(wiki, t["id"]) or {}).get("completed_at"))
    expect(page.locator(f'.task-ref-done[data-task-id="{t["id"]}"]')).to_be_visible()

    page.locator(".task-undo-toast").get_by_role("button", name="Undo").click()
    wait_until(page, lambda: not _deck_task(wiki, t["id"])["completed_at"])
    reopened = page.locator(f'.task-ref[data-task-id="{t["id"]}"]')
    expect(reopened).not_to_have_class("task-ref task-ref-done")
    expect(reopened.locator(".task-ref-check")).not_to_be_checked()
    # Undo leaves no trace in the task's history.
    assert [e["event"] for e in _deck_task(wiki, t["id"])["history"]] == ["created"]


def test_keep_task_chip_comes_back_and_undo_restores_its_dates(page, wiki):
    today = date.today().isoformat()
    t = _task(page, title="Milk", keep=True, start_date=today)
    wiki.write("notes", f"# Shopping\n\n- [[task:{t['id'][:8]}]]\n")
    page.goto("/page/notes")
    page.locator(".task-ref-check").check()
    expect(page.locator(".task-ref")).to_contain_text("⟲ ✓")      # done today, still open
    expect(page.locator(".task-ref-check")).not_to_be_checked()
    assert _deck_task(wiki, t["id"])["start_date"] is None
    page.locator(".task-undo-toast").get_by_role("button", name="Undo").click()
    wait_until(page, lambda: _deck_task(wiki, t["id"])["start_date"] == today)
    assert _deck_task(wiki, t["id"])["last_done"] is None


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
    page.locator('.fmt-btn[data-fmt="taskref"]').click()
    expect(page.locator(".wikilink-ac-item")).to_contain_text("Water plants")
    page.locator(".wikilink-ac-item", has_text="Water plants").click()
    expect(page.locator("#editor-textarea")).to_have_value(f"# Notes\n\n[[task:{t['id'][:8]}]]")
