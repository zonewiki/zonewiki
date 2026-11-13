"""Tests for the pure client-side tasks logic in public/tasks-core.js.

The module is DOM-free, so these tests import it in Node (``node -e``) and
compare JSON output. They skip automatically when Node is not installed.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parent.parent / "public" / "tasks-core.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


# tasks-core.js as an .mjs file: an older Node only loads a file as an ES
# module by that extension (there is no package.json to say so).
CORE_MJS = Path(tempfile.mkdtemp(prefix="taskscore_")) / "tasks-core.mjs"
CORE_MJS.write_text(CORE.read_text(encoding="utf-8"), encoding="utf-8")


def run_core(expr: str) -> object:
    """Evaluate an expression against TasksCore in Node and return it as JSON."""
    script = (
        f"import TasksCore from {json.dumps(CORE_MJS.as_uri())};"
        f"process.stdout.write(JSON.stringify({expr}));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


TODAY = "2026-07-26"


class TestDates:
    def test_add_days(self):
        assert run_core("TasksCore.addDays('2026-07-26', 6)") == "2026-08-01"
        assert run_core("TasksCore.addDays('2026-03-01', -1)") == "2026-02-28"



class TestLocale:
    """Dates in the wiki's locale; without one, US English with weeks from Monday."""

    def test_settings_from_the_body(self):
        assert run_core("TasksCore.dateSettings({})") == {"locale": "", "iso": False}
        assert run_core("TasksCore.dateSettings({locale: 'de-AT', dateFormat: 'iso'})") == {"locale": "de-AT", "iso": True}

    def test_short_date(self):
        assert run_core("TasksCore.shortDate('2026-10-05', '2026-10-18')") == "Oct 5"
        assert run_core("TasksCore.shortDate('2025-10-05', '2026-10-18')") == "Oct 5, 2025"
        assert run_core("TasksCore.shortDate('2026-10-05', '2026-10-18', {locale: 'en-GB'})") == "5 Oct"
        assert run_core("TasksCore.shortDate('2026-10-05', '2026-10-18', {locale: 'de-AT'})") == "5. Okt."

    def test_iso(self):
        iso = "{locale: 'de-AT', iso: true}"
        assert run_core(f"TasksCore.shortDate('2026-10-05T09:30', '2026-10-18', {iso})") == "2026-10-05"
        assert run_core(f"TasksCore.shortDate('2025-10-05', '2026-10-18', {iso})") == "2025-10-05"
        assert run_core("TasksCore.fullDayLabel('2026-10-21', {iso: true})") == "Wed 2026-10-21"
        assert run_core("TasksCore.longDayLabel('2026-10-21', {iso: true})") == "Wed 2026-10-21"
        assert run_core("TasksCore.longDayLabel('2026-10-21', {})") == "Wed, Oct 21"
        labels = run_core(f"TasksCore.boardBuckets([], '{TODAY}', '', {{iso: true}}).map(s => s.label)")
        assert "Tue 2026-07-28" in labels

    def test_week_start(self):
        assert run_core("TasksCore.weekStart('')") == 1
        assert run_core("TasksCore.weekStart('de-AT')") == 1
        assert run_core("TasksCore.weekStart('en-US')") == 0

    def test_calendar_heads_and_title(self):
        assert run_core("TasksCore.weekdayNames('', 1)") == ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        assert run_core("TasksCore.weekdayNames('en-US', 0)") == ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
        assert run_core("TasksCore.weekdayNames('de-AT', 1)") == ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        assert run_core("TasksCore.monthTitle(2026, 9)") == "October 2026"
        assert run_core("TasksCore.monthTitle(2026, 9, 'de-AT')") == "Oktober 2026"

    def test_board_day_labels(self):
        labels = run_core(f"TasksCore.boardBuckets([], '{TODAY}', '', {{locale: 'de-AT'}}).map(s => s.label)")
        assert "Di., 28." in labels   # 2026-07-28, a Tuesday


