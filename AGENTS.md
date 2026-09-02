# Leaf

A local-first, browser-based wiki. Markdown files are served and rendered at runtime by a Python server. Pages can be edited directly on disk or via an in-browser editor. Full-text search runs server-side.

---

## Meta

- **Keep this file up to date** — whenever the project structure, commands, constraints, or conventions change, update `AGENTS.md` to reflect the new state immediately.

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in `pages/` are the wiki content. Deliberate exception: Deck tasks live in `_deck.json`/`_deck_completed.json` (structured fields, concurrency-safe writes) — still plain files, still git-versioned, but not markdown.
- **Editable two ways** — edit files in any text editor, or click "Edit" in the browser
- **Nice looking** — clean typography, readable line lengths, good contrast
- **Searchable** — server-side full-text search across all pages

---

## Tech Stack

| Concern             | Choice                                       |
|---------------------|----------------------------------------------|
| Server              | Python + Flask                               |
| Markdown render     | `markdown-it-py` (server renders to HTML)    |
| Syntax highlight    | `highlight.js` (loaded client-side from CDN) |
| Styling             | Custom CSS (classless, semantic HTML)        |
| In-browser editor   | Plain `<textarea>` with live preview         |
| Search              | Integrated into the Pages view (`/pages`)    |
| Storage             | Plain `.md` files in `pages/`               |
| Container           | Docker (Arch Linux base image)              |

No build step. No database. No auth (local use only).

---

## Project Structure

```
/
├── AGENTS.md               # This file (project documentation)
├── Dockerfile              # Arch Linux-based container image
├── compose.yaml            # Docker Compose configuration
├── run.sh                  # Helper script: build/up/down/logs
├── .env                    # Host data path configuration
└── src/
    ├── requirements.txt
    ├── server.py           # Flask server — wiki routes, rendering, shell
    ├── deck.py             # Deck module — task API, [[deck]] embed, storage
    ├── search.py           # BM25 search ranking (stemming, fuzzy, excerpts)
    └── public/
        ├── style.css       # All styling
        ├── leaf.js         # Client-side JS (editor toggle, live preview, search UI, image paste)
        ├── deck-core.js    # Pure deck logic (no DOM) — shared by deck.js and Node-run tests
        ├── deck.js         # Deck (task management) app JS, loaded via [[deck]] embed
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `WIKI_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_assets/`.

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.

---

## Docker / Container Setup

### Files

- **`Dockerfile`** — Arch Linux base, installs Python + pip, installs `requirements.txt`, copies the `src/` directory into `/app`.
- **`compose.yaml`** — runs the Leaf container, mounts `$WIKI_DATA` (from `.env`) to `/wiki-data` inside the container, passes `WIKI_DATA=/wiki-data` as an env var so the server knows where data lives.
- **`run.sh`** — convenience script wrapping `docker build` plus whichever Compose command is available: `docker compose` or `docker-compose`. When it falls back to legacy `docker-compose` v1, `up` first does a quiet `down --remove-orphans` to avoid the known `ContainerConfig` recreate bug on newer Docker versions.
- **`.env`** — sets `WIKI_DATA` (default `~/leaf`) for the host wiki directory. `run.sh` passes the current user's UID/GID into Compose automatically so the container can write to the mounted wiki directory.

### Commands

```bash
./run.sh            # build image and start Leaf (default: "up")
./run.sh build      # build Docker image only
./run.sh up         # build and start in detached mode
./run.sh down       # stop and remove containers
./run.sh logs       # follow container logs
```

Leaf is available at **http://localhost:5000** after `./run.sh up`.

### Data directory

Set `WIKI_DATA` in `.env` to any host path (default: `~/leaf`). `run.sh` expands a leading `~` to your home directory before creating directories and invoking Compose:

```
WIKI_DATA=~/leaf        # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`. Layout:
- `$WIKI_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$WIKI_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically)

Uploaded image URLs use `/_assets/<filename>` (served by a Flask route from `UPLOADS_DIR`), not the static file path.

### File paste and drag & drop

In the editor, pasting a file from the clipboard or dropping a file onto the textarea automatically uploads it via `POST /api/upload`. All file types are accepted. Images are inserted as `![name](url)`; other files as `[name](url)`. Multiple files dropped at once are uploaded sequentially. The upload route uses the original filename when available (falling back to MIME-type guessing). If a file with that name already exists in `_assets/`, a numeric suffix is appended (`file-2.pdf`, `file-3.pdf`, …).

---

## Routes

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/`                   | Redirects to `/page/index`                      |
| GET    | `/page/<slug>`        | Render a wiki page (markdown → HTML)            |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `pages/<slug>.md`        |
| GET    | `/new`                | Editor for a new page (slug entered by user)    |
| POST   | `/new`                | Create `pages/<slug>.md` and redirect to it     |
| GET    | `/search?q=...`       | Full-text search results page                   |
| GET    | `/api/search?q=...`   | JSON search results (used by the search UI)     |
| POST   | `/api/preview`        | Render posted markdown to HTML (for live editor preview) |
| GET    | `/api/pages`          | JSON list of all existing page slugs (for wikilink hints) |
| POST   | `/api/upload`         | Upload an image file; returns `{ url }` for insertion into markdown |
| POST   | `/api/save/<slug>`    | Write page content to disk silently (no git commit, no redirect). For full-page edits returns 204; for section edits returns JSON `{ file_hash, start_char, end_char }` with updated anchors, or 409 on conflict. Used by auto-save. |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox (`idx` param) in a page; returns 204. |
| GET    | `/api/deck`           | JSON list of all tasks from `_deck.json` |
| POST   | `/api/deck`           | Create a task; returns JSON task object (201). Accepts a `blocking` id list — reconciled into other tasks' `blocked_by` server-side. |
| PUT    | `/api/deck/<id>`      | Update a task (title, tags, due_date, defer_days, completed, recurrence via `rec_type`/`rec_interval`/`rec_from`, page, blocked_by, blocking, focus_date, wont_do, skip). Setting `defer_days` with a `due_date` derives `focus_date = due_date - defer_days` (schedules the task; nothing hides it). `wont_do: true` archives the task without spawning a recurring instance; `wont_do: false` reopens it (removing any skip-spawned instance). `skip: true` archives the occurrence as won't-do **and** spawns the next instance. A `blocking` id list is reconciled into other tasks' `blocked_by` in the same write. Writes that would create a `blocked_by` cycle are rejected with 400 JSON. |
| DELETE | `/api/deck/<id>`      | Delete a task; returns 204. Strips the deleted id from every remaining task's `blocked_by`. |
| POST   | `/api/deck/batch-delete` | Delete multiple tasks; body `{ ids: [id1, id2, …] }`; returns 204. Also strips dangling `blocked_by` references. |
| POST   | `/api/deck/batch-complete` | Complete/uncomplete multiple tasks; body `{ ids: [...], completed: bool }`; returns JSON array of updated tasks. Handles recurring spawn/cleanup. |
| POST   | `/api/deck/restore`   | Re-insert full task snapshots (undo after delete); body `{ tasks: [...] }`. Snapshots keep their original id/created_at/completion state; unknown fields are dropped, existing ids skipped. A `blocking` list per snapshot restores reverse dependency edges. Returns restored tasks (201). |
| POST   | `/api/deck/reorder`   | Update sort order. Body `{ order: [id1, id2, …] }` densely renumbers the given ids to `0..n-1` — used when the full unfiltered Backlog is visible, which also rebalances away any float drift from prior sparse updates. Body `{ updates: [{ id, sort_order }, …] }` sets specific ids to specific (possibly fractional) values without touching any other task — used when a tag/search/page filter narrows the visible list, so reordering never collides with the `sort_order` of tasks the filter is hiding. |
| POST   | `/api/deck/batch-tag` | Add/remove tags from multiple tasks; body `{ ids: [...], add: [...], remove: [...] }` |
| GET    | `/api/deck/due-count` | Count tasks due today + overdue, excluding blocked (open-blocker) tasks |
| POST   | `/api/scratch`    | Create a new scratch note (`<timestamp>_<slug>.md`); returns `{ slug }` |
| POST   | `/api/daily`    | Open or create today's daily note (`YYYY-MM-DD_daily.md`); returns `{ slug }` |
| GET    | `/page/_scratch`        | Auto-generated index of all scratch notes, newest first |
| GET    | `/page/_daily`        | Auto-generated index of all daily notes, newest first |
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug |
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |
| GET    | `/pages`              | Flat list of all pages, filterable client-side  |
| GET    | `/history/<slug>`     | Git commit log for a page (only when git is enabled) |
| GET    | `/revision/<slug>/<sha>` | Render a page at a specific git commit (read-only view) |
| POST   | `/restore/<slug>/<sha>` | Restore a page to a prior revision; creates a new commit |
| GET    | `/page/_deleted`      | Auto-generated list of pages deleted from git history, with one-click restore (git only) |

