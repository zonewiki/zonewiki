# Editor

Part of Zone's spec (see `AGENTS.md` for the index).

## In-Browser Editor

- `/edit/<slug>` renders a split-pane view: `<textarea>` on the left, live preview on the right.
- Live preview updates as the user types (debounced, ~300ms) by calling `POST /api/preview`, which returns rendered HTML.
- **Very wide screens** (≥1800px): the preview pane may grow to 1100px (the writing pane keeps 800px); its text keeps the reading width (46rem), wide tables, code blocks and images alone in a paragraph use the rest.
- **Toolbar** — one line on every screen: the slug label shrinks with an ellipsis (full slug in its tooltip) and the buttons keep their size, so a long slug never pushes Done off-screen.
- **Formatting bar** (`templates/partials/format_bar.html`, `applyFormat` in `js/mdedit.js`; the task app's task description has it too, without Task list and Make task, see Description in `docs/spec/tasks.md`): one line of buttons between the toolbar and the textarea,
  not WYSIWYG,
  just markdown help: bold (`**`), italic (`_`), strikethrough, heading (cycles `##` → `###` → none on the cursor's line; the H1 is the title), bulleted/numbered/checklist (on all selected lines, replacing another list marker; all of that kind already → removed; blank lines kept), quote, inline code, code block (fences the selected lines), table (a 2-column template after the current line), math (a selection on one line, or the cursor in text, as inline `$…$`, toggling; selected lines, or the cursor on a blank line, as a `$$` block), diagram (a ` ```mermaid ` fence: the selected lines fenced, or without a selection a small `flowchart LR` after the current line, its edge selected; see Math and diagrams in `docs/spec/rendering.md`), link (selected text → `[text]()` with the page autocomplete open; a selected URL → `[](url)`), wikilink (`[[selection]]`, or a bare `[[` that opens the autocomplete), task (`[[task:` after the cursor, opening the task completion), task list (a dialog for what the list shows, which writes the `[[tasks: …]]` embed on its own line after the current one, or changes the one the cursor is on: see Task list dialog below; inside `[[tasks:` no page completion opens; editor only, as a task's notes show no embed), make task (see Task references), attach (file picker → the same upload as paste/drop, `uploadFileAtCursor`). Wraps toggle (markers inside or just outside the selection are removed) and keep surrounding spaces outside. **Ctrl+B / Ctrl+I / Ctrl+K** do bold, italic, link. Every change goes through `execCommand('insertText')` (`replaceRange`), so one Ctrl+Z undoes it and preview/auto-save follow as for typing. Buttons `preventDefault` on pointer/mouse down so the textarea keeps focus and selection (on a phone, the keyboard stays up). The whole bar fits beside the preview from a 1280px window up (compact buttons, the wikilink button `[[]]`); on narrower screens it scrolls sideways; under `(pointer: coarse)` the buttons are finger-sized.
- **Mobile editor** — on screens ≤640px the editor is not hidden; it becomes a full-screen stacked layout (textarea on top, preview below when toggled).
  The toolbar gains a **Preview** button (`#mobile-preview-btn`, class `mobile-only`) that calls the same `togglePreview()` as F2. The textarea font is 16px on mobile so iOS Safari doesn't zoom in on focus.
  Rename… and Delete move into a **⋯ dropdown** (`#editor-more-btn`, `.editor-more`) so the toolbar fits on one line; on wider screens `.editor-more` is `display: contents`, so the buttons stay inline.
- The editor textarea preserves tabs and disables spell-check.
- The textarea receives focus automatically on page load.
- **Auto-save while typing** — debounced ~2s after the user stops typing, the editor silently POSTs to `POST /api/save/<slug>`, which writes the file to disk (no git commit, no redirect).
  An unsaved-changes indicator (`●` dot in the toolbar) appears while there are unsaved changes and disappears when auto-save completes.
- **"Done" button** — POSTs to `POST /edit/<slug>`, which writes the file, creates a git commit, and redirects to `/page/<slug>`.
  This is the only editor action that commits.
  The toolbar has no Cancel button.
- **Auto-save on navigate away** — on the `pagehide` event, if the textarea content differs from the last auto-saved content, a `fetch(…, { keepalive: true })` POSTs to `POST /api/save/<slug>`.
  This silently preserves edits when the user follows a link or presses the back button (no git commit).
  Not `sendBeacon`: a page restored from the back/forward cache still gets the answer and its new file hash.
- **Saves run one at a time** (`queueSave` in `js/editor.js`: auto-save, Done/Ctrl+S, Enter on a wikilink, the banner's buttons): each sends the file hash the previous save returned.
  Two in flight at once (a slow connection, or Done right after an auto-save started) both sent the older hash, and the second was refused with the "modified on disk" banner.
  Ctrl+S uses `requestSubmit()` so it goes through the same submit handler; `form.submit()` skipped it.
- **Merge on save**: a save onto a page that changed since the editor loaded it (another editor's save, an assistant's write, an edit on disk) is merged line by line from the text the editor started from, when the two changed different lines.
  Zone keeps the texts behind recent hashes (`go/merge.go`) in two lists,
  so a busy page's auto-saves don't push out the text an idle editor started from: the texts handed out (`keepGivenBase`: every text `/edit` gives, read_page's, a conflict's) and the texts saves write (`keepBase`),
  each the 200 most recent,
  at most 20 MB,
  and merges with `git merge-file` (`mergeText`, `mergeSave`).
  A section edit is spliced into the text it came from, merged as a whole page, and gets its new range: the one section whose heading line is the one the editor's text starts with (`findSection`).
  Auto-save merges only when the editor sends `merge=1` (it then takes the merged text into its textarea; an editor that doesn't would undo the other's change with its next save),
  and answers `{file_hash, merged: true, by, content, sent_hash}` (`content` the section's, with `start_char`/`end_char` and `sent_start`/`sent_end`, for a section edit;
  `by` whose change it met, with users: the draft's author or the file's last commit's;
  `sent_hash` the save's own text as a whole page, kept as a base).
  Done merges too and commits the result.
  **No merge** (409 or Done's `?conflict=1`, as before; the 409 says `by`, gives the file's hash, kept as a base, and for a whole page `conflict`, the merged text with git's markers): the same lines changed on both sides,
  the hash's text no longer kept (an old tab, a restart),
  no git,
  a section whose heading isn't there exactly once afterwards.
  - **In the editor** (`takeMerged`, `replaceChangedLines` in `js/editor.js`): a merged answer goes into the textarea if it still holds what was sent, as one undoable insert over only the lines that differ (common start and end kept), the cursor and selection where they were relative to the text around;
    a note in the toolbar says "Merged bob's changes" (or "Merged changes made meanwhile") for 5 seconds (`#merge-note`).
    Typed on meanwhile: the textarea is left alone, the editor takes `sent_hash` (and range) as its hash, and its next save, 2 s later, merges again from the text it sent; it settles at the first pause in typing.
  - **The banner for a clash** ("bob changed the same lines while you were editing.", or "This page was changed …" without users;
    after Done's conflict the editor asks once with an auto-save for the details, `conflictInfo`): **Reload theirs** first commits your text as yours (`set_aside=1` on `/api/save`: your text, whole or spliced in where its section is now (`spliceIntoPage`;
    at the page's end if that can't be told, so nothing is replaced), committed as `Set aside an edit of <slug>`, then the page back as it was, `Reload <slug>`;
    without git nothing to commit),
    then reloads,
    so what you typed is in the page's history,
    not lost.
    **Keep mine** overwrites (`force_section` for a section, or no hash;
    `overwrite=1`;
    a section goes where its heading is now, the line the editor keeps in `section_heading`, found by `findSection`: never by its number alone, as a section added above moves the numbers;
    if that can't be told, the heading gone or several like it, nothing is written, a 409 `section_gone` the banner explains),
    and whatever the page held that was never in git is committed first (only for `overwrite=1` or `force_section`, so a new page's saves, which have no hash yet, commit nothing),
    by its author or Zone (`keepPending`, as before a delete, restore or rename: Commits in `docs/spec/users.md`).
    **Merge by hand** (whole pages only): the textarea gets the merged text with git's markers (`<<<<<<< yours` / `=======` / `>>>>>>> bob`) as one undoable insert, the cursor at the first;
    auto-save waits until no marker line is left, then save from the hash of the file the merge was made with;
    Done with markers left asks first.
  - **Leaving the page in conflict**: the save on `pagehide` sends `leaving=1` (and a section's `section_idx`);
    with the banner up or markers left it sends `keep_left=1` instead, which keeps the text the same way without writing the page;
    refused, its text can't wait for a banner, and writing it would lose the other's change.
    So it is kept for its user (`keepLeftDraft`, `leaveDraft` in `go/merge.go`): `.zone/drafts/<user>/<slug>.json` (`_` without users; not a page, not in git, shown to no one else),
    the whole page as left (a section spliced into the text it came from, else into the page where its section is now, or at the end: `spliceIntoPage`) with that text as its base.
    The user's next `/edit` of the page (a section editor redirects to the whole page) shows the banner: "You left changes here (date) that clash with a change made since.",
    with Reload theirs (the left text set aside into git),
    Keep mine (the left text over the page) and Merge by hand (the left text merged with the page from its base, with markers; also after a restart, as the base is in the file).
    Any save of the page by the user takes the kept text away (as does a page that holds it already); after 30 days it goes by itself (`draftLeft`, and `cleanLeftDrafts` at start).
- **Another user's draft** (with users): before your save onto a page someone else auto-saved and didn't commit,
  their draft is committed in their name (`Save <slug>`, `commitOthersDraft`; Commits in `docs/spec/users.md`),
  so your Done never commits their text as yours,
  and Keep mine can't lose it for good.
- **An AI assistant writing the open page** (MCP): its write tools first commit the draft the editor auto-saved, as its author's `Save <slug>` (see Pages open in the editor in `docs/spec/mcp.md`), so the draft is neither part of the assistant's commit nor lost to its overwrite.
  The editor's next save then carries a stale hash and is merged with the assistant's change (Merge on save above), or, where they clash, shows the banner.
- **Section editing** — each `##`/`###` directly in the page has a ✎ button linking to `/edit/<slug>?section=N&h=<key>`.
  The editor loads only that section's text, from its heading up to the next `##` or `###`.
  - **A ✎ from a page view older than the page** still opens its own section: `h` is a short hash of the heading's line (`sectionKeys`), and `findSection` takes section N if its heading is still that one, else the one section with that heading.
    Gone, or several and none at N: the whole page opens.
    A link without `h` goes by the number.
  - What is a section is the markdown parser's answer,
    for the ✎s and the editor alike (`pageSections`, `sectionHeading` in `go/sections.go`),
    so the Nth ✎ opens the Nth section: a setext heading (a text line, then `---`) is one,
    a `##` in an HTML comment or a code block isn't,
    and the frontmatter is none of it (it stays in the text before the first section).
    A heading in a quote, a list or a footnote has no ✎, as a section starting there would cut that block in two: edit it with the section around it.
  - On GET, the server computes a `file_hash` (SHA-256 prefix) and the section's `(start_char, end_char)` character range within the full file, embedding them as hidden form fields.
  - On save (auto-save or Done), the server verifies the hash matches the current file, then does a direct string-slice replacement at `[start_char:end_char]`.
    If the file changed on disk (hash mismatch), the save is merged when it can be (Merge on save above), else **rejected**: auto-save gets a 409 JSON error; Done redirects with `?conflict=1` (the editor, which sends Done with fetch, shows the banner in place).
  - After each auto-save, the server returns updated `{ file_hash, start_char, end_char }` so the next auto-save uses fresh offsets.
    (A whole page's save answers `{ file_hash }`, also one sent without a hash: a new page's first save, Keep mine.)
  - The section's trailing blank lines are preserved on every splice (the editor loads the section with them stripped), including the conflict banner's Keep mine (`force_section`), so the next heading never gets glued onto the section's last line.
  - This design avoids re-splitting the file on every save and eliminates the old index-based merge logic that could silently lose sections.
- **Undo** — all programmatic text insertions (list continuation, formatting bar, snippets, wikilink autocomplete, etc.) use `document.execCommand('insertText')` so they integrate correctly with the browser's native undo stack.

### Editor smart helpers

- **Wikilink autocomplete** (`linkAutocomplete` in `js/completion.js`,
  also used by the task app's task title and description) — typing `[[` opens a dropdown of existing page slugs filtered as you type; `[text](` the same as `/page/` links; in the editor `[[task:` lists open tasks instead (see Task references). Arrow keys navigate, Enter or Tab inserts `[[slug]]` (it prevents the key's default, so the editor's own Enter/Tab handling skips it), Escape closes. Slugs are fetched once from `/api/pages`, shared by every field. **On a page in your private folder** (`private/<you>/…`, also in the notes of a private task) the list ends with **New private page: <what you typed>**, which inserts `[[private/<you>/<slug>|<what you typed>]]`: wikilinks are absolute, so a private page is named in full, and the link shows your words. The offer is the **first** suggestion, so Enter makes the private page, unless what you typed is exactly an existing page's name: that page stays first and the offer follows. The offer is left out for a name that exists in your folder, one with `|` or `#`, or one starting `private/`. It only inserts text; the page exists once you open the link. On a **shared** page (and in a shared task's title or notes) your private pages are not listed at all, and no new-private offer is made: other users couldn't follow a link to one (see Private pages in `docs/spec/users.md`). To start a private page from a shared page, write `[[private/<you>/name]]` yourself. All of this is with users only (`<body data-users>`): without them `private/` is an ordinary folder, its pages are offered everywhere and there is no new-private offer.
- **Emoji** (the same `linkAutocomplete`, so in the editor, the task title and the task Description): typing `:` and two letters (`:smi`) lists matching emoji, the exact name first, then names starting with it, names containing it, tags and descriptions (`:happy` finds 😀);
  Enter, Tab or a click inserts the **emoji character itself**, so the file holds 😄, not `:smile:`, and reads the same in any editor, in grep and on GitHub.
  A typed-out shortcode (`:tada:`) turns into its emoji as the closing colon is typed (an unknown name stays as typed).
  The list only opens after a space, the start of a line or an opening bracket, quote or `*_~>`, so `Note:`, `10:30` and `http:` don't open it, and not inside code (an open fence above the caret, or an odd number of backticks on the line).
  The names are GitHub's (gemoji v4.1.0, MIT, `public/vendor/gemoji/emoji.json`, as downloaded; to update it, replace the file with a newer release's), fetched the first time `:xx` is typed, then cached by the browser.
  Sharing mode doesn't hide this list (no page or task names in it).
- **Shared with the task app's task description**: the editor's markdown helpers live in `js/mdedit.js` (`markdownEditor(textarea, opts)`): completion,
  the formatting bar and its shortcuts,
  snippets,
  list continuation,
  Enter on a selection,
  file paste/drop/attach,
  URL paste,
  and ticking a preview's checkbox (`toggleCheckboxInTextarea`).
  `js/editor.js` adds what only a page has: auto-save, the preview pane, Enter following a wikilink, Make task.
- **List continuation** (`continueList(textarea, e)`) — pressing Enter at the end of a list item (`- `, `* `, `1. `) inserts the next marker automatically (incrementing the number for ordered lists).
  Pressing Enter on an empty list item exits the list.
- **File paste and drag & drop** — pasting a file from the clipboard or dropping files onto the editor textarea uploads them via `POST /api/upload`.
  Images insert as `![name](url)`, other files as `[name](url)`.
- **URL paste** (`pasteUrlAsLink` in `js/util.js`, also used by the task app's task description): pasting a bare URL over selected text makes `[selection](url)`; otherwise a prompt asks for a label (none, or cancel, inserts the plain URL, which autolinks on render).
  The insert goes through `execCommand('insertText')`, so Ctrl+Z undoes it; it once assigned `textarea.value` and wiped the undo history.
- **Snippets** — typing a trigger word then pressing Tab expands it inline.
  Built-in snippets: `/date` → `2026-03-22`, `/time` → `HH:MM`, `/datetime` → `2026-03-22 HH:MM`, all in local time (`localDate` in `js/util.js`; `toISOString()` would give UTC's day).
  Add more by extending the `snippets` object in `js/mdedit.js` (the task app's description gets them too).
- **Task list dialog** (`taskListDialog` in `js/tasklist.js`): the formatting bar's Task list shows **List settings**, the dialog a list's ⋯ menu shows (Settings in `docs/spec/tasks.md`: one template, `task_settings_dialog`, and one set of fields, `settingsForm` and `changedParams` in `js/tasksettings.js`), instead of leaving the params to be typed.
  In the editor it has two fields more, which make a list where the list's own dialog only changes one:
  **List** first (a name makes it a list of its own, with the lists there are to choose from, `editView.TaskLists`; cleaned as `cleanList` does; none: all tasks) and **New tasks are keep tasks** (`keep=1`).
  Where the list's own dialog says which page it writes to, it shows the line it will write; **Insert** (or Enter) writes it, in one undo step, a blank line between it and text above; Cancel or Esc writes nothing; either way the cursor is back in the text.
  With the cursor on a line that is a `[[tasks: …]]` already, the dialog is **Change the task list**: the fields show that embed's params, and **Change** rewrites the line as the list's own dialog's Save rewrites the page (only what was changed; a param keeps its place, a new one goes last, an emptied one goes, `pin` replaces an old `home`), so a param without a field stays as written.
- **A block from the bar** (table, code block, math, diagram, task list) goes on its own lines after the current one, with a blank line to the text above it also when the cursor is on an empty line right under text (`insertBlockAfterLine`): in the text's paragraph a table or an embed would be its words.
- **Task list checkboxes** — `- [ ] task` and `- [x] task` render as interactive checkboxes in page view (rendered via `mdit-py-plugins` `tasklists_plugin` with `enabled=True`, so they are not disabled).
  Clicking a checkbox POSTs to `POST /api/toggle-checkbox/<path:slug>` with `idx` (0-based checkbox index), which toggles the `[ ]`/`[x]` in the `.md` file and answers the file's new hash (`{file_hash}`).
  **A page changed since the view loaded** (someone added an item above, an edit elsewhere): the article carries a key per checkbox line (`data-task-keys`: `mdtext.TaskLineKeys`, the line without its mark and the tick's stamps, so it stays the same once ticked),
  and a toggle sends its index and its key;
  the server ticks the item at that index if its line has that key, else the one item whose line has it (the page changed since the view loaded), and if none or several do, answers 409 and the toast says "The page changed: reload to tick this" with a Reload button (the checkbox goes back).
  The key is checked on every tick, so a tick after one found elsewhere still goes by its own line.
  Ticking also records when: it appends `<!--d:YYYY-MM-DDTHH:MM:SS-->` (or adds ` d:…` to an existing `<!--t:<created>-->`), unticking takes it out again; the page shows these comments as small date chips (`render._render_task_dates`) (400 for a missing/non-integer/negative idx).
  In page view each toggle shows an **undo toast** ("Checked “…” · Undo"; undo is a second toggle).
  The toggle requests are chained (`toggleCheckboxOnServer`), so an undo can never overtake the toggle it reverts.
  In the editor, clicking a checkbox in the live preview edits the textarea via `execCommand('insertText')` (undoable, triggers preview/auto-save).
  - **Index mapping must match the renderer.** Only task-list items count: a list marker (`-`/`*`/`+`/`1.`/`1)`, optionally inside `>` blockquotes) followed by `[ ]`/`[x]`, whitespace and text.
    Fenced code and a leading frontmatter block are skipped; brackets in prose or later on the same line are not checkboxes.
    Server: `taskItemRE` / `mdtext.TaskItemLines()`; client: `taskItemRE` / `taskCheckboxOffsets()` in `js/page.js`.
    Keep the two in sync.
- **Checkbox list continuation** — pressing Enter at the end of a `- [ ] task` or `- [x] task` line inserts `- [ ] ` on the next line (always unchecked).
  Pressing Enter on an empty checkbox item (`- [ ] ` with no text) exits the list.

### Rename

- The editor toolbar includes a "Rename" button that reveals an inline rename form (hidden by default); the page view's ⋯ menu has **Rename…**, which opens a dialog (`#page-rename-dialog`) with the same form plus `from=page`.
- The form has a single text input pre-filled with the current slug, and a "Rename" submit button.
- On submit, POSTs to `POST /rename/<slug>` with field `new_name` (human-readable name accepted, converted to slug server-side).
- Server renames the file (`<old>.md` → `<new>.md`), re-points every link to it (see Links below; a shared page can't be renamed into a private folder: it is refused, `docs/spec/users.md`), then redirects to `/page/<new-slug>`.
- Renaming `index` is forbidden — the server returns 400 if the old slug is `index`.
- If the new slug already exists, nothing is overwritten: the server redirects back with `?rename_name=…&rename_error=…` (to the editor, which shows the rename bar with the error and the name typed, or with `from=page` to the page view, whose rename dialog reopens with them);
  so does a name in a folder Zone skips (`_…`, `.…`) or another user's private folder.
  An unchanged or empty name goes back without an error.
  The browser and the MCP tool `rename_page` refuse alike (`renamePageOp` in `go/pageops.go`), and text the page held that wasn't committed yet is committed first (`Save <slug>`), so the rename's commit only moves the page.
- Validate new slug with `safePath()` before renaming.

**Links** — when a page is renamed, every link to it is re-pointed (`rewritePageLinks()` via `updateReferences()`), so nothing that backlinks count is left broken:
- `[[Old Name]]` / `[[Old Name|Label]]` — matched by comparing the raw or slugified page name to the old slug; only the page-name part is replaced, the `|Label` is kept.
- Markdown links in the forms backlinks recognise: relative (`old`, `../dir/old`, `old.md`) and absolute (`/page/old`).
  Each keeps its style, `#anchor` and title; relative hrefs are recomputed from the linking page's directory.
  Images are never touched.
- Links inside fenced code and inline code are left alone (`mdtext.MaskCode`).
- A page that can't be read or written keeps its links: the rename is done and committed all the same (`linksLeft`; the browser goes on to the new page, Zone's log names the page, the MCP tools add a note; `check_links` finds what is left).
- **Moved pages keep their own links.** A page that moves into another folder (a rename into a folder, a folder move, a note promoted into a folder) has its relative links re-based,
  through `rewriteMovedLinks()` (`rebaseLinks()` per page): each still reaches the same page or file from where the page is now,
  including links to other pages that moved too.
  Wikilinks name pages, so they only change when their target moves.

### File paste and drag & drop

In the editor, pasting a file from the clipboard or dropping a file onto the textarea automatically uploads it via `POST /api/upload`.
All file types are accepted.
Images are inserted as `![name](url)`; other files as `[name](url)`.
Multiple files dropped at once are uploaded sequentially.
The upload route uses the original filename when available (falling back to MIME-type guessing).
The file keeps its name; the URL the route answers with has it percent-encoded (`uploadURL`: all but letters, digits and `_.-~`), `/_assets/My%20Deck%20%28v2%29.pdf` for "My Deck (v2).pdf", because a space in a markdown link's target ends the link and a bracket may.
Links written before that, with the name as it is (`[Deck](/_assets/My Deck (v2).pdf)`, which markdown reads as text), are links all the same: the renderer encodes the target of a link to `/_assets/…` or `/private/<user>/_assets/…` that has a space in it, up to the file's extension before the closing bracket (`encodeUploadLinks` in `go/uploads.go`, for pages, previews, task notes and comments; code aside; the file on disk stays as written).
If a file with that name already exists in `_assets/`, a numeric suffix is appended (`file-2.pdf`, `file-3.pdf`, …).
The editor sends the page's slug along (`page`): with users, a file for a page in your private folder is kept in `private/<you>/_assets/` and only you can open it (see `docs/spec/users.md`).
A file over `upload-max` (413), or one the wiki has no room for under `storage-max` (507), is refused: the placeholder goes, and an alert shows the server's message (Storage limit in `docs/spec/users.md`).
