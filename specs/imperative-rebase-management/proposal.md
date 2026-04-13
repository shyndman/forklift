## Why

Forklift currently leaves in-container rebase control entirely to the agent. That gets upstream integrated, but it does not reliably enforce repo-specific continuability checks, so formatting, lint, generated-file, and similar failures can slip through conflict resolution and land in the final tree.

This change makes Forklift own the critical rebase transition points so every `git rebase --continue` is gated by repo-authored policy, while still letting agents perform normal Git conflict work. It also fixes a related operator experience problem: setup and future rebase-gate failures need to appear in the top-level run log instead of being buried in side log files.

## What Changes

- Add strict `rebase.continue_check` front matter support in `FORK.md` as a single shell string that defines the repo-authored continuability gate for rebases.
- Snapshot the active rebase gate at harness startup into immutable run state so agents cannot change enforcement mid-run by editing the workspace copy of `FORK.md`.
- Intercept `git rebase --continue` inside the container, run the snapped `continue_check`, and only allow the real continue when the command exits zero and leaves tracked, staged, and untracked workspace state unchanged.
- Surface full setup and rebase-gate output in the top-level run log instead of relegating those failures to separate `setup.log` or rebase-specific side logs.
- Auto-skip mechanically empty rebase commits without recording them, but allow agent-initiated skips for non-empty cases and surface those skipped commits in the final completion report.
- Require `STUCK.md` to exist before allowing `git rebase --abort`, and treat an allowed abort as a stuck outcome.
- Always render a `Skipped Commits:` section in the final completion report, using `None` when no agent-initiated skips occurred.

## Capabilities

### New Capabilities
- `imperative-rebase-management`: Harness-mediated rebase control that snapshots repo-defined continue checks, gates `rebase --continue`, auto-handles mechanically empty skips, records agent-directed skips, and requires `STUCK.md` before abort.

### Modified Capabilities
- `agent-sandbox-run`: `FORK.md` front matter grows a strict `rebase.continue_check` key, rebase control moves from pure agent convention to harness-enforced transition points, and setup/rebase gate diagnostics move into the primary run log.
- `opencode-agent-bridge`: The harness-owned command environment now mediates selected Git rebase subcommands while preserving normal Git access and the existing transcript split between top-level run logs and deep agent transcript logs.

## Impact

- Affected harness files: `docker/kitchen-sink/harness/run.sh` and helper scripts under `docker/kitchen-sink/harness/includes/`.
- Affected host orchestration/reporting: CLI summary and log-surfacing behavior in `src/forklift/cli.py`, post-run reporting, and any completion-report renderer that emits final operator-visible status.
- Affected docs/specs: `FORK.md`, `README.md`, and OpenSpec capability documents for sandbox run and agent bridge behavior.
- Runtime behavior changes by default from day one: all rebases run through harness-mediated continue/skip/abort handling, while the repo-defined continue gate is enforced only when `rebase.continue_check` is present.
