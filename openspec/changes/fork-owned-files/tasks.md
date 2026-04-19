## 1. Add the `forklift files` command surface

- [ ] 1.1 Create a new command module under `src/forklift/` for `forklift files`.
- [ ] 1.2 Add `--main-branch` (default `main`) and `--hash` flags to the command.
- [ ] 1.3 Register the subcommand in `src/forklift/cli.py` without changing existing `forklift`, `changelog`, or `clientlog` behavior.
- [ ] 1.4 Add a CLI parsing test proving `Forklift.parse(["files"])` routes to the new command class.

## 2. Reuse current-path diff parsing semantics

- [ ] 2.1 Extract or reuse the existing rename/copy destination-path normalization currently living in `src/forklift/changelog_analysis.py`.
- [ ] 2.2 Ensure the shared parsing path returns current-path entries suitable for both changelog analysis and `forklift files`.
- [ ] 2.3 Add parser tests covering add, rename, copy, and brace-style rename rows.

## 3. Implement owned-path analysis

- [ ] 3.1 Resolve the local branch pair `<main-branch>` and `upstream/<main-branch>` without fetching remotes.
- [ ] 3.2 Fail non-zero when required local refs are missing or `git merge-base` cannot be computed.
- [ ] 3.3 Collect ownership rows from the diff `upstream/<main-branch>..<main-branch>` with rename/copy detection enabled.
- [ ] 3.4 Keep only current-path rows with statuses `A`, `R`, or `C`, de-duplicate by path, and sort alphabetically.
- [ ] 3.5 Ignore uncommitted working tree state entirely.
- [ ] 3.6 Add tests proving the command does not misclassify paths that both upstream and the fork introduced after divergence.

## 4. Implement optional `--hash` output

- [ ] 4.1 Compute merge base for `<main-branch>` and `upstream/<main-branch>`.
- [ ] 4.2 For each owned path, find the first commit in `merge-base..<main-branch>` where the current path appears.
- [ ] 4.3 Print `path<TAB>shortsha` when `--hash` is set.
- [ ] 4.4 Add tests covering add, rename, and copy cases where the reported short SHA reflects the current path's first appearance, not rename ancestry.

## 5. Lock down output and read-only behavior

- [ ] 5.1 Print one path per line by default, with no headers.
- [ ] 5.2 Print `No fork-owned files.` when the result set is empty.
- [ ] 5.3 Ensure the command never creates run directories, updates run-state files, launches containers, fetches remotes, or mutates the working tree.
- [ ] 5.4 Add targeted integration tests covering default output, `--hash` output, empty output, and missing-ref failure behavior.

## 6. Update documentation

- [ ] 6.1 Update `README.md` with `forklift files` usage examples.
- [ ] 6.2 Document the command philosophy briefly: fork-owned files are absent from upstream and are generally safer places for fork-specific changes.
- [ ] 6.3 Document that `forklift files` trusts local refs and ignores uncommitted files.
