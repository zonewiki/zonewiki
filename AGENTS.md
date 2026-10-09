# Leaf

A local-first, browser-based wiki. Markdown files are served and rendered at runtime by a Python server. Pages can be edited directly on disk or via an in-browser editor. Full-text search runs server-side.

---

## Meta

- **Keep this file and the spec up to date** — whenever the project structure, commands, constraints, or conventions change, update `AGENTS.md`; whenever a feature's behaviour changes, update its file in `docs/spec/` (see Spec). Do it in the same change.

## Spec

How each feature behaves, in detail, lives in `docs/spec/`. Read the file for the area you are working on before changing it:

| File | Covers |
|------|--------|
| `docs/spec/routes.md` | Every route, what each page view includes, side columns on big screens |
| `docs/spec/rendering.md` | Markdown → HTML: autolinks, callouts, sortable tables, templates, client modules, page header, wikilinks, `[[task:…]]` references, image sizing, frontmatter, version history, TOC |
| `docs/spec/editor.md` | The in-browser editor: auto-save, formatting bar, section editing, smart helpers, rename, file paste |
| `docs/spec/navigation.md` | Search modal (page list), modules and the module switcher, sharing mode, scratch notes, keyboard shortcuts, presentation mode |
| `docs/spec/search.md` | Full-text search: BM25, semantic search, query syntax, highlights |
| `docs/spec/mcp.md` | The MCP server and its tools |
| `docs/spec/deck.md` | The deck (tasks): embed syntax, storage, dates, fields, views, interaction, cards, history, comments, keep tasks, lists, touch |

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in the data directory (`$WIKI_DATA`) are the wiki content. Deliberate exception: Deck tasks live in `_deck/tasks.json`/`_deck/completed.json` (structured fields, concurrency-safe writes) — still plain files, still git-versioned, but not markdown.
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
| Storage             | Plain `.md` files in `$WIKI_DATA` (deck tasks in `_deck/*.json`) |
| Container           | Compose with Podman or Docker (Ubuntu 26.04 LTS base image) |

No build step. No database. Jinja2 comes with Flask. Auth is optional (`LEAF_PASSWORD`, see Key Constraints).

---

## Project Structure

