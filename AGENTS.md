# Zone

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
  The manual is for users, the spec for whoever builds Zone: the manual says how to do things in what a user sees and clicks, briefly, and links to the spec for the full rules (see Manual).
  The manual, the README and the spec are also Zone's help: `/help` shows the manual in the browser, and the MCP tool `zone_help` serves all three to assistants answering users' questions about Zone (the manual's passages first), so a feature's spec should say how to use it, not only how it is built.
- **Commit messages** — a short subject (at most 72 characters) that says what the commit does, in the imperative ("Give shared lists owners", "Fix the tab bar's touch times"), a blank line, then the body: why, what was found on the way, what the tests cover, wrapped at about 72 columns.
  The explanation goes in the body, never in the subject: GitHub's commit list and blame cut a subject at about 70 characters.
  Older commits state the result instead ("A shared list has owners"); they stay as they are.

## Spec

How each feature behaves, in detail, lives in `docs/spec/`.
Read the file for the area you are working on before changing it:

| File | Covers |
|------|--------|
| `docs/spec/routes.md` | Every route by area, the task API, what a page view shows (breadcrumb, backlinks, related, linked tasks), side columns on big screens |
| `docs/spec/rendering.md` | The page view (header, menu, copy link, folding sections, print), Zone's markdown (raw HTML, autolinks, callouts, image sizing), wikilinks, `[[task:…]]` references, frontmatter, math and Mermaid diagrams, sortable tables, version history (history, revisions, diffs, Changes, undo); how a page is rendered (templates), client code |
| `docs/spec/editor.md` | The in-browser editor: opening it, saving (auto-save, Done), formatting bar, smart helpers (completion, emoji, snippets, the task list dialog, checkboxes), section editing, rename, file paste, the mobile editor, several editors (merge on save, conflicts); how it works |
| `docs/spec/navigation.md` | Search modal (page list), deleted pages, modules (the module switcher, top actions, the phones' tab bar, the Tasks counts, status badges), sharing mode, notes, keyboard shortcuts, presentation mode |
| `docs/spec/search.md` | Full-text search: query syntax, how words match, results and highlights, who searches what (private and group pages); indexing, BM25 ranking, semantic search and hybrid ranking, the index on disk, measuring retrieval |
| `docs/spec/mcp.md` | The MCP server: connecting an assistant (a token, OAuth, connections given less), the tools (one section each), what every write does, guides, `zone_help` (Zone's docs for assistants); how it works (protocol, auth, OAuth) |
| `docs/spec/tasks.md` | Tasks: embed syntax, dates, fields, repeating tasks, views, settings, export and import, interaction (filter, editing, quick actions, selection, cards), history, comments, news on your tasks (following, mentions, mail), importance, keep tasks, lists (who sees and manages them, the Inbox, My tasks, pins), a task's files, renaming a tag, touch, keyboard shortcuts; how it works (the code, storage, what a start does) |
| `docs/spec/security.md` | Principles, before you open it up (HTTPS, what to know), login and sessions (the session cookie, signing out, first run), the settings file and Zone's folders, the permission model, public wikis (visitors), the MCP token and OAuth, the Host and Origin checks, the page policy (CSP, raw HTML defused, framing), untrusted input, throttling, limits, supply chain |
| `docs/spec/users.md` | Several users: the settings file `.zone/config` (its keys, format, storage limit, password hashes, kept out of git, `zonewiki user`/`zonewiki group`/`zonewiki config`, reloading), roles (admins, disabled users, viewers, visitors, guides), groups (made of groups, of your own, owners, renamed, ended), managing users, invite links, sign-up, signing in, per-user MCP tokens, private pages, group pages, who may move a page to other readers, private tasks, assigning tasks, commit authors |
| `docs/spec/deployment.md` | Containerfile, compose, `run.sh`, `.env`, a NAS or server (prebuilt image, Synology), CI (Forgejo, GitHub), mail (SMTP), network and phone access, the version string, the home-screen app, the data directory (all of `.zone/`), backups, packing the repository, how big a wiki can be |
| `docs/spec/testing.md` | The test harness (fixtures, the server under test, the browser tests' gotchas), the guards that fail for a forgotten step, where each area's tests live, the shared case files, the suites and the bench, secret scanning (the pre-commit hook, CI's leaks job) |

The spec and this file are written a sentence a line (a long sentence a clause a line; a list item's following lines indented under its text), so a change shows as the sentence that changed, not as its paragraph.
Markdown joins the lines when it renders them.

A spec file is Zone's help as much as its design (`zone_help` hands it out by section), so it is laid out for a reader:
per feature, how to use it first (what a user sees and does), how it works after (the code, the files, the reasons);
enumerations (routes, tools, keys, shortcuts, syntax) as tables, a cell a line or two, the detail in a section below;
real headings, not long bullets with a bold label, and each heading unique in its file (`go/manual.go` finds a section by it);
and the rule as it is now, not how it was measured: measurements and test results go in the commit message (a spec says where the current numbers live, as How big a wiki can be in `docs/spec/deployment.md` does).

## Manual

`docs/manual/` is Zone's manual, for users: `README.md` is its contents and fixes the reading order, one file per area (getting started, pages, writing, history, notes, tasks, search, getting around, teams, assistants, the wiki guide, your data, running Zone yourself).
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
  and a person's UX is never traded for an assistant's, nor the other way round (Zone must be as good in the browser as if no assistant ever used it):
  what a person does in the browser an assistant can do over MCP, through the same operations (never a second implementation) and never with more than its user;
  what an assistant did is seen, told apart from its user's own work and undone as easily as it was done;
  and the wiki stays plain files that any model, tool or person reads without Zone.
  Zone is the memory and the rules, not the assistant: it brings no model of its own to chat with.
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
./run.sh            # build the image and start Zone (default: "up"), via compose
./run.sh build|up|down|logs
./run.sh test       # gofmt + go vet + go test -race, then the unit/API tests, in the tools image (extra args go to pytest)
./run.sh e2e        # browser tests + tasks-core.js (Node) tests in the tools image
./run.sh bench      # the tasks at a team's size against their budgets, then under the race detector (CI's bench job)
./run.sh retrieval  # search on the sample wiki (tests/retrieval) against its floor, by keywords and by meaning (CI's retrieval job)
./run.sh binaries   # the release binaries (Linux, macOS; amd64, arm64) into dist/, with SHA256SUMS (CI's release job, on a v* tag)
./run.sh leaks      # scan the git history for secrets (gitleaks, in the tools image)
./run.sh screenshots  # redo the README's screenshots from a sample wiki
./run.sh eval [FILE]  # measure retrieval on your wiki (questions in FILE, default _eval/retrieval.json in the wiki)
./run.sh user add|passwd|token|role|email|group|disable|enable|invite|reset|remove NAME, user list  # the users in the wiki's settings file (.zone/config)
./run.sh group includes NAME [+G] [-G], group owners NAME [+U] [-U], group list  # groups made of groups and a group's owners ([group NAME] in the settings file)
./run.sh config check|set|unset|passwd|token  # the wiki's settings file: check it, set a key, password and MCP token without users
```

Zone is at **http://localhost:5000**.
The deployment's settings are in `.env` (gitignored; `ZONE_DATA`, `ZONE_ALLOW_REMOTE`, `TZ` …, read once into `cfg`); the wiki's own (users, password, MCP token, hosts, locale …) are in the wiki's settings file `.zone/config`, kept out of git and reloaded when it changes (`settings()`).
The wiki lives outside the repo, in `ZONE_DATA` (default `~/zonewiki`), which must exist (unset, the binary makes `~/zonewiki`): pages at its root and in folders, uploads in `_assets/`, tasks and their lists in `_tasks/`, the settings file, the search index files, the semantic cache and session key in `.zone/`.
Details: `docs/spec/deployment.md`.

Without the container (Go 1.27 and a C compiler; `CGO_ENABLED=0` builds one without semantic search):

```bash
(cd go && go build -o zonewiki .)
go/zonewiki                                      # http://localhost:5000, the wiki in ~/zonewiki (ZONE_DATA=… for another), the checkout's public/ and docs/
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
**What Zone ships keeps its license notices**: a new or updated Go module, Go release or model changes `THIRD_PARTY_LICENSES`, a new vendored library brings its `LICENSE`; `tests/test_image.py` checks.
The binary carries them all (`zonewiki licenses` prints them).

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
  - **Who asks is an asker, not a name** (`asker` in `go/access.go`, package `internal/asker`): a function that answers who may see or change something, or reads for someone, takes an `asker` (a request's is `userOf(r)` / `rq.user`: the user, with what an assistant's connection was given less); one that only needs to know who someone is takes the name, `asker.Name` (a commit's author, whose folder, a settings lookup).
    Don't ask a permission with `asUser(name)` for a request: that is the person with all they may, for what Zone asks about a user it knows by name (whom a task is given to, who gets a mail). Don't put an asker where a name is written (JSON, a file, a message): its `Name`.
    `TestAskersAreMadeInFewPlaces` lists the functions that call `asUser` and the one that writes an asker out, and fails for `asUser(user.Name)`: a new use is added there after a look at where its name comes from.
  - **Private pages and tasks** (`private/<user>/`, tasks and private lists in `private/<user>/_tasks/`, with users): every route and tool that shows, lists or changes a page asks `go/access.go` (`canSee`, `canWrite`, `canSeePath`; `hiddenPage`/`refusePage` in the routes);
    every read of tasks takes the user (`taskFiles.Open(user)` …, the shared files, their own and their groups'), and what holds every user's tasks (the search index) filters with `canSeeTask`.
    A task is private by its list (`applyList` in `go/tasks.go`: a private list's task is in its user's files): a shared task never goes into a private list, and a private one leaves for a shared place only with `share`. Lists are read with `taskLists(user)`, which gives only those a user sees, and a write to a list or a task in it asks `canWriteList`: don't read another user's registry for them.
    **A list is in a place, as a page is** (A group's lists in `docs/spec/tasks.md`): everyone's (`_tasks/`), a user's (`private/<user>/_tasks/`) or a group's (`groups/<group>/_tasks/`, the task store's owner `@<group>`: `taskfiles.GroupOwner`). Its place is its owner's (`ownerPlace` in `go/access.go`, the same `place` a page has), which `canSeeTask` and `canSeeList` ask; the store reads and writes only the owners a user sees (`Store.Owners`: pass the user, `taskfiles.Everyone` only for what Zone does over all the tasks and filters later), and a group's task is given only to a member. A shared task is everyone's or a group's: ask `Task.Private()`/`privateOwner`, never `Owner == ""`, for "is it private". `tests/test_group_lists.py` probes the routes.
    **A group's pages** (`groups/<group>/`, with users; Group pages in `docs/spec/users.md`) are its members': the same functions answer (`placeOf` gives a page's place: everyone's, a user's, a group's, no one's), so code that asks `canSee`/`canSeePath` holds for them, and code that asks "is this private" must say which it means (`place.shared`, `place.own`, `place.within`).
    A group's folder is its group's whoever is in it (a group nobody is in: seen by no one), and a page moves to more people by whoever may write it, to fewer only by an admin or by whoever shared it while nobody else changed it, asked first either way (`checkReaders`, `mayNarrow` in `go/pages.go`; a task into a list fewer people see by its maker or an admin, `mayNarrowTask`): don't add a move that skips it.
    **A group can be made by anyone who may write** (`go/groups.go`, A group of your own in `docs/spec/users.md`), who owns it: what a non-admin changes in the settings file is one group's name on the users and that group's `owners`, for a group they own (and a group's name taken off themselves, when they leave one: `mayLeave`), and a new group never takes a name that something goes by (`groupNameTaken`: a group, a folder in `groups/`, now or in the history; only the group that went by a name takes it back, `was` in its section, and a name in a group's `was` is taken for every other), or it would be handed that folder, its lists or their history. Don't widen either.
    **A task's files** (`_tasks/files/<id>/_assets/`, with users) are seen by who sees the task (`taskPlace`, `seesTaskFiles`; before the task is made its uploader's alone, `claimUnsaved`, and once it is deleted for good no one's): an upload for a task goes there, never to the shared `_assets/`, whatever its list.
    A new route gets a probe in `tests/test_privacy.py`, which fails until it has one; `tests/test_group_pages.py` runs the same probes against a group's folder.
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
    **A connection can be given less than its user sees** (Connections given less to see in `docs/spec/mcp.md`): not their private place (`asker.NoPrivate`), only some of their groups (`asker.OnlyGroups`). Whose a private thing is and who is in a group are asked in `go/access.go` alone (`place.own`/`sees`, `canSeeTask`, `canSeeList`, `inGroup`, `groupsFor`; `lacksPrivate` where something private would be made): never compare an owner with `user.Name` or ask the settings for an asker's groups elsewhere (`TestWhatIsTheirsIsAskedInOnePlace` fails for it, but only as a narrow regex: `owner == user.Name` and `memberOf`/`groupsOf(user.Name)` with the asker called `user`, `rq.user`, `rq.who()` or `userOf(r)`, in `go/*.go`; another variable, a name copied first, `slices.Contains(owners, user.Name)` or `go/internal/` gets past it, see `docs/spec/testing.md`), and a new tool gets its probes in `tests/test_privacy.py`, which `tests/test_narrowed.py` asks again through a narrowed connection.
    **A connection can be given less than its user may change** (Read-only connections in `docs/spec/mcp.md`): one that only reads changes nothing: its asker is `ReadOnly`, and `changesNothing` in `go/access.go` (a viewer, or such an asker) is what `canWrite` and `mayWriteTasks` ask, so no write gets past it; the doors an assistant comes in by ask first, for the message (`noChangeMsg`: `callMCPTool`, the upload with a token). A token becomes an asker in one place (`mcpAuthorized`; a new door for a token takes it from there with `withAsker`), a write asks `canWrite`/`mayWriteTasks` or `changesNothing`, never `isViewer` alone, a tool that writes says so in `mcpToolTable`, and a connection is only ever made narrower without the password.
    In the browser a token is made only by whoever is signed in, for themselves, with their password again (`go/assistant.go`): don't drop that check.
    A password is changed in the browser the same way, only one's own and with the one they have (`go/password.go`).
  - **The settings file** `.zone/config` holds password hashes and tokens (only as hashes): it stays out of git with all of `.zone/` (account data never goes into git; `missingUsers` stops a clone without it from starting open) and is never shown (`canSeePath`, `safePath`),
    the commands write it 0600,
    and a reload with an error (or a removed file) keeps the settings from before,
    so a broken edit never opens the wiki.
  - The Host check (`hostAllowed`) validates the `Host` header (localhost, plus `hosts` from the settings file) against DNS rebinding; `ZONE_ALLOW_REMOTE=1` without `hosts` lets in any IP address (and names in `.internal`) too, but no other name.
  - The Origin check (`crossSite`) refuses cross-site writes (an `Origin` of another site, or `null`).
  - A page loads nothing from other hosts (`pageCSP`: `default-src 'self'`; images, media and frames also from the `external-content` setting's hosts; a markdown image elsewhere becomes a link): don't add a source to the policy.
  - Every response carries the page policy (`pageCSP`): only Zone's own script files run (`script-src 'self'`, so no inline `<script>`, `on…=` handler or `eval` in Zone's code, or the browser tests fail), no framing by other sites, `nosniff`.
  - Request bodies are capped in the checks (16 MB, an upload 100 MB or the settings' `upload-max`; 413 above).
    What the wiki may hold is `storage-max` (`go/storage.go`): past it nothing new is made (uploads, new pages and notes: 507), but an edit, a delete or a rename always goes through.
    The settings change only while no request runs (`betweenRequests`; requests don't wait for it), so a request's session check and access checks see the same users.
  - The session cookie is `Secure` over HTTPS (also behind a proxy's `X-Forwarded-Proto`); wrong passwords and MCP tokens are throttled per client and account (a right try clears only its own account), with a total per client that no success clears.
- `ZONE_ALLOW_REMOTE` is the single knob for network access with `run.sh`: unset, the port is bound to loopback only.
- **Git calls time out** after `gitTimeout` (60s, `runGit`): a hung git counts as failed (exit code 124).
- Slugs are file paths relative to `ZONE_DATA` without `.md`: `journal/my-page.md` → `journal/my-page`; URL-safe (lowercase, hyphens, `/`).
  Subdirectories are supported; `mdtext.ToSlug()` keeps `/` and slugifies each part; `iterPages()` skips `_*` and hidden folders.
  Routes take the rest of the path as the slug (`{slug...}`).
- **Writes stay inside the wiki**: resolve the path and check it with `safePath()` (inside the wiki; it also refuses folders starting with `_` or `.`, so nothing reads or writes pages in `.zone/`, `.git/`, `_tasks/` …; any path through a symlink or by a name the file system reads as another, so the file opened is the one whose place was judged; and a name with a control character or a backslash, which would put a line break into a commit message: A name holds no line break in `docs/spec/security.md`) before reading or writing;
  create parent folders (`os.MkdirAll`);
  write UTF-8 (`writeText`).
- **A page write is an operation in `go/pageops.go`** (delete, rename, restore, create, a save, Keep mine and Set aside after a conflict, a replace across pages, a link pointed at a new page's folder, a task tag's rename;
  a note's move and append in `quicknotes.go`): it checks access itself and keeps text not yet in git (`keepPending` before a write that replaces or removes a page's text, `commitOthersDraft` before one that continues it),
  and the routes and MCP tools only parse their input and turn the result or the refusal into their answer.
  A new write gets an operation there and a test through each door in `tests/test_page_writes.py`.
  That is a convention, not a guard: nothing fails when a write has no such test, so the review has to ask for it.
  (A list's rename, `renameListOp` in `go/tasklists.go`, writes pages too: the embeds and links that name the list.)
  **Page writes run one at a time** (`pagesMu`), from the reads they decide on to their commit: the doors take it, not the operations (a route that writes pages is registered as `writesPages(handler)`, an MCP tool that does says `toolWritesPages` in `mcpToolTable`);
  code under it never takes it again (lock order `pagesMu`, the task files' lock (`taskFiles.Lock`), `commitMu`).
  Task reads don't take the task files' lock: they wait only while a write replaces its files (`taskFiles.Hold` … `Release`, Storage in `docs/spec/tasks.md`), so code that writes a task file itself calls `taskFiles.Hold()` first, and a section under `taskFiles.Lock` calls only the store's `…Locked` readers.
  Pages are written atomically (`writeText` → `atomicfile.Write`: a link written through, a new file 0666 less the umask).
  `gitCommit` commits only the paths it is given (not what `.gitignore` leaves out) and returns an error, which a write returns (`notCommitted`; a `keepPending` that fails stops the write); links a move couldn't re-point are no failure (`linksLeft`: logged, the MCP tools say so).
- **The wiki's files have a format**, the number in `.zone-format` at the wiki's root, in git (The data format in `docs/spec/deployment.md`): Zone refuses a wiki of a newer format than its `wikiFormat` (`go/wikiformat.go`).
  A change to what Zone keeps in git that an older Zone would misread raises `wikiFormat`, with a step at start that takes the format before over and writes the new number into its own commit (`writeWikiFormat`), so a revert takes back both; readers stay lenient all the same.
- `index.md` is the home page and must always exist (Zone creates it at startup when missing).
  It can't be deleted or renamed via the UI or MCP.
- A rename re-points every link to the page, on pages and in tasks, in one commit (Rename in `docs/spec/editor.md`).
