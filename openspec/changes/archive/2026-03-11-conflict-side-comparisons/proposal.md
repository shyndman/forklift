## Why

Today, `forklift changelog` tells us **where** conflicts are likely (`Path` + `Conflict Count`), but not **what each side is trying to do** in that file.

That gap blocks the conversation we actually need during rebase planning:

- Fork side: what feature or behavior did we add?
- Upstream side: what feature or behavior is coming in?
- Are these ideas conceptually incompatible, or just mechanically overlapping in the same file?

Verified current state before this change:

- `EvidenceBundle` included conflict hotspots and diff summaries, but no per-path fork-vs-upstream intent breakdown (`src/forklift/changelog_models.py`, `src/forklift/changelog_analysis.py`).
- Renderer output showed `## Predicted Conflict Hotspots` with only `Path` and `Conflict Count` (`src/forklift/changelog_renderer.py`).
- LLM narrative targeted `## Summary`, `## Key Change Arcs`, and `## Risk and Review Notes` only (`src/forklift/changelog_llm.py`).

## What Changes

We add a conflict-pair evaluation layer for every merge-tree conflict path.

1. Deterministic analysis still collects side-specific evidence internally for each conflicted path.
2. Narrative generation now turns that evidence into decision-grade summaries:
   1. fork-side intent
   2. upstream-side intent written as a short paragraph when evidence supports it
   3. conceptual relationship
   4. why this is or is not a conceptual conflict
   5. merge considerations
3. Default changelog output remains mechanical-first:
   1. hotspot table first
   2. conceptual conflict summaries in the narrative
   3. supporting deterministic metrics after the narrative
4. Repo-local jargon must be translated into plain-English behavior descriptions, or the narrative must explicitly say there is insufficient evidence.

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
  - Default output emphasizes feature summaries and merge thinking, not raw evidence.
  - Opaque internal names are explained in plain English when evidence supports it.
- Dependencies/systems:
  - No new third-party dependency required for v1 (Git CLI + stdlib parsing + existing `pydantic-ai`).
  - Git command semantics used are documented and verified:
    - `git log [<options>] [<revision-range>] [[--] <path>...]`
    - `git diff ... -U<n>/--unified=<n> [--] <path>...`
  - Existing `pydantic-ai` dependency remains in place (currently pinned to `1.59.0` in `pyproject.toml`).

## Definition of Done

This proposal is considered complete when implementation produces all of the following:

1. For each merge-tree conflict path, changelog narrative includes fork-side and upstream-side feature summaries.
2. Narrative includes a `## Conflict Pair Evaluations` section with one subsection per evaluated path.
3. Each subsection includes conceptual relationship, explanation of the conflict type, and merge considerations.
4. Default changelog output does not dump raw conflict evidence structures to the operator.
5. Existing read-only behavior and existing no-conflict behavior remain intact.
