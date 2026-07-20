"""Comprehensive tests for the Leaf task app API."""

import sys
import os
import tempfile
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

    def test_create_invalid_start_date(self, client):
        resp = client.post("/api/deck", json={"title": "Bad start", "start_date": "2025/01/01"})
        assert resp.status_code == 400

    def test_update_invalid_due_date(self, client):
        task = create_task(client, title="Task").get_json()
        resp = client.put(f"/api/deck/{task['id']}", json={"due_date": "31-12-2025"})
        assert resp.status_code == 400

    def test_create_valid_due_date(self, client):
        resp = create_task(client, title="Dated", due_date="2025-06-15")
        assert resp.status_code == 201
        assert resp.get_json()["due_date"] == "2025-06-15"

    def test_create_valid_start_date(self, client):
        resp = create_task(client, title="Deferred", start_date="2025-07-01")
        assert resp.status_code == 201
        assert resp.get_json()["start_date"] == "2025-07-01"

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


class TestRecurring:
    def test_complete_recurring_spawns_new(self, client):
        task = create_task(client, title="Daily standup",
                           due_date="2025-03-01",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["title"] == "Daily standup"
        assert spawned["due_date"] == "2025-03-02"
        assert spawned["completed_at"] is None

    def test_daily_recurrence_interval(self, client):
        task = create_task(client, title="Every 3 days",
                           due_date="2025-01-10",
                           rec_type="daily", rec_interval=3).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2025-01-13"

    def test_weekly_recurrence(self, client):
        task = create_task(client, title="Weekly review",
                           due_date="2025-03-03",
                           rec_type="weekly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2025-03-10"

    def test_weekly_recurrence_interval(self, client):
        task = create_task(client, title="Biweekly",
                           due_date="2025-03-03",
                           rec_type="weekly", rec_interval=2).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2025-03-17"

    def test_monthly_recurrence(self, client):
        task = create_task(client, title="Monthly report",
                           due_date="2025-01-15",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2025-02-15"

    def test_monthly_recurrence_day_clamped(self, client):
        # Day 31 clamped to 28 to avoid overflow
        task = create_task(client, title="End of month",
                           due_date="2025-01-31",
                           rec_type="monthly", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["due_date"] == "2025-02-28"

    def test_spawned_task_start_date_is_none(self, client):
        task = create_task(client, title="Recurring deferred",
                           due_date="2025-03-01",
                           start_date="2025-02-28",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["start_date"] is None

    def test_spawned_task_preserves_tags(self, client):
        task = create_task(client, title="Tagged recurring",
                           due_date="2025-03-01",
                           rec_type="daily", rec_interval=1).get_json()
        client.put(f"/api/deck/{task['id']}", json={"tags": ["work", "standup"]})
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["tags"] == ["work", "standup"]

    def test_spawned_task_preserves_recurrence(self, client):
        task = create_task(client, title="Recurring",
                           due_date="2025-03-01",
                           rec_type="weekly", rec_interval=2).get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        spawned = next(t for t in tasks if t["id"] != task["id"])
        assert spawned["recurrence"] == {"type": "weekly", "interval": 2}

    def test_complete_non_recurring_no_spawn(self, client):
        task = create_task(client, title="One-off").get_json()
        client.put(f"/api/deck/{task['id']}", json={"completed": True})
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 0


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
        assert spawned[0]["start_date"] is None


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
            "title": "Recurring", "due_date": "2026-06-10",
            "rec_type": "daily", "rec_interval": 1,
        })
        task_id = r.get_json()["id"]
        resp = client.post("/api/deck/batch-complete", json={"ids": [task_id], "completed": True})
        assert resp.status_code == 200
        tasks = client.get("/api/deck").get_json()
        assert len(tasks) == 1
        spawned = [t for t in tasks if t["id"] != task_id]
        assert spawned[0]["due_date"] == "2026-06-11"
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
