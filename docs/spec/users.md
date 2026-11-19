# Users

Part of Zone's spec (see `AGENTS.md` for the index).

Several people on one wiki: each signs in with their own name and password, their changes are committed in their name, and an assistant connected with their MCP token acts as them.
Everything in the wiki is shared and editable by every user, except each user's private folder, `private/<name>/`, and their private tasks, which only they see.

Users are `[user NAME]` sections of the wiki's settings file, `.zone/config` (below).
Without them none of this applies: one password (or none) and one MCP token, and Zone commits as itself.

## In short

Three rules; the rest of this file is how they are kept.

1. **A folder is yours, a group's, or everyone's.** `private/<you>/` holds your private pages, your uploads (`_assets/`) and your private tasks (`_tasks/`): no one else sees it in Zone, in search, in history, or through their assistant.
   `groups/<group>/` holds a group's pages and their uploads: its members see and change them, and to anyone else they are not there (Group pages, below).
   Everything outside the two is shared, and every user may change it, but a viewer, who changes nothing (Roles, below).
   (Hidden inside Zone, not encrypted: whoever has the wiki's folder or git repository has it all.)
2. **Where something is kept is who sees it.** Not its tags, not its links, not its name.
   A new page starts where it is made: from a link to `private/<you>/…` or `groups/<group>/…`. A task is kept where its list is: one in a private list of yours in your folder, any other with the shared tasks.
3. **To more people by whoever may write; to fewer by an admin.** A private page goes to a group you are in or to everyone, a group's page to a group that includes its own or to everyone, by a rename out of the folder (Share…); a private task by moving it out of its private list (or making that list shared).
   The screen says who will see it, and asks.
   The other way takes something from people who have it, so it is not everyone's move: a page goes to fewer people by an admin, or by whoever shared it while nobody else has changed it (the mistake of a minute ago); a task into a list fewer people see by who made it, or an admin; a list is narrowed by its owners, or an admin.
   The screen names who will no longer see it, and asks (Who may move a page to other readers, below).
   Never the other way: others have seen a shared page or task, link to it and have comments on it, so Zone refuses to take one into a private folder or tasks, or a group's page from its group (the error says so).
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
groups = dev
disabled = yes

[group product]
includes = dev, design
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

- **Format**: `key = value` (spaces optional), the wiki's settings before the first section, a `[user NAME]` section per user with `password`, `mcp-token`, `role`, `groups`, `disabled`, `email` and `pending` (`configKeys`, `userKeys`; Roles and Sign-up, below),
  and a `[group NAME]` section for a group that says more than its name: `includes`, the groups it is made of, `owners`, the users who say who is in it, and `was`, the names it went by before, which Zone writes (`groupKeys`; Groups, below).
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
  a section other than `[user NAME]` and `[group NAME]`,
  a user's key outside a section or the wiki's inside one,
  a group that includes itself,
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
./run.sh user group carol +dev -hiring # put her in the group dev, take her out of hiring; alone: her groups
./run.sh group includes product +dev +design  # a group made of groups: who is in dev or design is in product; -dev takes one out
./run.sh group owners apollo +anna -bo  # who says who is in the group, in the browser (Groups); alone: its owners
./run.sh group list                    # the groups: what each includes, its owners, and who is in it
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

- Without the container it is `zonewiki user …`, `zonewiki group …`, `zonewiki config …` with `ZONE_DATA` set (`go/commands.go`: `userMain`, `groupMain`, `configMain`); on a NAS, through the container: `docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki user add alice`.
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
  `me` and `none` are reserved (`reservedNames`): no user is called one. The settings file is refused with such a section, naming its line (`fileUsers`), and `user add`, an invite, sign-up and the first-run page say so before they write (Assigning tasks, below).
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

### Groups

**Who belongs together is said once**: on the users, or by the groups a group is made of.

`groups = dev, hiring` in a user's section puts them in groups (`User.Groups`, `parseGroups` in `go/users.go`; commas or spaces between them, a leading `@` allowed, written A–Z).
A group is its name (lowercase letters, digits, hyphens; `groupNameRE`): there is no list of groups to keep, a group exists while someone is in it (`Settings.groups`), and one nobody is in has no members, which is no error.
A name that is none is an error of the settings file, which then keeps the settings from before.

- **Set them** with `zonewiki user group NAME +dev -hiring` (alone it says which groups the user is in; `user list` names them: `bob (in dev, hiring)`), in the Users tab's **Groups** field (below), or by hand in the file.
  The field takes only groups there are (and the name of a folder in `groups/` no one sees, which gives it its people again); a new group is made on **Groups** (A group of your own, below).
- **What a group is for**: what is for some and not for all. `memberOf(user, group)` asks; a disabled user is a member of none.
  A task list can be for groups (`"for": ["@hiring"]`: Lists for some, in `docs/spec/tasks.md`), and a user put in a group or taken out sees its lists, or no longer, at their next request.
- **A role is not a group**: an admin is in no group by being admin, and sees nothing of a group they are not in. They may put themselves in one, which the log of the settings shows ("alice's groups").
- **Who makes one**: an admin, as above, or anyone who may write, in the browser (A group of your own, below).

**A group made of groups**: when two teams share something, don't name everyone twice. Give the larger group a section that says which groups it is made of:

```ini
[group product]
includes = dev, design
```

Whoever is in `dev` or in `design` is in `product`, at once and for as long as they are, so someone who joins `dev` next month has what is for `@product` without anyone thinking of it.
A list for both teams is then for one group (`"for": ["@product"]`).

- **It goes through**: `company` may include `product`, and whoever is in `dev` is in all three. Who has `product` named on them (`groups = product`) is in it as before, and not thereby in `dev`.
- **A group that includes itself**, directly or by way of others, is an error of the file naming its line ("[group a] includes itself, by way of b"; `includesItself` in `go/configfile.go`), as is a name in `includes` that is no group's.
  A name nobody is in is none: a group made of empty groups has no members.
- **The section has the keys `includes`, `owners` and `was`** (`groupKeys`; owners and was: A group of your own, below), and any other is an error. A group without a section is its name on users, as above.
- **Set it** with `zonewiki group includes product +dev +design` (`-dev` takes one out; alone it says what the group includes and who is in it now; `zonewiki group list` says it of every group: `product includes design, dev: alice, bob`; `groupMain` in `go/commands.go`), on **Groups**, for an admin (An admin on Groups, below), or by hand in the file.
  Taking the last group out removes the key, and the section when it says nothing else (`setGroupKey`). The settings' log says it ("product includes design, dev").
- **Taking a group out is a narrowing**, like taking a user out of a group: its people no longer see what is for the larger group at their next request, and their tasks in its lists count as no one's (Lists for some in `docs/spec/tasks.md`).
  The command and the page say who that was ("No longer in product: bob. What is for @product they no longer see."); putting the group back gives it all back.
**A group of your own** (`go/groups.go`, `groups.html`): a project is six names from three teams, and nobody should wait for an admin to have a place for it.
On **Groups** (`/groups`: the user menu's entry, **All your groups** in the pill's menu, a link in the shortcuts dialog) whoever may write makes a group: a name, and who else is in it.
They are in it and are its **owner**; from the next request `groups/<name>/` is its people's and a task list can be for `@<name>`, as with any group.

- **Who is in it is still said on the users**: making the group writes its name into the `groups` of each of its people, and `owners = <you>` into a section `[group <name>]` (`makeGroupOp`, `setMembers`). So `memberOf`, a group made of groups and the Users tab's Groups field hold for it unchanged, and an admin sees and sets it like any other.
- **Names are chosen, not typed blind** (`js/namepick.js`): **People**, **Owners**, **Who else is in it** and an admin's group fields are pills, one per name, with × to take one out.
  Typing offers the users (or groups) there are that start with what is typed, then those that hold it; Enter, Tab, a comma or a click picks the one marked, ↑/↓ move, Backspace in an empty box takes the last pill out, and a pasted list becomes pills.
  What names nobody stays in the box, marked ("No user called x"), and the form isn't sent with it.
  The field itself is hidden and holds the names (`anna, bo`), so the form sends what it always did; without JavaScript it is a text field, and the server refuses a name that is no user's or no group's either (`usersNamed`, `groupsNamed`, against `knownGroups`).
- **Its owners say who is in it** (`Settings.Owners`, `ownerOf`, `managesGroup`): on `/groups` a group you own has **Manage…**, with **People** (the users named on it, `directMembers`; who is in it by way of a group it includes isn't listed and isn't this field's), **Owners**, and **Save** (`POST /groups/<group>/people`, `groupPeopleOp`).
  An owner's change touches that one name on the users and the group's `owners`, nothing else of the settings file.
  Taking someone out is a narrowing, as an admin's is: they no longer see the group's pages and lists at their next request, and the page says who that was.
  A group keeps an owner (the last can't take themselves out; they name who takes it over first). An owner needn't be in the group: they then see none of its pages.
  A removed user owns nothing (`dropOwner`), and a name in `owners` that is no user is none.
- **Taking someone out says whom first** (`js/admin.js`): a Save that takes names out of People (Manage…), or groups off a user (the admin page), turns into "Yes, take out bob" and the second click saves, as Leave… asks; what is for the group is theirs no longer at once. Without the script the line under the field says it.
- **An admin manages every group**, owned or not (An admin on Groups, below), the admin's Groups field on a user works as before, and `zonewiki group owners NAME +anna -bo` gives a group owners or takes them (none left: the group is an admin's alone again, as one made on the host).
- **A name that is taken is refused** (`groupNameTaken`): a group there is, a name a group is said to include, a folder in `groups/` (a group nobody is in keeps its folder, and a new group of that name would get its pages), or a name a task list is for.
  **And a name a group went by before**: the history of `groups/<name>/` (the versions and deleted pages of a group renamed since, which are read under that path by who is in a group of that name) and a deleted list that was for it stay its former group's, so a new group of the name would be shown them: the name stays taken.
  The group that went by it takes it back (`was = apollo` in its section, which a rename writes: the names it had), so a rename can be undone.
  On the host nobody is asked: `zonewiki user group` and a hand edit of the file make a group of any name.
  The groups' names are no secret among a wiki's users (a list's Who it is for… completes from them), so "taken" tells nothing new.
- **Not for a viewer** (who changes nothing: 403), a visitor, or a wiki without users (404).
- **A member leaves by themselves** (`mayLeave`, `leaveGroupOp`; `POST /groups/<group>/leave`): on `/groups` a group that is named on you and that you don't manage has **Leave…**, which asks first and then takes the group's name from your groups, yours alone.
  You no longer see its pages and lists from your next request; its owners can put you back.
  Not a group without owners (it is an admin's, and who is in it is the admin's to say), not one you are in only by way of a group it includes (there is nothing of it on you to take off: you leave that one), and not for who manages it (Manage… is their way, where a group keeps an owner).
- **What it doesn't do**: change what a group includes (an admin's).

**An admin on Groups**: `/groups` is the one page for groups, and an admin's shows more (`groupsView.Admin`).

- **Other groups** (`#others`), under their own: every other group there is (`knownGroups`: someone is in it, it has a section, or a group includes it), each with who is in it and by which way, "owned by anna" when it has owners, "has pages" when its folder is there, and **Manage…**.
  The admin sees who is in them, not their pages: there is no link to a group's home they aren't in.
- **Manage…** of every group has, beside People, Owners and Name, **Everyone in**: the groups it includes (pills of the groups there are, not itself; empty: none, and the key goes), **Save** (`POST /groups/includes`, `group`, `includes`; `groupIncludesRoute`, admins only, 404 for anyone else).
- **A group of groups** below: a new group's name and the groups it includes (**Make a group of groups**, the same route with `new=1`). It makes a group and changes none: a name a group has is refused ("There is a group product already. What it includes is under its Manage…."), as is one that is taken (`groupNameTaken`).
  A group that would include itself, or a name that is no group's, is refused and nothing changes.
  Done, the page says who is in the group now, and who no longer is (`?done=includes&group=…&left=…`, of which it says only users' names).
- **Seen by no one**: the folders in `groups/` of a group nobody is in (`strayGroups`); putting someone in that group (their Groups field on the admin page) gives the folder its people again.

**A group renamed** (`renameGroupOp`): **Rename** under Manage… on `/groups`, for its owners and an admin (`POST /groups/<group>/rename`, `name`).
What is the group's by its name goes along, so nobody loses anything: the lists that are for `@old` are for `@new`; the folder `groups/old/` becomes `groups/new/`, every link to its pages re-pointed on pages and in tasks, the files its pages show and those no page shows along, and a home that still says what Zone wrote says the new name; then the settings name it anew, on the users, in the groups that include it and its own section.
The order is that one: until the settings change nobody is in the new name's group, so what has moved is seen by no one for that moment and never by more.
**A step that fails** puts the ones before it back and the rename stops, everything as it was (a page of the group with text not in git yet that can't be committed first: the lists, which were for the new name already, are for the old one again).
**A step that is made but for its commit** counts as made (`writtenAnyway`): the rename goes on to the settings, or the group's pages were in a folder no group is named as, seen by no one. The page then says both, that the group has its new name and that the change is written but not committed (500), and the files are in the Uncommitted list, as after any write whose commit failed.
It is up to three commits (the lists, the folder with its links, the files no page shows), by whoever renamed it; an admin who is not in the group moves its pages without seeing any.
The new name must be free, as a new group's, or one the group went by before (`was`, which the rename keeps: A name that is taken, above). There is no command for it: a rename writes pages and task files, which only the running server does (One process, in `AGENTS.md`).

**A group ended** (`removeGroupOp` in `go/groups.go`): **Remove** at the foot of Manage… on `/groups`, for its owners and an admin (`POST /groups/<group>/remove`).
A group that was made by mistake, or has done its work, goes: its name comes off its people, out of the groups made of it, and its section goes.
**Its pages can't stay where no one would see them**, so with anything in its folder Remove asks what becomes of it, and one of the two is chosen before the button does anything:
- **Move the pages to the wiki** (`pages=move`, `folder`): `groups/dev/…` becomes `dev/…`, a folder of the shared wiki whose name can be typed (the group's by default; `groups/dev/roadmap` → `dev/roadmap`, the home → `dev/index`), every link to the pages re-pointed and the files they show copied along (`moveFolder`, `shareUploads`). The form says how many pages everyone who uses the wiki will see: it is a move to more people, asked as such.
  A folder of that name in the wiki already is refused ("give the pages' folder another name"), as is one in `private/` or `groups/`, or one Zone skips.
  Files in the folder that no page shows go with the folder; they stay in git.
- **Delete the pages** (`pages=delete`): the pages and their files are deleted, in one commit ("Remove the group dev: its pages"). They can't be brought back in the browser (Deleted Pages goes by the path, and nobody is in a group of that name), and stay in git on the host. Links to them are links to pages there aren't.

A group without a folder is removed without a question.
**A task list that is for the group keeps it** (`groupKept`): a list has its own owners and its own For, so the page names the lists (those you see; the others counted) and says to change whom they are for, or delete them, first. **A group that another is made of** is an admin's to remove (what a group is made of is an admin's): an owner is told so.
**The order** is the pages first, the settings last, so nothing is in a folder no group is named as while the group is still there. A step that is made but for its commit counts as made (`writtenAnyway`): the removal goes on, and the page says both that the group is removed and that a change isn't committed.
**Its name stays taken** while its folder has a history (A name that is taken, above): a group that never had a page frees its name.
There is no command for it, as for a rename.

- **How it is kept**: `Settings.Includes` is what each section says; `joinGroups` works out once, when the settings are read, every group each user is in (`User.In`), and `memberOf` looks there, so asking costs what it did.

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
  **A guide in a group's folder** (`groups/dev/AGENTS.md`, and what it pulls in) steers the assistant of everyone in the group, so it is an admin's too, and of the admins only one who is in the group sees it: the others in the group read it and are told "Only an admin of this wiki who is in the group can change this page … Ask an admin to join the group." (`groupGuideMsg`).
  A group with no admin in it has guides nobody changes until one joins.
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
- **Per user**: change their role (a select and Save),
  set their **groups** (pills of the groups there are, as on Groups, `js/namepick.js`; **Save**; none takes them out of every group; `POST /admin/users/{name}/groups`, `userGroupsRoute`, the field `groups` as the file writes them, `dev, hiring`; a name that is no group there is, or a folder in `groups/` no one sees, is refused and nothing changes: a new group is made on Groups),
  below the field, "also in product" names the groups they are in by way of those (`userRow.Also`);
  **Disable** or **Enable**,
  **Reset password** (makes a link),
  **Remove…** (a dialog that says what happens, as deleting a page does, then **Remove NAME**;
  `js/admin.js` opens it with what the row's `<details>` holds, and without JavaScript the `<details>` unfolds instead: their private pages, notes and tasks stay in `private/NAME/`, seen by no one, and someone added again under that name gets them back;
  an open link of theirs goes too).
  There is no field to type another user's password: the admin never knows it.
- **Groups are on Groups**: below the users a line says that a user's Groups field takes the groups there are, and that groups are made, renamed and made of other groups on **Groups** (`/groups`), where an admin manages every group (An admin on Groups, above).
  The "Seen by no one" note here names the folders in `private/` of a user who is gone (`strayPrivate`); those in `groups/` are on Groups.
- **Not yourself**: the admin's own row ("(you)") has no Disable or Remove…, and a POST that disables or removes the admin themselves is refused ("you can't … yourself here. Another admin can.").
  A slip there would lock them out of the wiki, and it would only be allowed with another admin left, who can do it.
  They can still change their own role, to step down while another admin remains.
  As the last user is always the admin themselves, this is also why the last user can't be removed here.
- **Plain forms**: each change is a POST (behind the Origin check, like every write), and the page works without JavaScript, which opens the Remove dialog and keeps the list quiet:
  **a row's Save buttons show once the field beside them is edited** (`js/admin.js`, `.saves-when-edited`; hidden, not gone, so nothing moves), and hide again when the edit is taken back (a pill picked or taken out counts). Picking a role saves nothing by itself; Enter in a field saves as its button does (in the Groups box, Enter with nothing typed). Without JavaScript the buttons are there all along.
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
  So is the name of a user who was removed (`userNameHasHistory`): their private folder is there or in the history, or a commit names them as its author.
  Removing a user keeps their private pages, tasks and lists, and their comments and history, so a stranger signing up under the name would get all of it; an admin may still give the name again (`user add`, an invite).
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
  The dummy hash has a real one's iterations but zeros for salt and key (`dummyHash`), so Zone doesn't spend the 600,000 rounds making it at every start.
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
- The user's name is shown beside Sign out (`.signout-user`, "Signed in as alice") on phones, in the page's top row, and **opens the account menu there** (Groups, Connect an assistant, Change password: what the row itself doesn't have; so a group is made, a token made and a password changed from a phone too).
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
  `create_task` and `update_task` take `list`: a task in one of the user's private lists is theirs alone, any other shared; `create_task` without it puts the task in the user's Inbox, as `c t` does, where it is theirs alone. A task with an assignee needs a shared list: without one `create_task` is a tool error that names them. `update_task` shares a private task only with `share: true`, and never takes a shared one into a private list or the Inbox. `private` is no argument anymore (the tool says to give `list`).
  The task text says "private" among its facts.
- **Guides**: a `CLAUDE.md`/`AGENTS.md` in another user's folder isn't a guide for you (`folderGuides(slug, user)`, `canSeePath`), not listed in read_guides' description (`toolList(user)`), and a guide's `@import` of a page you can't see stays as written (`guideText`).
  `rename_page` of a folder holding pages you may not move (`private/` itself) moves nothing.

## Private pages

Pages in `private/<name>/` (and its subfolders) are that user's: only they see and change them.
Make one from a link to it (`[[private/alice/diary]]` on one of your pages: the missing link opens its editor), by moving a note there (Move to wiki, folder `private/alice`), or by linking to it from a private page (below);
on a private page `[[` completion offers a new private page (Wikilink autocomplete in `docs/spec/editor.md`); `/new` (the form without JavaScript) has a **Private** box too (`private=1` puts the slug in your folder; the box shows only with users).
Rename it out of the folder, or use **Make shared** in its ⋯ menu, to share it.
**A shared page is made private only by an admin, or by whoever shared it while nobody else has changed it** (Who may move a page to other readers, below): others have seen it, and the pages that link to it would lose their links.
Create a new page in your folder and copy the text.
A new page is made private from the start (below).

- **Private home** (`go/private.go`): `/private` opens `private/<you>/index`,
  the hub of your own sub-wiki,
  and makes it on the first visit from a small template (`# Private`, a line that the pages in your private folder are hidden from the other users of this wiki, a `## Links` heading; committed in your name, `Create private/<you>/index`).
  It is an ordinary page: edit it, link your private pages from it (a missing `[[link]]` on it opens the editor), put what you like on it.
  Like the wiki's own `index`, it can't be renamed or deleted (`isProtected`: 400 in the routes, "can't be …" from `rename_page` and `delete_page`); should it vanish anyway, `/private` makes it again.
  Signed out it goes to the login; without users there is no private home (404, and `private/<x>/index` is an ordinary page).

- **Lock** (🔒, `.lock`): a private page shows it beside its title, in the search modal's page list (`private` in `/api/pages-list`) and on its search results (the result's meta starts with 🔒, `resultMeta`); shared pages and wikis without users show none.
- **Make shared** (the ⋯ menu of your own private page, `shareTargets` in `go/private.go`, `#page-privacy-dialog`): moves it out of your folder (`private/alice/notes/x` → `notes/x`). In a group, the entry is **Share…** and offers your groups' folders too (Group pages, below).
  The dialog says where it goes and that everyone who uses the wiki then sees and changes it,
  and submits the ordinary rename (`POST /rename/<slug>` with `from=page`: links re-point on pages and in tasks, the uploads it shows are copied to `_assets/` (Uploads, below), one commit `Rename <old> to <new>`; a refusal such as "A page named … already exists" comes back in the Rename dialog).
  Not offered without users, for a shared page, another user's page, the wiki's home or a private home, or a note (Move to wiki takes those out of the folder).
- **Who may move a page to other readers** (`checkReaders`, `mayNarrow`, `tookBack` in `go/pages.go`; one rule for a rename, Share…, Move to wiki, a folder move and an assistant's `rename_page`):
  a move is to more people when everyone who sees the page now still does (`place.within`), to fewer when someone no longer would.
  - **To more people: whoever may write the page, asked first.** Share… says who will see it. A rename that takes the page to another place comes back with the question, "“groups/dev/roadmap” will be seen and changed by everyone who uses this wiki. Now @dev (anna, bo) sees it.", and **Yes, move it** (`rename_ask`; the form sent again with `confirmed=<that name>` is the yes, and a yes to one name is none to another).
    An assistant's move to more people isn't asked again: its user asked for it.
  - **To fewer people: an admin, asked first.** "bob, erin will no longer see “plans”: there @dev (alice, carol, dana) sees it. They keep its versions up to now in the history, and links to it on pages they see become plain text." An admin moves a page only where they may write themselves: into a group they are in, or their own folder (an admin who is no member sees nothing of a group).
    An assistant passes `narrow=true` for it, after the same question as its refusal.
  - **Whoever shared a page takes it back while nobody else has changed it**: every commit of the page since it came to where it is (the move that shared it, or its first save) is theirs, nothing of anyone else's is in it uncommitted, and it goes back to where it came from or to a place at least that wide. Asked first, as an admin is.
  - **Anyone else is refused**, and told whose move it is: "“groups/dev/plans” is seen by fewer people than “plans” is now (bob, erin would no longer see it). A page goes to fewer people by an admin, or by whoever shared it while nobody else has changed it. Ask an admin."
    In the browser the rename dialog (and the editor's rename bar) comes back with the name typed and that message.
  - **What those left out keep** is what they had: the page's history is read by its path, so its versions up to the move are theirs to read (and a link to it on a page they see is plain text from then on); nothing written after reaches them.
  - **Why not everyone, with a question**: a delete is seen and undone by everyone (Deleted Pages) and costs the deleter the page too; a move into one's own group is neither seen nor undone by those left out, and costs the mover nothing.
    **Why not no one**: a wiki that starts with everything shared is sorted into team folders later, and a page shared by mistake is taken back.
  - **Who may delete or manage is per kind, on purpose**: a page by whoever may write it; a task by who made it or an admin; a list by its owners or an admin; a comment by its author; a guide by an admin.
  Links therefore never have to be unlinked: a move *out* of a private folder re-points the links of your own pages and tasks, and of the shared pages that linked to it (marked 🔒 for you, below), in the rename's one commit.
- **A link from a shared page to your private page** (typed or pasted) is allowed, but marked for you (`.wikilink-private`: a 🔒 after it, tooltip "Links to a private page: others see plain text"; the live preview too): other users see plain text there.
  `[[` completion on a shared page, and in a shared task's title or notes, doesn't offer your private pages (a private task's does).
- **A new shared page from a private page** (`processWikilinks`' `from`, `sharedNewOffer` in `go/private.go`): on a page in your private folder a link to a page that doesn't exist and isn't private is marked (`.wikilink-shared-new`: a 🌐 after it, tooltip "Creates a shared page";
  the live preview marks them too, the editor sending `?page=` to `/api/preview`) and carries `?from=private` (`/page/<slug>?from=private`, kept by the redirect to `/edit/<slug>?from=private`).
  The link also says which page it is on (`&on=<slug>`), so that editor (a new page only) shows a note above the text, **🌐 This new page is shared: everyone who uses the wiki will see it.**, with **Make it private** and **Keep shared** (just dismisses it, not remembered).
  **Make it private** (`POST /link-new` with `slug` and `on`; `linkNewOp` in `go/pageops.go`) points the link you clicked at the page in your folder (`[[Garden]]` becomes `[[private/<you>/garden|Garden]]`, every link to it on that page, one commit "Link to private/<you>/garden on <page>") and opens the editor for that page, which is made when it is first saved. Nothing of the shared name was written, so nothing moves.
  Without a page it can point (the link carries none, or one that isn't yours to change) the note says how instead: "For a private page, link to `private/<you>/<name>` instead."
  No note when the page is private already, a private page of that name exists, there are no users, or the link was on a shared page.
- **Private** is a module (`docs/spec/navigation.md`, Modules): in the switcher's first pill, Wiki · Private (the pages; Tasks · Notes, the tools, are the second), with a small lock as Notes has (`.module-lock`, tooltip "Private: only you see it"), and a tab on phones; **g p** goes to Private home.
  Private views (a page in your folder, the home included) make it current, notes make Notes current.
  **Who sees a page that isn't everyone's is its mark's tooltip** (🔒 "Private: only you see this page", 👥 "@dev: anna, bo see this page"), and where there is no hover (a phone) a line under the title says it in words (`.page-seen`).
  **The user menu** holds who is signed in and **Sign out** (desktop; phones have them in the page's top row, where the name opens the menu's other entries).
  It opens from the **user button** (`.top-user`, a round initial with the name as its tooltip) at the top right, after Search.
- **What "private" means**: hidden inside Zone, from other users and their assistants.
  Not encrypted, and not hidden from whoever has the wiki's folder or its git repository: the files, their history and a git remote hold everything.
  Search's keyword index (BM25) is made of the shared pages and tasks alone (`indexCache`);
  what isn't everyone's is searched along with it by those who see it, place by place (a user's private pages, their private tasks, a group's folder: `textsearch.Layer`, `Index.View`; their document count, frequencies and lengths join the shared ones at query time, so they rank exactly as if one index held what the viewer sees),
  while another user's words,
  counts and lengths take no part in anyone's ranking,
  nor in the vocabulary that prefix and typo matching draw on (else a typo that stops matching would tell that a word is in a private page).
  The semantic index holds every page's vectors, but a search looks only at the visible ones (pages, sections, tasks, Related, and the pages a new page is compared with).
  The query log (`query-log`) is the wiki owner's file in `.zone/`, never shown in Zone: with users it holds every user's queries and the private pages they found.
- **The rule lives in one place** (`go/access.go`): `placeOf(slug)` (the page's place: everyone's, one user's or no one's; `place.sees`, `place.own`, and `place.within`: whoever sees this place sees that one, which a link's mark and a move ask),
  `canSee(user, slug)`,
  `canWrite(user, slug)` (the same pages: everything shared is editable),
  `canSeePath(user, rel)` for files as git names them (pages, a user's uploads and tasks in `private/<user>/_assets/` and `_tasks/`, which the private folder covers, and a group's pages and uploads in `groups/<group>/`: Group pages, below).
  Names compare in lower case, and a path is judged as it is opened, cleaned (`private/bob/../alice/x` is alice's).
  Without users nothing is private, and `private/` is an ordinary folder.
- **No one's pages**: anything under `private/` outside a user's folder (a page in `private/` itself, `private/alice.md` beside the folder, the folder of a user no longer there) is seen by no one; Zone logs each such entry when it starts (`strayPrivate`), so the admin can move it.
- **Opening another user's page** (view, editor, history, a version, a diff, the search preview) is a plain 404, the same as for a page that doesn't exist, for every slug in their folder (`refusePage`), so it doesn't tell what exists there.
  Sharing mode's "Hidden page" stays for pages `noshare` hides.
- **Changing another user's page** is refused the same way, 404 (`canWrite`; `refuseWrite` in the routes): save and auto-save, new page, rename, delete, restore a version, toggle a checkbox, commit one file.
  Renaming a page *into* another user's folder goes back to the dialog with an error ("that folder is another user's"), as does Move to wiki into one; Append to page and Move to wiki's "Link from" take only pages you may change ("There is no page …").
  Renaming a shared page to another shared place re-points links on every page, other users' private ones too, in one commit (Rename in `docs/spec/editor.md`); renaming one into your private folder is an admin's, or yours while it is yours alone (Who may move a page to other readers, above).
- **Notes are private**: with users the New note dialog says "New private note" and "Only you see it, until you move it to the wiki";
  a new note goes to the top of your folder, `private/<you>/<timestamp>_<name>` (`createQuicknote`, `privateFolder`);
  Notes lists only yours, under a 🔒 like a private page (an empty Notes says "Notes are private: only you see them, until you move one to the wiki"; `listQuicknote`, `inQuicknote`; so do Notes filter and `list_pages`/`search_wiki` with `notes`).
  A top-level note from before users stays a shared page: it opens by its link and shows in search and the page list, but is not in anyone's Notes (move it into your folder, or use Move to wiki).
  Move to wiki and Append to page take a note out of your folder (to the folder you name, the top level by default; to the page); an old shared note (from before users) goes into your private folder by Move to wiki as by a rename (Who may move a page to other readers, above).
  `isQuicknote` takes a note at the top of a user's folder (not deeper, not in no one's folder, and without users only top-level notes).
- **Uploads** for a private page are private: the editor sends the page's slug with an upload (`page`, `markdownEditor`'s `page` option), and a file for a page in your own folder is kept in `private/<you>/_assets/` (`uploadDir`; URL `/private/<you>/_assets/<file>`, the folder's own path);
  any other page's uploads go to `_assets/` as before.
  `GET /_assets/<file>` serves `_assets/<file>` (a file straight in it, no subfolder), and `GET /private/<user>/_assets/<file>` only to that user (404 for anyone else, and for any other subfolder).
  **A page taken out of your folder takes the uploads it shows with it** (`shareUploads`, in the rename's commit): each `/private/<you>/_assets/<file>` it links,
  outside code,
  is copied to `_assets/` (your other pages keep their own; a name taken by a different file there gets `-2`, `-3` …) and the page points at the copy.
  A file goes only where the page goes: a page moved into a group's folder takes its files to the group's `_assets/`, and one taken into a private folder (by an admin, or back by whoever shared it) to that folder's, never to the shared `_assets/`.
- **History and commits**: a page's history, its versions and diffs leave out versions from when the page was another user's private page, before a rename (`pageLog`, `canSeePath` on the version's path).
  The uncommitted list shows, and Commit all commits, only files you may see (`visibleUncommitted`): another user's draft stays uncommitted until they commit it.
  A commit puts a page's text in the history under a name, so it is a write: a page you may not write (a guide, for an editor) is not yours to commit (`mayCommit`): Commit answers 403 with why, and Commit all leaves it.
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
  her own private list, pinned,
  and private tasks (open, closed, archived; one a shared page refers to with `[[task:…]]`),
  holding a word no response to bob may contain,
  and probes every route and MCP read tool as bob,
  and every write route and MCP write tool (her files and the commits must stay as they were);
  a route Zone registers, and an MCP tool, must be probed or listed as exempt there (`test_every_route_is_covered`, `test_every_tool_is_covered`).
  Further tests there cover what alice does with her own (notes, uploads, a new private page, history, Commit all, private tasks and their defaults); the Go tests in `go/tasklogic_test.go` the per-user task files.

## Group pages

Pages in `groups/<group>/` (and its subfolders) are that group's: whoever is in the group sees and changes them, and to anyone else there is no such page, as with another user's private page.
A team inside a larger wiki keeps there what only the team should read.

**To use it**: make a group on **Groups** (`/groups`, in the user menu: a name and who is in it; you own it and say who else joins), or have an admin put the people in one (`zonewiki user group anna +dev`, or the Users tab; a group can be made of other groups, so `groups/product/` is for whoever is in dev or in design: Groups, above).
From then on `groups/dev/` is theirs: make a page there with **New page** on the group's home or beside the group on Groups (`/new` with For at the group), from a link on one of its pages (`[[` offers "New page for @dev"; a link to a page there isn't asks, Make it a page of @dev), by sharing one of your private pages with it (Share…), or through an assistant (`write_page` with that slug).
**New list** beside the group on Groups opens New list… with For at the group (`/tasks?newlist_for=@dev`).
There is nothing to set up for the folder itself: it is the folder of the group `dev` whether or not a page is in it yet.

- **Who sees it** (`placeOf`, `place.sees` in `go/access.go`): the group's members now, `memberOf(user, group)`.
  Put in the group, you see its folder at your next request; taken out, or disabled, you no longer do.
  **An admin is no member by being admin** and sees nothing of the folder, as an admin sees no private page; they can put themselves in the group, which the settings' log shows.
  A viewer in the group reads and changes nothing. A visitor of a public wiki is in no group.
- **To anyone else it isn't there**: opening or changing a page of the group (view, editor, history, a version, a diff, the preview, a save, a rename into or out of it, a delete, a restore) is a 404, as for no page at all, by every route and MCP tool.
  Its pages are left out of search, the page list, Deleted Pages, the uncommitted list, Backlinks, Links to and Related, and a link to one renders as its words; a commit message that names one says "a group's page" (`hideNames`).
  A rename or a new page aimed at a group you aren't in is refused with "that folder is a group's you aren't in", which says nothing of whether there is such a group.
- **The folder is the group's whoever is in it**: `groups/nobody/`, of a group nobody is in, is seen by no one, and so is anything in `groups/` outside a group's folder (a page in `groups/` itself, a folder no group can be named as).
  Zone logs each such entry when it starts (`strayGroups`).
  So emptying a group never publishes its pages, and what is shared doesn't depend on who is in which group.
  **A wiki that already had a folder `groups/`** when it got users, or when Zone got group folders, finds its pages there seen only by the groups of those names, or by no one: move them out on disk, or put people in a group of the folder's name.
  Without users `groups/` is an ordinary folder.
- **To more people by whoever may write, to fewer by an admin** (`place.within`, `checkReaders` in `go/pages.go`; Who may move a page to other readers, under Private pages): one of your private pages moves into a group you are in, a group's page into the folder of a group that includes its own (`groups/dev/x` → `groups/product/x` while product includes dev) or out to everyone.
  A shared page into a group's folder, and a group's page into a private folder or another group's, is to fewer people: an admin's move (into a group they are in), or that of whoever shared it while nobody else changed it. Between groups it goes by how they are made, not by who happens to be in them.
- **The way in** (`go/private.go`): a pill in the switcher, for whoever is in a group (Modules in `docs/spec/navigation.md`; **g g**): called by the group for who is in one, **Groups** with a menu of them for who is in several.
  `/groups/<group>` opens the group's home, `groups/<group>/index`, and makes it on the first visit of someone in the group who may write, from a small template (`# dev`, a line that the pages in this folder are seen and changed by whoever is in the group, a `## Links` heading; committed in their name, `Create groups/dev/index`).
  It is an ordinary page of the group; like the wiki's home and a private one it can't be renamed or deleted (`isGroupHome`, `isProtected`). To anyone not in the group the address is a 404 that makes nothing.
  `/groups` lists your groups, those you are in and those you own: who is in each and by which way (the people it is named on, then each group it includes with that group's people: "anna, and everyone in dev (alice, carol)"; by name up to eight, a larger one by its number, "23 people (everyone in dev and design, and 2 named on it)"), its owners, how many pages its folder holds; and it is where a group is made, managed and renamed (A group of your own, above).
- **The mark**: a group's page shows **👥 dev** beside its title (`.group-mark`, a link to the group's home; its tooltip says who sees the page: "@dev: anna, bo and carol see this page", a group of more than eight by its number), 👥 in the search modal's page list (`group` in `/api/pages-list`), on a search result (`resultMeta`: "👥 groups/dev · edited …") and before a group's page in `[[` completion.
  🔒 stays what is yours alone; a shared page has no mark.
  **The breadcrumb names the place, not the path** (`buildBreadcrumb`): "👥 dev › Roadmap" and "🔒 Private › Diary", the first crumb leading to the place's home (in a browser the navigation trail takes the crumbs' place: `docs/spec/navigation.md`).
  **Where a folder is chosen** (Move to wiki's folder field: `folderChoices`) your places come first and are named, "👥 dev" for `groups/dev/` and "🔒 Private" for your own folder, then the wiki's folders; what is typed and saved is the path.
- **The admin page** says of each group whether it has pages (its folder is there), and names the folders in `groups/` and `private/` that **no one sees**, by their paths alone (the folder of a group nobody is in, of a user who is gone): until then only the start's log said so.
  Taking the last person out of a group says that its pages stay, seen by no one.
- **Share…** (the ⋯ menu, `shareTargets` in `go/private.go`, `#page-privacy-dialog`): on one of your private pages it offers each group you are in and everyone, on a group's page the groups that include its own and everyone, each with who sees the page then and where it goes (the same path below the folder: `private/alice/notes/x` → `groups/dev/notes/x` → `notes/x`).
  The choice submits the ordinary rename: links re-point, the files it shows go with it, one commit. It can't be taken back.
  A private page of someone in no group keeps the plain **Make shared…**; a viewer, a shared page, a private home, a note and a guide that isn't yours to change are offered nothing.
  **A note** is filed into a group's folder with Move to wiki: its folder field offers the folders of your groups, also one with no page in it yet (`pageFolders`), and not `groups/` itself.
- **A link whose target not every reader of the page sees** is marked for who does see it (a link to a group's page `.wikilink-group`: a 👥 after it, "Links to a page of @dev: others see plain text"; one to your private page `.wikilink-private` with its 🔒, as from a shared page; `place.within` in `wikilinker`; the live preview too): from a shared page to a group's, from a group's page to one of your private pages or to another group's.
  Not within the group, and not into a group that includes it. To the others the link is its words.
- **A new page from a group's page** (`newFrom`, `sharedNewOffer`): a link there to a page that doesn't exist and would be shared is marked 🌐 ("Creates a shared page") and carries `?from=group:<name>`; the editor it opens says **🌐 This new page is shared …**, with **Make it a page of @dev** (the link is pointed at `groups/dev/<name>` and the editor opens for that page) and **Keep shared**, as from a private page.
  **`[[` completion** on a group's page offers a new page in the group's folder first ("New page for @dev: …"), and of the pages there are those every reader of the page sees: the shared ones and that group's, not your private ones nor another group's; on a shared page nothing of a group's (`js/completion.js`).
  **`/new`** (the form without JavaScript) asks someone in a group whom the page is **For**: everyone, one of their groups, only them (`for=group:<name>`, `for=private`; `createPage`); someone in no group has the Private box, as before.
- **Uploads** for a group's page are the group's: the file is kept in `groups/<group>/_assets/` (`uploadPlace`, `uploadDir`; URL `/groups/<group>/_assets/<file>`) and served to its members only, 404 for anyone else and for any other subfolder (`serveGroupUpload`); an assistant reads and adds them with `read_file` and `upload_file` (`page`), or over HTTP with its user's token.
  A page that leaves the folder takes the uploads it shows with it (`shareUploads`), as one that leaves a private folder does: to `_assets/` for everyone, to another group's or a private folder's `_assets/` when it goes there (a move to fewer), never to more people than the page.
- **Search**: the keyword index of the shared pages has nothing of a group's; its pages are one layer, searched along with the shared ones by its members and nobody else, as a user's private pages are by them (`docShared`, `docPlace`, `Index.View`; a list for some's tasks likewise, Lists for some in `docs/spec/tasks.md`), so a group's words, counts and lengths take part in no one else's ranking, and a typo of a word only the group has suggests nothing to anyone else.
  The layer is made when a member first searches after a change in the folder, for all of them: with 2,000 pages in the group some 40 ms, and every further member's search finds it there (measured 2026-11-17, `TestAMembersIndexOfALargeGroup`).
- **A group's guides are an admin's who is in the group** (`adminsOnly`): `groups/dev/AGENTS.md` and what it pulls in steer the assistant of everyone in dev, so the others in the group read them and don't change them (Guides, above).
- **Tasks are not kept there**: a group's tasks are a list for the group (`"for": ["@dev"]`, Lists for some in `docs/spec/tasks.md`), which a page in the folder can show (`[[tasks: list=dev]]`).
  A `[[task:…]]` on a group's page counts as its title in search only for a task everyone sees, as on a shared page.
- **What Zone didn't see saved** in a group's folder (written on disk) has no known author: before a write by someone who isn't in the group re-points links there, it is committed as Zone's ("Save 3 files in groups/dev"), so nobody's commit carries text they don't see (`commitOthersDrafts`).
- **Assistants** (`docs/spec/mcp.md`): a group's page is a page at a path, so there is nothing new to call: `write_page` with `groups/dev/…` makes one, `rename_page` moves one (to fewer people only by an admin, with `narrow`), `upload_file` with `page` keeps a file with it.
  An assistant is told who sees a page with every answer about one that isn't everyone's ("seen by: the group @dev only …"), and the instructions name the folders of the groups its user is in.
- **What it is not**: encrypted, or hidden from whoever has the wiki's folder or its repository (as private pages).
  Not a folder whose readers some of its members only are: a group is who it is for. (A folder for named people is the folder of a group they make: A group of your own, above.)
- **Tests**: `tests/test_group_pages.py` gives the group dev a page (committed, deleted, uncommitted), an upload and a guide holding a word no answer to anyone else may contain, and runs the probes of `tests/test_privacy.py`, aimed at the group's folder, as an editor and as an admin who are not in it: every route and MCP read tool, and every write (the folder's files stay byte for byte).
  A route or tool probed there is probed here; further tests cover its members, a viewer among them, joining and leaving without a restart, a group made of groups, the moves, uploads, the guide, the history and a folder of no group. `TestGroupFolders` in `go/access_test.go` has the rule's table, `TestAGroupsPagesAreInItsMembersIndexAlone` the search.

## Private tasks

A task can be private: only its user sees it, on every task list, in search, on pages and to their assistant.
**Private and shared are said of lists, not of tasks**: a task is private when it is in your **Inbox** (it has no list yet: every new task starts there unless it is made in a list or names one) or in a private list of yours (Lists in `docs/spec/tasks.md`), and a task in a shared list is shared.
**A shared task is always in a list**, so nothing is everyone's without a place.
You make a private list with **New list…** and its **Private** box, and a private task by making it: from anywhere (`c t`, New task: the Inbox), or in a private list, with the dialog's **List** field or in a view of the list.
The dialog says under its fields who sees the task ("Private: only you see this task.").
**A shared task can't be made private**: your private lists and your Inbox are off in its List field, and the API and `update_task` answer 400 "A shared task stays shared".
**Where a task is kept is the one thing that decides who sees it; its tags never do**, and its list is what says where it is kept.
Cards of private tasks show 🔒 (`.task-private`).
Everything else (shared tasks) is everyone's to see and change, as shared pages are.

- **Storage**: shared tasks are in `_tasks/` (`tasks.json`, `closed.json`, `closed-YYYY-MM.json`, each list's in its folder `lists/<name>/`: Storage in `docs/spec/tasks.md`); a user's private ones are in the same files and folders in `private/<user>/_tasks/` (`taskFiles.Dir(owner)`), created on their first private task, beside their lists' registry (`lists.json`: their private lists and their pins).
  A task's file says whose it is: the task's `Owner` (`go/internal/taskmodel`) is not written into it, it is set from the folder a task was read from (`eachOwner`), and the API shows it as `"private": true|false` (only with users: without them the API's JSON is as before).
  A write that puts a task in a list sets where it is kept (`applyList` in `go/tasks.go`), and the task moves between the files in one commit.
  Ids are random UUIDs, unique across all files; restoring a deleted task (undo) never takes an id another file has (`taskFiles.IDsLocked`) and puts it back where it was (`placeSnapshot`: in its list, when you still have one of that name that is as private as the task was; a private task whose list is gone in your Inbox; a shared one whose list is gone in the shared list `general`, made for it; never a shared task into a private list, nor a private one into the open).
- **Reading and writing** (`go/internal/taskfiles`, the store `taskFiles` in `go/tasklogic.go`): every reader takes the user and reads the shared files and theirs (`Owners(user)`: `Open`, `All`, `Snapshot`, `ReadForWriteLocked`, `ClosedInYear`, `ClosedYears`, and on top of them `findTasks`, `resolveTaskRefs`, `findTaskBacklinks`);
  Zone itself (`""`) reads only the shared ones.
  A write splits the tasks by owner (`byOwner`) and writes the files that changed, all in one commit; a task can only be written by its own user, since another user's is never read for them.
  One lock (`taskFiles.Lock`) for all files' writes, and a second one that readers share and a write holds while it replaces its files (`Open` for `GET /api/tasks`, `All`, `Snapshot`; Storage in `docs/spec/tasks.md`): a task that is shared moves between two files, and a read between their writes would miss it.
  What Zone does over every task list covers every user's files (`EveryOwner`): the closed tasks' filing by month at start (`ArchiveOldClosed`), the lists' migration, a rename's links in tasks (`renamePageRefs`), a shared list's rename, the search index (`Every`, filtered per user by `canSeeTask`, `hiddenDoc`).
  A series' next occurrence stays in its list, so private (`spawnRecurring`).
  A private task's pasted files are private too (the description's editor sends `private/<you>/…` as the upload's page).
- **Another user's private task is no task**: by id the routes answer 404 as for an id no task has (`DELETE /api/tasks/<id>` of an unknown id stays a 204, as before);
  lists, counts, Closed and its years, the due count, Linked tasks and the section chips leave it out;
  a `[[task:…]]` to it renders as for a deleted task.
  A shared page that refers to a private task doesn't carry its title into the search index (`taskRefsAsTitles` resolves a page's references as its owner, or for a shared page as no one).
- **Another user's private list is no list**: it is in no switcher, no List field and no answer; its name on a shared page (`[[tasks: list=gifts]]`) is the shared list of that name or none ("There is no list #gifts"), to its owner too, so a shared page shows everyone the same.
  A name means the same registry for one user only: two users can each have a private `gifts`, and a shared one can exist beside them; whoever has both names the shared one `shared/gifts` (Names in `docs/spec/tasks.md`).
  Renaming, deleting, sharing, pinning or setting another user's list is refused as for a list that isn't there (404).
- **Pins are per user** (`pinned` in their registry): which lists are pills of their list switcher; one who has set none has the wiki's. A pin decides nothing about who sees a task or whose it is.
- **A new task is private until it is filed**: from New task, `c t` or an assistant's `create_task` without a list it is in its maker's Inbox (The Inbox in `docs/spec/tasks.md`). The dialog says so under the title: "Adds to your Inbox: only you see it.", and for a shared list "Adds to the list team: everyone sees it."
- **Sharing a task**: moving it out of your Inbox or its private list to a shared list (`list` with `share: true` in `PUT /api/tasks`, `batch-update`, `update_task`; the dialog and the list picker send `share` after saying what it does), moves it from your files to the shared ones, in one commit;
  it is an event in the task's History (`shared`, by who did it; older tasks may show `private` events from when tasks could move the other way).
  Without `share` such a move is a 400 that says the task would be shared; a shared task into a private list or an Inbox is a 400 always.
  **A task you share is yours to do, unless the write says otherwise** (`sharedBy` in `go/tasks.go`): shared without a word of who has it, it is assigned to who shares it, so it stays in their My tasks; a write that names someone, or no one (`assignee: null`), decides for itself. One rule for every door: the dialog (which shows your name, to be taken off), a move, a drop, a private list made shared or deleted into a shared one, an assistant's `update_task`. (Until 2026-11 the browser's dialog and move did this and the other doors didn't: there a task fell out of its owner's My tasks.)
  Making a private list shared (**Make shared…** in its ⋯ menu) shares its tasks with it, and deleting it into a shared list does when the dialog's box says so (into your Inbox its tasks stay private).
  The tasks' undo of an edit doesn't put a task that was private back in its list, so it never tries to take a shared task back.
- **A tag never moves a task**: adding a tag with a private list's name to a task, in the modal, a batch or `update_task`, leaves it where it is; such a tag on a shared task shows to everyone, as any tag.
- **History**: `private/<user>/_tasks/` files (the task files and `lists.json`) show only to that user in history views (`canSeePath`); the commit messages of task writes ("Update task") name no task.
  A private list's own commits name it ("Create list #gifts", in your files): they show to you alone.

## Assigning tasks

A shared task can be assigned to one user: who does it.
One, not several, so that "who has this?" has one answer; someone helping goes in a comment or a tag.
It is information: it never changes who sees a task or who may change it, and anyone who may change tasks may assign any task to anyone, themselves included, or to no one.

You assign a task in its dialog or by ending a new task's title with `@name`, see who has it on its card, and filter a list by it (`@anna`, `@me`, `@none`).
A page can show each user their own tasks with `[[tasks: assignee=me]]`, several selected tasks are assigned at once, and an assistant assigns and asks for "my tasks" through the MCP tools.
What happens on a task you have to do with is told to you (below). Not there: a group as an assignee, and rules that go by the assignee (who may close a task).

- **The field** is the task's `assignee`, a user's name, kept in its file after `tags` (`Task.Assignee` in `go/internal/taskmodel`; Task fields in `docs/spec/tasks.md`).
  A task without one has no such key.
- **Assigning** is a task write like any other: `assignee` in `POST /api/tasks`, `PUT /api/tasks/<id>` or `batch-update` (`applyAssignee` in `go/tasks.go`).
  The value is a user's name, `me` for whoever writes, or `""`, `null` or `none` for no one; a leading `@` and the case don't matter (`taskmodel.CleanAssignee`).
- **Who can be assigned** (`assignable` in `go/users.go`): a user who is not disabled, not waiting for approval and not a viewer (who couldn't complete the task or comment on it).
  Any other name is refused with a 400 that says why (`notAssignable`: "… is not a user of this wiki", "…'s account is disabled", "… is a viewer, and a viewer can't change tasks"), and nothing of the write is kept.
- **`me` and `none` are reserved names**, since they are the words for whoever is signed in and for no one.
  No user has one: `zonewiki user add`, an invite, sign-up and the first-run page refuse it ("… can't be a user name"), and a settings file that has a `[user me]` or `[user none]` (written by hand) is refused like any other mistake in it: Zone doesn't start with it, and a running Zone keeps the settings it had.
  (Until 2026-11 such a user from before the rule kept their account and couldn't be assigned.)
- **A private task has no assignee**: it is its owner's, and nobody else sees it.
  `assignee` on a private task is a 400 ("A private task is yours alone. Put it in a shared list to assign it"); a write that shares a task (`list` with `share`) can assign it at once.
  So a task for someone is made in a shared list: the dialog moves a new task there when you name someone (Where a new task goes in `docs/spec/tasks.md`).
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
  It always shows on a shared task (empty: "No one"); on a new task still in your Inbox, **Assignee** in the line under the fields ("+ Assignee") shows it and goes to it, as the dialog's other rarely set fields are asked for (Interaction in `docs/spec/tasks.md`).
  Type `@` (everyone, yourself first) or the start of a name; Tab or Enter completes it to a chip, and another name replaces it; the chip's × or Backspace takes it off.
  `me` is yourself.
  A name that is no user who can be assigned is not saved: the dialog stays open and the field says so.
  A private task has no field and no "+ Assignee": choosing a private list, or the Inbox, hides both.
  A new task starts with the one user the filter names (`@anna` or `@me`; `filterDefaultAssignee`), as it starts with the filter's tags.
- **In a new task's title** (`TasksCore.parseQuickAdd`): `@name` at the end, among the other shorthand ("Call Sam @bob #work tomorrow"), with completion as you type it.
  Only the name of a user who can be assigned, or `@me`; anything else stays in the title (`bob@example.org`, `@home`), as does a second name.
  Not on a private task.
- **On the card** (`.task-assignee`, after the tags): `@anna`, your own name in the accent colour, a former assignee's struck through. Not where the view says it: in My tasks your own name (every shared task there is yours), By person any name but a former assignee's.
- **What happens on your tasks is told to you**, in the app and, where you ask for it, by mail (`newsFor` in `go/tasklogic.go`, `TasksCore.newsFor` in the browser: one rule, `go/testdata/news_cases.json` runs through both; `go/notify.go`).
  **You have to do with a task** that you are assigned, that you made (`created_by`) or that you follow (`followers`: Following a task, below); a mention reaches you whatever the task. Only shared tasks: a private one has nobody else.
  **A task has news for you** when the latest of these is someone else's and after the last thing you did on it (anything of yours in its history, or a comment of yours): it was given to you (the last `assigned` entry, or `created` for a task made with your name on it, while it is open); a comment; a mention of you; it was closed or reopened; and, for who made it or follows it, it was given to someone else. Not a date, a tag, the title or the order: they are noise. History entries are in order and comments are; between the two the minute decides, and within one minute a comment of your own or your look at the task counts as the later one.
  **A look names the last comment it saw** (`look.Comment`; the `seen` entry's `to` in the API's answers: a comment's id, `-` for a task without one), and every comment after that one is news, whatever the minute: one written a moment after you looked is not lost. (Until 2026-11-16 a look said only its minute, and a comment within that minute was taken as seen; a look from then still goes by the minute, and so does one whose comment is gone.)
  **Following a task** (`applyFollow` in `go/tasks.go`, `Task.Followers`): **Follow** in a shared task's ⋯ menu and in its dialog (**Unfollow** the other way; a toast with Undo) makes you one of its followers, and from then on you are told what its maker is told: a comment, closed, reopened, given to someone else. It is not yours to do for that: it is in no My tasks, and with news it is under Updates there, as what you gave away is. At a team's size most of what matters to you is neither assigned to you nor made by you: a task your work waits for, one of your team's.
  Who follows is kept in the task (`"followers": ["dan", "alice"]`, in the order they came; no key without one), so the rule runs from the task alone, in the server and the browser, the mail asks the task whom to tell, and git keeps it; a follow is a write of the task's file and a commit, by who follows. **Only yourself**: a write says `follow: true` or `false` and names no one (`PUT /api/tasks/<id>`, `update_task`), as nobody writes under another's name; `followers` is no field of a write. Only a shared task (a private one has nobody else: 400), only with users, and not a viewer (403).
  **Following is a look**: what was said before is no news to who starts to follow, so their look is noted with it (`markSeen`). **Nothing in the history**: that someone follows is news to no one. **Nothing follows by itself**: not who commented, not a former assignee; a mention reaches whoever is named anyway.
  **Only who sees a task is told**: a follower who is none of its list's members anymore (a list narrowed, a group left) stays in the file, is not among the names the API answers (`followersSeen`), and has no news, count or mail of it; among them again, they follow as before. The dialog names the followers ("👁 Followed by you, bob"). A series' next occurrence keeps them. `is:following` in the filter lists what you follow; an assistant's `search_tasks` says "the user follows it".
  **On its card: what it is** (before your name): **new** for a task just given to you (`.task-new`; its tooltip says who gave it and when), and in words for anything else, **bob commented**, **bob mentioned you**, **anna closed it**, **anna reopened it**, **anna gave it on** (`.task-update-mark`).
  **Opening the task takes it away** (`markSeen` in `js/taskdata.js`; `seen` in `PUT /api/tasks/<id>`, `updateTaskLocked`): when you open its dialog or its details, the browser says so and Zone notes your look. So the mark goes when you have looked, and whoever gave you the task reads in its History that it arrived ("Seen · anna"). For a task that has no news for whoever opens it, `seen` notes nothing. **Mark all read**, in My tasks' ⋯ menu while there is news, sees them all.
  **What you have seen is yours, not the task's** (`go/taskseen.go`): a look is kept in Zone's folder, `.zone/seen/<user>.json` (`{"<task id>": {"at": "2026-11-15T09:30", "after": "…"}}`: when, and which entry of the task's history was its last then, since a history's times are to the minute and what happens after a look within its minute is news), out of git, one look a user and task. It writes no task file, makes no commit and changes no list's word: nobody's list loads again because someone looked, and no reader is a commit's author.
  The news' rule reads the looks with the task's history (`historyWithSeen`), and the API answers them there as `seen` entries, so the browser's rule and the History are as they were; a history a client sends back (an undo) is kept without them (`sentHistory`).
  A file names at most 300 tasks before those that can have no news anymore go (gone, or closed for two months: `canHaveNews`). Lost with Zone's folder, tasks show their news again and nothing else happens.
  (Until 2026-11 a look was a `seen` entry in the task's own history, a write and a commit; such an entry is still read, and counts until its user looks again.)
  **No time takes it away**: a task given to you weeks ago and never opened is the one that needs the mark. (Until 2026-11 it faded after seven days, and only a task given to you had one.)
  **On the Tasks links: how many** (`TaskCounts.New`, `updatesFor`): the notifications badge of the tasks, a filled count in the accent beside the due and Today counts (`.module-new`), with "2 updates for you" in the link's tooltip, on every page and on My tasks' pill: every task with news for you, wherever it is, and also one closed this month or last (`Store.RecentClosed`: the current closed files and last month's archives, which a page view may read; an older archive is never read for it, so news on a task closed longer ago isn't told). A list's pill counts those in the list. A click goes to My tasks, whose Today tab lists them under **Updates** (Views in `docs/spec/tasks.md`), the ones that aren't yours to do among them: what you gave away, what mentions you, what was closed.
  **Mentions** (`mentionsOf`, `logMentions`): `@name` in a comment or a description, of a user who can be given tasks (not in code, not the `@` of a mail address), is logged in the task's history as `mentioned`, with who was named, by who wrote it; it is news to them, on a task they have nothing else to do with too. A description logs only the names it didn't have before. (Not built: the name as a chip in the text, and completing it while typing.)
  **By mail, where you asked for it** (`mails`, `mailKinds`): three kinds, each a box in My tasks' settings dialog (there where Zone could mail you: the deployment's mail server, `ZONE_SMTP_*`, Mail in `docs/spec/deployment.md`, and your `email` in `.zone/config`), **none ticked until you tick it**: **a task is given to me** (`tellAssigned`: "bob gave you a task: <title>", several given in one write one mail), **someone mentions me** (`tellMentioned`: "bob mentioned you: <title>", with the text), **someone comments on a task I have, gave or follow** (`tellComment`: "bob commented: <title>", with the comment; not to who it mentions, who gets that mail). Each with the task's link where Zone knows its address (`base-url`), sent in the background (a write doesn't wait for the mail server; a failure is in Zone's log), never for what you did yourself. Kept as `"mail": "assigned,comments"` in your registry's `mine` (the kinds turned on, in that order). (Until 2026-11 the one mail there was, about a task assigned to you, was on unless turned off with `"mail": "off"`, which now reads as none: nobody is mailed who didn't ask.)
  There is no digest and no mail about watched pages.
- **In the filter** (`TasksCore.parseFilter`/`matchesFilter`, given `people`): `@anna`, `@anna|bob` (either), `-@anna` (not hers), `@me` (yours), `@none` (no one's, a former assignee's included).
  A private task has no assignee and is its user's own: it is `@me` to them, and not `@none`.
  A completed word becomes a chip, as a tag does; the suggestions are `me`, `none` and the users who have tasks among those the other words leave visible.
  `@` on the list focuses the field and starts a name.
- **On a page**: `[[tasks: assignee=me]]` shows whoever looks at the page their own tasks, `assignee=anna` hers, `assignee=none` those no one has (a former assignee's included); `a|b` either's, `-a` not theirs, and it joins `tag=` and `list=` (Task list embed in `docs/spec/tasks.md`).
  The chip is locked there, as a `tag=`'s is.
  A new task in such a list is assigned to the one user it names (`me`: whoever makes it), so it shows up where it was made; `a|b`, `none` and `-a` give it no one.
  List settings has it under More, "Shows only the tasks of".
  `/tasks?assignee=…` filters the same way, with an ordinary chip.
- **The counts** on the Tasks links and the list switcher count what each user's lists show them: `assignee=me` is whoever asks (`assigneeParamMatches` in `go/tasksyntax.go`, the server's mirror of the filter).
  An export of such a list holds the tasks it shows (`/api/tasks/export?assignee=`).
  **My tasks**, where the Tasks links go, is what is yours: of the shared tasks those assigned to you, whatever list they are in, and your own private ones; an unassigned shared task is its list's backlog (Lists → My tasks in `docs/spec/tasks.md`).
  Zone assigns a new task to no one; its maker says who has it. Only a task of your own that you share is yours unless you say otherwise (Sharing a task, above).
- **By person** (the Open tab's control, on a shared list and All tasks; Views in `docs/spec/tasks.md`): the tasks in a section per assignee, Unassigned and your own first, so a team sees who has what; a card dragged onto a section is that user's.
- **Several at once** (`openBulkAssignModal` in `js/taskselect.js`): **Assign…** in the selection bar and in the menu of a selected card, or **a** on the selected tasks (one is enough).
  The dialog lists the users who can be assigned, yourself first, then **No one**; the row of who has them all is ticked, one who has some says "1 of 4".
  A click (or ↑/↓ and Enter) assigns them in one `batch-update`, and the toast ("3 tasks assigned to bob") has one Undo for all, which leaves no trace in their histories.
  Private tasks among the selection are left alone.
- **Handing on** the tasks of someone who can no longer be given any: the admin page's Users tab says under a disabled user's (or a viewer's) status how many open tasks they still have, "2 open tasks to hand on", linked to `/tasks?view=open&assignee=<name>`; select them there and Assign….
  `zonewiki user disable` and `user remove` say the count too (`handOnNote`).
  A removed user's open link (an invite not taken, a reset) goes with them, by the command as by the admin page's Remove (`revokeLink`): whoever is added by that name later doesn't get in by it.
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
  A comment is its author's: only they edit or delete it, and an admin may delete any (Comments in `docs/spec/tasks.md`); the task itself anyone who sees it may change.
  **Nobody writes under another's name** (`go/taskby.go`): the routes that take comments or history whole (`PUT /api/tasks/<id>` and `batch-update` for an undo, `/api/tasks/restore`, `/api/tasks/import`) get them from the browser, which could say anything.
  A `by` that is the sender's own stays;
  another user's stays only on a comment (by its id) or a history entry (of that task, at that time, with those dates) Zone has seen under that name: it notes them as it deletes a task or a comment and before a request replaces a task's lists (`rememberBy`, in memory, the newest 20 000),
  so the undo that sends them back is believed.
  Any other name is dropped (`checkBy`: by no one), also an undo after a restart.
  An admin's import keeps the file's names (a wiki moved, a backup put back).
  `TestAuthors` in `tests/test_users.py`.
