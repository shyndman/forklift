#!/usr/bin/env bash
# Harness-owned rebase mediation helpers.

REBASE_CONTINUE_CHECK_EXIT_CODE=0
REBASE_CONTINUE_CHECK_STDOUT=""
REBASE_CONTINUE_CHECK_STDERR=""
INITIAL_REBASE_RESULT=""
REBASE_EVENT_STEP=""
REBASE_EVENT_TOTAL=""
REBASE_EVENT_SHA=""
REBASE_EVENT_SUBJECT=""
REBASE_EVENT_FILES=()
REBASE_HELPER_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
FORKLIFT_GIT_USER_NAME=${FORKLIFT_GIT_USER_NAME:-Forklift Agent}
FORKLIFT_GIT_USER_EMAIL=${FORKLIFT_GIT_USER_EMAIL:-forklift@github.com}

resolve_real_git_bin() {
  local resolved
  resolved=$(command -v git || true)
  if [[ -z "$resolved" ]]; then
    return 1
  fi
  REAL_GIT_BIN="$resolved"
  export REAL_GIT_BIN
  return 0
}

prepend_git_wrapper_path() {
  local wrapper_dir
  wrapper_dir="$REBASE_HELPER_ROOT/includes/bin"
  PATH="$wrapper_dir:$PATH"
  export PATH
}

enable_rebase_mediation() {
  if ! resolve_real_git_bin; then
    return 1
  fi
  prepend_git_wrapper_path
}

initialize_rebase_skipped_commits_file() {
  printf '[]\n' >"$REBASE_SKIPPED_COMMITS_FILE"
}

initialize_rebase_conflicting_commits_file() {
  printf '[]\n' >"$REBASE_CONFLICTING_COMMITS_FILE"
}

write_rebase_continue_check_file() {
  if [[ -z "$FORK_REBASE_CONTINUE_CHECK" ]]; then
    rm -f "$REBASE_CONTINUE_CHECK_FILE"
    return 0
  fi

  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -euo pipefail\n'
    printf '%s\n' "$FORK_REBASE_CONTINUE_CHECK"
  } >"$REBASE_CONTINUE_CHECK_FILE"
  chmod 0555 "$REBASE_CONTINUE_CHECK_FILE"
}

rebase_in_progress() {
  [[ -d "$WORKSPACE_DIR/.git/rebase-merge" || -d "$WORKSPACE_DIR/.git/rebase-apply" ]]
}

capture_status_snapshot() {
  "$REAL_GIT_BIN" -C "$WORKSPACE_DIR" status --porcelain=v1 --untracked-files=all
}

resolve_current_rebase_head_identity() {
  CURRENT_REBASE_SHA=$(run_real_git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD 2>/dev/null || true)
  CURRENT_REBASE_SUBJECT=$(run_real_git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD 2>/dev/null || true)
  [[ -n "$CURRENT_REBASE_SHA" && -n "$CURRENT_REBASE_SUBJECT" ]]
}

paused_rebase_is_clean_empty_stop() {
  local status_snapshot

  if ! rebase_in_progress; then
    return 1
  fi

  status_snapshot=$(capture_status_snapshot)
  [[ -z "$status_snapshot" ]]
}

run_real_git() {
  env -i \
    HOME="${HOME:-/home/forklift}" \
    PATH="$PATH" \
    LANG="${LANG:-C.UTF-8}" \
    LC_ALL="${LC_ALL:-${LANG:-C.UTF-8}}" \
    TERM="${TERM:-dumb}" \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_COMMITTER_NAME="$FORKLIFT_GIT_USER_NAME" \
    GIT_COMMITTER_EMAIL="$FORKLIFT_GIT_USER_EMAIL" \
    GIT_EDITOR="${FORKLIFT_GIT_EDITOR:-true}" \
    "$REAL_GIT_BIN" "$@"
}

has_caller_git_config_override() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -c|--config-env|--config-env=*|-c*)
        return 0
        ;;
    esac
  done
  return 1
}

is_allowed_paused_git_command() {
  local command_name
  command_name="${1:-}"
  case "$command_name" in
    add|checkout|diff|log|merge-file|rev-parse|show|status)
      return 0
      ;;
  esac
  return 1
}

