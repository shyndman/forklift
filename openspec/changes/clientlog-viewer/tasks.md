## 1. Run-state metadata

- [ ] 1.1 Update `RunDirectoryManager` to create `run-state.json` with `status:"starting"`, `run_id`, and `prepared_at` when preparing a run.
- [ ] 1.2 Extend `ContainerRunner` to update `run-state.json` transitions (`running`, `completed`, `failed`, `timed_out`) with `container_started_at`, `finished_at`, and `exit_code`.
- [ ] 1.3 Ensure run-state updates are atomic so readers never observe partial JSON.

## 2. Clientlog command scaffolding

- [ ] 2.1 Register `Clientlog` subcommand in `Forklift` CLI with `run_id` and flags (`--follow`, `--once`, `--no-pager`, `--tool-lines`, `--since`).
- [ ] 2.2 Resolve run paths (respecting `$XDG_STATE_HOME`), validate required files, and auto-select follow or pager mode from run-state metadata.

## 3. Log parsing & rendering

- [ ] 3.1 Implement incremental parsing for mixed ISO lines and JSON events, computing relative timestamps from session start.
- [ ] 3.2 Group events by step and render tool calls/results, agent messages, and agent thoughts in Rosé Pine-styled boxed blocks.
- [ ] 3.3 Render incomplete steps as pending blocks in `--once` mode when `step_finish` is absent.

## 4. Viewing modes & signal handling

- [ ] 4.1 Implement pager output for completed runs (default `less -R`) and follow loop for active runs with `--follow`, `--once`, and `--since` overrides.
- [ ] 4.2 Trap `SIGINT`/`SIGTERM` to stop follow mode cleanly, close resources, and exit with a clear interruption message.