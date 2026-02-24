## Context

Forklift configures stdlib logging just once in `Forklift.run()` and every module grabs the root logger. Messages are plain text, INFO-only unless `--debug` is set, and caught exceptions are logged with `logging.error()` so they lack stack traces. Each orchestration run currently produces scattered host logs along with an `opencode-client.log` file inside the run directory, but there is no shared identifier between those artifacts nor any real-time surfacing of the agent log stream. Structlog and Rich are not yet installed, so we miss out on structured events, contextual binding, Rich’s terminal-native tracebacks, and even a simple pointer telling operators where to find the agent’s `opencode-client.log` during a run.

## Goals / Non-Goals

**Goals:**
- Replace the stdlib logging stack with structlog (via `structlog.stdlib`, constrained as `structlog~=25.5`) so every module emits structured events, yet the CLI still prints colorized text through `rich.logging.RichHandler`.
- Always emit readable exceptions using Rich’s traceback formatter (`RichHandler(rich_tracebacks=True, tracebacks_show_locals=True)` from `rich~=14.3`), eliminating the dependency on `better_exceptions`.
- Generate a short run correlator identifier, bind it to every structlog event, and propagate it into sandbox env vars so agent artifacts carry the same tag.
- Print the absolute `opencode-client.log` path (with correlator) when the sandbox launches so operators immediately know where to inspect agent output without requiring live streaming.
- Keep host logging console-focused (Rich handler only) with no structured/file sinks at this stage.
- Preserve compatibility with existing CLI flags (e.g., `--debug`) for log level selection.

**Non-Goals:**
- Introducing a persistent log aggregation backend or remote shipping (local console/file output only).
- Redesigning container orchestration or the agent harness beyond exposing the correlator environment variable and log path pointer.
- Streaming or otherwise replicating the agent log contents in real time.

## Decisions

1. **Structlog bootstrap**
   - Remove `_configure_logging()` and replace it with a structlog configuration that uses `structlog.stdlib.LoggerFactory`, `structlog.stdlib.BoundLogger`, and a processor chain ending with `structlog.stdlib.ProcessorFormatter.wrap_for_formatter` so the final rendering happens in stdlib logging.
   - Configure the stdlib root logger with `rich.logging.RichHandler` (from Rich docs’ logging recipe) to produce colorized output while structlog handles structured context and metadata (e.g., correlator IDs).
   - Honor `--debug` by adjusting the Rich handler/structlog level dynamically.

2. **Application-wide API swap**
   - Replace every `import logging` usage with `import structlog` and `structlog.get_logger()` across `src/forklift/` modules.
   - Ensure dependency loggers still flow through the Rich handler by keeping a stdlib `logging.basicConfig(... handlers=[RichHandler(...)])` call while structlog’s `ProcessorFormatter` adapts events.

3. **Run correlator propagation**
   - When `RunDirectoryManager` fabricates the run directory, derive a compact correlator token (timestamp + entropy), encode it as four URL-safe Base64 characters, and store it in metadata.
   - Bind the token using `structlog.get_logger().bind(run=token)` immediately after preparation and expose the bound logger/reference so every importer shares the same context.
   - Inject `FORKLIFT_RUN_ID=<token>` into the container’s env via `_build_container_env()` so the harness and agent logs can reference it.

4. **Rich traceback formatter**
   - Drop `better_exceptions` entirely and rely on `RichHandler`’s `rich_tracebacks=True`/`tracebacks_show_locals=True` settings (documented in Rich’s logging guide) to show detailed stacks, plus `rich.traceback.install()` for uncaught exceptions if needed.

5. **Client log pointer instead of streaming**
   - As soon as `ContainerRunner.run()` starts the container (before we block on `communicate()`), compute the absolute path to `${run_paths.opencode_logs}/opencode-client.log` and log a single informational entry: e.g., `Agent log available at {path}` with the current correlator bound.
   - Do not tail or parse the log contents. If the file doesn’t exist yet, rely on its deterministic path; the harness always truncates/creates it (`docker/kitchen-sink/harness/run.sh` lines 23–35).
   - After the run completes, optionally log a reminder that the same path contains the final transcript.

## Risks / Trade-offs

- **Structlog + Rich dependency footprint** → Need to ensure both libs are added to `pyproject.toml` with semver-friendly ranges (e.g., `structlog~=25.5`, `rich~=14.3`) and keep them updated; mitigated by documenting usage and relying on standard constraints.
- **Operator visibility** → Without streaming, live insight still requires opening the file manually; mitigated by ensuring the pointer is printed prominently and includes the correlator.
- **Correlator collisions** → Poor token design could collide across runs; mitigate by combining timestamp + random entropy.
- **Security** → Rich tracebacks show locals by default; mitigate by allowing an opt-out env flag for production runs.

## Migration Plan

1. Add structlog (`structlog~=25.5`) and Rich (`rich~=14.3`) to `pyproject.toml`, remove unused stdlib logging configuration helpers, and scaffold the new bootstrap in `Forklift.run()`.
2. Implement structlog configuration + Rich handler wiring (including `--debug` handling and correlator binding), then verify a basic CLI invocation.
3. Update each module (`git.py`, `run_manager.py`, `container_runner.py`, etc.) to import and use structlog loggers.
4. Log the client path pointer when launching containers (and optionally after completion), ensuring the correlator is included.
5. Ensure Rich tracebacks are enabled (handler + optional `rich.traceback.install`) and document how to disable them when sensitive data is a concern.
6. Document the new logging behavior in README/AGENTS and verify during a dry-run (`uv run forklift --debug`).
