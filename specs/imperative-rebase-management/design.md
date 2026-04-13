## Context

Forklift currently launches the agent after optional `setup`, then leaves all in-container rebase control to that agent. The current harness contract in `docker/kitchen-sink/harness/includes/fork_context.sh` explicitly tells the agent to use Git directly, avoid tests/builds, and continue the rebase until complete. The only hard gates today are strict front-matter parsing plus `setup` before agent launch, and host-side verification after the container exits.

That leaves a gap at the most failure-prone seam: after conflicts are resolved but before `git rebase --continue` advances to the next commit. Repo-specific formatting, lint, generated-file, or other continuability checks are not enforced there, so the final tree can be integrated-but-invalid. At the same time, setup failures are currently surfaced through `/harness-state/setup.log`, which forces operators to dig through side artifacts instead of seeing the important failure at the top level.

Constraints and locked decisions from design discussion:
- `FORK.md` metadata stays strict and declarative.
- `rebase.continue_check` is a single shell string, not a list or DSL.
- The active rebase gate must be frozen at harness startup; the mutable workspace copy of `FORK.md` cannot change the running contract.
- `continue_check` may mutate tracked, staged, or untracked files. Any mutation is treated like failure: control returns to the agent until rerunning the check produces zero exit code and no state change.
- Untracked file changes count as mutations.
- Harness mediation of rebase commands must not be advertised up front to the agent.
- Mechanically empty skips should be automated silently; agent-initiated skips should be recorded and shown in the final summary.
- `git rebase --abort` should be rejected until `STUCK.md` exists, then allowed and surfaced as a stuck outcome.
- Setup and rebase-gate diagnostics should be hoisted into the top-level run log, while the OpenCode transcript remains the deep trace surface.

Stakeholders:
- Operators who need the top-level run log to explain failures without artifact hunting.
- Repo maintainers who need repo-owned continuability policy in version control.
- Agents, who still need natural Git access for conflict work even though Forklift now owns the continue/skip/abort seam.

## Goals / Non-Goals

**Goals:**
- Extend strict `FORK.md` front matter with `rebase.continue_check` as a single shell string.
- Freeze the active continue gate at harness startup so workspace edits cannot weaken enforcement mid-run.
- Make Forklift own the transition from paused rebase to resumed rebase by mediating `git rebase --continue` inside the container.
- Treat `continue_check` as passing only when it exits zero and leaves tracked, staged, and untracked state unchanged from the pre-check snapshot.
- Hoist setup and rebase-gate output into the top-level run log instead of side log files.
- Preserve the deep OpenCode transcript as a separate artifact.
- Auto-handle mechanically empty skips, record agent-driven skips, and always render a `Skipped Commits:` section in the final completion report.
- Require `STUCK.md` before allowing rebase abort.

**Non-Goals:**
- Replacing normal Git conflict-resolution commands with a full Forklift shell or bespoke conflict API.
- Building a general-purpose `FORK.md` task runner or multi-step rebase DSL.
- Streaming the full OpenCode transcript into the top-level run log.
- Preventing every possible misuse of raw Git outside the mediated rebase seam.
- Changing host-side publication/authorship rewrite behavior after a successful rebase.

## Current Implementation Touchpoints

