#!/usr/bin/env bash
# Bash-only rebase mediation helpers. All introspection, policy, event emission,
# and the agent/orchestrator lifecycle now live in the Python package under
# `harness/py/forklift_harness/`. This file keeps only the bits bash still needs:
# resolving the real git binary, enabling the PATH shim, detecting an in-progress
# rebase, and materializing the fork-supplied frozen continue check.

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

rebase_in_progress() {
  [[ -d "$WORKSPACE_DIR/.git/rebase-merge" || -d "$WORKSPACE_DIR/.git/rebase-apply" ]]
}

write_rebase_continue_check_file() {
  if [[ -z "$FORK_REBASE_CONTINUE_CHECK" ]]; then
    rm -f "$REBASE_CONTINUE_CHECK_FILE"
    return 0
  fi

  # The first line is a shebang and every other harness-injected preamble line
  # carries the trailing marker below, so the Python reader can strip the preamble
  # structurally instead of by a hard-coded line count. Keep the marker string in
  # sync with rebase_state.CONTINUE_CHECK_PREAMBLE_MARKER.
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -euo pipefail  # %s\n' 'forklift:continue-check-preamble'
    printf '%s\n' "$FORK_REBASE_CONTINUE_CHECK"
  } >"$REBASE_CONTINUE_CHECK_FILE"
  chmod 0555 "$REBASE_CONTINUE_CHECK_FILE"
}
