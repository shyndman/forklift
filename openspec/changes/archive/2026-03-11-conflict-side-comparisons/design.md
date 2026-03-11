## Context

We already detect likely conflicts using `git merge-tree`, then summarize results in changelog output. That is useful for triage, but too thin for design-level merge conversations.

Note: this archived design describes the change that originally introduced full-evidence conflict-side comparisons into changelog synthesis. The current implementation has since been tightened by `split-the-agent`, which keeps this fork-aware evidence in the bottom-half sections (`Conflict Pair Evaluations` and `Risk and Review Notes`) while restoring an upstream-only top half.

The user requirement is explicit:

1. Scope strictly to merge-tree conflict paths.
2. Compare both sides of each conflict path (fork vs upstream).
3. Use internal evidence to describe features in plain English.
4. Keep mechanical ordering.
5. Keep raw evidence out of default output.

## Audience and Implementation Style

This design is written so a junior engineer can implement it safely.

Principles:

1. Prefer explicit data structures over implicit dicts.
2. Parse only what we need.
3. Keep every step deterministic and testable.
4. Use deterministic evidence for synthesis, but do not dump that evidence directly to operators.

## Glossary

- **Fork side**: commits in `base..main`
- **Upstream side**: commits in `base..upstream/<main>`
- **Conflict path**: file path returned by merge-tree conflict parsing
- **Hunk header**: the `@@ ... @@` line in unified diff output
- **Mechanical ordering**: `conflict_count` descending, then path ascending
- **Plain-English summary**: behavior-level explanation of a feature, not just an internal name

## Current Verified Flow

```text
resolve refs/base
   -> compute hotspots
   -> build EvidenceBundle (global metrics + conflicts)
   -> send asdict(bundle) to LLM
   -> render markdown/terminal
```

Verified files:

- `src/forklift/changelog_analysis.py`
- `src/forklift/changelog_models.py`
- `src/forklift/changelog_llm.py`
- `src/forklift/changelog_renderer.py`
- `src/forklift/changelog.py`

## Target Flow

```text
resolve refs/base
   -> compute hotspots (already exists)
   -> for each conflict path (mechanical order):
        collect fork-side evidence
        collect upstream-side evidence
   -> attach side-comparison structures to EvidenceBundle
   -> generate narrative with required conflict-pair section
   -> render narrative and supporting metrics without raw conflict evidence blocks
```

## Decisions

### 1) Keep explicit side-comparison models

Decision:

- Keep explicit per-path side-comparison structures in the model layer.

Why:

- They make deterministic extraction testable even though the default renderer does not expose the raw structures.

### 2) Use Git CLI path-scoped extraction internally

Decision:

- Use path-limited `git log` and `git diff` for each conflict path.

Commands:

1. `git log --oneline <base>..<main> -- <path>`
2. `git log --oneline <base>..<upstream_ref> -- <path>`
3. `git diff --unified=0 <base>...<main> -- <path>`
4. `git diff --unified=0 <base>...<upstream_ref> -- <path>`

Parsing rules:

1. Commit sample lines: `"<sha> <subject>"`
2. Hunk headers: keep lines starting with `@@`
3. Churn: use existing stat parsing pattern from analysis module

Use of this data:

1. Internal evidence for LLM synthesis
2. Test fixtures and regression coverage
3. Not part of default operator-facing markdown

### 3) Keep full conflict-path coverage by default

Decision:

- Analyze all filtered conflict paths unless a safety cap is hit internally.

Why:

- Users asked not to thin aggressively.
- Internal caps can still protect runtime and prompt size.

### 4) Keep mechanical ordering end-to-end

Decision:

- Use the same ordering in collection, prompt payload, and narrative interpretation.

Order key:

1. `-conflict_count`
2. `path` ascending

### 5) Require conceptual summaries, not evidence dumps

Decision:

- LLM output must include `## Conflict Pair Evaluations`.
- Each path subsection must include:
  1. Fork-side intent
  2. Upstream-side intent
  3. Conceptual relationship
  4. Why this is or is not a conceptual conflict
  5. Merge considerations

Prompt rules:

1. Explain repo-local jargon in plain English.
2. Write `Upstream-side intent` as a short paragraph when evidence supports it, not a one-line label.
3. If exact behavior cannot be inferred safely, say `insufficient evidence`.
4. Do not restate churn counts, commit lists, hunk headers, or truncation metadata in final markdown.

### 6) Dependency posture for v1

Decision:

- Do not add new third-party dependencies in this change.

Verified third-party interfaces used:

1. Git CLI docs for path-limited `log`/`diff` and `--unified`
2. Pydantic AI docs for `Agent(...); await agent.run(...)`

## File-by-File Implementation Plan

### `src/forklift/changelog_models.py`

1. Keep dataclasses for side evidence and per-path comparison entries.
2. Keep deterministic evidence available to prompt-building and tests.
3. Ensure defaults remain safe and serialization remains stable.

### `src/forklift/changelog_analysis.py`

1. Keep internal evidence collectors for commits, hunk headers, and churn.
2. Preserve mechanical ordering while building per-path comparison entries.
3. Continue attaching internal side-comparison structures to `EvidenceBundle`.
4. Do not move formatting concerns into analysis.

### `src/forklift/changelog_llm.py`

1. Update prompt contract to require plain-English feature explanations.
2. Require explicit uncertainty when evidence is weak.
3. Forbid raw evidence dumping in the generated markdown.
4. Keep prompt payload grounded in deterministic side-comparison evidence.

### `src/forklift/changelog_renderer.py`

1. Render branch context, narrative, hotspot table, and supporting metrics.
2. Keep the narrative as the operator-facing place for conflict summaries.
3. Preserve existing no-conflicts behavior.

### `src/forklift/changelog.py`

1. Confirm flow remains read-only.
2. Ensure enriched bundle still reaches the narrative generator.
3. Keep renderer usage simple: render the synthesized narrative, not an extra evidence section.

### `tests/test_changelog.py`

Add or preserve tests for:

1. merge-tree-only scope (no non-conflict paths)
2. ordering (`conflict_count` desc, path asc)
3. hunk-header extraction (`@@` capture only)
4. sparse-side evidence behavior
5. prompt requirement for plain-English explanations
6. renderer output staying focused on narrative summaries

## Concrete Acceptance Checklist (Junior-Friendly)

Implementation is done only when all are true:

1. Running changelog on conflicting branches shows conceptual fork/upstream summaries in the narrative.
2. `Upstream-side intent` is a short paragraph when evidence supports a fuller explanation.
3. Output order matches mechanical ordering in every run.
4. Opaque feature names are explained in behavior-level language, or the output says `insufficient evidence`.
5. Operator-facing output stays focused on conceptual summaries.
6. Tests in `tests/test_changelog.py` cover new behavior and pass.

## Risks / Trade-offs

1. **Weak commit messages reduce semantic quality**
   - Mitigation: include multiple deterministic signals internally and require `insufficient evidence` fallback.
2. **Large conflict sets increase runtime/prompt size**
   - Mitigation: keep bounded internal sampling even though operators do not see those limits directly.
3. **More narrative responsibility falls on the prompt**
   - Accepted; this is the product goal.

## Non-Goals

1. No analysis of non-conflicting files.
2. No full-patch ingestion.
3. No changelog command side effects.
