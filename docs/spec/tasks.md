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
| `list` | a tag | — | Makes the embed a separate list: it shows the tasks with that tag, the tag is locked there and marked as a list everywhere (see Tasks → Separate lists). Cleaned like tags, lowercased (`cleanList`). |
| `keep` | `1`/`true`/`yes` | — | New tasks created in the embed are keep tasks (the edit modal's checkbox starts checked), e.g. a shopping list of staples. |
| `pin` | `1`/`true`/`yes`, or `default` | — | **Pins** this embed's list, your own tasks: the Tasks module links go to it (one: `/tasks?list=<name>`; several: all of them as one, `/tasks?pinned=1`), its page belongs to the Tasks module, the Tasks links' due count counts what the pinned lists show (a task in two once), and the list switcher marks it with a pin. Several may be pinned: `pin=default` first, then those in your private folder, then shared ones, each A–Z by name (`lookupPinnedIn`); a list pinned twice counts once. The first is the **default list**, where New task and `c t` add a task (see Modules); the New task dialog says which list a task goes to. Without any pin, the Tasks links go to `/tasks`. |
| `new` | tags, comma-separated | — | The tags a new task here gets, in place of the filter's single tags (the Pinned view sets it to the default list's). |
| `labels` | up to 2 comma-separated names | `Open,Focus` | The embed's own names for the tabs, in on-screen order, e.g. `labels=Catalogue,Shopping` for a shopping list (three names, from when Plan was a tab: the first and the last count). Only the words change: views, `view=` values and links keep their names. An empty entry keeps the default, `_` stands for a space (params split on whitespace). Rendered into the tab buttons by `buildTasksEmbed`; `tasks.js` reads them back (`tabLabel`) for the "← Back to …" link, the limit warning and the ⟲ tooltip. |

```markdown
[[tasks:]]
[[tasks: tag=urgent view=plan]]
[[tasks: view=open]]
[[tasks: list=groceries tag=weekly]]
[[tasks: view=focus limit=5]]
[[tasks: list=groceries keep=1 labels=Catalogue,Shopping]]
[[tasks: list=task view=focus limit=8 pin=1]]
[[tasks: tag=work plan=off]]
```

The params can be changed without editing the page: List settings… in the ⋯ menu of the embed's toolbar (see Settings).
In the editor, the formatting bar's Task list button shows the same dialog, with the list's name and keep, to write the embed or change the one the cursor is on (Task list dialog in `docs/spec/editor.md`).

