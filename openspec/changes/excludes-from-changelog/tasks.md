## 1. Extend FORK front-matter metadata contract

- [ ] 1.1 Update `docker/kitchen-sink/harness/includes/fork_context.sh` parser to accept `changelog.exclude` alongside `setup` while preserving strict unknown-key failures.
- [ ] 1.2 Add validation rules for `changelog.exclude` (ordered list of non-empty strings) and fail-closed error messages for malformed values.
- [ ] 1.3 Add/expand harness parser tests to cover: valid `changelog.exclude`, invalid `changelog` shape, non-string entries, and compatibility with existing `setup` flows.

## 2. Add deterministic exclusion filtering to changelog analysis

- [ ] 2.1 Extend changelog models (`src/forklift/changelog_models.py`) to represent active exclusion rules, baseline summary, filtered summary, and exclusion match counts needed by rendering.
- [ ] 2.2 Implement exclusion rule evaluation in `src/forklift/changelog_analysis.py` with gitignore-style ordered semantics (`!` negation, last-match-wins) against repo-relative paths.
- [ ] 2.3 Canonicalize rename/copy changed-file records to destination-path semantics before exclusion matching and summary aggregation.
- [ ] 2.4 Apply filtering consistently to deterministic hotspots and changed-file lists while retaining baseline totals from unfiltered data.
- [ ] 2.5 Load changelog exclusion metadata from `FORK.md` in command orchestration (`src/forklift/changelog.py`) and pass it into analysis.

## 3. Update rendering, docs, and verification

- [ ] 3.1 Replace deterministic scalar metric bullets in `src/forklift/changelog_renderer.py` with a baseline-vs-filtered-vs-delta comparison table.
- [ ] 3.2 Add an exclusion transparency subsection in the changelog output that lists active rules and aggregate matched-file counts.
- [ ] 3.3 Add/expand changelog tests (`tests/test_changelog.py`) for negation behavior, rename destination matching, baseline/filtered metric outputs, and hotspot filtering consistency.
- [ ] 3.4 Update `README.md` and `FORK.md` template guidance with `changelog.exclude` metadata examples and supported matching semantics.
- [ ] 3.5 Run targeted verification (`uv run pytest tests/test_changelog.py` plus harness parser tests) and static checks (`uv run basedpyright`) for touched modules.
