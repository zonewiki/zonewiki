# Zonewiki

A self-hosted, browser-based wiki with built-in tasks and quick notes, for one person or a team of up to a hundred, and their AI assistants.
Markdown files are served and rendered at runtime by a Go server (one binary).
Pages can be edited directly on disk or via an in-browser editor.
Full-text search runs server-side.
Tasks (lists, assignees, dates, series) and Notes (quick notes filed into the wiki later) sit beside the wiki as modules and share its links and search; tasks are stored as JSON, not markdown (see Goals & Principles).
With users, pages and tasks can be private or a group's; assistants reach all of it over MCP.

---

## Meta

- **Keep this file and the spec up to date** — whenever the commands, constraints, or conventions change, update `AGENTS.md`, and `docs/structure.md` when the project structure does; whenever a feature's behaviour changes, update its file in `docs/spec/` (see Spec).
  Do it in the same change.
- **Keep the manual up to date** — a change users see (a feature, a label, a step, a setting) updates its page in `docs/manual/` in the same change, as it updates the spec.
  The manual is for users, the spec for whoever builds Zonewiki: the manual says how to do things in what a user sees and clicks, briefly, and links to the spec for the full rules (see Manual).
  The manual, the README and the spec are also Zonewiki's help: `/help` shows the manual in the browser, and the MCP tool `zone_help` serves all three to assistants answering users' questions about Zonewiki (the manual's passages first), so a feature's spec should say how to use it, not only how it is built.
- **Commit messages** — a short subject (at most 72 characters) that says what the commit does, in the imperative ("Give shared lists owners", "Fix the tab bar's touch times"), a blank line, then the body: why, what was found on the way, what the tests cover, wrapped at about 72 columns.
  The explanation goes in the body, never in the subject: GitHub's commit list and blame cut a subject at about 70 characters.
  Older commits state the result instead ("A shared list has owners"); they stay as they are.

## Spec

How each feature behaves, in detail, lives in `docs/spec/`.
Read the file for the area you are working on before changing it:

| File | Covers |
|------|--------|
| `docs/spec/routes.md` | Every route by area, the task API, what a page view shows (breadcrumb, backlinks, related, linked tasks), side columns on big screens |
| `docs/spec/rendering.md` | The page view (header, menu, copy link, folding sections, print), Zonewiki's markdown (raw HTML, autolinks, callouts, image sizing), wikilinks, `[[task:…]]` references, frontmatter, math and Mermaid diagrams, sortable tables, version history (history, revisions, diffs, Changes, undo), how a page write is made (operations, locks, commits); how a page is rendered (templates), client code |
| `docs/spec/editor.md` | The in-browser editor: opening it, saving (auto-save, Done), formatting bar, smart helpers (completion, emoji, snippets, the task list dialog, checkboxes), section editing, rename, file paste, the mobile editor, several editors (merge on save, conflicts); how it works |
| `docs/spec/navigation.md` | Search modal (page list), deleted pages, modules (the module switcher, top actions, the phones' tab bar, the Tasks counts, status badges), sharing mode, notes, keyboard shortcuts, presentation mode |
| `docs/spec/search.md` | Full-text search: query syntax, how words match, results and highlights, who searches what (private and group pages); indexing, BM25 ranking, semantic search and hybrid ranking, the index on disk, measuring retrieval |
| `docs/spec/mcp.md` | The MCP server: connecting an assistant (a token, OAuth, connections given less), the tools (one section each), what every write does, guides, `zone_help` (Zonewiki's docs for assistants); how it works (protocol, auth, OAuth) |
| `docs/spec/tasks.md` | Tasks: embed syntax, dates, fields, repeating tasks, views, settings, export and import, interaction (filter, editing, quick actions, selection, cards), history, comments, news on your tasks (following, mentions, mail), importance, keep tasks, lists (who sees and manages them, the Inbox, My tasks, pins), a task's files, renaming a tag, touch, keyboard shortcuts; how it works (the code, storage, what a start does) |
| `docs/spec/security.md` | Principles, before you open it up (HTTPS, what to know), login and sessions (the session cookie, signing out, first run), the settings file and Zonewiki's folders, the permission model (who asks is an asker), public wikis (visitors), the MCP token and OAuth, the Host and Origin checks, the page policy (CSP, raw HTML defused, framing), untrusted input, throttling, limits, supply chain (secrets, code scanning, pins by hash) |
| `docs/spec/users.md` | Several users: the settings file `.zone/config` (its keys, format, storage limit, password hashes, kept out of git, `zonewiki user`/`zonewiki group`/`zonewiki config`, reloading), roles (admins, disabled users, viewers, visitors, guides), groups (made of groups, of your own, owners, renamed, ended), managing users, invite links, sign-up, signing in, per-user MCP tokens, private pages, group pages, who may move a page to other readers, private tasks, assigning tasks, commit authors |
| `docs/spec/deployment.md` | Containerfile, compose, `run.sh`, `.env`, the prebuilt image (compose.yaml; a NAS or server, Synology), CI (Forgejo, GitHub), mail (SMTP), network and phone access, the version string, the home-screen app, the data directory (all of `.zone/`), backups, moving a wiki (`zonewiki export`, `zonewiki import`), packing the repository, how big a wiki can be |
| `docs/spec/testing.md` | The test harness (fixtures, the server under test, the browser tests' gotchas), the guards that fail for a forgotten step, where each area's tests live, the shared case files, the suites and the bench, secret scanning (the pre-commit hook, CI's leaks job) |

The spec and this file are written a sentence a line (a long sentence a clause a line; a list item's following lines indented under its text), so a change shows as the sentence that changed, not as its paragraph.
Markdown joins the lines when it renders them.

A spec file is Zonewiki's help as much as its design (`zone_help` hands it out by section), so it is laid out for a reader:
per feature, how to use it first (what a user sees and does), how it works after (the code, the files, the reasons);
enumerations (routes, tools, keys, shortcuts, syntax) as tables, a cell a line or two, the detail in a section below;
real headings, not long bullets with a bold label, and each heading unique in its file (`go/manual.go` finds a section by it);
and the rule as it is now, not how it was measured: measurements and test results go in the commit message (a spec says where the current numbers live, as How big a wiki can be in `docs/spec/deployment.md` does).

## Manual

`docs/manual/` is Zonewiki's manual, for users: `README.md` is its contents and fixes the reading order, one file per area (getting started, pages, writing, history, notes, tasks, search, getting around, teams, assistants, the wiki guide, your data, running Zonewiki yourself).
It is in the browser at `/help` (Help in `docs/spec/routes.md`), in `zone_help`, and on zonewiki.com (`zonewiki manual DIR` writes it out as HTML).
It is written like the spec, a sentence a line, with headings unique in a file, but for a person in a browser: what to click or type and what happens, the words on the screen in **bold**, no function or file names of the code, only what is built.
A page stays readable in a few minutes; the full rules are a link away (`../spec/tasks.md#views`).
Links are written for the files (`tasks.md#lists`, `../screenshots/page.png`), and `tests/test_help.py` fails for one that leads nowhere, a heading renamed included.

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in the data directory (`$ZONE_DATA`) are the wiki content.
  Deliberate exception: Tasks live in `_tasks/tasks.json`/`_tasks/closed.json` (a list's in `_tasks/lists/<name>/`) and their lists in `_tasks/lists.json` (structured fields, concurrency-safe writes) — still plain files, still git-versioned, but not markdown.
- **Editable two ways** — edit files in any text editor, or click "Edit" in the browser
- **First-class for people and for their assistants** — more and more of a wiki is read and written by an AI assistant acting for its user, so an assistant is a user's second pair of hands, not an add-on;
  and a person's UX is never traded for an assistant's, nor the other way round (Zonewiki must be as good in the browser as if no assistant ever used it):
  what a person does in the browser an assistant can do over MCP, through the same operations (never a second implementation) and never with more than its user;
  what an assistant did is seen, told apart from its user's own work and undone as easily as it was done;
  and the wiki stays plain files that any model, tool or person reads without Zonewiki.
  Zonewiki is the memory and the rules, not the assistant: it brings no model of its own to chat with.
- **Good UX** — a feature is done when it is easy to use, not when it works: few steps, the obvious thing where a person looks for it, and nothing to learn that the screen could say.
- **Conceptually sound and clean** — a few ideas that hold everywhere (a page's place, a list's members, who may write), each said once and used by every door; a new feature fits them or the ideas change, never a special case beside them.
- **Nice looking** — clean typography, readable line lengths, good contrast
- **Searchable** — server-side full-text search across all pages
- **Secure by default** — a wiki holds what people would not publish, so every door (browser, MCP, files, mail links) is closed until it is opened on purpose, and a new feature adds no way around a rule that holds elsewhere.
  Least privilege: a token, link or user can do what it was given and no more, and a leaked one is limited, expiring or revocable.
  Input is untrusted (escaped on output, checked on arrival), secrets stay out of git and logs, and `docs/spec/security.md` says what each protection is and tests prove it.
- **One robust permission model** — who may read, write or administer a page, a task, a list or a folder is decided in one place that every door asks (the web UI, MCP tools, search, history, exports, mail digests), never re-implemented per feature.
  It denies by default, is the same for a person and for an AI agent acting for them (an agent never has more than its user), and what a user may not see doesn't appear in a search result, a count, a link preview or an error message.
  A rule is easy to read and to change, and tested at each door, including that a denied thing is really denied.
- **Scalable** — a wiki stays fast as it grows in pages, tasks and users (tens of thousands of pages, a team of a hundred):
  a request doesn't cost in proportion to the whole wiki where an index can answer it,
  and what is said about size is measured, not assumed.

---

## Tech Stack

| Concern             | Choice                                       |
|---------------------|----------------------------------------------|
| Server              | Go (`net/http`), one process, one binary (`go/`) |
| Markdown render     | `goldmark` (server renders to HTML), with Zonewiki's rules (`go/markdown.go`) |
| HTML views          | Go `html/template` in `go/templates` (embedded in the binary), auto-escaped; what every page's layout shows is a typed `pageShell` (`.Shell`), a view's data a struct embedding `view` (`pageView`, `editView`, `historyListView` …; `page()` takes nothing else) |
| Syntax highlight    | `highlight.js` (vendored in `public/vendor/`, no CDN) |
| Math, diagrams      | KaTeX and Mermaid, vendored in `public/vendor/`, loaded by a page that has math or a diagram |
| Fonts               | The system's sans for text; Source Serif 4 for headings, vendored in `public/vendor/source-serif/` (variable weight, Latin and Latin Extended) |
| Styling             | Custom CSS (classless, semantic HTML); the look (warm paper and ink, a deep green accent, serif headings) is shared with zonewiki.com and Zonewiki CP's pages: its tokens are the `:root` variables at the top of `public/style.css` |
| In-browser editor   | Plain `<textarea>` with live preview         |
| Search              | BM25 in `go/internal/textsearch`, optionally fused with semantic search (`go/embed.go`); search modal (Ctrl+P) and `/search` |
| Storage             | Plain `.md` files in `$ZONE_DATA` (tasks in `_tasks/*.json`) |
| Container           | Compose with Podman or Docker (Ubuntu 26.04 LTS base image) |
| Tests               | `go test` for the server's internals; pytest over HTTP against the server, Playwright in a browser |

No build step for the browser code (no bundler: the server serves `public/` as it is).
The binary carries `public/` and the docs too (`go:embed` in `zonefiles.go` at the root, a module `go/go.mod` takes from `../`), but a checkout's folders win, so an edit there needs no rebuild (What the binary holds in `docs/spec/deployment.md`).
No database.
Auth is optional (a password, or users; see Key Constraints).

---

## Project Structure

The repository's layout, a line for each file and folder and what it is for, is in `docs/structure.md`.
Read it before looking for where something lives; when the project structure changes, update it in the same change.

Page content does not live in the repo tree.
It lives in the host directory pointed to by `ZONE_DATA` (default: `~/zonewiki`), where markdown files sit at the root and in folders (a folder is part of the slug, see Key Constraints) and uploads live under `_assets/`.

---

## Running

```bash
./run.sh            # build the image from the checkout and start Zonewiki (default: "up"), via compose.build.yaml
./run.sh build|up|down|logs|update   # beside compose.yaml alone (no checkout), run.sh runs the prebuilt image instead
./run.sh test       # gofmt + go vet + go test -race, then the unit/API tests, in the tools image (extra args go to pytest)
./run.sh e2e        # browser tests + tasks-core.js (Node) tests in the tools image
./run.sh flaky [N] [pytest args]  # chosen browser tests N times each (20) on a busy machine: finds a test that fails now and then
./run.sh bench      # the tasks at a team's size against their budgets, then under the race detector (CI's bench job)
./run.sh retrieval  # search on the sample wiki (tests/retrieval) against its floor, by keywords and by meaning (CI's retrieval job)
./run.sh binaries   # the release binaries (Linux, macOS; amd64, arm64) into dist/, with SHA256SUMS (CI's release job, on a v* tag)
./run.sh leaks      # scan the git history for secrets (gitleaks, in the tools image)
./run.sh screenshots  # redo the README's screenshots from a sample wiki
./run.sh eval [FILE]  # measure retrieval on your wiki (questions in FILE, default _eval/retrieval.json in the wiki)
./run.sh user add|passwd|token|role|email|group|disable|enable|shared|invite|reset|remove NAME, user list  # the users in the wiki's settings file (.zone/config)
./run.sh group includes NAME [+G] [-G], group owners NAME [+U] [-U], group list  # groups made of groups and a group's owners ([group NAME] in the settings file)
./run.sh config check|set|unset|passwd|token  # the wiki's settings file: check it, set a key, password and MCP token without users
./run.sh export > FILE, import < FILE  # the whole wiki in one file, with its history and users, and a wiki made from one in an empty folder (zonewiki export, zonewiki import)
```

Zonewiki is at **http://localhost:5080**.
The deployment's settings are in `.env` (gitignored; `ZONE_DATA`, `ZONE_ALLOW_REMOTE`, `TZ` …, read once into `cfg`); the wiki's own (users, password, MCP token, hosts, locale …) are in the wiki's settings file `.zone/config`, kept out of git and reloaded when it changes (`settings()`).
The wiki lives outside the repo, in `ZONE_DATA` (default `~/zonewiki`), which must exist (unset, the binary makes `~/zonewiki`): pages at its root and in folders, uploads in `_assets/`, tasks and their lists in `_tasks/`, the settings file, the search index files, the semantic cache and session key in `.zone/`.
Details: `docs/spec/deployment.md`.

Without the container (Go 1.27 and a C compiler; `CGO_ENABLED=0` builds one without semantic search):

```bash
(cd go && go build -o zonewiki .)
go/zonewiki                                      # http://localhost:5080, the wiki in ~/zonewiki (ZONE_DATA=… for another), the checkout's public/ and docs/
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
- No nav bar (keyboard shortcuts): the module switcher in the breadcrumb, Sharing mode and Search at the top right on desktop (new notes and tasks from the keyboard, `c n` / `c t`), a bottom tab bar on phones (see Modules in `docs/spec/navigation.md`).
- Big screens (≥1320px): page views get side columns, TOC left and links right (see Side columns in `docs/spec/routes.md`); the text keeps its width.
- No external CSS frameworks — keep `style.css` and `tasks.css` (the task app's) self-contained.
- Mobile breakpoint at `640px`: page content readable, tables/pre scroll horizontally, larger touch targets.
  The editor is fully usable on phones (Mobile editor in `docs/spec/editor.md`).

---

## Tests

**Always run the full test suite before and after making changes to the server (`go/`).**

**A change to search (chunking, tokens, ranking, the model) is held to the retrieval floor** (`tests/retrieval/`, its `README.md`): `./run.sh test` fails when the keyword ranking finds less, or more, than `floors.json` says, `./run.sh retrieval` measures the hybrid ranking too. Raise the floor with a gain, in the same commit; never tune the sample wiki to a question.

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix).
A feature or fix is not complete until it has test coverage.

```bash
./run.sh test                         # gofmt, go vet, go test -race, then the unit/API tests (pytest over HTTP), in the tools image
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

**Secrets never go into the repo.** Turn on the pre-commit hook once per clone (`git config core.hooksPath .githooks`): it refuses a commit whose staged changes contain a secret (gitleaks); CI scans the whole history (`./run.sh leaks`; Secret scanning in `docs/spec/testing.md`).

---

## Dependencies

`go/go.mod`: `github.com/yuin/goldmark` (markdown), `golang.org/x/text` (search's case folding and NFKD), `github.com/yalue/onnxruntime_go` (semantic search).
Nothing else; add a dependency only when it clearly pays for itself.
**Everything is pinned to an exact version** (`go.sum` checks the modules; the Containerfiles pin Go, onnxruntime, gitleaks and the model by version and SHA-256, `tests/test_image.py` checks that they agree): to update,
raise a version,
run `./run.sh test` and `./run.sh e2e`,
and commit the new pins.
**What Zonewiki ships keeps its license notices**: a new or updated Go module, Go release or model changes `THIRD_PARTY_LICENSES`, a new vendored library brings its `LICENSE`; `tests/test_image.py` checks.
The binary carries them all (`zonewiki licenses` prints them).

- **One process.** The binary serves with `net/http` (at most 100 connections, idle ones closed after 60s).
  Zonewiki must stay a single process: the page and task locks (`pagesMu`, `taskFiles`' lock), the page/search caches, the throttles and the semantic-search worker live in its memory, so several processes on one wiki would lose page and task writes.
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
  Without the library, Zonewiki searches by keywords.
  Locally: the release's `lib/` on `LD_LIBRARY_PATH`; the model is downloaded on first use.
- The tests' Python packages (`tests/requirements.txt`: pytest, pytest-xdist, Playwright, werkzeug), pinned too, live only in the tools image.
- Base images are named by digest and CI actions by commit (`Containerfile`'s `ARG UBUNTU`, `Containerfile.tools`, every workflow; What builds Zonewiki is named by hash in `docs/spec/security.md`): a new image or action goes in the same way, never by tag alone.
- GitHub's code scanning (CodeQL, `.github/workflows/codeql.yml`) is told which of Zonewiki's functions check a path, a redirect or HTML in `.github/codeql/extensions/zone-go/models.yml` (Code scanning in `docs/spec/security.md`):
  a check that is renamed or moved takes its row along (`tests/test_image.py` fails for a row without its function),
  and only a function that really checks gets one.

---

## Key Constraints

Rules an agent would break without noticing.
Each says what to do and where the full rule and its reasons are; read that before changing the area.
Where a guard is named, it fails for a forgotten step; where none is, only the review catches it.

- No database, files are the only storage; no build step or bundler for the browser code.
- **One permission model** (`go/access.go`; The permission model in `docs/spec/security.md`):
  every route and tool that shows, lists or changes a page, task or list asks it (`canSee`, `canWrite`, `canSeePath`, `canSeeTask`, `canSeeList`, `canWriteList`, `mayWriteTasks`), denies by default, and holds for `visitor` too: never test `user != ""`.
  A page's or list's place (everyone's, a user's `private/<user>/`, a group's `groups/<group>/`) is who sees it (`placeOf`, `ownerPlace`); code that asks "is this private" says which it means (`place.shared`, `place.own`, `place.within`).
  A new route or tool gets its probes in `tests/test_privacy.py` (and so `tests/test_group_pages.py`, `tests/test_narrowed.py`), which fail until it has them.
- **Who asks is an asker, not a name** (Who asks is an asker in `docs/spec/security.md`): a permission takes `userOf(r)` / `rq.user`, never `asUser(name)`; a commit's author or a name written out is `asker.Name`.
  Ownership and group membership are asked only in `go/access.go`, never by comparing with `user.Name`; "is this task private" is `Task.Private()`, never `Owner == ""`.
  Guards: `TestAskersAreMadeInFewPlaces`, `TestWhatIsTheirsIsAskedInOnePlace` (a narrow regex, easy to slip past).
- **Writes go through `changesNothing`** (viewers, read-only connections): a write asks `canWrite`/`mayWriteTasks`, never `isViewer` alone; a writing MCP tool says so in `mcpToolTable`; a token becomes an asker only in `mcpAuthorized`.
  A new write route gets a probe in `tests/test_roles.py`.
- **Moving to fewer readers is asked** (`checkReaders`, `mayNarrow`, `mayNarrowTask`; Group pages in `docs/spec/users.md`): no move may skip it.
  A shared task never goes into a private list, a private one leaves only with `share` (`applyList`).
  A group's name is never reused for another (`groupNameTaken`), and what a non-admin may change in the settings file stays one group they own (`go/groups.go`): don't widen either.
- **A task's files** go to `_tasks/files/<id>/_assets/`, never the shared `_assets/` (`seesTaskFiles`, A task's files in `docs/spec/tasks.md`).
- **Guides are an admin's** (`adminsOnly`): `wiki-guide`, a folder's `AGENTS.md`/`CLAUDE.md` and the pages they pull in.
- **Gates for new routes**: one that must answer before a login is set goes in `setupExempt` (First run); one visitors may reach goes in `visitorRoutes`/`visitorEdits` and in `tests/test_public.py`, only after checking it answers nothing of a task, a note or a private page (Public wikis); a viewer's write in `viewerWrites`.
- **Don't loosen the doors** (each in `docs/spec/security.md`): `/mcp` needs a token; OAuth's rules (registered redirect URIs only, PKCE, Allow takes the password, hashed tokens, one-use refresh); tokens and passwords are made or changed in the browser only for oneself, with the password again (`go/assistant.go`, `go/password.go`); the Host check (`hostAllowed`) and the Origin check (`crossSite`); the page policy (`pageCSP`: no source added, no inline script or `on…=` handler or `eval`); body caps and `storage-max` (an edit, delete or rename always goes through); login throttling.
- **Sessions**: renew one with `renewSession` (keeps the id), never `setSession`; signing out ends it everywhere (`go/signout.go`).
- **The settings file** `.zone/config` (hashes only) and all of `.zone/` stay out of git and are never shown; the commands write it 0600; a broken reload keeps the settings from before; it changes only between requests (`betweenRequests`). See The settings file in `docs/spec/users.md`.
- **Paths**: check every path with `safePath()` before reading or writing (inside the wiki, no `_…`/`.…` folders, no symlinks, no control character or backslash); a path handed to git names one file (`GIT_LITERAL_PATHSPECS`), don't turn that off.
  Slugs are paths relative to `ZONE_DATA` without `.md`, lowercase with hyphens and `/` (`mdtext.ToSlug()`); `iterPages()` skips `_*` and hidden folders.
- **A page write is an operation in `go/pageops.go`**, under `pagesMu` taken by the door (`writesPages`, `toolWritesPages`), lock order `pagesMu` → `taskFiles.Lock` → `commitMu`; it keeps text not yet in git (`keepPending`) and returns `gitCommit`'s error.
  Code writing a task file itself calls `taskFiles.Hold()` first.
  A new write gets a test through each door in `tests/test_page_writes.py`; no guard catches a miss.
  See How a page write is made in `docs/spec/rendering.md`.
- **One process**: the locks, caches, throttles and the semantic worker live in memory, so two processes on one wiki lose writes.
- **The data format** (`.zone-format`, `wikiFormat` in `go/wikiformat.go`): a change to what Zonewiki keeps in git that an older Zonewiki would misread raises it, with a start-up step that writes the new number in its own commit (The data format in `docs/spec/deployment.md`).
- `index.md` always exists and can't be deleted or renamed; a rename re-points every link to the page, on pages and in tasks, in one commit.
- `ZONE_ALLOW_REMOTE` is the one knob for network access, with `run.sh` and for the binary; unset, the port is bound to loopback (compose's mapping; the binary's `--addr :PORT` on `127.0.0.1`, `listenAddr`).
  The container itself listens on every interface (the `Containerfile`'s `--addr 0.0.0.0:5080`); don't make the binary's default do so.
