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
| Syntax highlight    | `highlight.js` (vendored in `src/public/vendor/`, no CDN) |
| Styling             | Custom CSS (classless, semantic HTML)        |
| In-browser editor   | Plain `<textarea>` with live preview         |
| Search              | BM25 in `search.py`, optionally fused with semantic search (`embed.py`); search modal (Ctrl+P) and `/search` |
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
├── .forgejo/workflows/ci.yml  # Forgejo Actions: tests, e2e, build + push the image
└── src/
    ├── requirements.txt
    ├── server.py           # Flask server — wiki routes, rendering, shell
    ├── deck.py             # Deck module — task API, [[deck]] embed, storage
    ├── search.py           # BM25 search ranking (stemming, fuzzy, excerpts), rank fusion
    ├── embed.py            # Optional semantic search: section embeddings, Related pages
    ├── embed_setup.py      # Model choice + install/download at image build (kept small: own image layer)
    ├── mcp_server.py       # MCP protocol (JSON-RPC) for /mcp: wiki tools for LLM clients
    └── public/
        ├── style.css       # All styling
        ├── leaf.js         # Client-side JS (editor toggle, formatting bar, live preview, search UI, image paste)
        ├── deck-core.js    # Pure deck logic (no DOM) — shared by deck.js and Node-run tests
        ├── deck.js         # Deck (task management) app JS, loaded via [[deck]] embed
        ├── favicon.svg     # Browser tab icon
        ├── icon.svg        # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
        ├── manifest.json   # Web app manifest (Add to home screen)
        └── vendor/highlight/  # highlight.js 11.9.0 + github/github-dark themes (vendored, no CDN)
```

Page content does not live in the repo tree. It lives in the host directory pointed to by `WIKI_DATA` (default: `~/leaf`), where markdown files sit at the root and uploads live under `_assets/`.

Keep the structure flat. Do not add subdirectories under `pages/` unless the user explicitly asks for nested pages.

---

## Podman / Container Setup

### Files

- **`Containerfile`** — Ubuntu 26.04 LTS base, installs Python + pip via `apt`, installs `requirements.txt`, copies the `src/` directory into `/app`. Built with `podman build` (podman reads `Containerfile` by default).
- **`run.sh`** — convenience script driving `podman build` and `podman run` directly (no Compose dependency). It reads `WIKI_DATA` and the optional env vars (`LEAF_PASSWORD`, `SECRET_KEY`, `GIT_REMOTE`, `ALLOW_REMOTE`, `TZ`, `LEAF_EMBEDDINGS`, `LEAF_EMBED_MIN_SCORE`, `LEAF_MCP_TOKEN`, `LEAF_BASE_URL`) from `.env`, mounts `$WIKI_DATA` to `/wiki-data`, and passes `WIKI_DATA=/wiki-data` as an env var so the server knows where data lives. `up` uses `--replace`, so re-running it rebuilds and restarts the existing container, and `--init`, so a minimal init is PID 1 and reaps orphaned processes (with Leaf as PID 1 they piled up as zombies; see Version history). The container gets the host's timezone (`podman run --tz local`; a `TZ` in `.env`, e.g. `Europe/Berlin`, overrides it), so "today" on the server (due badge, deck dates, scratch-note names, "Last changed") is the user's day, not UTC. Without `/etc/localtime` on the host and no `TZ`, it stays UTC.
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

### CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on every push and pull request: **test** (unit/API tests plus the Node deck-core tests in an `ubuntu:26.04` job container, the app image's base) and **e2e** (browser tests in the Playwright image, mirroring `Containerfile.e2e`; keep the two in sync, including the version pin). On `main` and `v*` tags, once both pass, **image** builds the `Containerfile` with `LEAF_EMBEDDINGS=fastembed` and the `LEAF_BUILD` string `run.sh` would compute, and pushes it to the Forgejo registry as `git.example.org/<owner>/leaf:<short-sha>` (the public host, repo variable `REGISTRY` overrides it: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`), plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`). Builds run in the official `docker:cli` image (plus `nodejs`, `git`, `bash` from apk) and use plain `docker build -f Containerfile`/`docker push` against the host engine through the socket mounted into job containers (runner label `docker`, runner config `container.docker_host: automount`; the host's engine trusts the internal certificate via `/etc/docker/certs.d/git.example.org/ca.crt`, which the runner also mounts read-only into job containers via `container.options`; checkout goes over the runner's internal `http://web:3000`, so the test job needs no `ca-certificates` package), so layers are cached between runs. Repo settings: secret `REGISTRY_TOKEN` (token with package write scope), optional variable `REGISTRY_USER` (default: the repo owner).

### Mobile / phone access

Set `ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces — `run.sh` then maps `0.0.0.0:5000` and the server's host-header check is skipped. Phones on the same network reach Leaf at `http://<host-LAN-IP>:5000`. The UI is responsive down to phone widths and the editor works on mobile. The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one. Without it, full-screen views (the deck edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard. iOS Safari ignores the setting. There is no auth by default — for LAN use consider setting `LEAF_PASSWORD` in `.env` to get a login gate.

### Version

