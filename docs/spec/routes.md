# Routes and page views

Part of Zone's spec (see `AGENTS.md` for the index).

Every route the server answers, by area (pages, notes, search, history, uploads, private and group pages, accounts, assistants, the tasks page), the task API on its own, what a page view shows, and the side columns a page gets on big screens.

## Routes

Every route wants a session where the wiki has a login, but those that need none (`authExempt`): `/login`, `/logout`, `/setup`, `/signup`, `/welcome/<token>`, `/public/…`, `/api/health`, `/mcp` (its own token) and an MCP client's OAuth calls.
In a public wiki (`ZONE_PUBLIC`; Public wikis in `docs/spec/security.md`) a visitor reaches the ones listed there without one: pages, the page list, search, history, uploads, and with `edit` the editor and a new page.

### Slugs that name no page

A `<slug>` in a folder starting with `_` or `.` (`_assets/`, `_tasks/`, `.zone/`, `.git/`, `a/_drafts/` …) names no page: `safePath()` refuses it (also after `..`), so no route reads or writes a `.md` file there, even by direct URL (400, or 404 for the page preview).
These are the folders `iterPages()` skips.
Nor does a slug name a page through a symlink (a linked file or folder: 400), or with a `\` or a folder or page name ending in `.` or a space, which some file systems read as another name (A page is the file its name says in `docs/spec/security.md`); `iterPages()` lists none of them.
A file name starting with `_` (`_notes`, `a/_notes`) is still a page.

### Pages and the editor

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/`                   | Redirects to `/page/index`                      |
| GET    | `/page/<slug>`        | Render a wiki page (markdown → HTML); what it shows: The page view, below |
| GET    | `/pages`              | Redirects to `/` (browsing lives in the search modal) |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `<slug>.md`               |
| POST   | `/api/save/<slug>`    | Write page content to disk silently (no git commit, no redirect); used by auto-save; its answers below |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox in a page (`idx` param, with its line's `key`); see Ticking a checkbox, below |
| POST   | `/api/preview`        | Render posted markdown to HTML (for live editor preview); `?page=` the page being edited (your own private page: its links to new shared pages are marked) |
| GET    | `/new`                | A form for a new page's name and, with users, whom it is for (New page on a group's home and on the private home lead here; `for=group:<name>` or `for=private`: where For starts) |
| POST   | `/new`                | Create `<slug>.md` and redirect to it; `private=1` or `for=private` (with users): in your private folder; `for=group:<name>`: in the folder of a group you are in |
| POST   | `/link-new`           | A new page a link on a private page or a group's asks for, made in that folder instead of shared (`linkNewOp`); see below |
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug; a name in another place is asked first (below) |
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |

#### Saving silently

`POST /api/save/<slug>` returns 200 `{ file_hash }`; for section edits `{ file_hash, start_char, end_char }` with updated anchors.
A clean merge with another's change (`merge=1`) adds `merged`, `by` and the merged `content`.
A conflict is 409 `{ error: "file_changed", file_hash, by, conflict }` (Merge on save in `docs/spec/editor.md`).

#### Ticking a checkbox

`POST /api/toggle-checkbox/<slug>` finds the item by its line's `key` when the page changed since.
It returns 200 `{ file_hash }`, or 409 `{ error: "page_changed" }` when the line is no longer there once (Task list checkboxes in `docs/spec/editor.md`).

#### A new page from a private page's link

`POST /link-new` takes `slug` (the new page) and `on` (the page the link is on).
It points the link there and redirects to the editor of the page in the folder.

#### Renaming into another place

A name in another place (who sees the page changes) comes back with `rename_ask` until `confirmed` is that name (`confirmed=share` from Share…, for more people only).

### Note routes

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/notes`              | Auto-generated index of all notes, newest first, with a filter box (`?q=`) that searches notes only |
| POST   | `/api/notes`          | Create a new note (`<timestamp>_<slug>.md`); returns `{ slug }`. |
| POST   | `/promote/<slug>`     | Move a note to the wiki (see Notes → Move to wiki): fields `folder`, `name`, `keep_date`, `link_from`; redirects to the new page, or back with `?promote_error=…` |
| POST   | `/append/<slug>`      | Append a note to a page as a section (see Notes → Append to page): fields `page`, `heading`; redirects to the section, or back with `?append_error=…` |

### Search and the page list

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/search?q=...`       | Full-text search results page (up to 50, with a count; `&unlinked=1` as for the API) |
| GET    | `/api/search?q=...`   | JSON search results (used by the search UI); its parameters below |
| GET    | `/api/page-preview/<slug>` | `{ title, html }`: a page rendered for the search modal's preview pane (heading ids as `data-pid`, no task list embeds) |
| GET    | `/api/pages`          | JSON list of all existing page slugs (for wikilink hints) |
| GET    | `/api/pages-list`     | The search modal's page list: each page the user sees, plus the built-in entries; not for a visitor (Page List in `docs/spec/navigation.md`) |

