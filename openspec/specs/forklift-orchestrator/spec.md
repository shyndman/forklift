# forklift-orchestrator Specification

## Purpose
TBD - created by archiving change forklift-v0. Update Purpose after archive.
## Requirements
### Requirement: Run directory preparation
The host orchestrator SHALL, for invocations that require integration work, create a new run directory at `$XDG_STATE_HOME/forklift/runs/<project>_<YYYYMMDD_HHMMSS>` (defaults to `~/.local/state/forklift/runs/<project>_<YYYYMMDD_HHMMSS>`) containing a duplicated workspace copy of the current fork along with empty `harness-state` and metadata files. The duplicated workspace MUST have all Git remotes removed before being handed to the agent container. If the selected upstream target is already reachable from the configured main branch, the orchestrator SHALL exit successfully before run-directory creation.

#### Scenario: Fresh run setup
- **WHEN** the user runs `forklift` inside a repository whose `origin` and `upstream` remotes resolve successfully and the selected upstream target is not yet an ancestor of the configured main branch
- **THEN** the orchestrator creates `$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>/workspace` (or `~/.local/state/forklift/runs/<project>_<timestamp>/workspace`) populated with the fork contents and no Git remotes, alongside sibling `harness-state` and metadata locations
- **AND** metadata for the run records the selected target policy and resolved target SHA

#### Scenario: No-op integration short-circuit
- **WHEN** the selected upstream target is already reachable from the configured main branch before orchestration begins
- **THEN** the orchestrator exits with success and does not create a run directory or launch the container
- **AND** logs identify the selected policy and target SHA that made the run a no-op

### Requirement: Workspace ownership alignment
Before launching the container, the orchestrator SHALL adjust ownership or permissions of `workspace/` and `harness-state/` so they are writable by the container's non-root user (UID/GID 1000). This can be done via `chown -R 1000:1000` or by running the container with matching UID/GID.

#### Scenario: Writable mounts
- **WHEN** the orchestrator prepares a new run directory
- **THEN** `workspace/` and `harness-state/` are writable inside the container without requiring root privileges

### Requirement: Container execution with enforced timeout
The orchestrator SHALL start exactly one containerized agent run per invocation, mounting the run's `workspace` and `harness-state` directories read-write, and SHALL terminate the container after eight minutes of wall-clock time if it has not exited on its own. The container command MUST be fixed to the bundled harness entrypoint; generic overrides such as `FORKLIFT_DOCKER_COMMAND` SHALL NOT be supported. Only the validated OpenCode environment variables (`OPENCODE_MODEL`, `OPENCODE_VARIANT`, `OPENCODE_AGENT`, `OPENCODE_API_KEY`, and related settings) may be forwarded, ensuring the harness always executes the same deterministic client startup sequence.

#### Scenario: Timeout enforcement
- **WHEN** the containerized agent is still running at 8 minutes elapsed since launch
- **THEN** the orchestrator stops the container, exits with a timeout status, and does not create a pull request, and logs show that the container was launched with the fixed harness command and sanitized OpenCode environment

### Requirement: Upstream verification before pull request
After the container exits, the orchestrator SHALL verify that every commit in `upstream/{branch}` is reachable from `{branch}` in the run workspace before publishing rewritten output to local branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}`. If verification fails, no publication SHALL occur and the maintainer SHALL inspect the run directory or `STUCK.md` manually.

#### Scenario: Verified merge result
- **WHEN** the agent produces commits such that `git merge-base --is-ancestor upstream/{branch} {branch}` succeeds inside the workspace and rewritten output is available
- **THEN** the orchestrator publishes the rewritten result to local branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}`
- **AND** the orchestrator logs local review handoff instructions instead of creating a pull request

### Requirement: STUCK handoff
If the agent finishes without integrating upstream and writes `STUCK.md`, the orchestrator SHALL leave that file within the run directory unchanged, skip pull-request creation, and exit with a non-success status so the maintainer knows manual attention is needed.
#### Scenario: Stuck outcome captured
- **WHEN** the container exits after writing `STUCK.md` in the workspace
- **THEN** the orchestrator leaves the file in place, refrains from opening a pull request, and surfaces the "stuck" status via its own exit code/logs

### Requirement: OpenCode configuration handling
The host CLI SHALL read OpenCode credentials and defaults from `~/.config/forklift/opencode.env` (or the path supplied via env override), validate that required keys such as `OPENCODE_API_KEY`, `OPENCODE_MODEL`, `OPENCODE_VARIANT`, and `OPENCODE_AGENT` are present, and export only those values into the container environment. The CLI SHALL provide typed options (`--model`, `--variant`, `--agent`) that override the defaults, and MUST reject any values containing characters outside the safe whitelist (letters, digits, `.`, `_`, `-`, `/`) to block shell injection.

#### Scenario: Validated configuration
- **WHEN** an operator runs `forklift` with `--model claude-35-sonnet` and a populated `~/.config/forklift/opencode.env`
- **THEN** the CLI confirms the env file contains the required keys, accepts the sanitized override, and forwards only the validated values into the container environment without exposing other host secrets

### Requirement: Bounded post-merge authorship rewrite
After a successful container run, the orchestrator SHALL rewrite commit authorship only for commits in `upstream/{branch}..HEAD` within the run workspace branch `{branch}`. The orchestrator MUST NOT rewrite commits that are reachable from `upstream/{branch}` itself.

#### Scenario: Rewrite is limited to post-upstream commits
- **WHEN** the run workspace contains commits on `{branch}` whose ancestry includes `upstream/{branch}`
- **THEN** the rewrite operation updates only commits in the range `upstream/{branch}..HEAD`
- **AND** commit ancestry at or before `upstream/{branch}` remains unchanged

### Requirement: Local-only publication handoff
After bounded rewrite succeeds, the orchestrator SHALL publish the rewritten branch tip to the local repository branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}` and SHALL NOT push rewritten commits to GitHub remotes.

#### Scenario: Rewritten output published locally
- **WHEN** rewrite and verification both succeed for `{branch}`
- **THEN** the orchestrator creates or updates local branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}` to the rewritten tip
- **AND** no `git push` to remote `origin` is performed in post-run handling

### Requirement: Command modules SHALL be responsibility-separated
The host CLI implementation SHALL separate orchestration, post-run verification/publication, and support utilities into focused modules so each module owns one primary concern. The `forklift` command behavior, flags, and exit code semantics MUST remain unchanged after the split.

#### Scenario: Forklift command behavior remains stable after extraction
- **WHEN** the codebase is refactored to move `Forklift` internals out of a single large module
- **THEN** `forklift` continues to expose the same command entrypoint and flags
- **AND** existing success/failure exit code behavior remains unchanged

### Requirement: Client transcript tooling SHALL be componentized
Client transcript handling SHALL be split into parser, renderer, and command-follow orchestration components with clear boundaries. Transcript rendering semantics and follow-mode termination behavior MUST remain unchanged.

#### Scenario: Clientlog output semantics preserved across component split
- **WHEN** transcript parsing/rendering/follow logic is extracted into dedicated modules
- **THEN** snapshot mode still renders grouped step output equivalent to pre-split behavior
- **AND** follow mode still exits after terminal run-state detection using the existing debounce behavior

