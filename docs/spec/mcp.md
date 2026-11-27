# MCP server

Part of Zonewiki's spec (see `AGENTS.md` for the index).

How an AI assistant (any MCP client) works with Zonewiki: what the MCP server is, how an assistant connects (a token, or signing in through the browser with OAuth, and what a connection may see and do), the tools and what each does, what every write does, guides, who sees a page, and the links in results.
How it works (the protocol, the instructions, the size of the tool list, retrieval, auth and OAuth's internals) is at the end.

## What the MCP server is

`POST /mcp` lets an LLM client (any MCP client) read and write the wiki and the tasks through tools.
The assistant acts as the user whose token it is: it sees and changes what its user does, no more.
Every write is a git commit the user can undo, marked as an assistant's.

### What the assistant is told

When a client connects, Zonewiki sends it instructions (The instructions, under How it works) telling the model what Zonewiki is,
which tools find what,
to call `zone_help` for questions about Zonewiki itself,
and how to write (search before creating a page, extend rather than duplicate, edit_page/append_to_page over rewriting, a summary per write, `read_guides` before drafting).
The wiki's guide follows them (Guides).

**Offer to capture**: when the model researches something elsewhere (an issue tracker, chat, mail, code, the web) and finds a fact a wiki page on the topic lacks or contradicts, it offers to add it, naming the page and each fact in one line, once per answer, and writes only when the user agrees.
So the wiki keeps up with what the user looks up, without the model writing on its own.

## Connecting an assistant

**Two ways in.**
With a login, a client is given only the address, `http://localhost:5080/mcp`: it opens the wiki in its user's browser, they sign in and allow it, and it gets tokens of its own (Connecting with OAuth, below).
Or a client registers the address with the HTTP transport and the header `Authorization: Bearer <token>`, a token that doesn't change, from the page **Connect an assistant** (below) or from `./run.sh config token` (with users, `./run.sh user token NAME`): for a client that can't open a browser, a script, or a wiki without a login.
Either way the assistant acts as the user whose token it is.

### Connect an assistant

The page `/assistant` (`go/assistant.go`, `assistant.html`): the token made in the browser, without a shell on the host.
With users it is what **Settings** in the user menu opens (its first tab, **Assistant**; The settings pages in `docs/spec/navigation.md`) and makes the token of whoever is signed in, never another user's (no field names one);
without users it is in the menu at the top right all the same (three lines: **Settings**, or **Connect an assistant** in a wiki without a login; The user menu in `docs/spec/navigation.md`) and makes the wiki's token, and anyone who can open the wiki can open it (the shortcuts dialog links to it in every wiki too: "Connect an AI assistant").
A viewer may make one too: their assistant reads, and its writing tools are refused.

#### What the page shows

The page says where a client connects (`/mcp` on `base-url`, else on the address the request came in on: `siteURL`), whether there is a token, and has two plain forms.
It works without JavaScript.
What is to be copied (the address at the top, a new token, the commands) is a line of its own with a **Copy** button (`.copy-line`, `js/copyline.js`; without JavaScript there is no button, and a click selects the line).
The token's two actions are a row each: what there is ("Set" or "None") with Make a token, and Revoke.
A shared account has neither: its token is set on the host, and the row says so (Shared accounts in `docs/spec/users.md`).

#### Make a token

**Make a token** (`POST /assistant/token`) makes a new token (32 random bytes, as the commands make), kept as its hash in the settings file the way the admin page writes it (`changeSettings`, so every rule of the commands holds, and the log says "alice's MCP token (by alice)").
It replaces the one before, which stops working.
The answer to the POST shows it **once** (`Cache-Control: no-store`), with the line for Claude Code (`claude mcp add --transport http zone <url> --header "Authorization: Bearer <token>"`) and the header for any other client; a click selects each.
The page never shows it again: lost, make a new one.

**It takes the password again** (the user's own, or the wiki's; nothing to ask in a wiki without a login).
A token outlasts the session it was made in, and signing out or a new password doesn't end it, so a session someone else got hold of mustn't be enough to make one.
A wrong password is a 403 ("Incorrect password: no token was made."), and counts against the client as a wrong login does (`loginThrottle`: 429 with the wait), so the page guesses a password no faster than the login page.

#### Revoke the token

**Revoke the token** (`POST /assistant/revoke`, no password: it only ends access): the token is taken out of the settings file, the page says so (`?done=revoke`, said only when there is none), and `/mcp` answers 401 for it (403, off, when no token is left and OAuth is off).

#### Connections

With OAuth, the page says how an assistant signs in (the address to paste; the line for Claude Code, `claude mcp add --transport http zone <url>`), and lists the assistants its user allowed: the name each gave, the host its answer went to, what it may do (Read and change, or Only read) and, when that is less than its user, what it sees ("Sees only: …"), since when, the day it was last used with **its changes** under it (Changes by the user through that connection, `?user=<name>&via=<client>`: Filtering Changes in `docs/spec/rendering.md`), newest first.

- **Change…** sets what one may do and see, with the choices its user had on Allow (Change what a connection may do and see, below).
- **End** (`POST /assistant/connections/<id>/revoke`, no password) ends one: its tokens are refused from then on (`?done=disconnect`); another user's is a 404.

### Connecting with OAuth

`go/oauth.go`, `go/grants.go`, `oauth.html`.
What Claude's apps (web, Desktop, mobile: a "custom connector"), Claude Code (`claude mcp add --transport http zone <address>`, then `/mcp`) and ChatGPT (a connector, in developer mode) do with a remote MCP server: the user pastes the address of `/mcp`, a browser opens, they sign in to Zonewiki and click Allow.
Zonewiki is its own authorization server, on the same address, as the MCP authorization spec (2025-11-25) has it; nothing but Go's standard library.
How it is built (the metadata, registration, codes, tokens, where connections are kept) is in How OAuth works, at the end.

#### Only with a login

Someone has to sign in and allow, so OAuth is on when the wiki has users or a password (`oauthOn`), and there is none in a wiki without a login (its routes are a 404, `/mcp` answers as without it).
`mcp-oauth = no` in the settings file turns it off: static tokens only, and the connections there are stop working until it is on again.

#### What reaches it

An assistant that runs on its maker's servers (Claude's apps, ChatGPT) calls Zonewiki from there, so the wiki must be on the internet over https (set `base-url` and `hosts`).
One on the user's computer (Claude Code) reaches any wiki that computer does.

#### The question

