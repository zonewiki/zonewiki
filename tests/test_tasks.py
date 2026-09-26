"""Comprehensive tests for the Leaf task app API."""

import sys
import os
import json
import tempfile
import threading
from pathlib import Path
from datetime import date, timedelta

import pytest

_SESSION_TMP = tempfile.mkdtemp(prefix="leaf_tasks_test_")
os.environ.setdefault("WIKI_DATA", _SESSION_TMP)
os.environ["ALLOW_REMOTE"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import server
import deck as deck_module


@pytest.fixture()
def wiki_dir(tmp_path):
    orig_wiki = server.WIKI_DIR
    orig_pages = server.PAGES_DIR
    orig_deck = deck_module.DECK_FILE
    server.WIKI_DIR = tmp_path
    server.PAGES_DIR = tmp_path
    deck_module.DECK_FILE = tmp_path / "_deck.json"
    server.DECK_FILE = tmp_path / "_deck.json"
    server._page_meta_cache.clear()
    deck_module._task_docs_cache["key"] = None
    deck_module._task_docs_cache["docs"] = []
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    yield tmp_path
    server.WIKI_DIR = orig_wiki
    server.PAGES_DIR = orig_pages
    deck_module.DECK_FILE = orig_deck
    server.DECK_FILE = orig_deck


@pytest.fixture()
def client(wiki_dir):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def create_task(client, **kwargs):
    """Helper to create a task and return the response JSON."""
    payload = {"title": "Test task", **kwargs}
    resp = client.post("/api/deck", json=payload)
    return resp


# ---------- CRUD Basics ----------


class TestCRUD:
    def test_create_task(self, client):
        resp = create_task(client, title="Buy milk")
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "Buy milk"
        assert data["id"]
        assert data["completed_at"] is None
        assert data["sort_order"] is None

    def test_read_tasks_empty(self, client):
        resp = client.get("/api/deck")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_read_tasks_after_create(self, client):
        create_task(client, title="Task A")
        create_task(client, title="Task B")
        resp = client.get("/api/deck")
        tasks = resp.get_json()
        assert len(tasks) == 2
        titles = {t["title"] for t in tasks}
        assert titles == {"Task A", "Task B"}

    def test_update_task(self, client):
        task = create_task(client, title="Old title").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"title": "New title"})
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "New title"

    def test_update_description_and_tags(self, client):
        task = create_task(client, title="Tagged").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={
            "description": "Some notes",
            "tags": ["work", "urgent"],
        })
        data = resp.get_json()
        assert data["description"] == "Some notes"
        assert data["tags"] == ["work", "urgent"]

    def test_delete_task(self, client):
        task = create_task(client, title="To delete").get_json()
        resp = client.delete(f"/api/deck/{task['id']}")
        assert resp.status_code == 204
        remaining = client.get("/api/deck").get_json()
        assert len(remaining) == 0

    def test_delete_nonexistent_task(self, client):
        resp = client.delete("/api/deck/nonexistent-id")
        assert resp.status_code == 204

    def test_update_nonexistent_task(self, client):
        resp = client.put("/api/deck/nonexistent-id", json={"title": "Nope"})
        assert resp.status_code == 404


# ---------- Validation ----------


class TestValidation:
    def test_create_empty_title(self, client):
        resp = client.post("/api/deck", json={"title": ""})
        assert resp.status_code == 400

    def test_create_whitespace_title(self, client):
        resp = client.post("/api/deck", json={"title": "   "})
        assert resp.status_code == 400

    def test_create_missing_title(self, client):
        resp = client.post("/api/deck", json={})
        assert resp.status_code == 400

    def test_update_empty_title(self, client):
        task = create_task(client, title="Valid").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"title": ""})
        assert resp.status_code == 400

    def test_create_invalid_due_date(self, client):
        resp = client.post("/api/deck", json={"title": "Bad date", "due_date": "not-a-date"})
        assert resp.status_code == 400

    def test_update_invalid_due_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"due_date": "31-12-2025"})
        assert resp.status_code == 400

    def test_create_valid_due_date(self, client):
        resp = create_task(client, title="Dated", due_date="2025-06-15")
        assert resp.status_code == 201
        assert resp.get_json()["due_date"] == "2025-06-15"

    def test_null_date_clears_field(self, client):
        task = create_task(client, title="Has date", due_date="2025-06-15").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"due_date": None})
        assert resp.get_json()["due_date"] is None

    def test_empty_string_date_clears_field(self, client):
        task = create_task(client, title="Has date", due_date="2025-06-15").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"due_date": ""})
        assert resp.get_json()["due_date"] is None


# ---------- Batch Delete ----------


class TestBatchDelete:
    def test_batch_delete_multiple(self, client):
        t1 = create_task(client, title="A").get_json()
        t2 = create_task(client, title="B").get_json()
        t3 = create_task(client, title="C").get_json()
        resp = client.post("/api/deck/batch-delete", json={"ids": [t1["id"], t3["id"]]})
        assert resp.status_code == 204
        remaining = client.get("/api/deck").get_json()
        assert len(remaining) == 1
        assert remaining[0]["id"] == t2["id"]

    def test_batch_delete_empty_ids(self, client):
        resp = client.post("/api/deck/batch-delete", json={"ids": []})
        assert resp.status_code == 400

    def test_batch_delete_missing_ids(self, client):
        resp = client.post("/api/deck/batch-delete", json={})
        assert resp.status_code == 400

    def test_batch_delete_nonexistent_ids(self, client):
        create_task(client, title="Keep me")
        resp = client.post("/api/deck/batch-delete", json={"ids": ["no-such-id"]})
        assert resp.status_code == 204
        remaining = client.get("/api/deck").get_json()
        assert len(remaining) == 1


# ---------- Completion Toggle ----------


class TestCompletion:
    def test_complete_task(self, client):
        task = create_task(client, title="Finish").get_json()
        assert task["completed_at"] is None
        resp = client.put(f"/api/deck/{task['id']}", json={"completed": True})
        data = resp.get_json()
        assert data["completed_at"] is not None

    def test_uncomplete_task(self, client):
        task = create_task(client, title="Undo").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        resp = client.put(f"/api/deck/{task['id']}", json={"completed": False})
        data = resp.get_json()
        assert data["completed_at"] is None

    def test_complete_already_completed_noop(self, client):
        task = create_task(client, title="Already done").get_json()
        resp1 = client.put(f"/api/deck/{task['id']}", json={"completed": True})
        ts1 = resp1.get_json()["completed_at"]
        resp2 = client.put(f"/api/deck/{task['id']}", json={"completed": True})
        ts2 = resp2.get_json()["completed_at"]
        assert ts1 == ts2


# ---------- Recurring Tasks ----------


def _future(days):
    """Future date string — recurrence tests use future due dates so the
    catch-up rule (next due >= today) does not shift expectations."""
    return (date.today() + timedelta(days=days)).isoformat()


