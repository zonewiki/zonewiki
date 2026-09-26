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
| Search              | BM25 in `search.py`; search modal (Ctrl+P) and `/search` |
| Storage             | Plain `.md` files in `$WIKI_DATA` (deck tasks in `_deck*.json`) |
| Container           | Podman (Ubuntu 26.04 LTS base image)        |

No build step. No database. Auth is optional (`LEAF_PASSWORD`, see Key Constraints).

---

## Project Structure

```
/
├── AGENTS.md               # This file (project documentation)
├── Containerfile           # Ubuntu 26.04 LTS-based container image (built by podman)
├── Containerfile.e2e       # Test-only image: Playwright + Chromium for browser tests, Node for deck-core tests
├── run.sh                  # Helper script: build/up/down/logs/test/e2e (drives podman directly)
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
        ├── favicon.svg     # Browser tab icon
        ├── icon.svg        # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
        └── manifest.json   # Web app manifest (Add to home screen)
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `WIKI_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_assets/`.

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.

---

## Podman / Container Setup

### Files

- **`Containerfile`** — Ubuntu 26.04 LTS base, installs Python + pip via `apt`, installs `requirements.txt`, copies the `src/` directory into `/app`. Built with `podman build` (podman reads `Containerfile` by default).
- **`run.sh`** — convenience script driving `podman build` and `podman run` directly (no Compose dependency). It reads `WIKI_DATA` and the optional env vars (`LEAF_PASSWORD`, `SECRET_KEY`, `GIT_REMOTE`, `ALLOW_REMOTE`) from `.env`, mounts `$WIKI_DATA` to `/wiki-data`, and passes `WIKI_DATA=/wiki-data` as an env var so the server knows where data lives. `up` uses `--replace`, so re-running it rebuilds and restarts the existing container.
- **`.env`** — sets `WIKI_DATA` (default `~/leaf`) for the host wiki directory. In rootless podman (the normal case) in-container root maps to the host user, so files written to the mounted wiki directory are owned by the host user automatically; `run.sh` only passes an explicit `--user UID:GID` when podman runs rootful. When SELinux is enforcing, the volume is mounted with `:Z` relabelling.

### Commands

```bash
./run.sh            # build image and start Leaf (default: "up")
./run.sh build      # build the container image only
./run.sh up         # build and start in detached mode
./run.sh down       # stop and remove the container
./run.sh logs       # follow container logs
./run.sh test       # run unit/API tests inside the app image (extra args go to pytest; a tests/… path replaces the default whole suite)
./run.sh e2e        # run browser tests + deck-core.js (Node) tests in the test image (extra args go to pytest)
```

Leaf is available at **http://localhost:5000** after `./run.sh up`.

### Mobile / phone access

Set `ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces — `run.sh` then maps `0.0.0.0:5000` and the server's host-header check is skipped. Phones on the same network reach Leaf at `http://<host-LAN-IP>:5000`. The UI is responsive down to phone widths and the editor works on mobile. The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one. Without it, full-screen views (the deck edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard. iOS Safari ignores the setting. There is no auth by default — for LAN use consider setting `LEAF_PASSWORD` in `.env` to get a login gate.

### Version

Leaf's version is the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `src/` had uncommitted changes. It shows at the bottom of the shortcuts modal (`?`), in a `<meta name="generator">` tag, and at `/api/version`. The image has no `.git`, so `run.sh` computes the string and passes it as the `LEAF_VERSION` build arg (the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer). Without the container, `server.py` reads the same string from git itself (`_source_version()`); `$LEAF_VERSION` wins when set, and `unknown` is the fallback.

### Home-screen app

