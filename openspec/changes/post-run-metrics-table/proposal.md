## Why

Forklift currently logs pointers to `harness-state/opencode-client.log` during/after runs (`src/forklift/cli.py`) and provides transcript rendering via `forklift clientlog` (`src/forklift/clientlog.py`), but neither path surfaces aggregate token/cost totals. We need a first-class end-of-run summary so usage and spend are visible immediately on container-executed runs.

## What Changes

- Add a post-run usage summarizer that reads `harness-state/opencode-client.log` (the same path already used by the harness in `docker/kitchen-sink/harness/run.sh`, host CLI run flow in `src/forklift/cli.py`, and transcript command in `src/forklift/clientlog.py`) and computes grand totals from `step_finish` events.
- Print an unformatted, human-readable footer at the very end of CLI execution with run outcome plus right-aligned totals for input/output/reasoning/cache-read tokens, total tokens, and total cost.
- After the footer, render the selected completion report file using Rich Markdown (prefer `workspace/STUCK.md`, otherwise `workspace/DONE.md`) with no structlog prefixes.
- Integrate footer emission into all container-execution terminal branches currently present in `Forklift.run`: timeout (`SystemExit(2)`), non-zero container exit (`SystemExit(container_result.exit_code)`), successful container completion, and stuck outcomes surfaced from `post_container_results` (`SystemExit(4)` via `src/forklift/cli_post_run.py`).
- Preserve existing no-op behavior when `_is_target_already_integrated(...)` returns true (that branch exits before container execution and remains outside this summary scope).
- Keep the output intentionally spacious and easy to scan at rest (multi-line summary, one metric per line, right-aligned values).
- Ensure footer/report emission is terminal-end output: once `Run complete: <outcome>` is written, emit only plain footer/report text and no logger-driven lines afterward.

## Capabilities

### New Capabilities
- `post-run-metrics-summary`: Compute and display end-of-run usage/cost totals directly in host CLI output.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `src/forklift/cli.py` for final output ordering and outcome handling in container-execution branches
  - `src/forklift/cli_post_run.py` for `STUCK.md` handling/logging behavior during post-run processing
  - `src/forklift/post_run_metrics.py` (new helper module) for parsing and rendering usage totals
- Existing dependencies/patterns leveraged:
  - `rich~=14.3` is already a runtime dependency (`pyproject.toml`); this change reuses Rich for both table layout and Markdown report rendering
  - Verified dependency chain from PyPI metadata: Rich includes Markdown support and depends on `markdown-it-py>=2.2.0` (with `mdurl`) transitively; no new direct dependency entries are required for this change
  - current CLI output already mixes structlog with direct terminal writes; Rich Markdown rendering for completion reports stays inside existing output patterns
- Affected tests:
  - `tests/test_target_policy.py` for `Forklift.run` success/failure orchestration paths
  - `tests/test_cli_post_run.py` (currently post-run rewrite/publication focused) to be extended for stuck-path report behavior assertions
  - `tests/test_post_run_metrics.py` (new) for parser/formatter unit coverage
- External behavior: container-executed runs gain a final summary block with aligned totals and run outcome, followed by a Rich Markdown render of `STUCK.md`/`DONE.md` when present.
