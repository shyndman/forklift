## ADDED Requirements

### Requirement: Generate short correlator per run
During `RunDirectoryManager.prepare()`, Forklift SHALL generate a unique correlator identifier consisting of exactly four URL-safe Base64 characters (i.e., characters from `[A-Za-z0-9-_]`). The correlator SHALL be stored in `metadata.json` and exposed to later phases of the run lifecycle.

#### Scenario: Two sequential runs
- **WHEN** an operator executes Forklift twice in succession
- **THEN** each run's metadata SHALL contain distinct 4-character Base64 correlators.

### Requirement: Bind correlator to host logs
After preparing the run directory, Forklift SHALL bind the correlator to the structlog logger (e.g., via `structlog.get_logger().bind(run=<id>)`) so every subsequent log line includes the `run` field in its formatted output.

#### Scenario: Container timeout
- **WHEN** a container exceeds the timeout
- **THEN** the timeout error log SHALL include the 4-character correlator so the operator can filter related entries.

### Requirement: Propagate correlator into sandbox
Forklift SHALL set `FORKLIFT_RUN_ID=<correlator>` inside the container's environment and ensure the harness records the value inside `/harness-state/opencode-client.log`. The run correlator SHALL also be persisted to any additional host-side artifacts (e.g., PR stub, STUCK.md preview) when referenced.

#### Scenario: Agent writes STUCK.md
- **WHEN** the agent generates STUCK.md
- **THEN** the host log preview SHALL mention the correlator and the harness log SHALL include the same value in its banner.
