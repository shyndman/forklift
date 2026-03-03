## Why

Forklift currently always targets the upstream branch tip, which is a policy choice that does not fit forks that intentionally advance only by released versions. We need an explicit mode that rebases onto the latest upstream version tag and exits early when there is nothing new to integrate.

## What Changes

- Add a CLI policy option `--target-policy=<tip|latest-version>` (default `tip`) that controls whether upstream target selection uses branch tip or the latest version tag.
- Resolve version tags using semantic version ordering and support stable tags in `vX.Y.Z` and `X.Y.Z` forms (pre-release/build metadata are out of scope for this change).
- Treat ambiguous equivalent tags (for example `v13.5.2` and `13.5.2` pointing to different commits) as a fatal error.
- Add a pre-container no-op check: if the selected upstream target is already reachable from the configured main branch, exit successfully without creating a run.
- Record and log the selected target policy and resolved tag/SHA for auditability.
- Add targeted test coverage that combines fast unit tests (policy wiring/error paths) with git-backed integration tests in temporary repositories (tag resolution and merge-base no-op behavior).

## Capabilities

### New Capabilities
- `upstream-target-policy`: Policy-based upstream target resolution, including latest-version selection and no-op early exit.

### Modified Capabilities
- `forklift-orchestrator`: Upstream target selection and pre-run gating requirements now support both tip-based and latest-version-tag-based operation.

## Impact

- `src/forklift/cli.py` (`Forklift.run`) currently performs: remote discovery/fetch → `RunDirectoryManager.prepare(...)` → container run → `post_container_results(...)`. The new policy selection and pre-run no-op gate will be inserted into this orchestration path.
- `src/forklift/cli_runtime.py` currently contains argument validation helpers (`resolved_main_branch`, `validated_override`) and is the established location/pattern for fail-fast CLI validation.
- `src/forklift/run_manager.py` currently captures `main_branch`, `upstream_main_sha`, and `origin_main_sha` in metadata, adds `run_id`, and seeds `refs/remotes/upstream/<main_branch>` (plus helper branch `upstream-<main_branch>`) from `upstream_main_sha`.
- `src/forklift/cli_post_run.py` currently derives `upstream_ref` as `upstream/<main_branch>` and verifies ancestry via `ensure_upstream_merged(...)`; metadata `upstream_main_sha` is used only for logging context.
- `src/forklift/cli_authorship.py` currently resolves rewrite anchors from `upstream_ref` (`rev-parse upstream/<branch>`) and already short-circuits rewrite/local publication when `HEAD` equals that upstream anchor.
- `src/forklift/git.py` currently exposes generic git primitives (`run_git`, `fetch_remotes`, `ensure_upstream_merged`); `fetch_remotes` runs `git fetch <remote> --prune` and there is no existing helper for version-tag discovery/selection.
- `docker/kitchen-sink/harness/run.sh` currently derives `UPSTREAM_REF="upstream/${MAIN_BRANCH}"`, logs seeded SHA info, and instructs the agent to run `git rebase upstream/<branch>` against the seeded synthetic upstream ref.
- Existing test modules (`tests/test_cli_runtime.py`, `tests/test_cli_post_run.py`) use `unittest` with patched git calls; this change adds both mocked unit coverage and new real-git temp-repository integration coverage.
- Docs/spec touch points remain `README.md` and `openspec/specs/*`.
