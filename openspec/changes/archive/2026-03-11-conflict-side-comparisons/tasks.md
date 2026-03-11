## 1. Model layer: keep explicit internal side-comparison data structures

- [x] 1.1 Open `src/forklift/changelog_models.py` and identify current `EvidenceBundle` fields.
- [x] 1.2 Add a dataclass for sampled commit evidence (short sha + subject).
- [x] 1.3 Add a dataclass for one side of a conflict (commit samples, churn, hunk headers, truncation info).
- [x] 1.4 Add a dataclass for one conflict path comparison (path, conflict_count, fork side, upstream side).
- [x] 1.5 Add a dataclass for truncation metadata with `shown`, `total`, and `cap`.
- [x] 1.6 Extend `EvidenceBundle` to include ordered conflict-side comparison entries.
- [x] 1.7 Ensure new fields have safe defaults (`list` default factories, optional values where needed).
- [x] 1.8 Update any existing `EvidenceBundle(...)` construction sites to pass/accept new fields.

## 2. Analysis layer: collect deterministic side evidence per conflict path

- [x] 2.1 Open `src/forklift/changelog_analysis.py` and add new uppercase constants for internal sampling caps near existing constants.
- [x] 2.2 Add helper: parse `git log --oneline` lines into structured commit sample objects.
- [x] 2.3 Add helper: extract only `@@ ... @@` hunk header lines from unified diff output.
- [x] 2.4 Add helper: compute side-local churn totals for one path.
- [x] 2.5 For each conflict path, collect fork-side evidence from `base..main`.
- [x] 2.6 For each conflict path, collect upstream-side evidence from `base..upstream/<main>`.
- [x] 2.7 Preserve mechanical ordering (`conflict_count` desc, path asc) before building comparison entries.
- [x] 2.8 Return enriched `EvidenceBundle` from `build_evidence_bundle()` without changing read-only behavior.

## 3. Narrative layer: require conceptual summaries instead of evidence dumps

- [x] 3.1 Open `src/forklift/changelog_llm.py` and update prompt contract to require `## Conflict Pair Evaluations`.
- [x] 3.2 Require per-path subsections with: fork intent, upstream intent, conceptual relationship, why this is or is not a conceptual conflict, merge considerations.
- [x] 3.3 Require `Upstream-side intent` to be a short paragraph when evidence supports it.
- [x] 3.4 Add explicit instruction: explain repo-local jargon in plain English.
- [x] 3.5 Add explicit instruction: say `insufficient evidence` when signals are too sparse.
- [x] 3.6 Add explicit instruction: do not restate raw evidence structures in final markdown.
- [x] 3.7 Ensure narrative payload still includes deterministic side-comparison evidence internally.

## 4. Renderer layer: keep output focused on conceptual summaries

- [x] 4.1 Open `src/forklift/changelog_renderer.py` and keep the narrative as the operator-facing place for conflict summaries.
- [x] 4.2 Preserve hotspot table, branch context, and supporting metrics.
- [x] 4.3 Preserve current no-conflict behavior.

## 5. Command wiring: keep flow read-only and pass enriched data through

- [x] 5.1 Open `src/forklift/changelog.py` and verify flow remains `build -> narrative -> render`.
- [x] 5.2 Ensure enriched `EvidenceBundle` reaches the narrative generator.
- [x] 5.3 Confirm no writes, branch mutations, or orchestration lifecycle calls are introduced.

## 6. Tests: lock in summary-only behavior

- [x] 6.1 Add model tests in `tests/test_changelog.py` for new dataclass defaults and serialization shape.
- [x] 6.2 Add analysis test: only merge-tree conflict paths receive side comparisons.
- [x] 6.3 Add analysis test: ordering is `conflict_count` desc then path asc.
- [x] 6.4 Add analysis test: hunk-header extraction keeps only `@@` lines.
- [x] 6.5 Add analysis test: sparse-side evidence still produces an entry.
- [x] 6.6 Add LLM contract test: narrative requires `## Conflict Pair Evaluations` heading.
- [x] 6.7 Add LLM contract test: plain-English explanation is required for repo-local jargon.
- [x] 6.8 Add LLM contract test: `Upstream-side intent` is required to be paragraph-length when evidence supports it.
- [x] 6.9 Add renderer test: operator-facing output stays focused on narrative summaries.
- [x] 6.10 Add renderer test: no-conflict behavior remains unchanged.

## 7. Docs and verification

- [x] 7.1 Update changelog-facing docs (`README.md` or other relevant docs) to describe conceptual conflict summaries.
- [x] 7.2 Remove documentation that promises raw conflict evidence in default output.
- [x] 7.3 Run focused tests: `uv run pytest tests/test_changelog.py`.
- [x] 7.4 Run typing check: `uv run basedpyright`.
- [x] 7.5 Confirm both commands pass before marking implementation complete.
