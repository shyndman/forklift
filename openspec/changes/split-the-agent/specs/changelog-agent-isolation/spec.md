## ADDED Requirements

### Requirement: Upstream narrative synthesis SHALL be isolated from fork-aware evidence
`forklift changelog` SHALL generate `## Summary` and `## Key Change Arcs` from an upstream-only changelog evidence payload. The synthesis input for those sections MUST exclude fork-side conflict evidence, fork-side intent summaries, and full conflict-side comparison structures.

#### Scenario: Top-half report sections use upstream-only evidence
- **WHEN** changelog generation prepares the synthesis input for `## Summary` and `## Key Change Arcs`
- **THEN** that input contains only upstream-oriented changelog evidence derived from the selected local branch and `upstream/<main-branch>` comparison
- **AND** that input does not include fork-side conflict evidence or per-path fork/upstream comparison payloads

#### Scenario: Conflict evidence exists but top-half boundary holds
- **WHEN** merge-tree conflict analysis produces fork-aware comparison evidence for one or more paths
- **THEN** `## Summary` and `## Key Change Arcs` are still synthesized from the upstream-only payload
- **AND** the top-half synthesis input remains unable to describe fork-side behavior directly

### Requirement: Conflict and review synthesis SHALL remain full-context but section-scoped
`forklift changelog` SHALL generate `## Conflict Pair Evaluations` and `## Risk and Review Notes` from the full changelog evidence bundle, including fork-aware conflict-side comparisons when available. That synthesis step MUST be limited to those sections and MUST NOT author `## Summary` or `## Key Change Arcs`.

#### Scenario: Bottom-half sections use full conflict evidence
- **WHEN** changelog generation prepares synthesis input for `## Conflict Pair Evaluations` and `## Risk and Review Notes`
- **THEN** that input includes the full deterministic changelog evidence bundle used for conflict evaluation
- **AND** the resulting output covers only the conflict and review sections

#### Scenario: No conflict paths still preserves section ownership
- **WHEN** changelog generation finds zero conflict paths
- **THEN** the lower-half synthesis step still owns `## Conflict Pair Evaluations` and `## Risk and Review Notes`
- **AND** the upper-half synthesis step remains the only source of `## Summary` and `## Key Change Arcs`

### Requirement: Changelog output SHALL be assembled by the host from section-scoped results
The changelog command SHALL assemble the final markdown report on the host from separate section outputs rather than accepting one full-document narrative from a single agent. The final report MUST preserve this section order: `## Summary`, `## Key Change Arcs`, `## Conflict Pair Evaluations`, `## Risk and Review Notes`.

#### Scenario: Final report preserves the existing section order
- **WHEN** both changelog synthesis steps succeed
- **THEN** the host assembles one markdown report containing all four sections in the fixed order
- **AND** each section body comes from the synthesis step that owns that section

#### Scenario: Either synthesis step fails
- **WHEN** the upstream-only synthesis step or the full-context synthesis step fails
- **THEN** `forklift changelog` exits non-zero
- **AND** no partial changelog markdown is rendered

### Requirement: Changelog usage reporting SHALL aggregate both synthesis steps
The changelog command SHALL report one usage summary for the entire command, with token counts and estimated cost aggregated across both synthesis steps.

#### Scenario: Both synthesis steps return usage data
- **WHEN** changelog generation completes successfully after running both synthesis steps
- **THEN** the post-run usage summary reports combined token totals and combined estimated cost for the command
- **AND** the wall-clock duration reflects the full command execution rather than either synthesis step in isolation
