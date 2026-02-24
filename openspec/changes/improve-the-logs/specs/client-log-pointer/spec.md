## ADDED Requirements

### Requirement: Surface client log path at startup
When the Forklift agent process begins (right after the container launches and the harness starts logging), Forklift SHALL emit a single log line indicating the fully qualified path to the active `opencode-client.log` file (e.g., `/home/.../opencode-logs/opencode-client.log`). The message SHALL include the run correlator so operators can quickly navigate to the log location.

#### Scenario: Default run
- **WHEN** Forklift starts a run
- **THEN** the CLI SHALL log `Agent log: <path> (run=<correlator>)` or equivalent before any other agent-status messages.

### Requirement: Do not stream client log contents
Forklift SHALL NOT tail or inline the contents of `opencode-client.log` during the run. Operators are expected to open the file manually if deeper inspection is required.

#### Scenario: Agent produces verbose output
- **WHEN** the agent writes a large amount of trace data to `opencode-client.log`
- **THEN** Forklift SHALL still only log the file path once, without streaming or truncating contents.
