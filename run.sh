#!/usr/bin/env bash
# Build and run Leaf with compose (compose.yaml), and run its tests. This
# script works out what compose can't: the build string from git, the port's
# bind address from LEAF_ALLOW_REMOTE, the host's timezone, and the wiki path.
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

# The wiki folder from .env, default ~/leaf, with ~ expanded (compose would
# hand LEAF_HOST_DATA a literal ~).
LEAF_DATA=$(env_val LEAF_DATA)
LEAF_DATA="${LEAF_DATA:-~/leaf}"
case "$LEAF_DATA" in
  "~") LEAF_DATA="$HOME" ;;
  "~/"*) LEAF_DATA="$HOME/${LEAF_DATA:2}" ;;
esac
export LEAF_DATA

# The port's bind address from LEAF_ALLOW_REMOTE: 1 → all interfaces (LAN),
# unset/empty → loopback only (localhost only, more secure).
LEAF_ALLOW_REMOTE=$(env_val LEAF_ALLOW_REMOTE)
if [[ "${LEAF_ALLOW_REMOTE,,}" =~ ^(1|true|yes)$ ]]; then
  export LEAF_BIND="0.0.0.0"
else
  export LEAF_BIND="127.0.0.1"
fi

# The user Leaf runs as (compose.yaml), so the files it writes are the host
# user's: your own IDs. Rootless podman maps them into the container as they
# are (keep-id, compose.podman.yaml). Rootless docker can't: there the
# container's root is you, and any other UID would be a subordinate one.
export LEAF_UID="$(id -u)" LEAF_GID="$(id -g)"
COMPOSE_FILES=(-f compose.yaml)
if [[ $ENGINE == podman && "$(id -u)" -ne 0 ]]; then
  COMPOSE_FILES+=(-f compose.podman.yaml)
elif [[ $ENGINE == docker && "$(id -u)" -ne 0 ]] &&
     docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
  export LEAF_UID=0 LEAF_GID=0
fi

# The build Leaf's image comes from ("<hash> <commit date>", "+dirty" with
# uncommitted source changes), shown next to config.py's VERSION; same format
# as config.py's _source_build().
LEAF_BUILD=$(git log -1 --format='%h %cd' --date=format:'%Y-%m-%d %H:%M' 2>/dev/null || true)
if [[ -n "$LEAF_BUILD" && -n "$(git status --porcelain -- src 2>/dev/null)" ]]; then
  LEAF_BUILD="$LEAF_BUILD +dirty"
fi
export LEAF_BUILD="${LEAF_BUILD:-unknown}"

# The user's timezone, so "today" (due badge, deck dates, scratch-note names,
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
# with LEAF_COPY_REPO=1 (CI) copied in: there the engine is the host's while
# the checkout lives in the job container, so a mount would name a host path.
# Copied, nothing the command writes comes back; --rm has the engine remove
# the container even when this script is killed (a cancelled CI job), the
# trap removes one that never started.
in_image() {
  local image=$1 opts=()
  shift
  while [[ $1 != -- ]]; do opts+=("$1"); shift; done
  shift
  if [[ -n "$LEAF_COPY_REPO" ]]; then
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
    # The wiki must exist: Leaf doesn't make one where a typo in .env points.
    if [[ ! -d "$LEAF_DATA" ]]; then
      echo "No wiki at $LEAF_DATA. Create the folder, or set LEAF_DATA in .env to your wiki." >&2
      exit 1
    fi
    if [[ ! -f .env ]]; then
      echo "No .env: create one (e.g. echo 'LEAF_DATA=~/leaf' > .env, see README)." >&2
      exit 1
    fi
    # A container named leaf that compose didn't create (an older run.sh
    # started it with podman run) would block compose's.
    if $ENGINE container inspect leaf >/dev/null 2>&1 &&
       ! $ENGINE container inspect leaf --format '{{json .Config.Labels}}' | grep -q 'compose'; then
      $ENGINE rm -f leaf >/dev/null
    fi
    compose up -d --build
    echo "Leaf running at http://localhost:5000"
    echo "Leaf data: $LEAF_DATA"
    ;;
  down)
    compose down
    ;;
  logs)
    compose logs -f
    ;;
  test)
    # Unit/API tests inside the app image (repo mounted; pytest added on the fly).
    # Extra args go to pytest; without a tests/… path the whole suite runs.
    # Built without compose, which would want a .env the tests don't need.
    image=$(build_image leaf --build-arg "LEAF_BUILD=$LEAF_BUILD")
    in_image "$image" -- sh -c \
      'pip install --break-system-packages -q pytest 2>/dev/null
       for a in "$@"; do case "$a" in tests*) paths=1;; esac; done
       [ -n "$paths" ] || set -- tests "$@"
       python3 -m pytest -q "$@"' -- "${@:2}"
    ;;
  e2e)
    # Browser tests (headless Chromium via Playwright) plus the Node-run
    # deck-core.js tests, in the tools image (Containerfile.tools).
    # Screenshots land in tests/e2e/_screenshots/.
    image=$(build_image leaf-tools -f Containerfile.tools)
    in_image "$image" --ipc=host -- \
      python3 -m pytest tests/e2e tests/test_deck_core.py -v -p no:cacheprovider "${@:2}"
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
    idx=()
    if [[ -n "$GIT_INDEX_FILE" ]]; then
      repo=$(pwd -P)
      index="$(cd "$(dirname "$GIT_INDEX_FILE")" && pwd -P)/$(basename "$GIT_INDEX_FILE")"
      case "$index" in
        "$repo"/*) idx=(-e "GIT_INDEX_FILE=/repo/${index#"$repo"/}") ;;
        *) echo "leaks: the index $GIT_INDEX_FILE is outside the repo." >&2; exit 2 ;;
      esac
    fi
    image=$(build_image leaf-tools -f Containerfile.tools)
    in_image "$image" "${idx[@]}" -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory \
      -e GIT_CONFIG_VALUE_0='*' -- \
      gitleaks git --redact --no-banner --exit-code 3 "${@:2}" .
    ;;
  screenshots)
    # The README's screenshots (docs/screenshots/*.png), from a sample wiki,
    # in the tools image (Playwright).
    image=$(build_image leaf-tools -f Containerfile.tools)
    in_image "$image" --ipc=host -- python3 docs/screenshots/make_screenshots.py
    ;;
  *)
    echo "Usage: $0 [build|up|down|logs|test|e2e|leaks|screenshots]"
    echo "  build  - build the container image only"
    echo "  up     - build and start Leaf (default)"
    echo "  down   - stop Leaf"
    echo "  logs   - follow container logs"
    echo "  test   - run unit/API tests in the app image (extra args go to pytest)"
    echo "  e2e    - run browser and deck-core tests in the tools image (extra args go to pytest)"
    echo "  leaks  - scan the git history for secrets (extra args go to gitleaks git)"
    echo "  screenshots - redo the README's screenshots (docs/screenshots/)"
    exit 1
    ;;
esac
