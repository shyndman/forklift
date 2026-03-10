## Context

We already detect likely conflicts using `git merge-tree`, then summarize results in changelog output. That is useful for triage, but too thin for design-level merge conversations.

The user requirement is explicit:

1. Scope strictly to merge-tree conflict paths.
2. Compare both sides of each conflict path (fork vs upstream).
3. Include diff hunk headers.
4. Keep mechanical ordering.
5. Avoid aggressive thinning, but clearly announce truncation when limits are hit.

## Audience and Implementation Style

This design is written so a junior engineer can implement it safely.

Principles:

1. Prefer explicit data structures over implicit dicts.
2. Parse only what we need.
3. Keep every step deterministic and testable.
4. Do not infer behavior from LLM output; require evidence-backed sections.

## Glossary

- **Fork side**: commits in `base..main`
- **Upstream side**: commits in `base..upstream/<main>`
- **Conflict path**: file path returned by merge-tree conflict parsing
- **Hunk header**: the `@@ ... @@` line in unified diff output
- **Mechanical ordering**: `conflict_count` descending, then path ascending
- **Cap/truncation**: a configured maximum that limits collected evidence

## Current Verified Flow

```text
resolve refs/base
   -> compute hotspots
   -> build EvidenceBundle (global metrics + conflicts)
   -> send asdict(bundle) to LLM
   -> render markdown/terminal
```

Verified files:

- `src/forklift/changelog_analysis.py`
- `src/forklift/changelog_models.py`
- `src/forklift/changelog_llm.py`
- `src/forklift/changelog_renderer.py`
- `src/forklift/changelog.py`

## Target Flow

```text
resolve refs/base
   -> compute hotspots (already exists)
   -> for each conflict path (mechanical order):
        collect fork-side evidence
        collect upstream-side evidence
        compute truncation metadata
   -> attach side-comparison structures to EvidenceBundle
   -> generate narrative with required conflict-pair section
   -> render deterministic evidence + narrative + truncation notices
```

## Decisions

### 1) Add explicit side-comparison models

Decision:

- Extend changelog models with explicit per-path structures (not free-form dicts).

Minimum fields expected:

1. path + conflict_count
2. fork-side evidence
3. upstream-side evidence
4. truncation metadata

Why:

- Junior-friendly and testable: each field can be asserted directly.

### 2) Use Git CLI path-scoped extraction

Decision:

- Use path-limited `git log` and `git diff` for each conflict path.

Commands:

1. `git log --oneline <base>..<main> -- <path>`
2. `git log --oneline <base>..<upstream_ref> -- <path>`
3. `git diff --unified=0 <base>...<main> -- <path>`
4. `git diff --unified=0 <base>...<upstream_ref> -- <path>`

Parsing rules:

1. Commit sample lines: `"<sha> <subject>"`
2. Hunk headers: keep lines starting with `@@`
3. Churn: use existing stat parsing pattern from analysis module

### 3) Keep full conflict-path coverage by default

Decision:

- Analyze all filtered conflict paths unless a safety cap is hit.

Cap behavior:

1. If no cap hit: no truncation notice.
2. If cap hit: emit explicit `<shown>/<total> (cap <n>)` and warning text.

### 4) Keep mechanical ordering end-to-end

Decision:

- Use the same ordering in collection, prompt payload, and rendering.

Order key:

1. `-conflict_count`
2. `path` ascending

### 5) Require a new narrative section

Decision:

- LLM output must include `## Conflict Pair Evaluations`.

Each path subsection must include:

1. Fork-side intent
2. Upstream-side intent
3. Conceptual relationship
4. Merge conversation starters

### 6) Dependency posture for v1

Decision:

- Do not add new third-party dependencies in this change.

Verified third-party interfaces used:

1. Git CLI docs for path-limited `log`/`diff` and `--unified`
2. Pydantic AI docs for `Agent(...); await agent.run(...)`

Fallback options (not in v1):

1. `unidiff` (latest verified `0.7.5`) for structured diff parsing
2. `GitPython` (latest verified `3.1.46`) for repo abstraction

## File-by-File Implementation Plan

### `src/forklift/changelog_models.py`

1. Add dataclasses for side evidence and per-path comparison entry.
2. Add truncation structures so caps are machine-readable.
3. Extend `EvidenceBundle` with new fields (default empty list/None-safe values).
4. Keep backward compatibility within this change set by updating all constructors at once.

### `src/forklift/changelog_analysis.py`

1. Add constants for caps (uppercase, near existing constants).
2. Add helper to parse commit sample lines from `git log --oneline`.
3. Add helper to parse `@@ ... @@` lines from unified diff output.
4. Add helper to compute per-side churn for one path.
5. In `build_evidence_bundle`:
   1. use existing filtered conflict list
   2. iterate in mechanical order
   3. collect fork/upstream evidence per path
   4. attach truncation metadata when limits are exceeded
6. Return enriched `EvidenceBundle`.

### `src/forklift/changelog_llm.py`

1. Update prompt contract to require `## Conflict Pair Evaluations`.
2. Add strict instructions:
   1. use only provided evidence
   2. call out insufficient evidence explicitly
   3. avoid unsupported behavioral claims
3. Ensure prompt payload includes new side-evidence fields.
4. Update heading validation tests/logic if present.

### `src/forklift/changelog_renderer.py`

1. Add deterministic section for side comparisons.
2. Render per-path blocks in mechanical order.
3. Render truncation notices with exact count format.
4. Preserve existing no-conflicts behavior.

### `src/forklift/changelog.py`

1. Confirm flow remains read-only.
2. Ensure enriched bundle is passed through to LLM and renderer.
3. Do not add orchestration-related side effects.

### `tests/test_changelog.py`

Add tests for:

1. merge-tree-only scope (no non-conflict paths)
2. ordering (`conflict_count` desc, path asc)
3. hunk-header extraction (`@@` capture only)
4. sparse-side evidence behavior
5. truncation notice rendering and exact count format
6. required narrative heading `## Conflict Pair Evaluations`

## Concrete Acceptance Checklist (Junior-Friendly)

Implementation is done only when all are true:

1. Running changelog on conflicting branches shows per-path fork/upstream evidence.
2. Output order matches mechanical ordering in every run.
3. Cap hits are visible with `<shown>/<total> (cap <n>)`.
4. No-conflict branch pair still prints no-hotspot message and no side-evaluation sections.
5. Tests in `tests/test_changelog.py` cover new behavior and pass.

## Risks / Trade-offs

1. **Large conflict sets increase runtime/prompt size**
   - Mitigation: bounded sampling + explicit truncation notices.
2. **Weak commit messages reduce semantic quality**
   - Mitigation: include hunk headers and require “insufficient evidence” fallback.
3. **More verbose output**
   - Accepted; this is the product goal.

## Non-Goals

1. No analysis of non-conflicting files.
2. No full-patch ingestion.
3. No changelog command side effects.
4. No new dependency adoption in v1.
