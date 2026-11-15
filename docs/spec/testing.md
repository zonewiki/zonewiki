# Tests

Part of Zone's spec (see `AGENTS.md` for the index).

How the suite is set up and what each module covers.
The rules (run it, add tests with every change) are in `AGENTS.md`.

## Shared setup

`tests/conftest.py` holds the shared setup: every test gets a fresh temp wiki (`wiki_dir`: just `index.md`, `_assets/` and `_tasks/`; `git_wiki_dir`: the same set up as a git repository, as Zone does at start; `client`, a server on it),
plus helpers that modules import from `tests.conftest`: `HOST`,
`write_page`,
`create_task`,
`make_list` (a task list, made as New list… makes it) and `list_page` (`write_page`, plus the lists the page's `[[tasks: list=…]]` embeds show, made through the browser's routes with the embed's list settings, and pinned as a `pin=` there says: a page no longer makes or pins a list, so `pin=` in a test's page is this helper's to read, not Zone's; the browser tests have both in `tests/e2e/conftest.py`, taking the page),
`content_hash`,
`section_fields` (the editor's hidden fields for a section, read from its `/edit` page),
`preview` (markdown as `/api/preview` renders it),
`rendered` (a page's article),
`backlinks` and `task_backlinks` (a page's link sections).
The browser tests' `wiki` fixture builds on `git_wiki_dir`.

**The server under test** (`tests/goserver.py`): the binary is built once per run (`go build` in `go/`, by pytest's first process, which hands the path to the xdist workers in `ZONE_TEST_BINARY`).
The `client` fixture is a `GoClient`: it takes the calls of Flask's test client (get/post/put/delete with headers, data, json, query_string; cookies) and sends them over HTTP to a server started on the test's wiki,
answering with werkzeug's `TestResponse` (`get_json()`, `get_data(as_text=True)`, `status_code`, `headers`).
The browser tests' `wiki` fixture starts one on a port fixed for the session.
The settings a test changes in `tests/settings.py` (`monkeypatch.setattr(settings, "PASSWORD", …)`: `HOSTS`, `LOCALE`, `NOSHARE_TAGS`, `GIT_ENABLED` (a `PATH` without git) …) reach the server in its settings file,
`.zone/config` (`config_text` in `goserver.py`: the settings that differ from Zone's defaults, passwords and tokens as their hashes, then `settings.CONFIG_FILE`, more of the file's text, then the users as `[user …]` sections;
written before the server starts, and removed once the settings give none),
the deployment's (`ZONE_ALLOW_REMOTE`, `settings.ENV`) as its environment): when they change,
it restarts on the same wiki (and port) before the next request;
the browser tests' server restarts the moment a test sets one (a `__setattr__` on the module), since requests made with `page.request` don't pass `_new_page`'s route.
Zone's docs folder (`settings.DOCS`, which `TestZoneHelp` points elsewhere) reaches it as `--docs`;
the semantic index (`settings.SEMANTIC`, a `tests/semantic.py` `FakeIndex`) as `--semantic-test`: the server gets the fake concept embedder and floors (`go/embedtest.go`), syncs once at start when the test called `sync()`, and restarts after each `sync()`, so it sees the wiki as it is then.
A server makes the wiki a git repository at start (`gitSetup`), so `git_setup(tmp_path_factory)` (a server started and stopped) is how a test "restarts Zone" (start-up commits, `index.md`, the yearly archives).
What no route reaches (text functions, search ranking, the index's cache, git helpers, the protocol's limits) has Go tests in `go/*_test.go`;
`go/testdata/golden.json.gz` holds the text functions' answers on every string in the tests of 2026-10 (recorded from the Python Zone then) and on edge cases added since,
which `go/golden_test.go` checks the Go ones against,
and `tests/test_text_parity.py` the browser's copies of them (`public/js/mdtext.js`, run in Node: slugs, heading ids, a wikilink's parts, the checkboxes of a text, a tick, a query's words),
so the two sides can't drift apart: a case added for one is a case for both;
after a deliberate change to one of those functions, `ZONE_GOLDEN_UPDATE=1 go test -run TestGolden` (in `go/`) logs every case that changed and writes the new answers.

`tests/e2e/test_quiet_tab.py`: a tab nobody is at asks for neither the uncommitted count nor the Tasks counts through ten minutes, each is asked once after the pointer moved, and again when the tab comes back.
`TestLastChangedAsksGitOnce` (Go): a page view asks git about a page once, and again after Zone's commit, a change on disk and a commit by hand; without a reflog every time.

`tests/test_git.py` uses `git_wiki_dir` and covers every git path (commit-on-write, history/revision/diff/restore, `_deleted`, uncommitted/commit endpoints, git-disabled fallbacks); it skips when git is not installed.
A server makes any wiki a repository when it starts, so the other modules' wikis are repositories too; `settings.GIT_ENABLED = False` gives the server a `PATH` without git, for the fallbacks.

`tests/test_mcp.py` covers the MCP server (files: `upload_file` from text and base64, the name it gets, what it refuses, `read_file`'s image, text and other files and what it won't read, the file routes with the token and nothing else opened by it; with the fake semantic index too: read_page's related pages, a new page's pages close in content): the tool definitions (names, hints, their size, which take guides),
the HTTP transport and auth,
the wiki-guide page in the instructions,
folder guides (a write refused until their ids are passed, imports, a changed guide's new id, every page write gated, dry runs not; read_guides for a page, a folder and the wiki, the guided folders in its description, read_page's guide id),
the reading tools (tasks created through `/api/tasks`;
with and without the fake semantic index from `test_semantic.py`;
search_wiki's heading paths and its query log (only with `query-log`,
rotation),
read_page's links and one section,
list_pages' link counts and orphans,
check_links,
list_tags,
grep_wiki with regexes,
limits,
a bad pattern,
the timeout and the no-git fallback,
history: recent changes,
a page's versions and a diff),
and the writing tools: pages (create/overwrite,
exact-text edit,
find and replace across pages (dry run, code left alone, regex groups, a folder, the max_pages guard, a regex RE2 can't take refused),
append under a heading,
rename with links,
a folder move (subfolders, links, a page of its name, refusals),
delete,
similar-name and broken-wikilink notes) and tasks (create,
update fields and status,
id prefixes,
comment,
delete),
several edits at once,
restore_page (a version, a deleted page),
write_page's `base_hash` (merged with a change made since read_page, nothing written on a clash or an unknown hash),
a page open in the editor (its auto-saved draft committed first as the user's, the editor's 409 after),
plus one commit per write marked `(MCP)`;
and zone_help over a small docs folder (passages for a query, a doc, a section, the topic list, testing.md left out, no docs installed) and over the real docs.
The protocol's handling (versions, notifications, errors) and the limits long answers are cut at are `go/mcp_test.go`'s.

`tests/test_task_refs.py` covers `[[task:…]]` references: chip rendering and badges (dates as `<time>` for the locale), done/won't-do/missing, recurring series, list rows, code, Linked tasks, search by task title and MCP `read_page`.
`tests/e2e/test_task_refs.py` ticks chips (undo, keep tasks), the editor completion and Make task (the modal for one item, Cancel, batch mode for several lines with shared tags and per-line shorthand, headings skipped, inline selection, undo, the hidden tasks leaving the editor's keys alone).

`tests/test_semantic.py` covers semantic search through the server with a fake embedder (words mapped to a few "concept" axes, `tests/semantic.py`),
so it needs neither onnxruntime nor a model: the search dialog's mode (off, indexing, on, failed: rendered and in `X-Zone-Semantic`),
hybrid `/api/search` and the Related section.
The index itself (chunking with heading paths, a cache of an older chunking re-chunked with its vectors kept, the content-addressed cache, failure fallback, the worker, rank fusion, settings, the model's download) is covered by `go/embed_test.go`.

`go/eval_test.go` covers `zonewiki eval`: a page target matching any section and a section target only its own,
ranks by keywords on a temp wiki,
targets naming nothing (page, section, task),
the report's numbers per mode,
the questions file's format (questions without targets skipped and counted),
`--new`'s stubs from the query logs (known and repeated queries left out, newest first),
and that the semantic index it builds never writes the wiki's cache.

`tests/test_image.py` checks the Containerfiles: the model's revision and hashes are the ones `go/embed.go` checks,
the semantic layer copies nothing from the repo (a code change mustn't download the model again),
both images build with the same Go,
and the image ships the browser code,
the docs and the licenses.
It also keeps the notices current: `THIRD_PARTY_LICENSES` names the pinned Go, Go modules and model, every library in `public/vendor/` has a `LICENSE`, and Mermaid's notices record the hash of the vendored `mermaid.min.js`.

`tests/test_env_example.py` checks `.env.example` against the settings the source, `run.sh` and `compose.yaml` read (none missing, none unknown; the variables of settings that moved into the settings file, which the code names only to refuse and import them, don't count).

`tests/test_page_writes.py`'s rename-tag tests cover renaming a tag through both doors (tasks, archives, embeds and links in one commit, a list of that name left alone; refusals; asking before a merge; text not yet in git; a corrupt tasks file writing nothing),
its list tests New list… (a registry entry and no page), a list's rename (its tasks, the pins, the pages' embeds and links in one commit, a tag of that name left alone; its dialog's refusals; text not yet in git; a corrupt tasks file writing nothing), Delete… (its tasks moved, the pages' embeds saying there is no such list) and the pin's commits,
`tests/test_privacy.py` what it does across users (and whose a new task is that names no list: its maker's, in their Inbox, with no list made for it; each user's Inbox, its pill and its view their own, none of a user's in sharing mode, the wiki's without users),
`TestRenameTagInText` and `TestRenameListInText` in `go/pages_test.go` the rewriting of text,
and `test_rename_and_delete_list_dialogs` in `tests/e2e/test_tasks.py` the dialogs.

`TestListSwitcher` in `tests/test_tasks_embed.py` covers the Tasks page's row of lists (My tasks, the Inbox and the pinned lists as pills, the others and All tasks under More and what More names, each place's counts, a new list pinned for who made it, the pills' order by `POST /tasks/pin-order`, the filter field from eight lists, sharing mode),
`TestANewListIsPinnedForWhoMadeIt` and `TestPinOrder` in `go/tasklists_test.go` whose registry a pin and an order are written to,
`TestRegistryDefaultIsTheFirstPin` in `go/internal/taskfiles/lists_test.go` a registry from when a default list was named,
and in `tests/e2e/test_tasks.py` More opening, closing and naming the view, a list's menu (Pin, Move first, Unpin: a pin is a pill and no more), a pill dragged to another place without the page loading again, the filter and the mark on a list new to the browser.
**The Inbox** (a task in no list; with users its maker's own): `TestInbox` in `go/tasklists_test.go` covers where such a task is kept with and without users and that it has no assignee,
`TestInboxScope` the names `inbox` and `none` where a list is named, and a list called so from before;
`test_the_inbox_is_a_pill_and_a_view` in `tests/test_tasks_embed.py` its pill, its counts, its view and its line, the name refused for a list, and `test_my_tasks_and_the_inbox_have_none` that it has no backlinks;
`TestASharedTaskIsInAList` in `tests/test_task_assignee.py` that with users a shared task never leaves its list for none, and goes to the shared list `general` where it would (its list deleted, a restore or an import whose list is gone, a task left without one when Zone starts), and a shared list's backlog counted on its page.
The migration at start: `TestRegistryVersion` in `go/internal/taskfiles/lists_test.go` (a registry keeps the version it was read with; `home` is a setting like the others),
`TestInboxMigrationRenamesListsCalledInbox`, `TestInboxMigrationTurnsThePrivateListIntoTheInbox`, `TestInboxMigrationOfAUserWhoPinnedNothing`, `TestInboxMigrationWithoutASharedRegistry` and `TestInboxMigrationLeavesOutWhatWasNotPinned` in `go/tasklists_test.go` (a list called inbox renamed, a user's list `private` that took their new tasks turned into their Inbox, the lists nobody had pinned left out of My tasks, once per registry),
`TestSharedTasksWithoutAListGoToGeneral` and `TestGeneralInAWikiWithoutARegistry` (with users the shared tasks without a list go to `general` at every start, also in a wiki that never had a list).
In the browser, `tests/e2e/test_tasks.py`: `test_the_inbox_has_a_pill_and_a_view_of_its_own` (without users: the pill, the view, its locked chip, a new task there, one filed leaving, `?list=none`),
`test_c_t_makes_a_private_task_in_the_users_inbox` (with users: the line under the title, the task in her files, in My tasks and on her Inbox's pill, nothing of it for another user),
`test_a_name_on_a_new_inbox_task_moves_it_to_a_shared_list` (the only shared list, the one this browser last made a task in, none: the warning and the refused Save) and `test_a_name_typed_before_the_users_are_known_still_counts`,
`test_a_shared_task_cant_go_into_a_private_list` (nor into the Inbox: the List field's options and the move dialog's rows, the API's 400);
`tests/e2e/test_task_inbox.py`: an Inbox task filed into a shared list through the dialog (shared, assigned to its user, the name taken off) and through the move dialog (the row's note, no undo of a share), a shared list's "N unassigned" and its link, Leave out of My tasks in List settings (only on a list of one's own; without users any) with what it does to My tasks and the Tasks link, and where Delete… sends a list's tasks (a private list's to the Inbox, a shared one's to `general`, `general` as the only shared list).

`TestInitialTasks` in `tests/test_tasks_embed.py` covers the tasks a page brings along (what the API would answer, once a page, a task's text not ending the block, none in a preview),
and `test_a_page_draws_its_lists_without_asking_for_the_tasks` in `tests/e2e/test_tasks.py` that the lists draw from them with no request.

`tests/test_routes.py`'s `TestMergeOnSave` covers merge on save over HTTP (different lines merged,
the same lines 409 with the markers,
the fallbacks,
a section's new range,
saving on from the text sent,
set aside,
Keep mine committing what was never in git,
Done merging and committing,
text left in conflict kept for the next visit and gone after a save or 30 days),
and `TestApiToggleCheckbox` a tick from an older page view finding its item by its line (or refused),
also after a tick found elsewhere;
`tests/e2e/test_merge.py` two editors (alice and bob, two browser contexts) in the browser: a merge into the textarea (the note, the cursor, one undo),
typing on while a merged answer comes back,
and the banner's Merge by hand (auto-save waits for the markers),
Reload theirs and Keep mine (saving on from it),
also after Done,
changes left in conflict offered on the next visit (also when leaving with the banner up or markers left),
a merge arriving under a dialog,
and `replaceChangedLines`' edge cases;
`go/merge_test.go` covers `mergeText` (clean, the same change on both sides, no final newline, an empty base, conflict markers, no git) and the bases kept.
`tests/test_page_writes.py` runs each page write (delete, rename, restore, create, a note's Move to wiki and Append) through both doors,
the browser's route and the MCP tool,
on the same wiki: text not yet in git kept before a write replaces it,
the home page,
an existing target,
a missing page,
a restore to the same text or to a version without text,
and another user's private page (refused, with the answer a missing page gets).
`tests/test_users.py` covers users: signing in per user (the name field, wrong name or password, throttling, a removed user and a switch from one password to users signed out),
per-user MCP tokens,
commit authors (browser, tasks, uploads, notes, MCP; Zone's own commits and a wiki without users),
authors in the history and on tasks comments and History events (an undo keeps them),
and another user's draft committed in their name before a save,
Done,
checkbox,
delete,
rename,
Commit file/all or MCP write (`TestDrafts`).
`TestDisabled` covers a disabled user (no sign-in, told only with the right password, the session stopped and going on once enabled, the token refused).
`tests/test_task_assignee.py` covers a task's assignee: assigning, reassigning and unassigning through the API and `batch-update`, how a name and no one may be written, who can't be assigned and what the refusal says, private tasks (an Inbox task has none) and wikis without users, the reserved names `me` and `none`, a former assignee's name staying through edits and coming back only to a task that had it, the history and its undo, a series' next occurrence, restored and imported tasks, `GET /api/tasks/assignable` and the names in a page's task data, and `[[tasks: assignee=…]]` on the server (each user's counts, how the param is read, the export, the settings dialog's write, what New task gets), and the MCP tools (`TestAssistant`: assigning, the refusals as tool errors, `search_tasks`' `assignee`, a new task without a list in the Inbox)
In the browser (`tests/e2e/test_task_assignee.py`): the name on the card, the dialog's field with its completion and its refusal of a name that is no user, `@name` in a new task's title (the List field moving to the shared list; not on a task in a private list or in the Inbox chosen by hand), the filter's names, `@me`, `@none` and the `@` key, a new task taking the filter's assignee, a former assignee struck through and counted as no one's, the undo that puts one back, a wiki without users, one `assignee=me` page showing two users their own tasks, the settings dialog's field, several selected tasks assigned at once with their undo, and what My tasks holds (`test_my_tasks_holds_what_is_mine`: her Inbox and her private lists but one left out, what is assigned to her wherever it is, not an unassigned task of a pinned list; a task given away leaving it and its count, one taken from a list's backlog coming in; its settings).
`tests/test_tasks_core.py`'s `TestAssignee` covers the filter's and the title's `@` words in `tasks-core.js`.
**My tasks** (the home view's rule): `go/testdata/mine_cases.json` is one table of cases that runs through the server's rule (`TestInMineCases` in `go/tasklists_test.go`) and the browser's (`TestMyTasks` in `tests/test_tasks_core.py`), so the two can't drift apart;
`TestMyTasks` and `TestMyTasksSettings` in `go/tasklists_test.go` cover it on a wiki (without users every task; with users one's own tasks and the shared ones assigned to one; a list left out; pins changing nothing; its counts the Tasks links') and its settings in the registry (`TestRegistryMineWasAFlag` in `go/internal/taskfiles`: the old flag reads as nothing);
`TestMyTasks` in `tests/test_task_assignee.py` what each user's holds (a pin changing nothing, a list left out, who can't be given tasks, a wiki without users), the settings each user's own, an assistant's new task in the Inbox;
`TestListSwitcher` in `tests/test_tasks_embed.py` the view at `/tasks?mine=1` (its pill, its line with the lists left out, its counts, its settings dialog's route, `?pinned=1`, a wiki without lists);
`test_tasks_link_shows_my_tasks` in `tests/e2e/test_tasks.py` and `test_my_tasks_is_the_tasks_module` in `tests/e2e/test_mobile_nav.py` it in the browser without users (every task but a left-out list's, pinned or not; a new task there in the Inbox);
`tests/test_task_notify.py` the mail about a task given to someone, against a fake SMTP server (`FakeSMTP` of `tests/test_signup.py`; the mail is sent in the background, so `mails(smtp, n)` waits for it): who gets one and who doesn't, one mail a user for several tasks, an assistant's assignment, the box that turns it off, no mail server; `TestGivenToMe` in `tests/test_tasks_core.py` the card's new mark, `test_a_task_someone_gave_you_is_marked_new` and `test_my_tasks_settings_turn_the_mail_off` in `tests/e2e/test_task_assignee.py` both in the browser;
`tests/test_privacy.py` probes its scope on every route that takes it.
`TestHandingOn` covers the Users tab's count of a disabled user's open tasks.
(`go/assignee_test.go`: `assignable`, the reserved names in `zonewiki user add` and an invite, the count `user disable` and `user remove` print; the field in the file in `go/internal/taskmodel`).

`tests/test_roles.py` covers viewers: every write route and MCP write tool is probed as a viewer on shared pages and tasks and on her own note and private task from before,
and no file may change nor any commit be made (`test_every_write_route_is_probed` and `test_every_write_tool_is_probed` fail for a new one without a probe),
with the message,
a refusal that tells nothing of another user's folder,
and the interface (no Edit, ✎ or Rename, the editor 403, no Private or Notes, no writing tools in `tools/list`).
`tests/test_admin.py` covers the admin page and its Users tab (only admins, `/admin` opening the first tab, 404 for everyone else and without users, the Origin check, role, disable, enable, remove, refusals: the last admin, the last user, disabling or removing yourself, a change meanwhile kept;
the commit's author;
the top row) and `tests/e2e/test_admin.py` the Remove dialog and your own row in the browser,
and invite and reset links (the welcome page changes nothing until its form, once only, a role, a reset ending the old sessions, revoke, replacing, expiry, throttling, the cap per admin);
`go/configfile_test.go`'s `TestUserRoles`, `TestUserLinks` and `TestRolesInTheFile` the commands and the keys;
`go/gitstore_test.go`'s `TestMissingUsers` the start-up guard.
`tests/test_signup.py` covers sign-up against a fake SMTP server in the test (`FakeSMTP`, `ZONE_SMTP_TLS=none`): off without the deployment, users or `base-url`, Zone not starting without a mail server, the whole way from the form through the mail's link to a signed-in viewer, a name taken or bad keeping the link, an address with an account getting its name, bad addresses, `signup-domains`, `signup-role`, the cap per client, a new ask replacing the link, the mail server down, the link dead once sign-up is off, and approval (waiting, the login's message, Approve on the Users tab);
`go/mail_test.go` the SMTP client (`fakeSMTP`: the mail's headers and text, no mail without STARTTLS, a server that isn't there), the mail settings, `validEmail`, the sign-up keys and `pending`.
`settings.USERS` maps a name to its password and token, `settings.USER_SETTINGS` a name to more of its section (`role`, `disabled`);
`goserver.py` writes their hashes into the settings file (`password_hash`: a fixed salt and few iterations, so the server's settings stay the same between requests and logins are quick).
`tests/test_public.py` is the test of public wikis (`ZONE_PUBLIC`): it asks every route Zone registers without a session, in both modes, and fails for one that answers and isn't on its lists, which must be the server's (`visitorRoutes`, `visitorEdits`); it looks for a task's or a private page's words in what a visitor reads, and checks what a visitor writes (pages, as anonymous) and never does (uploads, renames, deletes, tasks, notes, guides).
`tests/test_privacy.py` is the leak test for private pages and tasks: alice's private page (committed, deleted, uncommitted),
note,
upload,
pinned list,
list and private tasks hold a word that no response to bob,
signed in or through his MCP token,
may contain,
and bob's writes must leave her files alone.
It probes every route and MCP tool; `test_every_route_is_covered` and `test_every_tool_is_covered` fail for a route Zone registers, or a tool it offers, that is neither probed nor exempt: a new one gets a probe there.
`go/access_test.go` covers the rules themselves (`canSee`, `canSeePath`, `hiddenPage` with sharing mode, `strayPrivate`), `go/tasklogic_test.go` the per-user task files (`canSeeTask`, `mayMakePrivate`).
`go/users_test.go` covers the hash format and `authenticate`; `go/security_test.go` the session's user.

`go/sections_test.go` covers sections as the parser finds them: each ✎ opening its section (`TestEachPencilOpensItsSection`),
the ✎s and the sections agreeing on random pages,
finding a section by number and heading (`findSection`),
the MCP tools' sections and search chunks going by the parser,
pages with Windows line breaks.
`tests/test_section_editing.py` covers the same over HTTP (a stale ✎, Keep mine with its section moved or gone).

`go/math_test.go` and `tests/test_math.py` cover math: the parser (pandoc's rules, prices and escapes, `$$` blocks open, closed and on one line, ```` ```math ````, `\|` in table cells, inline content),
and the source side agreeing with it (heading anchors, `$$` blocks no section or checkbox, wikilinks in math kept as TeX, section editing below a `$$` block).
`test_routes.py` checks that the vendored libraries are served gzipped and kept a day, Zone's own code not.

`tests/test_users.py`'s `TestPrivateHome`,
`TestMakeShared`,
`TestSharedNewPage`,
`TestLinkToPrivateFromShared` and `TestModuleMenu` cover the private home over HTTP (`/private` made once on the first visit, one per user, a login needed, 404 without users, never renamed or deleted by route or MCP tool, an ordinary page without users),
Make shared (the menu's entry and target, not offered for shared pages, homes, notes or without users, a rename into the folder that is refused for shared pages (nothing changes), the links that follow a page made shared, committed by the user, the lock, the page list and search marks),
the marked link from a shared page to a private one and the user menu's markup (the top-right user button, Sign out in it) (with users, without, the current module per view).
The browser side is in `tests/e2e/test_private.py`: the user menu by click,
keyboard and Esc,
with Sign out,
on a phone as a bottom sheet from the tab bar,
Private home and `g p`,
Make shared through the dialog,
the locks in the search modal,
`[[` completion's new private page, and a link from a private page to a new shared page (marked, the warning editor with its hint, Keep shared).

`tests/test_password.py` covers Change password over HTTP (`/password`: a user changes their own password with the one they have, kept as its hash;
the other sessions ended and this one going on as the same session;
the MCP token left alone;
a wrong current password, a short new one and two that differ refused, wrong ones throttled as at the login;
a viewer's;
signed out and cross-site refused;
without users the wiki's password, and no page without a login).
In the browser (`tests/e2e/test_private.py`): from the user menu to the page, a wrong password, the change, still signed in.

`tests/test_oauth.py` plays an MCP client over HTTP (Connecting with OAuth in `docs/spec/mcp.md`): the 401 and the metadata, off without a login and with `mcp-oauth = no`;
registration (what a redirect URI may be, nothing kept, a forged client id);
the consent page (through the login and back, who asks and where the answer goes, the password and its throttle, Allow answering with a page and a `Refresh` header, Deny, an unregistered redirect URI, what a client got wrong, a program on this computer at any port, cross-site and signed out);
tokens (the code once, with its verifier, client and redirect URI; refresh, a refresh token used twice, another client's, expiry by editing `.zone/grants`, the throttle);
connections (listed and ended on Connect an assistant, only one's own, a viewer's, a disabled or removed user's, a reset link against an ordinary new password, a wiki with one password).
In the browser (`tests/e2e/test_oauth.py`): from the login to Allow at phone width, Chromium following the answer to another origin under the page policy with no violation, the code turned into tokens, the connection ended on the page; Deny.

`tests/test_assistant.py` covers Connect an assistant over HTTP (`/assistant`: a user makes their own token with their password, kept as its hash and shown once;
a wrong password refused and throttled as at the login;
a new token replacing the old one, revoking, the other users' tokens left alone;
a viewer's assistant only reading;
signed out and cross-site refused;
without users the wiki's token, with the wiki's password or none;
the address from `base-url`;
the shortcuts dialog's link).
In the browser (`tests/e2e/test_private.py`): from the user menu to the page, a wrong password, the token made and working, its Copy button, the page at phone width, revoking.

`tests/test_socket.py` starts Zone on a Unix socket (`--addr unix:PATH`): `zonewiki health unix:PATH` answers over it and fails for a path nothing listens on,
and five wrong passwords from one address in the proxy's `X-Forwarded-For` make that client wait and no other.
On a socket the test holds, as systemd would (`--addr fd:N`, `ZONE_IDLE_EXIT=1s`): requests keep Zone up, the healthcheck alone doesn't and Zone exits with 0, a request sent then is answered by the next Zone started on the socket, a wrong password keeps Zone up until the right one, and the setting is ignored on a socket Zone opened itself.
In Go (`TestListenOnAHandedSocket`, `TestIdleLongEnough`): the handed socket's file stays when the listener closes, what isn't a listening socket is refused, and the idle decision by the clock, a running request and the throttles; `TestCommitDrafts`: a draft committed in its author's name, as before an idle exit.
In Go (`TestListenOnSocket`, `TestClientKeyOnSocket`): the socket's mode, a socket file left behind replaced, a live one and a plain file refused and left alone, and the socket's peer trusted whatever `trusted-proxies` holds.

`tests/test_config.py` covers the settings file through the server: a new user signs in without a restart,
a removed one is signed out,
a changed password (ending the old one's sessions) and MCP token,
a broken file keeping the settings from before (alice stays in, no one else gets in) and failing `/api/health` until it is fixed (the error itself only with a session),
a file without users and password kept until a restart,
other settings reloading,
the wiki's password as a hash,
the file kept out of git (a change logged by name and key only) and never served or shown in history and the uncommitted list,
a clone whose history has users but no settings file refused at start (an empty file allowed),
and a new wiki's first commit without the session key.
Zone looks at the file at most once a second, so a test that rewrites it waits that out (`write`) rather than polling logins, which the throttle would count.
`go/configfile_test.go` covers the parser (every error and its line),
the settings from the file (defaults, every check),
the reload (kept on an error, a removed file, at most once per `reloadEvery`, the noshare cache),
the line edits keeping comments,
the 0600 write,
`zonewiki user` and `zonewiki config` on a temp wiki (`set`/`unset`, `passwd`, `token`, `import` from an older environment),
token hashes,
the log line (`settingsChange`),
and `zonewiki health` failing on the error.
`tests/test_routes.py`'s `TestUsage` runs the binary: `zonewiki --help` (also `-h`, `help`) names every command, the environment and the flags (with two dashes, as everywhere in the docs; one works too), and each command answers `--help` with its usage, exit code 0, without a wiki.

`tests/test_run_sh.py` covers `run.sh`'s container calls and `.githooks/pre-commit` against a stub `podman` first on PATH (it logs its arguments; `run`/`start` exit with `FAKE_EXIT`),
in a scratch git repo holding copies of both,
so it needs no engine: `leaks` passes a temporary `GIT_INDEX_FILE` in as a `/repo/…` path (absolute or relative; outside the repo: exit 2),
in a git worktree it mounts the repo's `.git` at its own path, read-only, and passes the worktree's index by that path,
copy mode creates the container with `--rm`,
a build's output stays hidden unless it fails (then nothing runs) and a first build says so,
`test` runs go vet and go test before pytest,
`test` and `e2e` run pytest in parallel (pytest-xdist),
`eval` refuses a missing questions file and builds with `ZONE_SEMANTIC_SEARCH` and mounts the wiki read-only,
and the hook scans the temporary index of `commit -a` and `commit <path>`,
refuses a leak (exit 3) and only warns when the scan fails,
in a worktree as in the repo.
The hook tests skip when a local gitleaks is on PATH beside bash; one in a folder of its own (the tools image's) is left off the scratch repo's PATH, so they run there and in CI.

To run the suite without a local Go or venv: `./run.sh test` (gofmt, go vet, go test, then pytest, inside the tools image with the repo mounted).

Both `./run.sh test` and `./run.sh e2e` run in parallel with pytest-xdist: `test` with `-n auto` (a worker per CPU), `e2e` with a worker per two CPUs (each runs its own Chromium; at one per CPU the browsers starve and timing-sensitive tests fail).
An `-n` in the extra args overrides it (`-n 0`: one process).
Workers are separate processes: each has its own temp wikis, servers (`base_url`) and browser, so the fixtures need no locking; a test must keep its files under `tmp_path`.

## Browser tests (`tests/e2e/`)

End-to-end tests drive the real app in headless Chromium via Playwright.
They run in the tools image (`Containerfile.tools`, based on Microsoft's Playwright image; it also has Go, Node, gitleaks, and pngquant for the README's screenshots) so the app image stays browser-free and the app still has no build step or npm.
`./run.sh e2e` builds that image (cached) and runs them in parallel (above) with the repo bind-mounted; extra args pass through to pytest (e.g. `./run.sh e2e -k tasks`).
Without Playwright installed they skip, so plain `pytest tests/` is unaffected.

- `conftest.py` starts a server per test on a fresh temp wiki with its own git repo (on a port fixed for the session); each test gets a fresh browser context.
  Dates in the task app's date picker go through `pick_date(picker, d, tap=False)`: the tests take dates from the real clock (today + N), and the picker opens on the month of the date it holds, so near a month's end the date can be a page before or after.
  Tests of how the editor behaves over a slow connection (`test_editor.py`'s `slow_saves`) put a small proxy between the browser and the server that answers auto-saves 2.5 seconds late.
  A request to any other host is aborted (offline, deterministic) and fails the test: Zone loads nothing from a CDN.
  Uncaught page JS errors fail the test.
- Coverage: Notes filter and the search dialog's mode indicator (its wording, its update from `X-Zone-Semantic`, fitting on a phone; `test_search.py`),
  modules (breadcrumb switcher and current module, phone tab bar (a tap acts on the touch, no click of the browser's;
  a tap below the bar's box, as Firefox lays it out, still reaches the tab), New per module, due count on the Tasks links, pinned lists, desktop top actions, New task/`c t` adding to the Inbox from any page (starting today, without a list's tags or keep), `g t` going to My tasks;
  `test_mobile_nav.py`),
  side columns (sticky TOC and links from 1320px, active section, code and tables growing only when needed, none below 1320px; `test_layout.py`),
  editor (formatting bar and Ctrl+B/I/K, the Task list dialog writing and changing an embed, auto-save + unsaved dot, no commit on auto-save, Done/Ctrl+S commit, pagehide beacon, live preview/F2, list continuation),
  search modal (Ctrl+P/Esc, fuzzy ranking with file names and its tie-break, content matches, query syntax, highlights on the opened page, arrow/Enter nav, unlinked filter),
  tasks (an undo armed only after its own write,
  Plan-board drag to a day and to Unplanned,
  touch long-press drag via CDP touch events (board, Open-tab reorder, swipe still scrolls, a long press let go in place starts selection mode, also where nothing drags, and moving after the hold cancels it),
  board grouping by start date,
  modal "Days before due",
  Plan board in Open's order with in-section drops ignored,
  view tabs,
  start-date Today,
  selection (Shift/Ctrl+click, Ctrl+A, the selection bar and the menu of a selection, the bulk tag dialog's three states and undo, moving the selection to a list),
  touch: tap-to-edit,
  ⋯ menu/bottom sheet,
  selection mode (taps toggle, no drags, the bar in the tab bar's place, its ways out),
  target sizes,
  editor scrolling/compact phone layout,
  date picker placement),
  sortable tables (header click cycles ascending/descending/original, numeric columns, empty cells last, keyboard, not in the editor preview),
  layout (no horizontal page scroll at 390px phone width on page/editor/tasks views, mobile Preview button, tldr toggle geometry (and the open text lined up with the row beside the side columns), the heading copy-link icons),
  folding sections (a click,
  nested folds,
  Alt+click and `z`,
  the wavy line and its "folded" that unfolds,
  anchors/TOC/hashchange/`beforematch`/search highlights unfolding,
  `]` skipping folded headings, presentation and print, the chevron's place on desktop and phones; `test_fold.py`; printing and Print…: the dialog named after the page, the page alone on the paper, light on a dark screen, a real PDF out of headless Chromium: `test_print.py`), math and Mermaid diagrams (rendered from the vendored copies on a page, in the editor preview, task notes and presentation slides; a page without them loads neither library; a broken diagram keeps its source; `test_figures.py`), the Content-Security-Policy (a page's `<script>`, `onerror=` and `javascript:` link and the editor preview's handler don't run, an uploaded `.js` doesn't run as a script; `test_security.py`).
- A test that never ends: under load Playwright's driver or the browser sometimes dies (`new_page` fails with "Target crashed" or "Connection closed while reading from the driver"), and the next call of its sync API then waits forever, which nothing inside the test can interrupt.
  So `conftest.py` stops the process of a test that runs longer than `TEST_TIMEOUT` (120 s; the slowest takes under 10), setup and teardown included (`pytest_runtest_protocol`), after writing which test it was and where every thread stood.
  With pytest-xdist that process is one worker: the run reports the test as failed ("worker crashed while running …") and goes on with a new worker, instead of standing still until someone stops it.
  Such a failure says the machine was short of CPU or memory, not that Zone is wrong: run the test again by itself.
- A page is ready before a test acts on it: `page.goto`, `page.reload` and `page.wait_for_url` return only once Zone's code has started on the page and every task list that started shows its tasks (`_wait_when_there`, `READY_JS` in `conftest.py`).
  Before that a click does what the plain HTML does (a dialog's form posted as it is, its error in the URL) and a task list has no cards, which on a busy machine is what a test met.
  The page says so itself: `<html data-ready>` once the entry module `zonewiki.js` has run, `data-loaded` on a `.tasks-app` once it has drawn its tasks (`fetchTasks` in `js/taskdata.js`; on Closed once those are there); only the tests read the two.
  A page without Zone's script (a download, an error page) counts as ready, once it is read to its end: `wait_for_url` returns when the address has changed, before the new page's script tag is there to be found.
  `ready=False` on any of the three returns as Playwright does, for a test that keeps the script from running or looks at the page while it loads.
  After a navigation none of them started (a link clicked, a form that leaves the page), `wait_ready(page)` waits the same way where the next step needs it.
- Waiting: for what the page shows, `expect(…)`, which waits by itself, up to 15 s (`WAIT_MS`; Playwright's own 5 s ran out on a machine busy with other work).
  A passing test waits no longer for that; only a failing one takes so long to say so.
  An action whose result the app takes back is a `click()`, not a `check()`: `check()` fails when the box is unticked again before it looks.
  The page's address is asked with `expect(page).to_have_url(…)` / `not_to_have_url(…)`, not read from `page.url` right after the page changed it itself (`history.replaceState`): `page.url` is what Playwright last heard, which can be a moment behind what the page already shows.
  For what it can't watch (the API's answer, a file on disk, a request a route held back), `wait_until(page, predicate)` from `conftest.py`, which looks every 100 ms (as long as `expect`) through the page, so the page's routes and events go on meanwhile (`time.sleep` would stop them).
  A fixed `page.wait_for_timeout(…)` is only for showing that nothing happens (no request went out, a card stayed where it was) and for the pace of a gesture (a touch held, a drag's steps): never for something that is about to happen.
- Touch: `new_page(**TOUCH_PHONE)` gives a phone context with touch input, `hover: none` and `pointer: coarse` (use `.tap()`). Full-page screenshots of such a context lose the touch/hover emulation (Playwright re-renders with changed device metrics), so assert on computed styles/sizes rather than on a full-page image.
- `test_screenshots` writes full-page PNGs of each view at phone/desktop × light/dark to `tests/e2e/_screenshots/` (gitignored) for visual review.
  It asserts nothing about appearance.
- Playwright's HTML5 drag cannot auto-scroll mid-drag, so drag tests must keep source and target on screen (e.g. enlarge the viewport).
- Keep the `playwright==` pin in `tests/requirements.txt` equal to `Containerfile.tools`' image tag version.
- Content-Security-Policy: Zone's pages run under `script-src 'self'` (Security in `docs/spec/security.md`).
  The harness collects the violations Chromium reports on the console (`page.csp_violations`) and fails a test that has any, so new code can't bring in an inline script or handler unnoticed; a test that blocks one on purpose (`test_security.py`) clears the list.
  `test_security.py` also has what only a browser shows of the other rules: an iframe's `srcdoc` never frames a form, and a task's id is text on its card, not an element.
  Under that policy Playwright can't `eval` in the page: `page.wait_for_function` needs a function (`"() => scrollY > 0"`), as an expression string is evaluated with `eval`; `page.evaluate` works either way.

`go/tasklists_test.go` covers lists as places: the migration at start (lists and pins taken over from pages, each task given its list, per user with private and shared lists of one name, a wiki with nothing to take over, one that can't be read), a list's name, a task going into a list and the rules of private lists (`applyList`), rename, delete (a private list's tasks to the Inbox, a shared one's with users to `general`) and share with their tasks, pins per user, a list's settings, the Inbox and its migration (above), and where an undo's or an import's task goes (`placeSnapshot`).
`go/internal/taskfiles/lists_test.go` covers the registry file: its format read back as written, its version, a hand-edited one read leniently, each owner's own, who may write it, an unreadable one refusing a write, and a task's `list` in its file.
`go/internal/taskfiles/taskfiles_test.go` covers the task files on a temp folder, without a wiki or a server: a write filed by owner and year in one commit and an unchanged one committing nothing, what each user reads, the Closed view by year and its years, a corrupt file refusing the write and staying as it is, who may write (`MayWrite`, a task of another user's refused), and the archive at start.
`TestReadsAreCopiesOfAFileParsedOnce` there covers the parsed files: a read's changes show in no later read, and a file written by Zone, changed by hand, unusable or gone is read again.
`TestTasksChanged` in `tests/test_tasks.py` covers the `Zone-Tasks` word (another one after a write of a task or a list, the same without), `test_tasks_show_what_someone_else_changed_within_a_minute` in `tests/e2e/test_tasks.py` the lists loading again by it (Playwright's clock moves the minute; a busy list is told the next one).
`go/internal/atomicfile` has the atomic write's (a new file's mode, an old one's kept, a link written through).

`tests/test_task_app_wiring.py` reads the task app's sources (`public/tasks.js`, `public/js/task*.js`): every `app.name` a part uses is one `tasks.js` puts on `app` or a part returns, and no name is put there twice; a misspelled or moved function is found there, not in a browser when its line runs.

`tests/test_tasks_core.py` exercises the pure client logic in `public/tasks-core.js` (the locale's date helpers too) by running it under Node (`node -e`); those tests skip automatically when Node is not installed.
`./run.sh e2e` (and CI's e2e job) runs them, since the tools image (`Containerfile.tools`) installs Ubuntu's `nodejs`; `./run.sh test` leaves them to it.