```
/
├── AGENTS.md               # This file (how to work on Leaf; the index of the spec)
├── README.md               # For people who find the repo: what Leaf is, features, quick start
├── LICENSE                 # AGPL-3.0-or-later
├── docs/spec/              # The feature spec, one file per area (see Spec)
├── Containerfile           # Ubuntu 26.04 LTS-based container image
├── compose.yaml            # Builds and runs the image (run.sh drives it)
├── compose.podman.yaml     # Rootless podman only: userns keep-id (run.sh adds it)
├── Containerfile.e2e       # Test-only image: Playwright + Chromium for browser tests, Node for deck-core tests
├── run.sh                  # Helper script: build/up/down/logs (via compose), test/e2e
├── .env                    # Host data path configuration
├── .forgejo/workflows/ci.yml  # Forgejo Actions: tests, e2e, build + push the image
└── src/
    ├── requirements.txt
    ├── server.py           # Flask app: request checks, wiki routes (pages, editor, scratch, search, uploads, /deck, /mcp)
    ├── config.py           # Settings from the environment, the wiki's directories (read as config.X, so tests can patch them)
    ├── mdtext.py           # Markdown source text: frontmatter, fences, code masking, headings, sections, checkboxes
    ├── pages.py            # The wiki's files: slugs, listing, sharing mode, link graph, renames, deck lists/home, url()
    ├── gitstore.py         # Git: commits, moving a page (move_page), a page's log, versions, diffs, uncommitted files, deleted pages
    ├── render.py           # Markdown → HTML: wikilinks, [[toc]], [[deck]], [[task:…]], post-processing
    ├── retrieval.py        # Search and LLM retrieval (BM25 + optional semantic index)
    ├── views.py            # HTML building blocks: shell, breadcrumb, modules, menus, dialogs, side columns
    ├── mcp_tools.py        # The MCP tools: read and write pages and deck tasks (the protocol is mcp_server.py)
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
    └── public/
        ├── style.css       # All styling
        ├── leaf.js         # The entry ES module every page loads; imports the modules below
        ├── js/             # ES modules: chrome, git, util, completion, search, actions, pagemenu,
        │                   #   shortcuts, editor, page, present, raw (see Client code in `docs/spec/rendering.md`)
        ├── deck-core.js    # Pure deck logic (no DOM), an ES module — shared by deck.js, the editor and Node-run tests
        ├── deck.js         # Deck (task management) app, an ES module leaf.js imports; initDecks() starts each .tasks-app
        ├── favicon.svg     # Browser tab icon
        ├── icon.svg        # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
        ├── manifest.json   # Web app manifest (Add to home screen)
        └── vendor/highlight/  # highlight.js 11.9.0 + github/github-dark themes (vendored, no CDN)
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `WIKI_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_assets/`.

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.

---

## Container Setup (compose)

### Files

- **`Containerfile`** — Ubuntu 26.04 LTS base, installs Python + pip + git + `tzdata` via `apt`, installs `requirements.txt`, copies the `src/` directory into `/app`, sets `HOME=/tmp` (Leaf may run as a UID without an account). Build args `LEAF_EMBEDDINGS` and `LEAF_BUILD` (last layer).
- **`compose.yaml`** — builds the image (`build:` with those args) and runs the `leaf` container: `env_file: .env` passes every setting, `environment` adds `WIKI_DATA=/wiki-data` (overriding `.env`'s), `LEAF_HOST_DATA` (the host path, for the MCP tools' `file:` lines) and `TZ`; the port is `${LEAF_BIND:-127.0.0.1}:5000:5000`; the wiki is a bind mount with `create_host_path: false` (a mistyped path errors instead of starting an empty wiki) and `selinux: Z`; `init: true` (a minimal init as PID 1 reaps orphaned processes; with Leaf as PID 1 they piled up as zombies, see Version history in `docs/spec/rendering.md`); `restart: unless-stopped`; `user: ${LEAF_UID:-1000}:${LEAF_GID:-1000}` (not root: Leaf writes the wiki as the host user); a healthcheck fetching `/public/manifest.json` (reported by `compose ps`; restarting an unhealthy container is podman's `--health-on-failure` or an autoheal container's job); log driver `json-file` (Docker's default, podman's alias for k8s-file, so `logs -f` works on both). It works on its own (`docker compose up -d --build`), with `unknown` as the build and UTC unless `.env` sets `TZ`. With docker compose the settings reach the container through the API; podman-compose turns `env_file` into `podman create -e KEY=value` arguments, so there the secrets are on a command line for that moment.
- **`run.sh`** — the entry point, driving `<engine> compose` (podman if installed, else docker; a compose provider must be installed) for build/up/down/logs. It works out and exports what compose interpolates: `WIKI_DATA` from `.env` with `~` expanded (default `~/leaf`), `LEAF_BIND` from `ALLOW_REMOTE` (`0.0.0.0`, else `127.0.0.1`), `LEAF_BUILD` from git (see Version), and `TZ` (`.env`'s, else the host's from `/etc/timezone` or the `/etc/localtime` link, else UTC), so "today" on the server (due count, deck dates, scratch-note names, "Last changed") is the user's day. `LEAF_UID`/`LEAF_GID`: `id -u`/`id -g`; with rootless podman it adds `compose.podman.yaml` (`userns_mode: keep-id`: the host user keeps its UID inside, so Leaf runs as you and the wiki's files stay yours); with rootless docker, which has no keep-id, 0 (the container's root is the host user there; another UID would be a subordinate one). `up` refuses a missing wiki folder or `.env`, removes a `leaf` container an older run.sh started without compose (it would block the name), then `compose up -d --build`. `test`/`e2e` use the engine directly (no `.env` needed).
- **`.env`** — every setting (`WIKI_DATA`, default `~/leaf`, plus the optional `LEAF_PASSWORD`, `SECRET_KEY`, `GIT_REMOTE`, `ALLOW_REMOTE`, `LEAF_HOSTS`, `TZ`, `LEAF_EMBEDDINGS`, `LEAF_EMBED_MIN_SCORE`, `LEAF_MCP_TOKEN`, `LEAF_BASE_URL`, `LEAF_NOSHARE`); gitignored. In rootless podman (the normal case) in-container root maps to the host user, so files written to the mounted wiki directory are owned by the host user automatically.

### Commands

```bash
./run.sh            # build image and start Leaf (default: "up")
./run.sh build      # build the container image only (compose build)
./run.sh up         # build and start in detached mode (compose up -d --build)
./run.sh down       # stop and remove the container (compose down)
./run.sh logs       # follow container logs
./run.sh test       # run unit/API tests inside the app image (extra args go to pytest; a tests/… path replaces the default whole suite)
./run.sh e2e        # run browser tests + deck-core.js (Node) tests in the test image (extra args go to pytest)
```

Leaf is available at **http://localhost:5000** after `./run.sh up`.

### CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on every push and pull request: **test** (unit/API tests plus the Node deck-core tests in an `ubuntu:26.04` job container, the app image's base) and **e2e** (browser tests in the Playwright image, mirroring `Containerfile.e2e`; keep the two in sync, including the version pin). On `main` and `v*` tags, once both pass, **image** builds the `Containerfile` with `LEAF_EMBEDDINGS=fastembed` and the `LEAF_BUILD` string `run.sh` would compute, and pushes it to the Forgejo registry as `<REGISTRY>/<owner>/leaf:<short-sha>` (repo variable `REGISTRY`, Forgejo's public host, e.g. `git.example.org`: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`; without the variable the image job is skipped, so forks just test), plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`). Builds run in the official `docker:cli` image (plus `nodejs`, `git`, `bash` from apk) and use plain `docker build -f Containerfile`/`docker push` against the host engine through the socket mounted into job containers (runner label `docker`, runner config `container.docker_host: automount`; the host's engine trusts the internal certificate via `/etc/docker/certs.d/<REGISTRY>/ca.crt`, which the runner also mounts read-only into job containers via `container.options`; checkout goes over the runner's internal `http://web:3000`, so the test job needs no `ca-certificates` package), so layers are cached between runs. Repo settings: variable `REGISTRY` (the registry host; required for the image), secret `REGISTRY_TOKEN` (token with package write scope), optional variable `REGISTRY_USER` (default: the repo owner).