count_rebase_commits() {
  run_real_git -C "$WORKSPACE_DIR" rev-list --count "$UPSTREAM_REF..HEAD" 2>/dev/null || printf '0\n'
}

read_rebase_progress_snapshot() {
  local state_dir step_file total_file

  REBASE_EVENT_STEP=""
  REBASE_EVENT_TOTAL=""
  REBASE_EVENT_SHA=""
  REBASE_EVENT_SUBJECT=""
  REBASE_EVENT_FILES=()

  if [[ -d "$WORKSPACE_DIR/.git/rebase-merge" ]]; then
    state_dir="$WORKSPACE_DIR/.git/rebase-merge"
    step_file="msgnum"
    total_file="end"
  elif [[ -d "$WORKSPACE_DIR/.git/rebase-apply" ]]; then
    state_dir="$WORKSPACE_DIR/.git/rebase-apply"
    step_file="next"
    total_file="last"
  else
    return 1
  fi

  REBASE_EVENT_STEP=$(tr -d '[:space:]' <"$state_dir/$step_file")
  REBASE_EVENT_TOTAL=$(tr -d '[:space:]' <"$state_dir/$total_file")
  REBASE_EVENT_SHA=$(run_real_git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD 2>/dev/null || true)
  REBASE_EVENT_SUBJECT=$(run_real_git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD 2>/dev/null || true)
  mapfile -t REBASE_EVENT_FILES < <(
    run_real_git -C "$WORKSPACE_DIR" diff --name-only --diff-filter=U 2>/dev/null || true
  )

  [[ -n "$REBASE_EVENT_STEP" && -n "$REBASE_EVENT_TOTAL" ]]
}

