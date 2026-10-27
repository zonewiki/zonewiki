# Users

Part of Leaf's spec (see `AGENTS.md` for the index).

Several people on one wiki: each signs in with their own name and password, their changes are committed in their name, and an assistant connected with their MCP token acts as them. Everything in the wiki is shared and editable by every user, except each user's private folder, `private/<name>/`, which only they see. (Private deck tasks are still being built: `docs/plans/multi-user.md`.)

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

An assistant sees and changes what its user does in the browser, no more (`docs/spec/mcp.md` has the tools):

- **Another user's private page is no page**: `read_page`, `edit_page`, `append_to_page`, `rename_page`, `delete_page`, `file_scratch_note` answer "No page …" for it, as for a page that doesn't exist (`mcpPageSlug(raw, user)`, `existingPage`). A new slug in `private/` outside your own folder (`write_page`, a rename's or a restore's target, `history` or `read_guides` of a page there) is refused with "of private/, only your own folder private/<you>/ is yours to read or write", whether or not anything is there (`newSlug(raw, user)`, `notYours`).
- **Lists and searches leave them out** (`visiblePages(user)`, `visibleSlugs`): `list_pages` (and its link counts), `search_wiki` (`retrieve(rq, …)` filters before the limit), `grep_wiki` (`grepPages` takes the visible slugs; the hits are counted after), `check_links` (a link to a page you can't see isn't broken, and never suggested), `list_tags`, `replace_in_pages` (matches and changes only pages you may change), read_page's Linked from and Related, delete_page's "still link to it", write_page's similar pages, create_task's tag hints.
- **History**: `history` of a page as `pageLog` gives it (versions from before a page was yours are left out); recent changes (`history` without a slug) show of each commit only the files you may see and leave out a commit with none of them, asking git for more until `limit` are found (`gitLogAll(…, see)`); the uncommitted files likewise. A commit message naming another user's private page (`Promote private/alice/…_idea to idea`) names it "a private page" (`hideNames`), here and in the browser's history. `restore_page` takes only versions `pageLog` lists.
- **Scratch notes** from `write_page` with `scratch` go to your folder (its guides apply: `checkGuides` with `private/<you>/scratch-note`).
- **Guides**: a `CLAUDE.md`/`AGENTS.md` in another user's folder isn't a guide for you (`folderGuides(slug, user)`, `canSeePath`), not listed in read_guides' description (`toolList(user)`), and a guide's `@import` of a page you can't see stays as written (`guideText`). `rename_page` of a folder holding pages you may not move (`private/` itself) moves nothing.

## Private pages

Pages in `private/<name>/` (and its subfolders) are that user's: only they see and change them. Make one with **New page** (Ctrl+N) and its **Private** box (`private=1` on `POST /new` puts the slug in your folder; the box shows only with users), by naming a page there (`private/alice/diary`), or by renaming a page into it. Rename it out of the folder to share it.

