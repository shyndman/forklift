## ADDED Requirements

### Requirement: Changelog SHALL evaluate both sides of each merge-tree conflict path
`forklift changelog` SHALL generate conflict-side evaluations only for paths predicted as conflicted by merge-tree analysis after exclusion filtering.

#### Scenario: Evaluations are scoped to merge-tree conflicts
- **GIVEN** merge-tree returns conflict paths `A`, `B`, and `C`
- **AND** exclusion rules remove `C`
- **WHEN** changelog evidence is built
- **THEN** conflict-side evaluations are produced for `A` and `B` only
- **AND** no evaluation is produced for `C`

#### Scenario: No conflicts yields no side evaluations
- **GIVEN** merge-tree returns zero conflict paths
- **WHEN** changelog is rendered
- **THEN** the existing no-hotspot message is shown
- **AND** no conflict-side evaluation subsections are emitted

### Requirement: Conflict-side evidence SHALL include bounded fork/upstream context with hunk headers
For each merge-tree conflict path, deterministic evidence SHALL collect side-specific context from both ranges (`base..main` and `base..upstream/<main>`): representative commit subjects, side-local churn totals, and diff hunk headers (`@@ ... @@`).

#### Scenario: Side evidence is present for a conflicted path
- **GIVEN** conflict path `src/x.py` has commits on both sides
- **WHEN** side evidence is collected
- **THEN** fork-side commit samples are present
- **AND** upstream-side commit samples are present
- **AND** fork/upstream hunk-header samples include only `@@ ... @@` lines

#### Scenario: One side has sparse evidence
- **GIVEN** conflict path `src/y.py` has commits on fork side but none on upstream side
- **WHEN** evidence is collected
- **THEN** evaluation still exists for `src/y.py`
- **AND** upstream side is represented as sparse/empty evidence (not silently dropped)

### Requirement: Conflict-side evaluations SHALL preserve mechanical ordering and truncation transparency
Conflict-side sections SHALL be ordered by `conflict_count` descending and path ascending for ties, and SHALL explicitly show truncation counts whenever configured caps limit evidence.

#### Scenario: Mechanical ordering is deterministic
- **GIVEN** three conflict paths with counts:
  - `b.py` = 5
  - `a.py` = 5
  - `c.py` = 2
- **WHEN** changelog is rendered
- **THEN** conflict-side evaluation order is:
  1. `a.py` (count 5, lexicographic tie-break)
  2. `b.py` (count 5)
  3. `c.py` (count 2)

#### Scenario: Truncation notices are rendered when caps are hit
- **GIVEN** a cap allows 3 commit samples but 8 are available
- **WHEN** changelog is rendered
- **THEN** it includes `3/8 (cap 3)` for that truncated evidence dimension
- **AND** it includes a warning that additional evidence exists beyond configured limits

### Requirement: Narrative output SHALL include conflict pair evaluations grounded in deterministic evidence
The narrative contract SHALL require `## Conflict Pair Evaluations` and one subsection per evaluated conflict path, with fork intent, upstream intent, conceptual relationship, and merge-discussion starters.

#### Scenario: Narrative includes per-path conceptual comparison
- **GIVEN** deterministic side evidence is available for path `src/session/store.py`
- **WHEN** narrative is generated
- **THEN** the output contains `## Conflict Pair Evaluations`
- **AND** a subsection for `src/session/store.py`
- **AND** that subsection includes all four elements:
  1. fork-side intent
  2. upstream-side intent
  3. conceptual relationship
  4. merge discussion starters

#### Scenario: Insufficient evidence is called out explicitly
- **GIVEN** deterministic evidence for a path is too sparse for reliable conceptual interpretation
- **WHEN** narrative is generated
- **THEN** that path section explicitly states insufficient evidence
- **AND** it does not assert unsupported behavioral changes
