"""Search a wiki with Leaf (src/search.py, the keyword path of
src/retrieval.py) and with the Go prototype, and compare.

    python prototype/search/compare.py WIKI GO_BINARY OUT_DIR [N_QUERIES]

Python exports both indexes' documents (pages and tasks for the search
modal, sections and tasks for retrieve), each with the texts its tokens
come from; Go tokenizes and indexes them itself. Then, per query (the
wiki's _eval/retrieval.json questions, and N_QUERIES generated from the
wiki's own words: words, prefixes, typos, phrases, the query syntax):

- the parsed query (words, phrases, exclusions, tags, dates);
- the raw BM25 ranking of both indexes: the same documents in the same
  order, scores compared (exactly equal, or how far apart);
- do_search's results: page or task, excerpt, highlighted words, the
  section a result opens;
- retrieve's sections, and the /search page's 50.

Semantic search stays off (LEAF_SEMANTIC_SEARCH unset). OUT_DIR gets
mismatches.json; it holds your pages' text, keep it out of the repo.
"""

import json
import os
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


def main() -> None:
    wiki, binary, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    n_queries = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
    os.environ["LEAF_DATA"] = str(wiki)
    os.environ.pop("LEAF_SEMANTIC_SEARCH", None)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import retrieval
    import search

    # Where the server's init_deck puts the deck (without its archiving, a write).
    import deckstore
    deckstore.LEAF_DATA, deckstore.DECK_FILE = wiki, wiki / deckstore.DECK_DIR_NAME / "tasks.json"

    page_ix, chunk_ix = retrieval._search_index(), retrieval._chunk_index()
    assert retrieval.SEMANTIC is None

    def export(d: dict) -> dict:
        if "heading" in d:  # a page's section
            parts = [[f"{d['heading']}\n{d['text']}", 1], [d["title"], search.TITLE_WEIGHT], [d["slug"], 1]]
        elif d.get("type") == "task":
            parts = [[d["excerpt_text"], 1], [d["title"].removeprefix("[Task] "), search.TITLE_WEIGHT]]
        else:
            parts = [[d["excerpt_text"], 1], [d["title"], search.TITLE_WEIGHT], [d["slug"], 1]]
        # The recipe must be what retrieval.py / deckstore.py did.
        assert [t for text, n in parts for t in search.tokenize(text) * n] == d["tokens"], d.get("slug")
        return {"parts": parts, "text": retrieval._doc_text(d), "tags": d.get("tags") or [],
                "date": retrieval._doc_date(d) or "", "slug": d.get("slug", ""), "type": d.get("type") or "",
                "heading": d.get("heading", ""), "body": d.get("text", "") if "heading" in d else "",
                "excerpt_text": d.get("excerpt_text", "")}

    queries = make_queries(wiki, page_ix, n_queries)
    payload = {"pages": [export(d) for d in page_ix.docs], "chunks": [export(d) for d in chunk_ix.docs],
               "queries": queries}
    run = subprocess.run([binary], input=json.dumps(payload), stdout=subprocess.PIPE, text=True, check=True)
    go = json.loads(run.stdout)

    def idx(docs):
        at = {id(d): i for i, d in enumerate(docs)}
        return lambda d: at[id(d)]
    page_at, chunk_at = idx(page_ix.docs), idx(chunk_ix.docs)

    problems: dict[str, list] = {}

    def problem(kind: str, **what) -> None:
        problems.setdefault(kind, []).append(what)

    for which, ix, tokens in (("pages", page_ix, go["page_tokens"]), ("chunks", chunk_ix, go["chunk_tokens"])):
        for d, t in zip(ix.docs, tokens):
            if d["tokens"] != (t or []):
                problem("tokens", index=which, slug=d.get("slug"),
                        python=sorted(set(d["tokens"]) - set(t or [])), go=sorted(set(t or []) - set(d["tokens"])))

    exact = close = 0
    worst = 0.0
    for raw, g in zip(queries, go["queries"]):
        q = search.Query(raw)
        parsed = {"text": q.text, "phrases": q.phrases, "exclude_phrases": q.exclude_phrases,
                  "exclude_stems": sorted(q.exclude_stems), "tags": sorted(q.tags),
                  "exclude_tags": sorted(q.exclude_tags), "dates": [{"Kind": k, "Value": v} for k, v in q.dates]}
        gp = {k: v or [] for k, v in g["parsed"].items()}
        gp["text"] = g["parsed"]["text"]
        if parsed != gp:
            problem("parse", query=raw, python=parsed, go=gp)
            continue
        rt = q.ranking_text
        for which, ix, at, hits in (("pages", page_ix, page_at, g["page_hits"]),
                                    ("chunks", chunk_ix, chunk_at, g["chunk_hits"])):
            py = [(at(d), s) for s, d in ix.search(rt, limit=len(ix.docs))]
            gh = [(h["doc"], h["score"]) for h in hits or []]
            if [i for i, _ in py] != [i for i, _ in gh]:
                problem("ranking", query=raw, index=which, python=py[:10], go=gh[:10])
                continue
            for (_, a), (_, b) in zip(py, gh):
                if a == b:
                    exact += 1
                else:
                    close += 1
                    worst = max(worst, abs(a - b) / abs(a))

        py_results = [(r["slug"], r.get("type"), r["excerpt"], *href_parts(r["href"]))
                      for r in retrieval.do_search(raw)]
        go_results = []
        for r in g["search"] or []:
            d = payload["pages"][r["doc"]]
            anchor = chunk_ix.docs[r["section"]]["anchor"] if r["section"] >= 0 else ""
            go_results.append((d["slug"], d["type"] or None, r["excerpt"], r["words"] or [], anchor))
        if py_results != go_results:
            problem("do_search", query=raw, python=py_results, go=go_results)
        py_ret = [chunk_at(d) for d in retrieval.retrieve(raw)]
        if py_ret != (g["retrieve"] or []):
            problem("retrieve", query=raw, python=py_ret, go=g["retrieve"])
        py_all = [page_at(d) for d in retrieval._ranked_docs(q, page_ix, None, 50)]
        if py_all != (g["search_all"] or []):
            problem("search_all", query=raw, python=py_all, go=g["search_all"])

    out.mkdir(parents=True, exist_ok=True)
    (out / "mismatches.json").write_text(json.dumps(problems, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(page_ix.docs)} page/task docs, {len(chunk_ix.docs)} section/task docs, {len(queries)} queries")
    print(f"scores: {exact} exactly equal, {close} differ (worst relative {worst:.1e})")
    for kind in ("tokens", "parse", "ranking", "do_search", "retrieve", "search_all"):
        print(f"{kind}: {len(problems.get(kind, []))} mismatches")


def href_parts(href: str) -> tuple[list[str], str]:
    """A result link's highlighted words (?hl=) and #anchor."""
    parts = urlsplit(href)
    return [v for k, v in parse_qsl(parts.query) if k == "hl"], f"#{parts.fragment}" if parts.fragment else ""


def make_queries(wiki: Path, index, n: int) -> list[str]:
    """The wiki's eval questions, and n queries made from its words."""
    rng = random.Random(n)
    queries = []
    eval_file = wiki / "_eval" / "retrieval.json"
    if eval_file.exists():
        queries += [e["q"] for e in json.loads(eval_file.read_text(encoding="utf-8"))]
    texts = [d["excerpt_text"] for d in index.docs]
    words = sorted({w for t in texts for w in t.split() if w.strip(".,;:!?()[]*_`\"'#")})
    tags = sorted({t for d in index.docs for t in d.get("tags") or []})

    def word() -> str:
        return rng.choice(words).strip(".,;:!?()[]*_`\"'")

    def typo(w: str) -> str:
        if len(w) < 4:
            return w
        i = rng.randrange(len(w))
        return rng.choice([w[:i] + w[i + 1:], w[:i] + rng.choice("aeiorstn") + w[i + 1:],
                           w[:i] + w[i:i + 2][::-1] + w[i + 2:], w[:i] + rng.choice("aeiorstn") + w[i:]])

    def phrase() -> str:
        t = rng.choice(texts).split()
        if len(t) < 3:
            return word()
        i = rng.randrange(len(t) - 2)
        return " ".join(t[i:i + rng.randint(2, 3)])

    makers = [
        word, lambda: word()[:rng.randint(3, 5)], lambda: typo(word()), lambda: word().upper(),
        lambda: f"{word()} {word()}", phrase, lambda: f'"{phrase()}"', lambda: f"{word()} -{word()}",
        lambda: f'{word()} -"{phrase()}"', lambda: f"#{rng.choice(tags)}" if tags else word(),
        lambda: f"{word()} #{rng.choice(tags)}" if tags else word(), lambda: f"-#{rng.choice(tags)} {word()}" if tags else word(),
        lambda: f"{word()} edited:2026-{rng.randint(1, 12):02d}", lambda: f"after:2026-{rng.randint(1, 12):02d} {word()}",
        lambda: f"before:2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", lambda: "edited:2026",
        lambda: f"-before:2026-09 {typo(word())}", lambda: "the of and", lambda: "what is the",
        lambda: f"{word()} {typo(word())} {word()[:4]}", lambda: rng.choice(["Müller", "MÜLLER", "Straße", "strasse",
                                                                                "naïve", "café", "日本", "ﬁle", "Ärger"]),
        lambda: f'"{word()}', lambda: '""', lambda: "-", lambda: "#", lambda: f"{word()}'s {word()}-{word()}",
    ]
    queries += [rng.choice(makers)() for _ in range(n)]
    return queries


if __name__ == "__main__":
    main()
