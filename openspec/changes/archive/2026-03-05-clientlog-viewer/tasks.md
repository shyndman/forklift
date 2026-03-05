## 1. Run-state metadata

- [x] 1.1 Update `RunDirectoryManager` to create `run-state.json` with `status:"starting"`, `run_id`, and `prepared_at` when preparing a run.
- [x] 1.2 Extend `ContainerRunner` to update `run-state.json` transitions (`running`, `completed`, `failed`, `timed_out`) with `container_started_at`, `finished_at`, and `exit_code`.
- [x] 1.3 Ensure run-state updates use strict atomic writes (same-directory temp file, file `fsync`, `rename`/`replace`, directory `fsync`) so readers never observe partial JSON.

## 2. Clientlog command scaffolding

- [x] 2.1 Register `Clientlog` subcommand in `Forklift` CLI with `run_id` and a single follow flag (`-f`/`--follow`).
- [x] 2.2 Resolve run paths (respecting `$XDG_STATE_HOME`), validate required files, and render a formatted one-shot transcript snapshot by default.
- [x] 2.3 When `-f`/`--follow` is set, render existing history first and then stream appended events.
- [x] 2.4 Treat `run-state.json` as required input: if missing or unreadable, exit non-zero with a clear error and do not render transcript output.

## 3. Log parsing & rendering

- [x] 3.1 Implement incremental parsing for mixed ISO lines and JSON events, computing relative timestamps from session start.
- [x] 3.2 Group events into step blocks by `messageID` (with `part.id` kept as per-event metadata inside each block) and render tool calls/results, agent messages, and agent thoughts in Rosé Pine-styled boxed blocks.
- [x] 3.3 Render incomplete steps as pending blocks in default one-shot output when `step_finish` is absent.
- [x] 3.4 Ensure incomplete/unknown events are still emitted with all available fields/content (raw fallback allowed), including complete captured tool stdout/stderr, so formatting never omits diagnostic information.

## 4. Viewing modes & signal handling

- [x] 4.1 Implement follow loop for `-f`/`--follow` mode without changing default one-shot output behavior.
- [x] 4.2 In follow mode, surface unfinished steps immediately as pending and stream subsequent events for the same step in order (do not wait for `step_finish`; do not duplicate already streamed events).
- [x] 4.3 Trap `SIGINT`/`SIGTERM` to stop follow mode cleanly, close resources, and exit with a clear interruption message.