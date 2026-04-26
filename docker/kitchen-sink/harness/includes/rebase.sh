#!/usr/bin/env bash
# Harness-owned rebase mediation helpers.

REBASE_CONTINUE_CHECK_EXIT_CODE=0
REBASE_CONTINUE_CHECK_STDOUT=""
REBASE_CONTINUE_CHECK_STDERR=""
INITIAL_REBASE_RESULT=""

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
  local harness_root wrapper_dir
  harness_root=${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}
  wrapper_dir="$harness_root/includes/bin"
  PATH="$wrapper_dir:$PATH"
  export PATH
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

run_real_git() {
  "$REAL_GIT_BIN" "$@"
}

start_initial_rebase() {
  local rebase_exit_code

  INITIAL_REBASE_RESULT=""
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
    return 0
  fi

  if rebase_in_progress; then
    INITIAL_REBASE_RESULT="paused"
    record_current_conflicting_commit
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
  local before_status after_status continue_exit_code skip_exit_code

  emit_phase_message "rebase" "stdout" "Intercepted git rebase --continue"
  record_current_conflicting_commit

  if [[ ! -f "$REBASE_CONTINUE_CHECK_FILE" || ! -s "$REBASE_CONTINUE_CHECK_FILE" ]]; then
    emit_phase_message "rebase" "stdout" "Invoking real git rebase --continue"
    run_real_git -C "$WORKSPACE_DIR" "$@"
    return $?
  fi

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

  if [[ $continue_exit_code -ne 0 ]] && rebase_in_progress; then
    after_status=$(capture_status_snapshot)
    if [[ -z "$after_status" ]]; then
      emit_phase_message "rebase" "stdout" "Auto-skipping mechanically empty commit after failed continue"
      set +e
      run_real_git -C "$WORKSPACE_DIR" rebase --skip >/dev/null 2>&1
      skip_exit_code=$?
      set -e
      return $skip_exit_code
    fi
  fi

  return $continue_exit_code
}

handle_rebase_skip() {
  local rebase_sha rebase_subject skip_exit_code
  emit_phase_message "rebase" "stdout" "Intercepted git rebase --skip"
  rebase_sha=$(run_real_git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD 2>/dev/null || true)
  rebase_subject=$(run_real_git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD 2>/dev/null || true)
  if [[ -z "$rebase_sha" || -z "$rebase_subject" ]]; then
    emit_phase_message "rebase" "stderr" "Unable to determine REBASE_HEAD for git rebase --skip"
    return 1
  fi

  append_agent_skip_record "$rebase_sha" "$rebase_subject"
  emit_phase_message "rebase" "stdout" "Recorded explicit skip for $rebase_sha $rebase_subject"
  set +e
  run_real_git -C "$WORKSPACE_DIR" "$@"
  skip_exit_code=$?
  set -e
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

  emit_phase_message "rebase" "stdout" "Allowing git rebase --abort because STUCK.md is present"
  set +e
  run_real_git -C "$WORKSPACE_DIR" "$@"
  abort_exit_code=$?
  set -e
  return $abort_exit_code
}

classify_paused_rebase_command() {
  local arg
  PAUSED_REBASE_ACTION=""
  PAUSED_REBASE_HAS_REBASE=0
  PAUSED_REBASE_NORMALIZED=()

  for arg in "$@"; do
    case "$arg" in
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

  for arg in "${PAUSED_REBASE_NORMALIZED[@]}"; do
    if [[ "$arg" == "rebase" ]]; then
      PAUSED_REBASE_HAS_REBASE=1
      break
    fi
  done

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

fail_unsupported_paused_rebase_command() {
  emit_phase_message "rebase" "stderr" "Unsupported paused rebase command shape"
  printf 'git: unsupported paused rebase command; use git rebase --continue, --skip, or --abort\n' >&2
  return 1
}
