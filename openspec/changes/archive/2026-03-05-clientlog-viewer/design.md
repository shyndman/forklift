## Context

Forklift currently launches runs and leaves transcripts in `harness-state/opencode-client.log`, but the host CLI provides no structured way to read or tail them. Operators rely on `cat`/`less`/`tail -f` and mental parsing of JSON. We are adding a `clientlog` subcommand plus run-state metadata to deliver a formatted, Rosé Pine-themed transcript view with a default one-shot dump and optional follow mode.

Key code touch points:
- `src/forklift/cli.py`: register the subcommand, parse args, resolve run paths, and wire logging.
- `src/forklift/logs.py` (or new module): implement log parser + renderer using clypi styling.
- `RunDirectoryManager` / `ContainerRunner`: produce `run-state.json` with status+timestamps so viewers know if a run is active.

## Goals / Non-Goals

**Goals:**
- Provide `forklift clientlog <run-id>` with a default one-shot transcript dump and optional `-f`/`--follow` streaming mode.
- Render grouped steps with Rosé Pine palette, badges, relative timestamps, and full tool output.
- Introduce `run-state.json` persisted beside each run to advertise status (`starting`, `running`, `completed`, `failed`, `timed_out`).
- Handle signals in follow mode to keep terminal state clean.

**Non-Goals:**
- Replaying log events into structured JSON (plain text CLI only for now).
- Editing or mutating run artifacts (viewer is read-only).
- Supporting reduced-color terminals (24-bit color only).
- Web/GUI surfaces—the scope is a CLI feature.

## Decisions

1. **Command structure**: implement `class Clientlog(Command)` with positional `run_id` and a single optional flag (`-f`/`--follow`). Root `Forklift` gets `subcommand: Clientlog | None`. Rationale: keeps CLI surface minimal while preserving live-tail utility.
2. **Run state detection**: persist `/runs/<id>/run-state.json` containing `{status, run_id, prepared_at, container_started_at, finished_at, exit_code}`. `RunDirectoryManager` writes `status:"starting"` and `prepared_at` before container launch; `ContainerRunner` updates to `running` with `container_started_at`, then to `completed|failed|timed_out` on exit. Rationale: deterministic signal beats heuristics based on mtime.
3. **Rendering pipeline**: treat log as event stream. Parser distinguishes ISO-prefixed harness lines vs JSON OpenCode events, tagging each with relative timestamp (ms offset from first event). Step blocks are grouped by `messageID` only. `part.id` is retained as per-event identity/metadata inside a step block (for stable ordering and diagnostics) and does not create separate boxes. The renderer uses Rosé Pine palette constants plus clypi styler to emit headings, tool badges, narration italics, and a distinct style for agent thought content. Data fidelity is prioritized over styling: if an event is partial, unknown, or cannot be styled consistently, render its available content in a raw fallback representation instead of dropping fields. Rationale: grouping by logical step matches harness UI and keeps CLI readable while preserving developer-visible evidence.
4. **Default rendering + follow implementation**: default command behavior parses and renders the available transcript once, then exits. If `-f`/`--follow` is set, the command renders existing history first, then enters an async-friendly blocking loop (e.g., `while True: new_bytes = file.read(); if not new_bytes: sleep`) plus `select`/`os.stat` to detect growth. Incomplete steps without `step_finish` render as provisional “pending” blocks that still include all currently available event content. In follow mode specifically, newly arriving events for an unfinished step MUST be surfaced immediately in stream order (without waiting for `step_finish`), and each event should be emitted once in the appended stream. Rationale: predictable default output with simple tail semantics when explicitly requested.
5. **Signal handling**: register handlers for `SIGINT`/`SIGTERM` that set an `interrupted` flag, break loops, close files, and print a short dimmed status before exiting with appropriate code. Rationale: prevents half-rendered ANSI sequences when users press Ctrl+C.


## Risks / Trade-offs

- **Risk**: Missing or corrupt `run-state.json` breaks lifecycle context and indicates a run-integrity problem.
  - Mitigation: Treat this as fatal: validate `run-state.json` presence/readability up front, and exit non-zero with a clear remediation message if state metadata is missing or malformed.
- **Risk**: Large logs could consume memory during one-shot rendering.
  - Mitigation: Stream rendering directly to stdout and only retain per-step buffers needed for grouping.
- **Risk**: Parser must tolerate partial JSON writes during follow.
  - Mitigation: use incremental decoder (e.g., `json.JSONDecoder().raw_decode`) with carryover buffer for incomplete chunks.
- **Risk**: ANSI colors unreadable on non-24-bit terminals.
  - Mitigation: Intentional; call out in help text.
- **Trade-off**: `run-state.json` adds writes during run start/finish.
  - Mitigation: use strict atomic writes only: write to a temp file in the same directory, `fsync` the temp file, `rename`/`replace` into place, then `fsync` the parent directory.

## Migration Plan

1. Update `RunDirectoryManager` to create run-state with `status:"starting"`, `run_id`, and `prepared_at` as soon as run directories exist.
2. Update `ContainerRunner` to mark `running` with `container_started_at`, then `completed|failed|timed_out` on exit (capture exit code and `finished_at`).
3. Implement parser/renderer module and `Clientlog` command; add CLI registration.
4. Document new command in README / help output.
5. Release new Forklift version.

## Scope Decisions

- Keep `run-state.json` minimal to lifecycle metadata only (`status`, timestamps, `exit_code`); do not include container name/PID in this change.
- Keep `clientlog` output human-readable only in this change; no `--json` mode.
- Follow mode reads `opencode-client.log` only in this change; `opencode-server.log` is out of scope.
