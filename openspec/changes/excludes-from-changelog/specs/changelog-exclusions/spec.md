## ADDED Requirements

### Requirement: Changelog exclusions are configurable in FORK metadata
`forklift changelog` SHALL load optional exclusion rules from `FORK.md` front matter at key path `changelog.exclude` when present. The value MUST be an ordered list of non-empty string patterns.

#### Scenario: Exclusion rules are present
- **WHEN** `FORK.md` includes `changelog.exclude` with one or more string patterns
- **THEN** changelog analysis uses those patterns as the active exclusion rule set in declared order

#### Scenario: Exclusion rules are absent
- **WHEN** `FORK.md` is missing or has no `changelog.exclude` metadata
- **THEN** changelog analysis runs with an empty exclusion rule set

### Requirement: Exclusion matching uses gitignore-style ordered semantics
The changelog exclusion engine SHALL evaluate repo-relative paths using gitignore-style pattern behavior with ordered rules, `!` negation, and last-match-wins resolution.

#### Scenario: Negation re-includes a previously excluded path
- **WHEN** active rules include `generated/**` followed later by `!generated/keep.json`
- **THEN** `generated/keep.json` is included in filtered analysis while other matching generated paths remain excluded

#### Scenario: Rename paths use destination semantics for matching
- **WHEN** a changed file is represented as a rename or copy with old and new paths
- **THEN** exclusion matching evaluates the destination path only

### Requirement: Deterministic outputs include baseline and filtered metrics
The changelog renderer SHALL present deterministic supporting metrics as a comparison between baseline totals (all files) and filtered totals (after exclusions), including deltas.

#### Scenario: Comparative metrics are rendered
- **WHEN** changelog generation succeeds with active exclusion rules
- **THEN** deterministic output includes baseline, filtered, and delta values for files changed, insertions, and deletions

### Requirement: Exclusion effects are transparent and consistent across deterministic sections
The command SHALL apply exclusions consistently to deterministic hotspot and changed-file sections, and SHALL report active rules with aggregate exclusion match counts.

#### Scenario: Excluded paths do not appear in filtered deterministic sections
- **WHEN** a path matches the final exclusion state after rule evaluation
- **THEN** the path is omitted from filtered hotspot and top-changed-file sections, and exclusion metadata reports that it was matched
