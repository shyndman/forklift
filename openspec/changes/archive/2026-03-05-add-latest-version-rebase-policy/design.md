## Context

Forklift currently resolves exactly one upstream integration target: `upstream/<main-branch>` tip. That target is captured in run metadata, seeded into the detached workspace, used by harness instructions (`git rebase upstream/<branch>`), and re-used for post-run verification and rewrite anchoring. This change introduces a policy selector (`--target-policy=<tip|latest-version>`) while preserving the existing execution path by aliasing the resolved target commit back into the same synthetic upstream ref.

Current control-flow path (important for implementation order):
- `src/forklift/cli.py`: parses CLI args, orchestrates fetch + run-manager + container + post-run processing.
- `src/forklift/run_manager.py`: captures upstream SHA metadata and seeds synthetic upstream ref in workspace.
- `src/forklift/cli_post_run.py`: verifies ancestor relationship after container exits.
- `src/forklift/cli_authorship.py`: uses upstream ref as rewrite boundary anchor.
- `src/forklift/git.py`: centralized git subprocess helpers.

Key constraints:
- Keep default behavior unchanged when `--target-policy` is omitted.
- Accept version tags with or without `v` prefix.
- Support only stable version tags (`X.Y.Z` or `vX.Y.Z`) in v1; reject pre-release/build tags.
- Avoid ambiguous target selection and avoid silent fallback.
- Treat missing/ambiguous version resolution in `latest-version` mode as a fatal error.
- Detect no-op integrations before container startup to save time and avoid unnecessary run artifacts.

## Goals / Non-Goals

**Goals:**
- Add a CLI policy option (`--target-policy=<tip|latest-version>`) that selects either upstream tip or latest upstream version tag commit.
- Reuse existing sandbox/harness mechanics by seeding `refs/remotes/upstream/<branch>` to the resolved target commit (alias policy).
- Add deterministic pre-run no-op detection: exit success when selected target is already reachable from local main branch.
- Persist/log policy selection and resolved target metadata for traceability.

**Non-Goals:**
- Introducing arbitrary tag selection or user-provided tag filters.
- Changing the default integration policy away from upstream tip.
- Auto-fallback from latest-version mode to upstream tip on resolution errors.
- Changing publication/rewrite behavior beyond using the selected target anchor.

## Decisions

1. **Policy selection via explicit enum CLI option**
   - Decision: add `--target-policy=<tip|latest-version>` and keep `tip` as the default.
   - Rationale: clearer contract, easier future extension, and avoids introducing additional one-off flags.
   - Alternatives considered:
     - `--to-latest-version`: smaller immediate surface but accumulates flags as policies grow.

2. **Alias selected target into existing synthetic upstream ref**
   - Decision: regardless of policy, seed `refs/remotes/upstream/<main-branch>` to the selected target SHA in run workspace.
   - Rationale: minimizes churn across harness instructions, rewrite boundaries, and verification code.
   - Alternatives considered:
     - Introduce new explicit workspace refs for tag mode: clearer semantics but significantly broader implementation impact.

3. **Strict version-tag resolution semantics**
   - Decision: treat tags matching `v?X.Y.Z` as candidates, pick latest by version ordering, and hard-fail on ambiguous equivalent versions pointing to different SHAs.
   - Rationale: avoids nondeterministic rebases and keeps operator intent explicit.
   - Alternatives considered:
     - Lexicographic sort: incorrect ordering (e.g., `1.10.0` vs `1.9.0`).
     - Silent winner on ambiguity: unsafe and hard to audit.

4. **Pre-container no-op check in host repo**
   - Decision: before run directory creation/container launch, verify selected target is already ancestor of configured main branch and short-circuit success when true.
   - Rationale: avoids spending orchestration/runtime budget when integration is unnecessary.
   - Alternatives considered:
     - Keep existing post-run-only skip: still wastes container runs.

5. **Fail closed in latest-version mode**
   - Decision: if version resolution fails (including no matching tags), exit non-zero with actionable diagnostics.
   - Rationale: explicit policy should not silently degrade to different semantics.
   - Alternatives considered:
     - Fallback to tip: surprising behavior that masks configuration intent.

6. **Hybrid test strategy: unit + real-git integration**
   - Decision: cover control-flow/policy parsing with mocked unit tests, and validate tag/ref semantics with integration tests that create temporary git repositories and run real git commands.
   - Rationale: this feature's correctness depends on git behavior (`tag`, `rev-parse`, `merge-base`) that mocks can accidentally misrepresent.
   - Alternatives considered:
     - Mock-only tests: faster but high risk of false confidence around ref/tag edge cases.

7. **Deterministic version resolution rules for junior-friendly implementation**
   - Decision: parse tags into `(major, minor, patch, canonical_tag_name, sha)` candidates and sort by `(major, minor, patch)` descending.
   - Rationale: avoids relying on implicit git config or shell sorting behavior.
   - Tie-break rules:
     - If both `vX.Y.Z` and `X.Y.Z` exist and point to the same SHA, treat as one version candidate.
     - If both exist and point to different SHAs, fail fatally with both names and SHAs.
     - If multiple tags for different versions point to same SHA, highest version still wins.

