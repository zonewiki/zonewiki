# Leaf

A local-first, browser-based wiki. Markdown files are served and rendered at runtime by a Python server. Pages can be edited directly on disk or via an in-browser editor. Full-text search runs server-side.

---

## Meta

- **Keep this file up to date** — whenever the project structure, commands, constraints, or conventions change, update `AGENTS.md` to reflect the new state immediately.

---

## Goals & Principles

- **Simple over clever** — minimal dependencies, no build step, no bundler
- **Markdown is the source of truth** — `.md` files in `pages/` are the wiki content. Deliberate exception: Deck tasks live in `_deck/tasks.json`/`_deck/completed.json` (structured fields, concurrency-safe writes) — still plain files, still git-versioned, but not markdown.
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
| Storage             | Plain `.md` files in `$WIKI_DATA` (deck tasks in `_deck/*.json`) |
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
- **`run.sh`** — convenience script driving `podman build` and `podman run` directly (no Compose dependency). It reads `WIKI_DATA` and the optional env vars (`LEAF_PASSWORD`, `SECRET_KEY`, `GIT_REMOTE`, `ALLOW_REMOTE`, `TZ`, `LEAF_EMBEDDINGS`, `LEAF_EMBED_MIN_SCORE`, `LEAF_MCP_TOKEN`, `LEAF_BASE_URL`, `LEAF_NOSHARE`) from `.env`, mounts `$WIKI_DATA` to `/wiki-data`, and passes `WIKI_DATA=/wiki-data` as an env var so the server knows where data lives. `up` uses `--replace`, so re-running it rebuilds and restarts the existing container, and `--init`, so a minimal init is PID 1 and reaps orphaned processes (with Leaf as PID 1 they piled up as zombies; see Version history). The container gets the host's timezone (`podman run --tz local`; a `TZ` in `.env`, e.g. `Europe/Berlin`, overrides it), so "today" on the server (due count, deck dates, scratch-note names, "Last changed") is the user's day, not UTC. Without `/etc/localtime` on the host and no `TZ`, it stays UTC.
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
- `$WIKI_DATA/_deck/` — the deck's tasks (`tasks.json`, `completed.json`, `completed-YYYY.json`; see Deck → Storage)
- `$WIKI_DATA/_index/` — the semantic search cache (rebuildable, not in git)

Uploaded image URLs use `/_assets/<filename>` (served by a Flask route from `UPLOADS_DIR`), not the static file path.

### File paste and drag & drop

In the editor, pasting a file from the clipboard or dropping a file onto the textarea automatically uploads it via `POST /api/upload`. All file types are accepted. Images are inserted as `![name](url)`; other files as `[name](url)`. Multiple files dropped at once are uploaded sequentially. The upload route uses the original filename when available (falling back to MIME-type guessing). If a file with that name already exists in `_assets/`, a numeric suffix is appended (`file-2.pdf`, `file-3.pdf`, …).

---

## Routes

| Method | Path                  | Description                                     |
|--------|-----------------------|-------------------------------------------------|
| GET    | `/`                   | Redirects to `/page/index`                      |
| GET    | `/page/<slug>`        | Render a wiki page (markdown → HTML)            |
| GET    | `/deck`               | The built-in deck page (`deck_page()`): the full main deck (every open task, list tasks included), independent of the wiki's pages. `?list=<name>` shows that separate list instead, with the params of the page that declares it (keep, labels, limit, …) and a line linking to that page; `?view=`, `?task=`, `?tag=`, `?search=`, `?new=1` work as for any embed. Every link to "the deck" goes here (the Deck module links without a deck home, search results, Linked tasks, `g d`). |
| GET    | `/edit/<slug>`        | Show the in-browser editor for a page           |
| POST   | `/edit/<slug>`        | Save edited content to `pages/<slug>.md`        |
| GET    | `/new`                | Editor for a new page (slug entered by user)    |
| POST   | `/new`                | Create `pages/<slug>.md` and redirect to it     |
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
| POST   | `/delete/<slug>`      | Delete a page file; redirects to home. Forbidden for `index`. |
| GET    | `/pages`              | Redirects to `/` (browsing lives in the search modal) |
| GET    | `/history/<slug>`     | A page's saved versions, newest first (only when git is enabled; see Version history) |
| GET    | `/revision/<slug>/<sha>` | A page as one save left it (read-only), with ← Older / Newer → and Restore |
| GET    | `/diff/<slug>/<sha>`  | What one save changed in a page |
| POST   | `/restore/<slug>/<sha>` | Restore a page to a prior version; creates a new commit |
| GET    | `/page/_deleted`      | Auto-generated list of pages deleted from git history, with one-click restore (git only) |

Every page view includes:
- **Breadcrumb** — `Wiki Deck Scratch › Page Title` above the page header: its root is the module switcher (see Modules), the green leaf logo in front of Wiki, the home page. After some navigation it becomes a trail of up to 6 recently visited pages (`leaf.js`, `sessionStorage`); a phone (≤640px) shows at most 4 of them, the last 3 visited plus the current page. It stays on one line: the page titles shrink with an ellipsis, while the module switcher, separators, × and the backlinks chip keep their size. A trail that could not shrink once made the page wider than a phone, which pushed the full-screen deck edit modal's buttons off-screen.
- **Backlinks** — section at the bottom listing all pages that contain a `[[wikilink]]` pointing to the current page. Shows "No pages link here." when empty.
- **Backlinks chip** — when a page has backlinks, a `← N` chip sits at the right end of the breadcrumb (left of the top actions; hidden where the side columns list the backlinks). On desktop, hovering it lists the linking pages (`.bl-tooltip`) and clicking jumps to the Backlinks section. Touch devices (`hover: none`) have no hover, so there a tap toggles the list (`.bl-open`, delegated click handler in `leaf.js`) instead of scrolling down. A second tap or a tap elsewhere closes it. The hover rules are wrapped in `@media (hover: hover)` because a tap leaves `:hover` stuck, which would fight the toggle.
- **Related** — only with semantic search on (see Search → Semantic search): up to 5 pages closest in meaning, leaving out pages already linked either way (`_build_related_html`). Absent when none clears the similarity floor.
- **Linked tasks** — open deck tasks whose title/description link to the page. A task linking to a section (`[[Page#Section]]`, or `/page/<slug>#anchor`; `deck.find_task_backlinks` returns its `section` and `anchor`) names it after its title (`§ Section`, linking to the heading, named as the page's heading says), and that heading gets a `☑ N` chip (`_inject_section_tasks`, a `<details>` after the heading, no JS) listing the section's tasks. Each entry is a single link to that task in the deck (`/deck?task=<id>&view=open`, plus `&list=<name>` for a task in a separate list — `_task_href()`); links inside the task title are flattened to their labels (`_plain_task_title`) so anchors are never nested.

### Side columns (big screens)

From 1320px wide a page view (`view_page`, `shell(…, wide=True)` → `main.wide`) uses the room beside the text: three columns (`.page-grid`: 14rem · up to 60rem · 14rem, so the text sits in the middle of the window, where the other views have theirs), both sides sticky. Every other view (deck, scratch, search) gets the same top row width at that size, its content centred at the text width (46rem), so the breadcrumb and top actions stay put when switching modules.

- **Left** (`.rail-left`): "On this page" (`_rail_toc_html`): the page's `[[toc]]` (copied from the rendered box), or without one its rendered `##`/`###` headings when there are at least `RAIL_TOC_MIN_HEADINGS` (3), links in them flattened. Otherwise empty; the column stays so the page keeps its place. At its end **↑ Back to top** (`.rail-top`), shown once the page has scrolled a screen down; it scrolls up and drops a `#hash` from the URL. `leaf.js` marks the section being read (`.active`, `aria-current="location"`): the last heading scrolled past (120px from the top), at the end of the page the last one in view. A column taller than the window scrolls on its own: a fade marks the edge with more (`.rail-more-above`/`-below`, set by `leaf.js`, CSS `mask-image`), and the TOC scrolls its column (never the page) to keep the marked entry in view.
- **Right** (`.rail-context`): at its top the page's actions (`.rail-actions`: Edit and ⋯, opening the same page menu as the header's), shown once the header's have scrolled out of view (IntersectionObserver in `leaf.js`); then Backlinks, Linked tasks and Related (`_rail_copy`: the same sections with their ids as `data-id`; the anchors stay with the originals in `.page-foot`), and **Links to** (`_outlinks_html`: the existing pages this page links to, A–Z by title; only in the column), so a page nothing links to still shows its connections.
- **Middle** (`.page-col`, a size container): header, meta row and text keep the reading width (46rem, about 85 characters); a code block, table or image alone in its paragraph (`<p class="img-block">`, marked by `_mark_image_paragraphs` in `render_md`) is as wide as the text unless its content needs more, then grows up to the middle column, centred on the text (`width: max-content; min-width: 100%; max-width: 100cqw`); an image within a sentence stays inside the text. A deck embed keeps the text width, like the deck and scratch pages. Text itself is never widened: longer lines read worse.
- The inline copies (`.page-content .toc`, `.page-foot`) and the breadcrumb's backlinks chip are hidden there. `main.wide` has wider side padding (3.5rem), so the badges fixed at the top left don't touch the breadcrumb on a window just over 1320px. Below 1320px the page is the single 800px column as before.

---

## Rendering

