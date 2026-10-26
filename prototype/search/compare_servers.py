"""Search a wiki through Leaf's /api/search, in Python (Flask's test client)
and in the Go port (go/, a server on a copy), and compare the answers.

    python prototype/search/compare_servers.py [--semantic] WIKI GO_BINARY [N_QUERIES]

GO_BINARY: `go build -o … .` in go/. The wiki is copied twice (mtimes
kept: results show the last edit), one copy per server, so neither sees
what the other writes (git, .leaf/). The queries are compare.py's: the
wiki's _eval/retrieval.json questions and N_QUERIES made from its words;
each runs with limit=50, every fifth also with unlinked=1.

Results that differ only in order, or in which of equally scored results
make the cut, are counted apart: equal scores keep the order of the
pages' files, which is the directory listing's in Python (os.walk) and
A–Z in Go. Prints the first differing results of up to 8 queries.

--semantic turns semantic search on in both (a Python with
requirements-embed.txt, LEAF_MODEL_DIR set, onnxruntime's library on
LD_LIBRARY_PATH for Go): Python embeds its copy, Go starts from that
cache (it must embed nothing: the vectors and keys are the same), then
both answer the queries and each page's Related list is compared too.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORT = 5191
HOST = {"Host": "localhost:5000"}  # what Leaf's Host check lets in


def main() -> None:
    args = sys.argv[1:]
    semantic = "--semantic" in args
    args = [a for a in args if a != "--semantic"]
    wiki, binary = Path(args[0]), args[1]
    n = int(args[2]) if len(args) > 2 else 3000
    tmp = Path(tempfile.mkdtemp(prefix="leaf_cmp_"))
    py_wiki, go_wiki = tmp / "py", tmp / "go"
    shutil.copytree(wiki, py_wiki, symlinks=True)
    shutil.copytree(wiki, go_wiki, symlinks=True)
    os.environ["LEAF_DATA"] = str(py_wiki)
    if semantic:
        os.environ["LEAF_SEMANTIC_SEARCH"] = "on"
    else:
        os.environ.pop("LEAF_SEMANTIC_SEARCH", None)
    sys.path[:0] = [str(REPO / "src"), str(Path(__file__).parent)]
    import retrieval
    import server
    from compare import make_queries

    if semantic:
        started = time.time()
        retrieval.SEMANTIC.sync()
        print(f"Python embedded the wiki in {time.time() - started:.0f}s")
        cache = Path(".leaf/index/embeddings.json")
        (go_wiki / cache).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(py_wiki / cache, go_wiki / cache)
    log = open(tmp / "go.log", "w+")
    go = subprocess.Popen([binary, "-addr", f"127.0.0.1:{PORT}"], env=dict(os.environ, LEAF_DATA=str(go_wiki)),
                          stdout=subprocess.DEVNULL, stderr=log)
    try:
        time.sleep(1.5)
        if semantic:
            wait_until_on()
            log.seek(0)
            print("Go:", "".join(line for line in log if line.startswith("[semantic]")).strip())
            print("Go's cache embedded nothing:", (go_wiki / cache).read_bytes() != b"" and
                  json.loads((go_wiki / cache).read_text())["vectors"].keys()
                  == json.loads((py_wiki / cache).read_text())["vectors"].keys())
        client = server.app.test_client()
        compare(client, make_queries(py_wiki, retrieval._search_index(), n))
        if semantic:
            compare_related(client, sorted(s for _p, s in retrieval.iter_pages()))
    finally:
        go.terminate()
        shutil.rmtree(tmp)


def go_get(path: str) -> tuple[str, dict]:
    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=HOST)) as r:
        return r.read().decode(), dict(r.headers)


def wait_until_on() -> None:
    """Until Go's semantic index is ready (it reads the cache, then syncs)."""
    for _ in range(600):
        if go_get("/api/search?q=x")[1].get("X-Leaf-Semantic") == "on":
            return
        time.sleep(1)
    raise SystemExit("Go's semantic search didn't come on")


def related(html: str) -> list[str]:
    start = html.find('id="related"')
    if start < 0:
        return []
    return re.findall(r'href="/page/([^"]+)"', html[start:html.find("</section>", start)])


def compare_related(client, slugs: list[str]) -> None:
    """Each page's Related list, Python's and Go's."""
    differ = order_only = 0
    for slug in slugs:
        path = "/page/" + urllib.parse.quote(slug)
        py = related(client.get(path, headers=HOST).get_data(as_text=True))
        go = related(go_get(path)[0])
        if py == go:
            continue
        if sorted(py) == sorted(go):
            order_only += 1
            continue
        differ += 1
        if differ <= 8:
            print("RELATED", slug, "python", py, "go", go)
    print(f"{len(slugs)} pages' Related: {differ} differ, {order_only} in order only")


def cut_by_ties(q: str, py: list, go: list, key) -> bool:
    """Whether results differ only in which of equally ranked ones made the
    cut: a few at the end of a full list, or for a query of only dates,
    ones of the last result's day (newest first, so they tie)."""
    import search
    apart = [json.loads(k) for k in set(map(key, py)) ^ set(map(key, go))]
    if not search.Query(q).ranking_text.strip():
        return all(r["meta"][-10:] == py[-1]["meta"][-10:] for r in apart)
    return len(py) == 50 and len(apart) <= 4


def compare(client, queries: list[str]) -> None:
    key = lambda r: json.dumps(r, sort_keys=True)  # noqa: E731
    differ = order_only = cut_ties = twins = 0
    # A result without what names it: pages (or tasks) of the same text
    # score the same, by keywords and by meaning, so which comes first is
    # the tie's order.
    shape = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("slug", "href", "task_id")},  # noqa: E731
                                 sort_keys=True)
    for i, q in enumerate(queries):
        for extra in ("&limit=50", "&limit=30&unlinked=1") if i % 5 == 0 else ("&limit=50",):
            path = "/api/search?q=" + urllib.parse.quote(q) + extra
            py = client.get(path, headers=HOST).get_json()
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=HOST)) as r:
                go = json.loads(r.read())
            if py == go:
                continue
            if sorted(map(key, py)) == sorted(map(key, go)):
                order_only += 1
            elif len(py) == len(go) and cut_by_ties(q, py, go, key):
                cut_ties += 1
            elif sorted(map(shape, py)) == sorted(map(shape, go)):
                twins += 1
            else:
                differ += 1
                if differ <= 8:
                    print("QUERY", repr(q), extra)
                    for k, (a, b) in enumerate(zip(py, go)):
                        if a != b:
                            print("  at", k, "python", json.dumps(a, ensure_ascii=False)[:400])
                            print("  " + " " * len(str(k)), "    go", json.dumps(b, ensure_ascii=False)[:400])
                            break
                    if len(py) != len(go):
                        print("  results: python", len(py), "go", len(go))
    print(f"{len(queries)} queries: {differ} differ, {order_only} in order only, {cut_ties} in ties at the cut, "
          f"{twins} in which of twins (same text) came")


if __name__ == "__main__":
    main()
