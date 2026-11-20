# Deployment

Part of Zone's spec (see `AGENTS.md` for the index).

How Zone is built and run: the files that deploy it (`Containerfile`, the compose files, `run.sh`, `.env`), the commands,
running it without `run.sh` or without a container, the release binaries, on a NAS or server, CI on Forgejo and GitHub,
network access (a Unix socket, systemd), mail, phone access, the version string, the home-screen app,
the data directory (its data format) and its backups, how Zone packs the wiki's git repository, how big a wiki can be, and moving from Zone 1.

## Files

| File | What it is |
|------|------------|
| `Containerfile` | The image, in three stages |
| `compose.yaml` | Builds the image and runs the `zonewiki` container |
| `compose.podman.yaml` | Rootless podman only: `userns_mode: keep-id` (`run.sh` adds it) |
| `compose.nas.yaml` | A NAS or server without the repo: runs the prebuilt image |
| `run.sh` | The entry point: drives compose, runs the tests in the tools image |
| `.env.example` | The template for `.env` |
| `.env` | The deployment's settings (gitignored) |
| `.zone/config` | The wiki's own settings and its users, in the wiki |

### Containerfile

Three stages:

| Stage | What it does |
|-------|--------------|
| `build` | Go (a pinned release, checked against its checksum) builds the server from `go/` with cgo, Zone's own files built in |
| `semantic` | With `ZONE_SEMANTIC_SEARCH=on`, onnxruntime's library and the model's files; off, three empty folders |
| the image | Ubuntu 26.04 LTS with what the two stages made |

`build` runs `go mod download` first, so a code change doesn't fetch the modules again;
it copies the root's `go.mod` beside `go/` for it, the module of Zone's own files (What the binary holds, below).
Then it copies what `zonefiles.go` builds in (the browser code, the docs, the licenses) and the server's code, and builds.

`semantic` fetches onnxruntime's shared library (1.31.0, its GitHub release for the image's architecture) and the model's files (a fixed revision),
each checked against its SHA-256,
plus onnxruntime's license and third-party notices.
It is its own stage, copying nothing from the repo, so a code change never downloads the model again.

The image has:

- git, `tzdata` and CA certificates via `apt`;
- `libonnxruntime.so` in `/usr/local/lib` (`ldconfig`);
- the model in `/opt/zonewiki-model` (`ZONE_MODEL_DIR`);
- the binary as `/app/zonewiki`, with the browser code and the docs built in (no `public/` beside it: it serves its own copies);
- the licenses as `/app/LICENSE`,
  `/app/THIRD_PARTY_LICENSES` (Go, the Go modules, the model) and `/app/licenses/onnxruntime/`
  (the vendored libraries' are in the binary, with its copy of `public/vendor/`: `zonewiki licenses` prints them all;
  Mermaid's bundled packages in `public/vendor/mermaid/THIRD_PARTY_LICENSES`, written by `docs/licenses/mermaid_notices.py`);
- `HOME=/tmp` (Zone may run as a UID without an account).

Build args: `ZONE_SEMANTIC_SEARCH` and `ZONE_BUILD` (last layer).
The image is about 0.23 GB, 0.9 GB with the model.

### compose.yaml

It builds the image (`build:` with those args) and runs the `zonewiki` container (project and service `zonewiki`).
It works on its own (`docker compose up -d --build`), with `unknown` as the build and UTC unless `.env` sets `TZ`.

| Setting | Value and why |
|---------|---------------|
| `env_file: .env` | Passes every setting |
| `environment` | Adds `ZONE_DATA=/wiki-data` (overriding `.env`'s), `ZONE_HOST_DATA` and `TZ` |
| port | `${ZONE_BIND:-127.0.0.1}:5000:5000` |
| the wiki | A bind mount with `create_host_path: false` and `selinux: Z` |
| `init: true` | A minimal init as PID 1 reaps orphaned processes |
| `restart: unless-stopped` | |
| `user: ${ZONE_UID:-1000}:${ZONE_GID:-1000}` | Not root: Zone writes the wiki as the host user |
| healthcheck | `/app/zonewiki health` |
| log driver | `json-file` |

- `ZONE_HOST_DATA` is the host path, for the MCP tools' `file:` lines.
- `create_host_path: false`: a mistyped path errors instead of starting an empty wiki.
- `init: true`: with Zone as PID 1 orphaned processes piled up as zombies (see Packing the repository, below).
- The healthcheck, `/app/zonewiki health`, fetches `/api/health` from localhost (port 5000;
  `zonewiki health :8080` for a Zone started with `--addr :8080`, `zonewiki health unix:PATH` for one on a socket).
  It fails while the settings file has an error Zone didn't take (see Reloading in `docs/spec/users.md`).
  The image has no curl or Python.
  It is reported by `compose ps`;
  restarting an unhealthy container is podman's `--health-on-failure` or an autoheal container's job.
- The log driver `json-file` is Docker's default and podman's alias for k8s-file, so `logs -f` works on both.

With docker compose the settings reach the container through the API;
podman-compose turns `env_file` into `podman create -e KEY=value` arguments, so there the secrets are on a command line for that moment.

### compose.nas.yaml

For a NAS or server without the repo: it runs the prebuilt image `ghcr.io/zonewiki/zonewiki:latest` (amd64 and arm64, built by GitHub's CI, see CI (GitHub Actions)) instead of building it,
so the machine needs only Docker with Compose.
See On a NAS or server.

Unlike `compose.yaml`:

- no `build:`;
- `ZONE_DATA`, `ZONE_UID` and `ZONE_GID` are required (`${…:?message}`: compose stops with the message instead of guessing;
  a wrong UID would only show later as files Zone can't write);
- the port is `${ZONE_PORT:-5080}:5000` on every interface (not 5000 on the host: Synology's DSM uses it);
- no `selinux: Z` (NAS systems have no SELinux, and older compose versions reject the option);
- the log is capped (`max-size` 10m, 3 files).

The rest is as in `compose.yaml` (`init`, `restart`, healthcheck, `create_host_path: false`).

### run.sh

The entry point, driving `<engine> compose` (podman if installed, else docker; a compose provider must be installed) for build/up/down/logs (its commands: Commands, below).
It works out and exports what compose interpolates:

| Variable | From |
|----------|------|
| `ZONE_DATA` | `.env`, with `~` expanded (default `~/zonewiki`) |
| `ZONE_BIND` | `ZONE_ALLOW_REMOTE`: `0.0.0.0`, else `127.0.0.1` |
| `ZONE_BUILD` | git (see Version) |
| `TZ` | `.env`'s, else the host's from `/etc/timezone` or the `/etc/localtime` link, else UTC |
| `ZONE_UID`/`ZONE_GID` | `id -u`/`id -g`; 0 with rootless docker |

- `TZ` makes "today" on the server (due count, tasks dates, note names, "Last changed") the user's day.
- With rootless podman it adds `compose.podman.yaml` (`userns_mode: keep-id`: the host user keeps its UID inside, so Zone runs as you and the wiki's files stay yours).
- With rootless docker, which has no keep-id, `ZONE_UID`/`ZONE_GID` are 0 (the container's root is the host user there; another UID would be a subordinate one).

`up` refuses a missing wiki folder or `.env`, then runs `compose up -d --build`.
`test`/`e2e` use the engine directly (no `.env` needed), in the tools image.

Platforms: Linux is the only tested one; it is meant to work also on macOS and, on Windows, in WSL2 (Docker Desktop and Podman run on it there).
It sticks to bash 3.2 (macOS's stock bash: no `${x,,}`, no `declare -A`, no `mapfile`) and to options BSD tools share with GNU ones.
`.gitattributes` keeps `*.sh` and `.githooks/*` at LF line endings, so a Windows checkout with `core.autocrlf` doesn't break them.
Without the container, Zone itself should run on any OS Go builds for, with git (Without a container, below), though only Linux is tested.

### .env.example

The template for `.env` (`cp .env.example .env`): every setting with a comment, optional ones commented out, no real values.
`tests/test_env_example.py` keeps it in step with the code: every `ZONE_*` name the source, `run.sh`, `compose.yaml` or `compose.nas.yaml` reads is in it, and nothing else is (a typo in `.env` is silently ignored).

### .env

The deployment's settings, read by `run.sh`, compose and Zone at start; gitignored.

| Setting | What it sets |
|---------|--------------|
| `ZONE_DATA` | The wiki's folder (default `~/zonewiki`; Data directory, below) |
| `ZONE_ALLOW_REMOTE` | Network access (Network access, below) |
| `TZ` | The timezone |
| `ZONE_SEMANTIC_SEARCH` | Semantic search at build time |
| `ZONE_PUBLIC` | `view` or `edit`: the wiki is shown, or open to edits, without signing in |
| `ZONE_SIGNUP`, `ZONE_SMTP_*`, `ZONE_MAIL_FROM` | Sign-up and its mail server (Mail, below) |
| `ZONE_BIND`, `ZONE_UID`/`ZONE_GID` | For compose without `run.sh` |
| `ZONE_PORT` | The host's port for `compose.nas.yaml` |

`ZONE_PUBLIC` is described in Public wikis in `docs/spec/security.md`.
With rootless podman, keep-id (`compose.podman.yaml`) runs Zone as the host user, so files written to the mounted wiki directory are the host user's.

### .zone/config

The wiki's own settings and its users
(a password, MCP tokens, `hosts`, `base-url`, `git-remote`, `noshare`, `locale`, `date-format`, `query-log`, `semantic-min-score`, `trusted-proxies`, `upload-max`, `storage-max`, `external-content`, `no-login`, `mcp-oauth`, `signup-domains`, `signup-role`, `signup-approval`),
in the wiki,
kept out of git (it holds the users: back it up with the data folder),
reloaded when it changes,
edited by hand or with `./run.sh user …`/`./run.sh group …`/`./run.sh config …` (The settings file in `docs/spec/users.md`).

## Commands

| Command | What it does |
|---------|--------------|
| `./run.sh` | Build the image and start Zone (default: "up") |
| `./run.sh build` | Build the container image only (`compose build`) |
| `./run.sh up` | Build and start in detached mode (`compose up -d --build`) |
| `./run.sh down` | Stop and remove the container (`compose down`) |
| `./run.sh logs` | Follow the container's logs |
| `./run.sh test` | gofmt, go vet, go test -race, then the unit/API tests, in the tools image |
| `./run.sh e2e` | The browser tests and the tasks-core.js (Node) tests, in the tools image |
| `./run.sh bench` | The tasks at a team's size against their budgets, then against a race build |
| `./run.sh retrieval` | Search on the sample wiki (`tests/retrieval`) against its floor |
| `./run.sh binaries` | The release binaries into `dist/`, in the tools image (Release binaries, below) |
| `./run.sh leaks` | Scan the git history for secrets with gitleaks, in the tools image |
| `./run.sh screenshots` | Redo the README's screenshots from a sample wiki, in the tools image |
| `./run.sh eval [FILE]` | Measure retrieval on your wiki, in the app image |
| `./run.sh user …` | The users in the wiki's settings file (`zonewiki user`) |
| `./run.sh group …` | Groups made of groups and a group's owners (`zonewiki group`) |
| `./run.sh config …` | The wiki's settings file (`zonewiki config`) |

- `test`: extra args go to pytest; a tests/… path replaces the default whole suite but the browser tests.
- `e2e`: extra args go to pytest.
- `bench`: `tests/bench_tasks.py`, then once more against a server built with the race detector;
  `ZONE_BENCH_SLACK=3` allows three times the time budgets (The bench in `docs/spec/testing.md`).
- `retrieval`: by keywords and by meaning, in the app's image with the model.
- `binaries`: Linux and macOS, amd64 and arm64, with `SHA256SUMS`, `LICENSE` and `THIRD_PARTY_LICENSES`; CI's release job runs it on a `v*` tag.
- `leaks`: extra args go to `gitleaks git`; a leak exits with 3.
- `screenshots`: `docs/screenshots/*.png`, compressed with pngquant (the README shows phone.png at 640px).
- `eval`: see Measuring retrieval in `docs/spec/search.md`; with semantic search as `ZONE_SEMANTIC_SEARCH` says.
- `user`: `user add|passwd|token|role|email|group|disable|enable|invite|reset|remove NAME`, `user list`, in a one-off container (compose run; see `docs/spec/users.md`).
- `group`: `group includes NAME [+G] [-G]`, `group owners NAME [+U] [-U]`, `group list`.
- `config`: `config check|set|unset|passwd|token`: check it, set a key, the password and MCP token without users.

Zone is available at **http://localhost:5000** after `./run.sh up`.
To update: `git pull && ./run.sh up`.
To point Zone at an existing folder of Markdown notes, set `ZONE_DATA` to it: Zone makes it a git repository (if it isn't one) and adds a home page.

## Without run.sh

`run.sh` fills in what `compose.yaml` can't work out itself: the commit the image is built from, the timezone, the user ID and the address from `ZONE_ALLOW_REMOTE`.
To run `docker compose up -d --build` directly, set in `.env`:

- `ZONE_DATA` as an absolute path (no `~`);
- `ZONE_BIND=0.0.0.0` together with `ZONE_ALLOW_REMOTE=1`, for network access;
- `ZONE_UID` and `ZONE_GID` (`id -u`, `id -g`) if they aren't 1000;
- `TZ`, or the container runs on UTC.

With rootless Podman, add the file that keeps your user ID inside the container: `podman compose -f compose.yaml -f compose.podman.yaml up -d --build`.

## Without a container

With Go 1.27 or newer, a C compiler (for semantic search; `CGO_ENABLED=0` builds without it) and git:

```bash
(cd go && go build -o zonewiki .)
go/zonewiki                          # the wiki in ~/zonewiki; ZONE_DATA=/path/to/wiki go/zonewiki for another
```

In Windows PowerShell: `cd go; go build -o zonewiki.exe .; cd ..`, then `$env:ZONE_DATA="$HOME\zonewiki"; go\zonewiki.exe`.
For semantic search, onnxruntime's library must be where the loader finds it (`docs/spec/search.md`).
A port another program has stops Zone with what to do (`portTaken` in `go/server.go`): on a Mac, AirPlay Receiver listens on 5000, so `--addr :5001`.
`zonewiki --help` lists the flags, the commands (`user`, `group`, `config`, `eval`, `health`, `manual`, `licenses`; each takes `--help` too) and the environment.

### What the binary holds

The binary carries Zone's own files, so it is all a machine needs besides git:
the browser code (`public/`), the docs Help and `zone_help` show (`README.md`, `docs/manual/*.md`, `docs/spec/*.md`, `docs/screenshots/*.png`)
and the licenses (`LICENSE`, `THIRD_PARTY_LICENSES`; the vendored libraries' are in `public/vendor/`).
`zonewiki licenses` prints every license file it holds, each under its path, so a binary passed on alone still carries them.

A folder on disk wins over the copy built in, so a checkout serves its files as they are edited, without a rebuild (`go/files.go`):

| Files | From the first of |
|-------|-------------------|
| The browser code | `--public FOLDER`; `public/` beside the binary, or in the checkout it was built in (`go/zonewiki` → `public/`); the binary's copy |
| The docs | `--docs FOLDER` (a folder without them: none, and Help says so); the folder the browser code's folder is in, when `docs/spec/` is there; the binary's copy |

A `public/` found from the binary counts only when it holds Zone's entry module, `zonewiki.js`:
on a Mac's case-insensitive disk, `../public` of a binary in `~/Downloads` is `~/Public`, which Zone would otherwise serve.

Both are served alike (`go/static.go`): a file's type by its extension, `Last-Modified` (for the binary's copies, which have no time of their own, the time of the binary's file, so a browser asks again after an update),
`/public/vendor/` scripts and styles gzipped,
a folder or a missing file a 404,
and a name with `..`, an empty part or a NUL refused before it is looked up.

The files are built in by `go:embed` in `zonefiles.go` at the repository's root, package `zonefiles`:
`go:embed` reaches only files below its package's folder, and the server's module is `go/`.
The root is a module of its own (`go.mod` there, `module zonefiles`), which `go/go.mod` requires and takes from `../` (`replace zonefiles => ../`),
so `go build` in `go/` from a fresh clone builds it in, with no step before it.

## Release binaries

Each `v*` tag's GitHub release carries Zone as one file per platform, for a desktop or a server without Docker:

| File | For |
|------|-----|
| `zonewiki-linux-amd64` | Linux on a PC or server (x86-64) |
| `zonewiki-linux-arm64` | Linux on ARM (64-bit: a Raspberry Pi 4 or 5, an ARM server) |
| `zonewiki-macos-arm64` | A Mac with Apple silicon |
| `zonewiki-macos-amd64` | A Mac with an Intel processor |
| `SHA256SUMS` | The files' SHA-256 sums (`sha256sum -c SHA256SUMS --ignore-missing`; on a Mac `shasum -a 256 -c`) |
| `LICENSE`, `THIRD_PARTY_LICENSES` | Zone's license and the notices of what the binaries hold |

How to use one is in the manual (Download the binary in `docs/manual/self-hosting.md`): download, `chmod +x`, run, open `http://localhost:5000`.

### What a release binary is

- Zone's server with its own files built in (What the binary holds, above): the browser code, Help's docs, the licenses (`zonewiki licenses`).
- Built without cgo (`CGO_ENABLED=0`), so statically linked on Linux and without semantic search: it searches by keywords (`go/embed_nocgo.go`), as the images CI publishes do for now (Semantic search paused, below).
- 22 to 24 MB each (`-trimpath -ldflags="-s -w"`); the build string is the tagged commit and its date, from `go build`'s own record of the checkout (`sourceBuild`, Version below).
- It needs git at run time, for the wiki's history: Zone looks for it on `PATH` at start (`exec.LookPath`) and without it runs with no history, versions or undo.
- Started with nothing set, the wiki is `~/zonewiki`, made at the first start (Data directory, below), and it listens on port 5000, printing where to open it.
  On localhost it needs no login; reached from other devices it shows only its first-run page until one is set, as ever (First run in `docs/spec/security.md`).
- It isn't signed or notarized: macOS keeps a downloaded one from starting until the quarantine mark is taken off (`xattr -d com.apple.quarantine zonewiki`).
  The arm64 Mac binary carries the ad-hoc signature Go's linker gives every darwin/arm64 binary, which Apple silicon requires to run code at all.
- No Windows binary yet: a slug would take Windows' `\` from a page's path.

### How CI builds them

`./run.sh binaries` builds the four in the tools image, with its Go (pinned and checked against its checksum, the same as the `Containerfile`'s: `tests/test_image.py`),
`GOOS`/`GOARCH` set for each (Go cross-compiles without cgo), into `dist/` (gitignored), then copies `LICENSE` and `THIRD_PARTY_LICENSES` beside them and writes `SHA256SUMS` for all six.
It passes git `safe.directory`, so the build string comes from the checkout even when the repo's owner isn't the container's user.
Run in a git worktree, the container can't reach the repository's own `.git`, and the build string is `unknown`.

GitHub's **release** job (CI (GitHub Actions), below) runs on a `v*` tag once the checks pass:
it runs `./run.sh binaries`, makes the tag's release when it has none (`gh release create --verify-tag`, titled `Zone <version>`, with GitHub's generated notes)
and attaches `dist/*` to it (`gh release upload --clobber`, so a run again replaces them),
with `gh`, which GitHub's runners have, and the workflow's token (`contents: write`): no action of a third party, no secret.
A release already made for the tag (by hand, with notes of its own) is kept, and gets the files.
Forgejo's workflow builds no binaries.

## On a NAS or server

Any machine with Docker and Compose (a NAS, a Raspberry Pi, a small server) can run Zone from the prebuilt image, without the repo, git or a build:
`compose.nas.yaml` and a `.env` in one folder, then `docker compose -f compose.nas.yaml up -d`.
The image is `ghcr.io/zonewiki/zonewiki`, for amd64 and arm64 (Docker picks the right one):
`latest` follows `main`, a version tag (`1.2.0`, `1.2`) stays put, a short commit hash names one build.
To update: `docker compose -f compose.nas.yaml pull`, then `up -d` again.
The wiki is untouched by an update; it lives in the folder `ZONE_DATA` names.

`.env` needs (`.env.example` has them all):

- `ZONE_DATA`: the wiki folder, as an absolute path; it must exist (create it first, empty or with Markdown files in it).
- `ZONE_UID`, `ZONE_GID`: the owner of that folder (`id -u` and `id -g` as that user, or `ls -nd <folder>`).
  Zone runs as that user and writes the wiki as it.
- `TZ`, e.g. `Europe/Vienna`, so "today" is your day (else UTC).
- Optional: `ZONE_PORT` (the host's port, default 5080).
  `ZONE_SEMANTIC_SEARCH=on` does nothing with the prebuilt image for now: it has no semantic search since 2026-11-18 (Semantic search paused, below), so Zone searches by keywords.

The wiki's own settings go in its settings file (`.zone/config`, `docs/spec/users.md`), through the container, in the folder of `compose.nas.yaml` and `.env` (no repo needed):

```bash
docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki config set hosts nas.local, 192.168.1.20, wiki.example.org
docker compose -f compose.nas.yaml run --rm zonewiki /app/zonewiki user add alice     # or: config passwd, one password
```

The login needs no command: opened in a browser, a wiki without one asks for a password first
(First run in `docs/spec/security.md`; `compose.nas.yaml` tells Zone its port is on the LAN, `ZONE_BIND: 0.0.0.0`),
and shows nothing before.
The commands above set it just as well.

`hosts` are the names and addresses you open Zone by (The Host check in `docs/spec/security.md`);
without them only localhost gets in, and a browser opening Zone by another name gets a 400 that says to add it (the log names it too).
`ZONE_ALLOW_REMOTE=1` in `.env` instead lets in any IP address (`http://192.168.1.20:5000`) with nothing to set up; a name (`nas.local`) still goes in `hosts`.
Until a login is set Zone shows only its first-run page (`no-login = yes` in the settings file turns that off, for a wiki open on purpose);
`config token` (or `user token NAME`) makes a token for AI clients.

Zone is then at `http://<nas>:5080`.
Over the internet, put HTTPS in front (a reverse proxy, below); the session cookie is `Secure` when the proxy sends `X-Forwarded-Proto: https`.

### Synology

With Container Manager (DSM 7.2 or newer; Docker on older DSM works the same over SSH), on an x86 or ARM64 model.

1. **Folders**: in File Station, a folder for the project, e.g. `/volume1/docker/zonewiki`, and in it the wiki folder, e.g. `/volume1/docker/zonewiki/wiki`.
2. **UID and GID**: the user who owns them.
   DSM doesn't show IDs; with SSH on (Control Panel → Terminal & SNMP), log in as that user and run `id`.
   The first admin user is usually `uid=1026`, and group `users` is `gid=100`.
3. **Files**: put `compose.nas.yaml` into the project folder as `compose.yaml` (Container Manager looks for that name), and next to it a `.env` with at least `ZONE_DATA=/volume1/docker/zonewiki/wiki`, `ZONE_UID`, `ZONE_GID` and `TZ`;
   then, over SSH in the project folder, `hosts` (the NAS's name and LAN address) with the command above;
   or, without SSH, `ZONE_ALLOW_REMOTE=1` in `.env`.
   The login is set in the browser, on the page Zone shows first.
4. **Project**: Container Manager → Project → Create, name `zonewiki`, path the project folder; it finds `compose.yaml` there.
   Finish the wizard with the project set to start once created; the first start pulls the image (about 1.1 GB).
5. **HTTPS** (optional): Control Panel → Login Portal → Advanced → Reverse Proxy: source `HTTPS`, your host name, port 443; destination `HTTP`, `localhost`, port 5080.
   Under Custom Header, add `X-Forwarded-Proto` = `$scheme` if DSM doesn't send it.
   Add the host name to `hosts` (`config set hosts …`), and a certificate for it (Control Panel → Security → Certificate).
   The proxy reaches the container through Docker's network, so Zone sees it as the network's gateway (`172.17.0.1` or another in `172.16.0.0/12`);
   Zone's log names it on the first proxied request ("requests from … carry X-Forwarded-For"),
   and `config set trusted-proxies <that address>` lets Zone tell the proxy's clients apart when it throttles wrong passwords (without it they count as one).

Updates: over SSH, `docker compose pull && docker compose up -d` in the project folder (Container Manager's Image list may also offer the update for `ghcr.io/zonewiki/zonewiki`).

### Semantic search paused

Since 2026-11-18 the images CI publishes have no semantic search (`ZONE_SEMANTIC_SEARCH=off` at build time), and CI's **retrieval** job is skipped (`if: false`),
because Microsoft took onnxruntime's GitHub repository private "while we investigate malicious content", and both would run onnxruntime.
Its release downloads are gone with it, so a build with `ZONE_SEMANTIC_SEARCH=on` fails where its layer isn't cached.
They come back, as they were, once Microsoft has said what happened and onnxruntime has a source that can be trusted again.

## CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on a push to `main` or a `v*` tag, on every pull request and when started by hand (`workflow_dispatch`);
a newer run of the same branch or tag cancels the one before.
Its **checks** are a job per `run.sh` command, as on your machine, all in the tools image (`Containerfile.tools`),
so CI keeps no package lists or test commands of its own.

### Forgejo's jobs

| Job | Runs | When |
|-----|------|------|
| **test** | gofmt, go vet, go test, the unit/API tests (`./run.sh test`) | Every run but a pull request from another repository (Forgejo's runner, below) |
| **e2e** | The browser and Node tasks-core tests (`./run.sh e2e`) | The same |
| **leaks** | gitleaks over every commit (`./run.sh leaks`) | The same |
| **bench** | `./run.sh bench` with `ZONE_BENCH_SLACK=3` | After the checks |
| **retrieval** | `./run.sh retrieval` | After the checks (skipped for now: Semantic search paused) |
| **image** | Builds the `Containerfile` and pushes it to the Forgejo registry | Not a pull request's run, once the three checks pass |

Only **leaks** fetches the full history; the others check out the last commit.

**bench** (The bench in `docs/spec/testing.md`): the image job doesn't wait for it, so a slow runner fails a job that is looked at and holds up no image.
It waits for the checks because the jobs share the host's engine:
beside test's worker per CPU and e2e's browsers, the bench's clients at once measured the CPU they waited for
(a page beside thirty searches seven times slower than alone, where the slack allows three).
GitHub's workflow has the same job, beside the checks, since each of its jobs has a machine of its own.

**retrieval** measures search's quality on the sample wiki against its floor (Measuring retrieval in `docs/spec/search.md`).
It builds the app's image with the model, under a tag of its own (`zonewiki-retrieval`), from the engine's cache unless the code or the model changed.

**image** runs on a push to `main` or a `v*` tag, or a run started by hand.
It builds the `Containerfile` with `ZONE_SEMANTIC_SEARCH=off` (on until 2026-11-18: Semantic search paused) and the `ZONE_BUILD` string `run.sh` would compute,
and pushes it as `<REGISTRY>/<owner>/<repo>:<short-sha>` (the repository's name, lowercase),
plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`).
The repo variable `REGISTRY` is Forgejo's public host, e.g. `git.example.org`: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`.
Without the variable the image job is skipped, so forks just test.

### run.sh in CI

`run.sh` builds each image quietly (the build's output, which podman's `-q` doesn't hide for `RUN` steps, goes to a log shown only when the build fails;
a first build, with no image of that tag yet, prints one line saying so)
and runs it by ID, so parallel jobs on one engine can't swap a shared tag.
With `ZONE_COPY_REPO=1` (set by CI) it copies the repo into the container (`create --rm`, `cp`, `start -a`; the engine removes the container even when a cancelled job kills `run.sh`) instead of bind-mounting it,
since the checkout lives in the job container while the engine is the host's;
nothing the command writes comes back.

### Forgejo's runner

All jobs run in the official `docker:cli` image (plus `nodejs`, `git`, `bash` from apk)
and build against the host engine (the checks through `run.sh`, the image job with plain `docker build -f Containerfile`/`docker push`),
so layers are cached between runs.
They reach it through the socket mounted into job containers:

- runner label `docker`, runner config `container.docker_host: automount`;
- so a job runs with root on the host: the engine's socket lets it start any container, with the host's disk mounted.
  A pull request from another repository (a fork) runs nothing there (the checks' `if:`, on `github.event.pull_request.head.repo.full_name`; bench and retrieval need the checks, the image runs for no pull request), since its code would take the runner, the layer cache the image is built from, and the registry token of the next image job;
  its checks run on GitHub (CI (GitHub Actions), below), or by hand after a look, or once it is pushed as a branch of this repository;
- the host's engine trusts the internal certificate via `/etc/docker/certs.d/<REGISTRY>/ca.crt`, which the runner also mounts read-only into job containers via `container.options`;
- checkout goes over the runner's internal `http://web:3000`, so no job needs a `ca-certificates` package.

Repo settings:

| Setting | Kind | What |
|---------|------|------|
| `REGISTRY` | Variable | The registry host; required for the image |
| `REGISTRY_TOKEN` | Secret | A token with package write scope |
| `REGISTRY_USER` | Variable, optional | Default: the repo owner |

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs the same **checks** (`./run.sh test`, `e2e`, `leaks`), on the same events, on GitHub's `ubuntu-26.04` runners, through `run.sh`
(with podman, which the runners have and `run.sh` prefers; the checkout is on the runner, so no `ZONE_COPY_REPO`).

### GitHub's jobs

| Job | Runs | When |
|-----|------|------|
| **test**, **e2e**, **leaks** | The checks, as on Forgejo | Every run |
| **bench** | `./run.sh bench` | Beside the checks |
| **image** | Builds the `Containerfile` once per architecture, pushes it by digest | Not a pull request's run, once the checks pass |
| **manifest** | Joins the two digests under the tags | After **image** |
| **release** | `./run.sh binaries`, the files attached to the tag's GitHub release (Release binaries) | A `v*` tag, once the checks pass |

GitHub publishes the image `compose.nas.yaml` runs.
**image** builds the `Containerfile` (`ZONE_SEMANTIC_SEARCH=off`, the `ZONE_BUILD` string) once per architecture,
each natively on its own runner (`ubuntu-26.04` for amd64, `ubuntu-26.04-arm` for arm64;
no emulation, so the arm64 build compiles with cgo, and, with semantic search, fetches onnxruntime's arm64 build and the model as fast as the other),
and pushes it to `ghcr.io/<owner>/zonewiki` by digest only (cached between runs in GitHub's cache, a scope per architecture).
**manifest** joins the two digests under the tags:
`latest` (main),
the short commit hash,
and on a tag `v1.2.0` also `1.2.0` and `1.2`.

It logs in with the workflow's own `GITHUB_TOKEN` (`packages: write`), so the repo needs no secrets.
The image carries `org.opencontainers.image.source`, which links the package to the repo.
A new package on ghcr.io is private: make it public once in its settings on GitHub, so a NAS pulls without logging in.

## Network access

`ZONE_ALLOW_REMOTE` is the single knob for network access (with `run.sh`).

- Unset, `run.sh` exports `ZONE_BIND=127.0.0.1`, so the container's port 5000 is only exposed on the host's loopback interface, and the host-header check rejects any `Host` header but localhost and the settings file's `hosts`.
- With `ZONE_ALLOW_REMOTE=1`, `run.sh` exports `ZONE_BIND=0.0.0.0` (LAN-accessible port binding),
  and without `hosts` the host-header check lets in any IP address too (and names in `.internal`), so the LAN reaches Zone by address;
  a name it is opened by goes in `hosts`.

`compose.yaml` maps `${ZONE_BIND:-127.0.0.1}:5000:5000`; used without `run.sh`, `ZONE_BIND=0.0.0.0` must be set in `.env` alongside `ZONE_ALLOW_REMOTE=1`.

### A Unix socket

`zonewiki --addr unix:PATH` listens on a Unix socket at PATH and on no port:
for a reverse proxy on the same machine, when Zone runs from the binary or in a container with the socket's folder mounted and no network of its own.
Caddy reaches it with `reverse_proxy unix//run/zone/zone.sock`, nginx with `proxy_pass http://unix:/run/zone/zone.sock:;`.

Who may connect is who may reach the path: the socket itself is open to every local user (mode 0666, as a port on localhost is), so the folder it is in decides, and the proxy's user must be able to enter that folder.
Zone replaces a socket file that a Zone that was killed left behind;
it doesn't start when something still listens there, or when the path is a file of another kind (`listen` in `go/server.go`).

Whoever connects has no address and is taken for the proxy: its `X-Forwarded-For` names the client, whatever `trusted-proxies` holds (Throttling in `docs/spec/security.md`).
`hosts` and the rest of a proxy's setup stay as with a port.
The healthcheck over it: `zonewiki health unix:PATH`.

### Started by systemd, asleep when idle

`zonewiki --addr fd:3` serves on a listening socket Zone was started with, the way systemd hands one to a service it starts for a socket unit
(socket activation; `fd:3` is the first such socket, and Podman passes it on into a container).
systemd holds the socket and starts Zone when a connection comes:

```
# zone.socket                          # zone.service
[Socket]                               [Service]
ListenStream=/run/zone/zone.sock       Environment=ZONE_DATA=/srv/wiki ZONE_IDLE_EXIT=15m
                                       ExecStart=/usr/local/bin/zonewiki --addr fd:3
```

With `ZONE_IDLE_EXIT` (a duration: `15m`, `1h`; unset or `off`: never) Zone exits, with exit code 0, once no request came for that long (`exitWhenIdle` in `go/server.go`),
and uses no memory until the next one starts it again:
that one waits in the socket for the start, a fraction of a second, since the search index is in its files.

What counts as a request is everything but `/api/health`, so whatever watches Zone doesn't keep it up;
a browser tab nobody is at asks nothing either (`whileUsed`, Modules in `docs/spec/navigation.md`).

Zone stays up:

- while a request runs;
- while a throttle remembers a wrong password or token (for a day after a client's last wrong try, until its right one): an exit would forget the count and give a guesser five new tries each time;
- while it packs the repository (Packing the repository, below): a join of the packs stopped halfway is done again from the start.

What an exit loses is what a restart loses: the caches, which are built again, and the texts an editor's save is merged from,
so a tab left open over a sleep saves as after any restart
(plainly if the page is as it was; with a conflict to settle if it changed meanwhile; Several editors in `docs/spec/editor.md`).
Who wrote a draft that isn't committed yet is only in memory, so before the exit each draft is committed in its author's name (`commitDrafts` in `go/merge.go`).

`ZONE_IDLE_EXIT` on a socket or port Zone opened itself is ignored, with a line in the log, since nothing would start Zone again.
A Unix socket handed over is treated as one Zone opened (`--addr unix:PATH`, above): its peer is the proxy; the file is the holder's and stays when Zone exits.

## Mail

Zone sends mail for sign-up (Sign-up in `docs/spec/users.md`) and to tell a user that someone gave them a task (Assigning tasks there), mentions them or comments on a task of theirs (Mail in `docs/spec/tasks.md`; each kind only once the user turns it on),
through an SMTP server the deployment names in `.env` (`go/mail.go`, Go's `net/smtp`; no dependency):

```bash
ZONE_SIGNUP=1
ZONE_SMTP_HOST=smtp.example.org
ZONE_SMTP_PORT=587                 # default 587; 465 with ZONE_SMTP_TLS=tls
ZONE_SMTP_USER=wiki@example.org    # optional: AUTH PLAIN
ZONE_SMTP_PASSWORD=…
ZONE_SMTP_TLS=starttls             # starttls (default), tls or none
ZONE_MAIL_FROM=wiki@example.org
```

The password is a secret in `.env` (gitignored), as compose hands every setting over (`env_file`).

### Encrypted

Mail is encrypted unless `ZONE_SMTP_TLS=none`:

| `ZONE_SMTP_TLS` | What it does |
|-----------------|--------------|
| `starttls` (the default) | Refuses a server that doesn't offer STARTTLS, rather than sending the link in the clear |
| `tls` | Speaks TLS from the start (the default for port 465) |
| `none` | For a relay that takes mail unencrypted (a local postfix) |

The certificate is checked against `ZONE_SMTP_HOST`.
With `none` the password goes only to this machine (`net/smtp`'s `PlainAuth` refuses otherwise).

### Checked at start

Zone won't start on a mail setting without `ZONE_SMTP_HOST`,
a port or TLS mode it doesn't know,
a password without a user,
a missing or invalid `ZONE_MAIL_FROM` (a bare address, no name),
or `ZONE_SIGNUP` without a mail server.

### Time limits

20 seconds to connect, a minute for the whole mail.
The sign-up form waits for it, so it can say when the mail couldn't be sent (and logs why).
The mail about an assigned task is sent in the background: a task write never waits for the mail server, and a failure is only logged.

## Mobile / phone access

Set `ZONE_ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces:
`run.sh` then maps `0.0.0.0:5000` and the server's host-header check lets in any IP address (a name goes in `hosts`).
Phones on the same network reach Zone at `http://<host-LAN-IP>:5000`.

A wiki other devices can reach and that has no login yet answers only `/setup`, where the first login is chosen,
unless `no-login = yes` says it is open on purpose (First run in `docs/spec/security.md`, `go/setup.go`);
a password (`./run.sh config passwd`) or users (`./run.sh user add NAME`) set from the terminal do the same.

The UI is responsive down to phone widths and the editor works on mobile.
The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one.
Without it, full-screen views (the tasks edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard.
iOS Safari ignores the setting.

## Version

Zone's version is a semver constant, `version` in `go/config.go`, bumped by hand.
Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `go/` or `public/` had uncommitted changes
(a commit can't contain its own hash, so this part can't be a committed constant).

| Where | What it shows |
|-------|---------------|
| The shortcuts modal (`?`) | Both (`Zone 1.0.0 · c28ec9e 2026-09-26 19:22`) |
| Every page's bottom-left corner | The version and short hash (`Zone 1.0.0 · c28ec9e`, `+dirty` when so) |
| The `<meta name="generator">` tag | The version |
| `/api/version` | `{ version, build }` |

The corner shows the full build on hover (`.zone-build` in `layout.html`); it is faint, only from 1140px wide where the margin beside the text column has room, and not in the editor.

The image has no `.git`, so `run.sh` computes the build string and passes it as the `ZONE_BUILD` build arg
(the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer).
Without the container, the binary has the same string from what `go build` recorded of the checkout (`sourceBuild()`: the commit, its time, whether the tree had changes);
`$ZONE_BUILD` wins when set, and with neither the modal shows the version alone.

The shortcuts modal (`?`) also links to Zone's source (`sourceURL` in `go/config.go`) and names its license, AGPL-3.0-or-later:
whoever runs a changed Zone for others over a network has to offer them its source, so a fork points that constant at its own copy.

## Home-screen app

`public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `templates/layout.html`) give "Add to home screen" a proper Zone name and icon.
Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie.
The PNGs are rendered once from `public/icon.svg` (full-bleed green square, a white Z inside the central 80% "maskable" safe zone) and committed, so there is still no build step.
To change the icon, edit the SVG and re-render the PNGs, e.g. with a screenshot in the Playwright image (`zonewiki-tools`).
Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab.
iOS Safari is more lenient.

## Data directory

Set `ZONE_DATA` in `.env` to any host path (default: `~/zonewiki`).
`run.sh` expands a leading `~` to your home directory before starting the container.
A folder that `ZONE_DATA` names must exist: neither `run.sh up` nor Zone creates it (`loadConfig` exits with "no wiki at …"), so a mistyped path can't start an empty wiki somewhere else.

Zone started with `ZONE_DATA` unset (or empty), as the binary is on a desktop, takes `~/zonewiki` (`defaultData` in `go/config.go`: `$HOME/zonewiki`),
and makes that one folder at the first start, saying so in its log ("ZONE_DATA isn't set: made a new wiki at …");
the commands (`zonewiki user …`, `config …`) do the same, so a password can be set before the first start.
It makes nothing else: without a home folder, or with a file at `~/zonewiki`, it exits and says to set `ZONE_DATA`.
At start it prints where to open it and where the wiki is, under the line it listens on:
`Open http://localhost:5000 in a browser. The wiki is /home/anna/zonewiki`
(`base-url` when it is set; no such line on a socket or behind compose, whose port mapping Zone doesn't know: `ZONE_BIND` set).

In an existing wiki folder Zone creates what it needs (`_assets/`, `_tasks/`, `index.md`):

```
ZONE_DATA=~/zonewiki    # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`.

### Layout

- `$ZONE_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$ZONE_DATA/private/<user>/` — with users, each user's private pages (`docs/spec/users.md`); without users an ordinary folder
- `$ZONE_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically);
  a group's page's in `groups/<group>/_assets/`, a private page's in `private/<user>/_assets/`, a task's (with users) in `_tasks/files/<task id>/_assets/`
- `$ZONE_DATA/_tasks/` — the tasks (`tasks.json`, `closed.json`, `closed-YYYY-MM.json`, each list's in `lists/<name>/`, and `lists.json`, the lists' registry, with the settings of All tasks; see Storage in `docs/spec/tasks.md`);
  each user's private tasks in `private/<user>/_tasks/`
- `$ZONE_DATA/.git/` — the wiki's history: Zone makes the folder a git repository at its first start (`gitSetup`) and commits every change made through it (Packing the repository, below)
- `$ZONE_DATA/.zone-format` — the format the wiki's files are in, a number, in git (The data format, below)
- `$ZONE_DATA/.zone/` — Zone's own state, out of git (What is in .zone/, below)

Uploaded image URLs use `/_assets/<filename>` (served by `serveUpload` from `cfg.Uploads`), not the static file path.

### What is in .zone/

Zone's own state, none of it in git (`.git/info/exclude` has `/.zone/*`, `excludeState`; each file's name is `statePath(…)` in the code):

- `config` — the settings file: the wiki's settings and users, password and token hashes (`docs/spec/users.md`)
- `secret_key` — the key that signs the session cookies (0600; `secretKey`)
- `signed-out` — the ids of sessions signed out, until their cookies expire (`docs/spec/security.md`)
- `links` — the open invite, reset and sign-up links, their tokens as hashes (`docs/spec/users.md`)
- `grants` — the assistants' OAuth connections, their tokens as hashes (`docs/spec/mcp.md`)
- `config.lock`, `links.lock`, `grants.lock` — empty files the writers of those three lock (`lockFile`, flock), so two writers take turns
- `seen/<user>.json` — what each user has seen of the tasks' news (What you have seen, under News on your tasks in `docs/spec/tasks.md`)
- `drafts/<user>/` — an editor's text left in a conflict when its tab closed, for its user's next edit, 30 days at most (`docs/spec/editor.md`)
- `tmp/` — a three-way merge's scratch files, removed after each merge
- `index/` — search's files: `pages.idx` and `sections.idx` (the BM25 indexes), `tasks/<hash>.gob` (each closed tasks' archive's search docs) and `embeddings.json` (the semantic search cache) (`docs/spec/search.md`)
- `queries.jsonl` — the query log, only with `query-log = yes`
- `tasks-checked` — the mark that a start has checked the task files, so later starts skip it while git says they haven't changed (Storage in `docs/spec/tasks.md`)

### The data format

`.zone-format` at the wiki's root says which format the wiki's files are in: a number and a newline (`1`).
Zone writes it at the first start that took the wiki over, in a commit of its own ("Note the format of the wiki's files");
a wiki without it is from before it (or new), and its start runs every step that takes an older wiki over (Migrations at start in `docs/spec/tasks.md`) before it writes the file.

| The file says | Zone |
|---------------|------|
| Nothing (no file) | Runs the steps from before the file, which look at the files for what they need, then writes it |
| Its own format | Runs none of those steps |
| An older format | Runs the steps from that format on, each writing its number in its own commit |
| A newer format | Doesn't start: "the wiki's files are in format 2 (.zone-format), which a newer Zone wrote" |
| Anything but a number | Doesn't start, and says how to mend it |

**Why it is refused**: a newer Zone changed files in ways this one would misread and then write over (an image rolled back after an upgrade).
Start the newer Zone again, or go back: revert, newest first, the commits that raised the number (`git log -- .zone-format`), and with them the steps they wrote.

**Why it is in git, not in `.zone/`**: it speaks for the files in git, so it has to move with them.
A revert of a step's commit takes back the files and the number together, and so do a `git pull` and a checkout of an older commit;
a number in `.zone/` would still say the newer format, so the older Zone would refuse the wiki, and the newer one would skip the step the revert took back.
A backup or a copy of the folder has the file either way, and a clone of the remote has it too.
No route or tool reads or writes it (`safePath` takes only a page's `.md`).

**How it works** (`go/wikiformat.go`): `checkWikiFormat` reads the file before anything at start writes, `setUpTasks` runs the steps the format needs, and `noteWikiFormat` writes the first number after the steps from before the file (not while the lists couldn't be set up, `listsUnmigrated`: the next start runs them again).
A change to what Zone keeps in git that an older Zone would misread raises `wikiFormat`, with a step at start that takes a wiki of the format before over and writes the new number into the same commit (`writeWikiFormat`).
Readers stay lenient all the same: the number says which steps Zone ran, not that every file is as Zone wrote it (a file edited by hand or pulled from elsewhere is read as before).
The settings file and the rest of `.zone/` aren't covered: they are no wiki data (What is in .zone/, above).

What holds users' words or secrets is written readable by Zone's user alone (0600), whatever the umask: `config`, `secret_key`, `signed-out`, `links`, `grants`, `seen/`, `drafts/`, the index files, `index/tasks/` (private tasks' titles among them), `embeddings.json` (every page's slug) and `queries.jsonl` (every user's queries and what they found) (`atomicfile.WriteOwnerOnly`; a file from before is made so at its next write).
The pages themselves, in the wiki, keep the umask's mode, as a file made in an editor does.

### A git remote

Optional: `./run.sh config set git-remote git@github.com:user/wiki.git`.
At start and when it changes, `setGitRemote()` adds or updates `origin` to that URL, so `git push` works from the terminal.
Zone doesn't push by itself (Backups, below).

### Backups

**Back up the whole data directory** (`ZONE_DATA`): it is the wiki, and there is nothing of it anywhere else.
The simplest backup is a copy of that folder taken while Zone is stopped (`./run.sh down`), or a snapshot of the file system it is on (a NAS's snapshots, ZFS, btrfs).
Zone writes each file whole (a reader never sees half of one), but a copy taken file by file while Zone runs can catch a git commit or a task write half done across files.

- **What git holds**: the pages, the uploads (`_assets/` and the private and groups' ones), the tasks and their lists (`_tasks/`, `private/<user>/_tasks/`), each with its history.
  With `git-remote` set (A git remote, above), `git -C "$ZONE_DATA" push origin` copies that elsewhere (Zone doesn't push by itself); a change made on disk and not committed yet is in no commit.
- **What git doesn't hold, and a clone of the remote lacks**: `.zone/`, above all
  `config` (the users, their roles, groups, mail addresses and password hashes, the MCP tokens' hashes and every setting: without it a clone of a wiki with users won't start, `missingUsers`),
  `secret_key` (without it everyone signs in again),
  `grants` (without it every assistant connected through OAuth connects again),
  `links` (without it the open invite and reset links stop working)
  and `signed-out` (without it a copy of a cookie signed out works again, until it expires).
  Back these up with the folder; where the git remote is the only copy, keep a copy of `.zone/` beside it.
  They hold password and token hashes and keys: keep the backup as confidential as the wiki (Before you open it up in `docs/spec/security.md`).
- **What can be rebuilt, and needn't be backed up**: `.zone/index/` (Zone builds the search indexes again when they are first needed, which takes a while on a big wiki, and with semantic search embeds every section and task again in the background, which takes longer),
  `.zone/tmp/`, the lock files and the mark `tasks-checked` (a start without it looks the task files through again and changes nothing that is right).
  Lost, `seen/` shows each user the tasks' news again, `drafts/` loses only a text left in a conflict, and `queries.jsonl` is a log.
- **Restoring**: put the folder back where `ZONE_DATA` points and start Zone;
  the files must belong to the user Zone runs as, and `.zone/config` and `secret_key` should stay readable by it alone (0600: Zone warns in its log when others may read the settings file).

## Packing the repository

Zone packs the wiki's git repository itself, in the background, so that no commit (and no request that made one) waits for git's housekeeping.
git's own maintenance is off; Zone packs the loose objects every 50 commits and joins the packs into one when they have become many or heavy.
A run that fails is logged and leaves the repository as it is, only larger.

### git's own maintenance is off

git runs with `maintenance.autoDetach=false` and `gc.autoDetach=false` (package `gitrepo`'s environment; the tests' own git calls set the same, `settings.GIT_ENV`).
git 2.47+ starts its automatic maintenance after a commit as a detached process,
which nobody reaped with Zone as the container's PID 1,
so every commit left a zombie until the PID limit (2048) stopped everything (it also crashed test sessions).
Not detached, git reaps its own children; the compose files' `init: true` (a minimal init as PID 1) covers anything else.

git's own packing after a commit is off too (`gc.auto=0`, `maintenance.auto=false` in the same environment),
since it runs inside the commit, and the request that made it, when its turn comes.

### When Zone packs

`packSoon`, `packNow` in `go/gitstore.go`; `Repo.Pack` in package `gitrepo`.

- Every 50 commits (`packEvery`), and once after the start, Zone packs the loose objects in the background,
  one run at a time, beside whatever commits meanwhile.
- At start Zone packs only when an earlier run left 200 loose objects or more.
- A pack is `git repack -l`, which leaves what is packed as it is.
- Then, while no commit runs (`commitMu`), the loose objects that are packed now are removed
  (`git prune-packed`: beside a commit it would take away the folder a `git add` is writing into).

### Joining the packs

When the packs have become many or heavy they are made one, **with the differences between a file's versions computed anew** (`Repo.Join`: `git repack -a -l -f --window=50`).

**When** is decided by the packs' weight (`Repo.TimeToJoin`):

- there are more than 4 (`packsMany`), and those beside the largest outweigh half of it;
- or there are more than 50 (`packsMost`, for a repository whose largest pack is mostly uploads).

A join reads the whole history, so it takes as long as the history is long.
By weight, a young repository joins after some hundred commits,
and a large one when what is new is half of what is joined, or after 2,500 commits at the latest,
with the repository at most half again its joined size meanwhile.

**No commit waits for the join** (`joinPacks`):
the one pack is written beside whatever commits, on half of the machine's processors,
and only the packs it stands for (`Repo.Drop`) and what is loose and packed now are removed while no commit runs, which reads no object;
what is committed meanwhile stays loose for the next pack.

A join has two hours (`joinTimeout`);
what git was writing when one was stopped is removed a day later (`removeStaleTemps`),
and a pack kept by hand (a `.keep` file) is left alone.

Why `-f`: a file's versions become differences, so a task file written whole at every change takes what changed: about 0.4 KB a change with its commit and trees, once joined.
That needs `-f`: a pack of what was loose holds each file it touches whole once more, and a join without `-f` keeps what the packs have.
The task files' format makes no difference worth having (indented or a task a line, empty fields written or not).

### Uploads

**Uploads are left out of the comparing** (`noDeltaForUploads`):
`_assets/** -delta`, `private/*/_assets/** -delta`, `groups/*/_assets/** -delta` and `_tasks/files/*/_assets/** -delta` in the repository's own `.git/info/attributes`, which is in no commit, written at start beside what is there.
Images and PDFs have no differences worth finding, and comparing them makes a join many times slower.

## How big a wiki can be

Zone keeps no index of the wiki's pages or tasks in memory beyond per-page caches:
a page view walks the wiki's folders and looks at every page's links (cached by the file's mtime, so one `stat` a page) for its backlinks,
and reads the open tasks' file whole,
once a request (`rq.openTasks`),
for the Tasks links' counts and the page's linked tasks;
the tasks API reads the task files whole.
So a request grows with the wiki.
Measured through the real handlers (`go/bench_test.go`; `go test -run '^$' -bench . -benchtime 20x` in `go/`), warm, on a 16-core desktop in 2026-11:

| Request | Wiki | Time |
|---------|------|------|
| A page view | 1,000 pages, 500 open tasks | 4 ms |
| A page view | 5,000 pages | 10 ms |
| A page view | 20,000 pages | 50 ms |
| A page view | 1,000 pages, 5,000 open and 20,000 closed tasks | 27 ms |
| `GET /api/tasks` | 500 open tasks | 3 ms |
| `GET /api/tasks` | 5,000 open tasks | 31 ms |
| The page list (`/api/pages-list`) | 1,000 pages | 3 ms |
| The page list | 20,000 pages | 92 ms |

A personal or a small team's wiki is far below where this is felt; past some ten thousand pages or open tasks every view pays for it, and a NAS's disk and CPU bring that point closer.
Page writes run one at a time (`pagesMu`), each with its git commit (a few git processes), so a wiki is not for many people saving at once.
Closed tasks cost nothing until they are looked at: a page view doesn't read their files.
What would lift the ceiling, when it is needed: the pages' mtimes taken from the walk instead of a `stat` each (`pageStats`).
The task files are already parsed once and kept until a file's time, size or inode changes (`parsedFile` in `go/internal/taskfiles`).

## From Zone 1 (or Leaf)

Zone 2 reads only today's names; what Zone 1 still answered to from before its renames (Leaf, Deck, Scratch) is gone.
Start the last Zone 1 release once on the wiki first: it moves `.leaf/` to `.zone/` and `_deck/` to `_tasks/`, and takes the settings file out of git.
The rest is by hand:

| What | Before | Now |
|------|--------|-----|
| `.env` | `LEAF_…` variables | `ZONE_…`; the wiki's settings (password, users, hosts …) are in `.zone/config`, not there |
| Pages | `[[deck …]]`, `home=1` | `[[tasks: …]]`, `pin=1` |
| Addresses | `/deck`, `/api/deck/…`, `/api/scratch`, `/page/_scratch` | `/tasks`, `/api/tasks/…`, `/api/notes`, `/notes` |
| Keys | `g s`, `g d`, `c s` | `g n`, `g a`, `c n` |
| MCP tools | `leaf_help`, `file_scratch_note` | `zone_help`, `file_note` |
| MCP argument | `scratch` | `note` (write_page) or `notes` (search_wiki, list_pages) |
| The image | | `ghcr.io/zonewiki/zonewiki`, its binary `/app/zonewiki` |

To rewrite the embeds, in the wiki's folder, then commit:

```bash
grep -rlZi '\[\[deck' --include='*.md' . | xargs -0 sed -i -E 's/\[\[deck\]\]/[[tasks:]]/Ig; s/\[\[deck[[:space:]]+/[[tasks: /Ig'
```