class TestOrders:
    def _sorted(self, fn, tasks):
        return run_core(f"{json.dumps(tasks)}.sort(TasksCore.{fn}).map(t => t.id)")

    def test_open_order_unsorted_newest_first_then_sorted(self):
        assert self._sorted("byOpenOrder", [
            {"id": "s1", "sort_order": 1},
            {"id": "old", "created_at": "2026-07-25T09:00"},
            {"id": "s0", "sort_order": 0},
            {"id": "new", "created_at": "2026-07-26T09:00"},
        ]) == ["new", "old", "s0", "s1"]

    def test_no_focus_order(self):
        # Focus uses Open's order; there is no comparator of its own.
        assert run_core("typeof TasksCore.byFocusOrder") == "undefined"


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
        assert run_core(f"TasksCore.isFocus({json.dumps(task)}, '{TODAY}')") is expected


class TestBoardBuckets:
    def _by_key(self, tasks):
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
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
        assert "overdue" not in by_key        # no section of its own
        assert by_key[TODAY] == ["2"]
        assert by_key["2026-07-27"] == ["1"]
        assert by_key["later"] == ["5"]
        assert sorted(by_key["unplanned"]) == ["3", "4"]

    def test_past_start_date_is_on_today_not_overdue(self):
        # Regression: a start date in the past put the task under Overdue,
        # though the card itself says "since …" without any warning. It is
        # in Focus, today's load: on Today, carried over.
        by_key = self._by_key([{"id": "1", "title": "x", "start_date": "2026-07-01"}])
        assert by_key[TODAY] == ["1"]
        assert "overdue" not in by_key and "carried" not in by_key

    def test_carried_over_tasks_share_todays_open_order(self):
        by_key = self._by_key([
            {"id": "recent", "start_date": "2026-07-24", "created_at": "2026-07-02T09:00"},
            {"id": "today", "start_date": TODAY, "sort_order": 2},
            {"id": "sorted-1", "start_date": "2026-07-24", "sort_order": 1},
            {"id": "sorted-0", "start_date": "2026-07-01", "sort_order": 0},
        ])
        assert by_key[TODAY] == ["recent", "sorted-0", "sorted-1", "today"]

    def test_today_lists_its_carried_over_tasks_longest_slipping_first(self):
        tasks = [
            {"id": "recent", "start_date": "2026-07-24", "sort_order": 0},
            {"id": "old", "start_date": "2026-07-01", "sort_order": 1},
            {"id": "today", "start_date": TODAY},
            {"id": "done", "start_date": "2026-07-01", "closed_at": "2026-07-02T10:00"},
        ]
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        by_key = {s["key"]: s for s in sections}
        assert [t["id"] for t in by_key[TODAY]["carried"]] == ["old", "recent"]
        assert all("carried" not in s or s["key"] == TODAY for s in sections)
        assert run_core(f"[{json.dumps(tasks[1])}, {json.dumps(tasks[2])}, {json.dumps(tasks[3])}]"
                        f".map(t => TasksCore.isCarried(t, '{TODAY}'))") == [True, False, False]

    def test_day_sections_name_their_deadlines_wherever_the_cards_sit(self):
        tasks = [
            {"id": "unplanned", "due_date": "2026-07-28"},
            {"id": "planned-today", "start_date": TODAY, "due_date": "2026-07-28"},
            {"id": "planned-that-day", "start_date": "2026-07-28", "due_date": "2026-07-28"},
            {"id": "due-later", "due_date": "2026-09-01"},
        ]
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        due = {s["key"]: [t["id"] for t in s.get("due", [])] for s in sections}
        assert sorted(due["2026-07-28"]) == ["planned-that-day", "planned-today", "unplanned"]
        assert due[TODAY] == []
        # Cards stay where their start date puts them.
        by_key = {s["key"]: [t["id"] for t in s["tasks"]] for s in sections}
        assert sorted(by_key["unplanned"]) == ["due-later", "unplanned"] and by_key[TODAY] == ["planned-today"]
        assert "due-later" not in sum(due.values(), [])

    def test_unplanned_first_then_today_days_later(self):
        tasks = [
            {"id": "1", "title": "overdue", "due_date": "2026-07-01"},
            {"id": "2", "title": "carried", "start_date": "2026-07-20"},
            {"id": "3", "title": "loose"},
        ]
        keys = [s["key"] for s in run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")]
        assert keys[:2] == ["unplanned", TODAY]
        assert keys[-1] == "later"

    def test_due_date_alone_is_unplanned_not_on_its_deadline(self):
        by_key = self._by_key([{"id": "1", "title": "x", "due_date": "2026-07-28"}])
        assert by_key["unplanned"] == ["1"]
        assert by_key["2026-07-28"] == []

    def test_due_today_without_start_is_today(self):
        by_key = self._by_key([{"id": "1", "title": "x", "due_date": TODAY}])
        assert by_key[TODAY] == ["1"]

    def test_overdue_tasks_stay_where_their_start_puts_them(self):
        by_key = self._by_key([
            {"id": "slipped", "due_date": "2026-07-20", "start_date": "2026-07-18"},
            {"id": "today", "due_date": "2026-07-20", "start_date": TODAY},
            {"id": "accepted", "due_date": "2026-07-20", "start_date": "2026-07-29"},
            {"id": "due-today-later", "due_date": TODAY, "start_date": "2026-07-28"},
        ])
        assert sorted(by_key[TODAY]) == ["slipped", "today"]
        assert by_key["2026-07-29"] == ["accepted"]
        assert by_key["2026-07-28"] == ["due-today-later"]
        assert "overdue" not in by_key

    def test_unplanned_lists_its_overdue_tasks_most_overdue_first(self):
        tasks = [
            {"id": "loose", "sort_order": 0},
            {"id": "due-soon", "due_date": "2026-08-01"},
            {"id": "late", "due_date": "2026-07-20"},
            {"id": "very-late", "due_date": "2026-07-01"},
            {"id": "late-but-started", "due_date": "2026-07-20", "start_date": "2026-07-19"},
        ]
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        unplanned = sections[0]
        assert sorted(t["id"] for t in unplanned["tasks"]) == ["due-soon", "late", "loose", "very-late"]
        assert [t["id"] for t in unplanned["overdue"]] == ["very-late", "late"]

    def test_every_section_is_droppable(self):
        tasks = [
            {"id": "3", "title": "carried", "start_date": "2026-07-01"},
            {"id": "4", "title": "nothing"},
            {"id": "5", "title": "later", "start_date": "2026-09-01"},
        ]
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        assert all(s["droppable"] for s in sections)

    def test_seven_day_sections_always_present(self):
        sections = run_core(f"TasksCore.boardBuckets([], '{TODAY}')")
        day_sections = [s for s in sections if s["date"]]
        assert len(day_sections) == 7
        assert day_sections[0]["key"] == TODAY
        assert day_sections[0]["label"] == "Today"
        assert day_sections[1]["label"] == "Tomorrow"

    def test_days_and_later_follow_open_order(self):
        # Days and Later list tasks in Open's order: Unsorted
        # newest first, then Sorted.
        later = "2026-09-01"
        by_key = self._by_key([
            {"id": "t-sorted1", "start_date": TODAY, "sort_order": 1},
            {"id": "t-sorted0", "start_date": TODAY, "sort_order": 0},
            {"id": "t-old", "start_date": TODAY, "created_at": "2026-07-20T09:00"},
            {"id": "t-new", "start_date": TODAY, "created_at": "2026-07-25T09:00"},
            {"id": "m-sorted", "start_date": "2026-07-28", "sort_order": 0},
            {"id": "m-new", "start_date": "2026-07-28", "created_at": "2026-07-25T09:00"},
            {"id": "l-sorted", "start_date": later, "sort_order": 0},
            {"id": "l-new", "start_date": "2026-10-01", "created_at": "2026-07-25T09:00"},
        ])
        assert by_key[TODAY] == ["t-new", "t-old", "t-sorted0", "t-sorted1"]
        assert by_key["2026-07-28"] == ["m-new", "m-sorted"]
        assert by_key["later"] == ["l-new", "l-sorted"]

    def test_unplanned_is_in_open_order_its_overdue_by_deadline(self):
        tasks = [
            {"id": "sorted", "sort_order": 0, "due_date": "2026-07-20"},
            {"id": "unsorted", "created_at": "2026-07-25T09:00"},
            {"id": "deadline", "due_date": "2026-07-10", "sort_order": 5},
        ]
        assert self._by_key(tasks)["unplanned"] == ["unsorted", "sorted", "deadline"]
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        unplanned = next(s for s in sections if s["key"] == "unplanned")
        assert [t["id"] for t in unplanned["overdue"]] == ["deadline", "sorted"]

    def test_empty_later_and_unplanned_still_offered_as_drop_targets(self):
        # Regression: they used to be omitted when empty, so once every task
        # was planned there was nowhere to drag one back to "unplanned".
        sections = run_core(f"TasksCore.boardBuckets([], '{TODAY}')")
        by_key = {s["key"]: s for s in sections}
        assert by_key["later"]["tasks"] == [] and by_key["later"]["droppable"]
        assert by_key["unplanned"]["tasks"] == [] and by_key["unplanned"]["droppable"]
        assert by_key["unplanned"]["label"] == "Unplanned"
        keys = [s["key"] for s in sections]
        assert keys[0] == "unplanned" and keys[-1] == "later"


class TestFilterField:
    """The tasks' single filter field: text plus #tag words."""

    @pytest.mark.parametrize("q,expected", [
        ("", {"text": "", "tagGroups": [], "excludeTags": [], "importance": ""}),
        ("fix bug", {"text": "fix bug", "tagGroups": [], "excludeTags": [], "importance": ""}),
        ("#Work", {"text": "", "tagGroups": [["work"]], "excludeTags": [], "importance": ""}),
        ("fix  #work #home bug", {"text": "fix bug", "tagGroups": [["work"], ["home"]], "excludeTags": [], "importance": ""}),
        ("#a|b", {"text": "", "tagGroups": [["a", "b"]], "excludeTags": [], "importance": ""}),
        ("#a|#b", {"text": "", "tagGroups": [["a", "b"]], "excludeTags": [], "importance": ""}),
        ("#", {"text": "", "tagGroups": [], "excludeTags": [], "importance": ""}),            # a bare '#' while typing
        ("#work -#Mary", {"text": "", "tagGroups": [["work"]], "excludeTags": ["mary"], "importance": ""}),
        ("-#a|b -#c", {"text": "", "tagGroups": [], "excludeTags": ["a", "b", "c"], "importance": ""}),
        ("-#", {"text": "", "tagGroups": [], "excludeTags": [], "importance": ""}),           # a bare '-#' while typing
        ("pre-fix", {"text": "pre-fix", "tagGroups": [], "excludeTags": [], "importance": ""}),
        ("fix !high #work", {"text": "fix", "tagGroups": [["work"]], "excludeTags": [], "importance": "high"}),
        ("! !low", {"text": "", "tagGroups": [], "excludeTags": [], "importance": "low"}),      # the last one counts
        ("wow!", {"text": "wow!", "tagGroups": [], "excludeTags": [], "importance": ""}),
    ])
    def test_parse(self, q, expected):
        assert run_core(f"TasksCore.parseFilter({json.dumps(q)})") == {**expected, "assigneeGroups": [], "excludeAssignees": []}

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
        ("-#home", ["1"]),                # exclusion
        ("#work -#home", ["1"]),
        ("-#work|home", []),              # excludes either
        ("-#nope", ["1", "2", "3"]),
        ("fix -#WORK", []),
    ])
    def test_matches(self, q, ids):
        expr = (f"{json.dumps(self.TASKS)}.filter(t => TasksCore.matchesFilter(t, "
                f"TasksCore.parseFilter({json.dumps(q)}))).map(t => t.id)")
        assert run_core(expr) == ids

    @pytest.mark.parametrize("raw,q", [
        ("", ""), ("work", "#work"), ("a, b", "#a #b"), ("a|b", "#a|b"),
        ("work,-mary", "#work -#mary"),
    ])
    def test_tag_param_to_query(self, raw, q):
        # [[tasks: tag=…]] keeps its old syntax: comma = all, pipe = any.
        assert run_core(f"TasksCore.tagParamToQuery({json.dumps(raw)})") == q


