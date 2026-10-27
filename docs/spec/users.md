# Users

Part of Leaf's spec (see `AGENTS.md` for the index).

Several people on one wiki: each signs in with their own name and password, their changes are committed in their name, and an assistant connected with their MCP token acts as them. Everything in the wiki is shared and editable by every user. (Private pages and private deck tasks per user are being built: `docs/plans/multi-user.md`.)

Without `LEAF_USERS` none of this applies: one `LEAF_PASSWORD` (or none) and one `LEAF_MCP_TOKEN`, as before, and Leaf commits as itself.

## Setting up users

In `.env` (`go/users.go`, `loadUsers`):

```
LEAF_USERS=alice,bob
LEAF_USER_ALICE_PASSWORD=pbkdf2-sha256:600000:<salt>:<hash>
LEAF_USER_ALICE_MCP_TOKEN=<a long random token>     # optional
LEAF_USER_BOB_PASSWORD=pbkdf2-sha256:600000:<salt>:<hash>
```

- **Names**: lowercase letters, digits and inner hyphens (`userNameRE`), since a name becomes a folder name. In a setting's name it is upper case with `_` for `-` (`mary-ann` → `LEAF_USER_MARY_ANN_PASSWORD`, `userEnvName`).
- **Passwords are kept as hashes**, never in plain text: PBKDF2-SHA256 with a random 16-byte salt and 600,000 iterations (`hashPassword`; Go's own `crypto/pbkdf2`, no dependency), written `pbkdf2-sha256:<iterations>:<salt>:<hash>` (base64 without padding). The format has no `$`, which compose would read as a variable in `.env`. Make the line with `./run.sh passwd alice` (or `leaf passwd alice` without the container): it asks for the password twice, with the terminal's echo off (`echoOff`, `stty`; `run.sh` turns it off itself, since the container reads a pipe), and prints `LEAF_USER_ALICE_PASSWORD=…` to paste into `.env`. Changing a password is making a new line; restart Leaf to take it.
- **MCP tokens** are per user and optional: a user without one has no MCP access. Make one with `openssl rand -hex 32`.
- **Leaf refuses to start** (`loadConfig` says why) on a bad name, a name given twice, a user without a password or with something that isn't a hash, two users with the same token, and on `LEAF_PASSWORD` or `LEAF_MCP_TOKEN` set together with `LEAF_USERS` (with users each has their own; a shared one would make the user unknown).
- Adding or removing a user is editing `.env` and restarting. A removed user is signed out at once (their session no longer passes, see below).

## Signing in

- The login page asks for the user name and the password (`login.html`; the name field only with users). The name is compared in lower case, without surrounding spaces. A wrong name or password says "Incorrect user name or password." (not which one), keeps the name typed, and costs the same time either way: an unknown name is checked against a dummy hash (`authenticate`), so timing doesn't tell which names exist. Wrong tries are throttled per client as with `LEAF_PASSWORD` (`loginThrottle`, see `docs/spec/security.md`); not per user name, which would let anyone lock a user out.
- **The session names its user** (`session.User` in the signed cookie, `signedIn`). With users, a session must name one of them; without, it must name no one. So removing a user signs them out, and switching between `LEAF_PASSWORD` and `LEAF_USERS` signs everyone out.
- **The request knows its user**: the request checks (`checks` in `go/server.go`) put the session's user into the request's context (`withUser`), `userOf(r)` reads it, and `newReq` keeps it as `rq.user`. "" means no users (or no one signed in).
- The user's name is shown beside Sign out (`.signout-user`, "Signed in as alice").

## MCP

With users, `/mcp` takes any user's token (`LEAF_USER_<NAME>_MCP_TOKEN`) and the tools run as that user (`mcpAuthorized` returns the user, `callMCPTool` passes it to every tool). `/mcp` is off (403) while no user has a token; a wrong token is a 401 and throttled as before (`docs/spec/mcp.md`).

## Who made a change

- **Commits**: every commit Leaf makes for a user's action names them as its author, `alice <alice@leaf>` (`gitCommit(user, …)`); the committer stays Leaf. Commits Leaf makes on its own (start-up, archiving the deck, committing changes made on disk first before an assistant's write, `savePending`) are Leaf's. Without users all commits are Leaf's, as before.
- **History**: a page's history shows the author after a version's label ("Edited · alice", `.hist-by`; `GitEntry.Author`, `commitAuthor`), and the MCP `history` tool adds "(by alice)" to a version's or a change's message. Leaf's own commits, and every commit in a wiki without users, show no name.
- **Deck comments** record who wrote them (`by` in the comment, see Comments in `docs/spec/deck.md`): the cards and the edit modal show "12 Mar 09:30 · alice" (`commentStamp` in `deck.js`), MCP's task text "(alice)". An edit keeps the author. A task's History events (created, done, moved …) don't name anyone; the deck file's commits do.