- `docker/kitchen-sink/harness/includes/fork_context.sh:parse_fork_context()` is the primary `FORK.md` parser today. It is a shell function with an embedded Python parser and currently exports `FORK_SETUP_COMMAND`, `FORK_CONTEXT_BODY`, and `FORK_CHANGELOG_EXCLUDE_PATTERNS`.
- `src/forklift/changelog.py:load_changelog_exclude_patterns(repo_path: Path) -> list[str]` is the only current host-side `FORK.md` parser. It reads only `changelog.exclude`; there is no general mirrored host-side parser for `setup` or other keys.
- `docker/kitchen-sink/harness/run.sh:main()` initializes `HARNESS_STATE_DIR`, truncates `opencode-client.log` and `setup.log`, writes `harness-status.txt`, then runs phases in this exact order: `parse_fork_context`, `run_setup_command`, `write_instructions`, `build_agent_payload`, `launch_agent`.
- `docker/kitchen-sink/harness/includes/agent.sh:launch_agent()` invokes `timeout "$remaining" "${command_args[@]}" "$AGENT_PAYLOAD" >>"$CLIENT_LOG" 2>&1 || true`. Because `launch_agent()` is called from `run.sh`, any `PATH` change made in `run.sh` before that call is inherited by the agent process.
- `docker/kitchen-sink/harness/includes/setup.sh:run_setup_command()` writes detailed setup diagnostics and command output to `SETUP_LOG`. Host-side failure surfacing currently happens later through `src/forklift/cli.py:_log_setup_failure_details()` and `_log_harness_log_tail()`.
- `src/forklift/container_runner.py:ContainerRunner.run(...) -> ContainerRunResult` captures container stdout/stderr with `subprocess.Popen(..., stdout=PIPE, stderr=PIPE)` and `process.communicate(...)`. `src/forklift/cli.py` logs `Container stdout` and `Container stderr` only after the container exits; there is no live host streaming today.
- `src/forklift/post_run_metrics.py:render_completion_report(workspace: Path, *, console: Console | None = None) -> Path | None` currently reads `workspace/STUCK.md` or `workspace/DONE.md` verbatim and renders the markdown. It does not currently accept harness metadata or augment the rendered content.
- `src/forklift/run_state.py` and `run-state.json` are host-managed lifecycle metadata. The container does not receive a mount for that file. Any harness-to-host machine-readable data written from inside the container must go through `/harness-state`.
- `opencode-client.log` is the existing deep transcript surface. `docker/kitchen-sink/harness/includes/common.sh:log_client()` and `agent.sh` append to that file, `src/forklift/clientlog.py` reads it for `forklift clientlog`, and README already documents it as the agent transcript.
- `tests/test_harness_setup.py` is the existing harness/front-matter test module. It uses `unittest.TestCase`, temporary directories, and a `_run_harness_shell(commands: str)` helper to execute harness shell code.
- `tests/test_post_run_metrics.py` is the existing completion-report/usage-summary test module. It also uses `unittest.TestCase` and temp directories, making it the right place for `render_completion_report(...)` augmentation tests.
- The live docs/specs to update are `FORK.md`, `README.md`, `openspec/specs/agent-sandbox-run/spec.md`, and `openspec/specs/opencode-agent-bridge/spec.md`.

## Decisions

1. **Extend strict front matter with a single `rebase.continue_check` shell string**
   - Decision: extend `parse_fork_context()` in `docker/kitchen-sink/harness/includes/fork_context.sh` with a new top-level `rebase` object containing one required nested key, `continue_check`, whose value uses the same inline or block-string forms already accepted for `setup`. Extend `src/forklift/changelog.py:load_changelog_exclude_patterns()` only enough to keep strict unknown-key handling aligned; it remains a changelog-only host parser rather than a full mirror.
   - Example shape:
     ```yaml
     ---
     setup: uv sync
     rebase:
       continue_check: |
         uv run ruff format --check .
         uv run ruff check .
         uv run pytest
     ---
     ```
   - Why: this keeps the metadata contract strict, small, and consistent with existing `setup` behavior while letting repos externalize complexity into scripts when needed.
   - Alternatives considered:
     - List-of-commands or mini DSL: rejected because it expands parser complexity and creates a second task-runner language.
     - Body-text directives in `FORK.md`: rejected because machine policy should stay separate from agent-visible prose.

2. **Freeze rebase policy at harness startup in immutable run state**
   - Decision: during `parse_fork_context`, extract `rebase.continue_check`, render it into `/harness-state/rebase-continue-check.sh`, `chmod` it read-only/executable for the harness user, and execute only that frozen artifact during rebase mediation. The artifact is created in `run.sh` after context parsing succeeds and before `launch_agent()` runs.
   - Why: the repo-owned policy is part of the run contract, but the workspace is intentionally writable by the agent. Freezing policy at startup preserves enforcement integrity without needing to protect `FORK.md` itself.
   - Alternatives considered:
     - Re-read `/workspace/FORK.md` on every continue: rejected because the agent could edit the gate mid-run.
     - Make `FORK.md` read-only: rejected because mutating the workspace copy is legitimate for repo work and still would not communicate the active run contract clearly.

