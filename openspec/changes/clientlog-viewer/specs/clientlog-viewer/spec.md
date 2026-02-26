## ADDED Requirements

### Requirement: View run logs via clientlog subcommand
Forklift SHALL provide `forklift clientlog <run-id>` which resolves the run directory, validates the presence of `harness-state/opencode-client.log`, and streams or pages the transcript without mutating run artifacts.

#### Scenario: Run directory exists
- **WHEN** the user runs `forklift clientlog oh-my-pi_20260224_050225`
- **THEN** the command SHALL locate `~/.local/state/forklift/runs/oh-my-pi_20260224_050225/harness-state/opencode-client.log` and begin rendering events.

#### Scenario: Run directory missing
- **WHEN** the user supplies a run identifier that does not exist
- **THEN** the command SHALL exit non-zero with a clear error explaining that the run directory is missing.

### Requirement: Auto-select paging or follow mode
The clientlog command SHALL auto-detect whether a run is active by consulting `run-state.json` (or equivalent metadata) and default to follow mode for active runs or pager mode for completed runs, with flags to override (`--follow`, `--once`, `--no-pager`).

#### Scenario: Active run
- **WHEN** `run-state.json` reports `status = running`
- **THEN** the command SHALL stream new log events as they arrive, similar to `tail -f`, while still rendering previously parsed steps.

#### Scenario: Completed run
- **WHEN** `run-state.json` reports `status = completed`
- **THEN** the command SHALL render the entire transcript once and pipe through a pager that supports ANSI colors (default `less -R`) unless `--no-pager` is set.

### Requirement: Render structured steps with Rosé Pine palette
The viewer SHALL parse JSON events into step blocks containing step start, agent text, agent thought content, tool calls and results, and step finish metadata, displayed with Rosé Pine colors, bold headings, dim metadata, italics for narration, and relative timestamps based on session start (time zero).

#### Scenario: Completed step block
- **WHEN** a `step_finish` event is received for a message ID
- **THEN** the command SHALL render one boxed block containing all events with that message ID, including tool output previews up to a configurable line count.

#### Scenario: Thought content present
- **WHEN** a step includes thought content in event metadata
- **THEN** the command SHALL render that thought content as a distinct visual element from normal agent narration.

#### Scenario: Partial block in once mode
- **WHEN** `--once` is used and a step lacks `step_finish`
- **THEN** the command SHALL render the available events in a visually distinct “pending” block so users can see current activity while recognizing it is incomplete.

### Requirement: Graceful shutdown and signal handling
The viewer SHALL handle `SIGINT`/`SIGTERM` by stopping the follow loop, restoring terminal state, and closing files without emitting broken ANSI sequences.

#### Scenario: User interrupts follow mode
- **WHEN** the user presses `Ctrl+C` while streaming
- **THEN** the command SHALL print a short confirmation (e.g., “Interrupted, exiting follow mode”), flush outstanding output, and exit with status 130.
