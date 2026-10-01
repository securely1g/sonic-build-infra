#!/usr/bin/env bash
# Run from the consumer checkout; the image digest participates in Bazel actions.
set -euo pipefail
if [[ $# -lt 2 || ! $1 =~ ^ghcr.io/securely1g/sonic-build-tools@sha256:[0-9a-f]{64}$ ]]; then
  echo 'Usage: run.sh ghcr.io/securely1g/sonic-build-tools@sha256:DIGEST build|test [BAZEL_ARGS...]' >&2
  exit 2
fi
image=$1
shift
case "$1" in build|test) ;; *) echo 'Only build and test commands are supported' >&2; exit 2 ;; esac
command=$1
shift
docker pull "$image"
docker run --rm --init \
  --user "$(id -u):$(id -g)" \
  --env "SONIC_BUILD_TOOLS_IMAGE=$image" \
  --env HOME=/tmp/sonic-build-home \
  --mount "type=bind,src=$PWD,dst=/workspace" --workdir /workspace \
  "$image" /bin/bash -c '
    set -euo pipefail
    mkdir -p "$HOME"
    python3 /opt/sonic-build-tools-recipe/smoke_test.py
    recipe=$(python3 -c "import json; print(json.load(open(\"/etc/sonic-build-tools.json\"))[\"recipe_sha256\"])")
    command=$1
    shift
    exec bazel "$command" \
      "--action_env=SONIC_BUILD_TOOLS_IMAGE=$SONIC_BUILD_TOOLS_IMAGE" \
      "--host_action_env=SONIC_BUILD_TOOLS_IMAGE=$SONIC_BUILD_TOOLS_IMAGE" \
      "--test_env=SONIC_BUILD_TOOLS_IMAGE=$SONIC_BUILD_TOOLS_IMAGE" \
      "--action_env=SONIC_BUILD_TOOLS_RECIPE=$recipe" \
      "--host_action_env=SONIC_BUILD_TOOLS_RECIPE=$recipe" \
      "--test_env=SONIC_BUILD_TOOLS_RECIPE=$recipe" "$@"
  ' -- "$command" "$@"
