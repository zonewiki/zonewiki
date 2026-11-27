# Rendering

Part of Zonewiki's spec (see `AGENTS.md` for the index).

How a page looks and what its markdown can say, first: the page view (its header, menu, copy links, folding, printing), Zonewiki's markdown (raw HTML, autolinks, callouts, images), wikilinks, task references, frontmatter, math and diagrams, sortable tables, and a page's version history.
How it is built comes after: how a page is rendered on the server (goldmark and Zonewiki's rules, heading ids, templates) and the browser's code (Client code).

## The page view

`/page/<slug>` shows a page rendered from its markdown file (the route: `viewPage`, How a page is rendered, below).

### The page shell

The HTML shell includes: the notice line across the top when the settings file has one (The notice in `docs/spec/users.md`), status badges, breadcrumb (with the module switcher), page header (title, Edit, the ⋯ page menu), content area, backlinks, the action bar, and the search/shortcut/new-note modals.
There is no nav bar (see Modules in `docs/spec/navigation.md`).

The shared shell renders the product brand as `Zonewiki` (page titles, the switcher's accessible name), with a small green inline SVG logo, the open frame (`site-logo` in `templates/partials/icons.html`) on the breadcrumb's Wiki link.

### Page header

Every rendered page displays a `<header class="page-header">` above the article content containing:

- An `<h1>` with the page title (see The title, below)
- An Edit button linking to `/edit/<slug>`
- A **⋯ page menu** with the rarer actions (The page menu, below)

### The page menu

The ⋯ button (`.page-menu-btn`) opens the menu `#page-menu` (`page_menu` in `templates/partials/page.html`, its data from `pageMenu`; opening, keys and actions in `js/pagemenu.js`):

| Entry | Shown | Does |
|---|---|---|
| **Present** | only on a page with headings | presentation mode (`#present-btn`, `p`) |
| **History** | git only | the page's history (Version history) |
| **Print…** | always | the browser's print dialog (see Print and PDF) |
| **Move to wiki…**, **Append to page…** | notes only | see Notes in `docs/spec/navigation.md` |
| **Rename…** | not for `index` | a dialog, `#page-rename-dialog` (see Rename in `docs/spec/editor.md`) |
| **Delete…** | not for `index` | the `#page-delete-dialog` confirmation, also Ctrl+Delete |

One hidden menu element, positioned under the ⋯ button: a popover with ↑/↓, Esc (focus back to the ⋯), a bottom sheet at ≤640px, styled like the task app's ⋯ menu (`.task-menu` rules list `.page-menu` too).
It sits outside `.page-grid`, since the middle column is a size container and would anchor `position: fixed`.
Shortcut hints (`.menu-key`) hide on touch screens.

### The title

The title is the first `# Heading` in the file, or the slug as a fallback:
`mdtext.PageTitle()` returns the first `# ` heading in the file, skipping a leading frontmatter block and fenced code (so a `# comment` in a code example is never the title), else a title derived from the slug.

The `# H1` line is **stripped from the markdown** before rendering so the title is not duplicated in the body.
Use a simple approach: if the first non-empty line of the file matches `^#\s+`, remove it before rendering (`mdtext.StripH1`).
`mdtext.StripH1()` skips over a leading frontmatter block before looking for the first non-empty line, so the H1 immediately after frontmatter is still correctly stripped (not duplicated below the page header).

### Copy link

On a page view a link icon (`.heading-link`, added by `js/page.js`) sits by the title and by every heading with an anchor (`#`, `##`, `###` in the article; not a task list embed's), hidden until the heading is hovered (or the icon focused).
It sits after the heading's text.
A click copies the page's URL (title) or the section's URL with its `#anchor` (heading, which also puts the anchor in the address bar), confirmed by a "Link copied" toast and a ✓ in the icon's place for a moment.
The icon is sized in `em`, so it scales with the heading (at least 14px), and centred on the heading's first line.
Presentation mode hides it.

### Folding sections

On a page view every `##` and `###` of the article with something under it (not a task list embed's, not one in a quote or list) has a chevron (`.section-fold`, a button inside the heading, `aria-expanded`; `js/fold.js`).

- A click folds the section: everything after its `.section-header` up to the next heading of the same or a higher level (a `##` up to the next `##` or `#`, so a folded `##` hides its `###`s too; a `###` up to the next heading).
- Alt+click folds or unfolds every section (to the clicked one's new state); **z** folds every section, or unfolds them all when all are folded.
- A `###` folded inside a `##` stays folded when the `##` opens again.

A folded section's heading turns its chevron to › and has a wavy line in place of its straight rule (under the `##`'s header, under the `###` itself, which is then at least 8rem wide),
with **folded** in its middle (`.section-folded-label`, a button; on touch screens a larger one): a click on it unfolds the section.
Search highlights skip that word.
View only: the file is never changed and nothing is remembered, so every page opens unfolded.
The chevron hangs in the margin left of the heading from 800px wide (the copy-link icon stays after the text), shown when the heading is hovered and while folded; on narrower screens it sits after the heading's text, before the copy-link icon, always shown, larger on touch screens.

#### What unfolds a section

- Folded content is `hidden="until-found"` (marked `data-fold-hidden`; CSS drops its margins, padding and borders, which `until-found` would keep), so the browser's find in page still finds it and unfolds it (`beforematch`); browsers without `until-found` hide it outright.
- What points into a folded section unfolds it (`revealElement`: the sections holding the element, and the section a heading starts):
  a click on an anchor link to this page (the side TOC, a link in the text; not a heading's copy-link icon, so copying a folded section's link leaves it folded),
  a `hashchange`,
  a search highlight that **n**/**N** step to.
- **]**/**[** skip headings inside a folded section;
  the side TOC doesn't mark one.
- Printing (`beforeprint`) shows every section and restores the folds after; presentation mode's slides show folded content and no chevrons.

### Print and PDF

A page is printed, or saved as a PDF, by the browser: **Print…** in the page's ⋯ menu opens the browser's print dialog, where a printer prints it and "Save as PDF" (the destination, in Chrome, Edge, Firefox and Safari alike) makes the file; Ctrl+P does the same.
Zonewiki makes no PDF itself: no renderer on the server, and the file looks as the page does.

- **What is on the paper** (`@media print` at the end of `style.css`): the page alone, its title, tags, "Changed …" (with its day and time, which a screen shows on hover), the tldr (shown, not behind its switch) and the text, at the paper's width (`@page` margins 18mm and 16mm).
  Not Zonewiki around it: the breadcrumb, the top row and the phone's bar, the side columns, Edit and ⋯, the section ✎ and task chips, what links to the page, the build string; a task list embed shows its tasks without its tabs, toolbar and menus.
  The sheet's border and padding go (the paper is the sheet).
- **Always light**: the dark variables and dark rules are for screens (`@media screen and (prefers-color-scheme: dark)`), and code is highlighted with the light theme (`media="print, …"` on its stylesheet in `layout.html`), so a dark screen prints dark ink on white.
- **Page breaks**: none right after a heading; code blocks, quotes, callouts, images, diagrams and table rows are kept whole where they fit; code wraps instead of being cut off where the screen would scroll.
- **Folded sections** print unfolded (`beforeprint`, see Folding sections).
- **Print…** (`printPage` in `js/pagemenu.js`) does two things Ctrl+P can't:
  the tab's title is the page's title while the dialog is open, so the file is named "Garden plan.pdf", not "Garden plan — Zonewiki.pdf";
  and on a dark screen the Mermaid diagrams are drawn again in the light theme first (`diagramsForPaper` in `js/figures.js`), and in the dark one after (`afterprint`).

### Table of Contents

A page's contents are listed by the side column on big screens ("On this page", see Side columns in `docs/spec/routes.md`), built from its headings.
The `[[toc]]` directive, a box of the same links inside the text, was removed in 2026-10 as unused next to it; a `[[toc]]` left in a page is an ordinary wikilink now (to a page named toc).
The TOC ignores `##` lines inside fences.

## Markdown in Zonewiki

Pages are markdown as goldmark reads it (GFM), with Zonewiki's additions: the sections below, and wikilinks, task references, frontmatter, math and diagrams in their own sections.

### Raw HTML

Raw HTML in a page is rendered as written (goldmark's `WithUnsafe`), but no script in it runs: the Content-Security-Policy allows only Zonewiki's own script files (Scripts in pages don't run, `docs/spec/security.md`);
nor does a form, an iframe's `srcdoc` or a link's `ping` (No form in a page posts to Zonewiki either, there), and an `<iframe>` shows an upload but no view of Zonewiki's (Framing, there).
Form controls, `<meta>`, `<base>`, `<link>` and `<script>` in it show as text (`defuseHTML`: a form in a page could post to Zonewiki as the viewer; a `<script type="application/json">` would be data Zonewiki's own scripts read).
Its ids and names get `user-content-` in front (`<a name="note">` is `user-content-note`, as on GitHub), so none stands in for one of Zonewiki's elements;
a link to it works as written (`[see](#note)`: `hashTarget` in `js/util.js` finds it, and `js/page.js` scrolls there), and an SVG's `url(#g)` is pointed at its gradient's new id too;
a page's own `<style>` selects such an element as `#user-content-…`.

### Autolinks and external links

- **Bare URLs become links** (goldmark's linkify, with `urlRE`, which needs no dot in the host, so `http://localhost:5080/…` and `http://nas:5080` are links): `https://…`, `www.…` and email addresses, with trailing punctuation left outside the link.
  Code spans, fenced code and existing links are untouched.
  It covers text the editor's paste prompt never saw: files edited on disk, an unnamed paste, a URL inside pasted text.
- **External links open in a new tab**: a link to another site (http/https, not the host the request came in on) gets `target="_blank" rel="noopener noreferrer"` (`zoneTransformer.external`, on the parsed document, so autolinks too);
  `js/taskutil.js` does the same for links in task titles (`extAttrs`).
  Zonewiki's own links, `mailto:` and a full URL to Zonewiki itself stay in the tab.

### Callouts

**Callouts** (GitHub/Obsidian syntax, `alertTransformer`): a blockquote starting with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or `[!CAUTION]` renders as `<div class="markdown-alert markdown-alert-<kind>">` with a title line.
Styled in `style.css`: GitHub's kinds in Zonewiki's palette (slate, the accent green, plum, ochre, brick), a `--alert` variable per kind, dark variants.
Custom titles (`> [!TIP] My title`) are not supported; that stays a plain quote.

### Image sizing

- `![alt|300](url)` sets the display width to 300px: `zoneTransformer` takes `|300` off the alt text and gives the image `style="max-width:100%;width:300px"`.
  Works for any integer pixel width.
  `max-width:100%` ensures the image never overflows on narrow screens.
- An image on another host isn't loaded: `![alt](https://elsewhere.example/x.png)` is a link to it (`a.external-image`, a 🖼 before its alt text, or the address without one), unless the `external-content` setting names the host (A page loads nothing from other hosts in `docs/spec/security.md`).

### Mentions

`@name` of a user who is told by it is shown as a chip: `<span class="mention">@name</span>`, and for the reader's own name `class="mention mention-me"`, filled.
The text stays `@name`: the chip is how it is shown, nothing is written into the page.

| | |
|---|---|
| On a page | a chip for a user who reads the page and can be told (Mentions on pages in `docs/spec/navigation.md`); the editor's preview shows the same |
| In a task's description and comments | a chip for a user a task can be given to (Mentions in `docs/spec/tasks.md`) |
| Any other `@word` | the text it was: no such user, one who doesn't see the page, a name in code, the `@` of a mail address |
| Who isn't signed in, a wiki without users | no chips at all |
| In a heading, in a link's text | a chip too; the heading's id and its label in the side column are of the text as written |

How: `@name` is an inline of its own (`mentionParser` in `go/markdown.go`, triggered by `@` after a space, a bracket or a line's start, as `mentionRE` reads a mention), whose node holds the text as its child, so every reading of a heading's or a link's text reads the name as written.
The renderer puts the chip around it where `Env.Mention` says so (`mentionChips` in `go/mentions.go`), and nothing where it doesn't: without it a render is what it was.
Tests: `test_a_name_that_tells_is_a_chip` in `tests/test_page_mentions.py`.

### Syntax highlighting

`highlight.js` (vendored, see Dependencies in `AGENTS.md`) auto-highlights fenced code blocks client-side after load; math and Mermaid diagrams render client-side too (Math and diagrams).

### Task descriptions and comments

**Task descriptions and comments** render with the same markdown (`renderNotes`, `POST /api/render-notes`): wikilinks, `[[task:…]]` chips, task-date chips, without what only a page has (frontmatter, `[[tasks:]]`, ✎ per section, heading ids).
A `<style>` in them shows as text (inline `style=` stays): a task's text goes with it onto every page that shows its list (A task's text has no `<style>` in `docs/spec/security.md`).
See Description in `docs/spec/tasks.md`.

### Since the Go server (2026-10)

Zonewiki rendered with markdown-it until the server moved to Go; goldmark renders differently where no Zonewiki feature depends on it, and nothing a page relies on changed (checkboxes, callouts, heading ids, ✎ and ☑ chips, image widths, external links, task-date chips render as before).
What reads differently:

- Footnote markers read `1`, not `[1]`; ids are `fn:1`, not `fn1`.
  Inline footnotes `^[…]` are plain text.
- `~one~` is struck through, as in GFM (before: only `~~two~~`).
- Tight and loose lists and definition lists follow goldmark's rules.
- Typography as before (curly quotes, `--` and `---` as dashes, `...` an ellipsis), but `(c)`, `(r)` and `(tm)` stay as written (before: ©, ®, ™).
- `[x](file:///…)` is a link (before: text).
  `javascript:`, `vbscript:` and non-image `data:` links are still text.
- A bare `mailto:…` or `xmpp:…` isn't made a link (an address alone, `me@x.org`, is).

## Wikilinks

Wikilinks use the `[[Page Name]]` syntax:

| Write | Links to |
|---|---|
| `[[Page Name]]` | `<a href="/page/page-name" class="wikilink">Page Name</a>` (slug = lowercase, spaces → hyphens) |
| `[[journal/my-page]]` | a nested page |
| `[[Page Name\|Label]]` | the page, a link labelled `Label` |
| `[[Page#Section]]` | a heading on that page, labelled "Page › Section" |
| `[[Page#Section\|Label]]` | a heading on that page, with a label |
| `[[#Section]]` | a heading on the same page |

### Links to a section

`[[Page#Section]]` links to `/page/page#section`,
the id as the heading renders,
`mdtext.SectionAnchor`: the heading's text without markdown and links,
its code and math kept as written (`mdtext.PlainHeading`),
so ``## Run `snake_case` now`` is `#run-snake_case-now` to both;
a section with code in a link is read before the code is masked.
The section is ignored for backlinks and kept on rename.

### Labels

A label follows a `|`: `[[Page Name|Label]]`, `[[Page#Section|Label]]`.
It renders as a link's text does (`**bold**`, code and math in it render; HTML in it is text); a heading's id has the label's text, as `[[Page#Section]]` makes it.

### Missing pages

If the target page does not exist, the link is rendered with the CSS class `wikilink-missing` so it can be styled differently (e.g. red or dashed underline).
Clicking a missing wikilink redirects to `/edit/<slug>` so the page can be created immediately.
(With users, on a private page a link to a new page that would be shared is marked and warns in the editor: see Private pages in `docs/spec/users.md`.)

#### Did you mean

A link is a path from the root and never resolves by a page's name alone, so `[[foo]]` is missing though `journal/foo` is there.
The editor helps to fix it instead:
a missing link on a page carries that page (`?on=<slug>`, `wikilinker` in `go/render.go`, kept by the redirect to `/edit/<slug>?on=<slug>`),
and when you may change that page and it links to the missing one, the editor of the new page lists the pages whose last part is the missing one's (`sameNamePages` in `go/pages.go`: only pages you see, and sharing mode shows; at most five, by path):
**Did you mean `journal/foo`?** with a **Link to journal/foo** button for each, and **Write a new page** (just dismisses it, not remembered).
**Link to …** (`POST /relink` with `slug`, `on` and `target`; `relinkOp` in `go/pageops.go`) points every link to the missing page on that page at the one chosen, in one commit "Link to journal/foo on <page>", and opens it.
A link keeps its words (`[[Foo]]` becomes `[[journal/foo|Foo]]`; a label stays), code is left alone, and nothing is made under the missing name.
It is refused (nothing written) when the target isn't a page of that name you see, the missing page is there after all, or the page has no such link.
The missing page `[[old/foo]]` is offered `new/foo` alike, since only the last part counts.
`check_links` names such a page first among its guesses (`docs/spec/mcp.md`).

### Wikilinks in code

Code is exempt: wikilinks in code (and math, and raw HTML) are the parser's, so they stay as written.
`renderMd()` masks fenced code blocks and inline code spans (`mdtext.MaskCode`, private-use-character tokens) before running the `[[tasks:]]` and `[[task:…]]` pre-processors and restores them (`mdtext.UnmaskCode`) before markdown, so directives inside code render literally.
A page's own text that looks like such a token (`\ue000N\ue001`, or `\ue002N\ue003` for task references) is masked too and comes back as written, so it can't fail a render or swap in another code span, nor be written into a page by a rename's link rewrite.

### How wikilinks are parsed

- Wikilinks are an inline of goldmark's (`wikilinkParser`, before its links, an inline like a link; one line, no bracket inside), written by the renderer as links through `Env.Wikilink` (`wikilinker` in `go/render.go`), so code spans, math and raw HTML (an attribute's `[[…]]`) keep theirs as text.
- A table splits its rows at `|` before inlines are parsed: `escapeWikilinkPipes` writes the `|` of a wikilink on a table's lines as `\|` (GFM's pipe in a cell) before the parse, and the parser reads it back; elsewhere it stays as written.
- An HTML block is raw to goldmark: its wikilinks are linked in its text between the tags (`wikilinksInHTML`), not in its attributes.
- `mdtext.SplitWikilink` parses all forms (server), `wikilinkHref` in `js/mdtext.js` the same for the editor's Enter-on-a-wikilink (task descriptions render on the server, `renderNotes`).
- `/api/preview` renders them the same way, so the live editor preview shows wikilinks as the page does.
- `GET /api/pages` returns a JSON list of all existing slugs — used by the editor to hint which wikilinks are valid.

## Task references

`[[task:<id>]]` in a page shows one task inline as a live chip.
`<id>` is the first 8 characters of the task's id (`shortIDLen`, like a short git hash) or the whole id, case ignored (`taskRefRE`).
Nobody types ids: in the editor, `[[task:` (or the formatting bar's task button) opens a completion over open tasks (title words or id prefix, newest first, with list and deadline; `taskAcMatches` in `js/completion.js`, tasks from `/api/tasks`, refetched after a minute) that inserts the reference.

### What a chip shows

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

A completed recurring task stands for its series: the reference follows `spawned_from` to the current occurrence, so ticking it and reloading shows the next one.
That link is Zonewiki's own: a task restored or imported keeps `spawned_from` only when it comes back as Zonewiki had it (the undo of its delete; Who made a change in `docs/spec/users.md`), so nobody makes a reference to a closed task stand for a task of theirs.

### Ticking a chip

**Ticking** the chip's checkbox (`js/page.js`, page view and editor preview) PUTs `{ completed }` to `/api/tasks/<id>`,
so repeating and keep tasks behave as in the tasks,
then re-renders the chip from `/api/preview` of `[[task:<full id>]]` (one renderer: a repeating task shows its next occurrence, a keep task its ✓).
Undo toast: `completed: !done` plus the task's old `history` (no trace), or for a keep task its old `start_date`, `due_date`, `last_done` and `history` (fetched from `/api/tasks` before the change).

### Make task

**Make task** (formatting bar, `makeTasks` in `js/editor.js`): turns text in the editor into tasks and replaces it with their references, e.g. action items in meeting notes.
A selection within one line becomes one task and an inline reference.
Otherwise each non-empty line the selection touches (or the cursor's line) becomes a task, keeping its list marker and dropping a checklist's `[ ]` (`- [ ] Call Bob` → `- [[task:…]]`); blank lines, headings and lines that are already a reference stay.
Each task's description says `From [[<page>]]`.
The undo toast (both ways) deletes the tasks (`batch-delete`) and puts the text back.

#### One item

**One item opens the task app's edit modal** to review it first: the editor page carries a hidden task app (`buildTasksEmbed(…, headless)`: `data-headless`, `display:none`; `tasks.js` then ignores the task app's keyboard shortcuts and the "moved to another list" notice),
and `openTasksEdit(null, { title, description, start_date: null, onCreated })` (exported by `tasks.js`) opens it with the text as title (its quick-add preview shows the shorthand) and no start date.
Save (Enter) creates the task and `onCreated` puts the reference in; Cancel/Esc changes nothing.
The same modal serves `c t` in the editor (a task in the Inbox).

#### Several lines

**Several lines** open the same modal in batch mode (`openTasksEdit(null, { titles, … })`, `setBatch` in `js/taskmodal.js`): the titles are listed instead of the title field (each with its shorthand preview),
focus starts in Tags,
and the fields (notes, tags, dates, repeat, keep) apply to every task.
A line's own shorthand adds to them: its tags join the shared ones, its dates win over the shared ones.
Save creates them one by one (`createTask`) and `onCreated` gets them all; if one fails, or the text changed meanwhile, the created ones are deleted again and nothing is replaced.
Should the task app be missing, the editor creates them right away with `TasksCore.parseQuickAdd`.

### References in search, Linked tasks and MCP

- **Linked tasks** leaves out tasks the page shows inline (`pageTaskRefs`).
- **Search** indexes a reference as its task's title (`taskRefsAsTitles`, also in `semanticText`); a page with references is re-indexed when the task files change (the page doc cache keeps the task files' mtimes).
  Embeddings and MCP section docs are cached by page mtime, so they catch up with a renamed task when the page changes.
- **MCP `read_page`** keeps the raw markdown and appends what each reference stands for (title, status, dates, link; `mcpTaskRefs`).

### How references are rendered and resolved

- **Rendering** (`maskTaskRefs`/`injectTaskRefs`, chip from `taskRefHTML`): masked with private-use tokens (`\ue002N\ue003`) after code masking, so code keeps them literal and markdown never touches the chip, then replaced after markdown.
- **Resolving** (`resolveTaskRefs`): open tasks win over closed ones with the same prefix; archives count.
  The tasks are looked up among the search's records (`taskRecords`: a task without its description, comments and history, which is what a chip shows; Indexing in `docs/spec/search.md`), so a page with a reference doesn't read and copy every closed task of the wiki's years;
  an assistant's `update_task` finds its task the same way and reads that one whole (`findTasks`).

## Frontmatter

A page may start with a leading `---`-fenced block declaring these keys:

| Key | Holds |
|---|---|
| `tags:` | the page's tags, a list |
| `tldr:` | a short summary, quotes optional |
| `sources:` | where the page's facts come from, a list |

Any other key is parsed and ignored.
Dates aren't fields: the page's history has them ("Changed …" under the title, and its revisions).

### A tldr over several lines

`tldr:` may run over several lines,
as YAML writes them: `tldr: >` (folded: the lines join with spaces, a blank line is a line break),
`tldr: |` (literal: the lines as written, their common indent removed),
either with a chomping sign or indent (`>-`, `|2`, ignored),
or a plain value going on in indented lines below it (folded).
Everything indented after `tldr:` is the tldr's, `- item` and `key: value` lines too.
`.page-tldr` has `white-space: pre-line`, so the line breaks show.

### Lists of tags and sources

`tags:` and `sources:` are lists: comma-separated (`a, b`), bracketed (`[a, b]`), or a YAML block list, one `- item` line each under the key (quotes optional).
A comma inside `[…]` or `(…)` doesn't split a value, so `[Doc, v2](url)` and `[[page|A, B]]` stay whole; a lone `[[page]]` is a wikilink, not a bracketed list.
`mdtext.AddFrontmatterTags` (Append to page) rewrites a block list of tags as one `tags: a, b, c` line.

Tags render as clickable pills linking to `/search?q=%23<tag>` (the `#tag` filter: exactly the pages and tasks with that tag, not every page using the word;
a tag containing a space can't be written as `#tag` and keeps a plain word search), styled via the same `.task-tag` class Tasks uses.

### Sources

**Sources** render at the article's end (`renderSourcesHTML`, `<section class="page-sources">`, a "Sources" list under an `<h2 id="sources">`, `sources-1` … when the page has a heading of that name; styled small),
in the page view,
the live preview and revisions alike,
each one inline markdown: a bare URL,
`[label](url)` or `[[page]]` is a link.
Being in the file, a `[[page]]` source is a link like any other: it counts for backlinks, and a rename rewrites it.
Being an `<h2>`, it is in the side TOC (counting towards its minimum of headings) and gets the heading's copy-link icon and `]`/`[` like any section; in presentation mode it stays on the last slide (a slide starts at a heading directly in the article).

### Editing frontmatter

The **editor textarea always holds the raw, unparsed file** — including the frontmatter block verbatim — so editing it is just editing text like any other part of the file; there is no separate metadata-edit form.
The live preview pane shows the parsed tldr/tags block (both, at the top of the article) as you type, via the same `renderMd()` path — it has no separate meta row to place them in.

### The meta row

**`viewPage` is the exception**: it parses frontmatter itself and renders tags plus a tldr toggle in the `page-meta-wrapper` row below the H1 instead — together in `.page-meta-left` (tags pills then the `tldr` label, left-aligned),
with "Changed 3 days ago" right-aligned in `.page-meta-right`,
followed,
when the file is uncommitted,
by a link-styled **Commit** (the `commit_form` template, inline, `.commit-link`) on the same line rather than a separate button row (`justify-content: space-between` on the wrapper).
It renders with `RenderOpts{HideTags: true, HideTldr: true}` so nothing frontmatter-related is duplicated in the article body.

`.page-meta-wrapper` is a normal-flow block rendered as a sibling right after `</header>` (not nested inside it, not absolutely positioned) — so `.page-header`'s `border-bottom` stays anchored directly under the H1/actions row,
while the meta row can still grow (e.g. an expanded tldr) and push the article below it down instead of overlapping it.
It uses a small negative top margin (`margin: -1.1rem 0 1.5rem`) to sit close under the header despite being a separate block, rather than living inside the header's own margin.

#### Last changed

The line says only how long ago: "Changed 3 days ago", in Zonewiki's own words, in a `<time>` whose `title` holds the day and time (`2026-11-24 14:05`, the server's time zone), shown on hover and, on paper, after the words.
What git says of the page for that line (`lastChanged` in `go/views.go`: changes not committed, so the file's time, or the last commit's time) is kept per page while neither the page's file nor the repository changed (`changeStamp`: the file's time and size, and those of `.git/logs/HEAD` and `.git/index`, which Zonewiki's commits write and so do a commit, pull or checkout in a terminal),
so a view of a page seen before starts no git process;
a repository without a reflog is asked at every view.

#### The tldr toggle

The view-page tldr toggle is **not** a `<details>` — it's a visually-hidden checkbox (`.tldr-toggle`) emitted *before* `.page-meta-wrapper`, a `<label for="tldr-toggle">` *inside* the row, and the `<p class="page-tldr">` *after* the row.
Keeping those three as siblings is what makes `.tldr-toggle:checked ~ .page-tldr { display: block }` work, so expanding reveals the text on its own full-width line below while the label stays put on the tags line.
A `<details>` can't do this: its content is nested inside it, so making the text full-width drags the summary down with it.
Collapsed by default, CSS-only (no JS), and the revealed text uses `.95rem` rather than inheriting the row's `.75rem` meta size.
The open text sets only its top and bottom margins: from 1320px the side columns centre it like the row (`margin-inline: auto`), and a `margin` shorthand, being the more specific rule, would put it at the column's left edge instead.

### How frontmatter is parsed and rendered

- `mdtext.ParseFrontmatter()` extracts it (`Frontmatter`: tags, tldr, sources) and returns the remaining content with the block removed.
- `renderTagsPillsHTML()` renders a tag list as the pills (Lists of tags and sources, above);
  `renderFrontmatterHTML()` wraps that plus an italic tldr paragraph into a `<details class="page-frontmatter"><summary>tldr</summary>...</details>` block — collapsed by default, click to expand — and can leave out either half.
- `renderMd(rq, text, RenderOpts{…})` calls these internally, so most call sites (`/api/preview` live preview, revision view) get both tldr and tags rendered at the top of the article for free — those use the `<details>` form, since they have no meta row to place a toggle in.
  The page view is the exception (The meta row, above).

## Math and diagrams

Both render in the browser from vendored copies (`public/vendor/katex/`, `public/vendor/mermaid/`; see Dependencies in `AGENTS.md`), nothing from other hosts.
`js/figures.js` (`renderFigures`) loads a library the first time a page has something for it,
so a page without math or diagrams loads neither (Mermaid is 5.5 MB, 1.5 MB as it is sent: `/public/vendor/` scripts and styles go gzipped to a browser that takes it, gzipped once and kept in memory, and with `Cache-Control: public, max-age=86400`, since they change only with an update of Zonewiki;
`servePublic`).
It runs on the page view (the article and the side TOC), the editor's preview, presentation slides, the search dialog's preview, and task descriptions and comments (in the live list after each draw, from its caches, so a library that failed to load is tried again then).
Without the library (an error, offline before the first load) the source shows as written.

### Math

**Math** (KaTeX): `$…$` inline, `$$…$$` for display, and a ` ```math ` fence (display).
Bad TeX shows as written in KaTeX's error colour, its message as the title.
Task descriptions and comments are rendered as pages are (`renderNotes`), display math included; only a frontmatter source, one line's inline content, has inline math only.

#### Where math starts and ends

- Pandoc's rules, so prices stay text: the opening `$` has no space after it, the closing `$` none before it and no digit after it (`$5 and $10`, `$5–$10` are text), and inline math stays on one line (`mdtext.InlineMath`).
  A `$` followed by a letter does open math, so `$HOME/$USER` in running text is math: write it in backticks, or `\$`.
  `\$` is a literal dollar, also inside math.
- A line of only `$$` (up to three spaces before it) opens a display block that runs to the next line ending in `$$` (or to the end of the page, as an unclosed fence does); `$$tex$$` alone on its line is one too; a line that only starts with `$$` (`$$100 bills`) is text (`mdtext.MathFence`).
- In a table cell, `\|` is the cell's pipe as it is in code (`$\|x\|$` is |x|; KaTeX's double bar is `\Vert` there).

#### How math is kept and rendered

The server (`go/math.go`, a goldmark extension) keeps the TeX as written,
so `_`,
`*`,
`--` and quotes inside it are never emphasis or typography,
and writes it escaped into `<span class="math math-inline">`,
`<span class="math math-display">` (`$$…$$` in running text) or `<div class="math math-display">` (a `$$` block or a ` ```math ` fence);
`renderFigures` renders those (the TeX stays in `data-tex`).
KaTeX's stylesheet loads before the math renders, so it takes its height once.

The source-side text functions agree with the parser:

- `mdtext.FencedLines` counts a `$$` block as a fence (and,
  like the parser,
  no ``` line with another backtick after it: ```` ```x``` y ```` is inline code; `opensCodeFence` in `js/mdtext.js`),
  so a `- [ ]` line in one is no checkbox (`js/mdtext.js` `taskCheckboxOffsets` and the editor's preview highlight do the same, `mdtext.MathFence`).
- `mdtext.MaskCode` masks inline math with a `[[` in it, so wikilinks, task references and `[[tasks:]]` in math stay TeX (math without one isn't masked, so math in a link's text stays in the link).
- `mdtext.PlainHeading` keeps a heading's math as its TeX (and its code as written), so `## Area $x_1$` is `#area-x_1` to the heading and to `[[Page#Area $x_1$]]` alike.

### Diagrams

**Diagrams** (Mermaid): a ` ```mermaid ` fence becomes `<div class="mermaid-diagram">` with the SVG (`securityLevel: 'strict'`: no scripts or click handlers from a diagram's text), in Mermaid's `dark` theme when the browser prefers dark.
A label's HTML (`<b>`, `<br>`) stays, but not what makes or submits a form, a refresh or a frame, nor ids and names: Mermaid cleans labels with DOMPurify's defaults, which keep form controls, so `js/figures.js` gives it its own list (`dompurifyConfig`, which a diagram's `%%{init}%%` can't change: `secure`) and takes those out of the drawn SVG before it goes into the page (`defuseDiagram`; No form in a page posts to Zonewiki in `docs/spec/security.md`).
A diagram that doesn't parse keeps its source, with a `.mermaid-error` line under it (`Diagram: <Mermaid's message>`).
The SVGs are kept per source and theme (the last 50), so the editor's preview, redrawn as you type, doesn't flash the source.

### Figures and the rest of the page

- **What you were sent to stays in view**: a figure rendering above a page's `#section` or its current search highlight (or the search preview's target) makes the text there taller; `renderFigures` scrolls by as much, when the target was in view, so it stays where it was (`keepInPlace`).
- highlight.js leaves Mermaid fences alone (`HIGHLIGHT_CODE`), and search highlights skip `.math` and `.mermaid-diagram`.

## Sortable tables

Clicking a column header of a table in a page's article sorts its rows: ascending, descending, then back to the file's order (`sortTableBy` in `js/page.js`; Enter/Space on a focused header too).
View only: the file is never changed and nothing is remembered.
A column whose non-empty cells are all numbers (`tableCellNumber`: `-2`, `9.5`, `1,200`, `50 %`, `€3`) sorts numerically, any other column with a numeric-aware, case-insensitive collator (`item 9` before `item 10`, ISO dates in order).
Empty cells go last in both directions.
The state is the header's `aria-sort`, which the CSS turns into ▲/▼ (↕ on hover).
Only `article.page-content` tables with a header, one body, at least two rows and no merged cells (raw-HTML `colspan`/`rowspan`) are made sortable; the editor preview is not.
Clicks are delegated, so presentation slides (cloned from the article) sort too.

## Version history

Every save of a page is a git commit, so each page has a history of versions, any of which can be viewed, compared and restored, and the whole wiki's changes are listed in Changes, where a change can be undone.

### Setup and commits

- Git is installed in the container image.
  On startup, `gitSetup()` initializes a git repository in `ZONE_DATA` (if there is no `.git` yet; an ordinary one with a working tree, the wiki's files), writes an empty `.gitignore` when there is none, and commits all existing files.
- `cfg.GitEnabled` (git on the `PATH`) — all git paths are no-ops when git is absent (graceful degradation).
- Every write operation (Done/save, create, rename, delete, restore, upload) calls `gitCommit()` (a rename or move through `movePage()`, with the pages and task files whose links changed) to stage and commit the change: only those paths (`git commit -- paths`),
  so what else is staged (a `git add` by hand) stays out of Zonewiki's commit.
- **Auto-save (`/api/save`) is the exception** — it writes the file to disk but does not create a git commit.
- Assets are tracked like pages — a `git push` backs up everything.
- A commit that fails (a stale `index.lock`, a refusing hook) is logged and reported (an upload's too, since 2026-11: `storeUpload` returned nothing of it): the route answers 500 (Commit file and Commit all with `{"error": …}`), an MCP tool with a tool error, "the change is written but not committed";
  the file stays in the Uncommitted list, its draft's author still known.
  A commit that has to come first (text not yet in git, `keepPending`; another user's draft) and fails stops the write, so that text is never lost.
- Pages are written atomically (a temp file renamed into place, the file's permissions kept), and writes to pages run one at a time (`pagesMu`, from the reads a write decides on to its commit: two writes at once, say an assistant's `append_to_page` and a ticked checkbox, both keep their change).
- SHA values are validated with `shaRE` (`^[0-9a-f]{7,40}$`) before use.
- Git's own maintenance runs in the foreground and Zonewiki packs the repository itself, in the background: Packing the repository in `docs/spec/deployment.md`.
- Git calls time out after `gitTimeout` (60s, `runGit`): a hung git counts as failed (exit code 124).

### How a page write is made

Every page write is an operation in `go/pageops.go`: delete, rename, restore, create, a save, Keep mine and Set aside after a conflict, a replace across pages, a link pointed at a new page's folder, a task tag's rename, an undo from Changes, a markdown import;
a note's move and append are in `go/quicknotes.go`, and a list's rename (`renameListOp` in `go/tasklists.go`) writes pages too, the embeds and links that name the list.
An operation checks access itself and keeps text not yet in git: `keepPending` before a write that replaces or removes a page's text, `commitOthersDraft` before one that continues it.
The routes and MCP tools only parse their input and turn the result or the refusal into their answer, so a new write is a new operation, with a test through each door in `tests/test_page_writes.py`.
That is a convention, not a guard: nothing fails when a write has no such test.

Page writes run one at a time (`pagesMu`), from the reads they decide on to their commit.
The doors take it, not the operations: a route that writes pages is registered as `writesPages(handler)`, an MCP tool that does says `toolWritesPages` in `mcpToolTable`, and code under it never takes it again.
The lock order is `pagesMu`, then the task files' lock (`taskFiles.Lock`), then `commitMu`.
Task reads don't take the task files' lock: they wait only while a write replaces its files (`taskFiles.Hold` … `Release`, Storage in `docs/spec/tasks.md`), so code that writes a task file itself calls `taskFiles.Hold()` first, and a section under `taskFiles.Lock` calls only the store's `…Locked` readers.

Pages made or replaced in one commit are one operation, `writePagesOp`: a list of pages, each new or there already, the commit's message, and what to call them in the commit of text kept first.
A new page must have a path (`safePath()`), be the asker's to write (`canWrite`) and not be there yet;
one there already must be the asker's to write (`writablePage`), and what it held not yet in git is committed first (`keepPendingPages`, "Save <what>", a commit per author).
`checkRoom` asks for the new pages' text (a replacement adds nothing), and what is written new is counted (`added`).
One page that isn't the asker's or isn't as it was said to be (a new one there already, one there already missing, a page named twice), or a wiki without room, refuses the whole write: nothing is written or committed, not even the text kept first.
`createPageOp` (a new page in the browser, `write_page`) is a call of it with one new page, `replacePagesOp` (`replace_in_pages`) with pages all there already, so the rules of making a page are written once.

A markdown import (`importOp`) writes the converter's plan (`mdimport.Plan`) into a folder that isn't there yet: its files into the place's uploads, then its pages, the folder's index and a line on the place's home page (`index`, `private/<user>/index`, `groups/<group>/index`) that links the index, in one commit, "Import 214 pages and 87 files from my-vault".
The pages go through `writePagesOp`, new, with the home there already (its text not yet in git kept first, "Save index"), and the files' paths in its commit (`pagesWrite.also`); a home not there yet is made first in its own commit, as a visit makes it, so the import's undo never deletes a home.
Who may: anyone who may write pages in the folder (`checkImportFolder`: the shared wiki, their private folder, a group's they are in; not a place's own folder; a viewer or visitor nowhere) and upload to its place (`uploadPlace`); another user's folder answers as missing, a folder that is there is refused, so nothing clashes.
Under the uploads' lock (`uploadMu`, after the task files' lock, before `commitMu`) each file's name is checked again: one taken since the plan was made refuses the import, as the pages' links name it (an upload is never overwritten nor an imported file renamed).
It is refused whole, with nothing written, for a page outside the folder, a name that is no upload's or is given twice, a source that is no file, or a wiki without room for the files and the pages together (`checkRoom`); files written for pages `writePagesOp` then refuses are removed again.
Undo takes the import back whole, as a change of pages and uploads: the pages and files go, the home loses its line.
The converter's callbacks come from `importOptions` (the folder checked as for the import; a file's name free in the place's uploads and not given twice in one import, `freeImportName`; its link `uploadURL`).

A page is written with `writeText` (UTF-8, through `atomicfile.Write`: a link written through, a new file 0666 less the umask), after `safePath()` and `os.MkdirAll` for its folder.
`gitCommit` returns an error, which the write returns (`notCommitted`); links a move couldn't re-point are no failure (`linksLeft`: logged, and the MCP tools say so).

### A page's history (`/history`)

History is in the page view's ⋯ menu (and the breadcrumb of a revision or diff links back to it).

**`/history/<slug>`** lists the page's saved versions,
newest first,
one row each (`.hist-list`): date and time,
what the save did in words (`versionLabel`, of the message as `commitWhat` gives it, an assistant's without its `(MCP)`: Zonewiki's own commit messages read alike, so "Update p" → Edited, "Rename a to b" → Renamed from “a” or, on a page whose link was updated, Link updated, "Restore p to <sha>" → Restored the version of <date>, also Created/Deleted/Imported;
other messages as written), who made it (with users) and, for an assistant's, through which ("alice through Claude"; "alice through an assistant" for a token's, or without a name: Which assistant in `docs/spec/mcp.md`), and the lines added/removed (`--numstat`).
The row opens that version; the page as it is now is marked **Current** and opens the page itself (`isCurrent`: the newest commit equals the file on disk; with edits not committed yet a "Now · Not committed yet" row is the current one instead).
**Changes** opens the diff.

### A revision (`/revision`)

**`/revision/<slug>/<sha>`** shows that version with a bar (`.version-bar`) saying where it stands ("The version of 2026-10-08 14:32 · 2 versions back", "The current version", or a deleted page's last version),
**What this save changed** (the diff),
**Restore this version** (a plain button that asks first: `form[data-confirm]` in `js/git.js`; not offered for the current version) and **← Older / Newer →** (`versionIndex`; Newer from the version before the current one is **Current →**, the page).
The breadcrumb names the version by its time, not its hash.

### A diff (`/diff`)

**`/diff/<slug>/<sha>`** ("Changes") shows what that save changed, red taken out and green put in, without git's file headers (`diffLines` leaves out everything before a file's first `@@`; a hunk header becomes `⋯`), with **View this version** and the same ← Older / Newer →.
No Restore there: a diff shows a change, not a version.

### Renames in the history

A page's history goes on across renames, as `git log --follow` would have it, and records each commit's path for the file (`path`), so revision, diff and restore read a version under the name it had then (`versionPath`);
before, a version from before a rename was a 404.
`gitLog` doesn't ask `--follow`, which reads every commit of the history (2 s for a page at 200,000 commits):
it reads the log of the page's own path, which the commit-graph's filters answer (The commit-graph in `docs/spec/deployment.md`),
and where a commit made the file (`--raw`'s `A`) asks git whether that commit renamed another to it (`renamedTo`: `git diff-tree -M` on that one commit).
The newest such commit ends what is the page's under this name, and its history goes on under the old name from the commit before;
a commit that made the file anew (a page deleted and made again) is no end, as for `--follow`.
The diff of a rename passes both names (`gitDiff(sha, paths…)`, `-M`), so a pure rename shows no change instead of the whole page as new.

### Changes (`/changes`)

**`/changes`** (Changes, `go/changes.go`) lists the wiki's commits, newest first, under a heading per day (Today, Yesterday, then the date; 50, **Show more** doubles them up to 500, `?n=`).
Each row shows:

- the time, what the commit says, who made it (with users; on a phone the name and the assistant's mark stand under the text, what it changed under that with Undo at the right, all beside the time's column);
- an **assistant** mark where an assistant wrote it (its `Assistant` trailer, `commitMarkOf`, never its message, which a page's name can end in `(MCP)` too; the mark replaces the `(MCP)` an assistant's message ends in, and says **through Claude** where the commit names its connection: Which assistant in `docs/spec/mcp.md`);
- where its text is from when the assistant said ("from https://…": Where the text is from, there);
- then what it changed: each page by its slug, a link to what the commit changed on it (`/diff/<slug>/<sha>`), any other file (an upload) by its path, and each task it changed by its title as it is now, a link to the task (`/tasks?task=<id>…`, `taskLink`; The tasks a commit changed, below).

Found in the search modal's list (pinned above the pages, before Deleted Pages), from a page's History ("Changes across the wiki") and from Connect an assistant ("your assistants' changes": the list by you, only by an assistant).
Not for a public wiki's visitor.

It is the list the MCP tool `history` gives without a slug, from the same function (`wikiChanges`), so both doors show a commit alike:
only the files its reader may see (`canSeePath`), not a commit that changed none of them (it doesn't count toward the 50 either), and its message without the names of what they don't see (`hideNames`).
In sharing mode a commit shows only when nothing it changed is hidden: its message names what it changed.

#### The tasks a commit changed

A commit of tasks names the tasks it changed in its trailers, `Task: <id>` a task (`taskTrailers` in `go/changes.go`), written when the task store commits (`commitTasks`): the store knows which tasks its write changed, as it compares what each file held with what it writes (`Store.Touched` in `go/internal/taskfiles`: a task made, deleted or changed, wherever it went; not one whose file was written for another's sake, nor one that only went into its month's archive).
At most 20 are named (`taskTrailersMost`), the rest counted, `More-Tasks: 3`; an id that is no plain word (a file edited by hand) is counted, never written into a line.
So listing Changes reads no task file: the trailers come with the log (`taskMarkOf`), and the tasks they name are looked up once for the whole list among the search's records (`tasksOfChanges`, `tasksOfIDs`), which are kept current file by file.

What a row shows of them, for its reader:

- each task they see now by its title now (plain, at most 80 characters), linked to it in its list, open or on Closed;
- **Tasks** where the commit changed task files and names none they see: one from before 2026-11-20, a write of every user's task files that doesn't name them (a tag or a list renamed, a page's rename followed in the tasks), a task's file uploaded, a task deleted for good since;
- **other tasks** beside the named ones where it changed more than it names, or one of them was deleted for good;
- nothing of a task they don't see (another user's private one, a group's they aren't in, one moved there since): not its title, its id, nor that it was there (`canSeeTask`).

In sharing mode a task in a list that is not for sharing, or in no list (with users a user's Inbox), isn't named either, as the list switcher leaves those lists out.
The tool `history` names them alike, `"Water the beds" (task 1a2b3c4d)`, with the short id its task tools take.

#### Filtering Changes

**Filter**, a button above the list, opens the filters (a `<details>`, shut when the page loads, so the list starts at the top of a phone's screen); what is set stands beside the button (`.changes-on`: "by bob", "in garden/").
They are a GET form, so a filtered list has an address:

| Filter | Parameter | |
|---|---|---|
| **By** a user | `?user=` | with users; the commit's author; a name no user has is ignored |
| **Only by an assistant** | `?assistant=1` | its `Assistant` trailer, Which assistant in `docs/spec/mcp.md` |
| **Through** a connection | `?via=` | the `Assistant` trailer's value: a client's name, `MCP token`, `An MCP client`; offered: the reader's own connections, `MCP token` when they have one, the names on the commits listed (`changesVias`); shown when there is one |
| **Only text from outside** | `?outside=1` | an assistant said where its text is from (its `Source` trailer, Where the text is from in `docs/spec/mcp.md`) |
| **In the folder** | `?under=` | as the tools' `under` |
| **Since** a day | `?since=YYYY-MM-DD` | anything else is ignored |
| **Only what I watch** | `?watched=1` | with users: the pages and folders the reader watches, and of a commit only those pages (Watching pages in `docs/spec/navigation.md`) |

**All changes** drops them.
A filter is asked of each commit as it is read (`changesOf.takes`, `keep` in `gitLogAll`), so a filtered list is as long as an unfiltered one.
The tool `history` takes the same filters (`since`, `under`, `assistant`, `outside`, `watched`) into the same `changesOf`, so both doors list the same commits; Connect an assistant links each connection to its changes (`?user=<you>&via=<its name>`).
An undo comes back to the list as it was filtered.

### Undo

**Undo** (on a row of Changes; `POST /changes/undo`, `undoChangesOp` in `go/pageops.go`; an assistant's `undo_changes`) takes a change back whole: every page it changed is as it was before it, a page it made is deleted again, a page it deleted is back;
every task it changed is as it was before it (an edit reverted, a completion reopened and the series' next occurrence it made gone again, a move between lists or places moved back), a task it made is deleted again, a task it deleted is back with its id;
in one commit, "Undo 1a2b3c4: Update garden", and a change of pages and tasks together (a commit made by hand, say) in one commit too.
One click and no question: the undo is a change like any other, at the top of the list, and undoing it brings the change back.
The list then says what it did ("Undone: garden as before; the task “Water the beds” as before."), naming three of each kind and counting the rest ("p00, p01, p02 and 22 more deleted again": an import undone takes back thousands, and the sentence goes in the redirect's URL, which a proxy refuses past a few KB).
A page's History calls it "Undid the change of <time>".

#### Undoing a change of tasks

The tasks a change changed and what they were before it are read from the history only when it is undone (`tasksOfCommit` in `go/tasks.go`: its task files, `taskfiles.Store.FileOwner`, at the commit and its parent), never to list Changes; several changes of one task together go back to before the first of them, in the order of the history (`taskUndoPlan`, `newestFirst`).
Under the task files' lock (after `pagesMu`, before `commitMu`), each task has to be as the changes left it, alike in its file and kept in the same place (`taskUndoLocked`), and goes back whole, its history too: the task's History reads as it did before the change, as after the task app's own undo (Undo leaves no trace in `docs/spec/tasks.md`), and the undo is in Changes and in git, with the tasks it put back named (The tasks a commit changed), where it can be undone in its turn.
A task that already is as it was is left; the commit is made by the task store, the pages written before in it (`WriteBothLocked` with them).
No mail goes out for a task given back to someone by an undo.

#### What can be undone

**What can be undone**, and what the operation refuses otherwise, saying why:

- a change of pages, uploads and tasks (one that changed a list's registry, a task's files or any other file too is not: Undo doesn't put those back; a list is renamed or deleted back by hand);
- each page, upload and task one its reader sees, each page one they may write (a guide: an admin; a viewer: none; a change they don't see all of is answered as no change at all, 404, whoever's task, private upload or group's list it touched), each upload in a place they may upload to (Uploads undone, below);
- each task one they may write back where it was, as any write of tasks asks (`mayUndoTask`): its list still there and theirs to write (`canWriteList`), or their own Inbox; a task the change made goes only as deleting it goes (who made it, an admin: `mayDeleteTask`), which also makes it the one way an assistant takes a task away, and that undoable; a shared task never back into a private place ("a shared task stays shared"); back into a list fewer people see only by who made it or an admin (`mayNarrowTask`, as a move there: Moving a task to fewer or more people in `docs/spec/tasks.md`);
- no page moved (a rename is undone by renaming back, which asks who may);
- and no page or upload changed again since, by a commit or by what isn't committed yet, and no task changed again since (it isn't as the change left it, by anyone), nor between changes undone together by one not among them: the undo would take that along.
  The page's History restores any version instead; a task's text is in git.

Changes shows the buttons from what the list knows (`undoOffers`: the kinds of files, who may write (or upload there), the wiki's latest 1,000 commits for "changed again", `undoWindow`; for tasks the commits' Task trailers, no task file read: a change of tasks is offered when it names every task it changed, the reader sees each, and no later commit among those names one of them or changed one of its task files without naming all it changed); the operation checks again, exactly, so an offer that no longer holds is refused and said ("Not undone. garden was changed again since …", "Not undone. The task “Mow” was changed again since …").
For more than 20 files (`undoBatch`; an import is thousands) it asks git once, not a file at a time: one `git log --name-only` from the oldest change undone to HEAD for what changed each file since (`historySince`), one `git status` for what isn't committed (`uncommittedSet`); a merge in that range makes it ask file by file, as a log limited to a path reads merges otherwise.
An import of 5,000 notes is undone in about a second so (`tests/bench_import.py`), against 13 s a file at a time.

#### Uploads undone

**An upload is undone like a page**: a file straight in a place's `_assets/` (the shared `_assets/`, `private/<user>/_assets/`, `groups/<group>/_assets/`: `uploadOf`) that the change added is deleted again, one it replaced or deleted is written back as it was before the change, byte for byte (`gitBlob`: `git cat-file blob`, not the text a page's undo reads, which would make a picture's CR LF a LF).
So a file uploaded by mistake is taken away from Changes, and a change that adds pages and files together (a markdown import, How a page write is made) goes back whole.

- **Who**: whoever sees the file and may upload there (`mayUndoUpload`: the place's rule, as `uploadPlace` asks of an upload; a viewer or a read-only connection none).
  Another user's private file is no change to anyone else, answered as one that isn't there.
- **Changed again since**: as a page, by a commit not among those undone or by what is on disk and not committed; checked again under the uploads' lock (`uploadMu`, after the task files' lock and before `commitMu`), so a file uploaded meanwhile isn't overwritten.
- **Storage**: a file written back asks for room, as an upload does (`checkRoom`: what it adds over the file there now; refused when the wiki is full, Storage limit in `docs/spec/users.md`); one taken away is counted at once (`forgetUsage`).
- **Links**: a page that links the file since is left with a link to nothing, as a page linking an undone page is: the undo doesn't look at who links it, which would also say that a page is there.
- **Not undone**: a task's files (`_tasks/files/<id>/_assets/`, which go with their task), a list's registry, any other file.

This is the history's undo; the editor's Ctrl+Z after pasting a file takes back only the link it put in the text (Undoing an upload in `docs/spec/editor.md`).

#### An assistant's run

**An assistant's run of work together**: the commits an assistant made for one user through one connection, none more than 30 minutes after the one before (`sessionGap`; other people's commits between them don't end it), are a run.
The latest of them offers **Undo all N** when the run can be undone as one: nothing but the run's own commits changed its pages since the first of them, nor its tasks (as for one change, above).
It asks first, saying how many, by which assistant and from when to when; the result is one commit, "Undo 3 changes: …".

## How a page is rendered

### The render pipeline

`viewPage` (`go/routes.go`) reads `<slug>.md`, `renderMd()` (`go/render.go`) converts it to HTML with goldmark (`go/markdown.go`), and `page(…, "view.html", …)` renders it into the page (see Templates).
A path with a slug in it is built with `pathURL()`, which percent-encodes it, so a file named with a quote, `#` or space still gets a working link that is safe in an attribute.

One goldmark set up with Zonewiki's rules is built per kind (block, inline) and reused from a pool (`mdRenderer` in `go/markdown.go`), bound to one render's `Env` at a time;
how headings carry their ids is the renderer's (`Env.HeadingIDs`: `id` on a page, `data-pid` in the search preview, none in task descriptions and comments), not a rewrite of its output.

### Heading ids

**A heading's id is `mdtext.HeadingID`** of its text, the same function a `[[page#Section]]` link's anchor comes from (`SectionAnchor`) and the browser's `headingId` is held to (`tests/test_text_parity.py`):
what is no letter, digit, `_`, `-` or space goes, then the ends are trimmed, so "& more" is `more`.

### Section headings and HTML post-processing

Zonewiki's additions to goldmark's output work on the parsed document,
not by regexes over the HTML (`go/markdown.go`):

- the `heading` render function wraps each `##`/`###` directly in the page (not one in a quote, a list or a footnote: `sectionHeading`) in `.section-header` with its ✎ link when `renderMd` gets `EditSlug` (the page view;
  numbered as `pageSections` in `go/sections.go` cuts sections, from the same parser),
  and adds the `☑ N` chip from `SectionTasks` to any `##`/`###`;
- `taskDates` turns the task-date comments (`<!--t:… d:…-->`, written by `mdtext.ToggleCheckbox`) into chips;
- `zoneTransformer` handles image widths and `img-block` paragraphs;
- `taskTransformer` gives a list item starting `[ ]`/`[x]` a checkbox; the server ticks the boxes it made, found in the same parse (`taskItems` in `go/checkboxes.go`, Checkbox index mapping in `docs/spec/editor.md`).

### Templates

Every view is a Go `html/template` in `go/templates` (embedded in the binary) that fills the blocks of `layout.html` (head, the dialogs the client code opens, the status badges, top actions and action bar), rendered by `page(w, rq, status, template, title, …)` (`go/views.go`).
Templates escape by context; their functions (`funcs`) include `url` (`pathURL`) and `dict` (a template's arguments).
HTML that Zonewiki built itself is passed as `template.HTML` (rendered markdown, an excerpt with `<mark>`s), anything else is escaped.
Reusable parts are named templates in `templates/partials/` (`page.html`: the ⋯ menu and dialogs, link sections, side columns, meta row; `history.html`; `modules.html`; `icons.html`), which Go code renders with `macro(name, data)` (the breadcrumb, the task list embed).
Markdown-level fragments (task chips, ☑ chips) stay in `go/render.go`.

## Client code

No build step and no bundler: the browser loads ES modules as they are.
Every page loads `public/zonewiki.js` (`<script type="module">` in `layout.html`), which imports the modules in `public/js/` and `tasks.js`; each module wires up what it finds on the page.
They share code by `import`, not through `window`:

| Module | Does |
|---|---|
| `js/forms.js` | Imported first: a submit only from a form where Zonewiki puts one (`submitAllowed`: not from a closed `<dialog>`, nor from a form in rendered text, `[data-rendered]`, but Zonewiki's own task lists'; A click submits only a form Zonewiki put there in `docs/spec/security.md`) |
| `js/util.js` | Small shared helpers: `he`, `localDate`, `pasteUrlAsLink`, `continueList`, `showToast`, `putTask`, `hashTarget`, … |
| `js/mdtext.js` | The text functions the browser shares with the server (Shared text functions, below) |
| `js/completion.js` | `linkAutocomplete`: `[[`, `[[task:` and `[text](` completion (editor and the task app's task fields) |
| `js/chrome.js` | Around the page: navigation trail, backlinks chip, side columns, the Tasks links' counts (`refreshDueBadge`), the menu at the top right (the user menu with users) |
| `js/git.js` | Uncommitted badge, Commit buttons, forms that confirm first (`data-confirm`) |
| `js/search.js` | Search modal and its preview, Notes filter, highlights on a page opened from search |
| `js/actions.js` | New note / page / task (`openDefaultTask`, loading a hidden tasks when the page has none), action bar, sharing mode |
| `js/tasklist.js` | The editor's Task list button (`taskListDialog`): the list settings dialog (`js/tasksettings.js`) writing or changing a `[[tasks: …]]` in the text |
| `js/pagemenu.js` | The page's ⋯ menu and its dialogs (rename, move to wiki, append, delete) |
| `js/listsrow.js` | The Tasks page's lists, the row and on big screens the left column: More (closing, its filter), the column's filter, lists new to this browser, and dragging the pins into another order; draws both again when a list is made on the page (`zone:list-made`) |
| `js/shortcuts.js` | Keyboard shortcuts |
| `js/mdedit.js` | `markdownEditor`: the markdown helpers of the editor and the task app's task description (formatting bar, completion, snippets, lists, uploads) |
| `js/editor.js` | The editor: auto-save, preview, Enter on a wikilink, Make task, rename bar |
| `js/page.js` | Page view: checkboxes, `[[task:…]]` chips, sortable tables, syntax highlighting, lightbox |
| `js/figures.js` | `renderFigures`: math (KaTeX) and Mermaid diagrams, loading each library on first use (Math and diagrams) |
| `js/fold.js` | Folding a page view's sections (`foldAll`, `revealElement`) |
| `js/present.js` | Presentation mode |
| `js/admin.js` | The admin page's Remove dialog, its Save shown once a field is edited (Managing users in `users.md`) |
| `js/namepick.js` | Fields that take users or groups (`data-pick`): those there are, as pills (A group of your own in `users.md`) |
| `js/copyline.js` | Lines to copy (`.copy-line`: Connect an assistant's address, token and commands): shows each one's Copy button and copies its text (`copyText`) |
| `js/assistant.js` | Connect an assistant: a connection's Change… in a dialog (the row's `<details>` without JavaScript), its password field shown only while a choice gives the connection more (Change what a connection may do and see in `mcp.md`) |
| `js/taskutil.js` | What `tasks.js`'s task apps use that holds no state of an app's own (The task app's helpers, below) |
| `js/tasktexts.js` | Task descriptions and comments rendered as pages are, for every task app on the page (`renderTexts`, `renderedHtml`, `textsOf`; `highlightIn`: their code highlighted in place, the server's HTML kept as it came) |
| `tasks.js`, `tasks-core.js` | The task app (exports `initTaskApps`, `openTasksEdit`) and its pure logic (`TasksCore`; The task app's modules, below) |
| `js/taskdata.js`, `js/taskview.js`, `js/taskorder.js`, `js/tasktabs.js`, `js/taskchips.js`, `js/taskrender.js`, `js/taskmodal.js`, `js/taskselect.js`, `js/taskmenu.js`, `js/taskkeys.js`, `js/taskdnd.js`, `js/tasktouch.js`, `js/tasksettings.js` | The task app's parts, each `initTask…(app)` (The task app's modules, below) |
| `js/datepicker.js` | The task app's calendar popup (`openDatePicker`) and `swallowNextClick` |

### Shared text functions

`js/mdtext.js` holds the text functions the browser shares with the server, most the copy of a Go one: `toSlug`, `headingId`, `splitWikilink`/`wikilinkHref`, `queryWords`, `mathFence`, `opensCodeFence`, `toggleCheckboxText` (`mdtext.ToggleCheckbox`), and `taskCheckboxOffsets`, the browser's own count by lines for a card's subtasks.
No DOM and no imports, so Node runs them: `tests/test_text_parity.py` holds them to the answers in `go/testdata/golden.json.gz`, which the Go functions are held to too (the checkboxes' and the ticks' answers are the browser's alone).

### The task app's helpers

- `js/taskutil.js`: date formatting and classes (`formatDate`, `shortDate`, `dueDateClass`, `urgencyClass`, `todayStr`), `DATES`/`LOCALE`, notices and the toast's place, `esc`, the tag autocomplete (`attachTagAc`) and `#` words at the caret, links in a title (`linkifyTitle`, `plainTitle`), comment stamps.
- `js/tasktexts.js`: the HTML the server made of each text (`POST /api/render-notes`), kept per text and fetched once (`renderTexts`, `renderedHtml`, `textsOf`).

### The task app's modules

`tasks.js`: `initTasksInstance` puts one task app together (its elements, the state its parts share, what the embed and the address say it shows, the list's own listeners) and starts its parts.
`tasks-core.js`: its pure logic (`TasksCore`, also run by the Node tests: dates, orders, the filter, what a tab shows, the Plan board's sections, the quick-add shorthand).

The parts, each `initTask…(app)`, called by `initTasksInstance` for each task app:

| Part | Does |
|---|---|
| `taskdata` | the tasks read and written through `/api/tasks` with the Undo toast |
| `taskview` | how the list is shown as the browser remembers it (folds, sort, Plan on or off) and a line's expansion |
| `taskorder` | the manual order and its sections |
| `tasktabs` | the tabs and the filter field |
| `taskchips` | tags typed as chips |
| `taskrender` | drawing the list (`render`: one function per kind of view, the cards' handlers wired after) |
| `taskmodal` | the edit modal |
| `taskselect` | selection and the selection bar with bulk tags |
| `taskmenu` | the ⋯ menus and confirmations |
| `taskkeys` | the keys |
| `taskdnd` | drag and drop with the mouse |
| `tasktouch` | touch gestures |
| `tasksettings` | the settings dialog |

`app` holds what a part uses of the app (its elements and functions, as getters what is set later or changes), `app.st` the state the parts share (`st.tasks`, `st.activeView`, `st.selectedIds` …); what one part alone uses stays its own variable.
A part's functions go back into `app` for the others.
`tests/test_task_app_wiring.py` reads the sources: every `app.name` a part uses is put on `app` by `tasks.js` or returned by a part, and by one of them only.
