# Deployment

Part of Zone's spec (see `AGENTS.md` for the index).

## Files

- **`Containerfile`** — three stages. `build`: Go (a pinned release, checked against its checksum) builds the server from `go/` with cgo (`go mod download` first, so a code change doesn't fetch the modules again). `semantic`: with `ZONE_SEMANTIC_SEARCH=on`, onnxruntime's shared library (1.31.0, its GitHub release for the image's architecture) and the model's files (a fixed revision), each checked against its SHA-256, plus onnxruntime's license and third-party notices; off, three empty folders. Its own stage, copying nothing from the repo, so a code change never downloads the model again. The image: Ubuntu 26.04 LTS with git, `tzdata` and CA certificates via `apt`, `libonnxruntime.so` in `/usr/local/lib` (`ldconfig`), the model in `/opt/zonewiki-model` (`ZONE_MODEL_DIR`), the binary as `/app/zonewiki`, the browser code as `/app/public`, `README.md` plus `docs/spec/` next to it (Zone's documentation, for the MCP tool `zone_help`), the licenses as `/app/LICENSE`, `/app/THIRD_PARTY_LICENSES` (Go, the Go modules, the model) and `/app/licenses/onnxruntime/` (the vendored libraries' are in `/app/public/vendor/`; Mermaid's bundled packages in `public/vendor/mermaid/THIRD_PARTY_LICENSES`, written by `docs/licenses/mermaid_notices.py`); `HOME=/tmp` (Zone may run as a UID without an account). Build args `ZONE_SEMANTIC_SEARCH` and `ZONE_BUILD` (last layer). About 0.23 GB, 0.9 GB with the model.
- **`compose.yaml`** — builds the image (`build:` with those args) and runs the `zonewiki` container (project and service `zonewiki`): `env_file: .env` passes every setting, `environment` adds `ZONE_DATA=/wiki-data` (overriding `.env`'s), `ZONE_HOST_DATA` (the host path, for the MCP tools' `file:` lines) and `TZ`; the port is `${ZONE_BIND:-127.0.0.1}:5000:5000`; the wiki is a bind mount with `create_host_path: false` (a mistyped path errors instead of starting an empty wiki) and `selinux: Z`; `init: true` (a minimal init as PID 1 reaps orphaned processes; with Zone as PID 1 they piled up as zombies, see Version history in `docs/spec/rendering.md`); `restart: unless-stopped`; `user: ${ZONE_UID:-1000}:${ZONE_GID:-1000}` (not root: Zone writes the wiki as the host user); a healthcheck, `/app/zonewiki health`, which fetches `/api/health` from localhost (port 5000; `zonewiki health :8080` for a Zone started with `--addr :8080`) (it fails while the settings file has an error Zone didn't take, see Reloading in `docs/spec/users.md`; the image has no curl or Python; reported by `compose ps`; restarting an unhealthy container is podman's `--health-on-failure` or an autoheal container's job); log driver `json-file` (Docker's default, podman's alias for k8s-file, so `logs -f` works on both). It works on its own (`docker compose up -d --build`), with `unknown` as the build and UTC unless `.env` sets `TZ`. With docker compose the settings reach the container through the API; podman-compose turns `env_file` into `podman create -e KEY=value` arguments, so there the secrets are on a command line for that moment.
- **`compose.nas.yaml`** — for a NAS or server without the repo: runs the prebuilt image `ghcr.io/gklingler/zonewiki:latest` (amd64 and arm64, built by GitHub's CI, see CI) instead of building it, so the machine needs only Docker with Compose. Unlike `compose.yaml`: no `build:`; `ZONE_DATA`, `ZONE_UID` and `ZONE_GID` are required (`${…:?message}`: compose stops with the message instead of guessing; a wrong UID would only show later as files Zone can't write); the port is `${ZONE_PORT:-5080}:5000` on every interface (not 5000 on the host: Synology's DSM uses it); no `selinux: Z` (NAS systems have no SELinux, and older compose versions reject the option); the log is capped (`max-size` 10m, 3 files). The rest as in `compose.yaml` (`init`, `restart`, healthcheck, `create_host_path: false`). See On a NAS or server.
- **`run.sh`** — the entry point, driving `<engine> compose` (podman if installed, else docker; a compose provider must be installed) for build/up/down/logs. It works out and exports what compose interpolates: `ZONE_DATA` from `.env` with `~` expanded (default `~/zonewiki`), `ZONE_BIND` from `ZONE_ALLOW_REMOTE` (`0.0.0.0`, else `127.0.0.1`), `ZONE_BUILD` from git (see Version), and `TZ` (`.env`'s, else the host's from `/etc/timezone` or the `/etc/localtime` link, else UTC), so "today" on the server (due count, tasks dates, note names, "Last changed") is the user's day. `ZONE_UID`/`ZONE_GID`: `id -u`/`id -g`; with rootless podman it adds `compose.podman.yaml` (`userns_mode: keep-id`: the host user keeps its UID inside, so Zone runs as you and the wiki's files stay yours); with rootless docker, which has no keep-id, 0 (the container's root is the host user there; another UID would be a subordinate one). `up` refuses a missing wiki folder or `.env`, then `compose up -d --build`. `test`/`e2e` use the engine directly (no `.env` needed), in the tools image. Platforms: Linux is the only tested one; meant to work also on macOS and, on Windows, in WSL2 (Docker Desktop and Podman run on it there). It sticks to bash 3.2 (macOS's stock bash: no `${x,,}`, no `declare -A`, no `mapfile`) and to options BSD tools share with GNU ones. `.gitattributes` keeps `*.sh` and `.githooks/*` at LF line endings, so a Windows checkout with `core.autocrlf` doesn't break them. Without the container, Zone itself should run on any OS with Python and git (it uses no Unix-only modules), though only Linux is tested.
- **`.env.example`** — the template for `.env` (`cp .env.example .env`): every setting with a comment, optional ones commented out, no real values. `tests/test_env_example.py` keeps it in step with the code: every `ZONE_*` name the source, `run.sh`, `compose.yaml` or `compose.nas.yaml` reads is in it, and nothing else is (a typo in `.env` is silently ignored).
- **`.env`** — the deployment's settings, read by `run.sh`, compose and Zone at start: `ZONE_DATA` (default `~/zonewiki`), `ZONE_ALLOW_REMOTE`, `TZ`, `ZONE_SEMANTIC_SEARCH`, and for compose without `run.sh` `ZONE_BIND`, `ZONE_UID`/`ZONE_GID`, `ZONE_PORT` (`compose.nas.yaml`); gitignored. With rootless podman, keep-id (`compose.podman.yaml`) runs Zone as the host user, so files written to the mounted wiki directory are the host user's.
- **`.zone/config`** — the wiki's own settings and its users (a password, MCP tokens, `hosts`, `base-url`, `git-remote`, `noshare`, `locale`, `date-format`, `query-log`, `semantic-min-score`, `trusted-proxies`, `upload-max`, `storage-max`), in the wiki, kept out of git (it holds the users: back it up with the data folder), reloaded when it changes, edited by hand or with `./run.sh user …`/`./run.sh config …` (The settings file in `docs/spec/users.md`).

## Commands

```bash
./run.sh            # build image and start Zone (default: "up")
./run.sh build      # build the container image only (compose build)
./run.sh up         # build and start in detached mode (compose up -d --build)
./run.sh down       # stop and remove the container (compose down)
./run.sh logs       # follow container logs
./run.sh test       # gofmt, go vet, go test, then the unit/API tests, in the tools image (extra args go to pytest; a tests/… path replaces the default whole suite but the browser tests)
./run.sh e2e        # run browser tests + tasks-core.js (Node) tests in the tools image (extra args go to pytest)
./run.sh leaks      # scan the git history for secrets with gitleaks, in the tools image (extra args go to `gitleaks git`; a leak exits with 3)
./run.sh screenshots  # redo the README's screenshots (docs/screenshots/*.png) from a sample wiki, in the tools image; compressed with pngquant (the README shows phone.png at 640px)
./run.sh eval [FILE]  # measure retrieval on your wiki (see Measuring retrieval in search.md), in the app image, with semantic search as ZONE_SEMANTIC_SEARCH says
./run.sh user add|passwd|token|role|disable|enable|remove NAME, user list  # the users in the wiki's settings file, zonewiki user in a one-off container (compose run; see users.md)
./run.sh config check|set|unset|passwd|token  # the wiki's settings file: check it, set a key, the password and MCP token without users (zonewiki config)
```

Zone is available at **http://localhost:5000** after `./run.sh up`.

## On a NAS or server

Any machine with Docker and Compose (a NAS, a Raspberry Pi, a small server) can run Zone from the prebuilt image, without the repo, git or a build: `compose.nas.yaml` and a `.env` in one folder, then `docker compose -f compose.nas.yaml up -d`. The image is `ghcr.io/gklingler/zonewiki`, for amd64 and arm64 (Docker picks the right one): `latest` follows `main`, a version tag (`1.2.0`, `1.2`) stays put, a short commit hash names one build. To update: `docker compose -f compose.nas.yaml pull`, then `up -d` again. The wiki is untouched by an update; it lives in the folder `ZONE_DATA` names.

`.env` needs (`.env.example` has them all):

- `ZONE_DATA`: the wiki folder, as an absolute path; it must exist (create it first, empty or with Markdown files in it).
- `ZONE_UID`, `ZONE_GID`: the owner of that folder (`id -u` and `id -g` as that user, or `ls -nd <folder>`). Zone runs as that user and writes the wiki as it.
- `TZ`, e.g. `Europe/Vienna`, so "today" is your day (else UTC).
- Optional: `ZONE_PORT` (the host's port, default 5080), `ZONE_SEMANTIC_SEARCH=on` (semantic search: the model is in the image; plan on roughly 1 GB more RAM).

The wiki's own settings go in its settings file (`.zone/config`, `docs/spec/users.md`), through the container, in the folder of `compose.nas.yaml` and `.env` (no repo needed):

```bash
docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki config set hosts nas.local, 192.168.1.20, wiki.example.org
docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki user add alice     # or: config passwd, one password
```

The login needs no command: opened in a browser, a wiki without one asks for a password first (First run in `docs/spec/security.md`; `compose.nas.yaml` tells Zone its port is on the LAN, `ZONE_BIND: 0.0.0.0`), and shows nothing before. The commands above set it just as well.

`hosts` are the names and addresses you open Zone by (the Host check, Security in `docs/spec/security.md`); without them only localhost gets in. `ZONE_ALLOW_REMOTE=1` in `.env` instead skips the check: quicker, but then any name reaches Zone, which DNS rebinding can exploit. Until a login is set Zone shows only its first-run page (`no-login = yes` in the settings file turns that off, for a wiki open on purpose); `config token` (or `user token NAME`) makes a token for AI clients.

Zone is then at `http://<nas>:5080`. Over the internet, put HTTPS in front (a reverse proxy, below); the session cookie is `Secure` when the proxy sends `X-Forwarded-Proto: https`.

### Synology

With Container Manager (DSM 7.2 or newer; Docker on older DSM works the same over SSH), on an x86 or ARM64 model.

1. **Folders**: in File Station, a folder for the project, e.g. `/volume1/docker/zonewiki`, and in it the wiki folder, e.g. `/volume1/docker/zonewiki/wiki`.
2. **UID and GID**: the user who owns them. DSM doesn't show IDs; with SSH on (Control Panel → Terminal & SNMP), log in as that user and run `id`. The first admin user is usually `uid=1026`, and group `users` is `gid=100`.
3. **Files**: put `compose.nas.yaml` into the project folder as `compose.yaml` (Container Manager looks for that name), and next to it a `.env` with at least `ZONE_DATA=/volume1/docker/zonewiki/wiki`, `ZONE_UID`, `ZONE_GID` and `TZ`; then, over SSH in the project folder, `hosts` (the NAS's name and LAN address) with the command above; or, without SSH, `ZONE_ALLOW_REMOTE=1` in `.env`. The login is set in the browser, on the page Zone shows first.
4. **Project**: Container Manager → Project → Create, name `zonewiki`, path the project folder; it finds `compose.yaml` there. Finish the wizard with the project set to start once created; the first start pulls the image (about 1.1 GB).
5. **HTTPS** (optional): Control Panel → Login Portal → Advanced → Reverse Proxy: source `HTTPS`, your host name, port 443; destination `HTTP`, `localhost`, port 5080. Under Custom Header, add `X-Forwarded-Proto` = `$scheme` if DSM doesn't send it. Add the host name to `hosts` (`config set hosts …`), and a certificate for it (Control Panel → Security → Certificate). The proxy reaches the container through Docker's network, so Zone sees it as the network's gateway (`172.17.0.1` or another in `172.16.0.0/12`); Zone's log names it on the first proxied request ("requests from … carry X-Forwarded-For"), and `config set trusted-proxies <that address>` lets Zone tell the proxy's clients apart when it throttles wrong passwords (without it they count as one).

Updates: over SSH, `docker compose pull && docker compose up -d` in the project folder (Container Manager's Image list may also offer the update for `ghcr.io/gklingler/zonewiki`).

## CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on every push and pull request a **checks** job per `run.sh` command, as on your machine: **test** (gofmt, go vet, go test, the unit/API tests), **e2e** (browser and Node tasks-core tests) and **leaks** (gitleaks over every commit; the checkout fetches the full history), all in the tools image (`Containerfile.tools`). So CI keeps no package lists or test commands of its own. `run.sh` builds each image quietly (the build's output, which podman's `-q` doesn't hide for `RUN` steps, goes to a log shown only when the build fails; a first build, with no image of that tag yet, prints one line saying so) and runs it by ID, so parallel jobs on one engine can't swap a shared tag. With `ZONE_COPY_REPO=1` (set by CI) it copies the repo into the container (`create --rm`, `cp`, `start -a`; the engine removes the container even when a cancelled job kills `run.sh`) instead of bind-mounting it, since the checkout lives in the job container while the engine is the host's; nothing the command writes comes back. Only **leaks** fetches the full history; the others check out the last commit. On `main` and `v*` tags, once all three pass, **image** builds the `Containerfile` with `ZONE_SEMANTIC_SEARCH=on` and the `ZONE_BUILD` string `run.sh` would compute, and pushes it to the Forgejo registry as `<REGISTRY>/<owner>/<repo>:<short-sha>` (the repository's name, lowercase; repo variable `REGISTRY`, Forgejo's public host, e.g. `git.example.org`: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`; without the variable the image job is skipped, so forks just test), plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`). All jobs run in the official `docker:cli` image (plus `nodejs`, `git`, `bash` from apk) and build against the host engine (the checks through `run.sh`, the image job with plain `docker build -f Containerfile`/`docker push`) through the socket mounted into job containers (runner label `docker`, runner config `container.docker_host: automount`; the host's engine trusts the internal certificate via `/etc/docker/certs.d/<REGISTRY>/ca.crt`, which the runner also mounts read-only into job containers via `container.options`; checkout goes over the runner's internal `http://web:3000`, so no job needs a `ca-certificates` package), so layers are cached between runs. Repo settings: variable `REGISTRY` (the registry host; required for the image), secret `REGISTRY_TOKEN` (token with package write scope), optional variable `REGISTRY_USER` (default: the repo owner).

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs the same **checks** (`./run.sh test`, `e2e`, `leaks`) on GitHub's `ubuntu-26.04` runners, through `run.sh` (with podman, which the runners have and `run.sh` prefers; the checkout is on the runner, so no `ZONE_COPY_REPO`). On `main` and `v*` tags, once they pass, it publishes the image `compose.nas.yaml` runs: **image** builds the `Containerfile` (`ZONE_SEMANTIC_SEARCH=on`, the `ZONE_BUILD` string) once per architecture, each natively on its own runner (`ubuntu-26.04` for amd64, `ubuntu-26.04-arm` for arm64; no emulation, so the arm64 build compiles with cgo and fetches onnxruntime's arm64 build and the model as fast as the other), pushes it to `ghcr.io/<owner>/zonewiki` by digest only (cached between runs in GitHub's cache, a scope per architecture), and **manifest** joins the two digests under the tags: `latest` (main), the short commit hash, and on a tag `v1.2.0` also `1.2.0` and `1.2`. It logs in with the workflow's own `GITHUB_TOKEN` (`packages: write`), so the repo needs no secrets. The image carries `org.opencontainers.image.source`, which links the package to the repo. A new package on ghcr.io is private: make it public once in its settings on GitHub, so a NAS pulls without logging in.

## Network access

`ZONE_ALLOW_REMOTE` is the single knob for network access (with `run.sh`). When unset, `run.sh` exports `ZONE_BIND=127.0.0.1`, so the container's port 5000 is only exposed on the host's loopback interface, and the host-header check rejects any `Host` header but localhost and the settings file's `hosts`. When `ZONE_ALLOW_REMOTE=1`, `run.sh` exports `ZONE_BIND=0.0.0.0` (LAN-accessible port binding) and the host-header check is skipped unless `hosts` is set. `compose.yaml` maps `${ZONE_BIND:-127.0.0.1}:5000:5000`; used without `run.sh`, `ZONE_BIND=0.0.0.0` must be set in `.env` alongside `ZONE_ALLOW_REMOTE=1`.

Optional git remote: `./run.sh config set git-remote git@github.com:user/wiki.git`. At start and when it changes, `setGitRemote()` adds or updates `origin` to that URL, so `git push` works from the terminal.

## Mobile / phone access

Set `ZONE_ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces — `run.sh` then maps `0.0.0.0:5000` and the server's host-header check is skipped. Phones on the same network reach Zone at `http://<host-LAN-IP>:5000`. The UI is responsive down to phone widths and the editor works on mobile. The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one. Without it, full-screen views (the tasks edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard. iOS Safari ignores the setting. There is no auth by default — for LAN use set a password (`./run.sh config passwd`) or users (`./run.sh user add NAME`) to get a login gate.

## Version

The shortcuts modal (`?`) also links to Zone's source (`sourceURL` in `go/config.go`) and names its license, AGPL-3.0-or-later: whoever runs a changed Zone for others over a network has to offer them its source, so a fork points that constant at its own copy.

Zone's version is a semver constant, `version` in `go/config.go`, bumped by hand. Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `go/` or `public/` had uncommitted changes (a commit can't contain its own hash, so this part can't be a committed constant). The shortcuts modal (`?`) shows both (`Zone 1.0.0 · c28ec9e 2026-09-26 19:22`), every page's bottom-left corner the version and short hash (`Zone 1.0.0 · c28ec9e`, `+dirty` when so, the full build on hover; `.zone-build` in `layout.html`, faint, only from 1140px wide where the margin beside the text column has room, not in the editor), the `<meta name="generator">` tag the version, and `/api/version` returns `{ version, build }`. The image has no `.git`, so `run.sh` computes the build string and passes it as the `ZONE_BUILD` build arg (the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer). Without the container, the binary has the same string from what `go build` recorded of the checkout (`sourceBuild()`: the commit, its time, whether the tree had changes); `$ZONE_BUILD` wins when set, and with neither the modal shows the version alone.

## Home-screen app

`public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `templates/layout.html`) give "Add to home screen" a proper Zone name and icon. Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie. The PNGs are rendered once from `public/icon.svg` (full-bleed green square, a white Z inside the central 80% "maskable" safe zone) and committed, so there is still no build step. To change the icon, edit the SVG and re-render the PNGs, e.g. with a screenshot in the Playwright image (`zonewiki-tools`). Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab. iOS Safari is more lenient.

## Data directory

Set `ZONE_DATA` in `.env` to any host path (default: `~/zonewiki`). `run.sh` expands a leading `~` to your home directory before starting the container. The folder must exist: neither `run.sh up` nor Zone creates it (`loadConfig` exits with "no wiki at …"), so a mistyped path can't start an empty wiki somewhere else. Started without the container, Zone needs `ZONE_DATA` set (there is no default; unset or empty, it exits with "ZONE_DATA isn't set"), and the folder must exist too. In an existing wiki folder Zone creates what it needs (`_assets/`, `_tasks/`, `index.md`):

```
ZONE_DATA=~/zonewiki    # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`. Layout:
- `$ZONE_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$ZONE_DATA/private/<user>/` — with users, each user's private pages (`docs/spec/users.md`); without users an ordinary folder
- `$ZONE_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically); a private page's in `private/<user>/_assets/`
- `$ZONE_DATA/_tasks/` — the tasks (`tasks.json`, `completed.json`, `completed-YYYY.json`, and `settings.json` for the settings of All tasks; see Storage in `docs/spec/tasks.md`); each user's private tasks in `private/<user>/_tasks/`
- `$ZONE_DATA/.zone/` — Zone's own state: none of it in git: `config` (the settings file: the wiki's settings and users, `docs/spec/users.md`), `secret_key` (session key), `signed-out` (the sessions signed out, `docs/spec/security.md`) and `index/` (the semantic search cache, rebuildable)

Uploaded image URLs use `/_assets/<filename>` (served by `serveUpload` from `cfg.Uploads`), not the static file path.

## How big a wiki can be

Zone keeps no index of the wiki's pages or tasks in memory beyond per-page caches: a page view walks the wiki's folders and looks at every page's links (cached by the file's mtime, so one `stat` a page) for its backlinks, and reads the open tasks' file whole, once a request (`rq.openTasks`), for the Tasks links' counts and the page's linked tasks; the tasks API reads the task files whole. So a request grows with the wiki. Measured through the real handlers (`go/bench_test.go`; `go test -run '^$' -bench . -benchtime 20x` in `go/`), warm, on a 16-core desktop in 2026-11:

| Request | Wiki | Time |
|---------|------|------|
| A page view | 1,000 pages, 500 open tasks | 4 ms |
| A page view | 5,000 pages | 10 ms |
| A page view | 20,000 pages | 50 ms |
| A page view | 1,000 pages, 5,000 open and 20,000 completed tasks | 27 ms |
| `GET /api/tasks` | 500 open tasks | 3 ms |
| `GET /api/tasks` | 5,000 open tasks | 31 ms |
| The page list (`/api/pages-list`) | 1,000 pages | 3 ms |
| The page list | 20,000 pages | 92 ms |

A personal or a small team's wiki is far below where this is felt; past some ten thousand pages or open tasks every view pays for it, and a NAS's disk and CPU bring that point closer. Page writes run one at a time (`pagesMu`), each with its git commit (a few git processes), so a wiki is not for many people saving at once. Completed tasks cost nothing until they are looked at: a page view doesn't read their files. What would lift the ceiling, when it is needed: the pages' mtimes taken from the walk instead of a `stat` each (`pageStats`), and the parsed task lists kept by their files' mtimes.

## From Zone 1 (or Leaf)

Zone 2 reads only today's names; what Zone 1 still answered to from before its renames (Leaf, Deck, Scratch) is gone. Start the last Zone 1 release once on the wiki first: it moves `.leaf/` to `.zone/` and `_deck/` to `_tasks/`, and takes the settings file out of git. The rest is by hand:

- `.env`: `LEAF_…` variables are `ZONE_…`; the wiki's settings (password, users, hosts …) are in `.zone/config`, not there.
- Pages: `[[deck …]]` is `[[tasks: …]]` (in the wiki's folder: `grep -rlZi '\[\[deck' --include='*.md' . | xargs -0 sed -i -E 's/\[\[deck\]\]/[[tasks:]]/Ig; s/\[\[deck[[:space:]]+/[[tasks: /Ig'`, then commit), and `home=1` in one is `pin=1`.
- Addresses: `/deck`, `/api/deck/…`, `/api/scratch` and `/page/_scratch` are `/tasks`, `/api/tasks/…`, `/api/notes` and `/notes`; the keys `g s`, `g d` and `c s` are `g n`, `g a` and `c n`.
- MCP: the tools `leaf_help` and `file_scratch_note` are `zone_help` and `file_note`, and their `scratch` argument is `note` (write_page) or `notes` (search_wiki, list_pages).
- The image is `ghcr.io/gklingler/zonewiki`, its binary `/app/zonewiki`.