3. **Mediate selected `git rebase` commands through a shallow harness-owned Git wrapper**
   - Decision: add a harness-owned `git` wrapper script under the harness tree and prepend its directory to `PATH` in `run.sh` before `launch_agent()`. The wrapper keeps normal Git access intact, but normalizes argv enough to detect rebase actions and intercept only `rebase --continue`, `rebase --skip`, and `rebase --abort`. Unknown rebase invocation shapes during a paused rebase fail closed.
   - Why: conflict resolution is still Git-native work, so removing Git access or forcing a separate Forklift command would be unnatural. A narrow wrapper owns only the seam that matters.
   - Wrapper classification contract:
     - Preserve tokens that do not start with `-`, plus action flags `--continue`, `--skip`, and `--abort`.
     - If normalized argv begins with `rebase --continue`, `rebase --skip`, or `rebase --abort`, intercept that action.
     - During a paused rebase, if normalized argv still refers to `rebase` but does not match one of those recognized forms, fail closed with a plain Git-style error.
     - Otherwise pass through to the real Git binary unchanged.
   - Alternatives considered:
     - Tell the agent to use `forklift rebase-continue`: rejected because raw Git would remain an easy bypass and the agent workflow would feel fake.
     - Parse full Git CLI syntax: rejected because the real need is suspicious normalization, not a full Git parser.
     - Remove Git entirely: rejected because agents need `git add`, `git status`, `git diff`, checkout helpers, and other normal commands to resolve conflicts.

   **Junior implementation notes**
   - Resolve the real Git binary path before prepending the wrapper directory to `PATH`. Store it in an env var such as `REAL_GIT_BIN` so the wrapper never recursively calls itself.
   - Put the wrapper in a stable, testable location such as `docker/kitchen-sink/harness/includes/bin/git`.
   - The wrapper should be a tiny dispatcher only. Put multi-step logic in a sourced helper file such as `docker/kitchen-sink/harness/includes/rebase.sh` so unit-style harness tests can call those functions directly.
   - Only fail closed when a rebase is paused. Outside a paused rebase, every Git command should pass straight through.

4. **Define continue success as “zero exit and stable tree”, not “clean tree”**
   - Decision: before running `continue_check`, capture `git status --porcelain` including untracked files. After the check finishes, capture status again. The gate passes only when exit code is zero and the before/after snapshots are identical.
   - Why: a paused rebase can legitimately have staged conflict-resolution changes ready for continuation. What matters is that the check itself introduces no additional tracked, staged, or untracked changes.
   - Alternatives considered:
     - Require a completely clean tree: rejected because that would incorrectly reject valid paused-rebase states.
     - Ignore untracked files: rejected because repo-owned scripts may intentionally emit untracked artifacts that the agent must inspect, stage, or delete.
     - Treat mutating formatter runs as success: rejected because Forklift would then be hiding new work the agent still needs to handle.

   **Junior implementation notes**
   - Use one exact command for both snapshots so string comparison is deterministic: `git -C "$WORKSPACE_DIR" status --porcelain=v1 --untracked-files=all`.
   - Compare the raw before/after snapshot strings. Do not try to parse and normalize them into structs in the first implementation.
   - A paused rebase may legitimately have staged files from conflict resolution. That is why the gate compares before vs after instead of demanding an empty status.
   - Run the frozen check from the workspace root so repo-local relative paths still work.

5. **Hoist setup and rebase-gate diagnostics into top-level run logs; keep transcript separate**
   - Decision: remove setup/rebase side-log files as the primary failure surface. Harness-owned phases emit phase-tagged, operator-facing output to container stdout/stderr so the host’s normal top-level run log shows the important failure last. Under the current `ContainerRunner.run()` transport, this output appears after container completion because `process.communicate()` buffers stdout/stderr until exit. Live host streaming remains out of scope. The OpenCode transcript remains in `opencode-client.log` as the deep trace artifact.
   - Why: setup and rebase-gate failures are core product behavior, not secondary diagnostics. Operators should not have to discover and open special log files to learn why a run stopped.
   - Alternatives considered:
     - Keep `setup.log` and add `rebase-gate.log`: rejected because it repeats the same artifact-hunting problem.
     - Merge full transcript into the top-level log: rejected because transcript detail is useful as a separate forensic surface, not as the default operator log.
     - Add real-time log streaming: rejected as out of scope and unnecessary for fixing the current failure-surfacing problem.

