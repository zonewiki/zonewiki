#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Read WIKI_DATA from .env, fall back to ./wiki-data
WIKI_DATA=$(grep '^WIKI_DATA=' .env 2>/dev/null | cut -d= -f2-)
WIKI_DATA="${WIKI_DATA:-./wiki-data}"

# Ensure subdirs exist on the host before Docker creates them as root
mkdir -p "$WIKI_DATA" "$WIKI_DATA/_uploads"

case "${1:-up}" in
  build)
    docker build -t wiki .
    ;;
  up)
    docker compose up --build -d
    echo "Wiki running at http://localhost:5000"
    echo "Wiki data: $WIKI_DATA"
    ;;
  down)
    docker compose down
    ;;
  logs)
    docker compose logs -f
    ;;
  *)
    echo "Usage: $0 [build|up|down|logs]"
    echo "  build  - build the Docker image only"
    echo "  up     - build and start the wiki (default)"
    echo "  down   - stop the wiki"
    echo "  logs   - follow container logs"
    exit 1
    ;;
esac
