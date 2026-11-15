# Tasks

Part of Zone's spec (see `AGENTS.md` for the index).

### Task list embed

`[[tasks:]]` on a line by itself embeds the full task app (see Tasks) inline.
Syntax: `[[tasks: param=value param2=value2]]`.

The colon makes it an embed: `[[Tasks]]` stays a link to a page called Tasks.
Case doesn't matter (`[[Tasks:]]`), nor a space after the colon (`[[tasks:list=shop]]`).
(`tasksRE`, `embedArgs`.)

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `tag` | comma-separated tags | — | Pre-filters by tag. Comma = AND, pipe `a\|b` = OR, a leading `-` excludes (`tag=work,-mary`). The chips are locked (the page says what it shows); new tasks get the single tags, but a task's tags stay editable. |
| `assignee` | `me`, a user's name, `none` | — | With users: shows only the tasks assigned to that user; `me` is whoever looks at the page, so one page shows each user their own, and `none` those no one has (Assigning tasks in `docs/spec/users.md`). Joined as `tag` is: `a\|b` either's, a leading `-` not theirs (`assignee=-anna`). The chip is locked, and a new task here is assigned to the one user it names. Without users it filters nothing. |
| `view` | `focus`, `open`, `plan`, `closed` | Focus (or the tab remembered this session) | Opens on that tab whenever the page is opened; a URL `?view=` overrides it, and a reload stays on the tab it showed (see Views). Case doesn't matter; any other name (`views`) opens the default. |
| `plan` | `on`, `off` | on | What the Open tab shows: on, the board by start date (Plan); off, one list. With `view=open` or `view=plan` it decides which of the two opens (`view=open` alone is the list, `view=plan` the board, as always). The Plan switch still flips it, for the browser tab (see Views). |
| `limit` | integer 1–99 | none | WIP limit: Focus warns at this many tasks, and Plan day sections with at least this many are flagged amber. `?limit=N` works on `/tasks`. |
| `list` | a list's name | — | Shows that list: the tasks in it, with the list's own settings (see Lists). A new task made there goes into it. On a shared page the name is the shared list; on a page in your private folder your own list of that name first. A name no list has shows a line that says so, with a button that makes the list. `list=inbox` shows the Inbox, the tasks in no list: with users whoever looks their own (Lists → The Inbox; `list=none` says the same). Cleaned like tags, lowercased (`cleanList`). With `list=`, only `view`, `tag` and `assignee` of the embed count: they say how this page looks at the list; what the list is (`limit`, `plan`, `keep`, `labels`, `new`) is set on the list. |
| `mine` | `1` | — | Shows My tasks (Lists → My tasks): for whoever looks, their own. A new task made there goes into the Inbox. The embed's `view`, `plan`, `limit`, `labels`, `tag` and `assignee` count, as for an embed of no list. |
| `keep` | `1`/`true`/`yes` | — | New tasks created in the embed are keep tasks (the edit modal's checkbox starts checked), e.g. a shopping list of staples. For a list, set on the list (List settings…). |
| `pin` | | — | Means nothing anymore: a pin is of a list and the user's own (see Lists → Pins). Until 2026-11 it pinned the embed's list; the migration moved those pins and took `pin=` out of the pages. |
| `new` | tags, comma-separated | — | The tags a new task here gets, in place of the filter's single tags. For a list, set on the list. |
| `labels` | up to 2 comma-separated names | `Open,Focus` | The embed's own names for the tabs, in on-screen order, e.g. `labels=Catalogue,Shopping` for a shopping list (three names, from when Plan was a tab: the first and the last count). Only the words change: views, `view=` values and links keep their names. An empty entry keeps the default, `_` stands for a space (params split on whitespace). Rendered into the tab buttons by `buildTasksEmbed`; `tasks.js` reads them back (`tabLabel`) for the "← Back to …" link, the limit warning and the ⟲ tooltip. |

```markdown
[[tasks:]]
[[tasks: tag=urgent view=plan]]
[[tasks: view=open]]
[[tasks: list=groceries tag=weekly]]
[[tasks: view=focus limit=5]]
[[tasks: list=groceries view=open]]
[[tasks: tag=staple keep=1 labels=Catalogue,Shopping]]
[[tasks: tag=work plan=off]]
```

The params can be changed without editing the page: List settings… in the ⋯ menu of the embed's toolbar (see Settings).
In the editor, the formatting bar's Task list button shows the same dialog, with which list the embed shows, to write the embed or change the one the cursor is on (Task list dialog in `docs/spec/editor.md`).

**Headless instance**: `build_tasks_embed(params, headless=True)` renders the app hidden, for pages that only need its edit modal (the editor's Make task, see Task references).
`openEditModal(null, opts)` takes, besides `tags`, a starting `title`, `description`, `start_date` and an `onCreated(task)` callback; `titles` instead of `title` is batch mode (one task per title, the fields shared, `onCreated` gets the array).

**Implementation** (`go/tasksyntax.go` for the syntax, `go/tasksembed.go` for the HTML): `tasksRE` finds the directive and `parseTasksParams()` parses its `key=value` pairs.
`processTasksEmbeds()` swaps each one for a numbered placeholder before rendering, and `injectTasksEmbeds()` replaces each `<p>PLACEHOLDER</p>` with `buildTasksEmbed()`'s HTML (`templates/tasks_embed.html`) afterwards.
The embed passes its params as `data-init-*` attributes; it carries no script: `tasks.js` (an ES module `zonewiki.js` imports on every page) starts every `.tasks-app` on load (`initTaskApps`, marking each `data-ready`).
Element ids get a `-<n>` suffix after the first embed, so a page can hold several.
**A page brings its tasks along**: a page view with a task list (and `/tasks`) carries, once, the open tasks its lists show (`scopedTasks`: those of any of the lists' scopes, so My tasks or one list doesn't carry every open task the user sees; a list of every task, All tasks or a tag's view, takes them all) as `GET /api/tasks` answers them
and their notes' and comments' HTML as `POST /api/render-notes` would (`tasksInitial`: a `<script type="application/json" id="tasks-initial">` data block, which no browser runs; `<`, `>` and `&` in it are `\u` escapes, so no task's text ends it).
The lists the page starts with draw from it, each from its own copy, with no request (`initialTasks` in `js/taskdata.js`, `keepRendered` in `js/tasktexts.js`), so the list is there when the page is rather than two round trips later.
Every later load asks the server for the list's scope (`GET /api/tasks?list=`, `?lists=`, `?mine=1`: `scopeQuery`; the server's rule is the one `inScope` has in the browser), and so does a list that starts later (a preview's, the hidden one a page without a list fetches for its dialog): `initTaskApps` forgets the tasks once the page's lists have started.
`tasks.js` also reads `?tag=`, `?view=`, `?search=`, `?task=`, `?new=1` from the URL; `?new=1` opens the new-task modal in one embed only, one of My tasks or of the Inbox if the page has it, else the first.

The Tasks page itself is `/tasks`; embeds are for custom views on ordinary pages (a tag's tasks, a list with notes around it).
Nothing links to a `tasks.md` page.

**Task ↔ page linking** is a `[[wikilink]]` (or `/page/` link) in the task's title or description, which lists the task under the page's "Linked tasks".
The other way round, a page shows one task inline with `[[task:<id>]]` (see Task references).
There is no `page` field: `page` in embeds or API bodies is ignored.

## The task app

A task app at `/tasks` (a built-in page, `tasksPage()`), also embeddable via `[[tasks:]]` (see Task list embed).
Server side is `go/tasksapi.go` (the `/api/tasks/*` routes, `tasksRoutes`),
`go/tasks.go` (the writes, which the MCP tools share) and `go/tasksembed.go` (the embed's HTML, `taskCounts`) on top of `go/internal/taskfiles` (the files: where they are, read and written under one lock), `go/internal/taskmodel` (a task and its file's format) and `go/tasklogic.go` (recurrence, completing, history, comments, task backlinks) and `go/tasksyntax.go` (the syntax in page text).
Client side is `tasks.js` (DOM, one instance per embed) plus `tasks-core.js` (pure logic, tested under Node).

Several embeds per page work: each instance keeps its own task array, and a write in one refreshes the others (`refreshOtherInstances`).
Every instance also reloads on `visibilitychange` and bfcache `pageshow`, so a tab left open for days shows current tasks and today's day sections.
`refreshIfIdle` skips the reload while a drag, swipe, completion grace period, ⋯ menu or edit modal is in progress.
**What someone else changed shows within a minute**, without fetching tasks to find out: every write of a task file or a lists' registry gives the server another word (`taskfiles.Changes`: the start's time and a count; not a file changed by hand), which `GET /api/tasks` and `GET /api/tasks/due-count` answer in the `Zone-Tasks` header and a page's own tasks bring along (`"word"` in `tasksInitial`).
The page keeps the word of the tasks it holds (`<body data-tasks-word>`, `js/taskdata.js`); the due count it asks for every minute while it is used (`refreshDueBadge`, `whileUsed`) says the server's, and when that is another one the lists load again (`zone:tasks-changed`, `refreshIfIdle`; a list that was busy is told again the next minute).
After the page's own write the word is taken as its own.
It says nothing of what changed or whose, so another user's private write reloads a list too.

### Storage

- `_tasks/lists.json` is the lists' registry: the shared lists with their settings, the wiki's pins and the settings of All tasks; with users each user's own in `private/<user>/_tasks/lists.json` (their private lists, their pins, their settings of My tasks). It is no tasks file (see Lists → The registry).
  (Until 2026-11 the settings of All tasks were in `_tasks/settings.json`; the migration moved them.)
- The tasks' files live in the `_tasks/` folder (`tasksFile(owner)` and paths derived from it; with users each user's private tasks in `private/<user>/_tasks/`, the same files: Private tasks in `docs/spec/users.md`): open tasks in `tasks.json`,
  closed ones in `closed.json` (flat JSON arrays, committed to git on change).
  Closed tasks from earlier years live in `closed-YYYY.json` (only that exact name is an archive).
  `atomicfile.Write` creates the folder, so a new wiki gets it on the first write.
  Write routes read every closed file (`readClosedAllStrict`) and `writeClosed` files each task by its completion year, writing only files whose content changed; startup does the same split (`archiveOldClosed`) for a year change without writes.
  So archived tasks can be edited, reopened and tagged like any other; Closed shows them by year (see Views).
- All mutations go through one lock (`taskFiles.Lock`, `go/internal/taskfiles`) and atomic writes (temp file + rename, `go/internal/atomicfile`); the server handles requests concurrently.
- **A file is parsed once** (`readFile` in `go/internal/taskfiles`): every page view reads the open tasks (the Tasks links' count) and a write reads every file, so a file's tasks are kept as parsed and handed out as copies (`Task.Clone`: a reader may change its own) until the file is another one (its time, size or inode: Zone's own writes, which also forget it, `writeText`; a hand edit).
  Write paths use a strict reader that fails with `errTasksCorrupt` (503) on an unreadable file instead of treating it as empty and wiping every task.
- The files are JSON (`go/internal/taskmodel`): a list of tasks,
  two spaces of indent,
  text as written (no `\u` escapes but those JSON needs),
  each task's fields in one order (`id`, `title`, `description`, `tags`, `list` (only when it is in one), `assignee` (only when it has one), `start_date`, `due_date`, `created_at`, `closed_at`, `wont_do`, `keep`, `importance` (only when it isn't normal), `last_done`, `sort_order`, `recurrence`, `spawned_from`, `history`, `comments`),
  every field written,
  also when empty (`null`, `[]`, `false`),
  then any keys Zone doesn't know (from an older version or a hand edit),
  A–Z,
  their values kept as they were.
  A file is read leniently: a value of the wrong kind is its field's default (a number or a bool where text belongs is its JSON text, so an `id` of `7` is `"7"`; only `true` is true), so an odd file still reads and a write never refuses it for that.
  A write changes only the files whose tasks changed, so commits show only what changed.
  Files in another format (hand-edited: keys in another order, unknown keys not A–Z) are rewritten once when Zone starts, in a commit of their own ("Write the task files in Zone's format"); the tasks' values stay as they were (`1.0` reads `1`).
- Request bodies are validated (`jsonObject`, `asStr`, `asInt`, `asStrList`, `idList`): bad input is a 400 with JSON `{ error }`, never a 500. Numeric strings are accepted for integers; `tags` may be a comma-separated string.
- Reads clean the tags (`normalizeList`, `cleanTags`), since a file edited by hand may have `"local llm"` or `#work`.
  Older data formats are no longer converted: the conversions (task files in the wiki's root,
  closed tasks in `tasks.json`,
  `focus_date`/`defer_days`/`recurrence.anchor`/`page`/`focus_order`,
  spawns without `spawned_from`) were removed in 2026-10. A wiki last used before then must be opened once with an earlier Zone (git history) first.

### Dates

A task has two independent dates; changing one never moves the other.

- **`due_date`**: the deadline.
  Only a past due date makes a task overdue.
- **`start_date`**: the day the task becomes available, and the commitment to work on it.
  Having one makes a task *planned*.
  A past start date is not overdue, it means "available since" (cards show "since Sep 21").

**A start after the deadline** is usually a slip, but not always (a late task planned for next week), so it is never forbidden, only questioned.
Every quick way to set a start date (the Plan board, ⋯ Start today/tomorrow and Pick start date, the card's 📅 picker, **f**/**F**, **1**–**7**;
`setStart` and the picker go through `startsAfterDeadline` → `planOnDay`) asks "Move the deadline too?" (`confirmDeadlineMove`): Move deadline sets both dates,
with undo;
Cancel writes nothing.
An overdue task started today doesn't ask, on the board too: that is catching up on a missed deadline (swiping right never asks for that reason).
The edit modal shows a hint under the dates instead (`syncDateWarning`): "Starts after its deadline", with a button that moves the other date (the deadline, or the start when Due was moved before it).
It doesn't block saving.

**Locale** (`locale` in the settings file, a BCP 47 tag like `de-AT` or `en-GB`): how dates read and the day weeks start on, in the browser's `Intl` (no locale data in Zone).
`loadSettings` refuses a value that isn't a tag shape (`localeRE`);
`layout.html` puts it on `<body data-locale>`,
and `tasks.js` (`LOCALE`) passes it to `tasks-core.js`'s helpers: `shortDate` ("since …", history, comments; "Oct 5" or "5. Okt.", the year added when it isn't this year; or ISO, below),
`fullDayLabel` (the Plan board's days after Tomorrow),
and the date picker's month title (`monthTitle`),
weekday heads (`weekdayNames`, two letters) and first column (`weekStart`: `Intl.Locale`'s week info, Monday where the engine has none).
Unset: US-English dates and weeks from Monday, as before the setting (an explicit `en-US` starts them on Sunday).
Only dates: Today, Tomorrow, Tmrw, the shortcuts' labels and the rest of the UI stay English, and the edit modal's date buttons keep `YYYY-MM-DD` for days further off.
The task chips on pages are rendered by the server in US English inside `<time class="local-date" datetime>`, which `localizeDates` (`js/util.js`) rewrites in the locale: on the page view, a re-rendered chip, the editor's preview and the search modal's preview.

**ISO dates** (`date-format = iso`, the only value; anything else is an error in the settings file): dates read `2026-10-05` (ISO 8601) wherever the locale would write them (`<body data-date-format="iso">`, `TasksCore.dateSettings` → `{ locale, iso }`): `shortDate` gives the date itself,
always with its year;
the Plan board's days and the picker's tooltips are the locale's short weekday and the date (`fullDayLabel`, `longDayLabel`: "Mi 2026-10-21").
The calendar's month title, weekday heads and week start still follow the `locale` setting.
The server writes the task chips' dates as ISO itself (`shortDate`), so `localizeDates` leaves them.

The edit modal's "Days before due" is just another way to enter the start date (`start = due − N`) and is not stored; the dialog shows it once asked for ("+ Days before due", with a due date).
Recurring instances keep the gap between the two dates, but a new instance's start date is never before today (it can't have been available before the previous one was done); when that clamps it, the series remembers the gap in `recurrence.lead` for the next instance.

### Task fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | |
| `title` | string | Required. Links in it work on the card (see Title links) |
| `description` | string | Markdown, rendered as a page's text (see Description); `- [ ] item` lines are checkboxes |
| `tags` | string[] | One word each. `cleanTags()` on every write/read/restore: inner whitespace → `-`, leading `#` dropped, empties removed, case-insensitive duplicates collapsed. Tag inputs sanitize as you type (`TasksCore.sanitizeTagInput`), leaving deletions alone. A tag sorts tasks; it never puts one in a list. |
| `list` | string | The list the task is in, by its name; no key at all when it is in the Inbox (see Lists). Which list of that name is the task's file's to say: a task in a user's files is in one of their private lists (without `list`: in their Inbox), any other in a shared one. A write takes a list as the user names it (its name, or `shared/<name>`: Lists → Names), `""`, `null` or `inbox` for the Inbox (with users not for a shared task, which is always in a list: 400); a name no list has is a 400. With users the API also answers `"private": true|false`, from where the task is kept; `private` in a write is a 400 that points to `list`. |
| `assignee` | string | The user the task is assigned to (with users; Assigning tasks in `docs/spec/users.md`). No key at all when there is none: in the file and in the API's answers, so a wiki that assigns nothing keeps its files as they were. A write takes a user's name, `me` (whoever writes) or `""`/`null`/`none` (no one); a leading `@` and the case don't matter. |
| `due_date` | `"YYYY-MM-DD"` / `null` | Deadline |
| `start_date` | `"YYYY-MM-DD"` / `null` | Start date (see Dates) |
| `created_at` | ISO datetime | |
| `closed_at` | ISO datetime / `null` | |
| `sort_order` | number / `null` | Position in the Sorted section (Open's and Focus's, and inside Plan's days); `null` = Unsorted. Focus and Plan have no order of their own. |
| `recurrence` | `{ type, interval, from, day?, lead? }` / `null` | `type`: `daily`/`weekly`/`monthly`/`yearly`; `interval` 1–999 (`recIntervalMax`). Driven by the primary date (`due_date`, else `start_date`). `from`: `"due"` (default, calendar-aligned) or `"completion"`. `day` (monthly/yearly) is the series' day of month, so Jan 31 → Feb 28 → Mar 31; kept on re-save unless the type changes; moving the primary date re-anchors it, however it is moved (edit modal, date picker, a drag onto a day, MCP). An undo that moves a date back sends the old `recurrence` whole (`PUT`/`batch-update` take it, `cleanRecurrence`, applied after the date rules), so the series keeps its `day` and `lead`. `lead` is the start→due gap, stored only while a spawned start was clamped to today; editing either date drops it, an unchanged modal re-save keeps it. A keep task never has one (`keep` clears it). |
| `spawned_from` | string / `null` | Recurring instance: id of the completed task that spawned it, so undoing the completion removes exactly this instance |
| `wont_do` | bool | Abandoned: archived like a completed task, shown with "✗ won't do", never spawns a recurring instance |
| `keep` | bool | Keep task: completing it doesn't archive it (see Keep tasks) |
| `importance` | `"high"`, `"low"` | How much the task matters (see Importance). Normal, what a task is unless said, is no key at all: in the file and in the API's answers, so a wiki that sets none keeps its files as they were |
| `last_done` | `"YYYY-MM-DD"` / `null` | A keep task's last completion, shown on its card as "✓ Sep 21" |
| `comments` | `[{ id, at, by, text }]` | The user's comments, oldest first, uncapped (see Comments); `by` only with users. |
| `history` | `[{ at, event, by?, from?, to? }]` | What happened to the task, oldest first, capped at 30 (`historyLimit`): `created`, `start`/`due` (with `from`/`to` dates), `done`, `reopened`, `wontdo`, `skipped`, `repeat` (`to` = the next occurrence's date), `assigned` (with `from`/`to` user names, `null` no one). `at` is `YYYY-MM-DDTHH:MM`; `by` the user who did it, only with users. See History. |

### Views

A segmented control (`.view-tabs`, one `<button data-view>` each) in the order **Open · Focus** (`data-view` `open`/`focus`).
The Open tab shows one of two views (`activeView` in `tasks.js`): grouped by start date, the board (`plan`), or as one list (`open`).
The **Plan** switch right after the tabs (`button.tasks-plan-switch`, outside the toolbar: it modifies the Open tab, and on phones it shares the tabs' row, which keeps the search field at least 100px wide;
`role="switch"`, `aria-checked`;
shown on the Open tab;
**d** toggles;
not `p`, which starts presentation mode on a page with an embedded tasks) picks one: on,
the board;
off, the list.
A switch rather than a dropdown: it is one yes/no, seen at a glance and changed in one click, and "Plan" names what the board is for.
Its default is the embed's `plan=` (on without it; `data-init-plan`), set in the settings dialog.
A flip is remembered for the browser tab only (`sessionStorage` `zone-tasks-group:<path>|<list>|<tag>|<instance>`, `days` or `list`), as the active tab is, so a fresh load is back at the default, and the Open tab (a click, Tab) opens it (`tabView`, `openGroup`).
(Until 2026-11 the flip was kept in `localStorage` for good, and there was no default to set.) The views keep their names, so `?view=plan` / `view=plan` open Days and `view=open` the List, as they always did.
(Plan was a tab of its own until 2026-10; with every section in the manual order and labelled Unsorted/Sorted, it had become Open grouped by day, and the List what's left of Open: ranking across days, and every new task in one Unsorted list.) **Tab**/**Shift+Tab** cycle the tabs.
Initial view, when a page is opened: URL `?view=` > embed `view=` > tab remembered in `sessionStorage` > Focus.
**A reload (F5) keeps the tab**: the tab shown is remembered for the browser tab (`sessionStorage` `zone-tasks-shown:<path>#<instance>`, saved when a tab is chosen and when the page opens on one), and on a reload (`performance`'s navigation type `reload`) it comes first, before the URL's and the embed's `view`.
The tab the user chose is kept apart (`zone-tasks-tab:<path>#<instance>`): it is what a page without a `view` opens on, and a tab a link chose doesn't become that.
So a task opened from a link (`/tasks?task=…&view=open`) and then left for Focus is still on Focus after F5; the link's task is then not selected again either.
Opening the page anew (a link, the address bar, another browser tab) follows the URL and the embed as before.
On phones the control spans the full width.

- **Open, Plan off** (`open`): every open task, in two sections: **Unsorted** (no `sort_order`, newest first; new tasks land here) above **Sorted** (manual order).
  Open has no filter for unplanned tasks (an "Unplanned" toggle was removed): those are the Plan board's Unplanned section, where dragging to a day plans them.
  So Unsorted means "not yet ranked" and Unplanned "not yet scheduled", each with one home.
  A leftover `unplanned=` embed param is ignored.
- **Sorting** (Open and Plan): an **Order** dropdown in the toolbar (`select.tasks-sort`, shown on Open and Plan) picks **Manual**, **Due date** or **Created**; **o** cycles them.
  Due sorts by deadline, nearest first (overdue on top), tasks without one last, each in Open's order within (`TasksCore.byDeadline`); Created newest first (`TasksCore.byCreated`).
  They answer "what is due soonest" and "what came in lately", which the manual order doesn't.
  On Open a sorted list is flat: Due under **By deadline** and **No deadline**, Created under **Newest first**.
  On Plan the sections stay (the board still places tasks by start date) and each is sorted inside; Unplanned's overdue and Today's carried-over lists stay by deadline and by start.
  Nothing reorders in a sort, since the manual order is only edited where it is shown: Open doesn't drag, on Plan no section is `ordered`, so a card dropped on another card is a drop on its section (dragging between days still plans), and Ctrl+Shift+↑/↓ only says to press `o` until Manual.
  Each tab's sort is remembered per task list in this browser (`localStorage` `zone-tasks-sort:<path>|<list>|<tag>|<instance>`, JSON `{"open": "due", "plan": "created"}`; missing means manual).
  Focus has none: it is the day's manual order, worked top to bottom.
- **Open, Plan on** (`plan`, called Plan below): a day board grouped by start date (`TasksCore.bucketKey`): Unplanned (no start date), Today (with the tasks **carried over**: started on an earlier day and still open, `TasksCore.isCarried`), Tomorrow, the next weekdays, Later.
  Unplanned is first because planning starts there: its cards are dragged onto the days just below, not up past the whole week.
  **Overdue has no section**: a missed deadline shows on the card (red), and the task sits where its start date puts it (an earlier start or today: Today; a later one, accepted in the edit modal: that day).
  Only a deadline today puts a task without a start date on Today.
  Unplanned's header counts its overdue tasks in red (`· 2 overdue`, `.board-day-overdue`; `boardBuckets` gives Unplanned its `overdue` list), shown even when it is folded, so folding can't hide them.
  Later and Unplanned always show.
  Dropping a card on a day sets its start date, on Unplanned clears it, on Later opens a date picker (min today+7).
  An empty section is only its dashed header row (min 1.9rem, 2.5rem on coarse pointers), which takes the drop; nothing grows when a drag starts.
  Day headers show a count and turn amber at the limit.
  Each day's header also says how many tasks are **due** that day, wherever their cards sit (`· 2 due`, muted, `.board-day-due`; `boardBuckets` gives each day its `due` list): the board places cards by start date, so without it a deadline would be invisible until its day.
  A click (or tap) on it, or on Unplanned's overdue count, lists those tasks in a menu (a bottom sheet on phones; `headerTasksMenu`), and picking one selects its card; the tooltip names them too.
  **Folding**: a section with cards folds from its header: a click anywhere on the header (`.board-day--foldable`, `data-fold`;
  the label with its ▾ chevron is the button, `.board-day-fold` with `aria-expanded`, for the keyboard and screen readers;
  only the due or overdue count opens its own list) hides its cards,
  the header with its counts stays (`.board-day--folded`).
  Typically Unplanned, so a long backlog doesn't push Today off the first screen.
  A folded section still takes drops (its header is the drop target), and its cards aren't rendered, so j/k, Ctrl+A and the selection skip them.
  Folding is remembered per task list in this browser (`localStorage` `zone-tasks-folded:<path>|<list>|<tag>|<instance>`, a JSON list of `foldId`s: a day by its distance from today, `day1` for Tomorrow, so it stays folded as the days pass; the other sections by name).
  It never hides what you look for: while the viewer filters every section shows (without fold buttons; `userFiltering`: their own words, not the locked chips of a `list=` or `tag=` embed),
  and a task that has to be seen unfolds its own section (`unfoldFor`): a `?task=` link,
  a new task,
  a pick in a header's due or overdue list.
  **Order**: in Manual (see Sorting) inside every section tasks are in Open's order (`TasksCore.byOpenOrder`, shared with Open), and they can be reordered there, which edits that one order (there is no Plan order): plan tomorrow in the evening and put it in order on the spot.
  A card dropped on a card of a day or Later (`data-ordered` on the header) lands above or below it by the pointer, a line showing where (`dropSide`, `.task-drop-before`/`-after`, mouse and touch);
  on another day it also starts the task there (`planOnDay`, so a deadline it passes still asks), start date and place in one move.
  Dropped on a Later card from elsewhere, it asks for the date as on Later's heading and lands where Open's order puts it.
  Ctrl+Shift+↑/↓ moves the selection one place within its section, never out of it (`planKeyMove`), crossing the Unsorted/Sorted labels as on Open (below).
  As on Focus, only the moved tasks change place in the full order and hidden tasks keep theirs (`TasksCore.placeInOrder`).
  **Unsorted / Sorted labels**: a section with Unsorted tasks (listed first) labels them **Unsorted** and the rest **Sorted** (`.plan-subdivider`), as Open's sections do one level down;
  a section without any has no labels, so most days show none, and the labels appear where something is still to be ranked (often Today: a task added on Focus starts today, unsorted).
  Lighter than the day headings on purpose: as headings, "Sorted" would read as another day.
  Moves follow Open's rules, through Open's own code: `planSectionList` gives the section as Open lists its sections, `[#unsorted, …Unsorted ids, #sorted, …Sorted ids]`, and `applySectionList` / `sectionKeyMove` do the rest.
  So a task dropped among the Unsorted ones leaves the order (Unsorted is newest first, nothing moves within it), one dropped among the Sorted ones takes that place, and no other task is ranked as a side effect;
  Ctrl+Shift+↑ on the first Sorted task takes it into Unsorted, ↓ on an Unsorted one to the top of Sorted.
  The **Sorted** label takes drops (`data-sub="sorted"`, mouse and touch): first among the sorted, as Open's Sorted heading; the Unsorted label doesn't (a drop on it is a drop on its section).
  (Until 2026-10 a section had no labels, and a drop above an Unsorted task ranked it and every Unsorted task below it, so the day kept the order shown without a visible boundary.) **Unplanned** is the backlog, ranked while planning: it reorders like a day.
  A card from elsewhere dropped on an Unplanned card unplans it (as on the heading) and lands where Open's order puts it.
  Its header still counts the overdue tasks, most overdue first (Due date sorts the whole board by deadline).
  It used to be sorted by deadline and not reorderable; that hid the backlog's manual ranking where it is planned.
  A drop on a task's own heading does nothing, except Later's, which opens the date picker to choose another later date.
  **Carried over has no section**: those tasks are on Focus already (a past start means available), so they are today's load, and Today holds them, in Open's order with the rest, so Today reads as Focus does and its count and limit include them.
  A slipped plan stays visible without a section of its own: on Plan a carried-over card's "since Mon" badge is amber (`.task-carried`),
  and Today's header counts them,
  amber,
  before its due count (`· 3 carried`, `.board-day-carried`; `boardBuckets` gives Today its `carried` list, the longest-slipping first; a click lists them, `headerTasksMenu`, shown even folded).
  Committing to one again is optional: dropping it on Today's heading (its own section, but `dropOnBucket` starts it today: `recommit`) or swiping it right starts it today, which takes the badge away.
  (Carried over was a section of its own above Today until 2026-10;
  it made Today's count and limit leave out part of the day's load and split one list, Focus's, in two.) A drop on a day after the task's deadline (`planOnDay`, through `startsAfterDeadline`;
  not an overdue task dropped on Today) asks "Move the deadline too?" (`#task-deadline-modal`): Move deadline sets start and due to that day,
  with an undo toast restoring both;
  Cancel writes nothing.
  Wiring: `setupBoardDragAndDrop` per card plus list-level handlers attached once.
- **Focus**: what you're working on now (`TasksCore.isFocus`: `start_date <= today`, or due today/overdue; on the board: Today, its carried-over tasks included, and overdue tasks wherever their start date puts them).
  It is Open filtered: the same sections (Unsorted · Sorted) and the same order (`byOpenOrder`);
  its section headings look the same as the Open list's: the light labels of the Plan board's Unsorted/Sorted (`sectionHTML`: `.plan-subdivider.list-section`, see Sections), so there is one manual order, and ordering on Focus orders Open.
  That fits a shopping list: Open is the shop's order, Focus today's list, and an item added on Focus (Unsorted) is dragged into place among today's items, which places it in the shop's order (see Sections).
  Due and overdue tasks have no section of their own: they keep their place in the order, marked by their deadline colour.
  The Tasks links (with the due count) open here unless another tab was used this session.
  With a limit, a warning shows when Focus reaches it.
  Empty Focus says "Nothing planned for today" and links to Plan.
  New tasks created here get today as start date.
  (Focus once had its own order, the Lineup (`focus_order`) above Others; it was removed as a second commitment on top of the start date.)
- **Closed** is not a tab but a link under the list (`#task-closed-link`, `Closed (N) →`, count from `/api/tasks/closed-count`, refreshed after every write).
  It holds the tasks that are closed: done, and closed undone (won't do), which is why it isn't called Completed.
  That is its name everywhere: where people read it (the link, the view's heading, the settings dialog), in the params (`view=closed`), the routes (`/api/tasks/closed`), the files (`closed.json`), a task's `closed_at` and the code.
  It was called Completed: a wiki from then is renamed when Zone starts (`renameOldClosed`: `completed.json` and `completed-YYYY.json` become `closed.json` and `closed-YYYY.json`, never over a file that is there; a task's `completed_at` is read where it has no `closed_at`, and the files are written again with the new field), in one commit, "Name the task files of closed tasks closed".
  Nothing else is kept of the old names: a page's `view=completed`, a link with it and a client asking the old routes get what an unknown view or route gets.
  In Closed, the link reads `← Back to <tab>`, naming the tab it returns to.
  While a filter is on (text or a tag in the field, an embed's `tag=`) it reads `Closed →`, without a number: the total would read as what the filter leaves.
  The filter carries over into Closed, so a click shows the closed tasks it matches.
  (The link once counted them, `2 matches in Closed →`. That took every closed task of the year and its texts' HTML, loaded behind each filtered list right after it was drawn, and a second draw of the list when they arrived: too much for a number.)
  `/api/tasks` returns open tasks only, so closed tasks are fetched only when Closed is opened (`fetchClosed`, one request at a time); `tasksGen` refetches if `fetchTasks` replaced the list meanwhile.
  Closed's heading has a **year switcher** (`.closed-years`, years from `/api/tasks/closed-years`, shown when there is more than one) for the yearly archives;
  `?year=YYYY` opens that year (search links to archived tasks carry it), and going back to a tab returns to this year (`setClosedYear`).

**Sections** (Open and Focus; `SECTIONS`, `sectionHTML`, `.list-section[data-section]`): Unsorted (no `sort_order`, newest first) above Sorted (`sort_order`).
Both tabs head them alike, with the light label a Plan section uses for its Unsorted/Sorted (`.plan-subdivider`), not a day heading, so the labels look the same wherever they appear; Open's sort headings (By deadline, No deadline, Newest first) take the same label.
A heading is also an empty section's drop target, so it is at least 1.6rem tall (2.5rem on coarse pointers).
(Until 2026-11 Open's were day headings and Focus's light labels.) Moves treat both headings as slots in the visible list (`sectionList`, `applySectionList(list, moved)`): which side of the Sorted heading a task lands on decides its section;
a task dropped on a task lands where the pointer is: above it on its upper half, below it on the lower half, from Unsorted and within Sorted alike, a line on that edge showing it while dragging (`dropSide`, `.task-drop-before`/`-after`, mouse and touch; `sectionDrop`);
dropping on the Sorted heading moves the task to its top;
reordering within Unsorted does nothing;
leaving Sorted sets `sort_order` to `null`.
Ctrl+Shift+↑/↓ moves one slot, headings counting.
Only the moved tasks change place in the full order: each goes right after the visible task now above it (or right before the one below, when first), and every task the view or filter hides keeps its place.
So on Focus, sorting in one item never shuffles the catalogue around it.
Every save renumbers the whole order densely.

**No hiding**: every open task is always reachable from its own tasks (Open or Plan) and from search.
Dates decide where a task appears, never whether.

### Settings

**List settings…**, the first item of the list's **⋯ menu** in the toolbar (`#task-more-btn`; the toolbar has no button of its own for it), opens **List settings** (`#task-settings-modal`, `js/tasksettings.js`): what the list is and how it opens.
A setting is kept where it belongs: a list's own with the list, how a page looks at a list in that page's `[[tasks: …]]`.

| Field | Setting | Whose | |
|-------|---------|-------|---|
| Opens on | `view` | the page's embed; the list's on `/tasks?list=` | The Open or the Focus tab (by their names here); Closed is offered only where the page says `view=closed` |
| Plan | `plan` | the list's | The Open tab grouped by start date, or one list |
| The pin (a button beside the title) | | yours | Whether you pinned the list (Lists → Pins) |
| WIP limit (on request) | `limit` | the list's | 1–99, empty for none |
| Tab names (on request) | `labels` | the list's | The Open tab's and the Focus tab's; empty keeps Open and Focus |
| Tags of a new task (on request) | `new` | the list's | Comma-separated; empty: the single tags the view shows |
| Keep tasks (on request) | `keep` | the list's | New tasks there are keep tasks |
| Hide when sharing (on request) | `noshare` | the list's | The list is left out of the list switcher in sharing mode |
| Leave out of My tasks (on request) | `home` | the list's | `off`: its tasks are not in My tasks nor in the Tasks links' count (Lists → My tasks). Offered on a list that is yours: a private one, without users any |
| Shows only the tags (on request: Filter by tag) | `tag` | the page's embed | The filter: `a,b` both, `a\|b` either, `-a` without |
| Shows only the tasks of (on request: Filter by person; with users) | `assignee` | the page's embed | `me` whoever looks, a user's name, `a\|b` either, `none` no one's, `-a` not theirs |

An embed of no list (`[[tasks: tag=work]]`) has all of them but the pin, Keep tasks, Hide when sharing and Leave out of My tasks, written in its `[[tasks: …]]`.
All tasks (`/tasks`) has Opens on, Plan, WIP limit, Tab names and Tags of a new task, for everyone.
My tasks (`/tasks?mine=1`) has Opens on, Plan, WIP limit and Tab names, yours alone.

**It shows what is changed most**: Opens on, Plan and, on a list, the pin.
The other fields show only when set as the dialog opens (`[data-field]`, `showField` in `settingsForm`); for the rest a line of quiet words under the fields offers each, "+ WIP limit", "+ Tab names", "+ Tags of a new task", "+ Keep tasks", "+ Hide when sharing", "+ Leave out of My tasks", "+ Filter by tag", "+ Filter by person" (`.task-add-fields`, as in the task dialog): a click shows the field and puts the cursor in it (a box is ticked).
**The pin** is a button at the title's right, with the list switcher's pin (`.task-settings-pin`, `aria-pressed`): "Pin" on a list you haven't pinned, pressed and "Pinned" on one you have; a click pins or unpins.
Which pinned list's pill is first is said in the list's ⋯ menu (**Move first**), not here.
The line above Save says whose the settings are: the list's, for everyone (or "which only you see" for a private list), the page's, and that the pin is yours.

**Where it writes** (`data-settings-*` on `.tasks-app`, from `buildTasksEmbed`: `-page`, `-index`, `-raw` and `-embed` for an embed's `[[tasks: …]]` on a page, as written there; `-list`, `-list-set` and `-pin` for the list shown: its ref, its own settings as JSON, and how you pinned it):

- **A list's own settings**: the registry (Lists → The registry), wherever the dialog was opened: on `/tasks?list=<name>` or on a page that shows the list.
  They hold wherever the list shows.
- **An embed on a page**: that `[[tasks: …]]`: for a list the tab and the filter, for an embed of no list everything.
- **The pin**: your pins (`POST /tasks/pin-list`).
- **All tasks** (`/tasks`): the shared registry's `all`, `"all": {"view": "open", "limit": "5"}` (`allTasksParams`), for everyone.
  It has no pin and no filter (`allSettingKeys`).
- **My tasks** (`/tasks?mine=1`): your registry's `mine`, `"mine": {"view": "focus", "limit": "5"}` (`mineParams`, `setMineParams`, `mineSettingKeys`; without users the shared registry's), for you alone.
  `"mail": "off"` there turns off the mail about a task assigned to you (**Mail me when a task is assigned to me**, a box that is ticked unless turned off, shown where Zone could mail you: `data-settings-mail`; Assigning tasks in `docs/spec/users.md`).
  It has no pin and no filter, and the dialog says "For My tasks: yours alone."
  Unset, it opens on Focus with Plan on and no limit.
  (Until 2026-11 the view, then called Pinned, showed its first pinned list's settings and had no dialog.)

**Not which list it shows**: `list=` isn't in this dialog (it is in the editor's, where the dialog writes the embed: `.Editor` in `task_settings_dialog`).
Another name there would show another list, which reads like a rename and isn't one; a list is renamed by **Rename…** in the ⋯ menu of `/tasks?list=<name>` and made by **New list…** there.

**No ⋯ menu, so no settings**: the editor's preview, an old version, a page from the uncommitted list (only a page view renders its own file: `RenderOpts.EditSlug`) unless it shows a list you may change, a name no list has, the headless app, a viewer, sharing mode.

**Saving** (`POST /api/tasks/settings`, `tasksSettingsRoute`): the dialog sends only what was changed (`changedParams`), to each place its own: `{list, set}` for a list's settings (`setListSettingsOp`), `{page, index, was, set}` for an embed, `{all: true, was, set}` for All tasks, `{mine: true, was, set}` for My tasks. `set` maps a setting to its new value, `""` taking it out; `was` is an embed's params as the dialog saw them.
So saving without a change writes nothing, and a param the dialog doesn't know stays as written.
The server checks each value (`checkEmbedSettings`: one word without `[`, `]` or a backtick; `view` a view, `plan` on/off, `limit` 1–99, `new` cleaned like tags, `keep` and `noshare` a yes; a list takes only its own settings, `listSettingKeys`, an embed only an embed's, `embedSettingKeys`: `list` and `pin` are refused).
An embed's params are rewritten in place (`setEmbedParams`, `setEmbedArgs`): a param keeps its place,
a new one goes last,
one written twice is written once;
the rest of the page is untouched.
For an embed of no list, Opens on and Plan are written as the views' names: Open with Plan is `view=plan`, Open without `view=open`, and `plan=off` only where `view=` isn't one of those two; for a list the tab is `view=open` or `view=focus` and Plan the list's `plan=on` or `off`.
A page is changed in one commit, "Change the task list's settings on <slug>" (`changePageOp`: what it held uncommitted is committed first), a list in "Change the settings of list #<name>", All tasks in "Change the settings of All tasks", My tasks in "Change the settings of My tasks".
When the `[[tasks: …]]` is no longer as the dialog saw it (the page was edited meanwhile), nothing is written: 409, "The page has changed since.
Reload it and try again." Other refusals: 400 with the reason, 403 for a viewer, 404 for a page or a list you can't see; the dialog shows the message.
After a save the page reloads, and what the browser tab remembered (the active tab, a flip of the Plan switch) is forgotten, so the list opens as just set (`forgetSession`).

### Export and import

**A list's tasks as a file**, to keep a copy, move a list to another wiki or hand it to someone.
**Export tasks** and **Import tasks…** are in the list's **⋯ menu** in the toolbar, under List settings… (`#task-more-btn`, `.tasks-more-btn`; the cards' menu, `showMenu` in `js/taskmenu.js`, a bottom sheet on phones; the code in `js/tasksettings.js`).

- **Export tasks** (`GET /api/tasks/export?list=&tag=&assignee=`, `?mine=1` for My tasks; `apiExport`): downloads `zone-tasks-<list>-<date>.json` (`all` for All tasks, `my-tasks` for My tasks;
  the name goes into `Content-Disposition` quoted and escaped, `mime.FormatMediaType`, as the list's name is the request's): the tasks the list shows (its `list=` and `tag=`, not what is typed in the search field),
  open and closed of every year,
  each as the API shows it,
  with its description,
  dates,
  recurrence,
  comments and history: `{"zone_tasks": 1, "list": "shop", "tag": "", "exported_at": "…", "tasks": […]}`; each task says the list it is in (`list`).
  With users it holds the shared tasks and your own private ones, marked `"private": true`.
- **Import tasks…** (`POST /api/tasks/import`, `apiImport`): pick such a file, and a dialog (`#task-import-modal`) says what it holds ("Import 12 tasks from “shop.json”") and asks for a tag of your own; **Import** adds its tasks to the list the menu was opened in, Cancel nothing.
  With users, a task assigned to someone who can't be given tasks here comes in as no one's, and the toast says how many ("…; 2 were assigned to people who are not users of this wiki").
  A file that holds no list of tasks is refused at once, in a notice.
  They go into the list the menu was opened in, whatever list they were exported from, so a `shop` export imported into `groceries` shows up there, and they get the tags a new task there would (`new`, or the single tags the view shows); their own tags stay.
  On All tasks (and a tag's embed) each goes where its own list says: the list of that name here, when there is one as private as the task was; else a private task into your Inbox, and a shared one, with users, into the shared list `general` (made for it; without users into the Inbox).
  Imported on the Inbox's view, every task goes into the Inbox: with users yours alone, without an assignee.
  Closed tasks arrive closed.
  **A task keeps its id**, and one whose id is in the wiki already is skipped: importing the same file twice adds nothing, and a file exported here brings back only what was deleted since (an id that isn't a UUID is replaced).
  So a list can't be duplicated within one wiki this way.
  A task exported private stays private, wherever it is imported: into a shared list it goes to your Inbox instead; into a private list of yours every imported task is private, as a new one is.
  **A tag of your own** (the dialog's field, optional; `also`): the imported tasks get it too, on top of the list's, to find the batch again later (`from-anna`); cleaned like any tag, several comma-separated.
  One commit, "Import N tasks".
  The notice says what happened ("Imported 12 tasks; 3 already here.") and has **Undo** (also Ctrl+Z, for the few seconds it shows; `showUndo`): the tasks just added are deleted again (`/api/tasks/batch-delete` with the `ids` the import answered).
  A file that isn't an export (no `tasks` list, or a task without a title) is refused whole (the server's reason shows in the dialog), as is one with more than 10,000 tasks (`importMax`); a bare JSON list of tasks is taken too, so a file written by hand or by a script needs only `[{"title": "…"}]`.
  A viewer can't import (403).

The ⋯ menu is there where a list has settings (see Settings: not for a viewer, nor for a name no list has).

### Look

Tasks have their own tones, set on `.tasks-app` (so a task card elsewhere keeps the page's): `--border` a step darker than the wiki's, which every line in it uses (cards, section headers, labels, fields), so cards and sections stand out;
cards plain white on the warm paper of the page and on the white sheet,
where a firmer border and the shadow set them off (`--card-bg`;
in dark mode a step lighter than the page;
the urgency tints are mixed with it, so a tinted card is never lighter than a plain one;
not a hue: the urgency tints use red, orange, yellow and green, and a green or warm tint on the cards swallowed them;
blue was tried and dropped) with a soft resting shadow (`--card-shadow`;
hover still lifts it, `--shadow`);
Closed's heading a shade deeper than the cards (`--section-bg`);
the day headers are lighter, a heading on a line rather than a filled bar, an empty day a dashed box (its drop target).
A card's coloured left edge (3px; planned, see Focus and Plan) never shifts its content: a card without one makes up the 2px in padding, so checkboxes and titles line up on every card and every tab.
Titles are bold and tags plain muted words (the list's chip has its tint and icon), so the cards' urgency colours stand out.
Unsorted cards are hollow,
on Open,
Focus and in a Plan section with Unsorted/Sorted labels alike (`.task-inbox`; not in a sorted view, which has no Unsorted section): a dashed frame in a firmer grey than a card's border,
no fill and no shadow (they read as not placed yet; a grey fill took the code blocks' beige),
urgency colours their tint.
Dates as text take readable shades (`--amber-text` for a start today and the carried-over badge and count: `#d97706`, the Focus limit warning's amber, in both modes;
bold, since in light mode it is about 3:1;
`--blue-text` for a later start, a slate blue in Zone's palette (the note callout's), `--green-text` for a deadline today): the bright colours of the cards' left edges are only about 2:1 on a card;
the blue and green are at least 4.5:1, a darker shade in light mode and a brighter one in dark.
Each with a dark-mode value.

### Interaction

- **Filter field** (`#task-filter-search`, `TasksCore.parseFilter`/`matchesFilter`): `#words` filter by tag (all must match; `#a|b` = any), `-#word` excludes a tag (`-#a|b`: neither), `list:shop` shows the tasks in that list (`list:a|b` either, `-list:shop` not in it, `list:inbox` in no list, the Inbox, as `list:none` says too; a list is no tag, so `#shop` doesn't find it), `!high` (or `!`) and `!low` show the tasks of that importance, the rest searches title and description.
  `/` focuses it, `#` focuses it and starts a tag.
  Its tooltip lists the syntax (with users the `@` words too).
  While it holds something of yours (text, or a chip the page didn't set) an **×** at its end (`.chip-input-clear`, "Clear the filter") empties it: the text and your chips go, a page's locked chips stay, and the cursor stays in the field.
  With users, `@words` filter by assignee the same way (`@anna`, `@anna|bob`, `-@anna`, `@me`, `@none`), `@` focuses the field and starts a name, and typing one suggests `me`, `none` and who has tasks here (Assigning tasks in `docs/spec/users.md`).
  Typing a `#word` (or `-#word`) suggests tags of the tasks the other tag words leave visible (free text is ignored for this).
  Embed `tag=`/`?tag=` become a query via `TasksCore.tagParamToQuery`.
  New tasks get the filter's single tags.
- **Tag chips** (`chipField` in `tasks.js`): in the filter field and the edit modal's Tags field a completed tag becomes a chip in front of the typed text (`.chip-input` box wrapping the input, `.tag-chip`).
  A chip can't be edited, only removed: Backspace at the start of the field removes the last one, its × any.
  Filter chips are whole filter words (`#a`, `-#b`, `#a|b`); the query is the chips plus the text (`filterQuery()`), and `?tag=`/`[[tasks: tag=…]]` arrive as chips.
  In the Tags field (and the bulk tag dialog's) a completion, a comma or a space (typed or pasted: `#a #b, c` gives three tags; `chipsOnSeparators`) makes a chip, with no separator left behind, so several tags go in at once;
  tags are one word each, so a space ends one rather than becoming a `-` (other tag inputs still turn spaces into `-`).
  In the Tags field Enter with no suggestion open makes the typed tag a chip instead of saving (Enter on an empty field saves); Save takes the chips plus a half-typed tag (`editTags()`).
  The dropdown hangs under the whole box.
- **Tag completion** (`attachTagAc`: filter, Tags field, title `#tag` shorthand, bulk tag dialog `t`/`T`): ranked by `TasksCore.rankTags` (exact, prefix, substring, in-order letters; then usage count, then A–Z).
  The best match is pre-highlighted so Tab/Enter completes it; Esc closes only the dropdown.
  In the Tags field and the bulk tag dialog a tag may be typed with or without its `#` (`tagWordOf`): `#wo` and `wo` suggest the same tags, a lone `#` lists the most used, and the chip is the tag without it.
  Keys are handled in the capture phase.
  The dropdown is appended to the field's parent, since in a `<dialog>` only the top layer is visible.
- **Selection** (`selectedId`, `selectedIds`, `anchorId`): a click (mouse) selects one task;
  **Shift+click** or **Shift+↑/↓** (j/k) selects the range from the anchor;
  **Ctrl/Cmd+click** adds or drops one task (and becomes the anchor);
  **Ctrl/Cmd+A** with a task selected takes every task shown (what the filter leaves; without a selection it stays the page's select all);
  **Esc** clears it (an expanded task collapses first).
  A re-render keeps the selected tasks still shown.
  On several selected tasks **Space** completes (or reopens) them all, **Delete** closes the open ones undone (won't do), **t**/**T** open the bulk tag dialog, and **Ctrl+Shift+↑/↓** moves them.
- **Selection bar** (`#task-selbar`, `.tasks-selbar`, `syncSelBar`): while several tasks are selected,
  a bar fixed at the bottom of the window: "N selected",
  **All** (`selectAllVisible`; disabled when every task shown is selected),
  **Tag…** (the bulk tag dialog),
  **List…** (the list dialog, below),
  **Assign…** (with users: who has them, Assigning tasks in `docs/spec/users.md`),
  **Today** (start them today, `startToday`: one `batch-update` for the open ones not starting today, one undo putting back each one's start date and history; today is never after a deadline still to come, and starting an overdue task today is the catch-up case, so no deadline question),
  **Unplan** (clear their start dates, `startOn(ids, null)`, as the **0** key: one `batch-update` for the open ones that have one, one undo putting them back; shown while an open selected task has a start date, `anyPlanned`),
  **Done** (`completeMany`, as Space; **Reopen** when every selected task is closed, which also hides Today and Won't do),
  **Won't do** (`wontDoMany`, as the Delete key) and ✕ (clear).
  Toasts move up above it.
  The **⋯ menu** of a card in the selection (its button, a right-click, which then keeps the selection, or Shift+F10) offers the same for all of them, titled "N tasks" (`runSelectionAction`).
  With several task lists on a page only the active one's bar shows (`claimInstance`).
  At ≤640px the bar spans the bottom edge in place of the phone tab bar (hidden while it shows): "N selected", All and ✕ on top, the actions as buttons sharing the width under them (on a second row when one doesn't hold them), ≥44px tall; the page end and toasts keep clear of it.
- **Moving tasks to a list** (`openBulkListModal`, `.bulk-list-modal`; the selection bar's **List…**, **Move to list…** in a card's ⋯ menu, for the selection or that one task, and the **m** key on the selected tasks): the dialog, "Move 3 tasks to a list", lists every list you see (A–Z by name, a private one with 🔒, the shared one of a name you have a private list of as "name (shared)"), **Inbox** (with 🔒 with users) and **New list…**.
  A click on a row moves the tasks at once (↑/↓ go through the rows, Enter picks, Esc or Cancel leaves).
  The move sets each task's list in one `batch-update`; its tags stay.
  The row of the list every selected task is in is ticked and has the focus; a list only some are in says how many ("1 of 3").
  **A task is as private as its list** (Lists → Private lists): the rows of your private lists, and with users the Inbox's, are off while a shared task is selected ("a shared task stays shared"), and a row that would take a private task to a shared list says so ("shares it with everyone") and sends `share`, and with it `assignee`, you: a task of yours that you file stays in your My tasks (assign it to no one, or someone, afterwards).
  One undo puts every task back in its list ("3 tasks moved to list “work”", "3 tasks moved to the Inbox"), but those that were private and are shared now: that can't be taken back, so a move of only such tasks has a notice and no undo.
  The selection stays, as after Tag…; in a list's own view the moved tasks leave it.
  **New list…**, the last row, makes a list and moves the tasks into it, so the dialog is there with no list too (then Inbox, ticked, and New list…).
  The rows give way to the list's name (`.bulk-list-form`, `#bulk-list-name`), **Create and move** (or Enter) and **Back**; a name that won't do is said under it and the dialog stays ("There is a list #shop already").
  With users it asks **Private: only you see it and its tasks** (`#bulk-list-private`): off and not to be ticked while a shared task is selected, ticked when every selected task is private, so no task is shared by not looking; unticked with a private task selected, a line says that everyone will see it and that it can't be made private again.
  The list is made as New list… on the Tasks page makes it (`POST /api/tasks/lists`, `apiNewList`: `createListOp`), in a commit of its own before the move's, and the page stays: every task list on it knows the new list at once (the `zone:list-made` event, `tasks.js`), and on `/tasks` the list switcher is drawn again with it (`js/listsrow.js`).
  **It is pinned, last, beside the lists you pinned, but it is never your first pin**: with nothing pinned it is under More, since a first pin changes where the Tasks links go and where new tasks land (New list…, Lists), which a list made to hold a few tasks shouldn't.
  The toast says "3 tasks moved to the new list “shop”"; its undo takes the tasks back and leaves the list, which **Delete…** removes.
- **Bulk tags** (`openBulkTagModal`, `.bulk-tag-modal`; **t**, or **T** to remove, on the selected tasks): the dialog lists every tag the selection carries (`TasksCore.selectionTags`, case ignored, most common first),
  each a three-state box (`role="checkbox"`, `aria-checked`): on every task ✓,
  on some – ("2 of 3", left as it is),
  on none (struck through, the tag goes).
  A click or tap cycles all → none → some, back to some only where the tag started so.
  The field below adds tags as chips (`chipField`; completion from every tag in use, minus those every selected task has; a comma makes a chip).
  Opened with T, the field has no chips: it completes to the selection's own tags and turns the completed one off.
  Enter or Apply sends only the changed rows plus the typed tags (`TasksCore.bulkTagChange`) in one `batch-tag` request, a half-typed tag included; nothing changed sends nothing.
  The toast names the change ("#work added to 3 tasks", "#old removed from 2 tasks", else "Tags changed on N tasks"; counting only tasks whose tags changed), or where tasks went if the change took them out of the view; its Undo puts back each changed task's tags in one `batch-update`.
  On touch the field is not focused while there are rows to tick, so the keyboard waits until it is tapped.
- **Quick-add** (new tasks only, `TasksCore.parseQuickAdd`): trailing shorthand like `Call Bob tomorrow #work due:fri`.
  Start: `today`, `tomorrow`/`tmrw`, a weekday (next one, never today), `+3`/`+3d`/`+2w`, `YYYY-MM-DD`; due: `due:` + the same; tags: `#tag` starting with a letter, of which the first that names a list you see puts the task in that list instead (`#shop`: `quickList` in `js/taskmodal.js`; the completion offers the lists with the tags); with users, the assignee: `@name` of a user who can be assigned, or `@me` (anything else stays in the title); importance: `!` (or `!high`) and `!low`, as words of their own ("Call Bob !", not "Call Bob!").
  Only trailing words count, and shorthand that would empty the title is ignored.
  `#task-edit-quick` previews the result; shorthand wins over the date fields.
- **Completing**: the checkbox completes after a cancellable grace period (struck through meanwhile; several ticked in a row each keep their tick and strike while an earlier one leaves the list).
  Completing a recurring task spawns the next instance, stepping from the previous primary date (or today with `from: completion`) until ≥ today, with its start date not before today (see Dates); month steps clamp to the month's last day.
  Un-completing removes the spawned instance while it is untouched (`untouchedSpawn`: its history ends with the `repeat`, and title, description, tags and comments are the reopened task's);
  one you changed since stays, and completing the reopened task again then spawns no second one (`spawnRecurring` skips a task whose open spawn is still there).
- **Checkboxes in the description**: a checklist item (`- [ ] item`, as on a page) is a real checkbox in the card's description.
  Ticking it works as on a page: `POST /api/tasks/<id>/toggle-check` with `{ idx }` flips the Nth box (`mdtext.ToggleNthCheckbox`, numbered as `renderNotes` renders them; a description has no frontmatter, so a leading `---` block is text and its boxes count) and records when,
  `<!--d:YYYY-MM-DDTHH:MM:SS-->`,
  shown as a date chip;
  unticking takes the date out.
  An undo toast follows ("Checked “…” · Undo", undo is a second toggle); the toggles are chained (`toggleNoteCheck`), so an undo never overtakes the toggle it reverts.
  Each toggle is a change of the description like any other (a commit).
  The task's own checkbox is `.task-checkbox input`, and a page's own checklist next to a task list is the page's (`js/page.js` leaves the task app's boxes alone), so they never mix.
- **Subtasks**: the checklist items in a task's description are its subtasks (`subtaskCounts` in `js/taskutil.js`, counted as the description renders them: not in code).
  A card with subtasks says how many are done, "☑ 1/3" (`.task-subtasks`, in the accent once all are, `.task-subtasks-done`), where a card with another description has 📝: the line hides the description, and a click on it opens them as 📝 does.
  Completing a task with a subtask still open asks first ("“Plan trip” has 2 open subtasks.", **Complete anyway** or Cancel; `confirmOpenSubtasks`, `#task-subtasks-modal`): from the task's checkbox and Space (`tickTask`), and once for several selected tasks (Space, the selection bar's Done; `mayComplete`), where Cancel keeps the selection.
  Won't do, a task chip's checkbox on a page and an assistant (MCP) don't ask.
- **Won't do** (⋯ menu, the edit modal, the Delete key; cards have no ✗ button any more) archives without spawning;
  **Skip** (⏭, recurring only) archives as won't-do and spawns the next instance, the first occurrence after today (`spawn_recurring(skip=True)`): a daily task three days overdue comes back tomorrow, not as a card for today, so a skipped backlog leaves Focus.
  Completing catches up to today instead.
  Both show an undo toast; reopening a skipped entry removes its spawn (while untouched, as for un-completing).
- **A task can't be deleted**: nothing in the app deletes one, and an assistant (MCP) can't either.
  A task that won't be done, or was made by mistake, is closed undone with **Won't do**: it goes to Closed with "✗ won't do", and can be reopened from there.
  Won't do is in the ⋯ menu, in the edit modal (the button at the right of Save and Cancel, an open task's only; what was typed in the modal is dropped), on the **Delete** key and, for several selected tasks, in the selection bar and their menu (`wontDoMany`: one `batch-complete` with `wont_do`, one undo that reopens them; closed tasks in the selection stay as they are).
  The routes that delete (`DELETE /api/tasks/<id>`, `batch-delete`) remain for what takes back a task just made: the undo of an import, and of tasks the page editor created.
- **Planned cue**: a planned task's card gets a coloured left edge, the same on every tab for consistency: amber when it is in Focus already (start today or earlier), blue when planned later (`task-planned-now`/`-later`).
  On Open it tells decided tasks from undecided ones without changing the order.
  A due/overdue urgency colour wins.
  The 📅 date is coloured too: blue for a later start, amber for today, plain for a past start ("since …", never a warning).
- **Inline dates**: the ⏰ (due) and 📅 (start) chips open a date picker. **f** starts the selected task today,
  **F** tomorrow (again clears). **1**–**7** start the selected tasks on that day of the Plan board (1 today, 2 tomorrow, … 7 six days ahead),
  **0** unplans them,
  on every tab and for several selected tasks at once (`startOn`: one batch with one undo; a task whose deadline the day passes asks "Move the deadline too?" on its own, after the others).
  So planning works from the keyboard: walk Unplanned with j/k (Shift to take several) and press a day's number, however far the board puts it.
- **⋯ menu** (the `.task-menu-btn`, always shown, three dots drawn as an SVG (`MORE_ICON`) so they sit on the line's centre in any font;
  also **right-click** on a card, `openTaskMenu(id, anchor, {x, y})` at the pointer, selecting the card, a link in the card keeping the browser's menu;
  from the keyboard the menu key or **Shift+F10** on the selected card): all card actions (Edit…, Start today/tomorrow, Pick start date…, Unplan, Skip, Importance…, Won't do, Copy link, Select several;
  for closed tasks Edit…, Reopen, Copy link, Select several).
  **Copy link** puts the task's full URL in the clipboard (`taskHref`, as `taskLink` builds it: `/tasks?task=<id>&view=open`, `view=closed` (with `&year=` from an earlier year's archive) for a done task, `&list=` for a task in a list); opening it selects and flashes the task (see `?task=`).
  Paste it anywhere: chat, mail, a page.
  A popover with ↑/↓/Enter/Esc on desktop (tasks shortcuts suspended while open), a bottom sheet at ≤640px.
- **Edit modal**: double-click anywhere on a card (title and description too; only its controls, the checkbox, buttons, links and date chips, keep their own) or Enter on desktop;
  on touch the first tap selects the card (also showing a cut-off title whole, see One line per task) and a tap on the selected card opens it (`pressedSelected`, noted at `pointerdown`, since touch's compat mousedown selects before the click;
  pointer type of the last `pointerdown`;
  a double tap's `dblclick` is ignored, so it is just those two taps),
  or Edit… in the menu.
  Centred with `inset: 0; margin: auto` and **no transform** (a transform would clip `position: fixed` popups like the date picker), scrollable, sticky action bar.
  At ≤640px full screen.
  **The fields**: Title, Description, Tags, then one line of small boxes that wraps (`.task-props`), each with its name inside, in grey before its value (`.task-prop`, a `<label>`, so a click anywhere in it reaches the field): List (while there are lists), Due, Start and Importance, which every task shows.
  **List** (`#task-edit-list`, `fillLists` in `js/taskmodal.js`) is one choice: the lists you see, A–Z as the server names them (a private one with 🔒, the shared one of a name you have a private list of as "name (shared)"), first **Inbox** (with 🔒 with users, where it is off for a shared task), and last **New list…**. New list… asks for a name in a small dialog over this one (`.list-new-modal`, `openNewList`; with users **Private: only you see it and its tasks**, off for a shared task and ticked when the task is in a private list now) and makes the list at once (`POST /api/tasks/lists`, pinned as one made in the dialog that moves tasks: Moving tasks to a list); the field then names it, and the task goes into it with Save. A name that won't do is said in that dialog; Cancel or Esc leaves the field as it was. The field shows once the wiki has a list you see: a first list is made with New list… on the Tasks page or Move to list…. A new task starts in the list of the view it was made in, else in the Inbox; choosing another moves the task. A task is as private as its list (Lists → Private lists): for a shared task your private lists are off, and under the fields a line says who sees the task: "Private: only you see this task." in a private list or your Inbox, and for a private task about to leave for a shared list "Everyone who uses the wiki will see and change this task. It can’t be made private again."; Save then sends `share`. Choosing a shared list for a task of yours also puts your name in Assignee, once (`sharedByEdit`): it stays in your My tasks unless you take the name off.
  **The rest shows only when set** as the dialog opens: with users Assignee (a field of its own after Tags; Assigning tasks in `docs/spec/users.md`), Keep after done, and Repeat with "Every 2 weeks" and From done (the two only once a repeat is chosen). Repeat comes last, on a line of its own (`.task-prop-group`), so the two fields a chosen repeat adds come beside it and Repeat stays where it was clicked.
  Under the boxes a quiet line offers the ones not shown (`.task-add-fields`: "+ Assignee" with users, "+ Repeat", "+ Days before due", "+ Keep after done"): a click shows the field and goes to it (`showField`), and ticks Keep, since asking for the box is asking for that.
  A field stays for as long as the dialog is open, also emptied again; a line with nothing left to offer is gone.
  Not offered: Repeat for a keep task, Days before due without a due date (and it is never shown on opening: it is another way to set Start, which shows the result), Assignee for a task in a private list (it has none).
  Repeat without a date shows, but off, and the line under the boxes says why ("To repeat, a task needs a due or start date.", `#task-edit-rec-note`).
  **Tab goes from field to field**: Title, Description, the comment field while it is shown (and its Add once something is written), Tags, Assignee when it is shown, List, Due, Start, Importance, the fields shown of Days before due, Keep and the repeat fields, the buttons of the line that offers the others, then Save, Cancel, Won't do.
  What isn't a field of the task is no stop (`tabindex="-1"`) and has its own key or the mouse: Edit | Preview (F2), the formatting bar (Ctrl+B/I/K), Write a comment… (Alt+C) and the comment field's Cancel (Esc), a comment's ✎ and × and the links and boxes in comments, History.
  Saving a task into another list removes it from a view of its old list, with a notice that says where it went.
- **Date picker** (`openDatePicker`): a row of shortcuts above the month,
  counted from today,
  not from the current value: **Today**,
  **Tmrw**,
  **+2d**,
  **Mon** (next Monday),
  **+1w** (`QUICK`, `.dp-quick-btn`; short labels so all five fit one row, the accessible name and tooltip say it in full, e.g. "Next Monday: Mon, Oct 5", in the `locale` setting (see Dates); below the month only Clear; the current value marked, ones before the picker's `min` disabled).
  They commit like a picked day, so the card's 📅 asks about the deadline as usual.
  The month's title, weekday heads and first weekday follow the `locale` setting (see Dates; Monday without one).
  Positioned after every `draw()` (below the anchor, else above, else inside the window; never past a screen edge).
  A press outside only closes it: `pointerdown` in the capture phase, then `swallowNextClick` eats that press's mouse/click events, so a tap can't also hit what's underneath.
- **`?task=<id>`** selects, scrolls to and flashes (`.task-flash`) that task; a missing task gets a notice.
  A newly created task is flashed the same way when the view shows it.
- **Two people on one task** (`changedFields`, `openedData` in `js/taskmodal.js`; `textUnchanged` in `go/tasks.go`): the dialog remembers the task as it opened and **Save sends only the fields changed in it** (the repeat's three together, `share` with the list), so a deadline, a tag or an assignee someone else set meanwhile is not written back as it was. Nothing changed, nothing is sent.
  Fields merge that way; a text can't. A changed title or description goes with what it started from (`was: {title, description}`), and when the task's text is another one by now the write is refused whole (409, "Someone changed this task's description while you were editing it. …"): the dialog comes back with what was typed still in it, to copy.
  `PUT /api/tasks/<id>` takes `was` from any client; without it a write is as it always was.
- **Rejected writes** show the server's error as a toast (`showNotice`).
- **Toasts and modals** (`placeToast`): a toast shown while a modal dialog is open goes into the dialog, since in `<body>` it would sit under the dialog's top layer and its Undo couldn't be clicked (deleting a comment showed that); when the dialog closes the toast moves back to `<body>`.
- **One undo at a time** (`showUndo`): only the latest action's undo is live,
  from its toast's Undo or Ctrl+Z. An action that saves first and arms its undo when the server answers (bulk start with **0**–**7**, Done/Reopen on a selection, bulk tags, Won't do on a selection, Move deadline, completing a keep task, deleting a comment) drops the previous undo as it starts saving (`dismissUndo`),
  so a quick Ctrl+Z in between does nothing instead of undoing the action before.
- **Cards** (`taskHTML`, tasks.css "Cards"): one layout for every card.
  The first line: checkbox, title (as wide as it needs, at least 6rem, 4.5rem on phones; cut off with …, whole and wrapped on the expanded card), the tags right after it (pills), markers and dates at the right end (`.task-meta` sits in the line), ⋯ last.
  **Nothing jumps on hover**: what is always shown (markers, 📝, 💬, dates, ⋯) ends at the right and stays put; what hover adds (⏭, the empty ⏰ 📅, at 60% opacity, full on their own hover: at 30% they were hard to see) appears left of it (first in `.task-meta-right`, CSS `order`).
  A task with a description shows 📝 (`.task-has-notes`) on every card, since its line hides the description.
  Under the line of a card whose details are open (see One line per task): the description, then, under a small "Comments" heading (`.task-card-comments-label`) that says where the description ends, the latest 3 comments, newest first (`.task-card-comments`; "N earlier…" opens the modal at the comments).
  The description is plain text under the title, the task's own; only the comments have bars (see Comments), so the bar says which is which. The open card ends with room before the next task (`.task-details`: more padding and margin below).
  **Every card shows every tag**.
  The title and the tags share the line (desktop: `.task-meta` is `display: contents`, its tags and dates are grid columns of their own, `tags` and `dates`, with a `1fr` spacer between): each is as wide as it needs when both fit;
  short of room, both grow from their minimum (6rem for the title, the widest tag for the tags) by the same amount, so a long title can't push the tags out and many tags can't squeeze the title away.
  The tags then wrap onto more lines under the first, which makes a card two (or more) lines tall; the title is cut off with ….
  Dates and markers are never clipped: their column is as wide as they are (`white-space: nowrap`, so "📅 Tomorrow" counts as one piece) and stays at the first line's right end.
  The first line has one fixed height (`--line`), expanded or not, so expanding a card moves nothing on its line.
  Empty date placeholders (⏰ 📅 to set one) show on hover, left of the dates; with a mouse their room, and ⏭'s, is kept while hidden (`visibility`), so hovering a card that is short of room rewraps nothing; touch, which has no hover, sets dates in the modal or from ⋯.
  A double-click anywhere on a card but its controls opens the modal (titles are edited there, not in place).
  **Phones** (≤640px): the line is too short for title and dates,
  so the title has the first line to itself (beside the checkbox and ⋯, which keeps its finger-sized target without making the line taller),
  tags,
  markers and dates go to a second line with the card's whole width,
  and a card with none of them stays one line;
  rows are ≥44px as a whole.
  There every tag shows: the tags flow in that line (`display: contents`) and wrap onto more lines rather than drop out, the dates following the last tag at the right end, or on the next line when they don't fit beside it.
- **One line per task**: every card is its first line (title cut off with …, description and comments hidden; 📝 says a line has a description, 💬 N that it has comments).
  It is the only way a list shows: there are no full cards, and no switch (`compact=1` in an embed and the **v** key, which once chose, do nothing).
  Tags that don't fit beside the title wrap onto a second line rather than drop out (see Cards).
  Rows stay ≥44px on touch.
  **Expanding a task** (`expandedId`, `setExpanded`, `.task-expanded`): selecting a task (a click, arrows, j/k; `setSelected`) shows its whole title,
  wrapped,
  when the line cuts it off (`titleCut`),
  and nothing more: description and comments stay hidden,
  so you can read through tasks and the list stays short;
  the previous one closes.
  A task whose title fits stays a line (on touch the first tap only selects it).
  **Its details** (the description, the latest 3 comments;
  `detailsOpen`, `.task-details`, `toggleDetails`) open only on request: a click or tap on the **expand button** (`.task-expand-btn`, a chevron left of ⋯, turned up while open, `aria-expanded`;
  only on lines with a description or comments, though every line keeps its room so the dates end at the same place),
  on 📝 or on 💬 (each toggles them; 📝 is a button, finger-sized on touch without making the line taller),
  or **→** / **l** on the selected task;
  **←** / **h** closes them and keeps the whole title.
  **Opened, they stay open on the way** (`detailsMode`, `expandSelected`): the next selected task (j/k, a click, a tap) shows its details too, the previous one's closing, until they are closed (the button, ←/h, Esc); a task without any shows its whole title as usual.
  An open task's line doesn't move.
  **Enter** opens the modal; on touch the first tap selects and expands, a tap on the selected task opens the modal.
  **Esc** collapses it, details and all, and ends `detailsMode` (the next Esc clears the selection), until another task is selected; several selected or none close it too.
  It survives re-renders (`taskHTML` adds the classes); switching tabs resets it (`collapseAll`).
  Double-click still opens the modal directly.
  **Nothing moves under a click**: a mouse press on another card leaves the expanded one open (above or below it: collapsing one above would pull the pressed card up under the pointer;
  one below closes the same way, for one behaviour) for the double-click window (`COLLAPSE_DELAY_MS`, 400ms;
  `collapseLater`) and then collapses it with a short height animation (none with reduced motion),
  so a double-click's second click still lands on the card the first one selected.
  Each press of it selects that card again, which doesn't restart the wait.
  A pressed card is also not scrolled into view when it expands (`pressedCard`; it is in view already, and scrolling would move it from under the pointer).
- **Header**: tabs, filter and **+ New** on one line on desktop (`.tasks-toolbar` `flex: 1 1 20rem`), two lines on phones with a label-less "+" (accessible name "New task").

### Title links

A title can carry links: `[[wikilinks]]`, `[label](url)` and bare URLs show as links on the card (`linkifyTitle`; one to another site opens in a new tab, `class="task-link"`; a link to anything but the web, mail or the wiki, e.g. `javascript:`, stays text).
**Pasting a URL into the title** asks for its name, as in the description and the editor (`pasteUrlAsLink`, the same dialog): a name makes `[name](url)`, none (or Cancel) the bare URL; pasted over selected text, the text becomes the label.
`[[` and `[text](` complete pages there too, and `:name` emoji (see Emoji in `docs/spec/editor.md`). Where a title is plain text (the ⋯ menu, toasts, the subtasks and deadline dialogs: `plainTitle`; search results, task chips, Linked tasks: `mdtext.PlainTaskTitle`), links are reduced to their labels.

### Description

A task's description is markdown, edited and rendered as a page's text.

- **Rendering** (`renderNotes`, `POST /api/render-notes`): the page renderer, so headings, lists, checklists, tables, code (highlighted), quotes, callouts, images, wikilinks, `[[task:…]]` chips and the dates a ticked checkbox records look as on a page; lines in a row make one paragraph, as on a page.
  Not what only a page has: frontmatter, `[[tasks:]]` embeds, ✎ per section; headings get no ids (they would clash with the page's anchors).
  In the card the description takes a card's scale (`.task-desc.page-content`: headings the size of the text, tighter spacing, wide tables and code scroll, bullets and checklist boxes in one column a box wide (`--mark`), both centred in it and on the lowercase letters of the first line;
  the bullets are drawn there (`li::before`), since a list marker's place depends on the font;
  text starts at the same place with a box or a bullet, and the first box at the description's edge);
  an image opens full size.
  Comments render the same way, in the cards and the modal (`.task-comment-body`, at the description's scale),
  but keep their lines: a line's end is a line break (`Env.LineBreaks`), so a comment typed on three lines shows on three.
  In the modal a line separates each comment from the one before it; on a card each comment hangs on a bar of its own down its left (`border-left` of `.task-card-comment`), with a gap before the next, so the bar's end shows where a comment ends.
- **Where the HTML comes from** (`renderTexts`, `renderedHtml` in `tasks.js`): the tasks asks the server for the texts it hasn't rendered yet, in one request (`{ notes: [...], comments: [...] }`), and keeps the HTML per text for the page's life, shared by every task list app on it.
  Loading tasks (`fetchTasks`, Closed) and each write wait for it before drawing, so cards never flash plain text (the tasks a page brings along come with their HTML, see Task list embed); a text that arrives some other way shows as plain text until its HTML is there (`htmlOf`), and a failed request caches nothing.
  Code is highlighted once, when the HTML arrives.
- **Editing** (the edit modal's Description): the field is as tall as its text (`fitNotes` in `js/taskmodal.js`): two lines when empty, growing as the text does up to 45% of the window's height, where it scrolls.
  It has the page editor's markdown helpers (`markdownEditor` in `js/mdedit.js`): its formatting bar (`#task-edit-format`, without Make task, which is the editor's; shown only while the field has the focus, in the place of the "Description" label on the line above the field, which is as tall as the bar, so nothing moves; in a narrow dialog (≤640px) or under a finger on a line of its own under the label, where it scrolls sideways),
  Ctrl+B/I/K,
  Ctrl+Shift+X,
  `[[` / `[[task:` / `[text](` completion, snippets (`/date` + Tab), list continuation on Enter, Enter on a selection makes it a wikilink, file paste, drop and attach (uploads to `_assets/`, as in the editor), URL paste. Tab moves on to the next field unless it expands a snippet (the editor's Tab indents; a modal needs Tab between its fields). The modal's Save and Cancel stay: nothing is saved while typing.
- **Edit | Preview** (two buttons at the right of the line above the field, or **F2** in the modal; `setNotesPreview`; not shown for an empty description that isn't being written in, which has nothing to preview): Preview shows the description as the card will (`/api/render-notes`) in place of the field, no shorter than it and as tall as what it shows, up to the field's 45% of the window, where it scrolls.
  Tinted, outlined and labelled PREVIEW at its top (stays while it scrolls), so it is never taken for the field.
  Ticking a box in it ticks the item in the text and records when, as on the card (`toggleCheckboxText` in `js/mdtext.js`, mirroring `mdtext.ToggleNthCheckbox`; local time), so the preview shows the date chip; unticking takes the date out.
  The hidden field can't take the browser's undo; unticking undoes it.
  Edit goes back to the field, focused.
  The modal always opens in Edit.

### History

The server logs events on the task (`logEvent` in `go/tasklogic.go`): `created` (create and recurring spawn; a new task's first dates are not changes),
`start`/`due` whenever the date actually changes (`applyTaskFields`),
`done` (also for a keep task, whose cleared start date is part of it),
`reopened`,
`wontdo`,
`skipped`,
and with users `shared` (a private task made shared, by whoever did it: moved out of a private list, or its list made shared; older tasks may show `private` from when tasks could move the other way)
and `assigned` (the assignee changed: `from` and `to` name the users, `null` no one; `applyAssignee`).
A recurring task's next occurrence carries the series' history on (with the `done` or `skipped` just logged) plus a `repeat` entry ("Repeats: next Oct 3"), so the series reads as one log; it gets no `created` of its own.
Only these; title, tag or order edits are not logged.
Undo must not leave a trace, so every undo that PUTs old fields also sends the task's old `history` (`oldHistory` in `tasks.js`: start-date, won't-do, skip and keep undos), and the server applies a sent `history` last (`cleanHistory` validates it), replacing what that request logged.
Restore snapshots keep it too.
Reopen-undo and bulk-complete-undo go through `batch-complete` and do log (`done`/`reopened`).

With users (`docs/spec/users.md`) each event records who did it (`by`, the user every write function is given; written only when there is one, so a wiki without users keeps its files as they were; an undo's snapshot keeps it, `cleanHistory`);
a recurring task's `repeat` is by whoever completed the occurrence before it.

The edit modal shows it in a closed **History** section (newest first, with a count; with users the time reads "12 Mar 09:30 · alice", `commentStamp`), (`.task-edit-extras`).
Creation is the oldest entry; tasks from before the log get one from `created_at`.
There is no separate "Created:" line.

### Comments

What happened on a task, dated: a quick status line ("called Bob, waiting", "Alex: 6 people") or several lines.
A comment is markdown, rendered as the description is (see Description): paragraphs, lists, code, links, `[[task:…]]` chips.
Unlike a description, a comment keeps its lines: Enter starts a new line and the comment shows it (a `<br>`), without the blank line a new paragraph takes.
They are kept in `comments` (`[{ id, at, by, text }]`, oldest first;
`id` 8 hex chars, `at` `YYYY-MM-DDTHH:MM`, text up to `commentMax` = 2000 chars;
`by` the user who wrote it, written only when there is one (with users, `docs/spec/users.md`), so a wiki without users keeps its files as they were;
an edit keeps it;
shown after the time, "12 Mar 09:30 · alice", `commentStamp`).
Not part of the History: they are never capped, undo of other changes never touches them, and adding one logs no event.

- **Routes**: `POST /api/tasks/<id>/comments`, `PUT`/`DELETE …/comments/<cid>`, each returning the task, for open and closed tasks alike (`findForWriteLocked`).
  `PUT /api/tasks/<id>` with `comments` replaces the whole list, for undoing a delete; `cleanComments` validates it (and restore snapshots), dropping bad entries and renumbering duplicate ids.
- **Edit modal** (`renderComments` in `js/taskmodal.js`): Comments are the dialog's last section, after every field of the task and History, set apart by a line: they are saved at once and not with Save, whose bar stays at the dialog's bottom edge.
  Hidden for a new task; the list is newest first and as long as it is (the dialog scrolls, not the list).
  Under the heading is a line that looks like an empty field, **Write a comment…** (`.task-comment-open`, a button): a click puts the real field in its place, a text field of a few lines that grows with its text, focused.
  Enter starts a new line; **Ctrl+Enter** (or Add) saves the comment at once, not with Save, so Cancel never loses one, and puts the field away; Esc (or the field's Cancel) puts it away without saving, and doesn't close the modal.
  The field has the description's markdown helpers (`markdownEditor`): lists go on, `[[` completes pages, Ctrl+B/I/K, a pasted URL asks for its name, a pasted or dropped file is uploaded.
  ✎ edits a comment in place: the same field takes the comment's place, with its text (Save or Ctrl+Enter saves, Esc cancels without closing the modal); × deletes it with an undo toast.
  **Alt+C** shows the field too (or goes to it), from anywhere in the modal.
  A comment still in its field is not lost: the modal's Save saves it with the task, and ✎ or × on another comment saves it first.
  The text comes back with a file's line ends (`commentText`: `\r\n` becomes `\n`).
- **Cards**: a `💬 N` button (`.task-comments-btn`) in the meta row; its tooltip is the latest comment with its time.
  The latest 3 are listed (`commentCardHtml`): a comment of one line on the line of its time, as a status line reads; any other (several lines or paragraphs, a list, code) under its time.
  A click on it opens or closes the line's details, the latest 3 among them (see One line per task), whose "N earlier…" leads to the modal.
- **Carried on**: a recurring task's next occurrence takes the series' comments with it, like the history.
- **Search and MCP**: comment text is part of a task's search text (`taskSearchDocs`, so semantic search too), a comment counts as activity for `edited:`/`after:`/`before:` (`lastActivity`), and `mcpTaskText` lists them after the description under "Comments:" (`- YYYY-MM-DD HH:MM: text`, a comment's following lines indented under its first).

### Importance

A task is of **high**, **normal** or **low** importance (`importance`, Task fields); normal is what a task is unless something says otherwise, and says nothing anywhere.

- **On a card**, before the title, on its line (`importanceMark` in `js/taskutil.js`, `.task-importance`): **!** in a warm red for high, **↓** in grey for low, whose title also steps back (lighter, not bold). Normal shows nothing.
- **It marks, it doesn't sort**: no order changes with it. The manual order is where a task's rank is; importance says what must not be forgotten, or what may wait, wherever the task stands.
- **Set** in the edit modal (Importance: High, Normal, Low, after the dates),
  in a task's ⋯ menu (**Importance…**, which shows the three levels in the menu's place, the task's own ticked),
  with **i** on the selected task or tasks (`setImportance` in `js/taskselect.js`: one task by `PUT`, several by `batch-update`),
  and in a new task's title (`!`, `!low`: Quick-add).
- **Filter**: `!high` (or `!`) and `!low` in the filter field.
- **API**: `importance` in `POST`/`PUT /api/tasks` and `batch-update`: `"high"`, `"low"`, or `"normal"`, `""` or `null` for normal; anything else is 400 "importance must be high, normal or low". A file edited by hand is read leniently (`CleanImportance`: case and spaces aside, anything else is normal).
- **Carried on**: a recurring task's next occurrence keeps it; so does an export.
- **Assistants** (MCP): `create_task` and `update_task` take `importance`, and a task's line of facts says "importance: high" (or low).

### Keep tasks

A task with `keep: true` is never archived: ticking it off means "done for now, back to the pool".
It suits a shopping list used with the task app's views: Open is the catalogue of staples, starting items today puts them on Focus (the actual shopping list), and ticking one off sends it back to Open.
Completing a keep task (`keep_done`, in `PUT` and in `batch-complete`) clears its start date and its deadline, and sets `last_done` to today; it stays in `_tasks/tasks.json` with the same id, tags, description and place in Open's order, and Closed stays empty.
Since Closed can't reopen it, the card shows an undo toast that restores the old fields (`keepSnapshot`).
**Won't do** retires a staple.
Keep and Repeat both say what happens after done, so they exclude each other: `keep` clears `recurrence`, and the edit modal doesn't offer Repeat while "Keep after done" is checked (a Repeat that shows is off, and the line under the fields says "A keep task does not repeat.").
Cards mark keep tasks with ⟲ (`.task-keep`).
`[[tasks: keep=1]]` makes new tasks keep tasks.

### Lists

**A list is a place a task is in; a tag sorts tasks.** A task is in exactly one list and has any number of tags.
Five sentences say all of it:

1. A task is in one list.
2. A list is yours (private) or everyone's (shared); everyone has one list built in, the **Inbox**, where a new task goes unless it is made in a list or names one.
3. A shared task can be assigned to one person.
4. **My tasks** is what is yours: the tasks in your own lists, and the shared tasks assigned to you.
5. A pin is a shortcut: a pill in your list switcher.

Without users there is one person, and every list is theirs: the Inbox is the tasks without a list, and My tasks is every task.

A list has a name, an entry in the list switcher with its counts (below), settings of its own (the tab it opens on, a WIP limit, tab names, whether new tasks are keep tasks …), and it is shared or private: a private list and the tasks in it are yours alone.
You make one with **New list…** on the Tasks page (or in the dialog that moves tasks to a list, or in a task's List field), put a task in it with the **List** field of its dialog (or **Move to list…**), and show it on any page with `[[tasks: list=<name>]]`.
A page shows a list; it doesn't make one: editing the embed away, or deleting the page, changes nothing of the list, and a list is deleted only by **Delete…**, which asks where its tasks go.
Tags go across lists: `[[tasks: tag=sam]]` on Sam's page shows every task tagged so, whatever list it is in.

(Until 2026-11 a list was a tag a page declared with `[[tasks: list=<tag>]]`: a task could be in two, a list ended with its page, and a task moved by a change of its tags. A wiki from then is taken over once, when Zone starts: The migration, below.
Later in 2026-11 came the Inbox: until then a task could be in no list, which with users was everyone's; a pin also put a list's tasks in My tasks, and the first pinned list took new tasks. The Inbox's migration, below.)

- **The registry** (`go/internal/taskfiles/lists.go`; `ListsName`): the lists are in `lists.json` beside the task files, the shared ones in `_tasks/lists.json`, a user's private ones in `private/<user>/_tasks/lists.json`, read and written under the task files' lock, atomically, committed to git.

  ```json
  {
    "version": 2,
    "lists": [
      {"name": "shop", "view": "focus", "limit": "8", "keep": "1", "labels": "Catalogue,Shopping", "home": "off"},
      {"name": "task"}
    ],
    "pinned": ["task", "shop"],
    "mine": {"view": "focus"},
    "all": {"view": "open"},
    "order": ["0b7c…", "9e21…"]
  }
  ```

  Each list is one line: its name and its settings (`view`, `limit`, `plan`, `keep`, `labels`, `new`, `noshare`, `home`), their values text as an embed writes them, a setting that isn't set no key.
  So a list can be added or changed by hand in one line, and a commit shows the list that changed.
  `pinned` is whose registry it is: a user's own pins in theirs, the wiki's in the shared one (Pins, below); `all` (the shared one) the settings of All tasks, `mine` those of My tasks (a user's own; without users the shared one's), `order` the manual order of My tasks (task ids; the same owner).
  (Until 2026-11 `"mine": true` in a user's registry narrowed their Pinned to their own tasks; it reads as no settings and is written back no more.)
  It is read leniently (a value of another kind as text, a list without a name left out, a name twice once, unknown keys kept and written back A–Z) and written in one format; a file that is there but unreadable refuses every write (`ReadListsStrict`: never an empty registry, which would drop every list).
  `version` says that the wiki's lists are kept here: a shared registry without it (or none) is a wiki to take over (The migration), one with `1` a wiki from before the Inbox (The Inbox's migration).
  A registry keeps the version it has when it is written; a new one gets the current one, 2.
- **A task's list** is its `list`, the list's name (Task fields): `"list": "shop"` after its tags, no key when it is in the Inbox.
  The task's file says which registry the name is in: a task in a user's files is in one of their private lists (or, without `list`, in their Inbox), a task in the shared files in a shared one (`taskList`).
  So a name has to be unique only within one registry, and nobody learns another user's names from it.
- **Names** (`cleanList`, `checkListName`): one word, lowercase, spaces `-`, a leading `#` dropped, at most 60 characters, none of `[ ] , | = / "` (they mean something in an embed or a ref).
  No list can be called `inbox` or `none`: they name the Inbox (below).
  A name is unique among the shared lists and among one user's private lists.
  A user can't make a list by a name of one they see ("There is a list #shop already"), private or shared; they can still end up with a private and a shared list of one name (the migration, or someone making the shared one later, who doesn't see theirs).
  For them the plain name is their own list and the shared one is **`shared/<name>`**: that is a **ref**, how a list is named to one user (`ListInfo.Ref`, `taskLists(user)`, `resolveList`): in URLs (`/tasks?list=shared/gifts`), in pins, in the API's and the tools' `list`, in the browser.
  On a page, `[[tasks: list=gifts]]` is the shared list unless the page is in your private folder (`listOnPage`), so a shared page shows everyone the same list.
- **Private lists** (with users; `docs/spec/users.md`, Private tasks): **private and shared are said of lists, not of tasks**. A task in a private list, or in your Inbox, is yours alone and is kept in your files; a task in a shared list is shared.
  **With users a shared task is always in a list**: there is no place that is everyone's and nobody's.
  A new task may start in any list.
  **A shared task never goes into a private list nor into an Inbox** ("A shared task stays shared": others have seen it), by any route or tool.
  **A private task leaves for a shared list** only when the write says `share`, which the browser sends after telling you that everyone will see it; its History then says `shared`.
  Out of a private list into your Inbox, or the other way, it stays private, and nothing is asked.
  A private list can be made shared with **Make shared…** (below), its tasks with it; a shared one never private.
  Every reader asks `canSeeList` (through `taskLists(user)`, which gives only the lists a user sees), every write to a list or to a task in it `canWriteList` (`go/access.go`): today whoever sees a list and may write tasks may change it; members or roles per list would change those two and nothing else.
- **The Inbox** (`inboxRef`, `isInbox`, `inboxOwner` in `go/tasklists.go`; `inboxView` in `tasks.js`): the list every user has without making it: **the tasks without a `list`**.
  **A new task goes there unless it is made in a list's view or names a list**: from `c t`, New task, My tasks' **+ New**, an assistant's `create_task`, `POST /api/tasks` without `list`.
  With users it is yours alone (the task is kept in your files, `private/<user>/_tasks/`): what you capture is seen by nobody until you file it.
  Without users it is the wiki's: the tasks in no list, in the shared files, as they always were.
  - **Filing** is moving a task to a list: the List field, **Move to list…**, `m`. Into a shared list that shares it (`share`), and the browser then **assigns it to you** (the dialog shows the name, to be taken off; the move dialog sends it), so what was yours stays in your My tasks unless you say otherwise.
  - **Its view** is `/tasks?list=inbox`, headed **Inbox** (with 🔒 with users), the second pill of the list switcher; a line under the heading says what it is for.
    It is All tasks narrowed: All tasks' settings (the tab, Plan, the limit), no settings dialog of its own, a locked chip **Inbox** in the filter field (the filter word `list:inbox`), and a new task made there is in the Inbox.
    A task filed from there leaves the view, with the notice a list's view has.
    On a page, `[[tasks: list=inbox]]` shows whoever looks their own.
  - **It can't be renamed, deleted, shared or pinned**, and has no entry in a registry.
  - **`none`** names it too (`list=none`, `list:none`, `search_tasks(list="none")`): until 2026-11 that was the view of the tasks in no list.
    In a wiki that has a list called `inbox` or `none` from before the names were kept, the name is that list's (the Inbox's migration renames a list `inbox`).
  - **Not for a viewer** among users (they make no task), nor in sharing mode, where what is private isn't shown.
  - **A shared task that loses its list** (with users) goes to the shared list **`general`**, made for it when there is none (`generalListName`, `ensureGeneralLocked`): a list deleted without naming another, a restored or imported task whose list is gone, and the tasks that were in no list before the Inbox. It is a list like any other.
- **My tasks** (`/tasks?mine=1`; `mineFilter`, `inMine` in `go/tasklists.go`, `TasksCore.inMine` in the browser): your home view, the one the Tasks links open and their badge counts.
  It shows **what is yours: the tasks in your own lists (your Inbox, your private lists), and the shared tasks assigned to you**, wherever they are.
  - **An unassigned shared task is its list's**, not yours: it is in the list's view and in its pill's counts, and the list's heading says how many there are ("3 unassigned", a link to them; A list's backlog, below). Taking one (assigning it to yourself: `a`, Enter) puts it in My tasks.
    So My tasks is as short for one of a hundred as for one alone, and nothing is in everyone's.
  - **Assigned to you** is the task's `assignee`. An assignee who is no user anymore (or disabled, or a viewer now) is no one.
  - **Leave out of My tasks** (`home=off`, a list's setting: List settings → "+ Leave out of My tasks"; `ListInfo.LeftOut`): a list of your own whose tasks don't belong there, a catalogue of staples, a someday list. Its tasks are in its own view only, and not in the Tasks links' count.
    It counts for a list that is yours (a private one; without users any); a shared list's tasks are in My tasks by their assignee, so the dialog doesn't offer it there.
  - **Without users**, and for one no task can be given to (a viewer; `mineAssigns`), every list is the user's own: My tasks is every task but those of a list left out.
  - **With no list in the wiki** it is every task, and there is no such view: `/tasks?mine=1` goes to `/tasks`, where the Tasks links go then.
  - **One task app**: the cards' list chips say where each task is (a task in the Inbox has none), and a new task there goes into the Inbox.
    A task that leaves by a change of its assignee or its list gets the notice a list's view gives ("Saved; it no longer shows here.").
  - **Under the heading** a line says what it holds: "Your own tasks and what is assigned to you.", without users "Every open task.", and the lists left out, as links (`leftOutLinks`).
  - **Its settings are its own**, and yours (Settings, above): the tab, Plan, the limit, the tabs' names.
  - **Its manual order is its own**, and yours (`mineOrder`, `setMineOrder` in `go/tasklists.go`; `own`, `setOwnOrder` in `tasks.js`): a list's order (a task's `sort_order`) is the list's, for everyone who looks at it, a team's ranking of its backlog; what you rank in My tasks, across lists, is your sequence and moves nothing in any list.
    It is kept as task ids in your registry (`"order"`; without users the wiki's), written by `POST /api/tasks/reorder` with `mine: true` in a commit "Reorder My tasks": no task is written.
    The view lays it over its own copies of the tasks (a task's place in it stands for its `sort_order` there, a task not in it is Unsorted), so Unsorted and Sorted, Focus and the Plan board's sections work in My tasks as everywhere; it comes with the page's tasks (`"order"` in `tasksInitial`) and with `GET /api/tasks?with=order`.
    A task new to My tasks (one just given to you, a capture) is Unsorted, at the top, until you place it.
    Until you rank something there it is the lists' order of your tasks, so nothing moves on the day; ids of tasks that are closed or gone drop out at the next move.
    (Until 2026-11 a move in My tasks numbered its tasks' `sort_order` from 0, which put them above everyone else's in their lists.)
  - **The server and the browser have the same rule**: the page gets the lists left out and whether assignment counts (`data-init-out`, `data-init-mine`: `assign` or `lists`), the routes take the scope as `?mine=1` (the closed tasks' count and years, the export), and one table of cases runs through both (`go/testdata/mine_cases.json`).
  - **On a page**: `[[tasks: mine=1]]` shows it to whoever looks.
  - **Zone assigns nothing by itself**; the browser proposes you when you file a task of your own into a shared list.
  - (Until 2026-11 My tasks was every task except one assigned to someone else, or in a list you hadn't pinned unless it was assigned to you: every unassigned task of a pinned list, and of no list, was in everyone's.
    Before that the Tasks links opened the pinned lists as one, called Pinned and before that My lists, at `/tasks?pinned=1`, which answers My tasks now.)
- **A list's backlog** (a shared list, with users): under the list's heading, **N unassigned** (`.tasks-unassigned`; `tasksPageView.Unassigned`) links to the list filtered by `@none` (`/tasks?list=<ref>&assignee=none`): the open tasks no one has, a former assignee's included.
  A list left out of My tasks says so there too.
- **The list switcher** (`.tasks-places` in `templates/tasks_page.html`, `taskPlaces` and `taskMore` in `go/tasksembed.go`, `js/listsrow.js`): `/tasks` opens with a row of pills above its heading,
  on the page's white sheet with it, looking as the module switcher at the top of the page does (`.tasks-places-group`: one white capsule with a border, the pills inside it without one, in its type and height, the current one shaded).
  **My tasks is the first pill, the Inbox the second; a pinned list is a pill; a list that isn't pinned is under More.**
  In order: **My tasks** (above), whenever there is a row;
  **Inbox** (`data-place="inbox"`; for who has one, `hasInbox`: with a lock with users);
  then **the pinned lists** in their order,
  then **More ▾**.
  The pinned lists carry a pin (`.tasks-place-pin`, tooltip "Pinned list #<name>"), a private list a lock (tooltip "… (only you see it)").
  - **More** (`.lists-more`, a `<details>`; its pill is the summary, `.lists-more-btn`): a click opens a panel under it (`.lists-more-panel`) of links, a line each:
    **each list you see and haven't pinned**, A–Z by name (your own before the shared one of that name), a private one with a lock,
    then **All tasks** (`/tasks`: every task you see, in whatever list).
    No counts on the lists in it, and **no number on the pill**: what is yours to do is in My tasks.
    Escape and a click elsewhere close it.
    **It names the view the page shows when that is one of its own**: on an unpinned list the pill reads that list's name, on `/tasks` (and a tag's view, which is All tasks with a filter) "All tasks", and it is the shaded one; else it reads "More".
    It is there for a viewer and in sharing mode too (the page's ⋯ menu isn't), so it is no menu of the page's but links.
    On a narrow screen the panel is as wide as the row.
  - **Finding a list** (`.lists-more-filter`, with `moreFilterFrom` = 8 lists or more under More): a field at the panel's top, focused when it opens; what is typed leaves the lists whose name has it (All tasks stays), and Enter opens the first.
  - **A list new to you** (`.lists-more-new`; `zone_lists_seen` in the browser's `localStorage`): a list under More that this browser hasn't shown before, so one someone else made, or an assistant (a list you make in the browser is pinned), has "new" beside its name, and More a dot (`.has-new`) until it is opened.
    The first visit knows every list; a list is known once it was a pill, was under More when More was opened, or was the page's view.
    It is the browser's memory, not the wiki's: another browser marks it again.
  - **Dragging the pills** (`data-reorder` on the row, for who may pin; `POST /tasks/pin-order`, `pinOrderOp`): a pinned list's pill dragged with the mouse to another place among the pinned lists puts the pins in that order, in one commit ("Reorder the pinned lists").
    Let go outside the row, it goes back.
    My tasks, the Inbox and More stay where they are.
    With a finger there is no dragging (it scrolls and taps): **Move first** in the list's menu does it.
  The heading names what is shown: **List: <name>** (with 🔒 for a private one), **My tasks**, **Inbox** or **All tasks** (the window's title likewise).
  Every list's pill or link goes to `/tasks?list=<ref>`: the list with its settings,
  and as its **Backlinks** every page that embeds it (`listPages`: any `[[tasks: list=<name>]]` outside code on a page you may see that shows this list, A–Z by title; at the end, in the right column on big screens and as the breadcrumb's `← N` chip, as on a page view).
  Each pill but More carries counts (due today or overdue, in Focus): My tasks those the Tasks module link has, a list its own (`countTasks` over the list: everyone's tasks in it);
  the current one is shaded (`aria-current`): My tasks on `?mine=1`, the Inbox on `?list=inbox`, the list on `?list=<ref>`, More for a view under it, none for a name no list has.
  **Only `/tasks` has it**: a page that embeds a list shows the list, as any embed does, and no switcher, so the Tasks module and the wiki's pages stay apart; the pages are a list's Backlinks.
  With no list there is no switcher.
  Another user's private list is not in it; in sharing mode a list that hides when sharing (`noshare`) is left out of the pills and of More (on its own URL More then reads "More"), and the counts are hidden as on the module links.
  `js/chrome.js` keeps the counts current with the Tasks link's (`refreshDueBadge`: `GET /api/tasks/due-count?places=1` adds `places`, each pill's counts by its key: `list:<ref>`, `mine`, `inbox`).
  The pills wrap onto more lines on a narrow screen and are finger-sized under `(pointer: coarse)`.
  (Until 2026-11 every list was a pill, pinned or not, with an **All** pill last.)
- **Pins** (`pinnedLists`, `pinListOp`; `POST /tasks/pin-list` with `list` and `pin`: `1`, `first` or `0`): **a pin is a shortcut**: the list is a pill of your list switcher, and nothing else.
  It doesn't say what is in My tasks (that is whose a task is), nor where a new task goes (the Inbox, or the list in view).
  You pin, unpin and reorder in a list's ⋯ menu on `/tasks?list=`: **Pin** puts it last, **Move first** (on a pinned list that isn't) puts its pill at the front, **Unpin** takes it out; the pills can be dragged too (the list switcher, above).
  **A list you make starts pinned**, last, wherever it is made: New list…, the List field, the move dialog, an assistant's `create_task_list`.
  The pin in List settings pins and unpins.
  **Pins are the user's own**: in their registry (`pinned`, in order); a user who has set none has the wiki's (the shared registry's, which are everyone's without users), and starts from those with their first pin.
  One who unpinned everything has none (`"pinned": []`), not the wiki's again.
  With users the wiki's pins are set in `_tasks/lists.json` by hand.
  (Until 2026-11 a pin was `pin=1` or `pin=default` in an embed, and a page's pin pinned for everyone.
  Then, until the Inbox, a pin also put a list's tasks in My tasks, and the first pinned list took the new tasks; with nothing pinned they went to a private list `private`, which counted as pinned.
  A registry that names a `default` list reads as that list pinned first (`ParseRegistry`), and is written without the key.)
- **Where a new task goes** (`.task-edit-target`, `syncTarget` in `js/taskmodal.js`): the edit dialog of a new task says it under the title, and with users who sees it: "Adds to your Inbox: only you see it.", "Adds to the list team: everyone sees it.", "Adds to the list diary: only you see it."; without users "Adds to the Inbox." or "Adds to the list shop.".
  It follows the List field, and a `#word` in the title that names a list (Quick-add: `#shop` puts the task in the list shop, the first such word; the others are tags).
  Hidden when editing a task and for several titles at once.
  **A task for someone needs a shared list** (with users; `autoInbox`, `sharedDefault`, `syncAutoList`): the Inbox is yours alone, so on a new task still in the Inbox that nobody chose a list for, naming an assignee (the Assignee field, the title's `@name`, a view's `assignee=`) moves the List field to a shared list: the one this browser last made a task in (`localStorage` `zone-tasks-shared-list`), else the only one there is; taking the name away moves it back.
  With several shared lists and none remembered, or none at all, the line reads "A task for someone else needs a shared list: choose one in List." and Save says so on the List field (New list… in it makes one).
  Choosing a list yourself ends the following.
  An assistant's `create_task` with an assignee and no shared list is refused, naming the shared lists; `POST /api/tasks` likewise answers 400 ("A private task is yours alone. Put it in a shared list to assign it").
- **The Tasks page's ⋯ menu** (`.page-menu-btn` beside the heading of `/tasks`, on every view of it; the page's ⋯ menu, `#page-menu`, with `js/pagemenu.js`): **New list…**; on a list **Rename…**, **Pin** / **Move first** / **Unpin**, **Make shared…** (a private one) and **Delete…**; on a tag's view (`/tasks?tag=<tag>`) **Rename tag…** (below).
  Each but the pin is a dialog with a form the server answers (`data-dialog` on the menu item; a refusal comes back with the message in the dialog).
  Not for a viewer, nor in sharing mode.
- **New list…** (`#list-new-dialog`; `POST /tasks/new-list`, `createListOp` in `go/tasklists.go`): asks for the list's name and makes it: an entry in the registry, in one commit (`Create list #shop`).
  No page is written.
  **The new list is pinned for you**, last, in the same commit (a shared list with users: the shared registry and your own), so it is a pill at once and not out of sight under More; one who had set no pins starts from the wiki's, as with **Pin**.
  Then the list opens (`/tasks?list=<name>`).
  Nothing else changes by it: new tasks still go to the Inbox, and the tasks there stay.
  (`pin=0` in the form leaves the list unpinned; no dialog sends it.)
  With users the dialog has **Private: only you see it and its tasks**, which makes it a private list of yours.
  Refused, back in the dialog with the reason: no name, a name with `[ ] , | = /` or `"`, a list you already see ("There is a list #shop already").
  A tag of that name is a tag: making a list takes no task into it.
  Under an embed of a list that isn't there, **Create the list #<name>** does the same.
  An assistant makes a list with `create_task_list`, the same operation: the list is pinned for its user, last.
- **Rename…** (`#list-rename-dialog`; `POST /tasks/rename-list` with `list` and `new_name`; `renameListOp`): in one commit (`Rename list #shop to #food`) the list's name in its registry, the `list` of its tasks (open, closed, archived), the users' pins of it, and where pages and tasks' text name it: the `list=` of `[[tasks: …]]` embeds and the `?list=` of links to `/tasks`.
  Code stays as written, and so do a tag of that name, `tag=` and `#word` in prose.
  An assistant renames a list with `rename_task_list`, the same operation.
  A name of a list you see is refused: two lists become one by deleting one into the other.
  A shared list's name is everyone's, so the rename reaches every user's pins, private pages and private tasks' text too, as a page rename's links do (one who has a private list of the old name keeps their embeds of it); a private list's only its owner's.
  Refused with nothing changed: a tasks file, a registry or a page that can't be read.
  Text not yet in git is committed first (`keepPendingPages`).
- **Delete…** (`#list-delete-dialog`; `POST /tasks/delete-list` with `list`, `to`, `share` and `close`; `deleteListOp`): the list goes, in one commit (`Delete list #shop`); **its tasks stay**, open and closed ones, in the list `to` names.
  With none named, a private list's go to your Inbox (they stay private); a shared list's, with users, to the shared list `general`, made for them when there is none (a shared task is always in a list; `general` itself is deleted so only when it is empty); without users to the Inbox.
  The dialog offers those first: "Your Inbox", "#general", "The Inbox (no list)".
  **Close its N open tasks as won't do** (`#list-delete-close`, `close=1`; offered while the list has open tasks, not ticked): for a list that is over, its open tasks are closed undone in the same commit, as Won't do closes one (Closed shows them with "✗ won't do", and they can be reopened from there), so they aren't left open in another list or in none.
  No task is deleted: a task can't be (A task can't be deleted).
  A shared list's tasks can't go into a private one, and a private list's go to a shared list only with `share` (the dialog's box: "Share its tasks if they go to a shared list").
  Pins of it go; pages keep their embeds of it, which then say that there is no such list.
- **Make shared…** (a private list; `#list-share-dialog`; `POST /tasks/share-list`; `shareListOp`): the list moves to the shared registry and its tasks to the shared files, in one commit (`Share list #gifts`), each with `shared` in its History.
  Refused while a shared list has that name.
  There is no way back.
- **Renaming a tag** (**Rename tag…** in the ⋯ menu of `/tasks?tag=<tag>`, `#tag-rename-dialog`; `POST /tasks/rename`; the MCP tool `rename_task_tag`; `renameTagOp` in `go/pageops.go`): in one commit it changes the tag on every task (open, closed, archived) and, on pages and in tasks' text, the `tag=` and `new=` of `[[tasks: …]]` embeds and the `?tag=` of links to `/tasks`.
  A list is no tag: one of that name, its embeds and its links stay.
  Code stays as written, and so do a `#word` in prose and the `tags:` in pages' frontmatter, which sort pages rather than tasks.
  Case doesn't matter in matching: `Shop` and `shop` are one tag, and renaming a tag to its own name evens out how it is spelled.
  - **A name already in use** (a task you see has that tag) merges the two, so it asks first: the dialog comes back saying so with a **Merge** button (editing the name takes that back), and the MCP tool wants `merge: true`.
    A task with both tags ends up with one.
  - **Refused**, with nothing changed: a tag you don't see anywhere, an empty name, a name with `[ ] , | =` (they mean something in an embed), or a tasks file or page that can't be read.
    Text not yet in git is committed first (`keepPendingPages`).
  - **With users**: a tag on anything shared (a shared task, an embed on a shared page) is one name for everyone, so the rename reaches every user's private tasks and pages too, as a page rename's links do.
    A tag only you see stays yours: another user's private tag of that name is another tag and isn't touched.
    A tag only someone else has is refused as if it didn't exist, a new name only they use is no merge for you, and the answer counts only the tasks and pages you see.
- **Scopes** (`inScope`, `paramsScope`; `inScope` in `tasks.js`): a list view shows the tasks in that list;
  the Inbox (`list=inbox`) those in none;
  My tasks what its rule says (`mine=1`, `mineScope`; `scopeFilter` gives a scope's tasks for a user);
  several lists as one those in any of them (`lists=a|b`, which the routes still take: what Pinned was until 2026-11);
  the **main task list** (an embed with neither, including `/tasks`, URL `?tag=` notwithstanding) shows every task,
  as do a `tag=` embed (a task tagged `sam` belongs on a Sam page whatever list it is in),
  and the main Closed count.
  The due count is My tasks'.
- **On screen**: a list is marked as one wherever it shows: a list icon (an SVG mask in CSS `::before`, `--list-icon`, in the accent colour), a faint blue tint and a tooltip ("List", "List (private: only you see it)", `listTooltip`).
  A card shows its list as such a chip before its tags (`.task-tag-list`), but in a view of that one list, where every card would say the same.
  In a list's view the filter field holds a chip without × for it (`list:<name>`, shown as its name; `chipField`'s `locked`, `.tag-chip-locked`; Backspace skips it), so the page always shows what it says; an embed's `tag=` and `assignee=` words are locked the same way, and a URL `?tag=` stays removable.
  A save that takes a task out of view says where it went: "Saved to list “…”." when it left for a list (`listMovedNotice`), else "Saved; it no longer shows here." (`leftViewMessage`).
  Both are undo toasts (`offerUndoLeave`): undo PUTs back everything the save could change (`editSnapshot`: fields, list, order, recurrence and the old history, so no trace; not the list of a task that was private, which may have been shared by the save) and reloads the tasks, since the task left `tasks`.
  A new task created out of view only gets the notice.
- **An embed of a list that isn't there** (`noListHTML`, `tasks_no_list`; also `/tasks?list=<name>`): in the app's place a line, "There is no list #<name>.", and for who may make lists **Create the list #<name>** and a link to the tasks tagged so (`/tasks?tag=<name>`).
  Nothing else on the page changes.
- **The Inbox's migration** (`migrateInboxLocked` in `go/listsmigrate.go`, when Zone starts, after the migration below and under its locks; one commit, "Give every task a list: the Inbox").
  Once for each registry that says `version` 1 (lists as places, from before the Inbox), the shared one and each user's own, which then says 2 (each for itself: a wiki whose users have lists and that never had a shared one has no shared registry):
  1. A list called `inbox` is called `inbox-list` (its tasks and pins follow; embeds that name it show the Inbox from then on).
  2. With users, a user's list `private` that has no settings and took their new tasks (they pinned it, or pinned nothing) becomes their **Inbox**: its tasks lose their list, the list and its pin go. Zone made that list.
  3. **What wasn't in My tasks stays out of it**: the lists a user hadn't pinned are left out (`home=off`): without users any such list, with users each user's private ones.
     A shared list's unassigned tasks, which a pin put in My tasks, are in the list alone now: that is the change.

  At every start, with users: **a shared task without a list goes to the shared list `general`**, made for it and pinned for the wiki. That takes over the tasks that were in no list, and a wiki whose users are turned on later (its one Inbox becomes `general`).
  A task file or registry that can't be read stops it before anything is written, as below (`listsUnmigrated`).
- **The migration** (`migrateLists` in `go/listsmigrate.go`, when Zone starts and no registry, the shared one or a user's, has a `version`): a wiki from before lists were places is taken over, in one commit ("Make task lists their own"), under the page and task locks.
  It changes nobody's view of who sees what: no task changes its file.
  1. **The lists**: each name a shared page declared (`[[tasks: list=<name>]]` outside code) becomes a shared list, with its first page's (A–Z) params as its settings (`view`, `plan`, `limit`, `labels`, `new`, `keep`; `noshare` when that page was not for sharing).
     A name a user's private page declared becomes a private list of theirs, unless a shared page declares it too and they have no private task with that tag: their embed then shows the shared list.
     Another page's embed that set a list's settings differently loses that difference: the params stay in its text and mean nothing.
  2. **The tasks**, in every file of every owner (open, closed, archived): the first A–Z of a task's tags that names a list of its own kind (a shared task: the shared lists; a private task: its user's) becomes its `list`, and that tag goes.
     Any other list's tag stays, as a plain tag, so the task still shows in a `tag=` view.
     A private task without a list of its user's (it carried a shared list's tag, or none) is in their Inbox (until the Inbox it went to a private list `private` of theirs, made for it and pinned).
     A shared task carrying the tag of someone's private list keeps the tag and is in no list.
  3. **The pins**: `pin=1` and `pin=default` on shared pages become the wiki's (`pinned` in the shared registry), the default one first, then by name; those on a user's private pages theirs, followed by the wiki's, as before.
     A pin of a view that is no list (`[[tasks: tag=me pin=1]]`) has nowhere to go: the log says so.
     `pin=` is taken out of the pages' embeds.
  4. **Views by a list's tag**: an embed without `list=` whose `tag=` asks for a list's former tag (`[[tasks: tag=shop,urgent]]`) shows the list from then on (`[[tasks: list=shop tag=urgent]]`),
     and so does a link (`/tasks?tag=shop` becomes `/tasks?list=shop`),
     since the tag left the list's tasks and the filter would find none of them.
     A private page's embed names a list of its user's or a shared one.
     `tag=-shop` and `tag=shop|work` stay as written and no longer find the list's tasks (the filter words `-list:shop` and `list:shop` do); text in code stays, and so do links in tasks.
  5. **The settings of All tasks** move from `_tasks/settings.json` into the shared registry (`all`), and that file goes.

  The tasks are written first, then the users' registries, the shared one (whose `version` says "done") and last the pages, so a start that was interrupted runs again without harm: a task keeps the list it got.
  A task file that can't be read stops it before anything is written; Zone then runs, says so in its log, and refuses every change to lists until a start succeeds (`listsUnmigrated`), since a registry written meanwhile would stand for "taken over".
  A wiki with nothing to take over (no list declared, no pin, no private task, no `_tasks/settings.json`) gets no registry: its first list writes it.
  Going back to an older Zone: revert that commit in the wiki's repository; without it the older Zone finds every list empty.

### Touch

- With a mouse (`hover: hover`) ⏭ shows only on hover, for the selected card, or with focus inside, left of the dates (right-click opens the menu too);
  under `(hover: none)` ⏭ is hidden (the ⋯ menu has it);
  ⋯ is always shown;
  under `(pointer: coarse)` buttons,
  checkboxes and chips get finger-sized targets (a checkbox's label reaches into the card's padding and the gap before the title, without moving the box, so a tap just beside it ticks the task instead of selecting the card; a press on the label never selects, with a mouse either),
  fields use 16px text (no iOS zoom),
  and cards can't be text-selected.
- **Selection mode** (`selectMode`, `.tasks-selecting`;
  on a phone, there being no Shift or Ctrl, a **long press** on a card let go without moving, or ⋯ → **Select several**, which works with a mouse too): starts with that card selected and shows the selection bar even with one task or none ("Tap tasks to select", the actions disabled).
  A tap or click anywhere on a card adds or drops it and does nothing else (a capture-phase click handler on the list: the checkbox doesn't complete, links don't open, no editor, no expanding); each card's checkbox becomes a round mark, filled with a tick on selected cards, and ⋯ is hidden.
  No long-press drags, swipes or mouse drags while it lasts.
  It ends with ✕,
  Esc,
  another tab,
  Done or Won't do (Tag…, Today and Unplan keep it, for a next action),
  or the browser's back (a phone's back gesture): entering pushes a history entry (`tasksSelect` in its state),
  `popstate` ends the mode,
  and ending it otherwise goes back past that entry,
  so back then leaves the page as before.
  A background reload keeps the selected tasks still shown.
- **Long-press drag**: 350 ms without moving over 10px picks a card up and selects it (without scrolling).
  A floating copy (`.task-touch-ghost`) follows the finger, the page auto-scrolls near the edges, and release drops through the same code as a mouse drop (`touchDrop` adapters registered by each render; list touch listeners attached once).
  A touch `pointerdown` sets the card's `draggable` to false (mouse sets it back; cards are marked `data-drag`), and native `dragstart` is cancelled for touch, because Android Chrome and iOS Safari otherwise start their own HTML5 drag mid-gesture.
  Moving earlier is a normal scroll; a finished drag isn't a tap.
  Let go without having moved over 10px, the long press starts selection mode with that card instead of dropping it; where nothing drags (Closed, or a view without dragging) the press only selects the card (`holdCard`), letting go starts selection mode, and moving over 10px first cancels it.
  The browser's own long-press menu is suppressed on cards.
- **Swipes** (`swipeActions`; they may start on a date chip, which sits on the first line where a thumb starts, a mere tap still opening its picker) only move tasks in and out of Focus: right = start today (Open, Plan; on Plan also a carried-over task, committing to it again),
  left = clear the start date (Focus, Plan).
  No action, no movement (e.g. right on a Focus task, left on an unplanned or due task).
  A mostly sideways move (|dx| > 1.5·|dy|, over 10px) drags the card and reveals the label (`data-swipe-label`); past `min(110px, 30%)` it arms (`.task-swipe-armed`), and release runs `setStart` with an undo toast.
  Cards have `touch-action: pan-y` and `.tasks-list` `overflow-x: clip`.

### Keyboard shortcuts

- **g a**: go to `/tasks`, all tasks; **g t**: go where the Tasks link goes (My tasks)
- **→** / **l**, **←** / **h**: open / close the selected task's description and comments; open, they stay open as j/k move on (see One line per task)
- **o** (the Open tab): the order: manual, by deadline, newest first (see Sorting)
- **d** (the Open tab): Plan on / off, by start date or one list (see Views)
- **i**: the importance of the selected task, in turn high, low, normal; several selected all get what follows the first one's (see Importance)
- **Shift+F10** / menu key: the selected task's ⋯ menu (on several selected tasks, the selection's)
- **Shift+click**, **Ctrl/Cmd+click**, **Shift+↑/↓**, **Ctrl/Cmd+A**: select several tasks (see Selection); **t** / **T**: their tags; **m**: move them to a list
- **@** (with users): the filter field, starting an assignee's name, as **#** starts a tag
- **a** (with users): assign the selected tasks (see Assigning tasks in `docs/spec/users.md`)
- **c t**: a new task in your Inbox, starting today (from any page; see Modules → New task from anywhere).
  The tasks' keydown runs before the page's shortcuts, so while a `g`/`c` prefix waits for its second key (`keyPrefix` in `js/util.js`) the tasks leaves that key alone: the t of **c t** or **g t** never opens the bulk tag dialog on a selected task