Leaf's version is a semver constant, `VERSION` in `server.py`, bumped by hand. Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `src/` had uncommitted changes (a commit can't contain its own hash, so this part can't be a committed constant). The shortcuts modal (`?`) shows both (`Leaf 1.0.0 · c28ec9e 2026-09-26 19:22`), the `<meta name="generator">` tag the version, and `/api/version` returns `{ version, build }`. The image has no `.git`, so `run.sh` computes the build string and passes it as the `LEAF_BUILD` build arg (the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer). Without the container, `server.py` reads the same string from git itself (`_source_build()`); `$LEAF_BUILD` wins when set, and with neither the modal shows the version alone.

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
| GET    | `/deck`               | The built-in deck page (`deck_page()`): the full main deck, independent of the wiki's pages. `?list=<name>` shows that separate list instead; `?view=`, `?task=`, `?tag=`, `?search=`, `?new=1` work as for any embed. Every link to "the deck" goes here (due badge, search results, Linked tasks, `g d`, `c t`). |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `pages/<slug>.md`        |
| GET    | `/new`                | Editor for a new page (slug entered by user)    |
| POST   | `/new`                | Create `pages/<slug>.md` and redirect to it     |
| GET    | `/search?q=...`       | Full-text search results page (up to 50, with a count; `&unlinked=1` as for the API) |
| GET    | `/api/search?q=...`   | JSON search results (used by the search UI); `&unlinked=1`: only pages nothing links to, no tasks; `&limit=N` (default 15, max 100) |
| POST   | `/api/preview`        | Render posted markdown to HTML (for live editor preview) |
| GET    | `/api/pages`          | JSON list of all existing page slugs (for wikilink hints) |
| POST   | `/mcp`                | MCP server for LLM clients (see MCP server). GET/DELETE → 405. |
| GET    | `/api/version`        | `{ version, build }`: Leaf's version and build (see Version) |
| POST   | `/api/upload`         | Upload an image file; returns `{ url }` for insertion into markdown |
| POST   | `/api/save/<slug>`    | Write page content to disk silently (no git commit, no redirect). For full-page edits returns 204; for section edits returns JSON `{ file_hash, start_char, end_char }` with updated anchors, or 409 on conflict. Used by auto-save. |
| POST   | `/api/toggle-checkbox/<slug>` | Toggle the Nth checkbox (`idx` param) in a page; returns 204. |
| GET    | `/api/deck`           | JSON list of all tasks from `_deck.json` |
| POST   | `/api/deck`           | Create a task; returns JSON task object (201). |
| PUT    | `/api/deck/<id>`      | Update a task (title, tags, due_date, start_date, completed, recurrence via `rec_type`/`rec_interval`/`rec_from`, keep, last_done, wont_do, skip). The two dates are independent: setting one never moves the other. `wont_do: true` archives the task without spawning a recurring instance; `wont_do: false` reopens it (removing any skip-spawned instance). `skip: true` archives the occurrence as won't-do **and** spawns the next instance. `completed: true` on a keep task does not archive it (see Keep tasks). |
| DELETE | `/api/deck/<id>`      | Delete a task; returns 204. |
| POST   | `/api/deck/batch-delete` | Delete multiple tasks; body `{ ids: [id1, id2, …] }`; returns 204. |
| POST   | `/api/deck/batch-complete` | Complete/uncomplete multiple tasks; body `{ ids: [...], completed: bool }`; returns JSON array of updated tasks. Handles recurring spawn/cleanup; keep tasks stay open (see Keep tasks). |
| POST   | `/api/deck/restore`   | Re-insert full task snapshots (undo after delete); body `{ tasks: [...] }`. Snapshots keep their original id/created_at/completion state; unknown fields are dropped, existing ids skipped. Returns restored tasks (201). |
| POST   | `/api/deck/reorder`   | Update sort order. `field` is `sort_order` (default) or `focus_order`. Body `{ order: [id1, id2, …] }` densely renumbers the given ids to `0..n-1`; Open and Focus always send their whole manual order this way, tasks hidden by a filter included (see Sections). Body `{ updates: [{ id, <field> }, …] }` sets specific ids to specific (possibly fractional) values without touching any other task; a `null` value clears the field, taking the task out of the manual order. Both may be sent together. |
| POST   | `/api/deck/batch-tag` | Add/remove tags from multiple tasks; body `{ ids: [...], add: [...], remove: [...] }` |
| GET    | `/api/deck/due-count` | Count tasks due today + overdue |
| GET    | `/api/deck/completed` | Completed tasks of `?year=YYYY` (default this year; earlier years come from their archive file). |
| GET    | `/api/deck/completed-years` | `{ years }`: years with completed tasks, newest first, this year always included (Completed's year switcher). `?list=<name>` as for the count. |
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
- **Breadcrumb** — a small green leaf logo plus `Leaf › Page Title` above the page header, with the branded home link leading back to the home page. After some navigation it becomes a trail of up to 6 recently visited pages (`leaf.js`, `sessionStorage`); a phone (≤640px) shows at most 4 of them, the last 3 visited plus the current page. It stays on one line: the page titles shrink with an ellipsis, while the home link, separators, × and the backlinks chip keep their size. A trail that could not shrink once made the page wider than a phone, which pushed the full-screen deck edit modal's buttons off-screen.
- **Backlinks** — section at the bottom listing all pages that contain a `[[wikilink]]` pointing to the current page. Shows "No pages link here." when empty.
- **Backlinks chip** — when a page has backlinks, a `← N` chip sits at the top right of the breadcrumb. On desktop, hovering it lists the linking pages (`.bl-tooltip`) and clicking jumps to the Backlinks section. Touch devices (`hover: none`) have no hover, so there a tap toggles the list (`.bl-open`, delegated click handler in `leaf.js`) instead of scrolling down. A second tap or a tap elsewhere closes it. The hover rules are wrapped in `@media (hover: hover)` because a tap leaves `:hover` stuck, which would fight the toggle.
- **Related** — only with semantic search on (see Search → Semantic search): up to 5 pages closest in meaning, leaving out pages already linked either way (`_build_related_html`). Absent when none clears the similarity floor.
- **Linked tasks** — open deck tasks whose title/description link to the page. Each entry is a single link to that task in the deck (`/deck?task=<id>&view=open`, plus `&list=<name>` for a task in a separate list — `_task_href()`); links inside the task title are flattened to their labels (`_plain_task_title`) so anchors are never nested.

---

## Rendering

- `server.py` reads `pages/<slug>.md`, converts it to HTML using `markdown-it-py`, and injects it into a shared HTML shell (Python f-string in `server.py` — not a separate template file).
- The HTML shell includes: status badges, breadcrumb, page header (title, Edit/History/Present), content area, backlinks, and the search/shortcut/scratch modals. There is no nav bar (see Scratch).
- `highlight.js` (vendored, see Dependencies) auto-highlights fenced code blocks client-side after load.
- The shared shell renders the product brand as `Leaf`, with a small green inline SVG leaf logo in the breadcrumb home link.

### Autolinks and callouts

- **Bare URLs become links** (`gfm_autolink_plugin`): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link. Code spans, fenced code and existing links are untouched. It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **Callouts** (markdown-it's `alerts` option, GitHub/Obsidian syntax): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line. Styled in `style.css` (GitHub colours, a `--alert` variable per kind, dark variants). Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

### Sortable tables

Clicking a column header of a table in a page's article sorts its rows: ascending, descending, then back to the file's order (`sortTableBy` in `leaf.js`; Enter/Space on a focused header too). View only: the file is never changed and nothing is remembered. A column whose non-empty cells are all numbers (`tableCellNumber`: `-2`, `9.5`, `1,200`, `50 %`, `€3`) sorts numerically, any other column with a numeric-aware, case-insensitive collator (`item 9` before `item 10`, ISO dates in order). Empty cells go last in both directions. The state is the header's `aria-sort`, which the CSS turns into ▲/▼ (↕ on hover). Only `article.page-content` tables with a header, one body, at least two rows and no merged cells (raw-HTML `colspan`/`rowspan`) are made sortable; the editor preview is not. Clicks are delegated, so presentation slides (cloned from the article) sort too.

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

### Task references

`[[task:<id>]]` in a page shows one deck task inline as a live chip. `<id>` is the first 8 characters of the task's id (`deck.SHORT_ID_LEN`, like a short git hash) or the whole id, case ignored (`deck.TASK_REF_RE`). Nobody types ids: in the editor, `[[task:` (or the formatting bar's task button) opens a completion over open tasks (title words or id prefix, newest first, with list and deadline; `taskAcMatches` in `leaf.js`, tasks from `/api/deck`, refetched after a minute) that inserts the reference.

- **Rendering** (`server._mask_task_refs`/`_inject_task_refs`, chip from `_task_ref_html`): masked with private-use tokens (`\ue002N\ue003`) after code masking, so code keeps them literal and markdown never touches the chip, then replaced after `md.render`. The chip: a checkbox, the title (links flattened, escaped) linking to the task in the deck (`_task_href`, list and archive year included), and badges: ⏰ deadline (red overdue, green today), 📅 start when today or later (amber today, blue later), ↻ repeats, ⟲ keep (with "✓ <last done>"), the list's name; done tasks struck through with "✓ <date>", won't-do with "✗ won't do" and a disabled checkbox. Unknown id: a red dashed `task <id>?` (`.task-ref-missing`). A reference alone in a list item makes that `<li class="task-ref-item">`, a checklist row without the chip's box.
- **Resolving** (`deck.resolve_task_refs`): open tasks win over completed ones with the same prefix; archives count. A completed recurring task stands for its series: the reference follows `spawned_from` to the current occurrence, so ticking it and reloading shows the next one.
- **Ticking** the chip's checkbox (`leaf.js`, page view and editor preview) PUTs `{ completed }` to `/api/deck/<id>`, so repeating and keep tasks behave as in the deck, then re-renders the chip from `/api/preview` of `[[task:<full id>]]` (one renderer: a repeating task shows its next occurrence, a keep task its ✓). Undo toast: `completed: !done` plus the task's old `history` (no trace), or for a keep task its old `start_date`, `due_date`, `last_done`, `focus_order` and `history` (fetched from `/api/deck` before the change).
- **Linked tasks** leaves out tasks the page shows inline (`page_task_refs`).
- **Search** indexes a reference as its task's title (`_task_refs_as_titles`, also in `_semantic_text`); a page with references is re-indexed when the deck files change (the page doc cache keeps the deck's mtimes). Embeddings and MCP section docs are cached by page mtime, so they catch up with a renamed task when the page changes.
- **MCP `read_page`** keeps the raw markdown and appends what each reference stands for (title, status, dates, link; `_mcp_task_refs`).
- **Make task** (formatting bar, `makeTasks` in `leaf.js`): turns text in the editor into deck tasks and replaces it with their references, e.g. action items in meeting notes. A selection within one line becomes one task and an inline reference. Otherwise each non-empty line the selection touches (or the cursor's line) becomes a task, keeping its list marker and dropping a checklist's `[ ]` (`- [ ] Call Bob` → `- [[task:…]]`); lines that are already a reference stay. Each task's notes say `From [[<page>]]`.
  - **One item opens the deck's edit modal** to review it first: the editor page carries a hidden deck app (`_build_deck_embed(…, headless=True)`: `data-headless`, `display:none`; `deck.js` then ignores the deck's keyboard shortcuts and the "moved to another list" notice), and `window.openDeckEdit(null, { title, description, start_date: null, onCreated })` opens it with the text as title (its quick-add preview shows the shorthand) and no start date. Save (Enter) creates the task and `onCreated` puts the reference in; Cancel/Esc changes nothing. The same modal serves `c t` in the editor.
  - **Several lines** are created right away, one `POST /api/deck` each, with the deck's quick-add shorthand (`DeckCore.parseQuickAdd`, `deck-core.js` comes with the hidden deck app). If one fails, or the text changed meanwhile, the created ones are deleted again and nothing is replaced.
  - The undo toast (both ways) deletes the tasks (`batch-delete`) and puts the text back.

### Image sizing

- `![alt|300](url)` sets the display width to 300px. markdown-it renders this as `<img alt="alt|300" ...>`, and a post-render regex (`_apply_img_sizes`) rewrites it to `<img alt="alt" style="max-width:100%;width:300px" ...>`.
- Works for any integer pixel width. `max-width:100%` ensures the image never overflows on narrow screens.

### Frontmatter

