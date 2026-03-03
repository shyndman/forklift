## MODIFIED Requirements

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

### Requirement: Upstream verification before pull request
After the container exits, the orchestrator SHALL verify that every commit in the selected upstream target reference is reachable from the configured main branch in the run workspace before creating a pull request targeting `origin/<main-branch>`. If verification fails, no pull request SHALL be created and the maintainer SHALL inspect the run directory or STUCK.md manually.

#### Scenario: Verified integration result
- **WHEN** the agent produces commits such that `git merge-base --is-ancestor <selected-upstream-ref> <main-branch>` succeeds inside the workspace and there are unpushed changes
- **THEN** the orchestrator creates a pull request from the agent branch to `origin/<main-branch>`

#### Scenario: Verification uses seeded policy target alias
- **WHEN** `--target-policy=latest-version` is selected and the workspace seeds `upstream/<main-branch>` to a tag commit
- **THEN** post-run verification still uses `upstream/<main-branch>` as the canonical comparison reference
