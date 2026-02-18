## ADDED Requirements

### Requirement: OpenCode configuration handling
The host CLI SHALL read OpenCode credentials and defaults from `~/.config/forklift/opencode.env` (or the path supplied via env override), validate that required keys such as `OPENCODE_API_KEY`, `OPENCODE_MODEL`, `OPENCODE_VARIANT`, and `OPENCODE_AGENT` are present, and export only those values into the container environment. The CLI SHALL provide typed options (`--model`, `--variant`, `--agent`) that override the defaults, and MUST reject any values containing characters outside the safe whitelist (letters, digits, `.`, `_`, `-`, `/`) to block shell injection.

#### Scenario: Validated configuration
- **WHEN** an operator runs `forklift` with `--model claude-35-sonnet` and a populated `~/.config/forklift/opencode.env`
- **THEN** the CLI confirms the env file contains the required keys, accepts the sanitized override, and forwards only the validated values into the container environment without exposing other host secrets

## MODIFIED Requirements

### Requirement: Container execution with enforced timeout
The orchestrator SHALL start exactly one containerized agent run per invocation, mounting the run's `workspace` and `harness-state` directories read-write, and SHALL terminate the container after eight minutes of wall-clock time if it has not exited on its own. The container command MUST be fixed to the bundled harness entrypoint; generic overrides such as `FORKLIFT_DOCKER_COMMAND` SHALL NOT be supported. Only the validated OpenCode environment variables (`OPENCODE_MODEL`, `OPENCODE_VARIANT`, `OPENCODE_AGENT`, `OPENCODE_API_KEY`, and related settings) may be forwarded, ensuring the harness always executes the same deterministic client startup sequence.

#### Scenario: Timeout enforcement
- **WHEN** the containerized agent is still running at 8 minutes elapsed since launch
- **THEN** the orchestrator stops the container, exits with a timeout status, and does not create a pull request, and logs show that the container was launched with the fixed harness command and sanitized OpenCode environment
