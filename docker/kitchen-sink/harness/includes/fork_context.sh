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
  local fork_file setup_tmp body_tmp changelog_excludes_tmp
  fork_file="$WORKSPACE_DIR/FORK.md"
  FORK_CONTEXT_PRESENT=0
  FORK_CONTEXT_BODY=""
  FORK_SETUP_COMMAND=""
  FORK_CHANGELOG_EXCLUDE_PATTERNS=""

  if [[ ! -f "$fork_file" ]]; then
    return 0
  fi

  FORK_CONTEXT_PRESENT=1
  setup_tmp=$(mktemp)
  body_tmp=$(mktemp)
  changelog_excludes_tmp=$(mktemp)

  if ! python3 - "$fork_file" "$setup_tmp" "$body_tmp" "$changelog_excludes_tmp" <<'PY'; then
from pathlib import Path
import sys

fork_path = Path(sys.argv[1])
setup_out = Path(sys.argv[2])
body_out = Path(sys.argv[3])
changelog_excludes_out = Path(sys.argv[4])

content = fork_path.read_text(encoding="utf-8")
setup = ""
body = content
changelog_excludes: list[str] = []


def parse_setup_value(front_lines: list[str], start: int, line: str) -> tuple[str, int]:
    """Parse `setup` metadata while preserving existing inline and block forms."""

    value = line[len("setup:") :].strip()
    if value in ("|", "|-"):
        idx = start + 1
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
            break
        if not block:
            raise SystemExit(
                "FORK.md front matter is malformed: setup block string must include at least one command line."
            )
        return "\n".join(block).rstrip("\n"), idx

    if value == "":
        raise SystemExit(
            "FORK.md front matter is malformed: setup must be a non-empty string or block string."
        )
    return value, start + 1


def parse_changelog_metadata(
    front_lines: list[str],
    start: int,
) -> tuple[list[str], int]:
    """Parse `changelog.exclude` metadata as an ordered list of non-empty strings."""

    idx = start + 1
    nested_lines: list[str] = []
    while idx < len(front_lines):
        nested = front_lines[idx]
        if nested.startswith("  ") or nested.strip() == "":
            nested_lines.append(nested)
            idx += 1
            continue
        break

    if not nested_lines:
        raise SystemExit(
            "FORK.md front matter is malformed: changelog must define nested metadata with changelog.exclude."
        )

    local_idx = 0
    parsed_excludes: list[str] = []
    found_exclude = False
    while local_idx < len(nested_lines):
        nested_line = nested_lines[local_idx]
        stripped = nested_line.strip()
        if stripped == "" or stripped.startswith("#"):
            local_idx += 1
            continue
        if not nested_line.startswith("  "):
            raise SystemExit(
                "FORK.md front matter is malformed: changelog metadata must be indented by two spaces."
            )

        key_line = nested_line[2:]
        if not key_line.startswith("exclude:"):
            raise SystemExit(
                f"FORK.md front matter is malformed: unsupported changelog key line '{key_line}'. Only 'exclude' is allowed."
            )
        if found_exclude:
            raise SystemExit(
                "FORK.md front matter is malformed: duplicate changelog.exclude key."
            )
        trailing = key_line[len("exclude:") :].strip()
        if trailing:
            raise SystemExit(
                "FORK.md front matter is malformed: changelog.exclude must be a list with one '- pattern' per line."
            )
        found_exclude = True
        local_idx += 1

        while local_idx < len(nested_lines):
            candidate = nested_lines[local_idx]
            if candidate.strip() == "":
                local_idx += 1
                continue
            if candidate.lstrip().startswith("#"):
                local_idx += 1
                continue
            if not candidate.startswith("    "):
                break
            item = candidate[4:]
            if not item.startswith("-"):
                raise SystemExit(
                    "FORK.md front matter is malformed: changelog.exclude entries must be list items prefixed with '-'."
                )
            pattern = item[1:].strip()
            if not pattern:
                raise SystemExit(
                    "FORK.md front matter is malformed: changelog.exclude entries must be non-empty strings."
                )
            parsed_excludes.append(pattern)
            local_idx += 1

    if not found_exclude:
        raise SystemExit(
            "FORK.md front matter is malformed: changelog must include an 'exclude' key."
        )
    if not parsed_excludes:
        raise SystemExit(
            "FORK.md front matter is malformed: changelog.exclude must contain at least one pattern."
        )

    return parsed_excludes, idx

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
    parsed_changelog_excludes: list[str] | None = None
    while idx < len(front_lines):
        line = front_lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue

        if line.startswith("setup:"):
            if parsed_setup is not None:
                raise SystemExit("FORK.md front matter is malformed: duplicate 'setup' key.")
            parsed_setup, idx = parse_setup_value(front_lines, idx, line)
            continue

        if line.startswith("changelog:"):
            if line[len("changelog:") :].strip() != "":
                raise SystemExit(
                    "FORK.md front matter is malformed: changelog must be an object with nested keys."
                )
            if parsed_changelog_excludes is not None:
                raise SystemExit(
                    "FORK.md front matter is malformed: duplicate 'changelog' key."
                )
            parsed_changelog_excludes, idx = parse_changelog_metadata(front_lines, idx)
            continue

        if line.startswith("  "):
            raise SystemExit(
                f"FORK.md front matter is malformed: unexpected indentation for line '{line}'."
            )
        raise SystemExit(
            f"FORK.md front matter is malformed: unsupported key line '{line}'. Only 'setup' and 'changelog' are allowed."
        )

    setup = parsed_setup or ""
    changelog_excludes = parsed_changelog_excludes or []

setup_out.write_text(setup, encoding="utf-8")
body_out.write_text(body, encoding="utf-8")
changelog_excludes_out.write_text("\n".join(changelog_excludes), encoding="utf-8")
PY
    rm -f "$setup_tmp" "$body_tmp" "$changelog_excludes_tmp"
    return 1
  fi

  FORK_SETUP_COMMAND=$(cat "$setup_tmp")
  FORK_CONTEXT_BODY=$(cat "$body_tmp")
  FORK_CHANGELOG_EXCLUDE_PATTERNS=$(cat "$changelog_excludes_tmp")
  rm -f "$setup_tmp" "$body_tmp" "$changelog_excludes_tmp"
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
