# Your data

Everything in Zone is a plain file in one folder on disk: your pages as Markdown, your uploads as they were, your tasks as JSON.
Every change is a git commit, so you can read, search, back up and move your wiki with ordinary tools, and leave Zone at any time without losing anything.
This page says what is in the folder, how to keep it safe and what limits there are.

## What is in the wiki folder

The wiki folder is the folder Zone was pointed at when it was set up (`ZONE_DATA`, see [Running Zone yourself](self-hosting.md)).
In it:

| What | Where | Format |
|------|-------|--------|
| Pages | Anywhere in the folder, `garden/roses.md` is the page `garden/roses` | Markdown |
| Folders | Ordinary folders: a page's folder is the part of its name before the last `/` | Folders |
| Notes | `2026-03-24_18-50-08_call-anna.md` at the top, or in your private folder with users | Markdown |
| Private pages | `private/<you>/`, with users | Markdown |
| A group's pages | `groups/<name>/` | Markdown |
| Uploads | `_assets/` at the top (a private page's in `private/<you>/_assets/`, a group's in `groups/<name>/_assets/`) | The files as uploaded |
| Tasks | `_tasks/` (your private ones in `private/<you>/_tasks/`) | JSON |
| History | `.git/` | A git repository |
| The data format | `.zone-format` | A number, in git |
| Zone's own state | `.zone/` | Settings and indexes, kept out of git |

### Pages and uploads

A page is a `.md` file and nothing more.
Its first `# Heading` is its title, and links between pages are `[[wikilinks]]` in the text.
An image or file you upload goes into an `_assets/` folder and the page links to it by name.

Who sees a page is decided by where it is kept: `private/<you>/` is yours, `groups/<name>/` is that group's, and everything else is shared (see [Teams](teams.md)).

### Tasks

Tasks are the one thing that isn't Markdown, because they have fields (dates, a list, an assignee, comments) that Markdown can't hold safely.
They are JSON files in `_tasks/`: open tasks in `tasks.json`, closed ones in `closed.json` and one file per earlier month (`closed-2026-10.json`).
Each list has its own folder, `_tasks/lists/<name>/`, and `_tasks/lists.json` holds the lists and their settings.
They are plain text, in git like the pages, and readable by any script.

### The .zone folder

`.zone/` is Zone's own state, and it is never committed to git:

- `config`, the settings file: the wiki's settings, its users, their roles and groups, and the hashes of passwords and tokens.
- `secret_key`, the key that keeps people signed in.
- `grants` and `links`: assistants connected by signing in, and open invite and reset links.
- `index/`: the search indexes, which Zone can always build again.

