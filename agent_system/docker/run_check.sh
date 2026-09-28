#!/usr/bin/env bash
set -euo pipefail

LEAN_FILE="${1:?Usage: run_check.sh <file.lean>}"
TIMEOUT="${2:-300}"
CONTAINER_NAME="tfl-lean4-check-$$"

# Mount the file into the tfl_lean lake project (which has Mathlib's
# Computability.DFA / Computability.RegularExpressions pre-built) and run
# `lake env lean` from that project directory so Mathlib is on LEAN_PATH.
# MSYS_NO_PATHCONV avoids Git Bash mangling the /home/lean/... container path
# on Windows.
MSYS_NO_PATHCONV=1 docker run --rm \
    --name "$CONTAINER_NAME" \
    --memory=4g \
    --cpus=2 \
    -v "$(realpath "$LEAN_FILE"):/home/lean/check.lean:ro" \
    -w /home/lean/tfl_lean \
    tfl-lean4 \
    timeout "$TIMEOUT" lake env lean /home/lean/check.lean 2>&1

exit $?