class TestRecurring:
    def test_complete_recurring_spawns_new(self, client):
        task = create_task(client, title="Daily standup",
                           due_date=_future(10),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["title"] == "Daily standup"
        assert spawned["due_date"] == _future(11)
        assert spawned["completed_at"] is None

    def test_daily_recurrence_interval(self, client):
        task = create_task(client, title="Every 3 days",
                           due_date=_future(10),
                           rec_type="daily", rec_interval=3).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == _future(13)

    def test_weekly_recurrence(self, client):
        task = create_task(client, title="Weekly review",
                           due_date=_future(3),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == _future(10)

    def test_weekly_recurrence_interval(self, client):
        task = create_task(client, title="Biweekly",
                           due_date=_future(3),
                           rec_type="weekly", rec_interval=2).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == _future(17)

    def test_monthly_recurrence(self, client):
        task = create_task(client, title="Monthly report",
                           due_date="2099-01-15",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2099-02-15"

    def test_monthly_recurrence_day_clamped(self, client):
        # Jan 31 clamps to the real end of February
        task = create_task(client, title="End of month",
                           due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2099-02-28"

    def test_monthly_recurrence_leap_year(self, client):
        task = create_task(client, title="End of month",
                           due_date="2096-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2096-02-29"

    def test_monthly_recurrence_keeps_month_end_day(self, client):
        # The clamp is per-step, not sticky-forward: Mar 31 → Apr 30
        task = create_task(client, title="End of month",
                           due_date="2099-03-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2099-04-30"

    def _complete_and_get_spawn(self, client, task_id):
        client.put(f"/api/deck/{task_id}", json={"completed": True})
        return next(t for t in client.get("/api/deck").get_json()
                    if t.get("spawned_from") == task_id)

    def test_monthly_month_end_does_not_drift(self, client):
        # Regression: each step used the previous (clamped) due date as its
        # base, so Jan 31 → Feb 28 → Mar 28 → Apr 28… drifted forever.
        task = create_task(client, title="Month end", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        dues = []
        for _ in range(3):
            task = self._complete_and_get_spawn(client, task["id"])
            dues.append(task["due_date"])
        assert dues == ["2099-02-28", "2099-03-31", "2099-04-30"]

    def test_monthly_anchor_day_survives_edit_modal_save(self, client):
        # The edit modal re-sends rec_type/rec_interval with the unchanged due
        # date; that must not re-anchor the series to the clamped day.
        task = create_task(client, title="Month end", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        client.put(f"/api/deck/{spawned['id']}", json={
            "title": "Month end", "due_date": "2099-02-28",
            "rec_type": "monthly", "rec_interval": 1})
        nxt = self._complete_and_get_spawn(client, spawned["id"])
        assert nxt["due_date"] == "2099-03-31"

    def test_monthly_anchor_kept_when_only_plan_date_changes(self, client):
        task = create_task(client, title="Month end", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        client.put(f"/api/deck/{spawned['id']}", json={
            "due_date": "2099-02-28", "focus_date": "2099-02-20",
            "rec_type": "monthly", "rec_interval": 1})
        assert self._complete_and_get_spawn(client, spawned["id"])["due_date"] == "2099-03-31"

    def test_monthly_anchor_resets_when_due_date_moved(self, client):
        task = create_task(client, title="Rent", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={
            "due_date": "2099-01-15", "rec_type": "monthly", "rec_interval": 1})
        nxt = self._complete_and_get_spawn(client, task["id"])
        assert nxt["due_date"] == "2099-02-15"

    def test_yearly_leap_day_returns_in_leap_years(self, client):
        task = create_task(client, title="Leap", due_date="2092-02-29",
                           rec_type="yearly", rec_interval=1).get_json()
        dues = []
        for _ in range(4):
            task = self._complete_and_get_spawn(client, task["id"])
            dues.append(task["due_date"])
        assert dues == ["2093-02-28", "2094-02-28", "2095-02-28", "2096-02-29"]

    def test_plan_anchored_monthly_does_not_drift(self, client):
        task = create_task(client, title="Plan end", focus_date="2099-01-31",
                           rec_type="monthly", rec_interval=1, rec_anchor="plan").get_json()
        plans = []
        for _ in range(2):
            task = self._complete_and_get_spawn(client, task["id"])
            plans.append(task["focus_date"])
        assert plans == ["2099-02-28", "2099-03-31"]

    def test_spawned_task_preserves_tags(self, client):
        task = create_task(client, title="Tagged recurring",
                           due_date=_future(10),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"tags": ["work", "standup"]})
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["tags"] == ["work", "standup"]

    def test_spawned_task_preserves_recurrence(self, client):
        task = create_task(client, title="Recurring",
                           due_date=_future(10),
                           rec_type="weekly", rec_interval=2).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["recurrence"] == {"type": "weekly", "interval": 2, "from": "due", "anchor": "due"}

    def test_complete_non_recurring_no_spawn(self, client):
        task = create_task(client, title="One-off").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 0

    def test_plan_anchor_steps_focus_date(self, client):
        task = create_task(client, title="Weekly plan",
                           focus_date=_future(7),
                           rec_type="weekly", rec_interval=1,
                           rec_anchor="plan").get_json()
        assert task["recurrence"]["anchor"] == "plan"
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        active = client.get("/api/deck").get_json()
        assert len(active) == 1
        spawned = active[0]
        assert spawned["focus_date"] == _future(14)
        assert spawned["due_date"] is None

    def test_plan_anchor_from_completion(self, client):
        # from_completion + anchor=plan: step focus_date from today
        task = create_task(client, title="Interval habit",
                           focus_date=_future(7),
                           rec_type="weekly", rec_interval=1,
                           rec_anchor="plan", rec_from="completion").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        active = client.get("/api/deck").get_json()
        spawned = active[0]
        assert spawned["focus_date"] == _future(7)
        assert spawned["due_date"] is None

    def test_plan_anchor_does_not_carry_due_date(self, client):
        # Even if the task had a due_date, anchor=plan spawns without one
        task = create_task(client, title="Plan-anchored",
                           due_date=_future(10), focus_date=_future(7),
                           rec_type="weekly", rec_interval=1,
                           rec_anchor="plan").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        active = client.get("/api/deck").get_json()
        spawned = active[0]
        assert spawned["focus_date"] == _future(14)
        assert spawned["due_date"] is None

    def test_plan_anchor_overdue_plan_catches_up_to_today(self, client):
        # A long-stale plan date must not spawn an instance planned in the past.
        task = create_task(client, title="Stale weekly",
                           focus_date=(date.today() - timedelta(days=30)).isoformat(),
                           rec_type="weekly", rec_interval=1,
                           rec_anchor="plan").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        planned = date.fromisoformat(spawned["focus_date"])
        assert date.today() <= planned < date.today() + timedelta(days=7)
        # Still on the original weekday cadence.
        assert (planned - date.fromisoformat(task["focus_date"])).days % 7 == 0

    def test_due_anchor_default(self, client):
        # No rec_anchor → defaults to "due" behavior
        task = create_task(client, title="Default anchor",
                           due_date=_future(10),
                           rec_type="daily", rec_interval=1).get_json()
        assert task["recurrence"]["anchor"] == "due"
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        active = client.get("/api/deck").get_json()
        spawned = active[0]
        assert spawned["due_date"] == _future(11)


# ---------- Won't Do ----------


class TestWontDo:
    def test_create_default_wont_do_false(self, client):
        assert create_task(client, title="T").get_json()["wont_do"] is False

    def test_mark_wont_do_sets_flag_and_timestamp(self, client):
        task = create_task(client, title="Drop me").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"wont_do": True})
        data = resp.get_json()
        assert data["wont_do"] is True
        assert data["completed_at"] is not None

    def test_mark_wont_do_removes_from_active(self, client):
        task = create_task(client, title="Drop me").get_json()
        client.put(f"/api/deck/{task['id']}", json={"wont_do": True})
        assert client.get("/api/deck").get_json() == []

    def test_wont_do_appears_in_completed(self, client):
        task = create_task(client, title="Drop me").get_json()
        client.put(f"/api/deck/{task['id']}", json={"wont_do": True})
        completed = client.get("/api/deck/completed").get_json()
        assert len(completed) == 1
        assert completed[0]["id"] == task["id"]
        assert completed[0]["wont_do"] is True

    def test_wont_do_does_not_spawn_recurring(self, client):
        task = create_task(client, title="Repeating",
                           due_date="2025-03-01",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"wont_do": True})
        # No next instance should be created (unlike completion)
        assert client.get("/api/deck").get_json() == []

    def test_reopen_wont_do_restores_to_active(self, client):
        task = create_task(client, title="Bring back").get_json()
        client.put(f"/api/deck/{task['id']}", json={"wont_do": True})
        resp = client.put(f"/api/deck/{task['id']}", json={"wont_do": False})
        data = resp.get_json()
        assert data["wont_do"] is False
        assert data["completed_at"] is None
        active = client.get("/api/deck").get_json()
        assert len(active) == 1
        assert active[0]["id"] == task["id"]

    def test_completing_clears_wont_do_flag(self, client):
        """Uncompleting then completing normally leaves wont_do false."""
        task = create_task(client, title="Mixed").get_json()
        client.put(f"/api/deck/{task['id']}", json={"wont_do": True})
        client.put(f"/api/deck/{task['id']}", json={"wont_do": False})
        resp = client.put(f"/api/deck/{task['id']}", json={"completed": True})
        assert resp.get_json()["wont_do"] is False


# ---------- Undo Recurring ----------


class TestUndoRecurring:
    def test_uncomplete_recurring_removes_spawned(self, client):
        task = create_task(client, title="Repeating",
                           due_date="2025-03-01",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1

        # Uncomplete the original
        client.put(f"/api/deck/{task['id']}", json={"completed": False})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        assert tasks[0]["id"] == task["id"]
        assert tasks[0]["completed_at"] is None

    def test_uncomplete_non_recurring_no_side_effects(self, client):
        t1 = create_task(client, title="Task A").get_json()
        t2 = create_task(client, title="Task B").get_json()
        client.put(f"/api/deck/{t1['id']}", json={"completed": True})
        client.put(f"/api/deck/{t1['id']}", json={"completed": False})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 2


# ---------- Reorder ----------


class TestReorder:
    def test_reorder_updates_sort_order(self, client):
        t1 = create_task(client, title="First").get_json()
        t2 = create_task(client, title="Second").get_json()
        t3 = create_task(client, title="Third").get_json()

        resp = client.post("/api/deck/reorder", json={
            "order": [t3["id"], t1["id"], t2["id"]]
        })
        assert resp.status_code == 204

        tasks = client.get("/api/deck").get_json()
        by_id = {t["id"]: t for t in tasks}
        assert by_id[t3["id"]]["sort_order"] == 0
        assert by_id[t1["id"]]["sort_order"] == 1
        assert by_id[t2["id"]]["sort_order"] == 2

    def test_reorder_empty_order(self, client):
        resp = client.post("/api/deck/reorder", json={"order": []})
        assert resp.status_code == 400

    def test_reorder_missing_order(self, client):
        resp = client.post("/api/deck/reorder", json={})
        assert resp.status_code == 400

    def test_reorder_updates_sets_sparse_sort_order(self, client):
        """`updates` sets sort_order on the given ids only, without touching
        any other task's sort_order — used for reordering under a narrowed
        (tag/search/page-filtered) view so hidden tasks aren't disturbed."""
        t1 = create_task(client, title="First").get_json()
        t2 = create_task(client, title="Second").get_json()
        t3 = create_task(client, title="Third").get_json()
        client.post("/api/deck/reorder", json={
            "order": [t1["id"], t2["id"], t3["id"]]
        })

        resp = client.post("/api/deck/reorder", json={
            "updates": [{"id": t3["id"], "sort_order": 0.5}]
        })
        assert resp.status_code == 204

        tasks = client.get("/api/deck").get_json()
        by_id = {t["id"]: t for t in tasks}
        assert by_id[t1["id"]]["sort_order"] == 0
        assert by_id[t2["id"]]["sort_order"] == 1
        assert by_id[t3["id"]]["sort_order"] == 0.5

    def test_reorder_updates_ignores_unknown_id(self, client):
        t1 = create_task(client, title="First").get_json()
        resp = client.post("/api/deck/reorder", json={
            "updates": [{"id": "does-not-exist", "sort_order": 3}]
        })
        assert resp.status_code == 204
        tasks = client.get("/api/deck").get_json()
        assert tasks[0]["id"] == t1["id"]

    def test_reorder_updates_ignores_non_numeric_sort_order(self, client):
        t1 = create_task(client, title="First").get_json()
        resp = client.post("/api/deck/reorder", json={
            "updates": [{"id": t1["id"], "sort_order": "not-a-number"}]
        })
        assert resp.status_code == 204
        tasks = client.get("/api/deck").get_json()
        assert tasks[0]["sort_order"] is None

    def test_reorder_empty_updates_and_order(self, client):
        resp = client.post("/api/deck/reorder", json={"order": [], "updates": []})
        assert resp.status_code == 400


# ---------- sort_order on update ----------


class TestSortOrderUpdate:
    def test_update_sort_order_to_null(self, client):
        task = create_task(client, title="Ordered").get_json()
        # Give it a sort order via reorder
        client.post("/api/deck/reorder", json={"order": [task["id"]]})
        tasks = client.get("/api/deck").get_json()
        assert tasks[0]["sort_order"] == 0

        # Reset to inbox (null)
        resp = client.put(f"/api/deck/{task['id']}", json={"sort_order": None})
        assert resp.get_json()["sort_order"] is None

    def test_update_sort_order_to_value(self, client):
        task = create_task(client, title="Manual").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"sort_order": 5})
        assert resp.get_json()["sort_order"] == 5


# ---------- Due Count ----------


class TestDueCount:
    def test_due_count_empty(self, client):
        resp = client.get("/api/deck/due-count")
        data = resp.get_json()
        assert data == {"count": 0, "overdue": 0, "today": 0}

    def test_due_count_today(self, client):
        today_str = date.today().isoformat()
        create_task(client, title="Due today", due_date=today_str)
        resp = client.get("/api/deck/due-count")
        data = resp.get_json()
        assert data["today"] == 1
        assert data["overdue"] == 0
        assert data["count"] == 1

    def test_due_count_overdue(self, client):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        create_task(client, title="Overdue", due_date=yesterday)
        resp = client.get("/api/deck/due-count")
        data = resp.get_json()
        assert data["overdue"] == 1
        assert data["today"] == 0
        assert data["count"] == 1

    def test_due_count_excludes_completed(self, client):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        task = create_task(client, title="Done overdue", due_date=yesterday).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        resp = client.get("/api/deck/due-count")
        data = resp.get_json()
        assert data["count"] == 0

    def test_due_count_excludes_future(self, client):
        future = (date.today() + timedelta(days=7)).isoformat()
        create_task(client, title="Future", due_date=future)
        resp = client.get("/api/deck/due-count")
        data = resp.get_json()
        assert data["count"] == 0

    def test_due_count_mixed(self, client):
        today_str = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        create_task(client, title="Today", due_date=today_str)
        create_task(client, title="Overdue", due_date=yesterday)
        create_task(client, title="Future", due_date=future)
        create_task(client, title="No date")
        resp = client.get("/api/deck/due-count")
        data = resp.get_json()
        assert data["today"] == 1
        assert data["overdue"] == 1
        assert data["count"] == 2


# ---------- Tasks Page ----------


class TestDeckPage:
    def test_deck_route_removed(self, client):
        """The standalone /deck route no longer exists."""
        resp = client.get("/deck")
        assert resp.status_code == 404

    def test_deck_embed_in_wiki_page(self, client, wiki_dir):
        """A wiki page with [[deck]] renders the full deck app."""
        (wiki_dir / "deck.md").write_text("# Deck\n\n[[deck]]\n", encoding="utf-8")
        resp = client.get("/page/deck")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body
        assert "task-add-btn" in body
        assert "deck.js" in body


# ── Defer Days (relative defer) ──────────────────────────────────────────────


class TestDeferDays:
    def test_create_with_defer_days(self, client):
        resp = client.post("/api/deck", json={"title": "Deferred", "defer_days": 3, "due_date": "2026-07-10"})
        assert resp.status_code == 201
        task = resp.get_json()
        assert task["defer_days"] == 3
        assert task["due_date"] == "2026-07-10"

    def test_update_defer_days(self, client):
        resp = client.post("/api/deck", json={"title": "Task"})
        task_id = resp.get_json()["id"]
        resp = client.put(f"/api/deck/{task_id}", json={"defer_days": 5})
        assert resp.status_code == 200
        assert resp.get_json()["defer_days"] == 5

    def test_clear_defer_days(self, client):
        resp = client.post("/api/deck", json={"title": "Task", "defer_days": 3})
        task_id = resp.get_json()["id"]
        resp = client.put(f"/api/deck/{task_id}", json={"defer_days": None})
        assert resp.status_code == 200
        assert resp.get_json()["defer_days"] is None

    def test_defer_days_zero_means_hide_until_due(self, client):
        resp = client.post("/api/deck", json={"title": "Task", "defer_days": 0})
        assert resp.status_code == 201
        assert resp.get_json()["defer_days"] == 0

    def test_negative_defer_days_clamped_to_zero(self, client):
        resp = client.post("/api/deck", json={"title": "Task", "defer_days": -5})
        assert resp.status_code == 201
        assert resp.get_json()["defer_days"] == 0

    def test_recurring_spawn_preserves_defer_days(self, client):
        resp = client.post("/api/deck", json={
            "title": "Recurring",
            "due_date": "2026-06-10",
            "defer_days": 3,
            "rec_type": "daily",
            "rec_interval": 1,
        })
        task_id = resp.get_json()["id"]
        client.put(f"/api/deck/{task_id}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = [t for t in tasks if t["id"] != task_id and not t["completed_at"]]
        assert len(spawned) == 1
        assert spawned[0]["defer_days"] == 3


class TestPlanLead:
    """defer_days = "plan N days before due": derives focus_date = due - lead.
    It schedules only — nothing in the deck hides a task any more."""

    def test_lead_sets_focus_date_on_create(self, client):
        t = client.post("/api/deck", json={
            "title": "Renew", "due_date": "2027-07-01", "defer_days": 10,
        }).get_json()
        assert t["defer_days"] == 10
        assert t["focus_date"] == "2027-06-21"

    def test_lead_zero_plans_on_due_date(self, client):
        t = client.post("/api/deck", json={
            "title": "On due", "due_date": "2027-07-01", "defer_days": 0,
        }).get_json()
        assert t["focus_date"] == "2027-07-01"

    def test_focus_recomputed_when_due_changes(self, client):
        tid = client.post("/api/deck", json={
            "title": "X", "due_date": "2027-07-01", "defer_days": 10,
        }).get_json()["id"]
        t = client.put(f"/api/deck/{tid}", json={"due_date": "2027-08-01"}).get_json()
        assert t["focus_date"] == "2027-07-22"

    def test_lead_wins_over_focus_date_in_same_save(self, client):
        # The modal sends both focus_date and defer_days; the lead-derived plan
        # date must win over the focus_date sent alongside it.
        t = client.post("/api/deck", json={
            "title": "X", "due_date": "2027-07-01", "defer_days": 10,
            "focus_date": "2027-01-01",
        }).get_json()
        assert t["focus_date"] == "2027-06-21"

    def test_bare_focus_change_is_manual_override(self, client):
        # A Plan-board drag PUTs focus_date only (no defer_days) — it must stick.
        tid = client.post("/api/deck", json={
            "title": "X", "due_date": "2027-07-01", "defer_days": 10,
        }).get_json()["id"]
        t = client.put(f"/api/deck/{tid}", json={"focus_date": "2027-06-25"}).get_json()
        assert t["focus_date"] == "2027-06-25"
        assert t["defer_days"] == 10

    def test_lead_without_due_leaves_focus_none(self, client):
        t = client.post("/api/deck", json={"title": "X", "defer_days": 10}).get_json()
        assert t["defer_days"] == 10
        assert t["focus_date"] is None

    def test_lead_task_stays_in_backlog_api(self, client):
        # A lead time schedules but never hides: the task is returned by the
        # deck API like any other, whatever its plan date.
        future = (date.today() + timedelta(days=30)).isoformat()
        client.post("/api/deck", json={
            "title": "Far", "due_date": future, "defer_days": 10,
        })
        titles = [t["title"] for t in client.get("/api/deck").get_json()]
        assert titles == ["Far"]


class TestYearlyRecurrence:
    def test_yearly_spawn_advances_one_year(self, client):
        tid = client.post("/api/deck", json={
            "title": "Annual", "due_date": "2026-07-01",
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/deck").get_json()
                   if t["id"] != tid and not t["completed_at"]]
        assert len(spawned) == 1
        assert spawned[0]["due_date"] == "2027-07-01"
        assert spawned[0]["recurrence"]["type"] == "yearly"

    def test_yearly_leap_day_clamps(self, client):
        tid = client.post("/api/deck", json={
            "title": "Leap", "due_date": "2028-02-29",
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/deck").get_json()
                   if t["id"] != tid and not t["completed_at"]]
        assert spawned[0]["due_date"] == "2029-02-28"

    def test_yearly_spawn_carries_plan_lead(self, client):
        tid = client.post("/api/deck", json={
            "title": "Annual+lead", "due_date": "2026-07-01", "defer_days": 10,
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/deck").get_json()
                   if t["id"] != tid and not t["completed_at"]][0]
        assert spawned["due_date"] == "2027-07-01"
        assert spawned["defer_days"] == 10
        assert spawned["focus_date"] == "2027-06-21"


# ── Batch Complete ───────────────────────────────────────────────────────────


class TestBatchComplete:
    def test_batch_complete_multiple(self, client):
        r1 = client.post("/api/deck", json={"title": "A"})
        r2 = client.post("/api/deck", json={"title": "B"})
        id1, id2 = r1.get_json()["id"], r2.get_json()["id"]
        resp = client.post("/api/deck/batch-complete", json={"ids": [id1, id2], "completed": True})
        assert resp.status_code == 200
        updated = resp.get_json()
        assert len(updated) == 2
        assert all(t["completed_at"] is not None for t in updated)
        # Verify via GET
        tasks = client.get("/api/deck").get_json()
        for t in tasks:
            if t["id"] in (id1, id2):
                assert t["completed_at"] is not None

    def test_batch_uncomplete(self, client):
        r1 = client.post("/api/deck", json={"title": "A"})
        id1 = r1.get_json()["id"]
        client.put(f"/api/deck/{id1}", json={"completed": True})
        resp = client.post("/api/deck/batch-complete", json={"ids": [id1], "completed": False})
        assert resp.status_code == 200
        task = client.get("/api/deck").get_json()[0]
        assert task["completed_at"] is None

    def test_batch_complete_empty_ids(self, client):
        resp = client.post("/api/deck/batch-complete", json={"ids": [], "completed": True})
        assert resp.status_code == 400

    def test_batch_complete_spawns_recurring(self, client):
        r = client.post("/api/deck", json={
            "title": "Recurring", "due_date": _future(10),
            "rec_type": "daily", "rec_interval": 1,
        })
        task_id = r.get_json()["id"]
        resp = client.post("/api/deck/batch-complete", json={"ids": [task_id], "completed": True})
        assert resp.status_code == 200
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        spawned = [t for t in tasks if t["id"] != task_id]
        assert spawned[0]["due_date"] == _future(11)
        assert spawned[0]["completed_at"] is None

    def test_batch_uncomplete_removes_spawned(self, client):
        r = client.post("/api/deck", json={
            "title": "Recurring", "due_date": "2026-06-10",
            "rec_type": "daily", "rec_interval": 1,
        })
        task_id = r.get_json()["id"]
        client.post("/api/deck/batch-complete", json={"ids": [task_id], "completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        # Now uncomplete
        client.post("/api/deck/batch-complete", json={"ids": [task_id], "completed": False})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        assert tasks[0]["id"] == task_id
        assert tasks[0]["completed_at"] is None

    def test_batch_complete_idempotent(self, client):
        r = client.post("/api/deck", json={"title": "A"})
        task_id = r.get_json()["id"]
        client.put(f"/api/deck/{task_id}", json={"completed": True})
        # Complete again — should return empty updated list (already completed)
        resp = client.post("/api/deck/batch-complete", json={"ids": [task_id], "completed": True})
        assert resp.status_code == 200
        assert len(resp.get_json()) == 0


# ── Split completed storage (_deck.json / _deck_completed.json) ────────────────


class TestCompletedStorage:
    """Coverage for the active/completed file split introduced in 4c79e31."""

    def test_completed_endpoint_empty_initially(self, client):
        resp = client.get("/api/deck/completed")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_complete_moves_task_to_completed_file(self, client):
        task = create_task(client, title="Finish me").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        # Gone from active
        active = client.get("/api/deck").get_json()
        assert all(t["id"] != task["id"] for t in active)
        # Present in completed
        completed = client.get("/api/deck/completed").get_json()
        assert len(completed) == 1
        assert completed[0]["id"] == task["id"]
        assert completed[0]["completed_at"] is not None

    def test_uncomplete_moves_task_back_to_active(self, client):
        task = create_task(client, title="Round trip").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        client.put(f"/api/deck/{task['id']}", json={"completed": False})
        active = client.get("/api/deck").get_json()
        completed = client.get("/api/deck/completed").get_json()
        assert [t["id"] for t in active] == [task["id"]]
        assert completed == []

    def test_batch_complete_moves_to_completed_file(self, client):
        t1 = create_task(client, title="A").get_json()
        t2 = create_task(client, title="B").get_json()
        client.post("/api/deck/batch-complete", json={"ids": [t1["id"], t2["id"]], "completed": True})
        assert client.get("/api/deck").get_json() == []
        completed_ids = {t["id"] for t in client.get("/api/deck/completed").get_json()}
        assert completed_ids == {t1["id"], t2["id"]}

    def test_update_completed_task_in_place(self, client):
        """Editing a completed task's fields persists without un-completing it."""
        task = create_task(client, title="Old", description="orig").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        resp = client.put(f"/api/deck/{task['id']}", json={
            "title": "Edited", "description": "new notes",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Edited"
        assert data["description"] == "new notes"
        assert data["completed_at"] is not None  # still completed
        # Still only in the completed file, not active
        assert client.get("/api/deck").get_json() == []
        completed = client.get("/api/deck/completed").get_json()
        assert completed[0]["title"] == "Edited"

    def test_delete_completed_task(self, client):
        task = create_task(client, title="Del me").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        resp = client.delete(f"/api/deck/{task['id']}")
        assert resp.status_code == 204
        assert client.get("/api/deck/completed").get_json() == []

    def test_batch_delete_removes_completed_tasks(self, client):
        active_task = create_task(client, title="Active").get_json()
        done_task = create_task(client, title="Done").get_json()
        client.put(f"/api/deck/{done_task['id']}", json={"completed": True})
        resp = client.post("/api/deck/batch-delete", json={
            "ids": [active_task["id"], done_task["id"]],
        })
        assert resp.status_code == 204
        assert client.get("/api/deck").get_json() == []
        assert client.get("/api/deck/completed").get_json() == []

    def test_search_includes_completed_task(self, client):
        task = create_task(client, title="findable_completed_token").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        resp = client.get("/api/search?q=findable_completed_token",
                           headers={"Host": "localhost:5000"})
        results = resp.get_json()
        task_results = [r for r in results if r.get("type") == "task"]
        assert task_results
        assert task_results[0]["status"] == "done"


# ── Migration: legacy single-file → split files ────────────────────────────────


class TestCompletedMigration:
    """Coverage for _migrate_completed_split() (one-time startup migration)."""

    def _completed_file(self):
        return deck_module.DECK_FILE.parent / "_deck_completed.json"

    def test_migration_splits_mixed_file(self, wiki_dir):
        import json
        deck_module.DECK_FILE.write_text(json.dumps([
            {"id": "1", "title": "active", "completed_at": None},
            {"id": "2", "title": "done", "completed_at": "2026-01-01T00:00:00"},
        ]), encoding="utf-8")
        deck_module._migrate_completed_split()
        active = json.loads(deck_module.DECK_FILE.read_text(encoding="utf-8"))
        completed = json.loads(self._completed_file().read_text(encoding="utf-8"))
        assert [t["id"] for t in active] == ["1"]
        assert [t["id"] for t in completed] == ["2"]

    def test_migration_no_completed_writes_empty_file(self, wiki_dir):
        import json
        deck_module.DECK_FILE.write_text(json.dumps([
            {"id": "1", "title": "active", "completed_at": None},
        ]), encoding="utf-8")
        deck_module._migrate_completed_split()
        # Active file untouched, completed file created empty
        assert len(json.loads(deck_module.DECK_FILE.read_text(encoding="utf-8"))) == 1
        assert json.loads(self._completed_file().read_text(encoding="utf-8")) == []

    def test_migration_skipped_when_completed_exists(self, wiki_dir):
        import json
        deck_module.DECK_FILE.write_text(json.dumps([
            {"id": "2", "title": "done", "completed_at": "2026-01-01T00:00:00"},
        ]), encoding="utf-8")
        self._completed_file().write_text("[]", encoding="utf-8")
        deck_module._migrate_completed_split()
        # Already-migrated: deck.json is left alone, completed stays empty
        assert len(json.loads(deck_module.DECK_FILE.read_text(encoding="utf-8"))) == 1
        assert json.loads(self._completed_file().read_text(encoding="utf-8")) == []

    def test_migration_noop_when_deck_absent(self, wiki_dir):
        # No _deck.json on disk (fixture doesn't create one)
        assert not deck_module.DECK_FILE.exists()
        deck_module._migrate_completed_split()
        assert not self._completed_file().exists()

    def test_migration_handles_invalid_json(self, wiki_dir):
        deck_module.DECK_FILE.write_text("{ not valid json", encoding="utf-8")
        deck_module._migrate_completed_split()  # must not raise
        assert not self._completed_file().exists()


# ── Focus date (Today planning) ────────────────────────────────────────────────


class TestFocusDate:
    def test_create_with_focus_date(self, client):
        resp = create_task(client, title="Planned", focus_date="2026-07-20")
        assert resp.status_code == 201
        assert resp.get_json()["focus_date"] == "2026-07-20"

    def test_create_default_focus_date_none(self, client):
        assert create_task(client, title="No plan").get_json()["focus_date"] is None

    def test_create_invalid_focus_date(self, client):
        resp = create_task(client, title="Bad", focus_date="20-07-2026")
        assert resp.status_code == 400

    def test_update_focus_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"focus_date": "2026-07-21"})
        assert resp.get_json()["focus_date"] == "2026-07-21"

    def test_clear_focus_date(self, client):
        task = create_task(client, title="Task", focus_date="2026-07-20").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"focus_date": None})
        assert resp.get_json()["focus_date"] is None

    def test_update_invalid_focus_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"focus_date": "not-a-date"})
        assert resp.status_code == 400

    def test_recurring_spawn_resets_focus_date(self, client):
        task = create_task(client, title="Recurring",
                           due_date="2026-06-10", focus_date="2026-06-10",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["focus_date"] is None


# ── blocked_by dependencies ────────────────────────────────────────────────────


class TestBlockedBy:
    def test_create_default_empty(self, client):
        assert create_task(client, title="Free").get_json()["blocked_by"] == []

    def test_create_with_list(self, client):
        resp = create_task(client, title="Blocked", blocked_by=["a", "b"])
        assert resp.get_json()["blocked_by"] == ["a", "b"]

    def test_create_with_comma_string(self, client):
        resp = create_task(client, title="Blocked", blocked_by="a, b ,c")
        assert resp.get_json()["blocked_by"] == ["a", "b", "c"]

    def test_update_blocked_by_list(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"blocked_by": ["x", "y"]})
        assert resp.get_json()["blocked_by"] == ["x", "y"]

    def test_update_blocked_by_string(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"blocked_by": "x, y"})
        assert resp.get_json()["blocked_by"] == ["x", "y"]

    def test_clear_blocked_by(self, client):
        task = create_task(client, title="Task", blocked_by=["x"]).get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"blocked_by": []})
        assert resp.get_json()["blocked_by"] == []


# ── Page field (task ↔ page linking) ──────────────────────────────────────────


class TestPageField:
    def test_create_with_page(self, client):
        assert create_task(client, title="T", page="my-proj").get_json()["page"] == "my-proj"

    def test_create_default_page_none(self, client):
        assert create_task(client, title="T").get_json()["page"] is None

    def test_update_page(self, client):
        task = create_task(client, title="T").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"page": "linked"})
        assert resp.get_json()["page"] == "linked"

    def test_clear_page(self, client):
        task = create_task(client, title="T", page="linked").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"page": ""})
        assert resp.get_json()["page"] is None


