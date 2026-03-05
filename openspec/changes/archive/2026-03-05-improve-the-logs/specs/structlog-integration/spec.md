## ADDED Requirements

### Requirement: Structlog config replaces stdlib logging
The CLI SHALL initialize structlog (using `structlog.stdlib.LoggerFactory` and `structlog.configure()`) before any other module emits logs. Initialization SHALL install a `rich.logging.RichHandler` as the stdlib handler with `markup=False` by default and Route structured events through `structlog.stdlib.ProcessorFormatter` so the existing CLI log-level flag continues to control output.

#### Scenario: Debug flag enabled
- **WHEN** the operator runs `forklift --debug`
- **THEN** the Rich handler SHALL emit DEBUG-level messages with colorized formatting and structlog’s context (e.g., run correlator) rendered inline.

#### Scenario: Dependency emits stdlib logs
- **WHEN** a library imported by Forklift calls `logging.warning()`
- **THEN** the message SHALL appear once through the Rich handler using the configured formatter, showing the same timestamp + level schema as structlog events.

### Requirement: Rich tracebacks replace better_exceptions
All Forklift-controlled exception handlers SHALL continue to call `logger.exception()`, and the logging stack SHALL use Rich’s traceback formatter by configuring `RichHandler(rich_tracebacks=True, tracebacks_show_locals=True)` (per Rich’s logging docs) or by printing exceptions via `rich.traceback`. No dependency on `better_exceptions` SHALL remain.

#### Scenario: Git command fails
- **WHEN** `run_git()` raises `GitError`
- **THEN** the handler SHALL emit a Rich-formatted traceback showing local variables for each frame and the command’s stdout/stderr excerpt without any truncation.

### Requirement: Logging API usage is uniform
Every module under `src/forklift/` SHALL obtain loggers via `structlog.get_logger()` (optionally binding context via `.bind(...)`) instead of the stdlib logging module, and SHALL stop referencing module-local logger instances (e.g., `logging.getLogger(__name__)`).

#### Scenario: Container runner logs launch
- **WHEN** `ContainerRunner.run()` announces the docker command
- **THEN** it SHALL call `structlog.get_logger("container").info(...)` (or a pre-bound logger) without instantiating a stdlib logger.
