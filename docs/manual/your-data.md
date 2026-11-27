# Your data

Everything in Zonewiki is a plain file in one folder on disk: your pages as Markdown, your uploads as they were, your tasks as JSON.
Every change is a git commit, so you can read, search, back up and move your wiki with ordinary tools, and leave Zonewiki at any time without losing anything.
This page says what is in the folder, how to keep it safe and what limits there are.

## What is in the wiki folder

The wiki folder is the folder Zonewiki was pointed at when it was set up (`ZONE_DATA`, see [Running Zonewiki yourself](self-hosting.md)).
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
| Zonewiki's own state | `.zone/` | Settings and indexes, kept out of git |

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

`.zone/` is Zonewiki's own state, and it is never committed to git:

- `config`, the settings file: the wiki's settings, its users, their roles and groups, and the hashes of passwords and tokens.
- `secret_key`, the key that keeps people signed in.
- `grants` and `links`: assistants connected by signing in, and open invite and reset links.
- `index/`: the search indexes, which Zonewiki can always build again.
- `usage/`: how much the wiki is used, as counts a day for 8 weeks ([Usage](teams.md#usage)).

Zonewiki never shows this folder in the browser, in history or to an assistant.
It holds password hashes and keys, so treat it as confidential.
The full list is in [What is in .zone/](../spec/deployment.md#what-is-in-zone).

### The format file

`.zone-format` holds one number: the format the wiki's files are in.
Zonewiki reads it when it starts, to know that it can read them, and writes it into a new wiki.
It is committed with the files, so going back in the history takes it back with them.
Leave it as it is.

If Zonewiki won't start and says the files are in a format a newer Zonewiki wrote, the wiki was last run by a newer Zonewiki than this one: start the newer one again.
Going back is in [The data format](../spec/deployment.md#the-data-format).

## Every change is a commit

When the wiki folder is new to it, Zonewiki makes it a git repository and commits what is there.
From then on every change made through Zonewiki is a commit: clicking **Done** in the editor, a new page, a rename or move, a delete, an upload, a change to a task.
With users, each commit is made in the name of whoever made the change, and an assistant's are marked as such: **assistant** or **through Claude** in Zonewiki; in plain git a message ending in `(MCP)` and a line `Assistant: Claude` (or `Assistant: MCP token`) below it, which is what Zonewiki goes by.

While you type, the editor saves your text every couple of seconds without committing it.
**Done** (or Ctrl+S) makes the commit.
That's why the history has one version per editing session, not one per pause.

This history is what [History and undo](history.md) is built on: every version of every page, Changes for the whole wiki, and deleted pages brought back.

## Editing the files with another editor

You can edit the files directly, with any text editor, a script or a sync tool, while Zonewiki runs.
Zonewiki reads the files from disk each time, so:

- a changed page shows its new text the next time you open it;
- a new `.md` file is a new page, and shows in the page list and search;
- a deleted file is gone from the wiki.

Zonewiki doesn't commit changes made outside it: they show as [uncommitted changes](history.md#uncommitted-changes), which you commit from the badge at the top of every page.
You can also commit yourself with `git` in the folder; Zonewiki takes your commits as they are.

> [!TIP]
> If the page is open in Zonewiki's editor while you change the file, the editor merges your two edits when they touch different lines.
> When they touch the same lines, it asks which to keep.

Edit the task files by hand only with care: they must stay valid JSON.
Zonewiki refuses to overwrite a task file it can't read rather than lose its tasks, so a broken file stops changes to those tasks until it is fixed.

## Backups

The wiki folder is the whole wiki, and there is nothing of it anywhere else.
Back up the whole folder, including `.git/` and `.zone/`.

| How | What you get |
|-----|--------------|
| Copy the folder while Zonewiki is stopped (`./run.sh down`) | Everything, consistent |
| A snapshot of the disk (a NAS's snapshots, ZFS, btrfs) | Everything, consistent, without stopping Zonewiki |
| `git push` to a remote | Pages, uploads and tasks with all their history, but not `.zone/` |

A copy taken file by file while Zonewiki runs can catch a commit or a task change halfway, so stop Zonewiki first or use a snapshot.

### Pushing to a git remote

To push the history elsewhere, an admin sets a remote once:

```bash
./run.sh config set git-remote git@github.com:anna/wiki.git
```

Then `git push origin` in the wiki folder copies everything that is committed.
Zonewiki doesn't push by itself, so push now and then, or from a scheduled job.
Changes not committed yet aren't in it.

> [!WARNING]
> A git remote has no `.zone/`: no users, no passwords, no settings.
> A clone of a wiki with users won't start without its `config`, so keep a copy of `.zone/` beside the remote.
> Without `secret_key` everyone signs in again, and without `grants` every assistant connects again.

### What a backup needn't hold

`.zone/index/` can be left out: Zonewiki builds the search indexes again when they are needed, which takes a while on a big wiki.

### Restoring

Put the folder back where `ZONE_DATA` points and start Zonewiki.
The files must belong to the user Zonewiki runs as, and `.zone/config` and `.zone/secret_key` should be readable by that user alone.
The details are in [Backups](../spec/deployment.md#backups).

Treat every backup as you treat the wiki: it holds every user's private pages, and a copy of the folder holds the password hashes too.

## Moving to another Zonewiki

A wiki moves in one file: from your computer to a server or a NAS, to the hosted Zonewiki, or from there to a Zonewiki of your own.
Notes from another app, an Obsidian vault for one, come in as pages instead: see [Importing notes](importing.md).
The file holds the pages, uploads and tasks with all their history, and the users, who sign in afterwards with the password they had.

Where the wiki is now, write the file; Zonewiki may keep running:

```bash
./run.sh export > my-wiki.tar.gz
```

On the hosted Zonewiki, the file is **Download** under **Export** on your account page.

Where the wiki goes, the wiki folder must be empty and Zonewiki stopped:

```bash
./run.sh import < my-wiki.tar.gz
./run.sh up
```

On the hosted Zonewiki, open **Bring a wiki you have** under **Create your wiki** on your account page, and pick the file.
The user name and password you give there are yours in the moved wiki: a user of that name gets the password, otherwise you are added to its users.

Afterwards:

- Everyone signs in again.
- An assistant that was connected by signing in is connected again, at the new address; one with a token needs only the new address.
- Set the names the wiki is opened by, if it has any: `./run.sh config set hosts wiki.example.org` ([Running Zonewiki yourself](self-hosting.md)).
- Open invite and reset links are void: make new ones.

Two things to know before:

- Only saved text moves. The export tells you if pages hold changes that aren't in the history yet: open them, choose **Done**, and export again.
- The file is as private as the wiki. It holds every user's private pages and the hashes of their passwords.

With the binary alone the commands are `zonewiki export` and `zonewiki import FILE`.
All of it is in [Moving a wiki](../spec/deployment.md#moving-a-wiki).

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
When the wiki is full, Zonewiki refuses new pages, new notes and uploads, and says "The wiki is full".
It never refuses an edit to a page, a delete, a rename or a task, so nothing you typed is lost.
Delete pages or files to make room.
See [Storage limit](../spec/users.md#storage-limit).

## How big a wiki can be

A personal wiki or a team's is far below any limit you'd notice.
Zonewiki stays quick up to several thousand pages and open tasks; past some ten thousand of either, every page view takes noticeably longer, and a NAS's slower disk and processor bring that point closer.
Closed tasks cost nothing until you look at them.
Page saves are committed one at a time, so a wiki isn't made for many people saving at the same moment.

The measured numbers (a page view in a wiki of 1,000 to 20,000 pages, the task list at 500 and 5,000 open tasks) are in [How big a wiki can be](../spec/deployment.md#how-big-a-wiki-can-be).

## Leaving Zonewiki

There is nothing to export to leave Zonewiki.
Stop it, and the folder is still there: your pages as Markdown, your uploads, your tasks as JSON, and the full history in git.

- Open the folder in any Markdown editor or note app that reads a folder of files.
- Read the history with `git log` or any git tool.
- `[[wikilinks]]` are understood by many Markdown tools; elsewhere they read as plain text.
- The tasks are JSON, which a script turns into whatever the next tool imports.

From the hosted Zonewiki you get that folder as a file, **Download** under **Export** on your account page: unpack it, and it is the same.

Coming back works the same way: point Zonewiki at the folder again.
You can also point Zonewiki at a folder of Markdown notes you already have; it makes the folder a git repository if it isn't one and adds a home page.