- `server.py` reads `pages/<slug>.md`, converts it to HTML using `markdown-it-py`, and injects it into a shared HTML shell (Python f-string in `server.py` — not a separate template file).
- The HTML shell includes: status badges, breadcrumb (with the module switcher), page header (title, Edit, the ⋯ page menu), content area, backlinks, the action bar, and the search/shortcut/scratch modals. There is no nav bar (see Modules).
- `highlight.js` (vendored, see Dependencies) auto-highlights fenced code blocks client-side after load.
- The shared shell renders the product brand as `Leaf` (page titles, the switcher's accessible name), with a small green inline SVG leaf logo (`_LEAF_LOGO`) on the breadcrumb's Wiki link.

### Autolinks and callouts

- **Bare URLs become links** (`gfm_autolink_plugin`): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link. Code spans, fenced code and existing links are untouched. It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **External links open in a new tab**: a link to another site (http/https, not the host the request came in on) gets `target="_blank" rel="noopener noreferrer"` (`_is_external`, a `link_open` render rule, so autolinks too); `deck.js` does the same for links in task titles and notes (`extAttrs`). Leaf's own links, `mailto:` and a full URL to Leaf itself stay in the tab.
- **Callouts** (markdown-it's `alerts` option, GitHub/Obsidian syntax): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line. Styled in `style.css` (GitHub colours, a `--alert` variable per kind, dark variants). Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

### Sortable tables

Clicking a column header of a table in a page's article sorts its rows: ascending, descending, then back to the file's order (`sortTableBy` in `leaf.js`; Enter/Space on a focused header too). View only: the file is never changed and nothing is remembered. A column whose non-empty cells are all numbers (`tableCellNumber`: `-2`, `9.5`, `1,200`, `50 %`, `€3`) sorts numerically, any other column with a numeric-aware, case-insensitive collator (`item 9` before `item 10`, ISO dates in order). Empty cells go last in both directions. The state is the header's `aria-sort`, which the CSS turns into ▲/▼ (↕ on hover). Only `article.page-content` tables with a header, one body, at least two rows and no merged cells (raw-HTML `colspan`/`rowspan`) are made sortable; the editor preview is not. Clicks are delegated, so presentation slides (cloned from the article) sort too.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:
- An `<h1>` with the page title (derived from the first `# Heading` in the file, or the slug as a fallback)
- An Edit button linking to `/edit/<slug>`
- A **⋯ page menu** (`.page-menu-btn`, menu `#page-menu`, `_build_page_menu` in `server.py`; opening, keys and actions in `leaf.js`) with the rarer actions: **Present** (`#present-btn`, only on a page with headings, `p`), **History** (git only), **Rename…** (a dialog, `#page-rename-dialog`, see Rename) and **Delete…** (the `#page-delete-dialog` confirmation, also Ctrl+Delete); Rename… and Delete… not for `index`. One hidden menu element for both ⋯ buttons (header and right column), positioned under the pressed one: a popover with ↑/↓, Esc (focus back to the ⋯), a bottom sheet at ≤640px, styled like the deck's ⋯ menu (`.task-menu` rules list `.page-menu` too). It sits outside `.page-grid`, since the middle column is a size container and would anchor `position: fixed`. Shortcut hints (`.menu-key`) hide on touch screens.

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body. Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before passing to `md.render()`.

### Wikilinks

- Wikilinks use the `[[Page Name]]` syntax. Nested pages can be linked as `[[journal/my-page]]`.
- **Sections**: `[[Page#Section]]` links to a heading on that page (`/page/page#section`, the id as the heading renders, `section_anchor`), labelled "Page › Section"; `[[Page#Section|Label]]` with a label; `[[#Section]]` a heading on the same page. `split_wikilink` parses all forms (server), `wikilinkHref` in `leaf.js` the same for `deck.js`'s task notes and the editor's Enter-on-a-wikilink. The section is ignored for backlinks and kept on rename.
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
- **Ticking** the chip's checkbox (`leaf.js`, page view and editor preview) PUTs `{ completed }` to `/api/deck/<id>`, so repeating and keep tasks behave as in the deck, then re-renders the chip from `/api/preview` of `[[task:<full id>]]` (one renderer: a repeating task shows its next occurrence, a keep task its ✓). Undo toast: `completed: !done` plus the task's old `history` (no trace), or for a keep task its old `start_date`, `due_date`, `last_done` and `history` (fetched from `/api/deck` before the change).
- **Linked tasks** leaves out tasks the page shows inline (`page_task_refs`).
- **Search** indexes a reference as its task's title (`_task_refs_as_titles`, also in `_semantic_text`); a page with references is re-indexed when the deck files change (the page doc cache keeps the deck's mtimes). Embeddings and MCP section docs are cached by page mtime, so they catch up with a renamed task when the page changes.
- **MCP `read_page`** keeps the raw markdown and appends what each reference stands for (title, status, dates, link; `_mcp_task_refs`).
- **Make task** (formatting bar, `makeTasks` in `leaf.js`): turns text in the editor into deck tasks and replaces it with their references, e.g. action items in meeting notes. A selection within one line becomes one task and an inline reference. Otherwise each non-empty line the selection touches (or the cursor's line) becomes a task, keeping its list marker and dropping a checklist's `[ ]` (`- [ ] Call Bob` → `- [[task:…]]`); blank lines, headings and lines that are already a reference stay. Each task's notes say `From [[<page>]]`.
  - **One item opens the deck's edit modal** to review it first: the editor page carries a hidden deck app (`_build_deck_embed(…, headless=True)`: `data-headless`, `display:none`; `deck.js` then ignores the deck's keyboard shortcuts and the "moved to another list" notice), and `window.openDeckEdit(null, { title, description, start_date: null, onCreated })` opens it with the text as title (its quick-add preview shows the shorthand) and no start date. Save (Enter) creates the task and `onCreated` puts the reference in; Cancel/Esc changes nothing. The same modal serves `c t` in the editor (with the deck home's tags).
  - **Several lines** open the same modal in batch mode (`openDeckEdit(null, { titles, … })`, `setBatch` in `deck.js`): the titles are listed instead of the title field (each with its shorthand preview), focus starts in Tags, and the fields (notes, tags, dates, repeat, keep) apply to every task. A line's own shorthand adds to them: its tags join the shared ones, its dates win over the shared ones. Save creates them one by one (`createTask`) and `onCreated` gets them all; if one fails, or the text changed meanwhile, the created ones are deleted again and nothing is replaced. Should the deck app be missing, the editor creates them right away with `DeckCore.parseQuickAdd`.
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
- History is in the page view's ⋯ menu (and the breadcrumb of a revision or diff links back to it).
- **`/history/<slug>`** lists the page's saved versions, newest first, one row each (`.hist-list`): date and time, what the save did in words (`_version_label`: Leaf's own commit messages read alike, so "Update p" → Edited, "Rename a to b" → Renamed from “a” or, on a page whose link was updated, Link updated, "Restore p to <sha>" → Restored the version of <date>, also Created/Deleted/Imported; other messages as written) and the lines added/removed (`--numstat`). The row opens that version; the page as it is now is marked **Current** and opens the page itself (`_is_current`: the newest commit equals the file on disk; with edits not committed yet a "Now · Not committed yet" row is the current one instead). **Changes** opens the diff.
- **`/revision/<slug>/<sha>`** shows that version with a bar (`.version-bar`) saying where it stands ("The version of 2026-10-08 14:32 · 2 versions back", "The current version", or a deleted page's last version), **What this save changed** (the diff), **Restore this version** (a plain button that asks first: `form[data-confirm]` in `leaf.js`; not offered for the current version) and **← Older / Newer →** (`_version_nav`; Newer from the version before the current one is **Current →**, the page). The breadcrumb names the version by its time, not its hash.
- **`/diff/<slug>/<sha>`** ("Changes") shows what that save changed, red taken out and green put in, without git's file headers (`_diff_to_html` leaves out everything before a file's first `@@`; a hunk header becomes `⋯`), with **View this version** and the same ← Older / Newer →. No Restore there: a diff shows a change, not a version.
- **Renames**: `git_log` (`--follow`) records each commit's path for the file (`path`, from `--numstat`, `_numstat_path` reading `old => new` and `dir/{old => new}.md`), and revision, diff and restore read a version under the name it had then (`_version_path`); before, a version from before a rename was a 404. The diff of a rename passes both names (`git_diff(…, *also)`, `-M`), so a pure rename shows no change instead of the whole page as new.

### Table of Contents

- `[[toc]]` on a line by itself (case-insensitive) is replaced with a `<nav class="toc">` block listing all `##` and `###` headings in the page as anchor links.
- The TOC is built before `process_wikilinks` runs. `[[toc]]` is replaced with a placeholder, rendered, then the placeholder `<p>` is swapped for the nav HTML.
- Heading anchors are generated by `anchors_plugin` from `mdit-py-plugins`. The `_heading_id()` helper replicates the plugin's slugify for the href values in the TOC.
- If the page has no `##`/`###` headings, `[[toc]]` renders nothing.

### Deck Embed

`[[deck]]` on a line by itself embeds the full Deck app (see Deck) inline, using the same placeholder pattern as `[[toc]]`. Syntax: `[[deck param=value param2=value2]]`.

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `tag` | comma-separated tags | — | Pre-filters by tag. Comma = AND, pipe `a\|b` = OR, a leading `-` excludes (`tag=work,-mary`). The chips are locked (the page says what it shows); new tasks get the single tags, but a task's tags stay editable. |
| `view` | `focus`, `open`, `plan`, `completed` | Focus (or the tab remembered this session) | Opens on that tab on every load; a URL `?view=` overrides it. Old names are still accepted via `_FILTER_ALIASES` (`upcoming`, `today`, `all`, `backlog`, `due`, `done`), as is `filter` for `view`. |
| `limit` | integer 1–99 | none | WIP limit: Focus warns at this many tasks, and Plan day sections with at least this many are flagged amber. `?limit=N` works on `/deck`; `capacity` is an old alias. |
| `list` | a tag | — | Makes the embed a separate list: it shows the tasks with that tag, the tag is locked there and marked as a list everywhere (see Deck → Separate lists). Cleaned like tags, lowercased (`_clean_list`). |
| `compact` | `1`/`true`/`yes` | — | Every tab of the embed starts in one line per task (otherwise only Open and Completed do; see Deck → Compact); the viewer's own switch, once used, wins. |
| `keep` | `1`/`true`/`yes` | — | New tasks created in the embed are keep tasks (the edit modal's checkbox starts checked), e.g. a shopping list of staples. |
| `home` | `1`/`true`/`yes` | — | Makes this embed your own deck, the **deck's home**: the Deck module links go to its page, the page belongs to the Deck module, New adds a task in this embed, and the due count counts what it shows (see Modules). The first page A–Z with one wins; without any, the Deck links go to `/deck`. |
| `labels` | up to 3 comma-separated names | `Open,Plan,Focus` | The embed's own names for the tabs, in on-screen order, e.g. `labels=Catalogue,Plan,Shopping` for a shopping list. Only the words change: views, `view=` values and links keep their names. An empty entry keeps the default, `_` stands for a space (params split on whitespace). Rendered into the tab buttons by `_build_deck_embed`; `deck.js` reads them back (`tabLabel`) for the "← Back to …" link, the limit warning and the ⟲ tooltip. |

```markdown
[[deck]]
[[deck tag=urgent view=plan]]
[[deck view=open]]
[[deck list=groceries tag=weekly]]
[[deck view=focus limit=5]]
[[deck list=groceries keep=1 labels=Catalogue,Plan,Shopping]]
[[deck list=task view=focus limit=8 home=1]]
```

**Headless instance**: `_build_deck_embed(params, headless=True)` renders the app hidden, for pages that only need its edit modal (the editor's Make task, see Task references). `openEditModal(null, opts)` takes, besides `tags`, a starting `title`, `description`, `start_date` and an `onCreated(task)` callback; `titles` instead of `title` is batch mode (one task per title, the fields shared, `onCreated` gets the array).

**Implementation** (all in `deck.py`): `_DECK_RE` finds the directive and `_parse_tasks_params()` parses its `key=value` pairs. `process_deck_embeds()` swaps each one for a numbered placeholder before rendering, and `inject_deck_embeds()` replaces each `<p>PLACEHOLDER</p>` with `_build_deck_embed()`'s HTML afterwards. The embed passes its params as `data-init-*` attributes and loads `deck-core.js` then `deck.js` (`defer`, in that order: deck.js reads `window.DeckCore`). Element ids get a `-<n>` suffix after the first embed, so a page can hold several. `deck.js` also reads `?tag=`, `?view=`, `?search=`, `?task=`, `?new=1` from the URL; `?new=1` opens the new-task modal in one embed only, the deck's home if the page has it, else the first.

The deck itself is `/deck`; embeds are for custom views on ordinary pages (a tag deck, a list with notes around it). Nothing links to a `deck.md` page.

**Task ↔ page linking** is a `[[wikilink]]` (or `/page/` link) in the task's title or notes, which lists the task under the page's "Linked tasks". The other way round, a page shows one task inline with `[[task:<id>]]` (see Task references). There is no `page` field: `_normalize_task()` turns a legacy stored `page` into `[[slug]]` in the notes, and `page` in embeds or API bodies is ignored.

---

## In-Browser Editor

- `/edit/<slug>` renders a split-pane view: `<textarea>` on the left, live preview on the right.
- Live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- **Very wide screens** (≥1800px): the preview pane may grow to 1100px (the writing pane keeps 800px); its text keeps the reading width (46rem), wide tables, code blocks and images alone in a paragraph use the rest.
- **Toolbar** — one line on every screen: the slug label shrinks with an ellipsis (full slug in its tooltip) and the buttons keep their size, so a long slug never pushes Done off-screen.
- **Formatting bar** (`FORMAT_BAR` in `server.py`, `applyFormat` in `leaf.js`): one line of buttons between the toolbar and the textarea, not WYSIWYG, just markdown help: bold (`**`), italic (`_`), strikethrough, heading (cycles `##` → `###` → none on the cursor's line; the H1 is the title), bulleted/numbered/checklist (on all selected lines, replacing another list marker; all of that kind already → removed; blank lines kept), quote, inline code, code block (fences the selected lines), table (a 2-column template after the current line), link (selected text → `[text]()` with the page autocomplete open; a selected URL → `[](url)`), wikilink (`[[selection]]`, or a bare `[[` that opens the autocomplete), task (`[[task:` after the cursor, opening the task completion), make task (see Task references), attach (file picker → the same upload as paste/drop, `uploadFileAtCursor`). Wraps toggle (markers inside or just outside the selection are removed) and keep surrounding spaces outside. **Ctrl+B / Ctrl+I / Ctrl+K** do bold, italic, link. Every change goes through `execCommand('insertText')` (`replaceRange`), so one Ctrl+Z undoes it and preview/auto-save follow as for typing. Buttons `preventDefault` on pointer/mouse down so the textarea keeps focus and selection (on a phone, the keyboard stays up). On narrow screens the bar scrolls sideways; under `(pointer: coarse)` the buttons are finger-sized.
- **Mobile editor** — on screens ≤640px the editor is not hidden; it becomes a full-screen stacked layout (textarea on top, preview below when toggled). The toolbar gains a **Preview** button (`#mobile-preview-btn`, class `mobile-only`) that calls the same `togglePreview()` as F2. The textarea font is 16px on mobile so iOS Safari doesn't zoom in on focus. Rename… and Delete move into a **⋯ dropdown** (`#editor-more-btn`, `.editor-more`) so the toolbar fits on one line; on wider screens `.editor-more` is `display: contents`, so the buttons stay inline.
- The editor textarea preserves tabs and disables spell-check.
- The textarea receives focus automatically on page load.
- **Auto-save while typing** — debounced ~2s after the user stops typing, the editor silently POSTs to `POST /api/save/<slug>`, which writes the file to disk (no git commit, no redirect). An unsaved-changes indicator (`●` dot in the toolbar) appears while there are unsaved changes and disappears when auto-save completes.
- **"Done" button** — POSTs to `POST /edit/<slug>`, which writes the file, creates a git commit, and redirects to `/page/<slug>`. This is the only editor action that commits. The toolbar has no Cancel button.
- **Auto-save on navigate away** — on the `pagehide` event, if the textarea content differs from the last auto-saved content, a `fetch(…, { keepalive: true })` POSTs to `POST /api/save/<slug>`. This silently preserves edits when the user follows a link or presses the back button (no git commit). Not `sendBeacon`: a page restored from the back/forward cache still gets the answer and its new file hash.
- **Saves run one at a time** (`queueSave` in `leaf.js`: auto-save, Done/Ctrl+S, Enter on a wikilink, "Save my version"): each sends the file hash the previous save returned. Two in flight at once (a slow connection, or Done right after an auto-save started) both sent the older hash, and the second was refused with the "modified on disk" banner. Ctrl+S uses `requestSubmit()` so it goes through the same submit handler; `form.submit()` skipped it.
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

- The editor toolbar includes a "Rename" button that reveals an inline rename form (hidden by default); the page view's ⋯ menu has **Rename…**, which opens a dialog (`#page-rename-dialog`) with the same form plus `from=page`.
- The form has a single text input pre-filled with the current slug, and a "Rename" submit button.
- On submit, POSTs to `POST /rename/<slug>` with field `new_name` (human-readable name accepted, converted to slug server-side).
- Server renames the file (`pages/<old>.md` → `pages/<new>.md`), updates wikilink references in all other pages (see Key Constraints), then redirects to `/page/<new-slug>`.
- Renaming `index` is forbidden — the server returns 400 if the old slug is `index`.
- If the new slug already exists, nothing is overwritten: the server redirects back with `?rename_error=…` (to the editor, which shows the rename bar with the error, or with `from=page` to the page view, whose rename dialog reopens with it). An unchanged name goes back the same way.
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
  - **Built-in pages** — `api_pages_list()` adds Deck (`href: /deck`), Scratch and (git only) Deleted Pages, marked `builtin`. Deck and Scratch are modules, linked from the module switcher, so the empty-query list leaves them out; Deleted Pages, also marked `special`, is pinned above the pages whatever the sort, set apart by a stronger rule (`.page-list-special`). Typing matches all three like any other entry; "Unlinked only" leaves them out.
  - **Unlinked only** checkbox — pages with zero incoming wikilinks, in the title list and in the Content matches: the modal sends `/api/search?…&unlinked=1`, and the server keeps only such pages, no tasks (`do_search(unlinked=True)`, same counts as the list's, `all_backlink_counts`) before it cuts the results to 15. Toggling it re-runs the content search.
  - First match is highlighted (with no title match, the first content match once it arrives); **↑/↓** moves the highlight across both page results and content match cards; **Enter** opens the highlighted item.
  - **Preview pane** (≥1100px; `showModalPreview` in `leaf.js`): the modal widens (up to 1180px, 76vh) into results on the left and the highlighted result on the right. A page comes from `GET /api/page-preview/<slug>` (`{title, html}`: rendered without its H1, heading ids as `data-pid` so they don't clash with the page underneath, deck embeds as *(deck)*; cached per slug while the page is open), scrolled to the result's `#anchor` section, its `hl` words marked (`markTerms`, shared with the opened page's highlights). A task, the deck or a built-in page shows its result card. Updates 80ms after the highlight settles.

---

## Modules

Leaf has three modules, in this order: the **Wiki** (pages, home `/`), **Scratch** (scratch notes, `/page/_scratch`) and the **Deck** (`MODULES` in `server.py`). The Deck link goes to the **deck's home**, the page whose embed says `home=1` (your own deck, e.g. `[[deck list=task view=focus home=1]]`; `server._deck_home()`, from the deck embeds' params cached per page in `_page_meta_cache`, first slug A–Z; installed as `deck.HOME_FN`; `deck_home_slug()` looks it up once per request), else to `/deck` (`_module_href`). `/deck` stays the view of every task that search results, task links and `g d` open. There is still no nav bar: the modules sit where the eye already goes.

- **Current module**: `current_module()` names each view's (`/deck` and the deck's home page → deck; `/page/_scratch`, a scratch note and its history → scratch; `/search` and `/login` → none; everything else → wiki), and `shell()` puts it on `<body data-module>` (not in the editor). The server marks the matching `.module-link`s `aria-current="page"` (`_module_link_extras`), so the highlight is there at first paint.
- **Module switcher** (`home_link()`, `.breadcrumb-home.module-switch`): the root of every breadcrumb, leaf logo + Wiki, Scratch, Deck. On desktop it is set apart from the trail as a control: a bordered pill group with the current module shaded, and the first separator drawn as a thin vertical divider instead of `›` (`.module-switch + .breadcrumb-sep`). On phones and in the editor only the logo shows (the way home). The navigation trail leaves out the modules' own pages (`index`, `_scratch`, `/deck` and the deck's home, whatever the Deck link points at).
- **Top actions** (desktop, `top_actions_html()`, `.top-actions`, first in `<main>`): at the top right of the page, in line with the breadcrumb (absolutely positioned in `main`; the breadcrumb gets `min-height: 2.25rem` and right padding so its own end stays clear), in three groups: **Sharing mode** (`.top-sharing`, the eye, `aria-pressed`; see Sharing mode), set apart by a divider (`.top-sep`) since it switches a mode; the two "new" icons, **New scratch note** (`.top-scratch`, `openScratch`, `c s`) and **Add a task to your deck** (`.top-task`, `openHomeTask`, `c t`; a checkbox with a +, so it doesn't read as "done"); and **Search** (`.top-search`, Ctrl+P), the one used most, as a labelled pill with its shortcut (`.top-search-key`, `⌘P` on a Mac). Icons are 2.25rem, muted, a background on hover; name and shortcut in tooltip and accessible name. The pill has a fixed width, so the row's width (`--top-actions-w`, which the breadcrumb's padding and the status badges' position use) doesn't depend on the font; its border ends on the content's right edge. Hidden at ≤640px. They replaced the round New and Search buttons in the bottom-right corner, so desktop toasts and Sign out have the bottom corners to themselves again.
- **New task from anywhere** (`openHomeTask` in `leaf.js`, the `.top-task` button and `c t`): the task goes to the deck's home, tagged as it tags new tasks (`deck.new_task_tags`: its `list=` tag, then the single tags of `tag=`) and a keep task if it says `keep=1`. On the home page it clicks the home embed's own **+ New** (so the view's dates apply too, e.g. Focus's today); anywhere else it opens a deck app's edit modal with the home's tags, keep and start (`shell()` puts them on `<body>`: `data-deck-tags`, `data-deck-keep`, `data-deck-today` when the home's view is Focus, as its "+ New" starts tasks today there; without a home, today, as `/deck`'s Focus; `openEditModal` takes `tags`, which replace the filter's even when empty, `keep` and `start_date`): the page's own deck app (an embed, `/deck`, the editor's hidden one), or on a page without one a hidden one loaded on demand (`loadHeadlessDeck`: `GET /api/deck/headless`, the headless embed without its scripts, then `deck-core.js` and `deck.js`; once per page), so you stay on the page and a toast says "Added “…” to your deck". Should loading fail, the home page (or `/deck`) opens with `?new=1`. Without a home the task gets no tags. **g t** goes to the deck's home (`deckHomeHref`: where the Deck module link points, else `/deck`).
- **Action bar** (`action_bar_html()`, `nav.action-buttons`): the phone's: the three modules (`.action-module`), **New** (`.action-new`) and **Search** (`.action-search`, Ctrl+P, `openSearchModal`). Hidden on desktop (the top actions). At ≤640px it is a bottom tab bar across the full width: Wiki · Scratch · Deck · New · Search, icon over label, the current module in the accent green, each at least 44px. `shell(..., action_buttons=False)` omits it, and the editor and the login page leave it out. On phones it is hidden via CSS `:has()` whenever an input/textarea/select has focus, because the on-screen keyboard owns the bottom edge then, and `main` gets extra bottom space so the bar never covers the page's end.
- **New depends on the module** (`NEW_ACTIONS` in `leaf.js`, its title and accessible name say which): in the deck a new task (it clicks the deck's own **+ New**, `#task-add-btn`, the home embed's (`data-home`) on the home page, so the view's tags and dates apply), in scratch a scratch note (`openScratch`, `c s`), in the wiki a new page (`openNewPage`, Ctrl+N), elsewhere a scratch note.
- **Counts**: the Deck links (switcher and tab bar) carry two counts (`.module-counts`): tasks due today or overdue (`.module-due`, orange) and tasks in Focus (`.module-focus`, muted; `DeckCore.isFocus`: started, or due today or earlier), each hidden at zero, with a tooltip like "Deck: 5 in Focus · 1 overdue · 1 due today". `deck.deck_counts()` counts the deck home's tasks, its `list=` and `tag=` (`deck.tag_param_matches`, the server's reading of `tag=` as deck.js has it), or every task without a home. The server renders them into the page (`_deck_counts_html`, once per request), since filling them in by a fetch after the page had painted made the link change width on every load (flicker); `leaf.js` refreshes them from `/api/deck/due-count` every minute, on tab focus, on a back/forward-cache restore and after deck writes (`window.refreshDueBadge`). They replaced the separate due badge at the top left.
- **Status badges** (`.top-badges`: uncommitted count, and Sign out when auth is on; the first thing in `<main>`, after the remote 🌐 `.remote-badge`, which on desktop sits left of the module switcher, in the margin outside the content's edge, on the row's centre line, and in the editor at the top right): on desktop they sit in the top row, left of the top actions, centred on the row (the breadcrumb keeps their room free; Sign out, though inside, is fixed to the bottom-right); in the editor fixed at the top right, since its breadcrumb starts at the window's left edge. The row aligns to `main`'s side padding (`--main-pad`): the switcher starts on the content's left edge, the search pill ends on its right edge. At ≤640px they join the page flow above the breadcrumb, so they no longer cover it, Sign out on the right of that row (the bottom edge is the tab bar's), and 🌐 is hidden, since on a phone remote access is a given. The deck's due count is on the Deck module links (see Modules).

---

## Sharing mode

For sharing the screen: the page on screen stays as it is, everything that shows *other* pages around it is hidden, and pages not for sharing are left out entirely. Switched by the eye in the top actions or **S** (Shift+s; `toggleSharing` in `leaf.js`). The state is a cookie (`leaf-sharing=1`, a year), so the server knows it: `shell()` renders `<html class="sharing">` from the first paint (nothing flashes on load), and switching reloads the page, rendered for the new mode. While on, a thin amber bar runs across the top and the eye is highlighted. Phones have no switch (no top actions).

- **Around the page** (`html.sharing` in `style.css`): the breadcrumb's trail of visited pages (`leaf.js` marks it `.trail-link`/`.trail-sep`; the separator left before the page becomes the divider), the backlinks chip, Backlinks / Linked tasks / Related / Links to (side column and `.page-foot`), a section's linked-task chips, the status counts (deck counts on the Deck links, uncommitted badge). The search modal lists nothing until something is typed (no recent pages; nothing for ↑/↓ or the preview to reach), and the editor's `[[` page and task completion waits for two letters.
- **Pages not for sharing** (`LEAF_NOSHARE` in `.env`: comma-separated, `#tag` entries name frontmatter tags, `folder/` entries folders and everything below them, e.g. `LEAF_NOSHARE=#noshare,#people,people/,personal/`; a bare word counts as a tag; unset or empty: `#noshare`; don't quote the value). `is_noshare(slug)` (folders by slug, tags from the frontmatter, cached by mtime), `hidden_page(slug)` = sharing mode on and noshare. Such a page's view, editor, history and revisions show "Hidden page" (`hidden_page_view()`: neither title nor text, no trail stop); `/api/page-preview` 404s; search (`do_search`: `/api/search`, `/search`, the Scratch filter), `/api/pages`, `/api/pages-list`, the Scratch list, Deleted Pages, the uncommitted list, Backlinks, Links to and Related leave them out; a wikilink to one renders as its words without the link (`<span class="wikilink-hidden">`). The MCP tools are not a browser and ignore sharing mode.

## Scratch

- **Keybinding:** **c** then **s** creates a scratch note (see Keyboard Shortcuts); the action bar's New does too, in the Scratch module (see Modules).
- User types a title and submits → `POST /api/scratch` → server creates `<YYYY-MM-DD_HH-MM-SS>_<slug>.md` (local time) with `# Title\n\n` and returns `{ slug }` → browser navigates to `/edit/<slug>`.
- A `<dialog id="shortcuts-modal">` lists all keybindings, opened with `?`.
- **Scratch note filenames** match `SCRATCH_RE = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(.+)$"` e.g. `2026-03-24_18-50-08_my-note.md`. `is_scratch(slug)` identifies them.
- **`/page/_scratch`** — special auto-generated page (no `.md` file on disk) listing all scratch notes newest-first with title and timestamp. Not editable.
  - **Filter** (`#scratch-filter`, `initScratchFilter` in `leaf.js`): a box above the list that runs the search over scratch notes only (`/api/search?scratch=1`, `do_search(scratch=True)`), so ranking, stemming, typos, semantic hits and the query syntax (`"phrase"`, `-word`, `#tag`, `edited:` …) are the search's. Matches show in ranked order with their excerpt (marked), linking to the matching section with `?hl=` highlights, plus a count ("2 of 30"); everything else is hidden. `scratch=1` lifts the limit to `SCRATCH_FILTER_LIMIT` (1000), so every match is listed. The query is kept in `?q=` (`history.replaceState`), so Back returns to the filtered list and `/page/_scratch?q=…` links work. Enter opens the first match; Esc clears the box (a second Esc leaves it); an empty box restores the newest-first list. No filter when there are no notes.
- Daily notes (`/api/daily`, `/page/_daily`, `c d`/`g y`) were removed on purpose. Existing `YYYY-MM-DD_daily.md` files are ordinary pages.
- **`/page/_deleted`** — special auto-generated page listing all pages that exist in git history but not on disk (deleted pages), newest deletion first. Each entry shows title, slug, deletion date, and a one-click Restore button that calls `POST /restore/<slug>/<sha>`. Only available when `GIT_ENABLED`. Navigating to a deleted page's URL directly also shows a restore prompt instead of redirecting to the editor.
- Appears in the search modal as a synthetic entry injected by `api_pages_list()` with title "Scratch" (found by typing; the empty-query list leaves it out, see Page List) — matches searches for "scratch" or "_scratch". `_deleted` is similarly injected with title "Deleted Pages" (git only). A "Deck" entry (slug shown as `/deck`) links to the built-in deck page; entries may carry an `href` for targets that are not `/page/<slug>`.
- Special pages (starting with `_`) are not filtered out of the search modal or backlink counts but are treated as regular pages otherwise.

---

## Keyboard Shortcuts

Defined in the `keydown` listener in `leaf.js`. Bare-key shortcuts are suppressed when focus is inside an `input`, `textarea`, `select`, or `[contenteditable]`.

| Shortcut | Context | Action |
|----------|---------|--------|
| **Ctrl/⌘+P** | anywhere | Open search modal |
| **Ctrl/⌘+N** | anywhere | Open new-page modal |
| **g** then **h/s/d/t** | anywhere | Navigate: Home / Scratch / Deck (every task) / your deck (the deck's home) |
| **c** then **s/t** | anywhere | Create: scratch note / task in your deck (tagged as it tags new tasks) |
| **F2** | editor | Toggle preview pane |
| **Ctrl/⌘+S** | editor | Done — commit and go to page view |
| **Ctrl/⌘+B / I / K** | editor | Bold / italic / link (see Formatting bar) |
| **Ctrl/⌘+Shift+X** | editor | Toggle checkbox on current line |
| **Ctrl/⌘+Delete** | page view | Delete current page (confirmation dialog) |
| **S** | anywhere | Sharing mode on/off (see Sharing mode) |
| **e** | page view | Navigate to edit mode |
| **]** / **[** | page view | Next / previous section (`##`/`###`, not a deck embed's; `jumpSection` in `leaf.js`): scrolls it to the top and puts its anchor in the URL; the current one is the last heading scrolled past, as the side TOC marks it; `[` before the first goes to the top |
| **p** | page view | Enter presentation mode |
| **Ctrl/⌘+/** | anywhere | Show keyboard shortcuts and snippets (Vimium-safe) |
| **?** | anywhere | Show keyboard shortcuts and snippets (fallback) |
| **n** | deck page | Open new task modal |
| **n** / **N** | page opened from search | Next / previous search highlight (wins over the deck's **n** while highlights show) |
| **Esc** | page opened from search | Clear the search highlights |

---

## Presentation Mode

- **Present** is in the page's ⋯ menu (see Page header) whenever the page has a heading.
- Choosing Present (or pressing **`p`**) opens a fullscreen dark overlay, splitting the article into slides at each `<h2>` boundary.
- Content before the first `<h2>` (if any) becomes an intro slide.
- Navigation: **← →** arrow keys, or click "← Prev" / "Next →" buttons. **Esc** exits.
- The slide counter (`1 / N`) is shown in the centre of the control bar.
- Slide content re-uses `.page-content` CSS so typography, code blocks, and links render correctly inside the overlay. CSS custom properties are overridden to force dark colours.
- Syntax highlighting is re-applied via `hljs.highlightElement` when each slide is shown.
- The Present menu item is hidden (via JS, `syncPresentBtn`) on pages with no headings.

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
- **Performance**: page documents are tokenized once and cached in `server._search_doc_cache` keyed by slug, invalidated per-file on `mtime` change (so on-disk edits stay reflected). The BM25 index itself is cached too (`server._search_index()`), keyed by the identities of the cached docs, so it is rebuilt only when a page or the deck changed; it keeps an inverted index (`_postings`: term → docs), so a search scores only the docs containing its terms, ties in document order. Prefix expansion bisects the sorted vocabulary. Matching words in a text for excerpts, highlights and section links (`_match_spans`) caches each text's split into words and stems (`_analyze`, with the distinct stems sorted for prefix bisection and grouped by length for fuzzy candidates) and each word's fold and stem (`_fold_stem`); that was most of a search's time. `iter_pages` walks with `os.walk`, pruning `_*` and hidden folders instead of walking `.git`'s objects. A search takes a few tens of ms; a test wiki of 330 pages and 3,600 sections went from ~150 to ~25 ms. Deck task docs come from `deck.task_search_docs()`: title, notes, comments and tags (as `#tag` words in the text, and as a `tags` list for the `#tag` filter).
- **Results open the matching section** (`_best_sections`, `do_search`): a page result's `href` gets the anchor of the section the query matched, and its excerpt comes from that section, so the excerpt shows what the link jumps to. The section is one whose own text has the query as a phrase, else the best BM25-scored one whose own text (heading and body, not the page title) has a query word (`search.matches`), else, for a page found by meaning, its most similar section. A page matched only by its title gets no anchor (every section would match equally), nor does a match before the first heading or under a `####`+ heading, which has no id (`_section_anchor`; anchors_plugin `max_level=3`). Sections come from the same split as retrieval (`_page_chunk_docs`).
- **Highlights on the opened page** (`search.matched_words`, `initSearchHighlights` in `leaf.js`): a page result's `href` carries the words that matched as `?hl=…` (before the `#anchor`), as written in that page, so stems, typos and phrases are already resolved ("running" → `hl=runs`, a phrase as one `hl`); at most 10, tasks get none. The page marks every whole-word occurrence (`mark.search-hl`; phrase words may be separated by any whitespace), skipping `<pre>` code (highlight.js rewrites it) and deck embeds (`.tasks-app`). A bar (`.search-hl-bar`, bottom centre, top on phones where the bottom edge holds the tab bar) shows the terms and "2 of 5" with ↑ ↓ ×; **n**/**N** step (in the capture phase, so a deck embed's `n` doesn't also open a new task), **Esc** or × unmarks and drops `hl` from the URL (`history.replaceState`). It starts at the first match inside the section the `#anchor` opened. Listed in the `?` help.
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

- **Query syntax** (`search.Query`, the same in `/api/search`, `/search`, `search_wiki` and `search_tasks`' query; listed in the `?` help modal): `"exact phrase"` (case, accents and spacing ignored), `-word` (its stem must not occur; `-dockers` excludes "docker"), `-"phrase"`, `#tag` (pages by frontmatter `tags:`, tasks by their tags, case ignored) and `-#tag`, and **date filters**: `edited:2026-09` (a year, month or day, as a prefix), `after:2026-07-01` (on or after), `before:2026-08` (strictly before; partial dates compare as prefixes, so this is July and earlier), negated as `-edited:` (not then), `-after:` (= `before:`), `-before:` (= `after:`). A page's date is its last edit, a task's its last activity (`deck._last_activity`: the latest of completion, history events, comments and creation; `_doc_date`); undated docs fail a date filter. A query of only date filters lists everything passing, newest first (`_ranked_docs`), e.g. `edited:2026-09`. An unclosed quote, a lone `-` or `#`, or an invalid date (`edited:soon`) stay text. `Query.text` (words and phrases, no operators) ranks, both by keyword and by meaning, and is what excerpts mark; the rest are filters (`Query.accepts`, applied by `server._accepts` before ranking, so meaning hits obey them too). A query of only tags ranks by the tag words (`ranking_text`); one of only exclusions finds nothing. Docs carry lowercased `tags` for this (`_page_tags`, `task_search_docs`). In the search modal the instant title filter fuzzy-matches only the query's words (`queryWords` in `leaf.js`, mirroring `Query`: quotes dropped, `-…`, `#tag` and date-filter parts left out, so a query of only `#tag` shows no title hits), while Content matches get the raw query.
- **Stopwords** (`search.STOPWORDS`, `query_words`): English and German function words ("the", "is", "in", "und", "für") are dropped from the **query**, never from the index, unless that leaves nothing ("what is this" still searches). In a small wiki they aren't in every page, so BM25 gave them weight: a question phrased as a sentence ranked pages by its filler words. Words that are also nouns or names (may, will, it, man) are not in the list. Excerpts don't mark them either; a phrase match still marks the whole phrase.

---

## MCP server

`POST /mcp` lets an LLM client (any MCP client) read the wiki and the deck through tools. Protocol in `src/mcp_server.py` (JSON-RPC, no Flask), route and tools in `server.py` (`mcp_endpoint`, `_mcp_call_tool`). MCP's Streamable HTTP transport, reduced to what tools need: each POST carries one message (or a batch) and is answered with plain JSON; notifications get 202; no event stream (GET → 405), no sessions. `initialize` echoes the client's protocol version when it is in `PROTOCOL_VERSIONS`, else the newest, and sends `instructions` telling the model what Leaf is.

Register `http://localhost:5000/mcp` with your MCP client (HTTP transport) and the header `Authorization: Bearer <LEAF_MCP_TOKEN>`.

Tools, all read-only (`readOnlyHint`). A tool failure the model can fix (bad slug, empty query) is a result with `isError`, not a protocol error:

| Tool | Does |
|------|------|
| `search_wiki(query, limit=8, under)` | The most relevant **sections**, full text, best first, each with title › heading, slug, the page's last edit date (`edited:`, so the model can tell current notes from stale ones) and a URL with the heading anchor. Several may come from one page. Matching deck tasks are results too, ranked with the sections. `under` restricts it to a folder: pages whose slug is that prefix or below it (`_in_folder`, case ignored; `tickets`, `tickets/` and `/page/tickets` alike, `_clean_folder`), and no tasks. A folder without pages is a tool error, so the model corrects it. |
| `search_tasks(tag, status="open", query, limit=50)` | Deck tasks filtered by tag (comma = all must match, case-insensitive; a list is its tag) and status (`open`, `done` = completed or won't do, `all`), separate lists included. With a query, ranked by BM25 within the filter; without, open tasks by deadline, then start date, then title, done ones newest first. Starts with "Today is …" so the model can tell overdue from upcoming. |
| `read_page(slug)` | A page's raw markdown, frontmatter included, plus a list of the deck tasks its `[[task:…]]` references stand for. Accepts a slug, `/page/<slug>`, a full URL, `<slug>.md` or the page name (`_mcp_page_slug`); `safe_path` keeps it inside the wiki. |
| `list_pages(limit=100, under)` | `slug \| title \| edited date`, most recently edited first; `under` lists one folder. |

**Retrieval** (`server.retrieve`) works on sections, not pages: an LLM needs the passages, and the best ones may share a page. BM25 runs over sections (`_page_chunk_docs`, the same `embed.chunk_sections` split, cached per page by mtime; `_chunk_index`), fused with `SEMANTIC.search_chunks` when semantic search is on (sections match by `embed.chunk_key`). Without semantic search it is keyword-only and still works. Deck tasks share that BM25 index (`deck.task_search_docs()`, each doc carrying its `task`), so they rank with sections, as in the browser search, and their semantic hits (`SEMANTIC.search_tasks`) join the section hits by similarity. `search_tasks` with a query fuses BM25 and semantic hits within its filter. A task is shown by `_mcp_task_text`: title, a line of facts (status, tags, start, due, repeat, keep, list), the `/deck?task=…` link (`_task_href`), its notes and its comments.

**Links** in tool results start with `LEAF_BASE_URL` (e.g. `http://localhost:5000`, Leaf's address as your browser knows it) when set, else with the address the request came in on. A client in a container reaches Leaf as `host.containers.internal`, and links with that host don't open in the browser on the host.

**Auth**: `/mcp` is exempt from the session login (`check_auth`), since an MCP client can't log in, and checks its own: with `LEAF_MCP_TOKEN` set, every request needs `Authorization: Bearer <token>`; without one, `/mcp` is as open as the pages are, so a wiki with `LEAF_PASSWORD` keeps it closed (401) until a token is set. The Host and cross-site Origin checks apply as everywhere.

---

## Deck (Task App)

A task app at `/deck` (a built-in page, `deck_page()`), also embeddable via `[[deck]]` (see Deck Embed). Server side is `src/deck.py` (a Flask Blueprint: `/api/deck/*` routes, embed HTML, task search docs, task backlinks). Client side is `deck.js` (DOM, one instance per embed) plus `deck-core.js` (pure logic, tested under Node).

Several embeds per page work: each instance keeps its own task array, and a write in one refreshes the others (`refreshOtherInstances`). Every instance also reloads on `visibilitychange` and bfcache `pageshow`, so a tab left open for days shows current tasks and today's day sections. `refreshIfIdle` skips the reload while a drag, swipe, completion grace period, inline edit, ⋯ menu or edit modal is in progress. There is no polling.

### Storage

- The deck's files live in the `_deck/` folder (`deck.DECK_FILE` and paths derived from it): open tasks in `tasks.json`, completed ones in `completed.json` (flat JSON arrays, committed to git on change). Completed tasks from earlier years live in `completed-YYYY.json` (only that exact name is an archive). Until 2026-10 the files sat in the wiki's root as `_deck/tasks.json`, `_deck_completed.json` and `_deck_completed-YYYY.json`; on startup `_move_into_deck_dir()` moves them into `_deck/` in one commit (git sees renames, `--follow` keeps the history), leaving a file in place if its new name is taken. `_atomic_write` creates the folder, so a new wiki gets it on the first write. Write routes read every completed file (`_read_completed_all_strict`) and `_write_completed` files each task by its completion year, writing only files whose content changed; startup does the same split (`_archive_old_completed`) for a year change without writes. So archived tasks can be edited, reopened, deleted, tagged and restored like any other; Completed shows them by year (see Views).
- All mutations go through a module lock (`_LOCK`) and atomic writes (temp file + `os.replace`); the dev server is threaded. Write paths use a strict reader that raises `_DeckCorrupt` (503) on an unreadable file instead of treating it as empty and wiping every task.
- Request bodies are validated (`_json_object`, `_as_str`, `_as_int`, `_as_str_list`, `_id_list`): bad input is a 400 with JSON `{ error }`, never a 500. Numeric strings are accepted for integers; `tags` may be a comma-separated string.
- `_normalize_task()` upgrades legacy fields on every read and restore (`focus_date` → `start_date`, `defer_days`, `recurrence.anchor`, `page`); `_migrate_deck_files()` rewrites the files once at startup in its own commit.

### Dates

A task has two independent dates; changing one never moves the other.

- **`due_date`**: the deadline. Only a past due date makes a task overdue.
- **`start_date`**: the day the task becomes available, and the commitment to work on it. Having one makes a task *planned*. A past start date is not overdue, it means "available since" (cards show "since Sep 21").

**A start after the deadline** is usually a slip, but not always (a late task planned for next week), so it is never forbidden, only questioned. Every quick way to set a start date (the Plan board, ⋯ Start today/tomorrow and Pick start date, the card's 📅 picker, **f**/**F**; `setStart` and the picker go through `startsAfterDeadline` → `planOnDay`) asks "Move the deadline too?" (`confirmDeadlineMove`): Move deadline sets both dates, with undo; Cancel writes nothing. Outside the board, an overdue task started today doesn't ask: that is catching up on a missed deadline (swiping right never asks for that reason). The board does ask then, since the card would otherwise stay in Overdue. The edit modal shows a hint under the dates instead (`syncDateWarning`): "Starts after its deadline", with a button that moves the other date (the deadline, or the start when Due was moved before it). It doesn't block saving.

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
| `sort_order` | number / `null` | Position in the Sorted section (Open's and Focus's); `null` = Unsorted. Focus has no order of its own: a `focus_order` (the old Lineup) is dropped on read. |
| `recurrence` | `{ type, interval, from, day?, lead? }` / `null` | `type`: `daily`/`weekly`/`monthly`/`yearly`. Driven by the primary date (`due_date`, else `start_date`). `from`: `"due"` (default, calendar-aligned) or `"completion"`. `day` (monthly/yearly) is the series' day of month, so Jan 31 → Feb 28 → Mar 31; kept on re-save unless type or primary date changes. `lead` is the start→due gap, stored only while a spawned start was clamped to today; editing either date drops it, an unchanged modal re-save keeps it. A keep task never has one (`keep` clears it). |
| `spawned_from` | string / `null` | Recurring instance: id of the completed task that spawned it, so undoing the completion removes exactly this instance |
| `wont_do` | bool | Abandoned: archived like a completed task, shown with "✗ won't do", never spawns a recurring instance |
| `keep` | bool | Keep task: completing it doesn't archive it (see Keep tasks) |
| `last_done` | `"YYYY-MM-DD"` / `null` | A keep task's last completion, shown on its card as "✓ Sep 21" |
| `comments` | `[{ id, at, text }]` | The user's status lines, oldest first, uncapped (see Comments). |
| `history` | `[{ at, event, from?, to? }]` | What happened to the task, oldest first, capped at 30 (`HISTORY_LIMIT`): `created`, `start`/`due` (with `from`/`to` dates), `done`, `reopened`, `wontdo`, `skipped`, `repeat` (`to` = the next occurrence's date). `at` is `YYYY-MM-DDTHH:MM`. See History. |

### Views

A segmented control (`.view-tabs`, one `<button data-filter>` each) in the order **Open · Plan · Focus** (`data-filter` `open`/`due`/`focus`). **Tab**/**Shift+Tab** cycle them. Initial view: URL `?view=` > embed `view=` > tab remembered in `sessionStorage` (plain embeds only) > Focus. On phones the control spans the full width.

- **Open**: every open task, in two sections: **Unsorted** (no `sort_order`, newest first; new tasks land here) above **Sorted** (manual order). Open has no filter for unplanned tasks (an "Unplanned" toggle was removed): those are the Plan board's Unplanned section, where dragging to a day plans them. So Unsorted means "not yet ranked" and Unplanned "not yet scheduled", each with one home. A leftover `unplanned=` embed param is ignored.
- **Plan** (`due`): a day board grouped by `DeckCore.bucketKey`: Overdue (`due_date < today`, display-only), Today, Tomorrow, the next weekdays, Later, Unplanned (no start date; nearest deadline first). Later and Unplanned always show. Dropping a card on a day sets its start date, on Unplanned clears it, on Later opens a date picker (min today+7). An empty section is only its dashed header row (min 1.9rem, 2.5rem on coarse pointers), which takes the drop; nothing grows when a drag starts. Day headers show a count and turn amber at the limit. The board only decides the day: inside a section tasks are in Open's order (`DeckCore.byOpenOrder`, shared with Open), except Overdue (by deadline) and Unplanned (nearest deadline first, then Open's order). A drop within a task's own section, on a task or on its heading, does nothing; to reorder, use Open or Focus. The one exception is Later's heading, which opens the date picker to choose another later date. A drop on a day after the task's deadline (`planOnDay`) would leave an overdue or due-today card where the deadline puts it, so it asks "Move the deadline too?" (`#task-deadline-modal`): Move deadline sets start and due to that day, with an undo toast restoring both; Cancel writes nothing. Wiring: `setupBoardDragAndDrop` per card plus list-level handlers attached once.
- **Focus**: what you're working on now (`DeckCore.isFocus`: `start_date <= today`, or due today/overdue; the board's Overdue + Today). It is Open filtered: the same sections (Unsorted · Sorted) and the same order (`byOpenOrder`), so there is one manual order, and ordering on Focus orders Open. That fits a shopping list: Open is the shop's order, Focus today's list, and an item added on Focus (Unsorted) is dragged into place among today's items, which places it in the shop's order (see Sections). Due and overdue tasks have no section of their own: they keep their place in the order, marked by their deadline colour. The Deck links (with the due count) open here unless another tab was used this session. With a limit, a warning shows when Focus reaches it. Empty Focus says "Nothing planned for today" and links to Plan. New tasks created here get today as start date. (Focus once had its own order, the Lineup (`focus_order`) above Others; it was removed as a second commitment on top of the start date.)
- **Completed** is not a tab but a link under the list (`#task-completed-link`, `✓ Completed (N) →`, count from `/api/deck/completed-count`, refreshed after every write). In Completed, the link reads `← Back to <tab>`, naming the tab it returns to. While filtering another view it counts the matching completed tasks instead (`✓ 2 matches in Completed →`, no number while loading). `/api/deck` returns open tasks only, so completed tasks are fetched on demand (`fetchCompleted`, one request at a time); `deckGen` refetches if `fetchDeck` replaced the list meanwhile. Completed's heading has a **year switcher** (`.completed-years`, years from `/api/deck/completed-years`, shown when there is more than one) for the yearly archives; `?year=YYYY` opens that year (search links to archived tasks carry it), and going back to a tab returns to this year (`setCompletedYear`).

**Sections** (Open and Focus; `SECTIONS`, `sectionHTML`, `.list-section[data-section]`): Unsorted (no `sort_order`, newest first) above Sorted (`sort_order`). Moves treat both headings as slots in the visible list (`sectionList`, `applySectionList(list, moved)`): which side of the Sorted heading a task lands on decides its section; a task dropped on a task takes its slot (a move down lands after it, a move up before it; a task coming from Unsorted moves down: `sectionDrop`); dropping on the Sorted heading moves the task to its top; reordering within Unsorted does nothing; leaving Sorted sets `sort_order` to `null`. Ctrl+Shift+↑/↓ moves one slot, headings counting. Only the moved tasks change place in the full order: each goes right after the visible task now above it (or right before the one below, when first), and every task the view or filter hides keeps its place. So on Focus, sorting in one item never shuffles the catalogue around it. Every save renumbers the whole order densely.

**No hiding**: every open task is always reachable from its own deck (Open or Plan) and from search. Dates decide where a task appears, never whether.

### Interaction

- **Filter field** (`#task-filter-search`, `DeckCore.parseFilter`/`matchesFilter`): `#words` filter by tag (all must match; `#a|b` = any), `-#word` excludes a tag (`-#a|b`: neither), the rest searches title and notes. `/` focuses it, `#` focuses it and starts a tag. Typing a `#word` (or `-#word`) suggests tags of the tasks the other tag words leave visible (free text is ignored for this). Embed `tag=`/`?tag=` become a query via `DeckCore.tagParamToQuery`. New tasks get the filter's single tags.
- **Tag chips** (`chipField` in `deck.js`): in the filter field and the edit modal's Tags field a completed tag becomes a chip in front of the typed text (`.chip-input` box wrapping the input, `.tag-chip`). A chip can't be edited, only removed: Backspace at the start of the field removes the last one, its × any. Filter chips are whole filter words (`#a`, `-#b`, `#a|b`); the query is the chips plus the text (`filterQuery()`), and `?tag=`/`[[deck tag=…]]` arrive as chips. In the Tags field a completion or a comma (typed or pasted) makes a chip, with no `, ` left behind; Save takes the chips plus a half-typed tag (`editTags()`). The dropdown hangs under the whole box.
- **Tag completion** (`attachTagAc`: filter, Tags field, title `#tag` shorthand, bulk tag dialog `t`/`T`): ranked by `DeckCore.rankTags` (exact, prefix, substring, in-order letters; then usage count, then A–Z). The best match is pre-highlighted so Tab/Enter completes it; Esc closes only the dropdown. Keys are handled in the capture phase. The dropdown is appended to the field's parent, since in a `<dialog>` only the top layer is visible.
- **Quick-add** (new tasks only, `DeckCore.parseQuickAdd`): trailing shorthand like `Call Bob tomorrow #work due:fri`. Start: `today`, `tomorrow`/`tmrw`, a weekday (next one, never today), `+3`/`+3d`/`+2w`, `YYYY-MM-DD`; due: `due:` + the same; tags: `#tag` starting with a letter. Only trailing words count, and shorthand that would empty the title is ignored. `#task-edit-quick` previews the result; shorthand wins over the date fields.
- **Completing**: the checkbox completes after a cancellable grace period. Completing a recurring task spawns the next instance, stepping from the previous primary date (or today with `from: completion`) until ≥ today, with its start date not before today (see Dates); month steps clamp to the month's last day. Un-completing removes the spawned instance.
- **Checkboxes in notes**: a `- [ ] item` line (`*`/`+` too, `[x]` ticked) renders as a real checkbox in the card's notes (`renderDesc`). Ticking it flips the line in the description and saves (`DeckCore.toggleDescCheck`, which numbers items with the same `DESC_CHECK_RE` the renderer uses). The task's own checkbox is `.task-checkbox input`, so the two never mix. In the edit modal's notes, Enter continues a list exactly as in the page editor (`continueList`, shared from `leaf.js` like `pasteUrlAsLink`): after `- [x] eggs` it starts `- [ ] `, and Enter on an empty item ends the list.
- **Won't do** (⋯ menu; cards have no ✗ button any more) archives without spawning; **Skip** (⏭, recurring only) archives as won't-do and spawns the next instance. Both show an undo toast; reopening a skipped entry removes its spawn.
- **Delete** (`deleteWithUndo()`, from keyboard, modal or menu) confirms, then offers undo via `/api/deck/restore` with full snapshots.
- **Planned cue**: a planned task's card gets a coloured left edge, the same on every tab for consistency: amber when it is in Focus already (start today or earlier), blue when planned later (`task-planned-now`/`-later`). On Open it tells decided tasks from undecided ones without changing the order. A due/overdue urgency colour wins. The 📅 date is coloured too: blue for a later start, amber for today, plain for a past start ("since …", never a warning).
- **Inline dates**: the ⏰ (due) and 📅 (start) chips open a date picker. **f** starts the selected task today, **F** tomorrow (again clears).
- **⋯ menu** (on touch the `.task-menu-btn`; with a mouse, which has no ⋯ button, **right-click** on a card, `openTaskMenu(id, anchor, {x, y})` at the pointer, selecting the card, a link in the card keeping the browser's menu; from the keyboard the menu key or **Shift+F10** on the selected card): all card actions (Edit…, Start today/tomorrow, Pick start date…, Unplan, Skip, Won't do, Delete…; for completed tasks Edit…, Reopen, Delete…). A popover with ↑/↓/Enter/Esc on desktop (deck shortcuts suspended while open), a bottom sheet at ≤640px.
- **Edit modal**: double-click or Enter on desktop (in compact view Enter expands the task first, see Compact), tap on touch (pointer type of the last `pointerdown`), or Edit… in the menu. Centred with `inset: 0; margin: auto` and **no transform** (a transform would clip `position: fixed` popups like the date picker), scrollable, sticky action bar. At ≤640px full screen with Due | Start and Repeat | Every | From done on shared rows (`.task-row-dates`, `.task-row-repeat`). The List field sits in a closed `<details>`; saving a task into another list removes it from the view with a notice.
- **Date picker** (`openDatePicker`): a row of shortcuts above the month, counted from today, not from the current value: **Today**, **Tmrw**, **+2d**, **Mon** (next Monday), **+1w** (`QUICK`, `.dp-quick-btn`; short labels so all five fit one row, the accessible name and tooltip say it in full, e.g. "Next Monday: Mon, Oct 5"; below the month only Clear; the current value marked, ones before the picker's `min` disabled). They commit like a picked day, so the card's 📅 asks about the deadline as usual. Positioned after every `draw()` (below the anchor, else above, else inside the window; never past a screen edge). A press outside only closes it: `pointerdown` in the capture phase, then `swallowNextClick` eats that press's mouse/click events, so a tap can't also hit what's underneath.
- **`?task=<id>`** selects, scrolls to and flashes (`.task-flash`) that task; a missing task gets a notice. A newly created task is flashed the same way when the view shows it.
- **Rejected writes** show the server's error as a toast (`showNotice`).
- **Toasts and modals** (`placeToast`): a toast shown while a modal dialog is open goes into the dialog, since in `<body>` it would sit under the dialog's top layer and its Undo couldn't be clicked (deleting a comment showed that); when the dialog closes the toast moves back to `<body>`.
- **Cards** (`taskHTML`, style.css "Cards"): one layout for every card. The first line: checkbox, title (as wide as it needs, at least 6rem, 4.5rem on phones; it wraps), the tags right after it (pills), markers and dates at the right end (`.task-meta` sits in the line), the actions. Under it the notes, then the latest 3 comments, newest first (`.task-card-comments`; "N earlier…" opens the modal at the comments). Short of room, tags give way first and only whole (they wrap onto a second line that the one-line height hides; an empty `::before` lets even the first one go); dates and markers are never clipped: the meta column's minimum is their width (`white-space: nowrap`, so "📅 Tomorrow" counts as one piece), and the tags' own width doesn't count (`contain: inline-size`), they only fill what is left. The first line has one fixed height (`--line`, the compact row's), so a full card's first line sits exactly where a compact line's does. Empty date placeholders (⏰ 📅 to set one) show on hover in full cards, always on touch, never in compact lines. A double-click anywhere on a card but its title (which edits the title in place) and its controls opens the modal. **Phones** (≤640px): the line is too short for title and dates, so the title has the first line to itself (beside the checkbox; ⋯ spans both lines), tags, markers and dates go to a second line, and a card with none of them stays one line; compact lines the same, rows ≥44px as a whole.
- **Compact** (`applyCompact`, `.deck-compact`): every card but the expanded one collapses to its first line (title cut off with …, notes and comments hidden). **Per tab**: Open and Completed (long lists to scan) default to compact, Focus and Plan (a task's details matter) to full cards (`COMPACT_DEFAULT`); `[[deck compact=1]]` makes every tab start compact. Rows stay ≥44px on touch. Switched by the density button in the toolbar (`#task-density-btn`, `aria-pressed`) or **v**, remembered per deck and tab in `localStorage` (`leaf-deck-compact:<path>|<list>|<tag>|<instance>|<view>`, `compactFor`), so it survives reloads. **Expanding a task** (`expandedId`, `setExpanded`, `.task-expanded`): one task at a time looks exactly like a full card, so its line doesn't move. **Enter** on the selected task expands it, Enter again opens the modal; on touch the first tap expands, a tap on the expanded task opens the modal. Moving the selection (arrows, j/k, a click) moves the expansion along: the newly selected task expands, the previous one closes, so you can read through tasks. **Esc** collapses it (the next Esc clears the selection); several selected or none close it too. It survives re-renders (`taskHTML` adds the class); switching density resets it. Double-click still opens the modal directly.
- **Header**: tabs, filter, the density button and **+ New** on one line on desktop (`.tasks-toolbar` `flex: 1 1 20rem`), two lines on phones with a label-less "+" (accessible name "New task").

### History

The server logs events on the task (`_log` in `deck.py`): `created` (create and recurring spawn; a new task's first dates are not changes), `start`/`due` whenever the date actually changes (`_apply_task_fields`), `done` (also for a keep task, whose cleared start date is part of it), `reopened`, `wontdo` and `skipped`. A recurring task's next occurrence carries the series' history on (with the `done` or `skipped` just logged) plus a `repeat` entry ("Repeats: next Oct 3"), so the series reads as one log; it gets no `created` of its own. Only these; title, tag or order edits are not logged. Undo must not leave a trace, so every undo that PUTs old fields also sends the task's old `history` (`oldHistory` in `deck.js`: start-date, won't-do, skip and keep undos), and the server applies a sent `history` last (`_clean_history` validates it), replacing what that request logged. Restore snapshots keep it too. Reopen-undo and bulk-complete-undo go through `batch-complete` and do log (`done`/`reopened`).

The edit modal shows it in a closed **History** section (newest first, with a count), (`.task-edit-extras`). Creation is the oldest entry; tasks from before the log get one from `created_at`. There is no separate "Created:" line.

### Comments

Quick status lines on a task ("called Bob, waiting", "Alex: 6 people"), in `comments` (`[{ id, at, text }]`, oldest first; `id` 8 hex chars, `at` `YYYY-MM-DDTHH:MM`, text up to `COMMENT_MAX` = 2000 chars). Not part of the History: they are never capped, undo of other changes never touches them, and adding one logs no event.

- **Routes**: `POST /api/deck/<id>/comments`, `PUT`/`DELETE …/comments/<cid>`, each returning the task, for open and completed tasks alike (`_find_for_write`). `PUT /api/deck/<id>` with `comments` replaces the whole list, for undoing a delete; `_clean_comments` validates it (and restore snapshots), dropping bad entries and renumbering duplicate ids.
- **Edit modal** (`renderComments` in `deck.js`): a Comments field under Notes, hidden for a new task, with the list newest first. Enter (or Add) saves a comment at once, not with Save, so Cancel never loses one. ✎ edits one in place (Enter or leaving the field saves, Esc cancels without closing the modal), × deletes it with an undo toast.
- **Cards**: a `💬 N` button (`.task-comments-btn`) in the meta row (an expanded compact task also lists the latest 3, see Compact); its tooltip is the latest comment with its time, a click opens the modal with the comment field focused (`openEditModal(id, { focusComment: true })`).
- **Carried on**: a recurring task's next occurrence takes the series' comments with it, like the history.
- **Search and MCP**: comment text is part of a task's search text (`task_search_docs`, so semantic search too), a comment counts as activity for `edited:`/`after:`/`before:` (`_last_activity`), and `_mcp_task_text` lists them after the notes under "Comments:" (`- YYYY-MM-DD HH:MM: text`).

### Keep tasks

A task with `keep: true` is never archived: ticking it off means "done for now, back to the pool". It suits a shopping list used with the deck's views: Open is the catalogue of staples, starting items today puts them on Focus (the actual shopping list), and ticking one off sends it back to Open. Completing a keep task (`_keep_done`, in `PUT` and in `batch-complete`) clears its start date and its deadline, and sets `last_done` to today; it stays in `_deck/tasks.json` with the same id, tags, notes and place in Open's order, and Completed stays empty. Since Completed can't reopen it, the card shows an undo toast that restores the old fields (`keepSnapshot`). **Won't do** and **Delete** retire a staple. Keep and Repeat both say what happens after done, so they exclude each other: `keep` clears `recurrence`, and the edit modal disables Repeat while "Keep after done" is checked. Cards mark keep tasks with ⟲ (`.task-keep`). `[[deck keep=1]]` makes new tasks keep tasks.

### Separate lists

A separate list is a tag that a page declares with `[[deck list=<tag>]]`: a higher-order tag, nothing more. It gets a home page (its view's params, where links to its tasks open) and a marker, but otherwise behaves as a tag; in particular the main deck shows its tasks like any other (it once hid them, with a "Show separate lists" checkbox to bring them back; removed 2026-10, since the main deck is the view of all tasks you filter). There is no `list` field (an old stored one is ignored); a task is in a list by carrying its tag.

- **Declarations** (`server._deck_lists`, installed as `deck.LISTS_FN`): every page is scanned for `[[deck list=…]]` outside code (`_page_deck_lists`, kept in `_page_meta_cache`, so by mtime), giving `{name: {slug, title, params}}`. When several pages declare one, the first slug A–Z is its home. A tag no page declares is an ordinary tag (`/deck?list=<tag>` still shows its tasks, with a note saying no page declares it). Editing the declaration away ends the list at once.
- **A task's list** (`deck.task_list`, mirrored by `taskList` in `deck.js`): the first (A–Z) of its tags that is a declared list. Links to a list task (search, Linked tasks, task chips, MCP) carry `&list=<name>`; on a page that declares a list the task is in, its chips and Linked tasks use that list instead (`server._link_list`), so a task in two lists (`#presentideas #task`) opens in the list the page is about. A chip re-rendered after ticking keeps it: `leaf.js` sends the page's list embeds as `/api/preview?lists=…`. And `/deck?list=<name>` renders with the declaring embed's params, so the list looks the same everywhere.
- **Scopes** (`deck.in_scope`, `inScope` in `deck.js`): a list view shows its tag's tasks; the **main deck** (an embed with neither `list=` nor `tag=`, including `/deck`, URL `?tag=` notwithstanding) shows every task, list tasks included, as do a `tag=` embed (a `#task #sam` task belongs on a Sam page whatever lists exist), the main Completed count and, without a deck home, the due count.
- **Locked filter**: an embed's own filter words (`list=` and `tag=`; `embedFilterWords`) are chips without × (`chipField`'s `locked`, `.tag-chip-locked`; Backspace skips them), so the page always shows what it says and new tasks get its single tags. A URL `?tag=` stays removable. In the filter, the edit modal and on cards a list's tag comes first and is marked as a list: a list icon (an SVG mask in CSS `::before`, `--list-icon`, in the accent colour; so the text stays `#name`, what you type), a faint blue tint and a tooltip naming its home page ("List: Shop", `listTooltip`; titles from the embed's `data-list-homes`, `_deck_lists`' `title`). Other tags (locked or not) stay plain (`listWord`, `.tag-chip-list`; `.task-tag-list`). A task's tags are never locked: swapping `#groceries` for `#hardware` in the edit modal or with bulk tag (`t`/`T`) moves it to another list. A save that takes a task out of view says where it went: "Saved to list “…”." when it left for a list (`listMovedNotice`), else "Saved; it no longer shows here." (`leftViewMessage`). Both are undo toasts (`offerUndoLeave`): undo PUTs back everything the save could change (`editSnapshot`: fields, order, recurrence and the old history, so no trace) and reloads the deck, since the task left `tasks`. Bulk tag moving tasks out gives one toast ("2 tasks saved to list “…”.") whose undo restores each one's tags. A new task created out of view only gets the notice.

### Touch

- With a mouse (`hover: hover`) there is no ⋯ button (right-click), and ⏭ shows only on hover, for the selected card, or with focus inside (opacity, so nothing moves); under `(hover: none)` ⋯ is always shown and ⏭ hidden (the ⋯ menu has it); under `(pointer: coarse)` buttons, checkboxes and chips get finger-sized targets, fields use 16px text (no iOS zoom), and cards can't be text-selected.
- **Long-press drag**: 350 ms without moving over 10px picks a card up and selects it (without scrolling). A floating copy (`.task-touch-ghost`) follows the finger, the page auto-scrolls near the edges, and release drops through the same code as a mouse drop (`touchDrop` adapters registered by each render; list touch listeners attached once). A touch `pointerdown` sets the card's `draggable` to false (mouse sets it back; cards are marked `data-drag`), and native `dragstart` is cancelled for touch, because Android Chrome and iOS Safari otherwise start their own HTML5 drag mid-gesture. Moving earlier is a normal scroll; a finished drag isn't a tap.
- **Swipes** (`swipeActions`; they may start on a date chip, which sits on the first line where a thumb starts, a mere tap still opening its picker) only move tasks in and out of Focus: right = start today (Open, Plan), left = clear the start date (Focus, Plan). No action, no movement (e.g. right on a Focus task, left on an unplanned or due task). A mostly sideways move (|dx| > 1.5·|dy|, over 10px) drags the card and reveals the label (`data-swipe-label`); past `min(110px, 30%)` it arms (`.task-swipe-armed`), and release runs `setStart` with an undo toast. Cards have `touch-action: pan-y` and `.tasks-list` `overflow-x: clip`.

### Keyboard shortcuts

- **g d**: go to `/deck`; **g t**: go to the deck's home
- **v**: one line per task / full cards (see Compact)
- **Shift+F10** / menu key: the selected task's ⋯ menu
- **c t**: a new task in the deck's home, with its tags (from any page; see Modules → New task from anywhere)

---

## Styling Guidelines

- Max content width: `720px`, centered.
- Font: system font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`), 16px on every screen (phones too), line height 1.6. Headings in pages: `##` 1.4rem, `###` 1.15rem, so sections stand out by size, not only by weight and rule.
- Monospace: `"JetBrains Mono", "Fira Code", monospace`.
- Light/dark mode via `prefers-color-scheme` media query.
- No nav bar (keyboard shortcuts): the module switcher in the breadcrumb, New scratch note / New task / Search icons at the top right on desktop, a bottom tab bar on phones (see Modules).
- Big screens (≥1320px): page views get side columns, TOC left and links right (see Side columns); the text keeps its width.
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

`tests/test_task_refs.py` covers `[[task:…]]` references: chip rendering and badges, done/won't-do/missing, recurring series, list rows, code, Linked tasks, search by task title and MCP `read_page`. `tests/e2e/test_task_refs.py` ticks chips (undo, keep tasks), the editor completion and Make task (the modal for one item, Cancel, batch mode for several lines with shared tags and per-line shorthand, headings skipped, inline selection, undo, the hidden deck leaving the editor's keys alone).

`tests/test_semantic.py` covers semantic search with a fake embedder (words mapped to a few "concept" axes), so it needs neither fastembed nor a model: chunking, the content-addressed cache, failure fallback, the worker, rank fusion, hybrid `/api/search` and the Related section. It swaps `server.SEMANTIC` for an index over the temp wiki.

To run the suite without a local venv: `./run.sh test` (runs inside the app image with the repo mounted).

### Browser tests (`tests/e2e/`)

End-to-end tests drive the real app in headless Chromium via Playwright. They run in a separate test image (`Containerfile.e2e`, based on Microsoft's Playwright image) so the app image stays browser-free and the app still has no build step or npm. `./run.sh e2e` builds that image (cached) and runs them with the repo bind-mounted; extra args pass through to pytest (e.g. `./run.sh e2e -k deck`). Without Playwright installed they skip, so plain `pytest tests/` is unaffected.

- `conftest.py` serves `server.app` on a background thread (werkzeug `make_server`, random port) and points it at a fresh temp wiki with its own git repo per test; each test gets a fresh browser context. A request to any other host is aborted (offline, deterministic) and fails the test: Leaf loads nothing from a CDN. Uncaught page JS errors fail the test.
- Coverage: Scratch filter (`test_search.py`), modules (breadcrumb switcher and current module, phone tab bar, New per module, due count on the Deck links, the deck home, desktop top actions, New task/`c t`/`g t` following the deck home; `test_mobile_nav.py`), side columns (sticky TOC and links from 1320px, active section, code and tables growing only when needed, none below 1320px; `test_layout.py`), editor (formatting bar and Ctrl+B/I/K, auto-save + unsaved dot, no commit on auto-save, Done/Ctrl+S commit, pagehide beacon, live preview/F2, list continuation), search modal (Ctrl+P/Esc, fuzzy ranking with file names and its tie-break, content matches, query syntax, highlights on the opened page, arrow/Enter nav, unlinked filter), deck (Plan-board drag to a day and to Unplanned, touch long-press drag via CDP touch events (board, Open-tab reorder, swipe still scrolls, long press is not a tap), board grouping by start date, modal "Days before due", Plan board in Open's order with in-section drops ignored, view tabs, start-date Today, touch: tap-to-edit, ⋯ menu/bottom sheet, target sizes, editor scrolling/compact phone layout, date picker placement), sortable tables (header click cycles ascending/descending/original, numeric columns, empty cells last, keyboard, not in the editor preview), layout (no horizontal page scroll at 390px phone width on page/editor/deck views, mobile Preview button, tldr toggle geometry).
- Density: the test setup (`_new_page`'s init script) makes decks open with full cards, as before the per-tab defaults, since most tests exercise cards; a test of the real defaults calls `real_density(page)` first.
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
- Optional password auth: set `LEAF_PASSWORD` in `.env` to enable a login gate. If unset, no auth is required. `SECRET_KEY` can also be set to keep sessions across restarts; if absent, a random key is generated per-process. Routes `/login` (GET/POST) and `/logout` (POST) handle the session. A "Sign out" button is rendered in the bottom-left corner when auth is enabled (the bottom-right holds the New/Search buttons; bottom-right only on pages without them); on phones it sits in the status-badge row at the top, hidden while typing.
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
