# AI assistants

An AI assistant such as Claude can search, read and write your wiki and your tasks.
It works on your behalf: it sees and changes what you may, never more, and every change it makes is in the history, where you can undo it.
This page gets you connected first, then explains what a connection may do and how to keep an eye on it.

## Connect in five minutes

Every way starts from the wiki's assistant address: your wiki's address followed by `/mcp`, for example `https://wiki.example.com/mcp`, or `http://localhost:5080/mcp` for a wiki on your own computer.
You find it at the top of **Connect an assistant**, with a **Copy** button: **Settings** in the menu at the top right opens it (your initial; without users, the three lines), and on a phone behind your name or the three lines at the top of the page.

There are two ways in:

- **Sign in from the assistant.**
  You give the assistant the address, it opens your wiki in the browser, and you allow it.
  There is nothing to copy but the address.
  This works when your wiki has a login (users or a password).
  A wiki on your own computer starts without one: set one first ([Setting a login](self-hosting.md#setting-a-login)), or use a token.
- **A token.**
  You make a token on **Connect an assistant** and paste it into the assistant's settings.
  This works with any wiki, also one without a login, and any client, also one that can't open a browser, or a script.

> [!NOTE]
> Claude.ai, the Claude apps and ChatGPT run on their makers' servers and reach your wiki from there.
> They can connect only to a wiki that is on the internet over https.
> Claude Code runs on your computer and reaches any wiki your computer does, one at `http://localhost:5080` too.
> Putting a wiki on the internet: [HTTPS with a reverse proxy](self-hosting.md#https-with-a-reverse-proxy).

### Claude.ai and the Claude desktop app

1. In Claude, open the settings for connectors and add a **custom connector**.
2. Give it a name, such as `Zonewiki`, and paste the wiki's assistant address.
3. Connect it.
   Your wiki opens in the browser, asking **Connect an assistant?**
   Sign in first if you aren't.
4. Choose what it may do and see (below), type your password, and click **Allow**.
   The page says **Connected** and sends you back to Claude.

A connector you add on claude.ai is there in the desktop and mobile apps too.

### Claude Code

In a terminal, run this with your wiki's assistant address:

```sh
claude mcp add --transport http zone https://wiki.example.com/mcp
claude mcp add --transport http zone http://localhost:5080/mcp     # a wiki on your own computer
```

Then start Claude Code, type `/mcp`, pick `zone` and sign in.
Your browser opens the same **Connect an assistant?** page; click **Allow** and go back to the terminal.

A wiki without a login has nothing to sign in to, and Claude Code can't connect this way: Zonewiki answers that MCP is off.
There, connect it with a token (below): **Connect an assistant** shows the whole command.

### ChatGPT and other MCP clients

Any assistant that supports remote MCP servers can connect.
Look in its settings for adding an MCP server or connector (in ChatGPT, connectors you add yourself are in developer mode).
Give it the assistant address, with the HTTP transport where it asks.
If it signs in through the browser, you allow it as above.
If it doesn't, use a token.

Zonewiki has been tested with Claude's apps and Claude Code.
Other clients follow the same standard, but how each one sets up a server is up to its maker: see its own help.

### A token for any client

1. Open **Settings** in the menu at the top right: it opens **Connect an assistant**.
2. Under **Your token** (**The wiki's token** in a wiki without users), type your password and click **Make a token**.
   A wiki without a login asks for no password.
3. Copy the token.
   It is shown only this once: Zonewiki keeps only a fingerprint of it.
4. Give it to your assistant:
   - Claude Code: the page shows the whole command, ready to copy:
     `claude mcp add --transport http zone <address> --header "Authorization: Bearer <token>"`.
   - Any other client: add an MCP server with the HTTP transport at the address and the header `Authorization: Bearer <token>`.

You have one token.
**Make a new token** replaces it, and the old one stops working; **Revoke the token** ends it.
Signing out or changing your password doesn't end a token, so keep it like a password.

## What an assistant may do

An assistant acts on behalf of the user who connected it.
It sees the pages and tasks you see and changes what you may change: no more.
With users, another user's private pages don't exist for it, and a group's pages only if you are in the group (see [Teams](teams.md)).
If you are a viewer, your assistant only reads.

Every change it makes is a commit in your name, marked as an assistant's, and you can undo it ([An assistant's changes](#an-assistants-changes)).

> [!WARNING]
> An assistant reads whatever you point it at, a mail or a web page too, and such text can carry instructions meant for it.
> Give writing access to assistants you trust, and look over what they changed now and then.

## Give a connection less

When you click **Allow**, you can give an assistant less than you have.
The **Connect an assistant?** page asks two questions:

| Question | Choices |
|----------|---------|
| **What may it do?** | **Read and change** (the default): it sees and changes what you do. **Only read**: it searches and reads, and changes nothing. |
| **What may it see?** (with users) | **The shared wiki** is always given. Untick **Your private pages, notes and tasks** or any group to keep it out. |

A connection that only reads is refused anything that would write, and tells you what it would have written.
A connection without your private place can't see your private pages, notes, private lists or Inbox, so it doesn't make any either: a task it adds goes into a shared list.
A group you untick is, for that assistant, like a group you aren't in.

Use this for an assistant you try out, one that answers questions for you in another tool, or one you want to keep away from your private notes.
Your password is asked again on **Allow**, so a browser left signed in isn't enough to connect one.
You can change your answers later, without connecting again ([See, change and end connections](#see-change-and-end-connections)).

## See, change and end connections

**Connect an assistant** lists, under **Connections**, the assistants you allowed: the name each gave, where its answers go, what it **May** do (with "Sees only: …" when you gave it less), since when, and when it was last used.

- **Change…**: the two questions of **Allow** again, ticked as the connection has them now.
  Pick what it may do and see, and click **Save**: it holds from the assistant's next call, nothing to do there.
- **End**: it is refused from then on.
  It can ask again, and you decide again.

Taking something away (**Only read**, unticking your private place or a group) needs no password.
Giving something back or more (**Read and change**, your private place, a group) asks for your password, as **Allow** does; the field shows when you tick such a choice.
If you are a viewer, you are asked only what it may see: it only reads ([more](../spec/mcp.md#change-what-a-connection-may-do-and-see)).
A connection also ends when it goes unused for 90 days, or when you set a new password through a reset link.

## An assistant's changes

Everything an assistant changes is a normal version in the history, in your name, with a mark that an assistant made it.
On **Changes** (from the search list, a page's History, or "your assistants' changes" on **Connect an assistant**), such a row says **through Claude**, the name the assistant gave when you connected it, or just **assistant** when it used a token.
When it said where its text came from, the row says so too ("from https://…").

- Tick **Only by an assistant** to see just those changes; **By** narrows it to one person's, **Through** to one assistant's, and **Only text from outside** to what it wrote from a web page, a mail or a file.
  **its changes** beside a connection on **Connect an assistant** opens that assistant's changes.
- **Undo** on a row takes that change back whole.
- On the latest change of an assistant's run of work (its changes through one connection, close together), **Undo all N** takes the whole run back at once, after asking.

An undo is a change too, and can be undone.
More in [History and undo](history.md).

## What assistants can do

An assistant has a set of tools, which it picks by itself from what you ask.

| Area | It can |
|------|--------|
| Read and search | Search the wiki and the tasks, find every place exact text occurs, read a page or one section, list pages and folders, list tags and task lists, find broken links. |
| Write pages | Create a page, edit or append to one, find and replace across pages (showing a preview first), rename or move a page or folder with links following, delete a page. |
| Tasks | Find tasks by tag, list, person or status; add a task; change one (dates, tags, list, assignee, done, a comment); put tasks in order; make and rename lists and tags. |
| Notes | Write a note, find notes, file a note into the wiki or onto a page. |
| Files | Look at an uploaded image or text file, upload a file. |
| History | See recent changes or a page's versions, restore an old version or a deleted page, undo changes. |
| Guides | Read [your guides](wiki-guide.md) before it writes, so it follows how your wiki is kept. |
| Zonewiki's own help | Look things up in Zonewiki's documentation. |

It can't delete tasks (it closes them as won't do, as you would), change settings or users, or do anything you can't do yourself.

### Things to ask

```text
What did I write down about the garden irrigation?
Summarise my notes from this week and file each into the right page.
Add a task for Anna to send the Q3 plan by Friday, in the team list.
Put the tasks of the move list in the order we just talked about.
Which of my #work tasks are overdue?
Rename the page "Meeting notes" to "Meetings" and keep the links working.
What changed in the wiki since Monday?
Undo what you just did to the Q3 plan page.
```

When it looks something up elsewhere, in your mail or on the web, and finds a fact a wiki page lacks or gets wrong, it offers to add it, and writes only when you say yes.

## Ask your assistant about Zonewiki

Your assistant can read Zonewiki's documentation, this manual among it.
Ask it how Zonewiki works, in your own words:

```text
How do I make a task repeat every two weeks?
How do I share a page with only my group?
Where do I make a token for a script?
```

It answers from the docs instead of guessing.

## Tell assistants how your wiki is kept

Write down where things go, which tags you use and what stays private on a page called `wiki-guide`, and every assistant follows it from its next session.
See [The wiki guide](wiki-guide.md).
