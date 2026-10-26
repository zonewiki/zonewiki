# Search prototype

Does Leaf's keyword search survive a port to Go? This is `src/search.py`
in Go, all of it, plus the keyword path of `src/retrieval.py` (semantic
search off): what the search modal, the `/search` page and the MCP tools'
`retrieve` return. A harness runs both on a wiki and compares.

| Python | Go |
|---|---|
| `fold`, `stem`, `tokenize`, `strip_link_targets`, stopwords | `search.go` |
| `Query` (`"phrase"`, `-word`, `-"phrase"`, `#tag`, `-#tag`, `edited:`/`after:`/`before:`), `accepts` | `ParseQuery`, `Accepts` |
| `SearchIndex` (BM25, prefix and typo expansion) | `Index` |
| `_match_spans`, `matched_words`, `make_excerpt`, `fuse` | `matchSpans`, `MatchedWords`, `Excerpt`, `Fuse` |
| `difflib.get_close_matches` (typos) | `difflib.go` |
| `_bm25`, `_phrase_first`, `_ranked_docs`, `_best_sections`, `do_search`, `retrieve` | `retrieval.go` |

The goal is the same results, not a new search: a port that ranked
differently would change what people find.

What needed care:

- **Text is code points in Python, bytes in Go.** Excerpt windows and match
  spans count runes.
- **Python's character classes are Unicode-wide.** Words are letters and
  numbers of any script (`[^\W_]`); `\S`, `\d`, `str.split`, `str.strip`
  and `str.casefold` too. `fold` needs Unicode case folding (ß → ss) and
  NFKD decomposition: `golang.org/x/text` (cases, norm), the one dependency.
- **`difflib`**: `get_close_matches` decides which typos count. Its
  SequenceMatcher (longest matches, autojunk) is ported as Python 3.14 has it;
  only `ratio()` matters (the quick ratios are upper bounds of it), and
  equal scores order the larger string first.
- **Order**: Python's dicts keep insertion order, and a score is a sum whose
  order matters in floating point, so the expansion keeps the order terms
  were found in.
- `html.escape`'s entities (`&#x27;`, `&quot;`), so excerpts are the same
  bytes.

## Running it

Needs Go ≥ 1.27 and the repo's `.venv`.

```bash
cd prototype/search
go test ./...
go build -o /tmp/leafsearch .
cp -a ~/leaf /tmp/wiki-copy
../../.venv/bin/python compare.py /tmp/wiki-copy /tmp/leafsearch /tmp/leafsearch-out [N_QUERIES]
```

Python exports both indexes' documents with the texts their tokens come
from (checking the recipe against `retrieval.py`'s tokens); Go tokenizes
and indexes them itself. The queries: the wiki's own
`_eval/retrieval.json` questions, and `N_QUERIES` (default 3000) made from
the wiki's words: words, prefixes, typos (a letter dropped, swapped, changed,
added), upper case, two and three words, phrases from its pages, quoted,
with `-word`, `-"…"`, `#tag`, `-#tag`, date filters, stopwords only, accents
and ligatures, unclosed quotes, bare `-` and `#`. Per query it compares:
the parse; the raw BM25 ranking of both indexes (documents, order,
scores); `do_search`'s results (page or task, excerpt, highlighted words,
the section the link opens); `retrieve`'s sections; the `/search` page's 50.
Mismatches go to `OUT_DIR/mismatches.json`, which holds your pages' text.

## Results (2026-10-26)

On a real wiki: 338 pages and 312 tasks; 650 documents for page search,
2,820 for sections.

- **11,094 queries** in two runs (each the 47 eval questions, plus 1,000
  and 10,000 generated): tokens, parses, rankings, `do_search` results,
  `retrieve` and `/search` are the same for every one.
- **Scores**: 94–95% bit for bit the same (4.0 of 4.2 million in the larger
  run); the rest differ in the last bit (at most 4.4·10⁻¹⁶ relative,
  likely Go's `math.Log` against C's `log`), never enough to change an order.
- Of 1,047 queries, 790 have results, 85 go through the typo path, 479
  through prefixes, 418 have filters, and for 61 phrase-first reorders the
  ranking: the paths are all exercised.
- **Speed**: 8.7 ms a query in Go (do_search and retrieve, plus the full
  rankings the comparison needs), 17.7 ms in Python (do_search and
  retrieve). About twice as fast, unoptimized (no cache of folded words,
  maps allocated per call); Python's own caches (`_analyze`, `_fold_stem`)
  are in its number.

## In the port

`go/search.go` is this prototype's `search.go` (and `difflib.go`); the
documents, caches and routes are `go/retrieval.go` and `go/embed.go`'s
chunking. `compare_servers.py` checks the port end to end: the same
queries through `/api/search` of the Python app and of the Go server, each
on its own copy of the wiki:

```sh
cd go && go build -o /tmp/leafgo . && cd ..
.venv/bin/python prototype/search/compare_servers.py ~/leaf /tmp/leafgo [N_QUERIES]
```

On the 344-page wiki with 3,047 queries: the same results, excerpts,
highlighted words and sections, except the order of equally scored
results (Python keeps os.walk's order of the files, Go A–Z).

## Left for the port

- Semantic search and its fusion with BM25 (the Go + onnxruntime spike).
- Two Unicode versions: Python 3.14 has 16.0, Go 1.27 and `x/text` 17.0, so
  they may disagree on characters new in 17.0 (what is a letter, how it
  folds). Rare in a wiki; the port moves everything to one version anyway.
