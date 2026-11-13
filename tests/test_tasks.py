"""Comprehensive tests for the Zone task app API."""

import json
import os
import re
import subprocess
import threading
from datetime import date, timedelta

import pytest

from tests import settings
from tests.conftest import create_task, git_setup


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
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_read_tasks_after_create(self, client):
        create_task(client, title="Task A")
        create_task(client, title="Task B")
        resp = client.get("/api/tasks")
        tasks = resp.get_json()
        assert len(tasks) == 2
        titles = {t["title"] for t in tasks}
        assert titles == {"Task A", "Task B"}

    def test_update_task(self, client):
        task = create_task(client, title="Old title").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"title": "New title"})
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "New title"

    def test_update_description_and_tags(self, client):
        task = create_task(client, title="Tagged").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={
            "description": "Some notes",
            "tags": ["work", "urgent"],
        })
        data = resp.get_json()
        assert data["description"] == "Some notes"
        assert data["tags"] == ["work", "urgent"]

    def test_delete_task(self, client):
        task = create_task(client, title="To delete").get_json()
        resp = client.delete(f"/api/tasks/{task['id']}")
        assert resp.status_code == 204
        remaining = client.get("/api/tasks").get_json()
        assert len(remaining) == 0

    def test_delete_nonexistent_task(self, client):
        resp = client.delete("/api/tasks/nonexistent-id")
        assert resp.status_code == 204

    def test_update_nonexistent_task(self, client):
        resp = client.put("/api/tasks/nonexistent-id", json={"title": "Nope"})
        assert resp.status_code == 404


# ---------- Validation ----------


class TestValidation:
    def test_create_empty_title(self, client):
        resp = client.post("/api/tasks", json={"title": ""})
        assert resp.status_code == 400

    def test_create_whitespace_title(self, client):
        resp = client.post("/api/tasks", json={"title": "   "})
        assert resp.status_code == 400

    def test_create_missing_title(self, client):
        resp = client.post("/api/tasks", json={})
        assert resp.status_code == 400

    def test_update_empty_title(self, client):
        task = create_task(client, title="Valid").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"title": ""})
        assert resp.status_code == 400

    def test_create_invalid_due_date(self, client):
        resp = client.post("/api/tasks", json={"title": "Bad date", "due_date": "not-a-date"})
        assert resp.status_code == 400

    def test_update_invalid_due_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"due_date": "31-12-2025"})
        assert resp.status_code == 400

    def test_create_valid_due_date(self, client):
        resp = create_task(client, title="Dated", due_date="2025-06-15")
        assert resp.status_code == 201
        assert resp.get_json()["due_date"] == "2025-06-15"

    def test_null_date_clears_field(self, client):
        task = create_task(client, title="Has date", due_date="2025-06-15").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"due_date": None})
        assert resp.get_json()["due_date"] is None

    def test_empty_string_date_clears_field(self, client):
        task = create_task(client, title="Has date", due_date="2025-06-15").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"due_date": ""})
        assert resp.get_json()["due_date"] is None

    def test_status_and_keep_are_true_or_false(self, client):
        """1, "yes" or null is no bool: refused, the task as it was."""
        task = create_task(client, title="Task").get_json()
        for field, value in [("completed", 1), ("wont_do", "yes"), ("skip", None), ("keep", 0)]:
            resp = client.put(f"/api/tasks/{task['id']}", json={field: value})
            assert resp.status_code == 400
            assert resp.get_json()["error"] == f"{field} must be true or false"
        resp = client.post("/api/tasks/batch-complete", json={"ids": [task["id"]], "completed": "no"})
        assert resp.get_json()["error"] == "completed must be true or false"
        [same] = client.get("/api/tasks").get_json()
        assert same["completed_at"] is None and same["keep"] is False

    def test_idx_must_be_whole(self, client):
        task = create_task(client, title="Task", description="- [ ] a").get_json()
        resp = client.post(f"/api/tasks/{task['id']}/toggle-check", json={"idx": 0.5})
        assert resp.get_json()["error"] == "idx must be an integer"
        resp = client.post(f"/api/tasks/{task['id']}/toggle-check", json={"idx": "0"})
        assert resp.get_json()["description"].startswith("- [x] a")


# ---------- Batch Delete ----------


class TestBatchDelete:
    def test_batch_delete_multiple(self, client):
        t1 = create_task(client, title="A").get_json()
        t2 = create_task(client, title="B").get_json()
        t3 = create_task(client, title="C").get_json()
        resp = client.post("/api/tasks/batch-delete", json={"ids": [t1["id"], t3["id"]]})
        assert resp.status_code == 204
        remaining = client.get("/api/tasks").get_json()
        assert len(remaining) == 1
        assert remaining[0]["id"] == t2["id"]

    def test_batch_delete_empty_ids(self, client):
        resp = client.post("/api/tasks/batch-delete", json={"ids": []})
        assert resp.status_code == 400

    def test_batch_delete_missing_ids(self, client):
        resp = client.post("/api/tasks/batch-delete", json={})
        assert resp.status_code == 400

    def test_batch_delete_nonexistent_ids(self, client):
        create_task(client, title="Keep me")
        resp = client.post("/api/tasks/batch-delete", json={"ids": ["no-such-id"]})
        assert resp.status_code == 204
        remaining = client.get("/api/tasks").get_json()
        assert len(remaining) == 1


# ---------- Completion Toggle ----------


class TestCompletion:
    def test_complete_task(self, client):
        task = create_task(client, title="Finish").get_json()
        assert task["completed_at"] is None
        resp = client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        data = resp.get_json()
        assert data["completed_at"] is not None

    def test_uncomplete_task(self, client):
        task = create_task(client, title="Undo").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        resp = client.put(f"/api/tasks/{task['id']}", json={"completed": False})
        data = resp.get_json()
        assert data["completed_at"] is None

    def test_complete_already_completed_noop(self, client):
        task = create_task(client, title="Already done").get_json()
        resp1 = client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        ts1 = resp1.get_json()["completed_at"]
        resp2 = client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        ts2 = resp2.get_json()["completed_at"]
        assert ts1 == ts2


# ---------- Recurring Tasks ----------


def _future(days):
    """Future date string — recurrence tests use future due dates so the
    catch-up rule (next due >= today) does not shift expectations."""
    return (date.today() + timedelta(days=days)).isoformat()


