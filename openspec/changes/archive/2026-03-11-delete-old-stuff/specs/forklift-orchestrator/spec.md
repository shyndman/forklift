## ADDED Requirements

### Requirement: Run directory retention cleanup
Before preparing a new run workspace, the host orchestrator SHALL evaluate existing run directories under `$XDG_STATE_HOME/forklift/runs` (or `~/.local/state/forklift/runs` when `XDG_STATE_HOME` is unset) and remove directories older than 7 days.

#### Scenario: Startup cleanup removes expired run directories
- **WHEN** `forklift` starts and one or more existing run directories are older than 7 days
- **THEN** the orchestrator deletes those expired directories before continuing orchestration

#### Scenario: Startup cleanup preserves recent run directories
- **WHEN** `forklift` starts and a run directory is 7 days old or newer
- **THEN** the orchestrator leaves that directory in place

#### Scenario: Cleanup failure does not abort orchestration
- **WHEN** a run directory is older than 7 days but filesystem permissions or IO errors prevent deletion
- **THEN** the orchestrator logs the deletion failure and continues the invocation instead of exiting early
