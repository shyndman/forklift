## 1. Model layer: add explicit side-comparison data structures

- [x] 1.1 Open `src/forklift/changelog_models.py` and identify current `EvidenceBundle` fields.
- [x] 1.2 Add a dataclass for sampled commit evidence (short sha + subject).
- [x] 1.3 Add a dataclass for one side of a conflict (commit samples, churn, hunk headers, truncation info).
- [x] 1.4 Add a dataclass for one conflict path comparison (path, conflict_count, fork side, upstream side).
- [x] 1.5 Add a dataclass for truncation metadata with `shown`, `total`, and `cap`.
- [x] 1.6 Extend `EvidenceBundle` to include ordered conflict-side comparison entries.
- [x] 1.7 Ensure new fields have safe defaults (`list` default factories, optional values where needed).
- [x] 1.8 Update any existing `EvidenceBundle(...)` construction sites to pass/accept new fields.

## 2. Analysis layer: collect deterministic side evidence per conflict path

- [x] 2.1 Open `src/forklift/changelog_analysis.py` and add new uppercase constants for sampling caps near existing constants.
- [x] 2.2 Add helper: parse `git log --oneline` lines into structured commit sample objects.
- [x] 2.3 Add helper: extract only `@@ ... @@` hunk header lines from unified diff output.
- [x] 2.4 Add helper: compute side-local churn totals for one path.
- [x] 2.5 For each conflict path, collect fork-side evidence from `base..main`.
- [x] 2.6 For each conflict path, collect upstream-side evidence from `base..upstream/<main>`.
- [x] 2.7 Apply per-path caps (commit samples and hunk headers) and store truncation metadata when caps are exceeded.
- [x] 2.8 Preserve mechanical ordering (`conflict_count` desc, path asc) before building comparison entries.
- [x] 2.9 If any global cap is introduced, include global truncation metadata in the bundle.
- [x] 2.10 Return enriched `EvidenceBundle` from `build_evidence_bundle()` without changing read-only behavior.

## 3. Narrative layer: enforce conflict pair evaluation contract

- [x] 3.1 Open `src/forklift/changelog_llm.py` and update prompt contract to require `## Conflict Pair Evaluations`.
- [x] 3.2 Require per-path subsections with: fork intent, upstream intent, conceptual relationship, merge discussion starters.
- [x] 3.3 Add explicit instruction: use deterministic evidence only.
- [x] 3.4 Add explicit instruction: say "insufficient evidence" when signals are too sparse.
- [x] 3.5 Ensure narrative payload includes newly added side-comparison evidence fields and truncation metadata.

## 4. Renderer layer: show deterministic side evidence and truncation notices

- [x] 4.1 Open `src/forklift/changelog_renderer.py` and add a new section for conflict-side comparisons.
- [x] 4.2 Render entries in mechanical order only.
- [x] 4.3 For each path, render fork-side summary and upstream-side summary.
- [x] 4.4 Render truncation notices using exact format `<shown>/<total> (cap <n>)` when applicable.
- [x] 4.5 Preserve current no-conflict behavior (no side-evaluation section when no conflict paths exist).

## 5. Command wiring: keep flow read-only and pass enriched data through

- [x] 5.1 Open `src/forklift/changelog.py` and verify flow remains `build -> narrative -> render`.
- [x] 5.2 Ensure enriched `EvidenceBundle` reaches both narrative generator and renderer.
- [x] 5.3 Confirm no writes, branch mutations, or orchestration lifecycle calls are introduced.

## 6. Tests: add explicit, behavior-first coverage

- [x] 6.1 Add model tests in `tests/test_changelog.py` for new dataclass defaults and serialization shape.
- [x] 6.2 Add analysis test: only merge-tree conflict paths receive side comparisons.
- [x] 6.3 Add analysis test: ordering is `conflict_count` desc then path asc.
- [x] 6.4 Add analysis test: hunk-header extraction keeps only `@@` lines.
- [x] 6.5 Add analysis test: sparse-side evidence still produces an entry.
- [x] 6.6 Add analysis test: truncation metadata appears when caps are exceeded.
- [x] 6.7 Add renderer test: conflict-side section is present when conflicts exist.
- [x] 6.8 Add renderer test: no side section when conflicts are absent.
- [x] 6.9 Add LLM contract test: narrative requires `## Conflict Pair Evaluations` heading.
- [x] 6.10 Add LLM contract test: insufficient evidence wording path is accepted/expected.

## 7. Docs and verification

- [x] 7.1 Update changelog-facing docs (`README.md` or other relevant docs) with new section semantics.
- [x] 7.2 Document truncation notices so users know how to interpret caps.
- [x] 7.3 Run focused tests: `uv run pytest tests/test_changelog.py`.
- [x] 7.4 Run typing check: `uv run basedpyright`.
- [x] 7.5 Confirm both commands pass before marking implementation complete.
