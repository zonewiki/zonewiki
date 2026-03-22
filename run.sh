#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

case "${1:-up}" in
  build)
    docker build -t wiki .
    ;;
  up)
    docker compose up --build -d
    echo "Wiki running at http://localhost:5000"
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
