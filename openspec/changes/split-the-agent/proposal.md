## Why

`forklift changelog` was originally meant to summarize upstream changes for an operator already working in the fork. That boundary no longer exists: the current implementation sends one evidence bundle with both upstream and fork-side conflict data to one agent, so the top-level report can describe fork behavior instead of staying focused on upstream changes.

We need to restore the boundary without giving up the existing report shape. The fix is to split changelog synthesis into two section-scoped agents so the top half of the report never receives fork-aware evidence, while the conflict-analysis sections still can.

## What Changes

- Split changelog synthesis into two separate LLM calls with host-owned section assembly.
- Introduce an upstream-only payload for the agent that writes `## Summary` and `## Key Change Arcs`.
- Keep the existing full evidence bundle for a second agent that writes `## Conflict Pair Evaluations` and `## Risk and Review Notes`.
- Replace the current single markdown blob contract with section-scoped outputs so each agent can write only its assigned sections.
- Aggregate usage/cost across both model calls into the existing changelog post-run summary.
- Update changelog documentation and tests to codify the new isolation boundary.

## Capabilities

### New Capabilities
- `changelog-agent-isolation`: Section-scoped changelog synthesis that preserves the current report structure while enforcing an upstream-only boundary for the summary and key change arcs.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `src/forklift/changelog.py`
  - `src/forklift/changelog_analysis.py`
  - `src/forklift/changelog_models.py`
  - `src/forklift/changelog_llm.py`
  - `src/forklift/changelog_renderer.py`
  - `tests/test_changelog.py`
  - `README.md`
- Behavior change: the top half of changelog output becomes upstream-only again, while the bottom half remains fork-aware for conflict analysis.
- No new third-party dependencies are expected.
