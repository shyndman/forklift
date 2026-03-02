## Context

Forklift currently has two large command modules: `src/forklift/cli.py` (orchestration + rewrite + ownership + environment handling) and `src/forklift/clientlog.py` (parser + renderer + follow command loop). Their size increases cognitive load, makes targeted review harder, and raises the risk that a local change accidentally touches unrelated behavior.

The goal of this change is structural: preserve external behavior while introducing module boundaries that match responsibilities already present in the code. Existing tests (`tests/test_cli_post_run.py`, `tests/test_clientlog.py`, and container-run-state tests) provide behavior guardrails during extraction.

## Goals / Non-Goals

**Goals:**
- Split `Forklift` command internals into focused modules with explicit responsibility boundaries.
- Split `clientlog` internals into parser, renderer, and command orchestration modules.
- Preserve command entrypoints, flags, exit codes, and expected log/render output semantics.
- Keep compatibility import paths stable so existing tests and callers can continue importing `forklift.cli` and `forklift.clientlog`.

**Non-Goals:**
- Changing user-facing command behavior, output formats, or config contracts.
- Introducing new runtime dependencies.
- Reworking unrelated code paths outside `cli` and `clientlog`.
- Performing broad feature work beyond refactoring and dead-code cleanup discovered during extraction.

## Decisions

### 1) Extract by responsibility, not by arbitrary line count
Create modules along existing behavior seams:
- `cli`: bootstrap/orchestration, post-run processing, rewrite/publication, ownership/env utilities.
- `clientlog`: parser model, rendering, command follow loop.

**Rationale:** Responsibility-based boundaries are easier to understand and test than mechanical slicing.

**Alternatives considered:**
- Split each file into “part1/part2” buckets by size only: rejected because coupling remains unclear.
- Keep files large and rely on comments: rejected because it doesn’t reduce change risk.

### 2) Keep compatibility façades at existing import paths
Retain `forklift.cli` and `forklift.clientlog` as import-stable façades that re-export public command classes and relevant dataclasses/functions.

**Rationale:** Existing tests and external callers patch/import these modules directly; preserving paths avoids unnecessary breakage.

**Alternatives considered:**
- Hard rename and update all imports immediately: rejected due to avoidable churn.

### 3) Preserve behavior through extraction-first sequencing
Move code in small slices with tests green at each step: extract pure helpers first, then stateful command logic, then cleanup dead paths once verified unused.

**Rationale:** Incremental extraction narrows regressions and keeps failures attributable.

**Alternatives considered:**
- Big-bang rewrite of both files: rejected because debugging would be expensive.

### 4) Remove inert state only after proof of non-use
Where extracted code reveals truly unused paths (for example, fields or helper methods with no reads/callers), remove them in the same change only when behavior tests still pass.

**Rationale:** Refactor is a good opportunity to reduce noise, but safety comes first.

## Risks / Trade-offs

- **[Risk] Import cycles introduced during extraction** → **Mitigation:** keep shared constants/types in small dependency-light modules and preserve one-directional imports.
- **[Risk] Tests that patch `forklift.cli` symbols break after moving functions** → **Mitigation:** re-export moved call points in façade modules or update tests atomically.
- **[Risk] Renderer output drift from subtle formatting changes** → **Mitigation:** preserve existing rendering helpers and extend snapshot assertions where needed.
- **[Risk] Refactor scope creep into behavior changes** → **Mitigation:** explicitly constrain edits to structure and dead-code removal.

## Migration Plan

1. Extract `cli` helper units (rewrite/publication + ownership/env parsing + post-run checks) behind stable façade exports.
2. Extract `clientlog` parser and renderer into dedicated modules, then move command/follow orchestration.
3. Update imports/tests to point at new module locations only where needed, keeping `forklift.cli` and `forklift.clientlog` functional.
4. Run targeted tests for `cli` and `clientlog`, then full project checks used in this repo.
5. Rollback strategy: revert this change entirely; no persisted data or schema migration is involved.

## Open Questions

- None currently; the extraction boundaries are clear from existing method clusters.
