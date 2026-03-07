## Context

Forklift currently has one execution command (`forklift`) that prepares a run directory, starts a containerized agent, and optionally publishes rewritten output. The new `forklift changelog` command is a separate planning tool. It compares branch tips and predicts likely integration pain before any rebase is attempted.

This change must satisfy the user constraints established during exploration:
- keep the same branch-input contract as the core command (`--main-branch`, default `main`)
- fetch remotes before analysis
- compute conflicts deterministically with Git plumbing (`merge-base` + `merge-tree`)
- use an LLM only for narrative summary text
- fail hard if LLM summary fails
- print terminal Markdown using Rich
- never run the container or mutate repository history in this command

## Goals / Non-Goals

**Goals:**
- Add `forklift changelog` as a separate host-side command path.
- Reuse existing remote validation/fetch conventions (`origin` + `upstream`) so behavior is familiar.
- Produce deterministic conflict hotspots and supporting stats from local Git data.
- Produce LLM-authored narrative text from deterministic evidence.
- Keep existing `forklift` orchestration behavior unchanged.

**Non-Goals:**
- Running the sandbox container for changelog generation.
- Creating run directories, `run-state.json`, `DONE.md`, `STUCK.md`, or publication branches.
- Performing merge/rebase/cherry-pick operations.
- Implementing commit-by-commit narrative mode in v1.
- Writing report artifacts to disk in v1.

## Decisions

### 1) Add dedicated changelog modules with explicit responsibilities

Create small modules so a junior engineer can implement incrementally and test each layer:

1. `src/forklift/changelog.py`
   - `Command` subclass for CLI args and top-level `run()`.
2. `src/forklift/changelog_models.py`
   - dataclasses for analysis inputs/outputs (`ConflictHotspot`, `DiffSummary`, `EvidenceBundle`).
3. `src/forklift/changelog_analysis.py`
   - deterministic Git calls and parsing logic.
4. `src/forklift/changelog_llm.py`
   - model invocation + prompt assembly from deterministic evidence.
5. `src/forklift/changelog_renderer.py`
   - markdown section assembly and Rich rendering.

`src/forklift/cli.py` only wires the new subcommand; it should not hold analysis logic.

Rationale:
- Mirrors existing split used by `clientlog` modules.
- Reduces cognitive load for junior implementation and review.

### 2) Deterministic analysis pipeline order is fixed

Implement these steps in this exact order:

1. Resolve `repo_path` (same behavior as `Forklift._resolve_repo_path`).
2. Validate remotes using existing helper (`ensure_required_remotes`).
3. Fetch remotes using existing helper (`fetch_remotes`).
4. Resolve refs:
   - local ref: `<main_branch>`
   - upstream ref: `upstream/<main_branch>`
5. Compute base SHA:
   - `git merge-base <main_branch> upstream/<main_branch>`
6. Predict conflicts:
   - `git merge-tree --write-tree <main_branch> upstream/<main_branch>` (or equivalent merge-base-explicit form when needed)
   - parse the **Conflicted file info** block (`<mode> <object> <stage> <filename>`) for deterministic hotspot extraction
   - use exit status semantics as control flow: `0` clean, `1` conflicted, `>1` fatal/tooling error
7. Collect supporting stats:
   - `git diff --numstat <main_branch>...upstream/<main_branch>`
   - `git diff --name-status <main_branch>...upstream/<main_branch>`
   - optional `git log --oneline` counts per side for churn context

Rationale:
- Gives deterministic, reproducible results.
- Keeps the LLM stage independent from Git command handling.

### 2.1) Third-party dependency strategy and version gates

This change introduces one explicit external runtime requirement and one dependency decision:

1. **Git CLI version gate (required)**
   - Require Git **2.38+** on the host.
   - Reason: modern `git merge-tree` behavior (real merge simulation and conflict metadata output) is documented for modern mode and was introduced in Git 2.38 release notes.
   - Implementation expectation: check `git --version` early in `forklift changelog`; fail fast with actionable error when below 2.38.

2. **Narrative SDK choice (decision required, no new dep for v1)**
   - **Selected for v1:** reuse existing `pydantic-ai` dependency already in `pyproject.toml`.
   - **Implementation rule for v1:** do **not** add direct `openai` or `anthropic` packages to `pyproject.toml`; keep provider routing behind `pydantic-ai`.
   - **API boundary for v1:** use stable `Agent(...).run_sync(...)` / `await Agent(...).run(...)` flows and `RunResult.output`; avoid beta-marked APIs.
   - **Versioning stance for v1:** keep current repo pin (`pydantic-ai==1.59.0`) for this change; evaluate a separate dependency refresh to a `~=1.67` range in a dedicated follow-up change.
   - **Not selected for v1:** add direct provider SDKs (`openai`, `anthropic`).

Verified option comparison:

| Option | Version status (research snapshot) | Call pattern (official docs) | Decision |
|---|---|---|---|
| `pydantic-ai` | Latest observed: 1.67.0; repo currently declares 1.59.0 | `Agent(...).run_sync(...)` / `await Agent(...).run(...)` returning `result.output` | **Use this in v1** |
| `openai` SDK | Latest observed: 2.26.0 | `client.responses.create(model=..., input=...)` and read `response.output_text` | Not selected |
| `anthropic` SDK | Latest observed: 0.84.0 | `client.messages.create(model=..., messages=[...], max_tokens=...)` and read `message.content` | Not selected |

