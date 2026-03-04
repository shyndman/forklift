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

log_client() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$1" >>"$CLIENT_LOG"
}

print_header() {
  printf '== %s ==\n' "$1"
}

fail_harness() {
  local message
  message="$1"
  log_client "$message"
  printf '%s\n' "$message" >&2
  exit 1
}

# Keep this guidance stable so the API contract with the merge agent remains predictable.
default_instructions() {
  local upstream_sha upstream_date main_sha branch_name upstream_ref helper_branch
  branch_name="$MAIN_BRANCH"
  upstream_ref="$UPSTREAM_REF"
  helper_branch="$HELPER_BRANCH"
  upstream_sha=$(git -C "$WORKSPACE_DIR" rev-parse --short "$upstream_ref" 2>/dev/null || echo "unknown")
  upstream_date=$(git -C "$WORKSPACE_DIR" log --format='%ar' "$upstream_ref" -1 2>/dev/null || echo "unknown")
  main_sha=$(git -C "$WORKSPACE_DIR" rev-parse --short "$branch_name" 2>/dev/null || echo "unknown")

  cat <<TXT
You are the Forklift merge agent. Your job is to merge upstream changes into
this fork and leave the repository preserving the functionality of both the
upstream $upstream_ref branch and the fork's $branch_name branch.

== Environment ==
|- Working directory: $WORKSPACE_DIR (a git repository)
|- Git remotes were stripped before the run; \`git remote -v\` will show nothing.
  Do not add new remotes. Forklift seeded \`refs/remotes/$upstream_ref\`
  (helper branch \`$helper_branch\`) so \`git rebase $upstream_ref\` works without
  extra setup.
|- $upstream_ref is at $upstream_sha ($upstream_date)
|- local $branch_name is at $main_sha — use this as a reset point if the rebase goes
  badly wrong: \`git checkout $branch_name && git reset --hard $main_sha\`
|- Use git to inspect history, branches, and conflicts
|- Do not attempt to run tests or build the code; focus on the git operations and commit the final result.

== Task ==
1. Run \`git rebase $upstream_ref\` on the local \`$branch_name\` branch only.
   No other branches need attention.
2. Resolve any conflicts. Your goal is to preserve the functionality of both $upstream_ref and $branch_name. If this seems impossible, write a STUCK.md as described below.
   Refer to the FORK.md if supplied to understand intentional fork customizations worth
   preserving.
3. You MUST continue the rebase until it is complete. _Verify this!_
4. Commit the result with a clear message describing the merge and any fixes.

== If you get stuck ==
Write STUCK.md at the root of $WORKSPACE_DIR describing what you tried, what
failed, and what a human would need to do to finish.

== When done ==
Write DONE.md at the root of $WORKSPACE_DIR summarizing the decisions you made:
- Which conflicts you encountered and how you resolved them
- What fork customizations you identified and preserved
- What upstream changes you accepted and why
- Any test failures you fixed and how
TXT
}

# Parse optional FORK front matter into setup metadata while keeping agent-visible context body-only.
parse_fork_context() {
  local fork_file setup_tmp body_tmp
  fork_file="$WORKSPACE_DIR/FORK.md"
  FORK_CONTEXT_PRESENT=0
  FORK_CONTEXT_BODY=""
  FORK_SETUP_COMMAND=""

  if [[ ! -f "$fork_file" ]]; then
    return 0
  fi

  FORK_CONTEXT_PRESENT=1
  setup_tmp=$(mktemp)
  body_tmp=$(mktemp)

  if ! python3 - "$fork_file" "$setup_tmp" "$body_tmp" <<'PY'; then
from pathlib import Path
import sys

fork_path = Path(sys.argv[1])
setup_out = Path(sys.argv[2])
body_out = Path(sys.argv[3])

content = fork_path.read_text(encoding="utf-8")
setup = ""
body = content

lines = content.splitlines()
if lines and lines[0] == "---":
    closing = None
    for idx, line in enumerate(lines[1:], start=1):
        if line == "---":
            closing = idx
            break
    if closing is None:
        raise SystemExit("FORK.md front matter is malformed: missing closing '---' delimiter.")

    front_lines = lines[1:closing]
    body = "\n".join(lines[closing + 1 :])

    idx = 0
    parsed_setup = None
    while idx < len(front_lines):
        line = front_lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        if not line.startswith("setup:"):
            raise SystemExit(
                f"FORK.md front matter is malformed: unsupported key line '{line}'. Only 'setup' is allowed."
            )
        if parsed_setup is not None:
            raise SystemExit("FORK.md front matter is malformed: duplicate 'setup' key.")
        value = line[len("setup:") :].strip()
        if value in ("|", "|-"):
            idx += 1
            block: list[str] = []
            while idx < len(front_lines):
                block_line = front_lines[idx]
                if block_line.startswith("  "):
                    block.append(block_line[2:])
                    idx += 1
                    continue
                if block_line.strip() == "":
                    block.append("")
                    idx += 1
                    continue
                raise SystemExit(
                    "FORK.md front matter is malformed: setup block values must be indented by two spaces."
                )
            if not block:
                raise SystemExit(
                    "FORK.md front matter is malformed: setup block string must include at least one command line."
                )
            parsed_setup = "\n".join(block).rstrip("\n")
            break
        if value == "":
            raise SystemExit(
                "FORK.md front matter is malformed: setup must be a non-empty string or block string."
            )
        parsed_setup = value
        idx += 1

    setup = parsed_setup or ""

setup_out.write_text(setup, encoding="utf-8")
body_out.write_text(body, encoding="utf-8")
PY
    rm -f "$setup_tmp" "$body_tmp"
    return 1
  fi

  FORK_SETUP_COMMAND=$(cat "$setup_tmp")
  FORK_CONTEXT_BODY=$(cat "$body_tmp")
  rm -f "$setup_tmp" "$body_tmp"
  return 0
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
    print_header "Setup Output"
  } >>"$SETUP_LOG"

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

# Render harness instructions and fork context files using front-matter-stripped body content.
write_instructions() {
  print_header "Instructions" | tee "$INSTRUCTIONS_FILE"
  default_instructions | tee -a "$INSTRUCTIONS_FILE"
  print_header "FORK.md Context" | tee -a "$INSTRUCTIONS_FILE"
  if [[ $FORK_CONTEXT_PRESENT -eq 1 ]]; then
    printf '%s' "$FORK_CONTEXT_BODY" >"$FORK_CONTEXT_FILE"
    if [[ -n "$FORK_CONTEXT_BODY" ]]; then
      printf '%s\n' "$FORK_CONTEXT_BODY" | tee -a "$INSTRUCTIONS_FILE"
    else
      printf '(provided but empty after front matter stripping)\n' | tee -a "$INSTRUCTIONS_FILE"
    fi
  else
    printf '(none provided)\n' | tee -a "$INSTRUCTIONS_FILE"
    printf 'No FORK.md context provided.\n' >"$FORK_CONTEXT_FILE"
  fi
}

build_agent_payload() {
  AGENT_PAYLOAD=$(
    cat "$INSTRUCTIONS_FILE"
    printf '\n\n'
    cat "$FORK_CONTEXT_FILE" || printf ''
  )
}

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
