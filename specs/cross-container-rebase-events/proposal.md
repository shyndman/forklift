## Why

Forklift currently knows when a run starts and when it ends, but it does not surface live rebase position while the harness is working through upstream commits. When a rebase pauses on a conflict, the operator can eventually inspect artifacts and logs, but the top-level `forklift` output still lacks the most important situational context: which commit is blocked and where that commit sits in the overall sequence. This change adds a dedicated structured event channel from the container back to the host so Forklift can report visually front-loaded `N/Total` progress and explicit conflict events as they happen. The timing is right because the harness already owns the paused-rebase seam, so it now has a natural place to emit authoritative progress updates.

## What Changes

- Add a new cross-container structured rebase event channel backed by a host-created Unix domain socket bind-mounted into the container.
- Have the harness emit newline-delimited JSON rebase events for progress, conflict, continue, skip, completion, and abort/stuck transitions using Python rather than stdout parsing.
- Surface live top-level host logs with visually front-loaded ordinals such as `Rebase 5/31` or `Conflict 5/31` while preserving structured `step` and `total` fields for downstream processing.
- Teach the host container runner to create, own, read, and tear down the control socket independently of container stdout/stderr collection.
- Extend the harness rebase helpers to derive `step`, `total`, current commit SHA, current commit subject, and conflicted file names from the active Git rebase state whenever the rebase pauses or advances.
- Preserve existing `opencode-client.log`, top-level container stdout/stderr, and `harness-status.txt` behavior so the new channel adds live structured visibility without replacing existing artifacts.

## Scope

### New Capabilities
- `cross-container-rebase-events`: Dedicated structured rebase progress/event transport from the harness container to the host orchestrator over a bind-mounted Unix domain socket.

### Modified Capabilities
- `forklift-orchestrator`: Container launch now provisions a control mount and live event reader so host logs can report in-flight rebase progress and conflict state before the container exits.
- `agent-sandbox-run`: The harness now emits authoritative paused-rebase progress and conflict metadata over the control socket while preserving existing transcript and status artifacts.

## Impact

- Affected host code: `src/forklift/container_runner.py`, `src/forklift/cli.py`, run-directory preparation, and any helper types/modules that describe run paths or event payloads.
- Affected harness code: `docker/kitchen-sink/harness/run.sh` and `docker/kitchen-sink/harness/includes/rebase.sh`.
- Affected container runtime behavior: `docker run` gains one additional bind mount for a host-owned control directory and one additional env var advertising the socket path.
- Affected tests: container runner lifecycle tests, CLI runtime/logging tests, and harness rebase tests need new coverage for event emission, malformed payload handling, and live conflict/progress logging.
- No new runtime dependency is required because the harness can use the Python already present in the kitchen-sink image to write to the Unix domain socket.
