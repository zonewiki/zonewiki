# Tasks

Part of Zone's spec (see `AGENTS.md` for the index).
Each feature says first how it is used, then how it works.
What the task app is built on (its code, how a page brings its tasks and keeps them current, its files, what Zone takes over from an older wiki at start) is under How it works, at the end.

## The task app

The task app is at `/tasks` (a built-in page, `tasksPage()`), and any page can show it with `[[tasks:]]` (Task list embed).
Several task lists on one page work side by side, a list left open for days stays current, and what someone else changed shows within a minute (Keeping lists current, under How it works).

The Tasks page itself is `/tasks`; embeds are for custom views on ordinary pages (a tag's tasks, a list with notes around it).
Nothing links to a `tasks.md` page.

## Task list embed

`[[tasks:]]` on a line by itself embeds the full task app inline.
Syntax: `[[tasks: param=value param2=value2]]`.

The colon makes it an embed: `[[Tasks]]` stays a link to a page called Tasks.
Case doesn't matter (`[[Tasks:]]`), nor a space after the colon (`[[tasks:list=shop]]`).
(`tasksRE`, `embedArgs`.)

```markdown
[[tasks:]]
[[tasks: tag=urgent view=open group=day]]
[[tasks: view=open]]
[[tasks: list=groceries tag=weekly]]
[[tasks: view=today]]
[[tasks: list=groceries view=open]]
[[tasks: mine=1 group=list]]
[[tasks: tag=work group=none]]
[[tasks: list=team view=open group=person]]
```

| Parameter | Values | Default | Says |
|-----------|--------|---------|------|
| `list` | a list's name | — | Which list it shows (Embedding a list) |
| `mine` | `1` | — | Shows My tasks, whoever looks their own (Lists → My tasks) |
| `tag` | comma-separated tags | — | Only tasks with these tags (Filtering an embed) |
| `assignee` | `me`, a user's name, `none` | — | With users: only the tasks of that user (Filtering an embed) |
| `view` | `today`, `open`, `closed` | by the view (Which tab a page opens on) | Which tab it opens on |
| `group` | `none`, `day`, `person`, `list` | by the view | How the Open tab shows its tasks (Views) |

**An embed says which tasks it shows and how this page looks at them, nothing else** (`onPage` in `go/tasksembed.go`): which tasks by `list`, `mine`, `tag` and `assignee`; how by `view` and `group`.
What the tasks shown are, their WIP limit, tab names, a new task's tags and keep (Settings), is said once, where they are: a list's in the list's settings, My tasks' in yours, the Inbox's and every other embed's in All tasks', as `/tasks` shows them.
An embed's own `limit`, `labels`, `new`, `keep` or `pin` (from before) is ignored, and stays as written.

The params can be changed without editing the page: List settings… in the ⋯ menu of the embed's toolbar (see Settings).
In the editor, the formatting bar's Task list button shows the same dialog, with which list the embed shows, to write the embed or change the one the cursor is on (Task list dialog in `docs/spec/editor.md`).

### Embedding a list

`list=<name>` shows that list: the tasks in it, with the list's own settings (see Lists).
A new task made there goes into it.
On a shared page the name is the shared list; on a page in your private folder your own list of that name first.
A name no list has shows a line that says so, with a button that makes the list (A list that isn't there).
`list=inbox` shows the Inbox, the tasks in no list: with users whoever looks their own (Lists → The Inbox; `list=none` says the same).
The name is cleaned like tags, lowercased (`cleanList`).

`mine=1` shows My tasks, with your settings of it, and a new task made there goes into the Inbox; an embed of no list is All tasks, with its settings.
Over the settings of what an embed shows, its `view`, `group`, `tag` and `assignee` are this page's.

### Filtering an embed

`tag=` pre-filters by tag: comma = AND, pipe `a|b` = OR, a leading `-` excludes (`tag=work,-mary`).
The chips are locked (the page says what it shows); new tasks get the single tags, but a task's tags stay editable.

`assignee=` (with users) shows only the tasks assigned to that user; `me` is whoever looks at the page, so one page shows each user their own, and `none` those no one has (Assigning tasks in `docs/spec/users.md`).
It is joined as `tag` is: `a|b` either's, a leading `-` not theirs (`assignee=-anna`).
The chip is locked, and a new task here is assigned to the one user it names.
Without users it filters nothing.

### Which tab and how

`view=` opens on that tab whenever the page is opened; a URL `?view=` overrides it, and a reload stays on the tab it showed (Which tab a page opens on).
`closed` is the Closed view, which has no tab.
Case doesn't matter; any other name (`views`, `focus`, `plan`) opens the default.

`group=` says how the Open tab shows its tasks (Views): one list (`none`), the Plan board by start date (`day`), a section per assignee (`person`) or per list (`list`).
It says nothing of the tab: `view=open` alone opens the Open tab as `group=` says, or by day.
The control beside the tabs still changes it, for the browser tab.
A view that doesn't offer the one named (By person without users) shows Plan on the Open tab and one list on Today.

### The address of a task list

A task list also takes `?tag=`, `?view=`, `?search=`, `?from=`, `?assignee=`, `?task=`, `?new=1` from the URL (`tasks.js` reads them): `?tag=` and `?from=` arrive as chips of the filter field, `?search=` as its text; `?new` (whatever its value) opens the new-task modal in one embed only, one of My tasks or of the Inbox if the page has it, else the first.

### Tasks and pages

A task links to a page with a `[[wikilink]]` (or `/page/` link) in its title or description, which lists the task under the page's "Linked tasks".
The other way round, a page shows one task inline with `[[task:<id>]]` (Task references in `docs/spec/rendering.md`).
There is no `page` field: `page` in embeds or API bodies is ignored.

## Dates

A task has two independent dates; changing one never moves the other.

| Date | Field | Means |
|------|-------|-------|
| Due | `due_date` | The deadline; only a past due date makes a task overdue |
| Start | `start_date` | The day the task becomes available, and the commitment to work on it: having one makes a task *planned* |

A past start date is not overdue, it means "available since" (cards show "since Sep 21").

The edit modal's "Days before due" is just another way to enter the start date (`start = due − N`) and is not stored; the dialog shows it once asked for ("+ Days before due", with a due date).

### A start after the deadline

A start after the deadline is usually a slip, but not always (a late task planned for next week), so it is never forbidden, only questioned.

Every quick way to set a start date is one function, `startOn(ids, day)` in `js/taskselect.js` (the Plan board's drop on a day, ⋯ Start today/tomorrow and Pick start date, the card's 📅 picker, **f**/**F**, **0**–**7**, the selection bar, a swipe): it takes one task or the selection,
comes with one undo ("Starts Tomorrow · Undo", "3 tasks start tomorrow · Undo"),
leaves a task that starts that day already as it is (nothing toggles),
and asks "Move the deadline too?" (`confirmDeadlineMove`, through `startsAfterDeadline` → `planOnDay`) for a task whose deadline the day passes: Move deadline sets both dates,
with undo;
Cancel writes nothing.
An overdue task started today doesn't ask, on the board too: that is catching up on a missed deadline (swiping right never asks for that reason).

The edit modal shows a hint under the dates instead (`syncDateWarning`): "Starts after its deadline", with a button that moves the other date (the deadline, or the start when Due was moved before it).
It doesn't block saving.

### Locale

`locale` in the settings file (a BCP 47 tag like `de-AT` or `en-GB`) says how dates read and the day weeks start on, in the browser's `Intl` (no locale data in Zone).
Unset: US-English dates and weeks from Monday (an explicit `en-US` starts them on Sunday).
Only dates: Today, Tomorrow, Tmrw, the shortcuts' labels and the rest of the UI stay English.

`loadSettings` refuses a value that isn't a tag shape (`localeRE`);
`layout.html` puts it on `<body data-locale>`,
and `js/taskutil.js` (`LOCALE`, `DATES`) passes it to `tasks-core.js`'s helpers: `shortDate` (every date a card, a toast and the dialog write that isn't Today, Tomorrow or Yesterday: `formatDate` in `js/taskutil.js`, so a card's ⏰ and 📅, "since …", Closed's "done:" with the ISO date as its tooltip, history, comments; "Oct 5" or "5. Okt.", the year added when it isn't this year; or ISO, below),
`fullDayLabel` (the Plan board's days after Tomorrow),
and the date picker's month title (`monthTitle`),
weekday heads (`weekdayNames`, two letters) and first column (`weekStart`: `Intl.Locale`'s week info, Monday where the engine has none).
The task chips on pages are rendered by the server in US English inside `<time class="local-date" datetime>`, which `localizeDates` (`js/util.js`) rewrites in the locale: on the page view, a re-rendered chip, the editor's preview and the search modal's preview.

### ISO dates

`date-format = iso` in the settings file (the only value; anything else is an error in the settings file) writes dates `2026-10-05` (ISO 8601) wherever the locale would write them (`<body data-date-format="iso">`, `TasksCore.dateSettings` → `{ locale, iso }`): `shortDate` gives the date itself,
always with its year;
the Plan board's days and the picker's tooltips are the locale's short weekday and the date (`fullDayLabel`, `longDayLabel`: "Mi 2026-10-21").
The calendar's month title, weekday heads and week start still follow the `locale` setting.
The server writes the task chips' dates as ISO itself (`shortDate`), so `localizeDates` leaves them.

## Task fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | |
| `title` | string | Required; links in it work on the card (Title links) |
| `description` | string | Markdown, rendered as a page's text (Description); `- [ ] item` lines are checkboxes |
| `tags` | string[] | One word each (Tags) |
| `list` | string | The list the task is in, by its name; no key in the Inbox (A task's list) |
| `assignee` | string | With users: who has it; no key for no one (Assignee, maker and followers) |
| `followers` | `["dan", "alice"]` | With users: who follows it; no key without one (Following a task) |
| `created_by` | string | With users: who made it; not taken from a write (Assignee, maker and followers) |
| `due_date` | `"YYYY-MM-DD"` / `null` | Deadline (Dates) |
| `start_date` | `"YYYY-MM-DD"` / `null` | Start date (Dates) |
| `created_at` | ISO datetime | |
| `closed_at` | ISO datetime / `null` | |
| `sort_order` | number / `null` | Place in the manual order; `null` = Unsorted (Sections) |
| `recurrence` | `{ type, interval, from, day?, lead? }` / `null` | The series (Repeating tasks) |
| `spawned_from` | string / `null` | A series' occurrence: the id of the completed task that spawned it |
| `wont_do` | bool | Closed undone: archived like a completed task, shown with "✗ won't do", never spawns a next occurrence |
| `keep` | bool | Keep task: completing it doesn't archive it (Keep tasks) |
| `importance` | `"high"`, `"low"` | No key for normal (Importance) |
| `last_done` | `"YYYY-MM-DD"` / `null` | A keep task's last completion, shown on its card as "✓ Sep 21" |
| `comments` | `[{ id, at, by, text }]` | Oldest first, uncapped; `by` only with users (Comments) |
| `history` | `[{ at, event, by?, from?, to? }]` | What happened, oldest first, capped (History) |

`sort_order` is the place in the Sorted section (Open's and Today's, and inside Plan's days); Today and Plan have no order of their own.
`spawned_from` lets undoing a completion remove exactly the occurrence it spawned.

### Tags

A tag is one word; `taskmodel.CleanTags()` cleans tags on every write, read and restore: inner whitespace → `-`, leading `#` dropped, empties removed, case-insensitive duplicates collapsed.
Tag inputs sanitize as you type (`TasksCore.sanitizeTagInput`), leaving deletions alone.
A tag sorts tasks; it never puts one in a list.

### A task's list

`list` is the list's name, after the tags (`"list": "shop"`), and no key at all when the task is in the Inbox (see Lists).
Which list of that name is the task's file's to say: a task in a user's files is in one of their private lists (without `list`: in their Inbox), any other in a shared one (`taskList`).
So a name has to be unique only within one registry, and nobody learns another user's names from it.

A write takes a list as the user names it (its name, or `shared/<name>`: List names), `""`, `null` or `inbox` for the Inbox (with users not for a shared task, which is always in a list: 400); a name no list has is a 400.
With users the API also answers `"private": true|false`, from where the task is kept; `private` in a write is a 400 that points to `list`.

### Assignee, maker and followers

`assignee` is the user the task is assigned to (with users; Assigning tasks in `docs/spec/users.md`).
No key at all when there is none: in the file and in the API's answers, so a wiki that assigns nothing keeps its files as they were.
A write takes a user's name, `me` (whoever writes) or `""`/`null`/`none` (no one); a leading `@` and the case don't matter.

`created_by` is the user who made the task (`Task.CreatedBy`, set by `createTask`, carried on by a series' next occurrence).
No key in the file without one: a task from before the field, or a wiki without users.
The API answers it for those too, from the history's `created` entry (`Task.Maker`), so no file is rewritten for it.
It is not taken from a write.

`followers` are the users who follow the task (Following a task): told of its news as who made it is, without it being theirs to do.
In the order they came; no key at all without one.
It is no field of a write: `follow: true|false` in a write adds or removes whoever writes.
The API answers those who see the task.

## Repeating tasks

A task with a due or start date can repeat: **Repeat** in its dialog (asked for with "+ Repeat"), then **Every …** ("Every 2 weeks") and **From done**.
Completing an occurrence makes the next one; **Skip** (⏭) closes it and makes the next one too (Won't do and Skip).

| Key | Values | Says |
|-----|--------|------|
| `type` | `daily`, `weekly`, `monthly`, `yearly` | The step |
| `interval` | 1–999 (`taskmodel.RecIntervalMax`) | How many steps |
| `from` | `"due"` (default), `"completion"` | From the series' calendar, or from the day it was done |
| `day` | a day of the month (monthly, yearly) | The series' day of month |
| `lead` | days | The start→due gap, while a spawned start was clamped to today |

The series is driven by the primary date (`due_date`, else `start_date`).
`from: "due"` is calendar-aligned.
`day` keeps a monthly series on its day, so Jan 31 → Feb 28 → Mar 31; it is kept on re-save unless the type changes, and moving the primary date re-anchors it, however it is moved (edit modal, date picker, a drag onto a day, MCP).
An undo that moves a date back sends the old `recurrence` whole (`PUT`/`batch-update` take it, `taskmodel.CleanRecurrence`, applied after the date rules), so the series keeps its `day` and `lead`.
A keep task never has one (`keep` clears it).

### The next occurrence

Completing a recurring task spawns the next occurrence, stepping from the previous primary date (or today with `from: completion`) until ≥ today; month steps clamp to the month's last day.
The occurrences keep the gap between the two dates, but a new occurrence's start date is never before today (it can't have been available before the previous one was done); when that clamps it, the series remembers the gap in `recurrence.lead` for the next occurrence.
`lead` is stored only while a spawned start was clamped to today; editing either date drops it, an unchanged modal re-save keeps it.

A series without any date (only the API makes one) gets a due date.
The next occurrence stays in its list (so a private one stays private), keeps the title, description, tags, place in the order, assignee, followers, importance and maker, and carries the series' history on (History); it starts without comments (Comments of a series).

Un-completing removes the spawned occurrence while it is untouched (`untouchedSpawn`: its history ends with the `repeat`, and title, description, tags and comments are the reopened task's);
one you changed since stays, and completing the reopened task again then spawns no second one (`spawnRecurring` skips a task whose open spawn is still there).

## Views

A view says which tasks it shows and how the Open tab shows them, and has one name for each: `view=` is `today` (what is to do now), `open` (every open task) or `closed`; `group=` is `none`, `day`, `person` or `list`.
These are the names in embeds, in links (`/tasks?view=open&group=person`), in the registries and in the settings dialog.
Embeds and registries that still use the older names are rewritten once at start (The views' names, under Migrations at start); a link with `view=focus` or `view=plan` opens the default tab.

| Tab or view | Shows | Section |
|-------------|-------|---------|
| Open | Every open task: one list, the Plan board, by person or by list | Open as one list, The Plan board, By person and By list |
| Today | The open tasks that are to do now, drawn as Open draws its own | Today |
| Closed | The closed tasks, a page at a time; a link under the list, no tab | Closed |

Today is Open with a filter (`st.today`, set in `tasks.js` and `js/tasktabs.js`): the Today tab shows the Open tab's tasks that are to do now, and draws them as the Open tab draws its own (`st.activeView`: `open` one list, `plan` the board by what `st.cut` says, `closed`), so the grouping below works on both tabs, each keeping its own choice.
What is Today's alone is a rule on the filter, not a view of its own: a new task there starts today, the limit's warning, My tasks' Updates, the empty state, no Order (Today is in the manual order), which way a swipe goes.
A tab is asked for and remembered by a word (`focus` Today, `open` or `plan` the Open tab drawn that way, `closed`); `namedView` turns the names of links and embeds into that where a page opens.

### The tabs and the Open tab's control

A segmented control (`.view-tabs`, one `<button data-view>` each) in the order **Open · Today** (`data-view` `open`/`focus`).
On phones the tabs' control spans the full width.

A tab shows its tasks one of a few ways (`activeView`, and `st.cut` in `js/taskview.js`): as one list, or in sections (the board): by start date, by person or by list.
The control right after the tabs picks one (`.tasks-cut`, a segmented control of `<button data-group>`, the current one `aria-pressed`; outside the toolbar: it modifies the tab shown).
It shows on the Open tab and on Today, where there is something to choose (Today of one person without lists is one list, and has no control).
**d** goes through its choices;
not `p`, which starts presentation mode on a page with an embedded tasks.

| Choice | `group` | Sections | A drop on a section | Offered |
|--------|---------|----------|---------------------|---------|
| **List** | `none` | Unsorted, Sorted | ranks | always |
| **Plan** | `day` | Unplanned, Today, Tomorrow, the weekdays, Later (the board, below) | sets the start date | on the Open tab (on Today every task is today's) |
| **By person** | `person` | Unassigned, You, then everyone else who has tasks here, A–Z | assigns | with users, where tasks have assignees: not in My tasks, a private list or your Inbox |
| **By list** | `list` | Inbox, then each list with tasks here, your own before the shared ones | moves the task there | where the view holds several lists (My tasks, All tasks, a tag's view) and the wiki has a list |

One person without lists sees List and Plan, and **d** flips between them.
A control of named choices rather than a dropdown: each is one click, the one shown is seen at a glance, and "Plan" names what the board by day is for.

Its default is the view's `group=` (Plan without it; `data-init-group`), set in the settings dialog (The Open tab shows).
A choice is remembered for the browser tab only (`sessionStorage` `zone-tasks-group:<path>|<list>|<tag>|<instance>`, with `|today` after it for Today's: `list`, `days`, `person` or `lists`), as the active tab is, so a fresh load is back at the default, and the Open tab (a click, Tab) opens it (`tabView`, `openGroup`); one the view doesn't offer is Plan on the Open tab and one list on Today (`groupsOffered`, `defaultGroup`, `todayView`).
With more than two choices the control is `.tasks-cut--more`.
On a phone the control has the line under the tabs, with the Order (The header).

### Which tab a page opens on

Initial view, when a page is opened: URL `?view=` > embed `view=` > tab remembered in `sessionStorage` > the view's default (`data-default-view`, `DefaultView` in `buildTasksEmbed`).

**What is yours to do opens on Today, a place you look into on Open.**
Today: My tasks, a list of your own (without users any list), a view of every task on a page, and `/tasks` where the Tasks links go there (a wiki without lists).
Open: a shared list (with users: a team opens its list for the backlog and what no one has, which Today by its dates leaves out), the Inbox (what was captured, with or without a date), and All tasks beside My tasks.

**A reload (F5) keeps the tab**: the tab shown is remembered for the browser tab (`sessionStorage` `zone-tasks-shown:<path>#<instance>`, saved when a tab is chosen and when the page opens on one), and on a reload (`performance`'s navigation type `reload`) it comes first, before the URL's and the embed's `view`.
The tab the user chose is kept apart (`zone-tasks-tab:<path>#<instance>`): it is what a page without a `view` opens on, and a tab a link chose doesn't become that.
So a task opened from a link (`/tasks?task=…&view=open`) and then left for Today is still on Today after F5; the link's task is then not selected again either.
Opening the page anew (a link, the address bar, another browser tab) follows the URL and the embed as before.

**Tab**/**Shift+Tab** cycle the tabs: on the Tasks page, and on any page while a task is selected or the focus is in the list; a page that only embeds a list keeps Tab for the next link or field.

### Open as one list

**List** (`open`) shows every open task in two sections: **Unsorted** (no `sort_order`, newest first; new tasks land here) above **Sorted** (manual order).
Open has no filter for unplanned tasks: those are the Plan board's Unplanned section, where dragging to a day plans them.
So Unsorted means "not yet ranked" and Unplanned "not yet scheduled", each with one home.
A leftover `unplanned=` embed param is ignored.

### Sorting

An **Order** dropdown in the toolbar (`select.tasks-sort`, shown on Open and Plan) picks **Manual**, **Due date** or **Created**; **o** cycles them.
Due sorts by deadline, nearest first (overdue on top), tasks without one last, each in Open's order within (`TasksCore.byDeadline`); Created newest first (`TasksCore.byCreated`).
They answer "what is due soonest" and "what came in lately", which the manual order doesn't.

On Open a sorted list is flat: Due under **By deadline** and **No deadline**, Created under **Newest first**.
On Plan the sections stay (the board still places tasks by start date) and each is sorted inside; Unplanned's overdue and Today's carried-over lists stay by deadline and by start.
Nothing reorders in a sort, since the manual order is only edited where it is shown: Open doesn't drag, on Plan no section is `ordered`, so a card dropped on another card is a drop on its section (dragging between days still plans), and Ctrl+Shift+↑/↓ only says to press `o` until Manual.

Each tab's sort is remembered per task list in this browser (`localStorage` `zone-tasks-sort:<path>|<list>|<tag>|<instance>`, JSON `{"open": "due", "plan": "created"}`; missing means manual).
Today has none: it is the day's manual order, worked top to bottom.

### The Plan board

**Plan** (`view=open` with `group=day`) is a day board grouped by start date (`TasksCore.bucketKey`): Unplanned (no start date), Today (with the tasks **carried over**: started on an earlier day and still open, `TasksCore.isCarried`), Tomorrow, the next weekdays, Later.
Unplanned is first because planning starts there: its cards are dragged onto the days just below, not up past the whole week.
Later and Unplanned always show.

**Overdue has no section**: a missed deadline shows on the card (red), and the task sits where its start date puts it (an earlier start or today: Today; a later one, accepted in the edit modal: that day).
Only a deadline today puts a task without a start date on Today.
Unplanned's header counts its overdue tasks in red (`· 2 overdue`, `.board-day-overdue`; `boardBuckets` gives Unplanned its `overdue` list), shown even when it is folded, so folding can't hide them.

Dropping a card on a day sets its start date, on Unplanned clears it, on Later opens a date picker (min today+7).
A drop on a task's own heading does nothing, except Later's, which opens the date picker to choose another later date.
A drop on a day after the task's deadline (`planOnDay`, through `startsAfterDeadline`;
not an overdue task dropped on Today) asks "Move the deadline too?" (`#task-deadline-modal`): Move deadline sets start and due to that day,
with an undo toast restoring both;
Cancel writes nothing (A start after the deadline).
An empty section is only its dashed header row (min 1.9rem, 2.5rem on coarse pointers), which takes the drop; nothing grows when a drag starts.

Day headers show a count and turn amber at the limit.
Each day's header also says how many tasks are **due** that day, wherever their cards sit (`· 2 due`, muted, `.board-day-due`; `boardBuckets` gives each day its `due` list): the board places cards by start date, so without it a deadline would be invisible until its day.
A click (or tap) on it, or on Unplanned's overdue count, lists those tasks in a menu (a bottom sheet on phones; `headerTasksMenu`), and picking one selects its card; the tooltip names them too.

The board is Open grouped by day: every section is in the manual order and labelled Unsorted/Sorted where it needs to be, and the List is what's left of Open: ranking across days, and every new task in one Unsorted list.

#### Folding

A section with cards folds from its header: a click anywhere on the header (`.board-day--foldable`, `data-fold`;
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

#### Order on the board

In Manual (see Sorting) inside every section tasks are in Open's order (`TasksCore.byOpenOrder`, shared with Open), and they can be reordered there, which edits that one order (there is no Plan order): plan tomorrow in the evening and put it in order on the spot.
A card dropped on a card of a day or Later (`data-ordered` on the header) lands above or below it by the pointer, a line showing where (`dropSide`, `.task-drop-before`/`-after`, mouse and touch);
on another day it also starts the task there (`planOnDay`, so a deadline it passes still asks), start date and place in one move.
Dropped on a Later card from elsewhere, it asks for the date as on Later's heading and lands where Open's order puts it.
Ctrl+Shift+↑/↓ moves the selection one place within its section, never out of it (`planKeyMove`), crossing the Unsorted/Sorted labels as on Open (Unsorted and Sorted labels, below).
As on Today, only the moved tasks change place in the full order and hidden tasks keep theirs (`TasksCore.placeInOrder`).

#### Unsorted and Sorted labels

A section that holds both, Unsorted tasks (listed first) and Sorted ones, labels them **Unsorted** and **Sorted** (`.plan-subdivider`), as Open's sections do one level down;
a section without Unsorted tasks has no labels, and neither has one with nothing else, so most sections show none, and the labels appear where something is still to be ranked among what is (often Today: a task added on Today starts today, unsorted).
Lighter than the day headings on purpose: as headings, "Sorted" would read as another day.

Moves follow Open's rules, through Open's own code: `planSectionList` gives the section as Open lists its sections, `[#unsorted, …Unsorted ids, #sorted, …Sorted ids]`, and `applySectionList` / `sectionKeyMove` do the rest.
So a task dropped among the Unsorted ones leaves the order (Unsorted is newest first, nothing moves within it), one dropped among the Sorted ones takes that place, and no other task is ranked as a side effect;
Ctrl+Shift+↑ on the first Sorted task takes it into Unsorted, ↓ on an Unsorted one to the top of Sorted.
The **Sorted** label takes drops (`data-sub="sorted"`, mouse and touch): first among the sorted, as Open's Sorted heading; the Unsorted label doesn't (a drop on it is a drop on its section).

#### Unplanned

Unplanned is the backlog, ranked while planning: it reorders like a day.
A card from elsewhere dropped on an Unplanned card unplans it (as on the heading) and lands where Open's order puts it.
Its header still counts the overdue tasks, most overdue first (Due date sorts the whole board by deadline).

#### Carried over

Carried over has no section: those tasks are on the Today tab already (a past start means available), so they are today's load, and the board's Today section holds them, in Open's order with the rest, so Today reads as Today does and its count and limit include them.
A slipped plan stays visible without a section of its own: on Plan a carried-over card's "since Mon" badge is amber (`.task-carried`),
and Today's header counts them,
amber,
before its due count (`· 3 carried`, `.board-day-carried`; `boardBuckets` gives Today its `carried` list, the longest-slipping first; a click lists them, `headerTasksMenu`, shown even folded).
Committing to one again is optional: dropping it on Today's heading (its own section, but `dropOnBucket` starts it today: `recommit`) or swiping it right starts it today, which takes the badge away.

### By person and By list

By person and By list are the board with another key (`TasksCore.personBuckets`, `listBuckets`; `boardBuckets` in `tasks.js` picks by `st.cut`): the same sections with counts, folding, Unsorted and Sorted labels, the manual order inside a section, Order's sorts and the 200-card window; a section's header says how many of its tasks are overdue, in red, with their list on a click.
What is the day's alone stays the day's: the WIP limit's flag, the due and carried counts, Later's date picker.
**1**–**7** and **0** plan the selected tasks there as on every tab; dragging onto a day needs Plan.

On Today a section holds the tasks of the day: By person is who does what today, a team's morning; By list what each list has on.
`[[tasks: view=today group=person]]` and `/tasks?view=today&group=person` open it; a view's `group=day` is one list there.

#### Sections by person

**Unassigned** (what no one has, a former assignee's included: `assigneeIs` `none`) and **You** (the tasks assigned to you, and your private ones) are always there, as where a task is let go and taken; then a section for each other user who has tasks in the view, `@name`.
A card dropped on a section's header is that user's (`dropWrite` → `assignTasks`, the function the Assign… dialog uses: one `batch-update`, a toast "1 task assigned to bob" with an undo that leaves no trace; the mail goes as for any assignment); on Unassigned, no one's.
Dropped on a card there, it is assigned and takes that place in the manual order, as a drop on another day's card starts the task there.
A private task has no assignee: it stays under You, and another section doesn't take it ("A private task is yours alone: move it to a shared list to assign it").
A card there doesn't repeat who has it (a former assignee's name still shows, struck through).

#### Sections by list

The **Inbox** is always there, first; a list without tasks in the view has no section (**m** moves a task to any list).
A drop moves the task (`moveTasks`, the function Move to list… uses, with its undo).
A private task dropped on a shared list asks first ("Share this task?", `#task-share-modal`, `confirmShare`: everyone will see it, and it can't be made private again), and is then assigned to you, as the move dialog does it;
a shared task is never taken by a private list or, with users, the Inbox ("A shared task stays shared": the section doesn't light up, `refusesDrop`);
a task leaving a list for some for one more people see asks the same (Moving a task to fewer or more people).
A card there has no list chip.

### Today

**Today** (the tab; `focus` in the code) is what you're working on now (`TasksCore.isFocus`: `start_date <= today`, or due today/overdue; on the board: Today, its carried-over tasks included, and overdue tasks wherever their start date puts them).
It is Open filtered: the same sections (Unsorted · Sorted) and the same order (`byOpenOrder`);
its section headings look the same as the Open list's: the light labels of the Plan board's Unsorted/Sorted (`sectionHTML`: `.plan-subdivider.list-section`, see Sections), so there is one manual order, and ordering on Today orders Open.
That fits a shopping list: Open is the shop's order, Today today's list, and an item added on Today (Unsorted) is dragged into place among today's items, which places it in the shop's order (see Sections).
Due and overdue tasks have no section of their own: they keep their place in the order, marked by their deadline colour.
Today has no order of its own besides the start date: a start date is the one commitment.

The Tasks links (with the due count) open here unless another tab was used this session.
With a limit, a warning shows when Today reaches it.
Empty Today says "Nothing planned for today" and links to Plan (the Open tab by day, `data-goto-tab`).
New tasks created here get today as start date.

#### Updates

**Updates** (My tasks' Today only; `.updates-heading`, cards `.task-update`; `inViewOrder` in `js/taskrender.js`, `tasksInView` given the user) lists above Unsorted **the tasks with news for you** (`TasksCore.newsFor`: News on your tasks), **whatever their dates**, each card saying what the news is.
A task given to you often has no date, and would otherwise be on no tab the Tasks links open, while their count says it is there.

Among them are tasks that aren't in My tasks otherwise: what you gave away (someone commented, it was done), what mentions you, a task closed this month or last;
the server sends those along with My tasks (`withUpdates`: `GET /api/tasks?mine=1`, and the tasks a page of My tasks brings), the list holds them for Updates alone (`t.extra`) and no other tab shows them.
A task there is in neither section below, nothing is dragged into Updates and nothing ranks there (`sectionList` leaves its cards out);
opening the task, or doing something with it, takes it out: one of yours with a start date not after today is in Today below, else on Open.
Updates draws its first seven (Long sections); its heading counts them all, and **Mark all read** in the ⋯ menu sees them all.

On My tasks' Open tab a line says that there are some (`.tasks-updates-note`, `syncUpdatesNote` in `js/taskrender.js`: "2 updates for you →", which opens Today), so the Tasks links never count what the page doesn't show; the tasks themselves stay in their sections there.

### Closed

**Closed** is not a tab but a link under the list (`#task-closed-link`, `Closed (N) →`, count from `/api/tasks/closed-count`, refreshed after every write): it is seldom looked at, and a tab would put it beside what is done every day.
While it shows, the lines above the list that are about its open tasks ("4 unassigned", Given to others, what the view is for) aren't (`.tasks-shows-closed`, `markActiveTab`).
In Closed, the link reads `← Back to <tab>`, naming the tab it returns to.

It holds the tasks that are closed: done, and closed undone (won't do), which is why it isn't called Completed.
That is its name everywhere: where people read it (the link, the view's heading, the settings dialog), in the params (`view=closed`), the routes (`/api/tasks/closed`), the files (`closed.json`), a task's `closed_at` and the code.
A wiki whose files still have the older name is renamed at start (Closed tasks' old name, under Migrations at start); nothing else is kept of that name: a page's `view=completed`, a link with it and a client asking the old routes get what an unknown view or route gets.

While the filter has any word (`TasksCore.filterActive`: text, a tag, a name, `list:`, `from:`, `!`, `is:updated`, `is:following`) or the embed a tag, the link has no number (`Closed →`): the total would read as what the filter leaves, and counting the matches would take every closed task of the year to the browser for a number.
The view's own list, the locked `list:` chip of a list's view, of a page's embed of it or of the Inbox, is no such word: the count is of that list already (`scopeQuery`), so the link has its number (`scopeWord` in `js/tasktabs.js`).
The filter carries over into Closed, so a click shows the closed tasks it matches.

**Closed shows a page, which the server answers** (`GET /api/tasks/closed?limit=`, `Store.ClosedPage`, `loadClosed` in `js/taskdata.js`): of the year's closed tasks of the list's scope that the filter field's words take, the latest closed first, the first 50, and how many there are (`{tasks, total}`).
**Show more (N remaining)** asks for 50 more, a change of the filter asks again (a moment after the last key: `closedStale`), and a link to a closed task (`?task=`) gets that task among them however far down.
The server reads the filter as the browser does (`parseTaskFilter`, `taskFilter.matches` in `go/tasksyntax.go`: every word of Filtering), and looks through the year's tasks without copying them, so it copies and sends the page alone (at most 2,000 at once, `closedPageMax`): a team's year of closed tasks is too much for one answer.
Without `?limit=` the route still answers every closed task of the year the user sees.
`/api/tasks` returns open tasks only, so closed tasks are fetched only when Closed is opened (`fetchClosed`, one request at a time); `tasksGen` refetches if `fetchTasks` replaced the list meanwhile.

Closed's heading has a **year switcher** (`.closed-years`, years from `/api/tasks/closed-years`, shown when there is more than one) for the yearly archives;
`?year=YYYY` opens that year (search links to archived tasks carry it), and going back to a tab returns to this year (`setClosedYear`).

### Sections

The sections of Open and Today (`SECTIONS`, `sectionHTML`, `.list-section[data-section]`) are Unsorted (no `sort_order`, newest first) above Sorted (`sort_order`).
Both tabs head them alike, with the light label a Plan section uses for its Unsorted/Sorted (`.plan-subdivider`), not a day heading, so the labels look the same wherever they appear; Open's sort headings (By deadline, No deadline, Newest first) take the same label.
A heading is also an empty section's drop target, so it is at least 1.6rem tall (2.5rem on coarse pointers).

Moves treat both headings as slots in the visible list (`sectionList`, `applySectionList(list, moved)`): which side of the Sorted heading a task lands on decides its section;
a task dropped on a task lands where the pointer is: above it on its upper half, below it on the lower half, from Unsorted and within Sorted alike, a line on that edge showing it while dragging (`dropSide`, `.task-drop-before`/`-after`, mouse and touch; `sectionDrop`);
dropping on the Sorted heading moves the task to its top;
reordering within Unsorted does nothing;
leaving Sorted sets `sort_order` to `null`.
Ctrl+Shift+↑/↓ moves one slot, headings counting.
Only the moved tasks change place in the full order: each goes right after the visible task now above it (or right before the one below, when first), and every task the view or filter hides keeps its place.
So on Today, sorting in one item never shuffles the catalogue around it.

#### A move is saved as a move

A list tells the server each moved task with the task now before it and the next one after it that didn't move (`TasksCore.orderMoves`, `saveOrder` in `js/taskorder.js`; `POST /api/tasks/reorder` with `moves: [{id, after, before}]`), and the server gives the task a number between those two's as it has them by then (`placeTasks`, `placeBetween` in `go/tasks.go`; `sort_order` is a number, not a count), writing no other task.
So a list that is stale in someone's browser doesn't put its old order back over what a colleague ranked meanwhile; neighbours that are no longer in that order leave `after` to say where, and when the numbers between two tasks are used up the order is numbered anew as it stands.
My tasks' order, which is one user's own, is sent whole (My tasks' own order).

#### A position in the order

A task can also be given its place by naming it: `position` on `POST /api/tasks` and `PUT /api/tasks/<id>` (Updating a task in `docs/spec/routes.md`), and an assistant's `create_task` and `update_task` (`position`, `after`, `before`; `create_task` in `docs/spec/mcp.md`).
`top` puts the task above the first ranked task, `bottom` below the last, `{after: id}` and `{before: id}` right next to that task: the server finds the neighbour on the other side in the order it has, and the task gets the number between the two as a drop does (`movesTo`, then `placeTasks`), in the write's own commit.
Several tasks given one position (an assistant's `update_task` with `ids`) go there one after the other, in the order given, as a drop of several selected tasks.
The task to put it by must be open and ranked, and one the user sees: one they don't is refused in the words for a task that isn't there.
The order is one for all tasks, so the place holds in every view that shows both tasks; My tasks' own order is left as it is, as a drop in a list's view leaves it.

### Long sections

A long section draws its top (`windowed`, `DRAW` = 200 in `js/taskrender.js`): a section of more than 200 cards (Unsorted, Sorted, a sort's group, a Plan section) draws its first 200 and a row **Show N more** (`[data-draw-more]`), which shows that section whole from then on (`st.drawAll`).
**Updates draws its first seven** (`UPDATES_DRAW`), the latest news first, and the same row: it is above what is yours to do today, and in a team someone back from a week away has dozens.
The section's count is of all its tasks, the filter searches all of them, and a task that has to be seen (a `?task=` link's, a new one: `unfoldFor`, `st.mustDraw`) shows its section whole.
All tasks of a large wiki is thousands of cards, and a list is read from its top.

### No hiding

Every open task is always reachable from its own tasks (Open or Plan) and from search.
Dates decide where a task appears, never whether.

## Settings

**List settings…**, the first item of the list's **⋯ menu** (on a wiki page the ⋯ of the embed's toolbar, `#task-more-btn`; on the Tasks page the page's own ⋯ beside the heading, which holds the list's actions too: The Tasks page's ⋯ menu), opens **List settings** (`#task-settings-modal`, `js/tasksettings.js`): what the list is and how it opens.
A setting is kept where it belongs: what the tasks shown are with them (a list's own with the list, My tasks' with you, All tasks' for everyone), how a page looks at them in that page's `[[tasks: …]]`.

| Field | Setting | Whose | |
|-------|---------|-------|---|
| Opens on | `view` | the page's embed; the list's on `/tasks?list=` | The Open or the Today tab (by their names here); Closed is offered only where the page says `view=closed` |
| The Open tab shows | `group` | the page's embed; the list's on `/tasks?list=` | One list, Plan (the board by start date), By person or By list: those the view offers (and the one set) |
| WIP limit (on request) | `limit` | the list's | 1–99, empty for none (The WIP limit) |
| Tab names (on request) | `labels` | the list's | The Open tab's and the Today tab's; empty keeps Open and Today (Tab names) |
| Tags of a new task (on request) | `new` | the list's | Comma-separated, in place of the filter's single tags; empty: the single tags the view shows |
| Keep tasks (on request) | `keep` | the list's | New tasks there are keep tasks: the edit modal's box starts ticked (Keep tasks) |
| Hide when sharing (on request) | `noshare` | the list's | The list is left out of the list switcher in sharing mode |
| Leave out of My tasks (on request) | `home` | the list's | `off`: its tasks are not in My tasks nor in the Tasks links' count (Leave out of My tasks). Offered on a list that is yours: a private one, without users any |
| Shows only the tags (on request: Filter by tag) | `tag` | the page's embed | The filter: `a,b` both, `a\|b` either, `-a` without |
| Shows only the tasks of (on request: Filter by person; with users) | `assignee` | the page's embed | `me` whoever looks, a user's name, `a\|b` either, `none` no one's, `-a` not theirs |

### What each view offers

| View | Fields | For whom |
|------|--------|----------|
| A list (`/tasks?list=`, or a page that shows it) | all but the filter on `/tasks?list=`; on a page the tab, the grouping and the filter are the page's (the list's own fields only for who manages it) | everyone who sees the list, or only you for a private list |
| An embed of no list, of My tasks or of the Inbox (`[[tasks: tag=work]]`) | Opens on, The Open tab shows and the filter, written in its `[[tasks: …]]`; the rest is All tasks' (My tasks' for `mine=1`), set there | everyone who sees the page |
| All tasks (`/tasks`) | Opens on, The Open tab shows, WIP limit, Tab names, Tags of a new task | everyone |
| My tasks (`/tasks?mine=1`) | Opens on, The Open tab shows, WIP limit, Tab names, and **Mail me when** | you alone |

The dialog shows what is changed most: Opens on and The Open tab shows.
The other fields show only when set as the dialog opens (`[data-field]`, `showField` in `settingsForm`);
for the rest a line of quiet words under the fields offers each, "+ WIP limit", "+ Tab names", "+ Tags of a new task", "+ Filter by tag", "+ Filter by person", "+ Keep tasks", "+ Hide when sharing", "+ Leave out of My tasks" (`.task-add-fields`, as in the task dialog): a click shows the field and puts the cursor in it, and ticks a box (Keep tasks, Hide when sharing, Leave out of My tasks), since asking for it is asking for that.
A list is pinned and unpinned in the Tasks page's ⋯ menu, where this dialog is opened too, not here (The Tasks page's ⋯ menu).
The line above Save says whose the settings are: the list's, for everyone (or "which only you see" for a private list), or the page's.

### The WIP limit

`limit` is a work-in-progress limit: Today warns at this many tasks ("⚠ 6 tasks in Today (limit: 5)"), and Plan day sections with at least this many are flagged amber, with a ⚠ titled "Over the limit (6/5)" (`.board-day--over`).
A number outside 1–99 is taken as the nearest of them.

### Tab names

`labels` gives the view its own names for the tabs, in on-screen order, e.g. `Catalogue,Shopping` for a shopping list.
Only the words change: views, `view=` values and links keep their names.
An empty entry keeps the default, `_` stands for a space (params split on whitespace), and a name is cut to 30 characters.
They are rendered into the tab buttons by `buildTasksEmbed`; `tasks.js` reads them back (`tabLabel`) for the "← Back to …" link, the limit warning and the ⟲ tooltip.

### Not which list it shows

`list=` isn't in this dialog (it is in the editor's, where the dialog writes the embed: `.Editor` in `task_settings_dialog`).
Another name there would show another list, which reads like a rename and isn't one; a list is renamed by **Rename…** in the ⋯ menu of `/tasks?list=<name>` and made by **New list…** there.

### Where there are no settings

No List settings…: the editor's preview, an old version, a page from the uncommitted list (only a page view renders its own file: `RenderOpts.EditSlug`) unless it shows a list you may change, a name no list has, the headless app, a viewer, sharing mode.
Of those, a viewer's list on `/tasks` or on a page view keeps its ⋯ menu, with Export tasks (Export and import); the preview, an old version, the headless app and sharing mode have none.

### Where settings are kept

The app says where its settings go in `data-settings-*` on `.tasks-app`, from `buildTasksEmbed`: `-page`, `-index`, `-raw` and `-embed` for an embed's `[[tasks: …]]` on a page, as written there; `-list` and `-list-set` for the list shown: its ref and its own settings as JSON; `-mine` for My tasks, `-home` where Leave out of My tasks is offered, `-mail` where Zone could mail you.

| What | Kept in | Example |
|------|---------|---------|
| A list's own settings | the registry (The registry), wherever the dialog was opened: on `/tasks?list=<name>` or on a page that shows the list; they hold wherever the list shows | `{"name": "shop", "limit": "8"}` |
| An embed on a page | that `[[tasks: …]]`: the tab, the grouping and the filter, and nothing else | `[[tasks: tag=work view=open]]` |
| All tasks (`/tasks`) | the shared registry's `all` (`allTasksParams`), for everyone; no pin and no filter (`allSettingKeys`) | `"all": {"view": "open", "limit": "5"}` |
| My tasks (`/tasks?mine=1`) | your registry's `mine` (`mineParams`, `setMineParams`, `mineSettingKeys`; without users the shared registry's), for you alone; no pin and no filter | `"mine": {"view": "today", "limit": "5"}` |

My tasks' dialog says "For My tasks: yours alone." (without users "For My tasks.")
Unset, it opens on Today, with Plan on the Open tab and no limit.
`"mail"` in `mine` names the kinds of mail you turned on (**Mail me when**: a task is given to me, someone mentions me, someone comments on a task I have, gave or follow; three boxes, none ticked until you tick it, shown where Zone could mail you: `data-settings-mail`; Mail, under News on your tasks).

### Saving settings

The dialog sends only what was changed (`changedParams`) to `POST /api/tasks/settings` (`tasksSettingsRoute`), to each place its own:

| Body | Writes |
|------|--------|
| `{list, set}` | a list's settings (`setListSettingsOp`) |
| `{page, index, was, set}` | an embed on a page |
| `{all: true, was, set}` | All tasks |
| `{mine: true, was, set}` | My tasks |

`set` maps a setting to its new value, `""` taking it out; `was` is an embed's params as the dialog saw them.
So saving without a change writes nothing, and a param the dialog doesn't know stays as written.
The server checks each value (`checkEmbedSettings`: one word without `[`, `]` or a backtick; `view` a view, `group` one of `none`, `day`, `person`, `list`, `limit` 1–99, `new` cleaned like tags, `keep` and `noshare` a yes, `home` `off`, `assignee` words of the filter, `mail` the kinds of mail, and only where a task can be given to you; a list takes only its own settings, `listSettingKeys`, an embed only an embed's, `embedSettingKeys`: `list` and `pin` are refused).

An embed's params are rewritten in place (`setEmbedParams`, `setEmbedArgs`): a param keeps its place,
a new one goes last,
one written twice is written once;
the rest of the page is untouched.
Opens on is written as `view=` (`today`, `open`), and what the Open tab shows as `group=`: each when it was changed or was written already (`changedParams`); on a page both are the page's, on `/tasks?list=` the list's.

A page is changed in one commit, "Change the task list's settings on <slug>" (`changePageOp`: what it held uncommitted is committed first), a list in "Change the settings of list #<name>", All tasks in "Change the settings of All tasks", My tasks in "Change the settings of My tasks".
When the `[[tasks: …]]` is no longer as the dialog saw it (the page was edited meanwhile), nothing is written: 409, "The page has changed since.
Reload it and try again."
Other refusals: 400 with the reason, 403 for a viewer, 404 for a page or a list you can't see; the dialog shows the message.
After a save the page reloads, and what the browser tab remembered (the active tab, a choice of the Open tab's control) is forgotten, so the list opens as just set (`forgetSession`).

## Export and import

A list's tasks can be saved as a file, to keep a copy, move a list to another wiki or hand it to someone.
**Export tasks** and **Import tasks…** are in the list's **⋯ menu** in the toolbar, under List settings… (`#task-more-btn`, `.tasks-more-btn`; the cards' menu, `showMenu` in `js/taskmenu.js`, a bottom sheet on phones; the code in `js/tasksettings.js`).
The ⋯ menu is there for whoever sees the tasks, not only where a list has settings (`Menu` in `buildTasksEmbed`): not in sharing mode, nor in the headless app or for a name no list has.
**Export tasks** is in it for whoever sees the tasks, a viewer and a list's member who doesn't manage it too (the route answers what they see);
**Import tasks…** (and Mark all read) for who may write the tasks (`data-writes`: not a viewer), as the route asks: `mayWriteTasks`, and `canWriteList` for the list imported into.

### Export tasks

**Export tasks** (`GET /api/tasks/export?list=&tag=&assignee=`, `?mine=1` for My tasks, `?lists=` for several; `apiExport`) downloads `zone-tasks-<list>-<date>.json` (`all` for All tasks, `my-tasks` for My tasks;
the name goes into `Content-Disposition` quoted and escaped, `mime.FormatMediaType`, as the list's name is the request's): the tasks the list shows (its `list=` and `tag=`, not what is typed in the search field),
open and closed of every year,
each as the API shows it,
with its description,
dates,
recurrence,
comments and history: `{"zone_tasks": 1, "list": "shop", "tag": "", "exported_at": "…", "tasks": […]}`; each task says the list it is in (`list`).
With users it holds the shared tasks and your own private ones, marked `"private": true`: what you see, read as any view reads them (`Store.ReadAllStrict`, which holds up no task write while it reads; a file that can't be read is a 503, never an export without its tasks).

### Import tasks

**Import tasks…** (`POST /api/tasks/import`, `apiImport`): pick such a file, and a dialog (`#task-import-modal`) says what it holds ("Import 12 tasks from “shop.json”") and asks for a tag of your own; **Import** adds its tasks to the list the menu was opened in, Cancel nothing.
A file that holds no list of tasks is refused at once, in a notice; one with no tasks says "Nothing imported: the file has no tasks.", and one whose tasks are all here already "Nothing imported: the tasks are already here."
A viewer can't import (403).

The notice says what happened ("Imported 12 tasks; 3 already here.") and has **Undo** (also Ctrl+Z, for the few seconds it shows; `showUndo`): the tasks just added are deleted again (`/api/tasks/batch-delete` with the `ids` the import answered).
One commit, "Import N tasks".

A file that isn't an export (no `tasks` list, or a task without a title) is refused whole (the server's reason shows in the dialog), as is one with more than 10,000 tasks (`importMax`); a bare JSON list of tasks is taken too, so a file written by hand or by a script needs only `[{"title": "…"}]`.
Closed tasks arrive closed.

#### Where imported tasks go

They go into the list the menu was opened in, whatever list they were exported from, so a `shop` export imported into `groceries` shows up there, and they get the tags a new task there would (`new`, or the single tags the view shows); their own tags stay.
On All tasks (and a tag's embed) each goes where its own list says: the list of that name here, when there is one as private as the task was; else a private task into your Inbox, and a shared one, with users, into the shared list `general` (made for it; without users into the Inbox).
Imported on the Inbox's view, every task goes into the Inbox: with users yours alone, without an assignee.
A task exported private stays private, wherever it is imported: into a shared list it goes to your Inbox instead; into a private list of yours every imported task is private, as a new one is.
With users, a task assigned to someone who can't be given tasks here comes in as no one's, and the toast says how many ("…; 2 were assigned to people who can't be given tasks here": not users of this wiki, or disabled or viewers, or none of those a list for some it goes into is for).

#### A task keeps its id

One whose id is in the wiki already is skipped: importing the same file twice adds nothing, and a file exported here brings back only what was deleted since (an id that isn't a UUID is replaced).
It keeps the occurrence before it (`spawned_from`) only when it comes back as Zone had it (a task deleted since; an admin's import keeps the file's), else a `[[task:…]]` of a closed task would stand for it (Names in comments and history).
So a list can't be duplicated within one wiki this way.

#### A tag of your own

The dialog's field (optional; `also`) gives the imported tasks a tag too, on top of the list's, to find the batch again later (`from-anna`); cleaned like any tag, several comma-separated.

#### Names in comments and history

With users (`checkArrived` in `go/taskby.go`) a non-admin's import keeps their own name on a comment or a history entry and blanks everyone else's (by no one), as the file could say anything, unless Zone has seen that comment, with that text, or that entry under that name on that task since it started, and the task comes back as Zone had it (a task deleted and imported back, its title and description as they were); an admin's keeps the file's names (a wiki moved, a backup put back; Who made a change in `docs/spec/users.md`).

## Interaction

How a task list is worked with: its header, the filter, opening and editing a task, the quick actions on a card, selecting several, dragging, and the cards themselves.

### The header

Tabs, filter and **+ New** sit on one line on desktop (`.tasks-toolbar` `flex: 1 1 20rem`).

On a phone (≤640px) the tabs share their line with a search button, ⋯ and a label-less "+" (accessible name "New task"); the line under it holds what the tab adds (how it shows its tasks, the Order).
The filter field is behind the search button (`.tasks-search-btn`, `showFilter` in `js/tasktabs.js`): it shows once asked for (the button, or `/`, `#`, `@`), is put away when left empty, and stays for as long as it filters (`.tasks-filtered`), so what narrows the list is never out of sight.
Where nothing is grouped or filtered (Today, most days) the list starts right under the tabs.
A page's locked words (a list's name, an embed's `tag=`) are in the field, not on that line: the page's heading says what it shows.

### Filtering

The **filter field** (`#task-filter-search`, `TasksCore.parseFilter`/`matchesFilter`) narrows the list to what its words say:

| Word | Shows |
|------|-------|
| text | the tasks with it in their title or description |
| `#word` | the tasks with that tag; several `#words` must all match |
| `#a\|b` | the tasks with either tag |
| `-#word` | the tasks without that tag (`-#a\|b`: with neither) |
| `list:shop` | the tasks in that list (`list:a\|b` either); a list is no tag, so `#shop` doesn't find it |
| `-list:shop` | the tasks not in it |
| `list:inbox` | the tasks in no list, the Inbox, as `list:none` says too |
| `!high` (or `!`), `!low` | the tasks of that importance |
| `is:updated` | the tasks with news for you |
| `is:following` | the tasks you follow (Following a task) |
| `from:anna` | with users: the tasks anna made (`created_by`); `from:me` your own, `from:a\|b` either's, `-from:anna` not hers; a private task is its user's own making |
| `@anna` | with users: the tasks assigned to anna; `@anna\|bob` either's, `-@anna` not hers, `@me` yours, `@none` no one's (Assigning tasks in `docs/spec/users.md`) |

`/` focuses it and `#` focuses it and starts a tag; with users `@` focuses the field and starts a name, and typing one suggests `me`, `none` and who has tasks here.
Esc leaves the field.
Its tooltip lists the syntax (with users `is:updated`, `is:following` and the `@` words too).
While it holds something of yours (text, or a chip the page didn't set) an **×** at its end (`.chip-input-clear`, "Clear the filter") empties it: the text and your chips go, a page's locked chips stay, and the cursor stays in the field.

Typing a `#word` (or `-#word`) suggests tags of the tasks the other tag words leave visible (free text is ignored for this).
Embed `tag=`/`?tag=` become a query via `TasksCore.tagParamToQuery`, and `/tasks?from=me` arrives as that word.
New tasks get the filter's single tags.

#### Tag chips

In the filter field and the edit modal's Tags field a completed tag becomes a chip in front of the typed text (`chipField` in `js/taskchips.js`; `.chip-input` box wrapping the input, `.tag-chip`).
A chip can't be edited, only removed: Backspace at the start of the field removes the last one, its × any.
Filter chips are whole filter words (`#a`, `-#b`, `#a|b`); the query is the chips plus the text (`filterQuery()`), and `?tag=`/`[[tasks: tag=…]]` arrive as chips.

In the Tags field (and the bulk tag dialog's) a completion, a comma or a space (typed or pasted: `#a #b, c` gives three tags; `chipsOnSeparators`) makes a chip, with no separator left behind, so several tags go in at once;
tags are one word each, so a space ends one rather than becoming a `-` (other tag inputs still turn spaces into `-`).
In the Tags field Enter with no suggestion open makes the typed tag a chip instead of saving (Enter on an empty field saves); Save takes the chips plus a half-typed tag (`editTags()`).
The dropdown hangs under the whole box.

#### Tag completion

Tag completion (`attachTagAc`: filter, Tags field, title `#tag` shorthand, bulk tag dialog `t`/`T`) ranks by `TasksCore.rankTags` (exact, prefix, substring, in-order letters; then usage count, then A–Z).
The best match is pre-highlighted so Tab/Enter completes it, ↑/↓ pick another; Esc closes only the dropdown.
In the Tags field and the bulk tag dialog a tag may be typed with or without its `#` (`tagWordOf`): `#wo` and `wo` suggest the same tags, a lone `#` lists the most used, and the chip is the tag without it.
Keys are handled in the capture phase.
The dropdown is appended to the field's parent, since in a `<dialog>` only the top layer is visible.

### The edit dialog

A double-click anywhere on a card opens it (title and description too; only its controls, the checkbox, buttons, links and date chips, keep their own), as do Enter on desktop and Edit… in the menu.
On touch the first tap selects the card (also showing a cut-off title whole, see One line per task) and a tap on the selected card opens it (`pressedSelected`, noted at `pointerdown`, since touch's compat mousedown selects before the click;
pointer type of the last `pointerdown`;
a double tap's `dblclick` is ignored, so it is just those two taps).
It is centred with `inset: 0; margin: auto` and **no transform** (a transform would clip `position: fixed` popups like the date picker), scrollable, with a sticky action bar; at ≤640px full screen.

The fields are Title, Description, Tags, then one line of small boxes that wraps (`.task-props`), each with its name inside, in grey before its value (`.task-prop`, a `<label>`, so a click anywhere in it reaches the field): List (while there are lists), Due, Start and Importance, which every task shows.

| Field | Shows | |
|-------|-------|---|
| Title | always | Quick-add's shorthand at its end, on a new task (Quick-add) |
| Description | always | Markdown, with Edit and Preview (Description) |
| Tags | always | Chips (Tag chips) |
| Assignee | with users: always on a shared task, empty reading "No one"; on a new task still in the Inbox only when set as the dialog opens or asked for ("+ Assignee") | A field of its own after Tags (Assigning tasks in `docs/spec/users.md`). Not offered for a task in a private list (it has none) |
| List | while the wiki has a list you see | One choice (The List field) |
| Due, Start | always | (Dates) |
| Importance | always | High, Normal, Low (Importance) |
| Days before due | only when asked for ("+ Days before due"), never on opening; not offered without a due date | Another way to set Start, which shows the result (Dates) |
| Keep after done | when set as the dialog opens, or asked for ("+ Keep after done", which ticks it) | Keep tasks |
| Repeat | when set as the dialog opens, or asked for ("+ Repeat"); not offered for a keep task | Last, on a line of its own (`.task-prop-group`), so the two fields a chosen repeat adds come beside it and Repeat stays where it was clicked. Without a date it shows, but off, and the line under the boxes says why ("To repeat, a task needs a due or start date.", `#task-edit-rec-note`) |
| Every … ("Every 2 weeks"), From done | only once a repeat is chosen | (Repeating tasks) |

Under the boxes a quiet line offers the ones not shown (`.task-add-fields`: "+ Assignee" with users, on a new task in the Inbox, "+ Repeat", "+ Days before due", "+ Keep after done"): a click shows the field and goes to it (`showField`), and ticks Keep, since asking for the box is asking for that.
A field stays for as long as the dialog is open, also emptied again; a line with nothing left to offer is gone.
Under the dates a hint says when the start is after the deadline (A start after the deadline).
Saving a task into another list removes it from a view of its old list, with a notice that says where it went (When a task leaves the view).

The dialog's last parts are History (History) and Comments (Writing a comment), after every field.

#### The List field

The List field (`#task-edit-list`, `fillLists` in `js/taskmodal.js`) is one choice.
It shows once the wiki has a list you see; a first list is made with New list… on the Tasks page or Move to list….
Its choices are the lists you see, A–Z as the server names them (a private one with 🔒, the shared one of a name you have a private list of as "name (shared)"), first **Inbox** (with 🔒 with users, where it is off for a shared task), and last **New list…**.

**New list…** asks for a name in a small dialog over this one (`.list-new-modal`, `openNewList`; with users **Private: only you see it and its tasks**, off for a shared task and ticked when the task is in a private list now) and makes the list at once (`POST /api/tasks/lists`, pinned as one made in the dialog that moves tasks: The move dialog); the field then names it, and the task goes into it with Save.
A name that won't do is said in that dialog; Cancel or Esc leaves the field as it was.

A new task starts in the list of the view it was made in, else in the Inbox (Where a new task goes); choosing another moves the task.

A task is as private as its list (Private lists): for a shared task your private lists are off.
Under the fields a line says who will see the task when that changes or is private (`syncListNote`): "Private: only you see this task." for a task in a private list or your Inbox (a new task says who sees it under its title instead), "Everyone who uses the wiki will see and change this task. It can’t be made private again." for a private task about to leave for a shared list (Save then sends `share`), and the lines of a task leaving a list for some (Moving a task to fewer or more people).
A shared list for a task of yours also puts your name in Assignee, once (`sharedByEdit`): it stays in your My tasks unless you take the name off.

#### Tab order in the dialog

Tab goes from field to field: Title, Description, the comment field while it is shown (and its Add once something is written), Tags, Assignee when it is shown, List, Due, Start, Importance, the fields shown of Days before due, Keep and the repeat fields, the buttons of the line that offers the others, then Save, Cancel, Won't do.
What isn't a field of the task is no stop (`tabindex="-1"`) and has its own key or the mouse: Edit | Preview (F2), the formatting bar (Ctrl+B/I/K), Write a comment… (Alt+C) and the comment field's Cancel (Esc), a comment's ✎ and × and the links and boxes in comments, History.

#### Several tasks at once

The dialog of a new task can take several titles (`openEditModal` with `titles`: the editor's Make task on several lines, Task references in `docs/spec/rendering.md`): it lists them ("One task per line; the fields below apply to all of them.", `#task-edit-batch`), and Save makes one task per title with the fields shared.

### Quick-add

A new task's title takes trailing shorthand (`TasksCore.parseQuickAdd`), like `Call Bob tomorrow #work due:fri`:

| Word | Sets |
|------|------|
| `today`, `tomorrow`/`tmrw`, a weekday (the next one, never today), `+3`/`+3d`/`+2w`, `YYYY-MM-DD` | Start |
| `due:` + the same | Due |
| `#tag` starting with a letter | A tag; the first that names a list you see puts the task in that list instead (`#shop`: `quickList` in `js/taskmodal.js`; the completion offers the lists with the tags) |
| `@name` of a user who can be assigned, or `@me` | With users, the assignee (anything else stays in the title) |
| `!` (or `!high`), `!low`, as words of their own ("Call Bob !", not "Call Bob!") | Importance |

Only trailing words count, and shorthand that would empty the title is ignored.
`#task-edit-quick` previews the result.

The fields follow the title (`followTitle` in `js/taskmodal.js`): List, Start, Due, Importance and Assignee show what the title's words say as they are typed, and what they showed before once the word is gone, so the dialog never says two things.
A field changed by hand takes its word out of the title (`byHand`, `TasksCore.dropQuickWord`) and keeps what the hand gave it: with "Call Bob tomorrow" typed, picking Friday in Start leaves "Call Bob".
Tags stay words of the title until Save (the line under it names them): the Tags field holds the ones you put there.

### The date picker

The date picker (`openDatePicker`) has a row of shortcuts above the month,
counted from today,
not from the current value: **Today**,
**Tmrw**,
**+2d**,
**Mon** (next Monday),
**+1w** (`QUICK`, `.dp-quick-btn`; short labels so all five fit one row, the accessible name says it in full and the tooltip with the date, e.g. "Next Monday: Mon, Oct 5", in the `locale` setting (Locale); below the month only Clear; the current value marked, ones before the picker's `min` disabled).
They commit like a picked day, so the card's 📅 asks about the deadline as usual.
The month's title, weekday heads and first weekday follow the `locale` setting (Monday without one).
It is positioned after every `draw()` (below the anchor, else above, else inside the window; never past a screen edge).
A press outside only closes it: `pointerdown` in the capture phase, then `swallowNextClick` eats that press's mouse/click events, so a tap can't also hit what's underneath.

### A task's link

**Copy link** in a task's ⋯ menu puts the task's full URL in the clipboard (`taskHref`, as `taskLink` builds it: `/tasks?task=<id>&view=open`, `view=closed` (with `&year=` from an earlier year's archive) for a done task, `&list=` for a task in a list).
Paste it anywhere: chat, mail, a page.
`?task=<id>` selects, scrolls to and flashes (`.task-flash`) that task; a missing task gets a notice.
A newly created task is flashed the same way when the view shows it.

### Two people on one task

The dialog remembers the task as it opened and **Save sends only the fields changed in it** (`changedFields`, `openedData` in `js/taskmodal.js`; the repeat's three together, `share` with the list), so a deadline, a tag or an assignee someone else set meanwhile is not written back as it was.
Nothing changed, nothing is sent.
Fields merge that way; a text can't.
A changed title or description goes with what it started from (`was: {title, description}`; `textUnchanged` in `go/tasks.go`), and when the task's text is another one by now the write is refused whole (409, "Someone changed this task's description while you were editing it. …"): the dialog comes back with what was typed still in it, to copy.
`PUT /api/tasks/<id>` takes `was` from any client; without it a write is as it always was.

### Notices and undo

Rejected writes show the server's error as a toast (`showNotice`).
A toast shown while a modal dialog is open goes into the dialog (`placeToast`), since in `<body>` it would sit under the dialog's top layer and its Undo couldn't be clicked; when the dialog closes the toast moves back to `<body>`.

**One undo at a time** (`showUndo`): only the latest action's undo is live,
from its toast's Undo or Ctrl+Z.
An action that saves first and arms its undo when the server answers (bulk start with **0**–**7**, Done/Reopen on a selection, bulk tags, Won't do on a selection, Move deadline, completing a keep task, deleting a comment, importance) drops the previous undo as it starts saving (`dismissUndo`),
so a quick Ctrl+Z in between does nothing instead of undoing the action before.
An undo leaves no trace in the task's history (History).

### The task's ⋯ menu

Every card has a ⋯ button (`.task-menu-btn`, always shown, three dots drawn as an SVG (`MORE_ICON`) so they sit on the line's centre in any font).
**Right-click** on a card opens the menu too (`openTaskMenu(id, anchor, {x, y})` at the pointer, selecting the card; a link in the card keeps the browser's menu), and from the keyboard the menu key or **Shift+F10** on the selected card.
A popover with ↑/↓/Enter/Esc on desktop (tasks shortcuts suspended while open), a bottom sheet at ≤640px.

| Item | On | Does |
|------|----|------|
| Edit… | every task | The edit dialog |
| Start today, Start tomorrow, Pick start date… | an open task | The start date (A start after the deadline) |
| Unplan | an open task with a start date | Clears it |
| Skip this occurrence | an open recurring task | Won't do and Skip |
| Importance… | an open task | High, Normal, Low in the menu's place, the task's own ticked (Importance) |
| Move to list… | an open task | The move dialog |
| Assign… | with users, an open shared task | Assigning tasks in `docs/spec/users.md` |
| Follow / Unfollow | with users, a shared task | Following a task |
| Won't do | an open task | Won't do and Skip |
| Reopen | a closed task | Opens it again |
| Copy link | every task | A task's link |
| Select several | every task | Selection mode (Touch) |
| Delete for good… | a closed task you may delete | Deleting a task for good |

On a card in a selection of several the menu offers the selection's actions instead (The selection bar).

### Completing a task

The checkbox completes after a cancellable grace period (struck through meanwhile; several ticked in a row each keep their tick and strike while an earlier one leaves the list); Space does the same for the selected tasks.
A recurring task makes its next occurrence (Repeating tasks), a keep task goes back to the pool (Keep tasks).

### Checklists and subtasks

A checklist item in the description (`- [ ] item`, as on a page) is a real checkbox in the card's description.
Ticking it works as on a page: `POST /api/tasks/<id>/toggle-check` with `{ idx }` flips the Nth box (`mdtext.ToggleNthCheckbox`, numbered as `renderNotes` renders them; a description has no frontmatter, so a leading `---` block is text and its boxes count) and records when,
`<!--d:YYYY-MM-DDTHH:MM:SS-->`,
shown as a date chip;
unticking takes the date out.
An undo toast follows ("Checked “…” · Undo", undo is a second toggle); the toggles are chained (`toggleNoteCheck`), so an undo never overtakes the toggle it reverts.
Each toggle is a change of the description like any other (a commit).
The task's own checkbox is `.task-checkbox input`, and a page's own checklist next to a task list is the page's (`js/page.js` leaves the task app's boxes alone), so they never mix.

The checklist items in a task's description are its subtasks (`subtaskCounts` in `js/taskutil.js`, counted as the description renders them: not in code).
A card with subtasks says how many are done, "☑ 1/3" (`.task-subtasks`, in the accent once all are, `.task-subtasks-done`), where a card with another description has 📝: the line hides the description, and a click on it opens them as 📝 does.
Completing a task with a subtask still open asks first ("“Plan trip” has 2 open subtasks.", **Complete anyway** or Cancel; `confirmOpenSubtasks`, `#task-subtasks-modal`): from the task's checkbox and Space (`tickTask`), and once for several selected tasks (Space, the selection bar's Done; `mayComplete`), where Cancel keeps the selection.
Won't do, a task chip's checkbox on a page and an assistant (MCP) don't ask.

### Won't do and Skip

A task is closed, not deleted.
A task that won't be done is closed undone with **Won't do**: it goes to Closed with "✗ won't do", spawns no next occurrence, and can be reopened from there.
Won't do is in the ⋯ menu, in the edit modal (the button at the right of Save and Cancel, an open task's only; what was typed in the modal is dropped), on the **Delete** key and, for several selected tasks, in the selection bar and their menu (`wontDoMany`: one `batch-complete` with `wont_do`, one undo that reopens them; closed tasks in the selection stay as they are).
Cards have no ✗ button.

**Skip** (⏭, recurring only) archives as won't-do and spawns the next occurrence, the first after today (`spawnRecurring(…, skip)`): a daily task three days overdue comes back tomorrow, not as a card for today, so a skipped backlog leaves Today.
Completing catches up to today instead.
Both show an undo toast; reopening a skipped entry removes its spawn (while untouched, as for un-completing).

### Deleting a task for good

Who made a task can delete it for good: **Delete for good…** is in a closed task's ⋯ menu (`mayDelete` in `js/taskmenu.js`), for a task made by mistake, or shared by mistake.
It asks first, has no undo, and takes the task out of its file in a commit ("Delete task"); the wiki's git history keeps what it was, as it keeps a deleted page.

Who may (`mayDeleteTask` in `go/tasks.go`; `DELETE /api/tasks/<id>` and `batch-delete` answer 403 "Only who made a task, or an admin, deletes it" otherwise): without users anyone; with users who made the task (`created_by`), an admin, its user for a private one, and anyone for a task nobody is known to have made (no `created_by`: one from before users, or written by hand).
Others have seen a shared task, so anyone else closes it.
The same routes take back what was just made: the tasks the page editor created (their maker's), and an import's (what a user imported is theirs to delete while Zone runs: `noteBrought`).
An assistant (MCP) deletes no task.

### Setting dates quickly

The ⏰ (due) and 📅 (start) chips on a card open a date picker.
**f** starts the selected tasks today,
**F** tomorrow.
**1**–**7** start them on that day of the Plan board (1 today, 2 tomorrow, … 7 six days ahead; **1** is **f**, **2** is **F**),
**0** unplans them,
on every tab and for several selected tasks at once (`startOn`: one batch with one undo; a task whose deadline the day passes asks "Move the deadline too?" on its own, after the others).
So planning works from the keyboard: walk Unplanned with j/k (Shift to take several) and press a day's number, however far the board puts it.

### Selecting several tasks

A click (mouse) selects one task (`selectedId`, `selectedIds`, `anchorId`);
**Shift+click** or **Shift+↑/↓** (j/k) selects the range from the anchor;
**Ctrl/Cmd+click** adds or drops one task (and becomes the anchor);
**Ctrl/Cmd+A** with a task selected takes every task shown (what the filter leaves; without a selection it stays the page's select all);
**Esc** clears it (an expanded task collapses first).
A re-render keeps the selected tasks still shown.
On several selected tasks **Space** completes (or reopens) them all, **Delete** closes the open ones undone (won't do), **t**/**T** open the bulk tag dialog, and **Ctrl+Shift+↑/↓** moves them.
On a phone a long press, or ⋯ → Select several, starts selection mode (Touch).

### The selection bar

While several tasks are selected, or in selection mode, a bar is fixed at the bottom of the window (`#task-selbar`, `.tasks-selbar`, `syncSelBar`): "N selected" (in selection mode with none "Tap tasks to select"), then:

| Button | Does |
|--------|------|
| **All** | Selects every task shown (`selectAllVisible`; disabled when every task shown is selected) |
| **Tag…** | The bulk tag dialog (Tags on several tasks) |
| **List…** | The move dialog (Moving tasks between lists) |
| **Assign…** | With users: who has them (Assigning tasks in `docs/spec/users.md`) |
| **Today** | Starts them today (`startToday`) |
| **Unplan** | Clears their start dates (`startOn(ids, null)`, as the **0** key); shown while an open selected task has a start date (`anyPlanned`) |
| **Done** | Completes them (`completeMany`, as Space); **Reopen** when every selected task is closed, which also hides Today and Won't do |
| **Won't do** | Closes them undone (`wontDoMany`, as the Delete key) |
| ✕ | Clears the selection |

**Today** is one `batch-update` for the open ones not starting today, one undo putting back each one's start date and history; today is never after a deadline still to come, and starting an overdue task today is the catch-up case, so no deadline question.
**Unplan** is one `batch-update` for the open ones that have one, one undo putting them back.
Toasts move up above the bar.
The **⋯ menu** of a card in the selection (its button, a right-click, which then keeps the selection, or Shift+F10) offers the same for all of them, titled "N tasks" (`runSelectionAction`).
With several task lists on a page only the active one's bar shows (`claimInstance`).
At ≤640px the bar spans the bottom edge in place of the phone tab bar (hidden while it shows): "N selected", All and ✕ on top, the actions as buttons sharing the width under them (on a second row when one doesn't hold them), ≥44px tall; the page end and toasts keep clear of it.

### Tags on several tasks

**t**, or **T** to remove, on the selected tasks (or Tag… in the selection bar) opens the bulk tag dialog (`openBulkTagModal`, `.bulk-tag-modal`).
It lists every tag the selection carries (`TasksCore.selectionTags`, case ignored, most common first),
each a three-state box (`role="checkbox"`, `aria-checked`): on every task ✓,
on some – ("2 of 3", left as it is),
on none (struck through, the tag goes).
A click or tap cycles all → none → some, back to some only where the tag started so.
The field below adds tags as chips (`chipField`; completion from every tag in use, minus those every selected task has; a comma makes a chip).
Opened with T, the field has no chips: it completes to the selection's own tags and turns the completed one off.
Enter or Apply sends only the changed rows plus the typed tags (`TasksCore.bulkTagChange`) in one `batch-tag` request, a half-typed tag included; nothing changed sends nothing.
The toast names the change ("#work added to 3 tasks", "#old removed from 2 tasks", else "Tags changed on N tasks"; counting only tasks whose tags changed), or where tasks went if the change took them out of the view; its Undo puts back each changed task's tags in one `batch-update`.
On touch the field is not focused while there are rows to tick, so the keyboard waits until it is tapped.

### Drag and drop

A card is dragged with the mouse, or on touch after a long press (Long-press drag); where it is dropped says what changes:

| Dropped on | Changes |
|------------|---------|
| Open's and Today's sections | its place in the manual order (Sections) |
| a day of the Plan board | its start date, and within a section its place (The Plan board) |
| a section By person | who has it (Sections by person) |
| a section By list | its list (Sections by list) |

Wiring: `setupBoardDragAndDrop` per card plus list-level handlers attached once.

### Cards

One layout for every card (`taskHTML`, tasks.css "Cards").
The first line: checkbox, title (as wide as it needs, at least 6rem; cut off with …, whole and wrapped on the expanded card), the tags right after it (pills), markers and dates at the right end (`.task-meta` sits in the line), ⋯ last.
**Nothing jumps on hover**: what is always shown (markers, 📝, 💬, dates, ⋯) ends at the right and stays put; what hover adds (⏭, the empty ⏰ 📅, at 60% opacity, full on their own hover) appears left of it (first in `.task-meta-right`, CSS `order`).
A task with a description shows 📝 (`.task-has-notes`) on every card, since its line hides the description.

Under the line of a card whose details are open (One line per task): the description, then, under a small "Comments" heading (`.task-card-comments-label`) that says where the description ends, the latest 3 comments, newest first (`.task-card-comments`; "N earlier…" opens the modal at the comments).
The description is plain text under the title, the task's own; only the comments have bars (Comments on a card), so the bar says which is which.
The open card ends with room before the next task (`.task-details`: more padding and margin below).

**Every card shows every tag**.
The title and the tags share the line (desktop: `.task-meta` is `display: contents`, its tags and dates are grid columns of their own, `tags` and `dates`, with a `1fr` spacer between): each is as wide as it needs when both fit;
short of room, both grow from their minimum (6rem for the title, the widest tag for the tags) by the same amount, so a long title can't push the tags out and many tags can't squeeze the title away.
The tags then wrap onto more lines under the first, which makes a card two (or more) lines tall; the title is cut off with ….
Dates and markers are never clipped: their column is as wide as they are (`white-space: nowrap`, so "📅 Tomorrow" counts as one piece) and stays at the first line's right end.
The first line has one fixed height (`--line`), expanded or not, so expanding a card moves nothing on its line.
Empty date placeholders (⏰ 📅 to set one) show on hover, left of the dates; with a mouse their room, and ⏭'s, is kept while hidden (`visibility`), so hovering a card that is short of room rewraps nothing; touch, which has no hover, sets dates in the modal or from ⋯.
A double-click anywhere on a card but its controls opens the modal (titles are edited there, not in place).

On phones (≤640px) the line is too short for title and dates,
so the title has the first line to itself (beside the checkbox and ⋯, which keeps its finger-sized target without making the line taller),
tags,
markers and dates go to a second line with the card's whole width,
and a card with none of them stays one line;
rows are ≥44px as a whole.
There every tag shows: the tags flow in that line (`display: contents`) and wrap onto more lines rather than drop out, the dates following the last tag at the right end, or on the next line when they don't fit beside it.

#### The planned cue

A planned task's card gets a coloured left edge, the same on every tab for consistency: amber when it is in Today already (start today or earlier), blue when planned later (`task-planned-now`/`-later`).
On Open it tells decided tasks from undecided ones without changing the order.
A due/overdue urgency colour wins.
The 📅 date is coloured too: blue for a later start, amber for today, plain for a past start ("since …", never a warning).

### One line per task

Every card is its first line (title cut off with …, description and comments hidden; 📝 says a line has a description, 💬 N that it has comments).
It is the only way a list shows: there are no full cards, and no switch (`compact=1` in an embed and the **v** key do nothing).
Tags that don't fit beside the title wrap onto a second line rather than drop out (Cards).
Rows stay ≥44px on touch.

#### Expanding a task

Selecting a task (a click, arrows, j/k; `setSelected`) shows its whole title (`expandedId`, `setExpanded`, `.task-expanded`),
wrapped,
when the line cuts it off (`titleCut`),
and nothing more: description and comments stay hidden,
so you can read through tasks and the list stays short;
the previous one closes.
A task whose title fits stays a line (on touch the first tap only selects it).

Its details (the description, the latest 3 comments;
`detailsOpen`, `.task-details`, `toggleDetails`) open only on request: a click or tap on the **expand button** (`.task-expand-btn`, a chevron left of ⋯, turned up while open, `aria-expanded`;
only on lines with a description or comments, though every line keeps its room so the dates end at the same place),
on 📝 or on 💬 (each toggles them; 📝 is a button, finger-sized on touch without making the line taller),
or **→** / **l** on the selected task (one selected, with a description or comments);
**←** / **h** closes them and keeps the whole title.
**Opened, they stay open on the way** (`detailsMode`, `expandSelected`): the next selected task (j/k, a click, a tap) shows its details too, the previous one's closing, until they are closed (the button, ←/h, Esc); a task without any shows its whole title as usual.
An open task's line doesn't move.

**Enter** opens the modal; on touch the first tap selects and expands, a tap on the selected task opens the modal.
**Esc** collapses it, details and all, and ends `detailsMode` (the next Esc clears the selection), until another task is selected; several selected or none close it too.
It survives re-renders (`taskHTML` adds the classes); switching tabs resets it (`collapseAll`).
Double-click still opens the modal directly.

#### Nothing moves under a click

A mouse press on another card leaves the expanded one open (above or below it: collapsing one above would pull the pressed card up under the pointer;
one below closes the same way, for one behaviour) for the double-click window (`COLLAPSE_DELAY_MS`, 400ms;
`collapseLater`) and then collapses it with a short height animation (none with reduced motion),
so a double-click's second click still lands on the card the first one selected.
Each press of it selects that card again, which doesn't restart the wait.
A pressed card is also not scrolled into view when it expands (`pressedCard`; it is in view already, and scrolling would move it from under the pointer).

### Look

Tasks have their own tones, set on `.tasks-app` (so a task card elsewhere keeps the page's): `--border` a step darker than the wiki's, which every line in it uses (cards, section headers, labels, fields), so cards and sections stand out;
cards plain white on the warm paper of the page and on the white sheet,
where a firmer border and the shadow set them off (`--card-bg`;
in dark mode a step lighter than the page;
the urgency tints are mixed with it, so a tinted card is never lighter than a plain one;
not a hue: the urgency tints use red, orange, yellow and green, and a green, warm or blue tint on the cards swallowed them) with a soft resting shadow (`--card-shadow`;
hover still lifts it, `--shadow`);
Closed's heading a shade deeper than the cards (`--section-bg`);
the day headers are lighter, a heading on a line rather than a filled bar, an empty day a dashed box (its drop target).

A card's coloured left edge (3px; The planned cue) never shifts its content: a card without one makes up the 2px in padding, so checkboxes and titles line up on every card and every tab.
Titles are bold and tags plain muted words (the list's chip has its tint and icon), so the cards' urgency colours stand out.
Unsorted cards are hollow,
on Open,
Today and in a Plan section with Unsorted/Sorted labels alike (`.task-inbox`; not in a sorted view, which has no Unsorted section): a dashed frame in a firmer grey than a card's border,
no fill and no shadow (they read as not placed yet; a grey fill took the code blocks' beige),
urgency colours their tint.

Dates as text take readable shades (`--amber-text` for a start today and the carried-over badge and count: `#d97706`, the Today limit warning's amber, in both modes;
bold, since in light mode it is about 3:1;
`--blue-text` for a later start, a slate blue in Zone's palette (the note callout's), `--green-text` for a deadline today): the bright colours of the cards' left edges are only about 2:1 on a card;
the blue and green are at least 4.5:1, a darker shade in light mode and a brighter one in dark.
Each with a dark-mode value.

## Title links

A title can carry links: `[[wikilinks]]`, `[label](url)` and bare URLs show as links on the card (`linkifyTitle`; one to another site opens in a new tab, `class="task-link"`; a link to anything but the web, mail or the wiki, e.g. `javascript:`, stays text).
Pasting a URL into the title asks for its name, as in the description and the editor (`pasteUrlAsLink`, the same dialog): a name makes `[name](url)`, none (or Cancel) the bare URL; pasted over selected text, the text becomes the label.
`[[` and `[text](` complete pages there too, and `:name` emoji (Emoji in `docs/spec/editor.md`).
Where a title is plain text (the ⋯ menu, toasts, the subtasks and deadline dialogs: `plainTitle`; search results, task chips, Linked tasks: `mdtext.PlainTaskTitle`), links are reduced to their labels.

## Description

A task's description is markdown, edited and rendered as a page's text.

### How a description renders

The description renders with the page renderer (`renderNotes`, `POST /api/render-notes`), so headings, lists, checklists, tables, code (highlighted), quotes, callouts, images, wikilinks, `[[task:…]]` chips and the dates a ticked checkbox records look as on a page; lines in a row make one paragraph, as on a page.
Not what only a page has: frontmatter, `[[tasks:]]` embeds, ✎ per section; headings get no ids (they would clash with the page's anchors).

In the card the description takes a card's scale (`.task-desc.page-content`: headings the size of the text, tighter spacing, wide tables and code scroll, bullets and checklist boxes in one column a box wide (`--mark`), both centred in it and on the lowercase letters of the first line;
the bullets are drawn there (`li::before`), since a list marker's place depends on the font;
text starts at the same place with a box or a bullet, and the first box at the description's edge);
an image opens full size.

Comments render the same way, in the cards and the modal (`.task-comment-body`, at the description's scale),
but keep their lines: a line's end is a line break (`Env.LineBreaks`), so a comment typed on three lines shows on three.
In the modal a line separates each comment from the one before it; on a card each comment hangs on a bar of its own down its left (`border-left` of `.task-card-comment`), with a gap before the next, so the bar's end shows where a comment ends.

### Editing a description

The edit modal's Description field is as tall as its text (`fitNotes` in `js/taskmodal.js`): two lines when empty, growing as the text does up to 45% of the window's height, where it scrolls.
It has the page editor's markdown helpers (`markdownEditor` in `js/mdedit.js`): its formatting bar (`#task-edit-format`, without Make task, which is the editor's; shown only while the field has the focus, in the place of the "Description" label on the line above the field, which is as tall as the bar, so nothing moves; in a narrow dialog (≤640px) or under a finger on a line of its own under the label, where it scrolls sideways),
Ctrl+B/I/K,
Ctrl+Shift+X,
`[[` / `[[task:` / `[text](` completion, snippets (`/date` + Tab), list continuation on Enter, Enter on a selection makes it a wikilink, file paste, drop and attach (an upload as in the editor, kept by the task: A task's files), URL paste.
Tab moves on to the next field unless it expands a snippet (the editor's Tab indents; a modal needs Tab between its fields).
The modal's Save and Cancel stay: nothing is saved while typing.

### Edit and Preview

Two buttons at the right of the line above the field, or **F2** in the modal (`setNotesPreview`; not shown for an empty description that isn't being written in, which has nothing to preview), switch between the field and a preview.
Preview shows the description as the card will (`/api/render-notes`) in place of the field, no shorter than it and as tall as what it shows, up to the field's 45% of the window, where it scrolls.
It is tinted, outlined and labelled PREVIEW at its top (stays while it scrolls), so it is never taken for the field.
Ticking a box in it ticks the item in the text and records when, as on the card (`toggleCheckboxText` in `js/mdtext.js`, mirroring `mdtext.ToggleNthCheckbox`; local time), so the preview shows the date chip; unticking takes the date out.
The hidden field can't take the browser's undo; unticking undoes it.
Edit goes back to the field, focused.
The modal always opens in Edit.

### Where the HTML comes from

The task app asks the server for the texts it hasn't rendered yet, in one request (`renderTexts`, `renderedHtml` in `js/tasktexts.js`; `{ notes: [...], comments: [...] }`), and keeps the HTML per text for the page's life, shared by every task list app on it.

A long list renders a text when it is opened (over 150 tasks: `LAZY_FROM` in `js/taskdata.js`, `lazyTextsFrom` in `go/tasksembed.go`): the page brings such a list's tasks without their texts' HTML, and the list doesn't ask for all of them; a card's description and comments have their places empty and marked (`data-lazy`, not shown), and opening its details fetches that task's texts and draws the list again (`setExpanded` in `js/taskview.js`).
So no card shows markdown as plain text, and All tasks of a large wiki doesn't render and carry every description to show a list of titles.

Loading tasks (`fetchTasks`, Closed) and each write wait for it before drawing, so cards never flash plain text (the tasks a page brings along come with their HTML: A page brings its tasks along); a text that arrives some other way shows as plain text until its HTML is there (`htmlOf`), and a failed request caches nothing.
Code is highlighted once, when the HTML arrives.

## History

The server logs what happens to a task in its history (`logEvent` in `go/tasklogic.go`; the task's `history`, oldest first).
Only these events; title, tag or order edits are not logged.

| Event | Logged when | `by` (with users) | `from` / `to` |
|-------|-------------|-------------------|---------------|
| `created` | a new task; not a series' next occurrence (below); its first dates and first assignee are part of it, not changes | who made it | |
| `start`, `due` | the date actually changes (`applyTaskFields`) | who changed it | the dates, `null` none |
| `done` | the task is completed, a keep task too (whose cleared start date and deadline are part of it: `keepDone`) | who completed it | |
| `reopened` | a closed task is reopened | who reopened it | |
| `wontdo` | the task is closed undone: Won't do, the Delete key, a list deleted with its open tasks closed | who closed it | |
| `skipped` | an occurrence of a series is skipped (⏭) | who skipped it | |
| `repeat` | on a series' next occurrence, as the one before it is completed or skipped ("Repeats: next Oct 3") | whoever completed (or skipped) the occurrence before it | `to`: the next occurrence's date (its due date, else its start) |
| `assigned` | with users, the assignee changed (`applyAssignee`), also when a task you share is given to you (`sharedBy`) | who assigned it | the users, `null` no one |
| `mentioned` | with users, a comment or the description names a user with `@name`, on a shared task: a name the text didn't have before, not oneself, only one who sees the task's list (`logMentions`; Mentions) | who wrote the text | `to`: who was named |
| `shared` | with users, a private task made shared: moved out of a private list, or its list made shared (`sharedBy`) | whoever did it | |
| `private` | never now: older tasks may show it from when tasks could move the other way | | |
| `seen` | with users, a user opened the task while it had news for them: one a user, the latest; kept beside the tasks, in `.zone/seen/`, not in the task's file, and answered as part of the history (What you have seen) | who looked | `to`: the last comment the look saw (a comment's id, `-` for a task without one) |

(The events with users: Assigning tasks in `docs/spec/users.md`.)

The edit modal shows it in a closed **History** section (newest first, with a count; with users the time reads "12 Mar 09:30 · alice", `commentStamp`), (`.task-edit-extras`).
Creation is the oldest entry; tasks from before the log get one from `created_at`.
There is no separate "Created:" line.

### What the history keeps

Each entry is `{ at, event, by?, from?, to? }`: `at` is `YYYY-MM-DDTHH:MM`, `by` the user who did it, only with users.
The history is capped at 30 entries (`historyLimit`) beside the entries a task is read by for good, which are never dropped: `created`, the last `assigned` and the last `shared` (`capHistory`: who made it, who has it, who shared it).

With users (`docs/spec/users.md`) each event records who did it (`by`, the user every write function is given; written only when there is one, so a wiki without users keeps its files as they were; an undo's snapshot keeps it, `cleanHistory`);
a recurring task's `repeat` is by whoever completed the occurrence before it.

A recurring task's next occurrence carries the series' history on (with the `done` or `skipped` just logged) plus a `repeat` entry ("Repeats: next Oct 3"), so the series reads as one log; it gets no `created` of its own.

### Undo leaves no trace

Every undo that PUTs old fields also sends the task's old `history` (`oldHistory` in `js/taskdata.js`: start-date, won't-do, skip and keep undos), and the server applies a sent `history` last (`cleanHistory` validates it), replacing what that request logged.
Restore snapshots keep it too.
Reopen-undo and bulk-complete-undo go through `batch-complete` and do log (`done`/`reopened`).

## Comments

A comment says what happened on a task, dated: a quick status line ("called Bob, waiting", "Alex: 6 people") or several lines.
A comment is markdown, rendered as the description is (How a description renders): paragraphs, lists, code, links, `[[task:…]]` chips.
Unlike a description, a comment keeps its lines: Enter starts a new line and the comment shows it (a `<br>`), without the blank line a new paragraph takes.
Comments are not part of the History: they are never capped, undo of other changes never touches them, and adding one logs no event of its own, only `mentioned` for each user it names with `@name` (History).

### Writing a comment

Comments are the edit dialog's last section (`renderComments` in `js/taskmodal.js`), after every field of the task and History, set apart by a line: they are saved at once and not with Save, whose bar stays at the dialog's bottom edge.
Hidden for a new task; the list is newest first and as long as it is (the dialog scrolls, not the list).

Under the heading is a line that looks like an empty field, **Write a comment…** (`.task-comment-open`, a button): a click puts the real field in its place, a text field of a few lines that grows with its text, focused.
**Alt+C** shows the field too (or goes to it), from anywhere in the modal of a task that has been made.
Enter starts a new line; **Ctrl+Enter** (or Add) saves the comment at once, not with Save, so Cancel never loses one, and puts the field away; Esc (or the field's Cancel) puts it away without saving, and doesn't close the modal.
The field has the description's markdown helpers (`markdownEditor`): lists go on, `[[` completes pages, Ctrl+B/I/K, a pasted URL asks for its name, a pasted or dropped file is uploaded.

✎ edits a comment in place: the same field takes the comment's place, with its text (Save or Ctrl+Enter saves, Esc cancels without closing the modal); × deletes it with an undo toast.
A comment still in its field is not lost: the modal's Save saves it with the task, and ✎ or × on another comment saves it first.
The text comes back with a file's line ends (`commentText`: `\r\n` becomes `\n`).

### Who may change a comment

A comment is its author's (with users; `mayChangeComment` in `go/taskby.go`): ✎ and × show on your own comments, × also to an admin, who may delete any; a comment by no one (from before users) is anyone's.
The routes answer 403 "Only its author changes a comment" otherwise, and a write that sends a task's comments whole (the undo of a delete or an edit) changes only the sender's own (`othersCommentsKept`: every other comment was there with that text, and none of them is dropped but by an admin).

### Comments on a card

A `💬 N` button (`.task-comments-btn`) sits in the meta row; its tooltip is the latest comment with its time.
A click on it opens or closes the line's details (One line per task), which list the latest 3 (`commentCardHtml`): a comment of one line on the line of its time, as a status line reads; any other (several lines or paragraphs, a list, code) under its time.
"N earlier…" leads to the modal.

### Comments of a series

Comments stay on their occurrence: a recurring task's next occurrence starts without comments (the history is carried on, and capped; comments have no cap, and a copy of all of them in every occurrence would grow without end).
It names the occurrence before it (`spawned_from`), and its dialog's History ends with a link there, **The occurrence before →** (`.task-history-before`), where those comments are.

### Comments in search and MCP

Comment text is part of a task's search text (`taskSearchDocs`, so semantic search too), a comment counts as activity for `edited:`/`after:`/`before:` (`lastActivity`), and `mcpTaskText` lists them after the description under "Comments:" (`- YYYY-MM-DD HH:MM: text`, with users `- YYYY-MM-DD HH:MM (bob): text`, a comment's following lines indented under its first).

### How comments are kept

They are kept in `comments` (`[{ id, at, by, text }]`, oldest first;
`id` 8 hex chars, `at` `YYYY-MM-DDTHH:MM`, text up to `commentMax` = 2000 chars;
`by` the user who wrote it, written only when there is one (with users, `docs/spec/users.md`), so a wiki without users keeps its files as they were;
an edit keeps it;
shown after the time, "12 Mar 09:30 · alice", `commentStamp`).

`POST /api/tasks/<id>/comments`, `PUT`/`DELETE …/comments/<cid>` each return the task, for open and closed tasks alike (`findForWriteLocked`).
`PUT /api/tasks/<id>` with `comments` replaces the whole list, for undoing a delete; `cleanComments` validates it (and restore snapshots), dropping bad entries and renumbering duplicate ids.

## News on your tasks

What happens on your tasks is told to you (with users), in the app and, where you ask for it, by mail (`newsFor` in `go/tasklogic.go`, `TasksCore.newsFor` in the browser: one rule, `go/testdata/news_cases.json` runs through both; `go/notify.go`).

### Whose news it is

You have to do with a task that you are assigned, that you made (`created_by`) or that you follow (`followers`: Following a task); a mention reaches you whatever the task.
Only shared tasks: a private one has nobody else.

A task has news for you when the latest of these is someone else's and after the last thing you did on it (anything of yours in its history, or a comment of yours):

| News | For |
|------|-----|
| It was given to you (the last `assigned` entry, or `created` for a task made with your name on it, while it is open) | its assignee |
| A comment | who has to do with it |
| A mention of you | who is named |
| It was closed or reopened | who has to do with it |
| It was given to someone else | who made it or follows it |

Not a date, a tag, the title or the order: they are noise.
History entries are in order and comments are; between the two the minute decides, and within one minute a comment of your own or your look at the task counts as the later one.
A look names the last comment it saw (`look.Comment`; the `seen` entry's `to` in the API's answers: a comment's id, `-` for a task without one), and every comment after that one is news, whatever the minute: one written a moment after you looked is not lost.
A look that names no comment (an older one), or one whose comment is gone, goes by the minute.

### Following a task

**Follow** in a shared task's ⋯ menu and in its dialog (**Unfollow** the other way; a toast with Undo) makes you one of its followers (`applyFollow` in `go/tasks.go`, `Task.Followers`), and from then on you are told what its maker is told: a comment, closed, reopened, given to someone else.
It is not yours to do for that: it is in no My tasks, and with news it is under Updates there, as what you gave away is.
At a team's size most of what matters to you is neither assigned to you nor made by you: a task your work waits for, one of your team's.
The dialog names the followers above History ("👁 Followed by you, bob"; `renderFollow` in `js/taskmodal.js`), and the button there is saved at once as a comment is (`setFollow` in `js/taskdata.js`).
A series' next occurrence keeps them.
`is:following` in the filter lists what you follow; an assistant's `search_tasks` says "the user follows it".

Who follows is kept in the task (`"followers": ["dan", "alice"]`, in the order they came; no key without one), so the rule runs from the task alone, in the server and the browser, the mail asks the task whom to tell, and git keeps it; a follow is a write of the task's file and a commit, by who follows.

| Rule | |
|------|---|
| Only yourself | A write says `follow: true` or `false` and names no one (`PUT /api/tasks/<id>`, `update_task`), as nobody writes under another's name; `followers` is no field of a write |
| Only a shared task, with users | A private one has nobody else: 400 ("A private task is yours alone: there is no one else's news to follow …"); without users: 400 ("This wiki has no users, so nobody follows a task"); a viewer: 403 |
| Following is a look | What was said before is no news to who starts to follow, so their look is noted with it (`markSeen`) |
| Nothing in the history | That someone follows is news to no one |
| Nothing follows by itself | Not who commented, not a former assignee; a mention reaches whoever is named anyway |
| Only who sees a task is told | A follower who is none of its list's members anymore (a list narrowed, a group left) stays in the file, is not among the names the API answers (`followersSeen`), and has no news, count or mail of it; among them again, they follow as before |

### The marks and the counts

On its card a task with news says what it is (before your name): **new** for a task just given to you (`.task-new`; its tooltip says who gave it and when), and in words for anything else, **bob commented**, **bob mentioned you**, **anna closed it**, **anna reopened it**, **anna gave it on** (`.task-update-mark`).

Opening the task takes it away (`markSeen` in `js/taskdata.js`; `seen` in `PUT /api/tasks/<id>`, `updateTaskLocked`): when you open its dialog or its details, the browser says so and Zone notes your look.
So the mark goes when you have looked, and whoever gave you the task reads in its History that it arrived ("Seen · anna").
For a task that has no news for whoever opens it, `seen` notes nothing.
**Mark all read**, in My tasks' ⋯ menu while there is news, sees them all.
No time takes it away: a task given to you weeks ago and never opened is the one that needs the mark.

The Tasks links say how many (`TaskCounts.New`, `updatesFor`): the notifications badge of the tasks, a filled count in the accent beside the due and Today counts (`.module-new`), with "2 updates for you" in the link's tooltip, on every page and on My tasks' pill: every task with news for you, wherever it is, and also one closed this month or last (`Store.RecentClosed`: the current closed files and last month's archives, which a page view may read; an older archive is never read for it, so news on a task closed longer ago isn't told).
A list's pill counts those in the list.
A click goes to My tasks, whose Today tab lists them under **Updates** (Updates), the ones that aren't yours to do among them: what you gave away, what mentions you, what was closed.

### Mentions

`@name` in a comment or a description, of a user who can be given tasks (not in code, not the `@` of a mail address), is logged in the task's history as `mentioned` (`mentionsOf`, `logMentions`), with who was named, by who wrote it; it is news to them, on a task they have nothing else to do with too.
A description logs only the names it didn't have before.
In a list for some only its members are mentioned (Assigning in a list for some).
Not built: the name as a chip in the text, and completing it while typing.

### Mail

Mail goes where you asked for it (`mails`, `mailKinds`): three kinds, each a box in My tasks' settings dialog (there where Zone could mail you: the deployment's mail server, `ZONE_SMTP_*`, Mail in `docs/spec/deployment.md`, and your `email` in `.zone/config`, which sign-up or an admin's `zonewiki user email NAME ADDRESS` sets), **none ticked until you tick it**:

| Box | Mail | |
|-----|------|---|
| a task is given to me | `tellAssigned`: "bob gave you a task: <title>" | several given in one write one mail, "bob gave you N tasks" |
| someone mentions me (@name) | `tellMentioned`: "bob mentioned you: <title>" | with the text |
| someone comments on a task I have, gave or follow | `tellComment`: "bob commented: <title>" | with the comment; not to who it mentions, who gets that mail |

Each comes with the task's link where Zone knows its address (`base-url`), sent in the background (a write doesn't wait for the mail server; a failure is in Zone's log), never for what you did yourself.
The kinds are kept as `"mail": "assigned,comments"` in your registry's `mine` (the kinds turned on, in that order); `"mail": "off"` reads as none.
There is no digest and no mail about watched pages.

### What you have seen

What you have seen is yours, not the task's (`go/taskseen.go`): a look is kept in Zone's folder, `.zone/seen/<user>.json` (`{"<task id>": {"at": "2026-11-15T09:30", "after": "…", "comment": "…"}}`: when, which entry of the task's history was its last then, since a history's times are to the minute and what happens after a look within its minute is news, and the last comment it saw), out of git, one look a user and task.
It writes no task file, makes no commit and changes no list's word: nobody's list loads again because someone looked, and no reader is a commit's author.
The news' rule reads the looks with the task's history (`historyWithSeen`), and the API answers them there as `seen` entries, so the browser's rule and the History are as they were; a history a client sends back (an undo) is kept without them (`sentHistory`).
A file names at most 300 tasks before those that can have no news anymore go (gone, or closed for two months: `canHaveNews`).
Lost with Zone's folder, tasks show their news again and nothing else happens.
A `seen` entry in a task's own history (from older tasks) is still read, and counts until its user looks again.

## Importance

A task is of **high**, **normal** or **low** importance (`importance`, Task fields); normal is what a task is unless something says otherwise, and says nothing anywhere.

On a card it is a mark before the title, on its line (`importanceMark` in `js/taskutil.js`, `.task-importance`): **!** in a warm red for high, **↓** in grey for low, whose title also steps back (lighter, not bold).
Normal shows nothing.
It marks, it doesn't sort: no order changes with it.
The manual order is where a task's rank is; importance says what must not be forgotten, or what may wait, wherever the task stands.

| Where | How |
|-------|-----|
| The edit modal | Importance: High, Normal, Low, after the dates |
| A task's ⋯ menu | **Importance…**, which shows the three levels in the menu's place, the task's own ticked |
| The keyboard | **i** on the selected task or tasks (`setImportance` in `js/taskselect.js`: one task by `PUT`, several by `batch-update`) |
| A new task's title | `!`, `!low` (Quick-add) |
| The filter | `!high` (or `!`) and `!low` |
| The API | `importance` in `POST`/`PUT /api/tasks` and `batch-update`: `"high"`, `"low"`, or `"normal"`, `""` or `null` for normal; anything else is 400 "importance must be high, normal or low" |
| Assistants (MCP) | `create_task` and `update_task` take `importance`, and a task's line of facts says "importance: high" (or low) |

The ⋯ menu and **i** come with one undo, as every quick action does (Notices and undo): the toast ("High importance", "3 tasks of low importance") and Ctrl+Z give each task its importance back; tasks of that importance already are left alone.

A file edited by hand is read leniently (`CleanImportance`: case and spaces aside, anything else is normal).
A recurring task's next occurrence keeps it; so does an export.

## Keep tasks

A task with `keep: true` is never archived: ticking it off means "done for now, back to the pool".
It suits a shopping list used with the task app's views: Open is the catalogue of staples, starting items today puts them on Today (the actual shopping list), and ticking one off sends it back to Open.
Cards mark keep tasks with ⟲ (`.task-keep`).
A list's setting Keep tasks (`keep`, List settings…) makes new tasks there keep tasks.

Completing a keep task (`keepDone`, in `PUT` and in `batch-complete`) clears its start date and its deadline, and sets `last_done` to today; it stays in the open tasks' file with the same id, tags, description and place in Open's order, and Closed stays empty.
Since Closed can't reopen it, the card shows an undo toast that restores the old fields (`keepSnapshot`).
**Won't do** retires a staple.
Keep and Repeat both say what happens after done, so they exclude each other: `keep` clears `recurrence`, and the edit modal doesn't offer Repeat while "Keep after done" is checked (a Repeat that shows is off, and the line under the fields says "A keep task does not repeat.").

## Lists

**A list is a place a task is in; a tag sorts tasks.** A task is in exactly one list and has any number of tags.
Five sentences say all of it:

1. A task is in one list.
2. A list is **for** someone: you alone (private), a group, or everyone (shared); everyone has one list built in, the **Inbox**, where a new task goes unless it is made in a list or names one.
3. A shared task can be assigned to one person, a member of its list.
4. **My tasks** is what is yours: the tasks in your own lists, and the shared tasks assigned to you.
5. A pin is a shortcut: a pill in your list switcher.

Without users there is one person, and every list is theirs: the Inbox is the tasks without a list, and My tasks is every task.

A list has a name, an entry in the list switcher with its counts (below), settings of its own (the tab it opens on, a WIP limit, tab names, whether new tasks are keep tasks …), and it is shared or private: a private list and the tasks in it are yours alone.
You make one with **New list…** on the Tasks page (or in the dialog that moves tasks to a list, or in a task's List field), put a task in it with the **List** field of its dialog (or **Move to list…**), and show it on any page with `[[tasks: list=<name>]]`.
A page shows a list; it doesn't make one: editing the embed away, or deleting the page, changes nothing of the list, and a list is deleted only by **Delete…**, which asks where its tasks go.
Tags go across lists: `[[tasks: tag=sam]]` on Sam's page shows every task tagged so, whatever list it is in.
A wiki from before lists were places, or from before the Inbox, is taken over once at start (Migrations at start).

### Making a list

**New list…** (`#list-new-dialog`; `POST /tasks/new-list`, `createListOp` in `go/tasklists.go`) asks for the list's name and makes it: an entry in the registry, in one commit (`Create list #shop`).
A name can't hold `[ ] , | = / "`, a backslash or a control character (`checkListName`; a space or line break becomes `-`).
No page is written.
With users the dialog has **Private: only you see it and its tasks**, which makes it a private list of yours, and **For** (Lists for some).
Then the list opens (`/tasks?list=<name>`).
Nothing else changes by it: new tasks still go to the Inbox, and the tasks there stay.

**The new list is pinned for you**, last, in the same commit (a shared list with users: the list in the shared registry, the pin in your own), so it is a pill at once and not out of sight under More.
(`pin=0` in the form leaves the list unpinned; no dialog sends it.)
An assistant makes a list with `create_task_list`, the same operation: the list is pinned for its user, last (not for a connection that isn't given its user's private place: the pin is kept with their own lists).

Refused, back in the dialog with the reason: no name, a name with a character lists don't take (List names), a list you already see ("There is a list #shop already").
A tag of that name is a tag: making a list takes no task into it.
Under an embed of a list that isn't there, **Create the list #<name>** does the same.

### List names

A list's name (`cleanList`, `checkListName`) is one word, lowercase, spaces `-`, a leading `#` dropped, at most 60 bytes, none of `[ ] , | = / "`, a backtick or a line break (they mean something in an embed or a ref).
No list can be called `inbox` or `none`: they name the Inbox (below).
A new name is kept composed (NFC: `café` typed with a combining accent is the `café` of a keyboard that has é).
A name is unique among the shared lists and among one user's private lists, and so is its look: names that are one in their compatibility form, in lower case (NFKC, `listNameKey`: a full-width `ｔｅａｍ` is `team`, `ﬁ` is `fi`), are one name, so New list…, a rename or Make shared… refuses a name that looks like a list's there ("There is a list #team already"), also of a list for some that whoever asks doesn't see.
A user can't make a list by a name of one they see ("There is a list #shop already"), private or shared; they can still end up with a private and a shared list of one name (the migration, or someone making the shared one later, who doesn't see theirs).
For them the plain name is their own list and the shared one is **`shared/<name>`**: that is a **ref**, how a list is named to one user (`ListInfo.Ref`, `taskLists(user)`, `resolveList`): in URLs (`/tasks?list=shared/gifts`), in pins, in the API's and the tools' `list`, in the browser.
On a page, `[[tasks: list=gifts]]` is the shared list unless the page is in your private folder (`listOnPage`), so a shared page shows everyone the same list.
A task's `list` is the list's name (A task's list).

### A list that isn't there

An embed of a list that isn't there (`noListHTML`, `tasks_no_list`; also `/tasks?list=<name>`) shows in the app's place a line, "There is no list #<name>.", and for who may make lists **Create the list #<name>** and a link to the tasks tagged so (`/tasks?tag=<name>`).
Nothing else on the page changes.

### Private lists

With users a list is private (yours alone), for everyone, or for some: whoever sees a list sees its tasks.
**Private and shared are said of lists, not of tasks** (Private tasks in `docs/spec/users.md`): a task in a private list, or in your Inbox, is yours alone and is kept in your files; a task in a shared list is shared.
**With users a shared task is always in a list**: there is no place that is everyone's and nobody's.
A new task may start in any list.

**A shared task never goes into a private list nor into an Inbox** ("A shared task stays shared": others have seen it), by any route or tool.
**A private task leaves for a shared list** only when the write says `share`, which the browser sends after telling you that everyone will see it; its History then says `shared`.
Out of a private list into your Inbox, or the other way, it stays private, and nothing is asked.
A private list can be made shared with **Make shared…** (Making a list shared), its tasks with it; a shared one never private.

Every reader asks `canSeeList` (through `taskLists(user)`, which gives only the lists a user sees), every write to a list or to a task in it `canWriteList` (`go/access.go`): whoever sees a list and may write tasks changes its tasks, and the list itself (its name, its settings, deleting it) is its owners' and the admins' (`canManageList`; Who manages a list).

### Lists for some

With users a shared list is for everyone, or for the groups and users it names, its **members** (`"for": ["@hiring", "anna"]` in its entry: `@name` a group, which may be one made of other groups, `@product` for who is in dev or in design (Groups in `docs/spec/users.md`); a bare name a user; `List.For`, `ListInfo.For`, `listMember` in `go/access.go`).

Members see the list and its tasks, and change them.
To anyone else it is no list, as another user's private list is: no pill, no entry under More, no task of it in All tasks, My tasks, a tag's view, the counts, search, Linked tasks, a `[[task:…]]` chip, an export, the history, or an assistant's answers; its address (`/tasks?list=<name>`) and an embed of it say what they say of a name no list has, and a write that names it or one of its tasks finds nothing.
An admin is no member by being admin, as an admin sees no private page; they can put themselves in a group, which the settings' log shows.
Without users nothing is anyone's: a list for some left from when the wiki had users is everyone's (`listMember`), as a group's folder is an ordinary folder then.

#### Saying whom a list is for

You say whom it is for when you make it (**New list…**: the **For** field, "everyone" when empty, completing the groups there are; an assistant's `create_task_list` with `for`), and its owners change it with **Who it is for…** in its ⋯ menu (`#list-for-dialog`, `POST /tasks/list-for`, `setListForOp`; a commit "List #hiring is for @hiring, anna" or "… is for everyone").
The words are groups and users, commas or spaces between them (`cleanFor`): a name that is no user, or no group's name, is refused, and **you are one of them**: "You wouldn't see the list yourself: add your name, or a group you are in".
A private list is for one; a list for some is a shared one: in New list… ticking Private turns For off, and a For that names someone turns Private off (`js/listsrow.js`), as the server refuses both together.

The For field completes word by word (`js/listsrow.js`, the words in `data-words` of `#list-for-words`): the groups and the people who can be given tasks, those already named left out; the browser's own datalist would take the whole field for one word.
Without the script it offers the groups for the first word.

Its name is taken for everyone: two shared lists can't have one folder, so New list… with that name says "There is a list #hiring already" to who doesn't see it.
It stays taken once the list is deleted or renamed (`sharedNameTaken` in `go/tasklists.go`): its folder keeps whom its history is for, and a shared list made there, by New list…, a rename or Make shared…, would be for them too, its tasks out of everyone else's sight; only a list for the very same people takes the name back (the list renamed back, or one made for them again).
That a name is taken is the one thing anyone else can learn.

#### Narrowing a list

Narrowing asks when it takes tasks out of someone's sight (`askError` from `setListForOp`): with open tasks there assigned to people who would be no members, the dialog comes back saying so, "3 open tasks here are assigned to bob, dana, who will no longer see the list. The tasks keep their names and count as unassigned here, to hand on; for them again, they are theirs as before. Save again to go on." (`for_confirm`, a hidden `confirm` in the form); Save again narrows it.
With nobody's task at stake it asks nothing.

#### Assigning in a list for some

Its tasks are given only to its members (`applyAssignee`: "bob isn't one of those the list #hiring is for"): Assign…, the Assignee field and the title's `@name` offer them (`members` in the lists a page carries, `assignableIn` in `tasks.js`), and a mention of anyone else is text: no news, no mail.

Who no longer sees the list no longer has its tasks (`assigneeGone` in `go/tasksyntax.go`, `TasksCore.assigneeGone` with `people.members`): a list narrowed, a group someone left.
The task keeps the name and **counts as no one's**, as the task of someone who can no longer be given tasks does: the name struck through on the card ("Assigned to bob, who doesn't see this list anymore: it counts as no one's") and in the dialog, under Unassigned By person, in the list's "N unassigned", found by `@none` and still by `@bob`, said so to an assistant; so it comes up where the team looks for what nobody has, and is handed on.
Nobody is unassigned by it: among the list's members again, their tasks are theirs as before.
(A name kept on a task its bearer can't see, and shown as theirs, would be a task that looks taken care of and isn't.)

#### Moving a task to fewer or more people

A task that leaves a list for some for a list for everyone says who will see it (`widens` in `tasks.js`), as sharing a private task does: the move dialog's row reads "everyone will see it", the edit dialog's line "Everyone who uses the wiki will see and change this task. Now only those the list hiring is for do.", and a drop on the other list's section (By list) asks first (`confirmShare` with the list it leaves).

Into a list fewer people see it goes only by who made it, or an admin (`taskLosers`, `mayNarrowTask` in `go/tasks.go`), as a page goes to fewer people: a list for some that someone who sees the task now is none of.
The edit dialog's line names them ("bob, dana will no longer see this task: the list is for fewer people."; for anyone else "… would no longer see this task: only who made it, or an admin, moves it to a list for fewer people."), the move dialog's row reads "fewer people will see it" (`narrows` in `tasks.js`), and the server refuses anyone else (403).
Between lists the same people see it moves without a word.

#### How a list for some shows

The heading says it: 👥 beside the list's name, "For @hiring, anna: only they see this list and its tasks (anna, bo, …)", the sign a group's page has (🔒 is what is yours alone: a private list).
Its pill and its entry under More carry the people icon and say whom it is for (`TaskPlace.For`; their tooltips add "(for @hiring, anna)"), a task's list chip on a card reads "👥 hiring", and its tooltip names its members.

It is pinned like any other list: for who made it, and for anyone else once they pin it; until then it is first under More and in the left column, among **Your lists**.

#### Where a list for some is kept

Who sees a task is decided by where it is kept (Storage): the list's entry is not in `_tasks/lists.json`, which everyone reads, but in the list's own folder, `_tasks/lists/<name>/list.json` (one entry, as a line of the registry is), and that file is what tells every read of the tasks there whom they are for.
The store leaves the folder out for anyone else (`Store.dirs(who, owner)`, `Member`): every reader of a user's tasks, and every write, which therefore never touches the files of a list its user doesn't see (a task that names such a list is refused by the store itself, `byOwner`).
What holds every user's tasks (the search index, a task found by its id) asks `canSeeTask`, which asks the same file (`taskFiles.SeesList`); `canSeePath` hides the folder's files in the history.
An entry of its name in the registry as well (a merge, a hand edit) doesn't make it everyone's list: the folder's file says whom it is for (`withListFiles`).
A task that names the list but lies in another folder (a file moved by hand, a merge; the next write files it) is left out for anyone else too, by every read that shows tasks (`Store.shows`: open and closed, a year's closed, their count, the years, the news, the export): the folder and the task's own `list` never disagree about who sees it.
A list narrowed gets its file before it leaves the registry; one for everyone again loses the file and is back in the registry.
Renamed or deleted, its old folder keeps the file, marked gone (`{"name": "hiring", "for": […], "gone": true}`): no list anymore, and the folder's history is still only its members' to see, so its name is no other shared list's (Saying whom a list is for).

In search its tasks are a layer of their own, as a group's pages are (`docShared`, `placeOfDoc`, `listsForSome` in `go/retrieval.go`; Who searches what in `docs/spec/search.md`): they are in no index but its members', so their words, counts and lengths take part in no one else's ranking and a typo of a word only the list has suggests nothing to anyone else.
A list narrowed or widened splits the index again, though no task changed (the lists that are for some are part of what the split goes by, read from their folders at each search: a look per list).

A commit about it names it to its members only (`hideNames` in `go/access.go`): narrowing it ("List #hiring is for @hiring, anna"), pinning it for everyone and renaming it write the registry everyone reads, so the commit is in everyone's history; to anyone else it reads "A list for some changed".
The tasks' word leaves it out for anyone else (`Store.Word(who, …)`): a write to it doesn't make their browser load its lists again.

### Who manages a list

**A shared list has owners** (with users; `"owners": ["anna"]` in its entry, `List.Owners` in package `taskfiles`, `ListInfo.Owners`): who made it, with **New list…** or by making a private list shared.
**Owners and admins manage the list**: rename it, delete it, set its settings (`canManageList` in `go/access.go`; `listForManage` in the operations).
**Everyone else who may write tasks changes its tasks and leaves the list as it is** (`canWriteList`): its ⋯ menu has no Rename…, Delete… or List settings… for them, and the routes and tools answer 403, "#ops is bob's list: its tasks are yours to change, the list itself (its name, its settings, deleting it) is theirs and an admin's."
Among a hundred people a list isn't everyone's to rename or delete.
A list from before owners has none: it is the admins' ("#old is changed by an admin of this wiki …").
A private list is its user's.

| Action | Who may | In the browser | Route | MCP tool |
|--------|---------|----------------|-------|----------|
| Make a list | whoever may write tasks | New list… (the Tasks page's ⋯ menu, the List field, the move dialog), Create the list #<name> | `POST /tasks/new-list`; `POST /api/tasks/lists` (the List field and the move dialog) | `create_task_list` (with `private`, `for`) |
| Rename it | its owners and admins (a private list: its user) | Rename… | `POST /tasks/rename-list` | `rename_task_list` |
| Delete it | its owners and admins (a private list: its user) | Delete… | `POST /tasks/delete-list` | — |
| Change its settings | its owners and admins (a private list: its user) | List settings… | `POST /api/tasks/settings` (`{list, set}`) | — |
| Say whom it is for | a shared list's owners and admins | Who it is for… | `POST /tasks/list-for` | `create_task_list` with `for`, as it is made |
| Make it shared | a private list's user | Make shared… | `POST /tasks/share-list` | — |
| Pin or unpin it, for yourself | whoever sees it and may write tasks | Pin, Unpin | `POST /tasks/pin-list` (`pin`: `1`, `first`, `0`) | — (`create_task_list` pins the list it makes) |
| Reorder the pins | whoever may write tasks | dragging the pills | `POST /tasks/pin-order` | — |
| Change its tasks | whoever sees it and may write tasks (`canWriteList`) | the task app | `/api/tasks…` | `create_task`, `update_task` |

Without users, and in a wiki that has no admin (from before roles), "its owners and admins" is whoever may write.
A viewer does none of these.

#### The Tasks page's ⋯ menu

The ⋯ beside the heading of `/tasks` (`.page-menu-btn`, on every view of it; the page's ⋯ menu, `#page-menu`, with `js/pagemenu.js`) is the page's one such menu: the toolbar's ⋯ is not shown on this page.

| Item | Where |
|------|-------|
| **List settings…** | first, where the view has settings (Settings) |
| **New list…** | every view, for who may write tasks |
| **Rename…** | a list, for who manages it |
| **Pin** / **Unpin** | a list, for who may write tasks |
| **Make shared…** | a private list, for who may write tasks |
| **Who it is for…** | with users, a shared list, for who manages it |
| **Delete…** | a list, for who manages it |
| **Rename tag…** | a tag's view, `/tasks?tag=<tag>` (Renaming a tag), for who may write tasks |
| **Mark all read** | My tasks, while a task has news for you |
| **Export tasks**, **Import tasks…** | last: Export for whoever sees the tasks, Import for who may write them (Export and import) |

The view's own actions (`[data-tasks-action]`) are shown and run by `js/tasksettings.js` where the view has them.
Each but the pins is a dialog with a form the server answers (`data-dialog` on the menu item; a refusal comes back with the message in the dialog).
Not in sharing mode; a viewer's has Export tasks alone (`Menu`, `Write` in `tasksPageView`; none for a name no list has), and the pills don't reorder for them.
The ⋯ of an embed on a wiki page offers the view's own actions only: List settings…, Mark all read, Export tasks and Import tasks….

#### Renaming a list

**Rename…** (`#list-rename-dialog`; `POST /tasks/rename-list` with `list` and `new_name`; `renameListOp`) changes in one commit (`Rename list #shop to #food`) the list's name in its registry, the `list` of its tasks (open, closed, archived), the users' pins of it, and where pages and tasks' text name it: the `list=` of `[[tasks: …]]` embeds and the `?list=` of links to `/tasks`.
Code stays as written, and so do a tag of that name, `tag=` and `#word` in prose.
An assistant renames a list with `rename_task_list`, the same operation.
A name of a list you see is refused: two lists become one by deleting one into the other.
A shared list's name is everyone's, so the rename reaches every user's pins, private pages and private tasks' text too, as a page rename's links do (one who has a private list of the old name keeps their embeds of it); a private list's only its owner's.
Refused with nothing changed: a tasks file, a registry or a page that can't be read.
Text not yet in git is committed first (`keepPendingPages`).

#### Deleting a list

**Delete…** (`#list-delete-dialog`; `POST /tasks/delete-list` with `list`, `to`, `share`, `close` and `confirm`; `deleteListOp`): the list goes, in one commit (`Delete list #shop`); **its tasks stay**, open and closed ones, in the list `to` names.
With none named, a private list's go to your Inbox (they stay private); a shared list's, with users, to the shared list `general`, made for them when there is none (a shared task is always in a list); without users to the Inbox.
The dialog offers those first: "Your Inbox", "#general", "The Inbox (no list)", then the other lists ("#name (private)", or "(shared)" when deleting a private list).
`general` itself with tasks in it is deleted only with another list named for them ("With users a shared task is always in a list: name the list the tasks of #general go to"); with no other shared list the dialog says "No other shared list".

**Close its N open tasks as won't do** (`#list-delete-close`, `close=1`; offered while the list has open tasks, not ticked): for a list that is over, its open tasks are closed undone in the same commit, as Won't do closes one (Closed shows them with "✗ won't do", and they can be reopened from there), so they aren't left open in another list or in none.
No task is deleted by it.
A shared list's tasks can't go into a private one, and a private list's go to a shared list only with `share` (the dialog's box: "Share its tasks if they go to a shared list").
Into a list fewer people see (a list for some that someone who sees them now is none of) they go as any task goes there (Moving a task to fewer or more people): only when who deletes it made each of them, or is an admin, else the dialog says so ("bob, dana would no longer see the tasks of #ops: #hiring is for fewer people. Only who made a task, or an admin, moves it there: move them to a list as wide").
And as narrowing a list does, it asks first when open tasks there would be with someone who doesn't see that list ("1 open task of #solo is assigned to bob, who doesn't see #hiring. … Delete again to go on."; `askError`, `assignedOutside`; `delete_to` and a hidden `confirm` naming that list, so a yes counts for it alone), unless they are closed on the way.
Pins of it go; pages keep their embeds of it, which then say that there is no such list.

#### Making a list shared

**Make shared…** (a private list; `#list-share-dialog`; `POST /tasks/share-list`; `shareListOp`) moves the list to the shared registry and its tasks to the shared files, in one commit (`Share list #gifts`), each with `shared` in its History.
Refused while a shared list has that name.
There is no way back.

### The Inbox

**The Inbox** (`inboxRef`, `isInbox`, `inboxOwner` in `go/tasklists.go`; `inboxView` in `tasks.js`) is the list every user has without making it: **the tasks without a `list`**.
**A new task goes there unless it is made in a list's view or names a list**: from `c t`, New task, My tasks' **+ New**, an assistant's `create_task`, `POST /api/tasks` without `list`.
With users it is yours alone (the task is kept in your files, `private/<user>/_tasks/`): what you capture is seen by nobody until you file it.
Without users it is the wiki's: the tasks in no list, in the shared files.

**Filing** is moving a task to a list: the List field, **Move to list…**, `m`.
Into a shared list that shares it (`share`), and Zone then **assigns it to you** unless the write says who has it (`sharedBy`, whatever door shares it: Private tasks in `docs/spec/users.md`; the dialog shows the name, to be taken off; the move dialog sends it), so what was yours stays in your My tasks unless you say otherwise.

Its view is `/tasks?list=inbox`, headed **Inbox** (with 🔒 with users), the second pill of the list switcher; a line under the heading says what it is for ("New tasks land here, and only you see them. Move one to a list to file it; in a shared list everyone sees it.", without users "New tasks land here, in no list. Move one to a list to file it.").
It is All tasks narrowed: All tasks' settings (the tab, Plan, the limit), no settings dialog of its own, a locked chip **Inbox** in the filter field (the filter word `list:inbox`), and a new task made there is in the Inbox.
A task filed from there leaves the view, with the notice a list's view has.
On a page, `[[tasks: list=inbox]]` shows whoever looks their own.

It can't be renamed, deleted, shared or pinned, and has no entry in a registry.
**`none`** names it too (`list=none`, `list:none`, `search_tasks(list="none")`).
In a wiki that has a list called `inbox` or `none` from before the names were kept, the name is that list's (the Inbox's migration renames a list `inbox`).
With users there is no Inbox for a viewer (they make no task), nor in sharing mode, where what is private isn't shown.

#### The list general

A shared task that loses its list (with users) goes to the shared list **`general`**, made for it when there is none (`generalListName`, `ensureGeneralLocked`): a list deleted without naming another, a restored or imported task whose list is gone, and the tasks that were in no list before the Inbox.
It is a list like any other.

### My tasks

**My tasks** (`/tasks?mine=1`; `mineFilter`, `inMine` in `go/tasklists.go`, `TasksCore.inMine` in the browser) is your home view, the one the Tasks links open (until you switch to another view: they come back to that one, The Tasks link in `docs/spec/navigation.md`) and their badge counts.
It shows **what is yours: the tasks in your own lists (your Inbox, your private lists), and the shared tasks assigned to you**, wherever they are.
Under the heading a line says what it holds: "Your own tasks and what is assigned to you.", without users "Every open task.", and the lists left out, as links (`leftOutLinks`).

An unassigned shared task is its list's, not yours: it is in the list's view and in its pill's counts, and the list's heading says how many there are ("3 unassigned", a link to them; A list's backlog). Taking one (assigning it to yourself: `a`, Enter) puts it in My tasks.
So My tasks is as short for one of a hundred as for one alone, and nothing is in everyone's.
Assigned to you is the task's `assignee`; an assignee who is no user anymore (or disabled, or a viewer now) is no one.

Without users, and for one no task can be given to (a viewer; `mineAssigns`), every list is the user's own: My tasks is every task but those of a list left out.
With no list in the wiki it is every task, and there is no such view: `/tasks?mine=1` goes to `/tasks`, where the Tasks links go then.
On a page, `[[tasks: mine=1]]` shows it to whoever looks.

It is one task app: the cards' list chips say where each task is (a task in the Inbox has none), and a new task there goes into the Inbox.
A task that leaves by a change of its assignee or its list gets the notice a list's view gives ("Saved; it no longer shows here.").
Its settings are its own, and yours (Where settings are kept): the tab, Plan, the limit, the tabs' names, the mail.
Zone assigns nothing by itself, but a task of your own that you share: it is yours to do unless you say otherwise.

#### Leave out of My tasks

`home=off`, a list's setting (List settings → "+ Leave out of My tasks"; `ListInfo.LeftOut`), is for a list of your own whose tasks don't belong there: a catalogue of staples, a someday list.
Its tasks are in its own view only, and not in the Tasks links' count.
It counts for a list that is yours (a private one; without users any); a shared list's tasks are in My tasks by their assignee, so the dialog doesn't offer it there.

#### Given to others

"Given to others: 3", in the line under the heading (`.tasks-given`; `givenToOthers`, `givenHref` in `go/tasklogic.go`), links to the open shared tasks you made that someone else has: All tasks filtered `from:me -@me -@none`, By person (`/tasks?view=open&group=person&from=me&assignee=-me,-none`; `?group=` names what the board's sections stand for, for a link).
They are not yours to do, so they are not in My tasks nor in its counts; this is where you see what you are waiting for, who has it, and whether it arrived (its card says "new" until its assignee opened it).
Not shown when there is none.

#### My tasks' own order

My tasks' manual order is its own, and yours (`mineOrder`, `setMineOrder` in `go/tasklists.go`; `own`, `setOwnOrder` in `tasks.js`): a list's order (a task's `sort_order`) is the list's, for everyone who looks at it, a team's ranking of its backlog; what you rank in My tasks, across lists, is your sequence and moves nothing in any list.
It is kept as task ids in your registry (`"order"`; without users the wiki's), written by `POST /api/tasks/reorder` with `mine: true` in a commit "Reorder My tasks": no task is written.
The view lays it over its own copies of the tasks (a task's place in it stands for its `sort_order` there, a task not in it is Unsorted), so Unsorted and Sorted, Today and the Plan board's sections work in My tasks as everywhere; it comes with the page's tasks (`"order"` in `tasksInitial`) and with `GET /api/tasks?with=order`.
A task new to My tasks (one just given to you, a capture) is Unsorted, at the top, until you place it.
An assistant's position (A position in the order) is in the lists' order, not in this one.
Until you rank something there it is the lists' order of your tasks, so nothing moves on the day; ids of tasks that are closed or gone drop out at the next move.

#### One rule in server and browser

The page gets the lists left out and whether assignment counts (`data-init-out`, `data-init-mine`: `assign` or `lists`), the routes take the scope as `?mine=1` (the closed tasks' count and years, the export), and one table of cases runs through both (`go/testdata/mine_cases.json`).

### The list switcher

`/tasks` opens with a row of pills above its heading (on a big screen the left column instead: The lists in the left column, below) (`.tasks-places` in `templates/tasks_page.html`, `taskPlaces` and `taskMore` in `go/tasksembed.go`, `js/listsrow.js`),
on the page's white sheet with it, looking as the module switcher at the top of the page does (`.tasks-places-group`: one white capsule with a border, the pills inside it without one, in its type and height, the current one shaded).
**My tasks is the first pill, the Inbox the second; a pinned list is a pill; a list that isn't pinned is under More.**
In order: **My tasks** (above), whenever there is a row;
**Inbox** (`data-place="inbox"`; for who has one, `hasInbox`: with a lock with users; tooltip "Inbox: new tasks land here");
then **the pinned lists** in their order,
then **More ▾**.
The pinned lists carry a pin (`.tasks-place-pin`, tooltip "Pinned list #<name>"), a private list a lock (tooltip "… (only you see it)").

The heading names what is shown: **List: <name>** (with 🔒 for a private one), **My tasks**, **Inbox** or **All tasks** (the window's title likewise).
Every list's pill or link goes to `/tasks?list=<ref>`: the list with its settings,
and as its **Backlinks** every page that embeds it (`listPages`: any `[[tasks: list=<name>]]` outside code on a page you may see that shows this list, A–Z by title; at the end, in the right column on big screens and as the breadcrumb's `← N` chip, as on a page view).

**Only `/tasks` has it**: a page that embeds a list shows the list, as any embed does, and no switcher, so the Tasks module and the wiki's pages stay apart; the pages are a list's Backlinks.
With no list there is no switcher.
Another user's private list is not in it; in sharing mode a list that hides when sharing (`noshare`) is left out of the pills and of More (on its own URL More then reads "More"), and the counts are hidden as on the module links.

The pills wrap onto more lines on a narrow window and are finger-sized under `(pointer: coarse)`; **on a phone (≤640px) the row is one line that scrolls sideways**, the current pill brought into view (`js/listsrow.js`), and the line under the heading that only says what the view is isn't shown (one with a link in it, N unassigned or the lists left out, stays: `.tasks-list-home:not(:has(a))`), so the first tasks are on the first screen.

#### The counts on the pills

Each pill but More carries **one number: what is due today or overdue** there (a list's own, `countTasks` over the list: everyone's tasks in it), and how many of its tasks are new for you; the Today count is on the Tasks module links alone, where it is the day's load (`.tasks-place .module-focus` is not shown);
the current one is shaded (`aria-current`): My tasks on `?mine=1`, the Inbox on `?list=inbox`, the list on `?list=<ref>`, More for a view under it, none for a name no list has.
`js/chrome.js` keeps the counts current with the Tasks link's (`refreshDueBadge`: `GET /api/tasks/due-count?places=1` adds `places`, each pill's counts by its key: `list:<ref>`, `mine`, `inbox`).

#### More

**More** (`.lists-more`, a `<details>`; its pill is the summary, `.lists-more-btn`) opens on a click a panel under it (`.lists-more-panel`) of links, a line each:
**each list you see and haven't pinned**, A–Z by name (your own before the shared one of that name), a private one with a lock,
with users in two groups, each under its word when there are both (`TasksMore.Lists` and `Others`, `.lists-more-head`): **Your lists** (your private ones, the lists for some that you are one of, and the lists for everyone that hold an open task assigned to you), then **Other lists** (for everyone, the rest): among forty lists of a company the few that are yours come first, and the find field goes over both (a group's word goes with its last list);
then **All tasks** (`/tasks`: every task you see, in whatever list).
No counts on the lists in it, and **no number on the pill**: what is yours to do is in My tasks.
Escape and a click elsewhere close it.
It names the view the page shows when that is one of its own: on an unpinned list the pill reads that list's name, on `/tasks` (and a tag's view, which is All tasks with a filter) "All tasks", and it is the shaded one; else it reads "More".
It is there for a viewer and in sharing mode too (the page's ⋯ menu isn't), so it is no menu of the page's but links.
On a narrow screen the panel is as wide as the row.

With 8 lists or more under More (`moreFilterFrom`) a field at the panel's top finds a list (`.lists-more-filter`), focused when it opens; what is typed leaves the lists whose name has it (All tasks stays), and Enter opens the first.

#### A list new to you

A list under More that this browser hasn't shown before, so one someone else made, or an assistant (a list you make in the browser is pinned), has "new" beside its name (`.lists-more-new`; `zone_lists_seen` in the browser's `localStorage`), and More a dot (`.has-new`) until it is opened.
The first visit knows every list; a list is known once it was a pill, was under More when More was opened, or was the page's view.
It is the browser's memory, not the wiki's: another browser marks it again.

#### Dragging the pills

A pinned list's pill dragged with the mouse to another place among the pinned lists puts the pins in that order (`data-reorder` on the row, for who may pin; `POST /tasks/pin-order`, `pinOrderOp`), in one commit ("Reorder the pinned lists").
Let go outside the row, it goes back.
My tasks, the Inbox and More stay where they are.
With a finger there is no dragging (it scrolls and taps): **Unpin** and **Pin** again puts a list last.

#### The lists in the left column

**On a big screen (from 1320px wide, where pages get their side columns) the lists are the left column of `/tasks`, and the row of pills isn't shown**: the same entries, a line each, where there is room for all of them (`lists_rail` in `templates/tasks_page.html`, `.lists-rail`; the row stays for every narrower screen).
In three groups, from the top:
**the views**, My tasks, the Inbox and All tasks (no list of their own);
**Pinned**, the pinned lists in their order;
**Lists**, every other list A–Z (with users **Your lists** and **Other lists**, as in More), with the find field from 8 lists on.
The current view's line is shaded.
It keeps one rhythm: every line as high, the same room before each group and heading, a heading small, light and spaced, close to the lists it names;
the names start in one line, with no pin (the heading says it), and a list's lock or people after its name (the Inbox, always yours, has none there).
My tasks, the Inbox and the pins carry the pills' counts, kept current with them (`js/chrome.js` refreshes `.lists-rail-place` by `data-place` too); the other lists none, as under More.
The pins can be dragged up and down with the mouse, for who may pin, as the pills can (Dragging the pills).
A list new to this browser has "new" beside its name there as under More, and counts as seen once the column has shown it (More then gets no dot).
The column starts level with the sheet's title and scrolls on its own when it is taller than the window; the sheet keeps a page's width, centred, so the heading is where a page's is.
A list's Backlinks are the right column, as before.
Below 1320px the column isn't there and the row of pills is, as above.

### Pins

**A pin is a shortcut**: the list is a pill of your list switcher, and nothing else (`pinnedLists`, `pinListOp`; `POST /tasks/pin-list` with `list` and `pin`: `1`, `first` or `0`, anything else refused).
It doesn't say what is in My tasks (that is whose a task is), nor where a new task goes (the Inbox, or the list in view).
You pin and unpin in a list's ⋯ menu on `/tasks?list=`: **Pin** puts it last, **Unpin** takes it out; the pills can be dragged too (Dragging the pills); `pin=first` puts it first ("Pin list #shop first"), which no menu sends.
**A list you make starts pinned**, last, wherever it is made: New list…, the List field, the move dialog, an assistant's `create_task_list`.

**Pins are the user's own**: in their registry (`pinned`, in order); without users the wiki's, in the shared registry.
With users nobody pins for anyone else: a user who has pinned nothing has no pins, and finds their lists under More (Your lists first) or in the left column.
A connection not given its user's private place pins nothing (its user's pins are kept with their own lists).
Pins in the shared registry from before the wiki had users (or from an older Zone's "Pin for everyone") are no one's: no user reads them.
A registry that names a `default` list reads as that list pinned first (`ParseRegistry`), and is written without the key.

### Where a new task goes

The edit dialog of a new task says it under the title (`.task-edit-target`, `syncTarget` in `js/taskmodal.js`), and with users who sees it: "Adds to your Inbox: only you see it.", "Adds to the list team: everyone sees it.", "Adds to the list hiring: only those it is for see it." (a list for some), "Adds to the list diary: only you see it."; without users "Adds to the Inbox." or "Adds to the list shop.".
It follows the List field, and a `#word` in the title that names a list (Quick-add: `#shop` puts the task in the list shop, the first such word; the others are tags).
Hidden when editing a task and for several titles at once.

**A task for someone needs a shared list** (with users; `autoInbox`, `sharedChoices`): the Inbox is yours alone, so on a new task still in the Inbox that nobody chose a list for, naming an assignee (the Assignee field, the title's `@name`, a view's `assignee=`) turns the line into a question: "A task for someone else needs a shared list:" and the shared lists, each a button (`.task-edit-target-list`), the one this browser last made a task in first (`localStorage` `zone-tasks-shared-list`), then A–Z; past six it says "or another in List.".
A click on one is choosing it in the List field.
**Nothing is chosen for you**: a task goes where somebody looked.
Taking the name away takes the question away.
**Your own name asks nothing**: a task in your Inbox is yours as it is (a view's `assignee=me`, the title's `@me`), so it stays there, private, and is saved without an assignee.
With no shared list the line reads "A task for someone else needs a shared list: choose one in List." (New list… in it makes one), and Save without one says so on the List field.
An assistant's `create_task` with an assignee and no shared list is refused, naming the shared lists; `POST /api/tasks` likewise answers 400 ("A private task is yours alone. Put it in a shared list to assign it").

### Moving tasks between lists

A task moves to another list with the List field of its dialog (The List field), **Move to list…**, **m**, or a drop on a list's section By list; filing a task out of the Inbox is such a move (The Inbox).

#### The move dialog

The selection bar's **List…**, **Move to list…** in a card's ⋯ menu (for the selection or that one task) and the **m** key on the selected tasks open it (`openBulkListModal`, `.bulk-list-modal`): "Move 3 tasks to a list" lists every list you see (A–Z by name, a private one with 🔒, the shared one of a name you have a private list of as "name (shared)"), **Inbox** (with 🔒 with users) and **New list…**.
A click on a row moves the tasks at once (↑/↓ go through the rows, Enter picks, Esc or Cancel leaves).
The move sets each task's list in one `batch-update`; its tags stay.
The row of the list every selected task is in is ticked and has the focus; a list only some are in says how many ("1 of 3").

A task is as private as its list (Private lists): the rows of your private lists, and with users the Inbox's, are off while a shared task is selected ("a shared task stays shared"), and a row that would take a private task to a shared list says so ("shares it with everyone", with users "shares it with everyone, assigned to you") and sends `share`; Zone gives it to you: a task of yours that you file stays in your My tasks (assign it to no one, or someone, afterwards).
Rows for a list more or fewer people see say so too (Moving a task to fewer or more people).

One undo puts every task back in its list ("3 tasks moved to list “work”", "3 tasks moved to the Inbox"), but those that were private and are shared now: that can't be taken back, so a move of only such tasks has a notice and no undo.
The selection stays, as after Tag…; in a list's own view the moved tasks leave it.

#### A new list for the tasks

**New list…**, the last row, makes a list and moves the tasks into it, so the dialog is there with no list too (then Inbox, ticked, and New list…).
The rows give way to the list's name (`.bulk-list-form`, `#bulk-list-name`), **Create and move** (or Enter) and **Back**; a name that won't do is said under it and the dialog stays ("There is a list #shop already").
With users it asks **Private: only you see it and its tasks** (`#bulk-list-private`): off and not to be ticked while a shared task is selected, ticked when every selected task is private, so no task is shared by not looking; unticked with a private task selected, a line says that everyone will see it and that it can't be made private again.
The list is made as New list… on the Tasks page makes it (`POST /api/tasks/lists`, `apiNewList`: `createListOp`), pinned, last, in a commit of its own before the move's, and the page stays: every task list on it knows the new list at once (the `zone:list-made` event, `tasks.js`), and on `/tasks` the list switcher (the row and the left column) is drawn again with it (`js/listsrow.js`).
The toast says "3 tasks moved to the new list “shop”"; its undo takes the tasks back and leaves the list, which **Delete…** removes.

### A list on screen

A list is marked as one wherever it shows: a list icon (an SVG mask in CSS `::before`, `--list-icon`, in the accent colour), a faint blue tint and a tooltip (`listTooltip`: "List", "List (private: only you see it)", "List for some: only … see it and its tasks"; the Inbox's "In your Inbox: only you see it", without users "In the Inbox: in no list").
A card shows its list as such a chip before its tags (`.task-tag-list`), but in a view of that one list and By list, where the section says it.
In a list's view the filter field holds a chip without × for it (`list:<name>`, shown as its name; `chipField`'s `locked`, `.tag-chip-locked`; Backspace skips it), so the page always shows what it says; an embed's `tag=` and `assignee=` words are locked the same way, and a URL `?tag=` stays removable.

#### When a task leaves the view

A save that takes a task out of view says where it went: "Saved to list “…”." when it left for a list (`listMovedNotice`), else "Saved; it no longer shows here." (`leftViewMessage`).
Both are undo toasts (`offerUndoLeave`): undo PUTs back everything the save could change (`editSnapshot`: fields, list, order, recurrence and the old history, so no trace; not the list of a task that was private, which may have been shared by the save) and reloads the tasks, since the task left `tasks`.
A new task created out of view only gets the notice.

#### A list's backlog

Under a shared list's heading (with users), **N unassigned** (`.tasks-unassigned`; `tasksPageView.Unassigned`; its tooltip "The tasks of this list that no one has: take one with a, Enter") links to the list filtered by `@none` (`/tasks?list=<ref>&assignee=none`): the open tasks no one has, a former assignee's included.
A list left out of My tasks says so there too ("Left out of My tasks", a link).

## A task's files

With users, a file pasted, dropped or attached in a task's dialog (its description, a comment) is the task's: kept in `_tasks/files/<task id>/_assets/`, at `/_tasks/files/<id>/_assets/<file>`, and **seen by who sees the task** (`taskPlace`, `seesTaskFiles` in `go/access.go`; `serveTaskUpload`), 404 for anyone else as for a file that isn't there.
Nothing moves with a task: a private task's file is its owner's alone, shared with the task it is everyone's or its list's members', and a task that goes to another list, or whose list is narrowed, widened or renamed, takes its files along by being the same task.
Without users a task's file goes to `_assets/` like any other.
A file attached before task files were kept apart (in `_assets/`, or in `private/<you>/_assets/` for a private task) stays where it is.

The dialog sends the task's id with an upload (`task`, `markdownEditor`'s `task` option); for a new task it makes one up (a version 4 UUID) and creates the task with it (`id` in `POST /api/tasks`: only an id of that form that no task has), so the files pasted before the first Save are the task's from the start.
Until there is a task of that id (the dialog is open), its files are who put the first one there's alone, as their private place is (`claimUnsaved`, `unsavedBy`: a record in `.zone/unsaved-files.json`, out of git, forgotten after 30 days, as of a dialog closed without a Save): nobody else opens them, adds a file or replaces one, and the history names them to nobody else.
Made, the task's readers see them; created with an id whose folder is another's, or a deleted task's, the task is refused ("There is a task with this id already").
Once there is no task of that id any more (deleted for good; the files stay, for the undo), they are no one's, its maker's neither, until the undo brings the task back.
Never a visitor's.
A task's id in another case is no task's (`Find` matches it, `seesTaskFiles` refuses it): on a file system that ignores case (macOS, Windows) it would name the task's folder, so nobody reads or puts a file by it, and no task restored or imported takes an id another task has in any case (`IDsLocked`).
An assistant's `upload_file` takes a task's id as `page`.
A file's history is its task's readers' (`canSeePath`).

## Renaming a tag

**Rename tag…** in the ⋯ menu of `/tasks?tag=<tag>` (`#tag-rename-dialog`; `POST /tasks/rename`; the MCP tool `rename_task_tag`; `renameTagOp` in `go/pageops.go`) changes in one commit the tag on every task (open, closed, archived) and, on pages and in tasks' text, the `tag=` and `new=` of `[[tasks: …]]` embeds and the `?tag=` of links to `/tasks`.
A list is no tag: one of that name, its embeds and its links stay.
Code stays as written, and so do a `#word` in prose and the `tags:` in pages' frontmatter, which sort pages rather than tasks.
Case doesn't matter in matching: `Shop` and `shop` are one tag, and renaming a tag to its own name evens out how it is spelled.

A name already in use (a task you see has that tag) merges the two, so it asks first: the dialog comes back saying so with a **Merge** button (editing the name takes that back), and the MCP tool wants `merge: true`.
A task with both tags ends up with one.

Refused, with nothing changed: a tag you don't see anywhere, an empty name, a name with `[ ] , | =` (they mean something in an embed), or a tasks file or page that can't be read.
Text not yet in git is committed first (`keepPendingPages`).

With users a tag on anything shared (a shared task, an embed on a shared page) is one name for everyone, so the rename reaches every user's private tasks and pages too, as a page rename's links do.
A tag only you see stays yours: another user's private tag of that name is another tag and isn't touched.
A tag only someone else has is refused as if it didn't exist, a new name only they use is no merge for you, and the answer counts only the tasks and pages you see.

## Touch

On a phone there is no hover, Shift or Ctrl: a long press selects or drags, a swipe plans, and targets are finger-sized.

### Touch targets

With a mouse (`hover: hover`) ⏭ shows only on hover, for the selected card, or with focus inside, left of the dates (right-click opens the menu too);
under `(hover: none)` ⏭ is hidden (the ⋯ menu has it);
⋯ is always shown.
Under `(pointer: coarse)` buttons,
checkboxes and chips get finger-sized targets (a checkbox's label reaches into the card's padding and the gap before the title, without moving the box, so a tap just beside it ticks the task instead of selecting the card; a press on the label never selects, with a mouse either),
fields use 16px text (no iOS zoom),
and cards that can be dragged can't be text-selected, nor give iOS's long-press callout (`.task-item[data-drag]`).

### Selection mode

On a phone, there being no Shift or Ctrl, a **long press** on a card let go without moving, or ⋯ → **Select several** (which works with a mouse too), starts selection mode (`selectMode`, `.tasks-selecting`) with that card selected, and shows the selection bar even with one task or none ("Tap tasks to select", the actions disabled).
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

### Long-press drag

350 ms without moving over 10px picks a card up and selects it (without scrolling).
A floating copy (`.task-touch-ghost`) follows the finger, the page auto-scrolls near the edges, and release drops through the same code as a mouse drop (`touchDrop` adapters registered by each render; list touch listeners attached once).
A touch `pointerdown` sets the card's `draggable` to false (mouse sets it back; cards are marked `data-drag`), and native `dragstart` is cancelled for touch, because Android Chrome and iOS Safari otherwise start their own HTML5 drag mid-gesture.
Moving over 10px earlier is no long press: up or down a normal scroll, sideways a swipe where the card has one (Swipes) and nothing where it hasn't; a finished drag isn't a tap.
Let go without having moved over 10px, the long press starts selection mode with that card instead of dropping it; where nothing drags (Closed, or a view without dragging) the press only selects the card (`holdCard`), letting go starts selection mode, and moving over 10px first cancels it.
The browser's own long-press menu is suppressed on a card while a touch gesture runs.

### Swipes

Swipes (`swipeActions`; they may start on a date chip, which sits on the first line where a thumb starts, a mere tap still opening its picker) only move tasks in and out of Today:

| Swipe | Does | Where |
|-------|------|-------|
| Right | Start today (on Plan also a carried-over task, committing to it again) | Open, the board |
| Left | Clear the start date | Today, the board (by day, person or list) |

No action, no movement (e.g. right on a Today task, left on an unplanned or due task).
A mostly sideways move (|dx| > 1.5·|dy|, over 10px) drags the card and reveals the label (`data-swipe-label`); past `min(110px, 30%)` it arms (`.task-swipe-armed`), and release starts the task through `startOn` (A start after the deadline), with its undo toast.
Under `(pointer: coarse)` cards have `touch-action: pan-y`; `.tasks-list` has `overflow-x: clip`.

## Keyboard shortcuts

| Keys | What they do |
|------|--------------|
| **g a** | Go to `/tasks`, all tasks |
| **g t** | Go where the Tasks link goes (My tasks, or the view you were on last) |
| **c t** | A new task in your Inbox, starting today: from any page and the same on every one (Modules → New task from anywhere in `docs/spec/navigation.md`) |
| **n** | The list's own + New |
| **j** / **↓**, **k** / **↑** | Select the next / previous task (from the last back to the first) |
| **Shift+j/k**, **Shift+↑/↓** | Take the tasks passed along (Selecting several tasks) |
| **Shift+click**, **Ctrl/Cmd+click** | Select several tasks: a range, one more (Selecting several tasks) |
| **Ctrl/Cmd+A** | With a task selected: every task shown (Selecting several tasks) |
| **Esc** | Collapse the expanded task first, then clear the selection (in selection mode: end it, Touch) |
| **Enter** | Edit the selected task |
| **Space** | Complete it, or reopen a closed one (on several selected, all of them) |
| **Delete** | Close the selected open tasks as won't do (a task is never deleted); with Ctrl/⌘ it is the page's Delete page, and the tasks stay as they are |
| **→** / **l**, **←** / **h** | With one task selected that has a description or comments: open / close them; open, they stay open as j/k move on (Expanding a task) |
| **f** / **F** | Start the selected tasks today / tomorrow, on every tab |
| **1**–**7**, **0** | Start them on that day of the Plan board (1 today, 2 tomorrow …), unplan them; on every tab |
| **i** | The importance of the selected task, in turn high, low, normal; several selected all get what follows the one selected last (Importance) |
| **t** / **T** | The selected tasks' tags: add, remove (Tags on several tasks) |
| **m** | Move the selected tasks to a list |
| **a** | With users: assign the selected tasks (Assigning tasks in `docs/spec/users.md`) |
| **Shift+F10** / menu key | The selected task's ⋯ menu (on several selected tasks, the selection's) |
| **Ctrl+Shift+↑/↓** (or **Ctrl+Shift+J/K**) | Move the selected tasks within the manual order (in a sort, a notice says to press **o** until Manual; Sections) |
| **Ctrl+Z** | Undo what was just done to a task, while its notice shows (not in the edit modal, the deadline dialog, a text area or a text field such as the filter, where Ctrl+Z undoes the typing) |
| **o** | On the Open tab: the order, manual, by deadline, newest first (Sorting) |
| **d** | On the Open tab and on Today: how it shows its tasks, in turn: one list, Plan, by person, by list, as far as the view offers them (The tabs and the Open tab's control) |
| **Tab** / **Shift+Tab** | The next / previous tab (on the Tasks page, or on another page while a task is selected or the focus is in the list; Closed is not in the cycle) |
| **/** | The filter field (Esc leaves it) |
| **#** | The filter field, starting a tag |
| **@** | With users: the filter field, starting an assignee's name |
| In a suggestion list: **↑**/**↓**, **Tab**/**Enter**, **Esc** | Pick, take, close (Tag completion) |
| In a chip field: **Backspace** at its start | Remove the last chip (Tag chips) |
| In a menu or a list dialog: **↑**/**↓**, **Enter**, **Esc** | Go through the rows, pick, close |
| In the edit modal: **Ctrl+Enter** or **Ctrl+S** (**Cmd** on a Mac) | Save the task (Ctrl+Enter in the comment field saves the comment) |
| In the edit modal: **Alt+C** | Go to the comment field (a task already made) |
| In the edit modal: **F2** | Switch the description between Edit and Preview |
| In the edit modal: **Enter** in Tags or Assignee | Make the typed word a chip rather than saving |
| In the edit modal: **Tab** | The next field (Tab order in the dialog) |
| In the edit modal: **Esc** | Cancel: close without saving |

The shortcuts dialog (`?`, The shortcuts dialog in `docs/spec/navigation.md`) lists these keys in four groups (select, act on the selected, plan the selected, view) and the edit modal's in a fifth; a key added here goes there too.

The tasks' keydown runs before the page's shortcuts, so while a `g`/`c` prefix waits for its second key (`keyPrefix` in `js/util.js`) the tasks leaves that key alone: the t of **c t** or **g t** never opens the bulk tag dialog on a selected task.

## How it works

What the task app is built on: its code, how a page brings its tasks and keeps them current, which tasks a view takes, the lists' registry, the files, and what Zone takes over from an older wiki when it starts.

### The code

Server side is `go/tasksapi.go` (the `/api/tasks/*` routes, `tasksRoutes`),
`go/tasks.go` (the writes, which the MCP tools share) and `go/tasksembed.go` (the embed's HTML, `taskCounts`) on top of `go/internal/taskfiles` (the files: where they are, read and written under one lock), `go/internal/taskmodel` (a task and its file's format) and `go/tasklogic.go` (recurrence, completing, history, comments, task backlinks) and `go/tasksyntax.go` (the syntax in page text).
Client side is `tasks.js` (DOM, one instance per embed, its parts in `js/task*.js`: The task app's modules in `docs/spec/rendering.md`) plus `tasks-core.js` (pure logic, tested under Node).

### The embed's HTML

`go/tasksyntax.go` holds the syntax, `go/tasksembed.go` the HTML: `tasksRE` finds the directive and `parseTasksParams()` parses its `key=value` pairs.
`processTasksEmbeds()` swaps each one for a numbered placeholder before rendering, and `injectTasksEmbeds()` replaces each `<p>PLACEHOLDER</p>` with `buildTasksEmbed()`'s HTML (`templates/tasks_embed.html`) afterwards.
The embed passes its params as `data-init-*` attributes; it carries no script: `tasks.js` (an ES module `zonewiki.js` imports on every page) starts every `.tasks-app` on load (`initTaskApps`, marking each `data-ready`).
Element ids get a `-<n>` suffix after the first embed, so a page can hold several.

**Headless instance**: `buildTasksEmbed(…, headless)` renders the app hidden, for pages that only need its edit modal (the editor's Make task, Task references in `docs/spec/rendering.md`).
`openEditModal(null, opts)` takes, besides `tags`, a starting `title`, `description`, `start_date` and an `onCreated(task)` callback; `titles` instead of `title` is batch mode (one task per title, the fields shared, `onCreated` gets the array).

### A page brings its tasks along

A page view with a task list (and `/tasks`) carries, once, the open tasks its lists show (`scopedTasks`: those of any of the lists' scopes, so My tasks or one list doesn't carry every open task the user sees; a list of every task, All tasks or a tag's view, takes them all) as `GET /api/tasks` answers them
and their notes' and comments' HTML as `POST /api/render-notes` would (`tasksInitial`: a `<script type="application/json" class="tasks-initial">` data block, which no browser runs; `<`, `>` and `&` in it are `\u` escapes, so no task's text ends it).
The block is the first child of the page's first list's own element (`.tasks-app`), where `js/taskdata.js` looks for it: not by an id, which a page's raw HTML could give one of its elements first, handing the lists tasks of its own whose notes' "rendered HTML" would go into the cards as it is; and a page's raw HTML makes no `<script>` (Raw HTML in `docs/spec/rendering.md`).
**Up to 150 tasks** (`lazyTextsFrom`): a page whose lists show more brings none of them, and each list asks for its tasks when it starts, as below.

A team's All tasks is megabytes of JSON; a page is sent as it is, while **what `GET /api/tasks…` answers goes gzipped** to a browser that takes it (`gzipTasks`, `writeJSON` in `go/server.go`: an answer of 1 KB or more).
Only those answers: they hold the user's text and nothing secret beside it (no token, no form's field), which is what compressing the two together would give away; pages go uncompressed.

The lists the page starts with draw from it, each from its own copy, with no request (`initialTasks` in `js/taskdata.js`, `keepRendered` in `js/tasktexts.js`), so the list is there when the page is rather than two round trips later.
Every later load asks the server for the list's scope (`GET /api/tasks?list=`, `?lists=`, `?mine=1`: `scopeQuery`; the server's rule is the one `inScope` has in the browser), and so does a list that starts later (a preview's, the hidden one a page without a list fetches for its dialog): `initTaskApps` forgets the tasks once the page's lists have started.

### Keeping lists current

Several embeds per page work: each instance keeps its own task array, and a write in one refreshes the others (`refreshOtherInstances`).
Every instance also reloads on `visibilitychange` and bfcache `pageshow`, so a tab left open for days shows current tasks and today's day sections.
`refreshIfIdle` skips the reload while a drag, swipe, completion grace period, ⋯ menu or edit modal is in progress.

**What someone else changed shows within a minute**, without fetching tasks to find out: the server has a word for the tasks a page holds (`tasksWord`, `Store.Word`: the start's time and a count of the writes to those lists' files and to the registries the user reads; not a file changed by hand), which `GET /api/tasks` and `GET /api/tasks/due-count` answer in the `Zone-Tasks` header and a page's own tasks bring along (`"word"` in `tasksInitial`).
It says nothing of what changed in those lists.

**It is of what the page holds**: a page whose lists each show one list (or an Inbox) names them (`?held=a,b`, `pageScopes` in `js/chrome.js`), and a write to another list, or to another user's own tasks, leaves its word as it is; a page that holds more (My tasks, All tasks, a tag's view) has the word of every list its user sees.
So among a hundred people a list isn't loaded again all day for writes it doesn't show.

The page keeps the word of the tasks it holds (`<body data-tasks-word>`, `js/taskdata.js`); the due count it asks for every minute while it is used (`refreshDueBadge`, `whileUsed`) says the server's, and when that is another one the lists load again (`zone:tasks-changed`, `refreshIfIdle`; a list that was busy is told again the next minute).
After the page's own write the word is taken as its own.

**A long list of every task loads again at most every five minutes** (All tasks or a tag's view with more than 150 tasks; `EVERY_MS`, `holdsEvery` in `tasks.js`): in a team some task changes every minute, and such a list would load all of them again each time.
It is told once and loads when five minutes have passed since it last did; coming back to the browser tab, and the page's own writes, load it at once, as any list.

### Scopes

Which tasks a view takes (`inScope`, `paramsScope`; `inScope` in `tasks.js`):

| View | Takes |
|------|-------|
| a list (`list=<name>`) | the tasks in that list |
| the Inbox (`list=inbox`) | those in none |
| My tasks (`mine=1`, `mineScope`) | what its rule says (My tasks) |
| several lists as one (`lists=a\|b`, which the routes still take) | those in any of them |
| the **main task list** (an embed with neither, including `/tasks`, URL `?tag=` notwithstanding), a `tag=` embed, the main Closed count | every task |

A task tagged `sam` belongs on a Sam page whatever list it is in, so a `tag=` embed takes every task.
`scopeFilter` gives a scope's tasks for a user.
The due count is My tasks'.

### The registry

The lists are in `lists.json` beside the task files (`go/internal/taskfiles/lists.go`; `ListsName`), the shared ones in `_tasks/lists.json`, a user's private ones in `private/<user>/_tasks/lists.json`, read and written under the task files' lock, atomically, committed to git.
A list for some has its entry in its own folder instead (Where a list for some is kept).

```json
{
  "version": 3,
  "lists": [
    {"name": "shop", "view": "today", "limit": "8", "keep": "1", "labels": "Catalogue,Shopping", "home": "off"},
    {"name": "task"}
  ],
  "pinned": ["task", "shop"],
  "mine": {"view": "today"},
  "all": {"view": "open"},
  "order": ["0b7c…", "9e21…"]
}
```

Each list is one line: its name and its settings (`view`, `limit`, `group`, `keep`, `labels`, `new`, `noshare`, `home`), with `owners` where it has them (a list for some's `for` is in its own folder), their values text as an embed writes them, a setting that isn't set no key.
So a list can be added or changed by hand in one line, and a commit shows the list that changed.

| Key | Holds |
|-----|-------|
| `version` | That the wiki's lists are kept here: none, a wiki to take over (The lists' migration); `1`, a wiki from before the Inbox (The Inbox's migration); the current one is 3. Read only in a wiki without `.zone-format` |
| `lists` | The lists, a line each |
| `pinned` | A user's pins in theirs; without users the wiki's in the shared one (Pins) |
| `mine` | The settings of My tasks (a user's own; without users the shared one's) |
| `all` | The settings of All tasks (the shared one) |
| `order` | The manual order of My tasks, as task ids (the same owner as `mine`) |

`"mine": true` (an older registry's) reads as no settings and is written back no more.
It is read leniently (a value of another kind as text, a list without a name left out, a name twice once, unknown keys kept and written back A–Z) and written in one format; a file that is there but unreadable refuses every write (`ReadListsStrict`: never an empty registry, which would drop every list).
A registry keeps the version it has when it is written; a new one gets the current one.

### Storage

The tasks are plain JSON files in the wiki, committed to git; this is where they are and how Zone reads and writes them.

#### The task files

- `_tasks/lists.json` is the lists' registry: the shared lists with their settings, the pins (without users) and the settings of All tasks; with users each user's own in `private/<user>/_tasks/lists.json` (their private lists, their pins, their settings of My tasks). It is no tasks file (The registry).
- The tasks' files live in the `_tasks/` folder (with users each user's private tasks in `private/<user>/_tasks/`, the same files: Private tasks' files, below): open tasks in `tasks.json`,
  closed ones in `closed.json` (flat JSON arrays, committed to git on change).
- **Each list has its files in a folder of its own**, `lists/<name>/` in its owner's `_tasks/` (`_tasks/lists/shop/tasks.json`, `closed.json`, `closed-YYYY-MM.json`; `dirOf` in `go/internal/taskfiles`); the tasks in no list (an Inbox's) are in `_tasks/` itself.
  A file is written whole and kept whole in git at every change, so a write costs what its list weighs, not what the wiki weighs (the bench, `tests/bench_tasks.py`: The bench in `docs/spec/testing.md`, measures it at a team's size; Packing the repository in `docs/spec/deployment.md` keeps the repository small).
- Which list a task is in is still the task's to say (its `list`); the folder is where a write files it.
  A read takes every folder of an owner (`dirs`), so a task found in another list's folder (a file moved by hand, a wiki from before the folders) is read all the same and filed where it belongs by the next write of its owner's tasks, or at start.
- A task that moves to another list moves between two files in one commit; the file that takes it is written first, so a write that stops halfway leaves it in two files, never in none, and one task in two files is one task to every reader (`unique`).
- A list's file left without a task is removed, and its folder with it; a list's rename files its tasks into the folder of the new name in the rename's commit (`FileByListLocked`).
  A list's name that is no folder's name (a dot first, a path separator: none a list can be given, but a file edited by hand may say so) has no folder: its tasks stay with those of no list.
- `atomicfile.Write` creates the folder, so a new wiki gets it on the first write.

#### Closed tasks by month

`closed.json` holds those closed in the current month (and any without a usable date), `closed-YYYY-MM.json` those of an earlier month (only such a name, and the older `closed-YYYY.json`, is an archive).
So completing a task writes a month's closed tasks, not a year's: a file is written whole and kept whole in git at every change, and in a large wiki a year's is megabytes.
Write routes read the closed files they need (Reading and writing) and `writeClosed` files each task by its completion month, writing only files whose tasks changed, and removing an archive left without a task; startup does the same (`ArchiveOldClosed`) for a month's end without writes.
So archived tasks can be edited, reopened and tagged like any other; Closed shows them by year (see Closed), reading that year's files.

#### Reading and writing

All mutations go through one lock (`taskFiles.Lock`, `go/internal/taskfiles`) and atomic writes (temp file + rename, `go/internal/atomicfile`); the server handles requests concurrently.
**A read waits for a write's files, never for its commit** (`Store.view`, `Hold`, `Release`): a write holds the lock from its read to its commit, one write at a time;
readers (`Open`, `All`, `Snapshot`, the Closed readers) don't take it.
They share a second lock, which a write holds alone from the first file it replaces to the last (every write of the store's takes it; code that writes a task file itself calls `Hold` first) and lets go before git runs (`CommitFiles`, `commitLists`; `Unlock` at the latest).
So a reader sees every file of a write or none (a task that moves between two files is never in neither), and a page view doesn't stand still while someone's task is committed, nor while a commit waits for the packing: every page view computes the Tasks links' counts.

**A write of tasks reads their own files** (`ReadTasksForWriteLocked`; for one task `ReadTaskForWriteLocked`, `findForWriteLocked`: a change, a completion, a comment, a ticked subtask; for several a batch, `batch-update`, `batch-complete`, `batch-tag`, and a delete): the open tasks and, for the closed ones among them, the closed tasks of the files that hold them (this month's files are looked through first, then the archives, the newest first, until each task is found; a task no file has makes it look through them all, without copying them).
It writes whole only the closed files it read; a file it has to put a task into (this month's, for one just completed) is read then, so nothing is written without what it held (`writeClosed`, the store's `scope`).
So a change doesn't read and copy every closed task of the wiki's years, and an archive that can't be read stops only the writes that need it.
While closed tasks wait to be filed (a month has ended, a year's file from before the months: `unfiled`), a write reads every closed file (`readClosedAllStrict`) and files them.
What needs every task reads everything still: an import and the undo of a delete (for the ids there are), an export, a list's rename or delete, a private list shared.

**A write asks once per list whether its user sees it** (`byOwner` in `go/internal/taskfiles`): a write hands over every open task its user sees, and whom a list is for is a look at its folder (`SeesList`, `forOf`: a stat), so the answer is kept for the write.

**A file whose tasks a write didn't touch costs a comparison** (`holds` in `go/internal/taskfiles`): the store knows each file's tasks as it parsed or wrote them (`writeTasks`), and a write compares what it would write with that (`Task.Same`) before it reads or encodes anything.
Changing an open task doesn't read or encode the closed files.

**A file is parsed once** (`readFile` in `go/internal/taskfiles`): every page view reads the open tasks (the Tasks links' count) and a write reads every file it needs, so a file's tasks are kept as parsed and handed out as copies (`Task.Clone`: a reader may change its own) until the file is another one (its time, size or inode: Zone's own writes, which also forget it, `writeText`; a hand edit).
Write paths use a strict reader that fails with `taskfiles.ErrCorrupt` (503) on an unreadable file instead of treating it as empty and wiping every task.

**An archive nobody read for ten minutes is no longer kept parsed** (`archiveKept`, `forgetOldArchives`); the open tasks and this month's closed ones stay.
The Closed link's number is counted by looking at what is parsed, without a copy of the year's tasks (`CountClosedInYear`, `lookAt`), and so are the closed tasks with news for a page's user (`RecentClosed`).

**The counts are kept per user until the files change** (`keptCounts` in `go/tasksembed.go`, `Store.Stamp`): the Tasks links' counts, which every page view shows and every open page asks for once a minute, and the list switcher's, by place.
They are counted anew when a task file or registry the user reads is another one (its time and size, so a change by hand counts, and Zone's own writes by their number, so two within one tick of the clock are two), when the day is another one, or the settings are.
A look at each file (`Stamp`), not a read.

#### What a start reads

A start reads every task file when it has to (`setUpTasks`, `tasksChecked` in `go/tasksetup.go`): filing closed tasks by month and tasks by list, writing a hand-edited file in Zone's format and giving a shared task its list all read and encode every task of the wiki's years, and a hosted wiki starts whenever someone comes back to it.
So a start leaves a mark in Zone's folder (`.zone/tasks-checked`: the layout, whether the wiki has users, and the commit the task files were last Zone's at, which follows Zone's own commits of them: `noteTaskCommit`), and the next start does none of that while git says that nothing else wrote a task file since: none differs from what is committed, and no commit after the mark's touches one.
A file edited by hand, a commit by hand or pulled from elsewhere, users turned on or off, no git or no mark: it reads them all.
What a month's end leaves to file is seen without that (`Unfiled`: the files' names and a look at this month's closed files) and done.

#### Private tasks' files

With users, a user's private tasks (Private tasks in `docs/spec/users.md`) are kept apart from the shared ones, and nothing reads them for anyone else.

Shared tasks are in `_tasks/` (`tasks.json`, `closed.json`, `closed-YYYY-MM.json`, each list's in its folder `lists/<name>/`: The task files, above); a user's private ones are in the same files and folders in `private/<user>/_tasks/` (`taskFiles.Dir(owner)`), created on their first private task, beside their lists' registry (`lists.json`: their private lists and their pins).
A task's file says whose it is: the task's `Owner` (`go/internal/taskmodel`) is not written into it, it is set from the folder a task was read from (`eachOwner`), and the API shows it as `"private": true|false` (only with users: without them the API's JSON has no such key).
A write that puts a task in a list sets where it is kept (`applyList` in `go/tasks.go`), and the task moves between the files in one commit.
Ids are random UUIDs, unique across all files; restoring a deleted task (undo) never takes an id another file has (`taskFiles.IDsLocked`) and puts it back where it was (`placeSnapshot`, Private tasks in `docs/spec/users.md`).

Every reader takes the user and reads the shared files and theirs (`go/internal/taskfiles`, the store `taskFiles` in `go/tasklogic.go`; `Owners(user)`: `Open`, `All`, `Snapshot`, `ReadForWriteLocked`, `ClosedInYear`, `ClosedYears`, and on top of them `findTasks`, `resolveTaskRefs`, `findTaskBacklinks`);
Zone itself (`""`) reads only the shared ones.
A write splits the tasks by owner (`byOwner`) and writes the files that changed, all in one commit; a task can only be written by its own user, since another user's is never read for them.
One lock (`taskFiles.Lock`) for all files' writes, and a second one that readers share and a write holds while it replaces its files (`Open` for `GET /api/tasks`, `All`, `Snapshot`; Reading and writing, above): a task that is shared moves between two files, and a read between their writes would miss it.
What Zone does over every task list covers every user's files (`EveryOwner`): the closed tasks' filing by month at start (`ArchiveOldClosed`), the lists' migration, a rename's links in tasks (`renamePageRefs`), a shared list's rename, the search index (`Every`, filtered per user by `canSeeTask`, `hiddenDoc`).

#### The files' format

The files are JSON (`go/internal/taskmodel`): a list of tasks,
two spaces of indent,
text as written (no `\u` escapes but those JSON needs),
each task's fields in one order (`id`, `title`, `description`, `tags`, `list` (only when it is in one), `assignee` (only when it has one), `created_by` (only when it says), `followers` (only when it has some), `start_date`, `due_date`, `created_at`, `closed_at`, `wont_do`, `keep`, `importance` (only when it isn't normal), `last_done`, `sort_order`, `recurrence`, `spawned_from`, `history`, `comments`),
every field written,
also when empty (`null`, `[]`, `false`),
then any keys Zone doesn't know (from an older version or a hand edit),
A–Z,
their values kept as they were.
A file is read leniently: a value of the wrong kind is its field's default (a number or a bool where text belongs is its JSON text, so an `id` of `7` is `"7"`; only `true` is true), so an odd file still reads and a write never refuses it for that.
A write changes only the files whose tasks changed, so commits show only what changed.
Files in another format (hand-edited: keys in another order, unknown keys not A–Z) are rewritten once when Zone starts, in a commit of their own ("Write the task files in Zone's format"); the tasks' values stay as they were (`1.0` reads `1`).

Request bodies are validated (`jsonObject`, `asStr`, `asInt`, `asStrList`, `idList`): bad input is a 400 with JSON `{ error }`, never a 500.
Numeric strings are accepted for integers; `tags` may be a comma-separated string.
Reads clean the tags (`jsonobj.TextList`, `taskmodel.CleanTags`), since a file edited by hand may have `"local llm"` or `#work`.

### Migrations at start

A wiki from an older Zone is taken over when Zone starts, once, each step in a commit of its own; a second start changes nothing.
These steps are from before `.zone-format`, the file that says which format the wiki's files are in (The data format in `docs/spec/deployment.md`):
the first three, the task files', run whenever the task files may have changed (`tasks-checked`), since a file edited by hand or pulled from elsewhere can need them at any time;
the last three, the lists' and the views', run for a wiki without that file, each looking at the registries for whether it is needed, and a start writes the file after them, so a wiki that has it is past them.
A step after them raises the number in `.zone-format`, in its own commit.
Going back to the older Zone means reverting the step's commit in the wiki's repository;
going back to a Zone from before `.zone-format`, revert the commit that wrote it too ("Note the format of the wiki's files"), or a later start finds it and leaves out the step the revert took back.

| Step | Takes over | Commit |
|------|------------|--------|
| Closed tasks' old name | `completed*.json` files and `completed_at` | "Name the task files of closed tasks closed" |
| Tasks filed by list | every task of an owner in one `tasks.json` | "File the tasks by list" |
| Closed tasks by year | closed tasks filed by year | "Archive closed tasks" |
| The lists' migration | lists that pages declared, pins in embeds | "Make task lists their own" |
| The Inbox's migration | registries from before the Inbox | "Give every task a list: the Inbox" |
| The views' names | `view=focus\|open\|plan`, `plan=on\|off` | "Name the tasks' views Today and Open" |

#### Closed tasks' old name

The view of closed tasks was called Completed: a wiki from then is renamed when Zone starts (`renameOldClosed`: `completed.json` and `completed-YYYY.json` become `closed.json` and `closed-YYYY.json`, never over a file that is there; a task's `completed_at` is read where it has no `closed_at`, and the files are written again with the new field), in one commit, "Name the task files of closed tasks closed".
When the same start renames files, files tasks by list and rolls months into archives, that message names the commit.

#### Tasks filed by list

A wiki whose owners had every task in `_tasks/tasks.json` and the closed files beside it is filed by list at start, in one commit, "File the tasks by list" (`ArchiveOldClosed`, which also rolls months into archives then).
Going back to an older Zone: revert that commit, or it reads only the tasks in no list.

#### Closed tasks by year

Closed tasks filed by year, `closed-YYYY.json`, go to their months' files at the first start, and the year's file is removed, in the commit "Archive closed tasks", or in "File the tasks by list" when the same start files tasks by list.
A month that ends is rolled into its archive the same way at the next start without writes.

#### The lists' migration

When Zone starts on a wiki without `.zone-format` and no registry, the shared one or a user's, has a `version` (`migrateLists` in `go/listsmigrate.go`), a wiki from before lists were places is taken over, in one commit ("Make task lists their own"), under the page and task locks.
There a list was a tag a page declared with `[[tasks: list=<tag>]]`: a task could be in two, a list ended with its page, and a task moved by a change of its tags.
It changes nobody's view of who sees what: no task changes its file.

1. **The lists**: each name a shared page declared (`[[tasks: list=<name>]]` outside code) becomes a shared list, with its first page's (A–Z) params as its settings (the list settings: `view`, `group`, `limit`, `labels`, `new`, `keep`, `home`; `noshare` when that page was not for sharing).
   A name a user's private page declared becomes a private list of theirs, unless a shared page declares it too and they have no private task with that tag: their embed then shows the shared list.
   Another page's embed that set a list's settings differently loses that difference: the params stay in its text and mean nothing.
2. **The tasks**, in every file of every owner (open, closed, archived): the first A–Z of a task's tags that names a list of its own kind (a shared task: the shared lists; a private task: its user's) becomes its `list`, and that tag goes.
   Any other list's tag stays, as a plain tag, so the task still shows in a `tag=` view.
   A private task without a list of its user's (it carried a shared list's tag, or none) is in their Inbox.
   A shared task carrying the tag of someone's private list keeps the tag and is in no list.
3. **The pins**: `pin=1` and `pin=default` on shared pages become the wiki's (`pinned` in the shared registry), the default one first, then by name; those on a user's private pages theirs, followed by the wiki's.
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

#### The Inbox's migration

After the lists' migration and under its locks (`migrateInboxLocked` in `go/listsmigrate.go`; one commit, "Give every task a list: the Inbox"), in a wiki without `.zone-format`, once for each registry that says `version` 1 (lists as places, from before the Inbox), the shared one and each user's own, which then says the current version (each for itself: a wiki whose users have lists and that never had a shared one has no shared registry).
Before the Inbox a task could be in no list, which with users was everyone's; a pin also put a list's tasks in My tasks, and the first pinned list took new tasks (with nothing pinned they went to a private list `private`, which counted as pinned).

1. A list called `inbox` is called `inbox-list` (its tasks and pins follow; embeds that name it show the Inbox from then on).
2. With users, a user's list `private` that has no settings and took their new tasks (they pinned it, or pinned nothing) becomes their **Inbox**: its tasks lose their list, the list and its pin go. Zone made that list.
3. **What wasn't in My tasks stays out of it**: the lists a user hadn't pinned are left out (`home=off`): without users any such list, with users each user's private ones.
   A shared list's unassigned tasks, which a pin put in My tasks, are in the list alone now: that is the change.

With users, **a shared task without a list goes to the shared list `general`**, made for it and pinned for the wiki, at a start that reads the task files (What a start reads: not when they are as Zone left them with users on).
That takes over the tasks that were in no list, and a wiki whose users are turned on later (its one Inbox becomes `general`).
A task file or registry that can't be read stops it before anything is written, as above (`listsUnmigrated`).

#### The views' names

The views had four names, `view=focus|open|plan`, where `open` and `plan` also said how Open showed its tasks, beside `group=`, and `plan=on|off` from before that.
A wiki from then is taken over once, when Zone starts (`migrateViewsLocked` in `go/viewsmigrate.go`, after the lists' migrations; one commit, "Name the tasks' views Today and Open"): each `[[tasks: …]]` on a page and each registry's settings are rewritten so that every view shows what it showed
(`view=focus` is `view=today`;
`view=plan` is `view=open group=day`;
`view=open`, which was the one list, is `view=open group=none`;
`plan=on|off` is `group=day|none`;
of three `labels` the first and the last stay),
and the registries say `version` 3;
a wiki without a registry has `.zone-format` to say it, which the start writes after this step.
From then on the old names are no view's: a link with `view=focus` or `view=plan` opens the default tab.

#### Older formats

Older data formats are no longer converted: the conversions (task files in the wiki's root,
closed tasks in `tasks.json`,
`focus_date`/`defer_days`/`recurrence.anchor`/`page`/`focus_order`,
spawns without `spawned_from`) were removed in 2026-10.
A wiki last used before then must be opened once with an earlier Zone (git history) first.
