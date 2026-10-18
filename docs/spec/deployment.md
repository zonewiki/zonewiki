# Deployment

Part of Leaf's spec (see `AGENTS.md` for the index).

## Files

- **`Containerfile`** — Ubuntu 26.04 LTS base, installs Python + pip + git + `tzdata` via `apt`, installs `requirements.txt` (exact versions, the transitive ones too), with `LEAF_SEMANTIC_SEARCH=on` also `requirements-embed.txt` (onnxruntime, the tokenizer and their packages, pinned the same way) and the model (`embed_setup.py`), copies the `src/` directory into `/app` and `README.md` plus `docs/spec/` next to it (Leaf's documentation, for the MCP tool `leaf_help`), sets `HOME=/tmp` (Leaf may run as a UID without an account). Build args `LEAF_SEMANTIC_SEARCH` and `LEAF_BUILD` (last layer).
- **`compose.yaml`** — builds the image (`build:` with those args) and runs the `leaf` container: `env_file: .env` passes every setting, `environment` adds `LEAF_DATA=/wiki-data` (overriding `.env`'s), `LEAF_HOST_DATA` (the host path, for the MCP tools' `file:` lines) and `TZ`; the port is `${LEAF_BIND:-127.0.0.1}:5000:5000`; the wiki is a bind mount with `create_host_path: false` (a mistyped path errors instead of starting an empty wiki) and `selinux: Z`; `init: true` (a minimal init as PID 1 reaps orphaned processes; with Leaf as PID 1 they piled up as zombies, see Version history in `docs/spec/rendering.md`); `restart: unless-stopped`; `user: ${LEAF_UID:-1000}:${LEAF_GID:-1000}` (not root: Leaf writes the wiki as the host user); a healthcheck fetching `/public/manifest.json` (reported by `compose ps`; restarting an unhealthy container is podman's `--health-on-failure` or an autoheal container's job); log driver `json-file` (Docker's default, podman's alias for k8s-file, so `logs -f` works on both). It works on its own (`docker compose up -d --build`), with `unknown` as the build and UTC unless `.env` sets `TZ`. With docker compose the settings reach the container through the API; podman-compose turns `env_file` into `podman create -e KEY=value` arguments, so there the secrets are on a command line for that moment.
- **`compose.nas.yaml`** — for a NAS or server without the repo: runs the prebuilt image `ghcr.io/gklingler/leaf:latest` (amd64 and arm64, built by GitHub's CI, see CI) instead of building it, so the machine needs only Docker with Compose. Unlike `compose.yaml`: no `build:`; `LEAF_DATA`, `LEAF_UID` and `LEAF_GID` are required (`${…:?message}`: compose stops with the message instead of guessing; a wrong UID would only show later as files Leaf can't write); the port is `${LEAF_PORT:-5080}:5000` on every interface (not 5000 on the host: Synology's DSM uses it); no `selinux: Z` (NAS systems have no SELinux, and older compose versions reject the option); the log is capped (`max-size` 10m, 3 files). The rest as in `compose.yaml` (`init`, `restart`, healthcheck, `create_host_path: false`). See On a NAS or server.
- **`run.sh`** — the entry point, driving `<engine> compose` (podman if installed, else docker; a compose provider must be installed) for build/up/down/logs. It works out and exports what compose interpolates: `LEAF_DATA` from `.env` with `~` expanded (default `~/leaf`), `LEAF_BIND` from `LEAF_ALLOW_REMOTE` (`0.0.0.0`, else `127.0.0.1`), `LEAF_BUILD` from git (see Version), and `TZ` (`.env`'s, else the host's from `/etc/timezone` or the `/etc/localtime` link, else UTC), so "today" on the server (due count, deck dates, scratch-note names, "Last changed") is the user's day. `LEAF_UID`/`LEAF_GID`: `id -u`/`id -g`; with rootless podman it adds `compose.podman.yaml` (`userns_mode: keep-id`: the host user keeps its UID inside, so Leaf runs as you and the wiki's files stay yours); with rootless docker, which has no keep-id, 0 (the container's root is the host user there; another UID would be a subordinate one). `up` refuses a missing wiki folder or `.env`, removes a `leaf` container an older run.sh started without compose (it would block the name), then `compose up -d --build`. `test`/`e2e` use the engine directly (no `.env` needed). Platforms: Linux is the only tested one; meant to work also on macOS and, on Windows, in WSL2 (Docker Desktop and Podman run on it there). It sticks to bash 3.2 (macOS's stock bash: no `${x,,}`, no `declare -A`, no `mapfile`) and to options BSD tools share with GNU ones. `.gitattributes` keeps `*.sh` and `.githooks/*` at LF line endings, so a Windows checkout with `core.autocrlf` doesn't break them. Without the container, Leaf itself should run on any OS with Python and git (it uses no Unix-only modules), though only Linux is tested.
- **`.env.example`** — the template for `.env` (`cp .env.example .env`): every setting with a comment, optional ones commented out, no real values. `tests/test_env_example.py` keeps it in step with the code: every `LEAF_*` name the source, `run.sh`, `compose.yaml` or `compose.nas.yaml` reads is in it, and nothing else is (a typo in `.env` is silently ignored).
- **`.env`** — every setting (`LEAF_DATA`, default `~/leaf`, plus the optional `LEAF_PASSWORD`, `LEAF_SECRET_KEY`, `LEAF_GIT_REMOTE`, `LEAF_ALLOW_REMOTE`, `LEAF_HOSTS`, `TZ`, `LEAF_SEMANTIC_SEARCH`, `LEAF_SEMANTIC_MIN_SCORE`, `LEAF_QUERY_LOG`, `LEAF_MCP_TOKEN`, `LEAF_BASE_URL`, `LEAF_NOSHARE`, `LEAF_LOCALE`, `LEAF_DATE_FORMAT`); gitignored. With rootless podman, keep-id (`compose.podman.yaml`) runs Leaf as the host user, so files written to the mounted wiki directory are the host user's.

## Commands

```bash
./run.sh            # build image and start Leaf (default: "up")
./run.sh build      # build the container image only (compose build)
./run.sh up         # build and start in detached mode (compose up -d --build)
./run.sh down       # stop and remove the container (compose down)
./run.sh logs       # follow container logs
./run.sh test       # run unit/API tests inside the app image (extra args go to pytest; a tests/… path replaces the default whole suite)
./run.sh e2e        # run browser tests + deck-core.js (Node) tests in the tools image (extra args go to pytest)
./run.sh leaks      # scan the git history for secrets with gitleaks, in the tools image (extra args go to `gitleaks git`; a leak exits with 3)
./run.sh screenshots  # redo the README's screenshots (docs/screenshots/*.png) from a sample wiki, in the tools image; compressed with pngquant (the README shows phone.png at 640px)
./run.sh eval [FILE]  # measure retrieval on your wiki (see Measuring retrieval in search.md), in the app image, with semantic search as LEAF_SEMANTIC_SEARCH says
```

Leaf is available at **http://localhost:5000** after `./run.sh up`.

## On a NAS or server

Any machine with Docker and Compose (a NAS, a Raspberry Pi, a small server) can run Leaf from the prebuilt image, without the repo, git or a build: `compose.nas.yaml` and a `.env` in one folder, then `docker compose -f compose.nas.yaml up -d`. The image is `ghcr.io/gklingler/leaf`, for amd64 and arm64 (Docker picks the right one): `latest` follows `main`, a version tag (`1.2.0`, `1.2`) stays put, a short commit hash names one build. To update: `docker compose -f compose.nas.yaml pull`, then `up -d` again. The wiki is untouched by an update; it lives in the folder `LEAF_DATA` names.

`.env` needs (`.env.example` has them all):

- `LEAF_DATA`: the wiki folder, as an absolute path; it must exist (create it first, empty or with Markdown files in it).
- `LEAF_UID`, `LEAF_GID`: the owner of that folder (`id -u` and `id -g` as that user, or `ls -nd <folder>`). Leaf runs as that user and writes the wiki as it.
- `LEAF_HOSTS`: the names and addresses you open Leaf by, e.g. `LEAF_HOSTS=nas.local,192.168.1.20,wiki.example.org` (the Host check, Security in `docs/spec/security.md`). `LEAF_ALLOW_REMOTE=1` instead skips the check: quicker, but then any name reaches Leaf, which DNS rebinding can exploit.
- `TZ`, e.g. `Europe/Vienna`, so "today" is your day (else UTC).
- Optional: `LEAF_PORT` (the host's port, default 5080), `LEAF_PASSWORD` (a login gate; recommended as soon as more than you can reach the NAS), `LEAF_MCP_TOKEN` (AI clients), `LEAF_SEMANTIC_SEARCH=on` (semantic search: the model is in the image; plan on roughly 1 GB more RAM).

Leaf is then at `http://<nas>:5080`. Over the internet, put HTTPS in front (a reverse proxy, below); the session cookie is `Secure` when the proxy sends `X-Forwarded-Proto: https`.

### Synology

With Container Manager (DSM 7.2 or newer; Docker on older DSM works the same over SSH), on an x86 or ARM64 model.

1. **Folders**: in File Station, a folder for the project, e.g. `/volume1/docker/leaf`, and in it the wiki folder, e.g. `/volume1/docker/leaf/wiki`.
2. **UID and GID**: the user who owns them. DSM doesn't show IDs; with SSH on (Control Panel → Terminal & SNMP), log in as that user and run `id`. The first admin user is usually `uid=1026`, and group `users` is `gid=100`.
3. **Files**: put `compose.nas.yaml` into the project folder as `compose.yaml` (Container Manager looks for that name), and next to it a `.env` with at least `LEAF_DATA=/volume1/docker/leaf/wiki`, `LEAF_UID`, `LEAF_GID`, `LEAF_HOSTS` (the NAS's name and LAN address) and `TZ`.
4. **Project**: Container Manager → Project → Create, name `leaf`, path the project folder; it finds `compose.yaml` there. Finish the wizard with the project set to start once created; the first start pulls the image (about 1.1 GB).
5. **HTTPS** (optional): Control Panel → Login Portal → Advanced → Reverse Proxy: source `HTTPS`, your host name, port 443; destination `HTTP`, `localhost`, port 5080. Under Custom Header, add `X-Forwarded-Proto` = `$scheme` if DSM doesn't send it. Add the host name to `LEAF_HOSTS`, and a certificate for it (Control Panel → Security → Certificate).

Updates: over SSH, `docker compose pull && docker compose up -d` in the project folder (Container Manager's Image list may also offer the update for `ghcr.io/gklingler/leaf`).

## CI (Forgejo Actions)

`.forgejo/workflows/ci.yml` runs on every push and pull request a **checks** job per `run.sh` command, as on your machine: **test** (unit/API tests in the app image, `Containerfile`), **e2e** (browser and Node deck-core tests) and **leaks** (gitleaks over every commit; the checkout fetches the full history) in the tools image (`Containerfile.tools`). So CI keeps no package lists or test commands of its own. `run.sh` builds each image quietly (the build's output, which podman's `-q` doesn't hide for `RUN` steps, goes to a log shown only when the build fails; a first build, with no image of that tag yet, prints one line saying so) and runs it by ID, so parallel jobs on one engine can't swap a shared tag. With `LEAF_COPY_REPO=1` (set by CI) it copies the repo into the container (`create --rm`, `cp`, `start -a`; the engine removes the container even when a cancelled job kills `run.sh`) instead of bind-mounting it, since the checkout lives in the job container while the engine is the host's; nothing the command writes comes back. Only **leaks** fetches the full history; the others check out the last commit. On `main` and `v*` tags, once all three pass, **image** builds the `Containerfile` with `LEAF_SEMANTIC_SEARCH=on` and the `LEAF_BUILD` string `run.sh` would compute, and pushes it to the Forgejo registry as `<REGISTRY>/<owner>/leaf:<short-sha>` (repo variable `REGISTRY`, Forgejo's public host, e.g. `git.example.org`: the host's engine pushes, and the runner's server URL is the internal `http://web:3000`; without the variable the image job is skipped, so forks just test), plus `:latest` (main) or `:<version>` (tag `v1.2.3` → `1.2.3`). All jobs run in the official `docker:cli` image (plus `nodejs`, `git`, `bash` from apk) and build against the host engine (the checks through `run.sh`, the image job with plain `docker build -f Containerfile`/`docker push`) through the socket mounted into job containers (runner label `docker`, runner config `container.docker_host: automount`; the host's engine trusts the internal certificate via `/etc/docker/certs.d/<REGISTRY>/ca.crt`, which the runner also mounts read-only into job containers via `container.options`; checkout goes over the runner's internal `http://web:3000`, so no job needs a `ca-certificates` package), so layers are cached between runs. Repo settings: variable `REGISTRY` (the registry host; required for the image), secret `REGISTRY_TOKEN` (token with package write scope), optional variable `REGISTRY_USER` (default: the repo owner).

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs the same **checks** (`./run.sh test`, `e2e`, `leaks`) on GitHub's `ubuntu-26.04` runners, through `run.sh` (with podman, which the runners have and `run.sh` prefers; the checkout is on the runner, so no `LEAF_COPY_REPO`). On `main` and `v*` tags, once they pass, it publishes the image `compose.nas.yaml` runs: **image** builds the `Containerfile` (`LEAF_SEMANTIC_SEARCH=on`, the `LEAF_BUILD` string) once per architecture, each natively on its own runner (`ubuntu-26.04` for amd64, `ubuntu-26.04-arm` for arm64; no emulation, so the arm64 build installs onnxruntime and downloads its model as fast as the other), pushes it to `ghcr.io/<owner>/leaf` by digest only (cached between runs in GitHub's cache, a scope per architecture), and **manifest** joins the two digests under the tags: `latest` (main), the short commit hash, and on a tag `v1.2.0` also `1.2.0` and `1.2`. It logs in with the workflow's own `GITHUB_TOKEN` (`packages: write`), so the repo needs no secrets. The image carries `org.opencontainers.image.source`, which links the package to the repo. A new package on ghcr.io is private: make it public once in its settings on GitHub, so a NAS pulls without logging in.

## Network access

`LEAF_ALLOW_REMOTE` is the single knob for network access (with `run.sh`). When unset, `run.sh` exports `LEAF_BIND=127.0.0.1`, so the container's port 5000 is only exposed on the host's loopback interface, and the host-header check rejects any non-localhost `Host` header. When `LEAF_ALLOW_REMOTE=1`, `run.sh` exports `LEAF_BIND=0.0.0.0` (LAN-accessible port binding) and the host-header check is skipped. `compose.yaml` maps `${LEAF_BIND:-127.0.0.1}:5000:5000`; used without `run.sh`, `LEAF_BIND=0.0.0.0` must be set in `.env` alongside `LEAF_ALLOW_REMOTE=1`.

Optional git remote: set `LEAF_GIT_REMOTE` in `.env` to a remote URL (e.g. `git@github.com:user/wiki.git`). On startup, `git_setup()` adds or updates `origin` to that URL, so `git push` works from the terminal.

## Mobile / phone access

Set `LEAF_ALLOW_REMOTE=1` in `.env` (and restart with `./run.sh up`) to bind on all interfaces — `run.sh` then maps `0.0.0.0:5000` and the server's host-header check is skipped. Phones on the same network reach Leaf at `http://<host-LAN-IP>:5000`. The UI is responsive down to phone widths and the editor works on mobile. The viewport meta sets `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the layout viewport rather than only the visual one. Without it, full-screen views (the deck edit modal, the mobile editor) keep their full height and their bottom bars end up behind the keyboard. iOS Safari ignores the setting. There is no auth by default — for LAN use consider setting `LEAF_PASSWORD` in `.env` to get a login gate.

## Version

Leaf's version is a semver constant, `VERSION` in `config.py`, bumped by hand. Next to it the build is shown: the commit it was built from plus that commit's date, e.g. `c28ec9e 2026-09-26 19:22`, with ` +dirty` when `src/` had uncommitted changes (a commit can't contain its own hash, so this part can't be a committed constant). The shortcuts modal (`?`) shows both (`Leaf 1.0.0 · c28ec9e 2026-09-26 19:22`), every page's bottom-left corner the version and short hash (`Leaf 1.0.0 · c28ec9e`, `+dirty` when so, the full build on hover; `.leaf-build` in `layout.html`, faint, only from 1140px wide where the margin beside the text column has room, not in the editor), the `<meta name="generator">` tag the version, and `/api/version` returns `{ version, build }`. The image has no `.git`, so `run.sh` computes the build string and passes it as the `LEAF_BUILD` build arg (the `Containerfile` sets it as an env var in its last layer, so a new commit only rebuilds that layer). Without the container, `config.py` reads the same string from git itself (`_source_build()`); `$LEAF_BUILD` wins when set, and with neither the modal shows the version alone.

## Home-screen app

`src/public/manifest.json` plus `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` (linked from every page's `<head>` by `templates/layout.html`) give "Add to home screen" a proper Leaf name and icon. Everything under `/public/` is reachable without login, which matters because browsers may fetch these without the session cookie. The PNGs are rendered once from `src/public/icon.svg` (full-bleed green square, leaf inside the central 80% "maskable" safe zone) and committed, so there is still no build step. To change the icon, edit the SVG and re-render the PNGs, e.g. with a screenshot in the Playwright image (`leaf-e2e`). Caveat: Android only installs a standalone app over HTTPS; over plain `http://` on the LAN it adds a shortcut that opens in a browser tab. iOS Safari is more lenient.

## Data directory

Set `LEAF_DATA` in `.env` to any host path (default: `~/leaf`). `run.sh` expands a leading `~` to your home directory before starting the container. The folder must exist: neither `run.sh up` nor Leaf creates it (`config.py` exits with "no wiki at …"), so a mistyped path can't start an empty wiki somewhere else. Started without the container, Leaf needs `LEAF_DATA` set (there is no default; unset or empty, it exits with "LEAF_DATA isn't set"), and the folder must exist too. In an existing wiki folder Leaf creates what it needs (`_assets/`, `_deck/`, `index.md`):

```
LEAF_DATA=~/leaf        # or /home/user/my-wiki, /mnt/nas/wiki, etc.
```

Inside the container it is always mounted at `/wiki-data`. Layout:
- `$LEAF_DATA/*.md` — markdown files live directly in the data directory (edit on the host; changes are live immediately)
- `$LEAF_DATA/_assets/` — uploaded images, served at `/_assets/<filename>` (created automatically)
- `$LEAF_DATA/_deck/` — the deck's tasks (`tasks.json`, `completed.json`, `completed-YYYY.json`; see Storage in `docs/spec/deck.md`)
- `$LEAF_DATA/.leaf/` — Leaf's own state, not in git: `secret_key` (session key) and `index/` (the semantic search cache, rebuildable)

Uploaded image URLs use `/_assets/<filename>` (served by a Flask route from `UPLOADS_DIR`), not the static file path.
