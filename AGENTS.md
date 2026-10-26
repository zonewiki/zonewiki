# Leaf

A self-hosted, browser-based wiki. Markdown files are served and rendered at runtime by a Go server (one binary). Pages can be edited directly on disk or via an in-browser editor. Full-text search runs server-side.

---

## Meta

- **Keep this file and the spec up to date** — whenever the project structure, commands, constraints, or conventions change, update `AGENTS.md`; whenever a feature's behaviour changes, update its file in `docs/spec/` (see Spec). Do it in the same change. The README and the spec are also Leaf's help: the MCP tool `leaf_help` serves them to assistants answering users' questions about Leaf, so a feature's spec should say how to use it, not only how it is built.

## Spec

How each feature behaves, in detail, lives in `docs/spec/`. Read the file for the area you are working on before changing it:

| File | Covers |
|------|--------|
| `docs/spec/routes.md` | Every route, what each page view includes, side columns on big screens |
| `docs/spec/rendering.md` | Markdown → HTML: autolinks, callouts, sortable tables, templates, client modules, page header, wikilinks, `[[task:…]]` references, image sizing, frontmatter, version history |
| `docs/spec/editor.md` | The in-browser editor: auto-save, formatting bar, section editing, smart helpers, rename, file paste |
| `docs/spec/navigation.md` | Search modal (page list), modules and the module switcher, sharing mode, scratch notes, keyboard shortcuts, presentation mode |
| `docs/spec/search.md` | Full-text search: BM25, semantic search, query syntax, highlights, measuring retrieval |
| `docs/spec/mcp.md` | The MCP server and its tools, `leaf_help` (Leaf's docs for assistants) |
| `docs/spec/deck.md` | The deck (tasks): embed syntax, storage, dates, fields, views, interaction, cards, history, comments, keep tasks, lists, touch |
| `docs/spec/security.md` | Login and session key, the MCP token, the Host and Origin checks, framing, HTTPS cookies, throttling |
| `docs/spec/deployment.md` | Containerfile, compose, `run.sh`, `.env`, a NAS or server (prebuilt image, Synology), CI (Forgejo, GitHub), network and phone access, the version string, the home-screen app, the data directory |
| `docs/spec/testing.md` | The test setup (fixtures, the browser tests' harness and gotchas) and what each test module covers |

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in the data directory (`$LEAF_DATA`) are the wiki content. Deliberate exception: Deck tasks live in `_deck/tasks.json`/`_deck/completed.json` (structured fields, concurrency-safe writes) — still plain files, still git-versioned, but not markdown.
- **Editable two ways** — edit files in any text editor, or click "Edit" in the browser
- **Nice looking** — clean typography, readable line lengths, good contrast
- **Searchable** — server-side full-text search across all pages

---

## Tech Stack

| Concern             | Choice                                       |
|---------------------|----------------------------------------------|
| Server              | Go (`net/http`), one process, one binary (`go/`) |
| Markdown render     | `goldmark` (server renders to HTML), with Leaf's rules (`go/markdown.go`) |
| HTML views          | Go `html/template` in `go/templates` (embedded in the binary), auto-escaped |
| Syntax highlight    | `highlight.js` (vendored in `public/vendor/`, no CDN) |
| Styling             | Custom CSS (classless, semantic HTML)        |
| In-browser editor   | Plain `<textarea>` with live preview         |
| Search              | BM25 in `go/search.go`, optionally fused with semantic search (`go/embed.go`); search modal (Ctrl+P) and `/search` |
| Storage             | Plain `.md` files in `$LEAF_DATA` (deck tasks in `_deck/*.json`) |
| Container           | Compose with Podman or Docker (Ubuntu 26.04 LTS base image) |
| Tests               | `go test` for the server's internals; pytest over HTTP against the server, Playwright in a browser |

No build step for the browser code (no bundler: the server serves `public/` as it is). No database. Auth is optional (`LEAF_PASSWORD`, see Key Constraints).

---

## Project Structure

```
/
├── AGENTS.md               # This file (how to work on Leaf; the index of the spec)
├── README.md               # For people who find the repo: what Leaf is, features, quick start
├── CONTRIBUTING.md         # Bug reports, feature requests, how a change gets in (points to this file)
├── LICENSE                 # AGPL-3.0-or-later
├── docs/spec/              # The feature spec, one file per area (see Spec)
├── docs/screenshots/       # The README's screenshots + make_screenshots.py (./run.sh screenshots)
├── Containerfile           # The image: a Go build stage, onnxruntime + the model (opt-in), an Ubuntu 26.04 LTS runtime (also ships README.md and docs/spec/ for leaf_help)
├── compose.yaml            # Builds and runs the image (run.sh drives it)
├── compose.podman.yaml     # Rootless podman only: userns keep-id (run.sh adds it)
├── compose.nas.yaml        # NAS/server: runs the prebuilt ghcr.io image, no build (see docs/spec/deployment.md)
├── Containerfile.tools     # Tools image: Go, Playwright + Chromium, the tests' Python packages, Node for deck-core tests, gitleaks, git, pngquant
├── run.sh                  # Helper script: build/up/down/logs (via compose), test/e2e/leaks (CI runs these too); bash 3.2-compatible (macOS)
├── .githooks/pre-commit    # Refuses a commit with a secret in it (gitleaks); on with git config core.hooksPath .githooks
├── .gitattributes          # LF line endings for *.sh and .githooks/* (Windows checkouts)
├── .env.example            # Template for .env: every setting, commented
├── .env                    # Your settings (gitignored; copy of .env.example)
├── .forgejo/workflows/ci.yml  # Forgejo Actions: checks (run.sh test, e2e, leaks), build + push the image
├── .github/workflows/ci.yml   # GitHub Actions: the same checks, the image for amd64 + arm64 to ghcr.io
├── .forgejo/issue_template/   # Issue templates: bug.md, feature.md
├── go/                     # The server: one package (main), a file per area
│   ├── go.mod, go.sum      # Go modules, exact versions (goldmark, x/text, onnxruntime_go)
│   ├── server.go           # main: flags, start-up, the routes' mux, the request checks around them; leaf eval, leaf health
│   ├── config.go           # Settings from the environment (read once into cfg)
│   ├── security.go         # Host and Origin checks, the page policy, login, signed session, throttles
│   ├── routes.go           # The wiki's routes: pages, the editor, scratch notes, search, previews, uploads
│   ├── views.go            # The page shell, breadcrumb, modules, menus, side columns; templates/ (embedded)
│   ├── mdtext.go           # Markdown source text: frontmatter, fences, code masking, headings, sections, checkboxes
│   ├── pages.go            # The wiki's files: slugs, listing, sharing mode, link graph, renames, deck lists/home
│   ├── markdown.go         # Markdown → HTML on goldmark, with Leaf's rules (checkboxes, callouts, heading ids …)
│   ├── render.go           # A page's render: wikilinks, [[deck]], [[task:…]], frontmatter, sources
│   ├── gitstore.go         # Git: setup, commits, moves, grep over pages, a page's log, versions, diffs, deleted pages
│   ├── history.go          # History, revisions, diffs, restore, deleted pages, uncommitted changes
│   ├── scratch.go          # Scratch notes: create, Move to wiki, Append to page (the routes and MCP share it)
│   ├── deck.go             # Deck: the /api/deck routes (their task writes as functions the MCP tools share), the [[deck]] embed
│   ├── deckstore.go        # Deck tasks on disk: _deck/ files, lock, recurrence, history, comments, backlinks, search docs
│   ├── decksyntax.go       # The deck's page syntax: [[deck …]] params, [[task:…]], tag rules, date words
│   ├── task.go             # A deck task (Task): its fields, read leniently from a file, written in a fixed key order
│   ├── pyjson.go           # JSON objects that keep their key order, read as the Python Leaf read requests and MCP messages
│   ├── search.go, difflib.go  # BM25 search ranking (stemming, fuzzy, excerpts), the query syntax, rank fusion
│   ├── retrieval.go        # Search and LLM retrieval (BM25 + optional semantic index), the query log
│   ├── embed.go            # Optional semantic search: section embeddings, the index and its cache, Related pages
│   ├── embed_onnx.go       # The model on onnxruntime (cgo; embed_nocgo.go: built without cgo, keywords only)
│   ├── tokenizer.go        # The model's tokenizer
│   ├── mcp.go              # MCP protocol (JSON-RPC) for /mcp; mcp_tools.json the tool definitions
│   ├── mcptools.go         # The MCP tools that read, and the deck's; mcpwrite.go those that write pages
│   ├── manual.go           # Leaf's own docs (README + docs/spec) searched by section, for the MCP tool leaf_help
│   ├── eval.go             # leaf eval: measures search_wiki's retrieval on your wiki against your questions (./run.sh eval)
│   ├── embedtest.go        # The tests' fake embedder (-semantic-test)
│   └── *_test.go, testdata/  # Go tests (go test ./...)
├── tests/                  # pytest over HTTP against the server (conftest.py, goserver.py, settings.py); e2e/ in a browser
└── public/                 # The browser code, served as it is
    ├── style.css           # All styling
    ├── leaf.js             # The entry ES module every page loads; imports the modules below
    ├── js/                 # ES modules: chrome, git, util, completion, search, actions, pagemenu,
    │                       #   shortcuts, mdedit, editor, page, present (see Client code in `docs/spec/rendering.md`)
    ├── deck-core.js        # Pure deck logic (no DOM), an ES module — shared by deck.js, the editor and Node-run tests
    ├── deck.js             # Deck (task management) app, an ES module leaf.js imports; initDecks() starts each .tasks-app
    ├── favicon.svg         # Browser tab icon
    ├── icon.svg            # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
    ├── manifest.json       # Web app manifest (Add to home screen)
    ├── vendor/highlight/   # highlight.js 11.9.0 + github/github-dark themes (vendored, no CDN)
    └── vendor/gemoji/      # GitHub's emoji names (gemoji 4.1.0, MIT) for `:name` completion
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `LEAF_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_assets/`.

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.

---

## Running

```bash
./run.sh            # build the image and start Leaf (default: "up"), via compose
./run.sh build|up|down|logs
./run.sh test       # go vet + go test, then the unit/API tests, in the tools image (extra args go to pytest)
./run.sh e2e        # browser tests + deck-core.js (Node) tests in the tools image
./run.sh leaks      # scan the git history for secrets (gitleaks, in the tools image)
./run.sh screenshots  # redo the README's screenshots from a sample wiki
./run.sh eval [FILE]  # measure retrieval on your wiki (questions in FILE, default _eval/retrieval.json in the wiki)
```

Leaf is at **http://localhost:5000**. Every setting is in `.env` (gitignored), all prefixed `LEAF_` except `TZ`. The wiki lives outside the repo, in `LEAF_DATA` (default `~/leaf`), which must exist: pages at its root and in folders, uploads in `_assets/`, tasks in `_deck/`, the semantic cache and session key in `.leaf/`. Details: `docs/spec/deployment.md`.

Without the container (Go 1.27 and a C compiler; `CGO_ENABLED=0` builds one without semantic search):

```bash
(cd go && go build -o leaf .)
LEAF_DATA=~/leaf go/leaf                 # http://localhost:5000 (finds public/ from the binary)
LEAF_DATA=~/leaf go/leaf eval FILE       # measure retrieval
```

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`), 16px on every screen (phones too), line height 1.6. Headings in pages: `##` 1.4rem, `###` 1.15rem, so sections stand out by size, not only by weight and rule.
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- No nav bar (keyboard shortcuts): the module switcher in the breadcrumb, New scratch note / New task / Search icons at the top right on desktop, a bottom tab bar on phones (see Modules in `docs/spec/navigation.md`).
- Big screens (≥1320px): page views get side columns, TOC left and links right (see Side columns in `docs/spec/routes.md`); the text keeps its width.
- No external CSS frameworks — keep `style.css` self-contained.
- Mobile breakpoint at `640px`: page content readable, tables/pre scroll horizontally, larger touch targets. The editor is fully usable on phones (Mobile editor in `docs/spec/editor.md`).

---

## Tests

**Always run the full test suite before and after making changes to the server (`go/`).**

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix). A feature or fix is not complete until it has test coverage.

