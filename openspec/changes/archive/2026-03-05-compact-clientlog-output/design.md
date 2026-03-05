## Context

`forklift clientlog` currently uses step-grouped box rendering in `src/forklift/clientlog_renderer.py`:
- `render_snapshot()` groups by `message_id` and calls `_render_step_block()`
- `_render_step_event()` prints low-level internals (`part=...`, `call=...`, `snapshot=...`, token blobs)
- unknown events are printed as raw JSON fallback blocks

That behavior was useful for protocol debugging, but it is too noisy for day-to-day operator triage. The required change is a hard cutover to compact, operator-first output.

## Goals / Non-Goals

**Goals:**
- Make each event readable in one glance.
- Prioritize tool name, tool input arguments, tool response/output, and message text.
- Remove low-signal protocol internals from default display.
- Keep snapshot mode and follow mode consistent (same compact style).
- Keep implementation simple enough for a junior engineer to execute safely.

**Non-Goals:**
- No compatibility flag for old output (`--verbose`, `--raw`, etc.).
- No new machine-readable output mode.
- No special feature for malformed/truncated trailing JSON lines.
- No changes to run-state loading or run lifecycle behavior.

## Decisions

1. **Hard cutover: legacy renderer contract is removed**
   - Decision: Replace old step-box/ID-heavy output everywhere.
   - Rationale: user explicitly requested complete replacement.
   - Alternative rejected: dual modes (compact + legacy).

2. **Render by event, not by synthetic step summary**
   - Decision: snapshot and follow output both print compact event blocks/lines directly from parsed events.
   - Rationale: grouping headers (`Step <id>`) are noise for the intended usage.
   - Alternative rejected: keep grouped step boxes with reduced fields.

3. **Tool events are first-class**
   - Decision: `tool_use` output MUST include:
     - timestamp
     - tool name
     - key input arguments (`description`, `command`, or serialized input payload fallback)
     - output/response body (when present)
     - status only when non-success OR when needed to disambiguate
   - Rationale: this is the highest operator value.

4. **Messages are plain and readable**
   - Decision: text-bearing events render message text directly.
   - Rationale: message intent should not be buried behind protocol fields.

5. **Suppress internal protocol metadata by default**
   - Decision: do not print `messageID`, `part.id`, `callID`, `snapshot`, token/cost blobs.
   - Rationale: low-signal noise for this command’s primary use case.

6. **No completeness annotations**
   - Decision: do not emit labels like `open`, `pending-group`, or `incomplete`.
   - Rationale: user requested no special casing; renderer should simply print the events present.

## Canonical Output Contract (Junior Implementation Reference)

Use this as the exact visual target shape (wording may vary slightly, structure should not):

```text
[+00:12.481] TOOL bash
  args:
    description: Check working tree
    command: git status
  response:
    On branch main
    nothing to commit

[+00:13.102] MESSAGE
  I will start rebasing now.
```

Failure example:

```text
[+00:17.224] TOOL bash
  args:
    command: git rebase upstream/main
  response:
    fatal: unable to auto-detect email address
  status: failed
```

Unknown-but-JSON event example:

```text
[+00:18.005] EVENT step_finish
```

Raw non-JSON line example:

```text
[+00:18.991] RAW unexpected trailing line
```

## Step-by-Step Implementation Plan

1. Update `TranscriptRenderer.render_snapshot()`
   - Remove step grouping (`grouped_steps`, `_render_step_block` path).
   - Iterate events in order and emit compact lines directly.

2. Update `TranscriptRenderer.render_follow_events()`
   - Match the same compact formatting logic used by snapshot rendering.
   - Keep follow state mechanics only as needed for dedupe/order safety; do not emit step-status banners.

3. Replace `_render_step_event()` behavior
   - Introduce compact handlers:
     - `_render_compact_tool_event(...)`
     - `_render_compact_message_event(...)`
     - `_render_compact_generic_event(...)`
   - Remove references to suppressed fields.

4. Remove legacy-only helpers if unused
   - Candidate removals after refactor: `_render_step_block`, `_box`, and old step-title formatting.
   - Keep `format_relative()` and ANSI palette helpers as needed.

5. Keep parser behavior simple
   - Do not add special truncated-JSON logic.
   - Continue current line parsing flow (`feed` + `flush`) and let renderer print whatever parsed events exist.

6. Rewrite tests in `tests/test_clientlog.py`
   - Replace assertions that check for old strings (`Step msg-...`, `part=...`, `call=...`).
   - Add assertions for compact output structure and explicit suppression list.

## Risks / Trade-offs

- **[Risk] Over-removal of useful detail** → Mitigation: keep command/description/input and response visible; only suppress known low-signal internals.
- **[Risk] Snapshot and follow drift apart** → Mitigation: route both modes through the same compact event formatting helper.
- **[Risk] Unknown events become invisible** → Mitigation: always print a concise `EVENT <type>` line for JSON events we do not explicitly format.
- **[Risk] Junior engineer introduces partial rewrite regressions** → Mitigation: keep file edits localized to renderer + tests, and verify with targeted test run.
