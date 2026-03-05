## 1. Startup retention cleanup implementation

- [x] 1.1 Add host-side cleanup logic that scans Forklift runs root and identifies run directories older than 7 days using directory mtime.
- [x] 1.2 Implement recursive deletion of expired run directories with per-directory error handling and structured logging (deleted/failed/skipped summary).
- [x] 1.3 Invoke retention cleanup at the start of `Forklift.run` before run preparation begins.

## 2. Test coverage for cleanup behavior

- [x] 2.1 Add targeted unit tests for age-threshold selection (older-than-7-days deleted, 7-days-or-newer preserved).
- [x] 2.2 Add tests proving cleanup failures are logged and do not abort orchestration startup.
- [x] 2.3 Ensure orchestration flow tests still pass with startup cleanup enabled.

## 3. Documentation updates

- [x] 3.1 Update README run-artifacts section to document automatic one-week retention and startup evaluation behavior.
- [x] 3.2 Update any related operator-facing guidance that currently states run directories are retained indefinitely.
