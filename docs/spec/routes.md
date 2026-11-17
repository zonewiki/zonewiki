# Routes and page views

Part of Zone's spec (see `AGENTS.md` for the index).

## Routes

Every route but the login's own wants a session where the wiki has a login.
In a public wiki (`ZONE_PUBLIC`; Public wikis in `docs/spec/security.md`) a visitor reaches the ones listed there without one: pages, the page list, search, history, uploads, and with `edit` the editor and a new page.

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/`                   | Redirects to `/page/index`                      |
| GET    | `/page/<slug>`        | Render a wiki page (markdown → HTML)            |
| GET    | `/tasks`               | The built-in tasks page (`tasksPage()`): the full main tasks (every open task, whatever list it is in), independent of the wiki's pages. `?list=<name>` shows that list instead (a ref: its name, or `shared/<name>`), with its own settings (keep, labels, limit, …), and the pages that embed the list as its Backlinks (with a page view's side columns on big screens); `?list=inbox` the Inbox, the tasks in no list, with users your own (Lists → The Inbox in `docs/spec/tasks.md`; `?list=none` too); a name no list has, a line that says so; the list switcher above the heading (My tasks, the Inbox and the pinned lists as pills, the others and All tasks under More; this view's the current one), which reads "All tasks", "My tasks" or "List: <name>"; `?made=1` on a list just made says what its being your first pinned list changes; `?mine=1` shows My tasks, your home view, as one task app (Lists → My tasks in `docs/spec/tasks.md`; in a wiki without lists a redirect to `/tasks`). `?view=`, `?task=`, `?tag=`, `?search=`, `?new=1` work as for any embed. Every link to "the tasks" goes here (the Tasks module links without a task list home, search results, Linked tasks, `g d`). |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `<slug>.md`               |
| GET    | `/new`                | A form for a new page's name (without JavaScript; pages are made from links to them) |
| POST   | `/new`                | Create `<slug>.md` and redirect to it; `private=1` (with users): in your private folder |
| GET    | `/private`            | With users: your private home `private/<you>/index` (made on the first visit); 404 without users |
| GET    | `/groups`             | Your groups: those you are in and those you own, with who is in each; the form that makes one, and Manage… for those you own; for an admin also every other group, what each includes, the form for a group of groups and the folders no one sees (Groups in `docs/spec/users.md`); 404 without users |
| POST   | `/groups`             | Makes a group (`name`, `people`): you are in it and own it; refused for a name that is taken, 403 for a viewer |
| POST   | `/groups/includes`    | What a group includes (`group`, `includes`: groups there are; a new `group` makes a group of groups); admins only, 404 for anyone else |
| POST   | `/groups/<group>/people` | Who is in the group by name, and its owners (`people`, `owners`), for its owners and admins; 404 for anyone else |
| POST   | `/groups/<group>/rename` | The group's new name (`name`): its folder, the links to its pages, the lists for it and the settings follow; for its owners and admins, 404 for anyone else |
| POST   | `/groups/<group>/leave` | Whoever asks is no longer in the group: its name goes from their groups (a group named on them that has owners and that they don't manage; 404 otherwise) |
| GET    | `/groups/<group>`     | A group's home `groups/<group>/index` (made on the first visit of someone in the group who may write); 404 for anyone not in it |
| GET    | `/admin`, `/admin/users`    | The admin page, for admins (Managing users in `docs/spec/users.md`): `/admin` opens its first tab, Users; 404 for everyone else |
| GET    | `/assistant`                | Connect an assistant: the MCP token of whoever is signed in (without users, the wiki's), made and revoked in the browser (`docs/spec/mcp.md`) |
| POST   | `/assistant/token`, `/assistant/revoke` | Make a new token (with the password again; shown once) or take it away |
| POST   | `/assistant/connections/<id>/revoke` | End one of your assistants' connections (OAuth) |
| GET    | `/.well-known/oauth-protected-resource`, `/.well-known/oauth-protected-resource/mcp`, `/.well-known/oauth-authorization-server` | OAuth metadata for MCP clients (no login; 404 without a login or with `mcp-oauth = no`; Connecting with OAuth in `docs/spec/mcp.md`) |
| POST   | `/oauth/register`, `/oauth/token` | An MCP client registers, and turns a code or a refresh token into tokens (no login, any origin) |
| GET, POST | `/oauth/authorize`       | An assistant asks to connect: whoever is signed in allows it, with their password, or denies it |
| GET, POST | `/password`              | Change password: whoever is signed in sets a new one (without users, the wiki's), with the one they have (Signing in in `docs/spec/users.md`); 404 without a login |
| POST   | `/admin/users/<name>/role`, `/groups`, `/disable`, `/enable`, `/remove`, `/reset`, `/revoke`; `/admin/users/invite` | The Users tab's changes, admins only (404 for anyone else) |
| GET/POST | `/welcome/<token>`  | An invite or reset link's page: choose a password, once (no login needed) |
| GET    | `/search?q=...`       | Full-text search results page (up to 50, with a count; `&unlinked=1` as for the API) |
| GET    | `/api/search?q=...`   | JSON search results (used by the search UI); `&unlinked=1`: only pages nothing links to, no tasks; `&notes=1`: only notes (Notes page's filter; then `limit` goes up to 1000); `&limit=N` (default 15, max 100) |
| GET    | `/api/page-preview/<slug>` | `{ title, html }`: a page rendered for the search modal's preview pane (heading ids as `data-pid`, no task list embeds) |
| POST   | `/api/preview`        | Render posted markdown to HTML (for live editor preview); `?page=` the page being edited (your own private page: its links to new shared pages are marked) |
| POST   | `/api/render-notes`   | Task descriptions and comments as HTML (`renderNotes`): body `{ notes: [...], comments: [...] }`, answer the same with HTML (see Description in `docs/spec/tasks.md`) |
| GET    | `/api/pages`          | JSON list of all existing page slugs (for wikilink hints) |
| POST   | `/mcp`                | MCP server for LLM clients (see MCP server). GET/DELETE → 405. |
| GET    | `/api/version`        | `{ version, build }`: Zone's version and build (see Version in `docs/spec/deployment.md`) |
| GET    | `/api/health`         | `{ ok: true }`, or 503 `{ error }` while the settings file's last change wasn't taken (Reloading in `docs/spec/users.md`); no login needed, for `zonewiki health`; the error itself only with a session (or without a login), else a pointer to the log, as it may name users |
| POST   | `/api/upload`         | Upload a file; returns `{ url }` for insertion into markdown (the file's name percent-encoded); `page`: the page it is for (a private page's are kept in `private/<user>/_assets/`); `task`: the task it is for, by its id (kept in `_tasks/files/<id>/_assets/`; 404 for a task the user doesn't see) |
| GET    | `/_assets/<file>`     | A shared upload. This, the private one below and `POST /api/upload` also take an MCP token as a bearer token in place of a session (Files in `docs/spec/mcp.md`) |
| GET    | `/private/<user>/_assets/<file>` | A private upload, for that user only (404 for anyone else) |
| GET    | `/groups/<group>/_assets/<file>` | A group's upload, for its members only (404 for anyone else; Group pages in `docs/spec/users.md`); takes an MCP token as the other two do |
| GET    | `/_tasks/files/<id>/_assets/<file>` | A task's file, for who sees the task only (404 for anyone else; A task's files in `docs/spec/tasks.md`); takes an MCP token too |
| POST   | `/api/save/<slug>`    | Write page content to disk silently (no git commit, no redirect). For full-page edits returns 204; for section edits returns JSON `{ file_hash, start_char, end_char }` with updated anchors, or 409 on conflict. Used by auto-save. |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox (`idx` param) in a page; returns 204. |
| GET    | `/api/tasks`           | JSON list of all open tasks (`_tasks/tasks.json` and each list's `_tasks/lists/<name>/tasks.json`); `?held=a,b`: the lists the page holds, for the `Zone-Tasks` word; `?list=<ref>`, `?lists=a\|b`, `?mine=1`: those a view of that scope shows (what a task list loads); `?with=order`: `{ tasks, order }`, with the user's own order of My tasks (task ids; Lists → My tasks in `docs/spec/tasks.md`). The `Zone-Tasks` header is the word of the tasks answered |
| POST   | `/api/tasks`           | Create a task; returns JSON task object (201). |
| PUT    | `/api/tasks/<id>`      | Update a task (title, tags, due_date, start_date, completed, recurrence via `rec_type`/`rec_interval`/`rec_from`, keep, last_done, wont_do, skip). The two dates are independent: setting one never moves the other. `wont_do: true` archives the task without spawning a recurring instance; `wont_do: false` reopens it (removing any skip-spawned instance). `skip: true` archives the occurrence as won't-do **and** spawns the next instance. `completed: true` on a keep task does not archive it (see Keep tasks). `completed`, `wont_do`, `skip` and `keep` are `true` or `false`, anything else is refused (400, "completed must be true or false"); a date is `YYYY-MM-DD`, `null` or `""` (none). |
| DELETE | `/api/tasks/<id>`      | Delete a task; returns 204. The task app never calls it (a task is closed undone, not deleted): it is for taking back a task just made. |
| POST   | `/api/tasks/<id>/toggle-check` | Tick or untick the Nth checkbox in a task's description, recording when as on a page; body `{ idx }`; returns the task. |
| POST   | `/api/tasks/<id>/comments` | Add a comment (markdown, a line or several) to a task, open or done; body `{ text }`; returns the task (201). See Comments. |
| PUT    | `/api/tasks/<id>/comments/<cid>` | Change a comment's text (its time stays); returns the task. |
| DELETE | `/api/tasks/<id>/comments/<cid>` | Delete a comment; returns the task. |
| POST   | `/api/tasks/batch-delete` | Delete multiple tasks; body `{ ids: [id1, id2, …] }`; returns 204. Used only to undo an import and the tasks the page editor created. |
| POST   | `/api/tasks/batch-complete` | Complete/uncomplete multiple tasks; body `{ ids: [...], completed: bool }`; returns JSON array of updated tasks. Handles recurring spawn/cleanup; keep tasks stay open (see Keep tasks). With `wont_do: true` the open ones are closed undone instead, as `PUT`'s `wont_do` does one: no next occurrence, a keep task archived too. |
| POST   | `/api/tasks/restore`   | Re-insert full task snapshots (it undid a delete, while the task app had one; nothing in the app calls it now); body `{ tasks: [...] }`. Snapshots keep their original id/created_at/completion state, as far as those are an id and times as Zone writes them (A task snapshot's ids and times in `docs/spec/security.md`); unknown fields are dropped, existing ids skipped. Returns restored tasks (201). |
| POST   | `/api/tasks/reorder`   | `{ mine: true, order: [ids] }` keeps the order as the user's own of My tasks and writes no task (204). `{ moves: [{id, after, before}] }` places each task between the two named, in the order the server has (what a list sends; Sections in `docs/spec/tasks.md`). Else, as before: update `sort_order`, a list's manual order (Open's and Today's Sorted). Body `{ order: [id1, id2, …] }` densely renumbers the given ids to `0..n-1`; Open and Today always send the whole order this way, tasks they don't show included (see Sections). Body `{ updates: [{ id, sort_order }, …] }` sets specific ids to specific (possibly fractional) values without touching any other task; a `null` value clears the field, taking the task out of the manual order. Both may be sent together. |
| POST   | `/api/tasks/batch-tag` | Add/remove tags from multiple tasks; body `{ ids: [...], add: [...], remove: [...] }` |
| POST   | `/api/tasks/batch-update` | Change fields of several tasks in one commit; body `{ tasks: [{ id, ...fields as PUT takes them, history? }] }`, each task its own fields (a selection's Start today, the undo of a bulk tag or start). Status keys (`completed`, `wont_do`, `skip`) are refused (400); ids no longer in the tasks are skipped. Returns the updated tasks. |
| GET    | `/api/tasks/due-count` | `{ count, overdue, today, focus }`: tasks due today + overdue, and tasks in Today, of My tasks (see Modules), and `new` when there are tasks someone else gave the user that they haven't touched yet (no key at zero); the `Zone-Tasks` header is a word that changes with every write of tasks or lists (The task app in `docs/spec/tasks.md`); `?places=1` adds `places`, the same counts of each place in the list switcher by its key (`list:<ref>`, `mine`: My tasks, `inbox`: the Inbox; Lists in `docs/spec/tasks.md`) |
| POST   | `/api/tasks/settings`  | The settings dialog's Save: `{page, index, was, set}` rewrites the params of a page's `[[tasks: …]]`, `{list, set}` a list's own settings, `{all: true, was, set}` the settings of All tasks; `{ok}` or `{error}` (400, 403, 404, 409: Settings in `docs/spec/tasks.md`) |
| POST   | `/api/tasks/lists`     | `{name, private}`: make a task list where the page stays (New list… in the dialog that moves tasks to a list; Moving tasks to a list in `docs/spec/tasks.md`), as `/tasks/new-list` does; 201 `{ref, private, pinned}` (pinned beside the user's other pins, never as their first), a refusal `{error}` |
| GET    | `/api/tasks/export`    | The tasks a view shows (`?list=`, `?mine=1` for My tasks, `?tag=`, `?assignee=`; none of them: all), open and closed, as a JSON file to save (Export and import in `docs/spec/tasks.md`) |
| POST   | `/api/tasks/import`    | `{file, list, tag, new, also, private}`: adds an export's tasks to a list; `{added, skipped, ids}` |
| GET    | `/api/tasks/headless`  | A hidden task app (the headless embed, without its `<script>` tags), for a page that needs the edit modal (`c t`, the New task button; see Modules). |
| GET    | `/api/tasks/closed` | Closed tasks of `?year=YYYY` (default this year; earlier years come from their archive file). `?limit=N`: the Closed view's page, `{tasks, total}`: of the scope (`?list=`, `?mine=1`) and the filter field's words (`?q=`), the latest closed first, with `?task=<id>` that task among them (Views → Closed in `docs/spec/tasks.md`). |
| GET    | `/api/tasks/assignable` | `{ users, me }`: the users a task can be assigned to, A–Z, and who asks (Assigning tasks in `docs/spec/users.md`). `users` is empty without users and in sharing mode. |
| GET    | `/api/tasks/closed-years` | `{ years }`: years with closed tasks, newest first, this year always included (Closed's year switcher). `?list=<name>` and `?mine=1` as for the count. |
| GET    | `/api/tasks/closed-count` | `{ count }` of tasks in the Closed view (current year), for its link. `?list=<name>` counts that list's, `?mine=1` My tasks'; without them, every task's (a leftover `?all=1` changes nothing). |
| POST   | `/api/notes`      | Create a new note (`<timestamp>_<slug>.md`); returns `{ slug }`. |
| GET    | `/notes`                | Auto-generated index of all notes, newest first, with a filter box (`?q=`) that searches notes only |
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug |
| POST   | `/tasks/rename`       | Rename a task tag (fields `tag`, `new_name`, `merge`; the Rename tag… dialog of `/tasks?tag=<tag>`, Lists in `docs/spec/tasks.md`): redirects to the new tag's view; a refusal back to the dialog (`?tag=…&rename_name=…&rename_error=…`, plus `&rename_merge=1` when the name is in use) |
| POST   | `/tasks/new-list`     | Make a task list, pinned for who makes it (fields `name`, `private`, `pin`: `0` leaves it unpinned; the New list… dialog of `/tasks`, and Create under an embed of a list that isn't there; Lists in `docs/spec/tasks.md`): an entry in the lists' registry, then redirects to `/tasks?list=<name>`; a name that won't do back to the dialog (`/tasks?newlist_name=…&newlist_error=…`) |
| POST   | `/tasks/rename-list`  | Rename a list (fields `list`, `new_name`; its Rename… dialog): its tasks, the pins and the pages' embeds and links follow; redirects to the list by its new name, a refusal back to the dialog (`?list=…&rename_name=…&rename_error=…`) |
| POST   | `/tasks/delete-list`  | Delete a list (fields `list`, `to`, `share`, `close`; its Delete… dialog): its tasks go to the list `to` names or to none, its open ones closed as won't do with `close`; redirects there, a refusal back to the dialog (`&delete_error=…`) |
| POST   | `/tasks/share-list`   | Make a private list shared, its tasks with it (field `list`; its Make shared… dialog); a refusal back to the dialog (`&share_error=…`) |
| POST   | `/tasks/list-for`     | Say whom a shared list is for (fields `list`, `for`: groups `@name` and users, empty or `everyone` for all; its Who it is for… dialog, its owners'; Lists for some in `docs/spec/tasks.md`); a refusal back to the dialog (`&for_error=…&for_words=…`), and so a narrowing that would take open tasks out of their assignees' sight, which goes through with `confirm=1` (`&for_confirm=1`); who is no owner 403 |
| POST   | `/tasks/pin-list`     | Pin a list for the user (last), put its pill first, or unpin it (fields `list`, `pin`: `1`, `first`, `0`); with `everyone=1` (with users, who manages the list) the wiki's pins instead, what a user who has pinned nothing has; redirects to the list |
| POST   | `/tasks/pin-order`    | The user's pinned lists in another order (the list switcher's pills, dragged): every field `list` a ref, in the order wanted; the pinned ones among them come first, the rest stay as they were, nothing is pinned or unpinned; 204 |
| POST   | `/promote/<slug>`     | Move a note to the wiki (see Notes → Move to wiki): fields `folder`, `name`, `keep_date`, `link_from`; redirects to the new page, or back with `?promote_error=…` |
| POST   | `/append/<slug>`      | Append a note to a page as a section (see Notes → Append to page): fields `page`, `heading`; redirects to the section, or back with `?append_error=…` |
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |
| GET    | `/pages`              | Redirects to `/` (browsing lives in the search modal) |
| GET    | `/history/<slug>`     | A page's saved versions, newest first (only when git is enabled; see Version history) |
| GET    | `/revision/<slug>/<sha>` | A page as one save left it (read-only), with ← Older / Newer → and Restore |
| GET    | `/diff/<slug>/<sha>`  | What one save changed in a page |
| POST   | `/restore/<slug>/<sha>` | Restore a page to a prior version; creates a new commit |
| GET    | `/page/_deleted`      | Auto-generated list of pages deleted from git history, with one-click restore (git only) |

A `<slug>` in a folder starting with `_` or `.` (`_assets/`, `_tasks/`, `.zone/`, `.git/`, `a/_drafts/` …) names no page: `safePath()` refuses it (also after `..`), so no route reads or writes a `.md` file there, even by direct URL (400, or 404 for the page preview).
These are the folders `iterPages()` skips.
A file name starting with `_` (`notes`, `a/_notes`) is still a page.

Every page view includes:
- **Breadcrumb** — `Wiki Tasks Notes › Page Title` above the page header: its root is the module switcher (see Modules), the green Z logo in front of Wiki, the home page.
  After some navigation it becomes a trail of up to 6 recently visited pages (`js/chrome.js`, `sessionStorage`); a phone (≤640px) shows at most 4 of them, the last 3 visited plus the current page.
  It stays on one line: the page titles shrink with an ellipsis, while the module switcher, separators, × and the backlinks chip keep their size.
  A trail that could not shrink once made the page wider than a phone, which pushed the full-screen tasks edit modal's buttons off-screen.
- **Backlinks** — section at the bottom listing all pages that link to the current page (`findBacklinks`, `pageLinkTargets`): a `[[wikilink]]`, or a markdown link that is relative (`page`, `../dir/page`, `page.md`) or absolute (`/page/<slug>`), with or without an `#anchor` or a title;
  the forms a rename re-points.
  A link in code or math (`mdtext.MaskCode`) shows as text, so it is none, as a rename leaves it too.
  Shows "No pages link here." when empty.
