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



class TestIsUnplanned:
    """All tab's "Unplanned only" filter: work with no decided day."""

    def test_no_dates_is_unplanned(self):
        assert run_core("DeckCore.isUnplanned({title: 'x'})") is True

    def test_due_date_alone_is_still_unplanned(self):
        # A deadline is not a decision about when to do the work.
        t = {"due_date": "2026-08-01"}
        assert run_core(f"DeckCore.isUnplanned({json.dumps(t)})") is True

    def test_start_date_makes_it_planned(self):
        t = {"start_date": "2026-08-01"}
        assert run_core(f"DeckCore.isUnplanned({json.dumps(t)})") is False

    def test_past_start_date_still_counts_as_planned(self):
        t = {"start_date": "2026-01-01"}
        assert run_core(f"DeckCore.isUnplanned({json.dumps(t)})") is False


class TestIsFocus:
    """Focus = started, or due today or earlier."""

    @pytest.mark.parametrize("task,expected", [
        ({}, False),
        ({"start_date": TODAY}, True),
        ({"start_date": "2026-07-01"}, True),          # started long ago
        ({"start_date": "2026-07-27"}, False),         # starts tomorrow
        ({"due_date": TODAY}, True),
        ({"due_date": "2026-07-01"}, True),            # overdue
        ({"due_date": "2026-07-30"}, False),
        ({"due_date": "2026-07-01", "start_date": "2026-08-01"}, True),  # overdue wins
    ])
    def test_is_focus(self, task, expected):
        assert run_core(f"DeckCore.isFocus({json.dumps(task)}, '{TODAY}')") is expected


