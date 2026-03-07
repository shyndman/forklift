## Context

Forklift already records all needed usage data in `harness-state/opencode-client.log`, but it does not present an end-of-run summary in the CLI. Operators must open the log and manually inspect `step_finish` JSON entries to understand token usage and cost.

This change is output-only (no API or storage migration), but it touches the most sensitive part of the CLI lifecycle: terminal run completion paths. Today, `Forklift.run` exits from multiple branches (`success`, `non-zero failure`, `timed_out`, and `STUCK` via `SystemExit(4)` from post-run handling). A junior-friendly implementation must remove ambiguity about where summary output is emitted.

## Goals / Non-Goals

**Goals:**
- Always print a final run outcome line and a "Grand total" footer after container execution.
- Print the contents of `STUCK.md` or `DONE.md` (if present) as plain terminal text immediately after the footer.
- Display right-aligned numeric values for:
  - Input tokens
  - Output tokens
  - Reasoning tokens
  - Cache read tokens
  - Total tokens
  - Total cost
- Keep summary output outside structlog formatting.
- Keep completion-report output outside structlog formatting and avoid post-footer log noise.
- Preserve existing exit-code behavior exactly.
- Make the implementation easy to follow by separating parsing, formatting, and lifecycle integration.

**Non-Goals:**
- Streaming metrics during execution.
- Changing OpenCode client log format.
- Persisting summarized metrics into new files.
- Introducing new runtime dependencies.

## Decisions

### 1) Add a dedicated helper module for parsing + rendering
Create `src/forklift/post_run_metrics.py` with a narrow scope:
- Parse usage fields from `opencode-client.log`
- Build a typed totals object
- Render final summary output using Rich components

This keeps `cli.py` focused on orchestration and lets tests target metrics behavior directly.

#### Third-party dependency contract (verified)
- Forklift already depends on `rich~=14.3` in `pyproject.toml`; no new direct dependency is required for Markdown rendering.
- Verified via PyPI metadata: Rich currently exposes Markdown rendering and depends on `markdown-it-py>=2.2.0` (plus `mdurl`) transitively.
- Runtime lock already includes these transitives (`markdown-it-py`, `mdurl`), so implementation should not add a direct `markdown-it-py` dependency unless requirements change.
- Official Rich docs call path/order:
  1. `from rich.markdown import Markdown`
  2. `from rich.console import Console`
  3. `console = Console()`
  4. `console.print(Markdown(markup_text))`
- Rich reference constructor (14.3 docs): `Markdown(markup, code_theme='monokai', justify=None, style='none', hyperlinks=True, inline_code_lexer=None, inline_code_theme=None)`.

#### Dependency alternatives considered
- **Option A (chosen):** Use existing Rich dependency only; rely on transitive Markdown parser packages already pulled by Rich.
- **Option B:** Add `markdown-it-py` as a direct dependency for explicitness. Rejected for now because we do not import it directly.
- **Option C:** Replace Rich Markdown with another renderer. Rejected as unnecessary scope/risk for this output-only change.

#### Proposed data structures
Use simple dataclasses (or equivalent typed containers):
- `UsageTotals`
  - `input_tokens: int`
  - `output_tokens: int`
  - `reasoning_tokens: int`
  - `cache_read_tokens: int`
  - `total_tokens: int`
  - `total_cost: float`
- `UsageSummary`
  - `available: bool`
  - `totals: UsageTotals | None`
  - `reason_unavailable: str | None`

### 2) Define exact parsing rules (no hidden behavior)
Parsing logic SHALL be deterministic and line-order driven.

#### Parsing algorithm
1. Open `<run>/harness-state/opencode-client.log`.
2. If file is missing or unreadable, return unavailable summary with explicit reason.
3. Iterate line by line.
4. Skip non-JSON lines.
5. Parse JSON lines; skip invalid JSON silently.
6. Keep only events where `event["type"] == "step_finish"`.
7. For each `step_finish`:
   - If `part.cost` is numeric, add it to running `total_cost`.
   - If `part.tokens.total` is numeric, treat this event as the latest totals snapshot candidate.