```bash
./run.sh test                         # go vet, go test, then the unit/API tests (pytest over HTTP), in the tools image
./run.sh test tests/test_mcp.py -n 0  # one module, in one process
./run.sh e2e                          # browser tests (Playwright) and deck-core.js under Node
(cd go && go test ./...)              # the server's own tests, with a local Go
pytest tests/ --ignore=tests/e2e      # the API tests, with a local Go and pip install -r tests/requirements.txt
```

Two kinds of tests: **Go tests** (`go/*_test.go`) for what is inside the server (text functions, search ranking, the semantic index, git helpers, the MCP protocol); **pytest** (`tests/`) for everything a user or client reaches over HTTP, against the real server: each test gets a temp wiki and a server process on it (`tests/goserver.py`; the binary is built once per run). Settings a test changes go in `tests/settings.py` (`monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")`); the server restarts with them. Prefer an HTTP test when the behaviour shows through a route, a Go test when it doesn't.

Both `run.sh` commands run pytest in parallel with pytest-xdist (`-n`): `test` with a worker per CPU, `e2e` with one per two CPUs, since each worker runs its own Chromium and more starve the browsers until timing-sensitive tests fail. Pass `-n 0` to run in one process, e.g. to debug one test. Each worker is its own process with its own temp wikis, servers and browser, so tests must not share anything outside `tmp_path`.

