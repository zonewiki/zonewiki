#!/usr/bin/env bash
# Run Zonewiki with compose, and run its tests. Where it is decides which Zonewiki:
# in a checkout (a Containerfile beside it) it builds the image from the code
# there (compose.build.yaml); downloaded on its own next to compose.yaml, it
# runs the prebuilt image (ghcr.io/zonewiki/zonewiki). The tests and the other
# commands for working on Zonewiki need the checkout. This script works out what
# compose can't: the build string from git, the port's bind address from
# ZONE_ALLOW_REMOTE, your user ID, the host's timezone, and the wiki path.
set -e

cd "$(dirname "$0")"

if [[ -f Containerfile ]]; then
  CHECKOUT=1
  COMPOSE_FILES=(-f compose.build.yaml)
elif [[ -f compose.yaml ]]; then
  CHECKOUT=
  COMPOSE_FILES=(-f compose.yaml)
else
  echo "Error: run.sh needs compose.yaml beside it (the prebuilt image), or a checkout of Zonewiki." >&2
  exit 1
fi

# Read an optional KEY from .env (empty when unset).
env_val() {
  grep "^$1=" .env 2>/dev/null | cut -d= -f2-
}

# Say what the script is doing, on stderr (stdout is what a command prints,
# and build_image's is the image ID).
say() {
  echo "==> $*" >&2
}

# Podman if it's there, else Docker; each with its compose.
if command -v podman >/dev/null 2>&1; then
  ENGINE=podman
elif command -v docker >/dev/null 2>&1; then
  ENGINE=docker
else
  echo "Error: neither podman nor docker is installed." >&2
  exit 1
fi
compose() {
  if ! $ENGINE compose version >/dev/null 2>&1; then
    echo "Error: '$ENGINE compose' doesn't work: install a compose provider" \
         "(podman-compose or docker-compose)." >&2
    exit 1
  fi
  $ENGINE compose "${COMPOSE_FILES[@]}" "$@"
}

# The wiki folder from .env, default ~/zonewiki, with ~ expanded (compose
# would hand ZONE_HOST_DATA a literal ~).
ZONE_DATA=$(env_val ZONE_DATA)
[[ -n "$ZONE_DATA" ]] || ZONE_DATA="~/zonewiki"
case "$ZONE_DATA" in
  "~") ZONE_DATA="$HOME" ;;
  "~/"*) ZONE_DATA="$HOME/${ZONE_DATA:2}" ;;
esac
export ZONE_DATA

# The port's bind address from ZONE_ALLOW_REMOTE: 1 → all interfaces (LAN),
# unset/empty → ZONE_BIND from .env (what compose used without this script
# takes), else loopback only (localhost only, more secure). Exported, it
# would win over .env's, hence read here. Lowercased with tr, not ${,,}:
# macOS ships bash 3.2, which this script must run on.
ZONE_ALLOW_REMOTE=$(env_val ZONE_ALLOW_REMOTE | tr '[:upper:]' '[:lower:]')
case "$ZONE_ALLOW_REMOTE" in
  1|true|yes) ZONE_BIND="0.0.0.0" ;;
  *) ZONE_BIND=$(env_val ZONE_BIND) ;;
esac
export ZONE_BIND="${ZONE_BIND:-127.0.0.1}"

# The user Zonewiki runs as (the compose files' user:), so the files it writes
# are the host user's: your own IDs, unless .env names others (a NAS, where
# the wiki folder's owner isn't who runs this). Rootless podman maps them
# into the container as they are (keep-id, compose.podman.yaml, which has to
# be beside this script). Rootless docker can't: there the container's root
# is you, and any other UID would be a subordinate one.
ZONE_UID=$(env_val ZONE_UID) ZONE_GID=$(env_val ZONE_GID)
export ZONE_UID="${ZONE_UID:-$(id -u)}" ZONE_GID="${ZONE_GID:-$(id -g)}"
if [[ $ENGINE == podman && "$(id -u)" -ne 0 ]]; then
  if [[ ! -f compose.podman.yaml ]]; then
    echo "Error: rootless podman needs compose.podman.yaml beside run.sh (it keeps your user ID in the container)." >&2
    exit 1
  fi
  COMPOSE_FILES+=(-f compose.podman.yaml)
