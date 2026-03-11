## 1. Build the metrics helper module

- [x] 1.1 Create `src/forklift/post_run_metrics.py` with module docstring describing purpose and scope.
- [x] 1.2 Add typed containers (`UsageTotals`, `UsageSummary`) that represent available and unavailable summary states.
- [x] 1.3 Implement a parser function that reads `Path` input for `opencode-client.log` and iterates line-by-line.
- [x] 1.4 In parser, ignore non-JSON lines and JSON lines where `type != "step_finish"`.
- [x] 1.5 In parser, sum numeric `part.cost` across valid `step_finish` events.
- [x] 1.6 In parser, track the final `step_finish` event with numeric `part.tokens.total` and extract component values from that same snapshot.
- [x] 1.7 Add missing-field defaults (`0`) for token components in the final snapshot.
- [x] 1.8 Return unavailable summary with reason text when no valid usage payloads are found.
- [x] 1.9 Keep dependency surface unchanged: use existing `rich` runtime dependency; do not add direct `markdown-it-py` dependency unless implementation starts importing it directly.

## 2. Implement footer rendering behavior

- [x] 2.1 Add a renderer function that prints `Run complete: <outcome>` followed by a blank line and `Grand total` heading.
- [x] 2.2 Render metrics using Rich `Table` with one label column and one `justify="right"` value column.
- [x] 2.3 Render rows in exact order: Input, Output, Reasoning, Cache read, blank spacer, Total tokens, Total cost.
- [x] 2.4 Format token values with thousands separators and total cost with `$` + fixed 4 decimal places.
- [x] 2.5 Add unavailable rendering path (`Grand total: unavailable` + reason line).
- [x] 2.6 Add completion-report rendering via Rich Markdown (`rich.markdown.Markdown` + terminal render path) with no structlog formatting.
- [x] 2.7 Implement report-file precedence: print `STUCK.md` when present, otherwise `DONE.md`, otherwise no report section.

## 3. Integrate into CLI lifecycle safely

- [x] 3.1 Update `src/forklift/cli.py` to import parser/renderer from `post_run_metrics.py`.
- [x] 3.2 Add a single finalization path that emits footer exactly once for every terminal outcome.
- [x] 3.3 Preserve existing timed-out handling (exit code 2) while ensuring footer prints before exit.
- [x] 3.4 Preserve existing non-zero container failure handling while ensuring footer prints before exit.
- [x] 3.5 Handle `SystemExit(4)` from STUCK flow by printing footer then re-raising same exit code.
- [x] 3.6 Confirm successful flow still calls existing post-run publication logic, then prints success footer.
- [x] 3.7 Ensure completion-report Markdown render is emitted after footer and before process exit across all terminal outcomes.
- [x] 3.8 Remove/relocate post-run `STUCK.md` preview logging so report content is not duplicated in structlog format.
- [x] 3.9 Treat the first `Run complete: <outcome>` write as terminal-end output start; after that point, avoid `logger.info`/`logger.warning`/`logger.error`/`logger.exception` calls.

## 4. Add focused automated tests

- [x] 4.1 Create `tests/test_post_run_metrics.py` with parser tests for valid logs, mixed logs, malformed lines, and no-usage fallback.
- [x] 4.2 In `tests/test_post_run_metrics.py`, add rendering tests that assert right-aligned value-column behavior and expected row order.
- [x] 4.3 Update `tests/test_cli_runtime.py` to assert footer appears in success and failure output captures.
- [x] 4.4 Update `tests/test_cli_post_run.py` to assert stuck outcome still emits footer before exit.
- [x] 4.5 Add assertions that existing exit codes remain unchanged across all affected paths.
- [x] 4.6 Add tests for completion-report precedence (`STUCK.md` over `DONE.md`) and fallback when neither file exists.
- [x] 4.7 Add tests proving report output is rendered via Rich Markdown (still no structlog prefix formatting) and appears after the metrics block.
- [x] 4.8 Add tests that after the first `Run complete: <outcome>` line, no logger-driven lines appear and no `logger.*` calls are made before exit.

## 5. Verify end-to-end behavior

- [x] 5.1 Run `uv run pytest tests/test_post_run_metrics.py`.
- [x] 5.2 Run `uv run pytest tests/test_cli_runtime.py tests/test_cli_post_run.py`.
- [x] 5.3 Run `openspec validate --changes "post-run-metrics-table" --strict` (or repository-equivalent OpenSpec validation command) and fix any schema issues.
- [x] 5.4 If any dependency entry is added/changed during implementation, verify current registry version first and use non-pinned minor-friendly constraints (do not hard-pin exact patch versions).
