#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Read WIKI_DATA from .env, fall back to ~/leaf
WIKI_DATA=$(grep '^WIKI_DATA=' .env 2>/dev/null | cut -d= -f2-)
WIKI_DATA="${WIKI_DATA:-~/leaf}"

case "$WIKI_DATA" in
  "~")
    WIKI_DATA="$HOME"
    ;;
  "~/"*)
    WIKI_DATA="$HOME/${WIKI_DATA:2}"
    ;;
esac

# Ensure subdirs exist on the host before the container creates them
mkdir -p "$WIKI_DATA" "$WIKI_DATA/_assets"

HOST_UID=$(id -u)
HOST_GID=$(id -g)

# Read an optional KEY from .env (empty when unset).
env_val() {
  grep "^$1=" .env 2>/dev/null | cut -d= -f2-
}

# Derive the port-binding address from ALLOW_REMOTE.
# ALLOW_REMOTE=1 → bind on all interfaces (LAN accessible).
# Unset/empty   → bind on loopback only (localhost only, more secure).
ALLOW_REMOTE=$(env_val ALLOW_REMOTE)
if [[ "${ALLOW_REMOTE,,}" =~ ^(1|true|yes)$ ]]; then
  BIND_ADDR="0.0.0.0"
else
  BIND_ADDR="127.0.0.1"
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "Error: 'podman' is not installed."
  exit 1
fi

IMAGE=leaf
CONTAINER=leaf

# The build Leaf's image comes from ("<hash> <commit date>", "+dirty" with
# uncommitted source changes), shown next to server.py's VERSION; same format
# as server.py's _source_build().
LEAF_BUILD=$(git log -1 --format='%h %cd' --date=format:'%Y-%m-%d %H:%M' 2>/dev/null || true)
if [[ -n "$LEAF_BUILD" && -n "$(git status --porcelain -- src 2>/dev/null)" ]]; then
  LEAF_BUILD="$LEAF_BUILD +dirty"
fi
# Semantic search (see src/embed.py): the image installs the model at build
# time, so the setting is a build arg as well as an env var.
LEAF_EMBEDDINGS=$(env_val LEAF_EMBEDDINGS)
BUILD_ARGS=(--build-arg "LEAF_BUILD=${LEAF_BUILD:-unknown}" --build-arg "LEAF_EMBEDDINGS=${LEAF_EMBEDDINGS}")

# Give the container the host's timezone, so "today" (due badge, deck
# dates, scratch-note names, "Last changed") matches the user's day instead
# of UTC. TZ in .env (e.g. Europe/Berlin) overrides; without either, UTC.
TZ_FLAGS=()
TZ_VAL=$(env_val TZ)
if [[ -n "$TZ_VAL" ]]; then
  TZ_FLAGS=(--tz "$TZ_VAL")
elif [[ -e /etc/localtime ]]; then
  TZ_FLAGS=(--tz local)
fi

# Rootless podman already maps in-container root to the host user, so files
# written to the mounted volume are owned by the host user. Only a rootful
# podman (host user is root) needs an explicit --user to match that.
USER_FLAGS=()
if [[ "$HOST_UID" -eq 0 ]]; then
  USER_FLAGS=(--user "${HOST_UID}:${HOST_GID}")
fi

# Relabel the mounted volume when SELinux is enforcing.
VOLUME="${WIKI_DATA}:/wiki-data"
if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled 2>/dev/null; then
  VOLUME="${VOLUME}:Z"
fi

case "${1:-up}" in
  build)
    podman build "${BUILD_ARGS[@]}" -t "$IMAGE" .
    ;;
  up)
    podman build "${BUILD_ARGS[@]}" -t "$IMAGE" .
    # k8s-file (docker's json-file equivalent) so `podman logs -f` works even
    # when the podman default log driver is journald (logs -f is unsupported).
    # --init: a minimal init as PID 1 reaps orphaned processes (Leaf as PID 1
    # wouldn't, and they would pile up as zombies until the PID limit).
    podman run -d --init --name "$CONTAINER" --replace --restart unless-stopped \
      --log-driver k8s-file \
      "${USER_FLAGS[@]}" \
      "${TZ_FLAGS[@]}" \
      -p "${BIND_ADDR}:5000:5000" \
      -v "$VOLUME" \
      -e FLASK_ENV=production \
      -e WIKI_DATA=/wiki-data \
      -e "LEAF_PASSWORD=$(env_val LEAF_PASSWORD)" \
      -e "SECRET_KEY=$(env_val SECRET_KEY)" \
      -e "GIT_REMOTE=$(env_val GIT_REMOTE)" \
      -e "ALLOW_REMOTE=${ALLOW_REMOTE}" \
      -e "LEAF_EMBEDDINGS=${LEAF_EMBEDDINGS}" \
      -e "LEAF_EMBED_MIN_SCORE=$(env_val LEAF_EMBED_MIN_SCORE)" \
      -e "LEAF_MCP_TOKEN=$(env_val LEAF_MCP_TOKEN)" \
      -e "LEAF_BASE_URL=$(env_val LEAF_BASE_URL)" \
      -e "LEAF_NOSHARE=$(env_val LEAF_NOSHARE)" \
      "$IMAGE"
    echo "Leaf running at http://localhost:5000"
    echo "Leaf data: $WIKI_DATA"
    ;;
  down)
    podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
    ;;
  logs)
    podman logs -f "$CONTAINER"
    ;;
  test)
    # Unit/API tests inside the app image (repo mounted; pytest added on the fly).
    # Extra args go to pytest; without a tests/… path the whole suite runs.
    podman build -q "${BUILD_ARGS[@]}" -t "$IMAGE" . >/dev/null
    podman run --rm -v "$PWD:/repo:z" -w /repo "$IMAGE" sh -c \
      'pip install --break-system-packages -q pytest 2>/dev/null
       for a in "$@"; do case "$a" in tests*) paths=1;; esac; done
       [ -n "$paths" ] || set -- tests "$@"
       python3 -m pytest -q "$@"' -- "${@:2}"
    ;;
  e2e)
    # Browser tests (headless Chromium via Playwright) plus the Node-run
    # deck-core.js tests, in a separate test image that has both.
    # Screenshots land in tests/e2e/_screenshots/.
    podman build -q -t leaf-e2e -f Containerfile.e2e . >/dev/null
    podman run --rm --ipc=host -v "$PWD:/repo:z" -w /repo leaf-e2e \
      python3 -m pytest tests/e2e tests/test_deck_core.py -v -p no:cacheprovider "${@:2}"
    ;;
  *)
    echo "Usage: $0 [build|up|down|logs|test|e2e]"
    echo "  build  - build the container image only"
    echo "  up     - build and start Leaf (default)"
    echo "  down   - stop Leaf"
    echo "  logs   - follow container logs"
    echo "  test   - run unit/API tests in the app image (extra args go to pytest)"
    echo "  e2e    - run browser tests in the Playwright image (extra args go to pytest)"
    exit 1
    ;;
esac
