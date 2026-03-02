## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Upstream verification before pull request
After the container exits, the orchestrator SHALL verify that every commit in `upstream/{branch}` is reachable from `{branch}` in the run workspace before publishing rewritten output to local branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}`. If verification fails, no publication SHALL occur and the maintainer SHALL inspect the run directory or `STUCK.md` manually.

#### Scenario: Verified merge result
- **WHEN** the agent produces commits such that `git merge-base --is-ancestor upstream/{branch} {branch}` succeeds inside the workspace and rewritten output is available
- **THEN** the orchestrator publishes the rewritten result to local branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}`
- **AND** the orchestrator logs local review handoff instructions instead of creating a pull request
