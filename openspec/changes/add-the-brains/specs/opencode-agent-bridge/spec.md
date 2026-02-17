## ADDED Requirements

### Requirement: OpenCode server lifecycle
The kitchen-sink container entrypoint SHALL start the OpenCode server as `root`, sourcing credentials from the host-provided `~/.config/forklift/opencode.env`, and SHALL bind it to `/opt/opencode/opencode.sock` before any unprivileged process runs. The server MUST stream its stdout/stderr into `/harness-state/opencode-server.log` and SHALL terminate cleanly when the container stops so no API keys remain resident.

#### Scenario: Server ready before harness
- **WHEN** the container launches for a new run
- **THEN** `/opt/opencode/opencode.sock` exists with ownership `root:opencode`, permissions `0660`, and `/harness-state/opencode-server.log` records the successful startup before the harness script executes as the `forklift` user

### Requirement: Client isolation and logging
The harness SHALL invoke `opencode run` as the non-root `forklift` user immediately after rendering instructions, passing only the sanitized model/variant/agent values plus the rendered instructions and FORK.md content as inputs. The client MUST connect exclusively through `/opt/opencode/opencode.sock`, SHALL log stdout/stderr to `/harness-state/opencode-client.log`, and MUST operate without reading any host secrets or executing arbitrary shell provided by the operator.

#### Scenario: Deterministic agent launch
- **WHEN** the harness finishes printing instructions and FORK context
- **THEN** the OpenCode client starts automatically with the configured model/variant/agent, connects via `/opt/opencode/opencode.sock`, and appends its transcript to `/harness-state/opencode-client.log` without requiring host-side command overrides