**Headless instance**: `build_tasks_embed(params, headless=True)` renders the app hidden, for pages that only need its edit modal (the editor's Make task, see Task references).
`openEditModal(null, opts)` takes, besides `tags`, a starting `title`, `description`, `start_date` and an `onCreated(task)` callback; `titles` instead of `title` is batch mode (one task per title, the fields shared, `onCreated` gets the array).

**Implementation** (`go/tasksyntax.go` for the syntax, `go/tasksembed.go` for the HTML): `tasksRE` finds the directive and `parseTasksParams()` parses its `key=value` pairs.
`processTasksEmbeds()` swaps each one for a numbered placeholder before rendering, and `injectTasksEmbeds()` replaces each `<p>PLACEHOLDER</p>` with `buildTasksEmbed()`'s HTML (`templates/tasks_embed.html`) afterwards.
The embed passes its params as `data-init-*` attributes; it carries no script: `tasks.js` (an ES module `zonewiki.js` imports on every page) starts every `.tasks-app` on load (`initTaskApps`, marking each `data-ready`).
Element ids get a `-<n>` suffix after the first embed, so a page can hold several.
**A page brings its tasks along**: a page view with a task list (and `/tasks`) carries, once, the user's open tasks as `GET /api/tasks` answers them
and their notes' and comments' HTML as `POST /api/render-notes` would (`tasksInitial`: a `<script type="application/json" id="tasks-initial">` data block, which no browser runs; `<`, `>` and `&` in it are `\u` escapes, so no task's text ends it).
The lists the page starts with draw from it, each from its own copy, with no request (`initialTasks` in `js/taskdata.js`, `keepRendered` in `js/tasktexts.js`), so the list is there when the page is rather than two round trips later.
Every later load asks the server, and so does a list that starts later (a preview's, the hidden one a page without a list fetches for its dialog): `initTaskApps` forgets the tasks once the page's lists have started.
`tasks.js` also reads `?tag=`, `?view=`, `?search=`, `?task=`, `?new=1` from the URL; `?new=1` opens the new-task modal in one embed only, a pinned one if the page has it, else the first.

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
There is no polling.

### Storage

- `_tasks/settings.json` holds the settings of All tasks (see Settings); it is no tasks file.
- The tasks' files live in the `_tasks/` folder (`tasksFile(owner)` and paths derived from it; with users each user's private tasks in `private/<user>/_tasks/`, the same files: Private tasks in `docs/spec/users.md`): open tasks in `tasks.json`,
  closed ones in `closed.json` (flat JSON arrays, committed to git on change).
  Closed tasks from earlier years live in `closed-YYYY.json` (only that exact name is an archive).
  `atomicfile.Write` creates the folder, so a new wiki gets it on the first write.
  Write routes read every closed file (`readClosedAllStrict`) and `writeClosed` files each task by its completion year, writing only files whose content changed; startup does the same split (`archiveOldClosed`) for a year change without writes.
  So archived tasks can be edited, reopened and tagged like any other; Closed shows them by year (see Views).
- All mutations go through one lock (`taskFiles.Lock`, `go/internal/taskfiles`) and atomic writes (temp file + rename, `go/internal/atomicfile`); the server handles requests concurrently.
  Write paths use a strict reader that fails with `errTasksCorrupt` (503) on an unreadable file instead of treating it as empty and wiping every task.
- The files are JSON (`go/internal/taskmodel`): a list of tasks,
  two spaces of indent,
  text as written (no `\u` escapes but those JSON needs),
  each task's fields in one order (`id`, `title`, `description`, `tags`, `start_date`, `due_date`, `created_at`, `closed_at`, `wont_do`, `keep`, `importance` (only when it isn't normal), `last_done`, `sort_order`, `recurrence`, `spawned_from`, `history`, `comments`),
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
| `tags` | string[] | One word each. `cleanTags()` on every write/read/restore: inner whitespace → `-`, leading `#` dropped, empties removed, case-insensitive duplicates collapsed. Tag inputs sanitize as you type (`TasksCore.sanitizeTagInput`), leaving deletions alone. |
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
  It never hides what you look for: while the viewer filters every section shows (without fold buttons; `userFiltering`: their own words, not the locked `#tag` chip of a `list=` or `tag=` embed),
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

**List settings…**, the first item of the list's **⋯ menu** in the toolbar (`#task-more-btn`; the toolbar has no button of its own for it), opens **List settings** (`#task-settings-modal`, `js/tasksettings.js`): what the list is and how it opens, without editing the page.
It changes the list's params where they are written, so the dialog and a hand edit are the same thing:

| Field | Param | |
|-------|-------|---|
| Opens on | `view` | The Open or the Focus tab (by their names here); Closed is offered only where the page says `view=closed` |
| WIP limit | `limit` | 1–99, empty for none |
| Plan | `plan`, `view` | The Open tab grouped by start date, or one list |
| Pinned | `pin` | No, Pinned, or Pinned as the default list |
| Tab names (under More) | `labels` | The Open tab's and the Focus tab's; empty keeps Open and Focus |
| Tags of a new task (under More) | `new` | Comma-separated; empty: the list's tag and the single tags it shows |
| Shows only the tags (under More) | `tag` | The filter: `a,b` both, `a\|b` either, `-a` without |
| Shows only the tasks of (under More, with users) | `assignee` | `me` whoever looks, a user's name, `a\|b` either, `none` no one's, `-a` not theirs |


**More** (`<details class="task-settings-more">`) folds the fields that are rarely changed; it is open when the dialog opens with one of them set.
The line above Save says where the settings are written.

**Where it writes** (`data-settings-page`, `-index`, `-raw` on `.tasks-app`, from `buildTasksEmbed`: the page, which of its `[[tasks: …]]` outside code, and the params as written there):

