# Porting Leaf to Go: handoff

Where the Go port stands and how to go on; a new session starts here.
Keep it current as steps get done.

## Status

**Porting has started** (2026-10-26: the user said "continue with the go
port"). The port lives in `go/` (one `main` package, a file per Python
module, templates in `go/templates/`); Python stays the product until Go
passes the same tests (ground rules below).

Progress by step (see Order):

| Step | State |
|---|---|
| 1. Skeleton | Done: settings (`config.go`), the request checks, page policy, login, signed session, throttle (`security.go`), `/public/`, `/_assets/`, `/api/upload`, `/api/version`, git setup. |
| 2. Pages | Done: `mdtext.go`, `pages.go` (link graph, renames re-pointing links), `markdown.go` (goldmark, from the prototype) and `render.go`, `gitstore.go`, `history.go`, `scratch.go`, `routes.go`, `views.go` with every template in `go/templates/`. |
| 3. Deck | Done with step 2 (the page view needs it): `pyjson.go` (key order, ints/floats, Python's `json.dumps` byte for byte), `decksyntax.go`, `deckstore.go`, `deck.go` (every `/api/deck` route, the embed). |
| 4. Search | Done: `search.go` (the prototype's, with `difflib.go`), `retrieval.go` (page, section and task documents, per-file caches, `doSearch`, `retrieve`, the query log), `embed.go`'s chunking; `/api/search`, `/search`. `search/compare_servers.py` finds no difference from Python on a wiki but the order of equal scores. |
| 5. MCP | Done: `mcp.go` (protocol, endpoint; `mcp_tools.json` the tool definitions as clients see them), `mcptools.go` (reading, tasks, guides), `mcpwrite.go` (page writes, `replace_in_pages` in RE2), `manual.go` (`leaf_help`; `-docs` flag, which `tests/goserver.py` sets from `manual.docs_root`), gitstore's `grepPages`, `moveFolder`, `gitLogAll`, `isUncommitted`. Tests that patch Python's limits are `python_only`, with Go tests in `mcp_test.go`. |
| 6. Semantic | Next. |
| 7 | Not started (switch-over). `retrieval.go` ranks by keywords only, stubs semantic status ("off") and Related. |

Against Go: every API test passes but those needing semantic search (14:
8 in `test_semantic.py`, 6 in `test_mcp.py`); every browser test passes
(396, 2 skipped). `go test`:
Go's own tests, and `testdata/golden.json.gz` (Python's answers of 28 pure
functions on 6,259 texts, every string in the tests: `testdata/golden.py`
writes it) all the same.

Deliberate differences so far (none breaks a page):
- Rendering: goldmark's, as `goldmark/README.md` lists; markdown-it's
  typographer is goldmark's (curly quotes, dashes, …; not `(c)` → ©);
  `javascript:`, `vbscript:` and non-image `data:` links are text, as before.
- No empty `_deck/completed.json` (and commit) in a wiki without completed tasks.
- Paths with `.`/`..` segments are 400 (Go's router would redirect).
- The session cookie's format (users sign in once more).
- Search results with equal scores come in the pages' A–Z order (Python:
  the directory listing's).
- `replace_in_pages`' regex is RE2: no lookarounds or backreferences (an
  error the model sees), `\d`/`\w` ASCII; it runs in the server, since it
  can't backtrack (no `regexjob.py` process, no timeout). Replacements take
  Python's `\1`/`\g<name>` as before. The spec (`mcp.md`) says so at the
  switch-over.
- Empty regex matches right after a match are skipped (RE2), Python finds them.

Earlier, four spikes, each in its own folder with a README (method, results) and a
`compare.py` that checks Go against the Python on a real wiki:

| Spike | Folder | Result |
|---|---|---|
| Markdown rendering on goldmark | `goldmark/` (`render.go`) | Nothing broken on 338 pages, Leaf's docs, the CommonMark spec; small deliberate changes (README) |
| `render_md`'s pre-processing | `goldmark/` (`preprocess.go`) | Exactly Python's on every page and ~250,000 fuzz texts |
| Keyword search (`search.py`, retrieval's keyword path) | `search/` | Identical results on 11,094 queries; Go ~2× faster |
| Embeddings (tokenizer, onnxruntime) | `semantic/` | Vectors bit for bit Python's; same speed |

What the spikes leave open is the bulk, not risk: about 10,000 lines of
Python (`src/*.py`) and 800 of Jinja templates to port, 15–20k lines of Go,
2–4 months by the earlier estimate.

**The trade-off** (weighed; kept for the record):
- **For:** one binary, less memory, faster start, search about 2× faster.
- **Against:** months of work; cgo in the build (semantic search); a
  second test setup during the move; no speed gain where it's slow
  (embedding is onnxruntime either way). Python already has the fewest
  dependencies (11 runtime packages).

## Ground rules (from the user)

- **Rendering may change, but must not break.** Byte-identical HTML to
  markdown-it is not a goal; no workarounds just to look the same. Keep what
  Leaf's features depend on (`goldmark/README.md` has the list).
- **Everything else stays as it is:** files on disk, URLs, the JSON of the
  API, the MCP tools, settings. A user's wiki, `.env` and MCP client keep
  working without changes.
- Leaf's principles hold (AGENTS.md): one process, no database, no build
  step, minimal dependencies. The browser code (`src/public/`: JS, CSS) is
  not ported; the Go server serves it as it is.
- Python stays the product until Go passes the same tests; then it goes in
  one step, not area by area in production.

## Contracts the port must keep

These are read or written by users, other tools, or the browser code:

- **The wiki on disk**: pages (`.md`, slugs as `mdtext.to_slug`), `_assets/`,
  `index.md` always there.
- **`_deck/tasks.json`, `_deck/completed.json` and the archives**:
  `json.dumps(tasks, indent=2, ensure_ascii=False)`, written atomically
  (`deckstore._atomic_write`), under a lock (`deckstore._LOCK`). Same key
  order, same formatting, or every write is a noisy git diff.
- **Git**: the commits Leaf makes (messages, what each includes), the
  `GIT_TIMEOUT`, history and restore working on the same repo.
- **`.leaf/`**: `secret_key` (0600), the semantic cache
  `index/embeddings.json` (base64 float32, model name
  `fastembed:jinaai/jina-embeddings-v2-base-de`: keep it, so the cache stays
  valid; the vectors are the same, see `semantic/`), the query log `queries.jsonl`.
- **Checkboxes**: the Nth rendered checkbox is the Nth line
  `mdtext._TASK_ITEM_RE` finds (ticking toggles that line; `js/page.js`
  mirrors it).
- **Heading ids** (h1–h3, unique with `-1`), which `[[Page#Section]]`
  (`mdtext.section_anchor`) and the TOC link to; ✎ `?section=N` as
  `mdtext.split_sections` counts sections.
- **HTML the JS and CSS select on**: the classes in `goldmark/compare.py`'s
  `LEAF_CLASS`, the deck embed's markup and `data-` attributes, the page
  shell's ids.
- **Routes and their JSON** (`docs/spec/routes.md`), **the MCP tools**, their
  names, parameters and replies (`docs/spec/mcp.md`, `leaf_help` serves the
  README and spec).
- **Security** (`docs/spec/security.md`): every check, header and throttle,
  unchanged. Session cookies may change format (users log in once again).
- **Settings**: every `LEAF_*` in `.env.example`, same meaning.

## How to verify each area

- **The test switch**: `LEAF_TEST_GO=1` (`./run.sh go [pytest args]`) runs
  the tests against a Go server per test (`tests/goserver.py`; how it works:
  `docs/spec/testing.md`). Settings a test patches in `config` reach the
  server as environment (it restarts when they change). Tests that patch
  Python internals are marked `python_only` and get a Go test instead;
  still marked so: `test_editor.py`'s two `_slow_saves` tests (they slow
  Flask's view down: a test-only delay setting or a proxy would do).
- **The browser tests are the oracle** (503, `tests/e2e/`).
- **The unit and API tests** (`tests/*.py`, 1,477: `test_routes.py`,
  `test_mcp.py`, `test_tasks.py` and others): those through `client` run
  over HTTP against Go; tests of internals (`mdtext`, `search`, `deckstore`
  functions) become Go tests.
- **The harnesses** compare Go with Python on a real wiki: rendering,
  pre-processing (and `--fuzz N`), search, embeddings. Run them on a copy of
  the wiki (`config.py` creates `_assets/` in `LEAF_DATA`).
- An area is done when its browser and API tests pass against Go and its
  harness shows no difference that matters.

## Order

1. **Skeleton**: the Go server (net/http, one process), config from the
   environment, static files, the request checks (`check_host`,
   `check_origin`, the page policy, login and throttling), the test switch
   above. Done: the security tests pass against Go.
2. **Pages**: listing, slugs, read, save, rename (re-points every link,
   one commit), delete, uploads, git (`gitstore.py`), history. The views as
   Go `html/template` (Jinja's macros and filters have no equivalent: each
   template is rewritten). Rendering from `goldmark/`, plus what it still
   lacks: the HTML after markdown (deck embeds, task chips, frontmatter
   block, sources) and inline rendering (goldmark has no `renderInline`:
   render, then take off the one `<p>`).
3. **Deck**: the store (lock, atomic writes, recurrence, history, comments),
   the API, the embed's HTML. The JSON files are the riskiest contract.
4. **Search**: `search/` as it is, plus building the documents
   (`retrieval._page_search_doc`, `_page_chunks`, `embed.chunk_sections`,
   `deckstore.task_search_docs`) and the per-file caches.
5. **MCP**: the protocol and every tool, including `replace_in_pages`
   (regex: Go's RE2 has no lookarounds or backreferences, a change users
   see; but it can't hang, so `regexjob.py`'s subprocess goes).
6. **Semantic search**: `semantic/` plus the index (worker, cache,
   thresholds, Related pages, fusion).
7. **Switch over**: Containerfile (Go build stage; the semantic image adds
   `libonnxruntime.so` and the model), `run.sh`, both CIs (amd64 and arm64
   with cgo), AGENTS.md and `docs/spec/` (they name Python modules
   throughout), then remove the Python.

## Setup a session needs

- **Go 1.27.3** is in the tools image (`Containerfile.tools`, pinned with its
  checksums): `./run.sh go` builds and tests without a local Go. Locally, a
  Go toolchain on PATH is all `LEAF_TEST_GO=1 pytest …` needs.
- For `semantic/`: cgo (a C compiler), onnxruntime 1.31.0's shared library
  (the GitHub release, `onnxruntime-linux-x64-1.31.0.tgz`), the model
  (`embed_setup.download_model`, ~615 MB) and a venv with
  `requirements-embed.txt`. `semantic/README.md` has the commands.
- Go dependencies (`go/go.mod`, exact versions as for Python):
  `github.com/yuin/goldmark` v1.8.6, `golang.org/x/text` v0.43.0 (search's
  case folding and NFKD); semantic search will add
  `github.com/yalue/onnxruntime_go` (`semantic/`).

## Python and Go differ here

What the spikes ran into, so the port doesn't relearn it:

- **Regexes**: RE2 has no lookarounds or backreferences (`mdtext`'s inline
  code pattern, `pages._MD_LINK_REWRITE_RE`, render's `_TASK_REF_ITEM_RE`
  and more: about 66 regex sites to audit).
- **Unicode-wide classes**: Python's `\s`, `\d`, `\w`, `str.strip`,
  `str.split`, `str.splitlines` and `str.casefold` mean more than Go's
  (a non-breaking space is `\s` in Python; `splitlines` splits at U+2028).
  `goldmark/preprocess.go` and `search/search.go` have Python's versions.
- **Line ends**: Python reads files and git's output with universal
  newlines ("\r\n" and "\r" become "\n"); Go's `readText` and `runGit`
  do it too (`newlines`), anything reading a page another way must.
- **Text is code points in Python, bytes in Go**: offsets and lengths
  (excerpts, `len`, slicing) count runes.
- **Order**: dicts keep insertion order; float sums depend on it.
- **Library details**: `textwrap.dedent` (3.13+ counts any whitespace-only
  line as blank), `difflib.get_close_matches` (ported in `search/`),
  `html.escape`'s entities, `urllib.parse.quote`'s safe characters.
- **Unicode versions**: Python 3.14 has 16.0, Go 1.27 and `x/text` 17.0.

## Decisions still open

- `pyjson.go` keeps `_deck/*.json` byte for byte Python's while both run on
  one wiki. At the switch-over it could shrink to a typed task struct and
  `encoding/json` (unknown keys in a map), at the cost of one commit that
  reorders every task's keys; the int/float decoding (`UseNumber`) and
  `pyStr` stay.

- `[x](javascript:…)` renders as a link with goldmark (markdown-it: text).
  Not exploitable (the page policy blocks it), but dropping such hrefs is
  five lines.
- arm64 builds with cgo: native runners, emulation (as CI builds the image
  now), or a cross compiler.