emit_rebase_event_payload() {
  local event step total sha subject event_output
  event="$1"
  step="$2"
  total="$3"
  sha="$4"
  subject="$5"
  shift 5

  if [[ -z "${FORKLIFT_REBASE_EVENTS_SOCK:-}" ]]; then
    return 0
  fi

  if ! event_output=$(python3 - "$FORKLIFT_REBASE_EVENTS_SOCK" "$event" "$step" "$total" "$sha" "$subject" "$@" 2>&1 <<'PY'
from __future__ import annotations

import json
import socket
import sys

socket_path = sys.argv[1]
event_name = sys.argv[2]
step = int(sys.argv[3])
total = int(sys.argv[4])
sha = sys.argv[5]
subject = sys.argv[6]
files = [value for value in sys.argv[7:] if value]

payload = {
    "v": 1,
    "event": event_name,
    "step": step,
    "total": total,
}
if sha:
    payload["sha"] = sha
if subject:
    payload["subject"] = subject
if files:
    payload["files"] = files

client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.settimeout(1)
try:
    client.connect(socket_path)
    client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
except Exception as exc:  # noqa: BLE001
    print(f"Unable to emit structured rebase event {event_name}: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    client.close()
PY
  ); then

    if [[ -n "$event_output" ]]; then
      emit_phase_message "rebase" "stderr" "$event_output"
    else
      emit_phase_message "rebase" "stderr" "Unable to emit structured rebase event $event"
    fi
  fi
  return 0
}

emit_rebase_event_from_snapshot() {
  local event
  event="$1"
  if ! read_rebase_progress_snapshot; then
    return 0
  fi
  emit_rebase_event_payload \
    "$event" \
    "$REBASE_EVENT_STEP" \
    "$REBASE_EVENT_TOTAL" \
    "$REBASE_EVENT_SHA" \
    "$REBASE_EVENT_SUBJECT" \
    "${REBASE_EVENT_FILES[@]}"
}

emit_paused_rebase_events() {
  emit_rebase_event_from_snapshot "progress"
  emit_rebase_event_from_snapshot "conflict"
}

emit_complete_rebase_event() {
  local total
  total="$1"
  if [[ -z "$total" ]]; then
    return 0
  fi
  emit_rebase_event_payload "complete" "$total" "$total" "" ""
}

emit_post_rebase_transition_events() {
  local total
  total="$1"
  if rebase_in_progress; then
    emit_paused_rebase_events
    return 0
  fi
  emit_complete_rebase_event "${total:-$(count_rebase_commits)}"
}

auto_skip_clean_empty_rebase_stop() {
  local current_total skip_exit_code

  if ! paused_rebase_is_clean_empty_stop; then
    return 1
  fi

  current_total=""
  if read_rebase_progress_snapshot; then
    current_total="$REBASE_EVENT_TOTAL"
    emit_rebase_event_payload \
      "auto_skip" \
      "$REBASE_EVENT_STEP" \
      "$REBASE_EVENT_TOTAL" \
      "$REBASE_EVENT_SHA" \
      "$REBASE_EVENT_SUBJECT" \
      "${REBASE_EVENT_FILES[@]}"
  fi

  emit_phase_message "rebase" "stdout" "Auto-skipping clean empty rebase stop"
  set +e
  run_real_git -C "$WORKSPACE_DIR" rebase --skip
  skip_exit_code=$?
  set -e
  if [[ $skip_exit_code -eq 0 ]]; then
    emit_post_rebase_transition_events "$current_total"
  fi
  return $skip_exit_code
}

start_initial_rebase() {
  local rebase_exit_code rebase_total

  INITIAL_REBASE_RESULT=""
  rebase_total=$(count_rebase_commits)
  emit_phase_message "rebase" "stdout" "Starting initial rebase onto $UPSTREAM_REF"
  set +e
  run_real_git -C "$WORKSPACE_DIR" rebase "$UPSTREAM_REF"
  rebase_exit_code=$?
  set -e

  if [[ $rebase_exit_code -eq 0 ]]; then
    if rebase_in_progress; then
      INITIAL_REBASE_RESULT="failed"
      emit_phase_message "rebase" "stderr" "Initial rebase reported success but left rebase state behind"
      return 1
    fi

    INITIAL_REBASE_RESULT="completed"
    emit_phase_message "rebase" "stdout" "Initial rebase completed cleanly"
    emit_complete_rebase_event "$rebase_total"
    return 0
  fi

  if rebase_in_progress; then
    while rebase_in_progress && paused_rebase_is_clean_empty_stop; do
      if ! auto_skip_clean_empty_rebase_stop; then
        INITIAL_REBASE_RESULT="failed"
        emit_phase_message "rebase" "stderr" "Initial rebase could not auto-skip clean empty rebase stop"
        return 1
      fi
    done

    if ! rebase_in_progress; then
      INITIAL_REBASE_RESULT="completed"
      emit_phase_message "rebase" "stdout" "Initial rebase completed after auto-skipping clean empty stops"
      return 0
    fi

    INITIAL_REBASE_RESULT="paused"
    record_current_conflicting_commit
    emit_paused_rebase_events
    emit_phase_message "rebase" "stdout" "Initial rebase paused on conflicts"
    return 0
  fi

  INITIAL_REBASE_RESULT="failed"
  emit_phase_message "rebase" "stderr" "Initial rebase failed before entering a paused rebase state"
  return 1
}

run_continue_check() {
  local stdout_file stderr_file exit_code
  stdout_file=$(mktemp)
  stderr_file=$(mktemp)

  set +e
  (
    cd "$WORKSPACE_DIR"
    bash "$REBASE_CONTINUE_CHECK_FILE"
  ) >"$stdout_file" 2>"$stderr_file"
  exit_code=$?
  set -e

  REBASE_CONTINUE_CHECK_EXIT_CODE=$exit_code
  REBASE_CONTINUE_CHECK_STDOUT=$(cat "$stdout_file")
  REBASE_CONTINUE_CHECK_STDERR=$(cat "$stderr_file")
  rm -f "$stdout_file" "$stderr_file"
  return $exit_code
}

continue_check_command_text() {
  if [[ -n "${FORK_REBASE_CONTINUE_CHECK:-}" ]]; then
    printf '%s' "$FORK_REBASE_CONTINUE_CHECK"
    return 0
  fi

  if [[ ! -f "$REBASE_CONTINUE_CHECK_FILE" ]]; then
    return 0
  fi

  tail -n +3 "$REBASE_CONTINUE_CHECK_FILE"
}

emit_rebase_continue_failure() {
  local first_line exit_code status_snapshot command_text failure_message
  first_line="$1"
  exit_code="$2"
  status_snapshot="$3"
  command_text=$(continue_check_command_text)
  failure_message=$(
    cat <<EOF
$first_line

Command:
$command_text

Exit code:
$exit_code

Workspace state after check:
$status_snapshot

stdout:
$REBASE_CONTINUE_CHECK_STDOUT

stderr:
$REBASE_CONTINUE_CHECK_STDERR

Resolve state, then retry rebase continue.
EOF
  )

  emit_phase_message "rebase" "stderr" "Blocking git rebase --continue"
  printf '%s\n' "$failure_message" >&2
  log_client_block "rebase" "$failure_message"
}

append_agent_record() {
  local records_file sha subject dedupe_by_sha
  records_file="$1"
  sha="$2"
  subject="$3"
  dedupe_by_sha="${4:-0}"
  python3 - "$records_file" "$sha" "$subject" "$dedupe_by_sha" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
sha = sys.argv[2]
subject = sys.argv[3]
dedupe_by_sha = sys.argv[4] == "1"

try:
    raw_records = json.loads(path.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    raw_records = []

records: list[dict[str, str]] = []
if isinstance(raw_records, list):
    for entry in raw_records:
        if not isinstance(entry, dict):
            continue
        record_sha = entry.get("sha")
        record_subject = entry.get("subject")
        if isinstance(record_sha, str) and record_sha and isinstance(record_subject, str) and record_subject:
            records.append({"sha": record_sha, "subject": record_subject})

if dedupe_by_sha and any(record["sha"] == sha for record in records):
    print("duplicate")
    raise SystemExit(0)

records.append({"sha": sha, "subject": subject})
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
print("added")
PY
}

append_agent_skip_record() {
  append_agent_record "$REBASE_SKIPPED_COMMITS_FILE" "$1" "$2" 0
}

append_agent_conflict_record() {
  append_agent_record "$REBASE_CONFLICTING_COMMITS_FILE" "$1" "$2" 1
}

record_current_conflicting_commit() {
  local rebase_sha rebase_subject record_status
  rebase_sha=$(run_real_git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD 2>/dev/null || true)
  rebase_subject=$(run_real_git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD 2>/dev/null || true)
  if [[ -z "$rebase_sha" || -z "$rebase_subject" ]]; then
    return 0
  fi

  record_status=$(append_agent_conflict_record "$rebase_sha" "$rebase_subject")
  if [[ "$record_status" == "added" ]]; then
    emit_phase_message "rebase" "stdout" "Recorded conflicting commit for $rebase_sha $rebase_subject"
  fi
}

handle_rebase_continue() {
  local before_status after_status continue_exit_code skip_exit_code current_total

  emit_phase_message "rebase" "stdout" "Intercepted git rebase --continue"
  if paused_rebase_is_clean_empty_stop; then
    auto_skip_clean_empty_rebase_stop
    return $?
  fi

  record_current_conflicting_commit

  current_total=""
  if read_rebase_progress_snapshot; then
    current_total="$REBASE_EVENT_TOTAL"
    emit_rebase_event_payload \
      "continue" \
      "$REBASE_EVENT_STEP" \
      "$REBASE_EVENT_TOTAL" \
      "$REBASE_EVENT_SHA" \
      "$REBASE_EVENT_SUBJECT" \
      "${REBASE_EVENT_FILES[@]}"
  fi

  if [[ ! -f "$REBASE_CONTINUE_CHECK_FILE" || ! -s "$REBASE_CONTINUE_CHECK_FILE" ]]; then
    emit_phase_message "rebase" "stdout" "Invoking real git rebase --continue"
    set +e
    run_real_git -C "$WORKSPACE_DIR" "$@"
    continue_exit_code=$?
    set -e
  else
    before_status=$(capture_status_snapshot)
    emit_phase_message "rebase" "stdout" "Running frozen rebase continue check"
    run_continue_check || true
    after_status=$(capture_status_snapshot)

    if [[ $REBASE_CONTINUE_CHECK_EXIT_CODE -ne 0 ]]; then
      emit_rebase_continue_failure "Rebase continue check failed." "$REBASE_CONTINUE_CHECK_EXIT_CODE" "$after_status"
      return 1
    fi

    if [[ "$after_status" != "$before_status" ]]; then
      emit_rebase_continue_failure "Rebase continue check changed workspace state." "0" "$after_status"
      return 1
    fi

    emit_phase_message "rebase" "stdout" "Rebase continue check passed with stable workspace state"
    emit_phase_message "rebase" "stdout" "Invoking real git rebase --continue"
    set +e
    run_real_git -C "$WORKSPACE_DIR" "$@"
    continue_exit_code=$?
    set -e
  fi

  if [[ $continue_exit_code -ne 0 ]] && rebase_in_progress; then
    after_status=$(capture_status_snapshot)
    if [[ -z "$after_status" ]]; then
      if read_rebase_progress_snapshot; then
        current_total="$REBASE_EVENT_TOTAL"
        emit_rebase_event_payload \
          "auto_skip" \
          "$REBASE_EVENT_STEP" \
          "$REBASE_EVENT_TOTAL" \
          "$REBASE_EVENT_SHA" \
          "$REBASE_EVENT_SUBJECT" \
          "${REBASE_EVENT_FILES[@]}"
      fi
      emit_phase_message "rebase" "stdout" "Auto-skipping mechanically empty commit after failed continue"
      set +e
      run_real_git -C "$WORKSPACE_DIR" rebase --skip >/dev/null 2>&1
      skip_exit_code=$?
      set -e
      if [[ $skip_exit_code -eq 0 ]]; then
        emit_post_rebase_transition_events "$current_total"
      fi
      return $skip_exit_code
    fi

    emit_paused_rebase_events
  fi

  if [[ $continue_exit_code -eq 0 ]]; then
    emit_post_rebase_transition_events "$current_total"
  fi

  return $continue_exit_code
}

handle_rebase_skip() {
  local rebase_sha rebase_subject skip_exit_code current_total
  emit_phase_message "rebase" "stdout" "Intercepted git rebase --skip"
  rebase_sha=$(run_real_git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD 2>/dev/null || true)
  rebase_subject=$(run_real_git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD 2>/dev/null || true)
  if [[ -z "$rebase_sha" || -z "$rebase_subject" ]]; then
    if paused_rebase_is_clean_empty_stop; then
      auto_skip_clean_empty_rebase_stop
      return $?
    fi

    emit_phase_message "rebase" "stderr" "Unable to determine REBASE_HEAD for git rebase --skip"
    return 1
  fi

  current_total=""
  if read_rebase_progress_snapshot; then
    current_total="$REBASE_EVENT_TOTAL"
    emit_rebase_event_payload \
      "skip" \
      "$REBASE_EVENT_STEP" \
      "$REBASE_EVENT_TOTAL" \
      "$REBASE_EVENT_SHA" \
      "$REBASE_EVENT_SUBJECT" \
      "${REBASE_EVENT_FILES[@]}"
  fi

  append_agent_skip_record "$rebase_sha" "$rebase_subject"
  emit_phase_message "rebase" "stdout" "Recorded explicit skip for $rebase_sha $rebase_subject"
  set +e
  run_real_git -C "$WORKSPACE_DIR" "$@"
  skip_exit_code=$?
  set -e
  if [[ $skip_exit_code -eq 0 ]]; then
    emit_post_rebase_transition_events "$current_total"
  fi
  return $skip_exit_code
}

stuck_md_has_content() {
  local stuck_file
  stuck_file="$WORKSPACE_DIR/STUCK.md"
  [[ -f "$stuck_file" ]] && grep -q '[^[:space:]]' "$stuck_file"
}

handle_rebase_abort() {
  local abort_exit_code
  emit_phase_message "rebase" "stdout" "Intercepted git rebase --abort"
  if ! stuck_md_has_content; then
    emit_phase_message "rebase" "stderr" "Cannot abort rebase until STUCK.md explains what blocked progress."
    return 1
  fi

  emit_rebase_event_from_snapshot "abort"
  emit_phase_message "rebase" "stdout" "Allowing git rebase --abort because STUCK.md is present"
  set +e
  run_real_git -C "$WORKSPACE_DIR" "$@"
  abort_exit_code=$?
  set -e
  return $abort_exit_code
}

normalize_paused_rebase_command() {
  local arg skip_next_arg
  PAUSED_REBASE_HAS_CONFIG_OVERRIDE=0
  PAUSED_REBASE_NORMALIZED=()
  skip_next_arg=0

  for arg in "$@"; do
    if [[ $skip_next_arg -eq 1 ]]; then
      skip_next_arg=0
      continue
    fi

    case "$arg" in
      -c|--config-env)
        PAUSED_REBASE_HAS_CONFIG_OVERRIDE=1
        skip_next_arg=1
        ;;
      -c*|--config-env=*)
        PAUSED_REBASE_HAS_CONFIG_OVERRIDE=1
        ;;
      --continue|--skip|--abort)
        PAUSED_REBASE_NORMALIZED+=("$arg")
        ;;
      -*)
        ;;
      *)
        PAUSED_REBASE_NORMALIZED+=("$arg")
        ;;
    esac
  done
}

