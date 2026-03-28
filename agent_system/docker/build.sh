#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v docker compose &>/dev/null; then
    docker compose -f "$SCRIPT_DIR/docker-compose.yml" build lean4
else
    docker build -t tfl-lean4 -f "$SCRIPT_DIR/Dockerfile.lean4" "$SCRIPT_DIR"
fi
