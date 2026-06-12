#!/usr/bin/env bash
# Fork context parsing and instruction rendering helpers for the harness runtime.

# Mode-aware merge-agent guidance. The orchestrator selects the lifetime via
# FORKLIFT_AGENT_LIFETIME: `conflict` scopes each agent to a single paused
# conflict; `rebase` drives the whole paused rebase in one session. Both modes
# round-trip every transition through the wrapper and never author report files.
default_instructions() {
  if [[ "${FORKLIFT_AGENT_LIFETIME:-conflict}" == "rebase" ]]; then
    rebase_mode_instructions
  else
    conflict_mode_instructions
  fi
}

# Shared environment preamble used by both lifetime modes.
instruction_environment_preamble() {
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
  (helper branch \`$helper_branch\`) before starting the rebase for you.
|- $upstream_ref is at $upstream_sha ($upstream_date)
|- local $branch_name is at $main_sha — use this as a reset point if the rebase goes
  badly wrong: \`git checkout $branch_name && git reset --hard $main_sha\`
|- Use git to inspect history, branches, and conflicts
|- Forklift already started \`git rebase $upstream_ref\`. You are only here because it paused.
|- Do not attempt to run tests or build the code; focus on finishing the paused rebase.

<system_reminder>
In the context of this rebase, "ours" refers to the upstream project, and "theirs" refers to the fork that you help manage.
</system_reminder>
TXT
}

# Shared conflict-resolution policy used by both lifetime modes.
instruction_resolution_policy() {
  local upstream_ref branch_name
  upstream_ref="$UPSTREAM_REF"
  branch_name="$MAIN_BRANCH"
  cat <<TXT
Your goal is to preserve the functionality of both $upstream_ref and $branch_name.
Refer to the FORK.md if supplied to understand intentional fork customizations worth
preserving.
Exception: if upstream is introducing a feature that substantially overlaps one the
fork already maintains, prefer upstream's implementation and drop the fork's duplicate,
even when both could be kept. "Close enough" is acceptable here — adopting upstream
removes that feature from the fork's future maintenance burden. This is the only case
where you favor one side over merging; everywhere else, integrate both sides.
This applies only to functional overlap. Stylistic differences — visuals, branding,
wording, flavor (e.g. a custom welcome screen) — are never "close enough": the fork's
version is deliberate, so always preserve it over upstream's.
TXT
}

# Per-conflict lifetime: resolve exactly the current paused conflict, then continue.
conflict_mode_instructions() {
  local branch_name
  branch_name="$MAIN_BRANCH"
  instruction_environment_preamble
  cat <<TXT

== Task ==
A fresh agent handles each conflict, so resolve ONLY the single conflict the
rebase is currently paused on. Do not try to drive the rebase to completion —
Forklift advances to the next conflict (with a new agent) after you continue.

1. Inspect the current paused conflict on the local \`$branch_name\` branch only.
$(instruction_resolution_policy)
2. Resolve the conflict and stage the resolved files.
3. Finish this conflict with exactly one of:
   - \`git rebase --continue --resolution-note "<what changed & why>"\` once resolved.
   - \`git rebase --skip --resolution-note "<why this commit is dropped>"\` only when the
     commit is genuinely empty/redundant and dropping it is the truthful outcome.
   - \`git rebase --abort --reason "<what blocked progress and what a human must do>"\`
     if this conflict cannot be resolved while preserving both sides.
   The note/reason is mandatory; Forklift records it to summarize the rebase.
TXT
}

# Whole-rebase lifetime: one session drives every conflict to completion.
rebase_mode_instructions() {
  local branch_name upstream_ref
  branch_name="$MAIN_BRANCH"
  upstream_ref="$UPSTREAM_REF"
  instruction_environment_preamble
  cat <<TXT

== Task ==
1. Inspect the current paused rebase on the local \`$branch_name\` branch only.
   No other branches need attention.
2. Resolve any conflicts.
$(instruction_resolution_policy)
3. Finish each paused commit with exactly one of:
   - \`git rebase --continue --resolution-note "<what changed & why>"\` once resolved.
   - \`git rebase --skip --resolution-note "<why this commit is dropped>"\` only when a
     commit is genuinely mechanically empty and that is the truthful outcome.
   - \`git rebase --abort --reason "<what blocked progress and what a human must do>"\`
     if you cannot finish while preserving both $upstream_ref and $branch_name.
   The note/reason is mandatory on every transition; Forklift records it and there is
   no report file to author.
4. Repeat until the rebase is complete. Verify no rebase is still in progress and do
   not create any extra final commit after the rebase completes.
TXT
}

append_extra_run_instructions() {
  if [[ ! -f "$EXTRA_RUN_INSTRUCTIONS_FILE" || ! -s "$EXTRA_RUN_INSTRUCTIONS_FILE" ]]; then
    return
  fi

  cat "$EXTRA_RUN_INSTRUCTIONS_FILE" | tee -a "$INSTRUCTIONS_FILE"
}

# Parse optional FORK front matter into setup metadata while keeping agent-visible context body-only.
parse_fork_context() {
  local fork_file setup_tmp body_tmp changelog_excludes_tmp rebase_continue_check_tmp
  fork_file="$WORKSPACE_DIR/FORK.md"
  FORK_CONTEXT_PRESENT=0
  FORK_CONTEXT_BODY=""
  FORK_SETUP_COMMAND=""
  FORK_CHANGELOG_EXCLUDE_PATTERNS=""
  FORK_REBASE_CONTINUE_CHECK=""

  if [[ ! -f "$fork_file" ]]; then
    return 0
  fi

  FORK_CONTEXT_PRESENT=1
  setup_tmp=$(mktemp)
  body_tmp=$(mktemp)
  changelog_excludes_tmp=$(mktemp)
  rebase_continue_check_tmp=$(mktemp)

  if ! python3 - "$fork_file" "$setup_tmp" "$body_tmp" "$changelog_excludes_tmp" "$rebase_continue_check_tmp" <<'PY'; then
from pathlib import Path
import sys

fork_path = Path(sys.argv[1])
setup_out = Path(sys.argv[2])
body_out = Path(sys.argv[3])
changelog_excludes_out = Path(sys.argv[4])
rebase_continue_check_out = Path(sys.argv[5])

content = fork_path.read_text(encoding="utf-8")
setup = ""
body = content
changelog_excludes: list[str] = []
rebase_continue_check = ""


def parse_shell_string_value(
    front_lines: list[str],
    start: int,
    line: str,
    *,
    key: str,
) -> tuple[str, int]:
    """Parse shell-string metadata while preserving inline and block forms."""

    value = line[len(f"{key}:") :].strip()
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
                f"FORK.md front matter is malformed: {key} block string must include at least one command line."
            )
        return "\n".join(block).rstrip("\n"), idx

    if value == "":
        raise SystemExit(
            f"FORK.md front matter is malformed: {key} must be a non-empty string or block string."
        )
    return value, start + 1


def parse_rebase_metadata(
    front_lines: list[str],
    start: int,
) -> tuple[str, int]:
    """Parse `rebase.continue_check` metadata with strict unknown-key handling."""

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
            "FORK.md front matter is malformed: rebase must define nested metadata with rebase.continue_check."
        )

    local_idx = 0
    parsed_continue_check: str | None = None
    while local_idx < len(nested_lines):
        nested_line = nested_lines[local_idx]
        stripped = nested_line.strip()
        if stripped == "" or stripped.startswith("#"):
            local_idx += 1
            continue
        if not nested_line.startswith("  "):
            raise SystemExit(
                "FORK.md front matter is malformed: rebase metadata must be indented by two spaces."
            )

        key_line = nested_line[2:]
        if not key_line.startswith("continue_check:"):
            raise SystemExit(
                f"FORK.md front matter is malformed: unsupported rebase key line '{key_line}'. Only 'continue_check' is allowed."
            )
        if parsed_continue_check is not None:
            raise SystemExit(
                "FORK.md front matter is malformed: duplicate rebase.continue_check key."
            )

        shell_lines = [key_line]
        for candidate in nested_lines[local_idx + 1 :]:
            if candidate.startswith("  "):
                shell_lines.append(candidate[2:])
            else:
                shell_lines.append(candidate)

        parsed_continue_check, consumed_idx = parse_shell_string_value(
            shell_lines,
            0,
            key_line,
            key="continue_check",
        )
        local_idx += consumed_idx

    if parsed_continue_check is None:
        raise SystemExit(
            "FORK.md front matter is malformed: rebase must include a 'continue_check' key."
        )

    return parsed_continue_check, idx


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
    parsed_rebase_continue_check: str | None = None
    while idx < len(front_lines):
        line = front_lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue

        if line.startswith("setup:"):
            if parsed_setup is not None:
                raise SystemExit("FORK.md front matter is malformed: duplicate 'setup' key.")
            parsed_setup, idx = parse_shell_string_value(
                front_lines,
                idx,
                line,
                key="setup",
            )
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

        if line.startswith("rebase:"):
            if line[len("rebase:") :].strip() != "":
                raise SystemExit(
                    "FORK.md front matter is malformed: rebase must be an object with nested keys."
                )
            if parsed_rebase_continue_check is not None:
                raise SystemExit(
                    "FORK.md front matter is malformed: duplicate 'rebase' key."
                )
            parsed_rebase_continue_check, idx = parse_rebase_metadata(front_lines, idx)
            continue

        if line.startswith("  "):
            raise SystemExit(
                f"FORK.md front matter is malformed: unexpected indentation for line '{line}'."
            )
        raise SystemExit(
            f"FORK.md front matter is malformed: unsupported key line '{line}'. Only 'setup', 'changelog', and 'rebase' are allowed."
        )

    setup = parsed_setup or ""
    changelog_excludes = parsed_changelog_excludes or []
    rebase_continue_check = parsed_rebase_continue_check or ""

setup_out.write_text(setup, encoding="utf-8")
body_out.write_text(body, encoding="utf-8")
changelog_excludes_out.write_text("\n".join(changelog_excludes), encoding="utf-8")
rebase_continue_check_out.write_text(rebase_continue_check, encoding="utf-8")
PY
    rm -f "$setup_tmp" "$body_tmp" "$changelog_excludes_tmp" "$rebase_continue_check_tmp"
    return 1
  fi

  FORK_SETUP_COMMAND=$(cat "$setup_tmp")
  FORK_CONTEXT_BODY=$(cat "$body_tmp")
  FORK_CHANGELOG_EXCLUDE_PATTERNS=$(cat "$changelog_excludes_tmp")
  FORK_REBASE_CONTINUE_CHECK=$(cat "$rebase_continue_check_tmp")
  rm -f "$setup_tmp" "$body_tmp" "$changelog_excludes_tmp" "$rebase_continue_check_tmp"
  return 0
}

# Render harness instructions and fork context files using front-matter-stripped body content.
write_instructions() {
  print_header "Instructions" | tee "$INSTRUCTIONS_FILE"
  default_instructions | tee -a "$INSTRUCTIONS_FILE"
  append_extra_run_instructions
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