class TestTagMatchScore:
    def test_prefix_beats_substring_beats_subsequence(self):
        assert run_core("TasksCore.tagMatchScore('wo', 'work')") == 2
        assert run_core("TasksCore.tagMatchScore('or', 'work')") == 1
        assert run_core("TasksCore.tagMatchScore('wk', 'work')") == 0
        assert run_core("TasksCore.tagMatchScore('xyz', 'work')") == -1


def quick(title: str) -> dict:
    return run_core(f"TasksCore.parseQuickAdd({json.dumps(title)}, {json.dumps(TODAY)})")


class TestQuickAdd:
    # TODAY (2026-07-26) is a Sunday.

    def test_full_example(self):
        q = quick("Call Bob tomorrow #work due:fri")
        assert q == {"title": "Call Bob", "start_date": "2026-07-27",
                     "due_date": "2026-07-31", "tags": ["work"], "importance": "", "assignee": "", "found": True}

    @pytest.mark.parametrize("title,importance,rest", [
        ("Call Bob !", "high", "Call Bob"), ("Call Bob !high", "high", "Call Bob"), ("Call Bob !LOW", "low", "Call Bob"),
        ("Call Bob ! tomorrow #work", "high", "Call Bob"), ("Call Bob tomorrow !low", "low", "Call Bob"),
        ("Call Bob!", "", "Call Bob!"), ("Wow !!", "", "Wow !!"), ("Fix !important bug", "", "Fix !important bug"),
        ("!", "", "!"),                        # a title is never only shorthand
    ])
    def test_importance(self, title, importance, rest):
        q = quick(title)
        assert (q["importance"], q["title"]) == (importance, rest)

    @pytest.mark.parametrize("word,expected", [
        ("today", "2026-07-26"), ("tomorrow", "2026-07-27"), ("tmrw", "2026-07-27"),
        ("mon", "2026-07-27"), ("friday", "2026-07-31"), ("thurs", "2026-07-30"),
        ("sun", "2026-08-02"),          # a weekday is the next one, never today
        ("+3", "2026-07-29"), ("+3d", "2026-07-29"), ("+2w", "2026-08-09"),
        ("2026-10-03", "2026-10-03"),
    ])
    def test_start_words(self, word, expected):
        assert quick(f"Do it {word}")["start_date"] == expected

    def test_only_trailing_words_count(self):
        # A date word inside the sentence is text.
        assert quick("Read about the monday effect")["found"] is False
        q = quick("Plan monday meeting fri")
        assert q["title"] == "Plan monday meeting" and q["start_date"] == "2026-07-31"

    def test_numeric_hash_is_not_a_tag(self):
        assert quick("Fix #3")["found"] is False
        assert quick("Fix #3 #bugs")["title"] == "Fix #3"

    def test_invalid_date_stays_text(self):
        assert quick("Pay rent due:2026-02-31")["found"] is False
        assert quick("Pay rent due:someday")["found"] is False

    def test_never_leaves_an_empty_title(self):
        assert quick("tomorrow")["title"] == "tomorrow"
        assert quick("#work fri")["title"] == "#work"

    def test_tags_deduplicated_and_lowercased(self):
        assert quick("Buy milk #Home #home #shop")["tags"] == ["home", "shop"]

    def test_second_start_word_stays_in_title(self):
        q = quick("Meet tomorrow fri")
        assert q["title"] == "Meet tomorrow" and q["start_date"] == "2026-07-31"


