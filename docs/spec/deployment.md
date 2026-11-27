# Deployment

Part of Zonewiki's spec (see `AGENTS.md` for the index).

How Zonewiki is built and run: the files that deploy it (`Containerfile`, the compose files, `run.sh`, `.env`), the commands,
running it without `run.sh` or from source, the release binaries, with the prebuilt image (on a computer, a server or a NAS), CI on Forgejo and GitHub,
network access (a Unix socket, systemd), mail, phone access, the version string, the home-screen app,
the data directory (its data format) and its backups, moving a wiki to another Zonewiki (export and import), how Zonewiki packs the wiki's git repository, how big a wiki can be, and moving from Zonewiki 1.

## Files

| File | What it is |
|------|------------|
| `Containerfile` | The image, in three stages |
| `compose.yaml` | Runs the prebuilt image, without the repo (a computer, a server, a NAS) |
| `compose.build.yaml` | In a checkout: builds the image from it and runs the `zonewiki` container |
| `compose.podman.yaml` | Rootless podman only: `userns_mode: keep-id` (`run.sh` adds it) |
| `run.sh` | The entry point: drives compose (the prebuilt image beside `compose.yaml`, a build in a checkout), runs the tests in the tools image |
| `.env.example` | The template for `.env` |
| `.env` | The deployment's settings (gitignored) |
| `.zone/config` | The wiki's own settings and its users, in the wiki |

### Containerfile

Three stages:

| Stage | What it does |
|-------|--------------|
| `build` | Go (a pinned release, checked against its checksum) builds the server from `go/`, without cgo and with Go's `simd` experiment, Zonewiki's own files built in |
| `semantic` | With `ZONE_SEMANTIC_SEARCH=on`, the model's files; off, an empty folder |
| the image | Ubuntu 26.04 LTS with what the two stages made |

`build` runs `go mod download` first, so a code change doesn't fetch the modules again;
it copies the root's `go.mod` beside `go/` for it, the module of Zonewiki's own files (What the binary holds, below).
Then it copies what `zonefiles.go` builds in (the browser code, the docs, the licenses) and the server's code, and builds:
with `CGO_ENABLED=0` (a static binary; the stage has no C compiler) and `GOEXPERIMENT=simd`, Go's vector arithmetic, on which the binary runs semantic search's model itself (Running the model in `docs/spec/search.md`).

`semantic` fetches the model's files (a fixed revision),
each checked against its SHA-256.
It is its own stage, copying nothing from the repo, so a code change never downloads the model again.

The image has:

