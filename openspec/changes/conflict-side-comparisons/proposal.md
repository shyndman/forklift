## Why

Today, `forklift changelog` tells us **where** conflicts are likely (`Path` + `Conflict Count`), but not **what each side is trying to do** in that file.

That gap blocks the conversation we actually need during rebase planning:

- Fork side: what feature/change did we add?
- Upstream side: what feature/change is coming in?
- Are these ideas conceptually incompatible, or just mechanically overlapping in the same file?

Verified current state:

- `EvidenceBundle` includes conflict hotspots and diff summaries, but no per-path fork-vs-upstream intent breakdown (`src/forklift/changelog_models.py`, `src/forklift/changelog_analysis.py`).
- Renderer currently outputs `## Predicted Conflict Hotspots` with only `Path` and `Conflict Count` (`src/forklift/changelog_renderer.py`).
- LLM narrative currently targets `## Summary`, `## Key Change Arcs`, and `## Risk and Review Notes` only (`src/forklift/changelog_llm.py`).

## What Changes

We will add a new conflict-side comparison layer for every merge-tree conflict path.

1. Deterministic analysis will collect side-specific evidence per conflicted path:
   1. fork-side (`base..main`) commit subjects
   2. upstream-side (`base..upstream/<main>`) commit subjects
   3. side-local churn totals
   4. diff hunk headers (`@@ ... @@`) per side
2. Narrative generation will evaluate each path as a pair:
   1. fork intent
   2. upstream intent
   3. conceptual relationship type
   4. merge discussion starters
3. Output ordering stays mechanical-first:
   1. `conflict_count` descending
   2. path ascending for ties
4. Truncation is always explicit when caps are hit:
   1. show `<shown>/<total> (cap <n>)`
   2. show warning that more evidence exists

Verified command-flow constraint:

- `Changelog.run()` remains read-only and keeps the same high-level sequence:
  `build_evidence_bundle() -> generate_changelog_narrative() -> render_*`
  (`src/forklift/changelog.py`).

## Capabilities

### New Capabilities

- `changelog-conflict-side-comparisons`: per-conflict, side-by-side fork/upstream conceptual comparison backed by deterministic evidence.

### Modified Capabilities

- None.

## Impact

- Affected code:
  - `src/forklift/changelog_models.py`
  - `src/forklift/changelog_analysis.py`
  - `src/forklift/changelog_llm.py`
  - `src/forklift/changelog_renderer.py`
  - `src/forklift/changelog.py`
  - `tests/test_changelog.py`
- Affected user behavior:
  - Conflict hotspots expand from path/count-only to conceptual pair evaluations.
  - Changelog clearly announces when evidence limits may hide additional context.
- Dependencies/systems:
  - No new third-party dependency required for v1 (Git CLI + stdlib parsing + existing `pydantic-ai`).
  - Git command semantics used are documented and verified:
    - `git log [<options>] [<revision-range>] [[--] <path>...]`
    - `git diff ... -U<n>/--unified=<n> [--] <path>...`
  - Existing `pydantic-ai` dependency remains in place (currently pinned to `1.59.0` in `pyproject.toml`).

## Definition of Done

This proposal is considered complete when implementation produces all of the following:

1. For each merge-tree conflict path, changelog includes fork-side and upstream-side evidence summaries.
2. Narrative includes a `## Conflict Pair Evaluations` section with one subsection per evaluated path.
3. Evaluations are mechanically ordered by conflict severity.
4. Any evidence cap hit is visible in output with exact counts.
5. Existing read-only behavior and existing no-conflict behavior remain intact.
