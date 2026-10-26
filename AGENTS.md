# Leaf

A self-hosted, browser-based wiki. Markdown files are served and rendered at runtime by a Python server. Pages can be edited directly on disk or via an in-browser editor. Full-text search runs server-side.

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
| Server              | Python + Flask, served by waitress (one process, a thread pool) |
| Markdown render     | `markdown-it-py` (server renders to HTML)    |
| HTML views          | Jinja2 templates in `src/templates` (comes with Flask), auto-escaped |
| Syntax highlight    | `highlight.js` (vendored in `src/public/vendor/`, no CDN) |
| Styling             | Custom CSS (classless, semantic HTML)        |
| In-browser editor   | Plain `<textarea>` with live preview         |
| Search              | BM25 in `search.py`, optionally fused with semantic search (`embed.py`); search modal (Ctrl+P) and `/search` |
| Storage             | Plain `.md` files in `$LEAF_DATA` (deck tasks in `_deck/*.json`) |
| Container           | Compose with Podman or Docker (Ubuntu 26.04 LTS base image) |

No build step. No database. Jinja2 comes with Flask. Auth is optional (`LEAF_PASSWORD`, see Key Constraints).

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
├── tools/eval_retrieval.py # Measures search_wiki's retrieval on your wiki against your questions (./run.sh eval)
├── prototype/goldmark/     # Go prototype: Leaf's markdown rendering on goldmark, compare.py checks it against a wiki (README there)
├── Containerfile           # Ubuntu 26.04 LTS-based container image (also ships README.md and docs/spec/ for leaf_help)
├── compose.yaml            # Builds and runs the image (run.sh drives it)
├── compose.podman.yaml     # Rootless podman only: userns keep-id (run.sh adds it)
├── compose.nas.yaml        # NAS/server: runs the prebuilt ghcr.io image, no build (see docs/spec/deployment.md)
├── Containerfile.tools     # Tools image: Playwright + Chromium for browser tests, Node for deck-core tests, gitleaks, git, pngquant
├── run.sh                  # Helper script: build/up/down/logs (via compose), test/e2e/leaks (CI runs these too); bash 3.2-compatible (macOS)
├── .githooks/pre-commit    # Refuses a commit with a secret in it (gitleaks); on with git config core.hooksPath .githooks
├── .gitattributes          # LF line endings for *.sh and .githooks/* (Windows checkouts)
├── .env.example            # Template for .env: every setting, commented
├── .env                    # Your settings (gitignored; copy of .env.example)
├── .forgejo/workflows/ci.yml  # Forgejo Actions: checks (run.sh test, e2e, leaks), build + push the image
├── .github/workflows/ci.yml   # GitHub Actions: the same checks, the image for amd64 + arm64 to ghcr.io
├── .forgejo/issue_template/   # Issue templates: bug.md, feature.md
└── src/
    ├── requirements.txt    # runtime packages, exact versions (transitive too)
    ├── requirements-embed.txt  # semantic search: onnxruntime, tokenizers and their packages, exact versions
    ├── server.py           # Flask app: request checks, wiki routes (pages, editor, scratch, search, uploads, /deck, /mcp)
    ├── config.py           # Settings from the environment, the wiki's directories (read as config.X, so tests can patch them)
    ├── mdtext.py           # Markdown source text: frontmatter, fences, code masking, headings, sections, checkboxes
    ├── pages.py            # The wiki's files: slugs, listing, sharing mode, link graph, renames, deck lists/home, url()
    ├── gitstore.py         # Git: commits, moving a page (move_page), grep over pages (grep_pages), a page's log, versions, diffs, uncommitted files, deleted pages
    ├── render.py           # Markdown → HTML: wikilinks, [[deck]], [[task:…]], post-processing
    ├── retrieval.py        # Search and LLM retrieval (BM25 + optional semantic index)
    ├── views.py            # HTML building blocks: shell, breadcrumb, modules, menus, dialogs, side columns
    ├── mcp_tools.py        # The MCP tools: search (also grep), read, link checks, write pages and deck tasks (the protocol is mcp_server.py)
    ├── regexjob.py         # replace_in_pages' regex, run in its own process with a timeout (no ReDoS)
    ├── scratch.py          # Scratch notes: create, Move to wiki, Append to page (the routes and MCP share it)
    ├── history.py          # Blueprint: history, revisions, diffs, restore, deleted pages, uncommitted changes
    ├── templating.py       # The Jinja2 environment: render(), macro()
    ├── templates/          # Jinja2 templates of every view (layout.html, view.html, edit.html, …; partials/, history/)
    ├── deck.py             # Deck: the /api/deck routes (their task writes as functions the MCP tools share), the [[deck]] embed's HTML, the module counts
    ├── deckstore.py        # Deck tasks on disk: _deck/ files, lock, recurrence, history, comments, backlinks, search docs
    ├── decksyntax.py       # The deck's page syntax: [[deck …]] params, [[task:…]], tag rules
    ├── search.py           # BM25 search ranking (stemming, fuzzy, excerpts), rank fusion
    ├── embed.py            # Optional semantic search: section embeddings, Related pages
    ├── embed_setup.py      # Model choice + install/download at image build (kept small: own image layer)
    ├── mcp_server.py       # MCP protocol (JSON-RPC) for /mcp: wiki tools for LLM clients
    ├── manual.py           # Leaf's own docs (README + docs/spec) searched by section, for the MCP tool leaf_help
    └── public/
        ├── style.css       # All styling
        ├── leaf.js         # The entry ES module every page loads; imports the modules below
        ├── js/             # ES modules: chrome, git, util, completion, search, actions, pagemenu,
        │                   #   shortcuts, mdedit, editor, page, present (see Client code in `docs/spec/rendering.md`)
        ├── deck-core.js    # Pure deck logic (no DOM), an ES module — shared by deck.js, the editor and Node-run tests
        ├── deck.js         # Deck (task management) app, an ES module leaf.js imports; initDecks() starts each .tasks-app
        ├── favicon.svg     # Browser tab icon
        ├── icon.svg        # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
        ├── manifest.json   # Web app manifest (Add to home screen)
        ├── vendor/highlight/  # highlight.js 11.9.0 + github/github-dark themes (vendored, no CDN)
        └── vendor/gemoji/     # GitHub's emoji names (gemoji 4.1.0, MIT) for `:name` completion
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `LEAF_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_assets/`.

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.
---