`/api/search` takes `&unlinked=1`: only pages nothing links to, no tasks;
`&notes=1`: only notes (Notes page's filter; then `limit` goes up to 1000);
`&limit=N` (default 15, max 100).

`/api/pages-list` gives each page with `slug`, `title`, `links` (backlinks), `private`, `group`, `mtime`, `created`, `edited`, plus the built-in entries (Changes, Deleted Pages with git; Tasks, Notes).

### History and changes

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/history/<slug>`     | A page's saved versions, newest first (only when git is enabled; see Version history) |
| GET    | `/revision/<slug>/<sha>` | A page as one save left it (read-only), with ← Older / Newer → and Restore |
| GET    | `/diff/<slug>/<sha>`  | What one save changed in a page |
| POST   | `/restore/<slug>/<sha>` | Restore a page to a prior version; creates a new commit |
| GET    | `/changes`            | Changes: the wiki's commits, newest first, each as its reader may see it; `?user=`, `?assistant=1`, `?under=`, `?since=`, `?n=` (git only; see Version history) |
| POST   | `/changes/undo`       | Undo changes: `sha` (one, or several: an assistant's run of work), `back` (the list's address); see below |
| GET    | `/page/_deleted`      | Auto-generated list of pages deleted from git history, with one-click restore (git only) |
| GET    | `/uncommitted`        | Uncommitted Changes: the files changed since their last commit that the user sees (git only; 404 without) |
| GET    | `/diff-uncommitted/<slug>` | What a page's uncommitted change is, against its last commit (git only) |
| GET    | `/api/uncommitted`    | JSON `[{ slug, title, filepath }]`: the uncommitted files the user sees (the status badge's count) |
| POST   | `/api/commit-all`     | Commit every uncommitted file the user may commit, each in its own commit; `{ committed, files }` (400 without git) |
| POST   | `/api/commit-file`    | Commit one: body `{ filepath }`; `{ success: true }`, 400 for a file that isn't uncommitted, 403 for one the user may not write |

Version history is in `docs/spec/rendering.md`.
`POST /changes/undo` makes the pages the changes touched as they were before them, in one commit, and goes back to the list, which says what was done or why not (`?undone=`, `?undo_error=`).

### Uploads

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| POST   | `/api/upload`         | Upload a file (multipart, `file`); returns `{ url }` for insertion into markdown (the file's name percent-encoded); its fields below |
| GET    | `/_assets/<file>`     | A shared upload |
| GET    | `/private/<user>/_assets/<file>` | A private upload, for that user only (404 for anyone else) |
| GET    | `/groups/<group>/_assets/<file>` | A group's upload, for its members only (404 for anyone else; Group pages in `docs/spec/users.md`) |
| GET    | `/_tasks/files/<id>/_assets/<file>` | A task's file, for who sees the task only (404 for anyone else; A task's files in `docs/spec/tasks.md`) |

`POST /api/upload` takes:
- `page`: the page it is for (a private page's are kept in `private/<user>/_assets/`, a group's in its folder's);
- `task`: the task it is for, by its id (kept in `_tasks/files/<id>/_assets/`; 404 for a task the user doesn't see);
- `unique=1`: a random name (a pasted screenshot);
- `filename`: the name to keep it under; a name already taken answers 409 `{ conflict: true, filename }` unless `overwrite=1` (an MCP token's upload gets a free name instead); a name starting with `.` or holding a control character or a backslash is refused (400).

`POST /api/upload` and the four file routes also take an MCP token as a bearer token in place of a session (Files in `docs/spec/mcp.md`).

### Private and group pages

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/private`            | With users: your private home `private/<you>/index` (made on the first visit); 404 without users |
| GET    | `/groups`             | Your groups: those you are in and those you own, with who is in each; the form that makes one, and Manage… for those you own; more for an admin (below); 404 without users |
| POST   | `/groups`             | Makes a group (`name`, `people`): you are in it and own it; refused for a name that is taken, 403 for a viewer |
| POST   | `/groups/includes`    | What a group includes (`group`, `includes`: groups there are; a new `group` makes a group of groups); admins only, 404 for anyone else |
| POST   | `/groups/<group>/people` | Who is in the group by name, and its owners (`people`, `owners`), for its owners and admins; 404 for anyone else |
| POST   | `/groups/<group>/rename` | The group's new name (`name`): its folder, the links to its pages, the lists for it and the settings follow; for its owners and admins, 404 for anyone else |
| POST   | `/groups/<group>/remove` | Ends the group, for its owners and an admin, its pages moved or deleted (below); refused while a task list is for it (A group ended in `docs/spec/users.md`) |
| POST   | `/groups/<group>/leave` | Whoever asks is no longer in the group: its name goes from their groups (a group named on them that has owners and that they don't manage; 404 otherwise) |
| GET    | `/groups/<group>`     | A group's home `groups/<group>/index` (made on the first visit of someone in the group who may write); 404 for anyone not in it |

For an admin `/groups` also lists every other group, what each includes, the form for a group of groups and the folders no one sees (Groups in `docs/spec/users.md`).

`POST /groups/<group>/remove` takes `pages=move` with `folder`, which takes its pages to a folder of the shared wiki, or `pages=delete`, which deletes them (one of the two when its folder holds anything).

### Signing in and accounts

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET, POST | `/login`           | The login page and its form (`username` with users, `password`); `?next=` the local path to go to after; wrong passwords are throttled (429); without a login a redirect to `/` |
| POST   | `/logout`             | Sign out: the session ends wherever a copy of its cookie is (`.zone/signed-out`); redirects to `/login` |
| GET, POST | `/setup`           | First run: a wiki other devices reach with no login sets one here, then is signed in (First run in `docs/spec/security.md`); once there is a login, a redirect to `/` |
| GET, POST | `/signup`          | Sign-up (`email`): mails a link that makes the account (Sign-up in `docs/spec/users.md`); 404 while sign-up is off, a redirect to `/` for whoever is signed in |
| GET/POST | `/welcome/<token>`  | An invite or reset link's page: choose a password, once (no login needed) |
| GET, POST | `/password`        | Change password: whoever is signed in sets a new one (without users, the wiki's), with the one they have (Signing in in `docs/spec/users.md`); 404 without a login |
| GET    | `/admin`, `/admin/users` | The admin page, for admins (Managing users in `docs/spec/users.md`): `/admin` opens its first tab, Users; 404 for everyone else |
| POST   | `/admin/users/<name>/role`, `/groups`, `/disable`, `/enable`, `/remove`, `/reset`, `/revoke`; `/admin/users/invite` | The Users tab's changes, admins only (404 for anyone else) |

`/setup`'s form has `username` (empty for a password without users), `password` and `again`; a form that won't do is 400.

### Assistants

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| POST   | `/mcp`                | MCP server for LLM clients (`docs/spec/mcp.md`). GET/DELETE → 405. |
| GET    | `/assistant`          | Connect an assistant: the MCP token of whoever is signed in (without users, the wiki's), made and revoked in the browser (`docs/spec/mcp.md`) |
| POST   | `/assistant/token`, `/assistant/revoke` | Make a new token (with the password again; shown once) or take it away |
| POST   | `/assistant/connections/<id>/revoke` | End one of your assistants' connections (OAuth) |
| POST   | `/assistant/connections/<id>/read-only` | Make one of them read-only: from then on it changes nothing |
| GET    | `/.well-known/oauth-protected-resource`, `/.well-known/oauth-protected-resource/mcp`, `/.well-known/oauth-authorization-server` | OAuth metadata for MCP clients (no login; 404 without a login or with `mcp-oauth = no`; Connecting with OAuth in `docs/spec/mcp.md`) |
| POST   | `/oauth/register`, `/oauth/token` | An MCP client registers, and turns a code or a refresh token into tokens (no login, any origin) |
| GET, POST | `/oauth/authorize` | An assistant asks to connect: whoever is signed in allows it, with their password, or denies it |

### Zone itself

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/public/<file>`      | Zone's browser code, styles and vendored libraries, as they are in `public/` or the binary's copy of it (What the binary holds in `docs/spec/deployment.md`; no login needed) |
| GET    | `/api/version`        | `{ version, build }`: Zone's version and build (see Version in `docs/spec/deployment.md`) |
| GET    | `/api/health`         | `{ ok: true }`, or 503 `{ error }` while the settings file's last change wasn't taken (Reloading in `docs/spec/users.md`); no login needed, for `zonewiki health` |
| GET    | `/help`               | Help: Zone's manual, its contents first (see Help below) |
| GET    | `/help/<page>`        | A page of the manual (`docs/manual/<page>.md`) |
| GET    | `/help/spec`, `/help/spec/<name>` | The spec's list, and a spec file (`docs/spec/<name>.md`), where the manual links for the full rules |
| GET    | `/help/screenshots/<file>.png` | The screenshots the manual shows (`docs/screenshots/`) |

`/api/health` gives the error itself only with a session (or without a login), else a pointer to the log, as it may name users.

### Help

Zone's manual, `docs/manual/`, is written for users: how to do things, in what they see and click, with links to the spec for the full rules.
`/help` shows it in the browser (`go/help.go`, `help.html`), as a page is shown: the document's `#` heading as its title, its headings on the left on big screens, and at its end the pages before and after it.
The reading order is the contents' (`docs/manual/README.md`): the pages in the order its links name them, then any it doesn't name.
Links between the docs are written for the files (`tasks.md#views`, `../spec/users.md`, `../screenshots/page.png`, as they read on GitHub) and pointed at `/help/…` when shown.
It is linked from the user menu, the search modal's page list (Help, beside Tasks and Notes) and the shortcuts dialog (`?`).
Everyone who reaches the wiki reaches it, a public wiki's visitors too: it holds nothing of the wiki's.
The docs are the checkout's, or the binary's own copy of them (What the binary holds in `docs/spec/deployment.md`), so a binary run on its own has Help too;
with `--docs` naming a folder without them, `/help` says they aren't installed.

`zonewiki manual DIR` writes the same documents out as HTML fragments for a website (zonewiki.com's `/docs`): each document's body without a page around it, the links between them relative, the screenshots, and `pages.json` listing them with their titles in reading order.
The manual is also what `zone_help` searches first (`zone_help` in `docs/spec/mcp.md`).

A change users see updates its manual page in the same commit, as it updates the spec (`AGENTS.md`).
`tests/test_help.py` checks that every link in the manual leads to a document, a heading or a screenshot that is there.

### The tasks page and lists

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/tasks`              | The built-in tasks page (`tasksPage()`): every open task, or a list, the Inbox or My tasks; see The tasks page, below |
| POST   | `/tasks/rename`       | Rename a task tag (fields `tag`, `new_name`, `merge`; the Rename tag… dialog of `/tasks?tag=<tag>`, Renaming a tag in `docs/spec/tasks.md`); see below |
| POST   | `/tasks/new-list`     | Make a task list, pinned for who makes it (fields `name`, `private`, `pin`: `0` leaves it unpinned); see below |
| POST   | `/tasks/rename-list`  | Rename a list (fields `list`, `new_name`; its Rename… dialog): its tasks, the pins and the pages' embeds and links follow; see below |
| POST   | `/tasks/delete-list`  | Delete a list (fields `list`, `to`, `share`, `close`; its Delete… dialog); see below |
| POST   | `/tasks/share-list`   | Make a private list shared, its tasks with it (field `list`; its Make shared… dialog); a refusal back to the dialog (`&share_error=…`) |
| POST   | `/tasks/list-for`     | Say whom a shared list is for (fields `list`, `for`; its Who it is for… dialog, its owners'; Lists for some in `docs/spec/tasks.md`); see below |
| POST   | `/tasks/pin-list`     | Pin a list for the user (last), put its pill first, or unpin it (fields `list`, `pin`: `1`, `first`, `0`); see below |
| POST   | `/tasks/pin-order`    | The user's pinned lists in another order (the list switcher's pills, dragged); 204 |

#### The tasks page

`/tasks` shows the full main tasks (every open task, whatever list it is in), independent of the wiki's pages.
Every link to "the tasks" goes here (the Tasks module links without a task list home, search results, Linked tasks, `g a`).

- `?list=<name>` shows that list instead (a ref: its name, or `shared/<name>`), with its own settings (keep, labels, limit, …),
  and the pages that embed the list as its Backlinks (with a page view's side columns on big screens).
- `?list=inbox` shows the Inbox, the tasks in no list, with users your own (Lists → The Inbox in `docs/spec/tasks.md`; `?list=none` too).
- A name no list has shows a line that says so.
- `?mine=1` shows My tasks, your home view, as one task app (Lists → My tasks in `docs/spec/tasks.md`; in a wiki without lists a redirect to `/tasks`).
- `?view=`, `?task=`, `?tag=`, `?search=`, `?new=1` work as for any embed.

The list switcher sits above the heading: My tasks, the Inbox and the pinned lists as pills, the others and All tasks under More; this view's is the current one.
On a big screen (from 1320px) it is the page's left column instead, every list a line (The lists in the left column in `docs/spec/tasks.md`).
The heading reads "All tasks", "My tasks" or "List: <name>".

#### The list forms

Each form goes back to its dialog when it refuses, with the dialog's fields and the error in the address:

- `/tasks/rename` redirects to the new tag's view; a refusal back to the dialog (`?tag=…&rename_name=…&rename_error=…`, plus `&rename_merge=1` when the name is in use).
- `/tasks/new-list` comes from the New list… dialog of `/tasks`, and from Create under an embed of a list that isn't there (Lists in `docs/spec/tasks.md`).
  It makes an entry in the lists' registry, then redirects to `/tasks?list=<name>`; a name that won't do goes back to the dialog (`/tasks?newlist_name=…&newlist_error=…`).
- `/tasks/rename-list` redirects to the list by its new name, a refusal back to the dialog (`?list=…&rename_name=…&rename_error=…`).
- `/tasks/delete-list`: the list's tasks go to the list `to` names or to none, its open ones closed as won't do with `close`; it redirects there, a refusal back to the dialog (`&delete_error=…`).
- `/tasks/list-for`: `for` takes groups `@name` and users, empty or `everyone` for all.
  A refusal goes back to the dialog (`&for_error=…&for_words=…`), and so does a narrowing that would take open tasks out of their assignees' sight, which goes through with `confirm=1` (`&for_confirm=1`); who is no owner gets 403.
- `/tasks/pin-list` pins or unpins a list for whoever asks (`pin`: `1`, `first`, `0`), in their own registry; it redirects to the list.
- `/tasks/pin-order` takes every field `list` a ref, in the order wanted; the pinned ones among them come first, the rest stay as they were, nothing is pinned or unpinned.

## Task API

The JSON routes the task app and the editor call.
A task's fields, views and lists are in `docs/spec/tasks.md`.

| Method | Path                   | Description                                     |
|--------|------------------------|-------------------------------------------------|
| GET    | `/api/tasks`           | JSON list of all open tasks; the scope a view loads, below; the `Zone-Tasks` header is the word of the tasks answered |
| POST   | `/api/tasks`           | Create a task; returns JSON task object (201). |
| PUT    | `/api/tasks/<id>`      | Update a task's fields; see Updating a task, below |
| DELETE | `/api/tasks/<id>`      | Delete a task; returns 204. The task app never calls it (a task is closed undone, not deleted): it is for taking back a task just made. |
| POST   | `/api/tasks/<id>/toggle-check` | Tick or untick the Nth checkbox in a task's description, recording when as on a page; body `{ idx }`; returns the task. |
| POST   | `/api/tasks/<id>/comments` | Add a comment (markdown, a line or several) to a task, open or done; body `{ text }`; returns the task (201). See Comments. |
| PUT    | `/api/tasks/<id>/comments/<cid>` | Change a comment's text (its time stays); returns the task. |
| DELETE | `/api/tasks/<id>/comments/<cid>` | Delete a comment; returns the task. |
| POST   | `/api/tasks/batch-delete` | Delete multiple tasks; body `{ ids: [id1, id2, …] }`; returns 204. Used only to undo an import and the tasks the page editor created. |
| POST   | `/api/tasks/batch-complete` | Complete/uncomplete multiple tasks; body `{ ids: [...], completed: bool }`; returns JSON array of updated tasks; see below |
| POST   | `/api/tasks/batch-tag` | Add/remove tags from multiple tasks; body `{ ids: [...], add: [...], remove: [...] }` |
| POST   | `/api/tasks/batch-update` | Change fields of several tasks in one commit; body `{ tasks: [{ id, ...fields as PUT takes them, history? }] }`; see below |
| POST   | `/api/tasks/restore`   | Re-insert full task snapshots; body `{ tasks: [...] }`; returns restored tasks (201); see below |
| POST   | `/api/tasks/reorder`   | The user's own order of My tasks, or a list's manual order; see Reordering, below |
| GET    | `/api/tasks/due-count` | `{ count, overdue, today, focus }`, and `new`: the counts on the Tasks links; see below |
| POST   | `/api/tasks/settings`  | The settings dialog's Save, for an embed, a list or All tasks; `{ok}` or `{error}`; see below |
| POST   | `/api/tasks/lists`     | `{name, private}`: make a task list where the page stays, as `/tasks/new-list` does; see below |
| GET    | `/api/tasks/export`    | The tasks a view shows (`?list=`, `?mine=1` for My tasks, `?tag=`, `?assignee=`; none of them: all), open and closed, as a JSON file to save (Export and import in `docs/spec/tasks.md`) |
| POST   | `/api/tasks/import`    | `{file, list, tag, new, also, private}`: adds an export's tasks to a list; `{added, skipped, ids}` |
| GET    | `/api/tasks/headless`  | A hidden task app (the headless embed, without its `<script>` tags), for a page that needs the edit modal (`c t`, the New task button; see Modules in `docs/spec/navigation.md`). |
| GET    | `/api/tasks/closed`    | Closed tasks of `?year=YYYY` (default this year; earlier years come from their archive file); the Closed view's page, below |
| GET    | `/api/tasks/assignable` | `{ users, me }`: the users a task can be assigned to, A–Z, and who asks (Assigning tasks in `docs/spec/users.md`). `users` is empty without users and in sharing mode. |
| GET    | `/api/tasks/closed-years` | `{ years }`: years with closed tasks, newest first, this year always included (Closed's year switcher). `?list=<name>` and `?mine=1` as for the count. |
| GET    | `/api/tasks/closed-count` | `{ count }` of tasks in the Closed view (current year), for its link. `?list=<name>` counts that list's, `?mine=1` My tasks'; without them, every task's (a leftover `?all=1` changes nothing). |
| POST   | `/api/render-notes`    | Task descriptions and comments as HTML (`renderNotes`): body `{ notes: [...], comments: [...] }`, answer the same with HTML (see Description in `docs/spec/tasks.md`) |

Comments, Keep tasks and Sections, which the rows and sections here name, are in `docs/spec/tasks.md`.

### Reading tasks

`GET /api/tasks` reads `_tasks/tasks.json` and each list's `_tasks/lists/<name>/tasks.json`.

- `?held=a,b`: the lists the page holds, for the `Zone-Tasks` word.
- `?list=<ref>`, `?lists=a|b`, `?mine=1`: those a view of that scope shows (what a task list loads).
- `?with=order`: `{ tasks, order }`, with the user's own order of My tasks (task ids; Lists → My tasks in `docs/spec/tasks.md`).

### Updating a task

`PUT /api/tasks/<id>` changes title, tags, due_date, start_date, completed, recurrence via `rec_type`/`rec_interval`/`rec_from`, keep, last_done, wont_do, skip.

- The two dates are independent: setting one never moves the other.
- `wont_do: true` archives the task without spawning a recurring instance; `wont_do: false` reopens it (removing any skip-spawned instance).
- `skip: true` archives the occurrence as won't-do **and** spawns the next instance.
- `completed: true` on a keep task does not archive it (see Keep tasks).
- `completed`, `wont_do`, `skip` and `keep` are `true` or `false`, anything else is refused (400, "completed must be true or false").
- A date is `YYYY-MM-DD`, `null` or `""` (none).
- `position` gives the task its place in the manual order in the same commit: `"top"`, `"bottom"`, `{after: id}` or `{before: id}` (A position in the order in `docs/spec/tasks.md`); `POST /api/tasks` takes it too, and without it a new task is unranked.
  Only an open task has one, and the task named must be open and ranked; anything else is refused (400) with nothing written.

### Several tasks at once

`POST /api/tasks/batch-complete` handles recurring spawn/cleanup; keep tasks stay open (see Keep tasks).
With `wont_do: true` the open ones are closed undone instead, as `PUT`'s `wont_do` does one: no next occurrence, a keep task archived too.

`POST /api/tasks/batch-update` gives each task its own fields (a selection's Start today, the undo of a bulk tag or start).
Status keys (`completed`, `wont_do`, `skip`) are refused (400); ids no longer in the tasks are skipped.
It returns the updated tasks.
Each change is one task's update (`changeTaskFields`): `was` makes a text changed meanwhile a conflict (409, nothing written), and who a description names now is logged and told.

### Restoring snapshots

`POST /api/tasks/restore` undid a delete, while the task app had one; nothing in the app calls it now.
Snapshots keep their original id/created_at/completion state, as far as those are an id and times as Zone writes them (A task snapshot's ids and times in `docs/spec/security.md`); unknown fields are dropped, existing ids skipped.

### Reordering

`POST /api/tasks/reorder` takes one of these bodies:

| Body | What it does |
|------|--------------|
| `{ mine: true, order: [ids] }` | Keeps the order as the user's own of My tasks and writes no task (204) |
| `{ moves: [{id, after, before}] }` | Places each task between the two named, in the order the server has (what a list sends; Sections in `docs/spec/tasks.md`) |
| `{ order: [id1, id2, …] }` | Densely renumbers the given ids' `sort_order` to `0..n-1` |
| `{ updates: [{ id, sort_order }, …] }` | Sets specific ids to specific (possibly fractional) values without touching any other task; a `null` value clears the field, taking the task out of the manual order |

The last two, as before, update `sort_order`, a list's manual order (Open's and Today's Sorted), and both may be sent together.
Open and Today always send the whole order as `order`, tasks they don't show included (see Sections).

### The due count

`GET /api/tasks/due-count` gives tasks due today + overdue, and tasks in Today, of My tasks (see Modules in `docs/spec/navigation.md`), and `new` when there are tasks someone else gave the user that they haven't touched yet (no key at zero).
The `Zone-Tasks` header is a word that changes with every write of tasks or lists (Keeping lists current in `docs/spec/tasks.md`).
`?places=1` adds `places`, the same counts of each place in the list switcher by its key (`list:<ref>`, `mine`: My tasks, `inbox`: the Inbox; Lists in `docs/spec/tasks.md`).

### Settings and new lists

`POST /api/tasks/settings`: `{page, index, was, set}` rewrites the params of a page's `[[tasks: …]]`, `{list, set}` a list's own settings, `{all: true, was, set}` the settings of All tasks; `{ok}` or `{error}` (400, 403, 404, 409: Settings in `docs/spec/tasks.md`).

`POST /api/tasks/lists` is New list… in the dialog that moves tasks to a list (A new list for the tasks in `docs/spec/tasks.md`).
It answers 201 `{ref, private, pinned}` (pinned beside the user's other pins, never as their first), a refusal `{error}`.

### Closed tasks

`GET /api/tasks/closed` with `?limit=N` is the Closed view's page, `{tasks, total}`: of the scope (`?list=`, `?mine=1`) and the filter field's words (`?q=`), the latest closed first, with `?task=<id>` that task among them (Views → Closed in `docs/spec/tasks.md`).

## The page view

Every page view includes the breadcrumb at the top, and at the bottom Backlinks, Related and Linked tasks.

### Breadcrumb

`Wiki Tasks Notes › Page Title` above the page header: its root is the module switcher (see Modules in `docs/spec/navigation.md`), the green Z logo in front of Wiki, the home page.
After some navigation it becomes a trail of up to 6 recently visited pages (`js/chrome.js`, `sessionStorage`); a phone (≤640px) shows at most 4 of them, the last 3 visited plus the current page.
It stays on one line: the page titles shrink with an ellipsis, while the module switcher, separators, × and the backlinks chip keep their size.
A trail that could not shrink once made the page wider than a phone, which pushed the full-screen tasks edit modal's buttons off-screen.

### Backlinks

A section at the bottom lists all pages that link to the current page (`findBacklinks`, `pageLinkTargets`).
It shows "No pages link here." when empty.
A link is a `[[wikilink]]`, or a markdown link that is relative (`page`, `../dir/page`, `page.md`) or absolute (`/page/<slug>`), with or without an `#anchor` or a title;
the forms a rename re-points.
A link in code or math (`mdtext.MaskCode`) shows as text, so it is none, as a rename leaves it too.

### Backlinks chip

When a page has backlinks, a `← N` chip sits at the right end of the breadcrumb (left of the top actions; hidden where the side columns list the backlinks).
On desktop, hovering it lists the linking pages (`.bl-tooltip`) and clicking jumps to the Backlinks section.
Touch devices (`hover: none`) have no hover, so there a tap toggles the list (`.bl-open`, delegated click handler in `js/chrome.js`) instead of scrolling down.
A second tap or a tap elsewhere closes it.
The hover rules are wrapped in `@media (hover: hover)` because a tap leaves `:hover` stuck, which would fight the toggle.

### Related

Only with semantic search on (see Search → Semantic search): up to 5 pages closest in meaning, leaving out pages already linked either way (`relatedSection`).
Absent when none clears the similarity floor.

### Linked tasks

Open tasks whose title/description link to the page.
Each entry is a single link to that task in the tasks (`/tasks?task=<id>&view=open`, plus `&list=<name>` for a task in a separate list — `taskHref()`); links inside the task title are flattened to their labels (`mdtext.PlainTaskTitle`) so anchors are never nested.

A task linking to a section (`[[Page#Section]]`, or `/page/<slug>#anchor`; `findTaskBacklinks` returns its `section` and `anchor`) names it after its title (`§ Section`, linking to the heading, named as the page's heading says),
and that heading gets a `☑ N` chip (`RenderOpts.SectionTasks`, a `<details>` after the heading, no JS) listing the section's tasks.

## Side columns

From 1320px wide a page view uses the room beside the text: the page's contents on the left, its connections on the right, the text in the middle, both sides sticky.

### The three columns

A page view (`viewPage`, `shell{wide: true}` → `main.wide`) has three columns (`.page-grid`: 14rem · up to 60rem · 14rem), the side columns giving way down to 9rem so the middle one keeps the text at the width other views have.
The page's sheet is as wide as other views' unless the page has a table, code block or image wider than the text;
then it takes the whole middle column (`.sheet-wide`, `js/chrome.js`, measured after load and whenever the content's size changes).
So the text sits in the middle of the window, where the other views have theirs.
The Tasks page (`/tasks`) has the three columns too, its task lists on the left (The lists in the left column in `docs/spec/tasks.md`), a list's Backlinks on the right, its sheet as wide as a page's.
Every other view (notes, search) gets the same top row width at that size, its content centred at the text width (46rem), so the breadcrumb and top actions stay put when switching modules.

### Left: On this page

The left column (`.rail-left`) holds "On this page" (`railToc`): the page's `##`/`###` headings (frontmatter sources' "Sources" heading too, at the end) when there are at least `railTocMinHeadings` (3).
An entry is made from the heading as the render parsed it (`Env.TOC`, `tocLabel` in `go/markdown.go`), never cut out of the rendered HTML:
its text, its code, math, emphasis and strikethrough, a link as its text, a `[[task:…]]` as the task's title, images and raw HTML left out;
a heading written as raw HTML (`<h2>` in an HTML block) is none.
(Cut out of the article, with its links taken out by a pattern, a heading's raw HTML came out joined anew, past the defusing: `<<a>form` a form, No form in a page posts to Zone in `docs/spec/security.md`.)
Otherwise it is empty; the column stays so the page keeps its place.
At its end **↑ Back to top** (`.rail-top`), shown once the page has scrolled a screen down; it scrolls up and drops a `#hash` from the URL.
`js/chrome.js` marks the section being read (`.active`, `aria-current="location"`): the last heading scrolled past (120px from the top), at the end of the page the last one in view.
A column taller than the window scrolls on its own: a fade marks the edge with more (`.rail-more-above`/`-below`, set by `js/chrome.js`, CSS `mask-image`), and the TOC scrolls its column (never the page) to keep the marked entry in view.

### Right: the page's connections

The right column (`.rail-context`) holds Backlinks,
Linked tasks and Related (the `link_sections` template with `copy`: the same sections with their ids as `data-id`; the anchors stay with the originals in `.page-foot`),
and **Links to** (`outlinks`: the existing pages this page links to, A–Z by title; only in the column),
so a page nothing links to still shows its connections.

### Middle: the text

The middle column (`.page-col`, a size container) keeps header, meta row and text at the reading width (46rem, about 85 characters).
A code block, table or image alone in its paragraph (`<p class="img-block">`, marked on the parsed document by `zoneTransformer`) is as wide as the text unless its content needs more, then grows up to the middle column, centred on the text (`width: max-content; min-width: 100%; max-width: 100cqw`);
an image within a sentence stays inside the text.
A task list embed keeps the text width, like the tasks and notes pages.
Text itself is never widened: longer lines read worse.

### What changes around them

The sections at the page's end (`.page-foot`) and the breadcrumb's backlinks chip are hidden there.
`main.wide` has wider side padding (3.5rem), so the badges fixed at the top left don't touch the breadcrumb on a window just over 1320px.
Below 1320px the page is the single 800px column as before.
