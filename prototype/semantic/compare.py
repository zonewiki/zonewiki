"""Embed a wiki with Leaf's semantic search (src/embed.py's OnnxEmbedder) and
with the Go prototype, and compare token ids and vectors.

    python prototype/semantic/compare.py WIKI GO_BINARY MODEL_DIR LIBONNXRUNTIME

Run it with a Python that has the semantic packages (requirements-embed.txt).
The texts are what the semantic index embeds: every page's sections as
"title\\nsection" and every task's search text, in batches of 64 as the
index's worker sends them; then queries one by one, as embed_query does: the
wiki's _eval/retrieval.json questions and tokenizer edge cases. Prints how
many token sequences and vectors agree, the worst cosine and both speeds.
"""

import base64
import json
import os
import subprocess
import sys
import time
from array import array
from pathlib import Path

EDGE_CASES = [
    "", " ", "  leading and trailing  ", "tab\there", "new\nline\r\nCRLF", "a  b   c    d",
    "it's they're we've I'm you'll he'd 'quoted' don't", "Rock'n'Roll", "<s> </s> <pad> <unk> <mask>",
    "text<mask>more", "text <mask> more", "x <mask>", "<s<s>>", "Müller Straße ß ẞ ﬁ", "naïve café",
    "日本語のテキスト", "Привет мир", "😀👍🏽 emoji 🇦🇹", "é combining", "١٢٣ ٤٥٦", "3.14 1,000 2026-10-26",
    "https://example.com/path?q=1&r=2#frag", "```python\nprint('x')\n```", "| a | b |\n|---|---|",
    "  \x85", "zero​width", "a" * 3000, " ".join(["word"] * 700), "über " * 600,
]


def main() -> None:
    wiki, binary, model, lib = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    os.environ["LEAF_DATA"] = str(wiki)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import deckstore
    deckstore.LEAF_DATA, deckstore.DECK_FILE = wiki, wiki / deckstore.DECK_DIR_NAME / "tasks.json"
    import embed
    import retrieval
    from pages import iter_pages

    docs = []
    for path, slug in iter_pages():
        title, body = retrieval._semantic_text(path.read_text(encoding="utf-8", errors="replace"), slug)
        chunks = embed.chunk_page(body) or ([""] if title else [])
        docs += [f"{title}\n{c}".strip() for c in chunks]
    docs += [d["excerpt_text"] for d in deckstore.task_search_docs() if d["excerpt_text"].strip()]
    eval_file = wiki / "_eval" / "retrieval.json"
    queries = [e["q"] for e in json.loads(eval_file.read_text(encoding="utf-8"))] if eval_file.exists() else []
    queries += EDGE_CASES

    embedder = embed.OnnxEmbedder(model)
    tok, _session = embedder._load()

    def python(texts: list[str], batch: int) -> tuple[list, list, float]:
        ids = [tok.encode(t).ids for t in texts]
        start = time.perf_counter()
        vecs = []
        for i in range(0, len(texts), batch):
            vecs += [embed._normalize(v) for v in embedder.embed_docs(texts[i:i + batch])]
        return ids, vecs, time.perf_counter() - start

    def go(texts: list[str], batch: int) -> tuple[list, list, float]:
        run = subprocess.run([binary], input=json.dumps({"model": str(model), "lib": lib, "texts": texts,
                                                         "batch": batch}),
                             stdout=subprocess.PIPE, text=True, check=True)
        out = json.loads(run.stdout)
        vecs = []
        for b in out["vectors"]:
            v = array("f")
            v.frombytes(base64.b64decode(b))
            vecs.append(v)
        return out["ids"], vecs, out["seconds"]

    for name, texts, batch in (("documents", docs, 64), ("queries", queries, 1)):
        pi, pv, pt = python(texts, batch)
        gi, gv, gt = go(texts, batch)
        same_ids = sum(a == b for a, b in zip(pi, gi))
        diff_ids = [(t[:80], a[:12], b[:12]) for t, a, b in zip(texts, pi, gi) if a != b]
        same_vecs = sum(a == b for a, b in zip(pv, gv))
        cosines = [embed._dot(a, b) for a, b in zip(pv, gv)]
        worst = min(cosines) if cosines else 1.0
        max_abs = max((abs(x - y) for a, b in zip(pv, gv) for x, y in zip(a, b)), default=0.0)
        print(f"{name}: {len(texts)} texts (batches of {batch})")
        print(f"  token ids the same: {same_ids}/{len(texts)}")
        for t, a, b in diff_ids[:10]:
            print(f"    {t!r}\n      python {a}\n      go     {b}")
        print(f"  vectors bit for bit the same: {same_vecs}/{len(texts)}; worst cosine {worst:.9f}; "
              f"largest difference {max_abs:.2e}")
        print(f"  Python {len(texts) / pt:.1f} texts/s, Go {len(texts) / gt:.1f} texts/s")


if __name__ == "__main__":
    main()
