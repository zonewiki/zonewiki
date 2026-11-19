# The wiki guide

A guide tells every assistant how your wiki is kept: where things go, which tags you use, what stays private.
You write it once, as an ordinary page, and every assistant that connects follows it, whichever app it runs in.
This page explains the wiki guide and folder guides, and gives you a starter guide to copy.

## The wiki-guide page

The page `wiki-guide` is your wiki's guide.
Make it like any other page: create a page named `wiki-guide` and write your conventions in plain words.

Its text is shown to every assistant at the start of each session, together with Zone's own advice, and where they differ, your guide wins.
An edit shows from the next session on.
Keep it short and to the point: an assistant reads it every time, and a very long guide is cut, with a pointer to read the rest.

Like any page, it has a history, so you can see who changed it and restore an older version.

### No guide yet

Without a `wiki-guide` page, assistants are told to offer to start one.
When you tell an assistant how you want something kept ("meeting notes go under `meetings/`, one page per meeting"), it offers to write that down on the guide.
That way the guide grows from what you actually decide.

## Folder guides

A folder can have a guide of its own, for the pages in it and in its subfolders.
It is a page named `AGENTS` or `CLAUDE` in that folder (the file `AGENTS.md` or `CLAUDE.md`, in any case), for example `meetings/AGENTS`.
These are the names coding assistants look for too, so the same file guides an assistant that works on your wiki's files directly.

The guides for a page are the wiki's, then each folder's on the way down to the page.
So `wiki-guide` says what holds everywhere, and `meetings/AGENTS.md` adds what holds for meeting notes: their layout, their tags, who they are for.

A line that holds only `@other-page.md` pulls in that page's text, relative to the guide's folder.
Use it to share one piece between guides.

## How assistants use guides

Assistants are told to read the guides before they draft anything for the wiki, so the draft follows them from the start.
Zone also checks: an assistant that writes to a page under a guide it hasn't read is stopped, nothing is written, and it is shown the guide first.
When you change a guide, every assistant sees the new text before its next write there.

Guides cover pages.
Tasks aren't checked against them, but an assistant still reads in the wiki guide how you like tasks kept.

## Who may change guides

In a wiki without users you write your guides yourself.

With users, only an admin may change the wiki's guide and a shared folder's guide, and a page a guide pulls in.
Everyone else reads them like a viewer.
The reason: a guide steers every user's assistant, so whoever writes one could tell all of them what to do.

- A guide in a group's folder, such as `groups/garden/AGENTS.md`, steers everyone in the group, so it is an admin's too, and only an admin who is in the group can change it.
- A guide in your own private folder, such as `private/<you>/AGENTS.md`, is yours: only your own assistant reads it.

See [Teams](teams.md) for roles.

## A starter wiki guide

Copy this onto your `wiki-guide` page and change it to fit.
Delete what you don't need: a short guide that's true beats a long one that isn't.

```markdown
# Wiki guide

How this wiki is kept. Follow it when you write here, and ask me when it doesn't say.

## Where things go

- `projects/<name>`: one page per project: goal, status, decisions, links.
- `meetings/`: one page per meeting, named `YYYY-MM-DD-topic`.
- `people/<name>`: what I need to remember about a person I work with.
- `howto/`: how to do recurring things, written as steps.
- Before making a page, search for one on the topic and extend it.

## What stays private

- Anything about health, money, family or a person's private life goes under my private folder.
- Never copy text from a private page or a group's page into a shared one without asking.

## Tags

- Use the tags that exist (list them first); don't invent near-duplicates.
- Page tags: `#project`, `#howto`, `#reference`, `#idea`.
- Task tags: `#work`, `#home`, `#errand`, `#waiting`.

## Page layout

- Start with a one-line summary of what the page is for.
- Short sections with `##` headings; lists over long paragraphs.
- Link related pages with `[[wikilinks]]`; put sources in the frontmatter's `sources`.

## Notes and tasks

- Something to do is a task, not a line on a page; give it a due date only when there is one.
- A quick thought I haven't sorted is a note; file it into a page when its place is clear.
- Tasks for the team go in a shared list; my own stay in my Inbox.

## Ask me first

- Before deleting, renaming or moving a page.
- Before replacing text on more than one page.
- Before assigning a task to someone else.
- When you're unsure where something belongs.
```
