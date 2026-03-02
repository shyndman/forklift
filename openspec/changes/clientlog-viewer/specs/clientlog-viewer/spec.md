## ADDED Requirements

### Requirement: View run logs via clientlog subcommand
Forklift SHALL provide `forklift clientlog <run-id>` which resolves the run directory, validates the presence of `harness-state/opencode-client.log`, and renders a formatted transcript snapshot without mutating run artifacts.

#### Scenario: Run directory exists
- **WHEN** the user runs `forklift clientlog oh-my-pi_20260224_050225`
- **THEN** the command SHALL locate `~/.local/state/forklift/runs/oh-my-pi_20260224_050225/harness-state/opencode-client.log` and begin rendering events.

#### Scenario: Run directory missing
- **WHEN** the user supplies a run identifier that does not exist
- **THEN** the command SHALL exit non-zero with a clear error explaining that the run directory is missing.

### Requirement: Default one-shot output with optional follow mode
The clientlog command SHALL render all currently available log history in one pass by default and exit, while supporting `-f`/`--follow` to continue streaming appended events.

#### Scenario: Default invocation
- **WHEN** the user runs `forklift clientlog <run-id>` without flags
- **THEN** the command SHALL render the available transcript history once and exit.

#### Scenario: Follow flag enabled
- **WHEN** the user runs `forklift clientlog <run-id> --follow`
- **THEN** the command SHALL render existing history first and then stream newly appended events until interrupted.

### Requirement: Render structured steps with Rosé Pine palette
The viewer SHALL parse JSON events into step blocks containing step start, agent text, agent thought content, tool calls and results, and step finish metadata, displayed with Rosé Pine colors, bold headings, dim metadata, italics for narration, and relative timestamps based on session start (time zero). The viewer SHALL prioritize information fidelity over styling consistency and MUST NOT omit available event content solely because the event is incomplete or cannot be fully styled.

#### Scenario: Completed step block
- **WHEN** a `step_finish` event is received for a message ID
- **THEN** the command SHALL render one boxed block containing all events with that message ID, including full tool output captured for that step.

#### Scenario: Thought content present
- **WHEN** a step includes thought content in event metadata
- **THEN** the command SHALL render that thought content as a distinct visual element from normal agent narration.

#### Scenario: Partial block in default output
- **WHEN** default one-shot rendering sees a step that lacks `step_finish`
- **THEN** the command SHALL render the available events in a visually distinct “pending” block so users can see current activity while recognizing it is incomplete, and SHALL include every available event field/content captured so far.

#### Scenario: Unstyleable or unknown event payload
- **WHEN** an event payload is partial, malformed, or not recognized by the normal style mapper
- **THEN** the command SHALL emit a raw fallback representation containing the available event data rather than dropping that data from output.

### Requirement: Graceful shutdown and signal handling
The viewer SHALL handle `SIGINT`/`SIGTERM` by stopping the follow loop, restoring terminal state, and closing files without emitting broken ANSI sequences.

#### Scenario: User interrupts follow mode
- **WHEN** the user presses `Ctrl+C` while streaming
- **THEN** the command SHALL print a short confirmation (e.g., “Interrupted, exiting follow mode”), flush outstanding output, and exit with status 130.
