#!/usr/bin/env bash
# Shared logging and failure helpers for the harness runtime.

log_client() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$1" >>"$CLIENT_LOG"
}

print_header() {
  printf '== %s ==\n' "$1"
}

write_harness_status() {
  local status phase message status_file
  status="$1"
  phase="${2:-${HARNESS_PHASE:-unknown}}"
  message="${3:-}"
  status_file="${HARNESS_STATUS_FILE:-$HARNESS_STATE_DIR/harness-status.txt}"

  {
    printf 'status=%s\n' "$status"
    printf 'phase=%s\n' "$phase"
    printf 'message=%s\n' "$message"
  } >"$status_file"
}

fail_harness() {
  local message
  message="$1"
  write_harness_status "failed" "${2:-${HARNESS_PHASE:-unknown}}" "$message"
  log_client "$message"
  printf '%s\n' "$message" >&2
  exit 1
}
