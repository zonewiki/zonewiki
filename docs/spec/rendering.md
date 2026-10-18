# Rendering

Part of Leaf's spec (see `AGENTS.md` for the index).

## Rendering

- Raw HTML in a page is rendered as written (markdown-it's `html` option), but no script in it runs: the Content-Security-Policy allows only Leaf's own script files (Scripts in pages don't run, `docs/spec/security.md`).
- `server.py`'s `view_page` reads `<slug>.md`, `render.render_md()` converts it to HTML using `markdown-it-py`, and `views.page("view.html", …)` renders it into the page (see Templates). A path with a slug in it is built with `pages.url()`, which percent-encodes it, so a file named with a quote, `#` or space still gets a working link that is safe in an attribute.
- The HTML shell includes: status badges, breadcrumb (with the module switcher), page header (title, Edit, the ⋯ page menu), content area, backlinks, the action bar, and the search/shortcut/scratch modals. There is no nav bar (see Modules).
- `highlight.js` (vendored, see Dependencies) auto-highlights fenced code blocks client-side after load.
- **Deck task notes and comments** render with the same markdown (`render.render_notes`, `POST /api/render-notes`): wikilinks, `[[task:…]]` chips, task-date chips, without what only a page has (frontmatter, `[[toc]]`, `[[deck]]`, ✎ per section, heading ids). See Notes in `docs/spec/deck.md`.
- The shared shell renders the product brand as `Leaf` (page titles, the switcher's accessible name), with a small green inline SVG leaf logo (`_LEAF_LOGO`) on the breadcrumb's Wiki link.

### Autolinks and callouts

- **Bare URLs become links** (`gfm_autolink_plugin`): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link. Code spans, fenced code and existing links are untouched. It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **External links open in a new tab**: a link to another site (http/https, not the host the request came in on) gets `target="_blank" rel="noopener noreferrer"` (`_is_external`, a `link_open` render rule, so autolinks too); `deck.js` does the same for links in task titles and notes (`extAttrs`). Leaf's own links, `mailto:` and a full URL to Leaf itself stay in the tab.
- **Callouts** (markdown-it's `alerts` option, GitHub/Obsidian syntax): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line. Styled in `style.css` (GitHub colours, a `--alert` variable per kind, dark variants). Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

### Sortable tables

Clicking a column header of a table in a page's article sorts its rows: ascending, descending, then back to the file's order (`sortTableBy` in `js/page.js`; Enter/Space on a focused header too). View only: the file is never changed and nothing is remembered. A column whose non-empty cells are all numbers (`tableCellNumber`: `-2`, `9.5`, `1,200`, `50 %`, `€3`) sorts numerically, any other column with a numeric-aware, case-insensitive collator (`item 9` before `item 10`, ISO dates in order). Empty cells go last in both directions. The state is the header's `aria-sort`, which the CSS turns into ▲/▼ (↕ on hover). Only `article.page-content` tables with a header, one body, at least two rows and no merged cells (raw-HTML `colspan`/`rowspan`) are made sortable; the editor preview is not. Clicks are delegated, so presentation slides (cloned from the article) sort too.

### Templates

Every view is a Jinja2 template in `src/templates` that extends `layout.html` (head, the dialogs the client code opens, the status badges, top actions and action bar), rendered by `views.page(template, title, …)`. The environment (`templating.py`: `render`, `macro`) has auto-escaping on and `url` and `config` as globals; it is not Flask's `render_template`, so templates also render outside a request (tests, and Python callers that embed the result, like `deck.build_deck_embed`). A variable holding HTML that Leaf built itself ends in `_html` and is the only kind marked `|safe` (rendered markdown, an excerpt with `<mark>`s). Reusable parts are macros in `templates/partials/` (`page.html`: the ⋯ menu and dialogs, link sections, side columns, meta row; `history.html`; `modules.html`; `icons.html`), which Python can call with `templating.macro(template, name)`. Markdown-level fragments (task chips, the TOC, ☑ chips) stay in `render.py`. Escaped quotes in attributes come out as `&#34;` (markupsafe), not `&quot;`.

### Client code

No build step and no bundler: the browser loads ES modules as they are. Every page loads `public/leaf.js` (`<script type="module">` in `layout.html`), which imports the modules in `public/js/` and `deck.js`; each module wires up what it finds on the page. They share code by `import`, not through `window`:

| Module | Does |
|---|---|
| `js/util.js` | Small shared helpers: `he`, `localDate`, `toSlug`, `wikilinkHref`, `pasteUrlAsLink`, `continueList`, `showToast`, `putTask`, … |
| `js/completion.js` | `linkAutocomplete`: `[[`, `[[task:` and `[text](` completion (editor and the deck's task fields) |
| `js/chrome.js` | Around the page: navigation trail, backlinks chip, side columns, the Deck links' counts (`refreshDueBadge`) |
| `js/git.js` | Uncommitted badge, Commit buttons, forms that confirm first (`data-confirm`) |
| `js/search.js` | Search modal and its preview, the Scratch filter, highlights on a page opened from search |
| `js/actions.js` | New scratch note / page / task (`openHomeTask`, loading a hidden deck when the page has none), action bar, sharing mode |
| `js/pagemenu.js` | The page's ⋯ menu and its dialogs (rename, move to wiki, append, delete) |
| `js/shortcuts.js` | Keyboard shortcuts |
| `js/mdedit.js` | `markdownEditor`: the markdown helpers of the editor and the deck's task notes (formatting bar, completion, snippets, lists, uploads), `taskCheckboxOffsets` |
| `js/editor.js` | The editor: auto-save, preview, Enter on a wikilink, Make task, rename bar |
| `js/page.js` | Page view: checkboxes, `[[task:…]]` chips, sortable tables, syntax highlighting, lightbox |
| `js/present.js` | Presentation mode |
| `deck.js`, `deck-core.js` | The deck app (exports `initDecks`, `openDeckEdit`) and its pure logic (`DeckCore`, also run by the Node tests) |

### Section headings and HTML post-processing

Leaf's additions to markdown-it's output are render rules on the parsed tokens, not regexes over the HTML: `heading_open`/`heading_close` (`render._heading_open`/`_heading_close`) wrap each `##`/`###` in `.section-header` with its ✎ link when `render_md` gets `edit_slug` (the page view; numbered as `mdtext.split_sections` counts sections), and add the `☑ N` chip from `section_tasks`; `html_inline`/`html_block` turn the task-date comments (`<!--t:… d:…-->`, written by `toggle_nth_checkbox`) into chips; the `leaf_images` core rule handles image widths and `img-block` paragraphs.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:
- An `<h1>` with the page title (derived from the first `# Heading` in the file, or the slug as a fallback)
- An Edit button linking to `/edit/<slug>`
- A **⋯ page menu** (`.page-menu-btn`, menu `#page-menu`, `page_menu` in `templates/partials/page.html`, its data from `views.page_menu`; opening, keys and actions in `js/pagemenu.js`) with the rarer actions: **Present** (`#present-btn`, only on a page with headings, `p`), **History** (git only), **Move to wiki…** and **Append to page…** (scratch notes only, see Scratch), **Rename…** (a dialog, `#page-rename-dialog`, see Rename) and **Delete…** (the `#page-delete-dialog` confirmation, also Ctrl+Delete); Rename… and Delete… not for `index`. One hidden menu element for both ⋯ buttons (header and right column), positioned under the pressed one: a popover with ↑/↓, Esc (focus back to the ⋯), a bottom sheet at ≤640px, styled like the deck's ⋯ menu (`.task-menu` rules list `.page-menu` too). It sits outside `.page-grid`, since the middle column is a size container and would anchor `position: fixed`. Shortcut hints (`.menu-key`) hide on touch screens.

**Copy link**: on a page view a link icon (`.heading-link`, added by `js/page.js`) sits by the title and by every heading with an anchor (`#`, `##`, `###` in the article; not a deck embed's), hidden until the heading is hovered (or the icon focused). It hangs in the margin left of the heading from 800px wide, after the heading on narrower screens. A click copies the page's URL (title) or the section's URL with its `#anchor` (heading, which also puts the anchor in the address bar), confirmed by a "Link copied" toast and a ✓ in the icon's place for a moment. The icon is sized in `em`, so it scales with the heading (at least 14px), and centred on the heading's first line. Presentation mode hides it.

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body. Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before passing to `md.render()`.

### Wikilinks

- Wikilinks use the `[[Page Name]]` syntax. Nested pages can be linked as `[[journal/my-page]]`.
- **Sections**: `[[Page#Section]]` links to a heading on that page (`/page/page#section`, the id as the heading renders, `section_anchor`), labelled "Page › Section"; `[[Page#Section|Label]]` with a label; `[[#Section]]` a heading on the same page. `split_wikilink` parses all forms (server), `wikilinkHref` in `js/util.js` the same for the editor's Enter-on-a-wikilink (task notes render on the server, `render_notes`). The section is ignored for backlinks and kept on rename.
- Before passing markdown to `markdown-it-py`, run a regex pre-processing step that rewrites wikilinks to standard markdown links:
  - `[[Page Name]]` → `[Page Name](/page/page-name)` (slug = lowercase, spaces → hyphens)
  - `[[Page Name|Label]]` → `[Label](/page/page-name)`
- If the target page does not exist, render the link with a CSS class `wikilink-missing` so it can be styled differently (e.g. red or dashed underline). Clicking a missing wikilink redirects to `/edit/<slug>` so the page can be created immediately.
- The same pre-processing must run in the `/api/preview` endpoint so the live editor preview also renders wikilinks correctly.
- Code is exempt: `render_md()` masks fenced code blocks and inline code spans (`mask_code`, private-use-character tokens) before running the wikilink, `[[toc]]` and `[[deck]]` pre-processors and restores them (`unmask_code`) before `md.render()`, so directives inside code render literally. The TOC also ignores `##` lines inside fences.
- Add a route `GET /api/pages` that returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

### Task references

`[[task:<id>]]` in a page shows one deck task inline as a live chip. `<id>` is the first 8 characters of the task's id (`decksyntax.SHORT_ID_LEN`, like a short git hash) or the whole id, case ignored (`decksyntax.TASK_REF_RE`). Nobody types ids: in the editor, `[[task:` (or the formatting bar's task button) opens a completion over open tasks (title words or id prefix, newest first, with list and deadline; `taskAcMatches` in `js/completion.js`, tasks from `/api/deck`, refetched after a minute) that inserts the reference.

- **Rendering** (`render._mask_task_refs`/`_inject_task_refs`, chip from `_task_ref_html`): masked with private-use tokens (`\ue002N\ue003`) after code masking, so code keeps them literal and markdown never touches the chip, then replaced after `md.render`. The chip: a checkbox, the title (links flattened, escaped) linking to the task in the deck (`task_href`, list and archive year included), and badges: ⏰ deadline (red overdue, green today), 📅 start when today or later (amber today, blue later), ↻ repeats, ⟲ keep (with "✓ <last done>"), the list's name; done tasks struck through with "✓ <date>" (each date a `<time class="local-date">` in US English, which the browser rewrites in `LEAF_LOCALE`; with `LEAF_DATE_FORMAT=iso` `YYYY-MM-DD` from the server, see Dates in `docs/spec/deck.md`), won't-do with "✗ won't do" and a disabled checkbox. Unknown id: a red dashed `task <id>?` (`.task-ref-missing`). A reference alone in a list item makes that `<li class="task-ref-item">`, a checklist row without the chip's box.
- **Resolving** (`deckstore.resolve_task_refs`): open tasks win over completed ones with the same prefix; archives count. A completed recurring task stands for its series: the reference follows `spawned_from` to the current occurrence, so ticking it and reloading shows the next one.
- **Ticking** the chip's checkbox (`js/page.js`, page view and editor preview) PUTs `{ completed }` to `/api/deck/<id>`, so repeating and keep tasks behave as in the deck, then re-renders the chip from `/api/preview` of `[[task:<full id>]]` (one renderer: a repeating task shows its next occurrence, a keep task its ✓). Undo toast: `completed: !done` plus the task's old `history` (no trace), or for a keep task its old `start_date`, `due_date`, `last_done` and `history` (fetched from `/api/deck` before the change).
- **Linked tasks** leaves out tasks the page shows inline (`page_task_refs`).
- **Search** indexes a reference as its task's title (`_task_refs_as_titles`, also in `_semantic_text`); a page with references is re-indexed when the deck files change (the page doc cache keeps the deck's mtimes). Embeddings and MCP section docs are cached by page mtime, so they catch up with a renamed task when the page changes.
- **MCP `read_page`** keeps the raw markdown and appends what each reference stands for (title, status, dates, link; `_mcp_task_refs`).
- **Make task** (formatting bar, `makeTasks` in `js/editor.js`): turns text in the editor into deck tasks and replaces it with their references, e.g. action items in meeting notes. A selection within one line becomes one task and an inline reference. Otherwise each non-empty line the selection touches (or the cursor's line) becomes a task, keeping its list marker and dropping a checklist's `[ ]` (`- [ ] Call Bob` → `- [[task:…]]`); blank lines, headings and lines that are already a reference stay. Each task's notes say `From [[<page>]]`.
  - **One item opens the deck's edit modal** to review it first: the editor page carries a hidden deck app (`build_deck_embed(…, headless=True)`: `data-headless`, `display:none`; `deck.js` then ignores the deck's keyboard shortcuts and the "moved to another list" notice), and `openDeckEdit(null, { title, description, start_date: null, onCreated })` (exported by `deck.js`) opens it with the text as title (its quick-add preview shows the shorthand) and no start date. Save (Enter) creates the task and `onCreated` puts the reference in; Cancel/Esc changes nothing. The same modal serves `c t` in the editor (with the deck home's tags).
  - **Several lines** open the same modal in batch mode (`openDeckEdit(null, { titles, … })`, `setBatch` in `deck.js`): the titles are listed instead of the title field (each with its shorthand preview), focus starts in Tags, and the fields (notes, tags, dates, repeat, keep) apply to every task. A line's own shorthand adds to them: its tags join the shared ones, its dates win over the shared ones. Save creates them one by one (`createTask`) and `onCreated` gets them all; if one fails, or the text changed meanwhile, the created ones are deleted again and nothing is replaced. Should the deck app be missing, the editor creates them right away with `DeckCore.parseQuickAdd`.
  - The undo toast (both ways) deletes the tasks (`batch-delete`) and puts the text back.

### Image sizing

- `![alt|300](url)` sets the display width to 300px: a markdown-it core rule (`render._images`) takes `|300` off the alt text and gives the image `style="max-width:100%;width:300px"`.
- Works for any integer pixel width. `max-width:100%` ensures the image never overflows on narrow screens.

### Frontmatter

- A page may start with a leading `---`-fenced block declaring `tags:` (comma-separated, or a bracketed list `[a, b]`) and `tldr:` (a one-line summary, quotes optional). Any other key is parsed and ignored.
- `parse_frontmatter()` extracts it into a dict and returns the remaining content with the block removed. `render_tags_pills_html()` renders a tag list as clickable pills linking to `/search?q=%23<tag>` (the `#tag` filter: exactly the pages and tasks with that tag, not every page using the word; a tag containing a space can't be written as `#tag` and keeps a plain word search) (styled via the same `.task-tag` class Deck uses); `_render_frontmatter_html()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — with independent `show_tags`/`show_tldr` flags to omit either half.
- `render_md(text, *, show_tags=True, show_tldr=True)` calls these internally, so most call sites (`/api/preview` live preview, revision view) get both tldr and tags rendered at the top of the article for free — those use the `<details>` form, since they have no meta row to place a toggle in.
- **`view_page` is the exception**: it parses frontmatter itself and renders tags plus a tldr toggle in the `page-meta-wrapper` row below the H1 instead — together in `.page-meta-left` (tags pills then the `tldr` label, left-aligned), with "Last changed" right-aligned in `.page-meta-right`, followed, when the file is uncommitted, by a link-styled **Commit** (`commit_form_html(..., inline=True)`, `.commit-link`) on the same line rather than a separate button row (`justify-content: space-between` on the wrapper). It calls `render_md(..., show_tags=False, show_tldr=False)` so nothing frontmatter-related is duplicated in the article body.
- The view-page tldr toggle is **not** a `<details>` — it's a visually-hidden checkbox (`.tldr-toggle`) emitted *before* `.page-meta-wrapper`, a `<label for="tldr-toggle">` *inside* the row, and the `<p class="page-tldr">` *after* the row. Keeping those three as siblings is what makes `.tldr-toggle:checked ~ .page-tldr { display: block }` work, so expanding reveals the text on its own full-width line below while the label stays put on the tags line. A `<details>` can't do this: its content is nested inside it, so making the text full-width drags the summary down with it. Collapsed by default, CSS-only (no JS), and the revealed text uses `.95rem` rather than inheriting the row's `.75rem` meta size.
- `.page-meta-wrapper` is a normal-flow block rendered as a sibling right after `</header>` (not nested inside it, not absolutely positioned) — so `.page-header`'s `border-bottom` stays anchored directly under the H1/actions row, while the meta row can still grow (e.g. an expanded tldr) and push the article below it down instead of overlapping it. It uses a small negative top margin (`margin: -1.1rem 0 1.5rem`) to sit close under the header despite being a separate block, rather than living inside the header's own margin.
- `strip_h1()` skips over a leading frontmatter block before looking for the first non-empty line, so the H1 immediately after frontmatter is still correctly stripped (not duplicated below the page header).
- The **editor textarea always holds the raw, unparsed file** — including the frontmatter block verbatim — so editing it is just editing text like any other part of the file; there is no separate metadata-edit form. The live preview pane shows the parsed tldr/tags block (both, at the top of the article) as you type, via the same `render_md()` path — it has no separate meta row to place them in.
- `page_title()` returns the first `# ` heading in the file, skipping a leading frontmatter block and fenced code (so a `# comment` in a code example is never the title), else a title derived from the slug.

### Version history

- Git is installed in the container image. On startup, `git_setup()` initializes a bare repo in `LEAF_DATA` (if not already one), writes an empty `.gitignore`, and commits all existing files.
- Every write operation (Done/save, create, rename, delete, restore, upload) calls `git_commit()` (a rename or move through `_move_page()`, with the pages and deck files whose links changed) to stage and commit the change. **Auto-save (`/api/save`) is the exception** — it writes the file to disk but does not create a git commit. Assets are tracked like pages — a `git push` backs up everything.
- `GIT_ENABLED = shutil.which("git") is not None` — all git paths are no-ops when git is absent (graceful degradation).
- git runs with `maintenance.autoDetach=false` and `gc.autoDetach=false` (`_GIT_ENV`, also used by the tests' own git calls): git 2.47+ starts its automatic maintenance after a commit as a detached process, which nobody reaped with Leaf as the container's PID 1, so every commit left a zombie until the PID limit (2048) stopped everything (it also crashed test sessions). Not detached, git reaps its own children; `run.sh`'s `--init` covers anything else.
- SHA values are validated with `SHA_RE = r'^[0-9a-f]{7,40}$'` before use.
- History is in the page view's ⋯ menu (and the breadcrumb of a revision or diff links back to it).
- **`/history/<slug>`** lists the page's saved versions, newest first, one row each (`.hist-list`): date and time, what the save did in words (`version_label`: Leaf's own commit messages read alike, so "Update p" → Edited, "Rename a to b" → Renamed from “a” or, on a page whose link was updated, Link updated, "Restore p to <sha>" → Restored the version of <date>, also Created/Deleted/Imported; other messages as written) and the lines added/removed (`--numstat`). The row opens that version; the page as it is now is marked **Current** and opens the page itself (`is_current`: the newest commit equals the file on disk; with edits not committed yet a "Now · Not committed yet" row is the current one instead). **Changes** opens the diff.
- **`/revision/<slug>/<sha>`** shows that version with a bar (`.version-bar`) saying where it stands ("The version of 2026-10-08 14:32 · 2 versions back", "The current version", or a deleted page's last version), **What this save changed** (the diff), **Restore this version** (a plain button that asks first: `form[data-confirm]` in `js/git.js`; not offered for the current version) and **← Older / Newer →** (`version_nav`; Newer from the version before the current one is **Current →**, the page). The breadcrumb names the version by its time, not its hash.
- **`/diff/<slug>/<sha>`** ("Changes") shows what that save changed, red taken out and green put in, without git's file headers (`diff_to_html` leaves out everything before a file's first `@@`; a hunk header becomes `⋯`), with **View this version** and the same ← Older / Newer →. No Restore there: a diff shows a change, not a version.
- **Renames**: `git_log` (`--follow`) records each commit's path for the file (`path`, from `--numstat`, `_numstat_path` reading `old => new` and `dir/{old => new}.md`), and revision, diff and restore read a version under the name it had then (`version_path`); before, a version from before a rename was a 404. The diff of a rename passes both names (`git_diff(…, *also)`, `-M`), so a pure rename shows no change instead of the whole page as new.

### Table of Contents

- `[[toc]]` on a line by itself (case-insensitive) is replaced with a `<nav class="toc">` block listing all `##` and `###` headings in the page as anchor links.
- The TOC is built before `process_wikilinks` runs. `[[toc]]` is replaced with a placeholder, rendered, then the placeholder `<p>` is swapped for the nav HTML.
- Heading anchors are generated by `anchors_plugin` from `mdit-py-plugins`. The `heading_id()` helper replicates the plugin's slugify for the href values in the TOC.
- If the page has no `##`/`###` headings, `[[toc]]` renders nothing.