class TestTagRanking:
    COUNTS = {"work": 5, "workout": 1, "wood": 9, "at-work": 2, "web": 3}

    def rank(self, partial, counts=None, limit=8):
        return run_core(f"TasksCore.rankTags({json.dumps(partial)}, {json.dumps(counts or self.COUNTS)}, {limit})")

    def test_prefix_before_substring_most_used_first(self):
        assert self.rank("wo") == ["wood", "work", "workout", "at-work"]

    def test_exact_match_first_even_if_rarer(self):
        assert self.rank("work", {"work": 1, "workshop": 9}) == ["work", "workshop"]

    def test_case_insensitive(self):
        assert self.rank("WO", {"Work": 2, "wood": 1}) == ["Work", "wood"]

    def test_empty_partial_lists_most_used(self):
        assert self.rank("", limit=3) == ["wood", "work", "web"]

    def test_no_match(self):
        assert self.rank("xyz") == []

    def test_tag_counts(self):
        tasks = [{"tags": ["a", "b"]}, {"tags": ["a"]}, {}]
        assert run_core(f"TasksCore.tagCounts({json.dumps(tasks)})") == {"a": 2, "b": 1}


class TestBulkTags:
    def test_selection_tags_count_tasks_case_ignored(self):
        tasks = [{"tags": ["Work", "b"]}, {"tags": ["work", "a", "WORK"]}, {"tags": ["b"]}, {}]
        assert run_core(f"TasksCore.selectionTags({json.dumps(tasks)})") == [
            {"tag": "b", "count": 2}, {"tag": "Work", "count": 2}, {"tag": "a", "count": 1}]

    def change(self, states, added=()):
        return run_core(f"TasksCore.bulkTagChange({json.dumps(states)}, {json.dumps(list(added))})")

    def test_all_adds_none_removes_some_stays(self):
        assert self.change({"a": "all", "b": "none", "c": "some"}, ["new"]) == {
            "add": ["a", "new"], "remove": ["b"]}

    def test_typed_tag_set_to_none_goes(self):
        assert self.change({"a": "none"}, ["A", "x", "X"]) == {"add": ["x"], "remove": ["a"]}

    def test_nothing_changed(self):
        assert self.change({"a": "some"}) == {"add": [], "remove": []}


