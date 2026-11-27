# Editor

Part of Zonewiki's spec (see `AGENTS.md` for the index).

The in-browser editor, as a user meets it: opening it, saving (auto-save and Done), the formatting bar, the smart helpers (completion, emoji, lists, snippets, the task list dialog, checkboxes), section editing, renaming a page, pasting and dropping files, importing a folder of notes (an Obsidian vault), the editor on a phone, and several editors on one page (merge on save, conflicts).
How it works comes last: saves in order, the code the task app shares, section saves, the checkbox index.

## Opening the editor

`/edit/<slug>` renders a split-pane view: a `<textarea>` on the left, a live preview on the right.

- The textarea receives focus automatically on page load.
- It preserves tabs and disables spell-check.
- The live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- **Very wide screens** (≥1800px): the preview pane may grow to 1100px (the writing pane keeps 800px);
  its text keeps the reading width (46rem), and wide tables, code blocks and images alone in a paragraph use the rest.
- **Toolbar**: one line on every screen.
  The slug label shrinks with an ellipsis (full slug in its tooltip) and the buttons keep their size, so a long slug never pushes Done off-screen.
  The toolbar has no Cancel button.

## Saving

### Auto-save while typing

Debounced ~2s after the user stops typing, the editor silently POSTs to `POST /api/save/<slug>`, which writes the file to disk (no git commit, no redirect).
An unsaved-changes indicator (`●` dot in the toolbar) appears while there are unsaved changes and disappears when auto-save completes.

### The Done button

**"Done"** POSTs to `POST /edit/<slug>`, which writes the file, creates a git commit, and redirects to `/page/<slug>`.
Of the saves it is the one that commits the page (auto-save doesn't; a conflict's Reload theirs and Keep mine commit too, see A clash below);
renaming, an uploaded file and tasks made from lines are commits of their own.
Ctrl+S does the same as Done.

### Auto-save on navigate away

On the `pagehide` event, if the textarea content differs from the last auto-saved content, a `fetch(…, { keepalive: true })` POSTs to `POST /api/save/<slug>`.
This silently preserves edits when the user follows a link or presses the back button (no git commit).
Not `sendBeacon`: a page restored from the back/forward cache still gets the answer and its new file hash.

## Formatting bar

One line of buttons between the toolbar and the textarea:
not WYSIWYG,
just markdown help.
The task app's task description has it too, without Task list and Make task (see Description in `docs/spec/tasks.md`).

