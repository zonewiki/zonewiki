# Notes

A note is for writing something down now, before you know where it belongs: an idea, a call, a meeting.
Later you file it: you give it a proper place in the wiki, or add it to a page that already exists.
That way you capture things without stopping to organise, and the wiki stays tidy.

## Write a note

Start a note in any of these ways:

- press `c`, then `n`, on any page;
- on a phone, tap **Note** in the tab bar at the bottom;
- on the **Notes** page, click **+ New**.

Type a title and click **Create & Edit**.
(Leave the title empty and the note is called "Untitled".)
The editor opens with the cursor under the title, ready for the text.
Write, then click **Done**, or press `Ctrl+S`.

A note is a page like any other: Markdown, wikilinks, tasks, history.
What makes it a note is only that it hasn't been filed yet.

### Who sees your notes

In a wiki with users, notes are private.
The dialog says **New private note**, and only you see the note until you move it into the wiki.
The **Notes** module and its pill carry a small lock to remind you.

A viewer, someone who may read but not change the wiki, has no notes.

## Find a note

Click **Notes** in the module switcher, or press `g`, then `n`.
The Notes page lists your notes, newest first, each with the date and time it was made.

Type in the **Filter notes…** box above the list to search your notes only.
The matches are listed best first, with a short excerpt, and a count such as "2 of 30".
The search syntax works here too: `"a phrase"`, `-word`, `#tag`, `edited:2026-09` (see [Search](search.md#search-syntax)).

| Key | In the filter box |
|-----|-------------------|
| `Enter` | Open the first match |
| `Esc` | Clear the box; a second `Esc` leaves it |

The filter is kept in the address, so the browser's Back button returns to the filtered list.

## File a note

Open the note and click **⋯** at its top right.
A note's menu has two entries the other pages don't: **Move to wiki…** and **Append to page…**.
Either one takes the note out of Notes.

### Move to wiki

Use **Move to wiki…** when the note should become a page of its own.
The dialog asks:

| Field | What to put |
|-------|-------------|
| **Folder** | Where the page goes. It suggests the folders there are; empty means the top level. With users, your private folder and your groups' folders are offered first. |
| **Name** | The page's name, filled in with the note's title. |
| **Keep the date** | Tick it to keep the note's date in front of the name, such as `2026-11-03_kickoff`. Handy for meeting notes. |
| **Link from** | The page that should link to the new one. Zonewiki fills in the folder's index page, or the home page; empty means no link. |

Under the name Zonewiki shows the address the page will have.
Click **Move**.

If you named a page in **Link from**, that page opens in the editor, and Zonewiki has copied a link to the new page.
A hint says "Link copied. Paste it where it belongs on this page."
Put the cursor where the link should go and paste.
Zonewiki doesn't change that page by itself: you decide where the link goes.

Links that pointed to the note now point to the new page.
The page's history shows the move as "Moved from Notes".

With users, a note moved to a folder outside your private folder can be seen by more people:
everyone for a shared folder, the members for a group's folder.

### Append to page

Use **Append to page…** when the note belongs on a page you already have,
such as notes from a meeting with Anna that go on Anna's page.
The dialog asks:

| Field | What to put |
|-------|-------------|
| **Page** | The page to add the note to. It suggests pages as you type. |
| **Section heading** | The heading the note gets there, filled in with its title and date, such as `Weekly sync (2026-11-03)`. |

Click **Append**.
The note becomes a new section at the end of that page:

- its title goes, replaced by the section heading;
- its own headings move one level down, so they sit under the new section;
- its tags are added to the page's tags.

The note itself is deleted, and links to it now point to the new section.
The page's history shows the change as "Note appended".
If you change your mind, the note can be restored from [deleted pages](history.md), like any deleted page.

A heading the page already has is refused: pick another one.

## Notes and your assistant

An AI assistant connected to Zonewiki can make notes, find them and file them too, the same way.
See [AI assistants](assistants.md).