Rationale:
- Reusing `pydantic-ai` avoids adding new runtime dependencies and preserves multi-provider behavior.
- Direct SDKs are valid fallback options for a future change but add provider-specific branching we do not need in v1.

### 3) Evidence bundle contract is explicit and stable

The LLM input should be a bounded structured payload, not raw full diff text. Use one dataclass payload with this minimum shape:

- `base_sha`
- `main_branch`
- `upstream_ref`
- `conflicts: list[{path, conflict_count}]`
- `diff_summary: {files_changed, insertions, deletions}`
- `top_changed_files: list[{path, added, removed, status}]` (top N by churn)
- `important_notes: list[str]` (for caveats like repeated rebase conflicts)

Bound payload size deterministically (for example, top 30 files) before model invocation.

Rationale:
- Prevents context overrun for large repositories.
- Makes behavior predictable for tests.

### 4) LLM layer is mandatory but narrowly scoped

`changelog_llm.py` responsibilities:

1. Build prompt from `EvidenceBundle`.
2. Request markdown narrative with fixed headings:
   - `## Summary`
   - `## Notable Change Themes`
   - `## Risk and Review Notes`
3. Return text on success.
4. Raise typed error on failure (config/auth/network/model/runtime).

Top-level command behavior:
- if LLM call fails, exit non-zero and print clear error.
- do not emit fake/fallback narrative.

Rationale:
- Matches requirement that narrative is required output.
- Keeps deterministic conflict details authoritative.

### 5) Renderer output contract is fixed for v1

Final output (Markdown rendered through Rich) should always include, in order:

1. Title: `# Forklift Changelog`
2. Ref context section:
   - main branch
   - upstream ref
   - merge base short SHA
3. LLM narrative section (exact markdown from LLM)
4. Predicted conflict hotspots section:
   - table/list of `path` and `conflict_count`
   - explicit caveat line: tip-merge prediction may repeat during rebase
5. Deterministic supporting metrics section:
   - files changed, insertions, deletions
   - top changed files

Rationale:
- Operators get predictable shape every run.
- Human-readable and easy to paste into team discussion.

### 6) Guardrails to preserve existing orchestrator invariants

The changelog path must not call or import mutation-oriented orchestration helpers:

- `RunDirectoryManager.prepare`
- `ContainerRunner.run`
- `post_container_results`
- `rewrite_and_publish_local`

Rationale:
- Enforces non-mutating behavior by construction and test.

### 6.1) In-code intent capture (single tag per change)

To reduce long-term code drift, this change must add exactly one intent doc comment in code.

**Proposed location:**
- `src/forklift/changelog.py`
- the single orchestration function that coordinates fetch -> deterministic analysis -> LLM narrative -> render (expected to be `run()` on the changelog command class)
- place the intent block as the function doc comment immediately inside the function body

**Required intent text (verbatim):**

```text
<intent>
Generate a read-only changelog between <main_branch> and upstream/<main_branch> by combining deterministic git evidence (including merge-tree conflict hotspots) with an LLM narrative, without running container orchestration or mutating local history.
</intent>
```

**Uniqueness rule:**
- exactly one new `<intent>` block may be added in this change
- no additional `<intent>` blocks are allowed in other new/modified files for this change

**Verification rule:**
- implementation/test checklist must include a repository search proving exactly one `<intent>` block exists after this change is implemented

## Implementation Walkthrough (Junior-Friendly)

Implement in this sequence so each step has a small blast radius:

1. **CLI wiring only**
   - Add subcommand class import and field in `src/forklift/cli.py`.
   - Add minimal `changelog` command that prints placeholder markdown.
   - Add a smoke test proving command is routed.

2. **Deterministic analysis without LLM**
   - Implement `changelog_analysis.py` and `changelog_models.py`.
   - Unit-test each parser with fixture strings.
   - Keep command returning deterministic sections only while developing.

3. **LLM integration**
   - Implement `changelog_llm.py` to consume `EvidenceBundle`.
   - Add failure tests first (ensures hard-fail policy).
   - Add success test with mocked model client.

4. **Rich markdown rendering**
   - Implement `changelog_renderer.py` that takes deterministic + narrative sections.
   - Verify output order and caveat text in tests.

5. **End-to-end command tests**
   - Test: fetch called, analysis called, llm called, markdown printed.
   - Test: missing remote -> non-zero.
   - Test: llm failure -> non-zero.
   - Test: no orchestration helpers called.

## Risks / Trade-offs

- **[Risk] `git merge-tree` output format differences across Git versions**
  - **Mitigation:** parse only stable markers and add fixture tests for representative outputs.
- **[Risk] Large diffs produce oversized LLM prompts**
  - **Mitigation:** strict top-N bounding in evidence bundle.
- **[Risk] Users misread hotspot counts as exact future rebase conflict count**
  - **Mitigation:** mandatory caveat line in output.
- **[Trade-off] Hard-fail on LLM reduces availability**
  - **Accepted:** user explicitly requires narrative generation.

## Migration Plan

1. Add command scaffolding and tests (no behavior change to existing `forklift`).
2. Add deterministic analysis modules and parser tests.
3. Add LLM integration and failure-path tests.
4. Add renderer and command integration tests.
5. Update README with new command usage and caveats.

Rollback:
- Revert changelog modules + CLI wiring commit(s).
- Existing orchestration remains unaffected because no shared mutation flow is changed.

## Open Questions

- Should v1 cap the top changed file list at 20 or 30 entries?
- Should v1 expose a `--no-color` switch for Markdown rendering, or defer to a later change?
