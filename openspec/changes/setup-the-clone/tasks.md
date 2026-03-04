## 1. Harness bootstrap parsing and execution

- [x] 1.1 Add strict `FORK.md` front matter parsing in `docker/kitchen-sink/harness/run.sh` that only recognizes front matter when `---` is on line 1 and fails closed on malformed front matter.
- [x] 1.2 Add optional `setup` extraction (string or multiline block string) and execute it via `bash -lc` in `/workspace` with a 180-second timeout.
- [x] 1.3 Write setup stdout/stderr to `/harness-state/setup.log` and ensure setup failures/timeouts exit non-zero before agent launch.
- [x] 1.4 Add post-setup git cleanliness validation (tracked-file dirtiness check) and fail closed if setup mutates tracked files.

## 2. Agent context and launch flow updates

- [x] 2.1 Strip front matter from `FORK.md` before writing `/harness-state/fork-context.md`, before appending to `/harness-state/instructions.txt`, and before constructing OpenCode payload.
- [x] 2.2 Preserve existing deterministic OpenCode launch command/flags and ensure launch happens only after setup gate passes.
- [x] 2.3 Keep `STUCK.md` handling unchanged for agent-authored blocked work (do not repurpose it for setup/infrastructure failures).

## 3. Validation, regression tests, and docs

- [x] 3.1 Add harness-focused automated tests covering: no front matter, valid setup success, malformed front matter failure, setup non-zero failure, setup timeout failure, and dirty-worktree failure.
- [x] 3.2 Add assertions that front matter content is absent from agent-visible context artifacts while body content is preserved.
- [x] 3.3 Update `README.md` and `FORK.md` template guidance to document optional `setup`, strict front matter format, timeout, fail-closed behavior, and `/harness-state/setup.log` diagnostics.
- [x] 3.4 Run relevant test/type-check commands for modified areas and verify new/updated tests pass.