`src/public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `shell()`) give "Add to home screen" a proper Leaf name and icon. Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie. The PNGs are rendered once from `src/public/icon.svg` (full-bleed green square, leaf inside the central 80% "maskable" safe zone) and committed, so there is still no build step. To change the icon, edit the SVG and re-render the PNGs, e.g. with a screenshot in the Playwright image (`leaf-e2e`). Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab. iOS Safari is more lenient.

### Data directory

Set `WIKI_DATA` in `.env` to any host path (default: `~/leaf`). `run.sh` expands a leading `~` to your home directory before creating directories and starting the container:

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
| GET    | `/deck`               | Redirect to the page that embeds the deck, keeping the query string (`deck_page_slug()`: `deck.md` if it has an embed, else the page whose embed has no `tag=` filter, then by slug; falls back to `/page/deck`). Every link to "the deck" goes through it. With `?list=<name>` it goes to the page embedding `[[deck list=<name>]]` instead (dropping `list` from the query), or, if none does, to the main deck with `?list=` kept, which then shows that list. `[[deck list=…]]` embeds never count as the main deck. |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `pages/<slug>.md`        |
| GET    | `/new`                | Editor for a new page (slug entered by user)    |
| POST   | `/new`                | Create `pages/<slug>.md` and redirect to it     |
| GET    | `/search?q=...`       | Full-text search results page                   |
| GET    | `/api/search?q=...`   | JSON search results (used by the search UI)     |
| POST   | `/api/preview`        | Render posted markdown to HTML (for live editor preview) |
| GET    | `/api/pages`          | JSON list of all existing page slugs (for wikilink hints) |
| GET    | `/api/version`        | `{ version }`: Leaf's own version (see Version) |
| POST   | `/api/upload`         | Upload an image file; returns `{ url }` for insertion into markdown |
| POST   | `/api/save/<slug>`    | Write page content to disk silently (no git commit, no redirect). For full-page edits returns 204; for section edits returns JSON `{ file_hash, start_char, end_char }` with updated anchors, or 409 on conflict. Used by auto-save. |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox (`idx` param) in a page; returns 204. |
| GET    | `/api/deck`           | JSON list of all tasks from `_deck.json` |
| POST   | `/api/deck`           | Create a task; returns JSON task object (201). |
| PUT    | `/api/deck/<id>`      | Update a task (title, tags, due_date, start_date, completed, recurrence via `rec_type`/`rec_interval`/`rec_from`, page, wont_do, skip). The two dates are independent: setting one never moves the other. `wont_do: true` archives the task without spawning a recurring instance; `wont_do: false` reopens it (removing any skip-spawned instance). `skip: true` archives the occurrence as won't-do **and** spawns the next instance. |
| DELETE | `/api/deck/<id>`      | Delete a task; returns 204. |
| POST   | `/api/deck/batch-delete` | Delete multiple tasks; body `{ ids: [id1, id2, …] }`; returns 204. |
| POST   | `/api/deck/batch-complete` | Complete/uncomplete multiple tasks; body `{ ids: [...], completed: bool }`; returns JSON array of updated tasks. Handles recurring spawn/cleanup. |
| POST   | `/api/deck/restore`   | Re-insert full task snapshots (undo after delete); body `{ tasks: [...] }`. Snapshots keep their original id/created_at/completion state; unknown fields are dropped, existing ids skipped. Returns restored tasks (201). |
| POST   | `/api/deck/reorder`   | Update sort order. Body `{ order: [id1, id2, …] }` densely renumbers the given ids to `0..n-1` — used when the full unfiltered Open tab is visible, which also rebalances away any float drift from prior sparse updates. Body `{ updates: [{ id, sort_order }, …] }` sets specific ids to specific (possibly fractional) values without touching any other task — used when a tag/search/page filter narrows the visible list, so reordering never collides with the `sort_order` of tasks the filter is hiding. |
| POST   | `/api/deck/batch-tag` | Add/remove tags from multiple tasks; body `{ ids: [...], add: [...], remove: [...] }` |
| GET    | `/api/deck/due-count` | Count tasks due today + overdue |
| GET    | `/api/deck/completed-count` | `{ count }` of tasks in the Completed view (current year), for its link. `?list=<name>` counts that list's; without it, tasks in no list. |
| POST   | `/api/scratch`    | Create a new scratch note (`<timestamp>_<slug>.md`); returns `{ slug }` |
| GET    | `/page/_scratch`        | Auto-generated index of all scratch notes, newest first |
| POST   | `/rename/<slug>`      | Rename a page: moves the file, redirects to new slug |
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |
| GET    | `/pages`              | Redirects to `/` (browsing lives in the search modal) |
| GET    | `/history/<slug>`     | Git commit log for a page (only when git is enabled) |
| GET    | `/revision/<slug>/<sha>` | Render a page at a specific git commit (read-only view) |
| POST   | `/restore/<slug>/<sha>` | Restore a page to a prior revision; creates a new commit |
| GET    | `/page/_deleted`      | Auto-generated list of pages deleted from git history, with one-click restore (git only) |

Every page view includes:
- **Breadcrumb** — a small green leaf logo plus `Leaf › Page Title` above the page header, with the branded home link leading back to the home page.
- **Backlinks** — section at the bottom listing all pages that contain a `[[wikilink]]` pointing to the current page. Shows "No pages link here." when empty.
- **Backlinks chip** — when a page has backlinks, a `← N` chip sits at the top right of the breadcrumb. On desktop, hovering it lists the linking pages (`.bl-tooltip`) and clicking jumps to the Backlinks section. Touch devices (`hover: none`) have no hover, so there a tap toggles the list (`.bl-open`, delegated click handler in `leaf.js`) instead of scrolling down. A second tap or a tap elsewhere closes it. The hover rules are wrapped in `@media (hover: hover)` because a tap leaves `:hover` stuck, which would fight the toggle.
- **Linked tasks** — open deck tasks whose title/description link to the page. Each entry is a single link to that task in the deck (`/deck?task=<id>&view=open`, plus `&list=<name>` for a task in a separate list — `_task_href()`); links inside the task title are flattened to their labels (`_plain_task_title`) so anchors are never nested.

---

## Rendering

- `server.py` reads `pages/<slug>.md`, converts it to HTML using `markdown-it-py`, and injects it into a shared HTML shell (Python f-string in `server.py` — not a separate template file).
- The HTML shell includes: status badges, breadcrumb, page header (title, Edit/History/Present), content area, backlinks, and the search/shortcut/scratch modals. There is no nav bar (see Scratch).
- `highlight.js` auto-highlights fenced code blocks client-side after load.
- The shared shell renders the product brand as `Leaf`, with a small green inline SVG leaf logo in the breadcrumb home link.

### Autolinks and callouts

- **Bare URLs become links** (`gfm_autolink_plugin`): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link. Code spans, fenced code and existing links are untouched. It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **Callouts** (markdown-it's `alerts` option, GitHub/Obsidian syntax): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line. Styled in `style.css` (GitHub colours, a `--alert` variable per kind, dark variants). Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

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
- Code is exempt: `render_md()` masks fenced code blocks and inline code spans (`_mask_code`, private-use-character tokens) before running the wikilink, `[[toc]]` and `[[deck]]` pre-processors and restores them (`_unmask_code`) before `md.render()`, so directives inside code render literally. The TOC also ignores `##` lines inside fences.
- Add a route `GET /api/pages` that returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

### Image sizing

- `![alt|300](url)` sets the display width to 300px. markdown-it renders this as `<img alt="alt|300" ...>`, and a post-render regex (`_apply_img_sizes`) rewrites it to `<img alt="alt" style="max-width:100%;width:300px" ...>`.
- Works for any integer pixel width. `max-width:100%` ensures the image never overflows on narrow screens.

### Frontmatter

- A page may start with a leading `---`-fenced block declaring `tags:` (comma-separated, or a bracketed list `[a, b]`) and `tldr:` (a one-line summary, quotes optional). Any other key is parsed and ignored.
- `parse_frontmatter()` extracts it into a dict and returns the remaining content with the block removed. `_render_tags_pills_html()` renders a tag list as clickable pills linking to `/search?q=<tag>` (styled via the same `.task-tag` class Deck uses); `_render_frontmatter_html()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — with independent `show_tags`/`show_tldr` flags to omit either half.
- `render_md(text, *, show_tags=True, show_tldr=True)` calls these internally, so most call sites (`/api/preview` live preview, revision view, `_raw` snippets) get both tldr and tags rendered at the top of the article for free — those use the `<details>` form, since they have no meta row to place a toggle in.
- **`view_page` is the exception**: it parses frontmatter itself and renders tags plus a tldr toggle in the `page-meta-wrapper` row below the H1 instead — together in `.page-meta-left` (tags pills then the `tldr` label, left-aligned), with "Last changed" right-aligned in `.page-meta-right`, followed, when the file is uncommitted, by a link-styled **Commit** (`_commit_form_html(..., inline=True)`, `.commit-link`) on the same line rather than a separate button row (`justify-content: space-between` on the wrapper). It calls `render_md(..., show_tags=False, show_tldr=False)` so nothing frontmatter-related is duplicated in the article body.
- The view-page tldr toggle is **not** a `<details>` — it's a visually-hidden checkbox (`.tldr-toggle`) emitted *before* `.page-meta-wrapper`, a `<label for="tldr-toggle">` *inside* the row, and the `<p class="page-tldr">` *after* the row. Keeping those three as siblings is what makes `.tldr-toggle:checked ~ .page-tldr { display: block }` work, so expanding reveals the text on its own full-width line below while the label stays put on the tags line. A `<details>` can't do this: its content is nested inside it, so making the text full-width drags the summary down with it. Collapsed by default, CSS-only (no JS), and the revealed text uses `.95rem` rather than inheriting the row's `.75rem` meta size.
- `.page-meta-wrapper` is a normal-flow block rendered as a sibling right after `</header>` (not nested inside it, not absolutely positioned) — so `.page-header`'s `border-bottom` stays anchored directly under the H1/actions row, while the meta row can still grow (e.g. an expanded tldr) and push the article below it down instead of overlapping it. It uses a small negative top margin (`margin: -1.1rem 0 1.5rem`) to sit close under the header despite being a separate block, rather than living inside the header's own margin.
- `strip_h1()` skips over a leading frontmatter block before looking for the first non-empty line, so the H1 immediately after frontmatter is still correctly stripped (not duplicated below the page header).
- The **editor textarea always holds the raw, unparsed file** — including the frontmatter block verbatim — so editing it is just editing text like any other part of the file; there is no separate metadata-edit form. The live preview pane shows the parsed tldr/tags block (both, at the top of the article) as you type, via the same `render_md()` path — it has no separate meta row to place them in.
- `page_title()` returns the first `# ` heading in the file, skipping a leading frontmatter block and fenced code (so a `# comment` in a code example is never the title), else a title derived from the slug.

