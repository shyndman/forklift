#!/usr/bin/env bash
# Fork context parsing and instruction rendering helpers for the harness runtime.

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