Every page view includes:
- **Breadcrumb** — a small green leaf logo plus `Leaf › Page Title` above the page header, with the branded home link leading back to the home page.
- **Backlinks** — section at the bottom listing all pages that contain a `[[wikilink]]` pointing to the current page. Shows "No pages link here." when empty.

---

## Rendering

- `server.py` reads `pages/<slug>.md`, converts it to HTML using `markdown-it-py`, and injects it into a shared HTML shell (Python f-string in `server.py` — not a separate template file).
- The HTML shell includes: nav bar, page title, content area, edit button, search bar.
- `highlight.js` auto-highlights fenced code blocks client-side after load.
- The shared shell renders the product brand as `Leaf`, with a small green inline SVG leaf logo in the breadcrumb home link.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:
- An `<h1>` with the page title (derived from the first `# Heading` in the file, or the slug as a fallback)
- An Edit button linking to `/edit/<slug>`

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body. Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before passing to `md.render()`.

### Wikilinks

- Wikilinks use the `[[Page Name]]` syntax. Nested pages can be linked as `[[journal/my-page]]`.
- Before passing markdown to `markdown-it-py`, run a regex pre-processing step that rewrites wikilinks to standard markdown links:
  - `[[Page Name]]` → `[Page Name](/page/page-name)` (slug = lowercase, spaces → hyphens)
  - `[[Page Name|Label]]` → `[Label](/page/page-name)`
- If the target page does not exist, render the link with a CSS class `wikilink-missing` so it can be styled differently (e.g. red or dashed underline). Clicking a missing wikilink redirects to `/edit/<slug>` so the page can be created immediately.
- The same pre-processing must run in the `/api/preview` endpoint so the live editor preview also renders wikilinks correctly.
- Add a route `GET /api/pages` that returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

### Image sizing

