## Why

Forklift operators currently have to read raw `opencode-client.log` files or sift
through JSON events to understand why an agent stalled. The lack of a
purpose-built viewer slows incident response and makes prompt iteration tedious,
especially when trying to tail a live run.

## What Changes

- Add a `forklift clientlog <run-id>` subcommand that renders agent steps,
 tool calls, and narration using the Rosé Pine palette, grouping related
 events into easy-to-scan boxes with relative timestamps.
- Make default invocation render and print the formatted transcript snapshot
 to stdout (including pending/incomplete step blocks when `step_finish` is
 missing) without omitting available event content, and support
 `-f`/`--follow` to continue streaming new events.
- Persist lightweight run-state metadata (status, timestamps, exit code) so the
 viewer and other tooling can reliably detect whether a run is still active.
- Handle `SIGINT`/`SIGTERM` gracefully so follow mode exits cleanly without
 leaving the terminal in an inconsistent state.

## Capabilities

### New Capabilities
- `clientlog-viewer`: Adds a CLI experience for formatting and interactively
 browsing agent log transcripts, including grouping by steps and colorized
 tool call output.
- `run-state-tracker`: Emits and maintains `run-state.json` alongside each run
 so host-side tooling can tell whether the sandbox is still executing and
 support follow-mode behavior.

### Modified Capabilities
- _None._

## Impact

- `src/forklift/cli.py`: register the new clypi subcommand and bootstrap shared
 state/config handling.
- `src/forklift/logs.py` or new helpers: parse/format log events with the Rosé
 Pine palette, framing steps, tool calls, and narration.
- `RunDirectoryManager` / `ContainerRunner`: write and update the run-state
 metadata file as the container starts and exits.
- Documentation / README snippets highlighting `forklift clientlog` usage.