Use the fixtures and helpers in `tests/conftest.py` (`wiki_dir`, `git_wiki_dir`, `client`, `write_page`, `preview`, `section_fields`, `backlinks` …). Browser tests skip without Playwright, Node tests without Node. Before writing a browser test, read the harness notes in `docs/spec/testing.md` (offline, touch emulation, drag limits); keep the `playwright==` pin in `tests/requirements.txt` equal to `Containerfile.tools`' image tag. CI runs the same `run.sh` commands in the image `Containerfile.tools` builds, so a dependency or a test command changes in one place.

**Secrets never go into the repo.** Turn on the pre-commit hook once per clone (`git config core.hooksPath .githooks`): it refuses a commit whose staged changes contain a secret (gitleaks); CI scans the whole history (`./run.sh leaks`).

---

## Dependencies

`go/go.mod`: `github.com/yuin/goldmark` (markdown), `golang.org/x/text` (search's case folding and NFKD), `github.com/yalue/onnxruntime_go` (semantic search). Nothing else; add a dependency only when it clearly pays for itself. **Everything is pinned to an exact version** (`go.sum` checks the modules; the Containerfiles pin Go, onnxruntime, gitleaks and the model by version and SHA-256, `tests/test_image.py` checks that they agree): to update, raise a version, run `./run.sh test` and `./run.sh e2e`, and commit the new pins.

- **One process.** The binary serves with `net/http` (at most 100 connections, idle ones closed after 60s). Leaf must stay a single process: the deck's lock (`deckMu`), the page/search caches, the throttles and the semantic-search worker live in its memory, so several processes on one wiki would lose task writes.
- `highlight.js` 11.9.0 is vendored in `public/vendor/highlight/`, committed as downloaded: no CDN, no npm. Pages load nothing from other hosts. To update it, replace the three files with a newer release's. Likewise gemoji 4.1.0's `db/emoji.json` (and its LICENSE) in `public/vendor/gemoji/`, fetched by the browser only when emoji completion first opens.
- Semantic search needs cgo at build time and onnxruntime's shared library at run time, which the binary loads with `dlopen` when it first embeds: with `LEAF_SEMANTIC_SEARCH` on at build time, the image puts `libonnxruntime.so` (1.31.0, its GitHub release) in `/usr/local/lib` and the model in `/opt/leaf-model` (the `Containerfile`'s `semantic` stage, its own layer). Without the library, Leaf searches by keywords. Locally: the release's `lib/` on `LD_LIBRARY_PATH`; the model is downloaded on first use.
- The tests' Python packages (`tests/requirements.txt`: pytest, pytest-xdist, Playwright, werkzeug), pinned too, live only in the tools image.