- git, `tzdata` and CA certificates via `apt`;
- the model in `/opt/zonewiki-model` (`ZONE_MODEL_DIR`), when built with `ZONE_SEMANTIC_SEARCH=on`;
- the binary as `/app/zonewiki`, with the browser code and the docs built in (no `public/` beside it: it serves its own copies);
- the licenses as `/app/LICENSE`,
  `/app/THIRD_PARTY_LICENSES` (Go, the Go modules, the model)
  (the vendored libraries' are in the binary, with its copy of `public/vendor/`: `zonewiki licenses` prints them all;
  Mermaid's bundled packages in `public/vendor/mermaid/THIRD_PARTY_LICENSES`, written by `docs/licenses/mermaid_notices.py`);
- `HOME=/tmp` (Zonewiki may run as a UID without an account);
- the command `/app/zonewiki --addr 0.0.0.0:5080`: every interface, so the container's network bridge reaches Zonewiki (the binary's default port alone would be the container's localhost); what the host exposes is the port mapping's (Network access, below).

Build args: `ZONE_SEMANTIC_SEARCH` and `ZONE_BUILD` (last layer).
The image is about 0.23 GB, 0.9 GB with the model.

### compose.build.yaml

It builds the image (`build:` with those args) and runs the `zonewiki` container (project and service `zonewiki`).
`run.sh` takes it in a checkout; it works on its own too (`docker compose -f compose.build.yaml up -d --build`), with `unknown` as the build and UTC unless `.env` sets `TZ`.

| Setting | Value and why |
|---------|---------------|
| `env_file: .env` | Passes every setting |
| `environment` | Adds `ZONE_DATA=/wiki-data` (overriding `.env`'s), `ZONE_HOST_DATA` and `TZ` |
| port | `${ZONE_BIND:-127.0.0.1}:${ZONE_PORT:-5080}:5080` |
| the wiki | A bind mount with `create_host_path: false` and `selinux: Z` |
| `init: true` | A minimal init as PID 1 reaps orphaned processes |
| `restart: unless-stopped` | |
| `user: ${ZONE_UID:-1000}:${ZONE_GID:-1000}` | Not root: Zonewiki writes the wiki as the host user |
| healthcheck | `/app/zonewiki health` |
| log driver | `json-file` |

- `ZONE_HOST_DATA` is the host path, for the MCP tools' `file:` lines.
- `create_host_path: false`: a mistyped path errors instead of starting an empty wiki.
- `init: true`: with Zonewiki as PID 1 orphaned processes piled up as zombies (see Packing the repository, below).
- The healthcheck, `/app/zonewiki health`, fetches `/api/health` from localhost (port 5080;
  `zonewiki health :8080` for a Zonewiki started with `--addr :8080`, `zonewiki health unix:PATH` for one on a socket).
  It fails while the settings file has an error Zonewiki didn't take (see Reloading in `docs/spec/users.md`).
  The image has no curl or Python.
  It is reported by `compose ps`;
  restarting an unhealthy container is podman's `--health-on-failure` or an autoheal container's job.
- The log driver `json-file` is Docker's default and podman's alias for k8s-file, so `logs -f` works on both.

With docker compose the settings reach the container through the API;
podman-compose turns `env_file` into `podman create -e KEY=value` arguments, so there the secrets are on a command line for that moment.

### compose.yaml

Zonewiki without the repo: it runs the prebuilt image `ghcr.io/zonewiki/zonewiki:latest` (amd64 and arm64, built by GitHub's CI, see CI (GitHub Actions)) instead of building it,
so the machine needs only Docker (or Podman) with Compose: a computer, a server or a NAS.
It is the way the manual offers first for running Zonewiki in a container, with `run.sh` beside it or on its own (`docker compose up -d`; a Synology's Container Manager finds it by its name);
`compose.build.yaml` is for a checkout, to run its code (a change, a branch, semantic search).
See With the prebuilt image.

Unlike `compose.build.yaml`:

- no `build:`;
- `ZONE_DATA`, `ZONE_UID` and `ZONE_GID` are required (`${…:?message}`: compose stops with the message instead of guessing;
  a wrong UID would only show later as files Zonewiki can't write; `run.sh` fills them in);
- no `selinux: Z` (NAS systems have no SELinux, and older compose versions reject the option):
  on a host with SELinux enforcing (Fedora), label the wiki folder once, `chcon -Rt container_file_t <folder>`, or run Zonewiki from the repo;
- the log is capped (`max-size` 10m, 3 files);
- what is built in comes with the image, not from git or `.env`: the build string is CI's (`ZONE_BUILD`, Version), and semantic search's model is the image's (`ZONE_SEMANTIC_SEARCH=on` in `.env` turns it on).

The rest is as in `compose.build.yaml` (`init`, `restart`, healthcheck, `create_host_path: false`, the port: localhost only until `ZONE_BIND=0.0.0.0`, `ZONE_PORT` another port on the host).

### run.sh

The entry point, driving `<engine> compose` (podman if installed, else docker; a compose provider must be installed) for up/down/logs/update and the settings file's commands (its commands: Commands, below).
Where it is decides which Zonewiki it runs:

| Beside it | Compose file | `up` |
|-----------|--------------|------|
| A `Containerfile` (a checkout) | `compose.build.yaml` | Builds the image from the checkout, then starts it (`compose up -d --build`) |
| `compose.yaml` alone (downloaded with it) | `compose.yaml` | Starts the prebuilt image, pulled if it isn't there yet (`compose up -d`) |

Neither, it says it needs `compose.yaml` beside it.
Without a checkout, the commands for working on Zonewiki (`build`, `test`, `e2e`, `bench`, `retrieval`, `binaries`, `leaks`, `screenshots`, `eval`) say they need one, and do nothing.

It works out and exports what compose interpolates:

| Variable | From |
|----------|------|
| `ZONE_DATA` | `.env`, with `~` expanded (default `~/zonewiki`) |
| `ZONE_BIND` | `ZONE_ALLOW_REMOTE`: `0.0.0.0`; else `.env`'s `ZONE_BIND`; else `127.0.0.1` |
| `ZONE_BUILD` | git, in a checkout (see Version) |
| `TZ` | `.env`'s, else the host's from `/etc/timezone` or the `/etc/localtime` link, else UTC |
| `ZONE_UID`/`ZONE_GID` | `.env`'s, else `id -u`/`id -g`; 0 with rootless docker (unless `.env` names them) |

- A variable it exports wins over `.env`'s in compose's interpolation, so it reads `ZONE_BIND` and `ZONE_UID`/`ZONE_GID` from `.env` itself: what compose without `run.sh` takes from there holds with it too (a NAS whose wiki folder belongs to another user than who runs `run.sh`).

- `TZ` makes "today" on the server (due count, tasks dates, note names, a page's "Changed …") the user's day.
- With rootless podman it adds `compose.podman.yaml` (`userns_mode: keep-id`: the host user keeps its UID inside, so Zonewiki runs as you and the wiki's files stay yours); beside `compose.yaml` that file has to be there too, or it says so.
- With rootless docker, which has no keep-id, `ZONE_UID`/`ZONE_GID` are 0 (the container's root is the host user there; another UID would be a subordinate one).

`up` refuses a missing wiki folder or `.env`, then starts Zonewiki as the table says; `update` first pulls the newest code (`git pull`, in a checkout) or image (`compose pull`), then does the same.
It says where Zonewiki runs only once it does (`wait_up`):
compose is content with a container that exits at once and is started again (`restart: unless-stopped`), so `up` waits for the line Zonewiki prints when it listens, in the container's log since this start;
a container compose left running as it was counts as up.
One that stops running (a folder Zonewiki won't make a wiki, a wiki a newer Zonewiki wrote), or doesn't listen within a minute, fails the start, with the end of its log, how to follow it (`./run.sh logs`) and how to stop the retries (`./run.sh down`).
`test`/`e2e` use the engine directly (no `.env` needed), in the tools image.

Platforms: Linux is the only tested one; it is meant to work also on macOS and, on Windows, in WSL2 (Docker Desktop and Podman run on it there).
It sticks to bash 3.2 (macOS's stock bash: no `${x,,}`, no `declare -A`, no `mapfile`) and to options BSD tools share with GNU ones.
`.gitattributes` keeps `*.sh` and `.githooks/*` at LF line endings, so a Windows checkout with `core.autocrlf` doesn't break them.
Without the container, Zonewiki itself should run on any OS Go builds for, with git (From source, below), though only Linux is tested.

### .env.example

The template for `.env` (`cp .env.example .env`): every setting with a comment, optional ones commented out, no real values.
`tests/test_env_example.py` keeps it in step with the code: every `ZONE_*` name the source, `run.sh`, `compose.build.yaml` or `compose.yaml` reads is in it, and nothing else is (a typo in `.env` is silently ignored).

### .env

The deployment's settings, read by `run.sh`, compose and Zonewiki at start; gitignored.
Zonewiki itself reads no `.env` file, only its environment: `run.sh` and compose pass the file's settings on, and a binary run by hand (downloaded or built) takes them as environment variables (`ZONE_DATA=~/notes ./zonewiki`; Where settings live in `docs/manual/self-hosting.md` shows a start script for several).

| Setting | What it sets |
|---------|--------------|
| `ZONE_DATA` | The wiki's folder (default `~/zonewiki`; Data directory, below) |
| `ZONE_INIT` | `1`: make a folder that holds files a wiki, as `--init` does (A new wiki, below) |
| `ZONE_ALLOW_REMOTE` | Network access (Network access, below) |
| `TZ` | The timezone |
| `ZONE_SEMANTIC_SEARCH` | Semantic search at build time |
| `ZONE_PUBLIC` | `view` or `edit`: the wiki is shown, or open to edits, without signing in |
| `ZONE_SIGNUP`, `ZONE_SMTP_*`, `ZONE_MAIL_FROM` | Sign-up and the mail server (Mail, below) |
| `ZONE_MAIL_SPOOL` | `1`: no mail server; each mail is a file in the wiki, which the host sends (The mail folder, below) |
| `ZONE_BIND`, `ZONE_UID`/`ZONE_GID` | For compose without `run.sh`; with it, they win over what it works out (run.sh, above) |
| `ZONE_PORT` | The host's port, for compose (default 5080) |

`ZONE_PUBLIC` is described in Public wikis in `docs/spec/security.md`.
With rootless podman, keep-id (`compose.podman.yaml`) runs Zonewiki as the host user, so files written to the mounted wiki directory are the host user's.

### .zone/config

The wiki's own settings and its users
(a password, MCP tokens, `hosts`, `base-url`, `git-remote`, `noshare`, `locale`, `date-format`, `timezone`, `query-log`, `semantic-min-score`, `trusted-proxies`, `upload-max`, `storage-max`, `external-content`, `notice`, `imprint`, `privacy`, `no-login`, `mcp-oauth`, `oauth-log`, `signup-domains`, `signup-role`, `signup-approval`),
in the wiki,
kept out of git (it holds the users: back it up with the data folder),
reloaded when it changes,
edited by hand or with `./run.sh user …`/`./run.sh group …`/`./run.sh config …` (The settings file in `docs/spec/users.md`).

## Commands

| Command | What it does |
|---------|--------------|
| `./run.sh` | Start Zonewiki (default: "up") |
| `./run.sh up` | Start in detached mode: in a checkout built first (`compose up -d --build`), beside `compose.yaml` the prebuilt image (`compose up -d`) |
| `./run.sh down` | Stop and remove the container (`compose down`) |
| `./run.sh logs` | Follow the container's logs |
| `./run.sh update` | Pull the newest code (`git pull`) or prebuilt image (`compose pull`), then `up` |
| `./run.sh user …` | The users in the wiki's settings file (`zonewiki user`) |
| `./run.sh group …` | Groups made of groups and a group's owners (`zonewiki group`) |
| `./run.sh config …` | The wiki's settings file (`zonewiki config`) |
| `./run.sh export > FILE` | The whole wiki in one file, to move it to another Zonewiki (`zonewiki export`; Moving a wiki, below) |
| `./run.sh import < FILE` | The wiki from such a file, into an empty wiki folder (`zonewiki import`; its options before the `<`) |
| `./run.sh build` | Checkout only: build the container image only (`compose build`) |
| `./run.sh test` | gofmt, go vet, go test -race, then the unit/API tests, in the tools image |
| `./run.sh e2e` | The browser tests and the tasks-core.js (Node) tests, in the tools image |
| `./run.sh flaky [N]` | Chosen browser tests N times each on a busy machine, to find one that fails now and then (A test that fails now and then in `docs/spec/testing.md`) |
| `./run.sh bench` | The tasks at a team's size against their budgets, then against a race build |
| `./run.sh retrieval` | Search on the sample wiki (`tests/retrieval`) against its floor |
| `./run.sh binaries` | The release binaries into `dist/`, in the tools image (Release binaries, below) |
| `./run.sh leaks` | Scan the git history for secrets with gitleaks, in the tools image |
| `./run.sh screenshots` | Redo the README's screenshots from a sample wiki, in the tools image |
| `./run.sh eval [FILE]` | Measure retrieval on your wiki, in the app image |

From `test` on, the commands need a checkout (run.sh, above).

Each command says what it does, in lines starting with `==>` on stderr:
the image it builds and from which file (a first build, that it takes minutes; the build's own output only when it fails),
where it runs (the tools image, the app's, compose),
its steps (`test`: gofmt and go vet, go test, pytest; `binaries`: each file as it is built),
and where what it makes lands (`binaries`: the `dist/` folder by its full path; `screenshots`: `docs/screenshots/`).
`build` and `up` say that the binary is compiled inside the image, not into the checkout, and `build` points to `binaries` for files.

- `test`: extra args go to pytest; a tests/… path replaces the default whole suite but the browser tests.
- `e2e`: extra args go to pytest.
- `bench`: `tests/bench_tasks.py`, then once more against a server built with the race detector;
  `ZONE_BENCH_SLACK=3` allows three times the time budgets (The bench in `docs/spec/testing.md`).
- `retrieval`: by keywords and by meaning, in the app's image with the model.
- `binaries`: Linux and macOS, amd64 and arm64, with `SHA256SUMS`, `LICENSE` and `THIRD_PARTY_LICENSES`; CI's release job runs it on a `v*` tag.
- `leaks`: extra args go to `gitleaks git`; a leak exits with 3.
- `screenshots`: `docs/screenshots/*.png`, compressed with pngquant (the README shows phone.png at 640px).
- `eval`: see Measuring retrieval in `docs/spec/search.md`; with semantic search as `ZONE_SEMANTIC_SEARCH` says.
- `user`: `user add|passwd|token|role|email|group|disable|enable|shared|invite|reset|remove NAME`, `user list`, in a one-off container (compose run; see `docs/spec/users.md`); in a checkout the image is built first, quietly.
- `group`: `group includes NAME [+G] [-G]`, `group owners NAME [+U] [-U]`, `group list`.
- `config`: `config check|set|unset|passwd|token`: check it, set a key, the password and MCP token without users.

Zonewiki is available at **http://localhost:5080** after `./run.sh up` (the port `ZONE_PORT` says, if set).
To update: `./run.sh update`.
To point Zonewiki at an existing folder of Markdown notes, set `ZONE_DATA` to it: Zonewiki makes it a git repository (if it isn't one) and adds a home page.

## Without run.sh

`run.sh` fills in what the compose files can't work out themselves: the commit the image is built from, the timezone, the user ID and the address from `ZONE_ALLOW_REMOTE`.
Without it, `docker compose up -d` runs the prebuilt image (`compose.yaml`; With the prebuilt image, below), and in a checkout `docker compose -f compose.build.yaml up -d --build` builds it (a bare `docker compose up` there runs the prebuilt image too, not the checkout's code).
For either, set in `.env`:

- `ZONE_DATA` as an absolute path (no `~`);
- `ZONE_BIND=0.0.0.0` together with `ZONE_ALLOW_REMOTE=1`, for network access;
- `ZONE_UID` and `ZONE_GID` (`id -u`, `id -g`): `compose.yaml` requires them, `compose.build.yaml` takes 1000 without;
- `TZ`, or the container runs on UTC.

With rootless Podman, add the file that keeps your user ID inside the container: `podman compose -f compose.yaml -f compose.podman.yaml up -d` (or after `compose.build.yaml`).

## From source

With Go 1.27 or newer and git:

```bash
(cd go && go build -o zonewiki .)
go/zonewiki                          # the wiki in ~/zonewiki; ZONE_DATA=/path/to/wiki go/zonewiki for another
```

A checkout named `zonewiki` in the home folder (what `git clone` there makes) is `~/zonewiki`, the default wiki: Zonewiki refuses it (A new wiki, below), so clone under another name or set `ZONE_DATA`.
In Windows PowerShell: `cd go; go build -o zonewiki.exe .; cd ..`, then `$env:ZONE_DATA="$HOME\zonewiki"; go\zonewiki.exe`.
For semantic search at full speed, build with Go's vector arithmetic, `(cd go && GOEXPERIMENT=simd go build -o zonewiki .)` (an experiment of Go 1.27, which the images and the release binaries are built with); a plain build embeds many times slower (Running the model in `docs/spec/search.md`).
It needs Go 1.27 itself: an older `go` that fetches 1.27 for the plain build (`GOTOOLCHAIN=auto`) fails on the unknown experiment before it switches (seen with 1.24).
At the repository's root, `go build` builds the package of Zonewiki's built-in files instead, and writes an archive named `zonewiki` that doesn't run.
It listens on localhost only: a port alone in `--addr` (`:5080`, the default; `:5081`) is `127.0.0.1`'s unless `ZONE_ALLOW_REMOTE` is set, which opens it on every interface (`listenAddr` in `go/server.go`).
An address with a host is taken as written (`--addr 0.0.0.0:5080`), and asks for a login first, as `ZONE_ALLOW_REMOTE` does (First run in `docs/spec/security.md`).
A port another program has stops Zonewiki with what to do (`portTaken` in `go/server.go`): the default is not 5000, which a Mac's AirPlay Receiver and a Synology's DSM use, but another program may have 5080 too, most often a Zonewiki started before: `--addr :5081`.
The message asks whether a Zonewiki runs already, and names AirPlay Receiver only for port 5000.
`zonewiki --help` lists the flags, the commands (`user`, `group`, `config`, `eval`, `health`, `manual`, `licenses`; each takes `--help` too) and the environment.
`zonewiki version` (or `--version`) prints the version and the build, as the start-up line does (`Zonewiki 0.5.0 (c28ec9e 2026-09-26 19:22)`), without a wiki.

### What the binary holds

The binary carries Zonewiki's own files, so it is all a machine needs besides git:
the browser code (`public/`), the docs Help and `zone_help` show (`README.md`, `docs/manual/*.md`, `docs/spec/*.md`, `docs/screenshots/*.png`)
and the licenses (`LICENSE`, `THIRD_PARTY_LICENSES`; the vendored libraries' are in `public/vendor/`).
`zonewiki licenses` prints every license file it holds, each under its path, so a binary passed on alone still carries them.

A folder on disk wins over the copy built in, so a checkout serves its files as they are edited, without a rebuild (`go/files.go`):

| Files | From the first of |
|-------|-------------------|
| The browser code | `--public FOLDER`; `public/` beside the binary, or in the checkout it was built in (`go/zonewiki` → `public/`); the binary's copy |
| The docs | `--docs FOLDER` (a folder without them: none, and Help says so); the folder the browser code's folder is in, when `docs/spec/` is there; the binary's copy |

A `public/` found from the binary counts only when it holds Zonewiki's entry module, `zonewiki.js`:
on a Mac's case-insensitive disk, `../public` of a binary in `~/Downloads` is `~/Public`, which Zonewiki would otherwise serve.
And only when the folder and that file are whoever runs Zonewiki's, or root's, and no one else may write to them (`ownFolder`):
the browser runs what is in it as Zonewiki's own code, so a binary started in a shared place (`/tmp`) doesn't take a `public/` another user put beside it.
A folder passed over is named in the log, and the binary's copy is served; `--public` takes any folder.

Both are served alike (`go/static.go`): a file's type by its extension, `Last-Modified` (for the binary's copies, which have no time of their own, the time of the binary's file, so a browser asks again after an update),
`/public/vendor/` scripts and styles gzipped,
a folder or a missing file a 404,
and a name with `..`, an empty part or a NUL refused before it is looked up.

The files are built in by `go:embed` in `zonefiles.go` at the repository's root, package `zonefiles`:
`go:embed` reaches only files below its package's folder, and the server's module is `go/`.
The root is a module of its own (`go.mod` there, `module zonefiles`), which `go/go.mod` requires and takes from `../` (`replace zonefiles => ../`),
so `go build` in `go/` from a fresh clone builds it in, with no step before it.

## Release binaries

Each `v*` tag's GitHub release carries Zonewiki as one file per platform, for a desktop or a server without Docker:

| File | For |
|------|-----|
| `zonewiki-linux-amd64` | Linux on a PC or server (x86-64) |
| `zonewiki-linux-arm64` | Linux on ARM (64-bit: a Raspberry Pi 4 or 5, an ARM server) |
| `zonewiki-macos-arm64` | A Mac with Apple silicon |
| `zonewiki-macos-amd64` | A Mac with an Intel processor |
| `SHA256SUMS` | The files' SHA-256 sums (`sha256sum -c SHA256SUMS --ignore-missing`; on a Mac `shasum -a 256 -c`) |
| `LICENSE`, `THIRD_PARTY_LICENSES` | Zonewiki's license and the notices of what the binaries hold |

How to use one is in the manual (With the binary in `docs/manual/self-hosting.md`): download, `chmod +x`, run, open `http://localhost:5080`.

A tag is the version the binary says: `v0.5.0` for `const version = "0.5.0"` in `go/config.go` (so the tagged commit is the one without `-dev`).
CI's test job fails a `v*` tag that isn't (`./run.sh test`, from `GITHUB_REF`), and with it nothing of the tag is released: the image, the binaries and Forgejo's push to GitHub wait for the checks.

### What a release binary is

- Zonewiki's server with its own files built in (What the binary holds, above): the browser code, Help's docs, the licenses (`zonewiki licenses`).
- Built without cgo (`CGO_ENABLED=0`), so statically linked on Linux, and with Go's `simd` experiment (the tools image's `GOEXPERIMENT=simd`).
  Semantic search works in it as in the image (`ZONE_SEMANTIC_SEARCH=on`): the binary runs the model itself and downloads its files (0.32 GB, checked against their hashes) to `~/.cache/zonewiki/` when first used.
- 24 to 26 MB each (`-trimpath -ldflags="-s -w"`); the build string is the tagged commit and its date, from `go build`'s own record of the checkout (`sourceBuild`, Version below).
- It needs git at run time, for the wiki's history: Zonewiki looks for it on `PATH` at start (`exec.LookPath`) and without it runs with no history, versions or undo.
- Started with nothing set, the wiki is `~/zonewiki`, made at the first start (Data directory, below), and it listens on port 5080 of localhost only (`127.0.0.1:5080`), printing where to open it.
  There it needs no login; opened to other devices (`ZONE_ALLOW_REMOTE=1`) it shows only its first-run page until one is set, as ever (First run in `docs/spec/security.md`).
- It isn't signed or notarized: macOS keeps a downloaded one from starting until the quarantine mark is taken off (`xattr -d com.apple.quarantine zonewiki`).
  The arm64 Mac binary carries the ad-hoc signature Go's linker gives every darwin/arm64 binary, which Apple silicon requires to run code at all.
- No Windows binary: Zonewiki isn't built or tested for Windows, where a file's path and name follow other rules (which the permissions rest on) and no runner of Zonewiki's CI could check them.
  On Windows it runs in WSL2: the Linux binary there, or the container (Docker Desktop, Podman).

### How CI builds them

`./run.sh binaries` builds the four in the tools image, with its Go (pinned and checked against its checksum, the same as the `Containerfile`'s: `tests/test_image.py`),
`GOOS`/`GOARCH` set for each (Go cross-compiles without cgo), into `dist/` (gitignored), then copies `LICENSE` and `THIRD_PARTY_LICENSES` beside them and writes `SHA256SUMS` for all six.
It passes git `safe.directory`, so the build string comes from the checkout even when the repo's owner isn't the container's user.
Run in a git worktree, the container can't reach the repository's own `.git`, and the build string is `unknown`.

GitHub's **release** job (CI (GitHub Actions), below) runs on a `v*` tag once the checks pass:
it runs `./run.sh binaries`, makes the tag's release when it has none (`gh release create --verify-tag`, titled `Zonewiki <version>`, with GitHub's generated notes)
and attaches `dist/*` to it (`gh release upload --clobber`, so a run again replaces them),
with `gh`, which GitHub's runners have, and the workflow's token (`contents: write`): no action of a third party, no secret.
A release already made for the tag (by hand, with notes of its own) is kept, and gets the files.
Forgejo's workflow builds no binaries.

## With the prebuilt image

Any machine with Docker and Compose (your own computer, a server, a NAS, a Raspberry Pi) can run Zonewiki from the prebuilt image, without the repo, git or a build:
`run.sh`, `compose.yaml` (and for rootless Podman `compose.podman.yaml`) and a `.env` with `ZONE_DATA` in one folder, then `./run.sh up`.
`run.sh` fills in the rest, as in a checkout (run.sh, above), and gives the same commands (`./run.sh user add alice`, `./run.sh config set hosts …`, `./run.sh update`).
Without `run.sh`, `docker compose up -d` in that folder, with the `.env` below.
The image is `ghcr.io/zonewiki/zonewiki`, for amd64 and arm64 (Docker picks the right one):
`latest` follows `main`, a version tag (`1.2.0`, `1.2`) stays put, a short commit hash names one build.
To update: `./run.sh update` (without it, `docker compose pull`, then `up -d` again).
The wiki is untouched by an update; it lives in the folder `ZONE_DATA` names.

`.env` needs (`.env.example` has them all):

- `ZONE_DATA`: the wiki folder; it must exist (create it first, empty or with Markdown files in it).
  Without `run.sh`, as an absolute path.
- Without `run.sh`, or for another owner than who runs it: `ZONE_UID`, `ZONE_GID`, the owner of that folder (`id -u` and `id -g` as that user, or `ls -nd <folder>`).
  Zonewiki runs as that user and writes the wiki as it.
- Without `run.sh` (which takes the host's): `TZ`, e.g. `Europe/Vienna`, so "today" is your day (else UTC).
- For other devices (a NAS, a server): `ZONE_ALLOW_REMOTE=1` with `run.sh`, or `ZONE_BIND=0.0.0.0`, which publishes the port on every interface instead of localhost.
- Optional: `ZONE_PORT` (the host's port, default 5080), `ZONE_SEMANTIC_SEARCH=on` (semantic search: the model is in the image; plan on roughly 1 GB more RAM).

The wiki's own settings go in its settings file (`.zone/config`, `docs/spec/users.md`), through the container, in the folder of `compose.yaml` and `.env` (no repo needed):

```bash
./run.sh config set hosts nas.local, 192.168.1.20, wiki.example.org
./run.sh user add alice     # or: config passwd, one password
# without run.sh: docker compose run --rm zonewiki /app/zonewiki config set hosts …
```

On the network, the login needs no command: opened in a browser, a wiki without one asks for a password first
(First run in `docs/spec/security.md`; `compose.yaml` hands Zonewiki `ZONE_BIND`, so it knows its port is on the LAN),
and shows nothing before.
The commands above set it just as well.

`hosts` are the names and addresses you open Zonewiki by (The Host check in `docs/spec/security.md`);
without them only localhost gets in, and a browser opening Zonewiki by another name gets a 400 that says to add it (the log names it too).
`ZONE_ALLOW_REMOTE=1` in `.env` instead lets in any IP address (`http://192.168.1.20:5080`) with nothing to set up; a name (`nas.local`) still goes in `hosts`.
Until a login is set Zonewiki shows only its first-run page (`no-login = yes` in the settings file turns that off, for a wiki open on purpose);
`config token` (or `user token NAME`) makes a token for AI clients.

Zonewiki is then at `http://localhost:5080`, or on the network at `http://<nas>:<ZONE_PORT>`.
Over the internet, put HTTPS in front (a reverse proxy, below); the session cookie is `Secure` when the proxy sends `X-Forwarded-Proto: https`.

### Synology

With Container Manager (DSM 7.2 or newer; Docker on older DSM works the same over SSH), on an x86 or ARM64 model.

1. **Folders**: in File Station, a folder for the project, e.g. `/volume1/docker/zonewiki`, and in it the wiki folder, e.g. `/volume1/docker/zonewiki/wiki`.
2. **UID and GID**: the user who owns them.
   DSM doesn't show IDs; with SSH on (Control Panel → Terminal & SNMP), log in as that user and run `id`.
   The first admin user is usually `uid=1026`, and group `users` is `gid=100`.
3. **Files**: put `compose.yaml` into the project folder (Container Manager looks for that name), and next to it a `.env` with at least `ZONE_DATA=/volume1/docker/zonewiki/wiki`, `ZONE_UID`, `ZONE_GID`, `TZ`, `ZONE_BIND=0.0.0.0` and `ZONE_PORT=5080`;
   then, over SSH in the project folder, `hosts` (the NAS's name and LAN address) with the command above (`docker compose run …`, or `./run.sh config set hosts …` with `run.sh` put there too);
   or, without SSH, `ZONE_ALLOW_REMOTE=1` in `.env`.
   The login is set in the browser, on the page Zonewiki shows first.
4. **Project**: Container Manager → Project → Create, name `zonewiki`, path the project folder; it finds `compose.yaml` there.
   Finish the wizard with the project set to start once created; the first start pulls the image (about 1.1 GB).
5. **HTTPS** (optional): Control Panel → Login Portal → Advanced → Reverse Proxy: source `HTTPS`, your host name, port 443; destination `HTTP`, `localhost`, port 5080.
   Under Custom Header, add `X-Forwarded-Proto` = `$scheme` if DSM doesn't send it.
   Add the host name to `hosts` (`config set hosts …`), and a certificate for it (Control Panel → Security → Certificate).
   The proxy reaches the container through Docker's network, so Zonewiki sees it as the network's gateway (`172.17.0.1` or another in `172.16.0.0/12`);
   Zonewiki's log names it on the first proxied request ("requests from … carry X-Forwarded-For"),
   and `config set trusted-proxies <that address>` lets Zonewiki tell the proxy's clients apart when it throttles wrong passwords (without it they count as one).

Updates: over SSH, `docker compose pull && docker compose up -d` in the project folder (Container Manager's Image list may also offer the update for `ghcr.io/zonewiki/zonewiki`).

## CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on a push to `main` or a `v*` tag, on every pull request and when started by hand (`workflow_dispatch`);
runs go one at a time in the order they came, whatever their branch or tag, and none is cancelled (`concurrency: ci`, One run at a time, below).
Its **checks** are a job per `run.sh` command, as on your machine, all in the tools image (`Containerfile.tools`),
so CI keeps no package lists or test commands of its own.

### One run at a time

The jobs run their tests on the runner host's engine (Forgejo's runner, below), so two runs at once share its CPUs:
main and a tag pushed together took three times as long, and failed the browser tests where they wait and the bench at its budgets.
So the workflow has one concurrency group for the repository (`group: ci`, `cancel-in-progress: false`):
Forgejo queues every run of the group and starts them in the order they came, a tag's run after main's, and cancels none.
A push made while a run goes waits for it; the jobs of one run still go side by side.
Forgejo knows a group for a workflow only (one on a job is read and ignored), and without `cancel-in-progress: false` it would cancel, not queue.

A run cancelled by hand leaves its tests running: the runner removes the job's container, not the one `run.sh` started beside it on the host's engine.
So `run.sh` marks the container of a CI run with its run (the label `zonewiki-ci-run`, `<owner>/<repo>#<run id>`),
and each job first removes the containers another run of the repository left (`in_image`).
That is safe because runs don't overlap; with the queue turned off on the server (`CONCURRENCY_GROUP_QUEUE_ENABLED = false`) a run would remove its neighbour's.

### Forgejo's jobs

| Job | Runs | When |
|-----|------|------|
| **test** | gofmt, go vet, go test, the unit/API tests (`./run.sh test`) | Every run but a pull request from another repository (Forgejo's runner, below) |
| **e2e** | The browser and Node tasks-core tests (`./run.sh e2e`) | The same |
| **leaks** | gitleaks over every commit (`./run.sh leaks`) | The same |
| **bench** | `./run.sh bench` with `ZONE_BENCH_SLACK=3` | After the checks |
| **retrieval** | `./run.sh retrieval` | After the checks |
| **image** | Builds the `Containerfile` and pushes it to the Forgejo registry | Not a pull request's run, once the three checks pass |
| **github** | Pushes main's commit or the tag to another repository, as it is (Publishing to GitHub, below) | A push, with `PUBLISH_REPO` set, once the three checks pass |

Only **leaks** and **github** fetch the full history; the others check out the last commit.

**bench** (The bench in `docs/spec/testing.md`): the image job doesn't wait for it, so a slow runner fails a job that is looked at and holds up no image.
It waits for the checks because the jobs share the host's engine:
beside test's worker per CPU and e2e's browsers, the bench's clients at once measured the CPU they waited for
(a page beside thirty searches seven times slower than alone, where the slack allows three).
GitHub's workflow has no bench: what reaches GitHub has passed Forgejo's run (a fork's pull request runs no bench anywhere),
and GitHub's shared runners would measure their neighbours more than Zonewiki, a red mark on the public main that says nothing.

**retrieval** measures search's quality on the sample wiki against its floor (Measuring retrieval in `docs/spec/search.md`).
It builds the app's image with the model, under a tag of its own (`zonewiki-retrieval`), from the engine's cache unless the code or the model changed.

**image** runs on a push to `main` or a `v*` tag, or a run started by hand.
It builds the `Containerfile` with `ZONE_SEMANTIC_SEARCH=on` and the `ZONE_BUILD` string `run.sh` would compute,
and pushes it as `<REGISTRY>/<owner>/<repo>:<short-sha>` (the repository's name, lowercase),
plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`).
The repo variable `REGISTRY` is Forgejo's public host, e.g. `git.example.org`: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`.
Without the variable the image job is skipped, so forks just test.

### run.sh in CI

`run.sh` builds each image quietly (the build's output, which podman's `-q` doesn't hide for `RUN` steps, goes to a log shown only when the build fails;
a first build, with no image of that tag yet, prints one line saying so)
and runs it by ID, so parallel jobs on one engine can't swap a shared tag.
With `ZONE_COPY_REPO=1` (set by CI) it copies the repo into the container (`create --rm`, `cp`, `start -a`; a cancelled run's container goes on and is removed by the next run: One run at a time, above) instead of bind-mounting it,
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
| `PUBLISH_REPO` | Variable | `owner/name` of the repository to publish to (GitHub); without it the **github** job is skipped |
| `PUBLISH_KEY` | Secret | A private ssh key that may push there (a deploy key with write access); without it the job publishes nothing |
| `PUBLISH_CHECK` | Secret, optional | A shell script that refuses a push by failing (Publishing to GitHub, below) |

### Publishing to GitHub

Forgejo is where the work happens; the **github** job makes a copy elsewhere (GitHub) only when the checks have passed, and nothing is pushed by hand.
It pushes what Forgejo has, as it is: main's commit (`<sha>:refs/heads/main`) or the `v*` tag, never forced, so every commit there has the hash it has here.
A commit made later is a fast-forward on both sides, and the hashes stay the same.
GitHub's own workflow (CI (GitHub Actions), below) then runs there as for any push, and on a tag builds the release.

To set it up once:

1. Make a key for this alone: `ssh-keygen -t ed25519 -N "" -C "forgejo to github" -f KEY`.
2. In the other repository: Settings → Deploy keys → Add deploy key, with `KEY.pub` and **Allow write access**. A deploy key reaches that one repository only.
3. In this repository: variable `PUBLISH_REPO` (`owner/name`) and secret `PUBLISH_KEY` (the private key's text); then delete the key files.
4. The other repository should be empty (no README or license made with it): the first push puts main there, and a repository with history of its own refuses it.

What the job does, in order:

1. Does nothing without `PUBLISH_KEY` (a clone without the setting just tests).
2. Takes GitHub's host key with `ssh-keyscan` and checks it against the fingerprint GitHub publishes for it (written in the workflow); another key stops the job, so the key is never offered to an impostor.
3. For main: skips a commit the other repository has, and refuses (never forces) one whose history doesn't continue the other's main.
   A tag may name a commit the other repository has already, so it is pushed all the same.
4. Runs `PUBLISH_CHECK` if there is one: a script of the operator's own that gets `BASE` (the other repository's main, empty when it has none) and `HEAD` (the commit pushed) and refuses the push by failing.
   It is a secret so that a rule that is one person's business stays out of the repository.
5. Pushes.

The runs of the repository go one at a time (One run at a time, above), so two don't race for main.
A pull request runs nothing of this: only a push to this repository starts the job, and secrets don't reach other repositories' pull requests.
Tests: `tests/test_image.py` (the job's shape: after the checks, a push only, never forced, the host key's fingerprint).

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs the same **checks** (`./run.sh test`, `e2e`, `leaks`), on the same events, on GitHub's `ubuntu-26.04` runners, through `run.sh`
(with podman, which the runners have and `run.sh` prefers; the checkout is on the runner, so no `ZONE_COPY_REPO`).

### GitHub's jobs

| Job | Runs | When |
|-----|------|------|
| **test**, **e2e**, **leaks** | The checks, as on Forgejo | Every run |
| **image** | Builds the `Containerfile` once per architecture, pushes it by digest | Not a pull request's run, once the checks pass |
| **manifest** | Joins the two digests under the tags | After **image** |
| **release** | `./run.sh binaries`, the files attached to the tag's GitHub release (Release binaries) | A `v*` tag, once the checks pass |

GitHub publishes the image `compose.yaml` runs.
**image** builds the `Containerfile` (`ZONE_SEMANTIC_SEARCH=on`, the `ZONE_BUILD` string) once per architecture,
each natively on its own runner (`ubuntu-26.04` for amd64, `ubuntu-26.04-arm` for arm64;
no emulation, so the arm64 build is as fast as the other),
and pushes it to `ghcr.io/<owner>/zonewiki` by digest only (cached between runs in GitHub's cache, a scope per architecture).
**manifest** joins the two digests under the tags:
`latest` (main),
the short commit hash,
and on a tag `v1.2.0` also `1.2.0` and `1.2`.

It logs in with the workflow's own `GITHUB_TOKEN` (`packages: write`), so the repo needs no secrets.
The image carries `org.opencontainers.image.source`, which links the package to the repo.
A new package on ghcr.io is private: make it public once in its settings on GitHub, so a NAS pulls without logging in.

### GitHub's code scanning

`.github/workflows/codeql.yml` is a second workflow: CodeQL over the Go, the browser code, the scripts and the workflows, a job a language, on a push to main, a pull request and once a week.
What it leaves out and what it is told of Zonewiki's checks is in `.github/codeql/` (Code scanning in `docs/spec/security.md`).

## Network access

`ZONE_ALLOW_REMOTE` is the single knob for network access, with `run.sh` and for the binary run by hand.

- Unset, `run.sh` exports `ZONE_BIND=127.0.0.1`, so the container's port 5080 is only exposed on the host's loopback interface, and the host-header check rejects any `Host` header but localhost and the settings file's `hosts`.
- With `ZONE_ALLOW_REMOTE=1`, `run.sh` exports `ZONE_BIND=0.0.0.0` (LAN-accessible port binding),
  and without `hosts` the host-header check lets in any IP address too (and names in `.internal`), so the LAN reaches Zonewiki by address;
  a name it is opened by goes in `hosts`.

Both compose files map `${ZONE_BIND:-127.0.0.1}:${ZONE_PORT:-5080}:5080`; without `run.sh`, `ZONE_BIND=0.0.0.0` must be set in `.env`, alongside `ZONE_ALLOW_REMOTE=1` or `hosts`.

The binary run by hand listens on `127.0.0.1` unless `ZONE_ALLOW_REMOTE=1` (`ZONE_ALLOW_REMOTE=1 ./zonewiki`), which opens a port alone in `--addr` on every interface.
The `Host` check alone wouldn't keep the network out: a client on the LAN sends `Host: localhost`.

### A Unix socket

`zonewiki --addr unix:PATH` listens on a Unix socket at PATH and on no port:
for a reverse proxy on the same machine, when Zonewiki runs from the binary or in a container with the socket's folder mounted and no network of its own.
Caddy reaches it with `reverse_proxy unix//run/zone/zone.sock`, nginx with `proxy_pass http://unix:/run/zone/zone.sock:;`.

Who may connect is who may reach the path: the socket itself is open to every local user (mode 0666, as a port on localhost is), so the folder it is in decides, and the proxy's user must be able to enter that folder.
Zonewiki replaces a socket file that a Zonewiki that was killed left behind;
it doesn't start when something still listens there, or when the path is a file of another kind (`listen` in `go/server.go`).

Whoever connects has no address and is taken for the proxy: its `X-Forwarded-For` names the client, whatever `trusted-proxies` holds (Throttling in `docs/spec/security.md`).
`hosts` and the rest of a proxy's setup stay as with a port.
The healthcheck over it: `zonewiki health unix:PATH`.

### Started by systemd, asleep when idle

`zonewiki --addr fd:3` serves on a listening socket Zonewiki was started with, the way systemd hands one to a service it starts for a socket unit
(socket activation; `fd:3` is the first such socket, and Podman passes it on into a container).
systemd holds the socket and starts Zonewiki when a connection comes:

```
# zone.socket                          # zone.service
[Socket]                               [Service]
ListenStream=/run/zone/zone.sock       Environment=ZONE_DATA=/srv/wiki ZONE_IDLE_EXIT=15m
                                       ExecStart=/usr/local/bin/zonewiki --addr fd:3
```

With `ZONE_IDLE_EXIT` (a duration: `15m`, `1h`; unset or `off`: never) Zonewiki exits, with exit code 0, once no request came for that long (`exitWhenIdle` in `go/server.go`),
and uses no memory until the next one starts it again:
that one waits in the socket for the start, a fraction of a second, since the search index is in its files.

What counts as a request is everything but `/api/health`, so whatever watches Zonewiki doesn't keep it up;
a browser tab nobody is at asks nothing either (`whileUsed`, Modules in `docs/spec/navigation.md`).

Zonewiki stays up:

- while a request runs;
- while a throttle remembers a wrong password or token (for a day after a client's last wrong try, until its right one): an exit would forget the count and give a guesser five new tries each time;
- while it packs the repository (Packing the repository, below): a join of the packs stopped halfway is done again from the start;
- while the morning's mails go out (below).

**A Zonewiki that nobody asked sleeps again after a minute**, not after `ZONE_IDLE_EXIT`: one that no request reached since its start was started for its own work, by a health check or for the morning's mail, and that isn't someone using the wiki (`idleAfter`, `unaskedIdle` in `go/server.go`).
The minute counts from its start, and anew from when the morning's mails are out; what keeps a Zonewiki up, above, still does.
A Zonewiki started by a request is as before: the request waits in the socket and is the first thing answered, and from then on the setting's time holds.

**The morning's mail needs Zonewiki up in the morning** (The morning's mail in `docs/spec/tasks.md`): it goes out from 7:00 to noon in the wiki's time, the first time Zonewiki looks, and a Zonewiki asleep looks at nothing.
So Zonewiki says when it wants to be started (When to be started, below), and whoever holds the socket starts it then, with any request;
the healthcheck does (`zonewiki health unix:/run/zone/zone.sock`, or `GET /api/health`), which starts Zonewiki and doesn't keep it up afterwards.
Without a start in the morning the mail goes out on the mornings someone opens the wiki before noon, and on no others.

### When to be started

`.zone/wake` in the wiki's folder is one line: the next moment Zonewiki has something to do by itself, in UTC.

```
2026-11-26T06:00:00Z
```

| The file says | When |
|---------------|------|
| The next 7:00 of the wiki (its `timezone`: The wiki's timezone in `docs/spec/users.md`) | while a user has the morning's mail turned on and an address to send it to |
| This morning's 7:00, which may be past | until each of them was looked into today, up to noon |
| Tomorrow's 7:00 | from then on, and from noon |
| Nothing: there is no file | nobody wants the morning's mail, or Zonewiki sends no mail |

It means "from then on", not "at": the mail goes out until noon, so whoever starts Zonewiki sets the pace, and a host with many wikis needn't start them all in one minute.
A start a few minutes late costs nothing; one after noon sends no mail that day.

One wiki on one machine can be started by a timer at the wiki's hour, or by a script that reads the file:

```ini
# zone-morning.timer                   # zone-morning.service
[Timer]                                [Service]
OnCalendar=*-*-* 07:05:00              Type=oneshot
                                       ExecStart=/usr/local/bin/zonewiki health unix:/run/zone/zone.sock
```

Zonewiki writes the file when it starts, each minute when it changed, when a user turns the morning's mail on or off, when the settings change (an address confirmed, the timezone), and before it exits for being idle;
readable by its owner alone, and whole or not there (`writeWake`, `nextWake` in `go/digest.go`).
Whoever reads it for many wikis reads it as untrusted: a wiki's admin, or whoever writes its files, can say any time.

What an exit loses is what a restart loses: the caches, which are built again, and the texts an editor's save is merged from,
so a tab left open over a sleep saves as after any restart
(plainly if the page is as it was; with a conflict to settle if it changed meanwhile; Several editors in `docs/spec/editor.md`).
Who wrote a draft that isn't committed yet is only in memory, so before the exit each draft is committed in its author's name (`commitDrafts` in `go/merge.go`).

`ZONE_IDLE_EXIT` on a socket or port Zonewiki opened itself is ignored, with a line in the log, since nothing would start Zonewiki again.
A Unix socket handed over is treated as one Zonewiki opened (`--addr unix:PATH`, above): its peer is the proxy; the file is the holder's and stays when Zonewiki exits.

## Mail

Zonewiki sends mail for sign-up (Sign-up in `docs/spec/users.md`), for the link that confirms a user's address (Mail addresses there) and to tell a user that someone gave them a task (Assigning tasks there), mentions them or comments on a task of theirs, and the morning's mail, once a day (Mail and The morning's mail in `docs/spec/tasks.md`; each kind only once the user turns it on),
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

Mail is set up with a mail server or with the mail folder (below), one of the two:
where neither is, Zonewiki sends no mail, the **Notifications** tab of Settings isn't shown, and sign-up is refused at start.

### The mail folder

`ZONE_MAIL_SPOOL=1` instead of a mail server: Zonewiki sends nothing itself.
It writes each mail as a file into the wiki's `.zone/outbox/`, and whoever runs the host sends what is there and takes each file away.
It is for a Zonewiki that has no network of its own (a container without one), or a host with its own relay and its own rules for what goes out.

| | |
|---|---|
| The file's name | The time it was written, UTC, to the nanosecond, and 16 random hex digits: `20261125T193248.000000001Z-0123456789abcdef.json`. The oldest sorts first |
| What it holds | One JSON object with four fields: `to` (one address), `subject` (one line), `body` (plain text, `\n` line ends) and `at` (when it was written, RFC 3339, UTC) |
| Its rights | The folder is the owner's alone (0700), a file too (0600) |
| Whole or not there | A mail is written beside the folder (`.zone/outbox-….tmp`) and renamed into it: what is in the folder is a whole mail |
| At most 500 | With 500 files waiting, a new mail is dropped and the log says which (to whom, its subject): nobody fetching them mustn't fill the wiki |

Whoever sends a file writes the mail's headers (From, Date, Message-ID); Zonewiki says only who it is for and what it says.
A sender should treat a file as what the wiki's users wrote (a task's title is in a subject), and send it once: Zonewiki never looks at the folder again.
Zonewiki itself mails only an address its owner confirmed (Mail addresses in `docs/spec/users.md`), and the link that asks them to.

With the folder, mail counts as set up: every kind of mail is there as with a server.
`.zone/` is out of git and out of an export, so a mail waiting is in neither.

`go/spool.go`; tests: `TestTheMailFolder` in `tests/test_email.py`, `TestSpoolMail` and `TestLoadMailSpool` in `go/email_test.go`.

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

Zonewiki won't start on a mail setting without `ZONE_SMTP_HOST`,
a port or TLS mode it doesn't know,
a password without a user,
a missing or invalid `ZONE_MAIL_FROM` (a bare address, no name),
`ZONE_MAIL_SPOOL` beside any `ZONE_SMTP_*` or `ZONE_MAIL_FROM` (one way to send, not both),
or `ZONE_SIGNUP` without a mail server or the mail folder.

### Time limits

20 seconds to connect, a minute for the whole mail.
The sign-up form waits for it, so it can say when the mail couldn't be sent (and logs why).
The mail about an assigned task is sent in the background: a task write never waits for the mail server, and a failure is only logged.

## Mobile / phone access

Set `ZONE_ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces:
`run.sh` then maps `0.0.0.0:5080` and the server's host-header check lets in any IP address (a name goes in `hosts`).
Phones on the same network reach Zonewiki at `http://<host-LAN-IP>:5080`.

A wiki other devices can reach and that has no login yet answers only `/setup`, where the first login is chosen,
unless `no-login = yes` says it is open on purpose (First run in `docs/spec/security.md`, `go/setup.go`);
a password (`./run.sh config passwd`) or users (`./run.sh user add NAME`) set from the terminal do the same.

The UI is responsive down to phone widths and the editor works on mobile.
The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one.
Without it, full-screen views (the tasks edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard.
iOS Safari ignores the setting.

## Version

Zonewiki's version is a semver constant, `version` in `go/config.go`, bumped by hand.
Zonewiki is an alpha, so its version starts with 0:
a release with a new feature, or one that changes something people rely on (how tasks are stored, the settings file's keys, the MCP tools, URLs), raises the minor number,
a release of fixes only the patch number, so `0.5.x` is `0.5.0` and its fixes.
Between releases the version carries `-dev` and names the next release (`0.6.0-dev`), so a build from a commit after a tag never says it is the tagged release.
The release commit drops the suffix and gets the tag; the commit after it starts the next `-dev`.
1.0 will promise that a newer Zonewiki takes an older wiki over by itself (a step for each change of The data format, below) and that the MCP tools and settings don't break without notice.
(The numbers 1.0 to 2.0 in the history from before Zonewiki was published were never released.)
Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `go/` or `public/` had uncommitted changes
(a commit can't contain its own hash, so this part can't be a committed constant).

| Where | What it shows |
|-------|---------------|
| The shortcuts modal (`?`) | Both (`Zonewiki 0.5.0 · c28ec9e 2026-09-26 19:22`) |
| Every page's bottom-left corner | The version and short hash (`Zonewiki 0.5.0 · c28ec9e`, `+dirty` when so) |
| The `<meta name="generator">` tag | The version |
| `/api/version` | `{ version, build }` |
| `zonewiki version`, `--version` | Both, as the start-up line (`Zonewiki 0.5.0 (c28ec9e 2026-09-26 19:22)`) |

The corner shows the full build on hover (`.zone-build` in `layout.html`); it is faint, only from 1140px wide where the margin beside the text column has room, and not in the editor.

The image has no `.git`, so `run.sh` computes the build string and passes it as the `ZONE_BUILD` build arg
(the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer).
Without the container, the binary has the same string from what `go build` recorded of the checkout (`sourceBuild()`: the commit, its time, whether the tree had changes);
`$ZONE_BUILD` wins when set, and with neither the modal shows the version alone.

The shortcuts modal (`?`) also links to Zonewiki's source (`sourceURL` in `go/config.go`) and names its license, AGPL-3.0-or-later:
whoever runs a changed Zonewiki for others over a network has to offer them its source, so a fork points that constant at its own copy.

## Home-screen app

`public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `templates/layout.html`) give "Add to home screen" a proper Zonewiki name and icon.
Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie.
The PNGs are rendered once from `public/icon.svg` (full-bleed green square, the white open frame inside the central 80% "maskable" safe zone; written with the favicon and `docs/logo/` by `docs/logo/make_logo.py`) and committed, so there is still no build step.
To change the icon, change the drawing in `make_logo.py` and run it with `--png` in the Playwright image (`zonewiki-tools`), which renders the PNGs from the SVG.
Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab.
iOS Safari is more lenient.

## Data directory

Set `ZONE_DATA` in `.env` to any host path (default: `~/zonewiki`).
`run.sh` expands a leading `~` to your home directory before starting the container.
A folder that `ZONE_DATA` names must exist: neither `run.sh up` nor Zonewiki creates it (`loadConfig` exits with "no wiki at …"), so a mistyped path can't start an empty wiki somewhere else.

Zonewiki started with `ZONE_DATA` unset (or empty), as the binary is on a desktop, takes `~/zonewiki` (`defaultData` in `go/config.go`: `$HOME/zonewiki`),
and makes that one folder at the first start, saying so in its log ("ZONE_DATA isn't set: made a new wiki at …");
the commands (`zonewiki user …`, `config …`) do the same, so a password can be set before the first start.
It makes nothing else: without a home folder, or with a file at `~/zonewiki`, it exits and says to set `ZONE_DATA`;
when that file is the running binary itself (downloaded into the home folder and named `zonewiki`, as the README's steps name it), it says so, and to move the binary to another folder (`isExecutable`).
At start it prints where to open it and where the wiki is, under the line it listens on:
`Open http://localhost:5080 in a browser. The wiki is /home/anna/zonewiki`
(`base-url` when it is set; no such line on a socket or behind compose, whose port mapping Zonewiki doesn't know: `ZONE_BIND` set).

In an existing wiki folder Zonewiki creates what it needs (`_assets/`, `_tasks/`, `index.md`):

```
ZONE_DATA=~/zonewiki    # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`.

### A new wiki

A folder is a wiki when it has a `.zone-format`,
or a `.zone/secret_key`, which only a start writes: a wiki an older Zonewiki served, from before the format file, starts as it did and gets the file (The data format, below);
or a `.zone/imported`, which `zonewiki import` leaves: a wiki of that age moved here (Moving a wiki, below).
A folder with a `.zone-import/` holds an import that didn't finish, and Zonewiki doesn't start there (`unfinishedImport`).
A start on one that isn't makes it one: a home page, `_assets/`, `.zone/`, `.zone-format`, and, with git, a repository whose first commit holds its files as they are.
An empty folder (or one with only `.zone/`, which the commands write before a first start, `_assets/`, and an empty repository's `.git`) is made a wiki without asking.
A folder that holds files is made one only with `--init` or `ZONE_INIT=1` (`newWikiError`):
otherwise Zonewiki doesn't start, leaves the folder as it was, and names the first files it found, how to go on (`--init`, `ZONE_INIT=1` in `.env` for the container), and that ZONE_DATA may name another folder.
That is what a folder of Markdown notes wants once, and what a mistyped `ZONE_DATA` (the home folder, `~/Documents`) must never get: all its files committed.
After the first start the folder is a wiki, so `ZONE_INIT` may stay set.
A `.git` doesn't make a folder a wiki: a project's checkout and a home folder kept in git have one, and Zonewiki would commit its home page and its changes into that repository.
So notes kept in git already need `--init` once too; the wiki's history then goes on from theirs.
Zonewiki's own checkout (a `zonefiles.go` and a `go/go.mod`; `isSourceCheckout`) is told apart, since `git clone` in the home folder makes `~/zonewiki`, the default wiki:
the message says the folder is Zonewiki's source code, not a wiki, and to set `ZONE_DATA` or clone under another name, without offering `--init`.
The check runs at start before Zonewiki writes anything in the folder, after the one for a folder inside another repository (Layout, below).

### Layout

- `$ZONE_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$ZONE_DATA/private/<user>/` — with users, each user's private pages (`docs/spec/users.md`); without users an ordinary folder
- `$ZONE_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically);
  a group's page's in `groups/<group>/_assets/`, a private page's in `private/<user>/_assets/`, a task's (with users) in `_tasks/files/<task id>/_assets/`
- `$ZONE_DATA/_tasks/` — the tasks (`tasks.json`, `closed.json`, `closed-YYYY-MM.json`, each list's in `lists/<name>/`, and `lists.json`, the lists' registry, with the settings of All tasks; see Storage in `docs/spec/tasks.md`);
  each user's private tasks in `private/<user>/_tasks/`
- `$ZONE_DATA/.git/` — the wiki's history: Zonewiki makes the folder a git repository at its first start (`gitSetup`) and commits every change made through it (Packing the repository, below).
  A folder inside another repository's working tree is made one only where that repository ignores it (`git check-ignore`; a home folder kept in git for its dotfiles, which ignores the rest): otherwise Zonewiki doesn't start, leaves the folder as it was (it checks before it writes anything there), and says to use a folder outside it or to add the folder to that repository's `.gitignore` (`outerRepo`), since a repository of the wiki's own would hide its files from the outer one, whose `git add` records a pointer, not the pages.
  A folder that is a repository already is the wiki's, wherever it lies.
  Its log says which it did at each start: "made … a git repository" (its files as they are, in one commit), "found a git repository in …" (the history goes on), or, without git on `PATH`, that the wiki keeps no history
- `$ZONE_DATA/.zone-format` — the format the wiki's files are in, a number, in git (The data format, below)
- `$ZONE_DATA/.zone/` — Zonewiki's own state, out of git (What is in .zone/, below)

Uploaded image URLs use `/_assets/<filename>` (served by `serveUpload` from `cfg.Uploads`), not the static file path.

### What is in .zone/

Zonewiki's own state, none of it in git (`.git/info/exclude` has `/.zone/*`, `excludeState`; each file's name is `statePath(…)` in the code):

- `config` — the settings file: the wiki's settings and users, password and token hashes (`docs/spec/users.md`)
- `secret_key` — the key that signs the session cookies (0600; `secretKey`)
- `signed-out` — the ids of sessions signed out, until their cookies expire (`docs/spec/security.md`)
- `links` — the open invite, reset and sign-up links, their tokens as hashes (`docs/spec/users.md`)
- `removed-users` — the names of the users removed, which no one takes by signing up (Sign-up in `docs/spec/users.md`)
- `grants` — the assistants' OAuth connections, their tokens as hashes (`docs/spec/mcp.md`)
- `config.lock`, `links.lock`, `grants.lock`, `removed-users.lock` — empty files the writers of those four lock (`lockFile`, flock), so two writers take turns
- `usage/counts.json`, `usage/users.json` — how much the wiki is used, as counts a day for 8 weeks, and the day each user was last active (Usage in `docs/spec/users.md`); `counts.json` holds no name and is the one file here a host may read
- `seen/<user>.json` — what each user has seen of the tasks' news (What you have seen, under News on your tasks in `docs/spec/tasks.md`)
- `watch/<user>.json` — the pages and folders each user watches, what they have seen of them (Watching pages in `docs/spec/navigation.md`), the kinds of mail they turned on (Notifications in `docs/spec/users.md`) and the pages that mention them (Mentions on pages in `docs/spec/navigation.md`)
- `digest.json` — the last morning each user's mail was looked into, and up to when they were told what is new (The morning's mail in `docs/spec/tasks.md`)
- `wake` — the next moment Zonewiki wants to be started for the morning's mail, one line in UTC; no file when nobody wants it (When to be started, above)
- `unsaved-files.json` — who pasted the first file for a task not made yet (a new task's dialog before its first Save), whose those files are until then (A task's files in `docs/spec/tasks.md`)
- `drafts/<user>/` — an editor's text left in a conflict when its tab closed, for its user's next edit, 30 days at most (`docs/spec/editor.md`)
- `tmp/` — a three-way merge's scratch files, removed after each merge
- `imports/<id>/` — a zip of notes uploaded to import, unpacked, waiting for its uploader's Import or Cancel, an hour at most, and all removed at a start (The waiting import in `docs/spec/editor.md`)
- `index/` — search's files: `pages.idx` and `sections.idx` (the BM25 indexes of what is shared), `groups/<group>/` (the same for each group's pages and its lists' tasks, read by its members' searches alone), `tasks/<hash>.gob` (each closed tasks' archive's search docs) and `embeddings.json` (the semantic search cache) (`docs/spec/search.md`)
- `queries.jsonl` — the query log, only with `query-log = yes`
- `tasks-checked` — the mark that a start has checked the task files, so later starts skip it while git says they haven't changed (Storage in `docs/spec/tasks.md`)
- `imported` — a line `zonewiki import` leaves, with its date and version: the folder is a wiki that was moved here (Moving a wiki, below)

### The data format

`.zone-format` at the wiki's root says which format the wiki's files are in: a number and a newline (`1`).
Zonewiki writes it at a wiki's first start, in a commit of its own ("Note the format of the wiki's files").
A wiki without it is taken for a new one: Zonewiki takes nothing over from an older Zonewiki's files (what Zonewiki wrote before the file is no longer converted), and writes the file.

| The file says | Zonewiki |
|---------------|------|
| Nothing (no file) | Writes its own format |
| Its own format | Starts |
| An older format | Runs the steps from that format on, each writing its number in its own commit |
| A newer format | Doesn't start: "the wiki's files are in format 2 (.zone-format), which a newer Zonewiki wrote" |
| Anything but a number | Doesn't start, and says how to mend it |

Format 1 is the first, so there are no steps yet: the row for an older format is for when a change raises the number.

**Why it is refused**: a newer Zonewiki changed files in ways this one would misread and then write over (an image rolled back after an upgrade).
Start the newer Zonewiki again, or go back: revert, newest first, the commits that raised the number (`git log -- .zone-format`), and with them the steps they wrote.

**Why it is in git, not in `.zone/`**: it speaks for the files in git, so it has to move with them.
A revert of a step's commit takes back the files and the number together, and so do a `git pull` and a checkout of an older commit;
a number in `.zone/` would still say the newer format, so the older Zonewiki would refuse the wiki, and the newer one would skip the step the revert took back.
A backup or a copy of the folder has the file either way, and a clone of the remote has it too.
No route or tool reads or writes it (`safePath` takes only a page's `.md`).

**How it works** (`go/wikiformat.go`): `checkWikiFormat` reads the file before anything at start writes, and `noteWikiFormat` writes the number into a wiki that has none, after the task files are set up (`setUpTasks`).
A change to what Zonewiki keeps in git that an older Zonewiki would misread raises `wikiFormat`, with a step at start that takes a wiki of the format before over and writes the new number into the same commit (`writeWikiFormat`).
Readers stay lenient all the same: the number says which steps Zonewiki ran, not that every file is as Zonewiki wrote it (a file edited by hand or pulled from elsewhere is read as before).
The settings file and the rest of `.zone/` aren't covered: they are no wiki data (What is in .zone/, above).

What holds users' words or secrets is written readable by Zonewiki's user alone (0600), whatever the umask: `config`, `secret_key`, `signed-out`, `links`, `grants`, `seen/`, `watch/`, `digest.json`, `usage/users.json`, `unsaved-files.json`, `drafts/`, the index files (the groups' in `index/groups/` among them, with their pages' words), `index/tasks/` (private tasks' titles among them), `embeddings.json` (every page's slug) and `queries.jsonl` (every user's queries and what they found) (`atomicfile.WriteOwnerOnly`; a file from before is made so at its next write).
The pages themselves, in the wiki, keep the umask's mode, as a file made in an editor does.

### A git remote

Optional: `./run.sh config set git-remote git@github.com:user/wiki.git`.
At start and when it changes, `setGitRemote()` adds or updates `origin` to that URL, so `git push` works from the terminal.
Zonewiki doesn't push by itself (Backups, below).

### Backups

**Back up the whole data directory** (`ZONE_DATA`): it is the wiki, and there is nothing of it anywhere else.
The simplest backup is a copy of that folder taken while Zonewiki is stopped (`./run.sh down`), or a snapshot of the file system it is on (a NAS's snapshots, ZFS, btrfs).
Zonewiki writes each file whole (a reader never sees half of one), but a copy taken file by file while Zonewiki runs can catch a git commit or a task write half done across files.

- **What git holds**: the pages, the uploads (`_assets/` and the private and groups' ones), the tasks and their lists (`_tasks/`, `private/<user>/_tasks/`), each with its history.
  With `git-remote` set (A git remote, above), `git -C "$ZONE_DATA" push origin` copies that elsewhere (Zonewiki doesn't push by itself); a change made on disk and not committed yet is in no commit.
- **What git doesn't hold, and a clone of the remote lacks**: `.zone/`, above all
  `config` (the users, their roles, groups, mail addresses and password hashes, the MCP tokens' hashes and every setting: without it a clone of a wiki with users won't start, `missingUsers`),
  `secret_key` (without it everyone signs in again),
  `grants` (without it every assistant connected through OAuth connects again),
  `links` (without it the open invite and reset links stop working)
  `signed-out` (without it a copy of a cookie signed out works again, until it expires)
  and `removed-users` (without it a stranger could sign up under the name of a user removed whose trace is only there).
  Back these up with the folder; where the git remote is the only copy, keep a copy of `.zone/` beside it.
  They hold password and token hashes and keys: keep the backup as confidential as the wiki (Before you open it up in `docs/spec/security.md`).
- **What can be rebuilt, and needn't be backed up**: `.zone/index/` (Zonewiki builds the search indexes again when they are first needed, which takes a while on a big wiki, and with semantic search embeds every section and task again in the background, which takes longer),
  `.zone/tmp/`, `.zone/imports/`, the lock files and the mark `tasks-checked` (a start without it looks the task files through again and changes nothing that is right).
  Lost, `seen/` shows each user the tasks' news again, `watch/` leaves nobody watching a page and nobody with a mail turned on, `digest.json` has the next morning's mail tell of the last day, `drafts/` loses only a text left in a conflict, `unsaved-files.json` the files pasted in a dialog not saved yet to their uploader (no one opens them), and `queries.jsonl` is a log.
- **Restoring**: put the folder back where `ZONE_DATA` points and start Zonewiki;
  the files must belong to the user Zonewiki runs as, and `.zone/config` and `secret_key` should stay readable by it alone (0600: Zonewiki warns in its log when others may read the settings file).

## Moving a wiki

A wiki moves from one Zonewiki to another in one file: from a computer to a server, from a NAS to the hosted Zonewiki, or back.
`zonewiki export` writes the file, `zonewiki import` makes a wiki from it.
What moves is everything in the wiki's history (pages, uploads, tasks, lists), its users, who sign in as before, and what each of them keeps beside the wiki: the pages they watch, what they have read of those and of the tasks' news, and which mail they want (What each user keeps, below); a backup is the folder itself (Backups, above).

```bash
./run.sh export > my-wiki.tar.gz              # on the machine the wiki is on; Zonewiki may be running
./run.sh import < my-wiki.tar.gz              # on the other one: into an empty wiki folder, Zonewiki stopped
./run.sh config set hosts wiki.example.org    # the names it is opened by there, if any
./run.sh up
```

With the binary: `zonewiki export --output my-wiki.tar.gz` and `ZONE_DATA=/srv/wiki zonewiki import my-wiki.tar.gz`.
The hosted Zonewiki writes the same file (its account page's export) and takes one when a wiki is set up there.

| Command | What it does |
|---------|--------------|
| `zonewiki export` | Writes `zonewiki-YYYY-MM-DD.tar.gz` in the current folder |
| `zonewiki export --output FILE` | Writes FILE; `-` is stdout, which is what `./run.sh export` uses |
| `zonewiki export --work FOLDER` | Puts the export together in FOLDER, not beside FILE (for stdout: not in the wiki's `.zone/`) |
| `zonewiki import FILE` | Makes the wiki in `ZONE_DATA` from FILE; `-` is stdin, which is what `./run.sh import` uses |
| `zonewiki import --max-size SIZE FILE` | Refuses a wiki that would take more than SIZE on disk (`1GB`), its history and its files |
| `zonewiki import --users-only FILE` | Of the settings file, takes the users, the groups, `locale`, `date-format`, `timezone` and `noshare` only; a user's mail address comes along not confirmed (Mail addresses in `docs/spec/users.md`) |
| `zonewiki import --work FOLDER FILE` | Unpacks the archive in FOLDER, not in `ZONE_DATA` |

An import's options come before the file.

### What an export holds

The file is a gzipped tar with one folder, `wiki/`:

- a clone of the wiki's git repository, with its files checked out: every commit, the branch that is checked out, the tags; no remote;
- `wiki/.zone/config`, the settings file, when the wiki has one: the settings, the users, their roles and groups, and the hashes of passwords and MCP tokens.

So it holds every user's private pages and the password hashes, and is as confidential as the wiki: the command says so, and writes the file for its owner alone (0600).
An export doesn't write over a file that is there.

Unpacked, it is a wiki's folder: whoever leaves Zonewiki has the pages as files and the history as a git repository.

### What each user keeps

Two folders of `.zone/` hold a file a user, with what is theirs alone and not in the wiki's history, and an export takes them along for the users the settings file names:

| In the file | Holds | |
|---|---|---|
| `.zone/watch/<user>.json` | the pages and folders they watch, what they have seen of them, the kinds of mail they turned on, the pages that mention them | Watching pages and Mentions on pages in `docs/spec/navigation.md`, Notifications in `docs/spec/users.md` |
| `.zone/seen/<user>.json` | the tasks whose news they have read | News on your tasks in `docs/spec/tasks.md` |

So after a move a user watches what they watched, old news is not new again, and the mail they ticked is still ticked (it goes out once the address is confirmed where the wiki now is, if the import was made with `--users-only`).
An archive without these files (an older export, a wiki nobody watches a page in) imports as before.
The import says "What they watch, have read and want mailed came along (3 users)."

An import doesn't copy such a file: it reads it and writes what it understood (`importedWatch`, `importedSeen` in `go/transferstate.go`), for a user the imported settings have.

| In an archive | The import |
|---|---|
| A file of a name that is no user's | passes it over |
| A file that isn't JSON, or holds nothing to keep | passes it over: that user starts as new |
| A watch that could be no page or folder (`..`, an empty part, a control character), a mention of what could be no page or by what could be no user, a look at what could be no task, a kind of mail Zonewiki doesn't know, any other key | leaves it out, and keeps the rest |
| More than 5,000 watches or looks in one file | keeps the first 5,000 |
| A file over 1 MB | refuses the archive, as a settings file over 4 MB |
| A link or a folder where a file should be, a file of another folder of `.zone/` | not unpacked |

### What stays behind

| Not in the file | So, where the wiki moves to |
|---|---|
| `.zone/secret_key`, `signed-out` | everyone signs in again, with the password they had |
| `.zone/grants` | an assistant connected by signing in connects again (the address changes anyway); a static MCP token works as before |
| `.zone/links` | open invite and reset links don't work |
| `.zone/index/` | search's indexes are built again when first needed |
| `.zone/usage/` | the Usage page counts from nothing |
| `.zone/imports/` | a zip of notes waiting to be imported is uploaded again |
| `.zone/drafts/`, `removed-users`, `queries.jsonl` | a text left in a conflict is gone; a removed user's name is still not a stranger's to sign up with where a private folder, a commit or a task names it (Sign-up in `docs/spec/users.md`) |
| What isn't committed | lost: see Text that is not committed, below |
| The host's settings: `hosts`, `base-url`, `git-remote`, `trusted-proxies`, `storage-max`, `imprint`, `privacy`, `oauth-log` | set them again where they are needed (`zonewiki config set`); the import names the ones it left out |

### Text that is not committed

An export holds what is in git.
Zonewiki commits a page's text at Done or at the page's next change, so an auto-save not followed by either, and a file edited on disk, are in no commit yet.
The export counts such files and names the first ones on stderr (`2 files hold changes that aren't committed yet, and those changes aren't in the export: …`), and writes the archive all the same.
Open and save what should come along, then export again.

### Where an import puts the wiki

Into `ZONE_DATA`, which must be empty, or not there yet (the import makes the folder; the folder it is in must exist).
A folder that holds anything is refused, the fresh wiki a first start made included: an import makes a whole wiki and never adds to one.
Empty the folder, or point `ZONE_DATA` at a new one.

The import says what came: how many pages, the format, the users, and the settings it left in the file.
Then start Zonewiki: it brings a wiki of an older format up to date as it does at any start (The data format, above), and makes the folder's `_assets/`, the session key and the indexes.
A wiki in a format newer than this Zonewiki knows is refused, with the newer Zonewiki as the way to go.

Nothing is committed by an import: the history is the archive's, to the commit.

### How an export is made

`exportMain` in `go/transfer.go`.
It clones the repository as a git client fetches it (`git clone --no-local`), not by copying its files, which Zonewiki packs and prunes while it runs (Packing the repository, below): so an export is whole whatever the server does meanwhile, and takes none of its locks.
The clone has `core.symlinks=false`, so a link in the history is a file that names its target; its `origin`, which would name the folder the wiki was in, is removed.
The settings file is copied in as it is; Zonewiki writes it whole, by a rename, so the copy is never half a file.
The archive is written by Zonewiki itself (`internal/wikiarchive`), files and folders only, to a temporary file renamed into place.
The clone is made in a folder beside the output (for stdout in the wiki's `.zone/`, which the container can write and git ignores; `--work` names another) and removed afterwards, also when the command is stopped.

### How an import is made

`importMain` in `go/transfer.go`.
An archive is untrusted input: the hosted Zonewiki imports what its customers upload (An imported archive in `docs/spec/security.md`).
So nothing of it is used as it is:

1. **Unpack** (`wikiarchive.Read`), into `.zone-import/` in `ZONE_DATA`:
   only the repository's history (`.git/HEAD`, `.git/packed-refs`, `.git/objects/`, `.git/refs/`), `.zone/config` and the users' files (`.zone/watch/*.json`, `.zone/seen/*.json`: What each user keeps, above) are written out, as plain files.
   The repository's `config`, its `hooks/`, its `info/`, `objects/info/alternates` and the whole work tree are passed by.
   An entry that isn't a file or a folder where one of those should be (a link, a device), a name with `..`, a leading `/`, a backslash or a control character, anything beside `wiki/`, a file twice, a name that is both a file and a folder: the archive is refused.
   These checks aren't tar's: the package reads entries from a source (a gzipped tar here, or a zip) and unpacks them in one place (`wikiarchive.Unpack`), which its caller tells what to keep, how many bytes it may write (counted as they are written, not as a header states) and how many entries the archive may hold, and in which words to refuse.
   A zip's source refuses a name that isn't UTF-8 and takes a link from its mode bits; it is for the markdown import to come, and `zonewiki import` reads tar alone.
2. **Fetch the history** into a repository made here (`git init`, then `git fetch` of all branches and tags, with `transfer.fsckObjects`): what git reads and runs is this repository's config, never the archive's.
3. **Check out** the branch the archive had checked out, with `core.symlinks=false`.
   With `--max-size`, the files' sizes are asked of the history first (`git ls-tree -l`): a small history can hold files far bigger than itself.
4. **Check the format** (`.zone-format`): a newer one is refused.
5. **Take the settings file** through the same parser and checks a start uses (`importedSettings`); one Zonewiki wouldn't start with is refused, with the line.
   The host's keys (`hostKeys` in `go/configfile.go`) are taken out; with `--users-only` every key but the users, the groups and `usersOnlyKeys`.
   It is written 0600.
   The users' files are written beside it, each anew from what was read of it, for the users these settings have (`readUserFiles`).
6. **Move into place**: the wiki's entries are renamed up into `ZONE_DATA`, `.git` last, and `.zone-import/` is removed after that.

A failure before step 6 leaves `ZONE_DATA` empty again.
`.zone-import/` is there from the first step to the last, so a folder that holds it holds an import that didn't finish (killed, or out of room while moving):
Zonewiki doesn't start there (`unfinishedImport`), and the next import empties the folder and starts over.

The import leaves `.zone/imported`, a line with its date and version.
It makes the folder a wiki for the next start though it may have no `.zone-format` (an archive of a wiki from before that file): A new wiki, above.

## Packing the repository

Zonewiki packs the wiki's git repository itself, in the background, so that no commit (and no request that made one) waits for git's housekeeping.
git's own maintenance is off; Zonewiki packs the loose objects every 50 commits and joins the packs into one when they have become many or heavy.
A run that fails is logged and leaves the repository as it is, only larger.

### git's own maintenance is off

git runs with `maintenance.autoDetach=false` and `gc.autoDetach=false` (package `gitrepo`'s environment; the tests' own git calls set the same, `settings.GIT_ENV`).
git 2.47+ starts its automatic maintenance after a commit as a detached process,
which nobody reaped with Zonewiki as the container's PID 1,
so every commit left a zombie until the PID limit (2048) stopped everything (it also crashed test sessions).
Not detached, git reaps its own children; the compose files' `init: true` (a minimal init as PID 1) covers anything else.

git's own packing after a commit is off too (`gc.auto=0`, `maintenance.auto=false` in the same environment),
since it runs inside the commit, and the request that made it, when its turn comes.

### When Zonewiki packs

`packSoon`, `packNow` in `go/gitstore.go`; `Repo.Pack` in package `gitrepo`.

- Every 50 commits (`packEvery`), and once after the start, Zonewiki packs the loose objects in the background,
  one run at a time, beside whatever commits meanwhile.
- At start Zonewiki packs only when an earlier run left 200 loose objects or more.
- A pack is `git repack -l`, which leaves what is packed as it is.
- Then, while no commit runs (`commitMu`), the loose objects that are packed now are removed
  (`git prune-packed`: beside a commit it would take away the folder a `git add` is writing into).
- After each pack Zonewiki adds the new commits to the commit-graph (below), and at the start it writes one for a wiki that has none.

### The commit-graph

git's own maintenance would write a commit-graph, and it is off, so Zonewiki writes it (`writeGraph` in `go/gitstore.go`, `Repo.WriteGraph` in package `gitrepo`):
`git commit-graph write --reachable --changed-paths --split`, in the background beside whatever commits.
It holds, for each commit, a filter of the paths it changed (a Bloom filter, version 2 where git knows it),
so a log of one path (a page's history, its last change on the page view, whether a page was deleted, whether a name was ever used) reads only the commits that may have touched it, not the whole history.
`--split` writes what is new as a layer of its own, so a write costs the commits since the last one; git joins the layers as they grow.
The first write of a long history takes a second or two at 100,000 commits; until it is there, and for the commits since the last pack, git reads the history as it did without it.
It is a cache: the repository is whole without it, and git writes another from the commits.

A page's history follows renames without `git log --follow`, which reads every commit whatever the graph holds (Renames in the history in `docs/spec/rendering.md`).
A log of every path, as Changes, the deleted pages and who changed a page last read it, gets nothing from the filters: the deleted pages are read once and after that only from what was committed since (`listDeletedPages`).

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

Zonewiki keeps no index of the wiki's pages or tasks in memory beyond per-page caches:
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

A personal wiki or a team's is far below where this is felt; past some ten thousand pages or open tasks every view pays for it, and a NAS's disk and CPU bring that point closer.

Who changed a page last, which a search result says (Who changed a page last in `docs/spec/search.md`), measured the same way (`BenchmarkWhoChanged`, its function rather than a route):

| What | Wiki | Time |
|------|------|------|
| The scan of the history, at a run's first search | 1,000 pages, 5,000 commits | 42 ms |
| The same | 20,000 pages, 100,000 commits | 2.2 s (git's log alone: 2.1 s) |
| A search after a commit | 20,000 pages, 100,000 commits | 1.6 ms |
| A search when nothing moved | 20,000 pages | 7 µs |

The pages' last commits take some 160 bytes a page in memory.

A page's history, a link to a deleted page and the deleted pages in a long history (100,000 commits) are measured by the bench's `test_a_long_history` (The bench in `docs/spec/testing.md`), against the budgets in `tests/bench_tasks.py`: a page's history in some 0.1 s, as it is in a short one, with the commit-graph (above).
Page writes run one at a time (`pagesMu`), each with its git commit (a few git processes), so a wiki is not for many people saving at once.
Closed tasks cost nothing until they are looked at: a page view doesn't read their files.
What would lift the ceiling, when it is needed: the pages' mtimes taken from the walk instead of a `stat` each (`pageStats`).
The task files are already parsed once and kept until a file's time, size or inode changes (`parsedFile` in `go/internal/taskfiles`).

## From Zonewiki 1 (or Leaf)

Zonewiki takes nothing over from an older Zonewiki's files (Older data in `docs/spec/tasks.md`).
A wiki last run by Zonewiki 1 (or Leaf) is started once with the last Zonewiki 1 release (it moves `.leaf/` to `.zone/` and `_deck/` to `_tasks/`), then once with Zonewiki 1caf0d78 or older, which takes its lists and views over, before this one; `[[deck …]]` embeds and Zonewiki 1's names (`/deck`, `leaf_help`, `LEAF_…` in `.env`) are rewritten by hand.
