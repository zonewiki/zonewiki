# Routes and page views

Part of Leaf's spec (see `AGENTS.md` for the index).

## Routes

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/`                   | Redirects to `/page/index`                      |
| GET    | `/page/<slug>`        | Render a wiki page (markdown → HTML)            |
| GET    | `/deck`               | The built-in deck page (`deck_page()`): the full main deck (every open task, list tasks included), independent of the wiki's pages. `?list=<name>` shows that separate list instead, with the params of the page that declares it (keep, labels, limit, …) and a line linking to that page; `?view=`, `?task=`, `?tag=`, `?search=`, `?new=1` work as for any embed. Every link to "the deck" goes here (the Deck module links without a deck home, search results, Linked tasks, `g d`). |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `<slug>.md`               |
| GET    | `/new`                | Editor for a new page (slug entered by user)    |
| POST   | `/new`                | Create `<slug>.md` and redirect to it           |
| GET    | `/search?q=...`       | Full-text search results page (up to 50, with a count; `&unlinked=1` as for the API) |
| GET    | `/api/search?q=...`   | JSON search results (used by the search UI); `&unlinked=1`: only pages nothing links to, no tasks; `&scratch=1`: only scratch notes (the Scratch page's filter; then `limit` goes up to 1000); `&limit=N` (default 15, max 100) |
| GET    | `/api/page-preview/<slug>` | `{ title, html }`: a page rendered for the search modal's preview pane (heading ids as `data-pid`, no deck embeds) |
| POST   | `/api/preview`        | Render posted markdown to HTML (for live editor preview) |
| GET    | `/api/pages`          | JSON list of all existing page slugs (for wikilink hints) |
| POST   | `/mcp`                | MCP server for LLM clients (see MCP server). GET/DELETE → 405. |
| GET    | `/api/version`        | `{ version, build }`: Leaf's version and build (see Version) |
| POST   | `/api/upload`         | Upload an image file; returns `{ url }` for insertion into markdown |
| POST   | `/api/save/<slug>`    | Write page content to disk silently (no git commit, no redirect). For full-page edits returns 204; for section edits returns JSON `{ file_hash, start_char, end_char }` with updated anchors, or 409 on conflict. Used by auto-save. |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox (`idx` param) in a page; returns 204. |
| GET    | `/api/deck`           | JSON list of all open tasks (`_deck/tasks.json`) |
| POST   | `/api/deck`           | Create a task; returns JSON task object (201). |
| PUT    | `/api/deck/<id>`      | Update a task (title, tags, due_date, start_date, completed, recurrence via `rec_type`/`rec_interval`/`rec_from`, keep, last_done, wont_do, skip). The two dates are independent: setting one never moves the other. `wont_do: true` archives the task without spawning a recurring instance; `wont_do: false` reopens it (removing any skip-spawned instance). `skip: true` archives the occurrence as won't-do **and** spawns the next instance. `completed: true` on a keep task does not archive it (see Keep tasks). |
| DELETE | `/api/deck/<id>`      | Delete a task; returns 204. |
| POST   | `/api/deck/<id>/comments` | Add a comment (status line) to a task, open or done; body `{ text }`; returns the task (201). See Comments. |
| PUT    | `/api/deck/<id>/comments/<cid>` | Change a comment's text (its time stays); returns the task. |
| DELETE | `/api/deck/<id>/comments/<cid>` | Delete a comment; returns the task. |
| POST   | `/api/deck/batch-delete` | Delete multiple tasks; body `{ ids: [id1, id2, …] }`; returns 204. |
| POST   | `/api/deck/batch-complete` | Complete/uncomplete multiple tasks; body `{ ids: [...], completed: bool }`; returns JSON array of updated tasks. Handles recurring spawn/cleanup; keep tasks stay open (see Keep tasks). |
| POST   | `/api/deck/restore`   | Re-insert full task snapshots (undo after delete); body `{ tasks: [...] }`. Snapshots keep their original id/created_at/completion state; unknown fields are dropped, existing ids skipped. Returns restored tasks (201). |
| POST   | `/api/deck/reorder`   | Update `sort_order`, the one manual order (Open's and Focus's Sorted). Body `{ order: [id1, id2, …] }` densely renumbers the given ids to `0..n-1`; Open and Focus always send the whole order this way, tasks they don't show included (see Sections). Body `{ updates: [{ id, sort_order }, …] }` sets specific ids to specific (possibly fractional) values without touching any other task; a `null` value clears the field, taking the task out of the manual order. Both may be sent together. |
| POST   | `/api/deck/batch-tag` | Add/remove tags from multiple tasks; body `{ ids: [...], add: [...], remove: [...] }` |
| GET    | `/api/deck/due-count` | `{ count, overdue, today, focus }`: tasks due today + overdue, and tasks in Focus, of the deck's home (its `list=`/`tag=`), else of every task (see Modules) |
| GET    | `/api/deck/headless`  | A hidden deck app (the headless embed, without its `<script>` tags), for a page that needs the edit modal (`c t`, the New task button; see Modules). |
| GET    | `/api/deck/completed` | Completed tasks of `?year=YYYY` (default this year; earlier years come from their archive file). |
| GET    | `/api/deck/completed-years` | `{ years }`: years with completed tasks, newest first, this year always included (Completed's year switcher). `?list=<name>` as for the count. |
| GET    | `/api/deck/completed-count` | `{ count }` of tasks in the Completed view (current year), for its link. `?list=<name>` counts that list's (tasks tagged with it); without it, every task's (a leftover `?all=1` changes nothing). |
| POST   | `/api/scratch`    | Create a new scratch note (`<timestamp>_<slug>.md`); returns `{ slug }` |
| GET    | `/page/_scratch`        | Auto-generated index of all scratch notes, newest first, with a filter box (`?q=`) that searches scratch notes only |
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug |
| POST   | `/promote/<slug>`     | Move a scratch note to the wiki (see Scratch → Move to wiki): fields `folder`, `name`, `keep_date`, `link_from`; redirects to the new page, or back with `?promote_error=…` |
| POST   | `/append/<slug>`      | Append a scratch note to a page as a section (see Scratch → Append to page): fields `page`, `heading`; redirects to the section, or back with `?append_error=…` |
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |
| GET    | `/pages`              | Redirects to `/` (browsing lives in the search modal) |
| GET    | `/history/<slug>`     | A page's saved versions, newest first (only when git is enabled; see Version history) |
| GET    | `/revision/<slug>/<sha>` | A page as one save left it (read-only), with ← Older / Newer → and Restore |
| GET    | `/diff/<slug>/<sha>`  | What one save changed in a page |
| POST   | `/restore/<slug>/<sha>` | Restore a page to a prior version; creates a new commit |
| GET    | `/page/_raw`          | The `_raw` inbox: paste a URL or text to collect it (see Scratch) |
| POST   | `/api/raw/append-url` | Add `- [title](url)` to `_raw.md`; fields `url`, `title` |
| POST   | `/api/raw/create-page` | Save pasted text as `_raw/<uuid>.md` and link it from `_raw.md`; fields `text`, `title`; returns `{ slug, title }` |
| GET    | `/page/_deleted`      | Auto-generated list of pages deleted from git history, with one-click restore (git only) |

Every page view includes:
- **Breadcrumb** — `Wiki Deck Scratch › Page Title` above the page header: its root is the module switcher (see Modules), the green leaf logo in front of Wiki, the home page. After some navigation it becomes a trail of up to 6 recently visited pages (`js/chrome.js`, `sessionStorage`); a phone (≤640px) shows at most 4 of them, the last 3 visited plus the current page. It stays on one line: the page titles shrink with an ellipsis, while the module switcher, separators, × and the backlinks chip keep their size. A trail that could not shrink once made the page wider than a phone, which pushed the full-screen deck edit modal's buttons off-screen.
- **Backlinks** — section at the bottom listing all pages that contain a `[[wikilink]]` pointing to the current page. Shows "No pages link here." when empty.
- **Backlinks chip** — when a page has backlinks, a `← N` chip sits at the right end of the breadcrumb (left of the top actions; hidden where the side columns list the backlinks). On desktop, hovering it lists the linking pages (`.bl-tooltip`) and clicking jumps to the Backlinks section. Touch devices (`hover: none`) have no hover, so there a tap toggles the list (`.bl-open`, delegated click handler in `js/chrome.js`) instead of scrolling down. A second tap or a tap elsewhere closes it. The hover rules are wrapped in `@media (hover: hover)` because a tap leaves `:hover` stuck, which would fight the toggle.
- **Related** — only with semantic search on (see Search → Semantic search): up to 5 pages closest in meaning, leaving out pages already linked either way (`build_related_html`). Absent when none clears the similarity floor.
- **Linked tasks** — open deck tasks whose title/description link to the page. A task linking to a section (`[[Page#Section]]`, or `/page/<slug>#anchor`; `deckstore.find_task_backlinks` returns its `section` and `anchor`) names it after its title (`§ Section`, linking to the heading, named as the page's heading says), and that heading gets a `☑ N` chip (`render_md(…, section_tasks=)`, a `<details>` after the heading, no JS) listing the section's tasks. Each entry is a single link to that task in the deck (`/deck?task=<id>&view=open`, plus `&list=<name>` for a task in a separate list — `task_href()`); links inside the task title are flattened to their labels (`plain_task_title`) so anchors are never nested.

### Side columns (big screens)

From 1320px wide a page view (`view_page`, `views.page(…, wide=True)` → `main.wide`) uses the room beside the text: three columns (`.page-grid`: 14rem · up to 60rem · 14rem, so the text sits in the middle of the window, where the other views have theirs), both sides sticky. Every other view (deck, scratch, search) gets the same top row width at that size, its content centred at the text width (46rem), so the breadcrumb and top actions stay put when switching modules.

- **Left** (`.rail-left`): "On this page" (`rail_toc_html`): the page's `[[toc]]` (copied from the rendered box), or without one its rendered `##`/`###` headings when there are at least `RAIL_TOC_MIN_HEADINGS` (3), links in them flattened. Otherwise empty; the column stays so the page keeps its place. At its end **↑ Back to top** (`.rail-top`), shown once the page has scrolled a screen down; it scrolls up and drops a `#hash` from the URL. `js/chrome.js` marks the section being read (`.active`, `aria-current="location"`): the last heading scrolled past (120px from the top), at the end of the page the last one in view. A column taller than the window scrolls on its own: a fade marks the edge with more (`.rail-more-above`/`-below`, set by `js/chrome.js`, CSS `mask-image`), and the TOC scrolls its column (never the page) to keep the marked entry in view.
- **Right** (`.rail-context`): at its top the page's actions (`.rail-actions`: Edit and ⋯, opening the same page menu as the header's), shown once the header's have scrolled out of view (IntersectionObserver in `js/chrome.js`); then Backlinks, Linked tasks and Related (`rail_copy`: the same sections with their ids as `data-id`; the anchors stay with the originals in `.page-foot`), and **Links to** (`outlinks_html`: the existing pages this page links to, A–Z by title; only in the column), so a page nothing links to still shows its connections.
- **Middle** (`.page-col`, a size container): header, meta row and text keep the reading width (46rem, about 85 characters); a code block, table or image alone in its paragraph (`<p class="img-block">`, marked on the parsed tokens by `render._images`) is as wide as the text unless its content needs more, then grows up to the middle column, centred on the text (`width: max-content; min-width: 100%; max-width: 100cqw`); an image within a sentence stays inside the text. A deck embed keeps the text width, like the deck and scratch pages. Text itself is never widened: longer lines read worse.
- The inline copies (`.page-content .toc`, `.page-foot`) and the breadcrumb's backlinks chip are hidden there. `main.wide` has wider side padding (3.5rem), so the badges fixed at the top left don't touch the breadcrumb on a window just over 1320px. Below 1320px the page is the single 800px column as before.