class TestSanitizeTagInput:
    def clean(self, value, caret=None, lst=True):
        return run_core(f"TasksCore.sanitizeTagInput({json.dumps(value)}, {json.dumps(caret)}, {json.dumps(lst)})")

    @pytest.mark.parametrize("value,expected", [
        ("local ", "local-"),            # a space inside a tag is a hyphen at once
        ("local llm", "local-llm"),
        (" a", "a"),                     # no leading space
        ("work,", "work, "),             # a comma brings its one space
        ("work,  home", "work, home"),
        ("work ,home", "work, home"),    # a finished tag loses a trailing "-"
        ("a b, c  d, e", "a-b, c-d, e"),
    ])
    def test_list_field(self, value, expected):
        assert self.clean(value)["value"] == expected

    def test_single_tag_field(self):
        assert self.clean("local llm", lst=False)["value"] == "local-llm"
        assert self.clean("a, b", lst=False)["value"] == "a,-b"   # no list: comma is just a char

    def test_caret_follows_the_edit(self):
        # Typing "," between a and b: the caret lands before b, after the new space.
        assert self.clean("a,b", caret=2) == {"value": "a, b", "caret": 3}
        assert self.clean("local llm", caret=5) == {"value": "local-llm", "caret": 5}


class TestManualOrder:
    def _place(self, old, shown, moved):
        return run_core(f"TasksCore.placeInOrder({json.dumps(old)}, {json.dumps(shown)}, {json.dumps(moved)})")

    def test_moved_task_goes_after_the_visible_one_above_hidden_ones_stay(self):
        assert self._place(list("ahbc"), list("acb"), ["c"]) == list("achb")

    def test_moved_task_first_goes_before_the_one_below(self):
        assert self._place(list("ahbc"), list("cab"), ["c"]) == list("cahb")

    def test_every_section_is_ordered(self):
        tasks = [{"id": "c", "title": "C", "start_date": "2026-07-20"}]
        sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, '{TODAY}')")
        assert all(s.get("ordered") for s in sections)

    def test_by_deadline_nearest_first_then_open_order_undated_last(self):
        tasks = [
            {"id": "none-new", "created_at": "2026-07-02"},
            {"id": "fri", "due_date": "2026-07-31", "sort_order": 0},
            {"id": "tue-b", "due_date": "2026-07-28", "sort_order": 2},
            {"id": "tue-a", "due_date": "2026-07-28", "sort_order": 1},
            {"id": "none-sorted", "sort_order": 0},
        ]
        assert run_core(f"{json.dumps(tasks)}.sort(TasksCore.byDeadline).map(t => t.id)") == [
            "tue-a", "tue-b", "fri", "none-new", "none-sorted"]

    def test_by_created_newest_first(self):
        tasks = [
            {"id": "a", "created_at": "2026-07-01T09:00:00"},
            {"id": "c", "created_at": "2026-07-03T09:00:00"},
            {"id": "b", "created_at": "2026-07-02T09:00:00"},
        ]
        assert run_core(f"{json.dumps(tasks)}.sort(TasksCore.byCreated).map(t => t.id)") == ["c", "b", "a"]

    def test_board_sort_sorts_every_section_and_none_is_ordered(self):
        today = "2026-07-27"
        tasks = [
            {"id": "d-old", "start_date": today, "created_at": "2026-07-01", "sort_order": 0},
            {"id": "d-new", "start_date": today, "created_at": "2026-07-05", "sort_order": 1, "due_date": "2026-07-29"},
            {"id": "u-old", "created_at": "2026-07-01", "due_date": "2026-07-28"},
            {"id": "u-new", "created_at": "2026-07-05"},
        ]
        for sort, today_ids, unplanned_ids in (
                (None, ["d-old", "d-new"], ["u-new", "u-old"]),
                ("due", ["d-new", "d-old"], ["u-old", "u-new"]),
                ("created", ["d-new", "d-old"], ["u-new", "u-old"])):
            sections = run_core(f"TasksCore.boardBuckets({json.dumps(tasks)}, {json.dumps(today)}, {json.dumps(sort)})"
                                ".map(s => ({key: s.key, ordered: !!s.ordered, ids: s.tasks.map(t => t.id)}))")
            by_key = {s["key"]: s for s in sections}
            assert by_key[today]["ids"] == today_ids
            assert by_key["unplanned"]["ids"] == unplanned_ids
            assert by_key[today]["ordered"] is (sort is None)


