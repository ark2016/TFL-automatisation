#!/usr/bin/env bash
set -euo pipefail

LEAN_FILE="${1:?Usage: run_check.sh <file.lean>}"
TIMEOUT="${2:-120}"
CONTAINER_NAME="tfl-lean4-check-$$"

# Copy file into a temporary container and run lean
docker run --rm \
    --name "$CONTAINER_NAME" \
    --memory=2g \
    --cpus=2 \
    -v "$(realpath "$LEAN_FILE"):/home/lean/check.lean:ro" \
    tfl-lean4 \
    timeout "$TIMEOUT" lean /home/lean/check.lean 2>&1

exit $?
