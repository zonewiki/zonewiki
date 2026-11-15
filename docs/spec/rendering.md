# Rendering

Part of Zone's spec (see `AGENTS.md` for the index).

## Rendering

- Raw HTML in a page is rendered as written (goldmark's `WithUnsafe`), but no script in it runs: the Content-Security-Policy allows only Zone's own script files (Scripts in pages don't run, `docs/spec/security.md`).
  Form controls, `<meta>` and `<base>` in it show as text (`defuseHTML`: a form in a page could post to Zone as the viewer).
  One goldmark set up with Zone's rules is built per kind (block, inline) and reused from a pool (`mdRenderer` in `go/markdown.go`), bound to one render's `Env` at a time;
  how headings carry their ids is the renderer's (`Env.HeadingIDs`: `id` on a page, `data-pid` in the search preview, none in task descriptions and comments), not a rewrite of its output.
  Wikilinks are an inline of goldmark's (`wikilinkParser`, before its links), written by the renderer through `Env.Wikilink` (`wikilinker` in `go/render.go`), so code spans, math and raw HTML (an attribute's `[[…]]`) keep theirs as text.
  A table splits its rows at `|` before inlines are parsed: `escapeWikilinkPipes` writes the `|` of a wikilink on a table's lines as `\|` (GFM's pipe in a cell) before the parse, and the parser reads it back; elsewhere it stays as written.
  An HTML block is raw to goldmark: its wikilinks are linked in its text between the tags (`wikilinksInHTML`), not in its attributes.
- `viewPage` (`go/routes.go`) reads `<slug>.md`, `renderMd()` (`go/render.go`) converts it to HTML with goldmark (`go/markdown.go`), and `page(…, "view.html", …)` renders it into the page (see Templates).
  A path with a slug in it is built with `pathURL()`, which percent-encodes it, so a file named with a quote, `#` or space still gets a working link that is safe in an attribute.
- The HTML shell includes: status badges, breadcrumb (with the module switcher), page header (title, Edit, the ⋯ page menu), content area, backlinks, the action bar, and the search/shortcut/new-note modals.
  There is no nav bar (see Modules).
- `highlight.js` (vendored, see Dependencies) auto-highlights fenced code blocks client-side after load; math and Mermaid diagrams render client-side too (Math and diagrams).
- **Task descriptions and comments** render with the same markdown (`renderNotes`, `POST /api/render-notes`): wikilinks, `[[task:…]]` chips, task-date chips, without what only a page has (frontmatter, `[[tasks:]]`, ✎ per section, heading ids).
  See Notes in `docs/spec/tasks.md`.
