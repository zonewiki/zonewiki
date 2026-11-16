# Zone

A self-hosted, browser-based wiki.
Markdown files are served and rendered at runtime by a Go server (one binary).
Pages can be edited directly on disk or via an in-browser editor.
Full-text search runs server-side.

---

## Meta

- **Keep this file and the spec up to date** — whenever the project structure, commands, constraints, or conventions change, update `AGENTS.md`; whenever a feature's behaviour changes, update its file in `docs/spec/` (see Spec).
  Do it in the same change.
  The README and the spec are also Zone's help: the MCP tool `zone_help` serves them to assistants answering users' questions about Zone, so a feature's spec should say how to use it, not only how it is built.

## Spec

How each feature behaves, in detail, lives in `docs/spec/`.
Read the file for the area you are working on before changing it:

| File | Covers |
|------|--------|
| `docs/spec/routes.md` | Every route, what each page view includes, side columns on big screens |
| `docs/spec/rendering.md` | Markdown → HTML: autolinks, callouts, math and Mermaid diagrams, sortable tables, folding sections, templates, client modules, page header, wikilinks, `[[task:…]]` references, image sizing, frontmatter, version history |
| `docs/spec/editor.md` | The in-browser editor: auto-save, formatting bar, section editing, smart helpers, rename, file paste |
| `docs/spec/navigation.md` | Search modal (page list), modules and the module switcher, sharing mode, notes, keyboard shortcuts, presentation mode |
| `docs/spec/search.md` | Full-text search: BM25, semantic search, query syntax, highlights, measuring retrieval |
| `docs/spec/mcp.md` | The MCP server and its tools, `zone_help` (Zone's docs for assistants) |
| `docs/spec/tasks.md` | Tasks: embed syntax, storage, dates, fields, views, interaction, cards, history, comments, keep tasks, lists, touch |
| `docs/spec/security.md` | Login and session key, the MCP token, the Host and Origin checks, framing, HTTPS cookies, throttling |
| `docs/spec/users.md` | Several users: setup, the settings file `.zone/config` (its format and keys, reloading, `zonewiki user`/`zonewiki config`), password hashes, invite links, sign-up, signing in, per-user MCP tokens, private pages, private tasks, commit authors |
| `docs/spec/deployment.md` | Containerfile, compose, `run.sh`, `.env`, a NAS or server (prebuilt image, Synology), CI (Forgejo, GitHub), mail (SMTP), network and phone access, the version string, the home-screen app, the data directory |
| `docs/spec/testing.md` | The test setup (fixtures, the browser tests' harness and gotchas) and what each test module covers |

The spec and this file are written a sentence a line (a long sentence a clause a line; a list item's following lines indented under its text), so a change shows as the sentence that changed, not as its paragraph.
Markdown joins the lines when it renders them.

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in the data directory (`$ZONE_DATA`) are the wiki content.
  Deliberate exception: Tasks live in `_tasks/tasks.json`/`_tasks/closed.json` (a list's in `_tasks/lists/<name>/`) and their lists in `_tasks/lists.json` (structured fields, concurrency-safe writes) — still plain files, still git-versioned, but not markdown.
- **Editable two ways** — edit files in any text editor, or click "Edit" in the browser
- **Nice looking** — clean typography, readable line lengths, good contrast
- **Searchable** — server-side full-text search across all pages

---

## Tech Stack

| Concern             | Choice                                       |
|---------------------|----------------------------------------------|
| Server              | Go (`net/http`), one process, one binary (`go/`) |
| Markdown render     | `goldmark` (server renders to HTML), with Zone's rules (`go/markdown.go`) |
| HTML views          | Go `html/template` in `go/templates` (embedded in the binary), auto-escaped; what every page's layout shows is a typed `pageShell` (`.Shell`), a view's data a struct embedding `view` (`pageView`, `editView`, `historyListView` …; `page()` takes nothing else) |
| Syntax highlight    | `highlight.js` (vendored in `public/vendor/`, no CDN) |
| Math, diagrams      | KaTeX and Mermaid, vendored in `public/vendor/`, loaded by a page that has math or a diagram |
| Fonts               | The system's sans for text; Source Serif 4 for headings, vendored in `public/vendor/source-serif/` (variable weight, Latin and Latin Extended) |
| Styling             | Custom CSS (classless, semantic HTML); the look (warm paper and ink, a deep green accent, serif headings) is shared with zonewiki.com and Zone CP's pages: its tokens are the `:root` variables at the top of `public/style.css` |
| In-browser editor   | Plain `<textarea>` with live preview         |
| Search              | BM25 in `go/internal/textsearch`, optionally fused with semantic search (`go/embed.go`); search modal (Ctrl+P) and `/search` |
| Storage             | Plain `.md` files in `$ZONE_DATA` (tasks in `_tasks/*.json`) |
| Container           | Compose with Podman or Docker (Ubuntu 26.04 LTS base image) |
| Tests               | `go test` for the server's internals; pytest over HTTP against the server, Playwright in a browser |

No build step for the browser code (no bundler: the server serves `public/` as it is).
No database.
Auth is optional (a password, or users; see Key Constraints).

---

## Project Structure

```
/
├── AGENTS.md               # This file (how to work on Zone; the index of the spec)
├── README.md               # For people who find the repo: what Zone is, features, quick start
├── CONTRIBUTING.md         # Bug reports, feature requests, how a change gets in (points to this file)
├── LICENSE                 # AGPL-3.0-or-later
├── THIRD_PARTY_LICENSES    # Licenses of Go, the Go modules and the embedding model (the vendored libraries' are next to them)
├── docs/spec/              # The feature spec, one file per area (see Spec)
├── docs/plans/             # Working plans for changes in progress, deleted when done (`mcp-oauth.md`, `tasks-5-members.md`, `tasks-6-sound-at-100.md`)
├── docs/licenses/mermaid_notices.py  # Writes public/vendor/mermaid/THIRD_PARTY_LICENSES from the bundle's source map
├── docs/screenshots/       # The README's screenshots + make_screenshots.py (./run.sh screenshots)
├── Containerfile           # The image: a Go build stage, onnxruntime + the model (opt-in), an Ubuntu 26.04 LTS runtime (also ships README.md and docs/spec/ for zone_help)
├── compose.yaml            # Builds and runs the image (run.sh drives it)
├── compose.podman.yaml     # Rootless podman only: userns keep-id (run.sh adds it)
├── compose.nas.yaml        # NAS/server: runs the prebuilt ghcr.io image, no build (see docs/spec/deployment.md)
├── Containerfile.tools     # Tools image: Go, Playwright + Chromium, the tests' Python packages, Node for tasks-core tests, gitleaks, git, pngquant
├── run.sh                  # Helper script: build/up/down/logs, user/config (via compose), test/e2e/leaks (CI runs these too); bash 3.2-compatible (macOS)
├── .githooks/pre-commit    # Refuses a commit with a secret in it (gitleaks); on with git config core.hooksPath .githooks
├── .gitleaks.toml          # gitleaks: the default rules; the generic key rule skips public/vendor/ (minified names)
├── .gitattributes          # LF line endings for *.sh and .githooks/* (Windows checkouts)
├── .env.example            # Template for .env: every setting, commented
├── .env                    # Your settings (gitignored; copy of .env.example)
├── .forgejo/workflows/ci.yml  # Forgejo Actions: checks (run.sh test, e2e, leaks), build + push the image
├── .github/workflows/ci.yml   # GitHub Actions: the same checks, the image for amd64 + arm64 to ghcr.io
├── .forgejo/issue_template/   # Issue templates: bug.md, feature.md
├── go/                     # The server: package main, a file per area; pure code without Zone's state in go/internal/
│   ├── go.mod, go.sum      # Go modules, exact versions (goldmark, x/text, onnxruntime_go)
│   ├── server.go           # main: flags, start-up, the routes' mux, the request checks around them; zonewiki eval, zonewiki health, zonewiki user, zonewiki config
│   ├── login.go            # The login gate's routes: /login, /logout
│   ├── static.go           # public/ served as it is (gzipped, kept by the browser), a file sent with its type
│   ├── uploads.go          # Uploads: _assets/ served, POST /api/upload
│   ├── config.go           # What is fixed for the process, from the environment (read once into cfg)
│   ├── configfile.go       # The settings file .zone/config: its keys, Settings (settings(), reloaded when the file changes), the line edits
│   ├── storage.go          # upload-max and storage-max: sizes (25MB), what the wiki holds (not .git/, .zone/), checkRoom
│   ├── commands.go         # zonewiki user (add, passwd, token, role, group, disable, enable, invite, reset, remove, list) and zonewiki config (check, set, unset, passwd, token), which edit the settings file (filelock_*.go: one at a time)
│   ├── security.go         # Host and Origin checks, the page policy, login, signed session (and its user), throttles
│   ├── users.go            # Users: roles, password hashes, sign-in, the password prompt, who a task can be assigned to (assignable, the reserved names me and none)
│   ├── admin.go            # The admin page (/admin, admins), its Users tab: roles, disable, remove, writing the settings file as the commands do
│   ├── links.go            # Invite and reset links: .zone/links, /welcome/<token>
│   ├── signup.go           # Sign-up: /signup, the mail with its link, the account a sign-up link makes
│   ├── mail.go             # Mail through the deployment's SMTP server (ZONE_SMTP_*), for sign-up and assigned tasks
│   ├── notify.go           # A task given to a user is told to them: the mail (tellAssigned, called by the task writes), who gets one
│   ├── assistant.go        # Connect an assistant (/assistant): your MCP token made and revoked in the browser, with your password again
│   ├── oauth.go            # OAuth for /mcp: the metadata, client registration (nothing kept), the consent page /oauth/authorize, /oauth/token
│   ├── grants.go           # Connections: the assistants users let in through OAuth, their token hashes in .zone/grants
│   ├── password.go         # Change password (/password): your password changed in the browser, with the one you have
│   ├── signout.go          # Sessions signed out: .zone/signed-out
│   ├── stalls.go           # A request body or response that stands still for a minute fails (noStalls)
│   ├── setup.go            # The first-run page: /setup, a first login for a wiki other devices reach
│   ├── access.go           # Who may see and change what: private/<user>/ (canSee, canWrite, canSeePath)
│   ├── private.go          # The private home private/<user>/index: /private, its template, its protection
│   ├── routes.go           # The wiki's routes: pages, the editor, notes, search, previews
│   ├── pageops.go          # Page writes the routes and the MCP tools share (delete, rename, restore, create, save): checks, text not yet in git kept
│   ├── refusal.go          # What an operation refuses with (refusalError: a kind and a message), for pages and tasks alike; each door answers it its way
│   ├── views.go            # The page shell, breadcrumb, modules, menus, side columns; templates/ (embedded)
│   ├── pages.go            # The wiki's files: slugs, listing, sharing mode, link graph, renames, the pages that embed a list
│   ├── markdown.go         # Markdown → HTML on goldmark, with Zone's rules (checkboxes, callouts, heading ids …)
│   ├── math.go             # $…$ and $$…$$ math kept as TeX for KaTeX (a goldmark extension)
│   ├── sections.go         # Headings and sections as the parser finds them, for the ✎s, the editor, the MCP tools and search (pageHeadings, pageSections, findSection)
│   ├── render.go           # A page's render: wikilinks, [[tasks:]], [[task:…]], frontmatter, sources
│   ├── gitstore.go         # Git: setup, commits, moves, grep over pages, a page's log, versions, diffs, deleted pages
│   ├── history.go          # History, revisions, diffs, restore, deleted pages, uncommitted changes
│   ├── quicknotes.go       # Notes: create, Move to wiki, Append to page (the routes and MCP share it)
│   ├── merge.go            # Several editors on one page: drafts' authors, the bases, the three-way merge, texts left in conflict
│   ├── tasks.go            # Task writes (create, change, comment, tag, delete), as functions the routes and the MCP tools share
│   ├── tasklists.go        # Task lists: the lists a user sees (taskLists, refs), their pins, My tasks (the home view's rule, mineFilter, and its settings), and the list operations (make, rename, delete, share, pin, settings)
│   ├── listsmigrate.go     # The lists' migration at start: lists that pages declared and pins in embeds, taken over into the registries, once
│   ├── viewsmigrate.go     # The views' names of 2026-11 (view=today|open|closed, group=): embeds and registries from before rewritten at start, once
│   ├── tasksapi.go         # The /api/tasks routes
│   ├── tasksembed.go       # The [[tasks:]] embed: its HTML, its settings dialog, the counts the Tasks links show
│   ├── tasklogic.go        # What Zone does with tasks beyond keeping them: a series' next occurrence, completing, history, comments, backlinks, [[task:…]] references; taskFiles, the store, wired to the wiki here
│   ├── internal/taskfiles/ # Package taskfiles, no globals: the tasks on disk: _tasks/ files (users' private ones in private/<user>/_tasks/) read and written under one lock, filed by owner and month (parsed once, and a file a write didn't touch skipped), and the lists' registries beside them (lists.json: lists.go); a Store is handed the wiki's folder, its users, the clock, who may write and how a write is committed
│   ├── internal/atomicfile/ # Package atomicfile: a file written so that no reader sees half of it (pages, task files)
│   ├── taskby.go           # Comments and history a client sends whole: nobody writes under another's name
│   ├── taskseen.go         # What each user has seen of the tasks' news: .zone/seen/<user>.json, read into a task's history for the news' rule and the API
│   ├── tasksyntax.go       # The page syntax of tasks: [[tasks: …]] params, [[task:…]], tag rules, date words
│   ├── internal/taskmodel/ # Package taskmodel, no globals: a task (Task): its fields, read leniently from a file, written in a fixed key order; tags as the filter matches them; a series' dates (recurrence.go). The API's form of a task (with "private") is the server's (apiTask)
│   ├── internal/jsonobj/   # Package jsonobj: JSON objects read a member at a time (jsonObj in main): task requests, MCP messages and arguments, task files
│   ├── internal/gitrepo/   # Package gitrepo: git run in a repository, a process a call, under a time limit and a context, pathspecs literal, no detached maintenance and none inside a commit (Zone packs in the background: `go/gitstore.go`)
│   ├── internal/textsearch/  # Package textsearch, no globals: BM25 ranking (stemming, fuzzy, excerpts), the query syntax, rank fusion, difflib's ratio (search.go, difflib.go); the index in a file, mapped (indexfile.go, mmap_unix.go, mmap_other.go)
│   ├── internal/mdtext/    # Package mdtext, no globals: markdown source text (frontmatter, fences, code and math masking, headings and anchors, wikilink syntax, the splice of a section's text, checkboxes, slugs)
│   ├── internal/settingsfile/  # Package settingsfile: the settings file's format (the strict parser, line edits keeping comments, the owner-only write); what its keys mean stays in configfile.go
│   ├── internal/tokenizer/ # Package tokenizer: the embedding model's tokenizer (byte-level BPE from its tokenizer.json)
│   ├── retrieval.go        # Search and LLM retrieval (BM25 + optional semantic index), the query log
│   ├── embed.go            # Optional semantic search: section embeddings, the index and its cache, Related pages
│   ├── embed_onnx.go       # The model on onnxruntime (cgo; embed_nocgo.go: built without cgo, keywords only)
│   ├── mcp.go              # MCP protocol (JSON-RPC) for /mcp; mcp_tools.json the tool definitions
│   ├── mcpfiles.go         # Uploads for assistants: read_file, upload_file, and the file routes taking the MCP token
│   ├── mcptools.go         # The MCP tools that read, and the table of all tools with their dispatch (mcpToolTable); mcpwrite.go and mcpreplace.go those that write pages, mcptasks.go the task tools, mcpguides.go the guides, mcpargs.go what they share
│   ├── manual.go           # Zone's own docs (README + docs/spec) searched by section, for the MCP tool zone_help
│   ├── eval.go             # zonewiki eval: measures search_wiki's retrieval on your wiki against your questions (./run.sh eval)
│   ├── embedtest.go        # The tests' fake embedder (--semantic-test)
│   └── *_test.go, testdata/  # Go tests (go test ./...)
├── tests/                  # pytest over HTTP against the server (conftest.py, goserver.py, settings.py); e2e/ in a browser; bench_tasks.py measures the tasks at a team's size (not in the suites)
└── public/                 # The browser code, served as it is
    ├── style.css           # The styling: the look's tokens (:root), pages, the editor, search, dialogs, phones
    ├── tasks.css           # The task app's styling, loaded after style.css
    ├── zonewiki.js             # The entry ES module every page loads; imports the modules below
    ├── js/                 # ES modules: chrome, git, util, completion, search, actions, pagemenu, listsrow,
    │                       #   shortcuts, mdedit, mdtext (the text functions shared with the server), editor, tasklist (its Task list button), page, figures, fold, present, copyline (Copy buttons); the task app's parts
    │                       #   taskdata, taskview, taskorder, tasktabs, taskchips, taskrender, taskmodal, taskselect, taskmenu, taskkeys, taskdnd, tasktouch, tasksettings; tasktexts, taskutil, datepicker (see Client code in `docs/spec/rendering.md`)
    ├── tasks-core.js       # Pure task logic (no DOM), an ES module — shared by tasks.js, the editor and Node-run tests
    ├── tasks.js            # The task app, an ES module zonewiki.js imports; initTaskApps() starts each .tasks-app: it puts one together and starts its parts in js/task*.js (app, app.st; tests/test_task_app_wiring.py checks what they use of each other)
    ├── favicon.svg         # Browser tab icon
    ├── icon.svg            # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
    ├── manifest.json       # Web app manifest (Add to home screen)
    ├── vendor/highlight/   # highlight.js 11.9.0 + github/github-dark themes (vendored, no CDN)
    ├── vendor/gemoji/      # GitHub's emoji names (gemoji 4.1.0, MIT) for `:name` completion
    ├── vendor/katex/       # KaTeX 0.19.0 (MIT): katex.min.js, katex.min.css, fonts/*.woff2
    └── vendor/mermaid/     # Mermaid 12.3.0 (MIT): mermaid.min.js, THIRD_PARTY_LICENSES (what it bundles), COPYING.GPL-3.0 (elkjs's license as Zone takes it)
```

Page content does not live in the repo tree.
It lives in the host directory pointed to by `ZONE_DATA` (default: `~/zonewiki`), where markdown files sit at the root and in folders (a folder is part of the slug, see Key Constraints) and uploads live under `_assets/`.

---

## Running

```bash
./run.sh            # build the image and start Zone (default: "up"), via compose
./run.sh build|up|down|logs
./run.sh test       # gofmt + go vet + go test, then the unit/API tests, in the tools image (extra args go to pytest)
./run.sh e2e        # browser tests + tasks-core.js (Node) tests in the tools image
./run.sh leaks      # scan the git history for secrets (gitleaks, in the tools image)
./run.sh screenshots  # redo the README's screenshots from a sample wiki
./run.sh eval [FILE]  # measure retrieval on your wiki (questions in FILE, default _eval/retrieval.json in the wiki)
./run.sh user add|passwd|token|role|group|disable|enable|remove NAME, user list  # the users in the wiki's settings file (.zone/config)
./run.sh config check|set|unset|passwd|token  # the wiki's settings file: check it, set a key, password and MCP token without users
```

Zone is at **http://localhost:5000**.
The deployment's settings are in `.env` (gitignored; `ZONE_DATA`, `ZONE_ALLOW_REMOTE`, `TZ` …, read once into `cfg`); the wiki's own (users, password, MCP token, hosts, locale …) are in the wiki's settings file `.zone/config`, kept out of git and reloaded when it changes (`settings()`).
The wiki lives outside the repo, in `ZONE_DATA` (default `~/zonewiki`), which must exist: pages at its root and in folders, uploads in `_assets/`, tasks and their lists in `_tasks/`, the settings file, the search index files, the semantic cache and session key in `.zone/`.
Details: `docs/spec/deployment.md`.

Without the container (Go 1.27 and a C compiler; `CGO_ENABLED=0` builds one without semantic search):

```bash
(cd go && go build -o zonewiki .)
ZONE_DATA=~/zonewiki go/zonewiki                 # http://localhost:5000 (finds public/ from the binary)
ZONE_DATA=~/zonewiki go/zonewiki eval FILE       # measure retrieval
```

`zonewiki --help` lists the flags, commands and environment.
Flags are written with two dashes everywhere (`--addr :8080`, `user add NAME --mcp-token`); one dash works too.

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`), 16px on every screen (phones too), line height 1.6. Headings in pages: `##` 1.4rem, `###` 1.15rem, so sections stand out by size, not only by weight and rule.
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- No nav bar (keyboard shortcuts): the module switcher in the breadcrumb, New note / Add a task / Search icons at the top right on desktop, a bottom tab bar on phones (see Modules in `docs/spec/navigation.md`).
- Big screens (≥1320px): page views get side columns, TOC left and links right (see Side columns in `docs/spec/routes.md`); the text keeps its width.
- No external CSS frameworks — keep `style.css` and `tasks.css` (the task app's) self-contained.
- Mobile breakpoint at `640px`: page content readable, tables/pre scroll horizontally, larger touch targets.
  The editor is fully usable on phones (Mobile editor in `docs/spec/editor.md`).

---

## Tests

**Always run the full test suite before and after making changes to the server (`go/`).**

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix).
A feature or fix is not complete until it has test coverage.

```bash
./run.sh test                         # gofmt, go vet, go test, then the unit/API tests (pytest over HTTP), in the tools image
./run.sh test tests/test_mcp.py -n 0  # one module, in one process
./run.sh e2e                          # browser tests (Playwright) and tasks-core.js under Node
(cd go && go test ./...)              # the server's own tests, with a local Go
pytest tests/ --ignore=tests/e2e      # the API tests, with a local Go and pip install -r tests/requirements.txt
```

Two kinds of tests: **Go tests** (`go/*_test.go`, `go/internal/*/`) for what is inside the server (text functions, search ranking, the semantic index, git helpers, the MCP protocol);
**pytest** (`tests/`) for everything a user or client reaches over HTTP, against the real server: each test gets a temp wiki and a server process on it (`tests/goserver.py`; the binary is built once per run).
Settings a test changes go in `tests/settings.py` (`monkeypatch.setattr(settings, "PASSWORD", "pw")`); the server restarts with them.
Prefer an HTTP test when the behaviour shows through a route, a Go test when it doesn't.

Both `run.sh` commands run pytest in parallel with pytest-xdist (`-n`): `test` with a worker per CPU, `e2e` with one per two CPUs, since each worker runs its own Chromium and more starve the browsers until timing-sensitive tests fail.
Pass `-n 0` to run in one process, e.g. to debug one test.
Each worker is its own process with its own temp wikis, servers and browser, so tests must not share anything outside `tmp_path`.

Use the fixtures and helpers in `tests/conftest.py` (`wiki_dir`, `git_wiki_dir`, `client`, `write_page`, `preview`, `section_fields`, `backlinks` …).
Browser tests skip without Playwright, Node tests without Node.
Before writing a browser test, read the harness notes in `docs/spec/testing.md` (offline, touch emulation, drag limits); keep the `playwright==` pin in `tests/requirements.txt` equal to `Containerfile.tools`' image tag.
CI runs the same `run.sh` commands in the image `Containerfile.tools` builds, so a dependency or a test command changes in one place.

**Secrets never go into the repo.** Turn on the pre-commit hook once per clone (`git config core.hooksPath .githooks`): it refuses a commit whose staged changes contain a secret (gitleaks); CI scans the whole history (`./run.sh leaks`).

---

## Dependencies

`go/go.mod`: `github.com/yuin/goldmark` (markdown), `golang.org/x/text` (search's case folding and NFKD), `github.com/yalue/onnxruntime_go` (semantic search).
Nothing else; add a dependency only when it clearly pays for itself.
**Everything is pinned to an exact version** (`go.sum` checks the modules; the Containerfiles pin Go, onnxruntime, gitleaks and the model by version and SHA-256, `tests/test_image.py` checks that they agree): to update,
raise a version,
run `./run.sh test` and `./run.sh e2e`,
and commit the new pins.
**What Zone ships keeps its license notices**: a new or updated Go module, Go release or model changes `THIRD_PARTY_LICENSES`, a new vendored library brings its `LICENSE`; `tests/test_image.py` checks.

- **One process.** The binary serves with `net/http` (at most 100 connections, idle ones closed after 60s).
  Zone must stay a single process: the page and task locks (`pagesMu`, `taskFiles`' lock), the page/search caches, the throttles and the semantic-search worker live in its memory, so several processes on one wiki would lose page and task writes.
- `highlight.js` 11.9.0 is vendored in `public/vendor/highlight/`, committed as downloaded: no CDN, no npm.
  Pages load nothing from other hosts.
  To update it, replace the three files with a newer release's.
  Likewise gemoji 4.1.0's `db/emoji.json` (and its LICENSE) in `public/vendor/gemoji/`, fetched by the browser only when emoji completion first opens.
  KaTeX 0.19.0 and Mermaid 12.3.0 likewise,
  from their npm packages (`dist/`): KaTeX's `katex.min.js`,
  `katex.min.css` and `fonts/*.woff2` (browsers take the woff2 the CSS names first) in `public/vendor/katex/`,
  Mermaid's `mermaid.min.js` (the one-file build that sets `window.mermaid`) in `public/vendor/mermaid/`,
  each with its LICENSE;
  a page loads them only when it has math or a diagram (`js/figures.js`).
  To update, replace those files with a newer release's and run `./run.sh e2e` (`tests/e2e/test_figures.py`); for Mermaid also `python3 docs/licenses/mermaid_notices.py VERSION`, and check the licenses of what it now bundles.
- Semantic search needs cgo at build time and onnxruntime's shared library at run time,
  which the binary loads with `dlopen` when it first embeds: with `ZONE_SEMANTIC_SEARCH` on at build time,
  the image puts `libonnxruntime.so` (1.31.0, its GitHub release) in `/usr/local/lib` and the model in `/opt/zonewiki-model` (the `Containerfile`'s `semantic` stage, its own layer).
  Without the library, Zone searches by keywords.
  Locally: the release's `lib/` on `LD_LIBRARY_PATH`; the model is downloaded on first use.
- The tests' Python packages (`tests/requirements.txt`: pytest, pytest-xdist, Playwright, werkzeug), pinned too, live only in the tools image.
- Base images are named by digest and CI actions by commit (`Containerfile`'s `ARG UBUNTU`, `Containerfile.tools`, both `ci.yml`; What builds Zone is named by hash in `docs/spec/security.md`): a new image or action goes in the same way, never by tag alone.

---

## Key Constraints

- Do not use a database.
  Files are the only storage.
- Do not add a build step or bundler for the browser code.
- **Security** (details and reasons in `docs/spec/security.md`; don't weaken any of these):
  - `password` in `.zone/config` (a hash) turns on a login gate; the session key is one Zone keeps in `.zone/secret_key` (0600, excluded from git).
    Signing out ends the session wherever a copy of its cookie is (`go/signout.go`: its id in `.zone/signed-out`); renew a session with `renewSession`, which keeps the id, never with `setSession`.
  - Users instead (`[user …]` in `.zone/config`): a login per user, passwords only as hashes (`docs/spec/users.md`); the session names its user, `userOf(r)` / `rq.user` give it, and a write passes it to `gitCommit(user, …)` as the commit's author.
  - **Private pages and tasks** (`private/<user>/`, tasks and private lists in `private/<user>/_tasks/`, with users): every route and tool that shows, lists or changes a page asks `go/access.go` (`canSee`, `canWrite`, `canSeePath`; `hiddenPage`/`refusePage` in the routes);
    every read of tasks takes the user (`taskFiles.Open(user)` …, shared + their own files), and what holds every user's tasks (the search index) filters with `canSeeTask`.
    A task is private by its list (`applyList` in `go/tasks.go`: a private list's task is in its user's files): a shared task never goes into a private list, and a private one leaves for a shared place only with `share`. Lists are read with `taskLists(user)`, which gives only those a user sees, and a write to a list or a task in it asks `canWriteList`: don't read another user's registry for them.
    A new route gets a probe in `tests/test_privacy.py`, which fails until it has one.
    A path handed to git names one file, never a pattern (`GIT_LITERAL_PATHSPECS` in package `gitrepo`'s environment): don't turn that off for a call that takes a page's name.
  - **Guides are an admin's** (with users): `canWrite` is false for anyone else on `wiki-guide`, a folder's `AGENTS.md`/`CLAUDE.md` and the pages they pull in (`adminsOnly`, Guides in `docs/spec/users.md`): every user's assistant follows them.
  - **First run** (`go/setup.go`): a wiki other devices can reach (`ZONE_ALLOW_REMOTE`, `hosts`, `ZONE_BIND` beyond localhost) with no login answers only `/setup` until one is set, unless `no-login = yes`.
    A new route that must answer before that goes in `setupExempt`; the tests' server has `no-login = yes` (`settings.NO_LOGIN`).
  - **Viewers** (`role = viewer`) change nothing: `canWrite` in `go/access.go` is false for them, `mayWriteTasks` refuses every task write, `callMCPTool` the writing tools, and `viewerGate` a viewer's write to any route not in `viewerWrites`.
    A new write route gets a probe in `tests/test_roles.py`, which fails until it has one.
  - **Public wikis** (`ZONE_PUBLIC=view|edit` in `.env`, with users): a request without a session is the user `visitor` (`go/access.go`), who reaches only the routes in `visitorRoutes` (and `visitorEdits` with `edit`; `visitorGate` in `go/server.go`) and sees no task, note or private page.
    A new route is closed to visitors unless it is listed there, and `tests/test_public.py` fails until the route is listed in the test too: list one only after checking that nothing it answers is a task's, a note's or a private page's.
    Code that shows something to "whoever asks" must hold for `visitor` as well: ask `canSee`/`canSeeTask`, never `user != ""`.
  - `/mcp` requires a token: an MCP token (`mcp-token`, kept as its hash; with users, a user's own) or the access token of a connection a signed-in user allowed (OAuth, `go/oauth.go`). Without a login and a token it is off (403); with a login it answers 401 and says where to sign in.
    Don't make it open again.
    OAuth's rules (OAuth for `/mcp` in `docs/spec/security.md`) aren't to be loosened: the code goes only to a registered redirect URI (an unknown client or URI gets a page, never a redirect), PKCE is required, Allow takes the password again, tokens are kept as hashes, a refresh token works once. The answer after Allow is a page with a `Refresh` header because of `form-action 'self'`: don't widen the policy for it.
    In the browser a token is made only by whoever is signed in, for themselves, with their password again (`go/assistant.go`): don't drop that check.
    A password is changed in the browser the same way, only one's own and with the one they have (`go/password.go`).
  - **The settings file** `.zone/config` holds password hashes and tokens (only as hashes): it stays out of git with all of `.zone/` (account data never goes into git; `missingUsers` stops a clone without it from starting open) and is never shown (`canSeePath`, `safePath`),
    the commands write it 0600,
    and a reload with an error (or a removed file) keeps the settings from before,
    so a broken edit never opens the wiki.
  - The Host check (`hostAllowed`) validates the `Host` header (localhost, plus `hosts` from the settings file) against DNS rebinding; `ZONE_ALLOW_REMOTE=1` without `hosts` skips it.
  - The Origin check (`crossSite`) refuses cross-site writes (an `Origin` of another site, or `null`).
  - A page loads nothing from other hosts (`pageCSP`: `default-src 'self'`; images, media and frames also from the `external-content` setting's hosts; a markdown image elsewhere becomes a link): don't add a source to the policy.
  - Every response carries the page policy (`pageCSP`): only Zone's own script files run (`script-src 'self'`, so no inline `<script>`, `on…=` handler or `eval` in Zone's code, or the browser tests fail), no framing by other sites, `nosniff`.
  - Request bodies are capped in the checks (16 MB, an upload 100 MB or the settings' `upload-max`; 413 above).
    What the wiki may hold is `storage-max` (`go/storage.go`): past it nothing new is made (uploads, new pages and notes: 507), but an edit, a delete or a rename always goes through.
    The settings change only while no request runs (`betweenRequests`; requests don't wait for it), so a request's session check and access checks see the same users.
  - The session cookie is `Secure` over HTTPS (also behind a proxy's `X-Forwarded-Proto`); wrong passwords and MCP tokens are throttled per client.
- `ZONE_ALLOW_REMOTE` is the single knob for network access with `run.sh`: unset, the port is bound to loopback only.
- **Git calls time out** after `gitTimeout` (60s, `runGit`): a hung git counts as failed (exit code 124).
- Slugs are file paths relative to `ZONE_DATA` without `.md`: `journal/my-page.md` → `journal/my-page`; URL-safe (lowercase, hyphens, `/`).
  Subdirectories are supported; `mdtext.ToSlug()` keeps `/` and slugifies each part; `iterPages()` skips `_*` and hidden folders.
  Routes take the rest of the path as the slug (`{slug...}`).
- **Writes stay inside the wiki**: resolve the path and check it with `safePath()` (inside the wiki; it also refuses folders starting with `_` or `.`, so nothing reads or writes pages in `.zone/`, `.git/`, `_tasks/` …) before reading or writing;
  create parent folders (`os.MkdirAll`);
  write UTF-8 (`writeText`).
- **A page write is an operation in `go/pageops.go`** (delete, rename, restore, create, a save, a task tag's rename;
  a note's move and append in `quicknotes.go`): it checks access itself and keeps text not yet in git (`keepPending` before a write that replaces or removes a page's text, `commitOthersDraft` before one that continues it),
  and the routes and MCP tools only parse their input and turn the result or the refusal into their answer.
  A new write gets an operation there and a test through each door in `tests/test_page_writes.py`.
  (A list's rename, `renameListOp` in `go/tasklists.go`, writes pages too: the embeds and links that name the list.)
  **Page writes run one at a time** (`pagesMu`), from the reads they decide on to their commit: the doors take it, not the operations (a route that writes pages is registered as `writesPages(handler)`, an MCP tool that does says `toolWritesPages` in `mcpToolTable`);
  code under it never takes it again (lock order `pagesMu`, the task files' lock (`taskFiles.Lock`), `commitMu`).
  Task reads don't take the task files' lock: they wait only while a write replaces its files (`taskFiles.Hold` … `Release`, Storage in `docs/spec/tasks.md`), so code that writes a task file itself calls `taskFiles.Hold()` first, and a section under `taskFiles.Lock` calls only the store's `…Locked` readers.
  Pages are written atomically (`writeText` → `atomicfile.Write`: a link written through, a new file 0666 less the umask).
  `gitCommit` commits only the paths it is given (not what `.gitignore` leaves out) and returns an error, which a write returns (`notCommitted`; a `keepPending` that fails stops the write); links a move couldn't re-point are no failure (`linksLeft`: logged, the MCP tools say so).
- `index.md` is the home page and must always exist (Zone creates it at startup when missing).
  It can't be deleted or renamed via the UI or MCP.
- A rename re-points every link to the page, on pages and in tasks, in one commit (Rename in `docs/spec/editor.md`).
