#!/usr/bin/env python3
"""How well Leaf's retrieval (search_wiki) finds the right section for a
question, measured on a wiki and a list of questions with their answers.

    ./run.sh eval [FILE]          in the app image, with the semantic model
    LEAF_DATA=~/leaf python tools/eval_retrieval.py FILE    keywords only,
                                  unless requirements-embed.txt is installed

FILE is JSON, a list of {"q": "question", "expect": [target, …]}. A target
is a page ("people/alex"), a section ("people/alex#current-projects", the
anchor in the section's URL) or a task ("task:<id>"); "note" is free text.
A question is found at the rank of the first result matching one of its
targets, a page matching any of its sections. A question with no targets
yet is skipped (counted in the report): paste --new's stubs, fill them in
over time. The report gives hit@1,
hit@3, hit@k and MRR, by keywords alone and, with LEAF_SEMANTIC_SEARCH, hybrid,
then the questions that weren't found first in every mode.

--new lists the queries search_wiki was asked (the query log, which
LEAF_QUERY_LOG=1 turns on) that FILE doesn't have yet, newest first, as
stubs to paste into FILE: "results" is what the search found then, a hint
for "expect", not the answer.

Read-only: the semantic cache is read from the wiki; sections changed since
are embedded into a temporary copy, never written back.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"Can't read {path}: {e}")


def load_questions(path: Path) -> tuple[list[dict], int]:
    """The questions with targets, and how many have none yet."""
    data = _read_json(path)
    if not isinstance(data, list):
        sys.exit(f"{path}: expected a list of questions")
    questions, open_ = [], 0
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict) or not isinstance(item.get("q"), str) or not item["q"].strip():
            sys.exit(f"{path}: question {i} needs a \"q\"")
        expect = item.get("expect") or []
        if isinstance(expect, str):
            expect = [expect]
        if not isinstance(expect, list) or not all(isinstance(t, str) for t in expect):
            sys.exit(f"{path}: question {i}: \"expect\" is a list of targets")
        if expect:
            questions.append({"q": item["q"].strip(), "expect": expect})
        else:
            open_ += 1
    return questions, open_


def _norm(q: str) -> str:
    return " ".join(q.lower().split())


def new_queries(path: Path, log_paths: list[Path]) -> list[dict]:
    """Logged queries the questions file doesn't have, newest first, each
    once, as stubs: {"q", "expect": [], "results"}."""
    data = _read_json(path) if path.exists() else []
    known = {_norm(item["q"]) for item in data if isinstance(item, dict) and isinstance(item.get("q"), str)}
    entries = []
    for log in log_paths:
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and isinstance(entry.get("q"), str):
                entries.append(entry)
    entries.sort(key=lambda e: e.get("at", ""), reverse=True)
    stubs = []
    for e in entries:
        if _norm(e["q"]) not in known:
            known.add(_norm(e["q"]))
            stubs.append({"q": e["q"], "expect": [], "results": e.get("results", [])[:5]})
    return stubs


def matches(result: dict, target: str) -> bool:
    if target.startswith("task:"):
        return result.get("type") == "task" and result.get("task_id") == target[5:]
    slug, _, anchor = target.partition("#")
    return (result.get("type") != "task" and result.get("slug") == slug
            and (not anchor or result.get("anchor") == f"#{anchor}"))


def rank(results: list[dict], expect: list[str]) -> int | None:
    """The 1-based rank of the first result matching a target, None if none does."""
    return next((i for i, r in enumerate(results, 1) if any(matches(r, t) for t in expect)), None)


def unknown_targets(questions: list[dict]) -> list[str]:
    """Targets naming no page, section or task of the wiki (typos, renames):
    such a question could never be found."""
    import deckstore
    import retrieval
    sections = {(d["slug"], d["anchor"].lstrip("#")) for d in retrieval._page_chunk_docs()}
    slugs = {s for s, _a in sections}
    tasks = {d["task_id"] for d in deckstore.task_search_docs()}
    bad = []
    for q in questions:
        for t in q["expect"]:
            slug, _, anchor = t.partition("#")
            ok = (t[5:] in tasks if t.startswith("task:")
                  else (slug, anchor) in sections if anchor else slug in slugs)
            if not ok:
                bad.append(f"{t}  (in: {q['q']})")
    return bad


def run(questions: list[dict], k: int) -> list[int | None]:
    import retrieval
    return [rank(retrieval.retrieve(q["q"], limit=k), q["expect"]) for q in questions]


def summary(ranks: list[int | None], k: int) -> dict[str, str]:
    n = len(ranks)
    hits = lambda m: sum(1 for r in ranks if r and r <= m)  # noqa: E731
    mrr = sum(1 / r for r in ranks if r) / n if n else 0.0
    return {"hit@1": f"{hits(1)}/{n}", "hit@3": f"{hits(3)}/{n}",
            f"hit@{k}": f"{hits(k)}/{n}", "MRR": f"{mrr:.2f}"}


def semantic_index():
    """The wiki's semantic index, brought up to date in a temporary cache;
    None without LEAF_SEMANTIC_SEARCH or its packages (from_env says why)."""
    import retrieval
    idx = retrieval.semantic_from_env()
    if idx:
        idx.cache_path = Path(tempfile.mkdtemp(prefix="leaf-eval-")) / "embeddings.json"
        idx.sync()
    return idx


def report(questions: list[dict], by_mode: dict[str, list], k: int) -> str:
    modes = list(by_mode)
    stats = {m: summary(r, k) for m, r in by_mode.items()}
    lines = [f"{'':8}" + "".join(f"{m:>10}" for m in modes)]
    for key in stats[modes[0]]:
        lines.append(f"{key:8}" + "".join(f"{stats[m][key]:>10}" for m in modes))
    rows = [i for i in range(len(questions)) if any(by_mode[m][i] != 1 for m in modes)]
    if rows:
        lines += ["", f"Not first (rank, - = not in the top {k}):",
                  "".join(f"{m:>8}" for m in modes) + "  question"]
        for i in rows:
            lines.append("".join(f"{by_mode[m][i] or '-':>8}" for m in modes) + f"  {questions[i]['q']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("questions", type=Path, help="the questions (JSON)")
    parser.add_argument("-k", type=int, default=8, help="results per question (search_wiki's default: 8)")
    parser.add_argument("--new", action="store_true",
                        help="list logged search_wiki queries not in the file yet, as stubs to paste")
    args = parser.parse_args()
    if not os.environ.get("LEAF_DATA"):
        sys.exit("Set LEAF_DATA to the wiki's folder.")
    import retrieval
    if args.new:
        log = retrieval.query_log_path()
        stubs = new_queries(args.questions, [log.with_name("queries.1.jsonl"), log])
        if not stubs:
            print("No new queries in the log (LEAF_QUERY_LOG=1 turns it on).", file=sys.stderr)
            return
        print(",\n".join(json.dumps(s, ensure_ascii=False) for s in stubs))
        return
    questions, open_ = load_questions(args.questions)
    bad = unknown_targets(questions)
    if bad:
        print("Not in the wiki:\n  " + "\n  ".join(bad) + "\n", file=sys.stderr)
    by_mode = {}
    retrieval.SEMANTIC = None
    by_mode["keyword"] = run(questions, args.k)
    idx = semantic_index()
    if idx:
        retrieval.SEMANTIC = idx
        by_mode["hybrid"] = run(questions, args.k)
    print(report(questions, by_mode, args.k))
    if open_:
        print(f"\n{open_} question(s) without targets skipped.")


if __name__ == "__main__":
    main()
