## MODIFIED Requirements

### Requirement: Client isolation and logging
The harness SHALL invoke `opencode run` as the non-root `forklift` user only after rendering instructions and completing any valid `setup` command declared in `FORK.md` front matter. Setup execution SHALL occur in `/workspace` via `bash -lc`, SHALL be limited to 180 seconds, and SHALL write stdout/stderr to `/harness-state/setup.log`. The OpenCode client launch itself MUST remain deterministic: it SHALL pass only sanitized model/variant/agent values plus rendered instructions and stripped `FORK.md` body content as inputs, MUST connect to `127.0.0.1:$OPENCODE_SERVER_PORT`, SHALL log stdout/stderr to `/harness-state/opencode-client.log`, and MUST NOT accept operator-provided shell command overrides for client invocation.

#### Scenario: Deterministic launch after setup gate
- **WHEN** the harness finishes instructions rendering and either no `setup` exists or setup succeeds
- **THEN** the OpenCode client starts automatically with the configured model/variant/agent, attaches to the loopback server, and appends its transcript to `/harness-state/opencode-client.log`

#### Scenario: Setup failure blocks client invocation
- **WHEN** front matter setup fails validation, exits non-zero, times out, or leaves tracked git changes
- **THEN** the harness exits non-zero and does not invoke `opencode run`