- `![alt|300](url)` sets the display width to 300px. markdown-it renders this as `<img alt="alt|300" ...>`, and a post-render regex (`_apply_img_sizes`) rewrites it to `<img alt="alt" style="max-width:100%;width:300px" ...>`.
- Works for any integer pixel width. `max-width:100%` ensures the image never overflows on narrow screens.

### Frontmatter

- A page may start with a leading `---`-fenced block declaring `tags:` (comma-separated, or a bracketed list `[a, b]`) and `tldr:` (a one-line summary, quotes optional). Any other key is parsed and ignored.
- `parse_frontmatter()` extracts it into a dict and returns the remaining content with the block removed. `_render_tags_pills_html()` renders a tag list as clickable pills linking to `/search?q=<tag>` (styled via the same `.task-tag` class Deck uses); `_render_frontmatter_html()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — with independent `show_tags`/`show_tldr` flags to omit either half.
- `render_md(text, *, show_tags=True, show_tldr=True)` calls these internally, so most call sites (`/api/preview` live preview, revision view, `_raw` snippets) get both tldr and tags rendered at the top of the article for free — those use the `<details>` form, since they have no meta row to place a toggle in.
- **`view_page` is the exception**: it parses frontmatter itself and renders tags plus a tldr toggle in the `page-meta-wrapper` row below the H1 instead — together in `.page-meta-left` (tags pills then the `tldr` label, left-aligned), with "Last changed"/commit button right-aligned in `.page-meta-right` (`justify-content: space-between` on the wrapper). It calls `render_md(..., show_tags=False, show_tldr=False)` so nothing frontmatter-related is duplicated in the article body.
- The view-page tldr toggle is **not** a `<details>` — it's a visually-hidden checkbox (`.tldr-toggle`) emitted *before* `.page-meta-wrapper`, a `<label for="tldr-toggle">` *inside* the row, and the `<p class="page-tldr">` *after* the row. Keeping those three as siblings is what makes `.tldr-toggle:checked ~ .page-tldr { display: block }` work, so expanding reveals the text on its own full-width line below while the label stays put on the tags line. A `<details>` can't do this: its content is nested inside it, so making the text full-width drags the summary down with it. Collapsed by default, CSS-only (no JS), and the revealed text uses `.95rem` rather than inheriting the row's `.75rem` meta size.
- `.page-meta-wrapper` is a normal-flow block rendered as a sibling right after `</header>` (not nested inside it, not absolutely positioned) — so `.page-header`'s `border-bottom` stays anchored directly under the H1/actions row, while the meta row can still grow (e.g. an expanded tldr) and push the article below it down instead of overlapping it. It uses a small negative top margin (`margin: -1.1rem 0 1.5rem`) to sit close under the header despite being a separate block, rather than living inside the header's own margin.
- `strip_h1()` skips over a leading frontmatter block before looking for the first non-empty line, so the H1 immediately after frontmatter is still correctly stripped (not duplicated below the page header).
- The **editor textarea always holds the raw, unparsed file** — including the frontmatter block verbatim — so editing it is just editing text like any other part of the file; there is no separate metadata-edit form. The live preview pane shows the parsed tldr/tags block (both, at the top of the article) as you type, via the same `render_md()` path — it has no separate meta row to place them in.
- `page_title()` is unaffected: its regex scans the whole file for the first `# ` line regardless of what precedes it, so frontmatter never confuses title extraction.

### Version history

- Git is installed in the Docker image. On startup, `git_setup()` initializes a bare repo in `WIKI_DATA` (if not already one), writes an empty `.gitignore`, and commits all existing files.
- Every write operation (Done/save, create, rename, delete, restore, upload) calls `git_commit()` or `git_commit_rename()` to stage and commit the change. **Auto-save (`/api/save`) is the exception** — it writes the file to disk but does not create a git commit. Assets are tracked like pages — a `git push` backs up everything.
- `GIT_ENABLED = shutil.which("git") is not None` — all git paths are no-ops when git is absent (graceful degradation).
- SHA values are validated with `SHA_RE = r'^[0-9a-f]{7,40}$'` before use.
- History link appears next to Edit button on page view and in the editor toolbar.

### Table of Contents

- `[[toc]]` on a line by itself (case-insensitive) is replaced with a `<nav class="toc">` block listing all `##` and `###` headings in the page as anchor links.
- The TOC is built before `process_wikilinks` runs. `[[toc]]` is replaced with a placeholder, rendered, then the placeholder `<p>` is swapped for the nav HTML.
- Heading anchors are generated by `anchors_plugin` from `mdit-py-plugins`. The `_heading_id()` helper replicates the plugin's slugify for the href values in the TOC.
- If the page has no `##`/`###` headings, `[[toc]]` renders nothing.

### Deck Embed

`[[deck]]` on a line by itself embeds the full interactive Deck (task management) app inline within the wiki page. Follows the same placeholder pattern as `[[toc]]`.

