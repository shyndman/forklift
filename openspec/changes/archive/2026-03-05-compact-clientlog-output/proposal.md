## Why

Verified baseline from current code:
- `TranscriptRenderer.render_snapshot()` groups events by `message_id` and renders step boxes (`src/forklift/clientlog_renderer.py`).
- `TranscriptRenderer.render_follow_events()` renders `Step <id> • <status> • live` banners (`src/forklift/clientlog_renderer.py`).
- `_render_step_event()` and `_render_tool_event()` print protocol internals such as `part=...`, `call=...`, `snapshot=...`, and token blobs (`src/forklift/clientlog_renderer.py`).
- Unknown JSON events are currently emitted as pretty-printed raw fallback payloads (`src/forklift/clientlog_renderer.py`).

These verified behaviors make `forklift clientlog` noisy for operator triage. We need compact output that foregrounds tool calls and message text.

## What Changes

- Replace step-box rendering in `TranscriptRenderer` with compact event-first rendering in both snapshot and follow mode.
- Keep high-signal fields visible:
  - tool name
  - tool input arguments (for example description/command)
  - tool response/output
  - message text
- Remove verified noise sources from output:
  - `Step <id> ...` group headers
  - `part=...`, `call=...`, `snapshot=...`
  - token/cost blobs from lifecycle events
  - raw pretty-printed JSON fallback blocks
- Keep hard cutover semantics: no compatibility mode, no dual-format flag.
- Keep parser behavior as-is for malformed trailing lines (no new truncated-JSON-at-EOF feature work).

## Capabilities

### New Capabilities
- `clientlog-compact-output`: Defines the compact transcript contract for snapshot and follow rendering, including which fields are shown, which fields are suppressed, and failure/error visibility expectations.

### Modified Capabilities
- None.

## Impact

- Verified primary change surface:
  - `src/forklift/clientlog_renderer.py` (all user-visible formatting lives here today)
  - `tests/test_clientlog.py` (currently asserts legacy step/part/call output strings)
- Verified likely-unchanged surfaces:
  - `src/forklift/clientlog_parser.py` already provides needed event fields (`event_type`, `text`, `relative_ms`, `payload`) and line parsing behavior.
  - `src/forklift/clientlog_command.py` orchestrates reading/following and does not define formatting rules.
- User-facing behavior: `forklift clientlog` output changes materially and permanently to compact formatting.
- Dependencies/APIs: no external dependency changes expected.
