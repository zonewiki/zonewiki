# Leaf

A local-first, browser-based wiki. Markdown files are served and rendered at runtime by a Python server. Pages can be edited directly on disk or via an in-browser editor. Full-text search runs server-side.

---

## Meta

- **Keep this file up to date** — whenever the project structure, commands, constraints, or conventions change, update `AGENTS.md` to reflect the new state immediately.

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in `pages/` are the wiki content
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
    ├── server.py           # Flask server — all routes live here
    └── public/
        ├── style.css       # All styling
        ├── leaf.js         # Client-side JS (editor toggle, live preview, search UI, image paste)
        ├── tasks.js        # Task app JS (dedicated task management UI)
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
| POST   | `/api/add-task`               | Append `- [ ] description` under `## Todo` in `tasks.md`; creates the file/section if absent. Returns 204. |
| GET    | `/tasks`              | Dedicated task app page (list view with filters, sorting, drag-and-drop) |
| GET    | `/api/tasks`          | JSON list of all tasks from `_tasks.json` |
| POST   | `/api/tasks`          | Create a task; returns JSON task object (201) |
| PUT    | `/api/tasks/<id>`     | Update a task (title, priority, tags, due_date, completed, recurrence) |
| DELETE | `/api/tasks/<id>`     | Delete a task; returns 204 |
| POST   | `/api/tasks/reorder`  | Update sort order; body `{ order: [id1, id2, …] }` |
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
| **Ctrl/⌘+1** | anywhere | Open scratch modal |
| **Ctrl/⌘+2** | anywhere | Quick-add todo under `## Todo` on `tasks` page |
| **Ctrl/⌘+3** | anywhere | Open today's daily note (create if absent) |
| **Ctrl/⌘+Alt+0** | anywhere | Navigate to Home (`/page/index`) |
| **Ctrl/⌘+Alt+1** | anywhere | Navigate to Scratch (`/page/_scratch`) 
| **Ctrl/⌘+Alt+3** | anywhere | Navigate to Daily (`/page/_daily`) |
| **Ctrl/⌘+Alt+2** | anywhere | Navigate to Tasks (`/page/tasks`) |
| **F2** | editor | Toggle preview pane |
| **Ctrl/⌘+S** | editor | Done — commit and go to page view |
| **Ctrl/⌘+Shift+X** | editor | Toggle checkbox on current line |
| **Ctrl/⌘+Delete** | page view | Delete current page (confirmation dialog) |
| **e** | page view | Navigate to edit mode |
| **p** | page view | Enter presentation mode |
| **Ctrl/⌘+/** | anywhere | Show keyboard shortcuts and snippets (Vimium-safe) |
| **?** | anywhere | Show keyboard shortcuts and snippets (fallback) |

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

- `GET /api/search?q=...` reads every `.md` file in `pages/`, scores matches by frequency and title match, returns JSON: `[{ slug, title, excerpt }]`.
- Excerpt shows ~150 chars around the first match, with the match term bolded.
- `GET /search?q=...` renders a full results page for direct links / no-JS fallback.
- Search is case-insensitive, matches partial words.

---

## Task App

A dedicated task management mini-app at `/tasks`, separate from the markdown-based wiki pages.

### Storage

Tasks are stored in `_tasks.json` in the wiki data directory (alongside markdown files). The file is a flat JSON array of task objects, committed to git on changes (except reorder, which is silent).

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
| `recurrence` | `{ type, interval }` / `null` | Recurring schedule |

### UI

- **Filter tabs**: All (open) · Today · Upcoming · Overdue · Completed
- **Sort options**: Manual order (drag-and-drop) · Due date · Created · Title
- **Filters**: Tag text filter
- **Drag-and-drop**: Available in "Manual order" sort mode; reorders tasks persistently
- **Inline completion**: Checkbox toggles mark task complete instantly
- **Edit modal**: Double-click or ✎ button opens a modal for full task editing
- **Recurring tasks**: When a recurring task is completed, a new instance is automatically created with the next due date

### Keyboard shortcut

- **Ctrl+2** — Quick-add task (opens modal from any page, creates via `/api/tasks`)
- **Ctrl+Alt+2** — Navigate to the Tasks app

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

**Always run the full test suite before and after making changes to `src/server.py`.**

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
- The server binds to `127.0.0.1` only (not `0.0.0.0`) to prevent LAN exposure. `compose.yaml` mirrors this with `127.0.0.1:5000:5000`.
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