- **An embed on a page**: that `[[tasks: …]]`.
- **A list on `/tasks?list=<name>`**: the `[[tasks: …]]` whose params it shows (its pinned embed's, else its home's); the dialog names the page.
- **Pinned** (`/tasks?pinned=1`): the default list's `[[tasks: …]]`, whose params the view has.
- **All tasks** (`/tasks`): no page holds its params, so they are in `_tasks/settings.json`, `{"all": {"view": "open", "limit": "5"}}` (`allTasksFile`, `allTasksParams`), for everyone.
  It has no Pinned and no filter (the fields are hidden; `allSettingKeys`).

**Not which list it shows**: `list=` isn't in this dialog (it is in the editor's, where the dialog writes the embed: `.Editor` in `task_settings_dialog`).
Another name there would show another list and leave the tasks behind, which reads like a rename and isn't one; a list is renamed by **Rename…** in the ⋯ menu of `/tasks?list=<name>`, made by **New list…** there, and an embed is made a list by editing the page.

**No ⋯ menu, so no settings**: the editor's preview, an old version, a page from the uncommitted list (only a page view renders its own file: `RenderOpts.EditSlug`), a tag no page declares (`/tasks?list=<tag>`), the headless app, a viewer, a page you may not change, sharing mode.

**Saving** (`POST /api/tasks/settings`, `tasksSettingsRoute`): the dialog sends only what was changed (`changedParams`), `{page, index, was, set}` or `{all: true, was, set}`: `set` maps a param to its new value, `""` taking it out; `was` is the params as the dialog saw them.
So saving without a change writes nothing, and a param the dialog doesn't know (`keep`, anything else) stays as written.
The server checks each value (`checkEmbedSettings`: one word without `[`, `]` or a backtick; `view` a view, `plan` on/off, `limit` 1–99, `pin` `1`/`default`, `new` cleaned like tags; `list` is refused) and rewrites the params (`setEmbedParams`, `setEmbedArgs`): a param keeps its place,
a new one goes last,
one written twice is written once,
`pin` replaces the old `home`;
the rest of the page is untouched.
Opens on and Plan are written as the views' names: Open with Plan is `view=plan`, Open without `view=open`, and `plan=off` only where `view=` isn't one of those two.
A page is changed in one commit, "Change the task list's settings on <slug>" (`changePageOp`: what it held uncommitted is committed first), All tasks in "Change the settings of All tasks".
When the `[[tasks: …]]` is no longer as the dialog saw it (the page was edited meanwhile), nothing is written: 409, "The page has changed since.
Reload it and try again." Other refusals: 400 with the reason, 403 for a viewer, 404 for a page you can't see; the dialog shows the message.
After a save the page reloads, and what the browser tab remembered (the active tab, a flip of the Plan switch) is forgotten, so the list opens as just set (`forgetSession`).

### Export and import

**A list's tasks as a file**, to keep a copy, move a list to another wiki or hand it to someone.
**Export tasks** and **Import tasks…** are in the list's **⋯ menu** in the toolbar, under List settings… (`#task-more-btn`, `.tasks-more-btn`; the cards' menu, `showMenu` in `js/taskmenu.js`, a bottom sheet on phones; the code in `js/tasksettings.js`).

- **Export tasks** (`GET /api/tasks/export?list=&tag=&assignee=`, `apiExport`): downloads `zone-tasks-<list>-<date>.json` (`all` for All tasks;
  the name goes into `Content-Disposition` quoted and escaped, `mime.FormatMediaType`, as the list's name is the request's): the tasks the list shows (its `list=` and `tag=`, not what is typed in the search field),
  open and closed of every year,
  each as the API shows it,
  with its description,
  dates,
  recurrence,
  comments and history: `{"zone_tasks": 1, "list": "shop", "tag": "", "exported_at": "…", "tasks": […]}`.
  With users it holds the shared tasks and your own private ones, marked `"private": true`.