- **Backlinks chip** — when a page has backlinks, a `← N` chip sits at the right end of the breadcrumb (left of the top actions; hidden where the side columns list the backlinks).
  On desktop, hovering it lists the linking pages (`.bl-tooltip`) and clicking jumps to the Backlinks section.
  Touch devices (`hover: none`) have no hover, so there a tap toggles the list (`.bl-open`, delegated click handler in `js/chrome.js`) instead of scrolling down.
  A second tap or a tap elsewhere closes it.
  The hover rules are wrapped in `@media (hover: hover)` because a tap leaves `:hover` stuck, which would fight the toggle.
- **Related** — only with semantic search on (see Search → Semantic search): up to 5 pages closest in meaning, leaving out pages already linked either way (`build_related_html`).
  Absent when none clears the similarity floor.
- **Linked tasks** — open tasks whose title/description link to the page.
  A task linking to a section (`[[Page#Section]]`, or `/page/<slug>#anchor`; `findTaskBacklinks` returns its `section` and `anchor`) names it after its title (`§ Section`, linking to the heading, named as the page's heading says),
  and that heading gets a `☑ N` chip (`render_md(…, section_tasks=)`, a `<details>` after the heading, no JS) listing the section's tasks.
  Each entry is a single link to that task in the tasks (`/tasks?task=<id>&view=open`, plus `&list=<name>` for a task in a separate list — `taskHref()`); links inside the task title are flattened to their labels (`mdtext.PlainTaskTitle`) so anchors are never nested.

### Side columns (big screens)

From 1320px wide a page view (`viewPage`, `views.page(…, wide=True)` → `main.wide`) uses the room beside the text: three columns (`.page-grid`: 14rem · up to 60rem · 14rem, the side columns giving way down to 9rem so the middle one keeps the text at the width other views have;
the page's sheet as wide as other views' unless the page has a table,
code block or image wider than the text,
then the whole middle column (`.sheet-wide`,
`js/chrome.js`,
measured after load and whenever the content's size changes),
so the text sits in the middle of the window,
where the other views have theirs),
both sides sticky.
Every other view (tasks, notes, search) gets the same top row width at that size, its content centred at the text width (46rem), so the breadcrumb and top actions stay put when switching modules.

- **Left** (`.rail-left`): "On this page" (`rail_toc_html`): the page's rendered `##`/`###` headings (frontmatter sources' "Sources" heading too, at the end) when there are at least `railTocMinHeadings` (3), links in them flattened.
  Otherwise empty; the column stays so the page keeps its place.
  At its end **↑ Back to top** (`.rail-top`), shown once the page has scrolled a screen down; it scrolls up and drops a `#hash` from the URL.
  `js/chrome.js` marks the section being read (`.active`, `aria-current="location"`): the last heading scrolled past (120px from the top), at the end of the page the last one in view.
  A column taller than the window scrolls on its own: a fade marks the edge with more (`.rail-more-above`/`-below`, set by `js/chrome.js`, CSS `mask-image`), and the TOC scrolls its column (never the page) to keep the marked entry in view.
- **Right** (`.rail-context`): Backlinks,
  Linked tasks and Related (`rail_copy`: the same sections with their ids as `data-id`; the anchors stay with the originals in `.page-foot`),
  and **Links to** (`outlinks_html`: the existing pages this page links to, A–Z by title; only in the column),
  so a page nothing links to still shows its connections.
- **Middle** (`.page-col`, a size container): header, meta row and text keep the reading width (46rem, about 85 characters);
  a code block, table or image alone in its paragraph (`<p class="img-block">`, marked on the parsed tokens by `imageWidths`) is as wide as the text unless its content needs more, then grows up to the middle column, centred on the text (`width: max-content; min-width: 100%; max-width: 100cqw`);
  an image within a sentence stays inside the text.
  A task list embed keeps the text width, like the tasks and notes pages.
  Text itself is never widened: longer lines read worse.
- The sections at the page's end (`.page-foot`) and the breadcrumb's backlinks chip are hidden there.
  `main.wide` has wider side padding (3.5rem), so the badges fixed at the top left don't touch the breadcrumb on a window just over 1320px.
  Below 1320px the page is the single 800px column as before.
