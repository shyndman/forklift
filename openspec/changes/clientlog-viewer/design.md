## Context

Forklift currently launches runs and leaves transcripts in `harness-state/opencode-client.log`, but the host CLI provides no structured way to read or tail them. Operators rely on `cat`/`less`/`tail -f` and mental parsing of JSON. We are adding a `clientlog` subcommand plus run-state metadata to deliver a formatted, Rosé Pine-themed transcript view with auto follow/pager behavior.

Key code touch points:
- `src/forklift/cli.py`: register the subcommand, parse args, resolve run paths, and wire logging.
- `src/forklift/logs.py` (or new module): implement log parser + renderer using clypi styling.
- `RunDirectoryManager` / `ContainerRunner`: produce `run-state.json` with status+timestamps so viewers know if a run is active.

## Goals / Non-Goals

**Goals:**
- Provide `forklift clientlog <run-id>` with sane defaults and override flags for follow/pager mode.
- Render grouped steps with Rosé Pine palette, badges, relative timestamps, and configurable stdout preview lengths.
- Introduce `run-state.json` persisted beside each run to advertise status (`starting`, `running`, `completed`, `failed`).
- Handle signals in follow mode to keep terminal state clean.

**Non-Goals:**
- Replaying log events into structured JSON (plain text CLI only for now).
- Editing or mutating run artifacts (viewer is read-only).
- Supporting reduced-color terminals (24-bit color only).
- Web/GUI surfaces—the scope is a CLI feature.

## Decisions

1. **Command structure**: implement `class Clientlog(Command)` with positional `run_id` and optional flags (`--follow`, `--once`, `--pager`, `--no-pager`, `--tool-lines`). Root `Forklift` gets `subcommand: Clientlog | None`. Rationale: aligns with clypi’s union subcommand pattern and keeps behavior self-contained.
2. **Run state detection**: persist `/runs/<id>/run-state.json` containing `{status, run_id, prepared_at, container_started_at, finished_at, exit_code}`. `RunDirectoryManager` writes `status:"starting"` and `prepared_at` before container launch; `ContainerRunner` updates to `running` with `container_started_at`, then to `completed|failed|timed_out` on exit. Rationale: deterministic signal beats heuristics based on mtime.
3. **Rendering pipeline**: treat log as event stream. Parser distinguishes ISO-prefixed harness lines vs JSON OpenCode events, tagging each with relative timestamp (ms offset from first event). Events are grouped by `messageID` (step) and `part.id`. The renderer uses Rosé Pine palette constants plus clypi styler to emit headings, tool badges, narration italics, and a distinct style for agent thought content. Rationale: grouping by logical step matches harness UI and keeps CLI readable.
4. **Follow implementation**: use async-friendly blocking loop (e.g., `while True: new_bytes = file.read(); if not new_bytes: sleep`) plus `select`/`os.stat` to detect growth. Keep incomplete steps buffered until `step_finish`; for `--once` produce provisional blocks (dotted border). Rationale: simple tail semantics without external dependencies.
5. **Pager handling**: Completed runs render into a buffer and pipe via `less -R` using `subprocess.run`; `--no-pager` writes directly to stdout. Follow mode streams immediately (no pager). Rationale: ensures colors display properly when viewing historical logs.
6. **Signal handling**: register handlers for `SIGINT`/`SIGTERM` that set an `interrupted` flag, break loops, close files, and print a short dimmed status before exiting with appropriate code. Rationale: prevents half-rendered ANSI sequences when users press Ctrl+C.
7. **Configuration knobs**: expose `--tool-lines` (default 10) and `--since <seconds>` to limit backlog when following. Rationale: large logs shouldn’t flood the terminal.

## Risks / Trade-offs

- **Risk**: Missing or corrupt `run-state.json` → viewer misclassifies mode.
  - Mitigation: Fallback to heuristics (if missing, default to pager but honor `--follow`). Log warning.
- **Risk**: Large logs could consume memory before paging.
  - Mitigation: Stream to pager via pipe rather than building giant string; only buffer per-step when necessary.
- **Risk**: Parser must tolerate partial JSON writes during follow.
  - Mitigation: use incremental decoder (e.g., `json.JSONDecoder().raw_decode`) with carryover buffer for incomplete chunks.
- **Risk**: ANSI colors unreadable on non-24-bit terminals.
  - Mitigation: Intentional; call out in help text.
- **Trade-off**: `run-state.json` adds writes during run start/finish.
  - Mitigation: file is tiny; ensure writes flushed with `fsync` or rely on normal write semantics.

## Migration Plan

1. Update `RunDirectoryManager` to create run-state with `status:"starting"`, `run_id`, and `prepared_at` as soon as run directories exist.
2. Update `ContainerRunner` to mark `running` with `container_started_at`, then `completed|failed|timed_out` on exit (capture exit code and `finished_at`).
3. Implement parser/renderer module and `Clientlog` command; add CLI registration.
4. Document new command in README / help output.
5. Release new Forklift version.

## Open Questions

- Should `run-state.json` include container PID or docker name for other tooling?
- Do we want to expose JSON output mode for machine processing in future?
- Should follow mode also watch `opencode-server.log`? (Out of scope now but worth noting.)