**Syntax:** `[[deck param=value param2=value2]]`

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `tag` | comma-separated tags | — | Pre-filters tasks by tag. Comma = AND (all tags must match, the narrowing default); pipe `a\|b` = OR (any tag matches). |
| `filter` | tab name: `focus`, `backlog`, `plan` (aka `upcoming`), `completed` (internal aliases `open`/`due`/`done` also accepted) | `focus` | Sets the active tab and **always wins when specified** — a `[[deck filter=…]]` embed opens on that tab on every load and refresh. Normalized to the client `data-filter` value by `_FILTER_ALIASES`. A URL `?filter=` overrides it. |
| `page` | slug or `self` | — | Filters to tasks linked to this page; `self` resolves to the current page's slug |
| `capacity` | integer 1–99 | 5 | Day-board WIP limit: Plan-tab day sections with at least this many tasks are flagged amber. |
| `unplanned` | `1`/`true`/`yes` or `0`/`false`/`no` | — | Pins the Backlog's "Unplanned only" toggle on or off, overriding the user's stored preference for this embed. Omitted, the client uses the stored value, which **defaults on** — so a full-inventory embed wants `unplanned=0`. |

**Examples:**
```markdown
[[deck]]
[[deck tag=work]]
[[deck page=self]]
[[deck filter=backlog]]
[[deck tag=urgent filter=upcoming]]
[[deck page=self tag=sprint-1]]
[[deck filter=backlog unplanned=0]]
```

**Rendered output:** The full deck app HTML (tabs, toolbar, task list, edit modal) is injected inline, plus `<script src="/public/deck-core.js" defer>` and `<script src="/public/deck.js" defer>` (order matters — deck.js reads `window.DeckCore`). Data attributes pass initialization params (`data-init-tag`, `data-init-page`, `data-init-filter`, `data-init-capacity`, `data-init-unplanned`).

**Implementation:**
- `_DECK_RE` detects `[[deck …]]` (case-insensitive).
- `_parse_tasks_params()` parses space-separated `key=value` pairs.
- `_build_deck_embed()` generates the full app HTML with data attributes.
- `render_md()` uses a placeholder approach: replace `[[deck]]` with `_DECK_PLACEHOLDER` before markdown processing, then swap `<p>PLACEHOLDER</p>` with the embed HTML after render.
- Only one `[[deck]]` embed per page is supported (subsequent ones are removed due to DOM ID conflicts).
- `deck.js` also reads URL query params (`?tag=`, `?filter=`, `?new=1`) as fallback for initialization.

**Typical usage:** Create `deck.md` with `[[deck]]` to get the full deck at `/page/deck`.

**Task ↔ Page linking:**
- Tasks have an optional `page` field (slug string) linking them to a wiki page.
- The task edit modal includes a "Linked page" input.
- `[[deck page=self]]` filters to tasks whose `page` field matches the current page slug.
- The `page` field is preserved by `_spawn_recurring()` for recurring tasks.
- Tasks with a `page` field show a clickable page link in their metadata.

---

## In-Browser Editor

- `/edit/<slug>` renders a split-pane view: `<textarea>` on the left, live preview on the right.
- Live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- The editor textarea preserves tabs and disables spell-check.
- The textarea receives focus automatically on page load.
- **Auto-save while typing** — debounced ~2s after the user stops typing, the editor silently POSTs to `POST /api/save/<slug>`, which writes the file to disk (no git commit, no redirect). An unsaved-changes indicator (`●` dot in the toolbar) appears while there are unsaved changes and disappears when auto-save completes.
- **"Done" button** — POSTs to `POST /edit/<slug>`, which writes the file, creates a git commit, and redirects to `/page/<slug>`. This is the only editor action that commits. The toolbar has no Cancel button.
- **Auto-save on navigate away** — on the `pagehide` event, if the textarea content differs from the last auto-saved content, `navigator.sendBeacon` POSTs to `POST /api/save/<slug>`. This silently preserves edits when the user follows a link or presses the back button (no git commit).
- **Section editing** — each rendered `<h2>`/`<h3>` has a ✎ button linking to `/edit/<slug>?section=N`. The editor loads only that section's text.
  - On GET, the server computes a `file_hash` (SHA-256 prefix) and the section's `(start_char, end_char)` character range within the full file, embedding them as hidden form fields.
  - On save (auto-save or Done), the server verifies the hash matches the current file, then does a direct string-slice replacement at `[start_char:end_char]`. If the file changed on disk (hash mismatch), the save is **rejected**: auto-save gets a 409 JSON error; Done redirects to a full-file editor with a conflict banner.
  - After each auto-save, the server returns updated `{ file_hash, start_char, end_char }` so the next auto-save uses fresh offsets.
  - This design avoids re-splitting the file on every save and eliminates the old index-based merge logic that could silently lose sections.
- **Undo** — all programmatic text insertions (list continuation, bold, snippets, wikilink autocomplete, etc.) use `document.execCommand('insertText')` so they integrate correctly with the browser's native undo stack.

### Editor smart helpers

- **Wikilink autocomplete** — typing `[[` opens a dropdown of existing page slugs filtered as you type. Arrow keys navigate, Enter or Tab inserts `[[slug]]`, Escape closes. Slugs are fetched once from `/api/pages` on load.
- **List continuation** — pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists). Pressing Enter on an empty list item exits the list.
- **File paste and drag & drop** — pasting a file from the clipboard or dropping files onto the editor textarea uploads them via `POST /api/upload`. Images insert as `![name](url)`, other files as `[name](url)`.
- **Snippets** — typing a trigger word then pressing Tab expands it inline. Built-in snippets: `/date` → `2026-03-22`, `/time` → `HH:MM`, `/datetime` → `2026-03-22 HH:MM`. Add more by extending the `snippets` object in `leaf.js`.
- **Task list checkboxes** — `- [ ] task` and `- [x] task` render as interactive checkboxes in page view (rendered via `mdit-py-plugins` `tasklists_plugin`). Clicking a checkbox POSTs to `POST /api/toggle-checkbox/<path:slug>` with `idx` (0-based checkbox index), which toggles the `[ ]`/`[x]` in the `.md` file and returns 204. In the editor, clicking a checkbox in the live preview directly updates the textarea content (no server call — changes persist on save).
- **Checkbox list continuation** — pressing Enter at the end of a `- [ ] task` or `- [x] task` line inserts `- [ ] ` on the next line (always unchecked). Pressing Enter on an empty checkbox item (`- [ ] ` with no text) exits the list.

