# Users

Part of Zone's spec (see `AGENTS.md` for the index).

Several people on one wiki: each signs in with their own name and password, their changes are committed in their name, and an assistant connected with their MCP token acts as them.
Everything in the wiki is shared and editable by every user, except each user's private folder, `private/<name>/`, and their private tasks, which only they see.

Users are `[user NAME]` sections of the wiki's settings file, `.zone/config` (below).
Without them none of this applies: one password (or none) and one MCP token, and Zone commits as itself.

## In short

Three rules; the rest of this file is how they are kept.

1. **Your folder is yours.** `private/<you>/` holds your private pages, your uploads (`_assets/`) and your private tasks (`_tasks/`).
   No one else sees it in Zone, in search, in history, or through their assistant.
   Everything outside it is shared, and every user may change it, but a viewer, who changes nothing (Roles, below).
   (Hidden inside Zone, not encrypted: whoever has the wiki's folder or git repository has it all.)
2. **Where something is kept is who sees it.** Not its tags, not its links, not its name.
   A new page or task starts where it is made: from a link to `private/<you>/…`, an embed on a private page, your private pinned list or list.
3. **Private can be shared; shared stays shared.** A private page goes public by Make shared (a rename out of the folder), a private task by unticking its Private box.
   Never the other way: others have seen a shared page or task, link to it and have comments on it, so Zone refuses to take one into a private folder or tasks (the error says to make a new private one and copy the text).
   Making a page shared takes the uploads it shows with it.
   Nothing else moves anything.

## The settings file

The wiki's settings and its users are in `.zone/config` in the wiki, a small INI read by Zone's own parser (`go/configfile.go`; no dependency).
Zone reloads it when it changes, so nothing here needs a restart.
`.env` holds only the deployment (`docs/spec/deployment.md`).

```ini
# Zone's settings and users (docs/spec/users.md). Zone reloads this file when it changes.
locale = de-AT
hosts = wiki.example.org, nas.local
noshare = #noshare, people/

[user alice]
password = pbkdf2-sha256:600000:<salt>:<hash>
mcp-token = sha256:<the token's SHA-256, hex>
role = admin

[user bob]
password = pbkdf2-sha256:600000:<salt>:<hash>
disabled = yes
```

| Key | Does | Default |
|-----|------|---------|
| `password` | The login gate without users: a hash (`zonewiki config passwd`) | no login |
| `mcp-token` | Turns on `/mcp` without users: the token's hash (`zonewiki config token`) | `/mcp` off |
| `hosts` | Names Zone may be reached by besides localhost, comma-separated (the Host check, `docs/spec/security.md`) | localhost only |
| `base-url` | Zone's address as the browser knows it, for links handed to AI clients (`docs/spec/mcp.md`) | the address the request came in on |
| `git-remote` | A remote for the wiki's repository; `origin` is set to it (`setGitRemote`) | none |
| `noshare` | Pages hidden in sharing mode: `#tags` and `folders/` (`docs/spec/navigation.md`); empty: none | `#noshare` |
| `locale` | How dates read and the day weeks start on, a BCP 47 tag like `de-AT` (`docs/spec/tasks.md`) | US English dates, weeks from Monday |
| `date-format` | `iso`: dates as `YYYY-MM-DD` | the locale's |
| `query-log` | `yes`: keep `search_wiki`'s queries in `.zone/queries.jsonl` (`docs/spec/search.md`) | no |
| `semantic-min-score` | How similar a passage must be to be a semantic hit, -1 to 1 (`searchFloor`, `docs/spec/search.md`) | the model's floor |
| `trusted-proxies` | Reverse proxies whose `X-Forwarded-For` names the client, IP addresses or networks (`172.17.0.1, 10.0.0.0/8`), for the throttle on wrong passwords (`clientKey`, `docs/spec/security.md`); empty: none | loopback (`127.0.0.0/8, ::1`) |
| `upload-max` | The biggest upload, a size (`25MB`; below) (`Settings.uploadMax`, `docs/spec/security.md`) | 100 MB |
| `external-content` | Hosts a page may load images, media and frames from: `upload.wikimedia.org, *.example.org`; `*`: any (A page loads nothing from other hosts, `docs/spec/security.md`) | none: only Zone itself |
| `no-login` | `yes`: the wiki has no login on purpose, though other devices reach it, so Zone shows no first-run page (First run, `docs/spec/security.md`) | no |
| `mcp-oauth` | `no`: assistants connect with a static token only, not by signing in through the browser (Connecting with OAuth, `docs/spec/mcp.md`) | yes, with a login |
| `storage-max` | How much the wiki may hold, a size (`5GB`): past it, nothing new is made (Storage limit, below) | no limit |
| `signup-domains` | Sign-up takes only addresses at these domains, comma-separated (`example.org, example.net`; Sign-up, below) | any address |
| `signup-role` | The role an account made by sign-up gets: `viewer` or `editor` (never `admin`) | `viewer` |
| `signup-approval` | `yes`: an account made by sign-up can't sign in until an admin approves it | no |

- **Format**: `key = value` (spaces optional), the wiki's settings before the first section, a `[user NAME]` section per user with `password`, `mcp-token`, `role`, `disabled`, `email` and `pending` (`configKeys`, `userKeys`; Roles and Sign-up, below).
  `#` starts a comment only at the start of a line, so `noshare = #noshare` is a value.
- **Sizes** (`upload-max`, `storage-max`; `parseSize` in `go/storage.go`): bytes (`300`), or a number with KB, MB, GB or TB, any case, a space optional, decimals too (`1.5GB`).
  They count by 1024, as Zone's caps always have (100 MB is 100 × 1024 × 1024 bytes).
  Empty is unset; 0, or a unit Zone doesn't know, is an error.
- **Storage limit** (`storage-max`, `go/storage.go`): what counts is every file in the data directory but `.git/` and Zone's own `.zone/` (the settings file, the session key, the search cache): the user's pages, uploads (`_assets/`, `private/<user>/_assets/`), tasks and any other files there.
  Not the history: a deleted file still in git frees its space, and the history, mostly text, is the host's to plan for.
  Zone walks the directory only when a write asks for room, and then at most once a minute (`usedBytes`; a walk reads names and sizes, no contents: 20,000 files take about 10 ms); an upload or a new page adds its size at once (`added`), so uploads in a row can't slip past.
  A figure that says full is walked again first, but at most every 5 seconds (`usageRecheck`), so a client asking over and over in a full wiki doesn't make a walk each time: a file deleted on disk counts within seconds, a page deleted through Zone at once (`forgetUsage`).
  Refused once it would go past (`checkRoom`, `refusedFull`: 507 Insufficient Storage, "The wiki is full: 5.0 GB of 5 GB used. Delete pages or files to make room."; an MCP tool's error): an upload (its size, less the file's it replaces: a smaller file replacing one always fits),
  a new page (`/new`, the editor opened on a page that doesn't exist yet, its first save, `write_page`),
  a new note (`/api/notes`, `write_page` with `note`).
  Never refused: an edit to a page, even one that grows it (typed text is never turned away), a restore, a rename or move, a delete, Move to wiki and Append to page (a note's text moves), tasks (small, and capped per request).
  The browser shows the refusal of an upload or a new note (`alert`); the editor never opens on a new page, so nothing typed is refused.
- **Errors name their line** (`.zone/config line 3: "pasword" isn't a setting Zone knows.`),
  and Zone won't start on one: a key it doesn't know (a typo shouldn't leave Zone on a default),
  a value that doesn't check (`localeRE`, `parseNoshare` …; `query-log` takes yes/no, 1/0, true/false),
  a section other than `[user NAME]`,
  a user's key outside a section or the wiki's inside one,
  a key or a section given twice,
  a line that isn't `key = value`,
  `password` or `mcp-token` of the wiki together with users (each has their own; a shared one would make the user unknown).
  Messages never repeat a password or token.
- **Passwords are kept as hashes**, never in plain text: PBKDF2-SHA256 with a random 16-byte salt and 600,000 iterations (`hashPassword`; Go's own `crypto/pbkdf2`), written `pbkdf2-sha256:<iterations>:<salt>:<hash>` (base64 without padding); checked at sign-in by `checkPassword`.
- **MCP tokens are kept as hashes** too, `sha256:` and 64 lowercase hex digits (`hashToken`, `isTokenHash`): the commands make a token (32 random bytes, hex), show it once and keep only its hash; Zone compares the SHA-256 of the token a client sends (`tokenMatches`, constant time).
  A token is random, so its hash is no use to whoever has the repository.
  A plain token in the file is an error.
- **The file is kept out of git**, as all of `.zone/` is (the session key, caches, the query log): `.git/info/exclude` has `/.zone/*` (`excludeState`, also before a new wiki's first commit).
  **Account data stays out of git**: the file holds the users,
  their password and token hashes,
  roles,
  and whatever a later key adds about a person,
  and git history is forever (a removed user's data couldn't be erased from it, and wherever the repository went, a git remote, an export, the hashes would go along for offline guessing).
  What stays in git is authorship: commits name their user (`alice <alice@zone>`), a pseudonym once the account is gone.
  A key that says something about a person goes into this file or another one in `.zone/`, never into a committed one.
  Zone 1 committed the file until 2026-11,
  and its last release took it out at start: a wiki that never ran that release still tracks it (`git rm --cached .zone/config` and a commit take it out),
  and the history before holds it either way,
  so a wiki pushed where others can read it should get new passwords.
- **Changes go to the log**, not the history: each reload that took a change logs what changed by name and key only, never a value (`settingsChange`: `Change settings: add user carol, alice's password, locale`), and who made it on the Users tab (`(by alice)`, `settingsAuthor`).
- **Backups**: a backup of the data folder has the file; a clone of the git remote doesn't.
  Back up `.zone/config` too where the git remote is the only copy, or add the users again after a restore.
- **A wiki that lost its users doesn't start open** (`missingUsers`): when there is no settings file but the history has commits by users (a clone of its git remote),
  Zone refuses to start,
  naming one of them,
  and says to restore the file from a backup,
  add the users again,
  or create an empty `.zone/config` to run without a login on purpose.
  A new wiki has no such commits and starts as before.
- **Zone never shows it**,
  since it holds password hashes: `canSeePath` is false for `.zone/`,
  as for every hidden folder,
  for everyone,
  so history,
  the uncommitted list,
  Commit all and the MCP tools leave it (and a commit of nothing else) out,
  and `safePath` refuses it to every page route and MCP tool.
  Whoever has the file (a backup of the data folder, a hosted wiki's export) has the hashes: PBKDF2 at 600,000 rounds is made to withstand that, but a weak password can still be guessed offline.
  The git history and remote don't have them (above), but those of a wiki from before 2026-11 do.
- **Writing**: the commands and the admin page's Users tab (Managing users, below) write it owner-only (0600),
  whole,
  to a temporary file in `.zone/` synced and renamed over it (a reload never reads half a file),
  one command at a time (`lockFile` on `.zone/config.lock`, flock) and only onto the file as the command read it (changed meanwhile, by another command or an editor: "not changed: … Run it again."),
  keeping the comments and order of the lines they don't change (`go/internal/settingsfile`: `File.Set`, `Unset`, `RemoveUser`, `Write`).
  Zone logs a warning when others may read it.

### Commands

The file can be edited by hand, or with the commands; a running Zone takes each change within a second:

```bash
./run.sh user add alice                # asks for her password twice
./run.sh user add bob --mcp-token      # also makes him an MCP token, shown once
./run.sh user passwd alice             # a new password
./run.sh user token bob                # a new token; the old one stops working at once
./run.sh user add carol --role viewer   # admin, editor (the default) or viewer; the first user is an admin
./run.sh user role carol editor        # change a role
./run.sh user disable bob              # he can't sign in, his token stops; user enable bob undoes it
./run.sh user remove bob               # his private pages and tasks stay on disk
./run.sh user remove alice --password  # the last user: the wiki's password instead (or --open: no login)
./run.sh user list                     # the users, their roles (but editor), who has a token, who is disabled
./run.sh config set hosts nas.local    # any other key of the table; config unset KEY for its default
./run.sh config unset password         # no login (a running Zone takes it only at a restart)
./run.sh config passwd                 # without users: the wiki's password
./run.sh config token                  # without users: the wiki's MCP token, shown once
./run.sh config check                  # what's wrong, or each setting in effect and where it comes from
```

- Without the container it is `zonewiki user …`, `zonewiki config …` with `ZONE_DATA` set (`go/commands.go`: `userMain`, `configMain`); on a NAS, through the container: `docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki user add alice`.
  `run.sh` runs the binary in a one-off container of the app's image (`compose build -q zonewiki`, its output only on failure, then `compose run --rm --no-deps zonewiki /app/zonewiki …`), with `.env` and the wiki mounted as `up` has them, so the file is written as Zone's user;
  the password prompt's echo is off (`echoOff`, the container has a terminal; `-T` when stdin isn't one).
- They refuse a change Zone wouldn't start with ("not changed: …", the file as it was): a user who is there already (add) or isn't (the others),
  a value that doesn't check (or isn't one line),
  `config passwd`/`token` with users (each has their own),
  `config set password`/`mcp-token` (those have their commands; `config unset` takes them out: no login, `/mcp` off).
- **Between a password and users**: the first `user add` replaces the wiki's `password` and `mcp-token` (with users each has their own) and says so;
  the last user goes only with `user remove NAME --password` (asks for the wiki's password, set in the same write) or `--open` (no login), else it is refused: without users private/ is an ordinary folder everyone sees, and without a password the wiki has no login.
- **Never back to no admin**: once the wiki has an admin (not disabled),
  a change that would leave it without one is refused,
  whether it removes,
  demotes or disables the last ("that leaves the wiki without an admin. Make another user admin first": `keepsAdmin`, in `saveConfig`, so every command keeps to it).
  Taking the last user out (`--password`, `--open`) is no such change: without users there is no one to manage.
  An edit by hand isn't checked: it stays the way out.
- `config check` lists each setting, its value (passwords and tokens only as `(set)`) and where it comes from (`[line 3]`, `[default]`), and the users; it fails on an error in the file and on settings left in the environment.
- **Names**: lowercase letters, digits and inner hyphens (`userNameRE`), since a name becomes a folder name.
  `me` and `none` are reserved (`reservedNames`): no new user gets one, from `user add`, an invite, sign-up or the first-run page (Assigning tasks, below).
  A user's MCP token is optional: without one they have no MCP access.

### Reloading

The request checks (`checks` in `go/server.go`) call `reloadSettings`, which looks at the file at most once a second (`reloadEvery`: its time, size and whether it is still the same file, as an editor may replace it); a change reads it again.
No file watcher.
The work after a change (`applySettings`: the git remote; the log line) runs outside the lock (`takeSettings`), so the other requests don't wait for it.
The settings are held behind an atomic pointer (`settings()`, `Settings`); what is fixed for the process stays in `cfg`.
**A change takes effect between requests**: the checks count the requests running (`beginRequest`, `endRequest`),
and a change takes effect only while none is running (`betweenRequests`): at once when none is,
else when the last one ends (`endRequest` takes it under the lock `beginRequest` takes, so no request begins in between and holds it up again; a goroutine only sees to the time limit below);
until then requests go on with the settings before, so nothing waits for the change, and a newer change replaces one still waiting.
So a request sees one set of settings from its session check to its answer: else one whose session was checked against the users before the change could be judged by the ones after (a switch from users to a password makes `private/` an ordinary folder, for a user who no longer has a login).
A request still running after 10 s (`drainWait`: a slow download) doesn't hold the change up longer; the log says so.

- **A file with an error** on reload: Zone keeps the settings it has,
  logs the error with its line,
  and `GET /api/health` answers 503 with it (without a session: only that there is one, see the log, as it may name users),
  so `zonewiki health` (the container's healthcheck) fails until the file is fixed.
  A broken edit never locks everyone out or opens the wiki.
  A file removed while Zone runs counts the same (taking the users away would open the wiki): Zone keeps its settings until the file is back, or a restart.
  So does a file that takes the login away, no users and no password where there were (by hand, `user remove --open` or `config unset password`): only a restart takes that, on purpose.
- **What a reload changes**: a new user signs in at once; a removed or disabled user is signed out with their next request (the session must name a current user, not disabled); a new role applies from the user's next request; a changed MCP token takes effect at once, the old one refused.
  A changed password ends the sessions signed in with the old one (`passwordStamp`, below), the user's own included.
  Users ↔ no users signs everyone out, as `signedIn` checks.
  A new `git-remote` re-points `origin` (`setGitRemote`); a new `noshare` clears its cache; the change is logged (above).
  Throttles, caches and the search index stay.

## Roles

Each user has a role, `role` in their section (`roles`, `User.Role` in `go/users.go`):

| Role | Can |
|------|-----|
| `admin` | everything an editor can, and manage the users (`zonewiki user`, on the host) and write the guides (Guides, below) |
| `editor` | change everything shared but the guides, as every user could before roles: the default, so a section without `role` is an editor's |
| `viewer` | read what is shared; change nothing, not even private pages or notes of their own (Viewers, below) |

- **The first user is an admin**: `user add` on a wiki without users writes `role = admin` and says so, unless `--role` says otherwise.
  So one person running their own wiki is its admin without doing anything.
  Every other user is an editor unless `--role` says otherwise.
  A wiki from before roles has no admin until `zonewiki user role NAME admin` makes one; nothing else changes for it.
- **An admin isn't a superuser**: no admin sees another user's private pages, tasks or notes, or knows their password.
  Rule 1 above holds for everyone.
  **But an admin decides who a user is**,
  and that is a way in which Zone can't close,
  as it sends no mail and the admin hands the links out: a reset link sets a user's password for whoever opens it,
  the admin too,
  and someone invited under a removed user's name gets that user's private folder (as Remove's dialog says).
  So private pages are private from an admin as long as the admin leaves the account alone, not against one who takes it over.
  It doesn't go unseen: a reset ends the user's sessions and their old password stops working, and the settings file's log line names the change.
  Whoever has the host reads everything anyway (Security in the README).
  The Users tab and the README say so.
- **Disabled** (`disabled = yes`, `zonewiki user disable NAME`): the user can't sign in (with the right password the login says "Your account is disabled. Ask an admin of this wiki.", with a wrong one the usual "Incorrect user name or password.", and both count as a wrong try for the throttle),
  their sessions stop working at their next request (`signedIn`),
  and their MCP token is refused (401; `mcpAuthorized`; a disabled user's token alone doesn't turn `/mcp` on).
  Their pages, tasks and private folder stay as they are.
  Enabled again, they sign in as before, and a session from before not yet expired works again: disabling isn't how to end a stolen session, a new password is (Signing in, below).
  A disabled admin counts as no admin.

### Viewers

A viewer reads everything shared (pages, tasks, history, search) and changes nothing: no page, note, upload, checkbox or task, not even a private one of their own, no tag rename, no Commit.
They have no private folder in use (no Private, no Notes, `/private` is a 404); one kept from when they were an editor stays on disk and they still see it, but change nothing there either.

- **Why so strict**: it is what "viewer" means elsewhere, so an admin who invites a client or a relative as one isn't surprised; "a viewer changes nothing" is one rule to trust and test; and a viewer's "private" page would be in the wiki's git history all the same, the user least likely to know that.
  Someone who needs notes or tasks of their own is an editor.
- **The rule** is in `go/access.go`: `canWrite(user, slug)` is false for a viewer, so every page write (the operations in `go/pageops.go`, the MCP tools) refuses them;
  `readOnly(user, slug)` (sees it, can't change it) makes the refusal say so: **403** "You can read this wiki but not change it: you are a viewer.
  Ask an admin of this wiki." (`readOnlyMsg`, `refusedReadOnly`).
  Every task write passes `mayWriteTasks` (the store's `MayWrite`, asked in `WriteOpenLocked` and `WriteBothLocked` of `go/internal/taskfiles`), which refuses a viewer the same way (as JSON, so the task app shows it): no route or tool can change a task for them, whatever it does.
  Uploads and tag renames (`renameTagOp`) are refused too.
- **The gate** (`viewerGate` in `go/server.go`): a viewer's POST, PUT, PATCH or DELETE to a route not listed in `viewerWrites` is refused (403) before the route runs, whatever it names (another user's folder answers as anywhere else).
  Listed are only those that change nothing (sign in and out, the previews) and `/mcp`.
  A new write route is so closed to viewers until it is listed.
- **MCP**: a viewer's token works for reading: `tools/list` leaves out the tools that write (`writeTool`: those that write pages or tasks in `mcpToolTable`), and calling one answers with the message (`callMCPTool`).
- **The interface**: no Edit, ✎s, Rename or Delete on a page, checkboxes that don't tick (`pageView.ReadOnly`, `.read-only`);
  the editor is a 403 before anything is typed;
  no Private or Notes module, no New note or New task (at the top, in the tab bar, the task app's + New, a comment's field; `body[data-viewer]` in `style.css`), and `c n` / `c t` do nothing (`js/shortcuts.js`).
  The task modal still opens on a task: a change there comes back with the message.

### Visitors

Not a role a user has: whoever isn't signed in, where the server's `.env` says `ZONE_PUBLIC=view` (they read the shared pages) or `ZONE_PUBLIC=edit` (they edit them too, as "anonymous").
They see no tasks, notes or private pages.
Public wikis in `docs/spec/security.md` says what they reach and why it is set in `.env`.

### Guides

With users,
only an admin changes a shared guide: the wiki's (`wiki-guide`),
a folder's (`AGENTS.md`, `CLAUDE.md`, in any case),
and a page one of them pulls in with a line `@other.md`,
whether it is there yet or not (`guidePage`, `guideImports` in `go/access.go`; the pulled-in pages are looked up at most once a second).

- **Why**: a guide is what every user's assistant is told to follow, the wiki's in its instructions "where it differs from the advice above, it wins", a folder's before a write there (Guides in `docs/spec/mcp.md`).
  Whoever writes one steers them all: an editor, or an editor's assistant that read a prompt injection in a mail, could have every other user's assistant copy their private pages into the open, and it would stay in every session.
  An admin already decides who is a user.
- **The rule** is `adminsOnly(user, slug)` in `canWrite`: an editor can't edit, create, rename (from or to a guide's name), move (a folder with one in it), delete or restore a guide, nor tick a box on it, by any route or MCP tool.
  They read it as a viewer reads a page (`readOnly`: no Edit, the editor a 403), and the refusal says why: **403** "Only an admin of this wiki can change this page: it is a guide, which every user's assistant follows (or a page a guide pulls in).
  Ask an admin of this wiki." (`guideMsg`, `readOnlyWhy`).
  An MCP write is refused with it before the guides are asked for (`checkGuides`); `replace_in_pages` leaves the guides out.
- **A guide in a user's private folder is theirs**: only their own assistant reads it.
  **Without users** there are no admins: the wiki's one user writes its guides.
  A wiki with users but no admin (from before roles) has no one who can, until `zonewiki user role NAME admin`.
- **What still reaches a guide**: a page rename re-points the links in it, and a tag's rename the `[[tasks: …]]` on it, in the renamer's commit; neither writes words of the renamer's own choosing beyond the new name.
  An admin's assistant is an admin: if it reads a prompt injection, it can write a guide.
- **Tests**: `TestGuidesAreAnAdmins` in `tests/test_roles.py`.

## Managing users

**The admin page** (`/admin`, `go/admin.go`) is the admins' way to manage the wiki in the browser, as `zonewiki user` is on the host.
"Admin" in the user menu opens it (phones: beside Sign out at the top), shown to admins only; for anyone else, and in a wiki without users, `/admin` and everything under it is a 404, as for a page that isn't there.
It has section tabs (`adminTabs`), of which **Users** (`/admin/users`, `users.html`) is the one so far: `/admin` opens the first tab.

- **The top row** is every view's: the module switcher and `Admin › Users` (`breadcrumb`, not in the navigation trail), then the heading and the tabs.
- **The list**: each user's name, "(you)", "MCP" if they have a token, their role, and Active or Disabled.
  Then the invite form, the open links (Invite and reset links, below) and what the roles mean.
- **Per user**: change their role (a select and Change),
  **Disable** or **Enable**,
  **Reset password** (makes a link),
  **Remove…** (a dialog that says what happens, as deleting a page does, then **Remove NAME**;
  `js/admin.js` opens it with what the row's `<details>` holds, and without JavaScript the `<details>` unfolds instead: their private pages, notes and tasks stay in `private/NAME/`, seen by no one, and someone added again under that name gets them back;
  an open link of theirs goes too).
  There is no field to type another user's password: the admin never knows it.
- **Not yourself**: the admin's own row ("(you)") has no Disable or Remove…, and a POST that disables or removes the admin themselves is refused ("you can't … yourself here. Another admin can.").
  A slip there would lock them out of the wiki, and it would only be allowed with another admin left, who can do it.
  They can still change their own role, to step down while another admin remains.
  As the last user is always the admin themselves, this is also why the last user can't be removed here.
- **Plain forms**: each change is a POST (behind the Origin check, like every write), and the page works without JavaScript, which only opens the Remove dialog.
  Done, it answers with a redirect to `/admin/users?done=…&user=…`, and the page says what was done from a fixed set of messages, so a link can't make it say anything else.
  Refused, the page comes back (409) saying why.
  An admin who demoted themselves is sent to the home page.
- **The same rules as the commands**: the page edits the settings file with `go/internal/settingsfile` and saves it with `saveConfig` (checked, locked, only onto the file as read: a change made meanwhile by hand or a command is never overwritten),
  so a change that leaves the wiki without an admin is refused here too.
  The last user can't be removed here (they are the admin themselves): without users the wiki has no login, which is for the host (`zonewiki user remove NAME --password` or `--open`).
- **Taken at once, logged with the admin**: after the write Zone reloads the file at once (`changeSettings`), not within the second;
  the change takes effect once the requests running (the admin's own too) have ended (Reloading, above), and the page lists the users as that change has them (`latestSettings`).
  Its line in the log names the admin (`Change settings: bob's role (by alice)`, `settingsAuthor`); the file isn't in git.

## Invite and reset links

A link that lets one person set a password, once (`go/links.go`).
Zone doesn't mail these: the admin copies the link and sends it any way they like (only sign-up mails its link, below).

```bash
./run.sh user invite dave --role viewer   # an invite: the link, shown once
./run.sh user reset alice                 # a new password for alice
```

- **Made** on the admin page's Users tab (Invite someone: a name and a role; Reset password beside a user) or with `zonewiki user invite NAME [--role ROLE]` / `zonewiki user reset NAME`.
  The page shows the whole address (with `base-url`'s, else the address the admin's request came in on), the command `base-url`'s or the path alone.
  An invite is for a name no user has, a reset for a user there.
  While an invite is open its name is taken: `user add` of it is refused.
- **Kept** as the token's hash only (`hashToken` of 32 random bytes, as an MCP token),
  with the user's name,
  invite or reset,
  the role,
  who made it and when it expires,
  in `.zone/links`: owner-only (0600),
  written whole,
  one change at a time (`changeLinks`: a lock in the process and `lockFile` on `.zone/links.lock`, as a command may write it too),
  not committed (state, like the session key: the rest of `.zone/` is excluded from git).
- **Valid** 7 days (an invite) or 24 hours (a reset), **once**.
  A new link for the same name replaces the old one; **Revoke** on the Users tab (open links: for whom, what, by whom, until when) deletes it.
  Expired links go at the next change.
- **Capped**: 10 links per admin in an hour (`linksPerHour`, in memory), so a stolen session can't fill the list.
- **The welcome page** (`/welcome/<token>`, `welcome.html`; no login needed): opening it changes nothing, since mail scanners and chat previews open links.
  It greets the invitee by name (or says whose password it sets) and asks for a password twice, of at least 8 characters as on the first-run page (`passwordMin`; "Choose a password of at least 8 characters.", "The passwords differ.").
  Only its POST uses the link: an invite adds the user with its role, a reset sets the new password, which ends every session of the old one (`passwordStamp`) and the connections of the assistants they allowed (`revokeGrantsOf`: whoever needs a reset may have lost the account to someone; their static token stays, to be revoked on Connect an assistant);
  either way the change is logged as that user's, the link is gone, and they are signed in (a session made with the new hash, `setSessionHash`, as the settings take effect only once the request has ended).
  A user who was removed (reset), or a name someone took meanwhile (invite), is refused, as is a wiki that has no users any more.
  Its address holds the token, and no other site is told it (`Referrer-Policy: same-origin`, as every page).
  Not `no-referrer`, which the page sent until 2026-11: under it a browser sends the form with `Origin: null`, the Origin check refused that, and the links didn't work in a browser (`test_a_reset_link_works_in_a_browser` in `tests/e2e/test_admin.py`).
- **A link that doesn't work**, wrong, used, expired or replaced, gets one page for all ("This link doesn't work", 404), and a client trying them is throttled as for wrong passwords (`welcomeThrottle`).
- **No minimum length** for the password, as `user add` has none.

## Sign-up

People may ask for an account themselves, with their mail address (`go/signup.go`).
It is the deployment's to allow and the wiki's admin's to shape:

```bash
# .env (the deployment): on, and the mail server (Mail in docs/spec/deployment.md)
ZONE_SIGNUP=1
ZONE_SMTP_HOST=smtp.example.org
ZONE_MAIL_FROM=wiki@example.org
```

```ini
# .zone/config (the wiki): all optional
base-url = https://wiki.example.org      # needed: the mail's link is on it
signup-domains = example.org             # only these addresses; unset: any
signup-role = viewer                     # or editor; default viewer
signup-approval = yes                    # an admin approves each account; default no
```

- **On** when `ZONE_SIGNUP` is, with a mail server (Zone won't start with `ZONE_SIGNUP` and no `ZONE_SMTP_HOST`), the wiki has users (an admin to manage them) and `base-url` is set (`signupOn`).
  The link in the mail is always on `base-url`, never on the address the request came in on: a request with a forged `Host` would otherwise send the link to another site.
  Without users or `base-url` it is off and Zone says why in its log at start; `/signup` answers 404 then.
  The login page links to it ("No account yet? Sign up"), and the admin page's Users tab says it is on, for whom and as what.
- **Asking** (`/signup`, signed out; someone signed in is sent home): a mail address, nothing else.
  It must be a bare address (`name@example.org`: no display name, no spaces or commas, a domain with a dot; `validEmail`), at one of `signup-domains` if there are any (refused on the form, naming them: "This wiki takes sign-ups only with an address at …").
  Zone then mails the address a link, and the page says "Check your mail".
  The name and password are chosen only after the address is confirmed, so the form tells no one which names exist.
- **An address that has an account** gets a mail with its user name instead of a link, and the form says the same "Check your mail": the page tells no one who has an account.
- **The link** is a welcome link of kind `signup` (Invite and reset links, above): its hash in `.zone/links`, with the address, valid **24 hours**, **once**.
  Asking again with the same address replaces it; at most 200 sign-up links are open at once ("Too many sign-ups are waiting").
  Sign-up links aren't listed with the admin's open links: nobody made them, and they expire within a day.
  A sign-up link stops working when sign-up is turned off.
- **The link's page** (`/welcome/<token>`) says the address is confirmed and asks for a user name (as everywhere: lowercase letters, digits and inner hyphens) and a password twice (at least 8 characters).
  A name that is taken, by a user or an open invite, is refused there ("The user name alice is taken: choose another.") and the link stays good, as for any other mistake on the form.
  Then the account is made in the settings file, as theirs in the log: their password's hash, `role` (`signup-role`; nothing for an editor, the default), `email` and, with `signup-approval`, `pending = yes`.
  Without approval they are signed in and go to the home page; with it, the page says their account waits for an admin.
- **Approval** (`signup-approval = yes`): a `pending` user is disabled in every way a disabled user is (no sign-in, no session, no MCP token), and the login tells them, with the right password, "Your account waits for an admin of this wiki to approve it."
  The Users tab shows them as "Waiting for approval" with an **Approve** button (the Enable route: it takes out `pending` and `disabled`; the log says `approve NAME`); `zonewiki user enable NAME` does the same, and `user list` shows them as waiting.
  To turn one down, **Remove** them.
  Zone doesn't mail the admins about a waiting account: they see it on the Users tab.
- **The address is kept** as the user's `email` in the settings file (out of git, as all account data), shown to admins on the Users tab and by `user list`, and used to tell a second sign-up with it apart.
- **Capped**, so the form can't flood someone's inbox or the mail server's quota: 5 mails per client and 50 in all in an hour (`hourlyCap`, in memory; "Too many sign-ups in the last hour: try again later.", 429).
- **A mail that can't be sent** (the server down, refusing, no STARTTLS) is logged with why, the link is taken back, and the form says "Zone couldn't send the mail: try again later." (502).
- **Not yet**: a password reset by mail ("forgot password"): an admin makes a reset link, as before.

## Signing in

- The login page asks for the user name and the password (`login.html`; the name field only with users).
  The name is compared in lower case, without surrounding spaces.
  A wrong name or password says "Incorrect user name or password." (not which one), keeps the name typed, and costs the same time either way: an unknown name is checked against a dummy hash (`authenticate`), so timing doesn't tell which names exist.
  Wrong tries are throttled per client as with the wiki's one password (`loginThrottle`, see `docs/spec/security.md`); not per user name, which would let anyone lock a user out.
- **The session names its user** (`session.User` in the signed cookie, `signedIn`).
  With users, a session must name one of them; without, it must name no one.
  So removing a user signs them out, and switching between one password and users signs everyone out.
  It also carries a stamp of the password it was signed in with (`session.Pw`, `passwordStamp`: a MAC of the password's hash, which has its own salt): a new password,
  or the same one set again,
  or a user removed and added again,
  ends every session of the old one,
  so changing a password is how to sign out a stolen session.
  (Sessions from before the stamp count as the old password's: everyone signs in once.)
- **Change password** (`GET`/`POST /password`, `go/password.go`, `password.html`): whoever is signed in sets a new password in the browser, without a shell on the host.
  With users it is in the user menu ("Change password") and changes the password of whoever is signed in, never another user's (no field names one; an admin gives someone else a reset link);
  without users, with the wiki's one password, it changes that, for everyone (as `zonewiki config passwd`).
  The shortcuts dialog (`?`) links to it too, wherever there is a login, which is how a phone reaches it.
  A wiki without a login has no such page (404).
  A viewer may change theirs (`viewerWrites`).
  - **The form** takes the password you have (`current`) and the new one twice (`password`, `again`; at least 8 characters, `passwordMin`).
    The password you have is asked for because a session someone else got hold of (an unlocked laptop, a copied cookie) mustn't be enough to take the account over.
    A wrong one changes nothing (**403** "Incorrect password: nothing was changed.") and counts against the client as a wrong login does (`loginThrottle`: **429** with the wait), so the page guesses a password no faster than the login page.
    A new one too short or typed two ways is refused too (403).
  - **What it does**: the new password's hash goes into the settings file the way the admin page writes it (`changeSettings`; logged with who changed it).
    Every other session of the old password ends (the stamp above);
    the session it was changed in goes on, as the same session with the new stamp (its id kept, so Sign out still ends it everywhere), and the page says "Your password is changed" (`303` to `/password?done`).
    The MCP token and the connections are left as they are: an assistant stays connected (revoke the token, end a connection, on Connect an assistant).
  - Tests: `tests/test_password.py`, and from the menu in a browser in `tests/e2e/test_private.py`.
- **The request knows its user**: the request checks (`checks` in `go/server.go`) put the session's user into the request's context (`withUser`), `userOf(r)` reads it, and `newReq` keeps it as `rq.user`. "" means no users (or no one signed in).
- The user's name is shown beside Sign out (`.signout-user`, "Signed in as alice") on phones, in the page's top row.
  On desktop both are in the user menu (below): the form is hidden there (`.signout-user-menu`).

## MCP

With users, `/mcp` takes any user's token (`mcp-token` in their section) and the tools run as that user (`mcpAuthorized` returns the user, `callMCPTool` passes it to every tool).
A user's assistant can also sign in through the browser (OAuth): the user allows it, and it gets a connection of its own, listed on Connect an assistant (Connecting with OAuth in `docs/spec/mcp.md`).
A call without a token is a 401 that says where to sign in (with `mcp-oauth = no`: `/mcp` is off, 403, while no user has a token); a wrong token is a 401 and throttled as before (`docs/spec/mcp.md`).
A user makes and revokes their own token in the browser, on **Connect an assistant** in the user menu (`/assistant`, with their password; `docs/spec/mcp.md`); `zonewiki user token NAME` does it on the host, for any user.

An assistant sees and changes what its user does in the browser, no more (`docs/spec/mcp.md` has the tools):

- **Another user's private page is no page**: `read_page`, `edit_page`, `append_to_page`, `rename_page`, `delete_page`, `file_note` answer "No page …" for it, as for a page that doesn't exist (`mcpPageSlug(raw, user)`, `existingPage`).
  A new slug in `private/` outside your own folder (`write_page`, a rename's or a restore's target, `history` or `read_guides` of a page there) is refused with "of private/,
  only your own folder private/<you>/ is yours to read or write",
  whether or not anything is there (`newSlug(raw, user)`, `notYours`).
- **Lists and searches leave them out** (`visiblePages(user)`, `visibleSlugs`): `list_pages` (and its link counts),
  `search_wiki` (`retrieve(rq, …)` filters before the limit),
  `grep_wiki` (`grepPages` takes the visible slugs; the hits are counted after),
  `check_links`,
  read_page's "Broken links" and the "new links point to pages that don't exist" note of every write (a link into another user's folder is never reported broken, whether or not the page is there, and never suggested: `missingLinks`, else "no such page" would tell which private pages exist),
  `list_tags`,
  `replace_in_pages` (matches and changes only pages you may change),
  read_page's Linked from and Related,
  delete_page's "still link to it",
  write_page's similar pages,
  create_task's tag hints.
- **History**: `history` of a page as `pageLog` gives it (versions from before a page was yours are left out);
  recent changes (`history` without a slug) show of each commit only the files you may see and leave out a commit with none of them, asking git for more until `limit` are found (`gitLogAll(…, see)`);
  the uncommitted files likewise.
  A commit message naming another user's private page (`Promote private/alice/…_idea to idea`) names it "a private page" (`hideNames`), here and in the browser's history.
  `restore_page` takes only versions `pageLog` lists.
- **Notes** from `write_page` with `note` go to your folder (its guides apply: `checkGuides` with `private/<you>/note`).
- **Tasks**: the tasks tools see the shared tasks and the user's own private ones (Private tasks below): another user's private task is "No task with id …" to `update_task`,
  absent from `search_tasks`,
  `list_tags`' counts,
  `search_wiki`,
  read_page's `[[task:…]]` list ("no such task") and create_task's tag hints.
  `create_task` and `update_task` take `private` (true: the user's own; false: shared; refused without users); `create_task` makes a private task by default when the user's default task list is in their private folder, as `c t` does.
  The task text says "private" among its facts.
- **Guides**: a `CLAUDE.md`/`AGENTS.md` in another user's folder isn't a guide for you (`folderGuides(slug, user)`, `canSeePath`), not listed in read_guides' description (`toolList(user)`), and a guide's `@import` of a page you can't see stays as written (`guideText`).
  `rename_page` of a folder holding pages you may not move (`private/` itself) moves nothing.

## Private pages

Pages in `private/<name>/` (and its subfolders) are that user's: only they see and change them.
Make one from a link to it (`[[private/alice/diary]]` on one of your pages: the missing link opens its editor), by moving a note there (Move to wiki, folder `private/alice`), or by linking to it from a private page (below);
on a private page `[[` completion offers a new private page (Wikilink autocomplete in `docs/spec/editor.md`); `/new` (the form without JavaScript) has a **Private** box too (`private=1` puts the slug in your folder; the box shows only with users).
Rename it out of the folder, or use **Make shared** in its ⋯ menu, to share it.
**There is no way to make a shared page private** (rename, Move to wiki, `rename_page` and a folder move all refuse: "a shared page stays shared"): others have seen it, and the pages that link to it would lose their links.
Create a new page in your folder and copy the text.
A new page is made private from the start (below).

- **Private home** (`go/private.go`): `/private` opens `private/<you>/index`,
  the hub of your own sub-wiki,
  and makes it on the first visit from a small template (`# Private`, a line that the pages in your private folder are hidden from the other users of this wiki, a `## Links` heading; committed in your name, `Create private/<you>/index`).
  It is an ordinary page: edit it, link your private pages from it (a missing `[[link]]` on it opens the editor), put what you like on it.
  Like the wiki's own `index`, it can't be renamed or deleted (`isProtected`: 400 in the routes, "can't be …" from `rename_page` and `delete_page`); should it vanish anyway, `/private` makes it again.
  Signed out it goes to the login; without users there is no private home (404, and `private/<x>/index` is an ordinary page).

- **Lock** (🔒, `.lock`): a private page shows it beside its title, in the search modal's page list (`private` in `/api/pages-list`) and on its search results (the result's meta starts with 🔒, `resultMeta`); shared pages and wikis without users show none.
- **Make shared** (the ⋯ menu of your own private page, `sharedMove` in `go/private.go`, `#page-privacy-dialog`): moves it out of your folder (`private/alice/notes/x` → `notes/x`).
  The dialog says where it goes and that everyone who uses the wiki then sees and changes it,
  and submits the ordinary rename (`POST /rename/<slug>` with `from=page`: links re-point on pages and in tasks, the uploads it shows are copied to `_assets/` (Uploads, below), one commit `Rename <old> to <new>`; a refusal such as "A page named … already exists" comes back in the Rename dialog).
  Not offered without users, for a shared page, another user's page, the wiki's home or a private home, or a note (Move to wiki takes those out of the folder).
- **Shared stays shared** (`refuseMakingPrivate`, `sharedIntoPrivate` in `go/pages.go`): a rename,
  Move to wiki (also of an old shared note from before users),
  an assistant's `rename_page`,
  a folder move,
  that would put a shared page into a private folder (yours, or no one's: `private/carol/…`) is refused before anything moves,
  with "it is shared,
  and others have seen it: a shared page stays shared".
  In the browser the rename dialog (and the editor's rename bar) comes back with the name typed and that error.
  Links therefore never have to be unlinked: a move *out* of a private folder re-points the links of your own pages and tasks, and of the shared pages that linked to it (marked 🔒 for you, below), in the rename's one commit.
- **A link from a shared page to your private page** (typed or pasted) is allowed, but marked for you (`.wikilink-private`: a 🔒 after it, tooltip "Links to a private page: others see plain text"; the live preview too): other users see plain text there.
  `[[` completion on a shared page, and in a shared task's title or notes, doesn't offer your private pages (a private task's does).
- **A new shared page from a private page** (`processWikilinks`' `from`, `sharedNewOffer` in `go/private.go`): on a page in your private folder a link to a page that doesn't exist and isn't private is marked (`.wikilink-shared-new`: a 🌐 after it, tooltip "Creates a shared page";
  the live preview marks them too, the editor sending `?page=` to `/api/preview`) and carries `?from=private` (`/page/<slug>?from=private`, kept by the redirect to `/edit/<slug>?from=private`).
  That editor (a new page only) shows a note above the text, **🌐 This new page is shared: everyone who uses the wiki will see it. For a private page, link to `private/<you>/<name>` instead.**, with **Keep shared** (just dismisses it, not remembered).
  To make it private, change the link (`[[` completion on a private page offers a new private page, or write the path); there is no button that moves a shared page in, because a page never saved has nothing to move and the link you clicked would stay.
  No note when the page is private already, a private page of that name exists, there are no users, or the link was on a shared page.
- **Private** is a module (`docs/spec/navigation.md`, Modules): in the switcher's first pill, Wiki · Private (the pages; Tasks · Notes, the tools, are the second), with a small lock as Notes has (`.module-lock`, tooltip "Private: only you see it"), and a tab on phones; **g p** goes to Private home.
  Private views (a page in your folder, the home included) make it current, notes make Notes current.
  **The user menu** holds who is signed in and **Sign out** (desktop; phones have them in the page's top row).
  It opens from the **user button** (`.top-user`, a round initial with the name as its tooltip) at the top right, after Search.
- **What "private" means**: hidden inside Zone, from other users and their assistants.
  Not encrypted, and not hidden from whoever has the wiki's folder or its git repository: the files, their history and a git remote hold everything.
  Search's keyword index (BM25) is made of the shared pages and tasks alone (`indexCache`);
  a user's own private ones are added to it for them (`Index.WithExtra`: their document count, frequencies and lengths join the shared ones at query time, so they rank exactly as if one index held what they see),
  while another user's words,
  counts and lengths take no part in anyone's ranking,
  nor in the vocabulary that prefix and typo matching draw on (else a typo that stops matching would tell that a word is in a private page).
  The semantic index holds every page's vectors, but a search looks only at the visible ones (pages, sections, tasks, Related, and the pages a new page is compared with).
  The query log (`query-log`) is the wiki owner's file in `.zone/`, never shown in Zone: with users it holds every user's queries and the private pages they found.
- **The rule lives in one place** (`go/access.go`): `pageOwner(slug)` (the user, and whether the page is private at all),
  `canSee(user, slug)`,
  `canWrite(user, slug)` (the same pages: everything shared is editable),
  `canSeePath(user, rel)` for files as git names them (pages, and a user's uploads and tasks in `private/<user>/_assets/` and `_tasks/`, which the private folder covers).
  Names compare in lower case, and a path is judged as it is opened, cleaned (`private/bob/../alice/x` is alice's).
  Without users nothing is private, and `private/` is an ordinary folder.
- **No one's pages**: anything under `private/` outside a user's folder (a page in `private/` itself, `private/alice.md` beside the folder, the folder of a user no longer there) is seen by no one; Zone logs each such entry when it starts (`strayPrivate`), so the admin can move it.
- **Opening another user's page** (view, editor, history, a version, a diff, the search preview) is a plain 404, the same as for a page that doesn't exist, for every slug in their folder (`refusePage`), so it doesn't tell what exists there.
  Sharing mode's "Hidden page" stays for pages `noshare` hides.
- **Changing another user's page** is refused the same way, 404 (`canWrite`; `refuseWrite` in the routes): save and auto-save, new page, rename, delete, restore a version, toggle a checkbox, commit one file.
  Renaming a page *into* another user's folder goes back to the dialog with an error ("that folder is another user's"), as does Move to wiki into one; Append to page and Move to wiki's "Link from" take only pages you may change ("There is no page …").
  Renaming a shared page to another shared place re-points links on every page, other users' private ones too, in one commit (Rename in `docs/spec/editor.md`); renaming one into your private folder is refused (Shared stays shared, above).
- **Notes are private**: with users the New note dialog says "New private note" and "Only you see it, until you move it to the wiki";
  a new note goes to the top of your folder, `private/<you>/<timestamp>_<name>` (`createQuicknote`, `privateFolder`);
  Notes lists only yours, under a 🔒 like a private page (an empty Notes says "Notes are private: only you see them, until you move one to the wiki"; `listQuicknote`, `inQuicknote`; so do Notes filter and `list_pages`/`search_wiki` with `notes`).
  A top-level note from before users stays a shared page: it opens by its link and shows in search and the page list, but is not in anyone's Notes (move it into your folder, or use Move to wiki).
  Move to wiki and Append to page take a note out of your folder (to the folder you name, the top level by default; to the page); an old shared note (from before users) Move to wiki'd into your private folder is refused, like a rename (Shared stays shared, above).
  `isQuicknote` takes a note at the top of a user's folder (not deeper, not in no one's folder, and without users only top-level notes).
- **Uploads** for a private page are private: the editor sends the page's slug with an upload (`page`, `markdownEditor`'s `page` option), and a file for a page in your own folder is kept in `private/<you>/_assets/` (`uploadDir`; URL `/private/<you>/_assets/<file>`, the folder's own path);
  any other page's uploads go to `_assets/` as before.
  `GET /_assets/<file>` serves `_assets/<file>` (a file straight in it, no subfolder), and `GET /private/<user>/_assets/<file>` only to that user (404 for anyone else, and for any other subfolder).
  **A page taken out of your folder takes the uploads it shows with it** (`shareUploads`, in the rename's commit): each `/private/<you>/_assets/<file>` it links,
  outside code,
  is copied to `_assets/` (your other pages keep their own; a name taken by a different file there gets `-2`, `-3` …) and the page points at the copy.
- **History and commits**: a page's history, its versions and diffs leave out versions from when the page was another user's private page, before a rename (`pageLog`, `canSeePath` on the version's path).
  The uncommitted list shows, and Commit all commits, only files you may see (`visibleUncommitted`): another user's draft stays uncommitted until they commit it.
- **Everywhere else** another user's private pages are left out as sharing mode leaves out pages not for sharing (`hiddenPage(rq, slug)`, which checks both; see Sharing mode in `docs/spec/navigation.md`): search,
  the search modal's list,
  Notes,
  Deleted Pages,
  the uncommitted list,
  Backlinks,
  Links to,
  Related,
  folder suggestions,
  link counts (the search modal's, "unlinked" in search: `allBacklinkCounts(rq)` counts links only from pages you may see);
  a wikilink to one renders as its words without a link.
- **Tests**: `tests/test_privacy.py` gives alice a private page (committed, deleted, uncommitted),
  a note,
  an upload,
  her own pinned list and list,
  and private tasks (open, closed, archived; one a shared page refers to with `[[task:…]]`),
  holding a word no response to bob may contain,
  and probes every route and MCP read tool as bob,
  and every write route and MCP write tool (her files and the commits must stay as they were);
  a route Zone registers, and an MCP tool, must be probed or listed as exempt there (`test_every_route_is_covered`, `test_every_tool_is_covered`).
  Further tests there cover what alice does with her own (notes, uploads, a new private page, history, Commit all, private tasks and their defaults); the Go tests in `go/tasklogic_test.go` the per-user task files.

## Private tasks

A task can be private: only its user sees it, on every task list, in search, on pages and to their assistant.
A new task is made private by ticking **Private** in the edit modal (it shows only with users); untick it later to share the task (the modal says that everyone then sees and changes it).
**A shared task can't be made private**: its box is disabled, and the API and `update_task` answer 400 "A shared task stays shared".
**Where a task is kept is the one thing that decides who sees it; its tags never do.** A new task starts private where that is what you'd expect: from an embed on a page in your private folder,
from `/tasks?list=` of a list declared there,
from the editor's Make task on a private page,
from New task / `c t` when your default list is in your private folder.
Cards of private tasks show 🔒 (`.task-private`).
Everything else (shared tasks) is everyone's to see and change, as shared pages are.

- **Storage**: shared tasks stay in `_tasks/tasks.json`, `closed.json`, `closed-YYYY.json`; a user's private ones are in the same three kinds of file in `private/<user>/_tasks/` (`taskFiles.Dir(owner)`), created on their first private task.
  A task's file says whose it is: the task's `Owner` (`go/internal/taskmodel`) is not written into it, it is set from the folder a task was read from (`eachOwner`), and the API shows it as `"private": true|false` (only with users: without them the API's JSON is as before).
  Toggling Private (`private` in `POST`/`PUT /api/tasks`, `batch-update`) moves the task between the files in one commit.
  Ids are random UUIDs, unique across all files; restoring a deleted task (undo) never takes an id another file has (`taskFiles.IDsLocked`) and keeps it private.
- **Reading and writing** (`go/internal/taskfiles`, the store `taskFiles` in `go/tasklogic.go`): every reader takes the user and reads the shared files and theirs (`Owners(user)`: `Open`, `All`, `Snapshot`, `ReadForWriteLocked`, `ClosedInYear`, `ClosedYears`, and on top of them `findTasks`, `resolveTaskRefs`, `findTaskBacklinks`);
  Zone itself (`""`) reads only the shared ones.
  A write splits the tasks by owner (`byOwner`) and writes the files that changed, all in one commit; a task can only be written by its own user, since another user's is never read for them.
  One lock (`taskFiles.Lock`) for all files, taken by reads across them too (`Open` for `GET /api/tasks`, `All`, `Snapshot`): a task made private or shared moves between two files, and a read between their writes would miss it.
  What Zone does over every task list covers every user's files (`EveryOwner`): the yearly archive at start (`ArchiveOldClosed`), a rename's links in tasks (`renamePageRefs`), the search index (`Every`, filtered per user by `canSeeTask`, `hiddenDoc`).
  A series' next occurrence stays private (`spawnRecurring`).
  A private task's pasted files are private too (the description's editor sends `private/<you>/…` as the upload's page).
- **Another user's private task is no task**: by id the routes answer 404 as for an id no task has (`DELETE /api/tasks/<id>` of an unknown id stays a 204, as before);
  lists, counts, Closed and its years, the due count, Linked tasks and the section chips leave it out;
  a `[[task:…]]` to it renders as for a deleted task.
  A shared page that refers to a private task doesn't carry its title into the search index (`taskRefsAsTitles` resolves a page's references as its owner, or for a shared page as no one).
- **Lists and pins are per user**: a list declared on a page in your private folder (`[[tasks: list=…]]`) exists only for you (`taskLists(user)`); for others its name is a plain tag.
  When you and a shared page both declare one, yours is its home for you (`taskLists`: your own declaration first, A–Z, else the first shared one A–Z), whatever the pages' names; for the others the shared page is.
  Likewise a `pin=1` embed in your private folder pins its list for you alone, ahead of shared pins (`lookupPinnedIn(…, user)`), so with no `pin=default` your own list is the default one, and the Tasks link, its counts and New task's tags are your own.
  Pins on shared pages are everyone's: a list pinned there is pinned for every user.
  **So one list name can start tasks in different places for different users**: when alice declares `gifts` in her folder and a shared page declares it too, a new task in `/tasks?list=gifts` starts private for her and shared for bob.
  The tasks she tags `#gifts` that are shared show in the list for everyone, her private ones only for her.
- **Sharing a task**: unticking Private (`private: false` in `PUT /api/tasks`, `batch-update`, `update_task`) moves your task from your files to the shared ones, in one commit;
  it is an event in the task's History (`shared`, by who did it; older tasks may show `private` events from when tasks could move the other way).
  `private: true` on a task that is shared answers 400; it is accepted only when a task is created.
  The tasks' undo of an edit doesn't restore the Private box, so it never tries to take a shared task back.
- **Private lists are a location, not a tag**: a list declared on a page in your private folder is yours alone (Lists and pins are per user, above), and a new task made from `/tasks?list=` of that list, or from an embed on that page, starts private.
  A tag never moves a task: adding `#gifts` to a task, in the modal, a batch or `update_task`, leaves it where it is; creating one with the tag shared, shared.
  The tag of your private list on a shared task shows to others as a plain tag.
- **History**: `private/<user>/_tasks/` files show only to that user in history views (`canSeePath`); the commit messages ("Update task") name no task.

## Assigning tasks

A shared task can be assigned to one user: who does it.
One, not several, so that "who has this?" has one answer; someone helping goes in a comment or a tag.
It is information: it never changes who sees a task or who may change it, and anyone who may change tasks may assign any task to anyone, themselves included, or to no one.

You assign a task in its dialog or by ending a new task's title with `@name`, see who has it on its card, and filter a list by it (`@anna`, `@me`, `@none`).
A page can show each user their own tasks with `[[tasks: assignee=me]]`, several selected tasks are assigned at once, and an assistant assigns and asks for "my tasks" through the MCP tools.
Not there: telling someone that a task became theirs, a group as an assignee, and rules that go by the assignee (who may close a task).

- **The field** is the task's `assignee`, a user's name, kept in its file after `tags` (`Task.Assignee` in `go/internal/taskmodel`; Task fields in `docs/spec/tasks.md`).
  A task without one has no such key.
- **Assigning** is a task write like any other: `assignee` in `POST /api/tasks`, `PUT /api/tasks/<id>` or `batch-update` (`applyAssignee` in `go/tasks.go`).
  The value is a user's name, `me` for whoever writes, or `""`, `null` or `none` for no one; a leading `@` and the case don't matter (`taskmodel.CleanAssignee`).
- **Who can be assigned** (`assignable` in `go/users.go`): a user who is not disabled, not waiting for approval and not a viewer (who couldn't complete the task or comment on it).
  Any other name is refused with a 400 that says why (`notAssignable`: "… is not a user of this wiki", "…'s account is disabled", "… is a viewer, and a viewer can't change tasks"), and nothing of the write is kept.
- **`me` and `none` are reserved names**, since they are the words for whoever is signed in and for no one.
  No new user gets one (`zonewiki user add`, an invite, sign-up, the first-run page: "… can't be a user name").
  A user who has such a name from before keeps their account (signing in, pages, private tasks) but can't be assigned, and Zone says so in its log at start.
- **A private task has no assignee**: it is its owner's, and nobody else sees it.
  `assignee` on a private task is a 400 ("A private task is yours alone. Share it to assign it"); a write that shares a task (`private: false`) can assign it at once.
- **Only a change is checked.** A write that names the assignee the task already has changes nothing and is never refused.
  So a task keeps the name of a user who was disabled, removed or made a viewer since, through every other edit: the name stays in the file and in the API's answer, and can be taken off or replaced.
  A name the task had before is taken back as it is (`hadAssignee`: its history says so), so undoing the change that replaced a former assignee works; on any other task that name is refused.
- **A former assignee counts as no one.** A task whose assignee can no longer be given tasks matches `@none` as well as the name, so it comes up where the team looks for what nobody has, and can still be found by the name to be handed on (`TasksCore.assigneeIs`).
  Its card and the dialog show the name struck through.
  Nothing unassigns such tasks on its own: disabling is undone by enabling, and the settings file is also edited by hand, with no one to name as a rewrite's author.
- **History**: a change is the event `assigned`, with `from` and `to` (the names, `null` no one) and `by` whoever did it (History in `docs/spec/tasks.md`).
  A new task's first assignee is part of `created`, as its first dates are.
  An undo that sends the task's old `history` leaves no trace, as for the dates.
- **A series** carries its assignee on: a recurring task's next occurrence has the same one (`spawnRecurring`).
- **Restored and imported tasks** (`POST /api/tasks/restore`, `/api/tasks/import`; `sanitizeSnapshot`) have an assignee only as a write would take it: a name that can be assigned here, on a task that isn't private.
  So an export from another wiki doesn't bring its people along; the import's toast says how many tasks were assigned to people who are not users here (`unassigned` in the route's answer).
- **Assistants** (`docs/spec/mcp.md`): `create_task` and `update_task` take `assignee` (a user name, `me` for the assistant's user, `none`), `search_tasks` filters by it (`assignee=me`: "what do I have to do"), and a task's text says who it is assigned to.
- **In the dialog** (`js/taskmodal.js`): the **Assignee** field, after Tags.
  It shows when the task has an assignee; on a task that has none, **Assignee** in the line under the fields ("+ Assignee") shows it and goes to it, as the dialog's other rarely set fields are asked for (Interaction in `docs/spec/tasks.md`).
  Type `@` (everyone, yourself first) or the start of a name; Tab or Enter completes it to a chip, and another name replaces it; the chip's × or Backspace takes it off.
  `me` is yourself.
  A name that is no user who can be assigned is not saved: the dialog stays open and the field says so.
  A private task has no field and no "+ Assignee": ticking Private hides both.
  A new task starts with the one user the filter names (`@anna` or `@me`; `filterDefaultAssignee`), as it starts with the filter's tags.
- **In a new task's title** (`TasksCore.parseQuickAdd`): `@name` at the end, among the other shorthand ("Call Sam @bob #work tomorrow"), with completion as you type it.
  Only the name of a user who can be assigned, or `@me`; anything else stays in the title (`bob@example.org`, `@home`), as does a second name.
  Not on a private task.
- **On the card** (`.task-assignee`, after the tags): `@anna`, your own name in the accent colour, a former assignee's struck through.
- **In the filter** (`TasksCore.parseFilter`/`matchesFilter`, given `people`): `@anna`, `@anna|bob` (either), `-@anna` (not hers), `@me` (yours), `@none` (no one's, a former assignee's included).
  A completed word becomes a chip, as a tag does; the suggestions are `me`, `none` and the users who have tasks among those the other words leave visible.
  `@` on the list focuses the field and starts a name.
- **On a page**: `[[tasks: assignee=me]]` shows whoever looks at the page their own tasks, `assignee=anna` hers, `assignee=none` those no one has (a former assignee's included); `a|b` either's, `-a` not theirs, and it joins `tag=` and `list=` (Task list embed in `docs/spec/tasks.md`).
  The chip is locked there, as a `tag=`'s is.
  A new task in such a list is assigned to the one user it names (`me`: whoever makes it), so it shows up where it was made; `a|b`, `none` and `-a` give it no one.
  The same for **New task** from any page when the default list has `assignee=` (`newTaskAssignee`, `<body data-tasks-assignee>`).
  List settings has it under More, "Shows only the tasks of".
  `/tasks?assignee=…` filters the same way, with an ordinary chip.
- **The counts** on the Tasks links and the list switcher count what each user's lists show them: `assignee=me` is whoever asks (`assigneeParamMatches` in `go/tasksyntax.go`, the server's mirror of the filter).
  An export of such a list holds the tasks it shows (`/api/tasks/export?assignee=`).
- **Several at once** (`openBulkAssignModal` in `js/taskselect.js`): **Assign…** in the selection bar and in the menu of a selected card, or **a** on the selected tasks (one is enough).
  The dialog lists the users who can be assigned, yourself first, then **No one**; the row of who has them all is ticked, one who has some says "1 of 4".
  A click (or ↑/↓ and Enter) assigns them in one `batch-update`, and the toast ("3 tasks assigned to bob") has one Undo for all, which leaves no trace in their histories.
  Private tasks among the selection are left alone.
- **Handing on** the tasks of someone who can no longer be given any: the admin page's Users tab says under a disabled user's (or a viewer's) status how many open tasks they still have, "2 open tasks to hand on", linked to `/tasks?view=open&assignee=<name>`; select them there and Assign….
  `zonewiki user disable` and `user remove` say the count too (`handOnNote`).
  Until then they show under `@none`.
- **The History** reads "Assigned to bob (was anna)" and "Unassigned (was bob)".
- **The names** come with the page's tasks: a page view with a task list, and `/tasks`, carry them in the data block that brings the open tasks along (`"people"` in `tasksInitial`; `setPeople` in `js/taskutil.js`), so the list is drawn with no request.
  Any other page (New task from anywhere, the editor's Make task) asks once, `GET /api/tasks/assignable` (`loadPeople`; `people`): `{ "users": [...], "me": "<who asks>" }`, the users who can be assigned, A–Z, for anyone signed in (a viewer too: they see the names on the tasks anyway).
  In sharing mode `users` is empty.
- **Without users** there is no one to assign: a write with an assignee is a 400 ("This wiki has no users to assign a task to"), the route's `users` is empty, and the API shows no `assignee`.
  A file that has one (a wiki that had users) keeps it through reads and writes.

## Who made a change

- **Commits**: every commit Zone makes for a user's action names them as its author, `alice <alice@zone>` (`gitCommit(user, …)`); the committer stays Zone.
  Commits run one at a time (`commitMu` around `git add` and `git commit`), and each commits only its own paths, so one user's files never go into another's commit.
  Commits Zone makes on its own (start-up, archiving the tasks) are Zone's: the wiki's git committer is `Zone <zone@localhost>` (`setCommitter`), and the history shows no name for its commits (`commitAuthor`).
  **A draft is committed in its author's name**: Zone remembers who auto-saved a page's uncommitted text (`drafts` in `go/merge.go`, in memory: the editor's auto-save and a ticked checkbox note it, a commit of the file forgets it).
  Before another user's save,
  Done or checkbox tick on that page (and before a write that changes other pages on the way: a rename re-pointing links, a folder move, Append to page, `commitOthersDrafts`),
  the draft is committed first as its author's `Save <slug>` (`commitOthersDraft`),
  so it is in git under their name,
  not in the other's commit;
  one person's own auto-saves stay uncommitted.
  A write that replaces or removes the page's text (a delete, rename or restore, Keep mine, Reload theirs, and every assistant's write) commits whatever the page holds uncommitted first,
  the writer's own draft too,
  so it is never lost and the write's own commit holds only its change (`keepPending`, `keepPendingPages` in `go/pageops.go`: one commit per author);
  Commit file and Commit all commit a draft as its author too (`commitAuthorOf`).
  Text whose author Zone doesn't know (edited on disk, or auto-saved before a restart) is committed as before: by the one committing, or by Zone for an assistant's write.
  **In another user's private folder the folder says whose it is**: a rename,
  folder move or Append that rewrites links first commits every uncommitted file there in its owner's name,
  one commit per owner (`Save private/alice/diary`, or `Save 5 files in private/alice`; `commitOthersDrafts`),
  so a rename of a shared page never carries their unfinished text in its commit.
  Without users all commits are Zone's, as before.
- **History**: a page's history shows the author after a version's label ("Edited · alice", `.hist-by`; `GitEntry.Author`, `commitAuthor`), and the MCP `history` tool adds "(by alice)" to a version's or a change's message.
  Zone's own commits, and every commit in a wiki without users, show no name.
- **Tasks**: comments and History events record who wrote or did them (`by`, see Comments and History in `docs/spec/tasks.md`): the cards and the edit modal show "12 Mar 09:30 · alice" (`commentStamp` in `tasks.js`), MCP's task text names a comment's author "(alice)".
  Editing a comment keeps its author.
  Any user may edit or delete any comment on a task they can see, as they may change the task itself.
  **Nobody writes under another's name** (`go/taskby.go`): the routes that take comments or history whole (`PUT /api/tasks/<id>` and `batch-update` for an undo, `/api/tasks/restore`, `/api/tasks/import`) get them from the browser, which could say anything.
  A `by` that is the sender's own stays;
  another user's stays only on a comment (by its id) or a history entry (of that task, at that time, with those dates) Zone has seen under that name: it notes them as it deletes a task or a comment and before a request replaces a task's lists (`rememberBy`, in memory, the newest 20 000),
  so the undo that sends them back is believed.
  Any other name is dropped (`checkBy`: by no one), also an undo after a restart.
  An admin's import keeps the file's names (a wiki moved, a backup put back).
  `TestAuthors` in `tests/test_users.py`.
