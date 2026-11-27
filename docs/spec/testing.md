# Tests

Part of Zonewiki's spec (see `AGENTS.md` for the index).

How the suite is set up, which tests guard against a forgotten step, where each area's tests live and how the suites run.
The rules (run it, add tests with every change) are in `AGENTS.md`.
What a test checks is said by its name and its docstring: this file names modules, not cases.

## Harness

### Fixtures and helpers

`tests/conftest.py` holds the shared setup: every test gets a fresh temp wiki (`wiki_dir`: just `index.md`, `_assets/` and `_tasks/`; `git_wiki_dir`: the same set up as a git repository, as Zonewiki does at start; `client`, a server on it),
plus helpers that modules import from `tests.conftest`: `HOST`,
`write_page`,
`create_task`,
`make_list` (a task list, made as New list… makes it) and `list_page` (`write_page`, plus the lists the page's `[[tasks: list=…]]` embeds show, made through the browser's routes with the embed's list settings, and pinned as a `pin=` there says: a page no longer makes or pins a list, so `pin=` in a test's page is this helper's to read, not Zonewiki's; the browser tests have both in `tests/e2e/conftest.py`, taking the page),
`content_hash`,
`section_fields` (the editor's hidden fields for a section, read from its `/edit` page),
`preview` (markdown as `/api/preview` renders it),
`rendered` (a page's article),
`backlinks` and `task_backlinks` (a page's link sections).
The browser tests' `wiki` fixture builds on `git_wiki_dir`.

### The server under test

`tests/goserver.py`: the binary is built once per run (`go build` in `go/`, by pytest's first process, which hands the path to the xdist workers in `ZONE_TEST_BINARY`).
The `client` fixture is a `GoClient`: it takes the calls of Flask's test client (get/post/put/delete with headers, data, json, query_string; cookies) and sends them over HTTP to a server started on the test's wiki,
answering with werkzeug's `TestResponse` (`get_json()`, `get_data(as_text=True)`, `status_code`, `headers`).
The browser tests' `wiki` fixture starts one on a port fixed for the session.
That port is below the range the kernel hands out for port 0, in a block of the worker's own (`session_port` in `goserver.py`: `gw3` has 20300–20399):
between two tests it is free, and a port taken by another server, another worker or Chromium would fail every test the worker has left.

The settings a test changes in `tests/settings.py` (`monkeypatch.setattr(settings, "PASSWORD", …)`: `HOSTS`, `LOCALE`, `NOSHARE_TAGS`, `GIT_ENABLED` (a `PATH` without git) …) reach the server in its settings file,
`.zone/config` (`config_text` in `goserver.py`: the settings that differ from Zonewiki's defaults, passwords and tokens as their hashes, then `settings.CONFIG_FILE`, more of the file's text, then the users as `[user …]` sections;
written before the server starts, and removed once the settings give none),
the deployment's (`ZONE_ALLOW_REMOTE`, `settings.ENV`) as its environment): when they change,
it restarts on the same wiki (and port) before the next request;
the browser tests' server restarts the moment a test sets one (a `__setattr__` on the module), since requests made with `page.request` don't pass `_new_page`'s route.
`settings.USERS` maps a name to its password and token, `settings.USER_SETTINGS` a name to more of its section (`role`, `disabled`);
`goserver.py` writes their hashes into the settings file (`password_hash`: a fixed salt and few iterations, so the server's settings stay the same between requests and logins are quick).
The tests' server has `no-login = yes` (`settings.NO_LOGIN`), so the first-run page doesn't stand in the way.
Zonewiki looks at the settings file at most once a second, so a test that rewrites it while the server runs waits that out (`write` in `tests/test_config.py`) rather than polling logins, which the throttle would count.

Zonewiki's docs folder (`settings.DOCS`, which `TestZoneHelp` points elsewhere) reaches the server as `--docs`;
the semantic index (`settings.SEMANTIC`, a `tests/semantic.py` `FakeIndex`: words mapped to a few "concept" axes, so no model) as `--semantic-test`: the server gets the fake concept embedder and floors (`go/embedtest.go`), syncs once at start when the test called `sync()`, and restarts after each `sync()`, so it sees the wiki as it is then.

A server makes the wiki a git repository at start (`gitSetup`), so every module's wiki is a repository, and `git_setup(tmp_path_factory)` (a server started and stopped) is how a test "restarts Zonewiki" (start-up commits, `index.md`, the yearly archives).
`settings.GIT_ENABLED = False` gives the server a `PATH` without git, for the fallbacks; the git tests skip when git is not installed.

### Other test doubles

- Mail: `FakeSMTP` in `tests/test_signup.py` is an SMTP server in the test (`ZONE_SMTP_TLS=none`); `tests/test_task_notify.py` uses it too.
  Task mail is sent in the background, so `mails(smtp, n)` waits for it.
  The Go side has `fakeSMTP` in `go/mail_test.go`.
- A failed commit: `tests/test_groups_own.py` forces a rename to fail part way with a `pre-commit` hook in the wiki's repository that refuses a commit holding a group's page (`no_commits_of_the_groups_pages`).
- `run.sh` without an engine: `tests/test_run_sh.py` puts a stub `podman` first on `PATH` (it logs its arguments; `run`/`start` exit with `FAKE_EXIT`), in a scratch git repo holding copies of `run.sh` and `.githooks/pre-commit`.
  Its hook tests skip when a local gitleaks is on `PATH` beside bash; one in a folder of its own (the tools image's) is left off the scratch repo's `PATH`, so they run there and in CI.
- A socket handed over as systemd would: `tests/test_socket.py` holds the socket itself and starts Zonewiki with `--addr fd:N` (and `ZONE_IDLE_EXIT=1s`).
- Node: `tests/test_tasks_core.py` and `tests/test_text_parity.py` run `public/tasks-core.js` and `public/js/mdtext.js` under Node (`node -e`) and skip without it; the tools image installs Ubuntu's `nodejs`.
- The clock in the browser: Playwright's clock moves time where a test waits for a minute to pass (`test_tasks_show_what_someone_else_changed_within_a_minute`).

### Browser tests (`tests/e2e/`)

End-to-end tests drive the real app in headless Chromium via Playwright.
They run in the tools image (`Containerfile.tools`, based on Microsoft's Playwright image; it also has Go, Node, gitleaks, and pngquant for the README's screenshots) so the app image stays browser-free and the app still has no build step or npm.
`./run.sh e2e` builds that image (cached) and runs them in parallel (Suites and runs, below) with the repo bind-mounted; extra args pass through to pytest (e.g. `./run.sh e2e -k tasks`).
Without Playwright installed they skip, so plain `pytest tests/` is unaffected.

- `conftest.py` starts a server per test on a fresh temp wiki with its own git repo (on a port fixed for the session); each test gets a fresh browser context.
  Dates in the task app's date picker go through `pick_date(picker, d, tap=False)`: the tests take dates from the real clock (today + N), and the picker opens on the month of the date it holds, so near a month's end the date can be a page before or after.
  Tests of how the editor behaves over a slow connection (`test_editor.py`'s `slow_saves`) put a small proxy between the browser and the server that answers auto-saves 2.5 seconds late.
  A request to any other host is aborted (offline, deterministic) and fails the test: Zonewiki loads nothing from a CDN.
  Uncaught page JS errors fail the test.
- A test that never ends: under load Playwright's driver or the browser sometimes dies (`new_page` fails with "Target crashed" or "Connection closed while reading from the driver"), and the next call of its sync API then waits forever, which nothing inside the test can interrupt.
  So `conftest.py` stops the process of a test that runs longer than `TEST_TIMEOUT` (120 s; the slowest takes under 10), setup and teardown included (`pytest_runtest_protocol`), after writing which test it was and where every thread stood.
  With pytest-xdist that process is one worker: the run reports the test as failed ("worker crashed while running …") and goes on with a new worker, instead of standing still until someone stops it.
  Such a failure says the machine was short of CPU or memory, not that Zonewiki is wrong: run the test again by itself.
- A page is ready before a test acts on it: `page.goto`, `page.reload` and `page.wait_for_url` return only once Zonewiki's code has started on the page and every task list that started shows its tasks (`_wait_when_there`, `READY_JS` in `conftest.py`).
  Before that a click does what the plain HTML does (a dialog's form posted as it is, its error in the URL) and a task list has no cards, which on a busy machine is what a test met.
  The page says so itself: `<html data-ready>` once the entry module `zonewiki.js` has run, `data-loaded` on a `.tasks-app` once it has drawn its tasks (`fetchTasks` in `js/taskdata.js`; on Closed once those are there); only the tests read the two.
  A page without Zonewiki's script (a download, an error page) counts as ready, once it is read to its end: `wait_for_url` returns when the address has changed, before the new page's script tag is there to be found.
  `ready=False` on any of the three returns as Playwright does, for a test that keeps the script from running or looks at the page while it loads.
  After a navigation none of them started (a link clicked, a form that leaves the page), `wait_ready(page)` waits the same way where the next step needs it.
- Waiting: for what the page shows, `expect(…)`, which waits by itself, up to 15 s (`WAIT_MS`; Playwright's own 5 s ran out on a machine busy with other work).
  A passing test waits no longer for that; only a failing one takes so long to say so.
  An action whose result the app takes back is a `click()`, not a `check()`: `check()` fails when the box is unticked again before it looks.
  The page's address is asked with `expect(page).to_have_url(…)` / `not_to_have_url(…)`, not read from `page.url` right after the page changed it itself (`history.replaceState`): `page.url` is what Playwright last heard, which can be a moment behind what the page already shows.
  For what it can't watch (the API's answer, a file on disk, a request a route held back), `wait_until(page, predicate)` from `conftest.py`, which looks every 100 ms (as long as `expect`) through the page, so the page's routes and events go on meanwhile (`time.sleep` would stop them).
  A fixed `page.wait_for_timeout(…)` is only for showing that nothing happens (no request went out, a card stayed where it was) and for the pace of a gesture (a touch held, a drag's steps): never for something that is about to happen.
- The page catches up with the server: Zonewiki writes before it answers, so a test that saw a change in the API or on disk could act on a page that hadn't had the answer yet (a menu still offering Follow, a dialog opened from the task as it was, a box ticked and the page reloaded before its request went through).
  So `wait_until`, `page.goto` and `page.reload` first settle (`settle(page)` in `conftest.py`): they wait until the page's own fetch and XHR requests of its current document have their answers and the page has handled them, and fail naming a request that never ends.
  A test that holds a request back in a route passes `settled=False` (to `wait_until`, `goto` or `reload`).
  A request of a page that is gone is not waited for: the harness forgets a document's requests when the next one is asked for and again when it is there, since a page sends on until then (the uncommitted count, asked while the browser was sent to another host, never said it ended).
- Where things are: a test measures with `box_of(locator)` from `conftest.py`, never with Playwright's `locator.bounding_box()` (a guard fails for one: Guards, below).
  `box_of` first calls `still(page)`, which settles, then waits for the fonts (the serif heading font moves what is under it when it arrives) and for transitions and animations to end (on a phone the uncommitted badge comes with its request's answer and moves the page down).
  It never returns `None`: a list drawn again by a late answer replaces its cards, and a card found before and measured after had no box, which a busy machine met as `'NoneType' object is not subscriptable`; an element that isn't shown fails in `box_of`, by its name.
  `box_of(locator, moving=True)` measures at once, for a test that looks while something moves, or that stopped the page's clock or holds a request back (`still` would wait for what the test stopped).
  A pointer is put where a thing is, so the same holds before a `hover()` on what CSS shows only under the pointer: `still(page)` first.
  And before a size read in the page itself (`clientWidth`, `scrollWidth` in an `evaluate`), which `box_of` doesn't see: `still(page)` first.
  What fits itself to its room does so a frame after the room changed (the breadcrumb's trail, by a `ResizeObserver`, when the uncommitted badge arrives), and a size read in between is the old layout in the new room.
- A view opened is there: `open_tab(page, view)` returns once the view has what it asked the server for (it settles).
  Completed loads after its link's click and draws the list again; `data-loaded` is from the page's load and says nothing of it.
  A test that looks between two steps of a gesture that a timer ends (the open card closing 400 ms after a press on another) stops the page's clock meanwhile (`page.clock.pause_at`, then `resume`), or a slow machine runs the timer out between the looks.
- The pace of a touch: a swipe or quick move starts with `quick_touch(page, cdp, start, first_move)`, which sends the finger down and its first move again when this process took longer between them than a long press would allow (`LONG_PRESS_MS`): a busy machine otherwise turned the swipe into a drag.
- No test is run again when it fails (no rerun plugin): a test that fails now and then waits for the wrong thing, and a rerun would hide that.
  Such a test is found, and its fix shown to hold, with `./run.sh flaky` (A test that fails now and then, below).
- Touch: `new_page(**TOUCH_PHONE)` gives a phone context with touch input, `hover: none` and `pointer: coarse` (use `.tap()`).
  Long-press drags are sent as CDP touch events.
  Full-page screenshots of such a context lose the touch/hover emulation (Playwright re-renders with changed device metrics), so assert on computed styles/sizes rather than on a full-page image.
- `test_screenshots` writes full-page PNGs of each view at phone/desktop × light/dark to `tests/e2e/_screenshots/` (gitignored) for visual review.
  It asserts nothing about appearance.
- Playwright's HTML5 drag cannot auto-scroll mid-drag, so drag tests must keep source and target on screen (e.g. enlarge the viewport).
- Keep the `playwright==` pin in `tests/requirements.txt` equal to `Containerfile.tools`' image tag version.
- Content-Security-Policy: Zonewiki's pages run under `script-src 'self'` (The page policy in `docs/spec/security.md`).
  The harness collects the violations Chromium reports on the console (`page.csp_violations`) and fails a test that has any, so new code can't bring in an inline script or handler unnoticed; a test that blocks one on purpose (`test_security.py`) clears the list.
  Under that policy Playwright can't `eval` in the page: `page.wait_for_function` needs a function (`"() => scrollY > 0"`), as an expression string is evaluated with `eval`; `page.evaluate` works either way.

## Guards

Tests that fail when a change forgets a step elsewhere: a new route, tool, setting, field or dependency that the rest of the suite or the docs don't know of yet.

| Test | Enforces | When it fails |
|------|----------|---------------|
| `test_every_route_is_covered` (`tests/test_privacy.py`) | Every route Zonewiki registers is probed for leaks of another user's private page and tasks, or exempt | Add a probe to `READS` or `WRITES` (or, with a reason, to `EXEMPT`) |
| `test_every_tool_is_covered` (`tests/test_privacy.py`) | Every MCP tool is probed the same way | Add a probe to `MCP_READS` or `MCP_WRITES` |
| `tests/test_group_pages.py` | The privacy probes, aimed at a group's folder, run as an editor and an admin outside the group | Nothing to add: it takes `test_privacy.py`'s probes; fix the leak |
| `tests/test_group_lists.py` | A group's list leaks nothing to non-members on the routes that show or write tasks and lists | No completeness check: add a probe to `READS`/`WRITES` for a new task or list route by hand |
| `tests/test_narrowed.py` | The MCP probes of the three modules above, asked by the owner through a connection without the place, answer as to a stranger | Fix the door that asks a name instead of the asker (`go/access.go`) |
| `test_every_write_route_is_probed` (`tests/test_roles.py`) | Every write route is refused to a viewer with nothing changed or committed | Add it to `WRITES` (or `EXEMPT`); a viewer's write goes in `viewerWrites` |
| `test_every_write_tool_is_probed` (`tests/test_roles.py`) | Every writing MCP tool is refused to a viewer | Add it to `MCP_WRITES` |
| `test_the_lists_are_the_servers` (`tests/test_public.py`) | The test's `VISITOR`/`EDITS` equal `visitorRoutes`/`visitorEdits` in `go/server.go`, and name routes that exist | List a new visitor route in both, after checking it shows no task, note or private page |
| `test_every_route_is_a_visitors_or_closed` (`tests/test_public.py`) | Without a session every route sends to the login page unless it is a visitor's (or needs no login) | Close the route, or list it as above |
| `tests/test_task_app_wiring.py` | Every `app.name` a task-app part uses is put on `app` by `tasks.js` or returned by a part, none twice | Fix the misspelled or moved name, or return it from its part |
| `TestAskersAreMadeInFewPlaces` (`go/asker_test.go`) | Only the listed functions call `asUser`; no `asUser(user.Name)` (or `rq.user.Name`, `userOf(r).Name`); an `asker{…}` written only where a connection's is made | Pass the request's asker; a deliberate new `asUser` goes on the list after a look at where its name comes from |
| `TestWhatIsTheirsIsAskedInOnePlace` (`go/asker_test.go`) | Owners and group membership are asked of an asker only in `go/access.go` | Ask `place.own`/`sees`, `canSeeTask`, `canSeeList`, `inGroup` or `groupsFor` instead |
| `TestSame` (`go/internal/taskmodel/task_test.go`) | `Task.Same` notices every field of a task | Give a new field a case there (and in `Same`) |
| `TestRetrievalHoldsItsFloor` (`go/eval_test.go`) | Keyword search on `tests/retrieval/` finds exactly what `floors.json` says, no less and no more | Fix the regression, or raise the floor in the same commit with a gain; never tune the sample wiki |
| `TestGolden` (`go/golden_test.go`) | The Go text functions give `go/testdata/golden.json.gz`'s answers | After a deliberate change, update the file (Shared case files) |
| `tests/test_text_parity.py` | The browser's copies in `public/js/mdtext.js` give the same answers | Change the JavaScript along with the Go |
| `*_cases.json` runners (Shared case files) | The server's and the browser's task rules agree on one table | Change both sides, add the case to the table |
| `tests/test_image.py` | Base images and CI actions pinned by hash; the functions CodeQL is told are Zonewiki's checks exist (`.github/codeql/`); the model's revision and hashes are `go/embed.go`'s; both images build with the same Go and fetch the modules with the root's `go.mod`; the build stage copies what `zonefiles.go` builds in, and the image no copy beside the binary; the image ships the licenses; a `v*` tag releases the four binaries `./run.sh binaries` builds; `THIRD_PARTY_LICENSES` names what is pinned; every `public/vendor/` library has a `LICENSE`; Mermaid's notices match the vendored bundle | Update the pin, hash or notice that drifted (Dependencies in `AGENTS.md`; for Mermaid `python3 docs/licenses/mermaid_notices.py VERSION`) |
| `tests/test_env_example.py` | `.env.example` lists every setting the source, `run.sh` and the compose files read, and nothing else | Add the new variable to `.env.example`, or to `INTERNAL` if no user sets it (run, build, test or bench only) |
| `TestUsage` (`tests/test_routes.py`) | `zonewiki --help` names its commands, environment and flags (with two dashes); each command answers `--help` with exit 0 and without a wiki | Fix the usage text; a new command goes in the test's list |
| `tests/test_e2e_harness.py` | No browser test calls `locator.bounding_box()`: `box_of` in `conftest.py` is the one place | Measure with `box_of(locator)` (`moving=True` while something moves) |
| Browser harness (`tests/e2e/conftest.py`) | No request to another host, no uncaught JS error, no CSP violation | Load from `public/vendor/`, fix the error, move inline script into Zonewiki's script files |

`TestWhatIsTheirsIsAskedInOnePlace` is a regex over the lines of `go/*.go`, so it catches only those written its way: `owner == user.Name` or `user.Name != t.Owner` (a name ending in `owner`/`Owner`, `==` or `!=`) and `memberOf(…)`/`groupsOf(…)`, each with the asker named `user`, `rq.user`, `rq.who()` or `userOf(r)`.
It misses an asker under another name (`u.Name`, `who.Name`), its name copied first (`name := user.Name`), another way to compare (`slices.Contains(owners, user.Name)`, `strings.EqualFold`, a `switch`), another way into the settings (`s.Users[user.Name].Groups`, `groupMembers`), a line broken in two, and the packages in `go/internal/`: a review still has to look.
`TestAskersAreMadeInFewPlaces` reads the source with `go/ast`, so it is not fooled by formatting.

## Where tests live

Kinds: **Go** (`go test`, inside the server), **API** (pytest over HTTP against the real server), **browser** (Playwright), **Node** (pytest running browser code under Node).
Prefer an API test when the behaviour shows through a route, a Go test when it doesn't.

### Go

| Module | Spec | Covers |
|--------|------|--------|
| `go/access_test.go` | users.md, mcp.md | The permission rules: `canSee`, `canSeePath`, `hiddenPage` with sharing mode, `strayPrivate`, group folders, narrowed and read-only askers |
| `go/asker_test.go` | security.md | Guards (above); `TestAnAskerIsWrittenAsItsName` |
| `go/assignee_test.go` | tasks.md, users.md | `assignable`, the reserved names in `zonewiki user add` and an invite, the open tasks `user disable`/`remove` count |
| `go/bench_test.go` | deployment.md | Benchmarks behind How big a wiki can be (Suites and runs) |
| `go/configfile_test.go` | users.md | The settings file: parser errors, keys and checks, reload, line edits, 0600 write, `zonewiki user`/`group`/`config` on a temp wiki, roles and links |
| `go/embed_test.go` | search.md | The semantic index: chunking, its cache, failure fallback, the worker, rank fusion, the model's download |
| `go/eval_test.go` | search.md | `zonewiki eval`, its report and `--new`; the retrieval floor (`TestEvalFloors`, `TestEvalCommandHoldsAFloor`, `TestRetrievalHoldsItsFloor`) |
| `go/files_test.go` | deployment.md | The binary's own files: all of `public/` in it, served as the folder serves it (type, `Last-Modified`, gzip, 404s, no way out), a folder winning, the built-in docs for Help and `zone_help`, `public/` found only with `zonewiki.js`, `zonewiki licenses` |
| `go/gitstore_test.go` | rendering.md, users.md | Git helpers: commits, log, diffs, hung git, `TestLastChangedAsksGitOnce`, packing's schedule, drafts committed, `TestMissingUsers` |
| `go/golden_test.go` | rendering.md | The text functions against `golden.json.gz` |
| `go/importop_test.go`, `go/imports_test.go` | editor.md, security.md | A markdown import's write (`importOp`: one commit, undone whole, refused whole), its options (the folder, a guide's name refused, files' names); the waiting imports swept after their hour and cleared at a start |
| `go/lastchange_test.go` | search.md | Who changed a page last: a user, an assistant, a terminal, a visitor, a file changed since; the scan read only as far as needed, git asked again only when the repository moved, a reset |
| `go/mail_test.go` | users.md, deployment.md | The SMTP client, mail settings, `validEmail`, sign-up keys, `pending` |
| `go/markdown_test.go` | rendering.md | The renderer: checkboxes, callouts, heading ids, images, links, raw HTML defused, concurrent renders |
| `go/math_test.go` | rendering.md | The math parser and the source side agreeing with it |
| `go/mcp_test.go` | mcp.md | The protocol (versions, notifications, errors), tool definitions, cut answers, grep's timeouts, replace templates |
| `go/mdtext_test.go`, `go/text_test.go` | rendering.md | Wikilinks, code masked, task lines, checkbox numbering, frontmatter and tags, section links |
| `go/merge_test.go` | editor.md | `mergeText` and the bases kept |
| `go/oauth_test.go` | security.md, mcp.md | OAuth's signed client ids, redirect URIs, PKCE, codes; what Allow's and Change…'s choices give a connection (`chooseReach`) and which change gives it more (`less.beyond`) |
| `go/pages_test.go` | editor.md, tasks.md | Links re-pointed on a rename, folder moves, tags and lists renamed in text, `iterPages` |
| `go/retrieval_test.go` | search.md | Search's index: chunks, the cache by place (`TestIndexCacheStaysAsIfBuiltAfresh`), the index file at start, a group's pages and tasks in members' views alone, an edit in a group as a change to its index, a group's index files |
| `go/sections_test.go` | editor.md, mcp.md | Sections as the parser finds them: ✎s, `findSection`, MCP sections and search chunks, CRLF |
| `go/security_test.go` | security.md | Throttles, client key, host names, the session key, signing out, the session's user |
| `go/server_test.go` | deployment.md | Healthcheck address, Unix and handed sockets, idle exit |
| `go/stalls_test.go` | security.md | `noStalls`: a stalled body or response fails, a slow one doesn't |
| `go/storage_test.go` | security.md, users.md | Sizes, the size settings, what counts toward `storage-max`, `checkRoom` |
| `go/tasklists_test.go` | tasks.md | Lists as places, the Inbox, a start's #general, My tasks, pins, the case files, the search's task records, archives read at start |
| `go/tasklogic_test.go` | tasks.md, users.md | Per-user task files, `canSeeTask`, a shared task staying shared, the news' cases |
| `go/tasksettings_test.go` | tasks.md | An embed's parameters set by the settings dialog |
| `go/users_test.go` | users.md | The hash format, `authenticate` |
| `go/views_test.go` | routes.md | A page view and a search walk the wiki once |
| `go/wikiformat_test.go` | deployment.md, tasks.md | `.zone-format`: what it may say, a newer format refused, written once into a new wiki, which a start takes nothing over in |
| `go/internal/atomicfile/atomicfile_unix_test.go` | tasks.md | The atomic write: a new file's mode, an old one's kept, a link written through |
| `go/internal/gitrepo/gitrepo_test.go` | rendering.md | Git's time limit and context, its output streamed and stopped early, no pack inside a commit, Pack, Prune, Join, Drop, when to join |
| `go/internal/jsonobj/jsonobj_test.go` | mcp.md, tasks.md | JSON objects read a member at a time |
| `go/internal/mdimport/convert_test.go` | editor.md | Importing pages: slugs, titles, links found and rewritten, files, Obsidian's syntax, the report and index page; the sample vault and plain folder in `testdata/` against their `.golden` files (`-update` writes them) |
| `go/internal/mdtext/mdtext_test.go`, `text_test.go`, `sections_test.go` | rendering.md, editor.md | Frontmatter, lines, slugs, titles, wikilinks' parts, a section's splice |
| `go/internal/settingsfile/settingsfile_test.go` | users.md | The settings file parsed, edited keeping comments, written owner-only, group sections |
| `go/internal/taskfiles/concurrent_test.go` | tasks.md | `TestReadersBesideWriters`, the store under the race detector |
| `go/internal/taskfiles/lists_test.go` | tasks.md | The registry file: format, no version, lenient reads, owners, who may write |
| `go/internal/taskfiles/members_test.go` | tasks.md | A group's tasks in the store: in its folder, read by its members only, others' writes refused |
| `go/internal/taskfiles/taskfiles_test.go` | tasks.md | Task files on disk: filing by owner, list and month, commits, corrupt files, parsed-once reads, `TestHolds` |
| `go/internal/taskmodel/task_test.go` | tasks.md | A task read leniently and written in key order, assignees, `TestSame`, `TestKnownKeysWrittenOnce` |
| `go/internal/textsearch/search_test.go` | search.md | BM25, stemming, fuzzy, query syntax, excerpts, an index's views and layers |
| `go/internal/textsearch/difflib_test.go`, `indexfile_test.go` | search.md | Typo matching's bounds; the mapped index file, bad files, `MergeIndex` |
| `go/internal/tokenizer/tokenizer_test.go` | search.md | The embedding model's tokenizer |
| `go/internal/encoder/*_test.go`, `go/embed_model_test.go` | search.md | The embedding model run in Go: the kernels against plain loops, `exp` and GELU, a batch against each text alone, the whole network on a small made-up model against a plain reference, float16 and its widening, the safetensors file read where it is (also cut short or pointing outside itself), the weights outside Go's heap and a pass's memory reused; with the model's files, its vectors against onnxruntime's |
| `go/internal/wikiarchive/wikiarchive_test.go` | deployment.md, security.md | A wiki's archive: written and read back, what a reader refuses (names that lead out, links, devices, a file twice, no repository), what it passes by, the size |
| `go/internal/wikiarchive/unpack_test.go` | deployment.md, security.md | The unpacking a tar and a zip share: a table of hostile archives, each refused from both sources (names, links, devices, a file twice, a file and a folder of one name, the byte budget, the entries, a zip's understated size and non-UTF-8 name); a zip's keep; the size kept past the settings file |

### API (`tests/`)

| Module | Spec | Covers |
|--------|------|--------|
| `tests/test_admin.py` | users.md | The admin page's Users tab, groups of groups, invite and reset links |
| `tests/test_assistant.py` | mcp.md, security.md | Connect an assistant (`/assistant`): a token with the password, revoking |
| `tests/test_changes.py` | rendering.md, search.md | Changes (`/changes`): filters, which assistant, `source`, what it offers to undo; who changed a page last in search results |
| `tests/test_config.py` | users.md | The settings file through the server: reloads, a broken file, kept out of git |
| `tests/test_env_example.py` | deployment.md | Guard (above) |
| `tests/test_e2e_harness.py` | testing.md | Guard (above) |
| `tests/test_git.py` | rendering.md | Every git path: commit on write, history, diffs, restore, deleted pages, uncommitted, no git |
| `tests/test_group_pages.py` | users.md | A group's pages: the privacy probes, members, moves, Share…, its home |
| `tests/test_groups_own.py` | users.md | Groups made in the browser: names, owners, leaving, ending, renaming |
| `tests/test_image.py` | deployment.md, security.md | Guard (above) |
| `tests/test_group_lists.py` | tasks.md | A group's lists: the leak probes, members, moving a list to or from a group, the task's files |
| `tests/test_import.py` | editor.md, security.md | Import pages: a private, a group's and a shared import, the report, who sees a waiting import, a viewer refused, zips refused (names, not a zip, too big, a full wiki) leaving nothing, the waiting folder gone after Import, Cancel, a second upload, an hour, a restart; a note named as a guide; the import undone |
| `tests/test_list_owners.py` | tasks.md | A shared list's owners and what a member who is none may do |
| `tests/test_math.py` | rendering.md | Math over HTTP, section editing below a `$$` block |
| `tests/test_mcp.py` | mcp.md | The MCP tools over HTTP: files, guides, reading and writing tools, `zone_help` |
| `tests/test_narrow.py` | users.md | Who may move a page to fewer readers, asked first |
| `tests/test_narrowed.py` | mcp.md | Connections given less to see: the leak probes again, the consent page's boxes, a group taken away and given back with Change… |
| `tests/test_email.py` | users.md, deployment.md | A user's mail address: set with the password, confirmed by its link, no mail before, an admin's, the caps; the mail folder (`ZONE_MAIL_SPOOL`) |
| `tests/test_legal.py` | users.md | The imprint and privacy links: on the login page, for a visitor and without a login, none for the signed in or in the editor |
| `tests/test_notice.py` | users.md | The notice line: on every view, the login page and a visitor's, escaped, after a reload |
| `tests/test_oauth.py` | mcp.md, security.md | OAuth as a client plays it: registration, consent, tokens, connections, `TestReadOnly`, `TestChange` (Change…: less without the password, more with it, through the tokens it has) |
| `tests/test_page_writes.py` | editor.md, mcp.md | Each page write through both doors, Undo, tag and list renames |
| `tests/test_password.py` | users.md | Change password (`/password`) |
| `tests/test_privacy.py` | users.md | Private pages and tasks: the leak probes on every route and tool |
| `tests/test_public.py` | security.md | Public wikis (`ZONE_PUBLIC`): what a visitor reads and writes |
| `tests/test_rendering.py` | rendering.md | Rendering through the server: wikilinks, image widths, callouts, autolinks, a heading's id and its link's anchor |
| `tests/test_roles.py` | users.md | Viewers: every write refused, the interface; guides written by admins only |
| `tests/test_routes.py` | routes.md, editor.md, security.md, deployment.md | The wiki's routes: pages, save and merge, uploads, search, Host and Origin checks, body limits, `TestUsage`, the binary started on its own (`TestTheBinaryAlone`) |
| `tests/test_run_sh.py` | deployment.md | `run.sh`'s container calls and what each command says it does, in a checkout and beside `compose.yaml` alone (the prebuilt image, `update`, the checkout's commands refused, `.env`'s address and user), and the pre-commit hook, against a stub engine; the pre-push hook |
| `tests/test_search_filters.py` | search.md, navigation.md | The search's `is:` and `in:` filters through each door (`/api/search`, `/search`, `search_wiki`, `search_tasks`), that they only narrow, the Where menu's groups |
| `tests/test_section_editing.py` | editor.md | Section editing over HTTP: a stale ✎, Keep mine |
| `tests/test_semantic.py` | search.md | Semantic search through the server with the fake embedder |
| `tests/test_settings_pages.py` | navigation.md | The menu's Settings and the settings pages' tabs |
| `tests/test_usage.py` | users.md | Usage: who sees the page, each count through its door, the files in `.zone/usage/` |
| `tests/test_setup.py` | security.md | The first-run page (`/setup`) |
| `tests/test_signup.py` | users.md | Sign-up against `FakeSMTP`, approval |
| `tests/test_socket.py` | deployment.md | Zonewiki on a Unix socket and one handed over, idle exit |
| `tests/test_task_app_wiring.py` | rendering.md | Guard (above) |
| `tests/test_task_assignee.py` | tasks.md | A task's assignee through API and MCP, My tasks per user, a shared task kept in a list |
| `tests/test_task_follow.py` | tasks.md | Following a task and what a follower is told |
| `tests/test_watch.py` | navigation.md | Watching a page and a folder: what is news, looking, the count, a rename followed, what a watch tells of pages not seen, the tool `watch_page` |
| `tests/test_task_notify.py` | tasks.md | The mail about a task given to someone, the news' `seen` |
| `tests/test_task_order.py` | tasks.md | The manual order by moves: a list's and My tasks' own; one order for every owner's tasks (the top above all, room made among tasks of others) |
| `tests/test_task_refs.py` | rendering.md, tasks.md | `[[task:…]]` references |
| `tests/test_tasks.py` | tasks.md | The task API: fields, series, closed archives by month, batches, history, comments, `Zone-Tasks` |
| `tests/test_tasks_embed.py` | tasks.md | The `[[tasks:]]` embed, the row of lists, the Inbox's pill, the tasks a page brings |
| `tests/test_tasks_export.py` | tasks.md | Export and Import tasks… |
| `tests/test_tasks_settings.py` | tasks.md | The task app's settings dialog and its route |
| `tests/test_transfer.py` | deployment.md, security.md | `zonewiki export` and `zonewiki import`: a wiki moved with its history and users (who sign in, private pages still private), the settings left behind, `--users-only`, `--max-size`, formats newer and older, an archive's hooks, git config, work tree and links not used, an import that didn't finish |
| `tests/test_users.py` | users.md | Users: sign-in, tokens, commit authors, drafts, disabled users, the private home, Make shared |
| `tests/test_tasks_core.py` (Node) | tasks.md | `public/tasks-core.js`: dates, filter, order, `@` words, the case files |
| `tests/test_text_parity.py` (Node) | rendering.md | Guard (above) |

`tests/bench_tasks.py` and `tests/bench_import.py` are no test modules (Suites and runs).

### Browser (`tests/e2e/`)

| Module | Spec | Covers |
|--------|------|--------|
| `test_admin.py` | users.md | The Users tab's dialogs and buttons |
| `test_changes.py` | rendering.md | Changes found, filtered, leading to a diff, on a phone |
| `test_watch.py` | navigation.md | Watch in the page menu, the dot and the count, a change marked new and read |
| `test_editor.py` | editor.md | Auto-save, Done, preview, list continuation, the Task list dialog, slow saves |
| `test_figures.py` | rendering.md | Math and Mermaid from the vendored copies |
| `test_fold.py` | rendering.md | Folding sections |
| `test_import.py` | editor.md | Import pages from the search list: a zip uploaded, its report, Import, the folder's index, undone from Changes |
| `test_format_bar.py` | editor.md | The formatting bar |
| `test_groups.py` | users.md | Groups: Share…, the switcher, making and renaming, name pills, the narrowing question |
| `test_layout.py` | routes.md | Side columns, no horizontal scroll at phone width, copy-link icons |
| `test_merge.py` | editor.md | Two editors on one page: merges and the conflict banner |
| `test_mobile_nav.py` | navigation.md | Modules, the tab bar, top actions, the account menu on a phone |
| `test_oauth.py` | mcp.md | OAuth from login to Allow under the page policy; Change…'s dialog and its password field |
| `test_print.py` | rendering.md | Printing and PDF |
| `test_private.py` | users.md, mcp.md | The user menu, private home, Make shared, Change password, Connect an assistant |
| `test_public.py` | security.md | A public wiki in the browser |
| `test_quiet_tab.py` | navigation.md | A tab nobody is at asks nothing until the pointer moves |
| `test_search.py` | navigation.md, search.md | The search modal (its filters, a long file name), Notes filter, the semantic mode indicator |
| `test_security.py` | security.md | What only a browser shows: CSP, `srcdoc`, `ping`, framing, ids as text |
| `test_sharing.py` | navigation.md | Sharing mode |
| `test_shortcuts.py` | navigation.md | The shortcuts dialog: fits a desktop screen, scrolls inside a phone's, names every key the code answers |
| `test_tables.py` | rendering.md | Sortable tables |
| `test_task_assignee.py` | tasks.md | Assignees, My tasks, news and following, a group's list's dialogs |
| `test_task_inbox.py` | tasks.md | Filing Inbox tasks, Leave out of My tasks, where Delete… sends tasks |
| `test_task_refs.py` | tasks.md | Ticking task chips, completion, Make task |
| `test_tasks.py` | tasks.md | The task app: views, boards, selection, drag, lists' dialogs and pills, long lists |
| `test_touch.py` | tasks.md, editor.md | The task app and the editor on a touch phone |
| `test_usability.py` | tasks.md, editor.md | Filter field, phone toolbars, card swipes, a checkbox's undo |

## Shared case files

Some rules are written twice, once in Go and once in the browser's JavaScript; one file of cases runs through both, so they can't drift apart.
A case added for one side is a case for both.

| File | Server side | Browser side |
|------|-------------|--------------|
| `go/testdata/golden.json.gz` | `TestGolden` (`go/golden_test.go`) | `tests/test_text_parity.py` (`public/js/mdtext.js`: slugs, heading ids, a wikilink's parts, the checkboxes of a text and a tick (the browser's alone: no Go function gives them now), a query's words and its `is:`/`in:` filters, a list's name, a date word) |
| `go/testdata/mine_cases.json` | `TestInMineCases` (`go/tasklists_test.go`) | `TestMyTasks` (`tests/test_tasks_core.py`) |
| `go/testdata/news_cases.json` | `TestNewsCases` (`go/tasklogic_test.go`) | `TestNewsCases` (`tests/test_tasks_core.py`) |
| `go/testdata/filter_cases.json` | `TestFilterCases` (`go/tasklists_test.go`) | `TestFilterCases` (`tests/test_tasks_core.py`) |
| `go/testdata/focus_cases.json` | `TestFocusCases` (`go/tasklists_test.go`) | `TestFocusCases` (`tests/test_tasks_core.py`) |

`golden.json.gz` holds the text functions' answers on every string in the tests from when Zonewiki was ported from Python (recorded from the Python Zonewiki) and on edge cases added since.
After a deliberate change to one of those functions, `ZONE_GOLDEN_UPDATE=1 go test -run TestGolden` (in `go/`) logs every case that changed and writes the new answers; then make the JavaScript give them too.
The `*_cases.json` files are edited by hand: add the case, then change both sides until both pass.

## Suites and runs

### Commands

| Command | Runs |
|---------|------|
| `./run.sh test` | gofmt, go vet, `go test -race ./...`, then the API tests (pytest), in the tools image with the repo mounted; extra args go to pytest |
| `./run.sh e2e` | The browser tests and the Node tests (`tests/test_tasks_core.py`) in the tools image |
| `./run.sh flaky [N] [pytest args]` | The chosen browser tests N times each (20) on a machine made busy |
| `./run.sh bench` | `tests/bench_tasks.py` with its budgets, then against a race-built server; then `tests/bench_import.py` with its budgets |
| `./run.sh retrieval` | Search on `tests/retrieval/` by keywords and by meaning, in the app's image with the model |
| `./run.sh leaks` | gitleaks over the git history |
| `(cd go && go test ./...)` | The Go tests with a local Go |
| `pytest tests/ --ignore=tests/e2e` | The API tests with a local Go and `pip install -r tests/requirements.txt` |

`./run.sh test` leaves the Node tests to `./run.sh e2e`.

### Parallelism

Both `./run.sh test` and `./run.sh e2e` run in parallel with pytest-xdist: `test` with `-n auto` (a worker per CPU), `e2e` with a worker per two CPUs (each runs its own Chromium; at one per CPU the browsers starve and timing-sensitive tests fail).
An `-n` in the extra args overrides it (`-n 0`: one process, e.g. to debug one test).
Workers are separate processes: each has its own temp wikis, servers (`base_url`) and browser, so the fixtures need no locking; a test must keep its files under `tmp_path`.

### A test that fails now and then

A browser test that fails once in fifty runs fails on a busy machine: a CI runner with a second run beside it gives a browser a third of the CPU it has alone, and what was drawn in one frame takes several.
`./run.sh flaky [N] [pytest args]` makes the machine that busy on purpose: the chosen tests N times each (20 without a number; `ZONE_E2E_REPEAT` in `tests/e2e/conftest.py`), a worker per CPU (twice `e2e`'s) and four busy loops per CPU beside them.
`./run.sh flaky 50 -k one_line_per_task` before a fix shows the failure and how often it comes, and after it that it is gone; without `-k` it is the whole suite N times, which finds the tests nobody suspects.
A test that fails there waits for the wrong thing (Browser tests, above): fix what it waits for, never the number of tries.

### The race detector

**The Go tests run under the race detector** (`CGO_ENABLED=1 go test -race ./...` in `run.sh`): two requests that read and write the same memory without a lock fail the run, where they would otherwise be a wrong answer once in a while.
The detector needs cgo, so the tools image has `gcc` and `libc6-dev`; it sets `CGO_ENABLED=0` for everything else, as the image and the release binaries are built.
The image also sets `GOEXPERIMENT=simd` (Go's vector arithmetic, which runs semantic search's model), and `run.sh test` runs `go/internal/encoder`'s tests once more without it, as a plain `go build` has them.
What it can see is what the tests run at once: `TestReadersBesideWriters` in `go/internal/taskfiles/concurrent_test.go` is the task store's test for it (writers that change, close, reopen and move tasks between lists, and one that changes five at once as a batch does, beside readers of every kind: each reader sees every task once, and a reader's change of what it was handed is nobody else's).

**The server itself under the detector**: `ZONE_TEST_RACE=1 ./run.sh test …` builds the server the API tests start with `-race` (`tests/goserver.py`), and a race ends that server (`GORACE=halt_on_error=1`), so the test that met it fails and the server's log says where.
It is several times slower, so it is no part of a plain run: run it with the bench (`ZONE_TEST_RACE=1 ./run.sh test tests/bench_tasks.py -n 0 -s`, whose budgets it then passes over) after a change to what requests share (the task store's locks, the kept counts, the search's records).

### The retrieval floor

`tests/retrieval/` holds a sample wiki, questions and the least search must find for them (its `README.md`; Measuring retrieval in `docs/spec/search.md`).
`TestRetrievalHoldsItsFloor` (Go, in `./run.sh test`) runs `zonewiki eval --floor` on a copy of it by keywords, and fails under the floor or above it (a gain is kept by raising the floor in `floors.json` in the same change).
`./run.sh retrieval` measures the hybrid ranking too, in the app's image with the model; it is CI's `retrieval` job and no part of the suites.
`tests/test_run_sh.py` holds `./run.sh retrieval` to the app's image built with the model and the three files.

### The bench

`tests/bench_tasks.py` measures the tasks at a team's size and is no part of the suites (its name is no test module's): `./run.sh bench`, which is also a job of Forgejo's CI, not GitHub's (or `./run.sh test tests/bench_tasks.py -n 0 -s` for the table alone).
`./run.sh bench` runs it twice: with its budgets, and then against a server built with the race detector (`ZONE_TEST_RACE=1`, without budgets), which is the only run of several clients at once on the real server under the detector.
`ZONE_BENCH_SIZE=small|team` picks the wiki's size (team by default).
It prints the table and fails where a number is over its budget (`BUDGETS`): run it before and after a change to how tasks are read, written or sent.
The numbers are the server's alone, over HTTP on loopback; no browser draws anything.

**A budget guards a design, not a millisecond**: a time's budget is about twice what the tools image measures on a developer's machine (the measure is beside it in the file), so a busy machine passes, and a read that waits for a commit again or a write that reads the wiki again does not.
`ZONE_BENCH_SLACK` multiplies the time budgets, for a slower machine: CI runs with 3.
Sizes and counts are the same on any machine and have no slack.

Its parts:

- **Tasks**: a generated wiki in git (100 users with ten Inbox tasks each, 40 shared lists, 5,000 open and 25,000 closed tasks, most assigned), signed in as one user, the median over HTTP of: a page without tasks, My tasks, one list, All tasks (time and size), the due count, a task changed, a task completed, My tasks reordered, ten tasks tagged at once, a search right after a write.
  The repository's growth per 100 writes is measured three ways: as the commits leave it, packed in steps as Zonewiki packs every 50 commits, and joined as Zonewiki joins its packs (with how long the join takes).
  It also reads the Zonewiki process's resident memory at the end.
- **Reads beside writes** (`test_reads_beside_writes`): six clients who open a page without tasks, My tasks and the due count without a pause, and four who each change a task four times a second, for fifteen seconds; the 95th percentile and the slowest of each.
- **A write beside a join** (`test_a_write_beside_a_join`): the repository is given a history first (20,000 commits to 40 files of tasks, with `git fast-import`, and 55 small packs after it, so that Zonewiki's next pack is followed by a join), then a task is changed again and again while Zonewiki joins the packs; the slowest write has a budget of 250 ms.
- **Pages and groups** (`test_pages_and_groups_at_a_teams_size`): 2,000 shared pages and 200 in the folder of each of ten groups (300 words a page), every user in one group and every fifth in two, a group made of three others, and a guide that pulls a page in.
  It measures a group's page, the page list, a search by the same person again, a search by thirty people in turn, and thirty people who search twice a second at once for ten seconds, with a page opened ten times a second beside them (a page view asks whether its reader may change it, which walks the wiki's guides at most once a second).
  The two searches by thirty guard that nothing is made per searcher (Nothing is kept per viewer in `docs/spec/search.md`).
- **A large group** (`test_a_large_group`): 2,000 shared pages and a group's folder of 2,000, a member and someone outside it.
  It measures a member's search after an edit in the group's folder, and after a start the first search of someone outside the group, of a member and of a member with nobody before:
  they guard that an edit in a group costs its one page and that a start reads the group's index from its files (A group's index files in `docs/spec/search.md`).
- **A big vault imported** (`tests/bench_import.py`, its own module, run by `./run.sh bench` after the tasks): a generated Obsidian vault of 5,000 notes in 50 folders (linked to each other by name, some by a heading, with tags, callouts, task lists, frontmatter) and 500 pictures of 60 KB (random bytes, as a photo's are to zip), zipped, uploaded through Import pages and imported.
  It measures the upload and its report, Import, the files in its commit and how the repository grew, the folder's index and the home page after it, the first search (5,000 pages indexed) and one after it, the page list, the server's memory at most and after, the import undone and that undo undone.
  `ZONE_BENCH_SIZE=limit` (not CI's) runs it near the zip's limit of entries, 18,000 notes and 1,800 smaller pictures, for the numbers behind the limits (What an upload may be).
- **A long history** (`test_a_long_history`): a wiki given 100,000 commits before its server starts (with `git fast-import`: pages in folders edited, made and deleted, task-like files changed more often, a page renamed twice), so the start writes its commit-graph (The commit-graph in `docs/spec/deployment.md`).
  It measures how long that takes, a page's history, a renamed page's, a link to a page that never was (Zonewiki asks whether it was deleted) and the deleted pages a second time (the first look, which reads every commit, is printed).
  A history read with `--follow`, or without the graph, passes the budgets many times over.

### Go benchmarks

`go/bench_test.go` holds the benchmarks behind How big a wiki can be in `docs/spec/deployment.md` (a page view, with tasks, `GET /api/tasks`, the page list, who changed a page last).
**Nothing runs them**, neither `./run.sh test` (`go test` runs no benchmark without `-bench`) nor CI, so their numbers are as old as that table: `go test -run '^$' -bench . -benchtime 20x` in `go/`.

### CI

Forgejo and GitHub run the same `run.sh` commands in the image `Containerfile.tools` builds (CI in `docs/spec/deployment.md`): the checks (`test`, `e2e`, `leaks`), the retrieval floor (`./run.sh retrieval`), and the image;
the bench (`./run.sh bench` with `ZONE_BENCH_SLACK=3`) only Forgejo, on the same runner every time (Forgejo's jobs in `docs/spec/deployment.md`).
A dependency or a test command changes in one place, `run.sh` or the tools image.

## Secret scanning

No secret goes into the repo (No secrets in the repo in `docs/spec/security.md`): `.env` (tokens, password) is gitignored, and gitleaks looks for the rest.

- **The pre-commit hook** `.githooks/pre-commit` (on once per clone: `git config core.hooksPath .githooks`) runs gitleaks over the staged changes, with a local gitleaks or else `./run.sh leaks --pre-commit --staged` in the tools image; a leak (exit 3) refuses the commit, a scan that can't run only warns.
  For `commit -a` and `commit <path>` git hands the hook a temporary index (`GIT_INDEX_FILE`, a host path); `run.sh leaks` passes it into the container as a `/repo/…` path, so those changes are scanned too (an index outside the repo: exit 2, a warning).
  `run.sh` says it is building the tools image (on the first run, that it takes minutes) and scanning.
- **The clone's own hooks**: `.githooks/pre-push`, `post-commit` and `post-rewrite` (one script, a copy for each name) run the clone's own `.git/hooks/<name>` when it has one (executable), with the same arguments and input; a pre-push that refuses refuses the push.
  `core.hooksPath` has git look only in `.githooks/`, so without it a check of one's own, kept outside the repo in `.git/hooks/` and shared by the clone's worktrees, would never run.
- **In a git worktree** `.git` is a file that names the repo's own `.git` by its host path, where the worktree's index is too: `run.sh leaks` mounts that folder into the container at the same path, read-only, so the hook and the scan of the history work there as in the repo.
- **CI's leaks job** scans every commit (`./run.sh leaks`). gitleaks is a pinned release, checked against its published checksums in `Containerfile.tools`.
- Tests: `tests/test_run_sh.py` (Where tests live).