### Version history

- Git is installed in the container image. On startup, `git_setup()` initializes a bare repo in `WIKI_DATA` (if not already one), writes an empty `.gitignore`, and commits all existing files.
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
| `view` | tab name: `focus`, `open`, `plan` (aka `upcoming`), `completed` (`all` and `backlog`, the Open tab's former labels, and internal aliases `due`/`done` also accepted) | Focus (or the tab remembered this session) | Sets the active tab and **always wins when specified** — a `[[deck view=…]]` embed opens on that tab on every load and refresh. Normalized to the client `data-filter` value by `_FILTER_ALIASES`. A URL `?view=` overrides it. `filter` (embed and URL) is the parameter's old name and still accepted; `view` wins if both are given. |
| `capacity` | integer 1–99 | 5 | Day-board WIP limit: Plan-tab day sections with at least this many tasks are flagged amber. |
| `list` | list name | — | Makes the embed a **separate list**: it shows only tasks whose `list` field is that name, and new tasks created in it get the list. Every embed *without* `list=` shows only tasks in no list, and list tasks are also left out of the due badge and the main Completed count. Names are cleaned like tags but lowercased (`_clean_list`). Combines with `tag`/`view`. |
| `unplanned` | `1`/`true`/`yes` or `0`/`false`/`no` | — | Pins the Open tab's "Unplanned only" toggle on or off, overriding the user's stored preference for this embed. Omitted, the client uses the stored value, which **defaults off** — so a planning-queue embed wants `unplanned=1`. |

**Examples:**
```markdown
[[deck]]
[[deck tag=work]]
[[deck view=open]]
[[deck tag=urgent view=upcoming]]
[[deck list=groceries tag=weekly]]
[[deck view=open unplanned=1]]
[[deck list=groceries]]
```

**Rendered output:** The full deck app HTML (tabs, toolbar, task list, edit modal) is injected inline, plus `<script src="/public/deck-core.js" defer>` and `<script src="/public/deck.js" defer>` (order matters — deck.js reads `window.DeckCore`). Data attributes pass initialization params (`data-init-tag`, `data-init-list`, `data-init-view`, `data-init-capacity`, `data-init-unplanned`).

**Implementation:**
- All of it lives in `deck.py`. `_DECK_RE` detects `[[deck …]]` (case-insensitive), and `_parse_tasks_params()` parses space-separated `key=value` pairs.
- `_build_deck_embed(params, page_slug, instance_id)` generates the full app HTML with data attributes. Element ids get a `-<n>` suffix for every embed after the first, so several embeds can share a page.
- `render_md()` uses a placeholder approach: `process_deck_embeds()` replaces each `[[deck]]` with a numbered placeholder before markdown processing, and `inject_deck_embeds()` swaps each `<p>PLACEHOLDER</p>` for its embed HTML after render.
- `deck.js` also reads URL query params (`?tag=`, `?view=`, `?new=1`) as fallback for initialization.

**Typical usage:** Create `deck.md` with `[[deck]]` to get the full deck at `/page/deck`. The deck can live on any page, though: links to it (search results, Linked tasks, the due badge, `g d`, `c t`) use `/deck`, which finds the embedding page. Hard-coding `/page/deck` once sent wikis without `deck.md` to the editor for an empty page.

**Task ↔ Page linking:** a task links to a page with a `[[wikilink]]` (or `/page/` link) in its title or notes, which lists it under that page's "Linked tasks". There used to be a separate `page` field with a `[[deck page=self]]` filter; it was removed because it duplicated wikilinks (linking) and tags or lists (per-page views), and suggested isolation it did not provide. `_normalize_task()` turns a stored `page` into `[[slug]]` appended to the notes (unless the title or notes already link there), and `_migrate_deck_files()` rewrites the files once. `page=` in an embed is ignored, and so is `page` in API bodies.

---

## In-Browser Editor

- `/edit/<slug>` renders a split-pane view: `<textarea>` on the left, live preview on the right.
- Live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- **Mobile editor** — on screens ≤640px the editor is not hidden; it becomes a full-screen stacked layout (textarea on top, preview below when toggled). The toolbar gains a **Preview** button (`#mobile-preview-btn`, class `mobile-only`) that calls the same `togglePreview()` as F2. The textarea font is 16px on mobile so iOS Safari doesn't zoom in on focus. Rename… and Delete move into a **⋯ dropdown** (`#editor-more-btn`, `.editor-more`) so the toolbar fits on one line; on wider screens `.editor-more` is `display: contents`, so the buttons stay inline.
- The editor textarea preserves tabs and disables spell-check.
- The textarea receives focus automatically on page load.
- **Auto-save while typing** — debounced ~2s after the user stops typing, the editor silently POSTs to `POST /api/save/<slug>`, which writes the file to disk (no git commit, no redirect). An unsaved-changes indicator (`●` dot in the toolbar) appears while there are unsaved changes and disappears when auto-save completes.
- **"Done" button** — POSTs to `POST /edit/<slug>`, which writes the file, creates a git commit, and redirects to `/page/<slug>`. This is the only editor action that commits. The toolbar has no Cancel button.
- **Auto-save on navigate away** — on the `pagehide` event, if the textarea content differs from the last auto-saved content, `navigator.sendBeacon` POSTs to `POST /api/save/<slug>`. This silently preserves edits when the user follows a link or presses the back button (no git commit).
- **Section editing** — each rendered `<h2>`/`<h3>` has a ✎ button linking to `/edit/<slug>?section=N`. The editor loads only that section's text.
  - On GET, the server computes a `file_hash` (SHA-256 prefix) and the section's `(start_char, end_char)` character range within the full file, embedding them as hidden form fields.
  - On save (auto-save or Done), the server verifies the hash matches the current file, then does a direct string-slice replacement at `[start_char:end_char]`. If the file changed on disk (hash mismatch), the save is **rejected**: auto-save gets a 409 JSON error; Done redirects to a full-file editor with a conflict banner.
  - After each auto-save, the server returns updated `{ file_hash, start_char, end_char }` so the next auto-save uses fresh offsets.
  - The section's trailing blank lines are preserved on every splice (the editor loads the section with them stripped), including the conflict banner's "Save my version" (`force_section`), so the next heading never gets glued onto the section's last line.
  - This design avoids re-splitting the file on every save and eliminates the old index-based merge logic that could silently lose sections.
- **Undo** — all programmatic text insertions (list continuation, bold, snippets, wikilink autocomplete, etc.) use `document.execCommand('insertText')` so they integrate correctly with the browser's native undo stack.

### Editor smart helpers

- **Wikilink autocomplete** — typing `[[` opens a dropdown of existing page slugs filtered as you type. Arrow keys navigate, Enter or Tab inserts `[[slug]]`, Escape closes. Slugs are fetched once from `/api/pages` on load.
- **List continuation** — pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists). Pressing Enter on an empty list item exits the list.
- **File paste and drag & drop** — pasting a file from the clipboard or dropping files onto the editor textarea uploads them via `POST /api/upload`. Images insert as `![name](url)`, other files as `[name](url)`.
- **URL paste** (`pasteUrlAsLink` in `leaf.js`, also used by the deck's task notes): pasting a bare URL over selected text makes `[selection](url)`; otherwise a prompt asks for a label (none, or cancel, inserts the plain URL, which autolinks on render). The insert goes through `execCommand('insertText')`, so Ctrl+Z undoes it; it once assigned `textarea.value` and wiped the undo history.
- **Snippets** — typing a trigger word then pressing Tab expands it inline. Built-in snippets: `/date` → `2026-03-22`, `/time` → `HH:MM`, `/datetime` → `2026-03-22 HH:MM`. Add more by extending the `snippets` object in `leaf.js`.
- **Task list checkboxes** — `- [ ] task` and `- [x] task` render as interactive checkboxes in page view (rendered via `mdit-py-plugins` `tasklists_plugin`; `_enable_task_checkboxes` removes the plugin's `disabled` attribute from those inputs only). Clicking a checkbox POSTs to `POST /api/toggle-checkbox/<path:slug>` with `idx` (0-based checkbox index), which toggles the `[ ]`/`[x]` in the `.md` file and returns 204 (400 for a missing/non-integer/negative idx). In page view each toggle shows an **undo toast** ("Checked “…” · Undo"; undo is a second toggle). The toggle requests are chained (`toggleCheckboxOnServer`), so an undo can never overtake the toggle it reverts. In the editor, clicking a checkbox in the live preview edits the textarea via `execCommand('insertText')` (undoable, triggers preview/auto-save).
  - **Index mapping must match the renderer.** Only task-list items count: a list marker (`-`/`*`/`+`/`1.`/`1)`, optionally inside `>` blockquotes) followed by `[ ]`/`[x]`, whitespace and text. Fenced code and a leading frontmatter block are skipped; brackets in prose or later on the same line are not checkboxes. Server: `_TASK_ITEM_RE` / `_task_item_lines()`; client: `TASK_ITEM_RE` / `taskCheckboxOffsets()` in `leaf.js`. Keep the two in sync.
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
  - **Sort by** — Recently edited (default, so an empty query works as a quick switcher), Title A–Z, Title Z–A, Most linked. Applies while the query is empty; a sort picked by hand is remembered in `localStorage` (`leaf-search-sort`).
  - **Unlinked only** checkbox — pages with zero incoming wikilinks.
  - First match is highlighted; **↑/↓** moves the highlight across both page results and content match cards; **Enter** opens the highlighted item.

---

## Scratch

- There is no nav bar; navigation is via keyboard shortcuts. Phones have no keyboard, so at ≤640px a round **search button** (`MOBILE_SEARCH` in `server.py`, `.mobile-search`) sits in the bottom-right corner and opens the search modal (`openSearchModal`, the Ctrl+P action). `shell(..., search_button=False)` omits it, and the editor and the login page leave it out. It is hidden via CSS `:has()` whenever an input/textarea/select has focus, because the on-screen keyboard owns the bottom edge then. `main` and the toasts get extra bottom space so the button never covers them.
- **Status badges** (`.top-badges`: remote 🌐, deck due count, uncommitted count) are fixed at the top-left on desktop. At ≤640px they join the page flow above the breadcrumb, so they no longer cover it, and 🌐 is hidden, since on a phone remote access is a given.
- **Keybinding:** **c** then **s** creates a scratch note (see Keyboard Shortcuts).
- User types a title and submits → `POST /api/scratch` → server creates `<YYYY-MM-DD_HH-MM-SS>_<slug>.md` (local time) with `# Title\n\n` and returns `{ slug }` → browser navigates to `/edit/<slug>`.
- A `<dialog id="shortcuts-modal">` lists all keybindings, opened with `?`.
- **Scratch note filenames** match `SCRATCH_RE = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$"` e.g. `2026-03-24_18-50-08_my-note.md`. `is_scratch(slug)` identifies them.
- **`/page/_scratch`** — special auto-generated page (no `.md` file on disk) listing all scratch notes newest-first with title and timestamp. Not editable.
- Daily notes (`/api/daily`, `/page/_daily`, `c d`/`g y`) were removed on purpose. Existing `YYYY-MM-DD_daily.md` files are ordinary pages.
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
| **g** then **h/s/d/t** | anywhere | Navigate: Home / Scratch / Deck / Deck (tag=task) |
| **c** then **s/t** | anywhere | Create: scratch note / task-tagged deck item |
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
- **Selecting a task result** (search modal or `/search` page) opens `/deck?task=<id>&view=<open|completed>` (plus `&list=<name>` for a task in a separate list, so it lands on the list's page) — which redirects to the page embedding the deck — on the tab matching the task's state, with that task selected, scrolled into view and briefly highlighted (`.task-flash`) — rather than the bare `_deck` storage slug. The link is by id, not title: titles can repeat or contain a `#word` the filter field would read as a tag. Built by `_result_href()` in `server.py` (the search modal uses the result's `href`). A page's "Linked tasks" entries use the same link.

---

## Deck (Task App)

A task management app embedded into wiki pages via the `[[deck]]` directive. There is no standalone route — the deck app lives inside any wiki page that contains `[[deck]]`. The typical setup is a `deck.md` page with `[[deck]]` as its content, accessible at `/page/deck`.

Multiple `[[deck]]` embeds per page are supported (each with different filter params). They receive unique instance IDs to avoid DOM conflicts. `deck.js` keeps a registry of every instance on the page; each holds its own task array, and after a successful write in one embed the others are refreshed (`refreshOtherInstances`) so all panes stay in sync without a reload. `window.refreshDeck` refreshes every instance, not just the last-initialized one. A deck tab can stay open for days (phones keep their tabs), so every instance also reloads when the page becomes visible again (`visibilitychange`) or is restored from the back/forward cache (`pageshow` with `persisted`). That shows tasks changed elsewhere and recomputes the day sections for today. `refreshIfIdle` skips the reload while a drag or swipe, a completion grace period, an inline title edit, the ⋯ menu or the edit modal is in progress, since re-rendering would destroy it. There is no polling.

### Architecture

The deck logic is in `src/deck.py` (Flask Blueprint), registered by `server.py` on startup. This module handles:
- Task CRUD API routes (`/api/deck/*`)
- `[[deck]]` embed HTML generation
- Task search integration (tasks appear in `/api/search` results)
- Task backlinks (pages show linked tasks)

### Storage

Tasks are stored in `_deck.json` in the wiki data directory (alongside markdown files). The file is a flat JSON array of task objects, committed to git on changes. Completed tasks live in `_deck_completed.json`; at startup, completed tasks from previous years are rolled into per-year archives (`_deck_completed-YYYY.json`) so the hot file stays small. Archived tasks remain in the search index but are not shown in the Completed tab.

Request bodies are validated (`_json_object`, `_as_str`, `_as_int`, `_as_str_list`, `_id_list`): a non-object body or a wrongly typed field is a 400 with a JSON `{ error }`, never a 500. Numeric strings are accepted for integer fields, and `tags` may be a comma-separated string.

All deck mutations are serialized through a module-level lock (`_LOCK`) and written atomically (temp file + `os.replace`), because the Flask dev server is threaded and unguarded concurrent read-modify-write on the JSON files would otherwise lose updates. Write-path reads use a strict reader that refuses to overwrite a present-but-unreadable file (raises `_DeckCorrupt` → 503) rather than treating a torn read as an empty list and wiping every task.

### Dates

A task has two dates, each with one meaning:

- **`due_date`** is the deadline. Only a due date in the past makes a task overdue.
- **`start_date`** is the day the task becomes available. A start date in the past just means "available since". Having a start date is what makes a task *planned*.

Nothing is derived or stored beyond these two fields. The edit modal's "Days before due" number is only another way to enter the start date (`start = due − N`) and is not saved; when the modal opens, it shows the gap between the two dates. A recurring instance keeps that gap: when the series advances the due date, the start date moves by the same amount. Changing one date by hand (in the modal, the ⏰/📅 chips, or a board drag) never moves the other.

Older decks used `focus_date` (now `start_date`), `defer_days` (a stored "plan N days before due" rule) and `recurrence.anchor`. `_normalize_task()` converts them on every read (and in restore snapshots), and `_migrate_deck_files()` rewrites the files once at startup, with its own git commit ("Normalize deck files (date model, tags, page links)"; a no-op once the files are current).

### Task fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | Auto-generated unique ID |
| `title` | string | Task title (required) |
| `description` | string | Optional notes/description |
| `tags` | string[] | Tags, one word each. `_clean_tags()` runs on every write, read and restore: whitespace inside a tag becomes `-` (`local llm` → `local-llm`; the filter field splits on spaces, so a tag with a space could never be matched), a leading `#` is dropped, empties go, and case-insensitive duplicates collapse to the first spelling. The edit modal's field is comma-separated. Tag-only fields refuse spaces as you type (`DeckCore.sanitizeTagInput` on `input`, so paste and phone keyboards too): a space inside a tag becomes `-`, one after a comma or at the start is dropped, a comma gets one space after it. Deletions (`inputType` `delete…`) are left alone, or Backspace on `work, ` would get the space put straight back. |
| `due_date` | `"YYYY-MM-DD"` / `null` | Due date |
| `created_at` | ISO datetime | Auto-set on creation |
| `completed_at` | ISO datetime / `null` | Set when completed |
| `sort_order` | integer | Manual sort position (priority = position) |
| `recurrence` | `{ type, interval, from, day? }` / `null` | Recurring schedule. `type` is `daily`/`weekly`/`monthly`/`yearly`. The series is driven by the task's **primary date** — `due_date` if set, else `start_date`. `from` is `"due"` (default — the next date steps from the previous primary date, calendar-aligned) or `"completion"` (steps from the completion day, for interval habits). `day` (monthly/yearly, set on the first spawn) is the series' day of month, so a month-end series returns to the 31st after a short month instead of drifting (Jan 31 → Feb 28 → Mar 31). Re-saving the edit modal keeps `day` unless the type or the primary date changes. |
| `list` | string / `null` | Separate list the task belongs to (see Separate lists). `null` = the main deck. Kept by recurring spawns and restore snapshots. |
| `start_date` | `"YYYY-MM-DD"` / `null` | The **start date**: from that day on the task is available to work on. Having one is what makes a task "planned". Focus shows tasks with `start_date <= today`; a past date is not overdue or stale, it just means the task has been available since then. Cards show a past start date as "since Sep 21" (`shortDate()`, year added when not the current year), with no warning styling. |
| `spawned_from` | string / `null` | For instances spawned by completing a recurring task: the id of the completed task. Used to remove exactly the spawned instance when the completion is undone. |
| `wont_do` | bool | `true` marks the task abandoned ("won't do"). Archived like a completed task (moves to `_deck_completed.json` with `completed_at` set) but shown with a "✗ won't do" badge; marking won't-do never spawns a recurring instance. |

### UI

- **View tabs**: a segmented control (`.view-tabs`, `role=tablist`, one `<button data-filter>` per view) in the order Open · Plan · Focus (internal `data-filter` values `open`/`due`/`focus`); the active view's button is filled (`.active`, `aria-selected`). One click switches; **Tab**/**Shift+Tab** cycle the three tabs in on-screen order (wrapping), with keyboard focus following the active button. The fourth view, `completed`, is deliberately not a tab (see Completed link). On phones the control spans the full width with equal-width buttons. (It replaced an earlier `<select>` dropdown.) Precedence on load: URL `?view=` > `[[deck view=…]]` embed view (always wins when specified) > tab remembered this session > `focus` (Focus) default: opening the deck is usually about what to do now. An embed with `view=` therefore always opens on its configured tab. For a plain `[[deck]]` (no `view=`), the active tab is remembered in `sessionStorage` (keyed by page path + embed instance), so a refresh lands on the tab the user last clicked while a fresh load in a new tab starts on Focus.
- **Completed link**: rarely needed, so it is a quiet link under the list (`.deck-completed-link`, `#task-completed-link`) reading `✓ Completed (N) →`. N comes from `GET /api/deck/completed-count` (the completed tasks themselves load only when the view opens) and is refreshed after every write. In the Completed view no tab is highlighted, the list starts with a "Completed" divider, and the link reads `← Back to open tasks`, returning to the tab you came from. While the filter field is in use on another view, the link instead counts the completed tasks that match (`✓ 2 matches in Completed →`, `.has-matches`; `✓ No matches in Completed →` for none, and no number while they load — the unfiltered total is never shown while filtering, since it would read as the filtered count), so a search that finds nothing open still shows where the task went; the completed tasks are fetched once for that (`fetchCompleted`, one request in flight at a time). From Completed, Tab goes to the first tab and Shift+Tab to the last. `?view=completed`, `[[deck view=completed]]` and the session-remembered view still open it.
- **Focus tab**: everything available to work on now — tasks whose start date has arrived (`start_date <= today`) plus overdue/due-today deadlines (`DeckCore.isFocus`), i.e. exactly the Plan board's Overdue and Today sections. A task that started days ago is simply part of Focus; there is no "stale" or "carried over" handling. Deadlines (overdue + due today) are pinned in a "Due" section at the top, as on the Open tab: exactly what the due badge counts (`/api/deck/due-count`), so the badge's number is visible where its link (`/deck?view=focus`) lands. The capacity warning counts them too. An empty Focus says "Nothing planned for today" with a button to the Plan tab. A task created while on Focus gets today's start date prefilled (quick-add shorthand still overrides it), so it doesn't vanish from the view on save.
- **Plan (`due`) tab — day board**: shows **all** open tasks grouped by start date (`DeckCore.bucketKey`): `Overdue` (missed deadline, `due_date < today`), `Today` (`start_date <= today`, or due today), `Tomorrow` and weekday headers for the next days (`today..+6`), `Later`, and `Unplanned` (no start date; tasks with only a deadline are listed here nearest deadline first, not on their due day). Day headers show a count and are flagged amber at/over `CAPACITY` (5). Date sections, `Later`, and `Unplanned` are **drop targets** (Later and Unplanned are always shown, even when empty, so there is always somewhere to drop). An **empty section is only its header row** (`.board-day--empty`, dashed, min 1.9rem tall, 2.5rem under `pointer: coarse`), and the row itself takes the drop. So a quiet week stays compact, and nothing grows or moves when a drag starts; placeholders that appeared on drag start once shifted the section under the pointer. dragging a task card onto a day sets its `start_date` to that day; dropping on `Unplanned` clears it (`start_date = null`); dropping on `Later` opens a native date picker (min today+7) for the target date. `Overdue` is display-only. Drag wiring is `setupBoardDragAndDrop` (per-card) plus list-level `dragover`/`drop` handlers attached once; manual-reorder drag (`setupDragAndDrop`) is used only on other tabs.
- **Inline date editing**: the ⏰ (due) and 📅 (start) spans in each card's meta row open a date picker on click — no modal needed. Keyboard: **f** starts the selected task today, **F** tomorrow (pressing again clears).
- **Header layout**: tabs, filter field, sort, the Open tab's "Unplanned" toggle and **+ New** share one line on desktop (`.tasks-toolbar` has `flex: 1 1 20rem`, so it wraps under the tabs only when less than 20rem is left). On phones they take two lines: the tabs, then search · sort · toggle · a label-less "+" button (`.task-add-label` hidden; the button's accessible name is "New task").
- **Sort options**: Manual (drag-and-drop) · Created
- **Filter field**: one field (`#task-filter-search`) for text and tags. Words starting with `#` filter by tag: separate words must all match (`#a #b`), and a pipe means any (`#a|b`). The rest is a text search over title and description. `DeckCore.parseFilter`/`matchesFilter` implement it. Typing a `#word` anywhere in the field opens tag suggestions: tags of the tasks the *other tag words* leave visible in the current tab, minus those already used. Free text is ignored for suggestions; it is usually still being typed and would otherwise hide every tag. `/` focuses the field, and `#` focuses it and starts a tag word. `[[deck tag=a,b]]` / `?tag=` (comma = all, pipe = any) are converted with `DeckCore.tagParamToQuery` and prefilled together with `?search=`. A new task gets the filter's single-tag words as default tags.
- **Tag completion** (one helper, `attachTagAc` in `deck.js`, used by the filter field, the edit modal's Tags field, a new task's title `#tag` shorthand, and the bulk tag dialog `t`/`T`): suggestions are ranked by `DeckCore.rankTags`: exact match, then prefix, substring and in-order letters, and within each level the most-used tag (`tagCounts`) first, then A–Z. The first (best) suggestion is highlighted from the start, so **Tab or Enter completes a half-typed tag to it**; ↑/↓ pick another, hovering moves the highlight, and Esc closes the dropdown and keeps what was typed (it does not also close the dialog or leave the field). The key handler runs in the capture phase so the field's own Enter/Esc handling never sees a key the dropdown used. In the Tags field a completion appends `, ` for the next tag; in the bulk dialog Enter completes and applies; "Remove tag" (`T`) suggests only tags the selected tasks carry. The dropdown is appended to the field's parent, not `<body>`, because inside a `<dialog>` only the top layer is visible.
- **Drag-and-drop**: Available in "Manual" sort mode; reorders tasks persistently
- **`?task=<id>`**: selects that task after load, scrolls it into view and flashes it. If the Open tab's "Unplanned only" toggle would hide it, the toggle is switched off for that visit (the stored preference is kept). A task that no longer exists (deleted, or in a previous-year archive) gets a notice instead.
- **Inline completion**: Checkbox toggles mark task complete after a short grace period (cancellable)
- **Won't do**: each active task card has a ✗ button (desktop) that archives the task as abandoned; also in the ⋯ menu. Won't-do tasks appear in the Completed tab with a red "✗ won't do" badge instead of a "done:" date. Unchecking them there reopens them. An undo toast is shown after marking.
- **Skip occurrence**: recurring task cards additionally have a ⏭ button (desktop; also in the ⋯ menu) — archives this occurrence as won't-do but spawns the next instance, so the series continues. Undo (or reopening the won't-do entry) removes the spawned instance again.
- **No hiding**: the deck never hides a task. Every open task is reachable from its own deck (the main deck, or its separate list's page) via the Open tab or the Plan board at all times, and from search. Scheduling only decides *where* a task appears, not *whether* it does. (Much earlier versions had a Deferred tab with a hiding start date; that is gone, and today's `start_date` never hides anything.)
- **Separate lists**: a task's `list` field puts it in a named list that appears only in `[[deck list=<name>]]` embeds, deliberately kept out of the main deck (Open/Plan/Focus), tag embeds, the due badge and the main Completed count. Storage stays in `_deck.json`. `deck.js` drops out-of-scope tasks right after fetching (`inScope`), so tag suggestions, counts and reordering only ever see the list's own tasks. The edit modal's **List** field (suggestions: every list in use, `known_lists()`) moves a task between lists. It sits in a closed "List" section (`<details class="task-edit-links">`), since the embed usually fills it; its summary shows the value, and it is closed again every time the modal opens; a task saved into another list leaves the view with a notice ("Saved to list “x”." / "Moved to the main deck."). Search results and Linked tasks link to `/deck?…&list=<name>`, which opens the list's page.
- **Open tab "Unplanned" toggle**: the Open tab (formerly "All", before that "Backlog") lists every open task. It was renamed because it never held completed tasks, and the name pairs with the Completed link. A checkbox in the toolbar, shown only on this tab and **off by default**, narrows the list to tasks with no `start_date` — the queue still awaiting a decision about *when* to do them, the same tasks as the board's Unplanned section. A `due_date` alone does not count as planned: the deadline is the one day the work should not start. Giving a task a start date is therefore what removes it from the narrowed list. Predicate is `DeckCore.isUnplanned` (client-only; the server never filters views). It is a standing preference, so it persists in `localStorage` keyed by page path + embed instance, unlike the active tab (`sessionStorage`); the key carries a version suffix (now `-v3`) that is bumped whenever the default changes, so browsers that stored the previous default pick up the new one. `[[deck unplanned=1|0]]` pins it either way for an embed regardless of what is stored.
- **Rejected writes** (e.g. dependency cycles) surface as an auto-dismissing toast (`showNotice`) with the server's error message.
- **Quick-add shorthand** (new tasks only, `DeckCore.parseQuickAdd`): a title ending in shorthand fills the task's fields on save, e.g. `Call Bob tomorrow #work due:fri`. Start date: `today`, `tomorrow`/`tmrw`, a weekday (`fri`/`friday`; the next one, never today), `+3`/`+3d`/`+2w`, or `YYYY-MM-DD`. Due date: `due:` + any of those. Tags: `#tag` starting with a letter (so `Fix #3` keeps its number). Only **trailing** words are read, so a date word inside the sentence stays text; shorthand that would leave no title is ignored; a second start word stays in the title. A line under the title (`#task-edit-quick`) previews what was recognised, and on save the shorthand wins over the date fields. Editing an existing task never parses its title.
- **Edit modal**: double-click a card (or Enter on the selected task) on desktop, or **tap** a card on a touch device (tracked via the pointer type of the last `pointerdown`, so mouse clicks keep selecting), or "Edit…" in the ⋯ menu. Full task editing, including a "From done" checkbox on recurring tasks (repeat from completion date).
- **Recurring tasks**: completing one spawns the next instance (`spawned_from` links it to the completed task). The next due date steps from the previous due date (or from today when `recurrence.from` is `"completion"`) and is always advanced until ≥ today, so completing a long-overdue task never spawns an already-overdue instance. Monthly recurrence clamps to the real last day of the target month (Jan 31 → Feb 28/29); yearly steps 12 months and clamps the same way (Feb 29 → Feb 28 in non-leap years). A series advances its primary date (due, else start); when the task has both dates, the start date moves with the due date so the gap is kept (it may land before today, which just means "available since"). Un-completing removes exactly the spawned instance.
- **Delete undo**: deleting (keyboard, edit modal, or ⋯ menu — all via `deleteWithUndo()`) asks for confirmation and shows an undo toast; undo POSTs the full snapshots to `/api/deck/restore`, preserving every field including ids, completion state, and dependency edges.
- **⋯ task menu**: every card has a ⋯ button (`.task-menu-btn`) opening a menu with all card actions — for open tasks Edit…, Start today, Start tomorrow, Pick start date…, Unplan (if it has a start date), Skip this occurrence (if recurring), Won't do, Delete…; for completed tasks Edit…, Reopen, Delete…. A popover under the button on desktop (↑/↓ + Enter, Esc closes and returns focus; the deck's own shortcuts are suspended while it is open) and a bottom sheet over a dimmed backdrop at ≤640px.
- **Edit modal layout**: centred the native `<dialog>` way (`inset: 0; margin: auto`, no transform) and scrollable (`overflow-y: auto`) when taller than the window; the Save/Cancel/Delete bar is `position: sticky` at the bottom so it is always on screen. At ≤640px it is full screen and compact: Due date | Start share a line (Days before due below them), as do Repeat | Every N | From done (grid rows `.task-row-dates` / `.task-row-repeat`), and the action bar sits at the bottom edge. No transform on purpose: a transformed dialog makes `position: fixed` popups inside it (the date picker) resolve against the dialog and get clipped by its scrolling.
- **Date picker** (`openDatePicker`): placed after every `draw()` (its size is only known once the month is rendered; 6-week months are taller) — below its anchor, else above it, else pinned inside the window; left-aligned with the anchor but never past either screen edge (the card's ⏰/📅 chips sit at the right edge).
- **Touch devices**: under `(hover: none)` the small ✗/⏭ icons are hidden (the ⋯ menu carries them); under `(pointer: coarse)` the ⋯ button, checkboxes and date chips get finger-sized targets, and editor/toolbar fields use 16px text (iOS Safari zooms the page when focusing smaller text). Phones never fire HTML5 drag events, so touch has its own drag: a **long press** (350 ms, without moving more than 10px) picks a card up. A floating copy (`.task-touch-ghost`) then follows the finger, and the page auto-scrolls near the top and bottom edges. Releasing drops the card through the same code as a mouse drop. Moving before the press completes is a normal scroll, and a finished drag does not count as a tap. Each render, `setupBoardDragAndDrop`/`setupDragAndDrop` register a `touchDrop` adapter (`target(el)` / `drop(target)`), and the touch listeners on the list are attached once. Native `dragstart` is cancelled for touch input, and cards can't be text-selected under `(pointer: coarse)`, so a long press can't start a text selection or the iOS callout. The ⋯ menu's start-date actions still work as well. **Swipes** (every view, open tasks only): a mostly sideways move (|dx| > 1.5·|dy|, over 10px) moves the card with the finger, and the uncovered space shows "✓ Done" (right) or "📅 Start today" (left). Past `min(110px, 30% of the card)` the card is armed (`.task-swipe-armed`, coloured). Releasing then completes the task through the checkbox's grace period (`beginComplete`, so it can still be cancelled) or starts it today (`setStart`, with an undo toast). Cards have `touch-action: pan-y`, and `.tasks-list` has `overflow-x: clip`, so a pushed card never scrolls the page sideways.

### Keyboard shortcut

- **g d** — Navigate to Deck (`/deck`, which redirects to the page embedding it)
- **c t** — Quick-add to deck with `tag=task` (works from any page)

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`).
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- No nav bar (keyboard shortcuts); on phones a floating search button (see the Scratch section).
- No external CSS frameworks — keep `style.css` self-contained.
- Mobile breakpoint at `640px`: page content readable, tables/pre scroll horizontally, larger touch targets. The editor is fully usable on phones — see "Mobile editor" in the In-Browser Editor section.

---

## Local Development (without Podman)

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

`tests/test_git.py` initializes a real git repo in the temp wiki dir and covers every git path (commit-on-write, history/revision/diff/restore, `_deleted`, uncommitted/commit endpoints, git-disabled fallbacks); it skips when git is not installed. The other test modules point `WIKI_DIR` at a plain temp dir, so git calls there are silent no-ops.

To run the suite without a local venv: `./run.sh test` (runs inside the app image with the repo mounted).

### Browser tests (`tests/e2e/`)

End-to-end tests drive the real app in headless Chromium via Playwright. They run in a separate test image (`Containerfile.e2e`, based on Microsoft's Playwright image) so the app image stays browser-free and the app still has no build step or npm. `./run.sh e2e` builds that image (cached) and runs them with the repo bind-mounted; extra args pass through to pytest (e.g. `./run.sh e2e -k deck`). Without Playwright installed they skip, so plain `pytest tests/` is unaffected.

- `conftest.py` serves `server.app` on a background thread (werkzeug `make_server`, random port) and points it at a fresh temp wiki with its own git repo per test; each test gets a fresh browser context. All non-local requests (the highlight.js CDN) are aborted to keep runs offline and deterministic. Uncaught page JS errors fail the test.
- Coverage: editor (auto-save + unsaved dot, no commit on auto-save, Done/Ctrl+S commit, pagehide beacon, live preview/F2, list continuation), search modal (Ctrl+P/Esc, fuzzy ranking, content matches, arrow/Enter nav, unlinked filter), deck (Plan-board drag to a day and to Unplanned, touch long-press drag via CDP touch events (board, Open-tab reorder, swipe still scrolls, long press is not a tap), board grouping by start date, modal "Days before due", Open-tab "Unplanned only" default + persistence, view tabs, start-date Focus, touch: tap-to-edit, ⋯ menu/bottom sheet, target sizes, editor scrolling/compact phone layout, date picker placement), layout (no horizontal page scroll at 390px phone width on page/editor/deck views, mobile Preview button, tldr toggle geometry).
- Touch: `new_page(**TOUCH_PHONE)` gives a phone context with touch input, `hover: none` and `pointer: coarse` (use `.tap()`). Full-page screenshots of such a context lose the touch/hover emulation (Playwright re-renders with changed device metrics), so assert on computed styles/sizes rather than on a full-page image.
- `test_screenshots` writes full-page PNGs of each view at phone/desktop × light/dark to `tests/e2e/_screenshots/` (gitignored) for visual review. It asserts nothing about appearance.
- Playwright's HTML5 drag cannot auto-scroll mid-drag, so drag tests must keep source and target on screen (e.g. enlarge the viewport).
- Keep the `playwright==` pin in `Containerfile.e2e` equal to the image tag version.

`tests/test_deck_core.py` exercises the pure client logic in `src/public/deck-core.js` by running it under Node (`node -e`); those tests skip automatically when Node is not installed. The app image has no Node, so `./run.sh test` skips them; `./run.sh e2e` runs them, since the test image (`Containerfile.e2e`) installs Ubuntu's `nodejs`.

**Always run the full test suite before and after making changes to `src/server.py`.**

**Always add tests as part of the same change** — whenever a new feature is implemented or a bug is fixed, add tests covering the new behavior (and, for bugs, a regression test that fails without the fix). A feature or fix is not complete until it has test coverage.

The tests use an in-process Flask test client and a temporary directory — no running server or container needed.

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
- `ALLOW_REMOTE` is the single knob for network access. When unset, `run.sh` sets `BIND_ADDR=127.0.0.1` so podman only exposes port 5000 on the host's loopback interface, and the host-header check rejects any non-localhost `Host` header. When `ALLOW_REMOTE=1`, `run.sh` sets `BIND_ADDR=0.0.0.0` (LAN-accessible port binding) and the host-header check is skipped. The port mapping passed to `podman run` is `-p ${BIND_ADDR}:5000:5000`.
- A `before_request` hook in `server.py` validates the `Host` header against `ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}`, defeating DNS rebinding attacks. Set `ALLOW_REMOTE=1` in `.env` to skip this check and allow access from any host.
- **Cross-site writes are refused** (`check_origin`, a `before_request` hook after the Host check): a POST/PUT/PATCH/DELETE whose `Origin` header names another site, or is `null` (sandboxed iframe), gets 403. Without it any web page could post a form to `localhost:5000` and delete or overwrite pages, or write deck tasks (the deck API parses text/plain bodies, which need no CORS preflight), and raw HTML in markdown would turn an overwrite into a stored script. Requests without `Origin` (curl, scripts) are allowed. Behind a reverse proxy the proxy must pass the original `Host` through, or same-origin writes would be refused.
- Do not add a build step or bundler.
- Slugs are derived from file paths relative to `PAGES_DIR`: `journal/my-page.md` → slug `journal/my-page`. Slugs are URL-safe (lowercase, hyphens, forward slashes only).
- Subdirectories are fully supported. `to_slug()` preserves `/` as a path separator while slugifying each component. `iter_pages()` does a recursive `rglob("*.md")` and skips `_*` subdirectories.
- Flask routes use `<path:slug>` to capture slugs containing `/`.
- `safe_path()` uses `target.relative_to(pages_root)` to validate the path is inside `PAGES_DIR` (allows subdirs).
- File writes call `path.parent.mkdir(parents=True, exist_ok=True)` before writing to auto-create subdirectories.
- `pages/index.md` is the home page and must always exist. Do not allow it to be deleted or renamed via the UI.
- All file writes use `open(..., 'w', encoding='utf-8')`. Validate that the resolved path is inside `pages/` before writing (prevent path traversal — use `Path.resolve()` and `relative_to()`).
- When a page is renamed, every link to it is re-pointed (`rewrite_page_links()` via `update_references()`), so nothing that backlinks count is left broken:
  - `[[Old Name]]` / `[[Old Name|Label]]` — matched by comparing the raw or slugified page name to the old slug; only the page-name part is replaced, the `|Label` is kept.
  - Markdown links in the forms backlinks recognise: relative (`old`, `../dir/old`, `old.md`) and absolute (`/page/old`). Each keeps its style, `#anchor` and title; relative hrefs are recomputed from the linking page's directory. Images are never touched.
  - Links inside fenced code and inline code are left alone (`_mask_code`).
  - Deck tasks (active and completed): links in titles/descriptions (`deck.rename_page_refs()`). Changed deck files are written without their own commit and included in the rename commit.
