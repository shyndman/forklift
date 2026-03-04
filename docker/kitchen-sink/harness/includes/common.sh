#!/usr/bin/env bash
# Shared logging and failure helpers for the harness runtime.

log_client() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$1" >>"$CLIENT_LOG"
}

print_header() {
  printf '== %s ==\n' "$1"
}

fail_harness() {
  local message
  message="$1"
  log_client "$message"
  printf '%s\n' "$message" >&2
  exit 1
}
