## MODIFIED Requirements

### Requirement: Run directory preparation
The host orchestrator SHALL, for invocations that require integration work, create a new run directory at `$XDG_STATE_HOME/forklift/runs/<project>_<YYYYMMDD_HHMMSS>` (defaults to `~/.local/state/forklift/runs/<project>_<YYYYMMDD_HHMMSS>`) containing a duplicated workspace copy of the current fork along with empty `harness-state` and metadata files. The duplicated workspace MUST have all Git remotes removed before being handed to the agent container. If the selected upstream target is already reachable from the configured main branch, the orchestrator SHALL exit successfully before run-directory creation. Read-only commands such as `forklift changelog` MUST NOT call run-directory preparation, run-state lifecycle updates, container launch, or post-run publication helpers.

#### Scenario: Fresh run setup
- **WHEN** the user runs `forklift` inside a repository whose `origin` and `upstream` remotes resolve successfully and the selected upstream target is not yet an ancestor of the configured main branch
- **THEN** the orchestrator creates `$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>/workspace` (or `~/.local/state/forklift/runs/<project>_<timestamp>/workspace`) populated with the fork contents and no Git remotes, alongside sibling `harness-state` and metadata locations
- **AND** metadata for the run records the selected target policy and resolved target SHA

#### Scenario: No-op integration short-circuit
- **WHEN** the selected upstream target is already reachable from the configured main branch before orchestration begins
- **THEN** the orchestrator exits with success and does not create a run directory or launch the container
- **AND** logs identify the selected policy and target SHA that made the run a no-op

#### Scenario: Changelog command stays outside orchestration lifecycle
- **WHEN** the user runs `forklift changelog` for any supported main-branch value
- **THEN** the command performs branch analysis without creating a run directory
- **AND** no container launch or run-state lifecycle file updates occur
- **AND** no local publication branch is created as part of changelog execution
