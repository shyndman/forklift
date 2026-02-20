# opencode-agent-bridge Specification

## Purpose
TBD - created by archiving change add-the-brains. Update Purpose after archive.
## Requirements
### Requirement: OpenCode server lifecycle
The kitchen-sink container entrypoint SHALL start the OpenCode server as `root`, sourcing credentials from the host-provided `~/.config/forklift/opencode.env`, and SHALL bind it to `127.0.0.1:$OPENCODE_SERVER_PORT` before any unprivileged process runs. The server MUST stream its stdout/stderr into `/harness-state/opencode-server.log` and SHALL terminate cleanly when the container stops so no API keys remain resident.

#### Scenario: Server ready before harness
- **WHEN** the container launches for a new run
- **THEN** the HTTP health endpoint succeeds on `127.0.0.1:$OPENCODE_SERVER_PORT` and `/harness-state/opencode-server.log` records the successful startup before the harness script executes as the `forklift` user

### Requirement: Client isolation and logging
The harness SHALL invoke `opencode run` as the non-root `forklift` user immediately after rendering instructions, passing only the sanitized model/variant/agent values plus the rendered instructions and FORK.md content as inputs. The client MUST connect to the server via `127.0.0.1:$OPENCODE_SERVER_PORT`, SHALL log stdout/stderr to `/harness-state/opencode-client.log`, and MUST operate without reading any host secrets or executing arbitrary shell provided by the operator.

#### Scenario: Deterministic agent launch
- **WHEN** the harness finishes printing instructions and FORK context
- **THEN** the OpenCode client starts automatically with the configured model/variant/agent, attaches to the loopback server, and appends its transcript to `/harness-state/opencode-client.log` without requiring host-side command overrides