elif [[ $ENGINE == docker && "$(id -u)" -ne 0 && -z "$(env_val ZONE_UID)" ]] &&
     docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
  export ZONE_UID=0 ZONE_GID=0
fi

# The build Zonewiki's image comes from ("<hash> <commit date>", "+dirty" with
# uncommitted source changes), shown next to Zonewiki's version; same format as
# a local go build's (go/config.go sourceBuild).
# Only a checkout's: the prebuilt image carries CI's.
ZONE_BUILD=
[[ -z "$CHECKOUT" ]] ||
  ZONE_BUILD=$(git log -1 --format='%h %cd' --date=format:'%Y-%m-%d %H:%M' 2>/dev/null || true)
if [[ -n "$ZONE_BUILD" && -n "$(git status --porcelain -- go public 2>/dev/null)" ]]; then
  ZONE_BUILD="$ZONE_BUILD +dirty"
fi
export ZONE_BUILD="${ZONE_BUILD:-unknown}"

# The user's timezone, so "today" (due badge, tasks dates, note names,
# "Last changed") is their day instead of UTC: TZ in .env (e.g. Europe/Berlin),
# else the host's, else UTC.
TZ=$(env_val TZ)
if [[ -z "$TZ" ]]; then
  if [[ -r /etc/timezone ]]; then
    TZ=$(head -n1 /etc/timezone)
  elif [[ -L /etc/localtime ]]; then
    TZ=$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')
  fi
fi
export TZ="${TZ:-UTC}"

# Run a command in an image with the repo at /repo: in_image IMAGE [OPTION…]
# -- COMMAND…, the options going to the engine. The repo is bind-mounted, or
# with ZONE_COPY_REPO=1 (CI) copied in: there the engine is the host's while
# the checkout lives in the job container, so a mount would name a host path.
# Copied, nothing the command writes comes back; --rm has the engine remove
# the container when it ends, the trap removes one that never started.
# A cancelled CI job kills this script, not the container: the runner knows
# only its job container, and the tests went on beside the next run, which
# then took three times as long and failed where a test waits for the
# browser. So in a CI run (GITHUB_RUN_ID) the container carries its run
# (the label zonewiki-ci-run), and the next run of the repository first
# removes what another run left: runs don't overlap (the workflow's
# concurrency group), so another run's container is a cancelled one's.
in_image() {
  local image=$1 opts=()
  shift
  while [[ $1 != -- ]]; do opts+=("$1"); shift; done
  shift
  if [[ -n "$ZONE_COPY_REPO" ]]; then
    local c
    if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
      local repo="${GITHUB_REPOSITORY:-}#" left
      left=$($ENGINE ps -a --filter label=zonewiki-ci-run --format '{{.ID}} {{.Label "zonewiki-ci-run"}}' |
             awk -v repo="$repo" -v run="$repo$GITHUB_RUN_ID" 'index($2, repo) == 1 && $2 != run { print $1 }')
      if [[ -n "$left" ]]; then
        say "Removing the containers an earlier run left behind (a cancelled run's tests go on)…"
        # shellcheck disable=SC2086
        $ENGINE rm -f $left >/dev/null || true
      fi
      opts+=(--label "zonewiki-ci-run=$repo$GITHUB_RUN_ID")
    fi
    c=$($ENGINE create --rm "${opts[@]}" -w /repo "$image" "$@")
    trap "$ENGINE rm -f $c >/dev/null" EXIT
    $ENGINE cp ./. "$c:/repo"
    $ENGINE start -a "$c"
  else
    $ENGINE run --rm "${opts[@]}" -v "$PWD:/repo:z" -w /repo "$image" "$@"
  fi
}