| Button | What it does | Shortcut |
|--------|--------------|----------|
| Bold | wraps in `**` | **Ctrl+B** |
| Italic | wraps in `_` | **Ctrl+I** |
| Strikethrough | strikes the selection through | |
| Heading | cycles `##` → `###` → none on the cursor's line (the H1 is the title) | |
| Bulleted / numbered / checklist | on all selected lines, replacing another list marker; all of that kind already → removed; blank lines kept | |
| Quote | quotes the lines | |
| Inline code | wraps in backticks | |
| Code block | fences the selected lines | |
| Table | a 2-column template after the current line | |
| Math | inline `$…$` or a `$$` block (see below) | |
| Diagram | a ` ```mermaid ` fence (see below) | |
| Link | selected text → `[text]()` with the page autocomplete open; a selected URL → `[](url)` | **Ctrl+K** |
| Wikilink (`[[]]`) | `[[selection]]`, or a bare `[[` that opens the autocomplete | |
| Task | `[[task:` after the cursor, opening the task completion | |
| Task list | a dialog that writes or changes a `[[tasks: …]]` embed (Task list dialog, below) | |
| Make task | see Task references in `docs/spec/rendering.md` | |
| Attach | file picker → the same upload as paste/drop (`uploadFileAtCursor`) | |

### Buttons in detail

- **Math**: a selection on one line, or the cursor in text, becomes inline `$…$` (toggling);
  selected lines, or the cursor on a blank line, become a `$$` block.
- **Diagram**: the selected lines fenced, or without a selection a small `flowchart LR` after the current line, its edge selected (see Math and diagrams in `docs/spec/rendering.md`).
- **Task list**: the embed goes on its own line after the current one, or the dialog changes the one the cursor is on.
  Inside `[[tasks:` no page completion opens.
  Editor only, as a task's notes show no embed.
- **Wraps toggle** (markers inside or just outside the selection are removed) and keep surrounding spaces outside.
- **A block from the bar** (table, code block, math, diagram, task list) goes on its own lines after the current one,
  with a blank line to the text above it also when the cursor is on an empty line right under text (`insertBlockAfterLine`):
  in the text's paragraph a table or an embed would be its words.

### How the bar fits

The whole bar fits beside the preview from a 1280px window up (compact buttons, the wikilink button `[[]]`);
on narrower screens it scrolls sideways;
under `(pointer: coarse)` the buttons are finger-sized.
Buttons `preventDefault` on pointer/mouse down so the textarea keeps focus and selection (on a phone, the keyboard stays up).
Every change goes through `execCommand('insertText')` (`replaceRange`), so one Ctrl+Z undoes it and preview/auto-save follow as for typing.
The bar is `templates/partials/format_bar.html`, its actions `applyFormat` in `js/mdedit.js`.

## Smart helpers

What the editor does as you type, paste or click, beyond the bar.

### Wikilink autocomplete

The completion (`linkAutocomplete` in `js/completion.js`) is also used by the task app's task title and description.

| You type | It lists |
|----------|----------|
| `[[` | existing page slugs, filtered as you type |
| `[text](` | the same, as `/page/` links |
| `[[task:` (editor) | open tasks instead (see Task references in `docs/spec/rendering.md`) |
| `:` and two letters | emoji (see Emoji, below) |

Arrow keys navigate, Enter or Tab inserts `[[slug]]` (it prevents the key's default, so the editor's own Enter/Tab handling skips it), Escape closes.
Slugs are fetched once from `/api/pages`, shared by every field.

#### Private and group pages in the completion

All of this is with users only (`<body data-users>`): without them `private/` is an ordinary folder, its pages are offered everywhere and there is no new-private offer.

- **On a page in your private folder** (`private/<you>/…`, also in the notes of a private task) the list ends with **New private page: <what you typed>**, which inserts `[[private/<you>/<slug>|<what you typed>]]`:
  wikilinks are absolute, so a private page is named in full, and the link shows your words.
  The offer is the **first** suggestion, so Enter makes the private page, unless what you typed is exactly an existing page's name: that page stays first and the offer follows.
  The offer is left out for a name that exists in your folder, one with `|` or `#`, or one starting `private/`.
  It only inserts text; the page exists once you open the link.
- **On a shared page** (and in a shared task's title or notes) your private pages are not listed at all, and no new-private offer is made:
  other users couldn't follow a link to one (see Private pages in `docs/spec/users.md`).
  To start a private page from a shared page, write `[[private/<you>/name]]` yourself.
- **On a group's page** (`groups/<group>/…`) the first offer is **New page for @<group>: <what you typed>**, in the group's folder,
  and the list has what every reader of the page sees: the shared pages and that group's, not your private ones nor another group's;
  on a shared page a group's pages are not listed (Group pages in `docs/spec/users.md`).

### Emoji

The same `linkAutocomplete`, so in the editor, the task title and the task Description.

- Typing `:` and two letters (`:smi`) lists matching emoji: the exact name first, then names starting with it, names containing it, tags and descriptions (`:happy` finds 😀).
- Enter, Tab or a click inserts the **emoji character itself**, so the file holds 😄, not `:smile:`, and reads the same in any editor, in grep and on GitHub.
- A typed-out shortcode (`:tada:`) turns into its emoji as the closing colon is typed (an unknown name stays as typed).
- The list only opens after a space, the start of a line or an opening bracket, quote or `*_~>`, so `Note:`, `10:30` and `http:` don't open it,
  and not inside code (an open fence above the caret, or an odd number of backticks on the line).
- Sharing mode doesn't hide this list (no page or task names in it).

The names are GitHub's (gemoji v4.1.0, MIT, `public/vendor/gemoji/emoji.json`, as downloaded; to update it, replace the file with a newer release's), fetched the first time `:xx` is typed, then cached by the browser.

### List continuation

- Pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists; `continueList(textarea, e)`).
  Pressing Enter on an empty list item exits the list.
- **Checkbox list continuation**: pressing Enter at the end of a `- [ ] task` or `- [x] task` line inserts `- [ ] ` on the next line (always unchecked).
  Pressing Enter on an empty checkbox item (`- [ ] ` with no text) exits the list.

### URL paste

Pasting a bare URL over selected text makes `[selection](url)`;
otherwise a prompt asks for a label (none, or cancel, inserts the plain URL, which autolinks on render).
The insert goes through `execCommand('insertText')`, so Ctrl+Z undoes it; it once assigned `textarea.value` and wiped the undo history.
It is `pasteUrlAsLink` in `js/util.js`, also used by the task app's task description.

### Snippets

Typing a trigger word then pressing Tab expands it inline.

| Trigger | Expands to |
|---------|------------|
| `/date` | `2026-03-22` |
| `/time` | `HH:MM` |
| `/datetime` | `2026-03-22 HH:MM` |

All in local time (`localDate` in `js/util.js`; `toISOString()` would give UTC's day).
Add more by extending the `snippets` object in `js/mdedit.js` (the task app's description gets them too).

### Task list dialog

The formatting bar's Task list shows **List settings**, the dialog a list's ⋯ menu shows, instead of leaving the params to be typed
(Settings in `docs/spec/tasks.md`: one template, `task_settings_dialog`, and one set of fields, `settingsForm` and `changedParams` in `js/tasksettings.js`; the editor's part is `taskListDialog` in `js/tasklist.js`).

- In the editor it has one field more, **List** first: which list the embed shows (the lists there are to choose from, `editView.TaskLists`; cleaned as `cleanList` does; none: all tasks).
  A name no list has is written as typed: the embed then says so on the page, with a button that makes the list.
- It has no pin (a list is pinned on the Tasks page, by whoever pins it).
- Only what a page says is offered: which tasks, the tab, the grouping and the filter (Task list embed in `docs/spec/tasks.md`).
  What the tasks shown are (WIP limit, tab names, the tags of a new task, keep tasks: `LIST_OWN`) is set in the dialog of the list, of My tasks or of All tasks, and never written into the line;
  what a line says of them from before stays as written.
- Where the list's own dialog says which page it writes to, it shows the line it will write;
  **Insert** (or Enter) writes it, in one undo step, a blank line between it and text above;
  Cancel or Esc writes nothing; either way the cursor is back in the text.
- With the cursor on a line that is a `[[tasks: …]]` already, the dialog is **Change the task list**: the fields show that embed's params,
  and **Change** rewrites the line as the list's own dialog's Save rewrites the page (only what was changed; a param keeps its place, a new one goes last, an emptied one goes),
  so a param without a field stays as written (a `pin=` from before pins were the user's, too).

### Task list checkboxes

`- [ ] task` and `- [x] task` render as interactive checkboxes in page view (Zonewiki's `checkbox` node in `go/markdown.go`, rendered without `disabled`).

- Clicking a checkbox in page view toggles the `[ ]`/`[x]` in the `.md` file and shows an **undo toast** ("Checked “…” · Undo"; undo is a second toggle).
- Ticking also records when: it appends `<!--d:YYYY-MM-DDTHH:MM:SS-->` (or adds ` d:…` to an existing `<!--t:<created>-->`), unticking takes it out again;
  the page shows these comments as small date chips (`taskDates` in `go/markdown.go`).
- In the editor, clicking a checkbox in the live preview edits the textarea via `execCommand('insertText')` (undoable, triggers preview/auto-save).

#### How a tick is saved

A click POSTs to `POST /api/toggle-checkbox/<path:slug>` with `idx` (0-based checkbox index), which toggles the mark and answers the file's new hash (`{file_hash}`) (400 for a missing/non-integer/negative idx).
The tick is written to the file as an auto-save is, and not committed (`writeDraft`): it stays among the page's uncommitted changes until the next commit of the page (Done in the editor, or any other write that commits it), so a burst of ticks is no burst of commits.
The toggle requests are chained (`toggleCheckboxOnServer`), so an undo can never overtake the toggle it reverts.

**A page changed since the view loaded** (someone added an item above, an edit elsewhere):
the article carries a key per checkbox (`data-task-keys`: `taskLineKeys`, its line without the mark and the tick's stamps, so it stays the same once ticked),
and a toggle sends its index and its key;
the server ticks the item at that index if its line has that key, else the one item whose line has it (the page changed since the view loaded),
and if none or several do, answers 409 and the toast says "The page changed: reload to tick this" with a Reload button (the checkbox goes back).
The key is checked on every tick, so a tick after one found elsewhere still goes by its own line.
Which item an index means is the renderer's count (Checkbox index mapping, under How it works).

### Undo

All programmatic text insertions (list continuation, formatting bar, snippets, wikilink autocomplete, URL paste, uploads (Undoing an upload), etc.) use `document.execCommand('insertText')` so they integrate correctly with the browser's native undo stack.

## Section editing

Each `##`/`###` directly in the page has a ✎ button linking to `/edit/<slug>?section=N&h=<key>`.
The editor loads only that section's text, from its heading up to the next `##` or `###`.

- **A ✎ from a page view older than the page** still opens its own section: `h` is a short hash of the heading's line (`sectionKeys`), and `findSection` takes section N if its heading is still that one, else the one section with that heading.
  Gone, or several and none at N: the whole page opens.
  A link without `h` goes by the number.
- **What is a section** is the markdown parser's answer,
  for the ✎s and the editor alike (`pageSections`, `sectionHeading` in `go/sections.go`),
  so the Nth ✎ opens the Nth section: a setext heading (a text line, then `---`) is one,
  a `##` in an HTML comment or a code block isn't,
  and the frontmatter is none of it (it stays in the text before the first section).
- A heading in a quote, a list or a footnote has no ✎, as a section starting there would cut that block in two: edit it with the section around it.

How a section's save finds its place is under Section saves, in How it works.

## Rename

The editor toolbar includes a "Rename" button that reveals an inline rename form (hidden by default);
the page view's ⋯ menu has **Rename…**, which opens a dialog (`#page-rename-dialog`) with the same form plus `from=page`.

- The form has a single text input pre-filled with the current slug, and a "Rename" submit button.
- On submit, it POSTs to `POST /rename/<slug>` with field `new_name` (human-readable name accepted, converted to slug server-side).
- The server renames the file (`<old>.md` → `<new>.md`), re-points every link to it (see Links, below), then redirects to `/page/<new-slug>`.
- Renaming `index` is forbidden: the server returns 400 if the old slug is `index`.
- An unchanged or empty name goes back without an error.

### A name in another place

**A name in another place changes who sees the page** (with users: a private folder, a group's, the wiki), and asks first:
the form comes back with the question in place of an error (`?rename_name=…&rename_ask=…`: who will see it, or who no longer will),
and **Yes, move it** sends it again with `confirmed=<that name>`.
To fewer people it is an admin's move, or that of whoever shared the page while nobody else changed it;
anyone else is refused (Who may move a page to other readers in `docs/spec/users.md`).

### Refused renames

If the new slug already exists, nothing is overwritten: the server redirects back with `?rename_name=…&rename_error=…`
(to the editor, which shows the rename bar with the error and the name typed, or with `from=page` to the page view, whose rename dialog reopens with them);
so does a name in a folder Zonewiki skips (`_…`, `.…`) or another user's private folder.
The new slug is validated with `safePath()` before renaming.
The browser and the MCP tool `rename_page` refuse alike (`renamePageOp` in `go/pageops.go`),
and text the page held that wasn't committed yet is committed first (`Save <slug>`), so the rename's commit only moves the page.

### Links

When a page is renamed, every link to it is re-pointed (`rewritePageLinks()` via `updateReferences()`), so nothing that backlinks count is left broken:

- `[[Old Name]]` / `[[Old Name|Label]]` — matched by comparing the raw or slugified page name to the old slug; only the page-name part is replaced, the `|Label` is kept.
- **A link keeps its words where not all its readers see the page** (`keepWords`): `[[Old Name]]` on a page whose readers don't all see where the page is now (a shared page linking to one moved into a group's folder or renamed there, or to someone's private page) becomes `[[groups/dev/new-name|Old Name]]`,
  since those who don't see the target read the link as plain text (Wikilinks in `docs/spec/rendering.md`), which would otherwise be the new path of a page in a place they don't see.
  A task's links count as a shared page's, a private task's as its user's.
- Markdown links in the forms backlinks recognise: relative (`old`, `../dir/old`, `old.md`) and absolute (`/page/old`).
  Each keeps its style, `#anchor` and title; relative hrefs are recomputed from the linking page's directory.
  Images are never touched.
- Links inside fenced code and inline code are left alone (`mdtext.MaskCode`).
- A page that can't be read or written keeps its links: the rename is done and committed all the same
  (`linksLeft`; the browser goes on to the new page, Zonewiki's log names the page and why, the MCP tools add a note that names neither, as the page may be one the user doesn't see; `check_links` finds what is left).
- **Moved pages keep their own links.** A page that moves into another folder (a rename into a folder, a folder move, a note promoted into a folder) has its relative links re-based,
  through `rewriteMovedLinks()` (`rebaseLinks()` per page): each still reaches the same page or file from where the page is now,
  including links to other pages that moved too.
  Wikilinks name pages, so they only change when their target moves.

## File paste and drag & drop

In the editor, pasting a file from the clipboard or dropping a file onto the textarea automatically uploads it via `POST /api/upload`;
the bar's Attach button does the same from a file picker.
All file types are accepted.
Images are inserted as `![name](url)`; other files as `[name](url)`.
Multiple files dropped at once are uploaded sequentially.

### Undoing an upload

This is the editor's undo of the text; the file stays uploaded, a commit of its own, and Undo on Changes takes that away (Uploads undone in `docs/spec/rendering.md`).
While the file uploads a placeholder stands where it goes (`![uploading…]()`, or `[name]()`), and the link takes its place when the upload answers.
Both go in as typing does (`putText` in `js/mdedit.js`: `execCommand('insertText')`), so Ctrl+Z takes the link back to the placeholder, a second one takes that out, and what was typed before stays undoable; it once set `textarea.value`, which wiped the undo history.
The placeholder is looked for where it was put, then anywhere, since text typed before it meanwhile moves it.
Only where the browser can't insert (the textarea no longer shown, the focus elsewhere) is the text set directly, without an undo step.

### Names and clashes

The upload route uses the original filename when available (falling back to MIME-type guessing).
A pasted image (a screenshot) gets a random name (`unique=1`), so it never clashes.
If a file with that name already exists in the place's `_assets/`, the route answers 409 `{ conflict, filename }` and the editor asks (`askConflict` in `js/mdedit.js`):
keep the name to overwrite the file (`overwrite=1`), type another (`filename`), or cancel.
Only an assistant's upload with its token, which nobody can ask, gets a numeric suffix instead (`freeUploadName`: `file-2.pdf`, `file-3.pdf`, …); it never replaces a file.

### Where the file goes

The editor sends the page's slug along (`page`): with users, a file for a page in your private folder is kept in `private/<you>/_assets/` and only you can open it (see `docs/spec/users.md`).
A file over `upload-max` (413), or one the wiki has no room for under `storage-max` (507), is refused: the placeholder goes, and an alert shows the server's message (Storage limit in `docs/spec/users.md`).

### The link's URL

The file keeps its name; the URL the route answers with has it percent-encoded (`uploadURL`: all but letters, digits and `_.-~`),
`/_assets/My%20Deck%20%28v2%29.pdf` for "My Deck (v2).pdf", because a space in a markdown link's target ends the link and a bracket may.
Links written before that, with the name as it is (`[Deck](/_assets/My Deck (v2).pdf)`, which markdown reads as text), are links all the same:
the renderer encodes the target of a link to `/_assets/…` or `/private/<user>/_assets/…` that has a space in it, up to the file's extension before the closing bracket
(`encodeUploadLinks` in `go/uploads.go`, for pages, previews, task notes and comments; code aside; the file on disk stays as written).

## Importing pages

A folder of markdown notes, an Obsidian vault for one, becomes a folder of pages in the wiki, with the images and files its notes link to.
Every page is new, in a folder that isn't there yet (`my-vault`, `private/<you>/my-vault`, `groups/<group>/my-vault`), and that folder's index page lists them and says what changed on the way.

The rule: rewrite only what Zonewiki would misread, keep the rest as written.
A note whose text needs no change comes over byte for byte.

The conversion is `mdimport.Convert` in `go/internal/mdimport/`: it reads the folder and writes nothing, and makes a plan (the pages' slugs and texts, the files and their names, the index page, the report).
The server's own rules come to it as callbacks (`Options`): which part a page's path may have (`pagePart`), the name a file gets in the place's uploads, and the URL a link to it takes.

### What an import converts

| In | What is converted |
|---|---|
| Any folder | Paths and titles; links between the notes (wikilinks and markdown links, relative ones too); images and files the notes link to; frontmatter |
| An Obsidian vault | Also inline `#tags`, `%%comments%%`, `==highlights==`, embeds (`![[…]]`), block references, the callout kinds |

A folder is an Obsidian vault when it has an `.obsidian/` folder, or when the caller says so (`Options.Obsidian`: an unpacking that left `.obsidian/` out saw it).
In any other folder Obsidian's syntax is left as written.
Notion, Logseq and Dendron exports are folders like any other.

### Paths and titles of imported pages

| A note's path | Becomes |
|---|---|
| `My Note (2024).md` | `my-vault/my-note-2024`: each part slugified (`mdtext.ToSlug`) |
| `Bücher/Lesen.md` | `my-vault/b-cher/lesen`: a slug keeps a–z, 0–9 and `_` |
| `日記.md` | `my-vault/note`: a part with nothing left is `note`, reported |
| `Note.md` and `note.md` | `my-vault/note` and `my-vault/note-2`: a clash gets `-2`, `-3` … in the order of the paths, reported |
| `con.md` | `my-vault/con-`: a part the wiki refuses for a page (`pagePart`: a device's name, a `:`) gets a `-`, reported |
| `index.md` at the folder's root | `my-vault/index-2`: the index page has the name, reported |
| `_templates/Daily.md`, `.drafts/A.md` | `my-vault/templates/daily`, `my-vault/drafts/a`: the wiki skips `_` and `.` folders, so the first character goes |
| `.obsidian/`, `.trash/`, `.git/` | left out, with all in them |
| `.DS_Store` and other hidden files | left out |
| `*.canvas`, `*.base`, `*.excalidraw.md`, `*.excalidraw` | left out, reported |

Notes and folders each have their own names in a folder, so a page `note` and a folder `note/` can stand side by side.

Obsidian's title is the file name, Zonewiki's the first `# ` heading (The title in `docs/spec/rendering.md`):
a note without a `# ` heading (outside its frontmatter and code) gets `# My Note (2024)` on top, after its frontmatter, and a blank line under it.

### Which note or file a link finds

A link's target is looked for in this order, case ignored, `.md` optional and letters composed (a Mac writes `ü` in a file's name as `u` and a mark, a note as one letter), and the first rule that finds a file wins:

1. the path from the folder's root;
2. the path from the linking note's folder (a markdown link, or a wikilink starting `./` or `../`);
3. a path's end (`[[folder/Note]]` finds `a/folder/Note.md`);
4. the base name.

When one rule finds several, the one in the linking note's folder wins, else the shortest path, else the first by name; the link is reported with what it could be and what was chosen.
A markdown link that finds one file from the root and another from its note's folder takes the root's and is reported too.
A markdown link's target may be percent-encoded (`Other%20Note.md`) or in angle brackets (`<Other Note.md>`).

### What imported links become

Links in the wiki are paths from its root (Did you mean in `docs/spec/rendering.md`), so every link to a note is rewritten to its page's full path.
Code is left alone (`mdtext.MaskCode`).

| Written | Becomes |
|---|---|
| `[[My Note]]` | `[[my-vault/my-note\|My Note]]`: the written text is the label |
| `[[My Note#Heading]]` | `[[my-vault/my-note#Heading\|My Note › Heading]]` |
| `[[My Note\|alias]]` | `[[my-vault/my-note\|alias]]` |
| `[[My Note#A#B]]` | `[[my-vault/my-note#B\|My Note › B]]`: the last heading |
| `[[Note#^blockid]]` | a link to the page, reported as lost |
| `[x](Other%20Note.md)` | `[[my-vault/other-note\|x]]`; with no text, the note's name is the label |
| `![[pic.png]]`, `![[pic.png\|300]]`, `![[pic.png\|alt\|300]]` | `![](URL)`, `![\|300](URL)`, `![alt\|300](URL)` (Image sizing in `docs/spec/rendering.md`); a height (`300x200`) goes |
| `![[file.pdf]]`, `[[file.pdf]]` | `[file.pdf](URL)` |
| `![](img/pic.png)`, `[x](files/a.pdf)` | the same link to the file's URL, its title kept |
| `![[Other Note]]`, `![x](Other Note.md)` | a link to the page, reported as lost |
| a link that finds no note or file | left as written (a missing page), reported |
| `[[#Heading]]`, links elsewhere (`https:`, `mailto:` …), `[x](#anchor)` | as written; `[[#^blockid]]` reported as lost |

The URL is the file's in the uploads of the place the pages go to.
A wikilink in a table's row, written with `\|`, keeps `\|` in what it becomes.
`![[…]]` is an embed only in an Obsidian vault; in another folder it stays as written.

### Imported files

Only a file a note links to is copied, into the uploads of the place the pages go to (`_assets/`, `private/<you>/_assets/`, `groups/<group>/_assets/`).
It gets its name there once, in the order of the paths: the same file linked twice is one file, and two files of the same name in different folders get `pic.png` and `pic-2.png` (the server's `freeUploadName`).
A file with no name an upload can have is left out and reported, and links to it stay as written.
A file no note links to is left out and reported.

### Obsidian's own syntax

In an Obsidian vault, outside code:

| Written | Becomes |
|---|---|
| `#tag`, `#a/b` | added to the frontmatter's `tags:` (`mdtext.AddFrontmatterTags`, which writes `tags:` as one line), and left in the text as written; a nested tag keeps its `/` (`a/b`), which the `#a/b` filter finds |
| `%%comment%%` | removed, with a space before it; one on lines of its own goes with its lines |
| `==text==` | `<mark>text</mark>` |
| ` ^blockid` at a line's end, `^blockid` on a line of its own | removed (the line stays, empty) |
| `> [!tip] Title`, `> [!note]-`, `> [!example]` | `> [!TIP]` and `> **Title**` on a line of its own, then an empty `>` line; the fold sign (`-`, `+`) goes; a kind that isn't one of Zonewiki's five gives its name as the title (`**Example**`) |
| ```` ```dataview ```` blocks, `key:: value` fields, Tasks-plugin dates and signs (`📅`, `🔁` …) | as written, counted in the report as lost |
| Math, Mermaid, checkboxes | as written (a checkbox stays a checkbox, not a task) |

A tag is Obsidian's: `#` after a space or at a line's start, then letters, digits, `_`, `-` and `/`, not digits alone (`#123`, `#2024/11`).
So a heading's `# `, a `#` in a URL or in a word, and one in code are no tags.

Obsidian's kinds on Zonewiki's five (Callouts in `docs/spec/rendering.md`):

| Obsidian | Zonewiki |
|---|---|
| note, abstract, summary, tldr, info, todo, question, help, faq, example, quote, cite, any other | NOTE |
| tip, hint, success, check, done | TIP |
| important | IMPORTANT |
| warning, attention | WARNING |
| caution, failure, fail, missing, danger, error, bug | CAUTION |

Frontmatter carries over in every folder: other keys stay, `aliases` too, as a key.

### The import's index page

The index page `<folder>/index` has the folder's name as its title, says what was imported (a folder of markdown notes or an Obsidian vault, how many pages), lists every page by a wikilink to its full path, labelled with the note's path, and keeps the report, a section for each kind that has lines:

| Section | Lists |
|---|---|
| Renamed | a note or folder whose slug isn't its name slugified: a part with nothing left, a clash, a part refused, the index's name |
| Links with several targets | a link that found several, and the one chosen |
| Links to nothing | a link that found no note or file |
| Lost on the way | what has no equivalent: block references, embedded notes, Dataview and Tasks-plugin syntax |
| Left out | files no note links to, files with no name an upload can have, canvases, bases, Excalidraw drawings |

A line names the note's or file's path first, in code (`` `a.md`: `[[Nowhere]]` finds no note or file ``), and the lines are in the order of the paths.
The pages and files of the plan are in that order too.

## Mobile editor

On screens ≤640px the editor is not hidden; it becomes a full-screen stacked layout (textarea on top, preview below when toggled).

- The toolbar gains a **Preview** button (`#mobile-preview-btn`, class `mobile-only`) that calls the same `togglePreview()` as F2.
- The textarea font is 16px on mobile so iOS Safari doesn't zoom in on focus.
- Rename… and Delete move into a **⋯ dropdown** (`#editor-more-btn`, `.editor-more`) so the toolbar fits on one line;
  on wider screens `.editor-more` is `display: contents`, so the buttons stay inline.
- The formatting bar scrolls sideways, its buttons finger-sized, and keeps the keyboard up (How the bar fits, above).

## Several editors

What happens when a page changes under an open editor: another editor's save, an assistant's write, an edit on disk.

### Merge on save

A save onto a page that changed since the editor loaded it is merged line by line from the text the editor started from, when the two changed different lines.

- Auto-save merges only when the editor sends `merge=1` (it then takes the merged text into its textarea; an editor that doesn't would undo the other's change with its next save),
  and answers `{file_hash, merged: true, by, content, sent_hash}`
  (`content` the section's, with `start_char`/`end_char` and `sent_start`/`sent_end`, for a section edit;
  `by` whose change it met, with users: the draft's author or the file's last commit's;
  `sent_hash` the save's own text as a whole page, kept as a base).
- Done merges too and commits the result.
- A section edit is spliced into the text it came from, merged as a whole page, and gets its new range: the one section whose heading line is the one the editor's text starts with (`findSection`).

**No merge** (409 or Done's `?conflict=1`, as before; the 409 says `by`, gives the file's hash, kept as a base, and for a whole page `conflict`, the merged text with git's markers) when:
the same lines changed on both sides,
the hash's text is no longer kept (an old tab, a restart),
there is no git,
or a section's heading isn't there exactly once afterwards.
Then the banner shows (A clash, below).

#### The texts merged from

Zonewiki keeps the texts behind recent hashes (`go/merge.go`) in two lists,
so a busy page's auto-saves don't push out the text an idle editor started from:
the texts handed out (`keepGivenBase`: every text `/edit` gives, read_page's, a conflict's) and the texts saves write (`keepBase`),
each the 200 most recent,
at most 20 MB,
and merges with `git merge-file` (`mergeText`, `mergeSave`).

### A merged save in the editor

A merged answer goes into the textarea if it still holds what was sent, as one undoable insert over only the lines that differ (common start and end kept),
the cursor and selection where they were relative to the text around (`takeMerged`, `replaceChangedLines` in `js/editor.js`);
a note in the toolbar says "Merged bob's changes" (or "Merged changes made meanwhile") for 5 seconds (`#merge-note`).
Typed on meanwhile: the textarea is left alone, the editor takes `sent_hash` (and range) as its hash, and its next save, 2 s later, merges again from the text it sent;
it settles at the first pause in typing.

### A clash

The banner for a clash says "bob changed the same lines while you were editing." (or "This page was changed …" without users);
after Done's conflict the editor asks once with an auto-save for the details (`conflictInfo`).
It offers three buttons.

#### Reload theirs

First commits your text as yours (`set_aside=1` on `/api/save`: your text, whole or spliced in where its section is now (`spliceIntoPage`;
at the page's end if that can't be told, so nothing is replaced), committed as `Set aside an edit of <slug>`, then the page back as it was, `Reload <slug>`;
without git nothing to commit),
then reloads,
so what you typed is in the page's history,
not lost.

#### Keep mine

Overwrites (`force_section` for a section, or no hash; `overwrite=1`).
A section goes where its heading is now, the line the editor keeps in `section_heading`, found by `findSection`: never by its number alone, as a section added above moves the numbers;
if that can't be told, the heading gone or several like it, nothing is written, a 409 `section_gone` the banner explains.
Whatever the page held that was never in git is committed first (only for `overwrite=1` or `force_section`, so a new page's saves, which have no hash yet, commit nothing),
by its author or Zonewiki (`keepPending`, as before a delete, restore or rename: Commits in `docs/spec/users.md`).

#### Merge by hand

Whole pages only: the textarea gets the merged text with git's markers (`<<<<<<< yours` / `=======` / `>>>>>>> bob`) as one undoable insert, the cursor at the first;
auto-save waits until no marker line is left, then saves from the hash of the file the merge was made with;
Done with markers left asks first.

### Leaving the page in conflict

The save on `pagehide` sends `leaving=1` (and a section's `section_idx`);
with the banner up or markers left it sends `keep_left=1` instead, which keeps the text the same way without writing the page;
refused, its text can't wait for a banner, and writing it would lose the other's change.
So it is kept for its user (`keepLeftDraft`, `leaveDraft` in `go/merge.go`): `.zone/drafts/<user>/<slug>.json` (`_` without users; not a page, not in git, shown to no one else),
the whole page as left (a section spliced into the text it came from, else into the page where its section is now, or at the end: `spliceIntoPage`) with that text as its base.

The user's next `/edit` of the page (a section editor redirects to the whole page) shows the banner: "You left changes here (date) that clash with a change made since.",
with Reload theirs (the left text set aside into git),
Keep mine (the left text over the page) and Merge by hand (the left text merged with the page from its base, with markers; also after a restart, as the base is in the file).
Any save of the page by the user takes the kept text away (as does a page that holds it already);
after 30 days it goes by itself (`draftLeft`, and `cleanLeftDrafts` at start).

### Another user's draft

With users: before your save onto a page someone else auto-saved and didn't commit,
their draft is committed in their name (`Save <slug>`, `commitOthersDraft`; Commits in `docs/spec/users.md`),
so your Done never commits their text as yours,
and Keep mine can't lose it for good.

### An AI assistant writing the open page

Over MCP, its write tools first commit the draft the editor auto-saved, as its author's `Save <slug>` (see Pages open in the editor in `docs/spec/mcp.md`), so the draft is neither part of the assistant's commit nor lost to its overwrite.
The editor's next save then carries a stale hash and is merged with the assistant's change (Merge on save, above), or, where they clash, shows the banner.

## How it works

### Saves run one at a time

`queueSave` in `js/editor.js` queues every save (auto-save, Done/Ctrl+S, Enter on a wikilink, the banner's buttons): each sends the file hash the previous save returned.
Two in flight at once (a slow connection, or Done right after an auto-save started) both sent the older hash, and the second was refused with the "modified on disk" banner.
Ctrl+S uses `requestSubmit()` so it goes through the same submit handler; `form.submit()` skipped it.

### Shared with the task app's task description

The editor's markdown helpers live in `js/mdedit.js` (`markdownEditor(textarea, opts)`): completion,
the formatting bar and its shortcuts,
snippets,
list continuation,
Enter on a selection,
file paste/drop/attach,
URL paste,
and ticking a preview's checkbox (`toggleCheckboxInTextarea`).
`js/editor.js` adds what only a page has: auto-save, the preview pane, Enter following a wikilink, Make task.

### Section saves

- On GET, the server computes a `file_hash` (SHA-256 prefix) and the section's `(start_char, end_char)` character range within the full file, embedding them as hidden form fields.
- On save (auto-save or Done), the server verifies the hash matches the current file, then does a direct string-slice replacement at `[start_char:end_char]`.
  If the file changed on disk (hash mismatch), the save is merged when it can be (Merge on save, above), else **rejected**:
  auto-save gets a 409 JSON error; Done redirects with `?conflict=1` (the editor, which sends Done with fetch, shows the banner in place).
- After each auto-save, the server returns updated `{ file_hash, start_char, end_char }` so the next auto-save uses fresh offsets.
  (A whole page's save answers `{ file_hash }`, also one sent without a hash: a new page's first save, Keep mine.)
- The section's trailing blank lines are preserved on every splice (the editor loads the section with them stripped), including the conflict banner's Keep mine (`force_section`), so the next heading never gets glued onto the section's last line.
- This design avoids re-splitting the file on every save and eliminates the old index-based merge logic that could silently lose sections.

### Checkbox index mapping

**The server ticks the boxes the page shows, by construction.**
Only task-list items get a box: a list item whose first paragraph starts with `[ ]`/`[x]`, whitespace and text (`taskTransformer`); brackets in prose or later on the same line are not checkboxes.
The server finds them in the parse the page renders from, as `go/sections.go` finds headings (`taskItems` in `go/checkboxes.go`: the parser's `checkbox` nodes, each mapped back to its mark in the file), and numbers, keys (`taskLineKeys`) and ticks (`toggleNthCheckbox`) by that, for a page view and a task's notes alike.
So a `- [ ]` line the page shows no box for (in an HTML comment, raw HTML, indented code, a fence inside a quote or a list item, as well as fenced code, display math and the frontmatter) shifts no number: the first ones once did, when the server counted lines, and a tick went to the hidden line before the box ticked.

**The editor's preview, and a task's description preview, tick the box the server rendered too.**
With what it renders the server says where each box's mark is in the text it was sent (`taskMarkOffsets`, from the same parse, counted in the browser's characters, UTF-16 code units): `/api/preview` in the header `Zone-Task-Marks` (`12,57`), `/api/render-notes` as `marks`, one list a note.
A click on the Nth box of the preview ticks the textarea at the Nth mark (`toggleCheckboxInTextarea` in `js/mdedit.js`, `toggleCheckboxText` in `js/mdtext.js` for the description, whose preview is of the trimmed text).
When the text has changed since the preview was made (typed in the moment before), the click changes nothing and the box goes back: the preview that follows the text has its own marks.
The browser counts boxes itself only for a card's count of subtasks (`taskCheckboxOffsets` in `js/mdtext.js`, by lines: fenced code, display math and a leading frontmatter block skipped, but not a comment, raw HTML, indented code or a fence in a quote), which ticks nothing.
