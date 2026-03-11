## ADDED Requirements

### Requirement: Changelog command performs read-only preflight analysis
The CLI SHALL provide `forklift changelog` as a read-only command that compares a local main branch to its upstream tracking branch and reports net code-change information plus predicted integration hotspots.

#### Scenario: Default branch analysis
- **WHEN** the user runs `forklift changelog` without overriding branch flags
- **THEN** the command analyzes `main` and `upstream/main`
- **AND** the command does not perform merge, rebase, cherry-pick, container launch, or run-directory preparation

#### Scenario: Custom main branch analysis
- **WHEN** the user runs `forklift changelog --main-branch dev`
- **THEN** the command analyzes `dev` and `upstream/dev`
- **AND** all other behavior remains read-only

### Requirement: Changelog command refreshes remotes before analysis
Before collecting changelog evidence, the command SHALL validate required remotes and fetch both `origin` and `upstream`.

#### Scenario: Successful remote refresh
- **WHEN** `origin` and `upstream` are configured and reachable
- **THEN** the command fetches both remotes before running merge-base, merge-tree, or diff analysis

#### Scenario: Remote validation failure
- **WHEN** either `origin` or `upstream` is missing
- **THEN** the command exits non-zero with a clear configuration error
- **AND** no changelog markdown is printed

### Requirement: Changelog command SHALL require modern git-merge-tree support
Before running merge-tree analysis, the command SHALL verify host Git version compatibility for modern merge-tree mode (Git 2.38 or newer).

#### Scenario: Compatible git version
- **WHEN** host Git is version 2.38 or newer
- **THEN** the command proceeds with merge-base and merge-tree analysis

#### Scenario: Incompatible git version
- **WHEN** host Git is older than version 2.38
- **THEN** the command exits non-zero with guidance to upgrade Git
- **AND** the command does not attempt merge-tree conflict prediction

### Requirement: Deterministic evidence SHALL be computed from explicit Git commands
The command SHALL derive analysis evidence using deterministic Git command output for the selected branch pair. At minimum, evidence SHALL include merge base SHA, diff summary metrics, and changed-file metadata.

#### Scenario: Evidence bundle creation
- **WHEN** branch refs resolve successfully
- **THEN** the command computes merge base using `git merge-base <main-branch> upstream/<main-branch>`
- **AND** collects summary metrics from diff commands over `<main-branch>...upstream/<main-branch>`

### Requirement: Conflict hotspots SHALL come from merge-tree output
The command SHALL derive predicted conflict locations from modern merge-tree output (`git merge-tree --write-tree ...`). Reported path names and conflict counts MUST be parsed from merge-tree **Conflicted file info** entries (`<mode> <object> <stage> <filename>`) rather than inferred by the LLM or extracted from free-form informational messages.

#### Scenario: Predicted conflicts reported
- **WHEN** merge-tree returns exit status `1` and Conflicted file info entries for one or more paths
- **THEN** the output includes each conflicted path with an associated conflict occurrence count

#### Scenario: No conflicts predicted
- **WHEN** merge-tree returns exit status `0`
- **THEN** the output explicitly states that no hotspot paths were detected for the analyzed tips

#### Scenario: Merge-tree fatal error
- **WHEN** merge-tree returns an exit status greater than `1`
- **THEN** the command exits non-zero with a merge-tree execution/parsing error
- **AND** the command does not emit partial hotspot predictions from ambiguous output

### Requirement: Narrative summary SHALL be LLM-generated from deterministic evidence
The command SHALL send deterministic evidence to the configured model and use the model response as the narrative summary section of the changelog.

#### Scenario: Narrative generation succeeds
- **WHEN** deterministic evidence collection succeeds and model invocation returns content
- **THEN** the final markdown output includes model-authored narrative text describing net branch differences

#### Scenario: Narrative generation fails
- **WHEN** model invocation fails because of configuration, authentication, transport, or runtime errors
- **THEN** the command exits non-zero
- **AND** the command MUST NOT silently replace the narrative with deterministic-only fallback prose

### Requirement: Output format SHALL be Rich-rendered markdown with fixed sections
The command SHALL render Markdown in terminal output using Rich. The output SHALL include fixed top-level sections so operators can compare multiple runs consistently.

#### Scenario: Required markdown sections
- **WHEN** changelog generation succeeds
- **THEN** output includes sections for branch context, narrative summary, predicted conflict hotspots, and deterministic supporting metrics
- **AND** the hotspot section includes a caveat that tip-merge hotspot predictions may repeat during later commit-by-commit rebases

### Requirement: Changelog execution SHALL avoid local working-tree mutation
The command SHALL not modify tracked files, current checked-out branch, or local commit history. Remote-tracking refs MAY update due to fetch.

#### Scenario: Local repository state preserved
- **WHEN** the user runs `forklift changelog` in a clean repository
- **THEN** local tracked-file status remains clean after command completion
- **AND** the checked-out local branch remains unchanged
