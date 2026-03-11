## Context

`forklift changelog` already computes deterministic evidence (`merge-tree` hotspots and diff summaries) and then renders operator-facing Markdown. Today that deterministic layer has no notion of ignore policy, so high-churn generated assets dominate both metrics and narrative context.

At the same time, `FORK.md` front matter is currently strict in harness parsing and only accepts `setup`. That strictness is valuable (fail closed, predictable parsing), but it means introducing changelog policy in `FORK.md` requires an explicit schema extension so normal orchestrator runs do not fail when new metadata is present.

Stakeholders:
- Operators who need signal-heavy changelog reports
- Repo maintainers who want repo-level policy in version control
- Maintainers of harness/front-matter contract who need backwards-compatible strict parsing

## Goals / Non-Goals

**Goals:**
- Add repo-level changelog exclusion rules in `FORK.md` front matter.
- Use gitignore-style ordered matching with `!` negation and last-match-wins behavior.
- Apply exclusions consistently to deterministic metrics and conflict hotspot output.
- Render comparative deterministic metrics: baseline (`all files`) vs filtered (`excluding patterns`) vs delta.
- Make filtering auditable by listing active rules and matched-file counts.
- Normalize rename/copy records to destination-path semantics before rule evaluation.

**Non-Goals:**
- Replacing `.gitignore` for repository operations outside changelog generation.
- Adding per-invocation interactive filtering prompts to `forklift changelog`.
- Designing a multi-source policy stack (global config files, env var patterns, etc.) in this iteration.
- Changing merge/rebase orchestration behavior beyond front-matter schema acceptance.

## Decisions

1. **Front matter schema expansion keeps strict mode**
   - Decision: extend `FORK.md` front matter contract to allow `changelog` plus existing `setup`.
   - Proposed shape:
     ```yaml
     ---
     setup: uv sync
     changelog:
       exclude:
         - data/big-snapshot.json
         - generated/**/*.json
         - !generated/keep-this.json
     ---
     ```
   - Rationale: repo-level ownership is exactly where fork-specific noise policy belongs; strict schema remains to prevent silent typos.
   - Alternatives considered:
     - Body-text directives in `FORK.md`: rejected (mixes operator prose with machine config).
     - CLI-only `--exclude` flags: rejected for repeatability and policy drift across operators.

2. **Single canonical path model for filtering**
   - Decision: evaluate rules against repo-relative POSIX paths; for rename/copy records, canonicalize to destination path before matching.
   - Rationale: aligns with how users reason about post-integration paths and avoids duplicate accounting (`old => new` plus `new`).
   - Alternatives considered:
     - Match both source and destination: rejected (surprising double-inclusion/exclusion behavior).
     - Source-only matching: rejected (counterintuitive for maintainers writing future-facing rules).

3. **Deterministic evidence keeps baseline and filtered views**
   - Decision: compute two deterministic summaries from the same raw changed-file set:
     - Baseline: no filtering
     - Filtered: exclusions applied
   - Rationale: preserves transparency while still reducing noise in primary report interpretation.
   - Alternatives considered:
     - Only filtered numbers: rejected (hides scope and may reduce trust).
     - Only baseline numbers with hidden filter effects: rejected (fails user requirement).

4. **Gitignore-style matching implemented in-process with explicit scope**
   - Decision: implement ordered rule evaluation in changelog analysis with explicit support for `!` negation and last-match-wins on file paths only.
   - Rationale: keeps dependency footprint stable and provides deterministic behavior for changed-file artifacts.
   - Alternatives considered:
     - Add third-party matcher dependency: deferred unless parity gaps prove problematic.
     - Shell out to ad hoc git ignore commands: rejected due to awkward custom-rule injection and extra process complexity.

5. **Renderer upgrades metrics section to a comparison table**
   - Decision: replace scalar deterministic metrics block with a table that includes baseline, filtered, and delta, then append active exclusions and matched-file count.
   - Rationale: makes exclusion impact immediately legible and auditable.
   - Alternatives considered:
     - Keep old bullets + add one sentence: rejected (too opaque for large deltas).

## Risks / Trade-offs

- **[Risk] Partial mismatch vs full `.gitignore` parity could surprise users** → Mitigation: document exact supported semantics and add unit tests for representative patterns (`*`, `**`, anchored paths, negation order).
- **[Risk] Front-matter schema expansion could accidentally loosen strict validation** → Mitigation: keep allowlist-based parsing and explicit error messages for unknown keys/invalid `changelog.exclude` types.
- **[Risk] Baseline + filtered reporting increases output size** → Mitigation: keep comparison concise (single table + short rules list) and cap listed matched files to aggregate counts only.
- **[Trade-off] Destination-path rename semantics favors future state over provenance context** → Accepted because it matches operator intent for exclusion maintenance.

## Migration Plan

1. Update front-matter parser contract and tests to allow `changelog.exclude` while preserving existing `setup` behavior and fail-closed validation.
2. Extend changelog models to carry exclusion rules, baseline summary, filtered summary, and exclusion match counters.
3. Implement exclusion evaluation in analysis flow after rename canonicalization and before hotspot/metric rendering inputs are finalized.
4. Update renderer output format to comparison table and exclusion transparency section.
5. Update docs (`README.md`, `FORK.md` template) with new metadata examples and matching semantics.
6. Validate with targeted tests (analysis unit tests, command integration tests, front-matter parser regression tests).

## Open Questions

- Should a future iteration add CLI-level temporary overrides (for one-off exclusions) that layer on top of `FORK.md` rules?
- Do we want to expose an optional debug section listing top excluded paths by churn, or keep only aggregate matched counts?
