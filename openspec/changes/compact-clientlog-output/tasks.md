## 1. Prepare and map current behavior

- [x] 1.1 Read `src/forklift/clientlog_renderer.py` and note every place that prints `Step`, `part=`, `call=`, `snapshot=`, `tokens=` or raw fallback JSON.
- [x] 1.2 In a short implementation note (comment in PR description), define the suppression list exactly: `messageID`, `part.id`, `callID`, `snapshot`, token/cost payloads.
- [x] 1.3 Identify reusable helpers that should stay (`format_relative`, color helpers) vs helpers likely to be removed (`_render_step_block`, `_box` if no longer needed).

## 2. Implement compact snapshot rendering

- [x] 2.1 Refactor `TranscriptRenderer.render_snapshot()` to render events in input order (no grouping by `message_id`).
- [x] 2.2 Add/update a dedicated compact tool formatter that prints: timestamp, tool name, args, response body, and failure status when non-success.
- [x] 2.3 Add/update compact message rendering for text-bearing events so message text is shown directly.
- [x] 2.4 Add/update compact generic-event rendering so unknown JSON events render as concise `EVENT <type>` lines (no pretty-printed payload dump).
- [x] 2.5 Keep inline ISO/raw line handling readable and compact.

## 3. Implement compact follow rendering parity

- [x] 3.1 Refactor `TranscriptRenderer.render_follow_events()` to call the same compact event-formatting path used by snapshot rendering.
- [x] 3.2 Remove follow-mode step banners like `Step <id> • ... • live`.
- [x] 3.3 Ensure follow mode does not print synthetic completeness labels (`open`, `pending-group`, `incomplete`).

## 4. Remove legacy output paths cleanly

- [x] 4.1 Delete or stop using legacy step-box rendering code paths that exist only for old format.
- [x] 4.2 Verify no output string patterns remain for `part=`, `call=`, `snapshot=`, or token/cost blob lines in renderer output templates.
- [x] 4.3 Confirm no new compatibility flags or alternate output modes were introduced.

## 5. Keep parser behavior intentionally simple

- [x] 5.1 Do not add special-case logic for malformed/truncated trailing JSON-at-EOF records.
- [x] 5.2 Keep existing parser flow (`feed`/`flush`) intact unless a renderer dependency requires a minimal safe adjustment.

## 6. Rewrite and extend tests for junior-safe verification

- [x] 6.1 Update `ClientlogCommandTests.test_renders_pending_step_with_tool_output` expectations to compact-output assertions (tool/message content present, legacy markers absent).
- [x] 6.2 Update `ClientlogParserTests.test_parser_tracks_relative_time_and_follow_rendering` assertions to compact follow-output expectations (no `Step ... live` banner).
- [x] 6.3 Add assertions that suppressed fields are absent from rendered output (`part=`, `call=`, `snapshot=`, `tokens=`).
- [x] 6.4 Add a test/assertion covering unknown JSON event output as concise `EVENT <type>` (no raw JSON block).
- [x] 6.5 Add a test/assertion proving no synthetic completeness labels are emitted when lifecycle companions are missing.

## 7. Verification and final checks

- [x] 7.1 Run targeted tests: `uv run pytest tests/test_clientlog.py`.
- [x] 7.2 Manually run one snapshot command against a real run (`uv run forklift clientlog <run-id>`) and confirm compact readability by inspection.
- [x] 7.3 Confirm final output contains operator signal (tool name/args/response/message text) and omits legacy protocol clutter.
