## ADDED Requirements

### Requirement: Changelog SHALL evaluate both sides of each merge-tree conflict path
`forklift changelog` SHALL generate conflict-side evaluations only for paths predicted as conflicted by merge-tree analysis after exclusion filtering.

#### Scenario: Evaluations are scoped to merge-tree conflicts
- **GIVEN** merge-tree returns conflict paths `A`, `B`, and `C`
- **AND** exclusion rules remove `C`
- **WHEN** changelog evidence is built
- **THEN** conflict-pair evaluations are produced for `A` and `B` only
- **AND** no evaluation is produced for `C`

#### Scenario: No conflicts yields no side evaluations
- **GIVEN** merge-tree returns zero conflict paths
- **WHEN** changelog is rendered
- **THEN** the existing no-hotspot message is shown
- **AND** no conflict-pair subsections are emitted

### Requirement: Conflict-pair summaries SHALL be grounded in deterministic side evidence
For each merge-tree conflict path, deterministic analysis SHALL collect side-specific context from both ranges (`base..main` and `base..upstream/<main>`) so the narrative can explain the feature or behavior on each side.

#### Scenario: Internal evidence supports a conceptual summary
- **GIVEN** conflict path `src/x.py` has side-specific evidence on both branches
- **WHEN** narrative is generated
- **THEN** the path subsection explains what fork side is changing in plain English
- **AND** it explains what upstream side is changing in plain English
- **AND** it does not expose the raw evidence structure in default output

#### Scenario: One side has sparse evidence
- **GIVEN** conflict path `src/y.py` has limited evidence on upstream side
- **WHEN** narrative is generated
- **THEN** the path subsection still exists
- **AND** the upstream summary explicitly says there is insufficient evidence if exact behavior cannot be explained safely

### Requirement: Conflict-pair evaluations SHALL preserve mechanical ordering
Conflict-pair sections SHALL be ordered by `conflict_count` descending and path ascending for ties.

#### Scenario: Mechanical ordering is deterministic
- **GIVEN** three conflict paths with counts:
  - `b.py` = 5
  - `a.py` = 5
  - `c.py` = 2
- **WHEN** changelog is rendered
- **THEN** conflict-pair evaluation order is:
  1. `a.py` (count 5, lexicographic tie-break)
  2. `b.py` (count 5)
  3. `c.py` (count 2)

### Requirement: Narrative output SHALL explain jargon and provide detailed upstream intent
The narrative contract SHALL require `## Conflict Pair Evaluations` and one subsection per evaluated conflict path, with plain-English feature explanations instead of unexplained repo-local labels. When evidence supports it, `Upstream-side intent` SHALL be written as a short paragraph rather than a one-line sentence fragment.

#### Scenario: Narrative explains an internal feature name
- **GIVEN** deterministic evidence includes an internal term such as a prompt or action name
- **WHEN** narrative is generated
- **THEN** the summary explains what that term does in behavior-level language
- **AND** it does not leave the internal label unexplained

#### Scenario: Upstream intent receives paragraph-level explanation
- **GIVEN** deterministic evidence contains enough detail to explain the upstream feature
- **WHEN** narrative is generated
- **THEN** the `Upstream-side intent` field is a short paragraph
- **AND** it gives more than a one-line label for the upstream change

#### Scenario: Insufficient evidence is called out explicitly
- **GIVEN** deterministic evidence for a path is too sparse for reliable conceptual interpretation
- **WHEN** narrative is generated
- **THEN** that path section explicitly states insufficient evidence
- **AND** it does not assert unsupported behavioral changes