class TestTasksInView:
    """What a tab shows: the tasks of its kind that pass the filter."""

    TASKS = [
        {"id": "started", "title": "Started", "tags": ["home"], "start_date": "2026-07-20"},
        {"id": "later", "title": "Later", "tags": ["work"], "start_date": "2026-08-01"},
        {"id": "due", "title": "Due today", "tags": ["work"], "due_date": TODAY},
        {"id": "plain", "title": "No dates", "tags": []},
        {"id": "done", "title": "Done", "tags": ["work"], "start_date": "2026-07-01", "closed_at": "2026-07-02T10:00:00"},
    ]

    def ids(self, view, query=""):
        return run_core(f"TasksCore.tasksInView({json.dumps(self.TASKS)}, TasksCore.parseFilter({json.dumps(query)}), "
                        f"'{view}', '{TODAY}').map(t => t.id)")

    def test_focus_is_what_is_available_now(self):
        assert self.ids("focus") == ["started", "due"]

    def test_open_and_the_plan_board_show_every_open_task(self):
        assert self.ids("open") == self.ids("plan") == ["started", "later", "due", "plain"]

    def test_completed_shows_only_the_completed(self):
        assert self.ids("closed") == ["done"]

    def test_the_filter_takes_an_importance(self):
        tasks = [{"id": "a", "importance": "high"}, {"id": "b"}, {"id": "c", "importance": "low", "tags": ["work"]}]
        ids = lambda q: run_core(f"TasksCore.tasksInView({json.dumps(tasks)}, TasksCore.parseFilter({json.dumps(q)}), "
                                 f"'open', '{TODAY}').map(t => t.id)")
        assert ids("!high") == ids("!") == ["a"] and ids("!low") == ["c"] and ids("") == ["a", "b", "c"]
        assert ids("!low #work") == ["c"] and ids("!high #work") == []
        assert ids("!x") == []                  # any other word is text to find

    def test_the_filter_narrows_every_tab(self):
        assert self.ids("open", "#work") == ["later", "due"]
        assert self.ids("focus", "#work") == ["due"]
        assert self.ids("closed", "#home") == []
        assert self.ids("open", "dates") == ["plain"]


