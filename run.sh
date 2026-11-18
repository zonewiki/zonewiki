#!/usr/bin/env bash
# Build and run Zone with compose (compose.yaml), and run its tests. This
# script works out what compose can't: the build string from git, the port's
# bind address from ZONE_ALLOW_REMOTE, the host's timezone, and the wiki path.
set -e

cd "$(dirname "$0")"

# Read an optional KEY from .env (empty when unset).
env_val() {
  grep "^$1=" .env 2>/dev/null | cut -d= -f2-
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
# unset/empty → loopback only (localhost only, more secure). Lowercased with
# tr, not ${,,}: macOS ships bash 3.2, which this script must run on.
ZONE_ALLOW_REMOTE=$(env_val ZONE_ALLOW_REMOTE | tr '[:upper:]' '[:lower:]')
case "$ZONE_ALLOW_REMOTE" in
  1|true|yes) export ZONE_BIND="0.0.0.0" ;;
  *) export ZONE_BIND="127.0.0.1" ;;
esac

# The user Zone runs as (compose.yaml), so the files it writes are the host
# user's: your own IDs. Rootless podman maps them into the container as they
# are (keep-id, compose.podman.yaml). Rootless docker can't: there the
# container's root is you, and any other UID would be a subordinate one.
export ZONE_UID="$(id -u)" ZONE_GID="$(id -g)"
COMPOSE_FILES=(-f compose.yaml)
if [[ $ENGINE == podman && "$(id -u)" -ne 0 ]]; then
  COMPOSE_FILES+=(-f compose.podman.yaml)
elif [[ $ENGINE == docker && "$(id -u)" -ne 0 ]] &&
     docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
  export ZONE_UID=0 ZONE_GID=0
fi