8. After iteration:
   - If no valid `step_finish` usage payloads were found, return unavailable (`no usage events found`).
   - Else read component totals from the final snapshot event:
     - `tokens.input`
     - `tokens.output`
     - `tokens.reasoning`
     - `tokens.cache.read`
     - `tokens.total`
   - Missing component fields default to `0`.

#### Why final snapshot for tokens
The observed protocol behavior is:
- `tokens.total` is cumulative
- component token fields are emitted alongside each step
Using the final snapshot avoids accidental double-counting and aligns with displayed run total semantics.

### 3) Define exact rendering rules
Render output as plain terminal footer (not structlog event fields).

#### Output order
1. `Run complete: <outcome>`
2. blank line
3. `Grand total`
4. metrics rows (one row per metric)
5. optional blank line
6. optional completion report render (prefer `STUCK.md`, else `DONE.md`) via Rich Markdown

#### Metric row order
1. Input tokens
2. Output tokens
3. Reasoning tokens
4. Cache read tokens
5. (blank spacer row)
6. Total tokens
7. Total cost

#### Formatting rules
- Token numbers use thousands separators (e.g., `5,864,522`).
- Total cost uses `$` prefix and exactly 4 decimal places.
- Values are rendered in a single right-justified value column (Rich `Table` with `justify="right"`).
- If summary unavailable, print:
  - `Grand total: unavailable`
  - `Reason: <reason>`
- Completion report contents are rendered from the selected file via Rich Markdown (no truncation, no structlog prefixes, no log level/timestamp decorations).

#### Completion report selection
- If `workspace/STUCK.md` exists, print that file.
- Else if `workspace/DONE.md` exists, print that file.
- If both exist, `STUCK.md` wins.
- If neither exists, skip the report section.

### 4) Integrate once in CLI lifecycle (single finalization path)
Refactor `Forklift.run` so every terminal path flows through one finalization routine.

#### Integration shape
- Track outcome in local variable (`success`, `failure`, `timed out`, `stuck`).
- Execute existing run flow.
- Convert branch exits into outcome + exit code data.
- Before exiting, call summary renderer exactly once, then optional completion-report render exactly once.
- Treat the first `Run complete: <outcome>` write as the start of terminal-end output.
- After terminal-end output begins, do not call `logger.info`, `logger.warning`, `logger.error`, or `logger.exception`; emit only terminal output writes for footer/report text (`print(...)` or Rich `console.print(...)`).
- Raise/return using the existing exit code.

#### STUCK handling detail
`post_container_results` can raise `SystemExit(4)`. Catch that specific branch in `Forklift.run`, set outcome to `stuck`, render summary, then re-raise with code 4.

### 5) Junior-safe file-by-file plan
1. Create `src/forklift/post_run_metrics.py`
   - Add dataclasses
   - Add `parse_usage_summary(log_path: Path) -> UsageSummary`
   - Add `render_usage_summary(outcome: str, summary: UsageSummary) -> None`
2. Update `src/forklift/cli.py`
   - Import helper functions
   - Add single finalization path that always renders summary before exit
3. Add `tests/test_post_run_metrics.py`
   - Parser and formatter unit tests
4. Update existing CLI tests
   - `tests/test_cli_runtime.py`
   - `tests/test_cli_post_run.py`

## Risks / Trade-offs

- **[Lifecycle regressions]** Refactoring exit branches can alter exit semantics.
  - **Mitigation:** assert exact exit codes for success/failure/timed_out/stuck in tests.
- **[Protocol drift]** OpenCode may change payload shape.
  - **Mitigation:** tolerate missing fields, skip malformed lines, and emit unavailable reason instead of crashing.
- **[Formatting drift]** Small changes may break alignment guarantees.
  - **Mitigation:** add formatting tests that verify right-aligned values and required row order.

## Migration Plan

1. Implement parser and renderer module with unit tests first.
2. Integrate renderer call in CLI terminal flow.
3. Update/extend CLI path tests for all outcomes.
4. Run targeted tests.
5. Merge without feature flag (safe because behavior is additive output only).

Rollback strategy: revert `cli.py` integration and helper module changes.

## Open Questions

- None. Decisions are intentionally fixed for implementation clarity:
  - Show `Cache read tokens` (not cache write).
  - Format total cost with 4 decimal places.