## Risks / Trade-offs

- **[Risk] Tag fetch incompleteness** → Mitigation: ensure upstream fetch path includes tag visibility needed for resolution and emit diagnostics when candidates are absent.
- **[Risk] Semver edge cases (pre-release/build metadata)** → Mitigation: document supported version pattern explicitly for v1; reject unsupported formats with clear error text.
- **[Risk] Operator confusion because `upstream/<branch>` may point to tag commit in latest-version mode** → Mitigation: log policy + resolved tag + SHA and persist metadata fields so audit trail is explicit.
- **[Trade-off] Enum option is slightly more verbose than a one-off flag** → Accepted to make policy selection explicit and future-proof.
- **[Trade-off] Git-backed integration tests are slower and more setup-heavy** → Accepted to guarantee behavior against real git semantics; keep scope focused on critical tag/no-op paths.


## Migration Plan

1. Add `--target-policy=<tip|latest-version>` parsing and policy resolution utilities in host orchestration path.
2. Extend metadata payload to include selected policy, resolved target SHA, and resolved tag (when applicable).
3. Wire pre-run no-op check before run directory preparation.
4. Update run-manager seeding to use selected target SHA under existing synthetic upstream ref.
5. Update/extend tests with a split strategy: unit tests for policy wiring/fatal errors plus git-backed integration tests (temp repos) for latest-version resolution, ambiguity, no-tag failure, and pre-run no-op exit.
6. Update docs/specs to describe policy and no-op behavior.

## Implementation Blueprint (Step-by-Step)

1. **Add policy parsing in CLI**
   - File: `src/forklift/cli.py`
   - Add enum-like argument validation for `--target-policy` with allowed values `tip` and `latest-version`.
   - Default to `tip` if omitted.
   - Fail fast with `SystemExit(1)` and a clear error log if value is invalid.

2. **Resolve selected upstream target before run creation**
   - Files: `src/forklift/cli.py`, `src/forklift/git.py`
   - For `tip`: resolve target SHA from `upstream/<main-branch>` exactly as today.
   - For `latest-version`: list candidate tags, filter to stable `v?X.Y.Z`, resolve SHAs, apply ambiguity rules, and pick highest version.
   - Return structured target info `{policy, target_ref_label, target_sha, resolved_tag?}`.

3. **Pre-run no-op gate**
   - File: `src/forklift/cli.py`
   - Before `RunDirectoryManager.prepare(...)`, run `merge-base --is-ancestor <target_sha> <main-branch>` in source repo.
   - If ancestor check succeeds: log skip reason (policy + target), exit success, and do not create run directory.
   - If ancestor check fails: continue normal orchestration.

4. **Seed workspace upstream alias from selected target**
   - File: `src/forklift/run_manager.py`
   - Replace hardcoded upstream-tip SHA source with selected target SHA passed from CLI.
   - Continue seeding `refs/remotes/upstream/<main-branch>` and helper branch name exactly as current behavior.
   - Persist metadata fields for `target_policy`, `target_sha`, and `target_tag` (nullable).

5. **Keep post-run verification/rewrite aligned to the selected alias**
   - Files: `src/forklift/cli_post_run.py`, `src/forklift/cli_authorship.py`
   - Verify ancestor relationship against the same seeded `upstream/<main-branch>` alias.
   - Ensure rewrite anchor resolution still uses that same alias reference so boundaries stay consistent.

6. **Update docs/harness language**
   - Files: `README.md`, `docker/kitchen-sink/harness/run.sh` (if instructions mention policy context)
   - Explain `--target-policy`, fatal failure behavior in latest-version mode, and pre-run no-op exit.

## Test Blueprint (Exact Scope)

### Unit tests (mocked git calls)
- Policy parsing defaults to `tip`.
- Explicit `--target-policy=tip` and `--target-policy=latest-version` branch correctly.
- Invalid policy value exits with `SystemExit(1)`.
- Fatal resolution errors (no version tags, ambiguous equivalent tags) propagate with non-zero exit.

### Git-backed integration tests (temp repositories)
- Build repositories in temporary directories with real `git init`, commits, branches, and tags.
- Cases:
  1. Latest stable tag selection chooses highest semantic version.
  2. `vX.Y.Z` + `X.Y.Z` same SHA is accepted as non-ambiguous.
  3. `vX.Y.Z` + `X.Y.Z` different SHAs fails fatally.
  4. No stable tags in latest-version mode fails fatally.
  5. Pre-run no-op: target SHA already ancestor of main branch skips run creation/container launch.
  6. Non-no-op: target not ancestor continues into normal run setup.

### Completion criteria for testing
- Every new policy branch has at least one unit test and one assertion on emitted logs or exit behavior.
- Every git semantic rule above is covered by integration tests.
- Existing tip policy tests continue passing unchanged.

## Open Questions

- None for implementation. This change intentionally supports stable tags only (`X.Y.Z` and `vX.Y.Z`) and treats pre-release/build tags as unsupported in v1.
