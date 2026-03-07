## ADDED Requirements

### Requirement: Usage aggregation SHALL use explicit `step_finish` field rules
The host CLI SHALL parse usage metrics only from JSON events where `type` equals `step_finish` in `harness-state/opencode-client.log`. Cost SHALL be aggregated by summing every numeric `part.cost` value found in `step_finish` events. Total tokens SHALL be taken from the final observed numeric `part.tokens.total` value in log order.

#### Scenario: Compute totals from mixed transcript
- **WHEN** `opencode-client.log` contains plain-text harness lines, non-JSON lines, non-`step_finish` JSON events, and valid `step_finish` events
- **THEN** Forklift ignores all non-`step_finish` lines for usage aggregation
- **AND** Forklift computes totals from valid `step_finish` entries only

#### Scenario: Cost aggregation
- **WHEN** multiple `step_finish` events contain numeric `part.cost` values
- **THEN** Forklift reports total cost equal to the arithmetic sum of those values

#### Scenario: Token total selection
- **WHEN** multiple `step_finish` events contain numeric `part.tokens.total` values
- **THEN** Forklift reports grand total tokens from the final such event in log order

### Requirement: Component token rows SHALL come from final totals snapshot
Forklift SHALL populate Input tokens, Output tokens, Reasoning tokens, and Cache read tokens from the same final `step_finish` event used to source `tokens.total`. If any component field is missing in that final snapshot, Forklift SHALL treat the missing value as `0`.

#### Scenario: Missing component field in final snapshot
- **WHEN** the final `step_finish` event contains `tokens.total` but omits one or more component fields
- **THEN** Forklift prints `0` for omitted component rows
- **AND** Forklift still prints the reported total tokens value

### Requirement: End-of-run footer SHALL be printed for every terminal outcome
Forklift SHALL print a plain (non-structlog) end-of-run footer after container execution for all terminal outcomes: success, non-zero failure, timed out, and stuck.

#### Scenario: Success path
- **WHEN** run execution and post-run verification complete successfully
- **THEN** Forklift prints an outcome line `Run complete: success`
- **AND** Forklift prints the grand-total metrics block

#### Scenario: Failure path
- **WHEN** run execution ends with non-zero exit code, timeout, or stuck outcome
- **THEN** Forklift still prints an outcome line and grand-total footer before exiting
- **AND** Forklift preserves existing exit code semantics for that failure mode

### Requirement: Completion report rendering SHALL use Rich Markdown and be final
After rendering the grand-total footer, Forklift SHALL render one completion report file from the run workspace using Rich Markdown output (not structlog formatting; no log level, timestamp, or key/value prefix):
- `STUCK.md`, if present
- otherwise `DONE.md`, if present

Forklift SHALL feed the selected file contents to Rich Markdown rendering without truncation. If both files are present, `STUCK.md` takes precedence. If neither file exists, Forklift SHALL skip the report section without failing.

Forklift SHALL implement this rendering through the existing `rich` runtime dependency and SHALL NOT require adding a new direct Markdown parser dependency for this change.

#### Scenario: Stuck report present
- **WHEN** run workspace contains `STUCK.md`
- **THEN** Forklift renders `STUCK.md` content after the grand-total block using Rich Markdown output
- **AND** Forklift does not print a structlog-formatted `STUCK.md` preview

#### Scenario: Done report present
- **WHEN** run workspace contains `DONE.md` and does not contain `STUCK.md`
- **THEN** Forklift renders `DONE.md` content after the grand-total block using Rich Markdown output

### Requirement: Footer/report output SHALL be terminal-end output
Forklift SHALL treat terminal-end output as beginning at the first footer line (`Run complete: <outcome>`).

From that line until process exit, Forklift SHALL emit only terminal writes for the footer/report section (for example `print(...)` and/or `console.print(...)`) and SHALL NOT emit logger-driven output (`logger.info`, `logger.warning`, `logger.error`, `logger.exception`), except unavoidable interpreter-level crash output.

#### Scenario: Completion ordering stability
- **WHEN** Forklift reaches terminal output rendering for a run
- **THEN** the final CLI output sequence is: outcome line, grand-total block, optional report-file dump
- **AND** after `Run complete: <outcome>` is written, no logger-driven output lines appear before process exit

### Requirement: Footer layout and value alignment SHALL be stable
Forklift SHALL render a "Grand total" block with one metric per row and a single right-justified value column so all numeric values align to the same right boundary.

#### Scenario: Mixed-width numeric values
- **WHEN** displayed token/cost values have different character lengths
- **THEN** every value in the metrics block is right-aligned to one shared value column boundary

### Requirement: Numeric formatting SHALL be human-readable and consistent
Forklift SHALL format token counts with thousands separators and SHALL format total cost with a leading `$` and exactly four digits after the decimal point.

#### Scenario: Token and currency formatting
- **WHEN** Forklift renders non-zero totals
- **THEN** token rows use grouped digits (for example `5,864,522`)
- **AND** total cost row uses fixed four-decimal currency format (for example `$0.6562`)

### Requirement: Missing usage data SHALL degrade gracefully
If no valid usage payloads can be derived from `step_finish` events, Forklift SHALL not fabricate totals and SHALL print an explicit unavailable message with a reason.

#### Scenario: No usage events found
- **WHEN** `opencode-client.log` contains no valid `step_finish` usage payloads
- **THEN** Forklift prints `Grand total: unavailable` (or equivalent)
- **AND** Forklift includes a reason indicating no usage events were found
