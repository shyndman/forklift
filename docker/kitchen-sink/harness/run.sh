#!/usr/bin/env bash
set -euo pipefail

INSTRUCTIONS_FILE=/harness-state/instructions.txt
FORK_CONTEXT_FILE=/harness-state/fork-context.md
mkdir -p /harness-state

default_instructions() {
  cat <<'EOF'
Forklift Agent Instructions
===========================
1. Merge `upstream/main` into `main` inside /workspace.
2. Resolve conflicts carefully; prefer upstream changes when unsure and summarize any deviations.
3. Run the project's primary tests (npm test, pytest, cargo test, etc.) when time allows.
4. Craft meaningful commits summarizing the merge and any fixes required for a clean build.
5. If you cannot finish safely within 8 minutes, write STUCK.md explaining what you tried, what failed, and the help you need.
EOF
}

print_header() {
  printf '== %s ==\n' "$1"
}

print_header "Instructions" | tee "$INSTRUCTIONS_FILE"
default_instructions | tee -a "$INSTRUCTIONS_FILE"
print_header "FORK.md Context" | tee -a "$INSTRUCTIONS_FILE"
if [[ -f /workspace/FORK.md ]]; then
  cp /workspace/FORK.md "$FORK_CONTEXT_FILE"
  cat /workspace/FORK.md | tee -a "$INSTRUCTIONS_FILE"
else
  printf "(none provided)\n" | tee -a "$INSTRUCTIONS_FILE"
fi

if (($# > 0)); then
  print_header "Agent Command"
  agent_command="$*"
  printf '%s\n' "$agent_command"
  bash -lc "$agent_command"
fi

if (($# == 0)); then
  echo "Harness placeholder: no agent wired yet." >&2

fi