## Running

```bash
./run.sh            # build the image and start Leaf (default: "up"), via compose
./run.sh build|up|down|logs
./run.sh test       # unit/API tests in the app image (extra args go to pytest)
./run.sh e2e        # browser tests + deck-core.js (Node) tests in the tools image
./run.sh leaks      # scan the git history for secrets (gitleaks, in the tools image)
./run.sh screenshots  # redo the README's screenshots from a sample wiki
./run.sh eval [FILE]  # measure retrieval on your wiki (questions in FILE, default _eval/retrieval.json in the wiki)
```

Leaf is at **http://localhost:5000**. Every setting is in `.env` (gitignored), all prefixed `LEAF_` except `TZ`. The wiki lives outside the repo, in `LEAF_DATA` (default `~/leaf`), which must exist: pages at its root and in folders, uploads in `_assets/`, tasks in `_deck/`, the semantic cache and session key in `.leaf/`. Details: `docs/spec/deployment.md`.

Without the container:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt
LEAF_DATA=~/leaf python src/server.py          # waitress on http://localhost:5000
LEAF_DATA=~/leaf flask --app src/server.py run --debug   # auto-reload during development
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

**Always run the full test suite before and after making changes to the Python modules in `src/`.**

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix). A feature or fix is not complete until it has test coverage.

```bash
pytest tests/ -q                      # unit and API tests: in-process Flask client, a temp wiki per test
pytest tests/test_mcp.py -q           # one module
./run.sh test                         # the same in the app image, without a local venv
./run.sh e2e                          # browser tests (Playwright) and deck-core.js under Node
```

Both `run.sh` commands run the tests in parallel with pytest-xdist (`-n`): `test` with a worker per CPU (8 s → 3 s), `e2e` with one per two CPUs, since each worker runs its own Chromium and more starve the browsers until timing-sensitive tests fail (~2.5 min → ~30 s). Pass `-n 0` to run in one process, e.g. to debug one test. Locally: `pip install pytest-xdist`, then `pytest tests/ -q -n auto`; without `-n`, `pytest` runs in one process. Each worker is its own process with its own temp wikis, server thread and browser, so tests must not share anything outside `tmp_path`.

Use the fixtures in `tests/conftest.py` (`wiki_dir`, `git_wiki_dir`, `client`, `write_page`, …). Browser tests skip without Playwright, Node tests without Node. Before writing a browser test, read the harness notes in `docs/spec/testing.md` (offline, touch emulation, drag limits); keep the `playwright==` pin in `Containerfile.tools` equal to its image tag. CI runs the same `run.sh` commands in the images the two Containerfiles build, so a dependency or a test command changes in one place.

