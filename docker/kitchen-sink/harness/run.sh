#!/usr/bin/env bash
set -euo pipefail

INSTRUCTIONS_FILE=/harness-state/instructions.txt
FORK_CONTEXT_FILE=/harness-state/fork-context.md
CLIENT_LOG=${OPENCODE_CLIENT_LOG:-/harness-state/opencode-client.log}
OPENCODE_BIN=/opt/opencode/bin/opencode
OPENCODE_MODEL=${OPENCODE_MODEL:-}
OPENCODE_VARIANT=${OPENCODE_VARIANT:?OPENCODE_VARIANT is required}
OPENCODE_AGENT=${OPENCODE_AGENT:?OPENCODE_AGENT is required}
OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT:-4096}
OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT:-}

log_client() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$1" >>"$CLIENT_LOG"
}

mkdir -p /harness-state
: >"$CLIENT_LOG"

log_client "Agent Starting..."
log_client "  INSTRUCTIONS_FILE=$INSTRUCTIONS_FILE"
log_client "  FORK_CONTEXT_FILE=$FORK_CONTEXT_FILE"
log_client "  CLIENT_LOG=$CLIENT_LOG"
log_client "  OPENCODE_BIN=$OPENCODE_BIN"
log_client "  OPENCODE_MODEL=${OPENCODE_MODEL:-(default)}"
log_client "  OPENCODE_VARIANT=$OPENCODE_VARIANT"
log_client "  OPENCODE_AGENT=$OPENCODE_AGENT"
log_client "  OPENCODE_SERVER_PORT=$OPENCODE_SERVER_PORT"
log_client "  OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT:-(none)}"

default_instructions() {
  cat <<'TXT'
Forklift Agent Instructions
===========================
1. Merge `upstream/main` into `main` inside /workspace.
2. Resolve conflicts carefully; prefer upstream changes when unsure and summarize any deviations.
3. Run the project's primary tests (npm test, pytest, cargo test, etc.) when time allows.
4. Craft meaningful commits summarizing the merge and any fixes required for a clean build.
5. If you cannot finish safely within 8 minutes, write STUCK.md explaining what you tried, what failed, and the help you need.
TXT
}

print_header() {
  printf '== %s ==\n' "$1"
}

write_instructions() {
  print_header "Instructions" | tee "$INSTRUCTIONS_FILE"
  default_instructions | tee -a "$INSTRUCTIONS_FILE"
  print_header "FORK.md Context" | tee -a "$INSTRUCTIONS_FILE"
  if [[ -f /workspace/FORK.md ]]; then
    cp /workspace/FORK.md "$FORK_CONTEXT_FILE"
    cat /workspace/FORK.md | tee -a "$INSTRUCTIONS_FILE"
  else
    printf '(none provided)\n' | tee -a "$INSTRUCTIONS_FILE"
    printf 'No FORK.md context provided.\n' >"$FORK_CONTEXT_FILE"
  fi
}

write_instructions

AGENT_PAYLOAD=$(cat "$FORK_CONTEXT_FILE" 2>/dev/null || printf 'No FORK context provided.')

command_args=(
  "$OPENCODE_BIN" run
  --attach "http://127.0.0.1:$OPENCODE_SERVER_PORT"
)
if [[ -n "$OPENCODE_MODEL" ]]; then
  command_args+=(--model "$OPENCODE_MODEL")
fi
command_args+=(
  --variant "$OPENCODE_VARIANT"
  --agent "$OPENCODE_AGENT"
  --instructions-file "$INSTRUCTIONS_FILE"
)

if [[ -n "$OPENCODE_TIMEOUT" ]]; then
  command_args+=(--timeout "$OPENCODE_TIMEOUT")
fi

print_header "Agent Command" | tee -a "$INSTRUCTIONS_FILE"
printf '%q ' "${command_args[@]}" '<fork-context>' | tee -a "$INSTRUCTIONS_FILE"
printf '\n' | tee -a "$INSTRUCTIONS_FILE"

log_model=${OPENCODE_MODEL:-"(default)"}
log_client "Launching OpenCode client (model=$log_model variant=$OPENCODE_VARIANT agent=$OPENCODE_AGENT)"
if "${command_args[@]}" "$AGENT_PAYLOAD" >>"$CLIENT_LOG" 2>&1; then
  log_client "OpenCode client exited cleanly"
else
  status=$?
  log_client "OpenCode client failed with status $status"
  echo "OpenCode client failed; see $CLIENT_LOG" >&2
  exit $status
fi
