## MODIFIED Requirements

### Requirement: Agent instructions and STUCK reporting
Upon startup the harness SHALL parse optional `FORK.md` front matter only when line 1 is `---` and a closing `---` delimiter is present. The front matter MAY define `setup` as a string command (including multiline block strings). If parsing fails, the harness SHALL terminate before agent launch with a non-zero exit status. When `setup` is present, the harness SHALL execute it with `bash -lc` in `/workspace`, enforce a 180-second timeout, write stdout/stderr to `/harness-state/setup.log`, and fail closed (non-zero exit before agent launch) if setup exits non-zero, times out, or leaves tracked git changes in the workspace. If `FORK.md` exists, the harness SHALL provide only the body content (front matter stripped) to the agent context before work begins. Immediately after rendering these instructions the harness SHALL invoke the bundled `opencode run` client, passing the rendered instructions plus stripped FORK context as inputs, and MUST log the client’s stdout/stderr to `/harness-state/opencode-client.log` for auditing. `STUCK.md` SHALL remain dedicated to agent-authored blocked-work outcomes.

#### Scenario: Setup succeeds and agent launches
- **WHEN** `FORK.md` includes valid front matter with `setup: uv sync` and the command exits successfully within 180 seconds without tracked git changes
- **THEN** `/harness-state/setup.log` contains setup output, front matter is omitted from agent-visible context, and `opencode run` is launched normally with client transcript logging

#### Scenario: Setup fails closed
- **WHEN** `FORK.md` includes `setup`, and the setup command exits non-zero or exceeds 180 seconds
- **THEN** the harness exits non-zero before invoking `opencode run`, and setup diagnostics are available in `/harness-state/setup.log`

#### Scenario: Malformed front matter prevents agent launch
- **WHEN** `FORK.md` begins with `---` but lacks valid closing front matter delimiters or parsable structure
- **THEN** the harness exits non-zero before invoking `opencode run` and does not treat the failure as an agent-authored `STUCK.md` outcome
