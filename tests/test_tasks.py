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

    def test_monthly_anchor_kept_when_only_start_date_changes(self, client):
        task = create_task(client, title="Month end", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        client.put(f"/api/deck/{spawned['id']}", json={
            "due_date": "2099-02-28", "start_date": "2099-02-20",
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

    def test_start_only_monthly_does_not_drift(self, client):
        task = create_task(client, title="Start end", start_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        starts = []
        for _ in range(2):
            task = self._complete_and_get_spawn(client, task["id"])
            starts.append(task["start_date"])
        assert starts == ["2099-02-28", "2099-03-31"]

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
        assert spawned["recurrence"] == {"type": "weekly", "interval": 2, "from": "due"}

    def test_complete_non_recurring_no_spawn(self, client):
        task = create_task(client, title="One-off").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 0

    def test_start_only_series_steps_start_date(self, client):
        # No due date: the start date drives the series.
        task = create_task(client, title="Weekly start",
                           start_date=_future(7),
                           rec_type="weekly", rec_interval=1).get_json()
        assert "anchor" not in task["recurrence"]
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        active = client.get("/api/deck").get_json()
        assert len(active) == 1
        assert active[0]["start_date"] == _future(14)
        assert active[0]["due_date"] is None

    def test_start_only_series_from_completion(self, client):
        task = create_task(client, title="Interval habit",
                           start_date=_future(7),
                           rec_type="weekly", rec_interval=1,
                           rec_from="completion").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["start_date"] == _future(7)
        assert spawned["due_date"] is None

    def test_start_only_stale_series_catches_up_to_today(self, client):
        # A long-past start date must not spawn an instance starting in the past.
        task = create_task(client, title="Stale weekly",
                           start_date=(date.today() - timedelta(days=30)).isoformat(),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        started = date.fromisoformat(spawned["start_date"])
        assert date.today() <= started < date.today() + timedelta(days=7)
        # Still on the original weekday cadence.
        assert (started - date.fromisoformat(task["start_date"])).days % 7 == 0

    def test_both_dates_carry_the_gap(self, client):
        # Due drives the series; the start date keeps its distance to it.
        task = create_task(client, title="Report",
                           due_date=_future(10), start_date=_future(7),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == _future(17)
        assert spawned["start_date"] == _future(14)

    def test_gap_carried_from_completion(self, client):
        task = create_task(client, title="Habit",
                           due_date=_future(10), start_date=_future(8),
                           rec_type="daily", rec_interval=5,
                           rec_from="completion").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == _future(5)
        assert spawned["start_date"] == _future(3)

    def test_gap_carried_across_month_end_clamp(self, client):
        # Start 3 days before a month-end due date; Feb clamps the due date
        # and the start date follows it.
        task = create_task(client, title="Rent",
                           due_date="2099-01-31", start_date="2099-01-28",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == "2099-02-28"
        assert spawned["start_date"] == "2099-02-25"

    def test_overdue_series_catches_up_with_start_in_past(self, client):
        # The due date catches up to >= today; the carried start date may then
        # lie before today, which just means "available since".
        task = create_task(client, title="Weekly",
                           due_date=(date.today() - timedelta(days=21)).isoformat(),
                           start_date=(date.today() - timedelta(days=27)).isoformat(),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        due = date.fromisoformat(spawned["due_date"])
        assert date.today() <= due < date.today() + timedelta(days=7)
        assert date.fromisoformat(spawned["start_date"]) == due - timedelta(days=6)

    def test_undated_series_gets_a_due_date(self, client):
        task = create_task(client, title="API-made",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        spawned = client.get("/api/deck").get_json()[0]
        assert spawned["due_date"] == _future(1)
        assert spawned["start_date"] is None


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

    def test_reorder_updates_can_clear_the_field(self, client):
        """Dragging a task out of a manual order (Focus's Pinned, Open's
        Sorted) clears the field (null) in the same request that renumbers
        the rest."""
        t1 = create_task(client, title="First").get_json()
        t2 = create_task(client, title="Second").get_json()
        client.post("/api/deck/reorder", json={"order": [t1["id"], t2["id"]]})
        client.post("/api/deck/reorder", json={"order": [t1["id"], t2["id"]], "field": "focus_order"})
        resp = client.post("/api/deck/reorder", json={
            "order": [t2["id"]], "updates": [{"id": t1["id"], "focus_order": None}],
            "field": "focus_order"})
        assert resp.status_code == 204
        by_id = {t["id"]: t for t in client.get("/api/deck").get_json()}
        assert by_id[t1["id"]]["focus_order"] is None
        assert by_id[t2["id"]]["focus_order"] == 0
        # sort_order the same way: the task goes back to Open's Unsorted section.
        client.post("/api/deck/reorder", json={"updates": [{"id": t1["id"], "sort_order": None}]})
        by_id = {t["id"]: t for t in client.get("/api/deck").get_json()}
        assert by_id[t1["id"]]["sort_order"] is None
        assert by_id[t2["id"]]["sort_order"] == 1

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


# ---------- focus_order (the Focus tab's arrangement) on update ----------


class TestFocusOrderReset:
    def arranged(self, client, start):
        task = create_task(client, title="Arranged", start_date=start).get_json()
        client.post("/api/deck/reorder", json={"order": [task["id"]], "field": "focus_order"})
        return task["id"]

    def focus_order_after(self, client, tid, **data):
        return client.put(f"/api/deck/{tid}", json=data).get_json()["focus_order"]

    def test_moving_to_a_later_day_clears_it(self, client):
        today = date.today()
        tid = self.arranged(client, today.isoformat())
        tomorrow = (today + timedelta(days=1)).isoformat()
        assert self.focus_order_after(client, tid, start_date=tomorrow) is None

    def test_moving_to_today_or_earlier_keeps_it(self, client):
        today = date.today()
        tid = self.arranged(client, (today - timedelta(days=2)).isoformat())
        assert self.focus_order_after(client, tid, start_date=today.isoformat()) == 0
        yesterday = (today - timedelta(days=1)).isoformat()
        assert self.focus_order_after(client, tid, start_date=yesterday) == 0

    def test_other_edits_keep_it(self, client):
        tid = self.arranged(client, date.today().isoformat())
        assert self.focus_order_after(client, tid, title="Renamed") == 0
        later = (date.today() + timedelta(days=9)).isoformat()
        assert self.focus_order_after(client, tid, due_date=later) == 0

    def test_unplanning_clears_it(self, client):
        tid = self.arranged(client, date.today().isoformat())
        assert self.focus_order_after(client, tid, start_date=None) is None

    def test_explicit_focus_order_wins(self, client):
        # Undo of a move sends the old date and the old place together.
        tid = self.arranged(client, date.today().isoformat())
        assert self.focus_order_after(client, tid, start_date=None) is None
        assert self.focus_order_after(client, tid, start_date=date.today().isoformat(),
                                      focus_order=0) == 0


# ---------- Completed Count ----------


class TestCompletedCount:
    def test_counts_completed_and_wont_do(self, client):
        assert client.get("/api/deck/completed-count").get_json() == {"count": 0}
        a = create_task(client, title="A").get_json()["id"]
        b = create_task(client, title="B").get_json()["id"]
        create_task(client, title="C")
        client.put(f"/api/deck/{a}", json={"completed": True})
        client.put(f"/api/deck/{b}", json={"wont_do": True})
        assert client.get("/api/deck/completed-count").get_json() == {"count": 2}


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


class TestIndependentDates:
    """due_date and start_date are two independent fields: neither is derived
    from the other, and changing one never moves the other."""

    def test_moving_due_date_leaves_start_date(self, client):
        tid = create_task(client, due_date="2027-07-01", start_date="2027-06-21").get_json()["id"]
        t = client.put(f"/api/deck/{tid}", json={"due_date": "2027-08-01"}).get_json()
        assert t["start_date"] == "2027-06-21"

    def test_start_date_after_due_is_kept(self, client):
        t = create_task(client, due_date="2027-07-01", start_date="2027-07-05").get_json()
        assert t["start_date"] == "2027-07-05"

    def test_old_fields_in_payload_are_ignored(self, client):
        # focus_date / defer_days / rec_anchor are gone from the API.
        t = create_task(client, due_date="2027-07-01", focus_date="2027-06-01",
                        defer_days=3, rec_type="daily", rec_anchor="plan").get_json()
        assert t["start_date"] is None
        assert "focus_date" not in t and "defer_days" not in t
        assert "anchor" not in t["recurrence"]


class TestDateModelMigration:
    """Decks written before start_date: focus_date → start_date, defer_days and
    recurrence.anchor dropped — on read and, at startup, on disk."""

    OLD = {"id": "old-1", "title": "Old", "description": "", "tags": [],
           "due_date": "2027-07-01", "defer_days": 3, "focus_date": "2027-06-28",
           "created_at": "2026-01-01T00:00:00", "completed_at": None,
           "wont_do": False, "focus_order": None, "sort_order": 0, "page": None,
           "recurrence": {"type": "weekly", "interval": 1, "from": "due", "anchor": "due"},
           "spawned_from": None}

    def _assert_new(self, t):
        assert t["start_date"] == "2027-06-28"
        assert "focus_date" not in t and "defer_days" not in t
        assert t["recurrence"] == {"type": "weekly", "interval": 1, "from": "due"}

    def test_read_normalizes(self, client, wiki_dir):
        (wiki_dir / "_deck.json").write_text(json.dumps([self.OLD]), encoding="utf-8")
        [t] = client.get("/api/deck").get_json()
        self._assert_new(t)

    def test_write_persists_normalized(self, client, wiki_dir):
        (wiki_dir / "_deck.json").write_text(json.dumps([self.OLD]), encoding="utf-8")
        client.put("/api/deck/old-1", json={"title": "Renamed"})
        [t] = json.loads((wiki_dir / "_deck.json").read_text(encoding="utf-8"))
        self._assert_new(t)

    def test_startup_migration_rewrites_every_file(self, client, wiki_dir):
        done = {**self.OLD, "id": "old-2", "completed_at": "2026-02-01T00:00:00"}
        (wiki_dir / "_deck.json").write_text(json.dumps([self.OLD]), encoding="utf-8")
        (wiki_dir / "_deck_completed.json").write_text(json.dumps([done]), encoding="utf-8")
        (wiki_dir / "_deck_completed-2025.json").write_text(json.dumps([done]), encoding="utf-8")
        deck_module._migrate_deck_files()
        for name in ("_deck.json", "_deck_completed.json", "_deck_completed-2025.json"):
            [t] = json.loads((wiki_dir / name).read_text(encoding="utf-8"))
            self._assert_new(t)

    def test_startup_migration_leaves_new_files_untouched(self, client, wiki_dir):
        create_task(client, title="New", start_date="2027-01-01")
        path = wiki_dir / "_deck.json"
        before = path.stat().st_mtime_ns
        deck_module._migrate_deck_files()
        assert path.stat().st_mtime_ns == before

    def test_old_recurring_task_carries_gap(self, client, wiki_dir):
        old = {**self.OLD, "due_date": "2099-01-10", "focus_date": "2099-01-07"}
        (wiki_dir / "_deck.json").write_text(json.dumps([old]), encoding="utf-8")
        client.put("/api/deck/old-1", json={"completed": True})
        [spawned] = client.get("/api/deck").get_json()
        assert spawned["due_date"] == "2099-01-17"
        assert spawned["start_date"] == "2099-01-14"

    def test_old_plan_anchored_series_steps_start_date(self, client, wiki_dir):
        old = {**self.OLD, "due_date": None, "defer_days": None, "focus_date": "2099-01-07",
               "recurrence": {"type": "weekly", "interval": 1, "from": "due", "anchor": "plan"}}
        (wiki_dir / "_deck.json").write_text(json.dumps([old]), encoding="utf-8")
        client.put("/api/deck/old-1", json={"completed": True})
        [spawned] = client.get("/api/deck").get_json()
        assert spawned["start_date"] == "2099-01-14"
        assert spawned["due_date"] is None

    def test_restore_accepts_old_snapshot(self, client):
        resp = client.post("/api/deck/restore", json={"tasks": [self.OLD]})
        assert resp.status_code == 201
        self._assert_new(resp.get_json()[0])


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

    def test_yearly_spawn_carries_start_gap(self, client):
        tid = client.post("/api/deck", json={
            "title": "Annual+lead", "due_date": "2026-07-01", "start_date": "2026-06-21",
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/deck").get_json()
                   if t["id"] != tid and not t["completed_at"]][0]
        assert spawned["due_date"] == "2027-07-01"
        assert spawned["start_date"] == "2027-06-21"


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


class TestStartDate:
    def test_create_with_start_date(self, client):
        resp = create_task(client, title="Planned", start_date="2026-07-20")
        assert resp.status_code == 201
        assert resp.get_json()["start_date"] == "2026-07-20"

    def test_create_default_start_date_none(self, client):
        assert create_task(client, title="No plan").get_json()["start_date"] is None

    def test_create_invalid_start_date(self, client):
        resp = create_task(client, title="Bad", start_date="20-07-2026")
        assert resp.status_code == 400

    def test_update_start_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"start_date": "2026-07-21"})
        assert resp.get_json()["start_date"] == "2026-07-21"

    def test_clear_start_date(self, client):
        task = create_task(client, title="Task", start_date="2026-07-20").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"start_date": None})
        assert resp.get_json()["start_date"] is None

    def test_update_invalid_start_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"start_date": "not-a-date"})
        assert resp.status_code == 400

    def test_recurring_spawn_keeps_zero_gap(self, client):
        task = create_task(client, title="Recurring",
                           due_date="2099-06-10", start_date="2099-06-10",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["start_date"] == spawned["due_date"] == "2099-06-11"


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
            due_date="2026-08-01",
            start_date="2026-08-01", list="  Some List ",
            rec_type="weekly", rec_interval=3,
        )
        assert resp.status_code == 201
        t = resp.get_json()
        assert t["title"] == "Full"
        assert t["description"] == "notes"
        assert t["tags"] == ["a", "b"]
        assert t["due_date"] == "2026-08-01"
        assert t["start_date"] == "2026-08-01"
        assert t["list"] == "some-list"
        assert t["recurrence"] == {"type": "weekly", "interval": 3, "from": "due"}
        assert t["sort_order"] is None
        assert t["completed_at"] is None
        assert t["wont_do"] is False

    def test_create_defaults(self, client):
        t = create_task(client, title="Bare").get_json()
        assert t["recurrence"] is None
        assert "blocked_by" not in t
        assert t["tags"] == []
        assert t["due_date"] is None
        assert t["start_date"] is None


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


class TestRestore:
    def test_restore_roundtrips_all_fields(self, client):
        task = create_task(client, title="Full", description="notes",
                           tags=["a"], due_date="2026-09-01",
                           start_date="2026-08-30", list="some-list",
                           rec_type="weekly", rec_interval=2).get_json()
        client.delete(f"/api/deck/{task['id']}")
        assert client.get("/api/deck").get_json() == []
        resp = client.post("/api/deck/restore", json={"tasks": [task]})
        assert resp.status_code == 201
        restored = client.get("/api/deck").get_json()[0]
        assert restored["id"] == task["id"]
        assert restored["created_at"] == task["created_at"]
        for field in ("title", "description", "tags", "due_date",
                      "start_date", "list", "recurrence"):
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

# ── due-count ────────────────────────────────────────────────────────────────


class TestDueCount:
    """Nothing is hidden any more, so due-count reflects every dated task."""

    def test_overdue_task_counted(self, client):
        past = (date.today() - timedelta(days=2)).isoformat()
        create_task(client, title="Visible overdue", due_date=past)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["overdue"] == 1

    def test_overdue_task_with_future_plan_still_counted(self, client):
        # Planning an overdue task for later doesn't make the deadline go away.
        past = (date.today() - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        create_task(client, title="Planned later", due_date=past, start_date=future)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["overdue"] == 1

    def test_started_task_counted_when_due(self, client):
        today = date.today().isoformat()
        started = (date.today() - timedelta(days=3)).isoformat()
        create_task(client, title="Due today", due_date=today, start_date=started)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["today"] == 1

    def test_future_due_not_counted(self, client):
        future = (date.today() + timedelta(days=10)).isoformat()
        create_task(client, title="Far due", due_date=future, start_date=future)
        counts = client.get("/api/deck/due-count").get_json()
        assert counts["count"] == 0


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


class TestMalformedInput:
    """Regression: malformed JSON bodies raised inside the handlers (500)
    instead of being rejected with a 400 the client can show."""

    @pytest.mark.parametrize("path", ["/api/deck", "/api/deck/batch-delete",
                                      "/api/deck/batch-complete", "/api/deck/restore",
                                      "/api/deck/reorder", "/api/deck/batch-tag"])
    @pytest.mark.parametrize("body", ["[1, 2]", '"text"', "not json", "null"])
    def test_non_object_body_is_400(self, client, path, body):
        resp = client.post(path, data=body, content_type="application/json")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_update_non_object_body_is_400(self, client):
        task = create_task(client).get_json()
        assert client.put(f"/api/deck/{task['id']}", data="[]",
                          content_type="application/json").status_code == 400

    @pytest.mark.parametrize("payload", [
        {"title": 5},
        {"title": "T", "description": ["x"]},
        {"title": "T", "tags": 7},
        {"title": "T", "tags": [1, 2]},
        {"title": "T", "due_date": 20990101},
        {"title": "T", "start_date": 20990101},
        {"title": "T", "rec_type": "daily", "rec_interval": "often"},
        {"title": "T", "list": 3},
    ])
    def test_bad_field_types_are_400(self, client, payload):
        resp = client.post("/api/deck", json=payload)
        assert resp.status_code == 400, payload
        assert client.get("/api/deck").get_json() == []

    def test_tags_string_is_split_on_commas(self, client):
        task = create_task(client, tags="a, b,,c").get_json()
        assert task["tags"] == ["a", "b", "c"]

    def test_numeric_strings_still_accepted(self, client):
        task = create_task(client, due_date="2099-01-10",
                           rec_type="weekly", rec_interval="3").get_json()
        assert task["recurrence"]["interval"] == 3

    @pytest.mark.parametrize("path", ["/api/deck/batch-delete", "/api/deck/batch-complete"])
    def test_batch_ids_must_be_strings(self, client, path):
        # An object in the list is unhashable → used to raise building set(ids).
        assert client.post(path, json={"ids": [{"id": "x"}]}).status_code == 400

    def test_batch_tag_bad_lists_are_400(self, client):
        task = create_task(client).get_json()
        assert client.post("/api/deck/batch-tag",
                           json={"ids": [task["id"]], "add": "x"}).status_code == 400
        assert client.post("/api/deck/batch-tag",
                           json={"ids": [task["id"]], "add": [1]}).status_code == 400

    def test_restore_bad_interval_degrades_instead_of_500(self, client):
        resp = client.post("/api/deck/restore", json={"tasks": [
            {"title": "T", "recurrence": {"type": "daily", "interval": "x"}}]})
        assert resp.status_code == 201
        assert resp.get_json()[0]["recurrence"]["interval"] == 1


class TestNoTaskLinking:
    """Task-to-task links (blocked_by / blocking) were removed. The API ignores
    them, and dependency data left in existing deck files has no effect."""

    def test_link_fields_are_ignored(self, client):
        other = create_task(client, title="Other").get_json()
        t = create_task(client, title="T", blocked_by=[other["id"]], blocking=[other["id"]]).get_json()
        assert "blocked_by" not in t
        resp = client.put(f"/api/deck/{t['id']}", json={"blocked_by": [t["id"]], "blocking": [t["id"]]})
        assert resp.status_code == 200          # a self-"cycle" is no longer an error
        for task in client.get("/api/deck").get_json():
            assert "blocked_by" not in task

    def test_legacy_blocked_by_does_not_hide_from_due_count(self, client, wiki_dir):
        past = (date.today() - timedelta(days=1)).isoformat()
        (wiki_dir / "_deck.json").write_text(json.dumps([
            {"id": "a", "title": "Blocker", "created_at": "2026-01-01T00:00:00"},
            {"id": "b", "title": "Was blocked", "due_date": past, "blocked_by": ["a"],
             "created_at": "2026-01-01T00:00:00"},
        ]), encoding="utf-8")
        assert client.get("/api/deck/due-count").get_json()["count"] == 1

    def test_legacy_blocked_by_survives_untouched_on_delete(self, client, wiki_dir):
        # Deleting a task no longer rewrites other tasks' dependency lists.
        (wiki_dir / "_deck.json").write_text(json.dumps([
            {"id": "a", "title": "A", "created_at": "2026-01-01T00:00:00"},
            {"id": "b", "title": "B", "blocked_by": ["a"], "created_at": "2026-01-01T00:00:00"},
        ]), encoding="utf-8")
        client.delete("/api/deck/a")
        assert client.get("/api/deck").get_json() == [
            {"id": "b", "title": "B", "blocked_by": ["a"], "created_at": "2026-01-01T00:00:00"}]

    def test_restore_drops_link_fields(self, client):
        resp = client.post("/api/deck/restore", json={"tasks": [
            {"id": "x", "title": "T", "blocked_by": ["y"], "blocking": ["z"]}]})
        assert "blocked_by" not in resp.get_json()[0]


# ---------- Tags are one word each ----------


class TestTagCleaning:
    """Regression: a tag with a space ("locallm xxx") could never be matched,
    because the filter field splits on spaces."""

    def test_create_and_update_clean_tags(self, client):
        t = create_task(client, title="T", tags=["locallm xxx", "#work", " ", "Work", "a  b"]).get_json()
        assert t["tags"] == ["locallm-xxx", "work", "a-b"]
        t = client.put(f"/api/deck/{t['id']}", json={"tags": "local llm, #home"}).get_json()
        assert t["tags"] == ["local-llm", "home"]

    def test_batch_tag_add_and_remove_clean_tags(self, client):
        tid = create_task(client, title="T").get_json()["id"]
        [t] = client.post("/api/deck/batch-tag", json={"ids": [tid], "add": ["local llm"]}).get_json()
        assert t["tags"] == ["local-llm"]
        # Removing by the spelling with a space finds the stored one.
        [t] = client.post("/api/deck/batch-tag", json={"ids": [tid], "remove": ["local llm"]}).get_json()
        assert t["tags"] == []

    def test_restore_cleans_tags(self, client):
        snap = {"id": "snap-1", "title": "Restored", "tags": ["a b", "#c"]}
        [t] = client.post("/api/deck/restore", json={"tasks": [snap]}).get_json()
        assert t["tags"] == ["a-b", "c"]

    def test_old_file_is_cleaned_on_read_and_migrated_once(self, client, wiki_dir):
        old = {"id": "old-tags", "title": "Old", "tags": ["locallm xxx", "x"],
               "created_at": "2026-01-01T00:00:00"}
        path = wiki_dir / "_deck.json"
        path.write_text(json.dumps([old]), encoding="utf-8")
        [t] = client.get("/api/deck").get_json()
        assert t["tags"] == ["locallm-xxx", "x"]
        deck_module._migrate_deck_files()
        [stored] = json.loads(path.read_text(encoding="utf-8"))
        assert stored["tags"] == ["locallm-xxx", "x"]
        before = path.stat().st_mtime_ns
        deck_module._migrate_deck_files()
        assert path.stat().st_mtime_ns == before


# ---------- Separate lists ----------


class TestLists:
    """Tasks with a `list` live only in their [[deck list=…]] embed: the main
    deck, due badge and completed count leave them out."""

    def test_list_is_cleaned_and_cleared(self, client):
        task = create_task(client, list="  #Grocery Run ").get_json()
        assert task["list"] == "grocery-run"
        resp = client.put(f"/api/deck/{task['id']}", json={"list": ""})
        assert resp.get_json()["list"] is None

    def test_list_must_be_string(self, client):
        assert create_task(client, list=5).status_code == 400

    def test_new_task_has_no_list(self, client):
        assert create_task(client).get_json()["list"] is None

    def test_due_count_ignores_list_tasks(self, client):
        today = date.today().isoformat()
        create_task(client, due_date=today)
        create_task(client, due_date=today, list="groceries")
        assert client.get("/api/deck/due-count").get_json()["count"] == 1

    def test_completed_count_per_list(self, client):
        for lst in (None, "groceries", "groceries"):
            tid = create_task(client, list=lst).get_json()["id"]
            client.put(f"/api/deck/{tid}", json={"completed": True})
        assert client.get("/api/deck/completed-count").get_json()["count"] == 1
        assert client.get("/api/deck/completed-count?list=Groceries").get_json()["count"] == 2

    def test_recurring_spawn_keeps_list(self, client):
        tid = create_task(client, list="chores", due_date=date.today().isoformat(),
                          rec_type="weekly").get_json()["id"]
        client.put(f"/api/deck/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/deck").get_json() if t["spawned_from"] == tid]
        assert spawned and spawned[0]["list"] == "chores"

    def test_restore_keeps_list(self, client):
        task = create_task(client, list="chores").get_json()
        client.delete(f"/api/deck/{task['id']}")
        restored = client.post("/api/deck/restore", json={"tasks": [task]}).get_json()
        assert restored[0]["list"] == "chores"

    def test_embed_carries_list_and_suggestions(self, client, wiki_dir):
        create_task(client, list="books")
        (wiki_dir / "shop.md").write_text("# Shop\n\n[[deck list=Groceries]]\n", encoding="utf-8")
        body = client.get("/page/shop").get_data(as_text=True)
        assert 'data-init-list="groceries"' in body
        assert '<option value="books">' in body

    def test_search_links_to_list(self, client):
        tid = create_task(client, title="Buy oat milk", list="groceries").get_json()["id"]
        hit = next(r for r in client.get("/api/search?q=oat").get_json() if r.get("task_id") == tid)
        assert hit["href"] == f"/deck?task={tid}&view=open&list=groceries"
