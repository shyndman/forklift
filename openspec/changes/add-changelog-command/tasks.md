## 1. Create command skeleton and wire CLI entrypoints

- [ ] 1.1 Create `src/forklift/changelog.py` with a `Command` subclass that accepts `--main-branch` (default `main`) and optional `repo` path
- [ ] 1.2 Add changelog subcommand wiring in `src/forklift/cli.py` (imports + typed `subcommand` union) without changing existing `Forklift.run()` behavior
- [ ] 1.3 Add a minimal smoke implementation in `changelog.py` that only prints placeholder markdown so command routing can be tested independently of analysis logic
- [ ] 1.4 Add/update a command parsing test proving `forklift changelog` resolves to the new command class

## 2. Add strongly typed changelog data models

- [ ] 2.1 Create `src/forklift/changelog_models.py` with dataclasses for `ConflictHotspot`, `DiffSummary`, `ChangedFileStat`, and `EvidenceBundle`
- [ ] 2.2 Ensure model fields match design contract (`base_sha`, refs, conflict list, summary counts, top changed files)
- [ ] 2.3 Add unit tests that construct each dataclass and verify expected field defaults/types

## 3. Implement deterministic git analysis helpers

- [ ] 3.1 Create `src/forklift/changelog_analysis.py` and add helper `resolve_analysis_refs(repo_path, main_branch)` that returns `<main_branch>` and `upstream/<main_branch>`
- [ ] 3.2 Reuse `ensure_required_remotes` and `fetch_remotes` from `src/forklift/git.py` before any analysis command executes
- [ ] 3.3 Add host Git version gate (`git --version`) and fail fast when Git is older than 2.38 with an actionable upgrade message
- [ ] 3.4 Add helper for `git merge-base <main_branch> upstream/<main_branch>` and return full SHA
- [ ] 3.5 Add helper for `git merge-tree --write-tree <main_branch> upstream/<main_branch>` and capture both stdout and exit status
- [ ] 3.6 Parse merge-tree Conflicted file info lines (`<mode> <object> <stage> <filename>`) into `ConflictHotspot` items (`path`, `conflict_count`)
- [ ] 3.7 Implement merge-tree exit handling (`0` clean, `1` conflicted, `>1` fatal) and fail closed for fatal statuses
- [ ] 3.8 Add helpers for deterministic supporting stats using `git diff --numstat` and `git diff --name-status` over `<main_branch>...upstream/<main_branch>`
- [ ] 3.9 Build `EvidenceBundle` from deterministic outputs and cap top changed files to a fixed max size

## 4. Implement LLM narrative generation with hard-fail behavior

- [ ] 4.1 Create `src/forklift/changelog_llm.py` with one public function that accepts `EvidenceBundle` and returns markdown narrative text
- [ ] 4.2 Build a prompt that includes deterministic evidence only (never raw unbounded diff)
- [ ] 4.3 Integrate env/model loading so the changelog command uses configured credentials/model settings consistently
- [ ] 4.4 Raise a typed error when model invocation fails (config/auth/network/runtime)
- [ ] 4.5 In command orchestration, convert LLM errors into non-zero command exit with clear operator-facing message
- [ ] 4.6 Use stable `pydantic-ai` run APIs (`run_sync`/`run`) and avoid beta-only APIs in changelog narrative implementation

## 5. Implement markdown output renderer

- [ ] 5.1 Create `src/forklift/changelog_renderer.py` that assembles markdown sections in fixed order
- [ ] 5.2 Include required sections: branch context, narrative summary, predicted conflict hotspots, deterministic supporting metrics
- [ ] 5.3 Always include caveat text explaining tip-merge hotspot predictions may repeat during later rebase picks
- [ ] 5.4 Render markdown in terminal using Rich markdown rendering APIs

## 6. Integrate full changelog command flow

- [ ] 6.1 Replace placeholder command body with full orchestration flow in `src/forklift/changelog.py`
- [ ] 6.2 Ensure command path is read-only: no calls to `RunDirectoryManager.prepare`, `ContainerRunner.run`, `post_container_results`, or rewrite/publication helpers
- [ ] 6.3 Ensure command exits successfully only when deterministic analysis and LLM narrative both succeed
- [ ] 6.4 Ensure command exits non-zero for missing remotes, git command failures, or LLM failures
- [ ] 6.5 Add the single required `<intent>` doc comment (verbatim text from design section 6.1) to the changelog orchestration function in `src/forklift/changelog.py`, and verify no other `<intent>` blocks were added in this change

## 7. Add comprehensive tests

- [ ] 7.1 Add unit tests for merge-tree parser covering: no conflicts, one file with multiple conflict blocks, multiple conflicted files
- [ ] 7.2 Add unit tests for merge-tree exit semantics covering clean (`0`), conflicted (`1`), and fatal (`>1`) outcomes
- [ ] 7.3 Add unit tests for diff-stat parsing (`numstat`, `name-status`) including renamed and binary-file rows
- [ ] 7.4 Add unit tests for evidence-bundle truncation logic (top-N changed files)
- [ ] 7.5 Add command integration test: successful flow fetches remotes, builds evidence, calls LLM, and prints required markdown sections
- [ ] 7.6 Add command integration test: LLM error causes non-zero exit and no fallback narrative
- [ ] 7.7 Add command integration test: changelog path does not touch orchestration-only helpers

## 8. Update user documentation and run verification

- [ ] 8.1 Update `README.md` with `forklift changelog` usage examples (default branch and custom `--main-branch`)
- [ ] 8.2 Document that changelog is host-side analysis only (no container, no run directory)
- [ ] 8.3 Document host Git requirement (2.38+) and why older merge-tree modes are unsupported
- [ ] 8.4 Document hotspot caveat: predictions come from tip-merge analysis and may recur during commit-by-commit rebase
- [ ] 8.5 Run targeted tests for files touched by this change and record exact commands/results in the PR description