# The build Zone's image comes from ("<hash> <commit date>", "+dirty" with
# uncommitted source changes), shown next to Zone's version; same format as
# a local go build's (go/config.go sourceBuild).
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
# the container even when this script is killed (a cancelled CI job), the
# trap removes one that never started.
in_image() {
  local image=$1 opts=()
  shift
  while [[ $1 != -- ]]; do opts+=("$1"); shift; done
  shift
  if [[ -n "$ZONE_COPY_REPO" ]]; then
    local c
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
# print, e.g. apt's) is shown only when it fails; a first build, which takes
# minutes, says so.
build_image() {
  local tag=$1 log iid
  shift
  if ! $ENGINE image inspect "$tag" >/dev/null 2>&1; then
    echo "Building the $tag image (first time; takes a few minutes)…" >&2
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

case "${1:-up}" in
  build)
    compose build
    ;;
  up)
    # The wiki must exist: Zone doesn't make one where a typo in .env points.
    if [[ ! -d "$ZONE_DATA" ]]; then
      echo "No wiki at $ZONE_DATA. Create the folder, or set ZONE_DATA in .env to your wiki." >&2
      exit 1
    fi
    if [[ ! -f .env ]]; then
      echo "No .env: create one (cp .env.example .env, see README)." >&2
      exit 1
    fi
    compose up -d --build
    echo "Zone running at http://localhost:5000"
    echo "Zone data: $ZONE_DATA"
    ;;
  down)
    compose down
    ;;
  logs)
    compose logs -f
    ;;
  test)
    # The server's own checks (gofmt, go vet, go test under the race detector,
    # which needs cgo: the image is without it otherwise), then the unit/API
    # tests against it, in the tools image (Containerfile.tools), one worker per
    # CPU (-n 0: one process). Extra args go to pytest; without a tests/…
    # path the whole suite runs but the browser and tasks-core tests (e2e).
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    # ZONE_TEST_RACE=1: the server the API tests start is built with the race
    # detector too (tests/goserver.py); several times slower.
    in_image "$image" ${ZONE_TEST_RACE:+-e ZONE_TEST_RACE=1} -- sh -c \
      '(cd go && f=$(gofmt -l .) && if [ -n "$f" ]; then echo "Not gofmt-formatted (gofmt -w them):" $f >&2; exit 1; fi &&
         go vet ./... && CGO_ENABLED=1 go test -race ./...) || exit 1
       for a in "$@"; do case "$a" in tests*) paths=1;; esac; done
       [ -n "$paths" ] || set -- tests --ignore=tests/e2e --ignore=tests/test_tasks_core.py "$@"
       python3 -m pytest -q -p no:cacheprovider -n auto "$@"' -- "${@:2}"
    ;;
  bench)
    # The tasks at a team's size (tests/bench_tasks.py: 100 users, 40 lists,
    # 5,000 open and 25,000 closed tasks), in the tools image: its table, and
    # a failure where a budget is passed; then once more against a server
    # built with the race detector, for what that reports (no budgets: it is
    # several times slower). ZONE_BENCH_SLACK=3 allows three times the time
    # budgets (CI's runners are slower and noisier than a desk's machine).
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    in_image "$image" ${ZONE_BENCH_SLACK:+-e ZONE_BENCH_SLACK="$ZONE_BENCH_SLACK"} ${ZONE_BENCH_SIZE:+-e ZONE_BENCH_SIZE="$ZONE_BENCH_SIZE"} -- sh -c \
      'python3 -m pytest -q -p no:cacheprovider -n 0 -s tests/bench_tasks.py "$@" || exit 1
       echo; echo "The same against a server built with the race detector (no budgets):"
       ZONE_TEST_RACE=1 python3 -m pytest -q -p no:cacheprovider -n 0 tests/bench_tasks.py "$@"' -- "${@:2}"
    ;;
  e2e)
    # Browser tests (headless Chromium via Playwright) plus the Node-run
    # tasks-core.js tests, in the tools image (Containerfile.tools).
    # Screenshots land in tests/e2e/_screenshots/. In parallel (pytest-xdist),
    # one worker per two CPUs: each runs its own Chromium, and a worker per
    # CPU starves the browsers until timing-sensitive tests fail. -n 0 (or
    # any -n) in the extra args overrides it.
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    in_image "$image" --ipc=host -- sh -c \
      'n=$(( $(nproc) / 2 )); [ "$n" -ge 1 ] || n=1
       python3 -m pytest tests/e2e tests/test_tasks_core.py -v -p no:cacheprovider \
         -n "$n" "$@"' -- "${@:2}"
    ;;
  retrieval)
    # Search's quality against its floor (tests/retrieval: a sample wiki, the
    # questions asked of it and the least each ranking must find; Measuring
    # retrieval in docs/spec/search.md), in the app's image with the model
    # baked in, whatever .env says: the hybrid ranking can't be measured
    # without it. A tag of its own, so the image up runs stays as .env built
    # it. Fails when a ranking finds less than its floor; the keyword
    # ranking's floor is held by ./run.sh test too (TestRetrievalHoldsItsFloor).
    image=$(build_image zonewiki-retrieval -f Containerfile --build-arg "ZONE_BUILD=$ZONE_BUILD" \
      --build-arg ZONE_SEMANTIC_SEARCH=on)
    in_image "$image" -e ZONE_DATA=/repo/tests/retrieval/wiki -e ZONE_SEMANTIC_SEARCH=on -- \
      /app/zonewiki eval --floor /repo/tests/retrieval/floors.json /repo/tests/retrieval/questions.json "${@:2}"
    ;;
  leaks)
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
    in_image "$image" "${gitdir[@]}" "${idx[@]}" -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory \
      -e GIT_CONFIG_VALUE_0='*' -- \
      gitleaks git --redact --no-banner --exit-code 3 "${@:2}" .
    ;;
  screenshots)
    # The README's screenshots (docs/screenshots/*.png), from a sample wiki,
    # in the tools image (Playwright).
    image=$(build_image zonewiki-tools -f Containerfile.tools)
    in_image "$image" --ipc=host -- python3 docs/screenshots/make_screenshots.py
    ;;
  eval)
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
    in_image "$image" -v "$ZONE_DATA:/wiki-data:ro,z" -e ZONE_DATA=/wiki-data \
      -e "ZONE_SEMANTIC_SEARCH=$semantic" -- \
      /app/zonewiki eval "/wiki-data/$file" "${@:3}"
    ;;
  user|group|config)
    # The settings file's commands (zonewiki user …, zonewiki group …, zonewiki config …; see
    # docs/spec/users.md), in a one-off container of the app's image, with
    # .env and the wiki as up gives them, so the file is written as Zone's
    # user and import sees .env. A running Zone takes the change itself.
    if [[ ! -d "$ZONE_DATA" ]]; then
      echo "No wiki at $ZONE_DATA. Create the folder, or set ZONE_DATA in .env to your wiki." >&2
      exit 1
    fi
    if [[ ! -f .env ]]; then
      echo "No .env: create one (cp .env.example .env, see README)." >&2
      exit 1
    fi
    # The image as up builds it, its output only when the build fails.
    log=$(mktemp)
    if ! compose build -q zonewiki >"$log" 2>&1; then
      cat "$log" >&2
      rm -f "$log"
      exit 1
    fi
    rm -f "$log"
    tty=()
    [[ -t 0 ]] || tty=(-T)
    compose run --rm --no-deps "${tty[@]}" zonewiki /app/zonewiki "$@"
    ;;
  *)
    echo "Usage: $0 [build|up|down|logs|test|e2e|bench|retrieval|leaks|screenshots|eval|user|group|config]"
    echo "  build  - build the container image only"
    echo "  up     - build and start Zone (default)"
    echo "  down   - stop Zone"
    echo "  logs   - follow container logs"
    echo "  test   - gofmt, go vet, go test -race and the unit/API tests in the tools image (extra args go to pytest)"
    echo "  e2e    - run browser and tasks-core tests in the tools image (extra args go to pytest)"
    echo "  bench  - measure the tasks at a team's size against their budgets, then under the race detector"
    echo "  retrieval - measure search on the sample wiki (tests/retrieval) against its floor, by keywords and by meaning"
    echo "  leaks  - scan the git history for secrets (extra args go to gitleaks git)"
    echo "  screenshots - redo the README's screenshots (docs/screenshots/)"
    echo "  eval [FILE] - measure search on your wiki against questions in FILE (in the wiki)"
    echo "  user add|passwd|token|role|group|disable|enable|remove NAME, user list - the users in the wiki's settings file"
    echo "  group includes NAME [+G] [-G], group owners NAME [+U] [-U], group list - groups made of groups and a group's owners, in the wiki's settings file"
    echo "  config check|set|unset|passwd|token - the wiki's settings file: check it, set a key, the password"
    echo "                and MCP token without users"
    exit 1
    ;;
esac