- **Import tasks…** (`POST /api/tasks/import`, `apiImport`): pick such a file, and a dialog (`#task-import-modal`) says what it holds ("Import 12 tasks from “shop.json”") and asks for a tag of your own; **Import** adds its tasks to the list the menu was opened in, Cancel nothing.
  With users, a task assigned to someone who can't be given tasks here comes in as no one's, and the toast says how many ("…; 2 were assigned to people who are not users of this wiki").
  A file that holds no list of tasks is refused at once, in a notice.
  They get the tags a new task there would (the list's tag and the single tags it shows, or `new=`), in place of the tag of the list they were exported from, so a `shop` export imported into `groceries` shows up there; their other tags stay.
  On All tasks they keep their tags as they are.
  Closed tasks arrive closed.
  **A task keeps its id**, and one whose id is in the wiki already is skipped: importing the same file twice adds nothing, and a file exported here brings back only what was deleted since (an id that isn't a UUID is replaced).
  So a list can't be duplicated within one wiki this way.
  A task exported private stays private, wherever it is imported; on a list in your private folder every imported task is private, as a new one is.
  **A tag of your own** (the dialog's field, optional; `also`): the imported tasks get it too, on top of the list's, to find the batch again later (`from-anna`); cleaned like any tag, several comma-separated.
  One commit, "Import N tasks".
  The notice says what happened ("Imported 12 tasks; 3 already here.") and has **Undo** (also Ctrl+Z, for the few seconds it shows; `showUndo`): the tasks just added are deleted again (`/api/tasks/batch-delete` with the `ids` the import answered).
  A file that isn't an export (no `tasks` list, or a task without a title) is refused whole (the server's reason shows in the dialog), as is one with more than 10,000 tasks (`importMax`); a bare JSON list of tasks is taken too, so a file written by hand or by a script needs only `[{"title": "…"}]`.
  A viewer can't import (403).

The ⋯ menu is there where a list has settings (see Settings: not for a viewer, nor a tag no page declares).

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
Titles are bold and tags plain muted words (a list's tag keeps its tint and icon), so the cards' urgency colours stand out.
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

- **Filter field** (`#task-filter-search`, `TasksCore.parseFilter`/`matchesFilter`): `#words` filter by tag (all must match; `#a|b` = any), `-#word` excludes a tag (`-#a|b`: neither), `!high` (or `!`) and `!low` show the tasks of that importance, the rest searches title and description.
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
  **List…** (the list dialog, below; hidden while no page declares a list),
  **Assign…** (with users: who has them, Assigning tasks in `docs/spec/users.md`),
  **Today** (start them today, `startToday`: one `batch-update` for the open ones not starting today, one undo putting back each one's start date and history; today is never after a deadline still to come, and starting an overdue task today is the catch-up case, so no deadline question),
  **Unplan** (clear their start dates, `startOn(ids, null)`, as the **0** key: one `batch-update` for the open ones that have one, one undo putting them back; shown while an open selected task has a start date, `anyPlanned`),
  **Done** (`completeMany`, as Space; **Reopen** when every selected task is closed, which also hides Today and Won't do),
  **Won't do** (`wontDoMany`, as the Delete key) and ✕ (clear).
  Toasts move up above it.
  The **⋯ menu** of a card in the selection (its button, a right-click, which then keeps the selection, or Shift+F10) offers the same for all of them, titled "N tasks" (`runSelectionAction`).
  With several task lists on a page only the active one's bar shows (`claimInstance`).
  At ≤640px the bar spans the bottom edge in place of the phone tab bar (hidden while it shows): "N selected", All and ✕ on top, the actions as buttons sharing the width under them (on a second row when one doesn't hold them), ≥44px tall; the page end and toasts keep clear of it.
- **Moving several tasks to a list** (`openBulkListModal`, `.bulk-list-modal`; the selection bar's **List…**, **Move to list…** in the menu of a selected card): the dialog, "Move 3 tasks to a list", lists every list you see (the declared ones, A–Z, each as its `#name`, its page in the tooltip) and **No list**.
  A click on a row moves the selected tasks at once (↑/↓ go through the rows, Enter picks, Esc or Cancel leaves).
  A list is a tag, so the move swaps tags in one `batch-tag`: the chosen list's tag is added, the other lists' tags the selection carries go, and every other tag stays; No list only takes the lists' tags away.
  The row of the list every selected task is in (and no other) is ticked and has the focus; a list only some are in says how many ("1 of 3").
  One undo puts back every task's tags ("3 tasks moved to list “work”", "3 tasks taken out of their lists").
  The selection stays, as after Tag…; in a list's own view the moved tasks leave it.
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
  Start: `today`, `tomorrow`/`tmrw`, a weekday (next one, never today), `+3`/`+3d`/`+2w`, `YYYY-MM-DD`; due: `due:` + the same; tags: `#tag` starting with a letter; with users, the assignee: `@name` of a user who can be assigned, or `@me` (anything else stays in the title); importance: `!` (or `!high`) and `!low`, as words of their own ("Call Bob !", not "Call Bob!").
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
  **Copy link** puts the task's full URL in the clipboard (`taskHref`, as `taskLink` builds it: `/tasks?task=<id>&view=open`, `view=closed` (with `&year=` from an earlier year's archive) for a done task, `&list=` for a list task); opening it selects and flashes the task (see `?task=`).
  Paste it anywhere: chat, mail, a page.
  A popover with ↑/↓/Enter/Esc on desktop (tasks shortcuts suspended while open), a bottom sheet at ≤640px.
- **Edit modal**: double-click anywhere on a card (title and description too; only its controls, the checkbox, buttons, links and date chips, keep their own) or Enter on desktop;
  on touch the first tap selects the card (also showing a cut-off title whole, see One line per task) and a tap on the selected card opens it (`pressedSelected`, noted at `pointerdown`, since touch's compat mousedown selects before the click;
  pointer type of the last `pointerdown`;
  a double tap's `dblclick` is ignored, so it is just those two taps),
  or Edit… in the menu.
  Centred with `inset: 0; margin: auto` and **no transform** (a transform would clip `position: fixed` popups like the date picker), scrollable, sticky action bar.
  At ≤640px full screen.
  **The fields**: Title, Description, Tags, then one line of small boxes that wraps (`.task-props`), each with its name inside, in grey before its value (`.task-prop`, a `<label>`, so a click anywhere in it reaches the field): Due, Start and Importance, which every task shows.
  **The rest shows only when set** as the dialog opens: with users Assignee (a field of its own after Tags; Assigning tasks in `docs/spec/users.md`), Keep after done, Private, and Repeat with "Every 2 weeks" and From done (the two only once a repeat is chosen). Repeat comes last, on a line of its own (`.task-prop-group`), so the two fields a chosen repeat adds come beside it and Repeat stays where it was clicked.
  Under the boxes a quiet line offers the ones not shown (`.task-add-fields`: "+ Assignee" with users, "+ Repeat", "+ Days before due", "+ Keep after done", "+ Private"): a click shows the field and goes to it (`showField`), and ticks Keep or Private, since asking for the box is asking for that.
  A field stays for as long as the dialog is open, also emptied again; a line with nothing left to offer is gone.
  Not offered: Repeat for a keep task, Days before due without a due date (and it is never shown on opening: it is another way to set Start, which shows the result), Private for a shared task (it stays shared), Assignee for a private one (it has none).
  Repeat without a date shows, but off, and the line under the boxes says why ("To repeat, a task needs a due or start date.", `#task-edit-rec-note`).
  **Tab goes from field to field**: Title, Description, the comment field while it is shown (and its Add once something is written), Tags, Assignee when it is shown, Due, Start, Importance, the fields shown of Days before due, Keep, Private and the repeat fields, the buttons of the line that offers the others, then Save, Cancel, Won't do.
  What isn't a field of the task is no stop (`tabindex="-1"`) and has its own key or the mouse: Edit | Preview (F2), the formatting bar (Ctrl+B/I/K), Write a comment… (Alt+C) and the comment field's Cancel (Esc), a comment's ✎ and × and the links and boxes in comments, History.
  The List field sits in a closed `<details>`; saving a task into another list removes it from the view with a notice.
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
and with users `shared` (a private task made shared, by whoever did it; older tasks may show `private` from when tasks could move the other way)
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

### Separate lists

**A tag sorts tasks; a list is a place.** A tag filters (a task has any number of them, and none takes you anywhere).
A list is a tag a page declares with `[[tasks: list=<tag>]]`,
to say "I want to go there": it is an entry in the list switcher (below) with its counts,
it has a home page with its own view (`view=`, `keep=`, `labels=` …, where links to its tasks open),
a new task made there gets its tag and its location (private when the page is in your folder: Private tasks in `docs/spec/users.md`),
and its tag is marked as a list wherever it shows.
Underneath it stays a tag: a task can be in two lists,
the main task list shows every task,
list tasks included (it once hid them, with a "Show separate lists" checkbox to bring them back; removed 2026-10, since the main task list is the view of all tasks you filter),
and editing the declaration away takes the list out of the switcher,
never its tasks.
There is no `list` field (an old stored one is ignored); a task is in a list by carrying its tag.
A tag no page declares stays a filter.

- **The list switcher** (`.tasks-places` in `templates/tasks_page.html`, `taskPlaces` in `go/tasksembed.go`): one Tasks link can't stand for several lists,
  so `/tasks` opens with a row of pills above its heading,
  on the page's white sheet with it, looking as the module switcher at the top of the page does (`.tasks-places-group`: one white capsule with a border, the pills inside it without one, in its type and height, the current one shaded),
  one per list,
  **named by the list, never by a page that embeds it**: with several pinned lists (`pin=1`) first **Pinned**,
  all of them as **one task app** at `/tasks?pinned=1` (`pinnedView`: the default list's params, showing the tasks of any pinned list, `tag=a|b`, with the list-less pins' single tag;
  new tasks get the default list's tags, `new=`, so one Focus, one toolbar, and the cards' list chips say where each task is;
  under the heading a line names the lists it shows, "Your pinned lists, as one: #task, #shop", each a link to its list, a pin without a list by its page's title, and its **Backlinks** are the pages that embed any of them, once each: `pinnedSources`),
  then **the pinned lists** in their order,
  then **each other list** you see,
  A–Z by name,
  and **All** (`/tasks`, every task).
  Pinned and the pinned lists carry a pin (`.tasks-place-pin`, tooltip "Pinned list #<name>").
  The heading names what is shown: **List: <name>**, **Pinned** or **All tasks** (the window's title likewise).
  A pinned list is one pill even when other pages declare it too (in your private folder, or earlier A–Z, either of which makes another page its home: Declarations, below), and it keeps its pinned embed's view and counts.
  A single pin whose embed declares no list (`[[tasks: tag=me pin=1]]`) is a pill **Pinned**, at its page; `/tasks?pinned=1` with fewer than two pins redirects where the Tasks link goes.
  Every list pill links to `/tasks?list=<name>`: the list with its params (a pinned list's pinned embed's, else its home's),
  with no line naming a page,
  and as its **Backlinks** every page that embeds it (`listPages`: any `[[tasks: list=<name>]]` outside code on a page you may see, A–Z by title; at the end, in the right column on big screens and as the breadcrumb's `← N` chip, as on a page view).
  Each pill carries the counts the Tasks module link has (due today or overdue, in Focus: `countTasks` over the list's `list=` and `tag=`; Pinned's over the Pinned view's), and a lock when its page is in your private folder (tooltip "… (only you see it)");
  the current one is shaded (`aria-current`): All on `/tasks`, Pinned on `?pinned=1`, the list on `?list=<name>`, none for a tag no page declares.
  **Only `/tasks` has it**: a page that embeds a list (its home included) shows the list, as any embed does, and no switcher, so the Tasks module and the wiki's pages stay apart; the pages are a list's Backlinks.
  With nothing to switch between (no pin and no list) there is no switcher.
  Another user's private list is not in it; in sharing mode a list whose page is not for sharing is left out, and the counts are hidden as on the module links.
  `js/chrome.js` keeps the counts current with the Tasks link's (`refreshDueBadge`: `GET /api/tasks/due-count?places=1` adds `places`, each pill's counts by its key: `list:<name>`, `all`, `pinned`).
  The pills wrap onto more lines on a narrow screen and are finger-sized under `(pointer: coarse)`.
- **Where a new task goes** (`.task-edit-target`, `syncTarget` in `js/taskmodal.js`): the edit dialog of a new task says it under the title: "Adds to the list #<name>." (the first of its tags A–Z that is a list, as the server files it),
  or "Adds to no list (tags only).",
  with ",
  private" when its Private box is ticked.
  It follows the Tags field and the Private box, so swapping `#task` for `#shop` there moves the new task to another list before it is saved.
  Hidden when editing a task and for several titles at once.

- **The Tasks page's ⋯ menu** (`.page-menu-btn` beside the heading of `/tasks`, on every view of it; the page's ⋯ menu, `#page-menu`, with `js/pagemenu.js`): **New list…**, and on a list **Rename…** (below).
  Not for a viewer, nor in sharing mode.
- **New list…** (`#list-new-dialog` in `templates/tasks_page.html`; `POST /tasks/new-list`, `newListOp` in `go/pageops.go`): asks for the list's name and makes it, without editing a page.
  A list is a tag a page declares, so Zone writes that page: `lists/<name>` (`lists/shop`), titled by the name as typed ("Shop") and holding `[[tasks: list=<name>]]`, in one commit (`Create lists/shop`); the dialog names the page while you type.
  Then the list opens (`/tasks?list=<name>`), with its entry in the list switcher.
  The page is the list's home: List settings… writes there, and you can move, rename or extend it like any page; deleting it (or the embed) takes the list out of the switcher, never its tasks.
  With users the dialog has **Private: only you see it**, which puts the page in your private folder (`private/<you>/lists/<name>`): the list and the tasks made in it are yours alone (Private tasks in `docs/spec/users.md`).
  The name is cleaned as an embed's is (`cleanList`: lowercase, a leading `#` dropped, spaces to `-`).
  A tag tasks carry already can be made a list this way: its tasks show in it at once.
  Refused, back in the dialog with the reason: no name, a name with `[ ] , | =` or `"`, a list you already see ("There is a list #shop already"), a page already at `lists/<name>`.
  An assistant makes a list by writing such a page (`write_page`).
- **Renaming a list or tag** (**Rename…** in the ⋯ menu of `/tasks?list=<name>`, `#list-rename-dialog` in `templates/tasks_page.html`; the MCP tool `rename_task_tag`; `renameTagOp` in `go/pageops.go`): a list is a tag,
  so this renames the tag,
  and it works the same for a plain tag no page declares (open `/tasks?list=<tag>`).
  In one commit it changes the tag on every task (open, closed, archived) and, on pages and in tasks' text, the `list=`, `tag=` and `new=` of `[[tasks: …]]` embeds and the `?list=` and `?tag=` of links to `/tasks`.
  Pins and an embed's other params stay.
  Code stays as written, and so do a `#word` in prose and the `tags:` in pages' frontmatter, which sort pages rather than tasks.
  Case doesn't matter in matching: `Shop` and `shop` are one tag, and renaming a tag to its own name evens out how it is spelled.
  A list's name in an embed is lowercase (`cleanList`); task tags keep the spelling you typed.
  - **A name already in use** (a task you see has that tag, or a page you see declares that list) merges the two, so it asks first: the dialog comes back saying so with a **Merge** button (editing the name takes that back), and the MCP tool wants `merge: true`.
    A task with both tags ends up with one.
  - **Refused**, with nothing changed: a tag you don't see anywhere, an empty name, a name with `[ ] , | =` (they mean something in an embed), or a tasks file or page that can't be read.
    Text not yet in git is committed first (`keepPendingPages`).
  - **With users**: a tag on anything shared (a shared task, an embed on a shared page) is one name for everyone, so the rename reaches every user's private tasks and pages too, as a page rename's links do.
    A tag only you see stays yours: another user's private tag of that name is another tag and isn't touched.
    A tag only someone else has is refused as if it didn't exist, a new name only they use is no merge for you, and the answer counts only the tasks and pages you see.
- **Declarations** (`taskLists(user)`;
  `go/tasks.go` and `taskList` read it): every page the user sees is scanned (a list declared in a private folder is its user's alone,
  and a home for them ahead of a shared declaration: Private tasks in `docs/spec/users.md`) for `[[tasks: list=…]]` outside code (`pageTaskLists`,
  kept in `metaCache`,
  so by mtime),
  giving `{name: {slug, title, params}}`.
  When several pages declare one, the first slug A–Z is its home.
  A tag no page declares is an ordinary tag (`/tasks?list=<tag>` still shows its tasks, with a note saying no page declares it).
  Editing the declaration away ends the list at once.
- **A task's list** (`taskList`, mirrored by `taskList` in `tasks.js`): the first (A–Z) of its tags that is a declared list.
  Links to a list task (search, Linked tasks, task chips, MCP) carry `&list=<name>`; on a page that declares a list the task is in, its chips and Linked tasks use that list instead (`linkList`), so a task in two lists (`#presentideas #task`) opens in the list the page is about.
  A chip re-rendered after ticking keeps it: `js/page.js` sends the page's list embeds as `/api/preview?lists=…`.
  And `/tasks?list=<name>` renders with the declaring embed's params, so the list looks the same everywhere.
- **Scopes** (`inScope`, `inScope` in `tasks.js`): a list view shows its tag's tasks;
  the **main task list** (an embed with neither `list=` nor `tag=`, including `/tasks`, URL `?tag=` notwithstanding) shows every task,
  list tasks included,
  as do a `tag=` embed (a `#task #sam` task belongs on a Sam page whatever lists exist),
  the main Closed count and,
  without a task list home,
  the due count.
- **Locked filter**: an embed's own filter words (`list=` and `tag=`; `embedFilterWords`) are chips without × (`chipField`'s `locked`, `.tag-chip-locked`; Backspace skips them), so the page always shows what it says and new tasks get its single tags.
  A URL `?tag=` stays removable.
  In the filter,
  the edit modal and on cards a list's tag comes first and is marked as a list: a list icon (an SVG mask in CSS `::before`, `--list-icon`, in the accent colour; so the text stays `#name`, what you type),
  a faint blue tint and a tooltip naming its home page ("List: Shop", `listTooltip`; titles from the embed's `data-list-homes`, `taskLists`' `title`).
  Other tags (locked or not) stay plain (`listWord`, `.tag-chip-list`; `.task-tag-list`).
  A task's tags are never locked: swapping `#groceries` for `#hardware` in the edit modal or with bulk tag (`t`/`T`) moves it to another list.
  A save that takes a task out of view says where it went: "Saved to list “…”." when it left for a list (`listMovedNotice`), else "Saved; it no longer shows here." (`leftViewMessage`).
  Both are undo toasts (`offerUndoLeave`): undo PUTs back everything the save could change (`editSnapshot`: fields, order, recurrence and the old history, so no trace) and reloads the tasks, since the task left `tasks`.
  Bulk tag moving tasks out gives one toast ("2 tasks saved to list “…”.") whose undo restores each one's tags.
  A new task created out of view only gets the notice.

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

- **g a**: go to `/tasks`, all tasks; **g t**: go where the Tasks link goes (your pinned lists)
- **→** / **l**, **←** / **h**: open / close the selected task's description and comments; open, they stay open as j/k move on (see One line per task)
- **o** (the Open tab): the order: manual, by deadline, newest first (see Sorting)
- **d** (the Open tab): Plan on / off, by start date or one list (see Views)
- **i**: the importance of the selected task, in turn high, low, normal; several selected all get what follows the first one's (see Importance)
- **Shift+F10** / menu key: the selected task's ⋯ menu (on several selected tasks, the selection's)
- **Shift+click**, **Ctrl/Cmd+click**, **Shift+↑/↓**, **Ctrl/Cmd+A**: select several tasks (see Selection); **t** / **T**: their tags
- **@** (with users): the filter field, starting an assignee's name, as **#** starts a tag
- **a** (with users): assign the selected tasks (see Assigning tasks in `docs/spec/users.md`)
- **c t**: a new task in the default list, with its tags (from any page; see Modules → New task from anywhere).
  The tasks' keydown runs before the page's shortcuts, so while a `g`/`c` prefix waits for its second key (`keyPrefix` in `js/util.js`) the tasks leaves that key alone: the t of **c t** or **g t** never opens the bulk tag dialog on a selected task
