#!/usr/bin/env bash
set -euo pipefail

# Rebuild docker/kitchen-sink after editing this file so the image picks up harness changes.

WORKSPACE_DIR=${WORKSPACE_DIR:-/workspace}
HARNESS_STATE_DIR=${HARNESS_STATE_DIR:-/harness-state}
INSTRUCTIONS_FILE=${INSTRUCTIONS_FILE:-$HARNESS_STATE_DIR/instructions.txt}
FORK_CONTEXT_FILE=${FORK_CONTEXT_FILE:-$HARNESS_STATE_DIR/fork-context.md}
SETUP_LOG=${SETUP_LOG:-$HARNESS_STATE_DIR/setup.log}
CLIENT_LOG=${OPENCODE_CLIENT_LOG:-$HARNESS_STATE_DIR/opencode-client.log}
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

FORK_CONTEXT_PRESENT=0
FORK_CONTEXT_BODY=""
FORK_SETUP_COMMAND=""

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

source "$SCRIPT_DIR/includes/common.sh"
source "$SCRIPT_DIR/includes/fork_context.sh"
source "$SCRIPT_DIR/includes/setup.sh"
source "$SCRIPT_DIR/includes/agent.sh"

main() {
  mkdir -p "$HARNESS_STATE_DIR"
  : >"$CLIENT_LOG"
  : >"$SETUP_LOG"

  if [[ -z "$OPENCODE_VARIANT" ]]; then
    fail_harness "OPENCODE_VARIANT is required"
  fi
  if [[ -z "$OPENCODE_AGENT" ]]; then
    fail_harness "OPENCODE_AGENT is required"
  fi

  log_client "Agent Starting..."
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

  log_client "Configuring Forklift Agent git identity"
  git config --global user.name "Forklift Agent"
  git config --global user.email forklift@github.com
  log_client "  git user.name=$(git config --global user.name)"
  log_client "  git user.email=$(git config --global user.email)"

  if ! parse_fork_context; then
    fail_harness "Invalid FORK.md front matter; fix format and retry"
  fi
  if ! run_setup_command; then
    fail_harness "Setup command failed before agent launch; inspect $SETUP_LOG"
  fi

  write_instructions
  build_agent_payload
  launch_agent
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
