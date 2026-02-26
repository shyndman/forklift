## Why

Forklift operators currently have to read raw `opencode-client.log` files or sift
through JSON events to understand why an agent stalled. The lack of a
purpose-built viewer slows incident response and makes prompt iteration tedious,
especially when trying to tail a live run.

## What Changes

- Add a `forklift clientlog <run-id>` subcommand that renders agent steps,
 tool calls, and narration using the Rosé Pine palette, grouping related
 events into easy-to-scan boxes with relative timestamps.
- Provide both paging and follow modes (auto-detected via run status) so logs
 for completed runs open in a pager while live runs stream like `tail -f`.
- Persist lightweight run-state metadata (status, timestamps, exit code) so the
 viewer and other tooling can reliably detect whether a run is still active.
- Handle `SIGINT`/`SIGTERM` gracefully so follow mode exits cleanly without
 leaving the terminal in an inconsistent state.

## Capabilities

### New Capabilities
- `clientlog-viewer`: Adds a CLI experience for formatting and interactively
 browsing agent log transcripts, including grouping by steps and colorized
 tool call output with configurable preview lengths.
- `run-state-tracker`: Emits and maintains `run-state.json` alongside each run
 so host-side tooling can tell whether the sandbox is still executing and pick
 the appropriate viewing mode.

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