The question (`GET /oauth/authorize`, signed in: anyone else goes through the login and comes back, the question kept in `next`): the page says who asks (the name the client gave: it is only a claim), where the answer goes (the redirect URI's host), as whom the assistant will act ("as you, alice"), and for an address on this computer that any program on it could ask in this name.
**Allow** and **Deny**.

- **What may it see?** (`see` in the form, with `choose`; Connections given less to see, below): the shared wiki (always), the user's private pages, notes and tasks, and each group they are in, all ticked.
  Not asked in a wiki without users.
- **What may it do?** (`may` in the form; Read-only connections, below): **Read and change** (the default: "it sees and changes what you do, no more") or **Only read** ("it searches and reads what you see, and changes nothing").
  A viewer isn't asked: theirs reads only, and is kept as a connection that only reads, so it stays one if they are made an editor later (then they may give it more on Connect an assistant, with their password).
- The two questions are a template of their own (`partials/reach.html`), which Change… on Connect an assistant shows too.
- **Allow takes the password again** (`POST /oauth/authorize`), as making a token does and for the same reason: a connection outlasts the session, so a session someone else got hold of mustn't be enough.
  A wrong one is a 403 ("Incorrect password: nothing was allowed.") and counts against the client as a wrong login (`loginThrottle`: 429).
- After Allow the page says "Connected" and the browser goes on to the client; Deny goes back to it too (The answer is a page, under How OAuth works).

#### What ends a connection

End on Connect an assistant; the assistant disconnecting, if it tells Zonewiki (`/oauth/revoke`: A client's disconnect, under How OAuth works); its refresh token expiring or being used twice; its user being removed (the Users tab, `zonewiki user remove`); its user setting a new password by a **reset link** (they may have lost the account to someone; the welcome page says so).
A disabled user's are refused while they are disabled, and work again after.
An ordinary new password (Change password, `zonewiki user passwd`) leaves them, as it leaves a static token.

#### What OAuth doesn't offer yet

**Not yet**: Client ID Metadata Documents, CORS for clients that run in a browser.
A client that doesn't call `/oauth/revoke` when its user disconnects it leaves its connection in Zonewiki: End ends it.
Tested against real clients so far: Claude.

### Read-only connections

A connection given less than its user, chosen on the question or with Change… on Connect an assistant (`ReadOnly` on the grant, `read_only` in `.zone/grants`; `asker.ReadOnly` of who asks with its token, `changesNothing` in `go/access.go`).
The permission model refuses it every write, as it refuses a viewer's (`canWrite`, `mayWriteTasks`), whichever tool or route asks; the doors below ask first, for an answer that says why.
It reads as its user does, and changes nothing:
`tools/list` gives it the tools that write nothing (as a viewer's),
a writing tool called all the same is refused before it runs (`callMCPTool`, by the table every tool's writing is in: "This connection only reads: whoever connected you chose that. Nothing was changed. …", so the assistant says what it would have written),
and an upload with its token is 403.
It stays read-only through every refresh of its tokens, until its user lets it read and change again with Change…, with their password (a viewer's can't be: Change what a connection may do and see).
The same user's other connections, and their MCP token, are as they were.
A connection kept from before there was a choice reads and changes, as it did.
Not yet: a token that only reads.

### Connections given less to see

With users, a connection can be given only some of its user's places (`less` on the grant: `no_private`, `groups` in `.zone/grants`; `asker.NoPrivate`, `asker.OnlyGroups`).
The places are the ones the wiki has: **the shared wiki** (always: the pages and tasks everyone sees), **the user's private place** (their private pages, notes, private lists and the Inbox), and **each group** they are in (its pages, and its lists).
The question asks **What may it see?**, a box each, all ticked; what is unticked isn't there for the assistant.
All groups ticked means every group of the user's, also one they join later; otherwise the ticked ones, and never a group the user isn't in (or has left).

A place that wasn't given is, to that connection, what another user's private folder or a group they aren't in is to anyone: no page, task, list, upload, search result, count, version or commit message names it, at any tool or route its token reaches.
It holds because the limit is in the questions everything asks (`place.sees`, `canSeeTask`, `canSeeList`, `inGroup` in `go/access.go`; the task store reads no private file for it), not in the tools.
What is made once for all of a page's readers asks for the least of them: search reads a page with its `[[task:…]]` as the tasks' titles, and names there no task of a list for a group (`taskRefsAsTitles`), else a private page that refers to one would give its title to a connection given the private place but not the group.

**Without the private place** it makes nothing private either, since it couldn't see it again: a note, a page under `private/`, a private list, a task without a list (which would go to the Inbox) and a move into the Inbox are refused, saying so ("This connection wasn't given your private pages, notes and tasks …: use a shared page or a shared list").
A list it makes isn't pinned (pins are kept with the user's own lists), and My tasks' own order and settings aren't read.

A list that names the user themselves (`for = alice`) is theirs whatever groups the connection has; one for `@dev` goes with the group dev.
Connect an assistant says what such a connection sees ("Sees only: the shared wiki; the group dev"), and its Change… gives it less without the password and more with it (Change what a connection may do and see).

Not offered: a connection without the shared wiki, or with one folder or list of it.
A shared folder is no place: commit messages, links and counts name shared pages to everyone who sees any; a folder or list for an assistant alone is made a group's.
The static token is one per user and stays so: several assistants are several connections.

### Change what a connection may do and see

**Change…** on a connection's row opens a dialog with the questions of Allow (The question, above), **What may it do?** and **What may it see?**, ticked as the connection has them now, and **Save** (`POST /assistant/connections/<id>/change`, `?done=change`: "Changed what that assistant may do and see, from its next call on.").
It is the same form as Allow's, from the same code: the template `partials/reach.html`, and `chooseReach` in `go/oauth.go`, which reads the choices (`may`, `see` with `choose`) and makes what the connection is given less than its user (`less`).
Without JavaScript the row's `<details>` unfolds the form instead of the dialog (`js/assistant.js` moves it into the dialog).

- **Taking away needs no password**: to only read, without the private place, without a group.
- **Giving anything back or more takes the password again**, as Allow does: read and change where it only read, the private place where it had none, a group it didn't have; a change that takes one thing away and gives another counts as giving more (`less.beyond` in `go/grants.go`).
  The dialog shows the password field only while a choice gives more (a choice it has now is marked `data-had`); the server decides all the same.
  None is a 403 ("That gives it more than it has: enter your password.") and no guess; a wrong one a 403 ("Incorrect password: nothing was changed.") counted against the client as a wrong login (`loginThrottle`: 429 with the wait).
  Refused, the page comes back with the dialog open as chosen and why.
- **It goes no further than its user**: a viewer isn't asked what it may do (theirs only reads, whatever is posted); only the groups its user is in now are offered and taken (a group named in the form that isn't theirs is given no one).
  Every group ticked is every group of theirs, also one they join later, as on Allow, unless the connection had only some and none is new: then it keeps those it has, so ticking nothing new never hands it the groups its user joins later.
- **What it has is what the change is judged by**, when it is posted, not what the page showed: a second tab that still shows Read and change can't give it back to a connection the first made read-only without the password.
  A change made meanwhile between that read and the write is a 409 (`errGrantChanged`).
- **It holds for the tokens it has**, from their next call: the asker of a connection's call is made from its grant at every call (`connectionUser` in `go/mcp.go`), and a refresh keeps what it was given.
- Only the user's own: another user's connection, or one that isn't there, is a 404.

## Tools

The tools are defined in `go/mcp_tools.json` and listed below in two tables, those that read and those that write, each followed by a section per tool; uploads have their own section (Files).
A tool that writes is refused to a viewer and to a connection that only reads.

- **Pages** are named as in read_page: a slug, `/page/<slug>`, a full URL, `<slug>.md` or the page name (`mcpPageSlug`).
- **A task** as the tools show it (`mcpTaskText`) includes `id:`, its 8-character short id (`shortID`), which the task tools take and a page can use as `[[task:…]]`.
  A task `id` is its id or a prefix of it (`findTasks`: open, closed and archived tasks; an exact id wins; several matches are an error), and a `[[task:…]]` reference or a `/tasks?task=…` link work too (`findTask`).
  `mcpTaskText` shows a task's title, a line of facts (status, private, tags, with users `assigned to: <name>` (marked when it is the user, or someone who can no longer be given tasks), importance when it isn't normal, start, due, repeat, keep, list), the `/tasks?task=…` link (`taskHref`), its description and its comments.
  The tools call a task's text `description`, as the task's file and the edit dialog do; `notes`, its name in the tools before, is refused with a message saying so (an argument a tool doesn't know is otherwise dropped without a word, and an assistant's tool list may be older than the server).
- **A tool failure the model can fix** (bad slug, empty query, text not found) is a result with `isError`, not a protocol error.
  The tasks API's 400s (bad date, empty title, bad interval) come back as tool errors (`refusalOf` in `callMCPTool`).

### Reading

Annotated `readOnlyHint`.

| Tool | Does |
|------|------|
| `search_wiki(query, limit=8, under, notes)` | The most relevant **sections** of pages, full text, best first, and matching tasks; `under` a folder, `notes` only notes. |
| `search_tasks(tag, list, assignee, status="open", query, order="deadline", limit=50)` | Tasks filtered by tag, list, assignee and status, ranked by a query or listed by deadline or the manual order. |
| `read_page(slug, heading)` | A page's raw markdown with its facts (URL, file, hash), its task references and its place in the link graph; `heading` one section. |
| `grep_wiki(pattern, ignore_case=false, under, limit=50)` | Every line of every page matching a regex. |
| `list_pages(limit=100, under, orphans=false, notes=false)` | Pages, most recently edited first, with how many link to each; one folder, orphans or notes only. |
| `list_tags()` | Tags in use on pages and tasks, with counts, then the task lists the user sees. |
| `history(slug, version, since, under, limit=20)` | Git history: the wiki's recent commits, a page's versions, or what one version changed. |
| `zone_help(query, topic, heading, limit=5)` | Zonewiki's own documentation, searched by passage, or a doc or one section whole. |
| `read_guides(slug, guides)` | The guides for a page, a folder or the whole wiki, with the ids writes pass. |
| `read_file(url)` | An upload a page or task links to, by the link's target (Files, below): an image as an image, a text file as its text. |
| `check_links(under)` | Every link leading to no page, by page, with the page it was probably meant for. |

#### `search_wiki`

The most relevant **sections**, full text, best first, each with title › heading, slug, the page's last edit date and who made it (`edited: 2026-10-02 by alice through Claude`, so the model can tell current notes from stale ones, and the user's own words from an assistant's: Who changed a page last in `search.md`), a URL with the heading anchor and the page's file on the host (`file:`, see Links and file paths).
Up to three may come from one page (`sectionsPerPage` in `search.md`).
Matching tasks are results too, ranked with the sections (Retrieval, under How it works).

- `under` restricts it to a folder: pages whose slug is that prefix or below it (`inFolder`, case ignored; `tickets`, `tickets/` and `/page/tickets` alike, `cleanFolder`), and no tasks.
  A folder without pages is a tool error, so the model corrects it.
- `notes` restricts it to notes (`retrieve(…, quicknote=true)`), no tasks either: "what did I jot down about X".
- `query` is at most 500 characters, a few words (A query is a few words in `search.md`); a longer one is a tool error (`queryArg`), as for `search_tasks`.

With `query-log = yes` in the settings file each query is logged (Measuring retrieval in `search.md`).

#### `search_tasks`

Tasks filtered by these, separate lists included:

| Argument | Filters |
|----------|---------|
| `tag` | Comma = all must match, case-insensitive. |
| `list` | The tasks in that list (a list is no tag); `inbox`, or `none`: the Inbox, the tasks in no list, with users the user's own. |
| `status` | `open`, `closed` = done or won't do, `all`, or `updated`: the tasks with news for the user that they haven't seen, open or closed, as their Tasks links count them. |
| `assignee` | With users, a user's tasks, read as an embed's `assignee=` (`assigneeParamMatches`): `me` (the assistant's user: "my tasks"), a user name, `none`, `a\|b`, `-a`; without users it is a tool error. |

A task with news says in its line of facts what is new, all of it since the user last looked, the oldest first, with the time of the latest, as its card in Updates does: "update: bob commented twice, anna closed it (2026-03-12 09:30)" (`newsAll`, `newsText`; Updates in `docs/spec/tasks.md`); an assistant reading a task doesn't see it for the user: no tool logs `seen`.

With a query (at most 500 characters, as `search_wiki`'s), ranked by BM25 within the filter (fused with semantic hits: Retrieval).
Without, open tasks by deadline, then start date, then title, done ones newest first.
`order="manual"` lists the open ones in the manual order instead (`sort_order`, the Open view's list, top first: their priority; `manualBefore`), as the Open view shows it: a new task on top.
Without `list`, the tasks of My tasks come first, in the user's own order as that view shows it (My tasks' own order in `docs/spec/tasks.md`; `mineOrder`): a task new to it on top, so "top first" is the same for the assistant and in the browser; then every other task in the shared order.
With `list`, the shared order alone, as the list's view shows it, so what `update_task`'s `position` did reads back.
The result starts with "Today is …" so the model can tell overdue from upcoming.

#### `read_page`

A page's header, then its raw markdown, frontmatter included:

- its slug, URL and host file (`file:`);
- its hash (`hash:`, the whole file's, also when reading a section; kept as a base for merges, `keepBase`, for write_page's `base_hash`);
- after the text, a list of the tasks its `[[task:…]]` references stand for;
- then its place in the link graph (`pageLinks`):
  `Linked from (N): …`, the pages linking to it (`findBacklinks`, the page's Backlinks; the first 50 named, then "… and N more"),
  `Broken links (no such page): …` when it has any (`missingLinks`),
  and, with semantic search, `Related, not linked (close in meaning): …`: the page view's Related (`related`, its floor and its prose minimum), pages linked either way left out, so the model sees connections not written down yet.

So the model sees where a page sits without reading other pages.

With `heading`, only that section (`sectionLines`, the same match as append_to_page's `under_heading`: subsections included, headings in code fences don't count), with a `section: lines A–B of N` line in the header, numbered as grep_wiki numbers them.
A missing heading is an error listing the page's headings.
Task references and broken links are then the section's.

Accepts a slug, `/page/<slug>`, a full URL, `<slug>.md` or the page name (`mcpPageSlug`); `safePath` keeps it inside the wiki.
A whole guide's header also has its id (The guide gate).

#### `grep_wiki`

Every line of every page matching a regex, as `slug`, then `  <line>: <text>` under it (lines cut at 300 characters), with the total count and how many are shown.
For exact text and all its occurrences (ticket ids, names, TODOs, a term to replace), where search_wiki ranks and returns at most 20 sections.
Pages only: no tasks, nothing in `_` folders.

`grepPages` runs `git grep --no-index -P` (Perl-style; `-E`, extended regexes, when git is built without PCRE, found out once) in its own process, uncommitted pages included, with a 10s timeout (`grepTimeout`): a pattern that backtracks badly is killed rather than holding up the single server process, which Python's `re` couldn't be.
A bad pattern is a tool error that says what is wrong with it; the timeout, or the regex giving up on a line (PCRE's match limit), is one that says only that the search failed.
Without git, the pattern is matched as plain text and the result says so.

**The regex reads only what its asker sees.**
git is given the folders of `seenFolders` (`go/access.go`) as pathspecs, and no other: the wiki's top, its shared folders, and in `private/` and `groups/` the folders of the places the asker sees (judged as `placeOf` judges, in lower case), with Zonewiki's own folders (`_…`, `.…`) left out at every depth.
A folder made since they were listed isn't searched.
Filtering the hits afterwards wasn't enough: a pattern that gives up only on a line holding a word (`^(?=.*word)(?:.|.)*(*FAIL)`) failed only where that word was, so the error read other users' pages, uploads and drafts a guess at a time.

#### `list_pages`

`slug | title | edited date | pages linking to it` (`allBacklinkCounts`, the Backlinks rule), most recently edited first.

- `under` lists one folder.
- `notes` lists only notes ("Notes, …"; none: "No notes.").
- `orphans` keeps only pages no other page links to (`isOrphan`), leaving out the home pages (the wiki's, the private one, a group's) and notes, which are reached otherwise (the module switcher, `/private`, `/groups`), and `wiki-guide`, which the assistant reads; the search modal's "Unlinked only" filter keeps those.

#### `list_tags`

`tag | pages | open tasks | done tasks`, most used first: page frontmatter tags (`mdtext.PageTags`) and the tags of every task (`allTasks`, archived ones included), lowercased.
Then the task lists the user sees, apart (`mcpListsText`: each with its open tasks, whether it is private or left out of My tasks, and that a new task without a list is in the Inbox), since a list is where a task is and no tag.
So the model reuses tags instead of inventing `back-end` next to `backend`.

#### `history`

Git history, three ways:

| Called with | Gives |
|-------------|-------|
| no `slug` | Recent commits across the wiki (`wikiChanges`, the list the browser's Changes shows: `when \| version \| message \| pages and tasks`, a page by its slug, a task it changed by its title and short id, `"Water the beds" (task 1a2b3c4d)`, as Changes names it, else "tasks": The tasks a commit changed in `docs/spec/rendering.md`), from the day `since` (YYYY-MM-DD) or in a folder (`under`), after a line naming the files with uncommitted changes (`gitUncommittedFiles`); `assistant` and `outside` filter as Changes' **Through** and **Only text from outside** do (`changesOf`, one function for both doors: Filtering Changes in `docs/spec/rendering.md`), `assistant: "any"` as **Only by an assistant**; `watched` as **Only what I watch**, and in any list a change that is news to the user ends in `| new` (Watching pages in `docs/spec/navigation.md`). |
| `slug` | That page's versions (`gitLog`, across renames: `version \| when \| +added -removed \| message`), a deleted page's too, saying so, and saying when the page has uncommitted changes newer than the first one. |
| `slug` and `version` | What that version changed, as `gitDiff` with the rename handling of the browser's Changes view. |

An assistant's change is marked after its message, `(by alice through Claude)`, `(through an assistant)` without users or a name (Which assistant); its message is shown without the `(MCP)` its first line ends in.
Without git, a tool error.
A page that isn't there (deleted) is named as `read_guides` names a page not yet written (`pageOrSeenSlug`): it asks only that the asker may see it, so a viewer and a connection that only reads read a deleted page's history too; one in a place they don't see is refused as a write there is, saying nothing of whether it was ever there.

#### `zone_help`

Zonewiki's own documentation (`go/manual.go`), so the model answers how-to questions about Zonewiki (how to rename a task list's tabs, a setting, the query syntax) from the docs instead of guessing.
The docs are the manual, `docs/manual/*.md` (topic `manual` for its contents, `README.md`, and `manual/<page>` for a page, in reading order: Help in `docs/spec/routes.md`), `README.md` (topic `readme`) and `docs/spec/*.md` (topic = file name), all but `testing.md`;
the binary carries them, and `docsRoot` takes a checkout's instead (What the binary holds in `docs/spec/deployment.md`); with `--docs` naming a folder without them, the tool errors.

- With `query`, the best passages by BM25 (`manualFind`: sections split by `chunkSections` into pieces of at most `manualChunkChars`, 1500, the doc's title, topic and heading weighted like a page title; the index is rebuilt when a doc file changes), each as `Title › heading` with its `topic:` and `heading:`.
  The manual's best passages take the first half of the places (rounded up), the README's and the spec's the rest, each filling what the other leaves: the manual answers in a user's words, the spec has every rule.
  Their scores aren't compared, since a spec section that lists files scores high on any word it names.
- With `topic`, the whole doc (`tasks.md` and the case of the name are accepted); with `heading` too, one section, as read_page's `heading` (`sectionLines`), a missing one an error naming the doc's headings.
- With neither, the list of topics with their `##`/`###` headings.

The description tells the model the manual is written for users and the spec for developers, so it answers in what the user types or clicks, and points the user to the manual's page in the browser (`/help/<page>`).

#### `read_guides`

The guides for a page (also a new one) or a folder (`messages/`, or a folder's name without the slash), or without `slug` the whole wiki's (`readGuides`): each as `=== guide <slug> (id <id>) ===` and its text, the whole wiki's first, after a line with the ids to pass with writes.
Ids in `guides` (ones the model already has) are named, not repeated.
Reading them counts: a write with those ids goes through at once (The guide gate).
A name without the slash is a folder when the asker sees a page in it, never by what is on disk, and what `safePath` refuses (a folder outside the wiki, Zonewiki's own `_…` and `.…`) is refused before anything is looked at: so the answer tells nothing of another user's private folder, a group's the asker isn't in, or the server's folders.

Its description tells the model to call it before drafting anything meant for the wiki, and ends with the folders that have a guide and each guide's title (`toolList`, built at each `tools/list`; at most `guidedShown`, 25, then "… and N more"), so the model knows from the start, before it writes a word, that `messages/` has instructions.
Tool descriptions stay in the model's context like the instructions, but aren't cut at 2 KB.

#### `check_links`

Every link leading to no page, by page: `[[wikilinks]]` (`missingLinks`, the renderer's rule) and markdown links that resolve to a page slug (relative to the page's folder, `/page/` links too; `mdLinkMisses`; external links, anchors, folders and files such as `_assets/` aside), code aside.
Each comes with the existing slug it was probably meant for (`difflib.get_close_matches`, cutoff 0.8).
Writes name the new ones in a note in the same form (Wikilink check).

### Writing

The wiki is mostly written by LLMs, so they write through Zonewiki rather than to the files: each write is a git commit (no `/uncommitted` pile), a rename re-points links, and the result tells the model what it would otherwise have to look up.
Every write tool takes an optional `summary` for its commit message (What every write does), and every page write takes `guides` (The guide gate).
Tools that only add are annotated `destructiveHint: false`, those that change or remove `true`, so a client can ask before those.
The token `/mcp` requires (Auth) guards writes as it guards reads, and git keeps every change undoable.

| Tool | Does |
|------|------|
| `write_page(slug, content, overwrite=false, base_hash, note, summary, source)` | Creates a page, or with `overwrite` replaces one (merged with changes since `base_hash`); `note=true` makes a note. |
| `file_note(slug, name, folder, keep_date, page, heading, summary)` | Moves a note into the wiki (`name`) or appends it to a page (`page`), as the note's ⋯ actions do. |
| `upload_file(filename, text, content_base64, page)` | A file into the uploads, from its text or its bytes as base64 (Files, below); the answer is the markdown that links it. |
| `edit_page(slug, old_string, new_string, replace_all=false, edits, summary, source)` | Exact text replacement, one or a list (`edits`), in one commit. |
| `replace_in_pages(find, replace, regex=false, ignore_case=false, include_code=false, under, dry_run=true, max_pages=50, limit=50, summary)` | Find and replace across pages, in one commit; a dry run by default. |
| `append_to_page(slug, content, under_heading, summary, source)` | Adds at the end of the page, or of the section under a heading. |
| `rename_page(slug, new_slug, narrow=false, summary)` | Renames or moves a page or a folder, links following, in one commit. |
| `rename_task_tag(tag, new_tag, merge, summary)` | A task tag renamed on all tasks and in embeds and `/tasks` links on pages. |
| `delete_page(slug, summary)` | Deletes and commits (restorable from Deleted Pages); names the pages that still link to it. Not the home page. |
| `undo_changes(versions, summary)` | Takes back changes named by their versions, as Undo on Changes. |
| `restore_page(slug, version, summary)` | Writes a version's text back; brings back a deleted page. |
| `create_task_list(name, private, group, summary)` | A new task list: everyone's, a group's, or private. |
| `rename_task_list(list, new_name, summary)` | A task list's name, on its tasks, in pins and on pages. |
| `create_task(title, description, tags, list, assignee, importance, due, start, repeat, repeat_every, repeat_from, keep, position, after, before, summary, source)` | A task, as `POST /api/tasks` makes it, where it goes in the manual order. |
| `update_task(id, …same fields…, share, follow, add_tags, remove_tags, comment, status, ids, summary, source)` | Every change to existing tasks: fields, tags, a comment, status, its place in the manual order, several tasks' tags and order at once. |
| `watch_page(slug, watch=true)` | The user watches a page, or a folder with a `/` after it; `watch=false` stops. |

#### `write_page`

Creates a page.
`content`'s description gives the frontmatter's format and keys (`tags`, `tldr`, `sources`; see Frontmatter in `rendering.md`), since a model writing a page otherwise learns them only from pages it has read.
The slug may be a name (`Tickets/BUG 1234` → `tickets/bug-1234`, `mdtext.ToSlug`); folders starting with `_` or `.` are refused (`newSlug` via `safePath`: Zonewiki doesn't list or serve pages there).
Content gets one trailing newline.

**An existing page** (`mcpPageSlug`) is an error that points at edit_page/append_to_page, unless `overwrite`, which replaces its text.

- **`base_hash`** (with `overwrite`; read_page's `hash:`): if the page changed since the model read it (the user edited it meanwhile), the model's text is merged with that change as the editor's saves are (`mergeWrite`, `mergeText`; Merge on save in `editor.md`), and the result says it merged.
  Where both changed the same lines nothing is written, and the error shows only the clashing blocks with git's markers (`conflictBlocks`).
  A hash Zonewiki no longer keeps (a restart) or no git: nothing written, read it again.
- Without `base_hash`, an overwrite replaces whatever the page holds (what it held uncommitted committed first: Pages open in the editor).

**A new page's result** names up to five pages whose title or file name is close (`similarPages`: `difflib` ratio ≥ 0.8 on normalised titles and last slug parts), so the model notices when it should extend one instead.
With semantic search it also names the three pages closest to it in content (`closeInContent`, `relatedToText`: the new text's sections embedded at once, since the worker indexes the page later; pages it links to and those already named for a similar name left out), which a different name doesn't hide, with the advice to merge into one that covers the topic, else link them.

**`note=true`** makes a note instead (`writeQuicknote`, `quicknote.create`, as the browser's New note):

- `slug` is then just its name in any words (`Alex prep`), and Zonewiki puts the date and time in front (`2026-10-15_09-30-00_alex-prep`; a `/` is a hyphen, and a second note of that name in the same second gets the next second).
- Without a `# Title` in the content (after any frontmatter) the name becomes it; without either it is an error.
- `overwrite` is an error (a note is always new); the wiki's guide gates it as a page at the top level.
- The result says it is in Notes, not the wiki, and names file_note.

#### `file_note`

The note's two ⋯ actions (Notes in `navigation.md`), through the same code (`quicknote.move`, `quicknote.append`), so the files, link updates and commit messages (`Promote …`, `Append …`, plus the summary and `(MCP)`) are the browser's.
Exactly one of `name` and `page`:

- **`name`** (with `folder`, `keep_date`): Move to wiki, the new page at `promoted_slug(folder, name, date)`; the result reminds the model that nothing links to it yet (the browser's Link from, which opens an editor, has no MCP form).
- **`page`** (with `heading`, default `Title (YYYY-MM-DD)`, `appendHeading`): Append to page, the note as a `## heading` section at the page's end, its tags into the page's, the note deleted, links to it pointing at the section; the result gives the section's URL.

Refused: a slug that is no note (pointing at rename_page/append_to_page), the browser's refusals (a taken name, one still a quicknote name, a missing page, a heading the page already has).
Uncommitted edits of the note and the page are committed first (`keepPending`).
Guides gate the target page.

#### `edit_page`

Exact text replacement, the semantics of a coding agent's edit tool: `old_string` must occur once (else an error with the count) unless `replace_all`; not found is an error pointing at read_page.
An empty `new_string` deletes.
No read is needed first, so a passage from search_wiki can be edited directly; a concurrent change makes the match fail instead of being overwritten.

`edits` (instead of `old_string`/`new_string`, not with them): a list of such replacements, each applied to the text the ones before it left (`applyEdit`); one that fails is an error naming it (`Edit 2: …`) and nothing is written.
One commit for all.

#### `replace_in_pages`

Find and replace across pages, in one commit: a term, a name, a ticket prefix, a link spelled one way everywhere.
Where it'd otherwise take grep_wiki plus an edit_page per page, or `sed` on the files and a commit by hand.

- **`find`** is matched within a line (`replaceLines`), so it can't contain a line break (a passage spanning lines is edit_page's job).
  Literal text, or with `regex` a regular expression (RE2's syntax, as Go has it: Perl's classes and groups, `(?P<name>…)` or `(?<name>…)`, `(?i)`; no lookarounds or backreferences, an error the model sees, and `\d`/`\w`/`\s` are ASCII) whose groups `replace` names as `\1` or `\g<name>`, as Python's `re` did.
- **Code is left alone** (fenced blocks, inline code spans; a match touching one stays) unless `include_code`; frontmatter isn't code.
- Pages only, not tasks; `under` limits it to a folder (an unknown one is an error).

**By default a dry run**: nothing is written, and the result lists each changed line as `  <line>: - old` / `  <line>: + new` under its page (the first `limit` lines, cut as grep_wiki cuts them), so the model (or the user) checks it before calling again with `dry_run=false`.
A write changing more than `max_pages` pages (at most 1000) is refused, with nothing written; the dry run says so beforehand.

A write first commits the pages' uncommitted changes as `Save pages before replacing "<find>"` (`keepPendingPages`; one commit per user who auto-saved them, Zonewiki for the rest), then commits `Replace "<find>"`, and names the pages changed and any wikilinks the replacement made that lead nowhere.
The commits name no page and no count: the pages may be the writer's private ones beside shared ones, and the history shows everyone else the pages of a commit they see, so a count would tell of the others.

**A regex can't hang the server**: RE2 matches in time linear in the text (it never backtracks), so even `(a+)+$` runs in the server, without the process and timeout the Python Zonewiki needed (whose regexes had lookarounds, at the price of a pattern backtracking for hours).

#### `append_to_page`

Adds at the end of the page, or at the end of the section under a heading
(`appendAt`: the headings the markdown parser finds, `pageHeadings`, so setext ones too and not a `##` in a comment or the frontmatter; any level, compared as plain text without case and leading `#`s;
the section ends before the next heading of its level or higher, headings in fenced code don't count; subsections belong to it).
A missing heading is an error that lists the page's headings.
For logs: no read of the page first.

#### `rename_page`

`movePage`, as the editor's rename: links on pages and in tasks follow, and the page's own relative links are re-based for its new folder, all in one commit; the result says how many files changed, of those the user sees (the links on other users' private pages, a group's they aren't in or a list for some follow too, but aren't counted to them).
The home page can't be renamed, and an existing target is an error.

**To fewer readers**: with users a move that takes a page (or a folder of them) from people who see it is an admin's, or that of whoever shared it while nobody else changed it (Who may move a page to other readers in `docs/spec/users.md`): it answers "Not moved: bob, erin will no longer see … call again with narrow=true" until `narrow` is true, and "Ask an admin" to anyone else.
A move to more people is not asked about.

**A folder** moves too (`renameFolder`, `moveFolder`): `slug` with a trailing slash (`tickets/`), or without one when no page has that name but pages are below it (`folderArg`; a page of the folder's name wins).

- Every page in it and its subfolders moves to `new_slug` + the rest of its slug, links to each following as for one page, all in one commit `Move <folder>/ to <new>/`, after the pages' uncommitted changes are committed as `Save <folder>/`.
- Refused, before anything moves: a target page that exists (all named), a folder into itself (`tickets` → `tickets/old`), a target in a `_`/`.` folder.
- Other files in the folder (images) stay where they are; subfolders left empty are removed.
- The result lists the moves (the first 20), and counts the files whose links changed as for one page.

#### `rename_task_tag`

`renameTagOp`, as Rename tag… on `/tasks?tag=` (Renaming a tag in `docs/spec/tasks.md`): a task tag on all tasks, done ones too, and in embeds and `/tasks` links on pages, in one commit.
A `new_tag` in use is refused unless `merge` is true.
Says how many tasks and which pages it changed, of those the user sees.
Not pages' frontmatter tags.

#### `undo_changes`

The browser's Undo on Changes, through the same operation (`undoChangesOp`; Undo under Version history in `docs/spec/rendering.md`): the changes named by their versions (as `history` lists them; a list, or one string) are taken back whole, their pages and tasks as before them, in one commit `Undo <sha>: <what it said>` or `Undo N changes: …`.
Refused, with the reason, for a change that changed other files too (a list's registry, a task's files, an upload), moved a page, changed a page the user may not write or a task they may not put back (What can be undone), or whose pages or tasks were changed again since (a commit, or text not committed yet).
A task the change made is deleted again, as only who made it or an admin deletes one: the one way an assistant takes a task away, undoable as any undo.
The answer says which pages are as before and which are deleted again, and which tasks are as before, deleted again or back.

#### `restore_page`

Writes a version's text back (`gitShow` at the page's path then, across renames) and commits `Restore <slug> to <sha>`, as the browser's Restore; a deleted page comes back the same way.
The delete commit itself has no text (an error pointing at the version before it), and a version the page already reads as is an error.

#### `create_task_list`

A new task list (`createListOp`, as New list… on the Tasks page: pinned for the user, last, so it is a pill of their list switcher):

- shared;
- with `group` (with users: a group the user is in) the group's, kept in its folder, which only its members see (A group's lists in `docs/spec/tasks.md`);
- or with `private` (with users) the user's own, whose tasks only they see.

The name is cleaned as a list's is (one word); a name of a list the user sees is a tool error.
The answer names the list and its URL.
A list is deleted in the browser.

#### `rename_task_list`

`renameListOp`, as Rename… on a list's Tasks page (Renaming a list in `docs/spec/tasks.md`): the list's name, on its tasks, in the users' pins, and in the embeds and `/tasks` links on pages that name it, in one commit.
A name of a list the user sees is a tool error (two lists are made one by deleting one into the other, in the browser).
Says how many tasks and which pages it changed, of those the user sees; under a guide for one of those pages it needs the guide's id, as the page writes do.

#### `create_task`

A task through `createTask`, the same code as `POST /api/tasks`.

| Argument | Takes |
|----------|-------|
| `assignee` | With users (`update_task` takes it too): who does it, a user name, `me` or `none` (Assigning tasks in `docs/spec/users.md`). |
| `list` | The list the task goes in, by its name as `list_tags` gives it, `""` or `inbox` for the Inbox (`update_task` takes it too). |
| `due`, `start` | The deadline and the start: `YYYY-MM-DD` or a date word (Dates, below). |
| `repeat` | daily/weekly/monthly/yearly/none; `repeat_every` 1–999. |
| `position`, `after`, `before` | Its place in the manual order: `top` or `bottom`, or right after or before a task (its id), one of the three (A task's place in the order, below; `update_task` takes them too). |

**Assignee**: the description sends "Anna should …" there rather than to a tag.
A name that can't be assigned is a tool error saying why.

**Lists**: without `list` a new task goes into the user's Inbox, as `c t` does (with users theirs alone; The Inbox in `docs/spec/tasks.md`), and with no assignee unless one is given.
A call that names someone else as assignee (with users) needs a shared list: without one it is a tool error that names the shared lists there are, since the Inbox is the user's alone.
The user's own name (`me`) needs none: a task in their Inbox is theirs, and no name is kept with it (A private task is yours in `docs/spec/users.md`).
`update_task` gives a private task to someone else only with `list` and `share` in the same call; the name alone is a tool error that says so, and nothing is shared without being asked.
A task in the user's Inbox or one of their private lists is theirs alone (Private tasks in `docs/spec/users.md`), one in a shared list shared, and with users a shared task is always in a list (`update_task` with `list: ""` on one is a tool error): a tag never decides, and a list's name given as a tag is a tag.
A name no list has is a tool error.
`update_task` moves a task with `list`: never a shared task into a private list, and a private one to a shared place only with `share: true`.
`private` is no argument anymore: given, the tool says to give `list`.

**A task's place in the order**: without `position`, `after` or `before` a new task goes on top of the manual order, as one made in the browser does (The manual order in `docs/spec/tasks.md`).
So tasks made one after the other came out the wrong way round there, the last one first; `position: "bottom"` on each keeps them in the order they were made, and the description says so.
The place is the `position` field of the write (`positionField`, `moveTo` in `go/tasks.go`), in its commit, and the same operation as a drop in a list's view (`placeTasks`, the moves of `POST /api/tasks/reorder`):

- `top` puts the task above the first task, `bottom` below the last, `after`/`before` right next to that task, between it and its neighbour (`movesTo`): a number between theirs (`sort_order`), so no other task is written.
- The manual order is one for all tasks, so a place between two tasks is between them in every view that shows both: their list's, All tasks, `search_tasks` with `order="manual"`.
  My tasks' own order, the user's alone (My tasks' own order in `docs/spec/tasks.md`), is another one: a position leaves it as it is, as a drop in a list's view does.
- `update_task` with `mine: true` puts the task at the position in that own order instead, for "put this first for me" (`mineArg`, `positionMine`): no task is written and no list's order moves, in a commit "Reorder My tasks".
  It goes with a position (alone it is a tool error), works with `ids` as a position does, and with fields the fields are written first, in their own commit.
  The task, and the one to put it by, must be open and in My tasks; the result says where it is there: "In the user's own order of My tasks it comes first, before “A”." (`minePositionText`).
  A connection that wasn't given the user's private tasks is refused: the order is kept with them.
- The task to put it by is one the user sees (`findTask`: a task they don't see is "No task with id …", word for word as one that isn't there), and open; a closed one and the task itself are tool errors, with nothing written.
  A task with no place yet (a file edited by hand, an import) is placed first where it is shown (`placeUnplaced`), as for a drop.
- The result says where the task is now in its list's order: "In the manual order of #plan it comes after “A” and before “B”." (`positionText`).

**A date is the deadline**: the field descriptions send a date the user gives the task ("tomorrow", "by Friday") to `due`, and `start` only to a stated start ("start Monday", "work on it tomorrow"); the model had put "for tomorrow" in start.

**Dates** (`due`, `start`, and `history`'s `since`) are `YYYY-MM-DD` or a word quick-add knows (`today`, `tomorrow`/`tmrw`, a weekday prefix of 3+ letters = the next one, `+3`/`+3d`/`+2w`; `parseDateWord`, mirroring `TasksCore.parseDateWord`), resolved with Zonewiki's own date, so "tomorrow" is right whatever the client's clock or context says; the result starts `Created (today is YYYY-MM-DD).`

**Tag hints** (`tagHints`), after the task:

- the tags of the `similarTasks` (10) tasks most like it (BM25 over every task, open and done, by its title and notes) that it lacks, most common first, at most 5 (`Tags on similar tasks …: #home (2), …`), for the model to offer the user, since it can't see the tasks;
- and its tags used by no other task or page (`New tags, used nowhere else yet: …`, pointing at list_tags), which are often near-duplicates (`meeting` beside `meetings`).

Neither in a task list without tags.

#### `update_task`

Every change to existing tasks, in one tool (there were four: `tag_tasks` and `comment_task` are now its parameters, so the model has fewer tools to tell apart and every session loads fewer descriptions; `delete_task` is gone, as a task can't be deleted).
`updateTask`, as `PUT /api/tasks/<id>`: only the given fields change; an empty `due`/`start`/`description` clears it.
It takes `create_task`'s fields, and:

| Argument | Does |
|----------|------|
| `add_tags`, `remove_tags` | Work on the current tags (case ignored). |
| `repeat_every`, `repeat_from` | Alone, keep the task's repeat type. |
| `list`, `share` | Move the task to another list (`create_task`, Lists). |
| `position`, `after`, `before` | Its place in the manual order (`create_task`, A task's place in the order). |
| `follow` | `true`, `false`: the user follows the shared task, or no longer (Following a task in `docs/spec/tasks.md`); only the user themselves, the tool names no one. |
| `status` | `done`, `wont_do`, `skip`, `open` (below). |
| `comment` | A comment (below). |
| `ids` | Several tasks' tags and place at once, instead of `id` (below). |

**`status`**:

- `done`: a keep task goes back to the pool; a repeating one spawns its next occurrence, shown in the result.
- `wont_do`.
- `skip`: won't do this occurrence, the series goes on.
- `open`: reopen; the spawned occurrence goes again unless it was changed since.

There is no `deleted`: a task made by mistake is closed with `wont_do`, as in the app, and the error for any other status says so.

**`comment`**: `addComment`, a dated status line, its own commit "Comment on task" after the fields' (checked before anything is written, so an empty one changes nothing).

**A place alone** (`position`, `after` or `before` and no field) is a move in the manual order, as a drop in a list's view: `reorderTasks`, a commit "Reorder tasks", the result starting "Moved."; with fields it goes into their commit ("Update task"), and a reopened task (`status: "open"`) takes it at once.

**`ids`** instead of `id`: `tagTasks`, the function the `batch-tag` route uses, adds and removes tags on several tasks (open, closed, archived) in one commit, "Tag tasks".
With a position the tasks are moved too, **in the order given**: the first at the position, each next one right after the one before (`reorderTasks`, as a drop of several selected tasks), in a commit "Reorder tasks" before the tags'.
So one call puts a whole list in order (`ids` in that order, `position: "top"`); every task must be open, and an id given twice counts once.

- Only `add_tags`, `remove_tags`, `position`, `after`, `before` and `summary` go with it, anything else is refused.
- Case is ignored; a tag a task has keeps its spelling; a leading `#` is dropped.
- An unknown or ambiguous id fails the whole call, before anything is written; at most 200 per call.
- The result says on how many tasks the tags changed (and that the others had them so already) and lists each task's tags; a move lists the tasks in their new order.

Runs under the tasks lock, so add/remove tags see the current tags.
Annotated destructive, as it changes and deletes.

#### `watch_page`

What **Watch** in a page's menu does, for the user (Watching pages in `docs/spec/navigation.md`): `slug` names a page they see, or a folder with a slash after it (`projects/`: every page in it, those made later too); `watch: false` stops.
The answer says what is watched now and where its news shows (`/changes?watched=1`, `history` with `watched`).
It writes nothing of the wiki, no page and no commit, only what the user watches (`.zone/watch/`), but it is a tool that writes (`toolWritesOwn`): a viewer's assistant and a connection that only reads don't change what their user watches either, and a viewer watches in the browser.
What the assistant then writes on a watched page is news to its user, and its reading a page is no look of theirs.
A page the user doesn't see, or one that isn't there, is refused alike ("No page …"); without users there is nothing to watch.

### Files

Uploads (`_assets/`, File paste and drag & drop in `docs/spec/editor.md`) are read and added by assistants too (`go/mcpfiles.go`), in two ways, since a file that passes through a tool call is in the model's context whole: the tools for small files, and the routes over HTTP with the token for any size.

#### `read_file`

`url` is a link's target as a page or task has it (`/_assets/photo.png`, percent-encoded or not, with or without the wiki's address before it; `uploadFile`).

- A PNG, JPEG, GIF or WebP up to 4 MB comes as an image the model sees (a second content part, `{"type": "image", "data": <base64>, "mimeType": …}`, after a line naming the file, its type and size).
- A text file (anything that is valid UTF-8 without NUL bytes: `.txt`, `.csv`, `.md`, `.json`, SVG …) comes as its text, cut off after 200 KB.
- Anything else (a PDF, an archive) or a bigger image is only named, with how to fetch it over HTTP.

Only uploads are read: a shared one, or with users one of the user's own private ones, one of a group they are in, or a task's file of a task they see (another user's is "No such file", as one that isn't there); no path leaves `_assets/`, and dot files aren't files.

#### `upload_file`

`upload_file(filename, text | content_base64, page)`: a text file from `text`, any other from `content_base64` (line breaks and missing padding are fine), one of the two; at most 1 MB (`toolUploadMax`, or `upload-max` if that is less), and a bigger one is refused with how to send it over HTTP.
The name is the last part of `filename` (no dot files); **a taken name gets a number** (`deck-2.pdf`, `freeUploadName`), so nothing is ever replaced: there is nobody to ask.

Where it goes, by `page`:

- with users, a file for a page in the user's private folder goes to `private/<user>/_assets/` and only they can open it;
- one for a page of a group they are in to `groups/<group>/_assets/`, for the group;
- a task's id as `page` makes it the task's file, in `_tasks/files/<id>/_assets/`, seen by who sees the task (A task's files in `docs/spec/tasks.md`; "No task with this id" for one the user doesn't see);
- one for a shared page, or for none, is shared;
- one for a page in a place that isn't the user's (another user's private folder, a group's they aren't in, one the connection wasn't given) is refused with which folders are theirs (`uploadPlace`), rather than left with everyone's: judged by the place alone, so it says nothing of whether the page is there.

The task's id may be the 8-character short id the tools show (or more of it), a `[[task:…]]` reference or a `/tasks?task=…` link, when no page goes by that name (`uploadTaskRef`): it is found among the tasks the user sees, and an id that names none, or several, is refused rather than taken for a page's name, which would leave the task's file with everyone's.

The answer is the markdown that links it (`![name](url)` for an image, `[name](url)` otherwise, the URL percent-encoded), since an upload shows nowhere until a page or task links it.
It is saved as the browser's upload is (`storeUpload`: `storage-max`, a commit "Upload <name> (MCP)" by the user, with the trailer every assistant's write carries: Which assistant), and a viewer doesn't have the tool.

#### Over HTTP with the token

For an assistant with a shell and a file of any size: `POST /api/upload` (multipart, the field `file`, optionally `page`; the answer is `{"url": …}`) and `GET /_assets/<file>`, `/private/<user>/_assets/<file>`, `/groups/<group>/_assets/<file>` or `/_tasks/files/<id>/_assets/<file>` take the MCP token (or a connection's access token) as `Authorization: Bearer …` in place of a session (`tokenForFiles`, `tokenRequest` in the request checks).
The token is checked as `/mcp` checks it: a wrong one is 401 and counts against the client (`mcpThrottle`); the request then runs as the token's user, so a viewer's upload is refused (403) and a private upload is its owner's.
Its commit is an assistant's, as `upload_file`'s is (`saveUpload`: "Upload <name> (MCP)" and the `Assistant` trailer).
An upload sent this way takes a free name like the tool's, whatever `overwrite` says (`byToken`).
The token opens these routes only: a page, the task API or any other route with a bearer token and no session goes to the login as ever.
Both tools' refusals name the two `curl` commands (`fileRoutesHint`).

## What every write does

### Commit message and summary

Every write tool takes an optional `summary`; the commit message's first line is the action, then the summary, then `(MCP)` (`commitMsg`: `Update tickets/bug-1: add findings (MCP)`, `Create task (MCP)`), for whoever reads the history with git.
Zonewiki doesn't go by that line: a page's name can end in `(MCP)` too.
What tells the history the model's changes from the user's is the trailer after it (Which assistant).

### Which assistant

**Every write an assistant makes carries a trailer** after its first line that names its connection, `Assistant: Claude` (`mcpMark`, `commitTrailers`, `viaTrailerKey`): for a connection (OAuth, Connections above) the name its client gave when the user let it in, one line (`An MCP client` for one that gave none); for an MCP token, which names no client, `Assistant: MCP token` (`tokenVia`; a client that calls itself so is `An MCP client`: `clientName`).
The task tools' writes carry it as the page tools' do, and so does an upload with the token over HTTP.
`callMCPTool` hands the connection's name to the tool with its arguments (`viaArg`, a name no client can send: what one sends there is dropped), and a summary is one line, so neither names an assistant.

**The trailer alone says that an assistant wrote a commit** (`commitMarkOf` in `go/changes.go`, the one place that decides it, on every commit the history reads: `GitEntry`, `LogEntry`, `commitOf`), for Changes and its filters, an assistant's run and Undo all, a page's History and the tool `history`.
A line after the first is one no name in a commit message reaches (A name holds no line break in `docs/spec/security.md`), where a first line ending in `(MCP)` was one a person's page could have ("notes (MCP)"), so a person's save read as an assistant's (until 2026-11-20).
A commit made by hand with the trailer says what its author wrote, as its author does; one from before 2026-11-20 written with an MCP token has no trailer, and shows as a person's.
The history shows it beside who the commit is by, as the client's claim it is: "alice through Claude" on Changes and a page's History, `(by alice through Claude)` in the tool `history`, "by alice through Claude" under a search result whose page it changed last (Who changed a page last in `search.md`) (`commitMark.Via`); for a token or a client without a name **assistant** on Changes, "alice through an assistant" in a page's History and the tool, "by alice's assistant" under a search result.

### Where the text is from

The tools that write text (`write_page`, `edit_page`, `append_to_page`, `create_task`, `update_task`) take an optional `source`: where the words are from when not from the user, as the assistant says it (a URL, "a mail from Bob").
It is kept as a second trailer, `Source: …` (`commitTrailers`; one line, at most 200 characters, so it adds no trailer of its own), shown only for a commit the `Assistant` trailer says an assistant wrote, where the commit is: "from https://…" on its row of Changes and in a page's History, `(by alice through Claude; from https://…)` in the tool `history`.
It is said, not linked: it is the assistant's word, and what it tells a reader is that nobody here wrote this text, so it may be wrong, or written to steer an assistant (Pages can carry instructions in the README).
It is the write's, in the history; a page's own `sources:` in its frontmatter is the page's, for its readers.
Not yet: `read_page` saying which passages came from outside.

### Pages open in the editor

The browser editor auto-saves to the file without committing (a commit comes with Done/Ctrl+S).
A write tool that ran on such a page would take the user's draft into its own `(MCP)` commit, and an overwrite or delete would lose it, as it was never in git.
So every page write first commits what the page holds uncommitted (`keepPending` in `go/pageops.go`: `isUncommitted`, then `Save <slug>`, the message the Uncommitted list's commit gives; by the user who auto-saved it, with users, else Zonewiki), and the result says so: the user may have the page open.
The browser's writes that replace or remove a page's text (delete, restore, rename, Keep mine, Reload theirs) do the same, through the same function.
The editor's side is its own: each auto-save sends the hash of the text it last saw,
so its next save after the model's write is merged with it (the user's textarea gets the model's change),
or,
where both changed the same lines,
refused with the banner (Reload theirs, Keep mine, Merge by hand) instead of overwriting (Merge on save in `docs/spec/editor.md`).

### Wikilink check

Page writes end with a note naming the `[[wikilinks]]` the write *added* that lead to no page (`missingLinks` before and after, by the renderer's rule: the name as a slug, else slugified; code, `[[tasks:]]` and `[[task:…]]` aside), so the model fixes a typo or creates the page.
Links that were already broken are not repeated.

## Guides

The user's conventions for the wiki, which every assistant is given: the wiki's guide in the instructions, a folder's guides before a write there.

### The wiki's guide

The page `wiki-guide` (`guideSlug`) holds the user's conventions for keeping the wiki: where things go, tags, page layouts, what stays private.
Its text is appended to the `instructions` (`instructionsWithGuide`, which `handleMCP` asks for only on `initialize`), after a line saying it wins over the general advice and that the model should offer to add conventions the user settles.
So the model knows it before its first write, in every MCP client (a client-specific skill or rules file would reach only that client), and it is versioned and editable like any page.
Read anew at each `initialize`: an edit shows from the next session.
Over `guideChars` (8000) it is cut, with a pointer to read_page, since the instructions stay in the model's context all session.
Without the page, the instructions instead nudge the model to offer starting one when the user describes conventions.
It never counts as an orphan (list_pages).

### Folder guides

The user's instructions for writing the pages in a folder and its subfolders live in a `CLAUDE.md` or `AGENTS.md` in it (any case; `checkGuides`), the files coding assistants read, so one guide serves an assistant working on the files and one working through Zonewiki.
`wiki-guide` and a `CLAUDE.md`/`AGENTS.md` at the root are the whole wiki's.
The guides for a page (`folderGuides`) are the root's, then each folder's on the way down to the page's.
A line that is only `@other.md` is replaced by that page's text, relative to the guide's folder (`guideText`, Claude Code's import), at most 3 deep.
An import that isn't a page of the wiki stays as written.
A file already part of another guide is left out, so a root `CLAUDE.md` holding just `@wiki-guide.md` adds nothing.
Empty guides don't count.

### The guide gate

Every page write (write_page, edit_page, append_to_page, file_note (the target), rename_page (source and target, a folder too), delete_page, restore_page, undo_changes, replace_in_pages with `dry_run=false`, rename_task_tag and rename_task_list (the pages they change)) takes `guides`,
a list of guide ids (a comma-separated string works too).
A write to a page with a guide whose id isn't in the list is refused with nothing written.
The refusal is a tool error starting "Nothing was written.", with the ids to pass for this write and the text of each missing guide, as `=== guide <slug> (id <id>) ===` (cut at `folderGuideChars`, 8000, with a pointer to read_page).
An id is `<guide slug>:<8 hex>`, a SHA-256 of the guide's text with its imports.
When the user changes a guide, its old id stops counting and the next write shows the new text.
A write to a guide itself ends with its new id (`guideNote`).
`read_page` of a whole guide adds `guide id: <id>` to its header, since reading it is reading the guide (not for a guide that imports other pages, whose text the page doesn't show: it says to use read_guides).
Without guides nothing changes.
Task writes are not gated.

The gate is the backstop; read_guides is the way in: the model reads the guide before drafting, so the draft follows it, rather than drafting, being refused and writing it again.
Why a gate and not the instructions: Claude Code cuts a server's `instructions` at about 2 KB, so a guide appended there may never reach the model.
Attaching a guide to every result costs its tokens on every call, since the server keeps no sessions.
A refusal costs one call and sends each guide once per conversation, before the write.

## Who sees a page

With users (Group pages in `docs/spec/users.md`): for a user who is in a group the instructions say, before the guide, that where a page is kept is who sees it,
and name their folders (`groupsNote`: "private/alice/ only this user, groups/dev/ the people in that group, anywhere else everyone who uses the wiki … a page moves to more people; back to fewer only by an admin, or by whoever shared it while nobody else has changed it").
Every answer about a page that isn't everyone's says it with the page's facts
(`pageInfo`, `pageSeenBy`: after `url:` and `file:` a line `seen by: the group @dev only (anna, bo and carol; its folder is groups/dev/). Not for a page or task outside the group`, or `seen by: only alice (a private page, in private/alice/)`),
from `read_page` and from every write, so an assistant knows it before it quotes the page elsewhere.
A page is made for a group by its slug (`write_page` with `groups/dev/…`), and a write into a group the user isn't in is refused with which folders are theirs (`notYours`), which says nothing of what is there.
The tools' descriptions say only that a move to fewer readers is an admin's, or that of whoever shared the page while nobody else changed it, with `narrow` (`rename_page`): they are loaded by every session and kept under the budget of Size of the tool list (below).

## Links and file paths

**Links** in tool results start with `base-url` from the settings file (e.g. `http://localhost:5080`, Zonewiki's address as your browser knows it) when set, else with the address the request came in on (`siteURL`, as the page and OAuth's addresses: https when Zonewiki's own connection is, or a proxy's `X-Forwarded-Proto: https` says so).
A client in a container reaches Zonewiki as `host.containers.internal`, and links with that host don't open in the browser on the host.

**File paths** (`file:`, `hostFile`: search_wiki's sections, read_page) are the host's wiki folder plus `<slug>.md` (a slug is the file's path relative to the wiki, unchanged): `ZONE_HOST_DATA`, which `run.sh` sets to `ZONE_DATA`'s host path, since inside the container the wiki is only `/wiki-data`;
unset (no container), `ZONE_DATA` resolved.
So a client on the host can open or edit the file directly.
Tasks have none.

## How it works

### Protocol

Protocol in `go/mcp.go` (JSON-RPC; the tool definitions as clients see them in `go/mcp_tools.json`),
route `mcpEndpoint`,
tools in `go/mcp*.go` (those that read in `mcptools.go`, that write pages in `mcpwrite.go` and `mcpreplace.go`, the task tools in `mcptasks.go`, guides in `mcpguides.go`),
each with what it writes in one table (`mcpToolTable`: nothing, tasks, or pages, which puts it under `pagesMu`; a tool that writes is refused to a viewer and to a connection that only reads; a test holds the table against each definition's `readOnlyHint`, so a new writing tool can't be left out).
MCP's Streamable HTTP transport, reduced to what tools need: each POST carries one message (or a batch) and is answered with plain JSON; notifications get 202; no event stream (GET → 405), no sessions.
A batch holds at most 20 messages (`mcpBatchMost`; more is a 400, `-32600`): its calls run one after another in one request, a grep for up to its timeout and a write holding the page lock, so one POST can't queue thousands of them.

### The instructions

`initialize` echoes the client's protocol version when it is in `protocolVersions`,
else the newest,
and sends `instructions` (`mcpInstructions`): what What the assistant is told says, and Offer to capture.
The instructions stay short (about 1.3 KB, a test keeps them under 1.9 KB): Claude Code cuts a server's instructions at about 2 KB, so what every session needs comes first and the tools' descriptions carry the details;
the wiki's guide is appended after them (The wiki's guide), cut there too, which is why the instructions point to `read_guides`.
For a user in a group, a note on who sees a page goes before the guide (Who sees a page).

### Size of the tool list

Every session loads all tool definitions (about 30 KB),
so text is said once: the parameters many tools share (`summary`, `guides`) have one-line descriptions,
`read_guides` explains the guide gate,
and `update_task`'s `due`/`start` refer to `create_task`'s,
which carry the date rules.
Tests keep the total under 32,700 characters as a client gets it (`test_they_stay_small`) and the file under 38,000 bytes (`TestMCPToolDefinitions`): a new parameter raises them by what it costs, not more.

### Retrieval

**Retrieval** (`retrieve`) works on sections, not pages: an LLM needs the passages, and the best ones may share a page.
BM25 runs over sections (`pageChunks`, the same `chunkSections` split, cached per page by mtime; `chunkIndex`), fused with `searchChunks` when semantic search is on (sections match by `chunkKey`).
Without semantic search it is keyword-only and still works.
Tasks share that BM25 index (`taskSearchDocs()`, each doc carrying its task's record; the whole task is read for an answer: Indexing in `docs/spec/search.md`), so they rank with sections, as in the browser search, and their semantic hits (`searchTasks`) join the section hits by similarity.
`search_tasks` with a query fuses BM25 and semantic hits within its filter.

### Auth

`/mcp` is exempt from the session login (`checks`, `signedIn`),
since an MCP client can't log in,
and checks its own: it needs the wiki's MCP token (`mcp-token` in the settings file, kept as its hash: `tokenMatches`),
sent as `Authorization: Bearer <token>` (compared in constant time, `mcpAuthorized`).
With users (`docs/spec/users.md`) each user has their own token instead (`mcp-token` in their section),
and the tools act as the token's user: their writes are committed in that user's name,
`history` names authors,
and another user's private pages don't exist for them (MCP in `docs/spec/users.md`).
Without a token set and without a login, `/mcp` is off: every request gets 403 with a hint to set one.
With a login (users or a password) OAuth is on, and a request without a token is a 401 that says where to sign in (`/mcp` says where to sign in, below);
there the 403 is only for a wiki with `mcp-oauth = no` and no token.
The tools write, so an open `/mcp` would let anything that reaches the port change the wiki; a token is never optional.
A wrong or missing bearer gets 401 (`WWW-Authenticate: Bearer`); wrong tokens are throttled per client like wrong passwords (`mcpThrottle`, its own count: after 5, 429 with `Retry-After`, waits doubling from 1 minute to an hour; see `docs/spec/security.md`).
The Host and cross-site Origin checks apply as everywhere.

### How OAuth works

What Connecting with OAuth (above) is built on.

#### `/mcp` says where to sign in

With OAuth on, a call without a token, or with a wrong one, is a `401` with `WWW-Authenticate: Bearer resource_metadata="<address>/.well-known/oauth-protected-resource/mcp", scope="mcp"` (a wrong one also `error="invalid_token"`), whether or not a static token is set.
A call with no `Authorization` header at all is no guess and isn't counted against the client (a client finding its way); a wrong token is, as ever.

#### The metadata

No login needed: `GET /.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-protected-resource` (RFC 9728: `resource`, the address of `/mcp`; `authorization_servers`, Zonewiki's own address) and `GET /.well-known/oauth-authorization-server` (RFC 8414: the endpoints below, `code_challenge_methods_supported: ["S256"]`, `token_endpoint_auth_methods_supported: ["none"]`).
The addresses are on `base-url`, else on the address the request came in on (`siteURL`): behind a proxy set `base-url`, or `resource` won't be the address the user typed and the client refuses it.

#### Registration

`POST /oauth/register` (RFC 7591; no login, any origin): a client sends its `client_name` and `redirect_uris` and gets a `client_id`.
Public clients only: there is no secret.
Zonewiki keeps nothing: the client id *is* the name and the redirect URIs, signed with Zonewiki's key (`.zone/secret_key`; `oauthClient.id`, `clientOf`), so strangers calling the endpoint leave nothing behind, and a client id Zonewiki didn't sign is no client.
A redirect URI is `https`, or `http` on `localhost`, `127.0.0.1` or `[::1]` (a program on the user's computer, RFC 8252), without a fragment or a user, its host in ASCII (an international name as its punycode, `xn--…`, also when sent percent-encoded); one to ten of them (**400** `invalid_redirect_uri`).
So the question and Connect an assistant show every host as it is spelled: a name in letters that look like Latin ones (`clаude.ai` with a Cyrillic а) can't pass there for the name it copies.
Other schemes (`cursor://` …) are refused for now.
Client ID Metadata Documents aren't offered yet (the metadata doesn't advertise them, so clients register): they would have Zonewiki fetch an address a stranger names.

#### The question's checks

What The question (above) checks before and after the user answers:

- Before anything is shown, the client id and the redirect URI are checked: one Zonewiki doesn't know is a page saying so (**400**), never a redirect.
  The answer goes only where the registration said: that address exactly, or for a program on this computer the same address at whatever port.
- What else the client got wrong (no `response_type=code`, no PKCE challenge with `S256`, a `resource` that isn't this wiki's `/mcp`) is a page too (400), with a link back that tells the client (`error=…`).
- The POST of Allow is behind the Origin check as every form.
- **The answer is a page, not a redirect**: "Connected", with a `Refresh` header that sends the browser on to the client (`<redirect_uri>?code=…&state=…&iss=…`) and the same address as a link.
  The page policy lets a form lead only to Zonewiki itself (`form-action 'self'`), and browsers hold a form's redirects to that, so a redirect would be blocked; this way the policy stays as it is.
  Deny goes back the same way with `error=access_denied`.
- The code is 32 random bytes, kept in memory as its hash for a minute, for one use (`authCodes`; a restart drops the few that are open).
  Used, it is remembered for ten minutes (`spentFor`) with the connection made with it: one used again was copied (the client, or whoever read it on the way, has the tokens), so that connection is ended and the log says so (RFC 6749, 4.1.2), also when the second use came while the first was still being answered.

#### Tokens

`POST /oauth/token` (form-encoded; no login, any origin): `grant_type=authorization_code` with the `code`, its `code_verifier` (PKCE, S256), the same `client_id` and `redirect_uri`; or `grant_type=refresh_token` with the `refresh_token` and `client_id`.
The answer: an `access_token` (an hour), a `refresh_token`, `token_type: Bearer`, `scope: mcp`.

- Both are 32 random bytes and Zonewiki keeps only their hashes, as of a static token; not JWTs, so ending a connection takes effect at once.
- **A refresh token works once**: each use gives a new pair, and the access token before stops working.
  Unused for 90 days it expires, and the connection with it.
  One used a second time was copied (the client, or a thief, has a newer one): the connection is ended and the log says so.
  A client that lost an answer to a refresh therefore has to connect again.
- A refresh token is its client's: another client id gets nothing.
- What gets none is a **400** with `error`: `invalid_grant` (a code wrong, used, older than a minute, for another client or redirect URI, the wrong verifier; a refresh token wrong, used, expired or revoked; a user who can't sign in any more), `invalid_client`, `invalid_target` (another `resource`), `unsupported_grant_type`.
  Failures count against the client as wrong MCP tokens do (`mcpThrottle`: **429** with `Retry-After`).

**One scope**, `mcp`: what an assistant may do is its user's role, as with a static token.

#### A client's disconnect

`POST /oauth/revoke` (RFC 7009; form-encoded; no login, any origin): the `token`, an access or a refresh token, and optionally `client_id` and `token_type_hint`.
The metadata names it (`revocation_endpoint`).

- Either token ends the whole connection, as End does (`endGrantOf`): both tokens are refused from then on, and it is gone from Connect an assistant.
  So does an access token past its hour, and the refresh token before the current one (a client that lost the answer to a refresh has no newer).
- The answer is an empty **200** whether or not the token was one, as the RFC has it, so the route tells a guesser nothing and counts nothing against the client; only a call without `token` is a **400** (`invalid_request`).
- A `client_id` sent along must be the token's client: another client's, or one Zonewiki didn't sign, ends nothing (200 all the same).
  Without one the token is enough: whoever holds it could do more with it than end it.
- `token_type_hint` is not needed: the token is looked for as both.
- A token that is none writes nothing (`.zone/grants` stays as it is).

#### Where connections are kept

**Connections are kept** in `.zone/grants` (`grants.go`): the id, the user ("" in a wiki without users: the wiki's), the client's name, the hash of its client id, the redirect host, what it was given less than its user (whether it only reads, not their private place, only some of their groups), when it was made, the day it was last used, the token hashes and when they expire.
Owner-only (0600), written whole, one change at a time (`changeGrants`: a lock in the process and `lockFile` on `.zone/grants.lock`, as a command writes it too), not committed (state, like `.zone/links`), read again when the file changed (`readGrants`).
Not in the settings file: tokens change by the hour, and a change there is a change of the settings.

#### OAuth's tests

`tests/test_oauth.py` plays the client over HTTP (`TestRevoke`: a client's disconnect; `TestChange`: Change… through the tokens a connection has); `tests/e2e/test_oauth.py` holds that Allow and Deny get the browser back to the client under the page policy, and that Change… opens in its dialog and asks for the password only to give more.
