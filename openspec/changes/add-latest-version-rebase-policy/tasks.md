## 1. CLI argument plumbing and validation

- [ ] 1.1 Add `target_policy` CLI option to `Forklift` in `src/forklift/cli.py` with allowed values `tip` and `latest-version`.
- [ ] 1.2 Default `target_policy` to `tip` when the flag is omitted.
- [ ] 1.3 Add explicit validation helper (similar style to existing `resolved_main_branch`) that exits with `SystemExit(1)` on invalid policy values.
- [ ] 1.4 Ensure validated policy value is threaded into the orchestration path before run-directory creation.

## 2. Upstream target resolution implementation

- [ ] 2.1 Add git helper(s) in `src/forklift/git.py` to list candidate tags and resolve tag names to SHAs.
- [ ] 2.2 Implement stable-tag parser for only `X.Y.Z` and `vX.Y.Z` formats; ignore pre-release/build-metadata tags in v1.
- [ ] 2.3 Implement deterministic version comparison logic using numeric `(major, minor, patch)` ordering.
- [ ] 2.4 Implement equivalent-tag handling: allow `vX.Y.Z` + `X.Y.Z` when SHAs match.
- [ ] 2.5 Implement fatal ambiguity handling when `vX.Y.Z` + `X.Y.Z` for same version resolve to different SHAs.
- [ ] 2.6 Implement fatal no-tag handling when latest-version mode has zero supported stable tags.
- [ ] 2.7 Return structured resolution payload (policy, selected SHA, selected tag or null) for downstream logging/metadata.

## 3. Pre-run no-op gate and run metadata wiring

- [ ] 3.1 In `src/forklift/cli.py`, add a pre-run ancestor check (`merge-base --is-ancestor <target_sha> <main_branch>`) before `RunDirectoryManager.prepare(...)`.
- [ ] 3.2 If the pre-run ancestor check succeeds, exit success immediately without creating run directory or launching container.
- [ ] 3.3 If the pre-run ancestor check fails, continue existing orchestration flow unchanged.
- [ ] 3.4 Update run metadata write path to persist `target_policy`, `target_sha`, and nullable `target_tag`.
- [ ] 3.5 Update `src/forklift/run_manager.py` to seed `refs/remotes/upstream/<main-branch>` from resolved target SHA (not always upstream branch tip SHA).
- [ ] 3.6 Confirm `src/forklift/cli_post_run.py` and `src/forklift/cli_authorship.py` continue using the seeded upstream alias consistently.

## 4. Logging and operator-facing behavior

- [ ] 4.1 Add log event when target resolution succeeds (include policy, selected SHA, and selected tag when present).
- [ ] 4.2 Add log event when pre-run no-op short-circuit is taken (include policy + selected SHA).
- [ ] 4.3 Ensure fatal latest-version failures (no supported tags, ambiguous equivalent tags) produce actionable error logs.

## 5. Tests: fast unit layer (mocked git)

- [ ] 5.1 Add unit test for default policy (`tip`) when flag is omitted.
- [ ] 5.2 Add unit test for explicit `--target-policy=tip`.
- [ ] 5.3 Add unit test for explicit `--target-policy=latest-version`.
- [ ] 5.4 Add unit test for invalid policy value causing `SystemExit(1)`.
- [ ] 5.5 Add unit test for fatal no-tag resolution error propagation.
- [ ] 5.6 Add unit test for fatal ambiguous-equivalent-tag error propagation.

## 6. Tests: git-backed integration layer (real repositories)

- [ ] 6.1 Add integration test helper that creates temporary git repos with commits, branches, and tags using real `git` commands.
- [ ] 6.2 Add integration test: highest stable version is selected (`v1.2.10` beats `v1.2.9`).
- [ ] 6.3 Add integration test: `vX.Y.Z` and `X.Y.Z` on same SHA is accepted.
- [ ] 6.4 Add integration test: `vX.Y.Z` and `X.Y.Z` on different SHAs fails fatally.
- [ ] 6.5 Add integration test: no supported stable tags in latest-version mode fails fatally.
- [ ] 6.6 Add integration test: pre-run no-op path skips run creation and container launch.
- [ ] 6.7 Add integration test: non-no-op path proceeds into run preparation.

## 7. Docs, harness, and verification

- [ ] 7.1 Update `README.md` with `--target-policy` usage and stable-tag-only v1 behavior.
- [ ] 7.2 Document fatal behavior for missing/ambiguous latest-version resolution in README.
- [ ] 7.3 Update harness instruction text in `docker/kitchen-sink/harness/run.sh` only if policy context is displayed there.
- [ ] 7.4 Rebuild kitchen-sink image after any harness edits: `docker build -t forklift/kitchen-sink:latest docker/kitchen-sink`.
- [ ] 7.5 Run the targeted updated tests and record pass/fail evidence in implementation notes.
