"""tools/eval_retrieval.py: ranks, targets, the report, and that it leaves
the wiki alone."""

import importlib.util
import json
from pathlib import Path

import pytest

import retrieval
from tests.conftest import create_task, write_page
from tests.test_semantic import make_index

_spec = importlib.util.spec_from_file_location(
    "eval_retrieval", Path(__file__).resolve().parent.parent / "tools" / "eval_retrieval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


@pytest.fixture()
def wiki(wiki_dir, monkeypatch):
    monkeypatch.setattr(retrieval, "SEMANTIC", None)
    write_page(wiki_dir, "golf", "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n\n"
               "## Reifen\n\nWinterreifen im Keller.\n")
    write_page(wiki_dir, "git-remote", "# Git remote\n\nSet LEAF_GIT_REMOTE, then push to copy every commit.\n")
    return wiki_dir


def test_a_page_target_matches_any_section_a_section_target_only_its_own():
    results = [{"slug": "golf", "anchor": "#reifen"}, {"slug": "golf", "anchor": "#werkstatt"}]
    assert ev.rank(results, ["golf"]) == 1
    assert ev.rank(results, ["golf#werkstatt"]) == 2
    assert ev.rank(results, ["golf#bremsen", "other"]) is None
    assert ev.rank([{"type": "task", "task_id": "t1", "slug": "_deck"}], ["task:t1"]) == 1


def test_run_ranks_each_question_by_keywords(wiki):
    questions = [{"q": "Mechaniker Termin", "expect": ["golf#werkstatt"]},
                 {"q": "Winterreifen", "expect": ["golf#werkstatt"]},
                 {"q": "push commits", "expect": ["git-remote"]}]
    assert ev.run(questions, 8) == [1, None, 1]


def test_unknown_targets_names_missing_pages_sections_and_tasks(wiki, client):
    task_id = create_task(client, title="Reifen wechseln").get_json()["id"]
    questions = [{"q": "a", "expect": ["golf", "golf#werkstatt", f"task:{task_id}"]},
                 {"q": "b", "expect": ["golfe", "golf#bremsen", "task:nope"]}]
    bad = ev.unknown_targets(questions)
    assert [b.split()[0] for b in bad] == ["golfe", "golf#bremsen", "task:nope"]


def test_report_compares_modes_and_lists_questions_not_first():
    questions = [{"q": "one"}, {"q": "two"}, {"q": "three"}]
    out = ev.report(questions, {"keyword": [1, 3, None], "hybrid": [1, 1, 2]}, 8)
    assert "hit@1          1/3       2/3" in out
    assert "MRR           0.44      0.83" in out
    assert "one" not in out.split("Not first")[1]
    assert "       3       1  two" in out
    assert "       -       2  three" in out


def test_load_questions_takes_a_single_target_and_refuses_a_question_without_one(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps([{"q": " Bremsen ", "expect": "golf", "note": "x"}]))
    assert ev.load_questions(f) == [{"q": "Bremsen", "expect": ["golf"]}]
    f.write_text(json.dumps([{"q": "Bremsen"}]))
    with pytest.raises(SystemExit, match="question 1"):
        ev.load_questions(f)


def test_semantic_index_never_writes_the_wikis_cache(wiki, monkeypatch):
    live = wiki / ".leaf" / "index" / "embeddings.json"
    monkeypatch.setattr(retrieval, "semantic_from_env", lambda: make_index(wiki))
    idx = ev.semantic_index()
    assert idx.ready and idx.cache_path != live
    assert not live.exists()