classify_paused_rebase_command() {
  local arg command_name
  PAUSED_REBASE_ACTION=""
  PAUSED_REBASE_HAS_REBASE=0
  normalize_paused_rebase_command "$@"

  command_name="${PAUSED_REBASE_NORMALIZED[0]:-}"
  if [[ -n "$command_name" && "$command_name" != "rebase" ]] && ! is_allowed_paused_git_command "$command_name"; then
    PAUSED_REBASE_ACTION="unsupported"
    return 0
  fi

  for arg in "${PAUSED_REBASE_NORMALIZED[@]}"; do
    if [[ "$arg" == "rebase" ]]; then
      PAUSED_REBASE_HAS_REBASE=1
      break
    fi
  done

  if [[ $PAUSED_REBASE_HAS_REBASE -eq 1 && $PAUSED_REBASE_HAS_CONFIG_OVERRIDE -eq 1 ]]; then
    PAUSED_REBASE_ACTION="unsupported"
    return 0
  fi

  if [[ ${#PAUSED_REBASE_NORMALIZED[@]} -eq 2 && "${PAUSED_REBASE_NORMALIZED[0]}" == "rebase" ]]; then
    case "${PAUSED_REBASE_NORMALIZED[1]}" in
      --continue)
        PAUSED_REBASE_ACTION="continue"
        return 0
        ;;
      --skip)
        PAUSED_REBASE_ACTION="skip"
        return 0
        ;;
      --abort)
        PAUSED_REBASE_ACTION="abort"
        return 0
        ;;
    esac
  fi

  if [[ $PAUSED_REBASE_HAS_REBASE -eq 1 ]]; then
    PAUSED_REBASE_ACTION="unsupported"
  fi
}

format_git_command_for_log() {
  local arg rendered quoted
  rendered="git"

  for arg in "$@"; do
    printf -v quoted '%q' "$arg"
    rendered+=" $quoted"
  done

  printf '%s\n' "$rendered"
}

fail_unsupported_paused_rebase_command() {
  local rejected_command
  rejected_command=$(format_git_command_for_log "$@")

  emit_phase_message "rebase" "stderr" "Unsupported paused rebase command shape: $rejected_command"
  cat >&2 <<'EOF'
git: unsupported paused rebase command.

Forklift is mediating this paused rebase. Do not alter Git behavior or bypass the
wrapper with config overrides, aliases, alternate Git paths, or unsupported Git
commands.

Resolve conflicts, stage the resolved files, then use one of:
  git rebase --continue
  git rebase --skip
  git rebase --abort

If you cannot make progress using the wrapper-mediated flow, write STUCK.md at
the workspace root explaining what blocked progress and what a human should do.
EOF
  printf 'Rejected command: %s\n' "$rejected_command" >&2
  return 1
}
