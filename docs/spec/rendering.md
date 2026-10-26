# Rendering

Part of Leaf's spec (see `AGENTS.md` for the index).

## Rendering

- Raw HTML in a page is rendered as written (goldmark's `WithUnsafe`), but no script in it runs: the Content-Security-Policy allows only Leaf's own script files (Scripts in pages don't run, `docs/spec/security.md`).
- `viewPage` (`go/routes.go`) reads `<slug>.md`, `renderMd()` (`go/render.go`) converts it to HTML with goldmark (`go/markdown.go`), and `page(…, "view.html", …)` renders it into the page (see Templates). A path with a slug in it is built with `pathURL()`, which percent-encodes it, so a file named with a quote, `#` or space still gets a working link that is safe in an attribute.
- The HTML shell includes: status badges, breadcrumb (with the module switcher), page header (title, Edit, the ⋯ page menu), content area, backlinks, the action bar, and the search/shortcut/scratch modals. There is no nav bar (see Modules).
- `highlight.js` (vendored, see Dependencies) auto-highlights fenced code blocks client-side after load.
- **Deck task notes and comments** render with the same markdown (`renderNotes`, `POST /api/render-notes`): wikilinks, `[[task:…]]` chips, task-date chips, without what only a page has (frontmatter, `[[deck]]`, ✎ per section, heading ids). See Notes in `docs/spec/deck.md`.
- The shared shell renders the product brand as `Leaf` (page titles, the switcher's accessible name), with a small green inline SVG leaf logo (`site-logo` in `templates/partials/icons.html`) on the breadcrumb's Wiki link.

### Autolinks and callouts

- **Bare URLs become links** (goldmark's linkify, with `urlRE`, which needs no dot in the host, so `http://localhost:5000/…` and `http://nas:5000` are links): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link. Code spans, fenced code and existing links are untouched. It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **External links open in a new tab**: a link to another site (http/https, not the host the request came in on) gets `target="_blank" rel="noopener noreferrer"` (`leafTransformer.external`, on the parsed document, so autolinks too); `deck.js` does the same for links in task titles and notes (`extAttrs`). Leaf's own links, `mailto:` and a full URL to Leaf itself stay in the tab.
- **Callouts** (GitHub/Obsidian syntax, `alertTransformer`): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line. Styled in `style.css` (GitHub colours, a `--alert` variable per kind, dark variants). Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

### Sortable tables

Clicking a column header of a table in a page's article sorts its rows: ascending, descending, then back to the file's order (`sortTableBy` in `js/page.js`; Enter/Space on a focused header too). View only: the file is never changed and nothing is remembered. A column whose non-empty cells are all numbers (`tableCellNumber`: `-2`, `9.5`, `1,200`, `50 %`, `€3`) sorts numerically, any other column with a numeric-aware, case-insensitive collator (`item 9` before `item 10`, ISO dates in order). Empty cells go last in both directions. The state is the header's `aria-sort`, which the CSS turns into ▲/▼ (↕ on hover). Only `article.page-content` tables with a header, one body, at least two rows and no merged cells (raw-HTML `colspan`/`rowspan`) are made sortable; the editor preview is not. Clicks are delegated, so presentation slides (cloned from the article) sort too.

### Templates

Every view is a Go `html/template` in `go/templates` (embedded in the binary) that fills the blocks of `layout.html` (head, the dialogs the client code opens, the status badges, top actions and action bar), rendered by `page(w, rq, status, template, title, …)` (`go/views.go`). Templates escape by context; their functions (`funcs`) include `url` (`pathURL`) and `dict` (a template's arguments). HTML that Leaf built itself is passed as `template.HTML` (rendered markdown, an excerpt with `<mark>`s), anything else is escaped. Reusable parts are named templates in `templates/partials/` (`page.html`: the ⋯ menu and dialogs, link sections, side columns, meta row; `history.html`; `modules.html`; `icons.html`), which Go code renders with `macro(name, data)` (the breadcrumb, the deck embed). Markdown-level fragments (task chips, ☑ chips) stay in `go/render.go`.

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

Leaf's additions to goldmark's output work on the parsed document, not by regexes over the HTML (`go/markdown.go`): the `heading` render function wraps each `##`/`###` in `.section-header` with its ✎ link when `renderMd` gets `EditSlug` (the page view; numbered as `splitSections` counts sections), and adds the `☑ N` chip from `SectionTasks`; `taskDates` turns the task-date comments (`<!--t:… d:…-->`, written by `toggleNthCheckbox`) into chips; `leafTransformer` handles image widths and `img-block` paragraphs; `taskTransformer` gives exactly the lines `toggleNthCheckbox` counts a checkbox.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:
- An `<h1>` with the page title (derived from the first `# Heading` in the file, or the slug as a fallback)
- An Edit button linking to `/edit/<slug>`
- A **⋯ page menu** (`.page-menu-btn`, menu `#page-menu`, `page_menu` in `templates/partials/page.html`, its data from `pageMenu`; opening, keys and actions in `js/pagemenu.js`) with the rarer actions: **Present** (`#present-btn`, only on a page with headings, `p`), **History** (git only), **Move to wiki…** and **Append to page…** (scratch notes only, see Scratch), **Rename…** (a dialog, `#page-rename-dialog`, see Rename) and **Delete…** (the `#page-delete-dialog` confirmation, also Ctrl+Delete); Rename… and Delete… not for `index`. One hidden menu element for both ⋯ buttons (header and right column), positioned under the pressed one: a popover with ↑/↓, Esc (focus back to the ⋯), a bottom sheet at ≤640px, styled like the deck's ⋯ menu (`.task-menu` rules list `.page-menu` too). It sits outside `.page-grid`, since the middle column is a size container and would anchor `position: fixed`. Shortcut hints (`.menu-key`) hide on touch screens.

**Copy link**: on a page view a link icon (`.heading-link`, added by `js/page.js`) sits by the title and by every heading with an anchor (`#`, `##`, `###` in the article; not a deck embed's), hidden until the heading is hovered (or the icon focused). It hangs in the margin left of the heading from 800px wide, after the heading on narrower screens. A click copies the page's URL (title) or the section's URL with its `#anchor` (heading, which also puts the anchor in the address bar), confirmed by a "Link copied" toast and a ✓ in the icon's place for a moment. The icon is sized in `em`, so it scales with the heading (at least 14px), and centred on the heading's first line. Presentation mode hides it.

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body. Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before rendering (`stripH1`).

### Wikilinks

- Wikilinks use the `[[Page Name]]` syntax. Nested pages can be linked as `[[journal/my-page]]`.
- **Sections**: `[[Page#Section]]` links to a heading on that page (`/page/page#section`, the id as the heading renders, `sectionAnchor`), labelled "Page › Section"; `[[Page#Section|Label]]` with a label; `[[#Section]]` a heading on the same page. `splitWikilink` parses all forms (server), `wikilinkHref` in `js/util.js` the same for the editor's Enter-on-a-wikilink (task notes render on the server, `renderNotes`). The section is ignored for backlinks and kept on rename.
- Before the markdown is rendered, a pre-processing step (`processWikilinks`) turns wikilinks into links:
  - `[[Page Name]]` → `<a href="/page/page-name" class="wikilink">Page Name</a>` (slug = lowercase, spaces → hyphens)
  - `[[Page Name|Label]]` → a link labelled `Label`
- If the target page does not exist, render the link with a CSS class `wikilink-missing` so it can be styled differently (e.g. red or dashed underline). Clicking a missing wikilink redirects to `/edit/<slug>` so the page can be created immediately.
- The same pre-processing must run in the `/api/preview` endpoint so the live editor preview also renders wikilinks correctly.
- Code is exempt: `renderMd()` masks fenced code blocks and inline code spans (`maskCode`, private-use-character tokens) before running the wikilink and `[[deck]]` pre-processors and restores them (`unmaskCode`) before markdown, so directives inside code render literally. A page's own text that looks like such a token (`\ue000N\ue001`, or `\ue002N\ue003` for task references) is masked too and comes back as written, so it can't fail a render or swap in another code span, nor be written into a page by a rename's link rewrite. The TOC also ignores `##` lines inside fences.
- Add a route `GET /api/pages` that returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

### Task references

`[[task:<id>]]` in a page shows one deck task inline as a live chip. `<id>` is the first 8 characters of the task's id (`shortIDLen`, like a short git hash) or the whole id, case ignored (`taskRefRE`). Nobody types ids: in the editor, `[[task:` (or the formatting bar's task button) opens a completion over open tasks (title words or id prefix, newest first, with list and deadline; `taskAcMatches` in `js/completion.js`, tasks from `/api/deck`, refetched after a minute) that inserts the reference.

- **Rendering** (`maskTaskRefs`/`injectTaskRefs`, chip from `taskRefHTML`): masked with private-use tokens (`\ue002N\ue003`) after code masking, so code keeps them literal and markdown never touches the chip, then replaced after markdown. The chip: a checkbox, the title (links flattened, escaped) linking to the task in the deck (`taskHref`, list and archive year included), and badges: ⏰ deadline (red overdue, green today), 📅 start when today or later (amber today, blue later), ↻ repeats, ⟲ keep (with "✓ <last done>"), the list's name; done tasks struck through with "✓ <date>" (each date a `<time class="local-date">` in US English, which the browser rewrites in `LEAF_LOCALE`; with `LEAF_DATE_FORMAT=iso` `YYYY-MM-DD` from the server, see Dates in `docs/spec/deck.md`), won't-do with "✗ won't do" and a disabled checkbox. Unknown id: a red dashed `task <id>?` (`.task-ref-missing`). A reference alone in a list item makes that `<li class="task-ref-item">`, a checklist row without the chip's box.
- **Resolving** (`resolveTaskRefs`): open tasks win over completed ones with the same prefix; archives count. A completed recurring task stands for its series: the reference follows `spawned_from` to the current occurrence, so ticking it and reloading shows the next one.
- **Ticking** the chip's checkbox (`js/page.js`, page view and editor preview) PUTs `{ completed }` to `/api/deck/<id>`, so repeating and keep tasks behave as in the deck, then re-renders the chip from `/api/preview` of `[[task:<full id>]]` (one renderer: a repeating task shows its next occurrence, a keep task its ✓). Undo toast: `completed: !done` plus the task's old `history` (no trace), or for a keep task its old `start_date`, `due_date`, `last_done` and `history` (fetched from `/api/deck` before the change).
- **Linked tasks** leaves out tasks the page shows inline (`pageTaskRefs`).
- **Search** indexes a reference as its task's title (`taskRefsAsTitles`, also in `semanticText`); a page with references is re-indexed when the deck files change (the page doc cache keeps the deck's mtimes). Embeddings and MCP section docs are cached by page mtime, so they catch up with a renamed task when the page changes.
- **MCP `read_page`** keeps the raw markdown and appends what each reference stands for (title, status, dates, link; `mcpTaskRefs`).
- **Make task** (formatting bar, `makeTasks` in `js/editor.js`): turns text in the editor into deck tasks and replaces it with their references, e.g. action items in meeting notes. A selection within one line becomes one task and an inline reference. Otherwise each non-empty line the selection touches (or the cursor's line) becomes a task, keeping its list marker and dropping a checklist's `[ ]` (`- [ ] Call Bob` → `- [[task:…]]`); blank lines, headings and lines that are already a reference stay. Each task's notes say `From [[<page>]]`.
  - **One item opens the deck's edit modal** to review it first: the editor page carries a hidden deck app (`buildDeckEmbed(…, headless)`: `data-headless`, `display:none`; `deck.js` then ignores the deck's keyboard shortcuts and the "moved to another list" notice), and `openDeckEdit(null, { title, description, start_date: null, onCreated })` (exported by `deck.js`) opens it with the text as title (its quick-add preview shows the shorthand) and no start date. Save (Enter) creates the task and `onCreated` puts the reference in; Cancel/Esc changes nothing. The same modal serves `c t` in the editor (with the deck home's tags).
  - **Several lines** open the same modal in batch mode (`openDeckEdit(null, { titles, … })`, `setBatch` in `deck.js`): the titles are listed instead of the title field (each with its shorthand preview), focus starts in Tags, and the fields (notes, tags, dates, repeat, keep) apply to every task. A line's own shorthand adds to them: its tags join the shared ones, its dates win over the shared ones. Save creates them one by one (`createTask`) and `onCreated` gets them all; if one fails, or the text changed meanwhile, the created ones are deleted again and nothing is replaced. Should the deck app be missing, the editor creates them right away with `DeckCore.parseQuickAdd`.
  - The undo toast (both ways) deletes the tasks (`batch-delete`) and puts the text back.

### Image sizing

- `![alt|300](url)` sets the display width to 300px: `leafTransformer` takes `|300` off the alt text and gives the image `style="max-width:100%;width:300px"`.
- Works for any integer pixel width. `max-width:100%` ensures the image never overflows on narrow screens.

### Frontmatter

- A page may start with a leading `---`-fenced block declaring `tags:`, `tldr:` (a short summary, quotes optional) and `sources:` (where the page's facts come from). Any other key is parsed and ignored. Dates aren't fields: the page's history has them ("Last changed", and its revisions).
- `tldr:` may run over several lines, as YAML writes them: `tldr: >` (folded: the lines join with spaces, a blank line is a line break), `tldr: |` (literal: the lines as written, their common indent removed), either with a chomping sign or indent (`>-`, `|2`, ignored), or a plain value going on in indented lines below it (folded). Everything indented after `tldr:` is the tldr's, `- item` and `key: value` lines too. `.page-tldr` has `white-space: pre-line`, so the line breaks show.
- `tags:` and `sources:` are lists: comma-separated (`a, b`), bracketed (`[a, b]`), or a YAML block list, one `- item` line each under the key (quotes optional). A comma inside `[…]` or `(…)` doesn't split a value, so `[Doc, v2](url)` and `[[page|A, B]]` stay whole; a lone `[[page]]` is a wikilink, not a bracketed list. `addFrontmatterTags` (Append to page) rewrites a block list of tags as one `tags: a, b, c` line.
- **Sources** render at the article's end (`renderSourcesHTML`, `<section class="page-sources">`, a "Sources" list under an `<h2 id="sources">`, `sources-1` … when the page has a heading of that name; styled small), in the page view, the live preview and revisions alike, each one inline markdown: a bare URL, `[label](url)` or `[[page]]` is a link. Being in the file, a `[[page]]` source is a link like any other: it counts for backlinks, and a rename rewrites it. Being an `<h2>`, it is in the side TOC (counting towards its minimum of headings) and gets the heading's copy-link icon and `]`/`[` like any section; in presentation mode it stays on the last slide (a slide starts at a heading directly in the article).
- `parseFrontmatter()` extracts it (`Frontmatter`: tags, tldr, sources) and returns the remaining content with the block removed. `renderTagsPillsHTML()` renders a tag list as clickable pills linking to `/search?q=%23<tag>` (the `#tag` filter: exactly the pages and tasks with that tag, not every page using the word; a tag containing a space can't be written as `#tag` and keeps a plain word search) (styled via the same `.task-tag` class Deck uses); `renderFrontmatterHTML()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — and can leave out either half.
- `renderMd(rq, text, RenderOpts{…})` calls these internally, so most call sites (`/api/preview` live preview, revision view) get both tldr and tags rendered at the top of the article for free — those use the `<details>` form, since they have no meta row to place a toggle in.
- **`viewPage` is the exception**: it parses frontmatter itself and renders tags plus a tldr toggle in the `page-meta-wrapper` row below the H1 instead — together in `.page-meta-left` (tags pills then the `tldr` label, left-aligned), with "Last changed" right-aligned in `.page-meta-right`, followed, when the file is uncommitted, by a link-styled **Commit** (the `commit_form` template, inline, `.commit-link`) on the same line rather than a separate button row (`justify-content: space-between` on the wrapper). It renders with `RenderOpts{HideTags: true, HideTldr: true}` so nothing frontmatter-related is duplicated in the article body.
- The view-page tldr toggle is **not** a `<details>` — it's a visually-hidden checkbox (`.tldr-toggle`) emitted *before* `.page-meta-wrapper`, a `<label for="tldr-toggle">` *inside* the row, and the `<p class="page-tldr">` *after* the row. Keeping those three as siblings is what makes `.tldr-toggle:checked ~ .page-tldr { display: block }` work, so expanding reveals the text on its own full-width line below while the label stays put on the tags line. A `<details>` can't do this: its content is nested inside it, so making the text full-width drags the summary down with it. Collapsed by default, CSS-only (no JS), and the revealed text uses `.95rem` rather than inheriting the row's `.75rem` meta size. The open text sets only its top and bottom margins: from 1320px the side columns centre it like the row (`margin-inline: auto`), and a `margin` shorthand, being the more specific rule, would put it at the column's left edge instead.
- `.page-meta-wrapper` is a normal-flow block rendered as a sibling right after `</header>` (not nested inside it, not absolutely positioned) — so `.page-header`'s `border-bottom` stays anchored directly under the H1/actions row, while the meta row can still grow (e.g. an expanded tldr) and push the article below it down instead of overlapping it. It uses a small negative top margin (`margin: -1.1rem 0 1.5rem`) to sit close under the header despite being a separate block, rather than living inside the header's own margin.
- `stripH1()` skips over a leading frontmatter block before looking for the first non-empty line, so the H1 immediately after frontmatter is still correctly stripped (not duplicated below the page header).
- The **editor textarea always holds the raw, unparsed file** — including the frontmatter block verbatim — so editing it is just editing text like any other part of the file; there is no separate metadata-edit form. The live preview pane shows the parsed tldr/tags block (both, at the top of the article) as you type, via the same `renderMd()` path — it has no separate meta row to place them in.
- `pageTitle()` returns the first `# ` heading in the file, skipping a leading frontmatter block and fenced code (so a `# comment` in a code example is never the title), else a title derived from the slug.

### Version history

- Git is installed in the container image. On startup, `gitSetup()` initializes a bare repo in `LEAF_DATA` (if not already one), writes an empty `.gitignore`, and commits all existing files.
- Every write operation (Done/save, create, rename, delete, restore, upload) calls `gitCommit()` (a rename or move through `movePage()`, with the pages and deck files whose links changed) to stage and commit the change. **Auto-save (`/api/save`) is the exception** — it writes the file to disk but does not create a git commit. Assets are tracked like pages — a `git push` backs up everything.
- `cfg.GitEnabled` (git on the `PATH`) — all git paths are no-ops when git is absent (graceful degradation).
- git runs with `maintenance.autoDetach=false` and `gc.autoDetach=false` (`gitEnv`, also used by the tests' own git calls): git 2.47+ starts its automatic maintenance after a commit as a detached process, which nobody reaped with Leaf as the container's PID 1, so every commit left a zombie until the PID limit (2048) stopped everything (it also crashed test sessions). Not detached, git reaps its own children; `run.sh`'s `--init` covers anything else.
- SHA values are validated with `shaRE` (`^[0-9a-f]{7,40}$`) before use.
- History is in the page view's ⋯ menu (and the breadcrumb of a revision or diff links back to it).
- **`/history/<slug>`** lists the page's saved versions, newest first, one row each (`.hist-list`): date and time, what the save did in words (`versionLabel`: Leaf's own commit messages read alike, so "Update p" → Edited, "Rename a to b" → Renamed from “a” or, on a page whose link was updated, Link updated, "Restore p to <sha>" → Restored the version of <date>, also Created/Deleted/Imported; other messages as written) and the lines added/removed (`--numstat`). The row opens that version; the page as it is now is marked **Current** and opens the page itself (`isCurrent`: the newest commit equals the file on disk; with edits not committed yet a "Now · Not committed yet" row is the current one instead). **Changes** opens the diff.
- **`/revision/<slug>/<sha>`** shows that version with a bar (`.version-bar`) saying where it stands ("The version of 2026-10-08 14:32 · 2 versions back", "The current version", or a deleted page's last version), **What this save changed** (the diff), **Restore this version** (a plain button that asks first: `form[data-confirm]` in `js/git.js`; not offered for the current version) and **← Older / Newer →** (`versionIndex`; Newer from the version before the current one is **Current →**, the page). The breadcrumb names the version by its time, not its hash.
- **`/diff/<slug>/<sha>`** ("Changes") shows what that save changed, red taken out and green put in, without git's file headers (`diffLines` leaves out everything before a file's first `@@`; a hunk header becomes `⋯`), with **View this version** and the same ← Older / Newer →. No Restore there: a diff shows a change, not a version.
- **Renames**: `gitLog` (`--follow`) records each commit's path for the file (`path`, from `--numstat`, `numstatPath` reading `old => new` and `dir/{old => new}.md`), and revision, diff and restore read a version under the name it had then (`versionPath`); before, a version from before a rename was a 404. The diff of a rename passes both names (`gitDiff(sha, paths…)`, `-M`), so a pure rename shows no change instead of the whole page as new.

### Table of Contents

A page's contents are listed by the side column on big screens ("On this page", see Side columns in `docs/spec/routes.md`), built from its headings. The `[[toc]]` directive, a box of the same links inside the text, was removed in 2026-10 as unused next to it; a `[[toc]]` left in a page is an ordinary wikilink now (to a page named toc).

### Since the Go server (2026-10)

Leaf rendered with markdown-it until the server moved to Go; goldmark renders differently where no Leaf feature depends on it, and nothing a page relies on changed (checkboxes, callouts, heading ids, ✎ and ☑ chips, image widths, external links, task-date chips render as before). What reads differently:

- Footnote markers read `1`, not `[1]`; ids are `fn:1`, not `fn1`. Inline footnotes `^[…]` are plain text.
- `~one~` is struck through, as in GFM (before: only `~~two~~`).
- Tight and loose lists and definition lists follow goldmark's rules.
- Typography as before (curly quotes, `--` and `---` as dashes, `...` an ellipsis), but `(c)`, `(r)` and `(tm)` stay as written (before: ©, ®, ™).
- `[x](file:///…)` is a link (before: text). `javascript:`, `vbscript:` and non-image `data:` links are still text.
- A bare `mailto:…` or `xmpp:…` isn't made a link (an address alone, `me@x.org`, is).
