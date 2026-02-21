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
OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT:-210}
MAIN_BRANCH=${FORKLIFT_MAIN_BRANCH:-main}
UPSTREAM_REF="upstream/${MAIN_BRANCH}"
HELPER_BRANCH="upstream-${MAIN_BRANCH//\//-}"

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
log_client "  OPENCODE_TIMEOUT=${OPENCODE_TIMEOUT}s"

default_instructions() {
  local upstream_sha upstream_date main_sha
  upstream_sha=$(git -C /workspace rev-parse --short upstream/main 2>/dev/null || echo "unknown")
  upstream_date=$(git -C /workspace log --format='%ar' upstream/main -1 2>/dev/null || echo "unknown")
  main_sha=$(git -C /workspace rev-parse --short main 2>/dev/null || echo "unknown")

  cat <<TXT
You are the Forklift merge agent. Your job is to merge upstream changes into
this fork and leave the repository in a working state, while preserving the
functionality of both the fork, and the upstream project.

== Environment ==
- Working directory: /workspace (a git repository)
- The \`upstream\` remote is already configured and fetched; do not add or
  modify any git remotes
- upstream/main is at $upstream_sha ($upstream_date)
- local main is at $main_sha — use this as a reset point if the rebase goes
  badly wrong: \`git checkout main && git reset --hard $main_sha\`
- Use git to inspect history, branches, and conflicts

== Task ==
1. Run \`git rebase upstream/main\` on the local \`main\` branch only.
   No other branches need attention.
2. Resolve any conflicts. Your goal is to preserve the functionality of both the upstream and main branches. If this seems impossible, write a STUCK.md as described below.
	 Refer to the FORK.md if supplied to understand intentional fork customizations worth
   preserving.
3. Run the project's primary test suite (e.g. npm test, pytest, cargo test)
   and fix any failures introduced by the merge.
4. Commit the result with a clear message describing the merge and any fixes.

== If you get stuck ==
Write STUCK.md at the root of /workspace describing what you tried, what
failed, and what a human would need to do to finish.

== When done ==
Write DONE.md at the root of /workspace summarizing the decisions you made:
- Which conflicts you encountered and how you resolved them
- What fork customizations you identified and preserved
- What upstream changes you accepted and why
- Any test failures you fixed and how
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

AGENT_PAYLOAD=$(
  cat "$INSTRUCTIONS_FILE"
  printf '\n\n'
  cat "$FORK_CONTEXT_FILE" 2>/dev/null || printf ''
)

command_args=(
  "$OPENCODE_BIN" run
  --attach "http://127.0.0.1:$OPENCODE_SERVER_PORT"
	--log-level DEBUG
	--format json
	--dir /workspace
)
if [[ -n "$OPENCODE_MODEL" ]]; then
  command_args+=(--model "$OPENCODE_MODEL")
fi
command_args+=(
  --variant "$OPENCODE_VARIANT"
	# Temporarily omitting this as it has a bug
	# TODO(https://github.com/anomalyco/opencode/issues/6489): Wait until resolved
	# --agent "$OPENCODE_AGENT"
)

print_header "Agent Command" | tee -a "$INSTRUCTIONS_FILE"
printf '%q ' "${command_args[@]}" '<fork-context>' | tee -a "$INSTRUCTIONS_FILE"
printf '\n' | tee -a "$INSTRUCTIONS_FILE"

DONE_FILE=/workspace/DONE.md
STUCK_FILE=/workspace/STUCK.md
DEADLINE=$(( $(date +%s) + OPENCODE_TIMEOUT ))

log_model=${OPENCODE_MODEL:-"(default)"}
log_client "Launching OpenCode client (model=$log_model variant=$OPENCODE_VARIANT agent=$OPENCODE_AGENT)"

attempt=0
while true; do
  remaining=$(( DEADLINE - $(date +%s) ))
  if [[ $remaining -le 0 ]]; then
    log_client "Deadline reached; exiting"
    echo "Agent timed out; see $CLIENT_LOG" >&2
    exit 1
  fi

  attempt=$((attempt + 1))
  if [[ $attempt -eq 1 ]]; then
    log_client "Attempt $attempt (${remaining}s remaining)"
    timeout "$remaining" "${command_args[@]}" "$AGENT_PAYLOAD" >>"$CLIENT_LOG" 2>&1 || true
  else
    log_client "Attempt $attempt (${remaining}s remaining, continuing)"
    timeout "$remaining" "${command_args[@]}" --continue "Continue where you left off." >>"$CLIENT_LOG" 2>&1 || true
  fi

  if [[ -f "$DONE_FILE" ]]; then
    log_client "Agent completed successfully (DONE.md present)"
    exit 0
  fi

  if [[ -f "$STUCK_FILE" ]]; then
    log_client "Agent reported stuck (STUCK.md present)"
    echo "Agent is stuck; see $STUCK_FILE and $CLIENT_LOG" >&2
    exit 1
  fi

  log_client "Agent exited without signalling completion; retrying"
done
