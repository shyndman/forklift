#!/usr/bin/env bash
set -euo pipefail

# Rebuild docker/kitchen-sink after editing this file so the image picks up harness changes.

WORKSPACE_DIR=${WORKSPACE_DIR:-/workspace}
HARNESS_STATE_DIR=${HARNESS_STATE_DIR:-/harness-state}
INSTRUCTIONS_FILE=${INSTRUCTIONS_FILE:-$HARNESS_STATE_DIR/instructions.txt}
FORK_CONTEXT_FILE=${FORK_CONTEXT_FILE:-$HARNESS_STATE_DIR/fork-context.md}
SETUP_LOG=${SETUP_LOG:-$HARNESS_STATE_DIR/setup.log}
CLIENT_LOG=${OPENCODE_CLIENT_LOG:-$HARNESS_STATE_DIR/opencode-client.log}
HARNESS_STATUS_FILE=${HARNESS_STATUS_FILE:-$HARNESS_STATE_DIR/harness-status.txt}
REBASE_CONTINUE_CHECK_FILE=${REBASE_CONTINUE_CHECK_FILE:-$HARNESS_STATE_DIR/rebase-continue-check.sh}
REBASE_SKIPPED_COMMITS_FILE=${REBASE_SKIPPED_COMMITS_FILE:-$HARNESS_STATE_DIR/rebase-skipped-commits.json}
REBASE_CONFLICTING_COMMITS_FILE=${REBASE_CONFLICTING_COMMITS_FILE:-$HARNESS_STATE_DIR/rebase-conflicting-commits.json}
OPENCODE_BIN=/opt/opencode/bin/opencode
OPENCODE_MODEL=${OPENCODE_MODEL:-}
OPENCODE_VARIANT=${OPENCODE_VARIANT:-}
OPENCODE_AGENT=${OPENCODE_AGENT:-}
OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT:-4096}
OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT:-600}
SETUP_TIMEOUT_SECONDS=${SETUP_TIMEOUT_SECONDS:-180}
MAIN_BRANCH=${FORKLIFT_MAIN_BRANCH:-main}
UPSTREAM_REF="upstream/${MAIN_BRANCH}"
HELPER_BRANCH="upstream-${MAIN_BRANCH//\//-}"

export WORKSPACE_DIR HARNESS_STATE_DIR INSTRUCTIONS_FILE FORK_CONTEXT_FILE SETUP_LOG CLIENT_LOG HARNESS_STATUS_FILE
export REBASE_CONTINUE_CHECK_FILE REBASE_SKIPPED_COMMITS_FILE REBASE_CONFLICTING_COMMITS_FILE

FORK_CONTEXT_PRESENT=0
FORK_CONTEXT_BODY=""
FORK_SETUP_COMMAND=""
FORK_REBASE_CONTINUE_CHECK=""
HARNESS_PHASE=bootstrap

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

source "$SCRIPT_DIR/includes/common.sh"
source "$SCRIPT_DIR/includes/fork_context.sh"
source "$SCRIPT_DIR/includes/rebase.sh"
source "$SCRIPT_DIR/includes/setup.sh"
source "$SCRIPT_DIR/includes/agent.sh"

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

  log_client "Configuring Forklift git identity"
  git config --global user.name "Forklift Agent"
  git config --global user.email forklift@github.com
  log_client "  git user.name=$(git config --global user.name)"
  log_client "  git user.email=$(git config --global user.email)"
  configure_git_lfs_filters

  HARNESS_PHASE=context
  if ! parse_fork_context; then
    fail_harness "Invalid FORK.md front matter; fix format and retry"
  fi
  initialize_rebase_skipped_commits_file
  initialize_rebase_conflicting_commits_file
  write_rebase_continue_check_file
  export FORK_REBASE_CONTINUE_CHECK
  if ! resolve_real_git_bin; then
    fail_harness "Unable to resolve real git binary before enabling rebase mediation"
  fi
  prepend_git_wrapper_path

  HARNESS_PHASE=setup
  if ! run_setup_command; then
    fail_harness "Setup command failed before agent launch"
  fi

  HARNESS_PHASE=rebase
  if ! start_initial_rebase; then
    fail_harness "Initial rebase failed before agent launch" "$HARNESS_PHASE"
  fi

  case "$INITIAL_REBASE_RESULT" in
    completed)
      write_harness_status "completed" "$HARNESS_PHASE" "Initial rebase completed cleanly; agent launch skipped"
      return 0
      ;;
    paused)
      write_instructions
      build_agent_payload
      HARNESS_PHASE=agent
      if ! launch_agent; then
        fail_harness "Agent run failed; inspect $CLIENT_LOG" "$HARNESS_PHASE"
      fi
      write_harness_status "completed" "$HARNESS_PHASE" "Agent completed successfully"
      ;;
    *)
      fail_harness "Initial rebase returned unexpected outcome '$INITIAL_REBASE_RESULT'" "$HARNESS_PHASE"
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
