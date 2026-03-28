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
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `WIKI_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_uploads/`.

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
- `$WIKI_DATA/_uploads/` — uploaded images, served at `/uploads/<filename>` (created automatically)

Uploaded image URLs use `/uploads/<filename>` (served by a Flask route from `UPLOADS_DIR`), not the static file path.

### Image paste

In the editor, pasting an image from the clipboard (e.g. a screenshot) automatically uploads it via `POST /api/upload` and inserts `![image](/public/uploads/<uuid>.ext)` at the cursor. Accepted types: PNG, JPEG, GIF, WebP, AVIF.

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
| POST   | `/api/save/<slug>`    | Save page content silently (no redirect); returns 204. Used by auto-save. |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox (`idx` param) in a page; returns 204. |
| POST   | `/api/scratch`  | Create a new scratch file (`<timestamp>_<slug>.md`); returns `{ slug }` |
| GET    | `/page/_scratch`      | Auto-generated index of all scratches, newest first |
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug |
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |
| GET    | `/pages`              | Flat list of all pages, filterable client-side  |

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
- If the target page does not exist, render the link with a CSS class `wikilink-missing` so it can be styled differently (e.g. red or dashed underline).
- The same pre-processing must run in the `/api/preview` endpoint so the live editor preview also renders wikilinks correctly.
- Add a route `GET /api/pages` that returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

---

## In-Browser Editor

- `/edit/<slug>` renders a split-pane view: `<textarea>` on the left, live preview on the right.
- Live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- "Save" POSTs the textarea content to `POST /edit/<slug>`.
- After save, server writes the file and redirects back to `/page/<slug>`.
- The editor textarea preserves tabs and disables spell-check.
- The textarea receives focus automatically on page load.
- **Auto-save on navigate away** — on the `pagehide` event, if the textarea content differs from what it was at load time, `navigator.sendBeacon` POSTs to `POST /api/save/<slug>` (no redirect, returns 204). This silently preserves edits when the user follows a link, presses the back button, or uses the `e` shortcut on another page.

### Editor smart helpers

- **Wikilink autocomplete** — typing `[[` opens a dropdown of existing page slugs filtered as you type. Arrow keys navigate, Enter or Tab inserts `[[slug]]`, Escape closes. Slugs are fetched once from `/api/pages` on load.
- **List continuation** — pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists). Pressing Enter on an empty list item exits the list.
- **Image paste** — pasting an image from the clipboard uploads it via `POST /api/upload` and inserts `![image](url)` at the cursor.
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
- **Keybinding:** `Ctrl+0` opens the scratch modal.
- User types a title and submits → `POST /api/scratch` → server creates `<YYYY-MM-DD_HH-MM-SS>_<slug>.md` (local time) with `# Title\n\n` and returns `{ slug }` → browser navigates to `/edit/<slug>`.
- A `<dialog id="shortcuts-modal">` lists all keybindings, opened with `?`.
- **Scratch filenames** match `SCRATCH_RE = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$"` e.g. `2026-03-24_18-50-08_my-scratch.md`. `is_scratch(slug)` identifies them.
- **`/page/_scratch`** — special auto-generated page (no `.md` file on disk) listing all scratches newest-first with title and timestamp. Not editable.
- Appears in the search modal as a synthetic entry injected by `api_pages_list()` with title "Scratch" — matches searches for "scratch" or "_scratch".
- Special pages (starting with `_`) are not filtered out of the search modal or backlink counts but are treated as regular pages otherwise.

---

## Keyboard Shortcuts

Defined in the `keydown` listener in `leaf.js`. Bare-key shortcuts are suppressed when focus is inside an `input`, `textarea`, `select`, or `[contenteditable]`.

| Shortcut | Context | Action |
|----------|---------|--------|
| **Ctrl/⌘+P** | anywhere | Open search modal |
| **Ctrl/⌘+1** | anywhere | Open scratch modal |
| **Ctrl/⌘+Alt+0** | anywhere | Navigate to Home (`/page/index`) |
| **Ctrl/⌘+Alt+1** | anywhere | Navigate to Scratch (`/page/_scratch`) |
| **Ctrl/⌘+Alt+2** | anywhere | Navigate to Tasks (`/page/tasks`) |
| **Ctrl/⌘+Alt+3** | anywhere | Navigate to State (`/page/state`) |
| **F2** | editor | Toggle preview pane |
| **Ctrl/⌘+S** | editor | Save (submit editor form) |
| **Ctrl/⌘+Shift+X** | editor | Toggle checkbox on current line |
| **e** | page view | Navigate to edit mode |
| **Ctrl/⌘+/** | anywhere | Show keyboard shortcuts and snippets (Vimium-safe) |
| **?** | anywhere | Show keyboard shortcuts and snippets (fallback) |

---

## Search

- `GET /api/search?q=...` reads every `.md` file in `pages/`, scores matches by frequency and title match, returns JSON: `[{ slug, title, excerpt }]`.
- Excerpt shows ~150 chars around the first match, with the match term bolded.
- `GET /search?q=...` renders a full results page for direct links / no-JS fallback.
- Search is case-insensitive, matches partial words.

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`).
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- Nav bar: fixed top, contains site title (links to home), search input, "New Page" button.
- No external CSS frameworks — keep `style.css` self-contained and under ~300 lines.

---

## Local Development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate   # Create and activate virtualenv
pip install -r src/requirements.txt                  # Install dependencies
python src/server.py                                 # Start server on http://localhost:5000
flask --app src/server.py run --debug                # Auto-reload during development
```

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
- The server binds to `127.0.0.1` only (not `0.0.0.0`) to prevent LAN exposure. `compose.yaml` mirrors this with `127.0.0.1:5000:5000`.
- A `before_request` hook in `server.py` validates the `Host` header against `ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}`, defeating DNS rebinding attacks.
- Do not add a build step or bundler.
- Slugs are derived from file paths relative to `PAGES_DIR`: `journal/my-page.md` → slug `journal/my-page`. Slugs are URL-safe (lowercase, hyphens, forward slashes only).
- Subdirectories are fully supported. `to_slug()` preserves `/` as a path separator while slugifying each component. `iter_pages()` does a recursive `rglob("*.md")` and skips `_*` subdirectories.
- Flask routes use `<path:slug>` to capture slugs containing `/`.
- `safe_path()` uses `target.relative_to(pages_root)` to validate the path is inside `PAGES_DIR` (allows subdirs).
- File writes call `path.parent.mkdir(parents=True, exist_ok=True)` before writing to auto-create subdirectories.
- `pages/index.md` is the home page and must always exist. Do not allow it to be deleted or renamed via the UI.
- All file writes use `open(..., 'w', encoding='utf-8')`. Validate that the resolved path is inside `pages/` before writing (prevent path traversal — use `Path.resolve()` and `relative_to()`).
- When a page is renamed, all other `.md` files are scanned for wikilinks referencing the old slug and updated to the new slug. Match both `[[Old Name]]` and `[[Old Name|Label]]` forms by comparing the slugified page name inside the brackets against the old slug. Replace only the page-name portion, preserving any `|Label` suffix.