6. **Auto-skip only mechanically empty commits; record only agent-directed skips**
   - Decision: auto-skip only when the `rebase --continue` path proves the current commit is mechanically empty after the repo-defined check has already passed. For this feature, treat that proof as: the real `git rebase --continue` returns non-zero, the rebase is still paused, and `git status --porcelain=v1 --untracked-files=all` is empty. Explicit `git rebase --skip` commands are always treated as agent-directed skips: they are allowed, but the original rebased commit identity is captured before skipping and stored as run metadata.
   - Why: mechanically empty skips are normal Git housekeeping and not operator signal. Explicit skip commands are semantic choices and should always be visible later.
   - Alternatives considered:
     - Forbid `--skip`: rejected because legitimate rebases sometimes require it.
     - Record every skip including empty ones: rejected because it adds noise to the final summary.
     - Require `STUCK.md` for any skip: rejected because it turns valid Git recovery into false failure.

   **Junior implementation notes**
   - Capture the rebased commit identity before an agent-directed skip with `git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD` and `git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD`.
   - Treat missing `REBASE_HEAD` as a fail-closed wrapper error when a paused rebase is being skipped.
   - For the automatic mechanical-empty path, only auto-skip after a real `git rebase --continue` attempt returns non-zero, the rebase is still paused, and the full porcelain status snapshot is empty. Do not guess based solely on an explicit `git rebase --skip` request.

7. **Require `STUCK.md` before abort and map allowed abort to stuck outcome**
   - Decision: reject `git rebase --abort` unless `workspace/STUCK.md` already exists and contains at least one non-whitespace line. Once present, allow the real abort and treat the run as a stuck outcome.
   - Why: abort is a terminal control-flow decision, not a routine conflict-resolution step. Requiring `STUCK.md` ensures operators always receive actionable remediation details.
   - Alternatives considered:
     - Ban abort entirely: rejected because abort is a legitimate escape hatch when the rebase path is wrong.
     - Allow abort freely: rejected because it loses the explanation operators need.

8. **Augment final completion rendering with harness-owned skip metadata**
   - Decision: store only non-log, machine-readable rebase metadata needed after container exit in `/harness-state/rebase-skipped-commits.json`, containing an ordered JSON array of `{ "sha": str, "subject": str }` objects for agent-directed skips only. Extend `Forklift._render_terminal_summary(...)` to pass `run_paths.harness_state` into a widened `render_completion_report(workspace: Path, *, harness_state: Path, console: Console | None = None)` API. `render_completion_report` becomes responsible for reading `DONE.md`/`STUCK.md`, appending a deterministic `Skipped Commits:` section, and then rendering the combined markdown. Mechanically empty auto-skips never enter this file.
   - Why: skipped-commit reporting is a host concern and should not rely on the agent remembering to mention it. Keeping it out of `DONE.md`/`STUCK.md` avoids exposing mediation details in agent instructions.
   - Alternatives considered:
     - Inject skip details into agent-authored markdown files: rejected because it mixes harness metadata with agent narrative and risks disclosing hidden behavior.
     - Reconstruct skips after the fact from rewritten history: rejected because the exact rebased commit identity can disappear after skip advances the sequencer.
     - Reuse `run-state.json`: rejected because it is host-managed lifecycle metadata and is not mounted into the container.

   **Junior implementation notes**
   - Initialize `/harness-state/rebase-skipped-commits.json` to `[]` during harness startup. That keeps both the wrapper and host renderer simple.
   - The file format for the first implementation should stay minimal:
     ```json
     [
       {"sha": "abc1234", "subject": "Remove obsolete compatibility shim"}
     ]
     ```
   - When rendering the final report, treat a missing or unreadable skip file as `[]` so older runs and partially upgraded environments still render a report.

## Terms used in this spec

- **Paused rebase**: a repository state where `.git/rebase-merge` or `.git/rebase-apply` exists and Git expects `rebase --continue`, `--skip`, or `--abort` next.
- **Stable tree**: the exact raw output of `git status --porcelain=v1 --untracked-files=all` is unchanged before vs after `continue_check` runs.
- **Mechanically empty commit**: a rebased commit that Git can no longer apply because it produces no remaining change on top of the current base. This is the only case Forklift auto-skips silently.
- **Agent-directed skip**: any explicit `git rebase --skip` command issued by the agent. These are always recorded for the final summary.
- **Top-level run log**: host-visible `Container stdout` / `Container stderr` entries emitted by `src/forklift/cli.py` after the container exits.
- **Transcript**: `/harness-state/opencode-client.log`, the deep trace of harness and agent activity.

> [!REVIEW]
> How is a mechanically empty commit detected?


## File-by-file implementation plan