# ── Batch tag ──────────────────────────────────────────────────────────────────


class TestBatchTag:
    def test_add_tags_to_multiple(self, client):
        t1 = create_task(client, title="A").get_json()
        t2 = create_task(client, title="B").get_json()
        resp = client.post("/api/deck/batch-tag", json={
            "ids": [t1["id"], t2["id"]], "add": ["work"],
        })
        assert resp.status_code == 200
        tasks = {t["id"]: t for t in client.get("/api/deck").get_json()}
        assert "work" in tasks[t1["id"]]["tags"]
        assert "work" in tasks[t2["id"]]["tags"]

    def test_remove_tags(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/deck/{task['id']}", json={"tags": ["work", "urgent"]})
        client.post("/api/deck/batch-tag", json={"ids": [task["id"]], "remove": ["urgent"]})
        tags = client.get("/api/deck").get_json()[0]["tags"]
        assert tags == ["work"]

    def test_add_and_remove_combined(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/deck/{task['id']}", json={"tags": ["old"]})
        client.post("/api/deck/batch-tag", json={
            "ids": [task["id"]], "add": ["new"], "remove": ["old"],
        })
        assert client.get("/api/deck").get_json()[0]["tags"] == ["new"]

    def test_add_dedupes_case_insensitive(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/deck/{task['id']}", json={"tags": ["Work"]})
        client.post("/api/deck/batch-tag", json={"ids": [task["id"]], "add": ["work"]})
        assert client.get("/api/deck").get_json()[0]["tags"] == ["Work"]

    def test_empty_ids_400(self, client):
        resp = client.post("/api/deck/batch-tag", json={"ids": [], "add": ["x"]})
        assert resp.status_code == 400

    def test_no_add_or_remove_400(self, client):
        task = create_task(client, title="A").get_json()
        resp = client.post("/api/deck/batch-tag", json={"ids": [task["id"]]})
        assert resp.status_code == 400

    def test_batch_tag_applies_to_completed_task(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        resp = client.post("/api/deck/batch-tag", json={
            "ids": [task["id"]], "add": ["archived"],
        })
        assert resp.status_code == 200
        completed = client.get("/api/deck/completed").get_json()
        assert "archived" in completed[0]["tags"]


# ---------- Concurrent-write safety (regression for commit 079930f) ----------
#
# The Flask dev server is threaded, so two task mutations can run at once. The
# original storage layer did an unguarded read-modify-write on _deck.json with a
# truncate-in-place write, and _read_deck() swallowed a torn/empty read as an
# empty list. A create landing in that window read [], appended its one task,
# and overwrote every other task — exactly what wiped the deck in production.


class TestConcurrentWriteSafety:
    def test_empty_deck_file_is_not_overwritten(self, client, wiki_dir):
        """A zero-length _deck.json (a torn write) must not be treated as an
        empty list and clobbered — the create is refused with 503."""
        create_task(client, title="Keeper")
        deck_file = wiki_dir / "_deck.json"
        deck_file.write_text("", encoding="utf-8")  # simulate a torn write
        resp = create_task(client, title="Would clobber")
        assert resp.status_code == 503
        # The file was left untouched, not overwritten with just the new task.
        assert deck_file.read_text(encoding="utf-8") == ""

    def test_corrupt_deck_file_is_not_overwritten(self, client, wiki_dir):
        create_task(client, title="Keeper")
        deck_file = wiki_dir / "_deck.json"
        deck_file.write_text("{ this is not json", encoding="utf-8")
        resp = create_task(client, title="Would clobber")
        assert resp.status_code == 503
        assert deck_file.read_text(encoding="utf-8") == "{ this is not json"

    def test_genuinely_empty_deck_still_creates(self, client, wiki_dir):
        """An empty deck is stored as '[]', which must still accept new tasks."""
        deck_file = wiki_dir / "_deck.json"
        deck_file.write_text("[]", encoding="utf-8")
        resp = create_task(client, title="First")
        assert resp.status_code == 201
        assert [t["title"] for t in client.get("/api/deck").get_json()] == ["First"]

    def test_concurrent_creates_lose_no_tasks(self, wiki_dir):
        """Many creates from many threads must all survive — no lost updates."""
        threads_n, per_thread = 8, 20

        def worker(tid):
            with server.app.test_client() as c:
                for i in range(per_thread):
                    r = c.post("/api/deck", json={"title": f"t{tid}-{i}"})
                    assert r.status_code == 201

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        titles = [t["title"] for t in json.loads((wiki_dir / "_deck.json").read_text())]
        assert len(titles) == threads_n * per_thread
        assert len(set(titles)) == threads_n * per_thread

    def test_deck_file_always_valid_json_during_concurrent_writes(self, wiki_dir):
        """A reader hammering the file while writers run never sees a torn/empty
        file — atomic replace guarantees a complete document every time."""
        stop = threading.Event()
        errors = []

        def reader():
            while not stop.is_set():
                try:
                    raw = (wiki_dir / "_deck.json").read_text(encoding="utf-8")
                except FileNotFoundError:
                    continue  # not created yet, before the first write
                if raw == "":
                    continue  # os.replace window: file simply not swapped yet
                try:
                    if not isinstance(json.loads(raw), list):
                        errors.append("not a list")
                except json.JSONDecodeError:
                    errors.append("torn read")

        def writer():
            with server.app.test_client() as c:
                for i in range(40):
                    c.post("/api/deck", json={"title": f"w{i}"})

        r = threading.Thread(target=reader)
        w = threading.Thread(target=writer)
        r.start(); w.start()
        w.join(); stop.set(); r.join()
        assert errors == []


# ---------- Refactor regression tests ----------


class TestUncompleteClearsWontDo:
    def test_batch_uncomplete_clears_wont_do(self, client):
        """Reopening a won't-do task via batch-complete must clear the flag."""
        task_id = create_task(client, title="Abandoned").get_json()["id"]
        client.put(f"/api/deck/{task_id}", json={"wont_do": True})
        client.post("/api/deck/batch-complete", json={"ids": [task_id], "completed": False})
        active = client.get("/api/deck").get_json()
        task = next(t for t in active if t["id"] == task_id)
        assert task["completed_at"] is None
        assert task["wont_do"] is False


class TestCreateFieldParity:
    def test_create_applies_all_fields(self, client):
        """Create route goes through _apply_task_fields — every field lands."""
        resp = create_task(
            client, title="  Full  ", description=" notes ", tags=[" a ", "", "b"],
            due_date="2026-08-01", defer_days=-2,
            focus_date="2026-08-01", page="  some-page ", blocked_by="x, y",
            rec_type="weekly", rec_interval=3,
        )
        assert resp.status_code == 201
        t = resp.get_json()
        assert t["title"] == "Full"
        assert t["description"] == "notes"
        assert t["tags"] == ["a", "b"]
        assert t["due_date"] == "2026-08-01"
        assert t["defer_days"] == 0          # clamped to >= 0
        assert t["focus_date"] == "2026-08-01"
        assert t["page"] == "some-page"
        assert t["blocked_by"] == ["x", "y"]
        assert t["recurrence"] == {"type": "weekly", "interval": 3, "from": "due", "anchor": "due"}
        assert t["sort_order"] is None
        assert t["completed_at"] is None
        assert t["wont_do"] is False

    def test_create_defaults(self, client):
        t = create_task(client, title="Bare").get_json()
        assert t["recurrence"] is None
        assert t["blocked_by"] == []
        assert t["tags"] == []
        assert t["due_date"] is None
        assert t["defer_days"] is None


# ── Recurrence catch-up and from-completion mode ──────────────────────────────


class TestRecurrenceCatchUp:
    def test_overdue_daily_catches_up_to_today(self, client):
        """Completing a long-overdue daily task never spawns an overdue instance."""
        past = (date.today() - timedelta(days=30)).isoformat()
        task = create_task(client, title="Overdue daily", due_date=past,
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == date.today().isoformat()

    def test_overdue_weekly_stays_calendar_aligned(self, client):
        """Catch-up advances in whole intervals from the original due date."""
        base = date.today() - timedelta(days=30)
        task = create_task(client, title="Overdue weekly", due_date=base.isoformat(),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        next_due = date.fromisoformat(spawned["due_date"])
        assert next_due >= date.today()
        assert (next_due - base).days % 7 == 0
        assert (next_due - date.today()).days < 7

    def test_from_completion_steps_from_today(self, client):
        """recurrence.from == completion anchors the next due date on today."""
        past = (date.today() - timedelta(days=10)).isoformat()
        task = create_task(client, title="Water plants", due_date=past,
                           rec_type="daily", rec_interval=3,
                           rec_from="completion").get_json()
        assert task["recurrence"]["from"] == "completion"
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == (date.today() + timedelta(days=3)).isoformat()

    def test_from_completion_without_due_date(self, client):
        task = create_task(client, title="Habit", rec_type="weekly",
                           rec_interval=1, rec_from="completion").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == (date.today() + timedelta(days=7)).isoformat()

    def test_rec_from_defaults_to_due(self, client):
        task = create_task(client, title="Rent", rec_type="monthly").get_json()
        assert task["recurrence"]["from"] == "due"


# ── spawned_from tracking ─────────────────────────────────────────────────────


class TestSpawnedFrom:
    def test_spawned_instance_carries_spawned_from(self, client):
        task = create_task(client, title="Recurring", due_date=_future(5),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["spawned_from"] == task["id"]

    def test_uncomplete_removes_only_exact_spawned_instance(self, client):
        """A same-titled recurring sibling created after completion survives —
        the old title/time heuristic would have removed it too."""
        task = create_task(client, title="Standup", due_date=_future(5),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        sibling = create_task(client, title="Standup", due_date=_future(6),
                              rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": False})
        active_ids = {t["id"] for t in client.get("/api/deck").get_json()}
        assert task["id"] in active_ids
        assert sibling["id"] in active_ids
        spawned_left = [t for t in client.get("/api/deck").get_json()
                        if t.get("spawned_from") == task["id"]]
        assert spawned_left == []

    def test_uncomplete_legacy_instance_uses_heuristic(self, client, wiki_dir):
        """Spawned instances from before spawned_from existed are still removed."""
        task = create_task(client, title="Legacy", due_date=_future(5),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        deck_file = wiki_dir / "_deck.json"
        data = json.loads(deck_file.read_text(encoding="utf-8"))
        for t in data:
            t.pop("spawned_from", None)
        deck_file.write_text(json.dumps(data), encoding="utf-8")
        client.put(f"/api/deck/{task['id']}", json={"completed": False})
        tasks = client.get("/api/deck").get_json()
        assert [t["id"] for t in tasks] == [task["id"]]


# ── blocked_by cleanup on delete ──────────────────────────────────────────────


class TestBlockedRefCleanup:
    def test_delete_strips_blocked_by_references(self, client):
        blocker = create_task(client, title="Blocker").get_json()
        blocked = create_task(client, title="Blocked",
                              blocked_by=[blocker["id"]]).get_json()
        client.delete(f"/api/deck/{blocker['id']}")
        remaining = client.get("/api/deck").get_json()
        assert [t["id"] for t in remaining] == [blocked["id"]]
        assert remaining[0]["blocked_by"] == []

    def test_batch_delete_strips_blocked_by_references(self, client):
        b1 = create_task(client, title="B1").get_json()
        b2 = create_task(client, title="B2").get_json()
        blocked = create_task(client, title="Blocked",
                              blocked_by=[b1["id"], b2["id"]]).get_json()
        client.post("/api/deck/batch-delete", json={"ids": [b1["id"], b2["id"]]})
        remaining = client.get("/api/deck").get_json()
        assert remaining[0]["blocked_by"] == []

    def test_delete_strips_references_in_completed(self, client):
        blocker = create_task(client, title="Blocker").get_json()
        blocked = create_task(client, title="Blocked",
                              blocked_by=[blocker["id"]]).get_json()
        client.put(f"/api/deck/{blocked['id']}", json={"completed": True})
        client.delete(f"/api/deck/{blocker['id']}")
        completed = client.get("/api/deck/completed").get_json()
        assert completed[0]["blocked_by"] == []


# ── Server-side blocking reconciliation ───────────────────────────────────────


class TestBlockingReconcile:
    def test_put_blocking_adds_reverse_edge(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B").get_json()
        client.put(f"/api/deck/{a['id']}", json={"blocking": [b["id"]]})
        b_now = next(t for t in client.get("/api/deck").get_json() if t["id"] == b["id"])
        assert b_now["blocked_by"] == [a["id"]]

    def test_put_empty_blocking_removes_reverse_edge(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B", blocked_by=[a["id"]]).get_json()
        client.put(f"/api/deck/{a['id']}", json={"blocking": []})
        b_now = next(t for t in client.get("/api/deck").get_json() if t["id"] == b["id"])
        assert b_now["blocked_by"] == []

    def test_create_with_blocking(self, client):
        b = create_task(client, title="B").get_json()
        a = create_task(client, title="A", blocking=[b["id"]]).get_json()
        b_now = next(t for t in client.get("/api/deck").get_json() if t["id"] == b["id"])
        assert b_now["blocked_by"] == [a["id"]]

    def test_blocking_unknown_ids_are_harmless(self, client):
        a = create_task(client, title="A").get_json()
        resp = client.put(f"/api/deck/{a['id']}", json={"blocking": ["nope", a["id"]]})
        assert resp.status_code == 200
        a_now = client.get("/api/deck").get_json()[0]
        assert a_now["blocked_by"] == []  # no self-block

    def test_blocking_preserves_other_blockers(self, client):
        other = create_task(client, title="Other").get_json()
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B", blocked_by=[other["id"]]).get_json()
        client.put(f"/api/deck/{a['id']}", json={"blocking": [b["id"]]})
        b_now = next(t for t in client.get("/api/deck").get_json() if t["id"] == b["id"])
        assert sorted(b_now["blocked_by"]) == sorted([other["id"], a["id"]])


# ── Restore (faithful delete-undo) ────────────────────────────────────────────


class TestRestore:
    def test_restore_roundtrips_all_fields(self, client):
        task = create_task(client, title="Full", description="notes",
                           tags=["a"], due_date="2026-09-01",
                           focus_date="2026-08-30", page="some-page",
                           rec_type="weekly", rec_interval=2).get_json()
        client.delete(f"/api/deck/{task['id']}")
        assert client.get("/api/deck").get_json() == []
        resp = client.post("/api/deck/restore", json={"tasks": [task]})
        assert resp.status_code == 201
        restored = client.get("/api/deck").get_json()[0]
        assert restored["id"] == task["id"]
        assert restored["created_at"] == task["created_at"]
        for field in ("title", "description", "tags", "due_date",
                      "focus_date", "page", "recurrence"):
            assert restored[field] == task[field]

    def test_restore_keeps_yearly_recurrence(self, client):
        # Regression: the snapshot sanitizer whitelisted only daily/weekly/
        # monthly, so undoing the delete of a yearly task silently dropped
        # its recurrence.
        task = create_task(client, title="Birthday", due_date="2030-05-01",
                           rec_type="yearly", rec_interval=1).get_json()
        assert task["recurrence"]["type"] == "yearly"
        client.delete(f"/api/deck/{task['id']}")
        client.post("/api/deck/restore", json={"tasks": [task]})
        restored = client.get("/api/deck").get_json()[0]
        assert restored["recurrence"] == task["recurrence"]

    def test_restore_drops_unknown_recurrence_type(self, client):
        resp = client.post("/api/deck/restore", json={"tasks": [
            {"title": "T", "recurrence": {"type": "hourly", "interval": 1}}]})
        assert resp.get_json()[0]["recurrence"] is None

    def test_restore_completed_snapshot_lands_in_completed(self, client):
        task = create_task(client, title="Done one").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        snap = client.get("/api/deck/completed").get_json()[0]
        client.delete(f"/api/deck/{task['id']}")
        client.post("/api/deck/restore", json={"tasks": [snap]})
        assert client.get("/api/deck").get_json() == []
        completed = client.get("/api/deck/completed").get_json()
        assert completed[0]["id"] == task["id"]
        assert completed[0]["completed_at"] == snap["completed_at"]

    def test_restore_skips_existing_ids(self, client):
        task = create_task(client, title="Still here").get_json()
        resp = client.post("/api/deck/restore", json={"tasks": [task]})
        assert resp.status_code == 201
        assert resp.get_json() == []
        assert len(client.get("/api/deck").get_json()) == 1

    def test_restore_drops_unknown_fields(self, client):
        snap = create_task(client, title="T").get_json()
        client.delete(f"/api/deck/{snap['id']}")
        snap["evil"] = "payload"
        client.post("/api/deck/restore", json={"tasks": [snap]})
        restored = client.get("/api/deck").get_json()[0]
        assert "evil" not in restored

    def test_restore_requires_tasks(self, client):
        assert client.post("/api/deck/restore", json={"tasks": []}).status_code == 400

    def test_restore_skips_titleless_snapshots(self, client):
        resp = client.post("/api/deck/restore", json={"tasks": [{"title": "  "}]})
        assert resp.status_code == 201
        assert resp.get_json() == []

    def test_restore_reapplies_reverse_blocking_edges(self, client):
        blocker = create_task(client, title="Blocker").get_json()
        blocked = create_task(client, title="Blocked",
                              blocked_by=[blocker["id"]]).get_json()
        client.delete(f"/api/deck/{blocker['id']}")
        snap = dict(blocker, blocking=[blocked["id"]])
        client.post("/api/deck/restore", json={"tasks": [snap]})
        blocked_now = next(t for t in client.get("/api/deck").get_json()
                           if t["id"] == blocked["id"])
        assert blocked_now["blocked_by"] == [blocker["id"]]


# ── due-count ────────────────────────────────────────────────────────────────


class TestDueCount:
    """Nothing is hidden any more, so due-count reflects every dated task;
    only blocked tasks are excluded (they can't be worked on)."""

    def test_overdue_task_counted(self, client):
        past = (date.today() - timedelta(days=2)).isoformat()
        create_task(client, title="Visible overdue", due_date=past)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["overdue"] == 1

    def test_overdue_task_with_future_plan_still_counted(self, client):
        # Planning an overdue task for later doesn't make the deadline go away.
        past = (date.today() - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        create_task(client, title="Planned later", due_date=past, focus_date=future)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["overdue"] == 1

    def test_lead_time_task_counted_when_due(self, client):
        # A lead time no longer suppresses the count once the task is due.
        today = date.today().isoformat()
        create_task(client, title="Due today", due_date=today, defer_days=3)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["today"] == 1

    def test_future_due_not_counted(self, client):
        future = (date.today() + timedelta(days=10)).isoformat()
        create_task(client, title="Far due", due_date=future, defer_days=3)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["count"] == 0

    def test_blocked_task_not_counted(self, client):
        past = (date.today() - timedelta(days=2)).isoformat()
        blocker = create_task(client, title="Blocker").get_json()
        create_task(client, title="Blocked overdue", due_date=past,
                    blocked_by=[blocker["id"]])
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["count"] == 0


# ── Round 2: dependency cycle rejection ───────────────────────────────────────


class TestCycleRejection:
    def test_direct_cycle_rejected(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B", blocked_by=[a["id"]]).get_json()
        resp = client.put(f"/api/deck/{a['id']}", json={"blocked_by": [b["id"]]})
        assert resp.status_code == 400
        assert "cycle" in resp.get_json()["error"].lower()
        # And nothing was written
        a_now = next(t for t in client.get("/api/deck").get_json() if t["id"] == a["id"])
        assert a_now["blocked_by"] == []

    def test_self_block_rejected(self, client):
        a = create_task(client, title="A").get_json()
        resp = client.put(f"/api/deck/{a['id']}", json={"blocked_by": [a["id"]]})
        assert resp.status_code == 400

    def test_transitive_cycle_rejected(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B", blocked_by=[a["id"]]).get_json()
        c = create_task(client, title="C", blocked_by=[b["id"]]).get_json()
        resp = client.put(f"/api/deck/{a['id']}", json={"blocked_by": [c["id"]]})
        assert resp.status_code == 400

    def test_cycle_via_blocking_rejected(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B", blocked_by=[a["id"]]).get_json()
        # b blocking a would mean a -> b -> a
        resp = client.put(f"/api/deck/{b['id']}", json={"blocking": [a["id"]]})
        assert resp.status_code == 400

    def test_create_cycle_rejected(self, client):
        b = create_task(client, title="B").get_json()
        resp = client.post("/api/deck", json={
            "title": "A", "blocked_by": [b["id"]], "blocking": [b["id"]],
        })
        assert resp.status_code == 400

    def test_valid_chain_accepted(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B", blocked_by=[a["id"]]).get_json()
        resp = client.put(f"/api/deck/{b['id']}", json={"blocked_by": [a["id"]]})
        assert resp.status_code == 200


# ── Round 2: skip occurrence ──────────────────────────────────────────────────


class TestSkipOccurrence:
    def test_skip_archives_and_spawns(self, client):
        task = create_task(client, title="Recurring", due_date=_future(2),
                           rec_type="daily", rec_interval=1).get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"skip": True})
        assert resp.status_code == 200
        assert resp.get_json()["wont_do"] is True
        active = client.get("/api/deck").get_json()
        assert len(active) == 1
        assert active[0]["spawned_from"] == task["id"]
        assert active[0]["due_date"] == _future(3)
        completed = client.get("/api/deck/completed").get_json()
        assert completed[0]["id"] == task["id"]
        assert completed[0]["wont_do"] is True

    def test_undo_skip_removes_spawned(self, client):
        task = create_task(client, title="Recurring", due_date=_future(2),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"skip": True})
        client.put(f"/api/deck/{task['id']}", json={"wont_do": False})
        active = client.get("/api/deck").get_json()
        assert [t["id"] for t in active] == [task["id"]]
        assert active[0]["completed_at"] is None
        assert active[0]["wont_do"] is False

    def test_skip_non_recurring_is_plain_wont_do(self, client):
        task = create_task(client, title="One-off").get_json()
        client.put(f"/api/deck/{task['id']}", json={"skip": True})
        assert client.get("/api/deck").get_json() == []
        completed = client.get("/api/deck/completed").get_json()
        assert completed[0]["wont_do"] is True


# ── Round 2: due badge excludes blocked ───────────────────────────────────────


class TestDueCountBlocked:
    def test_blocked_overdue_not_counted(self, client):
        blocker = create_task(client, title="Blocker").get_json()
        past = (date.today() - timedelta(days=1)).isoformat()
        create_task(client, title="Blocked overdue", due_date=past,
                    blocked_by=[blocker["id"]])
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["count"] == 0

    def test_unblocked_after_blocker_completes(self, client):
        blocker = create_task(client, title="Blocker").get_json()
        past = (date.today() - timedelta(days=1)).isoformat()
        create_task(client, title="Blocked overdue", due_date=past,
                    blocked_by=[blocker["id"]])
        client.put(f"/api/deck/{blocker['id']}", json={"completed": True})
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["overdue"] == 1


# ── Round 2: capacity embed param ─────────────────────────────────────────────


class TestCapacityParam:
    def test_capacity_lands_in_embed(self, client, wiki_dir):
        (wiki_dir / "board.md").write_text("# Board\n\n[[deck capacity=7]]\n",
                                           encoding="utf-8")
        html_out = client.get("/page/board").get_data(as_text=True)
        assert 'data-init-capacity="7"' in html_out

    def test_invalid_capacity_is_empty(self, client, wiki_dir):
        (wiki_dir / "board.md").write_text("# Board\n\n[[deck capacity=lots]]\n",
                                           encoding="utf-8")
        html_out = client.get("/page/board").get_data(as_text=True)
        assert 'data-init-capacity=""' in html_out

    def test_embed_loads_deck_core(self, client, wiki_dir):
        (wiki_dir / "board.md").write_text("# Board\n\n[[deck]]\n", encoding="utf-8")
        html_out = client.get("/page/board").get_data(as_text=True)
        core = html_out.index('src="/public/deck-core.js"')
        app = html_out.index('src="/public/deck.js"')
        assert core < app


# ── Round 2: completed-task year archives ─────────────────────────────────────


class TestCompletedArchive:
    def _seed_completed(self, wiki_dir, tasks):
        (wiki_dir / "_deck_completed.json").write_text(
            json.dumps(tasks), encoding="utf-8")

    def test_old_years_rolled_into_archives(self, client, wiki_dir):
        year = date.today().year
        self._seed_completed(wiki_dir, [
            {"id": "old1", "title": "Ancient", "completed_at": f"{year-2}-05-01T10:00:00"},
            {"id": "old2", "title": "Last year", "completed_at": f"{year-1}-11-30T10:00:00"},
            {"id": "new1", "title": "Recent", "completed_at": f"{year}-01-02T10:00:00"},
        ])
        deck_module._archive_old_completed()
        hot = json.loads((wiki_dir / "_deck_completed.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in hot] == ["new1"]
        arch1 = json.loads((wiki_dir / f"_deck_completed-{year-2}.json").read_text(encoding="utf-8"))
        arch2 = json.loads((wiki_dir / f"_deck_completed-{year-1}.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in arch1] == ["old1"]
        assert [t["id"] for t in arch2] == ["old2"]

    def test_archive_merges_into_existing_file(self, client, wiki_dir):
        year = date.today().year
        (wiki_dir / f"_deck_completed-{year-1}.json").write_text(
            json.dumps([{"id": "prior", "title": "Already archived",
                         "completed_at": f"{year-1}-01-01T10:00:00"}]), encoding="utf-8")
        self._seed_completed(wiki_dir, [
            {"id": "old", "title": "Old", "completed_at": f"{year-1}-06-01T10:00:00"},
        ])
        deck_module._archive_old_completed()
        arch = json.loads((wiki_dir / f"_deck_completed-{year-1}.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in arch] == ["prior", "old"]

    def test_noop_when_nothing_old(self, client, wiki_dir):
        year = date.today().year
        self._seed_completed(wiki_dir, [
            {"id": "new1", "title": "Recent", "completed_at": f"{year}-01-02T10:00:00"},
        ])
        deck_module._archive_old_completed()
        assert not list(wiki_dir.glob("_deck_completed-*.json"))

    def test_archived_tasks_stay_searchable(self, client, wiki_dir):
        year = date.today().year
        self._seed_completed(wiki_dir, [
            {"id": "old1", "title": "Zephyrscale audit",
             "completed_at": f"{year-1}-05-01T10:00:00"},
        ])
        deck_module._archive_old_completed()
        results = client.get("/api/search?q=zephyrscale").get_json()
        assert any(r.get("type") == "task" for r in results)