# Build an image and print its ID: build_image TAG [OPTION…]. Commands run
# it by ID, so two builds at once (CI runs on one engine) can't swap a shared
# tag under them. The build's output (podman's -q still shows what RUN steps
# print, e.g. apt's) is shown only when it fails; each build says which
# image it builds from which file, a first build that it takes minutes.
build_image() {
  local tag=$1 log iid file=Containerfile a prev=
  shift
  for a in "$@"; do
    [[ $prev != -f ]] || file=$a
    prev=$a
  done
  if ! $ENGINE image inspect "$tag" >/dev/null 2>&1; then
    say "Building the $tag image from $file (first time; takes a few minutes)…"
  else
    say "Building the $tag image from $file (cached where nothing changed)…"
  fi
  log=$(mktemp) iid=$(mktemp)
  if ! $ENGINE build -q --iidfile "$iid" -t "$tag" "$@" . >"$log" 2>&1; then
    cat "$log" >&2
    rm -f "$log" "$iid"
    return 1
  fi
  cat "$iid"
  rm -f "$log" "$iid"
}

# The commands for working on Zonewiki need a checkout: say so instead of
# failing somewhere in compose.
needs_checkout() {
  if [[ -z "$CHECKOUT" ]]; then
    echo "./run.sh $1 needs a checkout of Zonewiki (git clone https://github.com/zonewiki/zonewiki);" \
         "beside compose.yaml alone, run.sh runs the prebuilt image: up, down, logs, update, user, group, config, export, import." >&2
    exit 1
  fi
}

# up and the settings file's commands need the wiki and .env: Zonewiki doesn't
# make a wiki where a typo in .env points.
needs_wiki() {
  if [[ ! -d "$ZONE_DATA" ]]; then
    echo "No wiki at $ZONE_DATA. Create the folder, or set ZONE_DATA in .env to your wiki." >&2
    exit 1
  fi
  if [[ ! -f .env ]]; then
    echo "No .env: create one with ZONE_DATA=<your wiki folder> (in a checkout: cp .env.example .env, see README)." >&2
    exit 1
  fi
}

# Start Zonewiki (up, update): a checkout builds the image first, compose.yaml
# pulls the prebuilt one if it isn't here yet.
start() {
  needs_wiki
  local since started
  since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  started=$(container_started)
  if [[ -n "$CHECKOUT" ]]; then
    say "Building Zonewiki's image from this checkout with $ENGINE compose (the binary is compiled inside it, not into this folder) and starting it…"
    compose up -d --build
  else
    say "Starting Zonewiki from the prebuilt image (ghcr.io/zonewiki/zonewiki) with $ENGINE compose…"
    compose up -d
  fi
  wait_up "$since" "$started"
  local port
  port=$(env_val ZONE_PORT)
  echo "Zonewiki running at http://localhost:${port:-5080}"
  echo "Zonewiki data: $ZONE_DATA"
}

# When the container's current run began (empty: none, or not running).
container_started() {
  $ENGINE inspect -f '{{if .State.Running}}{{.State.StartedAt}}{{end}}' zonewiki 2>/dev/null || true
}

# Say that Zonewiki runs only once it does. A start that fails (a folder
# Zonewiki won't make a wiki, a wiki a newer Zonewiki wrote) exits at once,
# and restart: unless-stopped starts it again, so compose up -d is content
# either way: wait for the line Zonewiki prints when it listens, in the log
# since this start ($1). A container compose left as it was (the same run
# as before up, $2) is up already. One that isn't running any more (exited,
# or restarting after an exit), or hasn't listened within a minute, shows
# the log's end instead, and the start fails.
wait_up() {
  local since=$1 started=$2 i log
  for i in $(seq 1 60); do
    log=$($ENGINE logs --since "$since" zonewiki 2>&1 || true)
    if grep -q " listening on " <<<"$log"; then
      return 0
    fi
    if [[ -n "$started" && "$(container_started)" == "$started" ]]; then
      return 0
    fi
    if [[ -z "$(container_started)" ]]; then
      break
    fi
    sleep 1
  done
  echo "Zonewiki didn't start. The end of its log (./run.sh logs follows it; ./run.sh down stops the retries):" >&2
  tail -n 5 <<<"$log" | sort -u >&2
  exit 1
}

