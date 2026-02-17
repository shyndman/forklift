## ADDED Requirements

### Requirement: OpenCode socket access control
The container SHALL expose the OpenCode Unix-domain socket at `/opt/opencode/opencode.sock` with ownership `root:opencode` and permissions `0660`, ensuring only the bundled `forklift` user (a member of the `opencode` group) can connect. Any additional user inside the container MUST be denied access.

#### Scenario: Socket restricted to forklift
- **WHEN** the harness connects to `/opt/opencode/opencode.sock` as user `forklift`
- **THEN** the connection succeeds, while a different unprivileged user attempting the same connection receives a permission error, demonstrating that only the intended client can reach the server

## MODIFIED Requirements

### Requirement: Agent instructions and STUCK reporting
Upon startup the harness SHALL instruct the agent to merge `upstream/main` into `main`, run any discoverable tests, craft meaningful commit messages, and, if blocked, create a `STUCK.md` file summarizing the problem, steps attempted, and current outcome. If `FORK.md` exists at the workspace root, the harness SHALL provide its contents to the agent before work begins. Immediately after rendering these instructions the harness SHALL invoke the bundled `opencode run` client (with no opportunity for operator-provided shell commands), passing the rendered instructions plus FORK context as inputs, and MUST log the client’s stdout/stderr to `/harness-state/opencode-client.log` for auditing.

#### Scenario: Blocked merge
- **WHEN** the agent determines the merge cannot be safely completed within the runtime (e.g., conflicting business logic) and writes `STUCK.md`
- **THEN** the harness log shows the deterministic `opencode run` invocation, `/harness-state/opencode-client.log` captures the agent output leading to the blockage, and the resulting `STUCK.md` includes plain-language descriptions of the blocking issue, the attempts made, and the resulting state so a human maintainer can decide next steps