**Secrets never go into the repo.** Turn on the pre-commit hook once per clone (`git config core.hooksPath .githooks`): it refuses a commit whose staged changes contain a secret (gitleaks); CI scans the whole history (`./run.sh leaks`).

---

## Dependencies

`src/requirements.txt`: flask, jinja2, markdown-it-py, mdit-py-plugins, waitress. Nothing else at runtime; add a dependency only when it clearly pays for itself. **Every package is pinned to an exact version, the transitive ones too** (a build installs what was tested, not the newest release; `tests/test_semantic.py` checks it): to update, raise a version, run `./run.sh test` and `./run.sh e2e`, and pin what pip then resolves.

- **One process.** `python src/server.py` serves with waitress (8 threads, at most 100 connections, idle ones closed after 60s). Leaf must stay a single process: the deck's lock (`deckstore._LOCK`), the page/search caches, the throttles and the semantic-search worker live in its memory, so several worker processes (gunicorn `-w 4` and the like) would lose task writes.
- `highlight.js` 11.9.0 is vendored in `src/public/vendor/highlight/`, committed as downloaded: no CDN, no npm. Pages load nothing from other hosts. To update it, replace the three files with a newer release's. Likewise gemoji 4.1.0's `db/emoji.json` (and its LICENSE) in `src/public/vendor/gemoji/`, fetched by the browser only when emoji completion first opens.
- Optional: onnxruntime, Hugging Face's `tokenizers` and NumPy for semantic search (`embed.OnnxEmbedder` runs the one model itself; fastembed and its 26 packages were dropped), installed by the `Containerfile` only when `LEAF_SEMANTIC_SEARCH` is on (`embed_setup.py`, which also downloads the model, pinned to a revision and SHA-256 hashes), at the exact versions `src/requirements-embed.txt` pins, with `--no-deps` (five packages; not `packaging`, which Ubuntu's pip brings and pip can't replace); `embed.py` works without NumPy. Locally: `pip install --no-deps -r src/requirements-embed.txt` (and `packaging`).

---

## Key Constraints

- Do not use a database. Files are the only storage.
- Do not add a build step or bundler.
- **Security** (details and reasons in `docs/spec/security.md`; don't weaken any of these):
  - `LEAF_PASSWORD` turns on a login gate; the session key is `LEAF_SECRET_KEY` or one Leaf keeps in `.leaf/secret_key` (0600, excluded from git).
  - `/mcp` requires `LEAF_MCP_TOKEN`: without one it is off (403), password or not. Don't make it open again.
  - `check_host` validates the `Host` header (localhost, plus `LEAF_HOSTS`) against DNS rebinding; `LEAF_ALLOW_REMOTE=1` without `LEAF_HOSTS` skips it.
  - `check_origin` refuses cross-site writes (an `Origin` of another site, or `null`).
  - Every response carries the page policy (`content_policy`): only Leaf's own script files run (`script-src 'self'`, so no inline `<script>`, `on…=` handler or `eval` in Leaf's code, or the browser tests fail), no framing by other sites, `nosniff`.
  - The session cookie is `Secure` over HTTPS (also behind a proxy's `X-Forwarded-Proto`); wrong passwords and MCP tokens are throttled per client.
- `LEAF_ALLOW_REMOTE` is the single knob for network access with `run.sh`: unset, the port is bound to loopback only.
- **Git calls time out** after `GIT_TIMEOUT` (60s, `run_git`): a hung git counts as failed (returncode 124).
- Slugs are file paths relative to `LEAF_DATA` without `.md`: `journal/my-page.md` → `journal/my-page`; URL-safe (lowercase, hyphens, `/`). Subdirectories are supported; `to_slug()` keeps `/` and slugifies each part; `iter_pages()` skips `_*` and hidden folders. Flask routes use `<path:slug>`.
- **Writes stay inside the wiki**: resolve the path and check it with `safe_path()` (`relative_to`; it also refuses folders starting with `_` or `.`, so nothing reads or writes pages in `.leaf/`, `.git/`, `_deck/` …) before reading or writing; create parent folders (`mkdir(parents=True, exist_ok=True)`); write with `encoding="utf-8"`.
- `index.md` is the home page and must always exist (Leaf creates it at startup when missing). It can't be deleted or renamed via the UI or MCP.
- A rename re-points every link to the page, on pages and in tasks, in one commit (Rename in `docs/spec/editor.md`).