- A page may start with a leading `---`-fenced block declaring `tags:` (comma-separated, or a bracketed list `[a, b]`) and `tldr:` (a one-line summary, quotes optional). Any other key is parsed and ignored.
- `parse_frontmatter()` extracts it into a dict and returns the remaining content with the block removed. `_render_tags_pills_html()` renders a tag list as clickable pills linking to `/search?q=%23<tag>` (the `#tag` filter: exactly the pages and tasks with that tag, not every page using the word; a tag containing a space can't be written as `#tag` and keeps a plain word search) (styled via the same `.task-tag` class Deck uses); `_render_frontmatter_html()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — with independent `show_tags`/`show_tldr` flags to omit either half.
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
- git runs with `maintenance.autoDetach=false` and `gc.autoDetach=false` (`_GIT_ENV`, also used by the tests' own git calls): git 2.47+ starts its automatic maintenance after a commit as a detached process, which nobody reaped with Leaf as the container's PID 1, so every commit left a zombie until the PID limit (2048) stopped everything (it also crashed test sessions). Not detached, git reaps its own children; `run.sh`'s `--init` covers anything else.
- SHA values are validated with `SHA_RE = r'^[0-9a-f]{7,40}$'` before use.
- History link appears next to Edit button on page view and in the editor toolbar.

### Table of Contents

- `[[toc]]` on a line by itself (case-insensitive) is replaced with a `<nav class="toc">` block listing all `##` and `###` headings in the page as anchor links.
- The TOC is built before `process_wikilinks` runs. `[[toc]]` is replaced with a placeholder, rendered, then the placeholder `<p>` is swapped for the nav HTML.
- Heading anchors are generated by `anchors_plugin` from `mdit-py-plugins`. The `_heading_id()` helper replicates the plugin's slugify for the href values in the TOC.
- If the page has no `##`/`###` headings, `[[toc]]` renders nothing.

### Deck Embed

`[[deck]]` on a line by itself embeds the full Deck app (see Deck) inline, using the same placeholder pattern as `[[toc]]`. Syntax: `[[deck param=value param2=value2]]`.

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `tag` | comma-separated tags | — | Pre-filters by tag. Comma = AND, pipe `a\|b` = OR, a leading `-` excludes (`tag=work,-mary`). |
| `view` | `focus`, `open`, `plan`, `completed` | Focus (or the tab remembered this session) | Opens on that tab on every load; a URL `?view=` overrides it. Old names are still accepted via `_FILTER_ALIASES` (`upcoming`, `today`, `all`, `backlog`, `due`, `done`), as is `filter` for `view`. |
| `limit` | integer 1–99 | none | WIP limit: Focus warns at this many tasks, and Plan day sections with at least this many are flagged amber. `?limit=N` works on `/deck`; `capacity` is an old alias. |
| `list` | list name | — | Makes the embed a separate list (see Deck → Separate lists). Cleaned like tags, lowercased (`_clean_list`). |
| `keep` | `1`/`true`/`yes` | — | New tasks created in the embed are keep tasks (the edit modal's checkbox starts checked), e.g. a shopping list of staples. |
| `labels` | up to 3 comma-separated names | `Open,Plan,Focus` | The embed's own names for the tabs, in on-screen order, e.g. `labels=Catalogue,Plan,Shopping` for a shopping list. Only the words change: views, `view=` values and links keep their names. An empty entry keeps the default, `_` stands for a space (params split on whitespace). Rendered into the tab buttons by `_build_deck_embed`; `deck.js` reads them back (`tabLabel`) for the "← Back to …" link, the limit warning and the ⟲ tooltip. |

```markdown
[[deck]]
[[deck tag=urgent view=plan]]
[[deck view=open]]
[[deck list=groceries tag=weekly]]
[[deck view=focus limit=5]]
[[deck list=groceries keep=1 labels=Catalogue,Plan,Shopping]]
```

**Headless instance**: `_build_deck_embed(params, headless=True)` renders the app hidden, for pages that only need its edit modal (the editor's Make task, see Task references). `openEditModal(null, opts)` takes, besides `tags`, a starting `title`, `description`, `start_date` and an `onCreated(task)` callback.

**Implementation** (all in `deck.py`): `_DECK_RE` finds the directive and `_parse_tasks_params()` parses its `key=value` pairs. `process_deck_embeds()` swaps each one for a numbered placeholder before rendering, and `inject_deck_embeds()` replaces each `<p>PLACEHOLDER</p>` with `_build_deck_embed()`'s HTML afterwards. The embed passes its params as `data-init-*` attributes and loads `deck-core.js` then `deck.js` (`defer`, in that order: deck.js reads `window.DeckCore`). Element ids get a `-<n>` suffix after the first embed, so a page can hold several. `deck.js` also reads `?tag=`, `?view=`, `?search=`, `?task=`, `?new=1` from the URL.

The deck itself is `/deck`; embeds are for custom views on ordinary pages (a tag deck, a list with notes around it). Nothing links to a `deck.md` page.

**Task ↔ page linking** is a `[[wikilink]]` (or `/page/` link) in the task's title or notes, which lists the task under the page's "Linked tasks". The other way round, a page shows one task inline with `[[task:<id>]]` (see Task references). There is no `page` field: `_normalize_task()` turns a legacy stored `page` into `[[slug]]` in the notes, and `page` in embeds or API bodies is ignored.

---

## In-Browser Editor

- `/edit/<slug>` renders a split-pane view: `<textarea>` on the left, live preview on the right.
- Live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- **Toolbar** — one line on every screen: the slug label shrinks with an ellipsis (full slug in its tooltip) and the buttons keep their size, so a long slug never pushes Done off-screen.
- **Formatting bar** (`FORMAT_BAR` in `server.py`, `applyFormat` in `leaf.js`): one line of buttons between the toolbar and the textarea, not WYSIWYG, just markdown help: bold (`**`), italic (`_`), strikethrough, heading (cycles `##` → `###` → none on the cursor's line; the H1 is the title), bulleted/numbered/checklist (on all selected lines, replacing another list marker; all of that kind already → removed; blank lines kept), quote, inline code, code block (fences the selected lines), table (a 2-column template after the current line), link (selected text → `[text]()` with the page autocomplete open; a selected URL → `[](url)`), wikilink (`[[selection]]`, or a bare `[[` that opens the autocomplete), task (`[[task:` after the cursor, opening the task completion), make task (see Task references), attach (file picker → the same upload as paste/drop, `uploadFileAtCursor`). Wraps toggle (markers inside or just outside the selection are removed) and keep surrounding spaces outside. **Ctrl+B / Ctrl+I / Ctrl+K** do bold, italic, link. Every change goes through `execCommand('insertText')` (`replaceRange`), so one Ctrl+Z undoes it and preview/auto-save follow as for typing. Buttons `preventDefault` on pointer/mouse down so the textarea keeps focus and selection (on a phone, the keyboard stays up). On narrow screens the bar scrolls sideways; under `(pointer: coarse)` the buttons are finger-sized.
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
- **Undo** — all programmatic text insertions (list continuation, formatting bar, snippets, wikilink autocomplete, etc.) use `document.execCommand('insertText')` so they integrate correctly with the browser's native undo stack.

### Editor smart helpers

- **Wikilink autocomplete** — typing `[[` opens a dropdown of existing page slugs filtered as you type; `[[task:` lists open deck tasks instead (see Task references). Arrow keys navigate, Enter or Tab inserts `[[slug]]`, Escape closes. Slugs are fetched once from `/api/pages` on load.
- **List continuation** (`continueList(textarea, e)`, also used by the deck's task notes) — pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists). Pressing Enter on an empty list item exits the list.
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
  - Filter input instantly narrows the list by title/slug (`titleMatch`: the better `fuzzyScore` of the title and the **file name**, the slug's last part scored on its own, so a file name starting with the query counts as a match at the start; a match reaching into folder names only scores `FOLDER_PENALTY` less, since ".../2026-05-26_alex-1on1/prep" is not the page "alex" names); after 150ms debounce fetches `/api/search` for full-text **Content matches** with excerpts shown below the page list.
  - **Several words** (`wordsScore`): each must fuzzy-match within one word of the title or file name, and all must match; the scores add up. Matching the whole input as one letter sequence let "alex again" find its letters spread over "Add alert exporter tag handling". A single word still matches across the whole title.
  - **Ties** in the fuzzy score are common (every title starting with the query scores the same), so `tieBreak` orders them: scratch notes last, then fewer folders in the slug, then the shorter name. "alex" lists the top-level `alex-morgan` before notes three folders down and the scratch note `…_alex_prep`, instead of alphabetically.
  - **Sort by** — Recently edited (default, so an empty query works as a quick switcher), Title A–Z, Title Z–A, Most linked. Applies while the query is empty; a sort picked by hand is remembered in `localStorage` (`leaf-search-sort`).
  - **Built-in pages first** — with an empty query, the entries `api_pages_list()` marks `special` (Deck, Scratch, Deleted Pages) are pinned above the pages in that order, whatever the sort, set apart by a stronger rule (`.page-list-special`). On a phone that makes the scratch list two taps away (search button → Scratch). Typing matches them like any other entry; "Unlinked only" leaves them out.
  - **Unlinked only** checkbox — pages with zero incoming wikilinks, in the title list and in the Content matches: the modal sends `/api/search?…&unlinked=1`, and the server keeps only such pages, no tasks (`do_search(unlinked=True)`, same counts as the list's, `all_backlink_counts`) before it cuts the results to 15. Toggling it re-runs the content search.
  - First match is highlighted; **↑/↓** moves the highlight across both page results and content match cards; **Enter** opens the highlighted item.

---

## Scratch

- There is no nav bar; navigation is via keyboard shortcuts. For mouse users, and for phones, which have no keyboard, two round **action buttons** (`ACTION_BUTTONS` in `server.py`, `.action-buttons`) sit in the bottom-right corner: **New note** (`.action-note`) opens the scratch-note dialog (`openScratch`, the `c s` action), and **Search** (`.action-search`, in the corner itself) opens the search modal (`openSearchModal`, the Ctrl+P action). On desktop they are small and muted (2.5rem, full colour on hover); at ≤640px they are finger-sized. `shell(..., action_buttons=False)` omits them, and the editor and the login page leave them out. The toasts sit above them and Sign out moves to the bottom-left corner. On phones they are hidden via CSS `:has()` whenever an input/textarea/select has focus, because the on-screen keyboard owns the bottom edge then, and `main` gets extra bottom space so the buttons never cover the page's end.
- **Status badges** (`.top-badges`: remote 🌐, deck due count, uncommitted count) are fixed at the top-left on desktop. At ≤640px they join the page flow above the breadcrumb, so they no longer cover it, and 🌐 is hidden, since on a phone remote access is a given.
- **Keybinding:** **c** then **s** creates a scratch note (see Keyboard Shortcuts).
- User types a title and submits → `POST /api/scratch` → server creates `<YYYY-MM-DD_HH-MM-SS>_<slug>.md` (local time) with `# Title\n\n` and returns `{ slug }` → browser navigates to `/edit/<slug>`.
- A `<dialog id="shortcuts-modal">` lists all keybindings, opened with `?`.
- **Scratch note filenames** match `SCRATCH_RE = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$"` e.g. `2026-03-24_18-50-08_my-note.md`. `is_scratch(slug)` identifies them.
- **`/page/_scratch`** — special auto-generated page (no `.md` file on disk) listing all scratch notes newest-first with title and timestamp. Not editable.
- Daily notes (`/api/daily`, `/page/_daily`, `c d`/`g y`) were removed on purpose. Existing `YYYY-MM-DD_daily.md` files are ordinary pages.
- **`/page/_deleted`** — special auto-generated page listing all pages that exist in git history but not on disk (deleted pages), newest deletion first. Each entry shows title, slug, deletion date, and a one-click Restore button that calls `POST /restore/<slug>/<sha>`. Only available when `GIT_ENABLED`. Navigating to a deleted page's URL directly also shows a restore prompt instead of redirecting to the editor.
- Appears in the search modal as a synthetic entry injected by `api_pages_list()` with title "Scratch" (pinned at the top of the empty-query list, see Page List) — matches searches for "scratch" or "_scratch". `_deleted` is similarly injected with title "Deleted Pages" (git only). A "Deck" entry (slug shown as `/deck`) links to the built-in deck page; entries may carry an `href` for targets that are not `/page/<slug>`.
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
| **Ctrl/⌘+B / I / K** | editor | Bold / italic / link (see Formatting bar) |
| **Ctrl/⌘+Shift+X** | editor | Toggle checkbox on current line |
| **Ctrl/⌘+Delete** | page view | Delete current page (confirmation dialog) |
| **e** | page view | Navigate to edit mode |
| **p** | page view | Enter presentation mode |
| **Ctrl/⌘+/** | anywhere | Show keyboard shortcuts and snippets (Vimium-safe) |
| **?** | anywhere | Show keyboard shortcuts and snippets (fallback) |
| **n** | deck page | Open new task modal |
| **n** / **N** | page opened from search | Next / previous search highlight (wins over the deck's **n** while highlights show) |
| **Esc** | page opened from search | Clear the search highlights |

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
- `GET /api/search?q=...` collects every `.md` file in `pages/` **and** all deck task titles/descriptions as documents, ranks them, and returns JSON: `[{ slug, title, excerpt, href, meta, type? }]`. `meta` (`_result_meta`) is the small line under a result's title in the modal and on `/search`, telling alike titles apart: a page's folder and last edit (`conversations/2026-07-08_alex-sam-debrief · edited 2026-10-02`), a task's list and status (`Deck › zoo · done 2026-05-01`). `href` is the server-computed destination URL for the result; task results have `type: "task"` and `task_id` fields.
- **Multi-word queries** score each term independently (rarer terms weigh more via IDF); the whole phrase no longer has to appear verbatim.
- **Exact phrase first** (`Query.phrase`, `server._phrase_first`): a document containing the query's words as a phrase (case, accents and spacing ignored, stopwords included) moves to the front, in BM25 order, even one the ranking had cut off: "alex again" puts the note saying "Alex again" above notes with both words apart. Browser search and `search_wiki` alike; the result opens the section with the phrase (`_best_sections`). The squashed text is cached on the doc (`_squashed`).
- **Words in any script**: `search.tokenize` splits on runs of letters/digits (`[^\W_]+`), so "Müller" is one word, and folds case and accents (`search.fold`: ü→u, é→e, ß→ss), so `muller`, `MÜLLER` and `Müller` match each other.
- **Link targets are not indexed** (`search.strip_link_targets`, pages and tasks): the `(url)` of `[text](url)`/`![alt](url)` and the URL of `[id]: url` definitions are dropped before tokenizing and excerpting, the link text stays. Otherwise every page with a `/page/…` link matched `page`. Bare URLs in the text stay searchable.
- **Stemming** (`search.stem`) collapses inflections (run/runs/running, database/databases) so word variations match.
- **Partial words** match via prefix expansion; **typos** match via an edit-distance fuzzy fallback (`difflib`) only when a term has no exact/prefix hit.
- **Title boost**: title tokens are folded into the document token bag `TITLE_WEIGHT` (3) extra times, so title matches rank higher.
- **File and folder names**: a page's slug words are in its token bag once (pages and sections alike), so `robin` finds `messages/2026-06-18_roof-update-alex-robin` even when the text never says it; once, like one mention in the text, so a file name never outranks what pages say.
- Excerpt (`search.make_excerpt`) shows a window around the first match, HTML-escaped, with every match in it marked (`<mark>`). Matches are whole words found the way the index finds them (`_match_spans`: same stem, prefix, else fuzzy), so `running` marks "runs" and `art` never marks the inside of "restart". A multi-word query found as a phrase (case and accents ignored) is marked as one span instead.
- **Performance**: page documents are tokenized once and cached in `server._search_doc_cache` keyed by slug, invalidated per-file on `mtime` change (so on-disk edits stay reflected). The BM25 index itself is cached too (`server._search_index()`), keyed by the identities of the cached docs, so it is rebuilt only when a page or the deck changed; it keeps an inverted index (`_postings`: term → docs), so a search scores only the docs containing its terms, ties in document order. Prefix expansion bisects the sorted vocabulary. Matching words in a text for excerpts, highlights and section links (`_match_spans`) caches each text's split into words and stems (`_analyze`, with the distinct stems sorted for prefix bisection and grouped by length for fuzzy candidates) and each word's fold and stem (`_fold_stem`); that was most of a search's time. `iter_pages` walks with `os.walk`, pruning `_*` and hidden folders instead of walking `.git`'s objects. A search takes a few tens of ms; a test wiki of 330 pages and 3,600 sections went from ~150 to ~25 ms. Deck task docs come from `deck.task_search_docs()`: title, notes and tags (as `#tag` words in the text, and as a `tags` list for the `#tag` filter).
- **Results open the matching section** (`_best_sections`, `do_search`): a page result's `href` gets the anchor of the section the query matched, and its excerpt comes from that section, so the excerpt shows what the link jumps to. The section is one whose own text has the query as a phrase, else the best BM25-scored one whose own text (heading and body, not the page title) has a query word (`search.matches`), else, for a page found by meaning, its most similar section. A page matched only by its title gets no anchor (every section would match equally), nor does a match before the first heading or under a `####`+ heading, which has no id (`_section_anchor`; anchors_plugin `max_level=3`). Sections come from the same split as retrieval (`_page_chunk_docs`).
- **Highlights on the opened page** (`search.matched_words`, `initSearchHighlights` in `leaf.js`): a page result's `href` carries the words that matched as `?hl=…` (before the `#anchor`), as written in that page, so stems, typos and phrases are already resolved ("running" → `hl=runs`, a phrase as one `hl`); at most 10, tasks get none. The page marks every whole-word occurrence (`mark.search-hl`; phrase words may be separated by any whitespace), skipping `<pre>` code (highlight.js rewrites it) and deck embeds (`.tasks-app`). A bar (`.search-hl-bar`, bottom centre, top on phones where the bottom corners hold the action buttons) shows the terms and "2 of 5" with ↑ ↓ ×; **n**/**N** step (in the capture phase, so a deck embed's `n` doesn't also open a new task), **Esc** or × unmarks and drops `hl` from the URL (`history.replaceState`). It starts at the first match inside the section the `#anchor` opened. Listed in the `?` help.
- `GET /search?q=...` renders a full results page for direct links / no-JS fallback.
- Search is case-insensitive. Returns up to 15 results (pages + tasks combined; `SEARCH_LIMIT`), or `&limit=N` (1–100, `SEARCH_MAX_LIMIT`). The search modal asks for 16: when the 16th comes back it shows 15 and an **All results →** link (`.search-more`) to `/search?q=…` (plus `&unlinked=1` when that box is checked), which lists up to 50 (`SEARCH_PAGE_LIMIT`) under a count ("17 results", "The first 50 results"). With semantic search each ranking goes as deep as the list must be long (`_ranked_docs`, `depth`).
- **Semantic search (opt-in, `src/embed.py`)**: set `LEAF_EMBEDDINGS` in `.env` to `fastembed` (local ONNX model, default `jinaai/jina-embeddings-v2-base-de`, German + English) or `fastembed:<model>`. (An Ollama backend existed and was removed as unused; it is in the git history.) Unset, Leaf is keyword-only and nothing below runs. With a fastembed model, `run.sh` passes the setting as a build arg and the `Containerfile` runs `embed_setup.py`, which installs fastembed and bakes the model into the image (`FASTEMBED_CACHE_PATH=/opt/fastembed`): the image grows by about 1 GB, and the first search doesn't wait for a download. Only `embed_setup.py` (model choice, `parse_spec`, `prepare`) is copied before that step, so the layer is rebuilt when the model setting or that small file changes, not on every edit to `embed.py`; it once re-downloaded the model on each rebuild. A missing dependency, a bad setting or a failing model is logged (`[semantic] …`) and search falls back to keywords.
  - **Chunks**: each page is split at its headings outside code fences (`chunk_page`), long sections further at paragraphs (`MAX_CHUNK_CHARS`). Each chunk is embedded as `title\nheading\nbody`; `server._semantic_text` drops the frontmatter block, the H1 and the `[[deck …]]`/`[[toc]]` directives (markup, not content: pages holding only a deck embed came out as each other's closest match) and puts tldr and tags first. **Deck tasks** are embedded too, one vector each, from their search text (title, notes, `#tags`; `tasks_fn` in `server.py`), keyed by a hash of it in the same cache (`"tasks"`: id → hash), so only new or edited tasks are embedded.
  - **Storage**: no vector database. Vectors are in memory and cached in `$WIKI_DATA/_index/embeddings.json` (rebuildable, listed in `.git/info/exclude` so it never shows as uncommitted; `iter_pages` skips `_*` dirs). The cache is content-addressed (chunk text hash → vector) and per model: editing one section re-embeds only that section, a model change starts over.
  - **Freshness**: a background thread (`SemanticIndex.start`) syncs at startup and whenever a search or page view pokes it; requests never wait for embedding, they query what is ready. A deleted page is filtered out by the caller (`known=`) before the worker catches up.
  - **Query**: brute-force cosine similarity, a page scoring as its best chunk. With NumPy (it comes with fastembed) the vectors are one matrix per index generation and a query is one product (~1 ms for 3,600 sections), else plain Python (~40 ms). `_scored` scores all sections and tasks for a query once per generation and keeps the last 32 queries' scores, so one search (pages, tasks, section links) scores each vector once. Hits below a per-model floor (`embed.THRESHOLDS`: search, related, tasks; measured on a test wiki; `LEAF_EMBED_MIN_SCORE` overrides the search one) are dropped, since vector search otherwise "finds" something for any query. Tasks have their own, higher floor (0.3 for the default model): short texts let nonsense score higher ("xqzt blorf" reached 0.27 against a task title).
  - **Hybrid ranking**: `server._ranked_docs` merges the BM25 list and the semantic list (page hits and task hits, ordered by similarity together, `_by_similarity`) by reciprocal rank fusion (`search.fuse`, `RRF_K = 60`, ranks only, so the two score scales never need normalising; ties go to BM25). A page found only by meaning gets its most similar section as excerpt and link target (see "Results open the matching section"), since it has no word to mark.
  - **Related pages**: each page's vector is the mean of its chunks; `SemanticIndex.related` lists the nearest above the related floor. A page with little prose (under `RELATED_MIN_CHARS`, 100 letters and digits outside links: `embed.prose_chars` ignores `[[wikilinks]]`, link labels and bare URLs) neither gets a list nor appears in one. That covers near-empty pages, whose vector is little more than their title, and hubs that are only lists of links (like the index), whose vector averages everything they link to; their neighbours were noise. Search still finds them.
- **Selecting a task result** (search modal or `/search` page) opens `/deck?task=<id>&view=<open|completed>` (plus `&list=<name>` for a task in a separate list, so it opens that list) (plus `&year=YYYY` for a task in an earlier year's archive) on the tab matching the task's state, with that task selected, scrolled into view and briefly highlighted (`.task-flash`) — rather than the bare `_deck` storage slug. The link is by id, not title: titles can repeat or contain a `#word` the filter field would read as a tag. Built by `_result_href()` in `server.py` (the search modal uses the result's `href`). A page's "Linked tasks" entries use the same link.

- **Query syntax** (`search.Query`, the same in `/api/search`, `/search`, `search_wiki` and `search_tasks`' query; listed in the `?` help modal): `"exact phrase"` (case, accents and spacing ignored), `-word` (its stem must not occur; `-dockers` excludes "docker"), `-"phrase"`, `#tag` (pages by frontmatter `tags:`, tasks by their tags, case ignored) and `-#tag`, and **date filters**: `edited:2026-09` (a year, month or day, as a prefix), `after:2026-07-01` (on or after), `before:2026-08` (strictly before; partial dates compare as prefixes, so this is July and earlier), negated as `-edited:` (not then), `-after:` (= `before:`), `-before:` (= `after:`). A page's date is its last edit, a task's its last activity (`deck._last_activity`: completion, else the latest history event, else creation; `_doc_date`); undated docs fail a date filter. A query of only date filters lists everything passing, newest first (`_ranked_docs`), e.g. `edited:2026-09`. An unclosed quote, a lone `-` or `#`, or an invalid date (`edited:soon`) stay text. `Query.text` (words and phrases, no operators) ranks, both by keyword and by meaning, and is what excerpts mark; the rest are filters (`Query.accepts`, applied by `server._accepts` before ranking, so meaning hits obey them too). A query of only tags ranks by the tag words (`ranking_text`); one of only exclusions finds nothing. Docs carry lowercased `tags` for this (`_page_tags`, `task_search_docs`). In the search modal the instant title filter fuzzy-matches only the query's words (`queryWords` in `leaf.js`, mirroring `Query`: quotes dropped, `-…`, `#tag` and date-filter parts left out, so a query of only `#tag` shows no title hits), while Content matches get the raw query.
- **Stopwords** (`search.STOPWORDS`, `query_words`): English and German function words ("the", "is", "in", "und", "für") are dropped from the **query**, never from the index, unless that leaves nothing ("what is this" still searches). In a small wiki they aren't in every page, so BM25 gave them weight: a question phrased as a sentence ranked pages by its filler words. Words that are also nouns or names (may, will, it, man) are not in the list. Excerpts don't mark them either; a phrase match still marks the whole phrase.

---

## MCP server

`POST /mcp` lets an LLM client (any MCP client) read the wiki and the deck through tools. Protocol in `src/mcp_server.py` (JSON-RPC, no Flask), route and tools in `server.py` (`mcp_endpoint`, `_mcp_call_tool`). MCP's Streamable HTTP transport, reduced to what tools need: each POST carries one message (or a batch) and is answered with plain JSON; notifications get 202; no event stream (GET → 405), no sessions. `initialize` echoes the client's protocol version when it is in `PROTOCOL_VERSIONS`, else the newest, and sends `instructions` telling the model what Leaf is.

Register `http://localhost:5000/mcp` with your MCP client (HTTP transport) and the header `Authorization: Bearer <LEAF_MCP_TOKEN>`.

Tools, all read-only (`readOnlyHint`). A tool failure the model can fix (bad slug, empty query) is a result with `isError`, not a protocol error:

| Tool | Does |
|------|------|
| `search_wiki(query, limit=8, under)` | The most relevant **sections**, full text, best first, each with title › heading, slug, the page's last edit date (`edited:`, so the model can tell current notes from stale ones) and a URL with the heading anchor. Several may come from one page. Matching deck tasks are results too, ranked with the sections. `under` restricts it to a folder: pages whose slug is that prefix or below it (`_in_folder`, case ignored; `tickets`, `tickets/` and `/page/tickets` alike, `_clean_folder`), and no tasks. A folder without pages is a tool error, so the model corrects it. |
| `search_tasks(tag, status="open", query, list, limit=50)` | Deck tasks filtered by tag (comma = all must match, case-insensitive), status (`open`, `done` = completed or won't do, `all`) and separate list, all lists included unless `list` is given. With a query, ranked by BM25 within the filter; without, open tasks by deadline, then start date, then title, done ones newest first. Starts with "Today is …" so the model can tell overdue from upcoming. |
| `read_page(slug)` | A page's raw markdown, frontmatter included, plus a list of the deck tasks its `[[task:…]]` references stand for. Accepts a slug, `/page/<slug>`, a full URL, `<slug>.md` or the page name (`_mcp_page_slug`); `safe_path` keeps it inside the wiki. |
| `list_pages(limit=100, under)` | `slug \| title \| edited date`, most recently edited first; `under` lists one folder. |

**Retrieval** (`server.retrieve`) works on sections, not pages: an LLM needs the passages, and the best ones may share a page. BM25 runs over sections (`_page_chunk_docs`, the same `embed.chunk_sections` split, cached per page by mtime; `_chunk_index`), fused with `SEMANTIC.search_chunks` when semantic search is on (sections match by `embed.chunk_key`). Without semantic search it is keyword-only and still works. Deck tasks share that BM25 index (`deck.task_search_docs()`, each doc carrying its `task`), so they rank with sections, as in the browser search, and their semantic hits (`SEMANTIC.search_tasks`) join the section hits by similarity. `search_tasks` with a query fuses BM25 and semantic hits within its filter. A task is shown by `_mcp_task_text`: title, a line of facts (status, tags, start, due, repeat, keep, list), the `/deck?task=…` link (`_task_href`) and its notes.

**Links** in tool results start with `LEAF_BASE_URL` (e.g. `http://localhost:5000`, Leaf's address as your browser knows it) when set, else with the address the request came in on. A client in a container reaches Leaf as `host.containers.internal`, and links with that host don't open in the browser on the host.

**Auth**: `/mcp` is exempt from the session login (`check_auth`), since an MCP client can't log in, and checks its own: with `LEAF_MCP_TOKEN` set, every request needs `Authorization: Bearer <token>`; without one, `/mcp` is as open as the pages are, so a wiki with `LEAF_PASSWORD` keeps it closed (401) until a token is set. The Host and cross-site Origin checks apply as everywhere.

---

## Deck (Task App)

A task app at `/deck` (a built-in page, `deck_page()`), also embeddable via `[[deck]]` (see Deck Embed). Server side is `src/deck.py` (a Flask Blueprint: `/api/deck/*` routes, embed HTML, task search docs, task backlinks). Client side is `deck.js` (DOM, one instance per embed) plus `deck-core.js` (pure logic, tested under Node).

Several embeds per page work: each instance keeps its own task array, and a write in one refreshes the others (`refreshOtherInstances`). Every instance also reloads on `visibilitychange` and bfcache `pageshow`, so a tab left open for days shows current tasks and today's day sections. `refreshIfIdle` skips the reload while a drag, swipe, completion grace period, inline edit, ⋯ menu or edit modal is in progress. There is no polling.

### Storage

- Open tasks live in `_deck.json`, completed ones in `_deck_completed.json` (flat JSON arrays, committed to git on change). Completed tasks from earlier years live in `_deck_completed-YYYY.json` (only that exact name is an archive). Write routes read every completed file (`_read_completed_all_strict`) and `_write_completed` files each task by its completion year, writing only files whose content changed; startup does the same split (`_archive_old_completed`) for a year change without writes. So archived tasks can be edited, reopened, deleted, tagged and restored like any other; Completed shows them by year (see Views).
- All mutations go through a module lock (`_LOCK`) and atomic writes (temp file + `os.replace`); the dev server is threaded. Write paths use a strict reader that raises `_DeckCorrupt` (503) on an unreadable file instead of treating it as empty and wiping every task.
- Request bodies are validated (`_json_object`, `_as_str`, `_as_int`, `_as_str_list`, `_id_list`): bad input is a 400 with JSON `{ error }`, never a 500. Numeric strings are accepted for integers; `tags` may be a comma-separated string.
- `_normalize_task()` upgrades legacy fields on every read and restore (`focus_date` → `start_date`, `defer_days`, `recurrence.anchor`, `page`); `_migrate_deck_files()` rewrites the files once at startup in its own commit.

### Dates

A task has two independent dates; changing one never moves the other.

- **`due_date`**: the deadline. Only a past due date makes a task overdue.
- **`start_date`**: the day the task becomes available, and the commitment to work on it. Having one makes a task *planned*. A past start date is not overdue, it means "available since" (cards show "since Sep 21").

The edit modal's "Days before due" is just another way to enter the start date (`start = due − N`) and is not stored. Recurring instances keep the gap between the two dates, but a new instance's start date is never before today (it can't have been available before the previous one was done); when that clamps it, the series remembers the gap in `recurrence.lead` for the next instance.

### Task fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID string | |
| `title` | string | Required |
| `description` | string | Notes (inline markdown; `- [ ] item` lines render as checkboxes, see Interaction) |
| `tags` | string[] | One word each. `_clean_tags()` on every write/read/restore: inner whitespace → `-`, leading `#` dropped, empties removed, case-insensitive duplicates collapsed. Tag inputs sanitize as you type (`DeckCore.sanitizeTagInput`), leaving deletions alone. |
| `due_date` | `"YYYY-MM-DD"` / `null` | Deadline |
| `start_date` | `"YYYY-MM-DD"` / `null` | Start date (see Dates) |
| `created_at` | ISO datetime | |
| `completed_at` | ISO datetime / `null` | |
| `sort_order` | number / `null` | Position in Open's Sorted section; `null` = Unsorted |
| `focus_order` | number / `null` | Position in Focus's Lineup; `null` = Others |
| `recurrence` | `{ type, interval, from, day?, lead? }` / `null` | `type`: `daily`/`weekly`/`monthly`/`yearly`. Driven by the primary date (`due_date`, else `start_date`). `from`: `"due"` (default, calendar-aligned) or `"completion"`. `day` (monthly/yearly) is the series' day of month, so Jan 31 → Feb 28 → Mar 31; kept on re-save unless type or primary date changes. `lead` is the start→due gap, stored only while a spawned start was clamped to today; editing either date drops it, an unchanged modal re-save keeps it. A keep task never has one (`keep` clears it). |
| `list` | string / `null` | Separate list; `null` = main deck |
| `spawned_from` | string / `null` | Recurring instance: id of the completed task that spawned it, so undoing the completion removes exactly this instance |
| `wont_do` | bool | Abandoned: archived like a completed task, shown with "✗ won't do", never spawns a recurring instance |
| `keep` | bool | Keep task: completing it doesn't archive it (see Keep tasks) |
| `last_done` | `"YYYY-MM-DD"` / `null` | A keep task's last completion, shown on its card as "✓ Sep 21" |
| `history` | `[{ at, event, from?, to? }]` | What happened to the task, oldest first, capped at 30 (`HISTORY_LIMIT`): `created`, `start`/`due` (with `from`/`to` dates), `done`, `reopened`, `wontdo`, `skipped`, `repeat` (`to` = the next occurrence's date). `at` is `YYYY-MM-DDTHH:MM`. See History. |

### Views

A segmented control (`.view-tabs`, one `<button data-filter>` each) in the order **Open · Plan · Focus** (`data-filter` `open`/`due`/`focus`). **Tab**/**Shift+Tab** cycle them. Initial view: URL `?view=` > embed `view=` > tab remembered in `sessionStorage` (plain embeds only) > Focus. On phones the control spans the full width.

- **Open**: every open task, in two sections: **Unsorted** (no `sort_order`, newest first; new tasks land here) above **Sorted** (manual order). Open has no filter for unplanned tasks (an "Unplanned" toggle was removed): those are the Plan board's Unplanned section, where dragging to a day plans them. So Unsorted means "not yet ranked" and Unplanned "not yet scheduled", each with one home. A leftover `unplanned=` embed param is ignored.
- **Plan** (`due`): a day board grouped by `DeckCore.bucketKey`: Overdue (`due_date < today`, display-only), Today, Tomorrow, the next weekdays, Later, Unplanned (no start date; nearest deadline first). Later and Unplanned always show. Dropping a card on a day sets its start date, on Unplanned clears it, on Later opens a date picker (min today+7). An empty section is only its dashed header row (min 1.9rem, 2.5rem on coarse pointers), which takes the drop; nothing grows when a drag starts. Day headers show a count and turn amber at the limit. The board only decides the day: inside a section tasks are in Open's order (`DeckCore.byOpenOrder`, shared with Open), except Overdue (by deadline) and Unplanned (nearest deadline first, then Open's order). A drop within a task's own section, on a task or on its heading, does nothing; to reorder, use Open (or Focus for today). The one exception is Later's heading, which opens the date picker to choose another later date. A drop on a day after the task's deadline (`planOnDay`) would leave an overdue or due-today card where the deadline puts it, so it asks "Move the deadline too?" (`#task-deadline-modal`): Move deadline sets start and due to that day, with an undo toast restoring both; Cancel writes nothing. Plan's Today and Focus can list tasks in a different order: Focus puts the Lineup first and never-sorted tasks last, while Open puts new tasks first. Wiring: `setupBoardDragAndDrop` per card plus list-level handlers attached once.
- **Focus**: what you're working on now (`DeckCore.isFocus`: `start_date <= today`, or due today/overdue; the board's Overdue + Today). It has two sections, the **Lineup** (`focus_order`, the order you tackle tasks in) and **Others** (in Open's order, never-sorted tasks last, oldest first; `DeckCore.byFocusOrder`). Due and overdue tasks have no section of their own (there used to be a Due section on top): like on Open they keep their place in the order, marked by their deadline colour, and can be lined up like any task. The due badge links here. Others' heading has **Line up all** (`lineUpAll`), appending its visible tasks to the Lineup. The Lineup persists across days. A Lineup place lasts exactly as long as the task stays in Focus: a write that takes it out of Focus, or brings it in, clears `focus_order` (`_in_focus` in `deck.py`, mirroring `isFocus`), unless the request itself sends `focus_order` (used by undo). Starting a due-today task keeps its place; moving a deadline out of Focus drops it. A stale place on a task outside Focus is cleared on read (`_normalize_task`); time only moves tasks into Focus, never out. With a limit, a warning shows when Focus reaches it. Empty Focus says "Nothing planned for today" and links to Plan. New tasks created here get today as start date.
- **Completed** is not a tab but a link under the list (`#task-completed-link`, `✓ Completed (N) →`, count from `/api/deck/completed-count`, refreshed after every write). In Completed, the link reads `← Back to <tab>`, naming the tab it returns to. While filtering another view it counts the matching completed tasks instead (`✓ 2 matches in Completed →`, no number while loading). `/api/deck` returns open tasks only, so completed tasks are fetched on demand (`fetchCompleted`, one request at a time); `deckGen` refetches if `fetchDeck` replaced the list meanwhile. Completed's heading has a **year switcher** (`.completed-years`, years from `/api/deck/completed-years`, shown when there is more than one) for the yearly archives; `?year=YYYY` opens that year (search links to archived tasks carry it), and going back to a tab returns to this year (`setCompletedYear`).

**Sections** (Open and Focus; `SECTIONS`, `sectionHTML`, `.list-section[data-section]`): each tab has a manual-order section (Sorted, Lineup) and a fixed-order one (Unsorted, Others). Moves treat both headings as slots in the visible list (`sectionList`, `applySectionList`): which side of the second heading a task lands on decides its section; dropping on the manual section's heading moves the task to that end; reordering within the fixed section does nothing; leaving the manual order sets the field to `null`. Ctrl+Shift+↑/↓ moves one slot, headings counting. Every save renumbers the tab's whole manual order densely, keeping the slots of tasks hidden by the filter.

**No hiding**: every open task is always reachable from its own deck (Open or Plan) and from search. Dates decide where a task appears, never whether.

### Interaction

- **Filter field** (`#task-filter-search`, `DeckCore.parseFilter`/`matchesFilter`): `#words` filter by tag (all must match; `#a|b` = any), `-#word` excludes a tag (`-#a|b`: neither), the rest searches title and notes. `/` focuses it, `#` focuses it and starts a tag. Typing a `#word` (or `-#word`) suggests tags of the tasks the other tag words leave visible (free text is ignored for this). Embed `tag=`/`?tag=` become a query via `DeckCore.tagParamToQuery`. New tasks get the filter's single tags.
- **Tag chips** (`chipField` in `deck.js`): in the filter field and the edit modal's Tags field a completed tag becomes a chip in front of the typed text (`.chip-input` box wrapping the input, `.tag-chip`). A chip can't be edited, only removed: Backspace at the start of the field removes the last one, its × any. Filter chips are whole filter words (`#a`, `-#b`, `#a|b`); the query is the chips plus the text (`filterQuery()`), and `?tag=`/`[[deck tag=…]]` arrive as chips. In the Tags field a completion or a comma (typed or pasted) makes a chip, with no `, ` left behind; Save takes the chips plus a half-typed tag (`editTags()`). The dropdown hangs under the whole box.
- **Tag completion** (`attachTagAc`: filter, Tags field, title `#tag` shorthand, bulk tag dialog `t`/`T`): ranked by `DeckCore.rankTags` (exact, prefix, substring, in-order letters; then usage count, then A–Z). The best match is pre-highlighted so Tab/Enter completes it; Esc closes only the dropdown. Keys are handled in the capture phase. The dropdown is appended to the field's parent, since in a `<dialog>` only the top layer is visible.
- **Quick-add** (new tasks only, `DeckCore.parseQuickAdd`): trailing shorthand like `Call Bob tomorrow #work due:fri`. Start: `today`, `tomorrow`/`tmrw`, a weekday (next one, never today), `+3`/`+3d`/`+2w`, `YYYY-MM-DD`; due: `due:` + the same; tags: `#tag` starting with a letter. Only trailing words count, and shorthand that would empty the title is ignored. `#task-edit-quick` previews the result; shorthand wins over the date fields.
- **Completing**: the checkbox completes after a cancellable grace period. Completing a recurring task spawns the next instance, stepping from the previous primary date (or today with `from: completion`) until ≥ today, with its start date not before today (see Dates); month steps clamp to the month's last day. Un-completing removes the spawned instance.
- **Checkboxes in notes**: a `- [ ] item` line (`*`/`+` too, `[x]` ticked) renders as a real checkbox in the card's notes (`renderDesc`). Ticking it flips the line in the description and saves (`DeckCore.toggleDescCheck`, which numbers items with the same `DESC_CHECK_RE` the renderer uses). The task's own checkbox is `.task-checkbox input`, so the two never mix. In the edit modal's notes, Enter continues a list exactly as in the page editor (`continueList`, shared from `leaf.js` like `pasteUrlAsLink`): after `- [x] eggs` it starts `- [ ] `, and Enter on an empty item ends the list.
- **Won't do** (✗ button, ⋯ menu) archives without spawning; **Skip** (⏭, recurring only) archives as won't-do and spawns the next instance. Both show an undo toast; reopening a skipped entry removes its spawn.
- **Delete** (`deleteWithUndo()`, from keyboard, modal or menu) confirms, then offers undo via `/api/deck/restore` with full snapshots.
- **Planned cue**: a planned task's card gets a coloured left edge, the same on every tab for consistency: amber when it is in Focus already (start today or earlier), blue when planned later (`task-planned-now`/`-later`). On Open it tells decided tasks from undecided ones without changing the order. A due/overdue urgency colour wins. The 📅 date is coloured too: blue for a later start, amber for today, plain for a past start ("since …", never a warning).
- **Inline dates**: the ⏰ (due) and 📅 (start) chips open a date picker. **f** starts the selected task today, **F** tomorrow (again clears).
- **⋯ menu** (`.task-menu-btn`): all card actions (Edit…, Start today/tomorrow, Pick start date…, Unplan, Skip, Won't do, Delete…; for completed tasks Edit…, Reopen, Delete…). A popover with ↑/↓/Enter/Esc on desktop (deck shortcuts suspended while open), a bottom sheet at ≤640px.
- **Edit modal**: double-click or Enter on desktop, tap on touch (pointer type of the last `pointerdown`), or Edit… in the menu. Centred with `inset: 0; margin: auto` and **no transform** (a transform would clip `position: fixed` popups like the date picker), scrollable, sticky action bar. At ≤640px full screen with Due | Start and Repeat | Every | From done on shared rows (`.task-row-dates`, `.task-row-repeat`). The List field sits in a closed `<details>`; saving a task into another list removes it from the view with a notice.
- **Date picker** (`openDatePicker`): positioned after every `draw()` (below the anchor, else above, else inside the window; never past a screen edge). A press outside only closes it: `pointerdown` in the capture phase, then `swallowNextClick` eats that press's mouse/click events, so a tap can't also hit what's underneath.
- **`?task=<id>`** selects, scrolls to and flashes (`.task-flash`) that task; a missing task gets a notice. A newly created task is flashed the same way when the view shows it.
- **Rejected writes** show the server's error as a toast (`showNotice`).
- **Header**: tabs, filter and **+ New** on one line on desktop (`.tasks-toolbar` `flex: 1 1 20rem`), two lines on phones with a label-less "+" (accessible name "New task").

### History

The server logs events on the task (`_log` in `deck.py`): `created` (create and recurring spawn; a new task's first dates are not changes), `start`/`due` whenever the date actually changes (`_apply_task_fields`), `done` (also for a keep task, whose cleared start date is part of it), `reopened`, `wontdo` and `skipped`. A recurring task's next occurrence carries the series' history on (with the `done` or `skipped` just logged) plus a `repeat` entry ("Repeats: next Oct 3"), so the series reads as one log; it gets no `created` of its own. Only these; title, tag or order edits are not logged. Undo must not leave a trace, so every undo that PUTs old fields also sends the task's old `history` (`oldHistory` in `deck.js`: start-date, won't-do, skip and keep undos), and the server applies a sent `history` last (`_clean_history` validates it), replacing what that request logged. Restore snapshots keep it too. Reopen-undo and bulk-complete-undo go through `batch-complete` and do log (`done`/`reopened`).

The edit modal shows it in a closed **History** section (newest first, with a count), next to **List** on one line while both are closed (`.task-edit-extras`; an opened one takes a full-width line below). Creation is the oldest entry; tasks from before the log get one from `created_at`. There is no separate "Created:" line.

### Keep tasks

A task with `keep: true` is never archived: ticking it off means "done for now, back to the pool". It suits a shopping list used with the deck's views: Open is the catalogue of staples, starting items today puts them on Focus (the actual shopping list), and ticking one off sends it back to Open. Completing a keep task (`_keep_done`, in `PUT` and in `batch-complete`) clears its start date, its deadline and its Lineup place, and sets `last_done` to today; it stays in `_deck.json` with the same id, tags, notes and place in Open's order, and Completed stays empty. Since Completed can't reopen it, the card shows an undo toast that restores the old fields (`keepSnapshot`; `focus_order` is sent explicitly so it isn't cleared). **Won't do** and **Delete** retire a staple. Keep and Repeat both say what happens after done, so they exclude each other: `keep` clears `recurrence`, and the edit modal disables Repeat while "Keep after done" is checked. Cards mark keep tasks with ⟲ (`.task-keep`). `[[deck keep=1]]` makes new tasks keep tasks.

### Separate lists

A task's `list` field puts it in a named list shown only in `[[deck list=<name>]]` embeds and at `/deck?list=<name>`. List tasks are kept out of the main deck, tag embeds, the due badge and the main Completed count. `deck.js` drops out-of-scope tasks right after fetching (`inScope`), so suggestions, counts and reordering only see the list's own tasks. Storage is still `_deck.json`; the edit modal's List field (suggestions from `known_lists()`) moves tasks between lists. Search results and Linked tasks link to `/deck?…&list=<name>`.

### Touch

- Under `(hover: none)` the ✗/⏭ icons are hidden (the ⋯ menu has them); under `(pointer: coarse)` buttons, checkboxes and chips get finger-sized targets, fields use 16px text (no iOS zoom), and cards can't be text-selected.
- **Long-press drag**: 350 ms without moving over 10px picks a card up and selects it (without scrolling). A floating copy (`.task-touch-ghost`) follows the finger, the page auto-scrolls near the edges, and release drops through the same code as a mouse drop (`touchDrop` adapters registered by each render; list touch listeners attached once). A touch `pointerdown` sets the card's `draggable` to false (mouse sets it back; cards are marked `data-drag`), and native `dragstart` is cancelled for touch, because Android Chrome and iOS Safari otherwise start their own HTML5 drag mid-gesture. Moving earlier is a normal scroll; a finished drag isn't a tap.
- **Swipes** (`swipeActions`) only move tasks in and out of Focus: right = start today (Open, Plan), left = clear the start date (Focus, Plan). No action, no movement (e.g. right on a Focus task, left on an unplanned or due task). A mostly sideways move (|dx| > 1.5·|dy|, over 10px) drags the card and reveals the label (`data-swipe-label`); past `min(110px, 30%)` it arms (`.task-swipe-armed`), and release runs `setStart` with an undo toast. Cards have `touch-action: pan-y` and `.tasks-list` `overflow-x: clip`.

### Keyboard shortcuts

- **g d**: go to `/deck`
- **c t**: quick-add a task tagged `task` (from any page)

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`).
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- No nav bar (keyboard shortcuts); floating New note and Search buttons in the bottom-right corner (see the Scratch section).
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

`tests/test_mcp.py` covers the MCP server: the protocol handler, the HTTP transport and auth, and the four tools (tasks created through `/api/deck`) (with and without the fake semantic index from `test_semantic.py`).

`tests/test_task_refs.py` covers `[[task:…]]` references: chip rendering and badges, done/won't-do/missing, recurring series, list rows, code, Linked tasks, search by task title and MCP `read_page`. `tests/e2e/test_task_refs.py` ticks chips (undo, keep tasks), the editor completion and Make task (the modal for one item, Cancel, several lines at once, inline selection, undo, the hidden deck leaving the editor's keys alone).

`tests/test_semantic.py` covers semantic search with a fake embedder (words mapped to a few "concept" axes), so it needs neither fastembed nor a model: chunking, the content-addressed cache, failure fallback, the worker, rank fusion, hybrid `/api/search` and the Related section. It swaps `server.SEMANTIC` for an index over the temp wiki.

To run the suite without a local venv: `./run.sh test` (runs inside the app image with the repo mounted).

### Browser tests (`tests/e2e/`)

End-to-end tests drive the real app in headless Chromium via Playwright. They run in a separate test image (`Containerfile.e2e`, based on Microsoft's Playwright image) so the app image stays browser-free and the app still has no build step or npm. `./run.sh e2e` builds that image (cached) and runs them with the repo bind-mounted; extra args pass through to pytest (e.g. `./run.sh e2e -k deck`). Without Playwright installed they skip, so plain `pytest tests/` is unaffected.

- `conftest.py` serves `server.app` on a background thread (werkzeug `make_server`, random port) and points it at a fresh temp wiki with its own git repo per test; each test gets a fresh browser context. A request to any other host is aborted (offline, deterministic) and fails the test: Leaf loads nothing from a CDN. Uncaught page JS errors fail the test.
- Coverage: editor (formatting bar and Ctrl+B/I/K, auto-save + unsaved dot, no commit on auto-save, Done/Ctrl+S commit, pagehide beacon, live preview/F2, list continuation), search modal (Ctrl+P/Esc, fuzzy ranking with file names and its tie-break, content matches, query syntax, highlights on the opened page, arrow/Enter nav, unlinked filter), deck (Plan-board drag to a day and to Unplanned, touch long-press drag via CDP touch events (board, Open-tab reorder, swipe still scrolls, long press is not a tap), board grouping by start date, modal "Days before due", Plan board in Open's order with in-section drops ignored, view tabs, start-date Today, touch: tap-to-edit, ⋯ menu/bottom sheet, target sizes, editor scrolling/compact phone layout, date picker placement), sortable tables (header click cycles ascending/descending/original, numeric columns, empty cells last, keyboard, not in the editor preview), layout (no horizontal page scroll at 390px phone width on page/editor/deck views, mobile Preview button, tldr toggle geometry).
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

`highlight.js` 11.9.0 (script plus the `github`/`github-dark` themes) is vendored in `src/public/vendor/highlight/`, committed as downloaded: no CDN, no npm, no build step. Pages load nothing from other hosts. To update it, replace the three files with a newer release's.

Optional: `fastembed` (pulls in onnxruntime and NumPy) for semantic search with a local model. It is not in `requirements.txt`; the `Containerfile` installs it only when `LEAF_EMBEDDINGS` names a fastembed model (`embed_setup.py`); NumPy comes with it (fast vector scoring; `embed.py` falls back to plain Python without it, e.g. in a local venv). Locally: `pip install fastembed`.

---

## Key Constraints

- Do not use a database. Files are the only storage.
- Optional password auth: set `LEAF_PASSWORD` in `.env` to enable a login gate. If unset, no auth is required. `SECRET_KEY` can also be set to keep sessions across restarts; if absent, a random key is generated per-process. Routes `/login` (GET/POST) and `/logout` (POST) handle the session. A "Sign out" button is rendered in the bottom-left corner when auth is enabled (the bottom-right holds the New note/Search buttons; bottom-right only on pages without them); on phones, like them, it is hidden while typing.
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
