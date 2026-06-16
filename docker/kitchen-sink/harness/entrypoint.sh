#!/usr/bin/env bash
set -euo pipefail

source /opt/forklift/harness/includes/runtime_env.sh

HOST_UID=${FORKLIFT_HOST_UID:-}
HOST_GID=${FORKLIFT_HOST_GID:-}

restore_mount_ownership() {
  if [[ ! "$HOST_UID" =~ ^[0-9]+$ ]]; then
    return
  fi
  if [[ ! "$HOST_GID" =~ ^[0-9]+$ ]]; then
    return
  fi

  chown -R "$HOST_UID:$HOST_GID" /workspace /harness-state >/dev/null 2>&1 || true
}

mkdir -p /harness-state

cleanup() {
  restore_mount_ownership
}
trap cleanup EXIT
trap 'cleanup; exit 0' SIGTERM SIGINT

exec runuser -u forklift -- /opt/forklift/harness/run.sh