### `docker/kitchen-sink/harness/run.sh`
- Add new defaults for:
  - `REBASE_CONTINUE_CHECK_FILE=${REBASE_CONTINUE_CHECK_FILE:-$HARNESS_STATE_DIR/rebase-continue-check.sh}`
  - `REBASE_SKIPPED_COMMITS_FILE=${REBASE_SKIPPED_COMMITS_FILE:-$HARNESS_STATE_DIR/rebase-skipped-commits.json}`
- Source a new helper file such as `includes/rebase.sh`.
- After `parse_fork_context` succeeds:
  - initialize `REBASE_SKIPPED_COMMITS_FILE` with `[]`
  - if a continue check is configured, write `REBASE_CONTINUE_CHECK_FILE`
  - resolve and export `REAL_GIT_BIN`
  - prepend the wrapper directory to `PATH`
- Keep existing phase order unchanged.

### `docker/kitchen-sink/harness/includes/fork_context.sh`
- Extend the embedded Python parser to accept one additional top-level key: `rebase`.
- Inside `rebase`, accept exactly one nested key: `continue_check`.
- Reuse the current `setup` string/block-string parsing rules for `continue_check`.
- Export a new shell variable such as `FORK_REBASE_CONTINUE_CHECK`.
- Continue failing closed on duplicate keys, unknown keys, bad indentation, empty values, and malformed front matter.

### `docker/kitchen-sink/harness/includes/rebase.sh` (new)
- Own all wrapper behavior here so the `git` wrapper stays tiny.
- Recommended helper functions:
  - `rebase_in_progress()`
  - `write_rebase_continue_check_file()`
  - `prepend_git_wrapper_path()`
  - `capture_status_snapshot()`
  - `run_continue_check()`
  - `handle_rebase_continue()`
  - `handle_rebase_skip()`
  - `handle_rebase_abort()`
  - `append_agent_skip_record()`
- Each helper should log enough context to stdout/stderr for operators and to `CLIENT_LOG` for transcript continuity.

### `docker/kitchen-sink/harness/includes/bin/git` (new)
- Compute its own directory.
- Source `../rebase.sh`.
- Dispatch to `handle_rebase_continue`, `handle_rebase_skip`, or `handle_rebase_abort` only when `rebase_in_progress()` is true and normalized argv matches a guarded action.
- Otherwise `exec "$REAL_GIT_BIN" "$@"`.

### `src/forklift/cli.py`
- Widen `_render_terminal_summary(...)` to accept `harness_state: Path`.
- Pass both `workspace` and `harness_state` into `render_completion_report(...)`.
- Remove or reduce setup-log tail surfacing once setup details are emitted through top-level container stdout/stderr. Keep failure handling for other harness artifacts that still matter.

### `src/forklift/post_run_metrics.py`
- Widen `render_completion_report` to `render_completion_report(workspace: Path, *, harness_state: Path, console: Console | None = None) -> Path | None`.
- Add a helper that reads `rebase-skipped-commits.json`, builds markdown for the `Skipped Commits:` section, appends it to the selected report body, and renders the combined markdown.
- Keep existing precedence: `STUCK.md` wins over `DONE.md`.

## Detailed control flow

### Harness startup

```python
parse_fork_context()
initialize_rebase_skipped_commits_file_to_empty_list()

if fork_md_declares_continue_check:
    write_frozen_continue_check_script()

real_git = resolve_real_git_before_path_prepend()
export REAL_GIT_BIN = real_git
prepend_wrapper_dir_to_PATH()

run_setup_command()
write_instructions()
build_agent_payload()
launch_agent()
```

### Intercepted `git rebase --continue`

```python
def handle_rebase_continue(argv):
    if no_frozen_continue_check_file:
        return exec_real_git(argv)

    before = capture_status_snapshot()
    check = run_frozen_continue_check()
    after = capture_status_snapshot()

    if check.exit_code != 0:
        print_continue_failure(check, after, reason="check_failed")
        return 1

    if after != before:
        print_continue_failure(check, after, reason="workspace_changed")
        return 1

    real_git_result = run_real_git_rebase_continue()

    if real_git_result.exit_code != 0 and rebase_in_progress() and capture_status_snapshot() == "":
        return run_real_git_rebase_skip_silently()

    return real_git_result.exit_code
```

### Intercepted `git rebase --skip`

```python
def handle_rebase_skip(argv):
    sha = git_rev_parse("REBASE_HEAD")
    subject = git_show_subject("REBASE_HEAD")
    append_agent_skip_record(sha, subject)
    return exec_real_git(argv)
```

### Intercepted `git rebase --abort`

