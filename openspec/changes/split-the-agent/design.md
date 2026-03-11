## Context

`forklift changelog` currently runs one synthesis step: `Changelog.run()` builds a single `EvidenceBundle`, `changelog_llm.py` serializes that full bundle into one prompt, and one agent writes the entire narrative. That worked while the report only summarized upstream changes, but it no longer preserves the original operator boundary because the same prompt now contains fork-aware conflict evidence and explicitly asks for fork-side descriptions.

This change has two hard constraints:

1. Preserve the existing report structure:
   - `## Summary`
   - `## Key Change Arcs`
   - `## Conflict Pair Evaluations`
   - `## Risk and Review Notes`
2. Restore a real knowledge boundary for the top half of the report without weakening the lower-half conflict analysis.

Stakeholders:

- Operators who want upstream-oriented summaries they can trust.
- Maintainers who still want deep conflict analysis in the bottom half.
- Future contributors who need an architecture that makes accidental prompt drift harder.

## Goals / Non-Goals

**Goals:**
- Split changelog synthesis into two section-scoped agent calls.
- Ensure the agent that writes `Summary` and `Key Change Arcs` receives upstream-only evidence.
- Keep the full current evidence available to the agent that writes `Conflict Pair Evaluations` and `Risk and Review Notes`.
- Make the host own final markdown assembly so section boundaries are enforced in code, not by convention.
- Preserve existing read-only changelog command behavior and post-run usage summary output.

**Non-Goals:**
- Redesigning the visible report structure.
- Removing fork-aware conflict analysis from the lower-half sections.
- Replacing the current deterministic evidence collectors with a new analysis pipeline.
- Adding new third-party dependencies or new operator-facing CLI flags in this change.

## Decisions

### 1. Keep one deterministic analysis pass and derive a second payload from it

Decision:
- Continue building the current full evidence bundle once in `changelog_analysis.py`.
- Add a projection step that derives a smaller upstream-only payload for the top-half agent.

Why:
- Git analysis is the expensive, correctness-sensitive part. Duplicating it would increase risk and create drift between top-half and bottom-half views of the same upstream branch.
- A projection function gives us an auditable, testable boundary for what the upstream-only agent can know.

Alternative considered:
- Build two separate evidence bundles independently.
- Rejected because it duplicates collection logic and raises the risk of inconsistent refs, summaries, or exclusion handling.

### 2. Split synthesis by section ownership, not by prompt suggestions

Decision:
- Replace the current single narrative API with two narrow APIs:
  1. upstream narrative generation for `Summary` and `Key Change Arcs`
  2. conflict/review generation for `Conflict Pair Evaluations` and `Risk and Review Notes`
- Each API returns only the sections it owns.

Why:
- The bug exists because one agent owns the whole narrative surface. A prompt-only reminder not to mention the fork would be fragile.
- Section-scoped return types make ownership explicit and testable.

Alternative considered:
- Keep one API and try to constrain the model with stricter wording.
- Rejected because the same model would still receive both sides of the evidence and could still leak that knowledge into the top half.

### 3. Make host-side assembly the only way the final document is produced

Decision:
- The host assembles final markdown in fixed section order from structured section outputs.
- The renderer no longer treats the narrative as one opaque markdown blob authored by one agent.

Why:
- This preserves the current operator-facing structure while enforcing the new boundary at the code level.
- It prevents the bottom-half agent from reintroducing top-half summaries.

Alternative considered:
- Let one agent produce a full document and splice in the other agent's text.
- Rejected because it recreates the same drift vector: one agent can still override or duplicate the other's section.

### 4. Aggregate both model runs into one changelog usage summary

Decision:
- Keep the existing single changelog usage table, but sum token counts and estimated cost across both agent runs.
- Keep one wall-clock measurement for the whole command.

Why:
- `forklift changelog` remains one operator command. The post-run summary should still answer "what did this command cost?" without making users reconcile two separate tables.

Alternative considered:
- Print two usage summaries, one per agent.
- Rejected because it increases noise and makes the CLI less scannable.

### 5. Run both agent calls after evidence construction without changing failure posture

Decision:
- After deterministic evidence is built and the upstream-only projection exists, run both synthesis steps from `Changelog.run()` and fail the command if either one fails.
- No partial markdown is rendered.

Why:
- The command output remains a coherent report, not a partially generated artifact.
- The new architecture should not weaken current hard-fail behavior.

Alternative considered:
- Fall back to whichever sections succeeded.
- Rejected because partial reports would be ambiguous and would hide synthesis failures from operators.

## Risks / Trade-offs

- [Projection drift] → If upstream-only payload construction accidentally includes fork-aware fields, the boundary collapses again. Mitigation: add direct tests asserting the projected payload omits fork-side comparison data and conflict-side evidence.
- [Section contract drift] → Future prompt edits could broaden an agent's scope. Mitigation: add contract tests for allowed headings and banned section labels in each prompt.
- [Dual-model cost and latency] → Two calls will cost more and may take longer. Mitigation: aggregate usage transparently and keep both calls bounded by the same deterministic evidence set.
- [Renderer complexity increases] → Host-side assembly is more explicit than passing one markdown blob through. Mitigation: use small section dataclasses so assembly remains straightforward and testable.

## Migration Plan

1. Add new payload/result dataclasses for upstream-only evidence and section-scoped outputs.
2. Add the upstream-only projection function in `changelog_analysis.py`.
3. Split `changelog_llm.py` into two prompt contracts and two public generation functions while reusing shared model/env plumbing.
4. Update `changelog.py` orchestration to call both generators, aggregate usage, and assemble final markdown in fixed order.
5. Update `changelog_renderer.py` to render the section-assembled document plus existing deterministic tables.
6. Update tests to lock in payload isolation, section ownership, failure behavior, and usage aggregation.
7. Update `README.md` and the new spec so the documented contract matches the implementation.

Rollback strategy:
- The change is code-only with no persisted state migration. Reverting the code and docs returns changelog generation to the prior single-agent behavior.

## Open Questions

- None. The operator-facing section split and isolation boundary are decided for this change.
