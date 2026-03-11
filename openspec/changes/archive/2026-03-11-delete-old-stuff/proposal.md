## Why

Forklift currently keeps every run directory forever, which causes unbounded disk growth on active machines and forces manual cleanup. We now need deterministic host-side retention so stale run artifacts are removed automatically without operator intervention.

## What Changes

- Add a startup cleanup pass that runs on every `forklift` invocation before normal orchestration work.
- Delete run directories under `$XDG_STATE_HOME/forklift/runs` (or `~/.local/state/forklift/runs`) when they are older than 7 days.
- Keep cleanup behavior non-blocking for orchestration: failures to delete individual directories are logged and processing continues.
- Emit structured cleanup logs summarizing which directories were removed and which were skipped/failed.
- Update docs/specs to reflect that run directories are retained for one week instead of indefinitely.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `forklift-orchestrator`: add a retention requirement that startup performs automatic run-directory cleanup for entries older than 7 days.

## Impact

- Affected code: `src/forklift/cli.py`, `src/forklift/run_manager.py` (or a new retention helper module), and related tests.
- Affected docs/specs: `README.md` and `openspec/specs/forklift-orchestrator/spec.md` (via change delta).
- Operational impact: bounded local disk usage for Forklift run artifacts with weekly retention.