Zone never shows this folder in the browser, in history or to an assistant.
It holds password hashes and keys, so treat it as confidential.
The full list is in [What is in .zone/](../spec/deployment.md#what-is-in-zone).

### The format file

`.zone-format` holds one number: the format the wiki's files are in.
Zone reads it when it starts, to know whether the files still need bringing up to date from an older Zone.
It is committed with the files, so going back in the history takes it back with them.
Leave it as it is.

If Zone won't start and says the files are in a format a newer Zone wrote, the wiki was last run by a newer Zone than this one: start the newer one again.
Going back is in [The data format](../spec/deployment.md#the-data-format).

## Every change is a commit

When the wiki folder is new to it, Zone makes it a git repository and commits what is there.
From then on every change made through Zone is a commit: clicking **Done** in the editor, a new page, a rename or move, a delete, an upload, a change to a task.
With users, each commit is made in the name of whoever made the change, and an assistant's are marked as such: **assistant** or **through Claude** in Zone, a message ending in `(MCP)` in plain git.

While you type, the editor saves your text every couple of seconds without committing it.
**Done** (or Ctrl+S) makes the commit.
That's why the history has one version per editing session, not one per pause.

This history is what [History and undo](history.md) is built on: every version of every page, Changes for the whole wiki, and deleted pages brought back.

## Editing the files with another editor

You can edit the files directly, with any text editor, a script or a sync tool, while Zone runs.
Zone reads the files from disk each time, so:

- a changed page shows its new text the next time you open it;
- a new `.md` file is a new page, and shows in the page list and search;
- a deleted file is gone from the wiki.

Zone doesn't commit changes made outside it: they show as [uncommitted changes](history.md#uncommitted-changes), which you commit from the badge at the top of every page.
You can also commit yourself with `git` in the folder; Zone takes your commits as they are.

> [!TIP]
> If the page is open in Zone's editor while you change the file, the editor merges your two edits when they touch different lines.
> When they touch the same lines, it asks which to keep.

Edit the task files by hand only with care: they must stay valid JSON.
Zone refuses to overwrite a task file it can't read rather than lose its tasks, so a broken file stops changes to those tasks until it is fixed.

## Backups

The wiki folder is the whole wiki, and there is nothing of it anywhere else.
Back up the whole folder, including `.git/` and `.zone/`.

| How | What you get |
|-----|--------------|
| Copy the folder while Zone is stopped (`./run.sh down`) | Everything, consistent |
| A snapshot of the disk (a NAS's snapshots, ZFS, btrfs) | Everything, consistent, without stopping Zone |
| `git push` to a remote | Pages, uploads and tasks with all their history, but not `.zone/` |

A copy taken file by file while Zone runs can catch a commit or a task change halfway, so stop Zone first or use a snapshot.

### Pushing to a git remote

To push the history elsewhere, an admin sets a remote once:

```bash
./run.sh config set git-remote git@github.com:anna/wiki.git
```

Then `git push origin` in the wiki folder copies everything that is committed.
Zone doesn't push by itself, so push now and then, or from a scheduled job.
Changes not committed yet aren't in it.

> [!WARNING]
> A git remote has no `.zone/`: no users, no passwords, no settings.
> A clone of a wiki with users won't start without its `config`, so keep a copy of `.zone/` beside the remote.
> Without `secret_key` everyone signs in again, and without `grants` every assistant connects again.

### What a backup needn't hold

`.zone/index/` can be left out: Zone builds the search indexes again when they are needed, which takes a while on a big wiki.

### Restoring

Put the folder back where `ZONE_DATA` points and start Zone.
The files must belong to the user Zone runs as, and `.zone/config` and `.zone/secret_key` should be readable by that user alone.
The details are in [Backups](../spec/deployment.md#backups).

Treat every backup as you treat the wiki: it holds every user's private pages, and a copy of the folder holds the password hashes too.

## Exporting tasks

Tasks are already files, but to hand a list to someone or move it to another wiki, export it:
open the list in Tasks, then the **⋯** menu in its toolbar, and choose **Export tasks**.
You get a JSON file with the tasks the list shows, open and closed, with their descriptions, dates, comments and history.

**Import tasks…** in the same menu adds such a file's tasks to the list you're in.
A task keeps its id, so importing the same file twice adds nothing.
See [Export and import](../spec/tasks.md#export-and-import) for the details.

## Size limits

| Limit | Default | Set by |
|-------|---------|--------|
| One upload | 100 MB | `upload-max` in `.zone/config`, such as `25MB` |
| The whole wiki | No limit | `storage-max` in `.zone/config`, such as `5GB` |
| A page, a task or an assistant's request | 16 MB | Fixed |

`storage-max` counts every file in the folder but the history and `.zone/`.
When the wiki is full, Zone refuses new pages, new notes and uploads, and says "The wiki is full".
It never refuses an edit to a page, a delete, a rename or a task, so nothing you typed is lost.
Delete pages or files to make room.
See [Storage limit](../spec/users.md#storage-limit).

## How big a wiki can be

A personal wiki or a small team's is far below any limit you'd notice.
Zone stays quick up to several thousand pages and open tasks; past some ten thousand of either, every page view takes noticeably longer, and a NAS's slower disk and processor bring that point closer.
Closed tasks cost nothing until you look at them.
Page saves are committed one at a time, so a wiki isn't made for many people saving at the same moment.

The measured numbers (a page view in a wiki of 1,000 to 20,000 pages, the task list at 500 and 5,000 open tasks) are in [How big a wiki can be](../spec/deployment.md#how-big-a-wiki-can-be).

## Leaving Zone

There is nothing to export to leave Zone.
Stop it, and the folder is still there: your pages as Markdown, your uploads, your tasks as JSON, and the full history in git.

- Open the folder in any Markdown editor or note app that reads a folder of files.
- Read the history with `git log` or any git tool.
- `[[wikilinks]]` are understood by many Markdown tools; elsewhere they read as plain text.
- The tasks are JSON, which a script turns into whatever the next tool imports.

Coming back works the same way: point Zone at the folder again.
You can also point Zone at a folder of Markdown notes you already have; it makes the folder a git repository if it isn't one and adds a home page.