### Rename

- The editor toolbar includes a "Rename" button that reveals an inline rename form (hidden by default).
- The form has a single text input pre-filled with the current slug, and a "Rename" submit button.
- On submit, POSTs to `POST /rename/<slug>` with field `new_name` (human-readable name accepted, converted to slug server-side).
- Server renames the file (`pages/<old>.md` → `pages/<new>.md`), updates wikilink references in all other pages (see Key Constraints), then redirects to `/page/<new-slug>`.
- Renaming `index` is forbidden — the server returns 400 if the old slug is `index`.
- If the new slug already exists, the server returns 400 (do not overwrite).
- Validate new slug with `safe_path()` before renaming.

---

## Page List

- `GET /pages` redirects to `/`. The search/browse UI lives in a modal dialog, not a dedicated page.
- **Search modal** — opened via the "Search" nav button or **Ctrl+P** (⌘P on Mac), closed with **Esc** or clicking the backdrop.
  - Backdrop click detection: `e.target === searchModal` (not coordinate comparison — coordinates break with `<select>` dropdowns).
  - Page data is loaded lazily on first open from `GET /api/pages-list` (JSON: slug, title, links, mtime) and cached for the session. Each list item shows the page title and slug — dates are not shown in the modal.
  - Filter input instantly narrows the list by title/slug; after 300ms debounce fetches `/api/search` for full-text **Content matches** with excerpts shown below the page list.
  - **Sort by** — Title A–Z, Title Z–A, Recently edited, Most linked.
  - **Unlinked only** checkbox — pages with zero incoming wikilinks.
  - First match is highlighted; **↑/↓** moves the highlight across both page results and content match cards; **Enter** opens the highlighted item.

---

## Scratch

- There is no nav bar. All navigation is via keyboard shortcuts.
- **Keybinding:** `Ctrl+1` opens the scratch modal.
- User types a title and submits → `POST /api/scratch` → server creates `<YYYY-MM-DD_HH-MM-SS>_<slug>.md` (local time) with `# Title\n\n` and returns `{ slug }` → browser navigates to `/edit/<slug>`.
- A `<dialog id="shortcuts-modal">` lists all keybindings, opened with `?`.
- **Scratch note filenames** match `SCRATCH_RE = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$"` e.g. `2026-03-24_18-50-08_my-note.md`. `is_scratch(slug)` identifies them.
- **`/page/_scratch`** — special auto-generated page (no `.md` file on disk) listing all scratch notes newest-first with title and timestamp. Not editable.
- **`/page/_daily`** — special auto-generated page (no `.md` file on disk) listing all daily notes newest-first with title and date. Not editable.
- **`/page/_deleted`** — special auto-generated page listing all pages that exist in git history but not on disk (deleted pages), newest deletion first. Each entry shows title, slug, deletion date, and a one-click Restore button that calls `POST /restore/<slug>/<sha>`. Only available when `GIT_ENABLED`. Navigating to a deleted page's URL directly also shows a restore prompt instead of redirecting to the editor.
- Appears in the search modal as a synthetic entry injected by `api_pages_list()` with title "Scratch" — matches searches for "scratch" or "_scratch". `_deleted` is similarly injected with title "Deleted Pages" (git only).
- Special pages (starting with `_`) are not filtered out of the search modal or backlink counts but are treated as regular pages otherwise.

---

## Keyboard Shortcuts

Defined in the `keydown` listener in `leaf.js`. Bare-key shortcuts are suppressed when focus is inside an `input`, `textarea`, `select`, or `[contenteditable]`.