class TestBoardBuckets:
    def _by_key(self, tasks):
        sections = run_core(f"DeckCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        return {s["key"]: [t["id"] for t in s["tasks"]] for s in sections}

    def test_grouping_is_by_start_date(self):
        by_key = self._by_key([
            {"id": "1", "title": "starts tomorrow", "start_date": "2026-07-27"},
            {"id": "2", "title": "starts today, due later",
             "due_date": "2026-08-20", "start_date": TODAY},
            {"id": "3", "title": "overdue", "due_date": "2026-07-01"},
            {"id": "4", "title": "nothing"},
            {"id": "5", "title": "starts later", "start_date": "2026-09-01"},
        ])
        assert by_key["overdue"] == ["3"]
        assert by_key[TODAY] == ["2"]
        assert by_key["2026-07-27"] == ["1"]
        assert by_key["later"] == ["5"]
        assert by_key["unplanned"] == ["4"]

    def test_past_start_date_is_today_not_overdue(self):
        # Regression: a start date in the past put the task under Overdue,
        # though the card itself says "since …" without any warning.
        by_key = self._by_key([{"id": "1", "title": "x", "start_date": "2026-07-01"}])
        assert by_key[TODAY] == ["1"]
        assert "overdue" not in by_key

    def test_due_date_alone_is_unplanned_not_on_its_deadline(self):
        by_key = self._by_key([{"id": "1", "title": "x", "due_date": "2026-07-28"}])
        assert by_key["unplanned"] == ["1"]
        assert by_key["2026-07-28"] == []

    def test_due_today_without_start_is_today(self):
        by_key = self._by_key([{"id": "1", "title": "x", "due_date": TODAY}])
        assert by_key[TODAY] == ["1"]

    def test_missed_deadline_is_overdue_even_if_started(self):
        by_key = self._by_key([{"id": "1", "title": "x", "due_date": "2026-07-20",
                                "start_date": "2026-07-18"}])
        assert by_key["overdue"] == ["1"]

    def test_unplanned_sorted_by_nearest_deadline(self):
        by_key = self._by_key([
            {"id": "a", "title": "a", "sort_order": 0},
            {"id": "b", "title": "b", "due_date": "2026-09-01"},
            {"id": "c", "title": "c", "due_date": "2026-08-01"},
        ])
        assert by_key["unplanned"] == ["c", "b", "a"]

    def test_later_and_unplanned_are_droppable_overdue_is_not(self):
        tasks = [
            {"id": "3", "title": "overdue", "due_date": "2026-07-01"},
            {"id": "4", "title": "nothing"},
            {"id": "5", "title": "later", "start_date": "2026-09-01"},
        ]
        sections = run_core(f"DeckCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        droppable = {s["key"]: s["droppable"] for s in sections}
        assert droppable["overdue"] is False
        assert droppable["later"] is True
        assert droppable["unplanned"] is True

    def test_seven_day_sections_always_present(self):
        sections = run_core(f"DeckCore.boardBuckets([], '{TODAY}')")
        day_sections = [s for s in sections if s["date"]]
        assert len(day_sections) == 7
        assert day_sections[0]["key"] == TODAY
        assert day_sections[0]["label"] == "Today"
        assert day_sections[1]["label"] == "Tomorrow"

    def test_empty_later_and_unplanned_still_offered_as_drop_targets(self):
        # Regression: they used to be omitted when empty, so once every task
        # was planned there was nowhere to drag one back to "unplanned".
        sections = run_core(f"DeckCore.boardBuckets([], '{TODAY}')")
        by_key = {s["key"]: s for s in sections}
        assert by_key["later"]["tasks"] == [] and by_key["later"]["droppable"]
        assert by_key["unplanned"]["tasks"] == [] and by_key["unplanned"]["droppable"]
        assert by_key["unplanned"]["label"] == "Unplanned"
        assert [s["key"] for s in sections][-2:] == ["later", "unplanned"]
        assert "overdue" not in by_key  # display-only, so still omitted when empty


class TestFilterField:
    """The deck's single filter field: text plus #tag words."""

    @pytest.mark.parametrize("q,expected", [
        ("", {"text": "", "tagGroups": []}),
        ("fix bug", {"text": "fix bug", "tagGroups": []}),
        ("#Work", {"text": "", "tagGroups": [["work"]]}),
        ("fix  #work #home bug", {"text": "fix bug", "tagGroups": [["work"], ["home"]]}),
        ("#a|b", {"text": "", "tagGroups": [["a", "b"]]}),
        ("#a|#b", {"text": "", "tagGroups": [["a", "b"]]}),
        ("#", {"text": "", "tagGroups": []}),            # a bare '#' while typing
    ])
    def test_parse(self, q, expected):
        assert run_core(f"DeckCore.parseFilter({json.dumps(q)})") == expected

    TASKS = [
        {"id": "1", "title": "Fix bug", "tags": ["work"]},
        {"id": "2", "title": "Buy milk", "description": "and bread", "tags": ["home"]},
        {"id": "3", "title": "Fix fence", "tags": ["home", "work"]},
    ]

    @pytest.mark.parametrize("q,ids", [
        ("fix", ["1", "3"]),
        ("bread", ["2"]),                 # description counts
        ("#work", ["1", "3"]),
        ("#work #home", ["3"]),           # separate words: all
        ("#work|home", ["1", "2", "3"]),  # pipe: any
        ("fix #home", ["3"]),
        ("#WORK", ["1", "3"]),
        ("#nope", []),
    ])
    def test_matches(self, q, ids):
        expr = (f"{json.dumps(self.TASKS)}.filter(t => DeckCore.matchesFilter(t, "
                f"DeckCore.parseFilter({json.dumps(q)}))).map(t => t.id)")
        assert run_core(expr) == ids

    @pytest.mark.parametrize("raw,q", [
        ("", ""), ("work", "#work"), ("a, b", "#a #b"), ("a|b", "#a|b"),
    ])
    def test_tag_param_to_query(self, raw, q):
        # [[deck tag=…]] keeps its old syntax: comma = all, pipe = any.
        assert run_core(f"DeckCore.tagParamToQuery({json.dumps(raw)})") == q


class TestTagMatchScore:
    def test_prefix_beats_substring_beats_subsequence(self):
        assert run_core("DeckCore.tagMatchScore('wo', 'work')") == 2
        assert run_core("DeckCore.tagMatchScore('or', 'work')") == 1
        assert run_core("DeckCore.tagMatchScore('wk', 'work')") == 0
        assert run_core("DeckCore.tagMatchScore('xyz', 'work')") == -1
