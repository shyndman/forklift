#!/usr/bin/env bash
# OpenCode launch and retry loop helpers for harness runtime.

launch_agent() {
  local command_args done_file stuck_file deadline log_model attempt remaining
  command_args=(
    "$OPENCODE_BIN" run
    --attach "http://127.0.0.1:$OPENCODE_SERVER_PORT"
    --log-level DEBUG
    --format json
    --dir "$WORKSPACE_DIR"
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

  done_file="$WORKSPACE_DIR/DONE.md"
  stuck_file="$WORKSPACE_DIR/STUCK.md"
  deadline=$(($(date +%s) + OPENCODE_TIMEOUT))

  log_model=${OPENCODE_MODEL:-"(default)"}
  log_client "Launching OpenCode client (model=$log_model variant=$OPENCODE_VARIANT agent=$OPENCODE_AGENT)"

  attempt=0
  while true; do
    remaining=$((deadline - $(date +%s)))
    if [[ $remaining -le 0 ]]; then
      log_client "Deadline reached; exiting"
      printf 'Agent timed out; see %s\n' "$CLIENT_LOG" >&2
      return 1
    fi

    attempt=$((attempt + 1))
    if [[ $attempt -eq 1 ]]; then
      log_client "Attempt $attempt (${remaining}s remaining)"
      timeout "$remaining" "${command_args[@]}" "$AGENT_PAYLOAD" >>"$CLIENT_LOG" 2>&1 || true
    else
      log_client "Attempt $attempt (${remaining}s remaining, continuing)"
      timeout "$remaining" "${command_args[@]}" --continue "Continue where you left off." >>"$CLIENT_LOG" 2>&1 || true
    fi

    if [[ -f "$done_file" ]]; then
      log_client "Agent completed successfully (DONE.md present)"
      return 0
    fi

    if [[ -f "$stuck_file" ]]; then
      log_client "Agent reported stuck (STUCK.md present)"
      printf 'Agent is stuck; see %s and %s\n' "$stuck_file" "$CLIENT_LOG" >&2
      return 1
    fi

    log_client "Agent exited without signalling completion; retrying"
  done
}
