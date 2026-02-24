## Why

Forklift currently configures stdlib logging once inside `_configure_logging()` (`src/forklift/cli.py`), which only attaches a root handler with the `% (asctime)s [%(levelname)s] %(message)s` format. Downstream modules such as `src/forklift/run_manager.py` and `src/forklift/container_runner.py` each grab `logging.getLogger(__name__)`, so everything ends up as plain INFO lines and caught exceptions are logged with `logging.error()` rather than stack traces. `RunDirectoryManager.prepare()` creates timestamped run directories (`<project>_<YYYYMMDD_%H%M%S>`) but metadata lacks any correlator, making it impossible to group log lines across host artifacts. Meanwhile, the harness writes live agent output to `opencode-logs/opencode-client.log` (mounted via `ContainerRunner._build_command()`), yet the CLI only prints container stdout/stderr after completion, forcing operators to open files manually when diagnosing failures.
## What Changes

- **Replace stdlib logging with structlog + Rich.** Follow these steps:
  1. Open `src/forklift/cli.py` and delete `_configure_logging()`.
  2. Add `structlog~=25.5` and `rich~=14.3` to `pyproject.toml`.
  3. In `cli.py`, create a new helper that:
     - Calls `logging.basicConfig(handlers=[RichHandler(rich_tracebacks=True, tracebacks_show_locals=True)], level=logging.INFO, format="%(message)s")` (import from `rich.logging`).
     - Calls `structlog.configure()` with `processors=[structlog.contextvars.merge_contextvars, structlog.processors.add_log_level, structlog.processors.EventRenamer("message"), structlog.stdlib.ProcessorFormatter.wrap_for_formatter]`, `logger_factory=structlog.stdlib.LoggerFactory()`, and `wrapper_class=structlog.stdlib.BoundLogger`.
     - Creates a `structlog.stdlib.ProcessorFormatter` that ultimately renders to the Rich handler.
  4. Replace every `import logging` + `logging.getLogger(__name__)` in `src/forklift/` files with `import structlog` + `logger = structlog.get_logger(__name__)`.
  5. Make sure `--debug` toggles the Rich handler level by calling `logging.getLogger().setLevel(logging.DEBUG)` when the CLI flag is present.
- **Generate a four-character correlator for every run.** In `RunDirectoryManager.prepare()`:
  1. After computing `timestamp`, build a deterministic string like `f"{timestamp}{random.getrandbits(16):04x}"`.
  2. Use `base64.urlsafe_b64encode` to encode 3 bytes of entropy, then take the first 4 characters (ensure padding is removed).
  3. Store this value in `metadata["run_id"]` and return it alongside `RunPaths`.
  4. In `Forklift.run()`, call `structlog.get_logger().bind(run=run_id)` right after `run_manager.prepare(...)` so every log line includes `run=<id>`.
  5. Pass the correlator via `container_env["FORKLIFT_RUN_ID"] = run_id`.
- **Show the client log path instead of streaming.** Right after `ContainerRunner.run()` starts the process (before `communicate()`), log: `logger.info("Agent log file located at %s", run_paths.opencode_logs / "opencode-client.log")`. Include the correlator automatically via the bound logger.
- **Keep output console-only.** Do not add JSON/file sinks right now; only the Rich console handler is required.
- **Use Rich tracebacks everywhere.** Because the Rich handler already formats exceptions, leave each `except` block calling `logger.exception("...", exc_info=True)` so the handler includes locals and stack traces.

## Capabilities

### New Capabilities
- `structlog-integration`: Configure structlog + Rich as the sole logging stack, including colored console output, always-on exception capture, run correlator binding, and Rich tracebacks.
- `run-correlator`: Generate, store, and propagate a compact correlator ID per run and inject it into both host logs and sandbox artifacts.
- `client-log-pointer`: As soon as the sandbox launches, emit the full path to `opencode-client.log` (tagged with the correlator) so operators can open it manually; do not stream its contents.

### Modified Capabilities
- `<existing-name>`: <what requirement is changing>

## Impact

- **Touch points (edit in this order):**
  1. `pyproject.toml`: add `structlog~=25.5` and `rich~=14.3` under `[project.dependencies]`.
  2. `src/forklift/cli.py`: remove `_configure_logging()`, add the new structlog/Rich bootstrap helper, bind the correlator, and log the client log path before entering the container wait.
  3. `src/forklift/run_manager.py`: generate the 4-character correlator inside `prepare()`, include it when writing `metadata.json`, and return it (e.g., by extending `RunPaths` or returning a tuple).
  4. `src/forklift/container_runner.py`: accept the run correlator (if necessary) and ensure logging of the client log path occurs before waiting for the container to finish.
  5. Documentation (`README.md`, `AGENTS.md`): add a short “Logs” section describing the correlator, console output, and where to find `opencode-client.log`.
  6. No other files require changes unless new helper modules are introduced for the logger setup.
