## Why

Forklift already has two useful read-only command surfaces: `forklift changelog` for branch-level preflight analysis and `forklift clientlog` for run transcript inspection. There is still a missing operator question in day-to-day fork work: which files are still ours to shape without inviting avoidable upstream conflicts?

`forklift files` should answer that in present-tense terms, not historical authorship. Rebases destroy stable provenance for "who added this first," and that is not what the operator needs anyway. The useful contract is simpler: list paths that are absent from upstream right now, because those paths are generally safer places to make fork-specific changes.

## What Changes

- Add a new host-side read-only subcommand: `forklift files`.
- Reuse the existing branch input contract already used by `forklift` and `forklift changelog`: `--main-branch` with default `main`.
- Define fork-owned files as current paths that exist on the local fork branch but not on `upstream/<main-branch>`.
- Compute ownership from local refs only; this command does not fetch remotes and does not inspect uncommitted working tree files.
- Treat rename and copy rows using destination-path semantics, so a renamed or copied fork-only path is listed under its current path.
- Add optional `--hash` output that shows the short commit where the current path first appeared in `merge-base..<main-branch>`.
- Keep output plain and agent-friendly: either one path per line or `path<TAB>hash`; print `No fork-owned files.` when the set is empty.
- Refactor shared diff parsing as needed so `forklift files` and `forklift changelog` use the same current-path normalization rules for rename/copy rows.

## Capabilities

### New Capabilities
- `fork-owned-files`: Adds a read-only command that lists fork-only paths from local branch refs, with optional introduction hashes for the current path names.

### Modified Capabilities
- `forklift-orchestrator`: Clarifies that `forklift files` is a read-only command path outside the run-directory, container, and publication lifecycle.

## Impact

- Affected code:
  - `src/forklift/cli.py` (subcommand registration and command routing)
  - new command module(s) under `src/forklift/` for `forklift files`
  - `src/forklift/changelog_analysis.py` or a new shared diff helper module if current-path parsing is extracted for reuse
  - `src/forklift/git.py` if new read-only Git helpers are added there
  - existing unittest suite under `tests/`
  - `README.md` for command usage and semantics
- Affected user behavior:
  - new `forklift files` command
  - no change to existing orchestration, changelog, or clientlog behavior
- Dependencies/systems:
  - uses local Git CLI only
  - reads local branch refs such as `<main-branch>` and `upstream/<main-branch>`
  - introduces no new third-party dependencies
