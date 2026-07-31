"""Tests for the pure client-side deck logic in src/public/deck-core.js.

The module is DOM-free and exports via module.exports under Node, so these
tests run it with ``node -e`` and compare JSON output. They skip automatically
when Node is not installed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parent.parent / "src" / "public" / "deck-core.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def run_core(expr: str) -> object:
    """Evaluate an expression against DeckCore in Node and return it as JSON."""
    script = (
        f"const DeckCore = require({json.dumps(str(CORE))});"
        f"process.stdout.write(JSON.stringify({expr}));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


TODAY = "2026-07-26"


class TestDates:
    def test_add_days(self):
        assert run_core("DeckCore.addDays('2026-07-26', 6)") == "2026-08-01"
        assert run_core("DeckCore.addDays('2026-03-01', -1)") == "2026-02-28"


class TestIsUnscheduled:
    """Backlog's "Unscheduled only" filter: keep work not yet placed on a day."""

    def test_no_dates_is_unscheduled(self):
        assert run_core("DeckCore.isUnscheduled({title: 'x'})") is True

    def test_due_date_makes_it_scheduled(self):
        t = {"due_date": "2026-08-01"}
        assert run_core(f"DeckCore.isUnscheduled({json.dumps(t)})") is False

    def test_focus_date_makes_it_scheduled(self):
        t = {"focus_date": "2026-08-01"}
        assert run_core(f"DeckCore.isUnscheduled({json.dumps(t)})") is False

    def test_both_dates_makes_it_scheduled(self):
        t = {"due_date": "2026-08-01", "focus_date": "2026-07-28"}
        assert run_core(f"DeckCore.isUnscheduled({json.dumps(t)})") is False

    def test_lead_time_alone_is_still_unscheduled(self):
        # defer_days without a due date derives no plan, so nothing is scheduled.
        t = {"defer_days": 3}
        assert run_core(f"DeckCore.isUnscheduled({json.dumps(t)})") is True


class TestIsBlocked:
    def test_open_blocker_blocks(self):
        expr = (
            "DeckCore.isBlocked({blocked_by: ['b']}, "
            "new Map([['b', {completed_at: null}]]))"
        )
        assert run_core(expr) is True

    def test_completed_blocker_does_not_block(self):
        expr = (
            "DeckCore.isBlocked({blocked_by: ['b']}, "
            "new Map([['b', {completed_at: '2026-01-01T00:00:00'}]]))"
        )
        assert run_core(expr) is False

    def test_dangling_ref_does_not_block(self):
        assert run_core("DeckCore.isBlocked({blocked_by: ['gone']}, new Map())") is False


class TestBoardBuckets:
    def test_grouping_is_plan_first(self):
        tasks = [
            {"id": "1", "title": "due tomorrow", "due_date": "2026-07-27"},
            {"id": "2", "title": "planned today, due later",
             "due_date": "2026-08-20", "focus_date": TODAY},
            {"id": "3", "title": "overdue", "due_date": "2026-07-01"},
            {"id": "4", "title": "someday"},
            {"id": "5", "title": "later", "due_date": "2026-09-01"},
        ]
        sections = run_core(f"DeckCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        by_key = {s["key"]: [t["id"] for t in s["tasks"]] for s in sections}
        assert by_key["overdue"] == ["3"]
        assert by_key[TODAY] == ["2"]          # plan wins over deadline
        assert by_key["2026-07-27"] == ["1"]
        assert by_key["later"] == ["5"]
        assert by_key["someday"] == ["4"]

    def test_later_and_someday_are_droppable_overdue_is_not(self):
        tasks = [
            {"id": "3", "title": "overdue", "due_date": "2026-07-01"},
            {"id": "4", "title": "someday"},
            {"id": "5", "title": "later", "due_date": "2026-09-01"},
        ]
        sections = run_core(f"DeckCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        droppable = {s["key"]: s["droppable"] for s in sections}
        assert droppable["overdue"] is False
        assert droppable["later"] is True
        assert droppable["someday"] is True

    def test_seven_day_sections_always_present(self):
        sections = run_core(f"DeckCore.boardBuckets([], '{TODAY}')")
        day_sections = [s for s in sections if s["date"]]
        assert len(day_sections) == 7
        assert day_sections[0]["key"] == TODAY
        assert day_sections[0]["label"] == "Today"
        assert day_sections[1]["label"] == "Tomorrow"


class TestTagMatchScore:
    def test_prefix_beats_substring_beats_subsequence(self):
        assert run_core("DeckCore.tagMatchScore('wo', 'work')") == 2
        assert run_core("DeckCore.tagMatchScore('or', 'work')") == 1
        assert run_core("DeckCore.tagMatchScore('wk', 'work')") == 0
        assert run_core("DeckCore.tagMatchScore('xyz', 'work')") == -1