case "${1:-up}" in
  build)
    needs_checkout build
    say "Building Zonewiki's image with $ENGINE compose (the binary is compiled inside it, not into this folder)…"
    compose build
    say "Built. ./run.sh up starts it; ./run.sh binaries builds binaries into dist/."
    ;;
  up)
    start
    ;;
  update)
    # A checkout pulls the code and builds it; beside compose.yaml, the
    # newest prebuilt image. Then Zonewiki starts again on it.
    if [[ -n "$CHECKOUT" ]]; then
      say "Pulling the newest code (git pull)…"
      git pull
    else
      say "Pulling the newest prebuilt image…"
      compose pull
    fi
    start
    ;;
  down)
    say "Stopping Zonewiki…"
    compose down
    ;;
  logs)
    compose logs -f
    ;;
  test)
    needs_checkout test
    # The server's own checks (gofmt, go vet, go test under the race detector,
    # which needs cgo: the image is without it otherwise), then the unit/API
    # tests against it, in the tools image (Containerfile.tools), one worker per
    # CPU (-n 0: one process). Extra args go to pytest; without a tests/…
    # path the whole suite runs but the browser and tasks-core tests (e2e).
    # In CI on a v* tag (GITHUB_REF, GitHub's and Forgejo's): the tag is the
    # version the binary says (go/config.go), or nothing is released, since
    # the image, the binaries and the push to GitHub all wait for the checks.
    case "${GITHUB_REF:-}" in
      refs/tags/v*)
        if ! grep -qxF "const version = \"${GITHUB_REF#refs/tags/v}\"" go/config.go; then
          echo "The tag ${GITHUB_REF#refs/tags/} isn't the version in go/config.go ($(grep '^const version = ' go/config.go)):" \
               "raise the version in a commit, then tag that commit." >&2
          exit 1
        fi
        ;;
    esac
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Running the tests in the tools image: gofmt, go vet, go test -race, then pytest…"
    # ZONE_TEST_RACE=1: the server the API tests start is built with the race
    # detector too (tests/goserver.py); several times slower.
    in_image "$image" ${ZONE_TEST_RACE:+-e ZONE_TEST_RACE=1} -- sh -c \
      'echo "==> gofmt, go vet" >&2
       (cd go && f=$(gofmt -l . ../zonefiles.go) && if [ -n "$f" ]; then echo "Not gofmt-formatted (gofmt -w them):" $f >&2; exit 1; fi &&
         go vet ./... && echo "==> go test -race" >&2 && CGO_ENABLED=1 go test -race ./...) || exit 1
       for a in "$@"; do case "$a" in tests*) paths=1;; esac; done
       [ -n "$paths" ] || set -- tests --ignore=tests/e2e --ignore=tests/test_tasks_core.py "$@"
       echo "==> pytest $*" >&2
       python3 -m pytest -q -p no:cacheprovider -n auto "$@"' -- "${@:2}"
    ;;
  bench)
    needs_checkout bench
    # The tasks at a team's size (tests/bench_tasks.py: 100 users, 40 lists,
    # 5,000 open and 25,000 closed tasks), in the tools image: its table, and
    # a failure where a budget is passed; then once more against a server
    # built with the race detector, for what that reports (no budgets: it is
    # several times slower). ZONE_BENCH_SLACK=3 allows three times the time
    # budgets (CI's runners are slower and noisier than a desk's machine).
    # Then a big vault imported (tests/bench_import.py: 5,000 notes).
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Measuring the tasks at a team's size in the tools image (tests/bench_tasks.py)…"
    in_image "$image" ${ZONE_BENCH_SLACK:+-e ZONE_BENCH_SLACK="$ZONE_BENCH_SLACK"} ${ZONE_BENCH_SIZE:+-e ZONE_BENCH_SIZE="$ZONE_BENCH_SIZE"} -- sh -c \
      'python3 -m pytest -q -p no:cacheprovider -n 0 -s tests/bench_tasks.py "$@" || exit 1
       echo; echo "The same against a server built with the race detector (no budgets):"
       ZONE_TEST_RACE=1 python3 -m pytest -q -p no:cacheprovider -n 0 tests/bench_tasks.py "$@" || exit 1
       echo; echo "A big vault imported (tests/bench_import.py, with its budgets):"
       python3 -m pytest -q -p no:cacheprovider -n 0 -s tests/bench_import.py' -- "${@:2}"
    ;;
  e2e)
    needs_checkout e2e
    # Browser tests (headless Chromium via Playwright) plus the Node-run
    # tasks-core.js tests, in the tools image (Containerfile.tools).
    # Screenshots land in tests/e2e/_screenshots/. In parallel (pytest-xdist),
    # one worker per two CPUs: each runs its own Chromium, and a worker per
    # CPU starves the browsers until timing-sensitive tests fail. -n 0 (or
    # any -n) in the extra args overrides it.
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Running the browser and tasks-core tests in the tools image (screenshots in tests/e2e/_screenshots/)…"
    in_image "$image" --ipc=host -- sh -c \
      'n=$(( $(nproc) / 2 )); [ "$n" -ge 1 ] || n=1
       python3 -m pytest tests/e2e tests/test_tasks_core.py -v -p no:cacheprovider \
         -n "$n" "$@"' -- "${@:2}"
    ;;
  flaky)
    needs_checkout flaky
    # Finds a browser test that fails now and then, and shows that its fix
    # holds: the chosen tests N times each (20; ZONE_E2E_REPEAT in
    # tests/e2e/conftest.py) on a machine made busy, as a CI runner with a
    # second run beside it is: a worker per CPU, twice e2e's, and four busy
    # loops per CPU beside them. ./run.sh flaky [N] [pytest args], e.g.
    # ./run.sh flaky 50 -k one_line_per_task. Without -k it is the whole
    # suite N times: name the tests.
    n=20
    if [[ ${2:-} =~ ^[0-9]+$ ]]; then n=$2; shift; fi
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Running the chosen browser tests $n times each on a busy machine, in the tools image…"
    in_image "$image" --ipc=host -e "ZONE_E2E_REPEAT=$n" -- sh -c \
      'cpus=$(nproc); i=0
       while [ "$i" -lt $(( cpus * 4 )) ]; do (while :; do :; done) & i=$(( i + 1 )); done
       python3 -m pytest tests/e2e -q -p no:cacheprovider -n "$cpus" "$@"; code=$?
       kill $(jobs -p) 2>/dev/null
       exit $code' -- "${@:2}"
    ;;
  retrieval)
    needs_checkout retrieval
    # Search's quality against its floor (tests/retrieval: a sample wiki, the
    # questions asked of it and the least each ranking must find; Measuring
    # retrieval in docs/spec/search.md), in the app's image with the model
    # baked in, whatever .env says: the hybrid ranking can't be measured
    # without it. A tag of its own, so the image up runs stays as .env built
    # it. Fails when a ranking finds less than its floor; the keyword
    # ranking's floor is held by ./run.sh test too (TestRetrievalHoldsItsFloor).
    image=$(build_image zonewiki-retrieval -f Containerfile --build-arg "ZONE_BUILD=$ZONE_BUILD" \
      --build-arg ZONE_SEMANTIC_SEARCH=on)
    say "Measuring search on tests/retrieval/ against floors.json, in the app's image…"
    in_image "$image" -e ZONE_DATA=/repo/tests/retrieval/wiki -e ZONE_SEMANTIC_SEARCH=on -- \
      /app/zonewiki eval --floor /repo/tests/retrieval/floors.json /repo/tests/retrieval/questions.json "${@:2}"
    ;;
  binaries)
    needs_checkout binaries
    # The release binaries (Release binaries in docs/spec/deployment.md):
    # zonewiki for Linux and macOS, amd64 and arm64, without cgo (keyword
    # search), the browser code and the docs built in (zonefiles.go), into
    # dist/ with LICENSE, THIRD_PARTY_LICENSES and SHA256SUMS; in the tools
    # image, with its pinned Go (the image's). CI's release job attaches them
    # to a v* tag's GitHub release. The build string is git's, as for any go
    # build (safe.directory: the repo is the host user's, not the container's).
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Building the release binaries in the tools image, into dist/…"
    in_image "$image" -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' -- sh -c \
      'set -e
       rm -rf dist
       mkdir dist
       for target in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64; do
         os=${target%/*} arch=${target#*/}
         name=zonewiki-$(echo "$os" | sed s/darwin/macos/)-$arch
         echo "==> dist/$name" >&2
         (cd go && CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -trimpath -ldflags="-s -w" -o "../dist/$name" .)
       done
       cp LICENSE THIRD_PARTY_LICENSES dist/
       cd dist
       sha256sum zonewiki-* LICENSE THIRD_PARTY_LICENSES > SHA256SUMS
       ls -l'
    if [[ -n "$ZONE_COPY_REPO" ]]; then
      say "Built in a copy of the repo (ZONE_COPY_REPO): nothing came back to dist/."
    else
      say "The binaries are in $PWD/dist/."
    fi
    ;;
  leaks)
    needs_checkout leaks
    # Secrets in the git history (gitleaks, in the tools image); extra args
    # go to `gitleaks git`, e.g. --pre-commit --staged for staged changes
    # only (the pre-commit hook). A leak exits with 3, so the hook can tell
    # it from an engine that doesn't work. safe.directory: the repo belongs
    # to the host user, not the container's. GIT_INDEX_FILE: for commit -a
    # or commit <path>, git hands the hook a temporary index (a host path);
    # without it, git in the container would scan .git/index, which lacks
    # those changes. One outside the repo can't be reached: exit 2.
    # In a git worktree .git is a file that names the repo's own .git by its
    # host path, where the worktree's index is too: that folder is mounted
    # at the same path, read-only, so git in the container finds both.
    idx=() gitdir=()
    repo=$(pwd -P)
    common=
    if [[ -f .git && -z "$ZONE_COPY_REPO" ]]; then
      common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) &&
        common=$(cd "$common" 2>/dev/null && pwd -P) || common=
      [[ -z "$common" ]] || gitdir=(-v "$common:$common:ro,z")
    fi
    if [[ -n "$GIT_INDEX_FILE" ]]; then
      index="$(cd "$(dirname "$GIT_INDEX_FILE")" && pwd -P)/$(basename "$GIT_INDEX_FILE")"
      case "$index" in
        "$repo"/*) idx=(-e "GIT_INDEX_FILE=/repo/${index#"$repo"/}") ;;
        "${common:-/nowhere}"/*) idx=(-e "GIT_INDEX_FILE=$index") ;;
        *) echo "leaks: the index $GIT_INDEX_FILE is outside the repo." >&2; exit 2 ;;
      esac
    fi
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Scanning the git history for secrets (gitleaks, in the tools image)…"
    in_image "$image" "${gitdir[@]}" "${idx[@]}" -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory \
      -e GIT_CONFIG_VALUE_0='*' -- \
      gitleaks git --redact --no-banner --exit-code 3 "${@:2}" .
    ;;
  screenshots)
    needs_checkout screenshots
    # The README's screenshots (docs/screenshots/*.png), from a sample wiki,
    # in the tools image (Playwright).
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    say "Taking the README's screenshots in the tools image…"
    in_image "$image" --ipc=host -- python3 docs/screenshots/make_screenshots.py
    say "The screenshots are in $PWD/docs/screenshots/."
    ;;
  eval)
    needs_checkout eval
    # Retrieval quality on your wiki (zonewiki eval, go/eval.go): the questions
    # are a file in the wiki (default _eval/retrieval.json), the wiki is
    # mounted read-only. The image is the app's with ZONE_SEMANTIC_SEARCH's model
    # baked in, as up builds it, so it compares keyword and hybrid ranking.
    file="${2:-_eval/retrieval.json}"
    if [[ ! -f "$ZONE_DATA/$file" ]]; then
      echo "No questions at $ZONE_DATA/$file (format: go/eval.go)." >&2
      exit 1
    fi
    semantic=$(env_val ZONE_SEMANTIC_SEARCH)
    image=$(build_image zonewiki -f Containerfile --build-arg "ZONE_BUILD=$ZONE_BUILD" \
      --build-arg "ZONE_SEMANTIC_SEARCH=$semantic")
    say "Measuring search on $ZONE_DATA against $file, in the app's image…"
    in_image "$image" -v "$ZONE_DATA:/wiki-data:ro,z" -e ZONE_DATA=/wiki-data \
      -e "ZONE_SEMANTIC_SEARCH=$semantic" -- \
      /app/zonewiki eval "/wiki-data/$file" "${@:3}"
    ;;
  user|group|config|export|import)
    # The settings file's commands (zonewiki user …, zonewiki group …, zonewiki config …; see
    # docs/spec/users.md), in a one-off container of the app's image, with
    # .env and the wiki as up gives them, so the file is written as Zonewiki's
    # user and import sees .env. A running Zonewiki takes the change itself.
    #
    # export and import (zonewiki export, zonewiki import; Moving a wiki in
    # docs/spec/deployment.md) run the same way. The container sees the
    # wiki's folder and nothing else of this machine, so the archive goes
    # through stdout and stdin: ./run.sh export > wiki.tar.gz,
    # ./run.sh import < wiki.tar.gz (its options before the <).
    case "$1" in
      export)
        if [[ -t 1 ]]; then
          echo "./run.sh export writes the wiki's archive to stdout: ./run.sh export > wiki.tar.gz" >&2
          exit 1
        fi
        set -- export --output - "${@:2}"
        ;;
      import)
        if [[ -t 0 ]]; then
          echo "./run.sh import reads a wiki's archive from stdin: ./run.sh import < wiki.tar.gz" >&2
          exit 1
        fi
        set -- import "${@:2}" -
        ;;
    esac
    needs_wiki
    # In a checkout, the image as up builds it, its output only when the
    # build fails; the prebuilt image compose run pulls if it isn't here.
    if [[ -n "$CHECKOUT" ]]; then
      say "Building Zonewiki's image with $ENGINE compose…"
      log=$(mktemp)
      if ! compose build -q zonewiki >"$log" 2>&1; then
        cat "$log" >&2
        rm -f "$log"
        exit 1
      fi
      rm -f "$log"
    fi
    # No terminal for an export either: one would change the archive's bytes.
    tty=()
    [[ -t 0 && "$1" != export ]] || tty=(-T)
    compose run --rm --no-deps "${tty[@]}" zonewiki /app/zonewiki "$@"
    ;;
  *)
    echo "Usage: $0 [up|down|logs|update|user|group|config|export|import|build|test|e2e|flaky|bench|retrieval|binaries|leaks|screenshots|eval]"
    echo "In a checkout, Zonewiki's image is built from it; beside compose.yaml alone, the prebuilt image runs."
    echo "  up     - start Zonewiki (default); in a checkout, build its image first"
    echo "  down   - stop Zonewiki"
    echo "  logs   - follow container logs"
    echo "  update - pull the newest code (checkout) or prebuilt image, and start Zonewiki again"
    echo "  user add|passwd|token|role|email|group|disable|enable|shared|invite|reset|remove NAME, user list - the users in the wiki's settings file"
    echo "  group includes NAME [+G] [-G], group owners NAME [+U] [-U], group list - groups made of groups and a group's owners, in the wiki's settings file"
    echo "  config check|set|unset|passwd|token - the wiki's settings file: check it, set a key, the password"
    echo "                and MCP token without users"
    echo "  export > FILE - write the whole wiki to one file, with its history and its users, to move it to another Zonewiki"
    echo "  import < FILE - make the wiki from such a file, in an empty wiki folder (--users-only, --max-size SIZE before the <)"
    echo "Only in a checkout:"
    echo "  build  - build the container image only"
    echo "  test   - gofmt, go vet, go test -race and the unit/API tests in the tools image (extra args go to pytest)"
    echo "  e2e    - run browser and tasks-core tests in the tools image (extra args go to pytest)"
    echo "  flaky [N] - run chosen browser tests N times each (20) on a busy machine, to find one that fails now and then (extra args go to pytest)"
    echo "  bench  - measure the tasks at a team's size against their budgets, then under the race detector"
    echo "  retrieval - measure search on the sample wiki (tests/retrieval) against its floor, by keywords and by meaning"
    echo "  binaries - build the release binaries (Linux and macOS, amd64 and arm64) into dist/, with SHA256SUMS"
    echo "  leaks  - scan the git history for secrets (extra args go to gitleaks git)"
    echo "  screenshots - redo the README's screenshots (docs/screenshots/)"
    echo "  eval [FILE] - measure search on your wiki against questions in FILE (in the wiki)"
    exit 1
    ;;
esac
