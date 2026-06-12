#!/usr/bin/env bash
set -euo pipefail

# Rebuild docker/kitchen-sink after editing this file so the image picks up harness changes.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

source "$SCRIPT_DIR/includes/runtime_env.sh"

OPENCODE_BIN=/opt/opencode/bin/opencode
OPENCODE_MODEL=${OPENCODE_MODEL:-}
OPENCODE_VARIANT=${OPENCODE_VARIANT:-}
OPENCODE_AGENT=${OPENCODE_AGENT:-}
OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT:-4096}
OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT:-600}
SETUP_TIMEOUT_SECONDS=${SETUP_TIMEOUT_SECONDS:-600}
MAIN_BRANCH=${FORKLIFT_MAIN_BRANCH:-main}
UPSTREAM_REF="upstream/${MAIN_BRANCH}"
HELPER_BRANCH="upstream-${MAIN_BRANCH//\//-}"

FORK_CONTEXT_PRESENT=0
FORK_CONTEXT_BODY=""
FORK_SETUP_COMMAND=""
FORK_REBASE_CONTINUE_CHECK=""
HARNESS_PHASE=bootstrap

source "$SCRIPT_DIR/includes/common.sh"
source "$SCRIPT_DIR/includes/fork_context.sh"
source "$SCRIPT_DIR/includes/rebase.sh"
source "$SCRIPT_DIR/includes/setup.sh"

main() {
  mkdir -p "$HARNESS_STATE_DIR"
  : >"$CLIENT_LOG"
  : >"$SETUP_LOG"
  write_harness_status "running" "$HARNESS_PHASE" "Harness starting"

  if [[ -z "$OPENCODE_VARIANT" ]]; then
    fail_harness "OPENCODE_VARIANT is required"
  fi
  if [[ -z "$OPENCODE_AGENT" ]]; then
    fail_harness "OPENCODE_AGENT is required"
  fi

  log_client "Harness starting..."
  log_client "  WORKSPACE_DIR=$WORKSPACE_DIR"
  log_client "  HARNESS_STATE_DIR=$HARNESS_STATE_DIR"
  log_client "  INSTRUCTIONS_FILE=$INSTRUCTIONS_FILE"
  log_client "  FORK_CONTEXT_FILE=$FORK_CONTEXT_FILE"
  log_client "  SETUP_LOG=$SETUP_LOG"
  log_client "  CLIENT_LOG=$CLIENT_LOG"
  log_client "  OPENCODE_BIN=$OPENCODE_BIN"
  log_client "  OPENCODE_MODEL=${OPENCODE_MODEL:-(default)}"
  log_client "  OPENCODE_VARIANT=$OPENCODE_VARIANT"
  log_client "  OPENCODE_AGENT=$OPENCODE_AGENT"
  log_client "  OPENCODE_SERVER_PORT=$OPENCODE_SERVER_PORT"
  log_client "  OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT}s"
  log_client "  SETUP_TIMEOUT_SECONDS=${SETUP_TIMEOUT_SECONDS}s"
  log_client "  FORKLIFT_MAIN_BRANCH=$MAIN_BRANCH"
  log_client "  FORKLIFT_RUN_ID=${FORKLIFT_RUN_ID:-unknown}"
  log_client "  FORKLIFT_REBASE_EVENTS_SOCK=${FORKLIFT_REBASE_EVENTS_SOCK:-unset}"
  log_client "  FORKLIFT_REBASE_CONTROL_SOCK=${FORKLIFT_REBASE_CONTROL_SOCK:-unset}"
  log_client "  FORKLIFT_AGENT_LIFETIME=${FORKLIFT_AGENT_LIFETIME:-conflict}"

  log_client "Configuring Forklift git identity"
  git config --global user.name "$FORKLIFT_GIT_USER_NAME"
  git config --global user.email "$FORKLIFT_GIT_USER_EMAIL"
  log_client "  git user.name=$(git config --global user.name)"
  log_client "  git user.email=$(git config --global user.email)"
  configure_git_lfs_filters

  HARNESS_PHASE=context
  if ! parse_fork_context; then
    fail_harness "Invalid FORK.md front matter; fix format and retry"
  fi
  write_rebase_continue_check_file
  export FORK_REBASE_CONTINUE_CHECK
  if ! enable_rebase_mediation; then
    fail_harness "Unable to resolve real git binary before enabling rebase mediation"
  fi

  HARNESS_PHASE=setup
  if ! run_setup_command; then
    fail_harness "Setup command failed before agent launch"
  fi

  HARNESS_PHASE=rebase
  write_instructions

  # Hand the rebase + agent lifecycle to the Python orchestrator. It drives the
  # initial rebase, runs the per-mode agent loop, and is the sole writer of
  # harness-state/rebase-report.json and the terminal harness status.
  HARNESS_PHASE=agent
  export OPENCODE_BIN UPSTREAM_REF
  export FORKLIFT_MAIN_BRANCH="$MAIN_BRANCH"
  local harness_py
  harness_py=$(cd -- "$SCRIPT_DIR" && pwd)/py
  exec env PYTHONPATH="$harness_py" python3 -m forklift_harness.orchestrate
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
