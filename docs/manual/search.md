# Search

One search finds your pages, notes and tasks, by title and by what they say.
This page covers the search dialog, the full results page, the search syntax (phrases, tags, dates, leaving words out) and who finds what.
Search is also the quickest way to jump to a page you know.

## Open the search

Press `Ctrl+P` (`⌘P` on a Mac), or click **Search** at the top right.
On a phone, tap **Search** in the tab bar.
`Esc`, or a click outside the dialog, closes it.

## Jump to a page

With nothing typed, the dialog lists your pages, most recently edited first.
So `Ctrl+P`, `↓`, `Enter` takes you back to the page you were just working on.

Above the list:

| Control | Does |
|---------|------|
| **Sort by** | **Recently edited**, **Title A–Z**, **Title Z–A** or **Most linked**. Zone remembers your choice. |
| **Unlinked only** | Shows only pages no other page links to: good for finding pages that got lost. |

**Changes** and **Deleted Pages** are pinned at the top of the list (see [History and undo](history.md)).
**Tasks** and **Notes** are found by typing their names.

## Search as you type

Type a few letters, and the list narrows to pages whose title or file name matches.
A few letters of each word are enough: `gard sh` finds "Garden shed".
With several words, each must match a word of the title.

A moment later, **Content matches** appear below: pages, notes and tasks whose text matches, each with a short excerpt and the matching words marked.

| Key | Does |
|-----|------|
| `↑` `↓` | Move through the titles and the content matches |
| `Enter` | Open the highlighted result |

On a wide screen the dialog shows the highlighted result next to the list, scrolled to the part that matched, so you can check it before you open it.

The dialog shows 15 content matches.
When there are more, click **All results →** for a page with up to 50 and a count.

## Search syntax

Words find pages and tasks that have any of them.
Those with more of your words, and with the rarer ones, rank higher.
Add these to narrow a search down:

| Type | Finds | Example |
|------|-------|---------|
| words | Pages and tasks with these words | `roof repair` |
| `"phrase"` | The words together, in this order | `"next steps"` |
| `-word` | Leaves out what contains the word | `garden -shed` |
| `-"phrase"` | Leaves out what contains the phrase | `-"next steps"` |
| `#tag` | Only pages and tasks with this tag | `#recipe` |
| `-#tag` | Only those without the tag | `#work -#done` |
| `edited:` | Edited in that year, month or day | `edited:2026-09` |
| `after:` | Edited on or after that date | `after:2026-07-01` |
| `before:` | Edited before that date | `before:2026-08` (July and earlier) |
| `-edited:` | Not edited then | `-edited:2026` |

You can combine them: `budget #work after:2026-09 -draft`.

### Dates in a search

A page's date is when it was last edited.
A task's is its last activity: when it was made, changed, commented on or completed.
A date can be a year (`2026`), a month (`2026-09`) or a day (`2026-09-15`).
A search of only dates, such as `edited:2026-09`, lists everything from then, newest first.

### Tags in a search

A page's tags are the ones in its frontmatter, the `tags:` line at its top (see [Writing](writing.md)).
A task's tags are its `#tags`.
Case doesn't matter: `#Work` and `#work` are the same.

## How words match

You don't have to type a word exactly as the page has it:

- **Case and accents** don't matter: `muller` finds "Müller".
- **Word forms** match each other: `running` finds "runs" and "run".
- **The start of a word** is enough: `garde` finds "garden".
- **Typos** are forgiven when nothing matches exactly: `gardn` finds "garden".
- **Small words** like "the", "is" and "in" (in English and German) are ignored, so you can type a question as a sentence.

A title counts more than the text, so a page named after your words comes first.
A page's file and folder names count too, once, as if the text said them.
A page that has your words as a phrase, in this order, moves to the top.

The words of a link's address, the `(url)` part of `[text](url)`, are not searched; the link's text is.

For the full rules, see [the spec](../spec/search.md#how-words-match).

## Read the results

Under each result's title is a short line telling similar results apart:

| Result | The line says | Example |
|--------|---------------|---------|
| A page | Its folder and when it was last edited | `projects/garden · edited 2026-10-02` |
| A task | Its list and its status | `Tasks › shopping · done 2026-05-01` |

In a wiki with users, the line also says whose a result is:
a 🔒 for your private pages and tasks (`🔒 Private/ideas · …`, `🔒 Tasks › …`),
a 👥 and the group's name for a group's pages (`👥 dev · …`) and for the tasks of a group's list.
What everyone sees has no sign.

### Open a page result

A page result opens at the section that matched, not at the top of the page.
The words you searched for are marked on the page, and a bar at the bottom (at the top on a phone) says how many there are, such as "2 of 5".

| Key | Does |
|-----|------|
| `n` | Next match |
| `N` | Previous match |
| `Esc` | Remove the marks |

You can also use the bar's ↑, ↓ and × buttons.

### Open a task result

A task result opens Tasks, on the tab and list the task is in, with the task selected and briefly highlighted.

## Who finds what

Search only ever shows you what you may see.
In a wiki with users:

- your private pages, notes and tasks are found by you, and by nobody else;
- a group's pages are found by its members, and by nobody else;
- what you can't see doesn't affect your results in any way, not even their order.

An admin finds no more than anyone else: not other users' private pages, and not the pages of groups they're not in.
[Teams](teams.md) explains private pages and groups.

### Search while sharing your screen

In [Sharing mode](navigation.md#sharing-mode) the search dialog lists nothing until you type, so no recent page names show on screen.
Pages marked as not for sharing are left out of the results altogether.

## Search your notes only

The **Filter notes…** box on the Notes page searches your notes and nothing else, with the same syntax.
See [Notes](notes.md#find-a-note).