| Shortcut | Context | Action |
|----------|---------|--------|
| **Ctrl/⌘+P** | anywhere | Open search modal |
| **Ctrl/⌘+N** | anywhere | Open new-page modal |
| **g** then **h/s/d/t/y** | anywhere | Navigate: Home / Scratch / Deck / Deck (tag=task) / Daily |
| **c** then **s/t/d** | anywhere | Create: scratch note / task-tagged deck item / daily note |
| **F2** | editor | Toggle preview pane |
| **Ctrl/⌘+S** | editor | Done — commit and go to page view |
| **Ctrl/⌘+Shift+X** | editor | Toggle checkbox on current line |
| **Ctrl/⌘+Delete** | page view | Delete current page (confirmation dialog) |
| **e** | page view | Navigate to edit mode |
| **p** | page view | Enter presentation mode |
| **Ctrl/⌘+/** | anywhere | Show keyboard shortcuts and snippets (Vimium-safe) |
| **?** | anywhere | Show keyboard shortcuts and snippets (fallback) |
| **n** | deck page | Open new task modal |

---

## Presentation Mode

- A **"Present"** button appears in the page header actions whenever a page contains at least one `##` heading.
- Clicking Present (or pressing **`p`**) opens a fullscreen dark overlay, splitting the article into slides at each `<h2>` boundary.
- Content before the first `<h2>` (if any) becomes an intro slide.
- Navigation: **← →** arrow keys, or click "← Prev" / "Next →" buttons. **Esc** exits.
- The slide counter (`1 / N`) is shown in the centre of the control bar.
- Slide content re-uses `.page-content` CSS so typography, code blocks, and links render correctly inside the overlay. CSS custom properties are overridden to force dark colours.
- Syntax highlighting is re-applied via `hljs.highlightElement` when each slide is shown.
- The Present button is hidden (via JS) on pages with no `##` headings.

---

## Search

- **Ranking is BM25**, implemented in pure Python (`src/search.py`, stdlib only — no database, no external deps). Pages and deck tasks are scored in **one combined index** so their relevance scores are directly comparable.
- `GET /api/search?q=...` collects every `.md` file in `pages/` **and** all deck task titles/descriptions as documents, ranks them, and returns JSON: `[{ slug, title, excerpt, href, type? }]`. `href` is the server-computed destination URL for the result; task results have `type: "task"` and `task_id` fields.
- **Multi-word queries** score each term independently (rarer terms weigh more via IDF); the whole phrase no longer has to appear verbatim.
- **Stemming** (`search.stem`) collapses inflections (run/runs/running, database/databases) so word variations match.
- **Partial words** match via prefix expansion; **typos** match via an edit-distance fuzzy fallback (`difflib`) only when a term has no exact/prefix hit.
- **Title boost**: title tokens are folded into the document token bag `TITLE_WEIGHT` (3) extra times, so title matches rank higher.
- Excerpt (`search.make_excerpt`) shows a window around the first match — the full query phrase if present, else the earliest query word — bolded via `<mark>`, HTML-escaped.
- **Performance**: page documents are tokenized once and cached in `server._search_doc_cache` keyed by slug, invalidated per-file on `mtime` change (so on-disk edits stay reflected). Deck task docs come from `deck.task_search_docs()`.
- `GET /search?q=...` renders a full results page for direct links / no-JS fallback.
- Search is case-insensitive. Returns up to 15 results (pages + tasks combined).
- **Selecting a task result** (search modal or `/search` page) opens `/page/deck?search=<title>&filter=<open|completed>` — the deck page with the task's title prefilled into the deck search filter (so only that task shows) and the tab set to match its state — rather than the bare `_deck` storage slug. Built by `resultHref()` in `leaf.js` and `_result_href()` in `server.py`.

---

## Deck (Task App)

A task management app embedded into wiki pages via the `[[deck]]` directive. There is no standalone route — the deck app lives inside any wiki page that contains `[[deck]]`. The typical setup is a `deck.md` page with `[[deck]]` as its content, accessible at `/page/deck`.

Multiple `[[deck]]` embeds per page are supported (each with different filter params). They receive unique instance IDs to avoid DOM conflicts. `deck.js` keeps a registry of every instance on the page; each holds its own task array, and after a successful write in one embed the others are refreshed (`refreshOtherInstances`) so all panes stay in sync without a reload. `window.refreshDeck` refreshes every instance, not just the last-initialized one.

### Architecture

The deck logic is in `src/deck.py` (Flask Blueprint), registered by `server.py` on startup. This module handles:
- Task CRUD API routes (`/api/deck/*`)
- `[[deck]]` embed HTML generation
- Task search integration (tasks appear in `/api/search` results)
- Task backlinks (pages show linked tasks)

### Storage

Tasks are stored in `_deck.json` in the wiki data directory (alongside markdown files). The file is a flat JSON array of task objects, committed to git on changes. Completed tasks live in `_deck_completed.json`; at startup, completed tasks from previous years are rolled into per-year archives (`_deck_completed-YYYY.json`) so the hot file stays small. Archived tasks remain in the search index but are not shown in the Completed tab.

All deck mutations are serialized through a module-level lock (`_LOCK`) and written atomically (temp file + `os.replace`), because the Flask dev server is threaded and unguarded concurrent read-modify-write on the JSON files would otherwise lose updates. Write-path reads use a strict reader that refuses to overwrite a present-but-unreadable file (raises `_DeckCorrupt` → 503) rather than treating a torn read as an empty list and wiping every task.

### Task fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | Auto-generated unique ID |
| `title` | string | Task title (required) |
| `description` | string | Optional notes/description |
| `tags` | string[] | Comma-separated tags |
| `due_date` | `"YYYY-MM-DD"` / `null` | Due date |
| `created_at` | ISO datetime | Auto-set on creation |
| `completed_at` | ISO datetime / `null` | Set when completed |
| `sort_order` | integer | Manual sort position (priority = position) |
| `recurrence` | `{ type, interval, from, anchor }` / `null` | Recurring schedule. `type` is `daily`/`weekly`/`monthly`/`yearly`. `from` is `"due"` (default — next date steps from the previous anchor date, calendar-aligned) or `"completion"` (steps from the completion day, for interval habits). `anchor` is `"due"` (default — advance the due date each cycle) or `"plan"` (advance the plan/focus date each cycle; spawned instance has no due date). |
| `defer_days` | integer ≥ 0 / `null` | "Plan N days before due." When set together with `due_date`, the engine derives `focus_date = due_date - defer_days` (0 = plan on the due date) on create, on due/lead change, and on every recurring spawn. It **schedules only — no task is ever hidden**. A bare `focus_date` change (e.g. a Plan-board drag, no `defer_days` in the payload) is a manual override and is not recomputed. |
| `page` | string / `null` | Wiki page slug this task is linked to (for `[[deck page=self]]` embeds) |
| `blocked_by` | string[] | Array of task IDs that block this task. Tasks with an open blocker are excluded from Focus and dimmed elsewhere; deleting a task strips it from all `blocked_by` lists. |
| `focus_date` | `"YYYY-MM-DD"` / `null` | Date the task is planned for; Focus tab shows tasks with `focus_date <= today`. Past focus dates are not removed — tasks stay in Focus as a working set — but cards show "planned Nd ago" and plans ≥3 days old are styled stale; a Focus-tab bar offers one-click "clear stale plans" (with undo). |
| `spawned_from` | string / `null` | For instances spawned by completing a recurring task: the id of the completed task. Used to remove exactly the spawned instance when the completion is undone. |
| `wont_do` | bool | `true` marks the task abandoned ("won't do"). Archived like a completed task (moves to `_deck_completed.json` with `completed_at` set) but shown with a "✗ won't do" badge; marking won't-do never spawns a recurring instance. |

### UI

- **Filter tabs** (labels): Focus · Plan · Backlog · Completed (internal `data-filter` values `focus`/`due`/`open`/`completed`). Precedence on load: URL `?filter=` > `[[deck filter=…]]` embed filter (always wins when specified) > tab remembered this session > `open` (Backlog) default. A filtered embed therefore always opens on its configured tab. For a plain `[[deck]]` (no filter), the active tab is remembered in `sessionStorage` (keyed by page path + embed instance), so a refresh lands on the tab the user last clicked while a fresh load in a new tab starts on Backlog.
- **Focus tab**: shows tasks with `focus_date <= today` and overdue/due-today tasks, excluding tasks with an open blocker (they return automatically when the blocker completes). Past focus dates are not removed — tasks stay in Focus as a working set — but a bar at the top counts plans ≥3 days old (`STALE_DAYS`) and clears them in one click, with undo. Sorted: overdue first, due today, then focused tasks.
- **Plan (`due`) tab — day board**: shows **all** open tasks grouped into day sections: `Overdue`, `Today`, `Tomorrow`, weekday headers for the next days (`today..+6`), `Later`, and `Someday` (tasks with no due date and no plan). Grouping is **plan-first** (`focus_date || due_date`) so a planned date overrides the deadline for placement. Day headers show a count and are flagged amber at/over `CAPACITY` (5). Date sections, `Later`, and `Someday` are **drop targets**: dragging a task card onto a day sets its `focus_date` to that day (re-plan); dropping on `Someday` clears the plan (`focus_date = null`); dropping on `Later` opens a native date picker (min today+7) for the target date. `Overdue` is display-only. Drag wiring is `setupBoardDragAndDrop` (per-card) plus list-level `dragover`/`drop` handlers attached once; manual-reorder drag (`setupDragAndDrop`) is used only on other tabs.
- **Inline date editing**: the ⏰ (due) and 📅 (plan) spans in each card's meta row open a native date picker on click — no modal needed. Keyboard: **f** plans the selected task for today, **F** for tomorrow (pressing again clears).
- **Sort options**: Manual order (drag-and-drop) · Created
- **Filters**: Tag text filter — comma-separated tags require all (AND); pipe-separated (`a|b`) match any (OR)
- **Drag-and-drop**: Available in "Manual order" sort mode; reorders tasks persistently
- **Inline completion**: Checkbox toggles mark task complete after a short grace period (cancellable)
- **Won't do**: each active task card has a ✗ button (hover-reveal) that archives the task as abandoned. Won't-do tasks appear in the Completed tab with a red "✗ won't do" badge instead of a "done:" date. Unchecking them there reopens them. An undo toast is shown after marking.
- **Skip occurrence**: recurring task cards additionally have a ⏭ button — archives this occurrence as won't-do but spawns the next instance, so the series continues. Undo (or reopening the won't-do entry) removes the spawned instance again.
- **No hiding**: the deck never hides a task. Every open task is reachable from Backlog or the Plan board at all times, and scheduling only decides *where* a task appears, not *whether* it does. (Earlier versions had a Deferred tab plus `start_date`/`defer_days` hiding; both are gone.)
- **Backlog "Unplanned only" toggle**: a checkbox in the toolbar, shown only on the Backlog tab and **on by default**, narrowing the list to tasks with no `focus_date` — the queue still awaiting a decision about *when* to do them. This is what makes the tab match its name: a backlog is work not yet claimed by a day, and unchecking widens it to every open task. A `due_date` alone does not count as planned: the day board only parks such a task on its deadline as a fallback (`planDate() = focus_date || due_date`), which looks placed but is the one day the work should not start. Planning a task is therefore what removes it from this list. Predicate is `DeckCore.isUnplanned` (client-only; the server never filters views). It is a standing preference, so it persists in `localStorage` keyed by page path + embed instance, unlike the active tab (`sessionStorage`); the key carries a `-v2` version suffix so a changed default reaches users who already toggled an earlier version of the filter. `[[deck unplanned=1|0]]` pins it either way for an embed regardless of what is stored.
- **Rejected writes** (e.g. dependency cycles) surface as an auto-dismissing toast (`showNotice`) with the server's error message.
- **Edit modal**: Double-click a card (or Enter on the selected task) opens a modal for full task editing, including a "From done" checkbox on recurring tasks (repeat from completion date) and a Links picker for blocked-by/blocking edges.
- **Blocked tasks**: cards whose `blocked_by` contains an open task are dimmed (`task-blocked-item`) and left out of Focus; they stay visible in Backlog/Plan so they can still be re-planned.
- **Recurring tasks**: completing one spawns the next instance (`spawned_from` links it to the completed task). The next due date steps from the previous due date (or from today when `recurrence.from` is `"completion"`) and is always advanced until ≥ today, so completing a long-overdue task never spawns an already-overdue instance. Monthly recurrence clamps to the real last day of the target month (Jan 31 → Feb 28/29); yearly steps 12 months and clamps the same way (Feb 29 → Feb 28 in non-leap years). A `defer_days` ("plan N days before due") rides along: the spawned instance re-derives `focus_date` from its own new due date, so it is planned for its own lead date. Un-completing removes exactly the spawned instance.
- **Delete undo**: deleting via keyboard shows an undo toast; undo POSTs the full snapshots to `/api/deck/restore`, preserving every field including ids, completion state, and dependency edges.

### Keyboard shortcut

- **g d** — Navigate to Deck (`/page/deck`)
- **c t** — Quick-add to deck with `tag=task` (works from any page)

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`).
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- Nav bar: fixed top, contains site title (links to home), search input, "New Page" button.
- No external CSS frameworks — keep `style.css` self-contained.
- Mobile breakpoint at `640px`: page content readable, tables/pre scroll horizontally, editor hidden with a message.

---

## Local Development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate   # Create and activate virtualenv
pip install -r src/requirements.txt                  # Install dependencies
python src/server.py                                 # Start server on http://localhost:5000
flask --app src/server.py run --debug                # Auto-reload during development
```

---

## Tests

Tests live in `tests/` and use pytest.

```bash
pip install pytest                                   # Install pytest (once, into the venv)
pytest tests/ -v                                     # Run all tests
pytest tests/test_section_editing.py -v              # Run section-editing tests only
```

`tests/test_deck_core.py` exercises the pure client logic in `src/public/deck-core.js` by running it under Node (`node -e`); those tests skip automatically when Node is not installed.

**Always run the full test suite before and after making changes to `src/server.py`.**

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix). A feature or fix is not complete until it has test coverage.

The tests use an in-process Flask test client and a temporary directory — no running server or Docker needed.

---

## Dependencies

`src/requirements.txt`:
```
flask>=3.0
markdown-it-py>=3.0
mdit-py-plugins>=0.4
```

`highlight.js` is loaded from a CDN in the HTML shell — no pip install needed.

---

## Key Constraints

- Do not use a database. Files are the only storage.
- Optional password auth: set `LEAF_PASSWORD` in `.env` to enable a login gate. If unset, no auth is required. `SECRET_KEY` can also be set to keep sessions across restarts; if absent, a random key is generated per-process. Routes `/login` (GET/POST) and `/logout` (POST) handle the session. A "Sign out" button is rendered in the top-right corner when auth is enabled.
- Optional git remote: set `GIT_REMOTE` in `.env` to a remote URL (e.g. `git@github.com:user/wiki.git`). On startup, `git_setup()` adds or updates `origin` to that URL, so `git push` works from the terminal.
- `ALLOW_REMOTE` is the single knob for network access. When unset, `run.sh` sets `BIND_ADDR=127.0.0.1` so Docker only exposes port 5000 on the host's loopback interface, and the host-header check rejects any non-localhost `Host` header. When `ALLOW_REMOTE=1`, `run.sh` sets `BIND_ADDR=0.0.0.0` (LAN-accessible port binding) and the host-header check is skipped. `compose.yaml` uses `${BIND_ADDR:-127.0.0.1}:5000:5000`.
- A `before_request` hook in `server.py` validates the `Host` header against `ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}`, defeating DNS rebinding attacks. Set `ALLOW_REMOTE=1` in `.env` to skip this check and allow access from any host.
- Do not add a build step or bundler.
- Slugs are derived from file paths relative to `PAGES_DIR`: `journal/my-page.md` → slug `journal/my-page`. Slugs are URL-safe (lowercase, hyphens, forward slashes only).
- Subdirectories are fully supported. `to_slug()` preserves `/` as a path separator while slugifying each component. `iter_pages()` does a recursive `rglob("*.md")` and skips `_*` subdirectories.
- Flask routes use `<path:slug>` to capture slugs containing `/`.
- `safe_path()` uses `target.relative_to(pages_root)` to validate the path is inside `PAGES_DIR` (allows subdirs).
- File writes call `path.parent.mkdir(parents=True, exist_ok=True)` before writing to auto-create subdirectories.
- `pages/index.md` is the home page and must always exist. Do not allow it to be deleted or renamed via the UI.
- All file writes use `open(..., 'w', encoding='utf-8')`. Validate that the resolved path is inside `pages/` before writing (prevent path traversal — use `Path.resolve()` and `relative_to()`).
- When a page is renamed, all other `.md` files are scanned for wikilinks referencing the old slug and updated to the new slug. Match both `[[Old Name]]` and `[[Old Name|Label]]` forms by comparing the slugified page name inside the brackets against the old slug. Replace only the page-name portion, preserving any `|Label` suffix.