- **What "private" means**: hidden inside Leaf, from other users and their assistants. Not encrypted, and not hidden from whoever has the wiki's folder or its git repository: the files, their history and a `LEAF_GIT_REMOTE` hold everything. Search's keyword statistics count private pages too (they don't show, they only weigh words slightly).
- **The rule lives in one place** (`go/access.go`): `pageOwner(slug)` (the user, and whether the page is private at all), `canSee(user, slug)`, `canWrite(user, slug)` (the same pages: everything shared is editable), `canSeePath(user, rel)` for files as git names them (pages, `_assets/private/<user>/`, `_deck/users/<user>/`). Names compare in lower case. Without users nothing is private, and `private/` is an ordinary folder.
- **No one's pages**: anything under `private/` outside a user's folder (a page in `private/` itself, `private/alice.md` beside the folder, the folder of a user no longer in `LEAF_USERS`) is seen by no one; Leaf logs each such entry when it starts (`strayPrivate`), so the admin can move it.
- **Opening another user's page** (view, editor, history, a version, a diff, the search preview) is a plain 404, the same as for a page that doesn't exist, for every slug in their folder (`refusePage`), so it doesn't tell what exists there. Sharing mode's "Hidden page" stays for pages `LEAF_NOSHARE` hides.
- **Changing another user's page** is refused the same way, 404 (`canWrite`; `refuseWrite` in the routes): save and auto-save, new page, rename, delete, restore a version, toggle a checkbox, commit one file. Renaming a page *into* another user's folder goes back to the dialog with an error ("that folder is another user's"), as does Move to wiki into one; Append to page and Move to wiki's "Link from" take only pages you may change ("There is no page …"). Renaming a shared page still re-points links on every page, other users' private ones too, in one commit (Rename in `docs/spec/editor.md`).
- **Scratch notes are private**: with users a new scratch note goes to the top of your folder, `private/<you>/<timestamp>_<name>` (`createScratch`, `privateFolder`); Scratch lists yours and the top-level notes from before users, which stay shared (`listScratch`). Move to wiki and Append to page take a note out of your folder (to the folder you name, the top level by default; to the page). `isScratch` takes a note at the top of a user's folder (not deeper, not in no one's folder, and without users only top-level notes).
- **Uploads** for a private page are private: the editor sends the page's slug with an upload (`page`, `markdownEditor`'s `page` option), and a file for a page in your own folder goes to `_assets/private/<you>/` (URL `/_assets/private/<you>/<file>`); any other page's uploads go to `_assets/` as before. `GET /_assets/{path...}` serves `_assets/<file>`, and `_assets/private/<user>/<file>` only to that user (404 for anyone else, and for any other subfolder).
- **History and commits**: a page's history, its versions and diffs leave out versions from when the page was another user's private page, before a rename (`pageLog`, `canSeePath` on the version's path). The uncommitted list shows, and Commit all commits, only files you may see (`visibleUncommitted`): another user's draft stays uncommitted until they commit it.
- **Everywhere else** another user's private pages are left out as sharing mode leaves out pages not for sharing (`hiddenPage(rq, slug)`, which checks both; see Sharing mode in `docs/spec/navigation.md`): search, the search modal's list, Scratch, Deleted Pages, the uncommitted list, Backlinks, Links to, Related, folder suggestions, link counts (the search modal's, "unlinked" in search: `allBacklinkCounts(rq)` counts links only from pages you may see); a wikilink to one renders as its words without a link.
- **Tests**: `tests/test_privacy.py` gives alice a private page (committed, deleted, uncommitted), a scratch note and an upload, holding a word no response to bob may contain, and probes every route and MCP read tool as bob, and every write route and MCP write tool (her files and the commits must stay as they were); a route Leaf registers, and an MCP tool, must be probed or listed as exempt there (`test_every_route_is_covered`, `test_every_tool_is_covered`). Further tests there cover what alice does with her own (scratch notes, uploads, a new private page, history, Commit all).

## Who made a change

- **Commits**: every commit Leaf makes for a user's action names them as its author, `alice <alice@leaf>` (`gitCommit(user, …)`); the committer stays Leaf. Commits Leaf makes on its own (start-up, archiving the deck, committing changes made on disk first before an assistant's write, `savePending`) are Leaf's. Without users all commits are Leaf's, as before.
- **History**: a page's history shows the author after a version's label ("Edited · alice", `.hist-by`; `GitEntry.Author`, `commitAuthor`), and the MCP `history` tool adds "(by alice)" to a version's or a change's message. Leaf's own commits, and every commit in a wiki without users, show no name.
- **Deck tasks**: comments and History events record who wrote or did them (`by`, see Comments and History in `docs/spec/deck.md`): the cards and the edit modal show "12 Mar 09:30 · alice" (`commentStamp` in `deck.js`), MCP's task text names a comment's author "(alice)". Editing a comment keeps its author.
