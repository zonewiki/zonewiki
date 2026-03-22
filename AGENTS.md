# Leave

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
| Search              | Server reads all `.md` files, scores matches |
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
├── .env                    # UID/GID for container user
└── wiki/
    ├── requirements.txt
    ├── server.py           # Flask server — all routes live here
    ├── pages/              # Wiki content (markdown files, volume-mounted)
    │   ├── index.md        # Home page (required)
    │   └── *.md
    └── public/
        ├── style.css       # All styling
        ├── wiki.js         # Client-side JS (editor toggle, live preview, search UI, image paste)
        └── uploads/        # Uploaded images (volume-mounted, persisted across rebuilds)
```

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.

---

## Docker / Container Setup

### Files

- **`Dockerfile`** — Arch Linux base, installs Python + pip, installs `requirements.txt`, copies the `wiki/` directory into `/app`.
- **`compose.yaml`** — runs the wiki container, mounts `$WIKI_DATA` (from `.env`) to `/wiki-data` inside the container, passes `WIKI_DATA=/wiki-data` as an env var so the server knows where data lives.
- **`run.sh`** — convenience script wrapping `docker build` and `docker compose` commands.
- **`.env`** — sets `UID` and `GID` (default `1000:1000`) used by the compose file.

### Commands

```bash
./run.sh            # build image and start wiki (default: "up")
./run.sh build      # build Docker image only
./run.sh up         # build and start in detached mode
./run.sh down       # stop and remove containers
./run.sh logs       # follow container logs
```

Wiki is available at **http://localhost:5000** after `./run.sh up`.

### Data directory

Set `WIKI_DATA` in `.env` to any host path (default: `./wiki-data`). The server reads this env var at startup:

```
WIKI_DATA=./wiki-data   # or /home/user/my-wiki, /mnt/nas/wiki, etc.
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
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug |
| GET    | `/pages`              | Flat list of all pages, filterable client-side  |

Every page view includes:
- **Breadcrumb** — `Wiki › Page Title` above the page header, linking back to the home page.
- **Backlinks** — section at the bottom listing all pages that contain a `[[wikilink]]` pointing to the current page. Shows "No pages link here." when empty.

---

## Rendering

- `server.py` reads `pages/<slug>.md`, converts it to HTML using `markdown-it-py`, and injects it into a shared HTML shell (Python f-string in `server.py` — not a separate template file).
- The HTML shell includes: nav bar, page title, content area, edit button, search bar.
- `highlight.js` auto-highlights fenced code blocks client-side after load.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:
- An `<h1>` with the page title (derived from the first `# Heading` in the file, or the slug as a fallback)
- An Edit button linking to `/edit/<slug>`

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body. Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before passing to `md.render()`.

### Wikilinks

- Wikilinks use the `[[Page Name]]` syntax.
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

### Editor smart helpers

- **Wikilink autocomplete** — typing `[[` opens a dropdown of existing page slugs filtered as you type. Arrow keys navigate, Enter or Tab inserts `[[slug]]`, Escape closes. Slugs are fetched once from `/api/pages` on load.
- **List continuation** — pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists). Pressing Enter on an empty list item exits the list.
- **Image paste** — pasting an image from the clipboard uploads it via `POST /api/upload` and inserts `![image](url)` at the cursor.
- **Snippets** — typing a trigger word then pressing Tab expands it inline. Built-in snippets: `/date` → `2026-03-22`, `/time` → `HH:MM`, `/datetime` → `2026-03-22 HH:MM`. Add more by extending the `snippets` object in `wiki.js`.

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

- `GET /pages` is the unified search and browse hub. Accepts `?q=` to pre-fill the search input (nav search Enter navigates here instead of `/search`).
- **Controls row:**
  - **Search/filter input** — instantly filters the page list by title/slug (client-side), and after 300ms debounce fetches `/api/search` to show full-text **Content matches** with excerpts above the list.
  - **Sort by** — Title A–Z (default), Title Z–A, Recently edited, Most linked.
  - **Unlinked only** — shows only pages with zero incoming wikilinks.
- Each row shows slug, creation date (`st_birthtime` → `st_ctime` fallback), and last-edited date (`st_mtime`), formatted `YYYY-MM-DD`.
- Backlink counts and timestamps are embedded server-side as `data-links` / `data-mtime`; sorting/filtering reads them client-side.

---

## Search

- `GET /api/search?q=...` reads every `.md` file in `pages/`, scores matches by frequency and title match, returns JSON: `[{ slug, title, excerpt }]`.
- Excerpt shows ~150 chars around the first match, with the match term bolded.
- The search bar in the nav fires requests on input (debounced 300ms) and shows a dropdown of results.
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
pip install -r wiki/requirements.txt                 # Install dependencies
python wiki/server.py                                # Start server on http://localhost:5000
flask --app wiki/server run --debug                  # Auto-reload during development
```

---

## Dependencies

`wiki/requirements.txt`:
```
flask>=3.0
markdown-it-py>=3.0
```

`highlight.js` is loaded from a CDN in the HTML shell — no pip install needed.

---

## Key Constraints

- Do not use a database. Files are the only storage.
- Do not add authentication — this is local use only.
- Do not add a build step or bundler.
- Slugs are derived from filenames: `my-page.md` → slug `my-page`. Slugs must be URL-safe (lowercase, hyphens only).
- When creating a page from the browser, sanitize the slug before writing to disk (strip anything not `[a-z0-9-]`).
- `pages/index.md` is the home page and must always exist. Do not allow it to be deleted or renamed via the UI.
- All file writes use `open(..., 'w', encoding='utf-8')`. Validate that the resolved path is inside `pages/` before writing (prevent path traversal — use `Path.resolve()` and check it starts with the `pages/` absolute path).
- When a page is renamed, all other `.md` files are scanned for wikilinks referencing the old slug and updated to the new slug. Match both `[[Old Name]]` and `[[Old Name|Label]]` forms by comparing the slugified page name inside the brackets against the old slug. Replace only the page-name portion, preserving any `|Label` suffix.
