## ADDED Requirements

### Requirement: Run directory preparation
The host orchestrator SHALL, upon each invocation, create a new run directory at `~/forklift/runs/<project>_<YYYYMMDD_HHMMSS>` containing a duplicated workspace copy of the current fork along with empty `harness-state` and metadata files. The duplicated workspace MUST have all Git remotes removed before being handed to the agent container.

#### Scenario: Fresh run setup
- **WHEN** the user runs `forklift` inside a repository whose `origin` and `upstream` remotes resolve successfully
- **THEN** the orchestrator creates `~/forklift/runs/<project>_<timestamp>/workspace` populated with the fork contents and no Git remotes, alongside sibling `harness-state` and metadata locations
### Requirement: Workspace ownership alignment
Before launching the container, the orchestrator SHALL adjust ownership or permissions of `workspace/` and `harness-state/` so they are writable by the container's non-root user (UID/GID 1000). This can be done via `chown -R 1000:1000` or by running the container with matching UID/GID.

#### Scenario: Writable mounts
- **WHEN** the orchestrator prepares a new run directory
- **THEN** `workspace/` and `harness-state/` are writable inside the container without requiring root privileges


### Requirement: Container execution with enforced timeout
The orchestrator SHALL start exactly one containerized agent run per invocation, mounting the run's `workspace` and `harness-state` directories read-write, and SHALL terminate the container after eight minutes of wall-clock time if it has not exited on its own.

#### Scenario: Timeout enforcement
- **WHEN** the containerized agent is still running at 8 minutes elapsed since launch
- **THEN** the orchestrator stops the container, exits with a timeout status, and does not create a pull request

### Requirement: Upstream verification before pull request
After the container exits, the orchestrator SHALL verify that every commit in `upstream/main` is reachable from `main` in the run workspace before creating a pull request targeting `origin/main`. If verification fails, no pull request SHALL be created and the maintainer SHALL inspect the run directory or STUCK.md manually.

#### Scenario: Verified merge result
- **WHEN** the agent produces commits such that `git merge-base --is-ancestor upstream/main main` succeeds inside the workspace and there are unpushed changes
- **THEN** the orchestrator creates a pull request from the agent branch to `origin/main`

### Requirement: STUCK handoff
If the agent finishes without integrating upstream and writes `STUCK.md`, the orchestrator SHALL leave that file within the run directory unchanged, skip pull-request creation, and exit with a non-success status so the maintainer knows manual attention is needed.
#### Scenario: Stuck outcome captured
- **WHEN** the container exits after writing `STUCK.md` in the workspace
- **THEN** the orchestrator leaves the file in place, refrains from opening a pull request, and surfaces the "stuck" status via its own exit code/logs