### Mobile / phone access

Set `ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces — `run.sh` then maps `0.0.0.0:5000` and the server's host-header check is skipped. Phones on the same network reach Leaf at `http://<host-LAN-IP>:5000`. The UI is responsive down to phone widths and the editor works on mobile. The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one. Without it, full-screen views (the deck edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard. iOS Safari ignores the setting. There is no auth by default — for LAN use consider setting `LEAF_PASSWORD` in `.env` to get a login gate.

### Version

Leaf's version is a semver constant, `VERSION` in `config.py`, bumped by hand. Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `src/` had uncommitted changes (a commit can't contain its own hash, so this part can't be a committed constant). The shortcuts modal (`?`) shows both (`Leaf 1.0.0 · c28ec9e 2026-09-26 19:22`), every page's bottom-left corner the version and short hash (`Leaf 1.0.0 · c28ec9e`, `+dirty` when so, the full build on hover; `.leaf-build` in `layout.html`, faint, only from 1140px wide where the margin beside the text column has room, not in the editor), the `<meta name="generator">` tag the version, and `/api/version` returns `{ version, build }`. The image has no `.git`, so `run.sh` computes the build string and passes it as the `LEAF_BUILD` build arg (the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer). Without the container, `config.py` reads the same string from git itself (`_source_build()`); `$LEAF_BUILD` wins when set, and with neither the modal shows the version alone.

### Home-screen app

