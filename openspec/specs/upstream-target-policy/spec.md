# upstream-target-policy Specification

## Purpose
TBD - created by archiving change add-latest-version-rebase-policy. Update Purpose after archive.
## Requirements
### Requirement: Latest-version target policy selection
When the operator sets `--target-policy=latest-version`, the orchestrator SHALL resolve the upstream integration target from version tags instead of the upstream branch tip.

#### Scenario: Consider only stable version tag shapes
- **WHEN** evaluating candidate tags for `--target-policy=latest-version`
- **THEN** the orchestrator only considers tags in `X.Y.Z` and `vX.Y.Z` form where `X`, `Y`, and `Z` are non-negative integers

#### Scenario: Ignore unsupported version-like tags
- **WHEN** tags include pre-release or build-metadata suffixes (for example `v1.2.3-rc1` or `1.2.3+build7`)
- **THEN** those tags are ignored for v1 latest-version resolution

#### Scenario: Resolve latest stable version tag
- **WHEN** upstream contains tags matching `vX.Y.Z` and/or `X.Y.Z`
- **THEN** the orchestrator selects the highest semantic version tag and uses that tag's commit as the integration target

#### Scenario: Equivalent v-prefixed and unprefixed tags on same commit
- **WHEN** both `vX.Y.Z` and `X.Y.Z` exist for the same version number and resolve to the same commit
- **THEN** the orchestrator treats them as a single version candidate and continues without error

#### Scenario: Reject ambiguous equivalent tag names
- **WHEN** both `vX.Y.Z` and `X.Y.Z` exist for the same version number but point to different commits
- **THEN** the orchestrator exits non-zero with a fatal ambiguity error that includes both tag names and commit SHAs

#### Scenario: Reject missing version tags
- **WHEN** `--target-policy=latest-version` is set and no version tags match the supported pattern
- **THEN** the orchestrator exits non-zero with a fatal error indicating no upstream version tags were found

### Requirement: Deterministic latest-version ordering
Latest-version target resolution SHALL be deterministic across environments and SHALL NOT depend on host git sort configuration.

#### Scenario: Higher numeric patch wins
- **WHEN** candidate tags include `v1.2.9` and `v1.2.10`
- **THEN** the orchestrator selects `v1.2.10` as the latest version

#### Scenario: Higher numeric minor wins
- **WHEN** candidate tags include `v1.9.5` and `v1.10.0`
- **THEN** the orchestrator selects `v1.10.0` as the latest version

### Requirement: Default upstream-tip policy compatibility
When `--target-policy` is omitted or set to `tip`, the orchestrator SHALL preserve existing behavior and target the upstream branch tip.

#### Scenario: Tip policy selected
- **WHEN** the operator runs `forklift` with `--target-policy=tip` or omits `--target-policy`
- **THEN** the orchestrator resolves the integration target from `upstream/<main-branch>` tip

### Requirement: Pre-run no-op short-circuit
Before creating a run directory, the orchestrator SHALL check whether the selected upstream target is already reachable from the configured main branch and SHALL exit successfully without launching a container when no integration is needed.

#### Scenario: Upstream target already integrated
- **WHEN** `git merge-base --is-ancestor <selected-target-sha> <main-branch>` succeeds in the source repository
- **THEN** the orchestrator exits with success and skips run-directory creation and container launch

#### Scenario: No-op exit records reason
- **WHEN** the pre-run no-op short-circuit is taken
- **THEN** logs include the selected target policy and target SHA that caused the short-circuit

#### Scenario: Upstream target not yet integrated
- **WHEN** the selected target is not an ancestor of the configured main branch
- **THEN** the orchestrator continues with normal run preparation and container execution

