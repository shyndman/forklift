#!/usr/bin/env bash
# Setup command execution helpers for harness bootstrap.

# Emit setup preflight diagnostics to simplify bootstrap failure triage.
log_setup_diagnostics() {
  local line_number command_line
  line_number=1

  {
    print_header "Setup Diagnostics"
    printf 'timestamp=%s\n' "$(date --iso-8601=seconds)"
    printf 'user=%s\n' "$(id -un 2>/dev/null || echo unknown)"
    printf 'uid_gid=%s\n' "$(id -u 2>/dev/null || echo unknown):$(id -g 2>/dev/null || echo unknown)"
    printf 'pwd=%s\n' "$(pwd)"
    printf 'workspace_dir=%s\n' "$WORKSPACE_DIR"
    printf 'setup_log=%s\n' "$SETUP_LOG"
    printf 'timeout_seconds=%s\n' "$SETUP_TIMEOUT_SECONDS"
    printf 'home=%s\n' "${HOME:-<unset>}"
    printf 'TMPDIR=%s\n' "${TMPDIR:-<unset>}"
    printf 'TEMP=%s\n' "${TEMP:-<unset>}"
    printf 'TMP=%s\n' "${TMP:-<unset>}"
    print_header "Setup Command (Line Numbered)"
    while IFS= read -r command_line; do
      printf '%4d | %s\n' "$line_number" "$command_line"
      line_number=$((line_number + 1))
    done <<<"$FORK_SETUP_COMMAND"
  } >>"$SETUP_LOG"

  log_tempdir_diagnostics
}

# Probe temp directories used by setup tools so tempdir failures are actionable.
log_tempdir_diagnostics() {
  local dir
  local probe_file
  local probe_status
  local -a candidate_dirs
  candidate_dirs=()

  if [[ -n "${TMPDIR:-}" ]]; then
    candidate_dirs+=("$TMPDIR")
  fi
  if [[ -n "${TEMP:-}" ]]; then
    candidate_dirs+=("$TEMP")
  fi
  if [[ -n "${TMP:-}" ]]; then
    candidate_dirs+=("$TMP")
  fi
  candidate_dirs+=("/tmp")

  {
    print_header "Setup Tempdir Probes"
  } >>"$SETUP_LOG"

  for dir in "${candidate_dirs[@]}"; do
    {
      printf 'dir=%s\n' "$dir"
      if [[ ! -e "$dir" ]]; then
        printf '  exists=no\n'
        continue
      fi
      if [[ ! -d "$dir" ]]; then
        printf '  directory=no\n'
        continue
      fi

      stat -c '  stat=%a %U:%G %n' "$dir"
      if [[ -w "$dir" ]]; then
        printf '  writable=yes\n'
      else
        printf '  writable=no\n'
      fi

      probe_file=$(mktemp "$dir/forklift-setup-probe.XXXXXX" 2>/dev/null)
      probe_status=$?
      if [[ $probe_status -eq 0 ]]; then
        printf '  mktemp=ok path=%s\n' "$probe_file"
        rm -f "$probe_file"
      else
        printf '  mktemp=failed exit_code=%s\n' "$probe_status"
      fi
    } >>"$SETUP_LOG" 2>&1
  done
}

# Execute optional bootstrap in workspace and gate agent launch on deterministic, clean outcomes.
run_setup_command() {
  if [[ -z "$FORK_SETUP_COMMAND" ]]; then
    log_client "No setup command declared in FORK.md front matter"
    return 0
  fi

  log_client "Running setup command from FORK.md front matter"
  {
    print_header "Setup Command"
    printf '%s\n' "$FORK_SETUP_COMMAND"
  } >>"$SETUP_LOG"
  log_setup_diagnostics
  print_header "Setup Output" >>"$SETUP_LOG"

  local setup_exit_code
  setup_exit_code=0
  set +e
  (
    cd "$WORKSPACE_DIR"
    timeout "$SETUP_TIMEOUT_SECONDS" bash -lc "$FORK_SETUP_COMMAND"
  ) >>"$SETUP_LOG" 2>&1
  setup_exit_code=$?
  set -e

  if [[ $setup_exit_code -eq 124 ]]; then
    log_client "Setup command timed out after ${SETUP_TIMEOUT_SECONDS}s"
    printf '\nSetup command timed out after %ss\n' "$SETUP_TIMEOUT_SECONDS" >>"$SETUP_LOG"
    return 1
  fi
  if [[ $setup_exit_code -ne 0 ]]; then
    log_client "Setup command failed with exit code $setup_exit_code"
    printf '\nSetup command failed with exit code %s\n' "$setup_exit_code" >>"$SETUP_LOG"
    return 1
  fi

  local dirty_status
  dirty_status=$(git -C "$WORKSPACE_DIR" status --porcelain --untracked-files=no)
  if [[ -n "$dirty_status" ]]; then
    log_client "Setup command left tracked git changes; failing closed"
    {
      print_header "Tracked Changes After Setup"
      printf '%s\n' "$dirty_status"
    } >>"$SETUP_LOG"
    return 1
  fi

  log_client "Setup command completed successfully"
  return 0
}
