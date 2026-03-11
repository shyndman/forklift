## 1. Split changelog evidence into explicit report surfaces

- [x] 1.1 Add dataclasses in `src/forklift/changelog_models.py` for the upstream-only narrative payload, section-scoped LLM outputs, and any combined usage/result wrappers needed by the new flow.
- [x] 1.2 Add a projection helper in `src/forklift/changelog_analysis.py` that derives the upstream-only narrative payload from the existing full `EvidenceBundle` without carrying fork-side conflict comparison data.
- [x] 1.3 Add or update analysis tests in `tests/test_changelog.py` to prove the upstream-only payload excludes fork-aware evidence while preserving the upstream-oriented summary inputs.

## 2. Split synthesis into two section-owned LLM contracts

- [x] 2.1 Replace the single changelog narrative prompt in `src/forklift/changelog_llm.py` with one prompt/function for `## Summary` + `## Key Change Arcs` and a second prompt/function for `## Conflict Pair Evaluations` + `## Risk and Review Notes`.
- [x] 2.2 Reuse shared model resolution, env bridging, error handling, and cost extraction helpers so both generation paths keep the current pydantic-ai integration behavior.
- [x] 2.3 Add LLM contract tests in `tests/test_changelog.py` that verify the upstream prompt bans fork-aware section content and the conflict prompt is restricted to the lower-half sections.

## 3. Rework changelog orchestration and rendering around host-side assembly

- [x] 3.1 Update `src/forklift/changelog.py` to build full evidence once, derive the upstream-only payload, run both synthesis steps, and hard-fail if either step fails.
- [x] 3.2 Aggregate token usage and estimated cost across both synthesis steps into the existing changelog usage summary shape in `src/forklift/changelog.py`.
- [x] 3.3 Update `src/forklift/changelog_renderer.py` so the host assembles final markdown from section-scoped outputs in the fixed order before rendering the existing hotspot and metrics sections.
- [x] 3.4 Add or update integration tests in `tests/test_changelog.py` covering successful two-agent assembly, either-agent failure behavior, and combined usage reporting.

## 4. Update documentation to match the new boundary

- [x] 4.1 Update `README.md` so changelog behavior describes an upstream-only top half plus fork-aware conflict/review bottom half.
- [x] 4.2 Remove or rewrite any stale changelog conflict-comparison documentation touched by this change so docs no longer describe a single agent owning the full report narrative.