class TestAssignee:
    """With users, '@' words name a task's assignee: in the filter (@anna,
    @anna|bob, -@anna, @me, @none) and at the end of a new task's title."""

    PEOPLE = "{me: 'alice', users: new Set(['alice', 'bob'])}"
    TASKS = [
        {"id": "a", "title": "Alice's", "assignee": "alice", "tags": ["work"]},
        {"id": "b", "title": "Bob's", "assignee": "bob"},
        {"id": "z", "title": "Zed's", "assignee": "zed"},     # zed can no longer be given tasks
        {"id": "n", "title": "No one's"},
    ]

    def ids(self, query, people=PEOPLE):
        return run_core(f"TasksCore.tasksInView({json.dumps(self.TASKS)}, "
                        f"TasksCore.parseFilter({json.dumps(query)}, {people}), 'open', '{TODAY}').map(t => t.id)")

    def test_the_filter_reads_the_words(self):
        f = run_core(f"(f => [f.assigneeGroups, f.excludeAssignees, f.tagGroups, f.text])"
                     f"(TasksCore.parseFilter('@Anna @bob|carol -@Dan|eve #work call @', {self.PEOPLE}))")
        assert f == [[["anna"], ["bob", "carol"]], ["dan", "eve"], [["work"]], "call"]

    def test_without_users_an_at_word_is_text(self):
        f = run_core("(f => [f.assigneeGroups, f.excludeAssignees, f.text])(TasksCore.parseFilter('@anna -@bob'))")
        assert f == [[], [], "@anna -@bob"]
        assert run_core("TasksCore.filterActive(TasksCore.parseFilter(''))") is False

    def test_a_name_any_of_several_and_not(self):
        assert self.ids("@bob") == ["b"]
        assert self.ids("@alice|bob") == ["a", "b"]
        assert self.ids("-@bob") == ["a", "z", "n"]
        assert self.ids("@alice #work") == ["a"] and self.ids("@bob #work") == []
        assert self.ids("@alice @bob") == []           # a task has one

    def test_me_is_whoever_is_signed_in(self):
        assert self.ids("@me") == ["a"]
        assert self.ids("@me", "{me: 'bob', users: new Set(['alice', 'bob'])}") == ["b"]
        assert self.ids("@me", "{me: '', users: new Set(['alice'])}") == []
        assert self.ids("-@me") == ["b", "z", "n"]

    def test_none_is_no_one_and_whoever_can_no_longer_be_assigned(self):
        assert self.ids("@none") == ["z", "n"]
        assert self.ids("@zed") == ["z"]               # still found by the name
        assert self.ids("-@none") == ["a", "b"]
        assert self.ids("@none|bob") == ["b", "z", "n"]

    def test_an_assignee_filter_counts_as_filtering(self):
        active = lambda q: run_core(f"TasksCore.filterActive(TasksCore.parseFilter({json.dumps(q)}, {self.PEOPLE}))")
        assert active("@me") and active("-@bob") and not active("@") and not active("")

    def quick(self, title, people=PEOPLE):
        q = run_core(f"TasksCore.parseQuickAdd({json.dumps(title)}, '{TODAY}', {people})")
        return q["title"], q["assignee"], q["found"]

    def test_quick_add_assigns(self):
        assert self.quick("Call Sam @bob") == ("Call Sam", "bob", True)
        assert self.quick("Call Sam @Bob #work tomorrow") == ("Call Sam", "bob", True)
        assert self.quick("Call Sam @me") == ("Call Sam", "alice", True)
        q = run_core(f"TasksCore.parseQuickAdd('Call Sam #work @bob due:tomorrow', '{TODAY}', {self.PEOPLE})")
        assert (q["tags"], q["assignee"], q["due_date"]) == (["work"], "bob", "2026-07-27")

    def test_quick_add_leaves_what_is_no_user_in_the_title(self):
        for title in ("Mail bob@example.org", "Shop @home", "Call Sam @zed", "Call @bob today's list", "@bob"):
            assert self.quick(title) == (title, "", False), title
        # One name only: a second stays text.
        assert self.quick("Call Sam @alice @bob") == ("Call Sam @alice", "bob", True)

    def test_quick_add_without_users_knows_no_at(self):
        assert self.quick("Call Sam @bob", "undefined") == ("Call Sam @bob", "", False)
        assert self.quick("Call Sam @me", "{me: 'me', users: new Set(['alice'])}") == ("Call Sam @me", "", False)
