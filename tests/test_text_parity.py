"""The browser's text functions (public/js/mdtext.js) against the server's.

Each of them does in the browser what a Go function does on the server (a
slug, a heading's id, a wikilink's parts, the checkboxes of a text, a tick,
a query's words and its is:/in: filters). go/golden_test.go holds the Go
functions to the answers in go/testdata/golden.json.gz; this holds the
JavaScript to the same file, in Node, so a change on one side fails until
the other follows.
"""

import gzip
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "go" / "testdata" / "golden.json.gz"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# What only the server's line splitting ends a line at (mdtext.SplitLines);
# a textarea's text has "\n" alone, so the browser's code splits on that.
OTHER_LINE_ENDS = set("\r\v\f\x1c\x1d\x1e\x85  ")

SCRIPT = """
import { readFileSync, writeFileSync } from 'node:fs';
import * as T from %(module)s;
import * as C from %(core)s;
const { now, today, texts } = JSON.parse(readFileSync(%(input)s, 'utf8'));
const out = texts.map(text => {
  const w = T.splitWikilink(text);
  return {
    to_slug: T.toSlug(text),
    heading_id: T.headingId(text),
    split_wikilink: [w.page, w.section, w.label],
    checkboxes: [T.taskCheckboxOffsets(text, true).length, T.taskCheckboxOffsets(text, false).length],
    toggle: [0, 1, 2, 3].map(i => T.toggleCheckboxText(text, T.taskCheckboxOffsets(text, true)[i], now)),
    toggle_notes: [0, 1].map(i => T.toggleCheckboxText(text, T.taskCheckboxOffsets(text, false)[i], now)),
    query_words: T.queryWords(text),
    query_where: (w => [w.kinds, w.exKinds, w.places, w.exPlaces, w.statuses, w.exStatuses].map(l => [...l].sort()))(T.queryWhere(text)),
    clean_list: T.cleanList(text) || null,
    parse_date_word: C.default.parseDateWord(text, today) || null,
  };
});
writeFileSync(%(output)s, JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def answers():
    """(the golden cases, the browser's answers to them), by one run of Node."""
    with gzip.open(GOLDEN, "rt", encoding="utf-8") as f:
        golden = json.load(f)
    work = Path(tempfile.mkdtemp(prefix="textparity_"))
    # As an .mjs file: an older Node only loads a file as an ES module by
    # that extension (there is no package.json to say so).
    module = work / "mdtext.mjs"
    module.write_text((REPO / "public" / "js" / "mdtext.js").read_text(encoding="utf-8"), encoding="utf-8")
    core = work / "tasks-core.mjs"                 # the task app's pure logic: a date word as quick-add reads one
    core.write_text((REPO / "public" / "tasks-core.js").read_text(encoding="utf-8"), encoding="utf-8")
    (work / "in.json").write_text(json.dumps({"now": golden["now"], "today": golden["today"],
                                              "texts": [c["text"] for c in golden["cases"]]}), encoding="utf-8")
    script = SCRIPT % {"module": json.dumps(module.as_uri()), "core": json.dumps(core.as_uri()), "input": json.dumps(str(work / "in.json")),
                       "output": json.dumps(str(work / "out.json"))}
    run = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return golden["cases"], json.loads((work / "out.json").read_text(encoding="utf-8"))


def _differences(answers, key, want=lambda case, key: case[key], got=lambda js, key: js[key], skip=lambda text: False):
    cases, js = answers
    return [(c["text"], got(j, key), want(c, key)) for c, j in zip(cases, js)
            if not skip(c["text"]) and got(j, key) != want(c, key)]


def _report(diffs):
    return f"{len(diffs)} differ, e.g. " + "; ".join(f"{t!r}: browser {g!r}, server {w!r}" for t, g, w in diffs[:5])


def _other_line_ends(text):
    return bool(OTHER_LINE_ENDS & set(text))


@pytest.mark.parametrize("key", ["to_slug", "heading_id", "split_wikilink"])
def test_names_and_links_agree(answers, key):
    diffs = _differences(answers, key)
    assert not diffs, _report(diffs)


def test_the_checkboxes_of_a_text_agree(answers):
    diffs = _differences(answers, "checkboxes", skip=_other_line_ends)
    assert not diffs, _report(diffs)


@pytest.mark.parametrize("key", ["toggle", "toggle_notes"])
def test_a_tick_writes_the_same_text(answers, key):
    diffs = _differences(answers, key, skip=_other_line_ends)
    assert not diffs, _report(diffs)


def test_a_querys_words_agree(answers):
    """The words that rank: the server puts phrases first, the browser keeps
    their place, so they are compared as sets of words."""
    diffs = _differences(answers, "query_words", want=lambda c, _: sorted(c["query_text"].split()),
                         got=lambda j, k: sorted(j[k].split()))
    assert not diffs, _report(diffs)


def test_a_querys_kinds_and_places_agree(answers):
    """is:, -is:, in: and -in: (queryWhere: the search modal's chips and
    title list read them as the server's ParseQuery does)."""
    diffs = _differences(answers, "query_where")
    assert not diffs, _report(diffs)


@pytest.mark.parametrize("key", ["clean_list", "parse_date_word"])
def test_a_lists_name_and_a_date_word_agree(answers, key):
    """A task list's name as stored (cleanList: one copy in js/mdtext.js for
    the task app and its settings), and a date as quick-add reads one
    (parseDateWord in tasks-core.js): the server's answers."""
    diffs = _differences(answers, key, want=lambda case, k: case.get(k))      # none: the key is left out
    assert not diffs, _report(diffs)
