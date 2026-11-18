# Zone

**What you, your small team and your AI assistants know, in one place you own: a wiki with notes and tasks.**

A self-hosted wiki with built-in tasks and quick notes, made to be read and
written by you and by your AI assistants alike. Your pages are plain Markdown
files in a folder you choose, so any assistant of any maker can work with
them, over [MCP](#connect-an-ai-assistant) as you: it sees and changes what
you may, no more, and every edit it makes is a git commit you can review and
undo. Zone renders the pages in the browser, lets you edit them there or in
any editor, searches them, and keeps every version in git. No database, no
frontend build, no cloud: run it on your machine, or self-host it behind
HTTPS. Alone it needs no accounts; for a small team, add users, with private
pages and tasks for each.

![A wiki page in Zone: the page in the middle, its table of contents on the left, backlinks and linked tasks on the right](docs/screenshots/page.png)

## Why Zone

**Your pages stay yours, as plain files.** Pages are Markdown in a folder you
choose, tasks are JSON next to them, history is git. Zone renders, searches
and edits them, but nothing is locked inside it: open the folder in any
editor, grep it, back it up with `git push`, or stop using Zone and keep
everything as it is. Search indexes and caches are rebuilt from the files.

- **A memory for your AI assistants, under your rules.** Through its
  [MCP server](#connect-an-ai-assistant), an AI assistant can do everything
  you can: search, read, create, edit, rename and restore pages, file
  notes, and manage tasks. It acts as you and never sees more than you do;
  its edits land as git commits marked `(MCP)`, listed under Changes, that
  you can review, diff or undo. Zone brings no model of its own: the assistant is yours, whichever
  you use, and what it learns stays in your files, not in its maker's cloud.
- **Pages, notes and tasks in one place.** The wiki, Tasks and Notes share links,
  search and history. A page can show a task as a chip, and a task can link to
  a page, with no plugins to combine.
- **Nothing gets lost.** Every page has its version history: see what each
  save changed, open any version, restore it in one click, bring back a
  deleted page. Renaming a page updates every link to it.
- **Any browser, nothing to install on the client.** Open a URL on your
  laptop or your phone; the editor works on both. The server is one Go
  binary with no database, and the frontend has no npm or bundler. It is
  small enough to read and change.
- **Your server, no cloud.** Runs on your machine by default, with no account
  and nothing loaded from other hosts. To reach it from elsewhere, put it
  behind HTTPS with a password (see [Security](#security)).

Zone is for one person's pages and tasks on a server you run, or a small
team's (or a household's) who share one wiki, each with their own login,
private pages and tasks, and the tasks assigned to them. It is deliberately a
single process with no plugins, no graph view and no real-time collaboration,
so it isn't a plugin platform. That keeps it small and readable.

## How Zone is organised

Zone has three modules: the **Wiki** for your pages, **Tasks** for your
tasks, and **Notes** for quick capture (with several users, **Private** stands beside the Wiki: your private home, the start page of
your own private pages; and **Groups**, or the name of the one group you are in, for a team's pages). You switch between them in the top
left corner (on a phone, in the tab bar at the bottom), and **New** creates
whatever the module you're in holds: a page, a note, a task. Search,
history, keyboard shortcuts and AI access work across all three.

### Wiki: your pages

Pages are Markdown files in folders, linked to each other. Zone renders them,
shows what links where, and lets you edit them in the browser or in any text
editor; a change on disk shows up immediately.

- `[[Wikilinks]]`, also to a section (`[[Page#Section]]`). Links to a page
  that doesn't exist yet show up red; following one creates the page.
- Backlinks, "Linked tasks" (tasks that mention the page) and, with semantic
  search, "Related pages". On wide screens the table of contents sits on the
  left and the links on the right, both following as you scroll.
- Math (`$e^{i\pi}$`, `$$…$$`) and Mermaid diagrams, rendered in the
  browser from Zone's own copies of KaTeX and Mermaid, no outside service.
- Callouts (`> [!WARNING]`), sortable tables, image sizing,
  frontmatter tags, a one-line summary and sources. Fold a long page's
  sections while reading (the chevron by a heading, or `z` for all).
- An editor with live preview and auto-save, a formatting bar, completion
  for links, pages and emoji (`:tada` → 🎉), snippets (`/date`), list continuation, and editing a
  single section from its heading. Paste or drop files and images to upload
  them. Two people (or you and your assistant) on one page: changes in
  different lines merge as you type; where you both changed the same lines,
  you choose (theirs, yours, or merge by hand), and nothing typed is lost.
- Renaming or moving a page updates every link to it, on other pages and in
  tasks.
- Every page's version history: what each save changed, any version, and
  one-click restore. Deleted pages can be brought back.
- Changes: what changed across the wiki, newest first, by whom and whether
  through an assistant; filter by person, by folder, by day, or to what
  assistants wrote.
- Present a page as slides, one per heading.
- Print a page, or save it as a PDF (Print… in the page's menu: a print
  layout that shows the page alone, light even on a dark screen).

![The editor in dark mode: Markdown on the left, the live preview on the right](docs/screenshots/editor.png)

### Notes: quick capture

For a thought, a call, a link: write it down first, decide where it belongs
later.

- `c n` opens a new note, named by its time; the Notes list shows
  them newest first, with a filter.
- **Move to wiki** turns a note into a page (pick a folder and a name, keep
  the date if you like); **Append to page** adds it to an existing page as a
  section. Either way, links to the note follow it.

### Tasks

A task list built around two different dates: the day you start on a task,
and its deadline. It has two views:

- **Open**: every task, as one **List** in an order you set by dragging,
  or as a **Plan**: a board of the coming days (today, tomorrow, the week,
  later) where you drag a task onto the day you'll do it. A team's list
  also shows **By person**: who has what, and a drag hands a task over.
- **Today**: what's on today, started or due.

Done tasks go to a monthly archive. Beyond that:

- Quick-add shorthand: `Call Bob tomorrow #work due:fri`.
- Importance (high, normal, low): a mark before the title, set with a key, the
  menu or `!` in the title.
- Repeating tasks (by due date or from when you finish them; skip one
  occurrence without ending the series), "keep" tasks that go back on the
  list when done (shopping staples), and "won't do".
- A description in full Markdown, written with the page editor's formatting bar and
  shown as a page shows it (checklists, tables, code, links), dated comments in
  Markdown too (a status line or several), a history of every date change, and undo for completing
  and deleting.
- Lists: a task is in one list. A new task lands in your Inbox; file it by moving it to a list.
  Make a list with New list… on the Tasks page, give it
  its own settings (a limit on how much is on Today, keep tasks for a shopping list's staples),
  and show it on any page (`[[tasks: list=groceries]]`). Your pinned lists are a row of
  pills on the Tasks page; the others, and all tasks, are under More.
- My tasks is where the Tasks module opens: what is yours. Alone, that is every task (leave a
  catalogue or a someday list out of it in the list's settings). With users, it is your own
  lists and the shared tasks assigned to you; a shared list's unassigned tasks are its backlog,
  to take with one key. A task someone gives you is marked new on its card, and mailed to
  you where Zone has a mail server and your address.
- Task lists inside any page (`[[tasks: tag=work view=open]]`), a single task inline
  as a live chip you can tick (`[[task:…]]`), and **Make task** in the editor
  to turn lines of meeting notes into tasks.
- The Tasks link shows how many tasks are due today or overdue.
- One line per task, opened where it is to read its description and comments; fully keyboard-driven; on phones, swipe to plan,
  long-press to drag, or long-press and let go to select several tasks.

![Tasks' Open tab by days: tasks grouped by the day they're planned for](docs/screenshots/tasks.png)

### Across Zone

- **Search** (Ctrl+P) across pages and tasks: fuzzy matching on names, full
  text with stemming, typo tolerance and accent folding, and the opened page
  scrolled to the matching section with the words highlighted. Query syntax:
  `"exact phrase"`, `-word`, `#tag`, `edited:2026-09`, `after:` / `before:`.
- **Semantic search** (optional) with a local embedding model, mixed into the
  keyword ranking. Nothing leaves your machine.
- **Git underneath**: every finished edit (Done or Ctrl+S; auto-save doesn't
  commit), task change and rename is a commit, so history, restore and
  `git push` backups come for free.
- **AI assistants** through an [MCP](https://modelcontextprotocol.io) server at
  `/mcp`: any MCP client can search and read
  your pages, notes and tasks, find every line matching a regex, check links
  (backlinks, orphan pages, broken wikilinks), and write: create and edit pages, find and
  replace across pages, rename and delete them (move whole folders), add and update
  tasks, rename task lists and tags, look at the images and files a page links to and
  upload new ones. Every write is a commit marked `(MCP)`, with links back into Zone. It's
  off until you set a token (see [Connect an AI assistant](#connect-an-ai-assistant)).
- **Several users** (optional, `./run.sh user add NAME`): each signs in with their own
  name and password, their changes are committed in their name, and each has
  a private folder (`private/<name>/`), private notes, private tasks
  (their Inbox and the tasks in a private list of theirs, 🔒 on their cards) and their own pinned lists.
  A team has a folder of its own too: the pages in `groups/<group>/` are seen and changed by
  the people in that group, and by no one else (anyone who may write makes a group on Groups and says who is in it; a task list can be for a group as well).
  A shared task can be assigned to a user (`@anna` in its title or its dialog);
  `@me` in the filter, or a page with `[[tasks: assignee=me]]`, shows everyone their own.
  Everything else is shared. Each has a role: **admin** (the first user;
  manages the others under **Admin**, on its Users tab: invite links, roles, disable,
  remove, password reset links; writes the guides AI assistants follow),
  **editor** (the default) or **viewer** (reads what is shared, changes nothing).
  Optionally people sign up themselves with their mail address
  (`ZONE_SIGNUP=1` and a mail server in `.env`; as viewers by default, and an admin
  approves them if you want).
  Everyone changes their own password in the browser (**Change password** in the user menu).
  A wiki can also be **public**: `ZONE_PUBLIC=view` in `.env` shows its shared pages to
  anyone without signing in, `ZONE_PUBLIC=edit` lets them edit pages too; tasks, notes and
  private pages stay with the users ([`docs/spec/security.md`](docs/spec/security.md), Public wikis).
  See [`docs/spec/users.md`](docs/spec/users.md).
- **Keyboard shortcuts** for everything; `?` lists them.
- **Sharing mode** for screen sharing: hides the trail of visited pages,
  backlinks and counts, and pages you've marked as not for sharing.
- **Phones**: a responsive layout with a tab bar, installable on the home
  screen. Light and dark mode follow your system.

<p align="center"><img src="docs/screenshots/phone.png" width="640" alt="Zone on a phone: a wiki page, and the Today view of Tasks"></p>

## Quick start

You need either **Docker** with Compose, or **Podman** with `podman-compose`.
`run.sh` uses whichever is installed. Linux is the only tested platform.
`run.sh` should also work on macOS (its stock bash 3.2 is enough) and on
Windows in WSL2, which Docker Desktop and Podman use there anyway.

```bash
git clone <this repository> zonewiki && cd zonewiki
mkdir -p ~/zonewiki                   # your wiki folder (Zone won't create it)
cp .env.example .env                  # then edit it: ZONE_DATA is your wiki folder
./run.sh up                           # build and start
```

Open <http://localhost:5000>. `./run.sh down` stops Zone, `./run.sh logs`
follows its log. To update: `git pull && ./run.sh up`.

To point Zone at an existing folder of Markdown notes, set `ZONE_DATA` to
it; Zone makes it a git repository (if it isn't one) and adds a home page.

**On a NAS or server** (Synology, QNAP, a Raspberry Pi, …), you don't need
the repository: `compose.nas.yaml` runs the prebuilt image
`ghcr.io/gklingler/zonewiki` (amd64 and arm64). Put it next to a `.env` and run
`docker compose -f compose.nas.yaml up -d`; Zone is then on port 5080, and
the first page it shows asks for a password. What
`.env` needs, and a step-by-step for Synology's Container Manager: "On a NAS
or server" in [`docs/spec/deployment.md`](docs/spec/deployment.md).

Without a container, you need Go 1.27 or newer, a C compiler (for semantic
search; `CGO_ENABLED=0` builds without it) and git:

```bash
(cd go && go build -o zonewiki .)
ZONE_DATA=~/zonewiki go/zonewiki
```

In Windows PowerShell: `cd go; go build -o zonewiki.exe .; cd ..`, then
`$env:ZONE_DATA="$HOME\zonewiki"; go\zonewiki.exe` starts Zone. For semantic search
outside the container, onnxruntime's library must be where the loader finds
it (`docs/spec/search.md`). `zonewiki --help` lists the flags, the commands
(`user`, `config`, `eval`, `health`; each takes `--help` too) and the environment.

## Configuration

Two places. `.env` (`.env.example` lists it) is how Zone runs, all optional
except `ZONE_DATA`:

| Setting | Does |
|---------|------|
| `ZONE_DATA` | The wiki folder (default `~/zonewiki`) |
| `ZONE_ALLOW_REMOTE=1` | Listen on the LAN (e.g. for your phone); by default only localhost |
| `ZONE_SEMANTIC_SEARCH=on` | Semantic search with a local model (adds about 0.7 GB to the image) |
| `TZ` | The timezone for "today", e.g. `Europe/Berlin` (default: the host's with `run.sh`, else UTC) |

### The settings file

The wiki's own settings and its users are in the wiki, in `.zone/config`
(passwords and MCP tokens only as hashes), kept out of git: the history
never holds account data. Back it up with the wiki folder; a clone of the
git remote doesn't have it. Zone reloads it
when it changes, so none of them needs a restart (but for taking the login
away, which Zone takes only at one). Set them with `run.sh`, or edit the file:

```bash
./run.sh config passwd                # a login: one password …
./run.sh user add alice               # … or users, each with their own (replaces the password)
./run.sh user add bob --mcp-token     # and an MCP token for his assistant
./run.sh user passwd alice            # also: user token, role, disable, invite, reset, remove, list
./run.sh config set locale de-AT      # any setting below; config unset KEY for its default
./run.sh config check                 # what's in effect, and where it comes from
```

| Setting | Does |
|---------|------|
| `password` | A login gate (`./run.sh config passwd`). Sessions survive restarts: Zone keeps its own key in `.zone/` (never in git) |
| `[user NAME]` | Several users instead of one password, each with a `password`, maybe an `mcp-token`, a `role` (admin, editor, viewer) and `disabled` (`./run.sh user …`, or the admin page); changes are committed in their name (see [`docs/spec/users.md`](docs/spec/users.md)) |
| `[group NAME]` | What a group says beyond its name: `includes = dev, design` makes it of other groups (whoever is in one of them is in it: `./run.sh group includes NAME +dev`), `owners = anna` names who says who is in it (a group made in the browser, on Groups) |
| `mcp-token` | Turns on `/mcp` (`./run.sh config token`; see [Connect an AI assistant](#connect-an-ai-assistant)) |
| `hosts` | Names Zone may be reached by besides localhost, e.g. `wiki.example.org` behind a reverse proxy (see Security) |
| `base-url` | Zone's address as your browser knows it, for links handed to AI clients |
| `git-remote` | A remote for the wiki's git repository |
| `noshare` | Pages hidden in sharing mode, e.g. `#noshare, people/` |
| `locale` | How dates read and the day weeks start on, e.g. `de-AT` or `en-GB` (default: US English dates, weeks from Monday). The UI's words stay English |
| `date-format` | `iso`: dates as `YYYY-MM-DD` (ISO 8601); weekday and month names still follow `locale` |
| `query-log` | `yes`: keep the queries AI clients send to `search_wiki` in `.zone/queries.jsonl` (never in git), to turn into questions for `./run.sh eval` |
| `semantic-min-score` | How similar a passage must be to count as a semantic match (e.g. `0.4`; the default suits the default model) |
| `trusted-proxies` | Your reverse proxy's address (or network), when it doesn't connect from localhost (e.g. `172.17.0.1` for a proxy on the host of a container): Zone then tells its clients apart when it slows down wrong passwords |
| `external-content` | Other sites a page may show images, video and frames from, e.g. `upload.wikimedia.org, *.example.org` (`*`: any). By default none: an image elsewhere shows as a link (see Security) |
| `mcp-oauth` | `no`: assistants connect with a token only, not by signing in through the browser (on by default with a login) |
| `no-login` | `yes`: no login on purpose, though other devices can reach Zone; without it Zone then asks for a password before it shows anything |
| `upload-max` | The biggest file an upload may be, e.g. `25MB` (default: `100MB`) |
| `storage-max` | How much the wiki may hold, e.g. `5GB` (default: no limit). Counts your pages and files, not their git history; when full, new pages, notes and uploads are refused until you delete something, while edits to pages always save |

A typo in the file keeps the settings Zone has and shows up in its log.

Coming from Zone 1 (or Leaf): Zone 2 reads only today's names. What to
rename, and how: "From Zone 1 (or Leaf)" in
[`docs/spec/deployment.md`](docs/spec/deployment.md).

### Without run.sh

`run.sh` fills in what `compose.yaml` can't work out itself: the commit the
image is built from, your timezone, your user ID, and the network address
from `ZONE_ALLOW_REMOTE`. To run `docker compose up -d --build` directly, set in
`.env`:

| Setting | Does |
|---------|------|
| `ZONE_DATA` | As an absolute path (no `~`) |
| `ZONE_BIND=0.0.0.0` | Together with `ZONE_ALLOW_REMOTE=1`, for network access (Zone is told too: without a login it asks for a password first) |
| `ZONE_UID`, `ZONE_GID` | Your user and group ID (`id -u`, `id -g`), if they aren't 1000 |
| `TZ` | Your timezone; otherwise UTC |

With rootless Podman, add the file that keeps your user ID inside the
container: `podman compose -f compose.yaml -f compose.podman.yaml up -d --build`.

## Connect an AI assistant

Zone's MCP server lets an AI assistant (any MCP client) search, read and write
your wiki and tasks.

**By signing in** (a wiki with a login): give the assistant the address,
`https://your-wiki.example/mcp`. In Claude add it as a custom connector, in
ChatGPT as a connector in developer mode; for Claude Code run
`claude mcp add --transport http zone <address>` and then `/mcp`. It opens
your wiki in the browser, you sign in and click **Allow**, and it is
connected as you. **Connect an assistant** (`/assistant`, in the user menu)
lists the connections and ends each. Assistants that run on their maker's
servers (Claude's apps, ChatGPT) reach only a wiki on the internet over
https (`base-url` and `hosts` set); Claude Code on your computer reaches any
wiki your computer does. `mcp-oauth = no` turns this off.

**With a token** (any wiki, any client, scripts): create one, then register
Zone with your client.

In the browser, open **Connect an assistant** (`/assistant`; with users it is
in the user menu at the top right, and each user makes their own token). It
shows the new token once, with the line to paste for Claude Code. Or on the
host:

```bash
./run.sh config token          # prints the token, once (with users: ./run.sh user token NAME)
```

In your client, add an MCP server with the HTTP transport at
`http://localhost:5000/mcp` and the header `Authorization: Bearer <token>`.
Zone keeps only the token's hash: lost, make a new one.

The assistant can also answer questions about Zone itself ("how do I give
the task list's tabs other names?"): it searches this README and Zone's spec.

To give the assistant your wiki's conventions (where things go, what's
private, how pages are tagged), write them on a page called `wiki-guide`.
Zone adds it to what the assistant is told when it connects, so it's followed
in every session and with any MCP client. For example:

```markdown
# Wiki guide

- Tickets go in `tickets/<id>`, one page each, with a `## Log` section.
- People get a page in `people/`; those pages are private: never quote them
  elsewhere.
- Tag pages with existing tags (list_tags) rather than new ones.
- Prefer extending a page over creating a new one.
```

Without the page, the assistant offers to start one when you tell it how you
like things.

A folder can have its own guide: a `CLAUDE.md` or `AGENTS.md` in it, the files
coding assistants already read, which then also work when an assistant edits the
files directly. They apply to the pages in that folder and its subfolders. A line
`@other.md` in a guide pulls in that page's text. Zone makes sure the assistant
has read a guide before it writes: its first change under a guide is refused,
with the guide's text and an id. It then fixes its change and resends it with
the id. The assistant is also told which folders have guides, and to read
them with `read_guides` before drafting anything for the wiki; reading a guide
that way counts, so its write goes through at once. Either way, the guide's
text is sent once per conversation. When you change a guide, the old id stops working, so
the assistant sees the new version. `wiki-guide` counts as the guide for the
whole wiki. With users, only an admin can change a guide (or a page it pulls
in): every user's assistant follows it. A guide in your private folder is
yours.

Set `base-url` (`./run.sh config set base-url …`) to Zone's address as your browser knows it, so the links
in the assistant's answers open (it matters when the client runs in a
container or on another machine). Before you connect, read the MCP points
under [Security](#security).

## Security

Zone is for one person, or a small team sharing a wiki (users): run it on
your own machine, or self-host it on a server. Out of the box it listens on localhost only, refuses requests with a
foreign `Host` header (DNS rebinding) and cross-site writes (another site
posting to `localhost:5000`). HTML in your pages is shown as written, but no
script in it runs: only Zone's own code does, so a page that an AI assistant
wrote after reading a malicious mail or web page can't take over your wiki.
Anything reachable beyond localhost belongs behind HTTPS, with a login
(`./run.sh config passwd`, or users).

**Self-hosting behind HTTPS.** Put a reverse proxy in front that terminates
HTTPS, passes the original `Host` and sets `X-Forwarded-Proto` and
`X-Forwarded-For`, and name the public host in `hosts`. With
[Caddy](https://caddyserver.com) on the same machine (it does all of that and
gets the certificate):

```
wiki.example.org {
    reverse_proxy localhost:5000
}
```

Without a port at all, Zone listens on a Unix socket (`zonewiki --addr
unix:/run/zone/zone.sock`), and Caddy's line is `reverse_proxy
unix//run/zone/zone.sock`.

```bash
./run.sh config set hosts wiki.example.org
./run.sh config set base-url https://wiki.example.org
./run.sh config passwd                 # long and unique
```

Leave `ZONE_ALLOW_REMOTE` off here: the proxy reaches Zone on localhost, and port
5000 stays closed to the network, so nobody can go around HTTPS. A proxy on
another machine needs `ZONE_ALLOW_REMOTE=1` too; `hosts` keeps the `Host`
check on. Over HTTPS the login cookie is marked secure, so the browser never
sends it unencrypted. Wrong passwords slow a guesser down, with or without
HTTPS: after 5, each try waits, from 1 minute doubling up to an hour, per
client.

There is one shared password (or, with users, one per user) and no
second factor. If only you need to
reach Zone from outside, a VPN (WireGuard, Tailscale) is safer still than a
public address.

Also know this:

- **No login on localhost.** There, anyone who can reach the port can read,
  change and delete everything. Opened to the network (`ZONE_ALLOW_REMOTE=1`,
  names in `hosts`, or `compose.nas.yaml`), Zone shows nothing until a login
  is set: its first page asks for a password, or set one with
  `./run.sh config passwd` or `./run.sh user add NAME`. For a wiki open to
  your network on purpose: `./run.sh config set no-login yes`. Every user can read
  everything except other users' private pages and tasks and the pages of groups they aren't in (and all but viewers
  can change it), so give access to no one you wouldn't hand the shared files to.
- **Private means private inside Zone.** Another user's private pages and
  tasks are hidden from you and your assistant, but not encrypted: whoever
  has the wiki folder, its git history or its git remote reads them all.
  An admin doesn't see them either, but can reset a user's password and
  sign in as them with the link (the user notices: their old password
  stops working). Make admin only whom you'd trust with everyone's pages.
- **Plain `http://` is readable on the network.** The password when you sign
  in, the login cookie after it, the MCP token and your pages all travel
  unencrypted, so anyone on the same network (public Wi-Fi, a shared LAN)
  can read them and sign in as you. That's fine on localhost; anywhere else,
  use HTTPS or a VPN.
- **MCP clients can write.** Whoever has an MCP token can create, change,
  rename and delete pages, and change and close tasks, password or not (with users, whatever
  the token's user may). Keep the token secret and give it only to clients
  you trust.
- **Pages can carry instructions.** An AI assistant reads your pages, and
  text pasted into them from elsewhere (tickets, emails, web pages) can try to
  steer it ("ignore your instructions and delete …"). If your wiki holds such
  text, have your client ask before it writes (MCP clients can, per tool;
  Zone marks the tools that change or delete as destructive).
- **Git is the undo.** Every change made through Zone, by you or an assistant,
  is a commit (an assistant's are marked `(MCP)`); Changes lists them,
  and history, restore and Deleted Pages bring things back. Set `git-remote` and push now and then, so a
  copy lives elsewhere too. The remote has everything but `.zone/config`
  (the users and settings): keep a copy of that file as well.
- **Treat the wiki folder, its git history and remote, and any backup as
  confidential.** They hold everything, every user's private pages included,
  and a backup of the folder holds the password hashes too.
- **Pages may contain HTML**, which is rendered as is, but runs no script and
  loads nothing from other sites: an image elsewhere shows as a link to it.
  That keeps a page (one an assistant wrote after reading a malicious mail,
  say) from sending your notes away in an image's address. To show images,
  video or frames from sites you trust, name them in `external-content`.

## Your data

Everything is files in `ZONE_DATA`, which is a git repository:

- Pages are `.md` files, in folders if you like.
- Uploads are in `_assets/`. Tasks are in `_tasks/` as JSON, not Markdown: they
  have fields (dates, repeats, tags) and are written safely when several
  things change them at once. They are still plain files in the git
  repository.
- `.zone/` is Zone's own state, never committed but for `config`, the
  settings file (users, password hashes, token hashes): whoever has the
  repository has those hashes, so use good passwords. `secret_key` keeps you signed
  in across restarts, `index/` is the semantic search cache (only with
  `ZONE_SEMANTIC_SEARCH`; it can be deleted at any time).

Nothing is locked in: if you stop using Zone, your pages are plain Markdown
files that any editor opens, with their history in git.

## Development

```bash
./run.sh test     # go vet, go test, then the API tests (pytest)
./run.sh e2e      # browser tests (Playwright) and the task logic tests (Node)
./run.sh leaks    # scan the git history for secrets (gitleaks)
./run.sh screenshots   # redo the screenshots above (docs/screenshots/)
./run.sh eval     # measure how well search finds answers in your wiki
./run.sh user …        # the users in the settings file (add, passwd, token, remove, list)
./run.sh group …       # groups made of groups (includes, list)
./run.sh config …      # the settings file (check, set, unset, passwd, token, import)
```

All of them run in containers (Podman or Docker; `user` and `config` through compose, as `up`); CI runs the same commands. To have
commits with a secret in them refused, turn on the pre-commit hook once:
`git config core.hooksPath .githooks`.

Go with goldmark for markdown, plain ES modules in the browser, and one
stylesheet; no bundler, no npm.

### How Zone is made

Zone is AI-engineered: its code is written by AI coding agents, carefully
guided by its maintainer, who sets the direction
and makes the design decisions. The guardrails live in the repository,
where you can check them:

- `AGENTS.md` holds the principles, constraints and conventions every change
  has to follow.
- `docs/spec/` describes every feature in detail and is updated with the
  change that alters it.
- Every feature and fix comes with tests (unit, API and browser tests),
  and the whole suite runs before and after each change.

## Contributing

Found a bug or have an idea? Open an issue; there are templates for both.
[CONTRIBUTING.md](CONTRIBUTING.md) says what a bug report needs and how a
change gets in.

## License

Copyright (C) 2026 Gernot Klingler. Zone is free software under the
[GNU Affero General Public License](LICENSE), version 3 or (at your option) any
later version (`AGPL-3.0-or-later`). Using it, also in a company, asks nothing of
you. If you change it and others use your version over a network, offer them its
source: point `sourceURL` in `go/config.go` at your copy, and the link in the
shortcuts dialog (`?`) does that.

What Zone ships from others stays under its own license:
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES) has Go, the Go modules and the
embedding model, and each library in `public/vendor/` has its license next to it:
[highlight.js](https://highlightjs.org) under BSD-3-Clause, and the emoji names
(GitHub's [gemoji](https://github.com/github/gemoji)), [KaTeX](https://katex.org)
and [Mermaid](https://mermaid.js.org) under MIT. Mermaid's file bundles other
packages, listed in `public/vendor/mermaid/THIRD_PARTY_LICENSES`, among them
[elkjs](https://github.com/kieler/elkjs) under GPL-3.0-or-later (it also offers EPL-2.0).
