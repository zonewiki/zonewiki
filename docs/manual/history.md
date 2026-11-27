# History and undo

Zonewiki keeps every version of every page, so nothing you or anyone else writes is lost for good.
This page shows how to look back at a page's versions, see what changed across the whole wiki,
bring back an old version or a deleted page, and undo a change, including one made by an AI assistant.

## Every change is a commit

Your wiki is a git repository, and Zonewiki records each change as a git **commit**: a saved version with a time, a short message and an author.
These all make a commit:

- **Done** in the editor
- making, renaming, moving, deleting or restoring a page
- uploading a file
- an AI assistant's change
- an undo

With several users, each commit is made in the name of whoever made the change, and an assistant's in the name of the person it works for.
In a wiki without users, every commit is Zonewiki's own and shows no person's name.

Two things write a page without a commit: auto-save while you type, and ticking a checkbox on a page.
Those are kept as [uncommitted changes](#uncommitted-changes) until the next commit of the page.

Because it's plain git, you can also read the history with any git tool, or back it all up with `git push` (see [Your data](your-data.md)).

## A page's history

Choose **History** in the page's ⋯ menu.
It lists every saved version of the page, newest first, one row each:

- the date and time;
- what the save did: Created, Edited, Renamed from "…", Link updated (when a rename elsewhere changed a link on this page), Restored the version of …, Undid the change of …, Deleted;
- who made it, and for an AI assistant's change through which assistant, as in "Anna through Claude" ("Anna through an assistant" when it used a token);
- how many lines were added and removed.

The page as it is now is marked **Current**.
If it has changes not committed yet, a **Now · Not committed yet** row is the current one.
The history follows a page across renames, so versions from before its old name are there too.

### Reading an old version

Click a row to open that version.
A bar above it says which one it is, such as "The version of 2026-10-08 14:32 · 2 versions back".
**← Older** and **Newer →** step through the versions; from the newest old version, **Current →** goes to the page itself.

### Seeing what a save changed

Click **Changes** on a row of the history, or **What this save changed** on a version.
You see the lines that save took out in red and the lines it put in in green.
**View this version** opens the whole page as that save left it.

### Restoring a version

On an old version, click **Restore this version** and confirm.
The page becomes that version again, as a new commit.
Nothing is thrown away: the page as it was before the restore stays in its history, so you can go back to it the same way.

## Changes across the wiki

**Changes** lists what changed in the whole wiki, newest first, grouped by day.
Open it from the search dialog (Ctrl+P, where it's listed above the pages), or with **Changes across the wiki** on any page's history.

Each row shows:

- the time and what the change was;
- who made it, with users;
- an **assistant** mark if an AI assistant made it, or **through Claude** (the name it gave when it was connected);
- **from …** with an address, if the assistant said where its text came from;
- what it changed: each page as a link to what changed on it, each task by its name as it is now, a link to the task, and the names of other files such as uploads.
  A change that named no task it changed (one from before November 2026, a tag renamed everywhere), or one whose task was deleted since, says **Tasks**.

**Show more** at the bottom loads older changes.
You only see changes to pages, tasks and files you're allowed to see: a task you don't see isn't named.

### Filtering changes

**Filter**, above the list, opens the filters that narrow it down; click **Show** to apply them.
What is set stands beside the button ("by ben", "in projects/").

| Filter | Shows |
|---|---|
| **By** | the changes of one person (with users) |
| **Only by an assistant** | the changes AI assistants made |
| **Through** | the changes one assistant made: a name it gave when it was connected, or **MCP token** for one that uses a token |
| **Only text from outside** | the changes whose text an assistant said came from elsewhere, a web page or a mail |
| **In the folder** | changes to pages in one folder, such as `projects` |
| **Since** | changes from that day on |
| **Only what I watch** | changes to the pages and folders you watch (below) |

**All changes** clears the filters.
A filtered list has its own address, so you can bookmark "what my assistant did this week".
**Connect an assistant** links straight to your assistants' changes, and **its changes** beside each connection to what that one changed.

## Watching pages

In a wiki with users you can watch a page, and Zonewiki tells you when someone else changes it.

- **Watch a page**: open its **⋯** menu and choose **Watch**. **Unwatch** is in the same place.
- **Watch a folder**: open **Changes**, click **Filter**, type the folder into **In the folder**, click **Show**, then **Watch this folder**.
  You are then told about every page in it, including pages made later.

When a page you watch has changed, the round button with your initial at the top right gets a dot.
Open it and choose **Watched pages**: the number beside it says how many pages have news.
You get the changes to what you watch, newest first, and each change you haven't looked at is marked **new**.
Click the page's name in a row to see what the change did.

A change is read once you open the page, or open what its latest change did.
**Mark all read** at the top reads them all at once.
Your own changes are never news; what your assistant changed for you is, so you see what it did.

If you'd rather be told by mail, the morning's mail has the watched pages that changed (see [News about your tasks](tasks.md#news-about-your-tasks)).

Above the list, **You watch N things** opens the list of what you watch, each with **Unwatch**.
Watching is yours alone: nobody else sees what you watch, and it changes nothing in the wiki.
The full rules are in [the spec](../spec/navigation.md#watching-pages).

## Undoing a change

A row in Changes can have an **Undo** button.
Click it and every page and task that change touched goes back to how it was before it:
an edited page gets its old text back, a page it made is deleted again, a page it deleted comes back;
an edited task gets its old fields back, a completed one is open again (without the next one of its series), a moved one goes back to its list, a task it made is deleted again and one it deleted comes back.
There's no question first, because the undo is itself a change at the top of the list, and undoing the undo brings the change back.
A page's history calls it "Undid the change of …".

**Undo** is offered only when it can be done cleanly:

- the change touched only pages and tasks, not a list's settings or uploads;
- you may edit every page it touched, and every task (a task it made is deleted only by whoever made it, or an admin; a shared task doesn't become private again; a task goes back to a list fewer people see only when you made it, or are an admin);
- it didn't rename or move a page (rename it back instead);
- none of its pages or tasks has changed since, by anyone.
  Other tasks changing in the meantime don't matter.

If a page changed again, restore the version you want from that page's history instead.
When an undo is refused, Changes says why, such as "Not undone. garden was changed again since …".
A task put back is exactly as it was, its history too: the undo itself shows in Changes, not in the task.

### Undoing an assistant's work

When an AI assistant makes several changes for you in one go, the latest of them offers **Undo all 3** (with the number of changes).
It asks first, saying which assistant made them and when, then takes them all back in one commit.
It's offered as long as nobody else changed those pages and tasks in between.
More about assistants and what they may do is in [AI assistants](assistants.md).

## Deleted pages

A deleted page stays in the history.
To bring one back, open **Deleted Pages** from the search dialog (Ctrl+P).
It lists every deleted page, most recently deleted first, with its name and the day it was deleted.

- Click a page's title to read its last version.
- Click **Restore** to bring it back as it was.

If you open the address of a deleted page, Zonewiki says "This page was deleted on …" and offers **Restore page**.

## Uncommitted changes

An **uncommitted change** is a page whose file differs from its last commit.
It comes from:

- auto-saved text you didn't finish with **Done**;
- ticking a checkbox on a page;
- editing the files directly on disk, with another editor or a script.

When there are some, a badge with their number appears at the top right, next to **Search**.
Click it for **Uncommitted Changes**, the list of those files, with **Diff** to see the change and **View** to open the page.
**Commit All** commits each file on its own.
A page with an uncommitted change also says "— uncommitted" after **Last changed**, with a **Commit** link.

You rarely need to do this yourself.
When someone else saves, renames or deletes a page that holds your uncommitted text, Zonewiki commits it first in your name,
so your text is in the history under your name and never ends up in someone else's change.
Text edited on disk has no known author, so it's committed by whoever commits it.
