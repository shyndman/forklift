#!/usr/bin/env bash
# Shared logging and failure helpers for the harness runtime.

log_client() {
  local stamp
  stamp="$(date --iso-8601=ns)"
  # date emits 9 fractional digits with a locale-dependent separator; normalize
  # to a dot and chop nanoseconds down to milliseconds for the clientlog parser.
  stamp="${stamp/,/.}"
  [[ "$stamp" =~ ^(.*\.[0-9]{3})[0-9]*(.*)$ ]] && stamp="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
  printf '%s %s\n' "$stamp" "$1" >>"$CLIENT_LOG"
}

log_client_block() {
  local phase text line
  phase="$1"
  text="$2"

  while IFS= read -r line || [[ -n "$line" ]]; do
    log_client "[$phase] $line"
  done <<<"$text"
}

emit_phase_message() {
  local phase stream message
  phase="$1"
  stream="$2"
  message="$3"

  if [[ "$stream" == "stderr" ]]; then
    printf '[%s] %s\n' "$phase" "$message" >&2
  else
    printf '[%s] %s\n' "$phase" "$message"
  fi
  log_client "[$phase] $message"
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