`src/public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `templates/layout.html`) give "Add to home screen" a proper Leaf name and icon. Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie. The PNGs are rendered once from `src/public/icon.svg` (full-bleed green square, leaf inside the central 80% "maskable" safe zone) and committed, so there is still no build step. To change the icon, edit the SVG and re-render the PNGs, e.g. with a screenshot in the Playwright image (`leaf-e2e`). Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab. iOS Safari is more lenient.

### Data directory

Set `WIKI_DATA` in `.env` to any host path (default: `~/leaf`). `run.sh` expands a leading `~` to your home directory before starting the container. The folder must exist: neither `run.sh up` nor Leaf creates it (`config.py` exits with "no wiki at …"), so a mistyped path can't start an empty wiki somewhere else. Started without the container, Leaf uses `$WIKI_DATA`, else `src/data`, which must exist too. In an existing wiki folder Leaf creates what it needs (`_assets/`, `_deck/`, `index.md`):

```
WIKI_DATA=~/leaf        # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`. Layout:
- `$WIKI_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$WIKI_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically)
- `$WIKI_DATA/_deck/` — the deck's tasks (`tasks.json`, `completed.json`, `completed-YYYY.json`; see Storage in `docs/spec/deck.md`)
- `$WIKI_DATA/_index/` — the semantic search cache (rebuildable, not in git)

Uploaded image URLs use `/_assets/<filename>` (served by a Flask route from `UPLOADS_DIR`), not the static file path.

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`), 16px on every screen (phones too), line height 1.6. Headings in pages: `##` 1.4rem, `###` 1.15rem, so sections stand out by size, not only by weight and rule.
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- No nav bar (keyboard shortcuts): the module switcher in the breadcrumb, New scratch note / New task / Search icons at the top right on desktop, a bottom tab bar on phones (see Modules in `docs/spec/navigation.md`).
- Big screens (≥1320px): page views get side columns, TOC left and links right (see Side columns in `docs/spec/routes.md`); the text keeps its width.
- No external CSS frameworks — keep `style.css` self-contained.
- Mobile breakpoint at `640px`: page content readable, tables/pre scroll horizontally, larger touch targets. The editor is fully usable on phones — see "Mobile editor" in the In-Browser Editor section.

---

## Local Development (without Podman)

```bash
python -m venv .venv && source .venv/bin/activate   # Create and activate virtualenv
pip install -r src/requirements.txt                  # Install dependencies
WIKI_DATA=~/leaf python src/server.py               # Start server on http://localhost:5000 (the folder must exist)
flask --app src/server.py run --debug                # Auto-reload during development
```

---

## Tests

Tests live in `tests/` and use pytest.

```bash
pip install pytest                                   # Install pytest (once, into the venv)
pytest tests/ -v                                     # Run all tests
pytest tests/test_section_editing.py -v              # Run section-editing tests only
```

`tests/conftest.py` holds the shared setup: it imports Leaf against a throwaway `WIKI_DATA`, and gives every test a fresh temp wiki (`wiki_dir`: the server and the deck pointed at it, every page/task cache emptied, just `index.md`; `git_wiki_dir`: the same as a git repo; `client`), plus helpers (`HOST`, `write_page`, `create_task`, `section_fields`) that modules import from `tests.conftest`. The browser tests' `wiki` fixture builds on `git_wiki_dir`.

`tests/test_git.py` uses `git_wiki_dir` and covers every git path (commit-on-write, history/revision/diff/restore, `_deleted`, uncommitted/commit endpoints, git-disabled fallbacks); it skips when git is not installed. The other test modules use the plain `wiki_dir`, so git calls there are silent no-ops.

`tests/test_mcp.py` covers the MCP server: the protocol handler, the HTTP transport and auth, the four reading tools (tasks created through `/api/deck`; with and without the fake semantic index from `test_semantic.py`), and the writing tools: pages (create/overwrite, exact-text edit, append under a heading, rename with links, delete, similar-name and broken-wikilink notes) and tasks (create, update fields and status, id prefixes, comment, delete), plus one commit per write marked `(MCP)`.

