## Why

`src/forklift/cli.py` and `src/forklift/clientlog.py` have grown large enough that routine changes require too much context loading and increase regression risk. We should split them now while behavior is fresh in tests, so future changes stay local and easier to review.

## What Changes

- Split `Forklift` command logic into focused modules (bootstrap/orchestration, post-run processing, authorship rewrite, and ownership helpers) while preserving existing CLI behavior and exit codes.
- Split `clientlog` into focused modules (parser, renderer, and command/follow loop) while preserving transcript rendering and follow semantics.
- Keep compatibility imports so existing entrypoints and test imports (`forklift.cli`, `forklift.clientlog`) continue to work.
- Remove dead helper paths discovered during extraction only when they are provably unused and covered by existing tests.
- Add or adjust focused tests around extracted seams to lock behavior during the refactor.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `forklift-orchestrator`: internal module structure requirements for command orchestration and transcript tooling are updated to require separated concerns without changing observable CLI behavior.

## Impact

- Affected code: `src/forklift/cli.py`, `src/forklift/clientlog.py`, and new helper modules under `src/forklift/`.
- Affected tests: `tests/test_cli_post_run.py`, `tests/test_clientlog.py`, plus any new focused tests for extracted units.
- Affected operator behavior: none expected; command names, flags, and output semantics remain stable.