def _past(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _today():
    return date.today().isoformat()


class TestRecurring:
    def test_complete_recurring_spawns_new(self, client):
        task = create_task(client, title="Daily standup",
                           due_date=_future(10),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 1
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["title"] == "Daily standup"
        assert spawned["due_date"] == _future(11)
        assert spawned["completed_at"] is None

    def test_daily_recurrence_interval(self, client):
        task = create_task(client, title="Every 3 days",
                           due_date=_future(10),
                           rec_type="daily", rec_interval=3).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == _future(13)

    def test_weekly_recurrence(self, client):
        task = create_task(client, title="Weekly review",
                           due_date=_future(3),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == _future(10)

    def test_weekly_recurrence_interval(self, client):
        task = create_task(client, title="Biweekly",
                           due_date=_future(3),
                           rec_type="weekly", rec_interval=2).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == _future(17)

    def test_monthly_recurrence(self, client):
        task = create_task(client, title="Monthly report",
                           due_date="2099-01-15",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2099-02-15"

    def test_monthly_recurrence_day_clamped(self, client):
        # Jan 31 clamps to the real end of February
        task = create_task(client, title="End of month",
                           due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2099-02-28"

    def test_monthly_recurrence_leap_year(self, client):
        task = create_task(client, title="End of month",
                           due_date="2096-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2096-02-29"

    def test_monthly_recurrence_keeps_month_end_day(self, client):
        # The clamp is per-step, not sticky-forward: Mar 31 → Apr 30
        task = create_task(client, title="End of month",
                           due_date="2099-03-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2099-04-30"

    def _complete_and_get_spawn(self, client, task_id):
        client.put(f"/api/tasks/{task_id}", json={"completed": True})
        return next(t for t in client.get("/api/tasks").get_json()
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
        client.put(f"/api/tasks/{spawned['id']}", json={
            "title": "Month end", "due_date": "2099-02-28",
            "rec_type": "monthly", "rec_interval": 1})
        nxt = self._complete_and_get_spawn(client, spawned["id"])
        assert nxt["due_date"] == "2099-03-31"

    def test_monthly_anchor_kept_when_only_start_date_changes(self, client):
        task = create_task(client, title="Month end", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        client.put(f"/api/tasks/{spawned['id']}", json={
            "due_date": "2099-02-28", "start_date": "2099-02-20",
            "rec_type": "monthly", "rec_interval": 1})
        assert self._complete_and_get_spawn(client, spawned["id"])["due_date"] == "2099-03-31"

    def test_monthly_anchor_resets_when_due_date_moved(self, client):
        task = create_task(client, title="Rent", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={
            "due_date": "2099-01-15", "rec_type": "monthly", "rec_interval": 1})
        nxt = self._complete_and_get_spawn(client, task["id"])
        assert nxt["due_date"] == "2099-02-15"

    def test_monthly_anchor_resets_when_only_the_date_is_moved(self, client):
        # The date picker, a drag onto a day and MCP send the date alone, no
        # rec_type: moving it re-anchors the series as the edit modal does.
        task = create_task(client, title="Rent", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        assert spawned["recurrence"]["day"] == 31
        client.put(f"/api/tasks/{spawned['id']}", json={"due_date": "2099-03-10"})
        assert self._complete_and_get_spawn(client, spawned["id"])["due_date"] == "2099-04-10"

    def test_undo_of_a_date_move_keeps_the_day_of_month(self, client):
        # Undo sends the old recurrence with the old date: the series on the
        # 31st stays there, though its Feb 28 date alone would re-anchor it.
        task = create_task(client, title="Rent", due_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        old = {"due_date": spawned["due_date"], "recurrence": spawned["recurrence"],
               "history": spawned["history"]}
        client.put(f"/api/tasks/{spawned['id']}", json={"due_date": "2099-03-10"})
        undone = client.put(f"/api/tasks/{spawned['id']}", json=old).get_json()
        assert undone["recurrence"] == spawned["recurrence"]
        assert undone["history"] == spawned["history"]
        assert self._complete_and_get_spawn(client, spawned["id"])["due_date"] == "2099-03-31"

    def test_undo_recurrence_via_batch_update_and_validation(self, client):
        task = create_task(client, title="Rent", start_date="2099-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        spawned = self._complete_and_get_spawn(client, task["id"])
        client.post("/api/tasks/batch-update", json={"tasks": [{"id": spawned["id"], "start_date": "2099-03-10"}]})
        client.post("/api/tasks/batch-update", json={"tasks": [
            {"id": spawned["id"], "start_date": "2099-02-28", "recurrence": spawned["recurrence"]}]})
        assert self._complete_and_get_spawn(client, spawned["id"])["start_date"] == "2099-03-31"
        resp = client.put(f"/api/tasks/{task['id']}", json={"recurrence": {"type": "hourly"}})
        assert resp.status_code == 400

    def test_rec_interval_is_capped(self, client):
        # A huge interval stepped a date past year 9999: completing failed.
        resp = create_task(client, title="Rare", due_date="2099-01-01",
                           rec_type="yearly", rec_interval=100000)
        assert resp.status_code == 400
        task = create_task(client, title="Rare", due_date="2099-01-01",
                           rec_type="daily", rec_interval=999).get_json()
        assert self._complete_and_get_spawn(client, task["id"])["due_date"] == "2101-09-27"

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
        client.put(f"/api/tasks/{task['id']}", json={"tags": ["work", "standup"]})
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["tags"] == ["work", "standup"]

    def test_spawned_task_preserves_recurrence(self, client):
        task = create_task(client, title="Recurring",
                           due_date=_future(10),
                           rec_type="weekly", rec_interval=2).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["recurrence"] == {"type": "weekly", "interval": 2, "from": "due"}

    def test_complete_non_recurring_no_spawn(self, client):
        task = create_task(client, title="One-off").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 0

    def test_start_only_series_steps_start_date(self, client):
        # No due date: the start date drives the series.
        task = create_task(client, title="Weekly start",
                           start_date=_future(7),
                           rec_type="weekly", rec_interval=1).get_json()
        assert "anchor" not in task["recurrence"]
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        active = client.get("/api/tasks").get_json()
        assert len(active) == 1
        assert active[0]["start_date"] == _future(14)
        assert active[0]["due_date"] is None

    def test_start_only_series_from_completion(self, client):
        task = create_task(client, title="Interval habit",
                           start_date=_future(7),
                           rec_type="weekly", rec_interval=1,
                           rec_from="completion").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["start_date"] == _future(7)
        assert spawned["due_date"] is None

    def test_start_only_stale_series_catches_up_to_today(self, client):
        # A long-past start date must not spawn an instance starting in the past.
        task = create_task(client, title="Stale weekly",
                           start_date=(date.today() - timedelta(days=30)).isoformat(),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        started = date.fromisoformat(spawned["start_date"])
        assert date.today() <= started < date.today() + timedelta(days=7)
        # Still on the original weekday cadence.
        assert (started - date.fromisoformat(task["start_date"])).days % 7 == 0

    def test_both_dates_carry_the_gap(self, client):
        # Due drives the series; the start date keeps its distance to it.
        task = create_task(client, title="Report",
                           due_date=_future(10), start_date=_future(7),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == _future(17)
        assert spawned["start_date"] == _future(14)

    def test_gap_carried_from_completion(self, client):
        task = create_task(client, title="Habit",
                           due_date=_future(10), start_date=_future(8),
                           rec_type="daily", rec_interval=5,
                           rec_from="completion").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == _future(5)
        assert spawned["start_date"] == _future(3)

    def test_gap_carried_across_month_end_clamp(self, client):
        # Start 3 days before a month-end due date; Feb clamps the due date
        # and the start date follows it.
        task = create_task(client, title="Rent",
                           due_date="2099-01-31", start_date="2099-01-28",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == "2099-02-28"
        assert spawned["start_date"] == "2099-02-25"

    def test_overdue_series_catches_up_with_start_not_in_past(self, client):
        # The due date catches up to >= today; the carried start date is then
        # never before today (the gap is remembered for the next instance).
        task = create_task(client, title="Weekly",
                           due_date=(date.today() - timedelta(days=21)).isoformat(),
                           start_date=(date.today() - timedelta(days=27)).isoformat(),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        due = date.fromisoformat(spawned["due_date"])
        assert date.today() <= due < date.today() + timedelta(days=7)
        assert date.fromisoformat(spawned["start_date"]) == max(due - timedelta(days=6), date.today())

    def test_undated_series_gets_a_due_date(self, client):
        task = create_task(client, title="API-made",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == _future(1)
        assert spawned["start_date"] is None


# ---------- Won't Do ----------


class TestWontDo:
    def test_create_default_wont_do_false(self, client):
        assert create_task(client, title="T").get_json()["wont_do"] is False

    def test_mark_wont_do_sets_flag_and_timestamp(self, client):
        task = create_task(client, title="Drop me").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"wont_do": True})
        data = resp.get_json()
        assert data["wont_do"] is True
        assert data["completed_at"] is not None

    def test_mark_wont_do_removes_from_active(self, client):
        task = create_task(client, title="Drop me").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": True})
        assert client.get("/api/tasks").get_json() == []

    def test_wont_do_appears_in_completed(self, client):
        task = create_task(client, title="Drop me").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": True})
        completed = client.get("/api/tasks/completed").get_json()
        assert len(completed) == 1
        assert completed[0]["id"] == task["id"]
        assert completed[0]["wont_do"] is True

    def test_wont_do_does_not_spawn_recurring(self, client):
        task = create_task(client, title="Repeating",
                           due_date="2025-03-01",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": True})
        # No next instance should be created (unlike completion)
        assert client.get("/api/tasks").get_json() == []

    def test_reopen_wont_do_restores_to_active(self, client):
        task = create_task(client, title="Bring back").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": True})
        resp = client.put(f"/api/tasks/{task['id']}", json={"wont_do": False})
        data = resp.get_json()
        assert data["wont_do"] is False
        assert data["completed_at"] is None
        active = client.get("/api/tasks").get_json()
        assert len(active) == 1
        assert active[0]["id"] == task["id"]

    def test_completing_clears_wont_do_flag(self, client):
        """Uncompleting then completing normally leaves wont_do false."""
        task = create_task(client, title="Mixed").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": True})
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": False})
        resp = client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        assert resp.get_json()["wont_do"] is False


# ---------- Undo Recurring ----------


class TestUndoRecurring:
    def test_uncomplete_recurring_removes_spawned(self, client):
        task = create_task(client, title="Repeating",
                           due_date="2025-03-01",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 1

        # Uncomplete the original
        client.put(f"/api/tasks/{task['id']}", json={"completed": False})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 1
        assert tasks[0]["id"] == task["id"]
        assert tasks[0]["completed_at"] is None

    def test_reopen_keeps_an_edited_next_occurrence(self, client):
        # Reopening a done recurring task (from Completed, days later)
        # deleted the next occurrence with the notes and comments added to it.
        task = create_task(client, title="Weekly", due_date=date.today().isoformat(),
                           rec_type="weekly").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        nxt = next(t for t in client.get("/api/tasks").get_json()
                   if t["spawned_from"] == task["id"])
        client.post(f"/api/tasks/{nxt['id']}/comments", json={"text": "called Bob"})
        client.put(f"/api/tasks/{task['id']}", json={"completed": False})
        ids = {t["id"] for t in client.get("/api/tasks").get_json()}
        assert ids == {task["id"], nxt["id"]}
        # Completing it again spawns no second occurrence.
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        client.post("/api/tasks/batch-complete", json={"ids": [task["id"]], "completed": False})
        client.post("/api/tasks/batch-complete", json={"ids": [task["id"]], "completed": True})
        assert [t["id"] for t in client.get("/api/tasks").get_json()] == [nxt["id"]]

    @pytest.mark.parametrize("change", [{"description": "notes"}, {"tags": ["x"]},
                                        {"title": "Renamed"}, {"due_date": "2099-01-01"}])
    def test_reopen_keeps_next_occurrence_changed_in_any_way(self, client, change):
        task = create_task(client, title="Daily", due_date=date.today().isoformat(),
                           rec_type="daily").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        nxt = client.get("/api/tasks").get_json()[0]
        client.put(f"/api/tasks/{nxt['id']}", json=change)
        client.put(f"/api/tasks/{task['id']}", json={"completed": False})
        assert len(client.get("/api/tasks").get_json()) == 2

    def test_uncomplete_non_recurring_no_side_effects(self, client):
        t1 = create_task(client, title="Task A").get_json()
        create_task(client, title="Task B")
        client.put(f"/api/tasks/{t1['id']}", json={"completed": True})
        client.put(f"/api/tasks/{t1['id']}", json={"completed": False})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 2


# ---------- Reorder ----------


class TestReorder:
    def test_reorder_updates_sort_order(self, client):
        t1 = create_task(client, title="First").get_json()
        t2 = create_task(client, title="Second").get_json()
        t3 = create_task(client, title="Third").get_json()

        resp = client.post("/api/tasks/reorder", json={
            "order": [t3["id"], t1["id"], t2["id"]]
        })
        assert resp.status_code == 204

        tasks = client.get("/api/tasks").get_json()
        by_id = {t["id"]: t for t in tasks}
        assert by_id[t3["id"]]["sort_order"] == 0
        assert by_id[t1["id"]]["sort_order"] == 1
        assert by_id[t2["id"]]["sort_order"] == 2

    def test_reorder_empty_order(self, client):
        resp = client.post("/api/tasks/reorder", json={"order": []})
        assert resp.status_code == 400

    def test_reorder_missing_order(self, client):
        resp = client.post("/api/tasks/reorder", json={})
        assert resp.status_code == 400

    def test_reorder_updates_sets_sparse_sort_order(self, client):
        """`updates` sets sort_order on the given ids only, without touching
        any other task's sort_order — used for reordering under a narrowed
        (tag/search/page-filtered) view so hidden tasks aren't disturbed."""
        t1 = create_task(client, title="First").get_json()
        t2 = create_task(client, title="Second").get_json()
        t3 = create_task(client, title="Third").get_json()
        client.post("/api/tasks/reorder", json={
            "order": [t1["id"], t2["id"], t3["id"]]
        })

        resp = client.post("/api/tasks/reorder", json={
            "updates": [{"id": t3["id"], "sort_order": 0.5}]
        })
        assert resp.status_code == 204

        tasks = client.get("/api/tasks").get_json()
        by_id = {t["id"]: t for t in tasks}
        assert by_id[t1["id"]]["sort_order"] == 0
        assert by_id[t2["id"]]["sort_order"] == 1
        assert by_id[t3["id"]]["sort_order"] == 0.5

    def test_reorder_updates_ignores_unknown_id(self, client):
        t1 = create_task(client, title="First").get_json()
        resp = client.post("/api/tasks/reorder", json={
            "updates": [{"id": "does-not-exist", "sort_order": 3}]
        })
        assert resp.status_code == 204
        tasks = client.get("/api/tasks").get_json()
        assert tasks[0]["id"] == t1["id"]

    def test_reorder_updates_ignores_non_numeric_sort_order(self, client):
        t1 = create_task(client, title="First").get_json()
        resp = client.post("/api/tasks/reorder", json={
            "updates": [{"id": t1["id"], "sort_order": "not-a-number"}]
        })
        assert resp.status_code == 204
        tasks = client.get("/api/tasks").get_json()
        assert tasks[0]["sort_order"] is None

    def test_reorder_updates_can_clear_the_field(self, client):
        """Dragging a task out of Sorted (on Open or Focus) clears its
        sort_order (null) in the same request that renumbers the rest."""
        t1 = create_task(client, title="First").get_json()
        t2 = create_task(client, title="Second").get_json()
        client.post("/api/tasks/reorder", json={"order": [t1["id"], t2["id"]]})
        resp = client.post("/api/tasks/reorder", json={
            "order": [t2["id"]], "updates": [{"id": t1["id"], "sort_order": None}]})
        assert resp.status_code == 204
        by_id = {t["id"]: t for t in client.get("/api/tasks").get_json()}
        assert by_id[t1["id"]]["sort_order"] is None
        assert by_id[t2["id"]]["sort_order"] == 0

    def test_reorder_has_no_focus_order(self, client):
        """Focus has no order of its own: the reorder API only knows sort_order."""
        t1 = create_task(client, title="First").get_json()
        resp = client.post("/api/tasks/reorder", json={"order": [t1["id"]], "field": "focus_order"})
        assert resp.status_code == 204
        task = client.get("/api/tasks").get_json()[0]
        assert task["sort_order"] == 0 and "focus_order" not in task

    def test_reorder_empty_updates_and_order(self, client):
        resp = client.post("/api/tasks/reorder", json={"order": [], "updates": []})
        assert resp.status_code == 400


# ---------- sort_order on update ----------


class TestSortOrderUpdate:
    def test_update_sort_order_to_null(self, client):
        task = create_task(client, title="Ordered").get_json()
        # Give it a sort order via reorder
        client.post("/api/tasks/reorder", json={"order": [task["id"]]})
        tasks = client.get("/api/tasks").get_json()
        assert tasks[0]["sort_order"] == 0

        # Reset to inbox (null)
        resp = client.put(f"/api/tasks/{task['id']}", json={"sort_order": None})
        assert resp.get_json()["sort_order"] is None

    def test_update_sort_order_to_value(self, client):
        task = create_task(client, title="Manual").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"sort_order": 5})
        assert resp.get_json()["sort_order"] == 5


# ---------- focus_order (the old Lineup) is gone ----------


class TestNoFocusOrder:
    def test_new_tasks_have_none(self, client):
        assert "focus_order" not in create_task(client).get_json()

    def test_put_ignores_it(self, client):
        tid = create_task(client, start_date=date.today().isoformat()).get_json()["id"]
        task = client.put(f"/api/tasks/{tid}", json={"focus_order": 3}).get_json()
        assert "focus_order" not in task

# ---------- Completed Count ----------


class TestCompletedCount:
    def test_counts_completed_and_wont_do(self, client):
        assert client.get("/api/tasks/completed-count").get_json() == {"count": 0}
        a = create_task(client, title="A").get_json()["id"]
        b = create_task(client, title="B").get_json()["id"]
        create_task(client, title="C")
        client.put(f"/api/tasks/{a}", json={"completed": True})
        client.put(f"/api/tasks/{b}", json={"wont_do": True})
        assert client.get("/api/tasks/completed-count").get_json() == {"count": 2}


# ---------- Due Count ----------


class TestDueCount:
    def test_due_count_empty(self, client):
        resp = client.get("/api/tasks/due-count")
        data = resp.get_json()
        assert data == {"count": 0, "overdue": 0, "today": 0}

    def test_due_count_today(self, client):
        today_str = date.today().isoformat()
        create_task(client, title="Due today", due_date=today_str)
        resp = client.get("/api/tasks/due-count")
        data = resp.get_json()
        assert data["today"] == 1
        assert data["overdue"] == 0
        assert data["count"] == 1

    def test_due_count_overdue(self, client):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        create_task(client, title="Overdue", due_date=yesterday)
        resp = client.get("/api/tasks/due-count")
        data = resp.get_json()
        assert data["overdue"] == 1
        assert data["today"] == 0
        assert data["count"] == 1

    def test_due_count_excludes_completed(self, client):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        task = create_task(client, title="Done overdue", due_date=yesterday).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        resp = client.get("/api/tasks/due-count")
        data = resp.get_json()
        assert data["count"] == 0

    def test_due_count_excludes_future(self, client):
        future = (date.today() + timedelta(days=7)).isoformat()
        create_task(client, title="Future", due_date=future)
        resp = client.get("/api/tasks/due-count")
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
        resp = client.get("/api/tasks/due-count")
        data = resp.get_json()
        assert data["today"] == 1
        assert data["overdue"] == 1
        assert data["count"] == 2


# ---------- Tasks Page ----------


class TestTasksPage:
    def test_tasks_embed_in_wiki_page(self, client, wiki_dir):
        """A wiki page with [[tasks:]] renders the full task app."""
        (wiki_dir / "tasks.md").write_text("# Tasks\n\n[[tasks:]]\n", encoding="utf-8")
        resp = client.get("/page/tasks")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "tasks-app" in body
        assert "task-add-btn" in body
        assert '<script type="module" src="/public/zonewiki.js"></script>' in body   # it loads tasks.js


# ── Dates ────────────────────────────────────────────────────────────────────


class TestIndependentDates:
    """due_date and start_date are two independent fields: neither is derived
    from the other, and changing one never moves the other."""

    def test_moving_due_date_leaves_start_date(self, client):
        tid = create_task(client, due_date="2027-07-01", start_date="2027-06-21").get_json()["id"]
        t = client.put(f"/api/tasks/{tid}", json={"due_date": "2027-08-01"}).get_json()
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


class TestYearlyRecurrence:
    def test_yearly_spawn_advances_one_year(self, client):
        tid = client.post("/api/tasks", json={
            "title": "Annual", "due_date": "2026-07-01",
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/tasks").get_json()
                   if t["id"] != tid and not t["completed_at"]]
        assert len(spawned) == 1
        assert spawned[0]["due_date"] == "2027-07-01"
        assert spawned[0]["recurrence"]["type"] == "yearly"

    def test_yearly_leap_day_clamps(self, client):
        tid = client.post("/api/tasks", json={
            "title": "Leap", "due_date": "2028-02-29",
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/tasks").get_json()
                   if t["id"] != tid and not t["completed_at"]]
        assert spawned[0]["due_date"] == "2029-02-28"

    def test_yearly_spawn_carries_start_gap(self, client):
        tid = client.post("/api/tasks", json={
            "title": "Annual+lead", "due_date": "2026-07-01", "start_date": "2026-06-21",
            "rec_type": "yearly", "rec_interval": 1,
        }).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        spawned = [t for t in client.get("/api/tasks").get_json()
                   if t["id"] != tid and not t["completed_at"]][0]
        assert spawned["due_date"] == "2027-07-01"
        assert spawned["start_date"] == "2027-06-21"


# ── Batch Complete ───────────────────────────────────────────────────────────


class TestBatchComplete:
    def test_batch_complete_multiple(self, client):
        r1 = client.post("/api/tasks", json={"title": "A"})
        r2 = client.post("/api/tasks", json={"title": "B"})
        id1, id2 = r1.get_json()["id"], r2.get_json()["id"]
        resp = client.post("/api/tasks/batch-complete", json={"ids": [id1, id2], "completed": True})
        assert resp.status_code == 200
        updated = resp.get_json()
        assert len(updated) == 2
        assert all(t["completed_at"] is not None for t in updated)
        # Verify via GET
        tasks = client.get("/api/tasks").get_json()
        for t in tasks:
            if t["id"] in (id1, id2):
                assert t["completed_at"] is not None

    def test_batch_wont_do(self, client):
        """wont_do closes them undone: no next occurrence, a keep task too."""
        ids = [client.post("/api/tasks", json=t).get_json()["id"] for t in (
            {"title": "A"},
            {"title": "Weekly", "due_date": "2026-01-05", "recurrence": {"type": "weekly", "interval": 1}},
            {"title": "Milk", "keep": True})]
        resp = client.post("/api/tasks/batch-complete", json={"ids": ids, "completed": True, "wont_do": True})
        assert resp.status_code == 200
        assert client.get("/api/tasks").get_json() == []          # nothing spawned, Milk gone too
        closed = client.get("/api/tasks/completed").get_json()
        assert sorted(t["title"] for t in closed) == ["A", "Milk", "Weekly"]
        assert all(t["wont_do"] and t["completed_at"] for t in closed)
        assert all(t["history"][-1]["event"] == "wontdo" for t in closed)
        # The undo: reopening them.
        client.post("/api/tasks/batch-complete", json={"ids": ids, "completed": False})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 3 and not any(t["wont_do"] or t["completed_at"] for t in tasks)
        assert client.get("/api/tasks/completed").get_json() == []

    def test_batch_wont_do_cant_reopen(self, client):
        tid = client.post("/api/tasks", json={"title": "A"}).get_json()["id"]
        resp = client.post("/api/tasks/batch-complete", json={"ids": [tid], "completed": False, "wont_do": True})
        assert resp.status_code == 400

    def test_batch_uncomplete(self, client):
        r1 = client.post("/api/tasks", json={"title": "A"})
        id1 = r1.get_json()["id"]
        client.put(f"/api/tasks/{id1}", json={"completed": True})
        resp = client.post("/api/tasks/batch-complete", json={"ids": [id1], "completed": False})
        assert resp.status_code == 200
        task = client.get("/api/tasks").get_json()[0]
        assert task["completed_at"] is None

    def test_batch_complete_empty_ids(self, client):
        resp = client.post("/api/tasks/batch-complete", json={"ids": [], "completed": True})
        assert resp.status_code == 400

    def test_batch_complete_spawns_recurring(self, client):
        r = client.post("/api/tasks", json={
            "title": "Recurring", "due_date": _future(10),
            "rec_type": "daily", "rec_interval": 1,
        })
        task_id = r.get_json()["id"]
        resp = client.post("/api/tasks/batch-complete", json={"ids": [task_id], "completed": True})
        assert resp.status_code == 200
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 1
        spawned = [t for t in tasks if t["id"] != task_id]
        assert spawned[0]["due_date"] == _future(11)
        assert spawned[0]["completed_at"] is None

    def test_batch_uncomplete_removes_spawned(self, client):
        r = client.post("/api/tasks", json={
            "title": "Recurring", "due_date": "2026-06-10",
            "rec_type": "daily", "rec_interval": 1,
        })
        task_id = r.get_json()["id"]
        client.post("/api/tasks/batch-complete", json={"ids": [task_id], "completed": True})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 1
        # Now uncomplete
        client.post("/api/tasks/batch-complete", json={"ids": [task_id], "completed": False})
        tasks = client.get("/api/tasks").get_json()
        assert len(tasks) == 1
        assert tasks[0]["id"] == task_id
        assert tasks[0]["completed_at"] is None

    def test_batch_complete_idempotent(self, client):
        r = client.post("/api/tasks", json={"title": "A"})
        task_id = r.get_json()["id"]
        client.put(f"/api/tasks/{task_id}", json={"completed": True})
        # Complete again — should return empty updated list (already completed)
        resp = client.post("/api/tasks/batch-complete", json={"ids": [task_id], "completed": True})
        assert resp.status_code == 200
        assert len(resp.get_json()) == 0


# ── Split completed storage (_deck.json / _deck_completed.json) ────────────────


class TestCompletedStorage:
    """Coverage for the active/completed file split introduced in 4c79e31."""

    def test_completed_endpoint_empty_initially(self, client):
        resp = client.get("/api/tasks/completed")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_complete_moves_task_to_completed_file(self, client):
        task = create_task(client, title="Finish me").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        # Gone from active
        active = client.get("/api/tasks").get_json()
        assert all(t["id"] != task["id"] for t in active)
        # Present in completed
        completed = client.get("/api/tasks/completed").get_json()
        assert len(completed) == 1
        assert completed[0]["id"] == task["id"]
        assert completed[0]["completed_at"] is not None

    def test_uncomplete_moves_task_back_to_active(self, client):
        task = create_task(client, title="Round trip").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        client.put(f"/api/tasks/{task['id']}", json={"completed": False})
        active = client.get("/api/tasks").get_json()
        completed = client.get("/api/tasks/completed").get_json()
        assert [t["id"] for t in active] == [task["id"]]
        assert completed == []

    def test_batch_complete_moves_to_completed_file(self, client):
        t1 = create_task(client, title="A").get_json()
        t2 = create_task(client, title="B").get_json()
        client.post("/api/tasks/batch-complete", json={"ids": [t1["id"], t2["id"]], "completed": True})
        assert client.get("/api/tasks").get_json() == []
        completed_ids = {t["id"] for t in client.get("/api/tasks/completed").get_json()}
        assert completed_ids == {t1["id"], t2["id"]}

    def test_update_completed_task_in_place(self, client):
        """Editing a completed task's fields persists without un-completing it."""
        task = create_task(client, title="Old", description="orig").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        resp = client.put(f"/api/tasks/{task['id']}", json={
            "title": "Edited", "description": "new notes",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Edited"
        assert data["description"] == "new notes"
        assert data["completed_at"] is not None  # still completed
        # Still only in the completed file, not active
        assert client.get("/api/tasks").get_json() == []
        completed = client.get("/api/tasks/completed").get_json()
        assert completed[0]["title"] == "Edited"

    def test_delete_completed_task(self, client):
        task = create_task(client, title="Del me").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        resp = client.delete(f"/api/tasks/{task['id']}")
        assert resp.status_code == 204
        assert client.get("/api/tasks/completed").get_json() == []

    def test_batch_delete_removes_completed_tasks(self, client):
        active_task = create_task(client, title="Active").get_json()
        done_task = create_task(client, title="Done").get_json()
        client.put(f"/api/tasks/{done_task['id']}", json={"completed": True})
        resp = client.post("/api/tasks/batch-delete", json={
            "ids": [active_task["id"], done_task["id"]],
        })
        assert resp.status_code == 204
        assert client.get("/api/tasks").get_json() == []
        assert client.get("/api/tasks/completed").get_json() == []

    def test_search_includes_completed_task(self, client):
        task = create_task(client, title="findable_completed_token").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        resp = client.get("/api/search?q=findable_completed_token",
                           headers={"Host": "localhost:5000"})
        results = resp.get_json()
        task_results = [r for r in results if r.get("type") == "task"]
        assert task_results
        assert task_results[0]["status"] == "done"


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
        resp = client.put(f"/api/tasks/{task['id']}", json={"start_date": "2026-07-21"})
        assert resp.get_json()["start_date"] == "2026-07-21"

    def test_clear_start_date(self, client):
        task = create_task(client, title="Task", start_date="2026-07-20").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"start_date": None})
        assert resp.get_json()["start_date"] is None

    def test_update_invalid_start_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"start_date": "not-a-date"})
        assert resp.status_code == 400

    def test_recurring_spawn_keeps_zero_gap(self, client):
        task = create_task(client, title="Recurring",
                           due_date="2099-06-10", start_date="2099-06-10",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        tasks = client.get("/api/tasks").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["start_date"] == spawned["due_date"] == "2099-06-11"


# ── Batch tag ──────────────────────────────────────────────────────────────────


class TestBatchTag:
    def test_add_tags_to_multiple(self, client):
        t1 = create_task(client, title="A").get_json()
        t2 = create_task(client, title="B").get_json()
        resp = client.post("/api/tasks/batch-tag", json={
            "ids": [t1["id"], t2["id"]], "add": ["work"],
        })
        assert resp.status_code == 200
        tasks = {t["id"]: t for t in client.get("/api/tasks").get_json()}
        assert "work" in tasks[t1["id"]]["tags"]
        assert "work" in tasks[t2["id"]]["tags"]

    def test_remove_tags(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"tags": ["work", "urgent"]})
        client.post("/api/tasks/batch-tag", json={"ids": [task["id"]], "remove": ["urgent"]})
        tags = client.get("/api/tasks").get_json()[0]["tags"]
        assert tags == ["work"]

    def test_add_and_remove_combined(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"tags": ["old"]})
        client.post("/api/tasks/batch-tag", json={
            "ids": [task["id"]], "add": ["new"], "remove": ["old"],
        })
        assert client.get("/api/tasks").get_json()[0]["tags"] == ["new"]

    def test_add_dedupes_case_insensitive(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"tags": ["Work"]})
        client.post("/api/tasks/batch-tag", json={"ids": [task["id"]], "add": ["work"]})
        assert client.get("/api/tasks").get_json()[0]["tags"] == ["Work"]

    def test_empty_ids_400(self, client):
        resp = client.post("/api/tasks/batch-tag", json={"ids": [], "add": ["x"]})
        assert resp.status_code == 400

    def test_no_add_or_remove_400(self, client):
        task = create_task(client, title="A").get_json()
        resp = client.post("/api/tasks/batch-tag", json={"ids": [task["id"]]})
        assert resp.status_code == 400

    def test_batch_tag_applies_to_completed_task(self, client):
        task = create_task(client, title="A").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        resp = client.post("/api/tasks/batch-tag", json={
            "ids": [task["id"]], "add": ["archived"],
        })
        assert resp.status_code == 200
        completed = client.get("/api/tasks/completed").get_json()
        assert "archived" in completed[0]["tags"]


# ── Batch update ───────────────────────────────────────────────────────────────


class TestBatchUpdate:
    def test_each_task_gets_its_own_fields(self, client):
        a = create_task(client, title="A", tags="x").get_json()
        b = create_task(client, title="B").get_json()
        resp = client.post("/api/tasks/batch-update", json={"tasks": [
            {"id": a["id"], "start_date": "2099-01-02"},
            {"id": b["id"], "tags": ["y", "z"]},
        ]})
        assert resp.status_code == 200
        assert {t["id"] for t in resp.get_json()} == {a["id"], b["id"]}
        tasks = {t["id"]: t for t in client.get("/api/tasks").get_json()}
        assert tasks[a["id"]]["start_date"] == "2099-01-02"
        assert tasks[a["id"]]["tags"] == ["x"]            # untouched
        assert tasks[b["id"]]["tags"] == ["y", "z"]
        assert tasks[b["id"]]["start_date"] is None

    def test_logs_and_restores_history(self, client):
        # A start date change is logged; an undo sends the old history back.
        a = create_task(client, title="A").get_json()
        client.post("/api/tasks/batch-update", json={"tasks": [{"id": a["id"], "start_date": "2099-01-02"}]})
        t = client.get("/api/tasks").get_json()[0]
        assert [e["event"] for e in t["history"]] == ["created", "start"]
        client.post("/api/tasks/batch-update", json={"tasks": [
            {"id": a["id"], "start_date": None, "history": a["history"]}]})
        t = client.get("/api/tasks").get_json()[0]
        assert t["start_date"] is None
        assert [e["event"] for e in t["history"]] == ["created"]

    def test_reaches_completed_tasks(self, client):
        a = create_task(client, title="A").get_json()
        client.put(f"/api/tasks/{a['id']}", json={"completed": True})
        client.post("/api/tasks/batch-update", json={"tasks": [{"id": a["id"], "tags": ["old"]}]})
        assert client.get("/api/tasks/completed").get_json()[0]["tags"] == ["old"]

    def test_unknown_ids_are_skipped(self, client):
        a = create_task(client, title="A").get_json()
        resp = client.post("/api/tasks/batch-update", json={"tasks": [
            {"id": "gone", "tags": ["x"]}, {"id": a["id"], "tags": ["y"]}]})
        assert [t["id"] for t in resp.get_json()] == [a["id"]]

    @pytest.mark.parametrize("body", [
        {}, {"tasks": []}, {"tasks": "x"}, {"tasks": [{"tags": ["x"]}]}, {"tasks": [{"id": 3}]},
    ])
    def test_bad_body_400(self, client, body):
        assert client.post("/api/tasks/batch-update", json=body).status_code == 400

    @pytest.mark.parametrize("key", ["completed", "wont_do", "skip"])
    def test_status_changes_are_refused(self, client, key):
        a = create_task(client, title="A").get_json()
        resp = client.post("/api/tasks/batch-update", json={"tasks": [{"id": a["id"], key: True}]})
        assert resp.status_code == 400
        assert client.get("/api/tasks").get_json()[0]["completed_at"] is None

    def test_a_bad_field_changes_nothing(self, client):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B").get_json()
        resp = client.post("/api/tasks/batch-update", json={"tasks": [
            {"id": a["id"], "tags": ["kept-out"]}, {"id": b["id"], "start_date": "not a date"}]})
        assert resp.status_code == 400
        assert all(not t["tags"] for t in client.get("/api/tasks").get_json())

    def test_one_commit(self, client, git_wiki_dir):
        a = create_task(client, title="A").get_json()
        b = create_task(client, title="B").get_json()
        log = lambda: subprocess.run(["git", "log", "--format=%s"], cwd=git_wiki_dir, capture_output=True,
                                     text=True, env=settings.GIT_ENV).stdout.splitlines()
        before = len(log())
        client.post("/api/tasks/batch-update", json={"tasks": [
            {"id": a["id"], "start_date": "2099-01-02"}, {"id": b["id"], "start_date": "2099-01-02"}]})
        assert log()[0] == "Update tasks"
        assert len(log()) == before + 1


# ---------- Concurrent-write safety (regression for commit 079930f) ----------
#
# The server handles requests concurrently, so two task mutations can run at once. The
# original storage layer did an unguarded read-modify-write on _deck.json with a
# truncate-in-place write, and read_tasks() swallowed a torn/empty read as an
# empty list. A create landing in that window read [], appended its one task,
# and overwrote every other task — exactly what wiped the tasks in production.


class TestConcurrentWriteSafety:
    def test_empty_tasks_file_is_not_overwritten(self, client, wiki_dir):
        """A zero-length _deck.json (a torn write) must not be treated as an
        empty list and clobbered — the create is refused with 503."""
        create_task(client, title="Keeper")
        tasks_file = wiki_dir / "_tasks" / "tasks.json"
        tasks_file.write_text("", encoding="utf-8")  # simulate a torn write
        resp = create_task(client, title="Would clobber")
        assert resp.status_code == 503
        # The file was left untouched, not overwritten with just the new task.
        assert tasks_file.read_text(encoding="utf-8") == ""

    def test_corrupt_tasks_file_is_not_overwritten(self, client, wiki_dir):
        create_task(client, title="Keeper")
        tasks_file = wiki_dir / "_tasks" / "tasks.json"
        tasks_file.write_text("{ this is not json", encoding="utf-8")
        resp = create_task(client, title="Would clobber")
        assert resp.status_code == 503
        assert tasks_file.read_text(encoding="utf-8") == "{ this is not json"

    def test_genuinely_empty_tasks_still_creates(self, client, wiki_dir):
        """An empty tasks is stored as '[]', which must still accept new tasks."""
        tasks_file = wiki_dir / "_tasks" / "tasks.json"
        tasks_file.write_text("[]", encoding="utf-8")
        resp = create_task(client, title="First")
        assert resp.status_code == 201
        assert [t["title"] for t in client.get("/api/tasks").get_json()] == ["First"]

    def test_concurrent_creates_lose_no_tasks(self, client, wiki_dir):
        """Many creates from many threads must all survive — no lost updates."""
        threads_n, per_thread = 8, 20

        def worker(tid):
            for i in range(per_thread):
                r = client.post("/api/tasks", json={"title": f"t{tid}-{i}"})
                assert r.status_code == 201

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        titles = [t["title"] for t in json.loads((wiki_dir / "_tasks" / "tasks.json").read_text())]
        assert len(titles) == threads_n * per_thread
        assert len(set(titles)) == threads_n * per_thread

    def test_tasks_file_always_valid_json_during_concurrent_writes(self, client, wiki_dir):
        """A reader hammering the file while writers run never sees a torn/empty
        file — atomic replace guarantees a complete document every time."""
        stop = threading.Event()
        errors = []

        def reader():
            while not stop.is_set():
                try:
                    raw = (wiki_dir / "_tasks" / "tasks.json").read_text(encoding="utf-8")
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
            for i in range(40):
                client.post("/api/tasks", json={"title": f"w{i}"})

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
        client.put(f"/api/tasks/{task_id}", json={"wont_do": True})
        client.post("/api/tasks/batch-complete", json={"ids": [task_id], "completed": False})
        active = client.get("/api/tasks").get_json()
        task = next(t for t in active if t["id"] == task_id)
        assert task["completed_at"] is None
        assert task["wont_do"] is False


class TestCreateFieldParity:
    def test_create_applies_all_fields(self, client):
        """Create route goes through _apply_task_fields — every field lands."""
        resp = create_task(
            client, title="  Full  ", description=" notes ", tags=[" a ", "", "b"],
            due_date="2026-08-01",
            start_date="2026-08-01",
            rec_type="weekly", rec_interval=3,
        )
        assert resp.status_code == 201
        t = resp.get_json()
        assert t["title"] == "Full"
        assert t["description"] == "notes"
        assert t["tags"] == ["a", "b"]
        assert t["due_date"] == "2026-08-01"
        assert t["start_date"] == "2026-08-01"
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
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == date.today().isoformat()

    def test_overdue_weekly_stays_calendar_aligned(self, client):
        """Catch-up advances in whole intervals from the original due date."""
        base = date.today() - timedelta(days=30)
        task = create_task(client, title="Overdue weekly", due_date=base.isoformat(),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
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
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == (date.today() + timedelta(days=3)).isoformat()

    def test_from_completion_without_due_date(self, client):
        task = create_task(client, title="Habit", rec_type="weekly",
                           rec_interval=1, rec_from="completion").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["due_date"] == (date.today() + timedelta(days=7)).isoformat()

    def test_rec_from_defaults_to_due(self, client):
        task = create_task(client, title="Rent", rec_type="monthly").get_json()
        assert task["recurrence"]["from"] == "due"


# ── spawned_from tracking ─────────────────────────────────────────────────────


class TestSpawnedFrom:
    def test_spawned_instance_carries_spawned_from(self, client):
        task = create_task(client, title="Recurring", due_date=_future(5),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        spawned = client.get("/api/tasks").get_json()[0]
        assert spawned["spawned_from"] == task["id"]

    def test_uncomplete_removes_only_exact_spawned_instance(self, client):
        """A same-titled recurring sibling created after completion survives —
        the old title/time heuristic would have removed it too."""
        task = create_task(client, title="Standup", due_date=_future(5),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        sibling = create_task(client, title="Standup", due_date=_future(6),
                              rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": False})
        active_ids = {t["id"] for t in client.get("/api/tasks").get_json()}
        assert task["id"] in active_ids
        assert sibling["id"] in active_ids
        spawned_left = [t for t in client.get("/api/tasks").get_json()
                        if t.get("spawned_from") == task["id"]]
        assert spawned_left == []

class TestRestore:
    def test_restore_roundtrips_all_fields(self, client):
        task = create_task(client, title="Full", description="notes",
                           tags=["a"], due_date="2026-09-01",
                           start_date="2026-08-30",
                           rec_type="weekly", rec_interval=2).get_json()
        client.delete(f"/api/tasks/{task['id']}")
        assert client.get("/api/tasks").get_json() == []
        resp = client.post("/api/tasks/restore", json={"tasks": [task]})
        assert resp.status_code == 201
        restored = client.get("/api/tasks").get_json()[0]
        assert restored["id"] == task["id"]
        assert restored["created_at"] == task["created_at"]
        for field in ("title", "description", "tags", "due_date",
                      "start_date", "recurrence"):
            assert restored[field] == task[field]

    def test_restore_keeps_yearly_recurrence(self, client):
        # Regression: the snapshot sanitizer whitelisted only daily/weekly/
        # monthly, so undoing the delete of a yearly task silently dropped
        # its recurrence.
        task = create_task(client, title="Birthday", due_date="2030-05-01",
                           rec_type="yearly", rec_interval=1).get_json()
        assert task["recurrence"]["type"] == "yearly"
        client.delete(f"/api/tasks/{task['id']}")
        client.post("/api/tasks/restore", json={"tasks": [task]})
        restored = client.get("/api/tasks").get_json()[0]
        assert restored["recurrence"] == task["recurrence"]

    def test_restore_drops_unknown_recurrence_type(self, client):
        resp = client.post("/api/tasks/restore", json={"tasks": [
            {"title": "T", "recurrence": {"type": "hourly", "interval": 1}}]})
        assert resp.get_json()[0]["recurrence"] is None

    def test_restore_completed_snapshot_lands_in_completed(self, client):
        task = create_task(client, title="Done one").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        snap = client.get("/api/tasks/completed").get_json()[0]
        client.delete(f"/api/tasks/{task['id']}")
        client.post("/api/tasks/restore", json={"tasks": [snap]})
        assert client.get("/api/tasks").get_json() == []
        completed = client.get("/api/tasks/completed").get_json()
        assert completed[0]["id"] == task["id"]
        assert completed[0]["completed_at"] == snap["completed_at"]

    def test_restore_skips_existing_ids(self, client):
        task = create_task(client, title="Still here").get_json()
        resp = client.post("/api/tasks/restore", json={"tasks": [task]})
        assert resp.status_code == 201
        assert resp.get_json() == []
        assert len(client.get("/api/tasks").get_json()) == 1

    def test_restore_drops_unknown_fields(self, client):
        snap = create_task(client, title="T").get_json()
        client.delete(f"/api/tasks/{snap['id']}")
        snap["evil"] = "payload"
        client.post("/api/tasks/restore", json={"tasks": [snap]})
        restored = client.get("/api/tasks").get_json()[0]
        assert "evil" not in restored

    def test_restore_takes_ids_and_times_only_as_zone_writes_them(self, client):
        # The browser builds the cards from them: an id that could be
        # markup gets a new one, a time that is none is now.
        evil = 'x"><form method=post action=/delete/index><button>ok</button></form>'
        resp = client.post("/api/tasks/restore", json={"tasks": [
            {"title": "Open", "id": evil, "created_at": "<b>", "spawned_from": evil,
             "history": [{"at": "2026-01-02T10:00", "event": "created", "by": "<i>"}],
             "comments": [{"id": evil, "at": "2026-01-02T10:00", "by": "<i>", "text": "hi"}]},
            {"title": "Done", "id": "<textarea>", "completed_at": "<textarea>"},
            {"title": "By hand", "id": "old-1", "created_at": "2025-03-04",
             "comments": [{"id": "c1", "at": "2026-01-02T10:00", "by": "alice", "text": "hi"}]}]})
        assert resp.status_code == 201
        stamp = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        uuid = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
        opened, done, by_hand = resp.get_json()
        assert "<" not in resp.get_data(as_text=True).replace("\\u003c", "<")
        assert uuid.match(opened["id"]) and stamp.match(opened["created_at"])
        assert opened["spawned_from"] is None
        assert "by" not in opened["history"][0] and "by" not in opened["comments"][0]
        assert re.match(r"^[0-9a-f]{8}$", opened["comments"][0]["id"])
        assert uuid.match(done["id"]) and stamp.match(done["completed_at"])
        assert client.get("/api/tasks/completed").get_json()[0]["id"] == done["id"]
        # What a file written by hand may hold stays.
        assert by_hand["id"] == "old-1" and by_hand["created_at"] == "2025-03-04"
        assert by_hand["comments"][0]["id"] == "c1" and by_hand["comments"][0]["by"] == "alice"

    def test_restore_requires_tasks(self, client):
        assert client.post("/api/tasks/restore", json={"tasks": []}).status_code == 400

    def test_restore_skips_titleless_snapshots(self, client):
        resp = client.post("/api/tasks/restore", json={"tasks": [{"title": "  "}]})
        assert resp.status_code == 201
        assert resp.get_json() == []

    def test_restore_keeps_fractional_sort_order(self, client):
        # Regression: a move between two tasks (reorder `updates`) can leave a
        # fractional sort_order; undoing that task's delete dropped it, and the
        # task fell back to Unsorted.
        task = create_task(client, title="Between").get_json()
        client.post("/api/tasks/reorder", json={"updates": [{"id": task["id"], "sort_order": 1.5}]})
        snap = client.get("/api/tasks").get_json()[0]
        client.delete(f"/api/tasks/{task['id']}")
        client.post("/api/tasks/restore", json={"tasks": [snap]})
        assert client.get("/api/tasks").get_json()[0]["sort_order"] == 1.5

# ── due-count ────────────────────────────────────────────────────────────────


class TestDueCount:
    """Nothing is hidden any more, so due-count reflects every dated task."""

    def test_overdue_task_counted(self, client):
        past = (date.today() - timedelta(days=2)).isoformat()
        create_task(client, title="Visible overdue", due_date=past)
        counts = client.get("/api/tasks/due-count").get_json()
        assert counts["overdue"] == 1

    def test_overdue_task_with_future_plan_still_counted(self, client):
        # Planning an overdue task for later doesn't make the deadline go away.
        past = (date.today() - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=5)).isoformat()
        create_task(client, title="Planned later", due_date=past, start_date=future)
        counts = client.get("/api/tasks/due-count").get_json()
        assert counts["overdue"] == 1

    def test_started_task_counted_when_due(self, client):
        today = date.today().isoformat()
        started = (date.today() - timedelta(days=3)).isoformat()
        create_task(client, title="Due today", due_date=today, start_date=started)
        counts = client.get("/api/tasks/due-count").get_json()
        assert counts["today"] == 1

    def test_future_due_not_counted(self, client):
        future = (date.today() + timedelta(days=10)).isoformat()
        create_task(client, title="Far due", due_date=future, start_date=future)
        counts = client.get("/api/tasks/due-count").get_json()
        assert counts["count"] == 0


class TestSkipOccurrence:
    def test_skip_archives_and_spawns(self, client):
        task = create_task(client, title="Recurring", due_date=_future(2),
                           rec_type="daily", rec_interval=1).get_json()
        resp = client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        assert resp.status_code == 200
        assert resp.get_json()["wont_do"] is True
        active = client.get("/api/tasks").get_json()
        assert len(active) == 1
        assert active[0]["spawned_from"] == task["id"]
        assert active[0]["due_date"] == _future(3)
        completed = client.get("/api/tasks/completed").get_json()
        assert completed[0]["id"] == task["id"]
        assert completed[0]["wont_do"] is True

    def test_undo_skip_removes_spawned(self, client):
        task = create_task(client, title="Recurring", due_date=_future(2),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        client.put(f"/api/tasks/{task['id']}", json={"wont_do": False})
        active = client.get("/api/tasks").get_json()
        assert [t["id"] for t in active] == [task["id"]]
        assert active[0]["completed_at"] is None
        assert active[0]["wont_do"] is False

    def test_skip_non_recurring_is_plain_wont_do(self, client):
        task = create_task(client, title="One-off").get_json()
        client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        assert client.get("/api/tasks").get_json() == []
        completed = client.get("/api/tasks/completed").get_json()
        assert completed[0]["wont_do"] is True

    # Skipping an overdue occurrence must not bring the series back today:
    # the next one is the first after today (catch-up is > today, not >= today).
    def test_skip_overdue_daily_due_moves_to_tomorrow(self, client):
        task = create_task(client, title="Daily", due_date=_past(3),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        active = client.get("/api/tasks").get_json()
        assert len(active) == 1
        assert active[0]["due_date"] == _future(1)

    def test_skip_overdue_daily_start_moves_to_tomorrow(self, client):
        task = create_task(client, title="Daily on focus", start_date=_past(3),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        active = client.get("/api/tasks").get_json()
        assert len(active) == 1
        assert active[0]["start_date"] == _future(1)
        assert active[0]["due_date"] is None

    def test_skip_overdue_weekly_moves_to_next_weekly_date_after_today(self, client):
        task = create_task(client, title="Weekly", due_date=_past(10),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        active = client.get("/api/tasks").get_json()
        assert active[0]["due_date"] == _future(4)

    def test_skip_due_today_moves_to_tomorrow(self, client):
        task = create_task(client, title="Daily today", due_date=_today(),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"skip": True})
        assert client.get("/api/tasks").get_json()[0]["due_date"] == _future(1)

    def test_completing_overdue_daily_still_lands_today(self, client):
        # Completing is unchanged: you did it today, so the next one is due today.
        task = create_task(client, title="Daily", due_date=_past(3),
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        assert client.get("/api/tasks").get_json()[0]["due_date"] == _today()


class TestLimitParam:
    def _embed(self, client, wiki_dir, directive):
        (wiki_dir / "board.md").write_text(f"# Board\n\n{directive}\n", encoding="utf-8")
        return client.get("/page/board").get_data(as_text=True)

    def test_no_limit_by_default(self, client, wiki_dir):
        assert 'data-init-limit=""' in self._embed(client, wiki_dir, "[[tasks:]]")

    def test_limit_lands_in_embed(self, client, wiki_dir):
        assert 'data-init-limit="7"' in self._embed(client, wiki_dir, "[[tasks: limit=7]]")

    def test_capacity_is_not_a_limit(self, client, wiki_dir):
        assert 'data-init-limit=""' in self._embed(client, wiki_dir, "[[tasks: capacity=4]]")

    def test_invalid_limit_is_empty(self, client, wiki_dir):
        assert 'data-init-limit=""' in self._embed(client, wiki_dir, "[[tasks: limit=lots]]")

    def test_tasks_code_comes_with_leaf_js(self, client, wiki_dir):
        # The embed carries no script of its own: zonewiki.js, an ES module on
        # every page, imports tasks.js, which imports tasks-core.js.
        (wiki_dir / "board.md").write_text("# Board\n\n[[tasks:]]\n[[tasks:]]\n", encoding="utf-8")
        html_out = client.get("/page/board").get_data(as_text=True)
        assert html_out.count("<script") == 2   # highlight.js and zonewiki.js
        assert '<script type="module" src="/public/zonewiki.js"></script>' in html_out


# ── The files' format ─────────────────────────────────────────────────────────


class TestFileFormat:
    """Zone writes each task's fields in one order; files in another (the
    Python Zone's, a hand edit) are rewritten once, at start, in a commit of
    their own. Keys Zone doesn't know stay."""

    OLD = [{"id": "a1", "title": "Old", "description": "", "tags": [], "due_date": None,
            "created_at": "2026-01-02T10:00:00", "completed_at": None, "wont_do": False,
            "sort_order": 1.0, "recurrence": None, "blocked_by": ["x"], "spawned_from": None}]

    def log(self, wiki):
        return subprocess.run(["git", "log", "--format=%s"], cwd=wiki, capture_output=True, text=True,
                              env=settings.GIT_ENV).stdout.splitlines()

    def test_rewritten_once_at_start(self, git_wiki_dir, tmp_path_factory):
        tasks = git_wiki_dir / "_tasks" / "tasks.json"
        tasks.write_text(json.dumps(self.OLD, indent=2), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=git_wiki_dir, env=settings.GIT_ENV, check=True)
        subprocess.run(["git", "commit", "-qm", "old"], cwd=git_wiki_dir, env=settings.GIT_ENV, check=True)
        git_setup(tmp_path_factory)   # a start
        assert self.log(git_wiki_dir)[0] == "Write the task files in Zone's format"
        task = json.loads(tasks.read_text(encoding="utf-8"))[0]
        assert list(task)[:5] == ["id", "title", "description", "tags", "start_date"]
        assert list(task)[-1] == "blocked_by" and task["blocked_by"] == ["x"]
        assert task["sort_order"] == 1 and task["comments"] == [] and task["keep"] is False
        before = self.log(git_wiki_dir)
        git_setup(tmp_path_factory)   # nothing left to rewrite
        assert self.log(git_wiki_dir) == before

    def test_unknown_keys_survive_an_edit(self, client, wiki_dir):
        (wiki_dir / "_tasks" / "tasks.json").write_text(json.dumps(self.OLD), encoding="utf-8")
        resp = client.put("/api/tasks/a1", json={"title": "New"})
        assert resp.get_json()["blocked_by"] == ["x"]
        task = json.loads((wiki_dir / "_tasks" / "tasks.json").read_text(encoding="utf-8"))[0]
        assert task["title"] == "New" and task["blocked_by"] == ["x"]

    def test_text_is_written_as_it_is(self, client, wiki_dir):
        create_task(client, title="Fix <b> & “quotes” ü")
        raw = (wiki_dir / "_tasks" / "tasks.json").read_text(encoding="utf-8")
        assert '"title": "Fix <b> & “quotes” ü"' in raw


# ── Round 2: completed-task year archives ─────────────────────────────────────


class TestCompletedArchive:
    def _seed_completed(self, wiki_dir, tasks):
        (wiki_dir / "_tasks" / "completed.json").write_text(
            json.dumps(tasks), encoding="utf-8")

    def test_old_years_rolled_into_archives(self, client, wiki_dir, tmp_path_factory):
        year = date.today().year
        self._seed_completed(wiki_dir, [
            {"id": "old1", "title": "Ancient", "completed_at": f"{year-2}-05-01T10:00:00"},
            {"id": "old2", "title": "Last year", "completed_at": f"{year-1}-11-30T10:00:00"},
            {"id": "new1", "title": "Recent", "completed_at": f"{year}-01-02T10:00:00"},
        ])
        git_setup(tmp_path_factory)   # a start archives
        hot = json.loads((wiki_dir / "_tasks" / "completed.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in hot] == ["new1"]
        arch1 = json.loads((wiki_dir / "_tasks" / f"completed-{year-2}.json").read_text(encoding="utf-8"))
        arch2 = json.loads((wiki_dir / "_tasks" / f"completed-{year-1}.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in arch1] == ["old1"]
        assert [t["id"] for t in arch2] == ["old2"]

    def test_archive_merges_into_existing_file(self, client, wiki_dir, tmp_path_factory):
        year = date.today().year
        (wiki_dir / "_tasks" / f"completed-{year-1}.json").write_text(
            json.dumps([{"id": "prior", "title": "Already archived",
                         "completed_at": f"{year-1}-01-01T10:00:00"}]), encoding="utf-8")
        self._seed_completed(wiki_dir, [
            {"id": "old", "title": "Old", "completed_at": f"{year-1}-06-01T10:00:00"},
        ])
        git_setup(tmp_path_factory)   # a start archives
        arch = json.loads((wiki_dir / "_tasks" / f"completed-{year-1}.json").read_text(encoding="utf-8"))
        assert [t["id"] for t in arch] == ["prior", "old"]

    def test_noop_when_nothing_old(self, client, wiki_dir, tmp_path_factory):
        year = date.today().year
        self._seed_completed(wiki_dir, [
            {"id": "new1", "title": "Recent", "completed_at": f"{year}-01-02T10:00:00"},
        ])
        git_setup(tmp_path_factory)   # a start archives
        assert not list((wiki_dir / "_tasks").glob("completed-*.json"))

    def test_archived_tasks_stay_searchable(self, client, wiki_dir, tmp_path_factory):
        year = date.today().year
        self._seed_completed(wiki_dir, [
            {"id": "old1", "title": "Zephyrscale audit",
             "completed_at": f"{year-1}-05-01T10:00:00"},
        ])
        git_setup(tmp_path_factory)   # a start archives
        results = client.get("/api/search?q=zephyrscale").get_json()
        assert any(r.get("type") == "task" for r in results)


class TestCompletedByYear:
    """Earlier years' completed tasks (the yearly archives) are shown by year
    in Completed and can be changed like any other completed task."""

    def _seed(self, wiki_dir):
        year = date.today().year
        (wiki_dir / "_tasks" / f"completed-{year-1}.json").write_text(json.dumps([
            {"id": "old", "title": "Last year", "completed_at": f"{year-1}-05-01T10:00:00"},
        ]), encoding="utf-8")
        (wiki_dir / "_tasks" / "completed.json").write_text(json.dumps([
            {"id": "new", "title": "This year", "completed_at": f"{year}-01-02T10:00:00"},
        ]), encoding="utf-8")
        return year

    def _ids(self, path):
        return [t["id"] for t in json.loads(path.read_text(encoding="utf-8"))]

    def test_completed_by_year(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        assert [t["id"] for t in client.get("/api/tasks/completed").get_json()] == ["new"]
        got = client.get(f"/api/tasks/completed?year={year-1}").get_json()
        assert [t["id"] for t in got] == ["old"]
        assert client.get("/api/tasks/completed?year=abc").status_code == 400

    def test_completed_view_and_count_leave_out_unarchived_old_tasks(self, client, wiki_dir):
        # A year change without a restart: last year's task is still in the
        # current file, but belongs to last year's view.
        year = date.today().year
        (wiki_dir / "_tasks" / "completed.json").write_text(json.dumps([
            {"id": "old", "title": "Old", "completed_at": f"{year-1}-12-31T10:00:00"},
            {"id": "new", "title": "New", "completed_at": f"{year}-01-01T10:00:00"},
        ]), encoding="utf-8")
        assert [t["id"] for t in client.get("/api/tasks/completed").get_json()] == ["new"]
        assert client.get("/api/tasks/completed-count").get_json()["count"] == 1
        assert [t["id"] for t in client.get(f"/api/tasks/completed?year={year-1}").get_json()] == ["old"]
        assert client.get("/api/tasks/completed-years").get_json()["years"] == [str(year), str(year-1)]

    def test_completed_years_per_list(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        (wiki_dir / "shop.md").write_text("# Shop\n\n[[tasks: list=groceries]]\n", encoding="utf-8")
        arch = wiki_dir / "_tasks" / f"completed-{year-2}.json"
        arch.write_text(json.dumps([{"id": "g", "title": "Milk", "tags": ["groceries"],
                                     "completed_at": f"{year-2}-05-01T10:00:00"}]), encoding="utf-8")
        years = lambda q: client.get("/api/tasks/completed-years" + q).get_json()["years"]  # noqa: E731
        # The main task list's are every task's: list tasks included.
        assert years("") == [str(year), str(year-1), str(year-2)]
        assert years("?list=groceries") == [str(year), str(year-2)]

    def test_edit_keeps_task_in_its_archive(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        resp = client.put("/api/tasks/old", json={"title": "Renamed"})
        assert resp.status_code == 200
        arch = wiki_dir / "_tasks" / f"completed-{year-1}.json"
        assert self._ids(arch) == ["old"]
        assert json.loads(arch.read_text(encoding="utf-8"))[0]["title"] == "Renamed"
        assert self._ids(wiki_dir / "_tasks" / "completed.json") == ["new"]

    def test_reopen_moves_task_out_of_archive(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        assert client.put("/api/tasks/old", json={"completed": False}).status_code == 200
        assert self._ids(wiki_dir / "_tasks" / f"completed-{year-1}.json") == []
        assert [t["id"] for t in client.get("/api/tasks").get_json()] == ["old"]
        # Completed again now, it belongs to this year.
        client.put("/api/tasks/old", json={"completed": True})
        assert self._ids(wiki_dir / "_tasks" / "completed.json") == ["new", "old"]

    def test_batch_reopen_and_tag_reach_archives(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        tagged = client.post("/api/tasks/batch-tag", json={"ids": ["old"], "add": ["x"]}).get_json()
        assert tagged[0]["tags"] == ["x"]
        done = client.post("/api/tasks/batch-complete", json={"ids": ["old"], "completed": False}).get_json()
        assert [t["id"] for t in done] == ["old"]
        assert self._ids(wiki_dir / "_tasks" / f"completed-{year-1}.json") == []

    def test_delete_and_restore_archived_task(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        arch = wiki_dir / "_tasks" / f"completed-{year-1}.json"
        snapshot = json.loads(arch.read_text(encoding="utf-8"))[0]
        assert client.delete("/api/tasks/old").status_code == 204
        assert self._ids(arch) == []
        # Undo puts it back into its own year, not this year's file.
        assert client.post("/api/tasks/restore", json={"tasks": [snapshot]}).status_code == 201
        assert self._ids(arch) == ["old"]
        assert self._ids(wiki_dir / "_tasks" / "completed.json") == ["new"]

    def test_batch_delete_reaches_archives(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        client.post("/api/tasks/batch-delete", json={"ids": ["old", "new"]})
        assert self._ids(wiki_dir / "_tasks" / f"completed-{year-1}.json") == []
        assert self._ids(wiki_dir / "_tasks" / "completed.json") == []

    def test_unchanged_archives_are_not_rewritten(self, client, wiki_dir):
        year = self._seed(wiki_dir)
        arch = wiki_dir / "_tasks" / f"completed-{year-1}.json"
        raw = '[{"id": "old", "title": "Last year", "completed_at": "%d-05-01T10:00:00"}]' % (year - 1)
        arch.write_text(raw, encoding="utf-8")
        create_task(client, title="Unrelated")
        client.put("/api/tasks/new", json={"title": "Edited"})
        assert arch.read_text(encoding="utf-8") == raw

    def test_other_files_are_not_archives(self, client, wiki_dir):
        # Only _deck_completed-YYYY.json is an archive: anything else that
        # matches the prefix is left alone by the year split.
        self._seed(wiki_dir)
        other = wiki_dir / "_tasks" / "completed-backup.json"
        other.write_text(json.dumps([{"id": "b", "title": "B", "completed_at": "2001-01-01T00:00:00"}]),
                         encoding="utf-8")
        client.put("/api/tasks/new", json={"title": "Edited"})
        assert self._ids(other) == ["b"]

    def test_search_links_archived_task_to_its_year(self, client, wiki_dir):
        year = date.today().year
        (wiki_dir / "_tasks" / f"completed-{year-1}.json").write_text(json.dumps([
            {"id": "old", "title": "Zephyrscale audit", "completed_at": f"{year-1}-05-01T10:00:00"},
        ]), encoding="utf-8")
        results = client.get("/api/search?q=zephyrscale").get_json()
        task = next(r for r in results if r.get("type") == "task")
        assert task["href"] == f"/tasks?task=old&view=completed&year={year-1}"

    def test_search_link_of_this_years_task_has_no_year(self, client, wiki_dir):
        self._seed(wiki_dir)
        client.put("/api/tasks/new", json={"title": "Quillfeather done"})
        results = client.get("/api/search?q=quillfeather").get_json()
        task = next(r for r in results if r.get("type") == "task")
        assert task["href"] == "/tasks?task=new&view=completed"


class TestMalformedInput:
    """Regression: malformed JSON bodies raised inside the handlers (500)
    instead of being rejected with a 400 the client can show."""

    @pytest.mark.parametrize("path", ["/api/tasks", "/api/tasks/batch-delete",
                                      "/api/tasks/batch-complete", "/api/tasks/restore",
                                      "/api/tasks/reorder", "/api/tasks/batch-tag"])
    @pytest.mark.parametrize("body", ["[1, 2]", '"text"', "not json", "null"])
    def test_non_object_body_is_400(self, client, path, body):
        resp = client.post(path, data=body, content_type="application/json")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_update_non_object_body_is_400(self, client):
        task = create_task(client).get_json()
        assert client.put(f"/api/tasks/{task['id']}", data="[]",
                          content_type="application/json").status_code == 400

    @pytest.mark.parametrize("payload", [
        {"title": 5},
        {"title": "T", "description": ["x"]},
        {"title": "T", "tags": 7},
        {"title": "T", "tags": [1, 2]},
        {"title": "T", "due_date": 20990101},
        {"title": "T", "start_date": 20990101},
        {"title": "T", "rec_type": "daily", "rec_interval": "often"},
    ])
    def test_bad_field_types_are_400(self, client, payload):
        resp = client.post("/api/tasks", json=payload)
        assert resp.status_code == 400, payload
        assert client.get("/api/tasks").get_json() == []

    def test_tags_string_is_split_on_commas(self, client):
        task = create_task(client, tags="a, b,,c").get_json()
        assert task["tags"] == ["a", "b", "c"]

    def test_numeric_strings_still_accepted(self, client):
        task = create_task(client, due_date="2099-01-10",
                           rec_type="weekly", rec_interval="3").get_json()
        assert task["recurrence"]["interval"] == 3

    @pytest.mark.parametrize("path", ["/api/tasks/batch-delete", "/api/tasks/batch-complete"])
    def test_batch_ids_must_be_strings(self, client, path):
        # An object in the list is unhashable → used to raise building set(ids).
        assert client.post(path, json={"ids": [{"id": "x"}]}).status_code == 400

    def test_batch_tag_bad_lists_are_400(self, client):
        task = create_task(client).get_json()
        assert client.post("/api/tasks/batch-tag",
                           json={"ids": [task["id"]], "add": "x"}).status_code == 400
        assert client.post("/api/tasks/batch-tag",
                           json={"ids": [task["id"]], "add": [1]}).status_code == 400

    def test_restore_bad_interval_degrades_instead_of_500(self, client):
        resp = client.post("/api/tasks/restore", json={"tasks": [
            {"title": "T", "recurrence": {"type": "daily", "interval": "x"}}]})
        assert resp.status_code == 201
        assert resp.get_json()[0]["recurrence"]["interval"] == 1


class TestNoTaskLinking:
    """Task-to-task links (blocked_by / blocking) were removed. The API ignores
    them, and dependency data left in existing tasks files has no effect."""

    def test_link_fields_are_ignored(self, client):
        other = create_task(client, title="Other").get_json()
        t = create_task(client, title="T", blocked_by=[other["id"]], blocking=[other["id"]]).get_json()
        assert "blocked_by" not in t
        resp = client.put(f"/api/tasks/{t['id']}", json={"blocked_by": [t["id"]], "blocking": [t["id"]]})
        assert resp.status_code == 200          # a self-"cycle" is no longer an error
        for task in client.get("/api/tasks").get_json():
            assert "blocked_by" not in task

    def test_restore_drops_link_fields(self, client):
        resp = client.post("/api/tasks/restore", json={"tasks": [
            {"id": "x", "title": "T", "blocked_by": ["y"], "blocking": ["z"]}]})
        assert "blocked_by" not in resp.get_json()[0]


# ---------- Tags are one word each ----------


class TestTagCleaning:
    """Regression: a tag with a space ("locallm xxx") could never be matched,
    because the filter field splits on spaces."""

    def test_create_and_update_clean_tags(self, client):
        t = create_task(client, title="T", tags=["locallm xxx", "#work", " ", "Work", "a  b"]).get_json()
        assert t["tags"] == ["locallm-xxx", "work", "a-b"]
        t = client.put(f"/api/tasks/{t['id']}", json={"tags": "local llm, #home"}).get_json()
        assert t["tags"] == ["local-llm", "home"]

    def test_batch_tag_add_and_remove_clean_tags(self, client):
        tid = create_task(client, title="T").get_json()["id"]
        [t] = client.post("/api/tasks/batch-tag", json={"ids": [tid], "add": ["local llm"]}).get_json()
        assert t["tags"] == ["local-llm"]
        # Removing by the spelling with a space finds the stored one.
        [t] = client.post("/api/tasks/batch-tag", json={"ids": [tid], "remove": ["local llm"]}).get_json()
        assert t["tags"] == []

    def test_restore_cleans_tags(self, client):
        snap = {"id": "snap-1", "title": "Restored", "tags": ["a b", "#c"]}
        [t] = client.post("/api/tasks/restore", json={"tasks": [snap]}).get_json()
        assert t["tags"] == ["a-b", "c"]

    def test_hand_edited_file_is_cleaned_on_read(self, client, wiki_dir):
        (wiki_dir / "_tasks" / "tasks.json").write_text(json.dumps([
            {"id": "t", "title": "T", "tags": ["locallm xxx", "#x"], "created_at": "2026-01-01T00:00:00"},
        ]), encoding="utf-8")
        [t] = client.get("/api/tasks").get_json()
        assert t["tags"] == ["locallm-xxx", "x"]


# ---------- Separate lists ----------


class TestLists:
    """A separate list is a tag some page declares with [[tasks: list=<tag>]]:
    the tag is marked as a list and links to its tasks open /tasks?list=<tag>,
    but otherwise it is a tag: the main task list, the due badge and the main
    Completed count include its tasks."""

    @pytest.fixture()
    def shop(self, wiki_dir):
        (wiki_dir / "shop.md").write_text(
            "# Shop\n\n[[tasks: list=Groceries keep=1 labels=Catalogue,,Shopping]]\n", encoding="utf-8")

    def test_tag_without_a_page_is_no_list(self, client):
        today = date.today().isoformat()
        create_task(client, due_date=today, tags=["groceries"])
        assert client.get("/api/tasks/due-count").get_json()["count"] == 1

    def test_due_count_includes_list_tasks(self, client, shop):
        today = date.today().isoformat()
        create_task(client, due_date=today)
        create_task(client, due_date=today, tags=["groceries"])
        assert client.get("/api/tasks/due-count").get_json()["count"] == 2

    def test_completed_count_per_scope(self, client, shop):
        for tags in ([], ["groceries"], ["Groceries", "x"]):
            tid = create_task(client, tags=tags).get_json()["id"]
            client.put(f"/api/tasks/{tid}", json={"completed": True})
        count = lambda q: client.get("/api/tasks/completed-count" + q).get_json()["count"]  # noqa: E731
        assert count("") == 3
        assert count("?list=Groceries") == 2
        assert count("?all=1") == 3      # the old "show separate lists" scope

    def test_list_field_is_gone(self, client):
        task = create_task(client, list="groceries").get_json()
        assert "list" not in task
        assert "list" not in client.put(f"/api/tasks/{task['id']}", json={"list": "x"}).get_json()

    def test_embed_carries_list_and_declared_lists(self, client, wiki_dir, shop):
        body = client.get("/page/shop").get_data(as_text=True)
        assert 'data-init-list="groceries"' in body
        assert 'data-lists="[&#34;groceries&#34;]"' in body
        # The home page's title, for the list tag's tooltip.
        assert 'data-list-homes="{&#34;groceries&#34;:&#34;Shop&#34;}"' in body
        assert 'id="task-edit-list"' not in body
        assert "task-show-lists" not in body       # the main task list hides nothing

    def test_tasks_list_page_uses_the_declaring_embed(self, client, shop):
        body = client.get("/tasks?list=groceries").get_data(as_text=True)
        assert 'data-init-list="groceries"' in body and 'data-init-keep="1"' in body
        assert ">Catalogue</button>" in body and ">Shopping</button>" in body
        assert 'href="/page/shop">Shop</a>' in body

    def test_tasks_list_page_without_declaration(self, client):
        body = client.get("/tasks?list=books").get_data(as_text=True)
        assert 'data-init-list="books"' in body
        assert "these are the tasks tagged #books" in body

    def test_search_links_to_list(self, client, shop):
        tid = create_task(client, title="Buy oat milk", tags=["groceries"]).get_json()["id"]
        hit = next(r for r in client.get("/api/search?q=oat").get_json() if r.get("task_id") == tid)
        assert hit["href"] == f"/tasks?task={tid}&view=open&list=groceries"
        assert hit["meta"].startswith("Tasks › groceries")

    def test_list_follows_the_page(self, client, wiki_dir, shop):
        """Declarations are read live: editing the page away ends the list."""
        tid = create_task(client, title="Buy oat milk", tags=["groceries"]).get_json()["id"]
        (wiki_dir / "shop.md").write_text("# Shop\n\nNothing.\n", encoding="utf-8")
        os.utime(wiki_dir / "shop.md", (1, 1))   # a new mtime even within the same tick
        hit = next(r for r in client.get("/api/search?q=oat").get_json() if r.get("task_id") == tid)
        assert hit["href"] == f"/tasks?task={tid}&view=open"


# ---------- Keep tasks ----------


class TestImportance:
    """A task's importance: high or low; normal is what a task is when nothing says so."""

    def _in_file(self, wiki_dir, title):
        return next(t for t in json.loads((wiki_dir / "_tasks/tasks.json").read_text()) if t["title"] == title)

    def test_set_changed_and_cleared(self, client, wiki_dir):
        plain = create_task(client, title="Plain").get_json()
        assert "importance" not in plain and "importance" not in self._in_file(wiki_dir, "Plain")   # files as they were
        task = create_task(client, title="Tax return", importance="high").get_json()
        assert task["importance"] == "high" and self._in_file(wiki_dir, "Tax return")["importance"] == "high"
        # In the file it comes after keep, where the fields' order has it.
        keys = list(self._in_file(wiki_dir, "Tax return"))
        assert keys[keys.index("keep") + 1] == "importance"
        low = client.put(f"/api/tasks/{task['id']}", json={"importance": " Low "}).get_json()
        assert low["importance"] == "low"
        for normal in ("normal", "", None):
            client.put(f"/api/tasks/{task['id']}", json={"importance": "high"})
            assert "importance" not in client.put(f"/api/tasks/{task['id']}", json={"importance": normal}).get_json()
        assert "importance" not in self._in_file(wiki_dir, "Tax return")
        # Another change leaves it alone.
        client.put(f"/api/tasks/{task['id']}", json={"importance": "high"})
        assert client.put(f"/api/tasks/{task['id']}", json={"title": "Taxes"}).get_json()["importance"] == "high"

    def test_anything_else_is_refused(self, client):
        task = create_task(client, title="Tax return").get_json()
        for bad in ("urgent", 1, True, ["high"]):
            resp = client.put(f"/api/tasks/{task['id']}", json={"importance": bad})
            assert resp.status_code == 400 and "importance must be high, normal or low" in resp.get_data(as_text=True)
        assert create_task(client, title="X", importance="urgent").status_code == 400

    def test_a_file_edited_by_hand_reads(self, client, wiki_dir):
        tasks = [{"id": f"0000000{i}-0000-4000-8000-000000000000", "title": f"T{i}", "importance": v}
                 for i, v in enumerate(["HIGH", "low", "normal", "urgent", 3])]
        (wiki_dir / "_tasks/tasks.json").write_text(json.dumps(tasks))
        got = {t["title"]: t.get("importance") for t in client.get("/api/tasks").get_json()}
        assert got == {"T0": "high", "T1": "low", "T2": None, "T3": None, "T4": None}

    def test_the_next_occurrence_keeps_it(self, client):
        task = create_task(client, title="Water plants", importance="high", due_date=_future(1),
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/tasks/{task['id']}", json={"completed": True})
        [nxt] = [t for t in client.get("/api/tasks").get_json() if t["title"] == "Water plants"]
        assert nxt["id"] != task["id"] and nxt["importance"] == "high"

    def test_several_at_once_and_a_restored_task(self, client):
        a, b = (create_task(client, title=t).get_json() for t in ("A", "B"))
        resp = client.post("/api/tasks/batch-update", json={"tasks": [{"id": a["id"], "importance": "low"},
                                                                      {"id": b["id"], "importance": "high"}]})
        assert resp.status_code == 200
        got = {t["title"]: t.get("importance") for t in client.get("/api/tasks").get_json()}
        assert got == {"A": "low", "B": "high"}
        snap = next(t for t in client.get("/api/tasks").get_json() if t["title"] == "B")
        client.delete(f"/api/tasks/{b['id']}")
        client.post("/api/tasks/restore", json={"tasks": [snap]})
        assert next(t for t in client.get("/api/tasks").get_json() if t["title"] == "B")["importance"] == "high"


class TestKeep:
    """A keep task is never archived: completing it sends it back to Open
    (no start date, no deadline) and records last_done."""

    def _keep_task(self, client, **kw):
        today = date.today().isoformat()
        task = create_task(client, title="Milk", keep=True, start_date=today,
                           due_date=_future(2), **kw).get_json()
        client.post("/api/tasks/reorder", json={"order": [task["id"]]})
        return task

    def test_new_task_defaults(self, client):
        task = create_task(client).get_json()
        assert task["keep"] is False and task["last_done"] is None

    def test_completing_keeps_it_open(self, client):
        tid = self._keep_task(client)["id"]
        done = client.put(f"/api/tasks/{tid}", json={"completed": True}).get_json()
        assert done["completed_at"] is None
        assert done["start_date"] is None and done["due_date"] is None
        assert done["sort_order"] == 0      # keeps its place in Open's order
        assert done["last_done"] == date.today().isoformat()
        assert [t["id"] for t in client.get("/api/tasks").get_json()] == [tid]
        assert client.get("/api/tasks/completed").get_json() == []

    def test_undo_restores_the_fields(self, client):
        task = self._keep_task(client)
        tid = task["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        back = client.put(f"/api/tasks/{tid}", json={
            "start_date": task["start_date"], "due_date": task["due_date"],
            "last_done": None}).get_json()
        assert back["start_date"] == task["start_date"] and back["due_date"] == task["due_date"]
        assert back["last_done"] is None

    def test_batch_complete_keeps_keep_tasks_open(self, client):
        keep_id = self._keep_task(client)["id"]
        other_id = create_task(client, title="Candles").get_json()["id"]
        updated = client.post("/api/tasks/batch-complete",
                              json={"ids": [keep_id, other_id], "completed": True}).get_json()
        assert {t["id"] for t in updated} == {keep_id, other_id}
        active = client.get("/api/tasks").get_json()
        assert [t["id"] for t in active] == [keep_id]
        assert active[0]["last_done"] == date.today().isoformat()
        assert [t["id"] for t in client.get("/api/tasks/completed").get_json()] == [other_id]

    def test_keep_never_repeats(self, client):
        task = create_task(client, keep=True, due_date=_future(1), rec_type="weekly").get_json()
        assert task["recurrence"] is None
        tid = create_task(client, due_date=_future(1), rec_type="weekly").get_json()["id"]
        assert client.put(f"/api/tasks/{tid}", json={"keep": True}).get_json()["recurrence"] is None

    def test_wont_do_retires_a_keep_task(self, client):
        tid = self._keep_task(client)["id"]
        client.put(f"/api/tasks/{tid}", json={"wont_do": True})
        assert client.get("/api/tasks").get_json() == []
        assert client.get("/api/tasks/completed").get_json()[0]["wont_do"] is True

    def test_restore_keeps_keep_and_last_done(self, client):
        tid = self._keep_task(client)["id"]
        task = client.put(f"/api/tasks/{tid}", json={"completed": True}).get_json()
        client.delete(f"/api/tasks/{tid}")
        restored = client.post("/api/tasks/restore", json={"tasks": [task]}).get_json()[0]
        assert restored["keep"] is True and restored["last_done"] == task["last_done"]

    def test_bad_last_done_is_rejected(self, client):
        tid = create_task(client, keep=True).get_json()["id"]
        assert client.put(f"/api/tasks/{tid}", json={"last_done": "soon"}).status_code == 400

    def test_embed_keep_param(self, client, wiki_dir):
        (wiki_dir / "shop.md").write_text("# Shop\n\n[[tasks: list=groceries keep=1]]\n", encoding="utf-8")
        body = client.get("/page/shop").get_data(as_text=True)
        assert 'data-init-keep="1"' in body
        assert 'id="task-edit-keep"' in body
        (wiki_dir / "plain.md").write_text("# Plain\n\n[[tasks:]]\n", encoding="utf-8")
        assert 'data-init-keep=""' in client.get("/page/plain").get_data(as_text=True)


# ---------- Recurring: the next start is never in the past ----------


class TestRecurringStartNotInPast:
    """Regression: a daily task with start and due dates (start 3 days ago,
    due yesterday) came back after completion as "since 2 days ago"."""

    def _d(self, n):
        return (date.today() + timedelta(days=n)).isoformat()

    def _complete(self, client, tid):
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        return next(t for t in client.get("/api/tasks").get_json() if t["spawned_from"] == tid)

    def test_daily_start_is_clamped_to_today(self, client):
        tid = create_task(client, rec_type="daily", start_date=self._d(-3),
                          due_date=self._d(-1)).get_json()["id"]
        new = self._complete(client, tid)
        assert new["due_date"] == self._d(0)
        assert new["start_date"] == self._d(0)
        assert new["recurrence"]["lead"] == 2

    def test_gap_comes_back_on_the_next_instance(self, client):
        # Weekly, start 3 days before due, completed late: this week's start
        # would be yesterday, so it is today; next week's is 3 days before due again.
        tid = create_task(client, rec_type="weekly", start_date=self._d(-8),
                          due_date=self._d(-5)).get_json()["id"]
        first = self._complete(client, tid)
        assert (first["start_date"], first["due_date"]) == (self._d(0), self._d(2))
        second = self._complete(client, first["id"])
        assert (second["start_date"], second["due_date"]) == (self._d(6), self._d(9))
        assert "lead" not in second["recurrence"]

    def test_unclamped_series_stores_no_lead(self, client):
        tid = create_task(client, rec_type="weekly", start_date=self._d(1),
                          due_date=self._d(3)).get_json()["id"]
        new = self._complete(client, tid)
        assert (new["start_date"], new["due_date"]) == (self._d(8), self._d(10))
        assert "lead" not in new["recurrence"]

    def test_editing_a_date_forgets_the_lead(self, client):
        tid = create_task(client, rec_type="daily", start_date=self._d(-3),
                          due_date=self._d(-1)).get_json()["id"]
        new = self._complete(client, tid)
        same = client.put(f"/api/tasks/{new['id']}", json={
            "rec_type": "daily", "start_date": new["start_date"],
            "due_date": new["due_date"]}).get_json()
        assert same["recurrence"]["lead"] == 2           # modal re-save, nothing changed
        moved = client.put(f"/api/tasks/{new['id']}", json={"due_date": self._d(4)}).get_json()
        assert "lead" not in moved["recurrence"]


class TestEmbedLabels:
    def _tabs(self, client, wiki_dir, directive):
        (wiki_dir / "shop.md").write_text(f"# Shop\n\n{directive}\n", encoding="utf-8")
        body = client.get("/page/shop").get_data(as_text=True)
        return re.findall(r'role="tab" data-view="(\w+)" aria-selected="false">([^<]*)</button>', body)

    def test_default_labels(self, client, wiki_dir):
        assert self._tabs(client, wiki_dir, "[[tasks:]]") == [("open", "Open"), ("focus", "Focus")]

    def test_custom_labels_keep_the_views(self, client, wiki_dir):
        assert self._tabs(client, wiki_dir, "[[tasks: labels=Catalogue,Shopping_list]]") == [
            ("open", "Catalogue"), ("focus", "Shopping list")]
        assert self._tabs(client, wiki_dir, "[[tasks: labels=,Shopping]]") == [
            ("open", "Open"), ("focus", "Shopping")]

    def test_three_labels_from_the_plan_tab_days_keep_first_and_last(self, client, wiki_dir):
        assert self._tabs(client, wiki_dir, "[[tasks: labels=Catalogue,Plan,Shopping_list]]") == [
            ("open", "Catalogue"), ("focus", "Shopping list")]

    def test_labels_are_escaped(self, client, wiki_dir):
        tabs = self._tabs(client, wiki_dir, "[[tasks: labels=<b>x</b>]]")
        assert tabs[0] == ("open", "&lt;b&gt;x&lt;/b&gt;")


# ---------- Task history ----------


class TestHistory:
    """Each task logs what happened to it: created, start/due changes,
    done, reopened, won't do, skipped. Undo restores the old log."""

    def _events(self, task):
        return [(e["event"], e.get("from"), e.get("to")) if e["event"] in ("start", "due", "repeat")
                else e["event"] for e in task["history"]]

    def test_create_logs_only_created(self, client):
        task = create_task(client, start_date=_future(1), due_date=_future(3)).get_json()
        assert self._events(task) == ["created"]
        assert len(task["history"][0]["at"]) == 16          # YYYY-MM-DDTHH:MM

    def test_date_changes_are_logged_with_from_and_to(self, client):
        tid = create_task(client).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"start_date": _future(1)})
        client.put(f"/api/tasks/{tid}", json={"start_date": _future(1), "title": "Same date"})
        task = client.put(f"/api/tasks/{tid}", json={"due_date": _future(5), "start_date": None}).get_json()
        assert self._events(task) == [
            "created", ("start", None, _future(1)), ("due", None, _future(5)), ("start", _future(1), None)]

    def test_completion_events(self, client):
        tid = create_task(client).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        client.put(f"/api/tasks/{tid}", json={"completed": False})
        client.put(f"/api/tasks/{tid}", json={"wont_do": True})
        task = client.put(f"/api/tasks/{tid}", json={"wont_do": False}).get_json()
        assert self._events(task) == ["created", "done", "reopened", "wontdo", "reopened"]

    def test_batch_complete_and_keep_log_done(self, client):
        a = create_task(client).get_json()["id"]
        k = create_task(client, keep=True, start_date=date.today().isoformat()).get_json()["id"]
        client.post("/api/tasks/batch-complete", json={"ids": [a, k], "completed": True})
        keep = next(t for t in client.get("/api/tasks").get_json() if t["id"] == k)
        assert self._events(keep) == ["created", "done"]    # the cleared start is part of "done"
        done = client.get("/api/tasks/completed").get_json()[0]
        assert self._events(done) == ["created", "done"]

    def test_skip_and_spawn(self, client):
        tid = create_task(client, due_date=_future(1), rec_type="daily").get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"skip": True})
        skipped = client.get("/api/tasks/completed").get_json()[0]
        assert self._events(skipped) == ["created", "skipped"]
        spawned = client.get("/api/tasks").get_json()[0]
        # The next occurrence carries the series' history on.
        assert self._events(spawned) == ["created", "skipped", ("repeat", None, _future(2))]

    def test_next_occurrence_carries_the_series_history(self, client):
        # Regression: ticking off a repeating task left the next occurrence
        # with only "created"; the series' history stayed with the archived one.
        today = date.today().isoformat()
        tid = create_task(client, rec_type="daily", start_date=today).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"due_date": _future(1)})
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        nxt = client.get("/api/tasks").get_json()[0]
        assert self._events(nxt) == [
            "created", ("due", None, _future(1)), "done", ("repeat", None, _future(2))]
        # Ticked off again: the history keeps growing on the series.
        client.put(f"/api/tasks/{nxt['id']}", json={"completed": True})
        third = client.get("/api/tasks").get_json()[0]
        assert self._events(third)[-2:] == ["done", ("repeat", None, _future(3))]
        assert len(self._events(third)) == 6

    def test_undo_sends_the_old_history_back(self, client):
        task = create_task(client).get_json()
        tid = task["id"]
        client.put(f"/api/tasks/{tid}", json={"start_date": _future(1)})
        undone = client.put(f"/api/tasks/{tid}", json={
            "start_date": None, "history": task["history"]}).get_json()
        assert undone["history"] == task["history"]

    def test_history_from_requests_is_validated(self, client):
        tid = create_task(client).get_json()["id"]
        task = client.put(f"/api/tasks/{tid}", json={"history": [
            {"at": "2026-01-01T10:00", "event": "start", "from": "bad", "to": "2026-01-02"},
            {"at": "2026-01-01T10:00", "event": "hacked"}, "junk",
        ]}).get_json()
        assert task["history"] == [
            {"at": "2026-01-01T10:00", "event": "start", "from": None, "to": "2026-01-02"}]

    def test_history_is_capped(self, client):
        tid = create_task(client).get_json()["id"]
        for i in range(40):
            client.put(f"/api/tasks/{tid}", json={"due_date": _future(i + 1)})
        task = next(t for t in client.get("/api/tasks").get_json() if t["id"] == tid)
        assert len(task["history"]) == 30
        assert self._events(task)[-1] == ("due", _future(39), _future(40))

    def test_restore_keeps_history(self, client):
        tid = create_task(client).get_json()["id"]
        task = client.put(f"/api/tasks/{tid}", json={"start_date": _future(2)}).get_json()
        client.delete(f"/api/tasks/{tid}")
        restored = client.post("/api/tasks/restore", json={"tasks": [task]}).get_json()[0]
        assert restored["history"] == task["history"]



# ---------- Comments ----------


class TestComments:
    def _url(self, tid, cid=None):
        return f"/api/tasks/{tid}/comments" + (f"/{cid}" if cid else "")

    def test_add_edit_delete(self, client):
        tid = create_task(client).get_json()["id"]
        resp = client.post(self._url(tid), json={"text": "  called Bob, waiting  "})
        assert resp.status_code == 201
        [c] = resp.get_json()["comments"]
        assert c["text"] == "called Bob, waiting"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", c["at"]) and c["id"]
        client.post(self._url(tid), json={"text": "Bob answered"})
        task = client.get("/api/tasks").get_json()[0]
        assert [x["text"] for x in task["comments"]] == ["called Bob, waiting", "Bob answered"]
        # Edit keeps the time; delete removes just that one.
        edited = client.put(self._url(tid, c["id"]), json={"text": "called Bob twice"}).get_json()
        assert edited["comments"][0] == {**c, "text": "called Bob twice"}
        left = client.delete(self._url(tid, c["id"])).get_json()
        assert [x["text"] for x in left["comments"]] == ["Bob answered"]
        # Comments are not history events.
        assert [e["event"] for e in left["history"]] == ["created"]

    def test_bad_requests(self, client):
        tid = create_task(client).get_json()["id"]
        assert client.post(self._url(tid), json={"text": "  "}).status_code == 400
        assert client.post(self._url(tid), json={"text": 3}).status_code == 400
        assert client.post(self._url(tid), data="x", content_type="text/plain").status_code == 400
        assert client.post(self._url("nope"), json={"text": "x"}).status_code == 404
        assert client.put(self._url(tid, "nope"), json={"text": "x"}).status_code == 404
        assert client.delete(self._url(tid, "nope")).status_code == 404
        cid = client.post(self._url(tid), json={"text": "x"}).get_json()["comments"][0]["id"]
        assert client.put(self._url(tid, cid), json={"text": ""}).status_code == 400
        long = client.post(self._url(tid), json={"text": "y" * 5000}).get_json()["comments"][-1]
        assert len(long["text"]) == 2000   # at most

    def test_several_lines(self, client):
        # Markdown of several lines is kept as written, with a file's line ends.
        tid = create_task(client).get_json()["id"]
        [c] = client.post(self._url(tid), json={"text": "Bought:\r\n\r\n- brushes\r\n- primer\n"}).get_json()["comments"]
        assert c["text"] == "Bought:\n\n- brushes\n- primer"
        html = client.post("/api/render-notes", json={"comments": [c["text"]]}).get_json()["comments"][0]
        assert "<p>Bought:</p>" in html and html.count("<li>") == 2
        edited = client.put(self._url(tid, c["id"]), json={"text": "one\r\ntwo"}).get_json()
        assert edited["comments"][0]["text"] == "one\ntwo"

    def test_on_a_completed_task(self, client):
        tid = create_task(client).get_json()["id"]
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        assert client.post(self._url(tid), json={"text": "done by Sam"}).status_code == 201
        done = client.get("/api/tasks/completed").get_json()[0]
        assert done["comments"][0]["text"] == "done by Sam"
        assert done["completed_at"]

    def test_other_writes_leave_them_alone(self, client):
        tid = create_task(client).get_json()["id"]
        client.post(self._url(tid), json={"text": "status"})
        client.put(f"/api/tasks/{tid}", json={"title": "Renamed", "history": []})
        assert client.get("/api/tasks").get_json()[0]["comments"][0]["text"] == "status"

    def test_put_replaces_the_list_for_undo(self, client):
        tid = create_task(client).get_json()["id"]
        before = client.post(self._url(tid), json={"text": "keep me"}).get_json()["comments"]
        client.delete(self._url(tid, before[0]["id"]))
        restored = client.put(f"/api/tasks/{tid}", json={"comments": before}).get_json()
        assert restored["comments"] == before
        # Validated: junk entries go, duplicate ids get new ones.
        dup = client.put(f"/api/tasks/{tid}", json={"comments": [
            before[0], before[0], {"text": "no time"}, "junk", {"at": "2026-01-01T10:00", "text": " "}]}).get_json()
        assert [c["text"] for c in dup["comments"]] == ["keep me", "keep me"]
        assert len({c["id"] for c in dup["comments"]}) == 2
        assert client.put(f"/api/tasks/{tid}", json={"comments": "x"}).status_code == 400

    def test_next_occurrence_carries_them_on(self, client):
        tid = create_task(client, rec_type="daily", due_date=_future(0)).get_json()["id"]
        client.post(self._url(tid), json={"text": "first round"})
        client.put(f"/api/tasks/{tid}", json={"completed": True})
        nxt = client.get("/api/tasks").get_json()[0]
        assert nxt["id"] != tid
        assert [c["text"] for c in nxt["comments"]] == ["first round"]

    def test_restore_keeps_them(self, client):
        task = create_task(client).get_json()
        snap = client.post(self._url(task["id"]), json={"text": "noted"}).get_json()
        client.delete(f"/api/tasks/{task['id']}")
        [restored] = client.post("/api/tasks/restore", json={"tasks": [snap]}).get_json()
        assert restored["comments"] == snap["comments"]

    def test_searchable(self, client):
        tid = create_task(client, title="Plan on-call").get_json()["id"]
        client.post(self._url(tid), json={"text": "asked alex about headcount"})
        results = client.get("/api/search?q=headcount", headers={"Host": "localhost:5000"}).get_json()
        assert [r.get("task_id") for r in results] == [tid]


# ---------- Notes rendered as pages, checkboxes in them ----------


class TestNotes:
    def test_render_notes_as_a_page_renders(self, client, wiki_dir):
        (wiki_dir / "garden.md").write_text("# Garden\n", encoding="utf-8")
        resp = client.post("/api/render-notes", json={
            "notes": ["## Plan\nfirst\nsecond\n\n- [ ] dig <!--d:2026-10-01T10:00:00-->\n\n"
                      "| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nx = 1\n```\n\nSee [[Garden]] and [[Nowhere]], _soon_."],
            "comments": ["**done**, see [[Garden]]"],
        })
        assert resp.status_code == 200
        [note], [comment] = resp.get_json()["notes"], resp.get_json()["comments"]
        assert "<h2>Plan</h2>" in note                       # no id: it would clash with the page's anchors
        assert "<p>first\nsecond</p>" in note                # one paragraph, as on a page
        assert 'class="task-list-item-checkbox"' in note
        assert '<span class="task-date task-date-done" title="Done 2026-10-01T10:00:00">2026-10-01</span>' in note
        assert "<table>" in note and '<code class="language-py">' in note
        assert '<a href="/page/garden" class="wikilink">Garden</a>' in note
        assert "wikilink-missing" in note and "<em>soon</em>" in note
        # A comment renders as the notes do: a line is a paragraph.
        assert comment.strip() == '<p><strong>done</strong>, see <a href="/page/garden" class="wikilink">Garden</a></p>'

    def test_render_notes_task_chips_and_no_page_directives(self, client):
        tid = create_task(client, title="Order seeds").get_json()["id"]
        [note] = client.post("/api/render-notes", json={"notes": [f"[[task:{tid[:8]}]]\n\n[[tasks:]]"]}).get_json()["notes"]
        assert 'class="task-ref"' in note and "Order seeds" in note
        assert "tasks-app" not in note

    def test_render_notes_validates(self, client):
        assert client.post("/api/render-notes", json={}).get_json() == {"notes": [], "comments": []}
        assert client.post("/api/render-notes", json={"notes": "x"}).status_code == 400
        assert client.post("/api/render-notes", json={"notes": [1]}).status_code == 400
        assert client.post("/api/render-notes", data="x").status_code == 400

    def _toggle(self, client, tid, idx):
        return client.post(f"/api/tasks/{tid}/toggle-check", json={"idx": idx})

    def test_toggle_check_records_when_and_undoes(self, client):
        tid = create_task(client, title="Bake", description="Need:\n- [ ] eggs\n- [x] flour").get_json()["id"]
        resp = self._toggle(client, tid, 0)
        assert resp.status_code == 200
        desc = resp.get_json()["description"]
        assert re.fullmatch(r"Need:\n- \[x\] eggs <!--d:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-->\n- \[x\] flour", desc)
        assert client.get("/api/tasks").get_json()[0]["description"] == desc
        # Again (undo): unticked, the date gone.
        assert self._toggle(client, tid, 0).get_json()["description"] == "Need:\n- [ ] eggs\n- [x] flour"
        assert self._toggle(client, tid, 1).get_json()["description"] == "Need:\n- [ ] eggs\n- [ ] flour"

    def test_toggle_check_counts_as_render_notes_does(self, client):
        # Notes have no frontmatter: a leading --- block is text, and its
        # checkbox is the first one, as rendered. Fenced code has none.
        desc = "---\n- [ ] one\n---\n```\n- [ ] code\n```\n- [ ] two"
        tid = create_task(client, title="T", description=desc).get_json()["id"]
        [html] = client.post("/api/render-notes", json={"notes": [desc]}).get_json()["notes"]
        assert html.count("task-list-item-checkbox") == 2
        toggled = self._toggle(client, tid, 1).get_json()["description"]
        assert re.search(r"\n- \[x\] two <!--d:[^>]+-->$", toggled)
        assert "- [ ] one" in toggled and "- [ ] code" in toggled

    def test_toggle_check_out_of_range_and_errors(self, client):
        task = create_task(client, title="T", description="- [ ] a").get_json()
        assert self._toggle(client, task["id"], 5).get_json()["description"] == "- [ ] a"
        assert self._toggle(client, task["id"], -1).status_code == 400
        assert client.post(f"/api/tasks/{task['id']}/toggle-check", json={"idx": "x"}).status_code == 400
        assert self._toggle(client, "nope", 0).status_code == 404
