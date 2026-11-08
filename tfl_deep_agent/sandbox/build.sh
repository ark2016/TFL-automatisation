#!/bin/sh
# Builds the sandbox image from Dockerfile.sandbox (P5).
#
#   sh tfl_deep_agent/sandbox/build.sh [image]      (default image: tfl-sandbox:latest)
#
# The Dockerfile goes in on stdin with no build context, so no repository file ends up in the image.
set -eu
IMAGE="${1:-tfl-sandbox:latest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v docker >/dev/null 2>&1 || { echo "docker is not on PATH" >&2; exit 1; }
docker build -t "$IMAGE" - < "$HERE/Dockerfile.sandbox"
echo "built $IMAGE"
