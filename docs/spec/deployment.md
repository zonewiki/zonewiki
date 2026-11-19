# Deployment

Part of Zone's spec (see `AGENTS.md` for the index).

## Files

- **`Containerfile`** — three stages.
  `build`: Go (a pinned release, checked against its checksum) builds the server from `go/` with cgo (`go mod download` first, so a code change doesn't fetch the modules again).
  `semantic`: with `ZONE_SEMANTIC_SEARCH=on`, onnxruntime's shared library (1.31.0, its GitHub release for the image's architecture) and the model's files (a fixed revision), each checked against its SHA-256, plus onnxruntime's license and third-party notices; off, three empty folders.
  Its own stage, copying nothing from the repo, so a code change never downloads the model again.
  The image: Ubuntu 26.04 LTS with git,
  `tzdata` and CA certificates via `apt`,
  `libonnxruntime.so` in `/usr/local/lib` (`ldconfig`),
  the model in `/opt/zonewiki-model` (`ZONE_MODEL_DIR`),
  the binary as `/app/zonewiki`,
  the browser code as `/app/public`,
  `README.md` plus `docs/spec/` next to it (Zone's documentation, for the MCP tool `zone_help`),
  the licenses as `/app/LICENSE`,
  `/app/THIRD_PARTY_LICENSES` (Go, the Go modules, the model) and `/app/licenses/onnxruntime/` (the vendored libraries' are in `/app/public/vendor/`; Mermaid's bundled packages in `public/vendor/mermaid/THIRD_PARTY_LICENSES`, written by `docs/licenses/mermaid_notices.py`);
  `HOME=/tmp` (Zone may run as a UID without an account).
  Build args `ZONE_SEMANTIC_SEARCH` and `ZONE_BUILD` (last layer).
  About 0.23 GB, 0.9 GB with the model.
- **`compose.yaml`** — builds the image (`build:` with those args) and runs the `zonewiki` container (project and service `zonewiki`): `env_file: .env` passes every setting,
  `environment` adds `ZONE_DATA=/wiki-data` (overriding `.env`'s),
  `ZONE_HOST_DATA` (the host path, for the MCP tools' `file:` lines) and `TZ`;
  the port is `${ZONE_BIND:-127.0.0.1}:5000:5000`;
  the wiki is a bind mount with `create_host_path: false` (a mistyped path errors instead of starting an empty wiki) and `selinux: Z`;
  `init: true` (a minimal init as PID 1 reaps orphaned processes; with Zone as PID 1 they piled up as zombies, see Version history in `docs/spec/rendering.md`);
  `restart: unless-stopped`;
  `user: ${ZONE_UID:-1000}:${ZONE_GID:-1000}` (not root: Zone writes the wiki as the host user);
  a healthcheck,
  `/app/zonewiki health`,
  which fetches `/api/health` from localhost (port 5000;
  `zonewiki health :8080` for a Zone started with `--addr :8080`, `zonewiki health unix:PATH` for one on a socket) (it fails while the settings file has an error Zone didn't take, see Reloading in `docs/spec/users.md`;
  the image has no curl or Python;
  reported by `compose ps`;
  restarting an unhealthy container is podman's `--health-on-failure` or an autoheal container's job);
  log driver `json-file` (Docker's default, podman's alias for k8s-file, so `logs -f` works on both).
  It works on its own (`docker compose up -d --build`), with `unknown` as the build and UTC unless `.env` sets `TZ`.
  With docker compose the settings reach the container through the API; podman-compose turns `env_file` into `podman create -e KEY=value` arguments, so there the secrets are on a command line for that moment.
- **`compose.nas.yaml`** — for a NAS or server without the repo: runs the prebuilt image `ghcr.io/zonewiki/zonewiki:latest` (amd64 and arm64, built by GitHub's CI, see CI) instead of building it, so the machine needs only Docker with Compose.
  Unlike `compose.yaml`: no `build:`;
  `ZONE_DATA`, `ZONE_UID` and `ZONE_GID` are required (`${…:?message}`: compose stops with the message instead of guessing; a wrong UID would only show later as files Zone can't write);
  the port is `${ZONE_PORT:-5080}:5000` on every interface (not 5000 on the host: Synology's DSM uses it);
  no `selinux: Z` (NAS systems have no SELinux, and older compose versions reject the option);
  the log is capped (`max-size` 10m, 3 files).
  The rest as in `compose.yaml` (`init`, `restart`, healthcheck, `create_host_path: false`).
  See On a NAS or server.
- **`run.sh`** — the entry point, driving `<engine> compose` (podman if installed, else docker; a compose provider must be installed) for build/up/down/logs.
  It works out and exports what compose interpolates: `ZONE_DATA` from `.env` with `~` expanded (default `~/zonewiki`),
  `ZONE_BIND` from `ZONE_ALLOW_REMOTE` (`0.0.0.0`, else `127.0.0.1`),
  `ZONE_BUILD` from git (see Version),
  and `TZ` (`.env`'s, else the host's from `/etc/timezone` or the `/etc/localtime` link, else UTC),
  so "today" on the server (due count, tasks dates, note names, "Last changed") is the user's day.
  `ZONE_UID`/`ZONE_GID`: `id -u`/`id -g`;
  with rootless podman it adds `compose.podman.yaml` (`userns_mode: keep-id`: the host user keeps its UID inside, so Zone runs as you and the wiki's files stay yours);
  with rootless docker, which has no keep-id, 0 (the container's root is the host user there; another UID would be a subordinate one).
  `up` refuses a missing wiki folder or `.env`, then `compose up -d --build`.
  `test`/`e2e` use the engine directly (no `.env` needed), in the tools image.
  Platforms: Linux is the only tested one; meant to work also on macOS and, on Windows, in WSL2 (Docker Desktop and Podman run on it there).
  It sticks to bash 3.2 (macOS's stock bash: no `${x,,}`, no `declare -A`, no `mapfile`) and to options BSD tools share with GNU ones.
  `.gitattributes` keeps `*.sh` and `.githooks/*` at LF line endings, so a Windows checkout with `core.autocrlf` doesn't break them.
  Without the container, Zone itself should run on any OS Go builds for, with git (Without a container, below), though only Linux is tested.
- **`.env.example`** — the template for `.env` (`cp .env.example .env`): every setting with a comment, optional ones commented out, no real values.
  `tests/test_env_example.py` keeps it in step with the code: every `ZONE_*` name the source, `run.sh`, `compose.yaml` or `compose.nas.yaml` reads is in it, and nothing else is (a typo in `.env` is silently ignored).
- **`.env`** — the deployment's settings, read by `run.sh`, compose and Zone at start: `ZONE_DATA` (default `~/zonewiki`), `ZONE_ALLOW_REMOTE`, `TZ`, `ZONE_SEMANTIC_SEARCH`, `ZONE_PUBLIC` (`view` or `edit`: the wiki is shown, or open to edits, without signing in; Public wikis in `docs/spec/security.md`), sign-up and its mail server (`ZONE_SIGNUP`, `ZONE_SMTP_*`, `ZONE_MAIL_FROM`; Mail, below), and for compose without `run.sh` `ZONE_BIND`, `ZONE_UID`/`ZONE_GID`, `ZONE_PORT` (`compose.nas.yaml`); gitignored.
  With rootless podman, keep-id (`compose.podman.yaml`) runs Zone as the host user, so files written to the mounted wiki directory are the host user's.
- **`.zone/config`** — the wiki's own settings and its users (a password, MCP tokens, `hosts`, `base-url`, `git-remote`, `noshare`, `locale`, `date-format`, `query-log`, `semantic-min-score`, `trusted-proxies`, `upload-max`, `storage-max`, `external-content`, `no-login`, `mcp-oauth`, `signup-domains`, `signup-role`, `signup-approval`),
  in the wiki,
  kept out of git (it holds the users: back it up with the data folder),
  reloaded when it changes,
  edited by hand or with `./run.sh user …`/`./run.sh group …`/`./run.sh config …` (The settings file in `docs/spec/users.md`).

## Commands

```bash
./run.sh            # build image and start Zone (default: "up")
./run.sh build      # build the container image only (compose build)
./run.sh up         # build and start in detached mode (compose up -d --build)
./run.sh down       # stop and remove the container (compose down)
./run.sh logs       # follow container logs
./run.sh test       # gofmt, go vet, go test -race, then the unit/API tests, in the tools image (extra args go to pytest; a tests/… path replaces the default whole suite but the browser tests)
./run.sh e2e        # run browser tests + tasks-core.js (Node) tests in the tools image (extra args go to pytest)
./run.sh bench      # the tasks at a team's size against their budgets (tests/bench_tasks.py), then once more against a server built with the race detector; ZONE_BENCH_SLACK=3 allows three times the time budgets (The bench in docs/spec/testing.md)
./run.sh retrieval  # search on the sample wiki (tests/retrieval) against its floor, by keywords and by meaning, in the app's image with the model
./run.sh leaks      # scan the git history for secrets with gitleaks, in the tools image (extra args go to `gitleaks git`; a leak exits with 3)
./run.sh screenshots  # redo the README's screenshots (docs/screenshots/*.png) from a sample wiki, in the tools image; compressed with pngquant (the README shows phone.png at 640px)
./run.sh eval [FILE]  # measure retrieval on your wiki (see Measuring retrieval in search.md), in the app image, with semantic search as ZONE_SEMANTIC_SEARCH says
./run.sh user add|passwd|token|role|group|disable|enable|invite|reset|remove NAME, user list  # the users in the wiki's settings file, zonewiki user in a one-off container (compose run; see users.md)
./run.sh group includes NAME [+G] [-G], group owners NAME [+U] [-U], group list  # groups made of groups and a group's owners, in the wiki's settings file (zonewiki group)
./run.sh config check|set|unset|passwd|token  # the wiki's settings file: check it, set a key, the password and MCP token without users (zonewiki config)
```

Zone is available at **http://localhost:5000** after `./run.sh up`.
To update: `git pull && ./run.sh up`.
To point Zone at an existing folder of Markdown notes, set `ZONE_DATA` to it: Zone makes it a git repository (if it isn't one) and adds a home page.

## Without run.sh

`run.sh` fills in what `compose.yaml` can't work out itself: the commit the image is built from, the timezone, the user ID and the address from `ZONE_ALLOW_REMOTE`.
To run `docker compose up -d --build` directly, set in `.env`:
`ZONE_DATA` as an absolute path (no `~`);
`ZONE_BIND=0.0.0.0` together with `ZONE_ALLOW_REMOTE=1`, for network access;
`ZONE_UID` and `ZONE_GID` (`id -u`, `id -g`) if they aren't 1000;
`TZ`, or the container runs on UTC.
With rootless Podman, add the file that keeps your user ID inside the container: `podman compose -f compose.yaml -f compose.podman.yaml up -d --build`.

## Without a container

With Go 1.27 or newer, a C compiler (for semantic search; `CGO_ENABLED=0` builds without it) and git:

```bash
(cd go && go build -o zonewiki .)
ZONE_DATA=~/zonewiki go/zonewiki
```

In Windows PowerShell: `cd go; go build -o zonewiki.exe .; cd ..`, then `$env:ZONE_DATA="$HOME\zonewiki"; go\zonewiki.exe`.
For semantic search, onnxruntime's library must be where the loader finds it (`docs/spec/search.md`).
`zonewiki --help` lists the flags, the commands (`user`, `group`, `config`, `eval`, `health`; each takes `--help` too) and the environment.

## On a NAS or server

Any machine with Docker and Compose (a NAS, a Raspberry Pi, a small server) can run Zone from the prebuilt image, without the repo, git or a build: `compose.nas.yaml` and a `.env` in one folder, then `docker compose -f compose.nas.yaml up -d`.
The image is `ghcr.io/zonewiki/zonewiki`, for amd64 and arm64 (Docker picks the right one): `latest` follows `main`, a version tag (`1.2.0`, `1.2`) stays put, a short commit hash names one build.
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

The login needs no command: opened in a browser, a wiki without one asks for a password first (First run in `docs/spec/security.md`; `compose.nas.yaml` tells Zone its port is on the LAN, `ZONE_BIND: 0.0.0.0`), and shows nothing before.
The commands above set it just as well.

`hosts` are the names and addresses you open Zone by (the Host check, Security in `docs/spec/security.md`); without them only localhost gets in.
`ZONE_ALLOW_REMOTE=1` in `.env` instead skips the check: quicker, but then any name reaches Zone, which DNS rebinding can exploit.
Until a login is set Zone shows only its first-run page (`no-login = yes` in the settings file turns that off, for a wiki open on purpose); `config token` (or `user token NAME`) makes a token for AI clients.

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
5. **HTTPS** (optional): Control Panel → Login Portal → Advanced → Reverse Proxy: source `HTTPS`, your host name, port 443; destination `HTTP`, `localhost`, port 5080. Under Custom Header, add `X-Forwarded-Proto` = `$scheme` if DSM doesn't send it.
   Add the host name to `hosts` (`config set hosts …`), and a certificate for it (Control Panel → Security → Certificate).
   The proxy reaches the container through Docker's network, so Zone sees it as the network's gateway (`172.17.0.1` or another in `172.16.0.0/12`);
   Zone's log names it on the first proxied request ("requests from … carry X-Forwarded-For"), and `config set trusted-proxies <that address>` lets Zone tell the proxy's clients apart when it throttles wrong passwords (without it they count as one).

Updates: over SSH, `docker compose pull && docker compose up -d` in the project folder (Container Manager's Image list may also offer the update for `ghcr.io/zonewiki/zonewiki`).

### Semantic search paused

Since 2026-11-18 the images CI publishes have no semantic search (`ZONE_SEMANTIC_SEARCH=off` at build time), and CI's **retrieval** job is skipped (`if: false`),
because Microsoft took onnxruntime's GitHub repository private "while we investigate malicious content", and both would run onnxruntime.
Its release downloads are gone with it, so a build with `ZONE_SEMANTIC_SEARCH=on` fails where its layer isn't cached.
They come back, as they were, once Microsoft has said what happened and onnxruntime has a source that can be trusted again.

## CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on a push to `main` or a `v*` tag, on every pull request and when started by hand (`workflow_dispatch`; a newer run of the same branch or tag cancels the one before) a **checks** job per `run.sh` command,
as on your machine: **test** (gofmt, go vet, go test, the unit/API tests),
**e2e** (browser and Node tasks-core tests) and **leaks** (gitleaks over every commit; the checkout fetches the full history),
all in the tools image (`Containerfile.tools`).
So CI keeps no package lists or test commands of its own.
A **bench** job after them runs `./run.sh bench` with `ZONE_BENCH_SLACK=3` (The bench in `docs/spec/testing.md`); the image job doesn't wait for it, so a slow runner fails a job that is looked at and holds up no image.
It waits for the checks because the jobs share the host's engine: beside test's worker per CPU and e2e's browsers, the bench's clients at once measured the CPU they waited for (a page beside thirty searches seven times slower than alone, where the slack allows three).
GitHub's workflow has the same job, beside the checks, since each of its jobs has a machine of its own.
A **retrieval** job, likewise after the checks, runs `./run.sh retrieval` (skipped for now: Semantic search paused): search's quality on the sample wiki against its floor (Measuring retrieval in `docs/spec/search.md`). It builds the app's image with the model, under a tag of its own (`zonewiki-retrieval`), from the engine's cache unless the code or the model changed.
`run.sh` builds each image quietly (the build's output, which podman's `-q` doesn't hide for `RUN` steps, goes to a log shown only when the build fails; a first build, with no image of that tag yet, prints one line saying so) and runs it by ID, so parallel jobs on one engine can't swap a shared tag.
With `ZONE_COPY_REPO=1` (set by CI) it copies the repo into the container (`create --rm`, `cp`, `start -a`; the engine removes the container even when a cancelled job kills `run.sh`) instead of bind-mounting it, since the checkout lives in the job container while the engine is the host's;
nothing the command writes comes back.
Only **leaks** fetches the full history; the others check out the last commit.
On a run that isn't a pull request's (a push to `main` or a `v*` tag, or one started by hand),
once all three pass,
**image** builds the `Containerfile` with `ZONE_SEMANTIC_SEARCH=off` (on until 2026-11-18: Semantic search paused) and the `ZONE_BUILD` string `run.sh` would compute,
and pushes it to the Forgejo registry as `<REGISTRY>/<owner>/<repo>:<short-sha>` (the repository's name, lowercase;
repo variable `REGISTRY`, Forgejo's public host, e.g. `git.example.org`: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`;
without the variable the image job is skipped, so forks just test),
plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`).
All jobs run in the official `docker:cli` image (plus `nodejs`,
`git`,
`bash` from apk) and build against the host engine (the checks through `run.sh`,
the image job with plain `docker build -f Containerfile`/`docker push`) through the socket mounted into job containers (runner label `docker`,
runner config `container.docker_host: automount`;
the host's engine trusts the internal certificate via `/etc/docker/certs.d/<REGISTRY>/ca.crt`, which the runner also mounts read-only into job containers via `container.options`;
checkout goes over the runner's internal `http://web:3000`, so no job needs a `ca-certificates` package),
so layers are cached between runs.
Repo settings: variable `REGISTRY` (the registry host; required for the image), secret `REGISTRY_TOKEN` (token with package write scope), optional variable `REGISTRY_USER` (default: the repo owner).

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs the same **checks** (`./run.sh test`, `e2e`, `leaks`), on the same events, on GitHub's `ubuntu-26.04` runners, through `run.sh` (with podman, which the runners have and `run.sh` prefers; the checkout is on the runner, so no `ZONE_COPY_REPO`).
On a run that isn't a pull request's,
once they pass,
it publishes the image `compose.nas.yaml` runs: **image** builds the `Containerfile` (`ZONE_SEMANTIC_SEARCH=off`, the `ZONE_BUILD` string) once per architecture,
each natively on its own runner (`ubuntu-26.04` for amd64, `ubuntu-26.04-arm` for arm64; no emulation, so the arm64 build compiles with cgo, and, with semantic search, fetches onnxruntime's arm64 build and the model as fast as the other),
pushes it to `ghcr.io/<owner>/zonewiki` by digest only (cached between runs in GitHub's cache, a scope per architecture),
and **manifest** joins the two digests under the tags: `latest` (main),
the short commit hash,
and on a tag `v1.2.0` also `1.2.0` and `1.2`.
It logs in with the workflow's own `GITHUB_TOKEN` (`packages: write`), so the repo needs no secrets.
The image carries `org.opencontainers.image.source`, which links the package to the repo.
A new package on ghcr.io is private: make it public once in its settings on GitHub, so a NAS pulls without logging in.

## Network access

`ZONE_ALLOW_REMOTE` is the single knob for network access (with `run.sh`).
When unset, `run.sh` exports `ZONE_BIND=127.0.0.1`, so the container's port 5000 is only exposed on the host's loopback interface, and the host-header check rejects any `Host` header but localhost and the settings file's `hosts`.
When `ZONE_ALLOW_REMOTE=1`, `run.sh` exports `ZONE_BIND=0.0.0.0` (LAN-accessible port binding) and the host-header check is skipped unless `hosts` is set.
`compose.yaml` maps `${ZONE_BIND:-127.0.0.1}:5000:5000`; used without `run.sh`, `ZONE_BIND=0.0.0.0` must be set in `.env` alongside `ZONE_ALLOW_REMOTE=1`.

### A Unix socket

`zonewiki --addr unix:PATH` listens on a Unix socket at PATH and on no port: for a reverse proxy on the same machine, when Zone runs from the binary or in a container with the socket's folder mounted and no network of its own.
Caddy reaches it with `reverse_proxy unix//run/zone/zone.sock`, nginx with `proxy_pass http://unix:/run/zone/zone.sock:;`.
Who may connect is who may reach the path: the socket itself is open to every local user (mode 0666, as a port on localhost is), so the folder it is in decides, and the proxy's user must be able to enter that folder.
Zone replaces a socket file that a Zone that was killed left behind; it doesn't start when something still listens there, or when the path is a file of another kind (`listen` in `go/server.go`).
Whoever connects has no address and is taken for the proxy: its `X-Forwarded-For` names the client, whatever `trusted-proxies` holds (the throttle in `docs/spec/security.md`).
`hosts` and the rest of a proxy's setup stay as with a port.
The healthcheck over it: `zonewiki health unix:PATH`.

### Started by systemd, asleep when idle

`zonewiki --addr fd:3` serves on a listening socket Zone was started with, the way systemd hands one to a service it starts for a socket unit (socket activation; `fd:3` is the first such socket, and Podman passes it on into a container).
systemd holds the socket and starts Zone when a connection comes:

```
# zone.socket                          # zone.service
[Socket]                               [Service]
ListenStream=/run/zone/zone.sock       Environment=ZONE_DATA=/srv/wiki ZONE_IDLE_EXIT=15m
                                       ExecStart=/usr/local/bin/zonewiki --addr fd:3
```

With `ZONE_IDLE_EXIT` (a duration: `15m`, `1h`; unset or `off`: never) Zone exits, with exit code 0, once no request came for that long (`exitWhenIdle` in `go/server.go`), and uses no memory until the next one starts it again: that one waits in the socket for the start, a fraction of a second, since the search index is in its files.
What counts as a request is everything but `/api/health`, so whatever watches Zone doesn't keep it up; a browser tab nobody is at asks nothing either (`whileUsed`, Modules in `docs/spec/navigation.md`).
Zone stays up while a request runs, and while a throttle remembers a wrong password or token (for a day after a client's last wrong try, until its right one): an exit would forget the count and give a guesser five new tries each time.
It also stays up while it packs the repository (Zone packs the repository itself, in `docs/spec/rendering.md`): a join of the packs stopped halfway is done again from the start.
What an exit loses is what a restart loses: the caches, which are built again, and the texts an editor's save is merged from, so a tab left open over a sleep saves as after any restart (plainly if the page is as it was; with a conflict to settle if it changed meanwhile; Several editors in `docs/spec/editor.md`).
Who wrote a draft that isn't committed yet is only in memory, so before the exit each draft is committed in its author's name (`commitDrafts` in `go/merge.go`).
`ZONE_IDLE_EXIT` on a socket or port Zone opened itself is ignored, with a line in the log, since nothing would start Zone again.
A Unix socket handed over is treated as one Zone opened (`--addr unix:PATH`, above): its peer is the proxy; the file is the holder's and stays when Zone exits.

Optional git remote: `./run.sh config set git-remote git@github.com:user/wiki.git`.
At start and when it changes, `setGitRemote()` adds or updates `origin` to that URL, so `git push` works from the terminal.

## Mail

Zone sends mail for sign-up (Sign-up in `docs/spec/users.md`) and to tell a user that someone gave them a task (Assigning tasks there; each user can turn that off), through an SMTP server the deployment names in `.env` (`go/mail.go`, Go's `net/smtp`; no dependency):

```bash
ZONE_SIGNUP=1
ZONE_SMTP_HOST=smtp.example.org
ZONE_SMTP_PORT=587                 # default 587; 465 with ZONE_SMTP_TLS=tls
ZONE_SMTP_USER=wiki@example.org    # optional: AUTH PLAIN
ZONE_SMTP_PASSWORD=…
ZONE_SMTP_TLS=starttls             # starttls (default), tls or none
ZONE_MAIL_FROM=wiki@example.org
```

- **Encrypted** unless `ZONE_SMTP_TLS=none`: `starttls` (the default) refuses a server that doesn't offer STARTTLS, rather than sending the link in the clear; `tls` speaks TLS from the start (the default for port 465).
  The certificate is checked against `ZONE_SMTP_HOST`.
  `none` is for a relay that takes mail unencrypted (a local postfix); the password then goes only to this machine (`net/smtp`'s `PlainAuth` refuses otherwise).
- **Checked at start**: Zone won't start on a mail setting without `ZONE_SMTP_HOST`, a port or TLS mode it doesn't know, a password without a user, a missing or invalid `ZONE_MAIL_FROM` (a bare address, no name), or `ZONE_SIGNUP` without a mail server.
- **Time limits**: 20 seconds to connect, a minute for the whole mail; the sign-up form waits for it, so it can say when the mail couldn't be sent (and logs why). The mail about an assigned task is sent in the background: a task write never waits for the mail server, and a failure is only logged.
- The password is a secret in `.env` (gitignored), as compose hands every setting over (`env_file`).

## Mobile / phone access

Set `ZONE_ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces — `run.sh` then maps `0.0.0.0:5000` and the server's host-header check is skipped.
Phones on the same network reach Zone at `http://<host-LAN-IP>:5000`.
The UI is responsive down to phone widths and the editor works on mobile.
The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one.
Without it, full-screen views (the tasks edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard. iOS Safari ignores the setting.
A wiki other devices can reach and that has no login yet answers only `/setup`, where the first login is chosen, unless `no-login = yes` says it is open on purpose (First run in `docs/spec/security.md`, `go/setup.go`); a password (`./run.sh config passwd`) or users (`./run.sh user add NAME`) set from the terminal do the same.

## Version

The shortcuts modal (`?`) also links to Zone's source (`sourceURL` in `go/config.go`) and names its license, AGPL-3.0-or-later: whoever runs a changed Zone for others over a network has to offer them its source, so a fork points that constant at its own copy.

Zone's version is a semver constant, `version` in `go/config.go`, bumped by hand.
Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `go/` or `public/` had uncommitted changes (a commit can't contain its own hash, so this part can't be a committed constant).
The shortcuts modal (`?`) shows both (`Zone 1.0.0 · c28ec9e 2026-09-26 19:22`),
every page's bottom-left corner the version and short hash (`Zone 1.0.0 · c28ec9e`, `+dirty` when so, the full build on hover; `.zone-build` in `layout.html`, faint, only from 1140px wide where the margin beside the text column has room, not in the editor),
the `<meta name="generator">` tag the version,
and `/api/version` returns `{ version, build }`.
The image has no `.git`, so `run.sh` computes the build string and passes it as the `ZONE_BUILD` build arg (the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer).
Without the container, the binary has the same string from what `go build` recorded of the checkout (`sourceBuild()`: the commit, its time, whether the tree had changes); `$ZONE_BUILD` wins when set, and with neither the modal shows the version alone.

## Home-screen app

`public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `templates/layout.html`) give "Add to home screen" a proper Zone name and icon.
Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie.
The PNGs are rendered once from `public/icon.svg` (full-bleed green square, a white Z inside the central 80% "maskable" safe zone) and committed, so there is still no build step.
To change the icon, edit the SVG and re-render the PNGs, e.g. with a screenshot in the Playwright image (`zonewiki-tools`).
Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab. iOS Safari is more lenient.

## Data directory

Set `ZONE_DATA` in `.env` to any host path (default: `~/zonewiki`).
`run.sh` expands a leading `~` to your home directory before starting the container.
The folder must exist: neither `run.sh up` nor Zone creates it (`loadConfig` exits with "no wiki at …"), so a mistyped path can't start an empty wiki somewhere else.
Started without the container, Zone needs `ZONE_DATA` set (there is no default; unset or empty, it exits with "ZONE_DATA isn't set"), and the folder must exist too.
In an existing wiki folder Zone creates what it needs (`_assets/`, `_tasks/`, `index.md`):

```
ZONE_DATA=~/zonewiki    # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`.
Layout:
- `$ZONE_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$ZONE_DATA/private/<user>/` — with users, each user's private pages (`docs/spec/users.md`); without users an ordinary folder
- `$ZONE_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically); a group's page's in `groups/<group>/_assets/`, a private page's in `private/<user>/_assets/`, a task's (with users) in `_tasks/files/<task id>/_assets/`
- `$ZONE_DATA/_tasks/` — the tasks (`tasks.json`, `closed.json`, `closed-YYYY-MM.json`, each list's in `lists/<name>/`, and `lists.json`, the lists' registry, with the settings of All tasks; see Storage in `docs/spec/tasks.md`); each user's private tasks in `private/<user>/_tasks/`
- `$ZONE_DATA/.git/` — the wiki's history: Zone makes the folder a git repository at its first start (`gitSetup`) and commits every change made through it
- `$ZONE_DATA/.zone/` — Zone's own state, none of it in git (`.git/info/exclude` has `/.zone/*`, `excludeState`; each file's name is `statePath(…)` in the code):
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
  - `tasks-checked`, `views-named` — marks that a start has checked the task files, and rewritten the views' old names in the pages, so later starts skip it

Uploaded image URLs use `/_assets/<filename>` (served by `serveUpload` from `cfg.Uploads`), not the static file path.

### Backups

**Back up the whole data directory** (`ZONE_DATA`): it is the wiki, and there is nothing of it anywhere else.
The simplest backup is a copy of that folder taken while Zone is stopped (`./run.sh down`), or a snapshot of the file system it is on (a NAS's snapshots, ZFS, btrfs).
Zone writes each file whole (a reader never sees half of one), but a copy taken file by file while Zone runs can catch a git commit or a task write half done across files.

- **What git holds**: the pages, the uploads (`_assets/` and the private and groups' ones), the tasks and their lists (`_tasks/`, `private/<user>/_tasks/`), each with its history.
  With `git-remote` set, `git -C "$ZONE_DATA" push origin` copies that elsewhere (Zone doesn't push by itself); a change made on disk and not committed yet is in no commit.
- **What git doesn't hold, and a clone of the remote lacks**: `.zone/`, above all
  `config` (the users, their roles, groups, mail addresses and password hashes, the MCP tokens' hashes and every setting: without it a clone of a wiki with users won't start, `missingUsers`),
  `secret_key` (without it everyone signs in again),
  `grants` (without it every assistant connected through OAuth connects again),
  `links` (without it the open invite and reset links stop working)
  and `signed-out` (without it a copy of a cookie signed out works again, until it expires).
  Back these up with the folder; where the git remote is the only copy, keep a copy of `.zone/` beside it.
  They hold password and token hashes and keys: keep the backup as confidential as the wiki (Before you open it up in `docs/spec/security.md`).
- **What can be rebuilt, and needn't be backed up**: `.zone/index/` (Zone builds the search indexes again when they are first needed, which takes a while on a big wiki, and with semantic search embeds every section and task again in the background, which takes longer), `.zone/tmp/`, the lock files and the marks `tasks-checked` and `views-named` (a start without them looks the files through again and changes nothing that is right).
  Lost, `seen/` shows each user the tasks' news again, `drafts/` loses only a text left in a conflict, and `queries.jsonl` is a log.
- **Restoring**: put the folder back where `ZONE_DATA` points and start Zone; the files must belong to the user Zone runs as, and `.zone/config` and `secret_key` should stay readable by it alone (0600: Zone warns in its log when others may read the settings file).

## How big a wiki can be

Zone keeps no index of the wiki's pages or tasks in memory beyond per-page caches: a page view walks the wiki's folders and looks at every page's links (cached by the file's mtime, so one `stat` a page) for its backlinks,
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

- `.env`: `LEAF_…` variables are `ZONE_…`; the wiki's settings (password, users, hosts …) are in `.zone/config`, not there.
- Pages: `[[deck …]]` is `[[tasks: …]]` (in the wiki's folder: `grep -rlZi '\[\[deck' --include='*.md' . | xargs -0 sed -i -E 's/\[\[deck\]\]/[[tasks:]]/Ig; s/\[\[deck[[:space:]]+/[[tasks: /Ig'`, then commit), and `home=1` in one is `pin=1`.
- Addresses: `/deck`, `/api/deck/…`, `/api/scratch` and `/page/_scratch` are `/tasks`, `/api/tasks/…`, `/api/notes` and `/notes`; the keys `g s`, `g d` and `c s` are `g n`, `g a` and `c n`.
- MCP: the tools `leaf_help` and `file_scratch_note` are `zone_help` and `file_note`, and their `scratch` argument is `note` (write_page) or `notes` (search_wiki, list_pages).
- The image is `ghcr.io/zonewiki/zonewiki`, its binary `/app/zonewiki`.
