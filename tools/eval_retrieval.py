#!/usr/bin/env python3
"""How well Leaf's retrieval (search_wiki) finds the right section for a
question, measured on a wiki and a list of questions with their answers.

    ./run.sh eval [FILE]          in the app image, with the semantic model
    LEAF_DATA=~/leaf python tools/eval_retrieval.py FILE    keywords only,
                                  unless fastembed is installed

FILE is JSON, a list of {"q": "question", "expect": [target, …]}. A target
is a page ("people/alex"), a section ("people/alex#current-projects", the
anchor in the section's URL) or a task ("task:<id>"); "note" is free text.
A question is found at the rank of the first result matching one of its
targets, a page matching any of its sections. The report gives hit@1,
hit@3, hit@k and MRR, by keywords alone and, with LEAF_EMBEDDINGS, hybrid,
then the questions that weren't found first in every mode.

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


def load_questions(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"Can't read {path}: {e}")
    if not isinstance(data, list):
        sys.exit(f"{path}: expected a list of questions")
    questions = []
    for i, item in enumerate(data, 1):
        expect = item.get("expect") if isinstance(item, dict) else None
        if isinstance(expect, str):
            expect = [expect]
        if not isinstance(item, dict) or not str(item.get("q", "")).strip() or not expect:
            sys.exit(f"{path}: question {i} needs a \"q\" and an \"expect\"")
        questions.append({"q": item["q"].strip(), "expect": expect})
    return questions


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
    None without LEAF_EMBEDDINGS or fastembed (from_env says why)."""
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
    args = parser.parse_args()
    if not os.environ.get("LEAF_DATA"):
        sys.exit("Set LEAF_DATA to the wiki's folder.")
    questions = load_questions(args.questions)
    import retrieval
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


if __name__ == "__main__":
    main()