```python
def handle_rebase_abort(argv):
    if stuck_md_missing_or_blank():
        print("Cannot abort rebase until STUCK.md explains what blocked progress.")
        return 1
    return exec_real_git(argv)
```

## Output contract

### Continue-check failure shown to agent/operator

When `continue_check` exits non-zero, print full output to container stderr/stdout using this structure:

```text
Rebase continue check failed.

Command:
<exact snapped command text>

Exit code:
<integer>

Workspace state after check:
<raw git status snapshot, may be empty>

stdout:
<full stdout, may be empty>

stderr:
<full stderr, may be empty>

Resolve state, then retry rebase continue.
```

When the check exits zero but changes the repo, keep the same shape but replace the first line with `Rebase continue check changed workspace state.` and use `Exit code: 0`.

### Final completion report section

Always append one blank line and then this exact markdown section to the rendered report body:

```md
## Skipped Commits

None
```

or

```md
## Skipped Commits

- `abc1234` Remove obsolete compatibility shim
- `def5678` Regenerate fixtures
```

This section is host-generated. Do not require the agent to write it.

## Testing guidance for a junior engineer

- Parser additions belong in `tests/test_harness_setup.py` because that file already exercises `parse_fork_context()` and `run_setup_command()` through sourced shell helpers.
- Wrapper tests should stay in `tests/test_harness_setup.py` first, using `_run_harness_shell(...)` to source the harness and call helper functions directly in a temporary repo.
- Completion-report augmentation belongs in `tests/test_post_run_metrics.py` beside existing `render_completion_report(...)` tests.
- Add at least one end-to-end harness test covering each of these cases:
  - valid `rebase.continue_check` parsed and written to frozen script
  - check exits non-zero and blocks continue
  - check mutates tracked files and blocks continue
  - check creates untracked files and blocks continue
  - explicit skip records SHA + subject
  - abort rejected when `STUCK.md` missing or blank
  - rendered report shows `## Skipped Commits` with `None`
  - rendered report shows recorded skips in order

## Risks / Trade-offs

- **[Risk] Narrow Git wrapper rejects unusual but valid rebase invocations** → **Mitigation:** keep interception scope tiny, fail closed only for unrecognized rebase actions during a paused rebase, and preserve passthrough for all other Git commands.
- **[Risk] Repo-defined continue checks create churn by repeatedly mutating files** → **Mitigation:** treat any mutation as failure, surface full resulting status to the agent, and require a stable rerun before continuation.
- **[Risk] Top-level logs become too verbose when setup or checks emit large output** → **Mitigation:** accept full output because the user explicitly wants the failure surface to be complete; keep transcript separate so only harness-owned phases are hoisted.
- **[Risk] Hidden mediation could confuse agents after repeated failures** → **Mitigation:** make failure messages describe the current repo state plainly without explaining harness mechanics, so the guidance remains actionable even when the implementation is intentionally opaque.
- **[Risk] Final skipped-commit metadata and rendered report diverge** → **Mitigation:** keep one host-owned metadata source of truth and generate the `Skipped Commits:` section deterministically during report rendering.

## Migration Plan

1. Extend `docker/kitchen-sink/harness/includes/fork_context.sh:parse_fork_context()` for strict `rebase.continue_check` parsing and keep `src/forklift/changelog.py:load_changelog_exclude_patterns()` aligned on unknown-key validation where needed.
2. Add `/harness-state/rebase-continue-check.sh` snapshotting after successful context parse and introduce the narrow Git wrapper into the container command environment before `launch_agent()`.
3. Implement mediated behavior for `rebase --continue`, `--skip`, and `--abort`, including stable-tree checks, mechanical-empty auto-skip, agent-skip metadata capture, and `STUCK.md` gating for abort.
4. Replace side-log-first setup failure handling with top-level harness log emission, and reuse the same pattern for rebase-gate output, using the existing post-run container stdout/stderr transport unless a later change explicitly adds live streaming.
5. Add `/harness-state/rebase-skipped-commits.json`, widen `render_completion_report(...)` to accept `harness_state`, and preserve transcript behavior and existing post-run verification/publication flow.
6. Update docs/specs (`README.md`, `FORK.md`, capability specs) to describe new metadata, operator-visible behavior, and final skip reporting.
7. Validate with targeted harness tests, front-matter parser tests, and post-run rendering tests before implementation is considered complete.

## Open Questions

- None. Current decisions are locked for this spec.
