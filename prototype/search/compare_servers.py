"""Search a wiki through Leaf's /api/search, in Python (Flask's test client)
and in the Go port (go/, a server on a copy), and compare the answers.

    python prototype/search/compare_servers.py WIKI GO_BINARY [N_QUERIES]

GO_BINARY: `go build -o … .` in go/. The wiki is copied twice (mtimes
kept: results show the last edit), one copy per server, so neither sees
what the other writes (git, .leaf/). The queries are compare.py's: the
wiki's _eval/retrieval.json questions and N_QUERIES made from its words;
each runs with limit=50, every fifth also with unlinked=1.

Results that differ only in order, or in which of equally scored results
make the cut, are counted apart: equal scores keep the order of the
pages' files, which is the directory listing's in Python (os.walk) and
A–Z in Go. Prints the first differing results of up to 8 queries.
"""

import json
import os
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
    wiki, binary = Path(sys.argv[1]), sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
    tmp = Path(tempfile.mkdtemp(prefix="leaf_cmp_"))
    py_wiki, go_wiki = tmp / "py", tmp / "go"
    shutil.copytree(wiki, py_wiki, symlinks=True)
    shutil.copytree(wiki, go_wiki, symlinks=True)
    os.environ["LEAF_DATA"] = str(py_wiki)
    os.environ.pop("LEAF_SEMANTIC_SEARCH", None)
    sys.path[:0] = [str(REPO / "src"), str(Path(__file__).parent)]
    import retrieval
    import server
    from compare import make_queries

    go = subprocess.Popen([binary, "-addr", f"127.0.0.1:{PORT}"], env=dict(os.environ, LEAF_DATA=str(go_wiki)),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        compare(server.app.test_client(), make_queries(py_wiki, retrieval._search_index(), n))
    finally:
        go.terminate()
        shutil.rmtree(tmp)


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
    differ = order_only = cut_ties = 0
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
    print(f"{len(queries)} queries: {differ} differ, {order_only} in order only, {cut_ties} in ties at the cut")


if __name__ == "__main__":
    main()
