# Navigation, modules and notes

Part of Zone's spec (see `AGENTS.md` for the index).

## Page List

- `GET /pages` redirects to `/`.
  The search/browse UI lives in a modal dialog, not a dedicated page.
- **Search modal** — opened via the "Search" nav button or **Ctrl+P** (⌘P on Mac), closed with **Esc** or clicking the backdrop.
  - Backdrop click detection: `e.target === searchModal` (not coordinate comparison — coordinates break with `<select>` dropdowns).
  - Page data is loaded lazily on first open from `GET /api/pages-list` (JSON: slug, title, links, mtime) and cached for the session.
    Each list item shows the page title and slug — dates are not shown in the modal.
  - Filter input instantly narrows the list by title/slug (`titleMatch`: the better `fuzzyScore` of the title and the **file name**, the slug's last part scored on its own, so a file name starting with the query counts as a match at the start;
    a match reaching into folder names only scores `FOLDER_PENALTY` less, since ".../2026-05-26_alex-1on1/prep" is not the page "alex" names);
    after 150ms debounce fetches `/api/search` for full-text **Content matches** with excerpts shown below the page list.
  - **Several words** (`wordsScore`): each must fuzzy-match within one word of the title or file name, and all must match; the scores add up.
    Matching the whole input as one letter sequence let "alex again" find its letters spread over "Add alert exporter tag handling".
    A single word still matches across the whole title.
  - **Ties** in the fuzzy score are common (every title starting with the query scores the same),
    so `tieBreak` orders them: notes last,
    then fewer folders in the slug,
    then the shorter name. "alex" lists the top-level `alex-morgan` before notes three folders down and the note `…_alex_prep`,
    instead of alphabetically.
  - **Sort by** — Recently edited (default, so an empty query works as a quick switcher), Title A–Z, Title Z–A, Most linked.
    Applies while the query is empty; a sort picked by hand is remembered in `localStorage` (`zone-search-sort`).
  - **Built-in pages** — `apiPagesList()` adds Tasks (`href: /tasks`), Notes and (git only) Deleted Pages, marked `builtin`.
    Tasks and Notes are modules, linked from the module switcher, so the empty-query list leaves them out; Deleted Pages, also marked `special`, is pinned above the pages whatever the sort, set apart by a stronger rule (`.page-list-special`).
    Typing matches all three like any other entry; "Unlinked only" leaves them out.
  - **Search mode** (`#search-mode`, right of the controls): whether Content matches are found by meaning too.
    A dot and a word: "Keywords + meaning" (dot in the accent colour), "Meaning: indexing…" (the wiki isn't embedded yet, e.g. after a start with a new model or chunking; dot faded), or "Keywords only" (semantic search off, or its model failing; grey); the tooltip says why and what turns it on.
    The page renders the state (`semanticStatus`: on, indexing, failed, off) as `data-state`, every `/api/search` response says the current one in `X-Zone-Semantic`, and `js/search.js` words it (`SEARCH_MODES`), so "indexing" turns into "on" with the next search.
  - **Unlinked only** checkbox — pages with zero incoming wikilinks,
    in the title list and in the Content matches: the modal sends `/api/search?…&unlinked=1`,
    and the server keeps only such pages,
    no tasks (`do_search(unlinked=True)`, same counts as the list's, `allBacklinkCounts`) before it cuts the results to 15. Toggling it re-runs the content search.
  - First match is highlighted (with no title match, the first content match once it arrives); **↑/↓** moves the highlight across both page results and content match cards; **Enter** opens the highlighted item.
  - **Preview pane** (≥1100px; `showModalPreview` in `js/search.js`): the modal widens (up to 1180px, 76vh) into results on the left and the highlighted result on the right.
    A page comes from `GET /api/page-preview/<slug>` (`{title, html}`: rendered without its H1, heading ids as `data-pid` so they don't clash with the page underneath, task list embeds as *(tasks)*; cached per slug while the page is open),
    scrolled to the result's `#anchor` section,
    its `hl` words marked (`markTerms`, shared with the opened page's highlights).
    A task, the tasks or a built-in page shows its result card.
    Updates 80ms after the highlight settles.

---

## Modules

Zone has three modules, in this order: the **Wiki** (pages, home `/`), **Tasks** (`/tasks`) and **Notes** (quick notes, `/notes`) (`modules` in `go/views.go`).
**With users** a fourth,
**Private** (your private home `/private`, `docs/spec/users.md`),
comes after Wiki,
and the switcher has two groups by kind,
each a pill of its own: the pages,
**Wiki · Private**,
and your tools,
**Tasks · Notes** (`group` in `moduleLinks`; on the phone's tab bar a divider between them).
Private and Notes each carry a lock (`.module-lock` in the link, tooltip "Private: only you see it", "Notes: only you see it"; `own` in `moduleLinks`).
Tasks is in neither a shared nor a private group: it shows the shared tasks and your own private ones, and its home may be in your folder.
The groups were Wiki · Tasks and Private · Notes,
by who sees them,
until 2026-11. Who is signed in,
**Connect an assistant** (your MCP token, `/assistant`, `docs/spec/mcp.md`),
**Change password** (`/password`, Signing in in `docs/spec/users.md`) and Sign out are in the **user menu**,
opened by the **user button** at the top right on desktop (phones have Sign out in the page's top row, and both pages' links in the shortcuts dialog).
Without users there is one pill, Wiki · Tasks · Notes.
The Tasks link goes to your **pinned lists**,
the lists you pinned (your own tasks; a list's ⋯ menu or List settings pins it: Pins in `docs/spec/tasks.md`;
`pinnedLists`, from your pins in the lists' registry, the wiki's when you have set none, in their order;
`rq.pinnedLists()` looks them up once per request): one at `/tasks?list=<name>` with its settings,
several as one task app at `/tasks?pinned=1` (Pinned),
as the list switcher opens them;
no pin, `/tasks` (`tasksLinkHref`, `moduleLinks`).
Its counts are of the pinned lists.
The **first pinned list** is where New task puts a task (`defaultList()`, `lookupDefaultList()`).
Your other lists and all tasks are one click from there, in the task app's **list switcher** (Lists in `docs/spec/tasks.md`).
`/tasks` stays the view of every task that search results, task links and `g d` open.
There is still no nav bar: the modules sit where the eye already goes.

- **Current module**: `currentModule()` names each view's (`/tasks` → tasks; `/notes`, a note and its history → notes; with users a page in your own private folder, the private home included → private; `/search` and `/login` → none; everything else → wiki),
  and `layout.html` puts it on `<body data-module>` (not in the editor).
  The server marks the matching `.module-link`s `aria-current="page"` (`moduleLinks`), so the highlight is there at first paint.
- **Module switcher** (`switch` in `templates/partials/modules.html`, `.breadcrumb-home.module-switch`): the root of every breadcrumb, Z logo + Wiki, Tasks, and Notes; with users Wiki · Private, then Tasks · Notes (`.module-group`, one pill each).
  On desktop it is set apart from the trail as a control: bordered white pills (`--surface`) with the current module shaded, and the first separator drawn as a thin vertical divider instead of `›` (`.module-switch + .breadcrumb-sep`).
  On phones and in the editor only the logo shows (the way home).
  The navigation trail leaves out the modules' own pages (`index`, `/notes`, `/tasks`, your private home `private/<you>/index`, and whatever the Tasks link points at).
  Where the row has no room for every page of the trail (a window narrower than the side columns' 1320px leaves it two or three names next to the switcher and the top actions), the oldest pages leave the row until each name left shows whole or at least 72px of it (`fitTrail`, `.trail-off`; the page itself always stays, and the pages stay in the list: a wider window shows them again).
  The trail itself is added by `js/chrome.js` from `sessionStorage` after the page is drawn, so until it has run (`.trail-ready` on the breadcrumb) `style.css` keeps the crumbs after the switcher invisible (they keep their room, so nothing jumps), showing them after a second should the script fail.
- **Top actions** (desktop, `templates/partials/top_actions.html`, `.top-actions`, first in `<main>`): at the top right of the page,
  in line with the breadcrumb (absolutely positioned in `main`; the breadcrumb gets `min-height: 2.25rem` and right padding so its own end stays clear),
  in two groups: **Sharing mode** (`.top-sharing`, the eye, `aria-pressed`; see Sharing mode),
  set apart by a divider (`.top-sep`) since it switches a mode;
  and **Search** (`.top-search`, Ctrl+P), the one used most, as a labelled pill with its shortcut (`.top-search-key`, `⌘P` on a Mac), white on the paper like the module switcher's pills.
  The icon is 2.25rem, muted, a background on hover; name and shortcut in tooltip and accessible name.
  There are no "new" buttons on desktop: a new note is `c n` (or `c s`, `openNewNote`), a new task `c t` (`openDefaultTask`); the row stays calm, and phones have both in the action bar.
  (Until 2026-11 the row had New note and Add a task icons between the eye and Search.) The pill has a fixed width, so the row's width (`--top-actions-w`, which the breadcrumb's padding and the status badges' position use) doesn't depend on the font; its border ends on the content's right edge.
  Hidden at ≤640px.
  They replaced the round New and Search buttons in the bottom-right corner, so desktop toasts and Sign out have the bottom corners to themselves again.
- **New task from anywhere** (`openDefaultTask` in `js/actions.js`: `c t`, and the phone's **Task** tab): the task goes into your first pinned list, tagged as it tags new tasks (`newTaskTags`: the list's `new`) and a keep task if the list says so.
  On a page that shows that list or Pinned it clicks that embed's own **+ New** (so the view's dates apply too, e.g. Focus's today);
  anywhere else it opens a task list app's edit modal with that list, its tags,
  keep and start (`layout.html` puts them on `<body>`: `data-tasks-list`, `data-tasks-tags`, `data-tasks-keep`, `data-tasks-today` when that list's view is Focus, as its "+ New" starts tasks today there;
  without a pin, today, as `/tasks`'s Focus;
  `openEditModal` takes `list`, `tags`, which replace the filter's even when empty, `keep` and `start_date`): the page's own task app (an embed, `/tasks`, the editor's hidden one),
  or on a page without one a hidden one loaded on demand (`loadHeadlessTasks`: `GET /api/tasks/headless`, the headless embed without its scripts, then `tasks-core.js` and `tasks.js`; once per page),
  so you stay on the page and a toast says "Added “…” to your tasks".
  Should loading fail, where the Tasks link goes (or `/tasks`) opens with `?new=1`.
  Without a pin the task gets no tags.
  The dialog says which list the task goes to (Where a new task goes in `docs/spec/tasks.md`). **g t** goes where the Tasks link goes (`tasksLinkHref`: where the Tasks module link points, else `/tasks`).
- **Action bar** (`action_bar` in `templates/partials/modules.html`, `nav.action-buttons`): the phone's: three groups set apart by dividers (`.action-sep`), as on desktop: the modules (`.action-module`; with users a divider between Wiki · Private and Tasks · Notes);
  the two "new" actions (`icons.new_note`, `icons.new_task`; on desktop only their shortcuts), **Note** (`.action-new-note`, a new note, `openNewNote`, `c s`) and **Task** (`.action-task`, a new task);
  and **Search** (`.action-search`, Ctrl+P, `openSearchModal`).
  Navigation and actions apart, so the "new" buttons say what they make instead of a bare +.
  Hidden on desktop (the top actions).
  At ≤640px it is a bottom tab bar across the full width: Wiki · Tasks · Notes | Note · Task | Search (with users Wiki · Private | Tasks · Notes | Note · Task | Search, Private with a lock icon, Notes with a small lock at its icon's top right: `.action-lock`),
  icon over label,
  the current module in the accent green,
  each at least 44px.
  The Tasks tab's counts stand right of its icon, due above Focus, centred on the icon; they may reach a little past the tab's edge, clear of the next tab's icon, and the tabs keep one width, the icons evenly spaced.
  `views.page(…, action_buttons=False)` omits it, and the editor and the login page leave it out.
  On phones it is hidden via CSS `:has()` whenever an input/textarea/select has focus, because the on-screen keyboard owns the bottom edge then, and `main` gets extra bottom space so the bar never covers the page's end.
  A tap on the bar acts on its `touchend` (`item.click()`) and cancels the browser's own click (`preventDefault`; a long press over 500ms or a move over 10px is left to the browser).
  Firefox for Android as an installed app, its toolbar scrolled away, draws the bar lower than it lays it out, so a tap on the strip under the labels landed below the bar's box, on the page (on the tasks a card, selected and scrolled into view).
  So the tab is found by position, in capture listeners on `document` before the page sees the touch: a touch on the bar, or below its box where nothing covers the bar (`elementFromPoint` at its bottom edge; a menu's bottom sheet or a dialog does), goes to the tab nearest its x.
- **Task** (`js/actions.js`): in the tasks module it clicks the task app's own **+ New** (`#task-add-btn`, a pinned embed's (`data-pinned`) on a page with one), so the view's tags and dates apply; elsewhere it adds a task to your tasks as `c t` does (`openDefaultTask`).
  A new page comes from a link to it (a missing wikilink opens its editor, `docs/spec/rendering.md`), so every page is linked from somewhere; or from a note and its **Move to wiki**, which asks what links to it.
  There is no New page button or shortcut (Ctrl+N was one until 2026-10, but browsers keep it for a new window).
- **Counts**: the Tasks links (switcher and tab bar) carry two counts (`.module-counts`): tasks due today or overdue (`.module-due`, orange) and tasks in Focus (`.module-focus`, muted; `TasksCore.isFocus`: started, or due today or earlier),
  each hidden at zero,
  with a tooltip like "Tasks: 5 in Focus · 1 overdue · 1 due today".
  `taskCounts()` counts the pinned lists' tasks, as Pinned shows them, or every task without a pin.
  The server renders them into the page (`taskCounts`, once per request, into `partials/modules.html`), since filling them in by a fetch after the page had painted made the link change width on every load (flicker);
  `refreshDueBadge` in `js/chrome.js` refreshes them from `/api/tasks/due-count` every minute while someone uses the page (`whileUsed` in `js/util.js`: a key, the pointer or a scroll since the last time; a tab nobody is at asks nothing), on tab focus, on a back/forward-cache restore and after tasks writes (`tasks.js` calls it).
  They replaced the separate due badge at the top left.
- **Status badges** (`.top-badges`: uncommitted count, asked from `/api/uncommitted` when a page loads, when its tab comes back and every 30 seconds while someone uses the page (`whileUsed`, as the Tasks counts above), and Sign out when auth is on;
  the first thing in `<main>`, after the remote 🌐 `.remote-badge`, which on desktop sits left of the module switcher, in the margin outside the content's edge, on the row's centre line, and in the editor at the top right): on desktop they sit in the top row,
  left of the top actions,
  centred on the row (the breadcrumb keeps their room free; Sign out, though inside, is fixed to the bottom-right);
  in the editor fixed at the top right, since its breadcrumb starts at the window's left edge.
  The row aligns to `main`'s side padding (`--main-pad`): the switcher starts on the content's left edge, the search pill ends on its right edge.
  At ≤640px they join the page flow above the breadcrumb, so they no longer cover it, Sign out on the right of that row (the bottom edge is the tab bar's), and 🌐 is hidden, since on a phone remote access is a given.
  The tasks' due count is on the Tasks module links (see Modules).

---

## Sharing mode

For sharing the screen: the page on screen stays as it is, everything that shows *other* pages around it is hidden, and pages not for sharing are left out entirely.
Switched by the eye in the top actions or **S** (Shift+s; `toggleSharing` in `js/actions.js`).
The state is a cookie (`zone-sharing=1`, a year;), so the server knows it: `layout.html` renders `<html class="sharing">` from the first paint (nothing flashes on load), and switching reloads the page, rendered for the new mode.
While on, a thin amber bar runs across the top and the eye is highlighted.
Phones have no switch (no top actions).

- **Around the page** (`html.sharing` in `style.css`): the breadcrumb's trail of visited pages (`js/chrome.js` marks it `.trail-link`/`.trail-sep`; the separator left before the page becomes the divider),
  the backlinks chip,
  Backlinks / Linked tasks / Related / Links to (side column and `.page-foot`),
  a section's linked-task chips,
  the status counts (tasks counts on the Tasks links, uncommitted badge).
  The search modal lists nothing until something is typed (no recent pages; nothing for ↑/↓ or the preview to reach), and the editor's `[[` page and task completion waits for two letters.
- **Pages not for sharing** (`noshare` in the settings file, `docs/spec/users.md`: comma-separated, `#tag` entries name frontmatter tags, `folder/` entries folders and everything below them, e.g. `noshare = #noshare, #people, people/, personal/`;
  a bare word counts as a tag;
  unset or empty: `#noshare`;
  don't quote the value).
  `is_noshare(slug)` (folders by slug, tags from the frontmatter, cached by mtime), `hidden_page(slug)` = sharing mode on and noshare.
  Such a page's view, editor, history, revisions and diffs (`/diff`, `/diff-uncommitted`) show "Hidden page" (`hiddenPageView()`: neither title nor text, no trail stop);
  `/api/page-preview` 404s;
  search (`doSearch`: `/api/search`, `/search`, Notes filter), `/api/pages`, `/api/pages-list`, Notes list, Deleted Pages, the uncommitted list, Backlinks, Links to and Related leave them out;
  a wikilink to one renders as its words without the link (`<span class="wikilink-hidden">`).
  The MCP tools are not a browser and ignore sharing mode.

## Notes

- **Keybinding:** **c** then **n** creates a note (see Keyboard Shortcuts); so do the phone's action bar's Note (see Modules) and **+ New** in the Notes page's header (`.notes-new`, also when there are no notes yet; not shown to a viewer).
- User types a title and submits → `POST /api/notes` → server creates `<YYYY-MM-DD_HH-MM-SS>_<slug>.md` (local time;
  with users in your private folder, `private/<you>/…`, so notes are your own: see `docs/spec/users.md`) with `# Title\n\n` (`quicknote.create`;
  a `/` in the title is a hyphen in the name, so it makes no folder, and a second note of the same name in the same second takes the next second) and returns `{ slug }` → browser navigates to `/edit/<slug>`.
  The editor opens with the cursor under the title, ready for the body, as for a brand-new page: a page that holds only its `# Title` line (`only_title` in `edit_page`, `data-cursor="end"` on the textarea) starts with the cursor at its end; any other page at its start.
- A `<dialog id="shortcuts-modal">` lists all keybindings, opened with `?`.
- **Assistants** (MCP) make, find and file notes too: `write_page` with `note`, `list_pages`/`search_wiki` with `notes`, `file_note` (see `mcp.md`).
  Creating, moving and appending live in `go/quicknotes.go`, shared by the routes and the MCP tools.
- **Note filenames** match `quicknoteRE` (`^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$`, optionally after `private/<user>/`) e.g. `2026-03-24_18-50-08_my-note.md`.
  `isQuicknote(slug)` identifies them: at the top level, or with users at the top of a user's private folder.
- **`/notes`** — special auto-generated page (no `.md` file on disk) listing all notes you may see (the top level's and your own) newest-first with title and timestamp.
  Not editable.
  - **Filter** (`#notes-filter`, `initNotesFilter` in `js/search.js`): a box above the list that runs the search over notes only (`/api/search?notes=1`, `doSearch(…, quicknote=true)`),
    so ranking,
    stemming,
    typos,
    semantic hits and the query syntax (`"phrase"`, `-word`, `#tag`, `edited:` …) are the search's.
    Matches show in ranked order with their excerpt (marked), linking to the matching section with `?hl=` highlights, plus a count ("2 of 30"); everything else is hidden.
    `notes=1` lifts the limit to `notesFilterLimit` (1000), so every match is listed.
    The query is kept in `?q=` (`history.replaceState`), so Back returns to the filtered list and `/notes?q=…` links work.
    Enter opens the first match; Esc clears the box (a second Esc leaves it); an empty box restores the newest-first list.
    No filter when there are no notes.
- **Move to wiki** (the note's ⋯ menu, `#page-promote-dialog`, `promote_dialog` in `templates/partials/page.html`; `POST /promote/<slug>`): gives the note a real name, so it leaves Notes.
  The dialog asks for a **folder** (a `<datalist>` of the existing ones, `pageFolders()`),
  the **name** (pre-filled with the note's title, not its timestamped slug; a `/` in it is a hyphen) and whether to **keep the date** (`YYYY-MM-DD_` in front of the name, as in `conversations/2026-07-08_…`),
  and previews the slug (`promotedSlug` in `js/pagemenu.js` mirrors `mdtext.PromotedSlug()`, its `toSlug` mirrors `mdtext.ToSlug`).
  **Link from** names the page to link it from, since a page in the wiki is almost always linked from somewhere;
  `js/pagemenu.js` fills in the folder's `index` page while one exists (the home page `index` when the folder is empty, so a top-level page gets the paste step too), until you edit the field;
  empty means no link.
  With one,
  Move first copies the new page's link (`[[slug|Title]]`, `mdtext.PageWikilink`) to the clipboard (`copyText`: the Clipboard API, or the copy command where it isn't available, as on plain http on the LAN; the form's `copied` field says whether it worked),
  and after the move that page opens in the editor (`/edit/<page>?paste=<slug>&copied=1`) with a hint (`.paste-hint`, `paste_hint_html`): one row of a clipboard icon,
  "Link copied.
  Paste it where it belongs on this page." over the link (one line, cut off with …), Copy and ×, so you paste it where it belongs; the page itself isn't changed.
  A page that already linked to the note (its link now following) opens the moved page instead.
  The page fields of both dialogs suggest from one `<datalist id="wiki-pages">` (`wiki_pages` from `pageMenu`: every page but notes) and take a slug, `/page/<slug>` or a name (`pageFromInput`).
  It is a rename underneath: links to the note follow (`updateReferences`), one commit `Promote <old> to <new>` (text the note held that wasn't committed yet is committed first, `Save <note>`, as before a rename), which the history calls "Moved from Quicknote".
  An empty name, a taken one or one that would still be a quicknote name goes back to the note with `?promote_error=…` and the dialog open.
- **Append to page** (the note's ⋯ menu, `#page-append-dialog`, `append_dialog` in `templates/partials/page.html`; `POST /append/<slug>`): for a note that belongs on an existing page (meeting notes on a person's page).
  The dialog asks for the page (the shared `#wiki-pages` suggestions) and the section heading (`Title (YYYY-MM-DD)`, `appendHeading`; `[`, `]` and `|` are dropped, they would break wikilinks to it).
  `mdtext.NoteAsSection()` makes the note a `## heading` section: its H1 goes, a tldr becomes an italic first paragraph (a line each), its sources a `Sources:` list at the section's end, its headings move down so the highest is `###` (fenced code untouched);
  its frontmatter tags join the page's (`mdtext.AddFrontmatterTags`, adding the line or block).
  The section goes at the end of the page,
  the note is deleted (so Deleted Pages can restore it),
  and links to it point at the section (`update_references(old, new, section)` → `rewrite_page_links(…, section=)`: a link with its own `#anchor` keeps it, since the note's headings keep their text),
  pages and tasks alike,
  in one commit `Append <note> to <page>` (the note's text not committed yet first, `Save <note>`);
  the page's history calls it "Note appended".
  Refused (no page, the note itself, no heading, a heading the page already has) → back to the note with `?append_error=…`, the dialog open.
- Daily notes (`/api/daily`, `/page/_daily`, `c d`/`g y`) were removed on purpose.
  Existing `YYYY-MM-DD_daily.md` files are ordinary pages.
- **`/page/_deleted`** — special auto-generated page listing all pages that exist in git history but not on disk (deleted pages), newest deletion first.
  Each entry shows title, slug, deletion date, and a one-click Restore button that calls `POST /restore/<slug>/<sha>`.
  Only available when `cfg.GitEnabled`.
  Navigating to a deleted page's URL directly also shows a restore prompt instead of redirecting to the editor.
- Appears in the search modal as a synthetic entry injected by `apiPagesList()` with title "Notes" (found by typing; the empty-query list leaves it out, see Page List) — matches searches for "notes" (slug `/notes`, href `/notes`).
  `_deleted` is similarly injected with title "Deleted Pages" (git only).
  A "Tasks" entry (slug shown as `/tasks`) links to the built-in tasks page; entries may carry an `href` for targets that are not `/page/<slug>`.
- Special pages (starting with `_`) are not filtered out of the search modal or backlink counts but are treated as regular pages otherwise.

---

## Keyboard Shortcuts

Defined in the `keydown` listener in `js/shortcuts.js`.
Bare-key shortcuts are suppressed when focus is inside an `input`, `textarea`, `select`, or `[contenteditable]`.

| Shortcut | Context | Action |
|----------|---------|--------|
| **Ctrl/⌘+P** | anywhere | Open search modal |
| **g** then **h/n/a/t/p** | anywhere | Navigate: Home / Notes / all tasks (`/tasks`) / Tasks (its home) / your private home (with users). |
| **c** then **n/t** | anywhere | Create: note / task in your first pinned list (with the tags it gives new tasks). |
| **F2** | editor | Toggle preview pane |
| **Ctrl/⌘+S** | editor | Done — commit and go to page view |
| **Ctrl/⌘+B / I / K** | editor | Bold / italic / link (see Formatting bar) |
| **Ctrl/⌘+Shift+X** | editor | Toggle checkbox on current line |
| **Ctrl/⌘+Delete** | page view | Delete current page (confirmation dialog) |
| **S** | anywhere | Sharing mode on/off (see Sharing mode) |
| **e** | page view | Navigate to edit mode |
| **]** / **[** | page view | Next / previous section (`##`/`###`, not a task list embed's; `jumpSection` in `js/shortcuts.js`): scrolls it to the top and puts its anchor in the URL; the current one is the last heading scrolled past, as the side TOC marks it; `[` before the first goes to the top |
| **z** | page view | Fold every section, or unfold them all when all are folded (see Folding sections in `docs/spec/rendering.md`) |
| **p** | page view | Enter presentation mode |
| **Ctrl/⌘+/** | anywhere | Show keyboard shortcuts and snippets (Vimium-safe) |
| **?** | anywhere | Show keyboard shortcuts and snippets (fallback) |
| **n** | tasks page | Open new task modal |
| **n** / **N** | page opened from search | Next / previous search highlight (wins over the task app's **n** while highlights show) |
| **Esc** | page opened from search | Clear the search highlights |

---

## Presentation Mode

- **Present** is in the page's ⋯ menu (see Page header) whenever the page has a heading.
- Choosing Present (or pressing **`p`**) opens a fullscreen overlay (a white sheet filling the screen, in dark mode the sheet's dark tone; larger type, serif headings with a short accent bar, the progress in the accent), splitting the article into slides at each `<h2>` boundary.
- Content before the first `<h2>` (if any) becomes an intro slide.
- Folded sections (Folding sections in `docs/spec/rendering.md`) show on their slides.
- Navigation: **← →** arrow keys, or click "← Prev" / "Next →" buttons.
  **Esc** exits.
- The slide counter (`1 / N`) is shown in the centre of the control bar.
- Slide content re-uses `.page-content` CSS so typography, code blocks, and links render correctly inside the overlay; the overlay sets `--bg` to the sheet's `--surface`, so what paints itself in the page's colour matches.
- Syntax highlighting is re-applied via `hljs.highlightElement` when each slide is shown.
- The Present menu item is hidden (via JS, `syncPresentBtn`) on pages with no headings.