`tests/test_task_refs.py` covers `[[task:…]]` references: chip rendering and badges, done/won't-do/missing, recurring series, list rows, code, Linked tasks, search by task title and MCP `read_page`. `tests/e2e/test_task_refs.py` ticks chips (undo, keep tasks), the editor completion and Make task (the modal for one item, Cancel, batch mode for several lines with shared tags and per-line shorthand, headings skipped, inline selection, undo, the hidden deck leaving the editor's keys alone).

`tests/test_semantic.py` covers semantic search with a fake embedder (words mapped to a few "concept" axes), so it needs neither fastembed nor a model: chunking, the content-addressed cache, failure fallback, the worker, rank fusion, hybrid `/api/search` and the Related section. It swaps `retrieval.SEMANTIC` for an index over the temp wiki.

To run the suite without a local venv: `./run.sh test` (runs inside the app image with the repo mounted).

### Browser tests (`tests/e2e/`)

End-to-end tests drive the real app in headless Chromium via Playwright. They run in a separate test image (`Containerfile.e2e`, based on Microsoft's Playwright image) so the app image stays browser-free and the app still has no build step or npm. `./run.sh e2e` builds that image (cached) and runs them with the repo bind-mounted; extra args pass through to pytest (e.g. `./run.sh e2e -k deck`). Without Playwright installed they skip, so plain `pytest tests/` is unaffected.

- `conftest.py` serves `server.app` on a background thread (werkzeug `make_server`, random port) and points it at a fresh temp wiki with its own git repo per test; each test gets a fresh browser context. A request to any other host is aborted (offline, deterministic) and fails the test: Leaf loads nothing from a CDN. Uncaught page JS errors fail the test.
- Coverage: Scratch filter (`test_search.py`), modules (breadcrumb switcher and current module, phone tab bar, New per module, due count on the Deck links, the deck home, desktop top actions, New task/`c t`/`g t` following the deck home; `test_mobile_nav.py`), side columns (sticky TOC and links from 1320px, active section, code and tables growing only when needed, none below 1320px; `test_layout.py`), editor (formatting bar and Ctrl+B/I/K, auto-save + unsaved dot, no commit on auto-save, Done/Ctrl+S commit, pagehide beacon, live preview/F2, list continuation), search modal (Ctrl+P/Esc, fuzzy ranking with file names and its tie-break, content matches, query syntax, highlights on the opened page, arrow/Enter nav, unlinked filter), deck (Plan-board drag to a day and to Unplanned, touch long-press drag via CDP touch events (board, Open-tab reorder, swipe still scrolls, long press is not a tap), board grouping by start date, modal "Days before due", Plan board in Open's order with in-section drops ignored, view tabs, start-date Today, touch: tap-to-edit, ⋯ menu/bottom sheet, target sizes, editor scrolling/compact phone layout, date picker placement), sortable tables (header click cycles ascending/descending/original, numeric columns, empty cells last, keyboard, not in the editor preview), layout (no horizontal page scroll at 390px phone width on page/editor/deck views, mobile Preview button, tldr toggle geometry).
- Density: the test setup (`_new_page`'s init script) makes decks open with full cards, as before the per-tab defaults, since most tests exercise cards; a test of the real defaults calls `real_density(page)` first.
- Touch: `new_page(**TOUCH_PHONE)` gives a phone context with touch input, `hover: none` and `pointer: coarse` (use `.tap()`). Full-page screenshots of such a context lose the touch/hover emulation (Playwright re-renders with changed device metrics), so assert on computed styles/sizes rather than on a full-page image.
- `test_screenshots` writes full-page PNGs of each view at phone/desktop × light/dark to `tests/e2e/_screenshots/` (gitignored) for visual review. It asserts nothing about appearance.
- Playwright's HTML5 drag cannot auto-scroll mid-drag, so drag tests must keep source and target on screen (e.g. enlarge the viewport).
- Keep the `playwright==` pin in `Containerfile.e2e` equal to the image tag version.

`tests/test_deck_core.py` exercises the pure client logic in `src/public/deck-core.js` by running it under Node (`node -e`); those tests skip automatically when Node is not installed. The app image has no Node, so `./run.sh test` skips them; `./run.sh e2e` runs them, since the test image (`Containerfile.e2e`) installs Ubuntu's `nodejs`.

**Always run the full test suite before and after making changes to the Python modules in `src/`.**

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix). A feature or fix is not complete until it has test coverage.

The tests use an in-process Flask test client and a temporary directory — no running server or container needed.

---

## Dependencies

`src/requirements.txt`:
```
flask>=3.0
jinja2>=3.1
markdown-it-py>=3.0
mdit-py-plugins>=0.4
waitress>=3.0
```

`python src/server.py` serves with waitress, a production server (Flask's own is for development only): **one process**, 8 threads, at most 100 connections, idle ones closed after 60s. Leaf must stay a single process: the deck's lock (`deckstore._LOCK`), the page/search caches, the login throttle and the semantic-search worker live in its memory, so several worker processes (gunicorn `-w 4` and the like) would lose task writes. `flask --app src/server.py run --debug` still uses Flask's server for development, and the e2e tests use werkzeug's `make_server`.

`highlight.js` 11.9.0 (script plus the `github`/`github-dark` themes) is vendored in `src/public/vendor/highlight/`, committed as downloaded: no CDN, no npm, no build step. Pages load nothing from other hosts. To update it, replace the three files with a newer release's.

Optional: `fastembed` (pulls in onnxruntime and NumPy) for semantic search with a local model. It is not in `requirements.txt`; the `Containerfile` installs it only when `LEAF_EMBEDDINGS` names a fastembed model (`embed_setup.py`); NumPy comes with it (fast vector scoring; `embed.py` falls back to plain Python without it, e.g. in a local venv). Locally: `pip install fastembed`.

---

## Key Constraints

- Do not use a database. Files are the only storage.
- Optional password auth: set `LEAF_PASSWORD` in `.env` to enable a login gate. If unset, no auth is required. `SECRET_KEY` signs the session cookie; without it Leaf makes a key once and keeps it in `$WIKI_DATA/_leaf/secret_key` (mode 0600, listed in `.git/info/exclude` so it never reaches git or a `GIT_REMOTE`; `_secret_key()`), so restarts don't sign anyone out. A wiki folder Leaf can't write to gets a new key per start. Routes `/login` (GET/POST) and `/logout` (POST) handle the session. A "Sign out" button is rendered in the bottom-right corner when auth is enabled and the visitor is signed in (not on the login page); on phones it sits in the status-badge row at the top, hidden while typing. With `ALLOW_REMOTE`, the login page shows the 🌐 remote-access badge in its form's heading row (it has no breadcrumb for the badge to sit beside).
- `/mcp` requires `LEAF_MCP_TOKEN` (a bearer token): without one set it is off (403), whether or not `LEAF_PASSWORD` is set, because its tools write pages and tasks. Don't make it open again (see Auth in `docs/spec/mcp.md`).
- Optional git remote: set `GIT_REMOTE` in `.env` to a remote URL (e.g. `git@github.com:user/wiki.git`). On startup, `git_setup()` adds or updates `origin` to that URL, so `git push` works from the terminal.
- `ALLOW_REMOTE` is the single knob for network access (with `run.sh`). When unset, `run.sh` exports `LEAF_BIND=127.0.0.1`, so the container's port 5000 is only exposed on the host's loopback interface, and the host-header check rejects any non-localhost `Host` header. When `ALLOW_REMOTE=1`, `run.sh` exports `LEAF_BIND=0.0.0.0` (LAN-accessible port binding) and the host-header check is skipped. `compose.yaml` maps `${LEAF_BIND:-127.0.0.1}:5000:5000`; used without `run.sh`, `LEAF_BIND=0.0.0.0` must be set in `.env` alongside `ALLOW_REMOTE=1`.
- A `before_request` hook in `server.py` (`check_host`) validates the `Host` header against `ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}` plus the names in `LEAF_HOSTS` (comma-separated, compared without port and case, `config.host_name`), defeating DNS rebinding attacks. `ALLOW_REMOTE=1` without `LEAF_HOSTS` skips the check (LAN access by IP); with `LEAF_HOSTS` set the check stays on. Behind a reverse proxy on the same host, `LEAF_HOSTS` alone is enough (the port stays on loopback).
- **Clickjacking**: every response carries `Content-Security-Policy: frame-ancestors 'self'` (added to an existing policy, e.g. an SVG upload's) and `X-Frame-Options: SAMEORIGIN` (`deny_framing`), so no other site can frame Leaf.
- **Git calls time out** after `GIT_TIMEOUT` (60s, `run_git`): a hung git counts as failed (returncode 124) instead of holding its request.
- **Self-hosting behind HTTPS**: the session cookie is `Secure` when the request came over HTTPS, Leaf's own or a proxy's `X-Forwarded-Proto: https` (`_SessionInterface.get_cookie_secure`); over plain http (localhost, LAN) it works as before. Wrong passwords and wrong MCP tokens are throttled per client (`Throttle`, `LOGIN_THROTTLE`/`MCP_THROTTLE`, in memory): after 5 failures in a row each try waits 1 minute, doubling up to an hour, refused unchecked (429, the login page says how long; `/mcp` sends `Retry-After`); a success clears the client. The client (`_client_key`) is the remote address, or, when that is loopback/private (a proxy), the last `X-Forwarded-For` entry (the one the proxy appended). Secrets are compared as bytes (`_same_secret`), so non-ASCII passwords work.
- **Cross-site writes are refused** (`check_origin`, a `before_request` hook after the Host check): a POST/PUT/PATCH/DELETE whose `Origin` header names another site, or is `null` (sandboxed iframe), gets 403. Without it any web page could post a form to `localhost:5000` and delete or overwrite pages, or write deck tasks (the deck API parses text/plain bodies, which need no CORS preflight), and raw HTML in markdown would turn an overwrite into a stored script. Requests without `Origin` (curl, scripts) are allowed. Behind a reverse proxy the proxy must pass the original `Host` through, or same-origin writes would be refused.
- Do not add a build step or bundler.
- Slugs are derived from file paths relative to `WIKI_DIR`: `journal/my-page.md` → slug `journal/my-page`. Slugs are URL-safe (lowercase, hyphens, forward slashes only).
- Subdirectories are fully supported. `to_slug()` preserves `/` as a path separator while slugifying each component. `iter_pages()` does a recursive `rglob("*.md")` and skips `_*` subdirectories.
- Flask routes use `<path:slug>` to capture slugs containing `/`.
- `safe_path()` uses `target.relative_to(pages_root)` to validate the path is inside `WIKI_DIR` (allows subdirs).
- File writes call `path.parent.mkdir(parents=True, exist_ok=True)` before writing to auto-create subdirectories.
- `index.md` is the home page and must always exist: Leaf creates it (`# Home`, committed) at startup when the wiki has none (`ensure_home_page`), e.g. a new, empty wiki folder. Do not allow it to be deleted or renamed via the UI.
- All file writes use `open(..., 'w', encoding='utf-8')`. Validate that the resolved path is inside `WIKI_DIR` before writing (prevent path traversal — use `Path.resolve()` and `relative_to()`).
- When a page is renamed, every link to it is re-pointed (`rewrite_page_links()` via `update_references()`), so nothing that backlinks count is left broken:
  - `[[Old Name]]` / `[[Old Name|Label]]` — matched by comparing the raw or slugified page name to the old slug; only the page-name part is replaced, the `|Label` is kept.
  - Markdown links in the forms backlinks recognise: relative (`old`, `../dir/old`, `old.md`) and absolute (`/page/old`). Each keeps its style, `#anchor` and title; relative hrefs are recomputed from the linking page's directory. Images are never touched.
  - Links inside fenced code and inline code are left alone (`mask_code`).
  - Deck tasks (active and completed): links in titles/descriptions (`deckstore.rename_page_refs()`). Changed deck files are written without their own commit and included in the rename commit.
