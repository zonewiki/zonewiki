# Project structure

What each file and folder of the repository is for.
`AGENTS.md` points here; keep it up to date when the structure changes.

```
/
├── AGENTS.md               # How to work on Zone; the index of the spec
├── README.md               # For people who find the repo: what Zone is, features, quick start
├── CONTRIBUTING.md         # Bug reports, feature requests, how a change gets in (points to AGENTS.md)
├── SECURITY.md             # How to report a vulnerability (privately, GitHub's advisories), what counts
├── LICENSE                 # AGPL-3.0-or-later
├── THIRD_PARTY_LICENSES    # Licenses of Go, the Go modules and the embedding model (the vendored libraries' are next to them)
├── zonefiles.go            # Package zonefiles: Zone's own files built into the binary (go:embed): public/, README.md, the manual, the spec, the screenshots, the licenses
├── go.mod                  # Module zonefiles, the root's: go:embed reaches only below its package's folder, so go/go.mod takes it from ../ (replace)
├── docs/manual/            # The manual, for users (see Manual in AGENTS.md); /help shows it
├── docs/spec/              # The feature spec, one file per area (see Spec in AGENTS.md)
├── docs/structure.md       # This file
├── docs/licenses/mermaid_notices.py  # Writes public/vendor/mermaid/THIRD_PARTY_LICENSES from the bundle's source map
├── docs/screenshots/       # The README's and the manual's screenshots + make_screenshots.py (./run.sh screenshots)
├── Containerfile           # The image: a Go build stage (Zone's own files built in), onnxruntime + the model (opt-in), an Ubuntu 26.04 LTS runtime with the binary and the licenses
├── compose.yaml            # Runs the prebuilt ghcr.io image, no checkout or build; run.sh drives it beside it alone (see docs/spec/deployment.md)
├── compose.build.yaml      # Builds the image from the checkout and runs it (run.sh drives it in a checkout)
├── compose.podman.yaml     # Rootless podman only: userns keep-id (run.sh adds it to either)
├── Containerfile.tools     # Tools image: Go (and gcc, for its race detector), Playwright + Chromium, the tests' Python packages, Node for tasks-core tests, gitleaks, git, pngquant
├── run.sh                  # Helper script: up/down/logs/update, user/config (via compose; beside compose.yaml alone the prebuilt image, in a checkout a build), build, test/e2e/bench/retrieval/binaries/leaks (CI runs these too); bash 3.2-compatible (macOS)
├── .githooks/pre-commit    # Refuses a commit with a secret in it (gitleaks); on with git config core.hooksPath .githooks
├── .githooks/pre-push      # Runs the clone's own .git/hooks/<same name>, if any (core.hooksPath would skip it); post-commit and post-rewrite are copies
├── .gitleaks.toml          # gitleaks: the default rules; the generic key rule skips public/vendor/ (minified names)
├── .gitattributes          # LF line endings for *.sh and .githooks/* (Windows checkouts)
├── .env.example            # Template for .env: every setting, commented
├── .env                    # Your settings (gitignored; copy of .env.example)
├── dist/                   # The release binaries ./run.sh binaries builds, with SHA256SUMS (gitignored)
├── .forgejo/workflows/ci.yml  # Forgejo Actions: checks (run.sh test, e2e, leaks), the bench (run.sh bench), search's floor (run.sh retrieval), build + push the image
├── .github/workflows/ci.yml   # GitHub Actions: the same checks, the image for amd64 + arm64 to ghcr.io, a v* tag's release binaries
├── .forgejo/issue_template/   # Issue templates: bug.md, feature.md
├── .github/ISSUE_TEMPLATE/    # The same two for GitHub, and config.yml (a security problem goes to a private report)
├── go/                     # The server: package main, a file per area; pure code without Zone's state in go/internal/
│   ├── go.mod, go.sum      # Go modules, exact versions (goldmark, x/text, onnxruntime_go), and zonefiles from ../
│   ├── server.go           # main: flags, start-up, the routes' mux, the request checks around them; zonewiki eval, zonewiki health, zonewiki manual, zonewiki licenses, zonewiki user, zonewiki group, zonewiki config
│   ├── login.go            # The login gate's routes: /login, /logout
│   ├── static.go           # public/ served as it is (gzipped, kept by the browser), from the folder or the binary's copy; a file sent with its type
│   ├── files.go            # Zone's own files: public/ and the docs from a folder on disk (--public, --docs, found from the binary) or the binary's copies (zonefiles); zonewiki licenses
│   ├── uploads.go          # Uploads: _assets/ served (a private page's and a group's from their folders, a task's from _tasks/files/<id>/), POST /api/upload
│   ├── config.go           # What is fixed for the process, from the environment (read once into cfg)
│   ├── configfile.go       # The settings file .zone/config: its keys, Settings (settings(), reloaded when the file changes), the line edits
│   ├── storage.go          # upload-max and storage-max: sizes (25MB), what the wiki holds (not .git/, .zone/), checkRoom
│   ├── commands.go         # zonewiki user (add, passwd, token, role, email, group, disable, enable, invite, reset, remove, list), zonewiki group (includes, owners, list) and zonewiki config (check, set, unset, passwd, token), which edit the settings file (filelock_*.go: one at a time)
│   ├── security.go         # Host and Origin checks, the page policy, login, signed session (and its user), throttles
│   ├── users.go            # Users: roles, groups (who is in which, also by way of a group made of groups), password hashes, sign-in, the password prompt, who a task can be assigned to (assignable, the reserved names me and none)
│   ├── groups.go           # Groups in the browser (/groups): a group made by whoever may write, who owns it; its owners say who is in it (written on the users, as an admin's change), a member leaves by themselves; a group renamed, its folder, the lists for it and the settings along; an admin's every group there, what each includes (groups made of groups), the folders no one sees; a group ended, its pages moved to the wiki or deleted
│   ├── admin.go            # The admin page (/admin, admins), its Users tab: roles, each user's groups (of those there are; groups themselves are on /groups), disable, remove, writing the settings file as the commands do
│   ├── links.go            # Invite and reset links: .zone/links, /welcome/<token>
│   ├── signup.go           # Sign-up: /signup, the mail with its link, the account a sign-up link makes, the names it may not take (.zone/removed-users)
│   ├── mail.go             # Mail through the deployment's SMTP server (ZONE_SMTP_*), for sign-up and assigned tasks
│   ├── notify.go           # A task given to a user is told to them: the mail (tellAssigned, called by the task writes), who gets one
│   ├── assistant.go        # Connect an assistant (/assistant): your MCP token made and revoked in the browser, with your password again; your assistants' connections changed (less without the password, more with it) or ended
│   ├── oauth.go            # OAuth for /mcp: the metadata, client registration (nothing kept), the consent page /oauth/authorize, /oauth/token
│   ├── grants.go           # Connections: the assistants users let in through OAuth, their token hashes in .zone/grants
│   ├── password.go         # Change password (/password): your password changed in the browser, with the one you have
│   ├── signout.go          # Sessions signed out: .zone/signed-out
│   ├── stalls.go           # A request body or response that stands still for a minute fails (noStalls)
│   ├── setup.go            # The first-run page: /setup, a first login for a wiki other devices reach
│   ├── access.go           # Who may see and change what: a page's place (placeOf: everyone's, private/<user>/, groups/<group>/), canSee, canWrite, canSeePath
│   ├── private.go          # The private home private/<user>/index (/private) and a group's home groups/<group>/index (/groups, /groups/<group>): their templates, their protection; Share… (where a page can go), what moves with it, the place's mark
│   ├── routes.go           # The wiki's routes: pages, the editor, notes, search, previews
│   ├── pageops.go          # Page writes the routes and the MCP tools share (delete, rename, restore, create, save, undo): checks, text not yet in git kept
│   ├── refusal.go          # What an operation refuses with (refusalError: a kind and a message), for pages and tasks alike; each door answers it its way
│   ├── views.go            # The page shell, breadcrumb, modules, menus, side columns; templates/ (embedded)
│   ├── pages.go            # The wiki's files: slugs, listing, sharing mode, link graph, renames, the pages that embed a list
│   ├── markdown.go         # Markdown → HTML on goldmark, with Zone's rules (checkboxes, callouts, heading ids …)
│   ├── math.go             # $…$ and $$…$$ math kept as TeX for KaTeX (a goldmark extension)
│   ├── sections.go         # Headings and sections as the parser finds them, for the ✎s, the editor, the MCP tools and search (pageHeadings, pageSections, findSection)
│   ├── checkboxes.go       # Checkboxes as the parser finds them, so a tick goes to the box the page shows (taskItems, taskLineKeys, toggleNthCheckbox)
│   ├── render.go           # A page's render: wikilinks, [[tasks:]], [[task:…]], frontmatter, sources
│   ├── gitstore.go         # Git: setup, commits, moves, grep over pages, a page's log, versions, diffs, deleted pages
│   ├── history.go          # History, revisions, diffs, restore, deleted pages, uncommitted changes
│   ├── changes.go          # Changes (/changes): the wiki's commits as their reader may see them (wikiChanges, which the MCP tool history lists too), by day, filtered by user, assistant, folder and day; whether an assistant wrote a commit and which (its trailer alone: commitMarkOf); what a row offers to undo (undoOffers) and the route that does (undoChangesOp in pageops.go)
│   ├── lastchange.go       # Who changed each page last (its last commit's author, whether an assistant and which), kept in memory from one scan of the history and the commits since, for search results
│   ├── quicknotes.go       # Notes: create, Move to wiki, Append to page (the routes and MCP share it)
│   ├── merge.go            # Several editors on one page: drafts' authors, the bases, the three-way merge, texts left in conflict
│   ├── tasks.go            # Task writes (create, change, comment, tag, move in the manual order, delete; the tasks an undo from Changes puts back), as functions the routes and the MCP tools share
│   ├── tasklists.go        # Task lists: the lists a user sees (taskLists, refs), their pins, My tasks (the home view's rule, mineFilter, and its settings), and the list operations (make, rename, delete, share, pin, settings)
│   ├── tasksetup.go        # What a start does over the task files (filed by month and list, Zone's format, a shared task in no list into #general), and the mark that lets later starts skip it
│   ├── wikiformat.go       # The format of the wiki's files, .zone-format at its root (in git): read at start, a newer one refused, written into a new wiki
│   ├── tasksapi.go         # The /api/tasks routes
│   ├── tasksembed.go       # The [[tasks:]] embed: its HTML, its settings dialog, the counts the Tasks links show
│   ├── tasklogic.go        # What Zone does with tasks beyond keeping them: a series' next occurrence, completing, history, comments, backlinks, [[task:…]] references; taskFiles, the store, wired to the wiki here
│   ├── internal/taskfiles/ # Package taskfiles, no globals: the tasks on disk: _tasks/ files (users' private ones in private/<user>/_tasks/, a group's in groups/<group>/_tasks/: owner @<group>) read and written under one lock, filed by owner and month (parsed once, and a file a write didn't touch skipped), each user reading and writing the owners they see (`Owners`), and the lists' registries beside them (lists.json: lists.go); a Store is handed the wiki's folder, its users, the clock, who may write and how a write is committed
│   ├── internal/asker/     # Package asker: who is asking (Asker): a user, or an assistant's connection given less; what every permission question is asked for (`asker` in package main)
│   ├── internal/atomicfile/ # Package atomicfile: a file written so that no reader sees half of it (pages, task files)
│   ├── taskby.go           # Comments and history a client sends whole: nobody writes under another's name
│   ├── taskseen.go         # What each user has seen of the tasks' news: .zone/seen/<user>.json, read into a task's history for the news' rule and the API
│   ├── tasksyntax.go       # The page syntax of tasks: [[tasks: …]] params, [[task:…]], tag rules, date words
│   ├── internal/taskmodel/ # Package taskmodel, no globals: a task (Task): its fields, read leniently from a file, written in a fixed key order; tags as the filter matches them; a series' dates (recurrence.go). The API's form of a task (with "private") is the server's (apiTask)
│   ├── internal/jsonobj/   # Package jsonobj: JSON objects read a member at a time (jsonObj in main): task requests, MCP messages and arguments, task files
│   ├── internal/gitrepo/   # Package gitrepo: git run in a repository, a process a call, under a time limit and a context, pathspecs literal, no detached maintenance and none inside a commit (Zone packs in the background: `go/gitstore.go`)
│   ├── internal/textsearch/  # Package textsearch, no globals: BM25 ranking (stemming, fuzzy, excerpts), the query syntax, rank fusion, difflib's ratio (search.go, difflib.go); the index in a file, mapped (indexfile.go, mmap_unix.go, mmap_other.go)
│   ├── internal/mdtext/    # Package mdtext, no globals: markdown source text (frontmatter, fences, code and math masking, headings and anchors, wikilink syntax, the splice of a section's text, checkboxes, slugs)
│   ├── internal/settingsfile/  # Package settingsfile: the settings file's format ([user …] and [group …] sections; the strict parser, line edits keeping comments, the owner-only write); what its keys mean stays in configfile.go
│   ├── internal/tokenizer/ # Package tokenizer: the embedding model's tokenizer (byte-level BPE from its tokenizer.json)
│   ├── retrieval.go        # Search and LLM retrieval (BM25 + optional semantic index), the query log
│   ├── embed.go            # Optional semantic search: section embeddings, the index and its cache, Related pages
│   ├── embed_onnx.go       # The model on onnxruntime (cgo; embed_nocgo.go: built without cgo, keywords only)
│   ├── mcp.go              # MCP protocol (JSON-RPC) for /mcp; mcp_tools.json the tool definitions
│   ├── mcpfiles.go         # Uploads for assistants: read_file, upload_file, and the file routes taking the MCP token
│   ├── mcptools.go         # The MCP tools that read, and the table of all tools with their dispatch (mcpToolTable); mcpwrite.go and mcpreplace.go those that write pages, mcptasks.go the task tools, mcpguides.go the guides, mcpargs.go what they share
│   ├── manual.go           # Zone's own docs (the manual, README, docs/spec) searched by section, for the MCP tool zone_help
│   ├── help.go             # Help: the manual and the spec in the browser (/help), and zonewiki manual DIR (them as HTML for a website)
│   ├── eval.go             # zonewiki eval: measures search_wiki's retrieval on your wiki against your questions (./run.sh eval)
│   ├── embedtest.go        # The tests' fake embedder (--semantic-test)
│   └── *_test.go, testdata/  # Go tests (go test ./...)
├── tests/                  # pytest over HTTP against the server (conftest.py, goserver.py, settings.py); e2e/ in a browser; bench_tasks.py measures the tasks at a team's size (not in the suites: ./run.sh bench, a job of CI's); retrieval/ a sample wiki, questions and the least search must find for them (its README.md)
└── public/                 # The browser code, served as it is (and built into the binary)
    ├── style.css           # The styling: the look's tokens (:root), pages, the editor, search, dialogs, phones
    ├── tasks.css           # The task app's styling, loaded after style.css
    ├── zonewiki.js             # The entry ES module every page loads; imports the modules below
    ├── js/                 # ES modules: forms (a submit only from a form where Zone puts one), chrome, git, util, completion, search, actions, pagemenu, listsrow,
    │                       #   shortcuts, mdedit, mdtext (the text functions shared with the server), editor, tasklist (its Task list button), page, figures, fold, present, copyline (Copy buttons), assistant (a connection's Change… dialog), namepick (users and groups as pills); the task app's parts
    │                       #   taskdata, taskview, taskorder, tasktabs, taskchips, taskrender, taskmodal, taskselect, taskmenu, taskkeys, taskdnd, tasktouch, tasksettings; tasktexts, taskutil, datepicker (see Client code in `docs/spec/rendering.md`)
    ├── tasks-core.js       # Pure task logic (no DOM), an ES module — shared by tasks.js, the editor and Node-run tests
    ├── tasks.js            # The task app, an ES module zonewiki.js imports; initTaskApps() starts each .tasks-app: it puts one together and starts its parts in js/task*.js (app, app.st; tests/test_task_app_wiring.py checks what they use of each other)
    ├── favicon.svg         # Browser tab icon
    ├── icon.svg            # Home-screen icon source; icon-192/512.png and apple-touch-icon.png rendered from it
    ├── manifest.json       # Web app manifest (Add to home screen)
    ├── vendor/highlight/   # highlight.js 11.9.0 + github/github-dark themes (vendored, no CDN)
    ├── vendor/gemoji/      # GitHub's emoji names (gemoji 4.1.0, MIT) for `:name` completion
    ├── vendor/katex/       # KaTeX 0.19.0 (MIT): katex.min.js, katex.min.css, fonts/*.woff2
    └── vendor/mermaid/     # Mermaid 12.3.0 (MIT): mermaid.min.js, THIRD_PARTY_LICENSES (what it bundles), COPYING.GPL-3.0 (elkjs's license as Zone takes it)
```
