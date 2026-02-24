## 1. Logging Infrastructure

- [x] 1.1 Add structlog (`structlog~=25.5`) and Rich (`rich~=14.3`) to `pyproject.toml`, implement the new bootstrap in `Forklift.run()` (`structlog.configure` + `RichHandler`), and wire `--debug` to the handler level.
- [x] 1.1a Verify logging bootstrap by running `uv run forklift --debug --version` (or another no-op command) and confirming colored DEBUG output plus that the handler type is `rich.logging.RichHandler` when inspecting `logging.getLogger().handlers`.
- [x] 1.2 Replace every stdlib logging import in `src/forklift/` with structlog loggers (`structlog.get_logger()` / `.bind()`), ensuring dependency logs still flow through the Rich handler.
- [x] 1.2a Run `rg "logging\." src/forklift` to confirm no direct stdlib logging calls remain (outside the bootstrap) and fire a dependency log (e.g., enable git verbose output) to ensure it routes through structlog/Rich unchanged.
- [x] 1.3 Audit all exception handlers to call `logger.exception()` and rely on Rich tracebacks rather than `better_exceptions`.
- [x] 1.3a Intentionally trigger a `GitError` (e.g., by pointing at a missing repo) to confirm `logger.exception()` outputs include Rich-formatted stack traces with locals.

## 2. Run Correlator

- [x] 2.1 Generate a correlator token during run directory preparation, ensure it is exactly four URL-safe Base64 characters, persist it in metadata, and bind it to structlog (`structlog.get_logger().bind(run=...)`).
- [x] 2.1a Execute a dry run and inspect `metadata.json` plus CLI log output to ensure the correlator value appears in both places, remains consistent across the run, and matches the four-character Base64 URL-safe format.
- [x] 2.2 Propagate `FORKLIFT_RUN_ID` into the container env plus harness logs, and update documentation describing operator usage.
- [x] 2.2a After a run, open `/harness-state/opencode-client.log` (or the run snapshot) to confirm the correlator is recorded, and verify docs mention how to reference it.

## 3. Agent Log Pointer

- [x] 3.1 Emit a single log line right after the container launches that points to `${run_paths.opencode_logs}/opencode-client.log`, including the run correlator for easy correlation, and ensure no streaming/tailing occurs.
- [x] 3.1a Launch a sandbox run and verify the CLI prints the path with the correct correlator, then confirm no agent log lines are inlined afterward.
- [x] 3.2 Optionally log a reminder once the run finishes (success, stuck, timeout) that the same file contains the full transcript, without dumping contents.
- [x] 3.2a Test success and timeout paths to ensure the reminder (or warning when the file never appears) logs correctly and still avoids streaming output.