- The shared shell renders the product brand as `Zone` (page titles, the switcher's accessible name), with a small green inline SVG Z logo (`site-logo` in `templates/partials/icons.html`) on the breadcrumb's Wiki link.

### Autolinks and callouts

- **Bare URLs become links** (goldmark's linkify, with `urlRE`, which needs no dot in the host, so `http://localhost:5000/…` and `http://nas:5000` are links): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link.
  Code spans, fenced code and existing links are untouched.
  It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **External links open in a new tab**: a link to another site (http/https, not the host the request came in on) gets `target="_blank" rel="noopener noreferrer"` (`zoneTransformer.external`, on the parsed document, so autolinks too);
  `js/taskutil.js` does the same for links in task titles (`extAttrs`).
  Zone's own links, `mailto:` and a full URL to Zone itself stay in the tab.
- **Callouts** (GitHub/Obsidian syntax, `alertTransformer`): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line.
  Styled in `style.css`: GitHub's kinds in Zone's palette (slate, the accent green, plum, ochre, brick), a `--alert` variable per kind, dark variants.
  Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

### Sortable tables

Clicking a column header of a table in a page's article sorts its rows: ascending, descending, then back to the file's order (`sortTableBy` in `js/page.js`; Enter/Space on a focused header too).
View only: the file is never changed and nothing is remembered.
A column whose non-empty cells are all numbers (`tableCellNumber`: `-2`, `9.5`, `1,200`, `50 %`, `€3`) sorts numerically, any other column with a numeric-aware, case-insensitive collator (`item 9` before `item 10`, ISO dates in order).
Empty cells go last in both directions.
The state is the header's `aria-sort`, which the CSS turns into ▲/▼ (↕ on hover).
Only `article.page-content` tables with a header, one body, at least two rows and no merged cells (raw-HTML `colspan`/`rowspan`) are made sortable; the editor preview is not.
Clicks are delegated, so presentation slides (cloned from the article) sort too.

### Math and diagrams

Both render in the browser from vendored copies (`public/vendor/katex/`, `public/vendor/mermaid/`; see Dependencies in `AGENTS.md`), nothing from other hosts.
`js/figures.js` (`renderFigures`) loads a library the first time a page has something for it,
so a page without math or diagrams loads neither (Mermaid is 5.5 MB, 1.5 MB as it is sent: `/public/vendor/` scripts and styles go gzipped to a browser that takes it, gzipped once and kept in memory, and with `Cache-Control: public, max-age=86400`, since they change only with an update of Zone;
`servePublic`).
It runs on the page view (the article and the side TOC), the editor's preview, presentation slides, the search dialog's preview, and task descriptions and comments (in the live list after each draw, from its caches, so a library that failed to load is tried again then).
Without the library (an error, offline before the first load) the source shows as written.

- **Math** (KaTeX): `$…$` inline, `$$…$$` for display, and a ` ```math ` fence (display).
  The server (`go/math.go`, a goldmark extension) keeps the TeX as written,
  so `_`,
  `*`,
  `--` and quotes inside it are never emphasis or typography,
  and writes it escaped into `<span class="math math-inline">`,
  `<span class="math math-display">` (`$$…$$` in running text) or `<div class="math math-display">` (a `$$` block or a ` ```math ` fence);
  `renderFigures` renders those (the TeX stays in `data-tex`).
  KaTeX's stylesheet loads before the math renders, so it takes its height once.
  - Pandoc's rules, so prices stay text: the opening `$` has no space after it, the closing `$` none before it and no digit after it (`$5 and $10`, `$5–$10` are text), and inline math stays on one line (`mdtext.InlineMath`).
    A `$` followed by a letter does open math, so `$HOME/$USER` in running text is math: write it in backticks, or `\$`.
    `\$` is a literal dollar, also inside math.
  - A line of only `$$` (up to three spaces before it) opens a display block that runs to the next line ending in `$$` (or to the end of the page, as an unclosed fence does); `$$tex$$` alone on its line is one too; a line that only starts with `$$` (`$$100 bills`) is text (`mdtext.MathFence`).
  - In a table cell, `\|` is the cell's pipe as it is in code (`$\|x\|$` is |x|; KaTeX's double bar is `\Vert` there).
  - The source-side text functions agree with the parser: `mdtext.FencedLines` counts a `$$` block as a fence (and,
    like the parser,
    no ``` line with another backtick after it: ```` ```x``` y ```` is inline code; `opensCodeFence` in `js/mdtext.js`), so a `- [ ]` line in one is no checkbox (`js/mdtext.js` `taskCheckboxOffsets` and the editor's preview highlight do the same, `mdtext.MathFence`); `mdtext.MaskCode` masks inline math with a `[[` in it, so wikilinks, task references and `[[tasks:]]` in math stay TeX (math without one isn't masked, so math in a link's text stays in the link); `mdtext.PlainHeading` keeps a heading's math as its TeX (and its code as written), so `## Area $x_1$` is `#area-x_1` to the heading and to `[[Page#Area $x_1$]]` alike.
  - Bad TeX shows as written in KaTeX's error colour, its message as the title.
    One paragraph's inline content (comments) has inline math only.
- **Diagrams** (Mermaid): a ` ```mermaid ` fence becomes `<div class="mermaid-diagram">` with the SVG (`securityLevel: 'strict'`: no scripts or click handlers from a diagram's text), in Mermaid's `dark` theme when the browser prefers dark.
  A diagram that doesn't parse keeps its source, with a `.mermaid-error` line under it (`Diagram: <Mermaid's message>`).
  The SVGs are kept per source and theme (the last 50), so the editor's preview, redrawn as you type, doesn't flash the source.
- **What you were sent to stays in view**: a figure rendering above a page's `#section` or its current search highlight (or the search preview's target) makes the text there taller; `renderFigures` scrolls by as much, when the target was in view, so it stays where it was (`keepInPlace`).
- highlight.js leaves Mermaid fences alone (`HIGHLIGHT_CODE`), and search highlights skip `.math` and `.mermaid-diagram`.

### Templates

Every view is a Go `html/template` in `go/templates` (embedded in the binary) that fills the blocks of `layout.html` (head, the dialogs the client code opens, the status badges, top actions and action bar), rendered by `page(w, rq, status, template, title, …)` (`go/views.go`).
Templates escape by context; their functions (`funcs`) include `url` (`pathURL`) and `dict` (a template's arguments).
HTML that Zone built itself is passed as `template.HTML` (rendered markdown, an excerpt with `<mark>`s), anything else is escaped.
Reusable parts are named templates in `templates/partials/` (`page.html`: the ⋯ menu and dialogs, link sections, side columns, meta row; `history.html`; `modules.html`; `icons.html`), which Go code renders with `macro(name, data)` (the breadcrumb, the task list embed).
Markdown-level fragments (task chips, ☑ chips) stay in `go/render.go`.

### Client code

No build step and no bundler: the browser loads ES modules as they are.
Every page loads `public/zonewiki.js` (`<script type="module">` in `layout.html`), which imports the modules in `public/js/` and `tasks.js`; each module wires up what it finds on the page.
They share code by `import`, not through `window`:

| Module | Does |
|---|---|
| `js/util.js` | Small shared helpers: `he`, `localDate`, `pasteUrlAsLink`, `continueList`, `showToast`, `putTask`, … |
| `js/mdtext.js` | The text functions the browser shares with the server, each the copy of a Go one: `toSlug`, `headingId`, `splitWikilink`/`wikilinkHref`, `queryWords`, `mathFence`, `opensCodeFence`, `taskCheckboxOffsets`, `toggleCheckboxText`. No DOM and no imports, so Node runs them: `tests/test_text_parity.py` holds them to the answers the Go functions are held to (`go/testdata/golden.json.gz`) |
| `js/completion.js` | `linkAutocomplete`: `[[`, `[[task:` and `[text](` completion (editor and the task app's task fields) |
| `js/chrome.js` | Around the page: navigation trail, backlinks chip, side columns, the Tasks links' counts (`refreshDueBadge`), the user menu (with users) |
| `js/git.js` | Uncommitted badge, Commit buttons, forms that confirm first (`data-confirm`) |
| `js/search.js` | Search modal and its preview, Notes filter, highlights on a page opened from search |
| `js/actions.js` | New note / page / task (`openDefaultTask`, loading a hidden tasks when the page has none), action bar, sharing mode |
| `js/tasklist.js` | The editor's Task list button (`taskListDialog`): the list settings dialog (`js/tasksettings.js`) writing or changing a `[[tasks: …]]` in the text |
| `js/pagemenu.js` | The page's ⋯ menu and its dialogs (rename, move to wiki, append, delete) |
| `js/listsrow.js` | The Tasks page's row of lists: More (closing, its filter, lists new to this browser) and dragging the pinned pills into another order; draws the row again when a list is made on the page (`zone:list-made`) |
| `js/shortcuts.js` | Keyboard shortcuts |
| `js/mdedit.js` | `markdownEditor`: the markdown helpers of the editor and the task app's task description (formatting bar, completion, snippets, lists, uploads) |
| `js/editor.js` | The editor: auto-save, preview, Enter on a wikilink, Make task, rename bar |
| `js/page.js` | Page view: checkboxes, `[[task:…]]` chips, sortable tables, syntax highlighting, lightbox |
| `js/figures.js` | `renderFigures`: math (KaTeX) and Mermaid diagrams, loading each library on first use (Math and diagrams) |
| `js/fold.js` | Folding a page view's sections (`foldAll`, `revealElement`) |
| `js/present.js` | Presentation mode |
| `js/admin.js` | The admin page's Remove dialog (Managing users in `users.md`) |
| `js/copyline.js` | Lines to copy (`.copy-line`: Connect an assistant's address, token and commands): shows each one's Copy button and copies its text (`copyText`) |
| `js/taskutil.js` | What `tasks.js`'s task apps use that holds no state of an app's own: date formatting and classes (`formatDate`, `shortDate`, `dueDateClass`, `urgencyClass`, `todayStr`), `DATES`/`LOCALE`, notices and the toast's place, `esc`, the tag autocomplete (`attachTagAc`) and `#` words at the caret, links in a title (`linkifyTitle`, `plainTitle`), comment stamps |
| `js/tasktexts.js` | Task descriptions and comments rendered as pages are, for every task app on the page: the HTML the server made of each text (`POST /api/render-notes`), kept per text and fetched once (`renderTexts`, `renderedHtml`, `textsOf`) |
| `tasks.js`, `tasks-core.js` | The task app (exports `initTaskApps`, `openTasksEdit`): `initTasksInstance` puts one together (its elements, the state its parts share, what the embed and the address say it shows, the list's own listeners) and starts its parts; and its pure logic (`TasksCore`, also run by the Node tests: dates, orders, the filter, what a tab shows, the Plan board's sections, the quick-add shorthand) |
| `js/taskdata.js`, `js/taskview.js`, `js/taskorder.js`, `js/tasktabs.js`, `js/taskchips.js`, `js/taskrender.js`, `js/taskmodal.js`, `js/taskselect.js`, `js/taskmenu.js`, `js/taskkeys.js`, `js/taskdnd.js`, `js/tasktouch.js`, `js/tasksettings.js` | The task app's parts, each `initTask…(app)`, called by `initTasksInstance` for each task app: the tasks read and written through `/api/tasks` with the Undo toast (`taskdata`), how the list is shown as the browser remembers it (folds, sort, Plan on or off) and a line's expansion (`taskview`), the manual order and its sections (`taskorder`), the tabs and the filter field (`tasktabs`), tags typed as chips (`taskchips`), drawing the list (`render` in `taskrender`: one function per kind of view, the cards' handlers wired after), the edit modal, selection and the selection bar with bulk tags, the ⋯ menus and confirmations, the keys, drag and drop with the mouse, touch gestures, the settings dialog. `tests/test_task_app_wiring.py` reads the sources: every `app.name` a part uses is put on `app` by `tasks.js` or returned by a part, and by one of them only. `app` holds what a part uses of the app (its elements and functions, as getters what is set later or changes), `app.st` the state the parts share (`st.tasks`, `st.activeView`, `st.selectedIds` …); what one part alone uses stays its own variable. A part's functions go back into `app` for the others |
| `js/datepicker.js` | The task app's calendar popup (`openDatePicker`) and `swallowNextClick` |

### Section headings and HTML post-processing

Zone's additions to goldmark's output work on the parsed document,
not by regexes over the HTML (`go/markdown.go`): the `heading` render function wraps each `##`/`###` directly in the page (not one in a quote, a list or a footnote: `sectionHeading`) in `.section-header` with its ✎ link when `renderMd` gets `EditSlug` (the page view;
numbered as `pageSections` in `go/sections.go` cuts sections, from the same parser),
and adds the `☑ N` chip from `SectionTasks` to any `##`/`###`;
`taskDates` turns the task-date comments (`<!--t:… d:…-->`, written by `mdtext.ToggleNthCheckbox`) into chips;
`zoneTransformer` handles image widths and `img-block` paragraphs;
`taskTransformer` gives exactly the lines `mdtext.ToggleNthCheckbox` counts a checkbox.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:
- An `<h1>` with the page title (derived from the first `# Heading` in the file, or the slug as a fallback)
- An Edit button linking to `/edit/<slug>`
- A **⋯ page menu** (`.page-menu-btn`, menu `#page-menu`, `page_menu` in `templates/partials/page.html`, its data from `pageMenu`; opening, keys and actions in `js/pagemenu.js`) with the rarer actions: **Present** (`#present-btn`, only on a page with headings, `p`),
  **History** (git only),
  **Print…** (see Print and PDF),
  **Move to wiki…** and **Append to page…** (notes only, see Notes),
  **Rename…** (a dialog, `#page-rename-dialog`, see Rename) and **Delete…** (the `#page-delete-dialog` confirmation, also Ctrl+Delete);
  Rename… and Delete… not for `index`.
  One hidden menu element, positioned under the ⋯ button: a popover with ↑/↓, Esc (focus back to the ⋯), a bottom sheet at ≤640px, styled like the task app's ⋯ menu (`.task-menu` rules list `.page-menu` too).
  It sits outside `.page-grid`, since the middle column is a size container and would anchor `position: fixed`.

### Print and PDF

A page is printed, or saved as a PDF, by the browser: **Print…** in the page's ⋯ menu opens the browser's print dialog, where a printer prints it and "Save as PDF" (the destination, in Chrome, Edge, Firefox and Safari alike) makes the file; Ctrl+P does the same.
Zone makes no PDF itself: no renderer on the server, and the file looks as the page does.

- **What is on the paper** (`@media print` at the end of `style.css`): the page alone, its title, tags, "Last changed", the tldr (shown, not behind its switch) and the text, at the paper's width (`@page` margins 18mm and 16mm).
  Not Zone around it: the breadcrumb, the top row and the phone's bar, the side columns, Edit and ⋯, the section ✎ and task chips, what links to the page, the build string; a task list embed shows its tasks without its tabs, toolbar and menus.
  The sheet's border and padding go (the paper is the sheet).
- **Always light**: the dark variables and dark rules are for screens (`@media screen and (prefers-color-scheme: dark)`), and code is highlighted with the light theme (`media="print, …"` on its stylesheet in `layout.html`), so a dark screen prints dark ink on white.
- **Page breaks**: none right after a heading; code blocks, quotes, callouts, images, diagrams and table rows are kept whole where they fit; code wraps instead of being cut off where the screen would scroll.
- **Folded sections** print unfolded (`beforeprint`, see Folding sections).
- **Print…** (`printPage` in `js/pagemenu.js`) does two things Ctrl+P can't:
  the tab's title is the page's title while the dialog is open, so the file is named "Garden plan.pdf", not "Garden plan — Zone.pdf";
  and on a dark screen the Mermaid diagrams are drawn again in the light theme first (`diagramsForPaper` in `js/figures.js`), and in the dark one after (`afterprint`).
  Shortcut hints (`.menu-key`) hide on touch screens.

**Copy link**: on a page view a link icon (`.heading-link`, added by `js/page.js`) sits by the title and by every heading with an anchor (`#`, `##`, `###` in the article; not a task list embed's), hidden until the heading is hovered (or the icon focused).
It sits after the heading's text.
A click copies the page's URL (title) or the section's URL with its `#anchor` (heading, which also puts the anchor in the address bar), confirmed by a "Link copied" toast and a ✓ in the icon's place for a moment.
The icon is sized in `em`, so it scales with the heading (at least 14px), and centred on the heading's first line.
Presentation mode hides it.

**Folding sections** (`js/fold.js`): on a page view every `##` and `###` of the article with something under it (not a task list embed's, not one in a quote or list) has a chevron (`.section-fold`, a button inside the heading, `aria-expanded`).
A click folds the section: everything after its `.section-header` up to the next heading of the same or a higher level (a `##` up to the next `##` or `#`, so a folded `##` hides its `###`s too; a `###` up to the next heading).
Alt+click folds or unfolds every section (to the clicked one's new state); **z** folds every section, or unfolds them all when all are folded.
A `###` folded inside a `##` stays folded when the `##` opens again.
A folded section's heading turns its chevron to › and has a wavy line in place of its straight rule (under the `##`'s header, under the `###` itself, which is then at least 8rem wide),
with **folded** in its middle (`.section-folded-label`, a button; on touch screens a larger one): a click on it unfolds the section.
Search highlights skip that word.
View only: the file is never changed and nothing is remembered, so every page opens unfolded.
The chevron hangs in the margin left of the heading from 800px wide (the copy-link icon stays after the text), shown when the heading is hovered and while folded; on narrower screens it sits after the heading's text, before the copy-link icon, always shown, larger on touch screens.
- Folded content is `hidden="until-found"` (marked `data-fold-hidden`; CSS drops its margins, padding and borders, which `until-found` would keep), so the browser's find in page still finds it and unfolds it (`beforematch`); browsers without `until-found` hide it outright.
- What points into a folded section unfolds it (`revealElement`: the sections holding the element, and the section a heading starts): a click on an anchor link to this page (the side TOC, a link in the text; not a heading's copy-link icon, so copying a folded section's link leaves it folded),
  a `hashchange`,
  a search highlight that **n**/**N** step to. **]**/**[** skip headings inside a folded section;
  the side TOC doesn't mark one.
- Printing (`beforeprint`) shows every section and restores the folds after; presentation mode's slides show folded content and no chevrons.

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body.
Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before rendering (`mdtext.StripH1`).

### Wikilinks

- Wikilinks use the `[[Page Name]]` syntax.
  Nested pages can be linked as `[[journal/my-page]]`.
- **Sections**: `[[Page#Section]]` links to a heading on that page (`/page/page#section`,
  the id as the heading renders,
  `mdtext.SectionAnchor`: the heading's text without markdown and links,
  its code and math kept as written (`mdtext.PlainHeading`),
  so ``## Run `snake_case` now`` is `#run-snake_case-now` to both;
  a section with code in a link is read before the code is masked),
  labelled "Page › Section";
  `[[Page#Section|Label]]` with a label;
  `[[#Section]]` a heading on the same page.
  `mdtext.SplitWikilink` parses all forms (server), `wikilinkHref` in `js/mdtext.js` the same for the editor's Enter-on-a-wikilink (task descriptions render on the server, `renderNotes`).
  The section is ignored for backlinks and kept on rename.
- Wikilinks are parsed with the markdown (`wikilinkParser`, an inline like a link; one line, no bracket inside) and written as links (`wikilinker`).
  A label renders as a link's text does (`**bold**`, code and math in it render; HTML in it is text); a heading's id has the label's text, as `[[Page#Section]]` makes it.
  - `[[Page Name]]` → `<a href="/page/page-name" class="wikilink">Page Name</a>` (slug = lowercase, spaces → hyphens)
  - `[[Page Name|Label]]` → a link labelled `Label`
- If the target page does not exist, render the link with a CSS class `wikilink-missing` so it can be styled differently (e.g. red or dashed underline).
  Clicking a missing wikilink redirects to `/edit/<slug>` so the page can be created immediately.
  (With users, on a private page a link to a new page that would be shared is marked and warns in the editor: see Private pages in `docs/spec/users.md`.)
- `/api/preview` renders them the same way, so the live editor preview shows wikilinks as the page does.
- Code is exempt: wikilinks in code (and math, and raw HTML) are the parser's, so they stay as written.
  `renderMd()` masks fenced code blocks and inline code spans (`mdtext.MaskCode`, private-use-character tokens) before running the `[[tasks:]]` and `[[task:…]]` pre-processors and restores them (`mdtext.UnmaskCode`) before markdown, so directives inside code render literally.
  A page's own text that looks like such a token (`\ue000N\ue001`, or `\ue002N\ue003` for task references) is masked too and comes back as written, so it can't fail a render or swap in another code span, nor be written into a page by a rename's link rewrite.
  The TOC also ignores `##` lines inside fences.
- Add a route `GET /api/pages` that returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

### Task references

`[[task:<id>]]` in a page shows one task inline as a live chip.
`<id>` is the first 8 characters of the task's id (`shortIDLen`, like a short git hash) or the whole id, case ignored (`taskRefRE`).
Nobody types ids: in the editor, `[[task:` (or the formatting bar's task button) opens a completion over open tasks (title words or id prefix, newest first, with list and deadline; `taskAcMatches` in `js/completion.js`, tasks from `/api/tasks`, refetched after a minute) that inserts the reference.

- **Rendering** (`maskTaskRefs`/`injectTaskRefs`, chip from `taskRefHTML`): masked with private-use tokens (`\ue002N\ue003`) after code masking, so code keeps them literal and markdown never touches the chip, then replaced after markdown.
  The chip: a checkbox,
  the title (links flattened, escaped) linking to the task in the tasks (`taskHref`, list and archive year included),
  and badges: ⏰ deadline (red overdue, green today),
  📅 start when today or later (amber today, blue later),
  ↻ repeats,
  ⟲ keep (with "✓ <last done>"),
  the list's name;
  done tasks struck through with "✓ <date>" (each date a `<time class="local-date">` in US English, which the browser rewrites in the `locale` setting; with `date-format = iso` `YYYY-MM-DD` from the server, see Dates in `docs/spec/tasks.md`), won't-do with "✗ won't do" and a disabled checkbox.
  Unknown id: a red dashed `task <id>?` (`.task-ref-missing`).
  A reference alone in a list item makes that `<li class="task-ref-item">`, a checklist row without the chip's box.
- **Resolving** (`resolveTaskRefs`): open tasks win over closed ones with the same prefix; archives count.
  A completed recurring task stands for its series: the reference follows `spawned_from` to the current occurrence, so ticking it and reloading shows the next one.
- **Ticking** the chip's checkbox (`js/page.js`, page view and editor preview) PUTs `{ completed }` to `/api/tasks/<id>`,
  so repeating and keep tasks behave as in the tasks,
  then re-renders the chip from `/api/preview` of `[[task:<full id>]]` (one renderer: a repeating task shows its next occurrence, a keep task its ✓).
  Undo toast: `completed: !done` plus the task's old `history` (no trace), or for a keep task its old `start_date`, `due_date`, `last_done` and `history` (fetched from `/api/tasks` before the change).
- **Linked tasks** leaves out tasks the page shows inline (`pageTaskRefs`).
- **Search** indexes a reference as its task's title (`taskRefsAsTitles`, also in `semanticText`); a page with references is re-indexed when the task files change (the page doc cache keeps the task files' mtimes).
  Embeddings and MCP section docs are cached by page mtime, so they catch up with a renamed task when the page changes.
- **MCP `read_page`** keeps the raw markdown and appends what each reference stands for (title, status, dates, link; `mcpTaskRefs`).
- **Make task** (formatting bar, `makeTasks` in `js/editor.js`): turns text in the editor into tasks and replaces it with their references, e.g. action items in meeting notes.
  A selection within one line becomes one task and an inline reference.
  Otherwise each non-empty line the selection touches (or the cursor's line) becomes a task, keeping its list marker and dropping a checklist's `[ ]` (`- [ ] Call Bob` → `- [[task:…]]`); blank lines, headings and lines that are already a reference stay.
  Each task's description says `From [[<page>]]`.
  - **One item opens the task app's edit modal** to review it first: the editor page carries a hidden task app (`buildTasksEmbed(…, headless)`: `data-headless`, `display:none`; `tasks.js` then ignores the task app's keyboard shortcuts and the "moved to another list" notice),
    and `openTasksEdit(null, { title, description, start_date: null, onCreated })` (exported by `tasks.js`) opens it with the text as title (its quick-add preview shows the shorthand) and no start date.
    Save (Enter) creates the task and `onCreated` puts the reference in; Cancel/Esc changes nothing.
    The same modal serves `c t` in the editor (a task in the Inbox).
  - **Several lines** open the same modal in batch mode (`openTasksEdit(null, { titles, … })`, `setBatch` in `js/taskmodal.js`): the titles are listed instead of the title field (each with its shorthand preview),
    focus starts in Tags,
    and the fields (notes, tags, dates, repeat, keep) apply to every task.
    A line's own shorthand adds to them: its tags join the shared ones, its dates win over the shared ones.
    Save creates them one by one (`createTask`) and `onCreated` gets them all; if one fails, or the text changed meanwhile, the created ones are deleted again and nothing is replaced.
    Should the task app be missing, the editor creates them right away with `TasksCore.parseQuickAdd`.
  - The undo toast (both ways) deletes the tasks (`batch-delete`) and puts the text back.

### Image sizing

- `![alt|300](url)` sets the display width to 300px: `zoneTransformer` takes `|300` off the alt text and gives the image `style="max-width:100%;width:300px"`.
- Works for any integer pixel width.
- An image on another host isn't loaded: `![alt](https://elsewhere.example/x.png)` is a link to it (`a.external-image`, a 🖼 before its alt text, or the address without one), unless the `external-content` setting names the host (A page loads nothing from other hosts in `docs/spec/security.md`).
  `max-width:100%` ensures the image never overflows on narrow screens.

### Frontmatter

- A page may start with a leading `---`-fenced block declaring `tags:`, `tldr:` (a short summary, quotes optional) and `sources:` (where the page's facts come from).
  Any other key is parsed and ignored.
  Dates aren't fields: the page's history has them ("Last changed", and its revisions).
- `tldr:` may run over several lines,
  as YAML writes them: `tldr: >` (folded: the lines join with spaces, a blank line is a line break),
  `tldr: |` (literal: the lines as written, their common indent removed),
  either with a chomping sign or indent (`>-`, `|2`, ignored),
  or a plain value going on in indented lines below it (folded).
  Everything indented after `tldr:` is the tldr's, `- item` and `key: value` lines too.
  `.page-tldr` has `white-space: pre-line`, so the line breaks show.
- `tags:` and `sources:` are lists: comma-separated (`a, b`), bracketed (`[a, b]`), or a YAML block list, one `- item` line each under the key (quotes optional).
  A comma inside `[…]` or `(…)` doesn't split a value, so `[Doc, v2](url)` and `[[page|A, B]]` stay whole; a lone `[[page]]` is a wikilink, not a bracketed list.
  `mdtext.AddFrontmatterTags` (Append to page) rewrites a block list of tags as one `tags: a, b, c` line.
- **Sources** render at the article's end (`renderSourcesHTML`, `<section class="page-sources">`, a "Sources" list under an `<h2 id="sources">`, `sources-1` … when the page has a heading of that name; styled small),
  in the page view,
  the live preview and revisions alike,
  each one inline markdown: a bare URL,
  `[label](url)` or `[[page]]` is a link.
  Being in the file, a `[[page]]` source is a link like any other: it counts for backlinks, and a rename rewrites it.
  Being an `<h2>`, it is in the side TOC (counting towards its minimum of headings) and gets the heading's copy-link icon and `]`/`[` like any section; in presentation mode it stays on the last slide (a slide starts at a heading directly in the article).
- `mdtext.ParseFrontmatter()` extracts it (`Frontmatter`: tags, tldr, sources) and returns the remaining content with the block removed.
  `renderTagsPillsHTML()` renders a tag list as clickable pills linking to `/search?q=%23<tag>` (the `#tag` filter: exactly the pages and tasks with that tag, not every page using the word;
  a tag containing a space can't be written as `#tag` and keeps a plain word search) (styled via the same `.task-tag` class Tasks uses);
  `renderFrontmatterHTML()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — and can leave out either half.
- `renderMd(rq, text, RenderOpts{…})` calls these internally, so most call sites (`/api/preview` live preview, revision view) get both tldr and tags rendered at the top of the article for free — those use the `<details>` form, since they have no meta row to place a toggle in.
- **`viewPage` is the exception**: it parses frontmatter itself and renders tags plus a tldr toggle in the `page-meta-wrapper` row below the H1 instead — together in `.page-meta-left` (tags pills then the `tldr` label, left-aligned),
  with "Last changed" right-aligned in `.page-meta-right`,
  followed,
  when the file is uncommitted,
  by a link-styled **Commit** (the `commit_form` template, inline, `.commit-link`) on the same line rather than a separate button row (`justify-content: space-between` on the wrapper).
  What git says of the page for that line (`lastChanged` in `go/views.go`: changes not committed, or the last commit's day and time, shown as "3 days ago" in Zone's own words) is kept per page while neither the page's file nor the repository changed (`changeStamp`: the file's time and size, and those of `.git/logs/HEAD` and `.git/index`, which Zone's commits write and so do a commit, pull or checkout in a terminal),
  so a view of a page seen before starts no git process;
  a repository without a reflog is asked at every view.
  It renders with `RenderOpts{HideTags: true, HideTldr: true}` so nothing frontmatter-related is duplicated in the article body.
- The view-page tldr toggle is **not** a `<details>` — it's a visually-hidden checkbox (`.tldr-toggle`) emitted *before* `.page-meta-wrapper`, a `<label for="tldr-toggle">` *inside* the row, and the `<p class="page-tldr">` *after* the row.
  Keeping those three as siblings is what makes `.tldr-toggle:checked ~ .page-tldr { display: block }` work, so expanding reveals the text on its own full-width line below while the label stays put on the tags line.
  A `<details>` can't do this: its content is nested inside it, so making the text full-width drags the summary down with it.
  Collapsed by default, CSS-only (no JS), and the revealed text uses `.95rem` rather than inheriting the row's `.75rem` meta size.
  The open text sets only its top and bottom margins: from 1320px the side columns centre it like the row (`margin-inline: auto`), and a `margin` shorthand, being the more specific rule, would put it at the column's left edge instead.
- `.page-meta-wrapper` is a normal-flow block rendered as a sibling right after `</header>` (not nested inside it, not absolutely positioned) — so `.page-header`'s `border-bottom` stays anchored directly under the H1/actions row,
  while the meta row can still grow (e.g. an expanded tldr) and push the article below it down instead of overlapping it.
  It uses a small negative top margin (`margin: -1.1rem 0 1.5rem`) to sit close under the header despite being a separate block, rather than living inside the header's own margin.
- `mdtext.StripH1()` skips over a leading frontmatter block before looking for the first non-empty line, so the H1 immediately after frontmatter is still correctly stripped (not duplicated below the page header).
- The **editor textarea always holds the raw, unparsed file** — including the frontmatter block verbatim — so editing it is just editing text like any other part of the file; there is no separate metadata-edit form.
  The live preview pane shows the parsed tldr/tags block (both, at the top of the article) as you type, via the same `renderMd()` path — it has no separate meta row to place them in.
- `mdtext.PageTitle()` returns the first `# ` heading in the file, skipping a leading frontmatter block and fenced code (so a `# comment` in a code example is never the title), else a title derived from the slug.

### Version history

- Git is installed in the container image.
  On startup, `gitSetup()` initializes a bare repo in `ZONE_DATA` (if not already one), writes an empty `.gitignore`, and commits all existing files.
- Every write operation (Done/save, create, rename, delete, restore, upload) calls `gitCommit()` (a rename or move through `movePage()`, with the pages and task files whose links changed) to stage and commit the change: only those paths (`git commit -- paths`),
  so what else is staged (a `git add` by hand) stays out of Zone's commit.
  A commit that fails (a stale `index.lock`, a refusing hook) is logged and reported: the route answers 500 (Commit file and Commit all with `{"error": …}`), an MCP tool with a tool error, "the change is written but not committed";
  the file stays in the Uncommitted list, its draft's author still known.
  A commit that has to come first (text not yet in git, `keepPending`; another user's draft) and fails stops the write, so that text is never lost.
  Pages are written atomically (a temp file renamed into place, the file's permissions kept), and writes to pages run one at a time (`pagesMu`, from the reads a write decides on to its commit: two writes at once, say an assistant's `append_to_page` and a ticked checkbox, both keep their change).
  **Auto-save (`/api/save`) is the exception** — it writes the file to disk but does not create a git commit.
  Assets are tracked like pages — a `git push` backs up everything.
- `cfg.GitEnabled` (git on the `PATH`) — all git paths are no-ops when git is absent (graceful degradation).
- git runs with `maintenance.autoDetach=false` and `gc.autoDetach=false` (package `gitrepo`'s environment; the tests' own git calls set the same, `settings.GIT_ENV`): git 2.47+ starts its automatic maintenance after a commit as a detached process,
  which nobody reaped with Zone as the container's PID 1,
  so every commit left a zombie until the PID limit (2048) stopped everything (it also crashed test sessions).
  Not detached, git reaps its own children; `run.sh`'s `--init` covers anything else.
- SHA values are validated with `shaRE` (`^[0-9a-f]{7,40}$`) before use.
- History is in the page view's ⋯ menu (and the breadcrumb of a revision or diff links back to it).
- **`/history/<slug>`** lists the page's saved versions,
  newest first,
  one row each (`.hist-list`): date and time,
  what the save did in words (`versionLabel`: Zone's own commit messages read alike, so "Update p" → Edited, "Rename a to b" → Renamed from “a” or, on a page whose link was updated, Link updated, "Restore p to <sha>" → Restored the version of <date>, also Created/Deleted/Imported;
  other messages as written) and the lines added/removed (`--numstat`).
  The row opens that version; the page as it is now is marked **Current** and opens the page itself (`isCurrent`: the newest commit equals the file on disk; with edits not committed yet a "Now · Not committed yet" row is the current one instead).
  **Changes** opens the diff.
- **`/revision/<slug>/<sha>`** shows that version with a bar (`.version-bar`) saying where it stands ("The version of 2026-10-08 14:32 · 2 versions back", "The current version", or a deleted page's last version),
  **What this save changed** (the diff),
  **Restore this version** (a plain button that asks first: `form[data-confirm]` in `js/git.js`; not offered for the current version) and **← Older / Newer →** (`versionIndex`; Newer from the version before the current one is **Current →**, the page).
  The breadcrumb names the version by its time, not its hash.
- **`/diff/<slug>/<sha>`** ("Changes") shows what that save changed, red taken out and green put in, without git's file headers (`diffLines` leaves out everything before a file's first `@@`; a hunk header becomes `⋯`), with **View this version** and the same ← Older / Newer →.
  No Restore there: a diff shows a change, not a version.
- **Renames**: `gitLog` (`--follow`) records each commit's path for the file (`path`, from `--numstat`, `numstatPath` reading `old => new` and `dir/{old => new}.md`), and revision, diff and restore read a version under the name it had then (`versionPath`);
  before, a version from before a rename was a 404. The diff of a rename passes both names (`gitDiff(sha, paths…)`, `-M`), so a pure rename shows no change instead of the whole page as new.

### Table of Contents

A page's contents are listed by the side column on big screens ("On this page", see Side columns in `docs/spec/routes.md`), built from its headings.
The `[[toc]]` directive, a box of the same links inside the text, was removed in 2026-10 as unused next to it; a `[[toc]]` left in a page is an ordinary wikilink now (to a page named toc).

### Since the Go server (2026-10)

Zone rendered with markdown-it until the server moved to Go; goldmark renders differently where no Zone feature depends on it, and nothing a page relies on changed (checkboxes, callouts, heading ids, ✎ and ☑ chips, image widths, external links, task-date chips render as before).
What reads differently:

- Footnote markers read `1`, not `[1]`; ids are `fn:1`, not `fn1`.
  Inline footnotes `^[…]` are plain text.
- `~one~` is struck through, as in GFM (before: only `~~two~~`).
- Tight and loose lists and definition lists follow goldmark's rules.
- Typography as before (curly quotes, `--` and `---` as dashes, `...` an ellipsis), but `(c)`, `(r)` and `(tm)` stay as written (before: ©, ®, ™).
- `[x](file:///…)` is a link (before: text).
  `javascript:`, `vbscript:` and non-image `data:` links are still text.
- A bare `mailto:…` or `xmpp:…` isn't made a link (an address alone, `me@x.org`, is).
