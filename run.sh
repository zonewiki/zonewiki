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

# Ensure subdirs exist on the host before Docker creates them as root
mkdir -p "$WIKI_DATA" "$WIKI_DATA/_uploads"

HOST_UID=$(id -u)
HOST_GID=$(id -g)

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
  LEGACY_DOCKER_COMPOSE=0
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
  LEGACY_DOCKER_COMPOSE=1
else
  echo "Error: neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

case "${1:-up}" in
  build)
    docker build -t leaf .
    ;;
  up)
    if [[ "$LEGACY_DOCKER_COMPOSE" -eq 1 ]]; then
      # docker-compose v1 can crash while recreating containers on newer Docker releases.
      env WIKI_DATA="$WIKI_DATA" UID="$HOST_UID" GID="$HOST_GID" "${COMPOSE_CMD[@]}" down --remove-orphans >/dev/null 2>&1 || true
    fi
    env WIKI_DATA="$WIKI_DATA" UID="$HOST_UID" GID="$HOST_GID" "${COMPOSE_CMD[@]}" up --build -d
    echo "Leaf running at http://localhost:5000"
    echo "Leaf data: $WIKI_DATA"
    ;;
  down)
    env WIKI_DATA="$WIKI_DATA" UID="$HOST_UID" GID="$HOST_GID" "${COMPOSE_CMD[@]}" down
    ;;
  logs)
    env WIKI_DATA="$WIKI_DATA" UID="$HOST_UID" GID="$HOST_GID" "${COMPOSE_CMD[@]}" logs -f
    ;;
  *)
    echo "Usage: $0 [build|up|down|logs]"
    echo "  build  - build the Docker image only"
    echo "  up     - build and start Leaf (default)"
    echo "  down   - stop Leaf"
    echo "  logs   - follow container logs"
    exit 1
    ;;
esac
