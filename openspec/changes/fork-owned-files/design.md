## Context

`forklift files` is meant to support a fork-specific workflow decision: prefer editing files the fork still owns, because those paths are less likely to conflict with future upstream syncs. That idea sounds like historical authorship at first, but rebases erase that story. After replaying commits onto newer upstream history, a path that was originally added in the fork may now appear as a modification if upstream later introduced the same file.

So this command cannot honestly answer "who created this file first?" from current Git history alone. The useful and stable question is narrower: which current paths are still absent from upstream?

This change also has a UX constraint: the output should be cheap to consume from both terminals and agents. No prose-heavy report, no markdown table by default, no working-tree noise.

## Goals / Non-Goals

**Goals:**
- Add `forklift files` as a host-side read-only subcommand.
- Use the same `--main-branch` contract as the existing command family.
- Define ownership in present-tense terms: paths absent from `upstream/<main-branch>` now.
- Include adds, renames, and copies using current/destination path semantics.
- Offer an optional short-hash column for when the current path first appeared on the fork side.
- Keep the output plain, stable, and easy to pipe into other tooling.
- Reuse existing diff parsing behavior instead of creating a parallel rename/copy convention.

**Non-Goals:**
- Recording or reconstructing historical file provenance across rebases.
- Fetching remotes as part of `forklift files`.
- Inspecting uncommitted, staged, or untracked working tree files.
- Producing markdown, Rich tables, or a narrative explanation report.
- Chasing rename ancestry beyond the current path name.

## Decisions

### 1. Define ownership as current fork-only paths, not historical provenance

Decision:
- A file is "owned by the fork" when its current path exists on `<main-branch>` but not on `upstream/<main-branch>`.

Why:
- This matches the operator goal: safer places to make fork-specific changes.
- It stays true after rebases, where first-introduction provenance is no longer recoverable from branch history alone.

Alternative considered:
- Define ownership as "first introduced by the fork historically."
- Rejected because rebases erase that fact unless Forklift starts persisting separate metadata.

### 2. Discover owned paths from the upstream-to-fork diff, not from merge-base-to-fork

Decision:
- Use the diff between `upstream/<main-branch>` and `<main-branch>` to decide whether a path is fork-only now.
- Keep only rows whose current-path status is `A`, `R`, or `C`.

Why:
- A merge-base-to-fork diff can lie for this command. If both upstream and the fork add the same path after divergence, `merge-base..<main-branch>` still shows the fork side as an add even though the path is no longer fork-only.
- Comparing directly against `upstream/<main-branch>` answers the present-tense exclusivity question honestly.

Alternative considered:
- Use `git diff --name-status <merge-base>..<main-branch>` and treat `A` as owned.
- Rejected because it misclassifies paths that upstream now also owns.

### 3. Reuse current-path diff semantics for rename and copy rows

Decision:
- Treat rename and copy rows by their destination path.
- Enable rename/copy detection in the ownership diff so copied paths can be reported as fork-owned when their current path is absent upstream.
- Refactor shared parsing as needed so `forklift files` and `forklift changelog` rely on the same normalization rules.

Why:
- The current path is what the operator edits now.
- The existing changelog diff parser already normalizes rename/copy rows to destination paths; reusing that behavior avoids a second convention.

Alternative considered:
- Exclude renames or copies to keep the list simpler.
- Rejected because fork-only renamed/copied paths are still safe edit territory and should be visible.

### 4. Define `--hash` as current-path introduction within `merge-base..<main-branch>`

Decision:
- `--hash` prints the short commit where the current path first appeared in the range `merge-base..<main-branch>`.
- For renamed or copied paths, the reported hash is the rename/copy commit that introduced that current path name, not the original content ancestor.

Why:
- This keeps the value local to the current path name and avoids rename chasing.
- It aligns with the operator mental model: when did this path enter the fork side in its current form?

Alternative considered:
- Follow rename ancestry back to the original add commit.
- Rejected because the command is about current safe paths, not archaeology.

### 5. Keep the command local-only, read-only, and plain-text by default

Decision:
- `forklift files` trusts local refs and does not fetch remotes.
- It ignores working tree state and looks only at committed branch history.
- Output is headerless plain text:
  - default: one path per line
  - `--hash`: `path<TAB>shortsha`
  - empty set: `No fork-owned files.`

Why:
- This keeps the command fast, predictable, and useful for agents.
- It also creates a clean separation from a future explicit fetch command.

Alternative considered:
- Auto-fetch before analysis or emit richer tabular output.
- Rejected because both add noise or side effects to a command that should be a cheap local inspection tool.

## Risks / Trade-offs

- [Stale local refs] → Because the command trusts local refs, stale `upstream/*` refs can produce stale ownership answers. Mitigation: document the contract plainly; do not hide it with implicit network activity.
- [Copy detection surprise] → Git does not always emit copy rows without explicit detection flags. Mitigation: enable rename/copy detection in the ownership diff.
- [Current-path semantics differ from provenance] → Users may initially assume the hash means original authorship. Mitigation: keep the flag name generic (`--hash`) and document that it refers to the current path's first appearance in `merge-base..<main-branch>`.
- [Shared helper extraction scope] → Pulling too much out of changelog analysis could create churn. Mitigation: extract only the current-path parsing seam that both commands actually need.

## Migration Plan

1. Add a new `forklift files` command module and wire it into `src/forklift/cli.py`.
2. Extract or reuse current-path diff parsing helpers so rename/copy destination-path handling is shared with changelog code.
3. Implement owned-path collection from `upstream/<main-branch>..<main-branch>` using `A`, `R`, and `C` statuses.
4. Add optional `--hash` lookup using `merge-base..<main-branch>` and current-path-only history lookup.
5. Add tests covering adds, renames, copies, shared-path false positives, empty output, and failure cases for missing refs.
6. Update `README.md` with command usage and the ownership philosophy.

Rollback strategy:
- The change is read-only and adds no persisted state. Reverting the command, docs, and shared helper extraction returns the CLI surface to its current behavior.

## Open Questions

- None. The ownership semantics, hash meaning, sort order, and output shape are decided for this change.
