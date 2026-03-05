# run-state-tracker Specification

## Purpose
TBD - created by archiving change clientlog-viewer. Update Purpose after archive.
## Requirements
### Requirement: Emit run-state metadata
Forklift SHALL write `run-state.json` inside each run directory immediately after preparation with at least `status`, `run_id`, and `prepared_at` (ISO-8601) fields.

#### Scenario: Run prepared
- **WHEN** `RunDirectoryManager` finishes cloning and scaffolding a run
- **THEN** it SHALL write `run-state.json` containing `status:"starting"`, the run identifier, and `prepared_at`.

### Requirement: Update status during container lifecycle
Forklift SHALL update `run-state.json` when the sandbox container transitions to `running` and when it exits, recording `container_started_at`, `finished_at`, `exit_code`, and final status (`completed`, `failed`, `timed_out`).

#### Scenario: Container starts
- **WHEN** `ContainerRunner` successfully launches the container
- **THEN** it SHALL update `run-state.json` with `status:"running"` and `container_started_at` reflecting container start time.

#### Scenario: Container exits successfully
- **WHEN** the container exits with code 0
- **THEN** Forklift SHALL write `status:"completed"`, `exit_code:0`, and `finished_at`.

#### Scenario: Container exits with failure or timeout
- **WHEN** the container exits non-zero or times out
- **THEN** Forklift SHALL write `status:"failed"` (or `"timed_out"`), include `exit_code`, and `finished_at`.

### Requirement: Atomic writes and readability
Updates to `run-state.json` SHALL use strict atomic replacement (write a temp file in the same directory, `fsync` it, `rename`/`replace` into place, then `fsync` the parent directory) so readers never parse partial JSON, and the file SHALL remain valid UTF-8 JSON throughout the run.

#### Scenario: Concurrent reader
- **WHEN** clientlog reads `run-state.json` while the container updates it
- **THEN** the file SHALL still parse successfully (no truncated content) because writes are atomic.