---

## Key Constraints

- Do not use a database. Files are the only storage.
- Do not add a build step or bundler for the browser code.
- **Security** (details and reasons in `docs/spec/security.md`; don't weaken any of these):
  - `LEAF_PASSWORD` turns on a login gate; the session key is `LEAF_SECRET_KEY` or one Leaf keeps in `.leaf/secret_key` (0600, excluded from git).
  - `/mcp` requires `LEAF_MCP_TOKEN`: without one it is off (403), password or not. Don't make it open again.
  - The Host check (`hostAllowed`) validates the `Host` header (localhost, plus `LEAF_HOSTS`) against DNS rebinding; `LEAF_ALLOW_REMOTE=1` without `LEAF_HOSTS` skips it.
  - The Origin check (`crossSite`) refuses cross-site writes (an `Origin` of another site, or `null`).
  - Every response carries the page policy (`pageCSP`): only Leaf's own script files run (`script-src 'self'`, so no inline `<script>`, `on…=` handler or `eval` in Leaf's code, or the browser tests fail), no framing by other sites, `nosniff`.
  - The session cookie is `Secure` over HTTPS (also behind a proxy's `X-Forwarded-Proto`); wrong passwords and MCP tokens are throttled per client.
- `LEAF_ALLOW_REMOTE` is the single knob for network access with `run.sh`: unset, the port is bound to loopback only.
- **Git calls time out** after `gitTimeout` (60s, `runGit`): a hung git counts as failed (exit code 124).
- Slugs are file paths relative to `LEAF_DATA` without `.md`: `journal/my-page.md` → `journal/my-page`; URL-safe (lowercase, hyphens, `/`). Subdirectories are supported; `toSlug()` keeps `/` and slugifies each part; `iterPages()` skips `_*` and hidden folders. Routes take the rest of the path as the slug (`{slug...}`).
- **Writes stay inside the wiki**: resolve the path and check it with `safePath()` (inside the wiki; it also refuses folders starting with `_` or `.`, so nothing reads or writes pages in `.leaf/`, `.git/`, `_deck/` …) before reading or writing; create parent folders (`os.MkdirAll`); write UTF-8 (`writeText`).
- `index.md` is the home page and must always exist (Leaf creates it at startup when missing). It can't be deleted or renamed via the UI or MCP.
- A rename re-points every link to the page, on pages and in tasks, in one commit (Rename in `docs/spec/editor.md`).
