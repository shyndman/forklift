## Context

Forklift writes one run directory per invocation under `$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>` (or `~/.local/state/forklift/runs/...`) and currently never reclaims old runs. On active repositories this grows quickly because each run contains a full workspace clone plus harness/opencode logs. The requested behavior is deterministic retention: every startup should prune run directories older than one week.

## Goals / Non-Goals

**Goals:**
- Enforce automatic run-directory cleanup on every `forklift` invocation.
- Delete only directories older than 7 days.
- Keep cleanup failures non-fatal so orchestration still runs.
- Provide structured logs that make deletions and failures auditable.

**Non-Goals:**
- Adding user-configurable retention windows in this change.
- Deleting non-run data outside the configured Forklift runs root.
- Changing run artifact contents, run-state schema, or post-run behavior.

## Decisions

1. **Cleanup trigger: run once at startup before run preparation**
   - Decision: execute cleanup near the beginning of `Forklift.run`, before upstream target checks and before any new run directory is created.
   - Rationale: guarantees consistent enforcement and avoids accidental deletion of the current invocation’s artifacts.
   - Alternatives considered:
     - Cleanup at process exit: misses failed/crashed invocations and delays reclamation.
     - Background cleanup job: more moving parts, unnecessary for current scope.

2. **Expiration policy: fixed age threshold of 7 days**
   - Decision: treat a run directory as expired when its directory mtime is strictly older than `now - 7 days`.
   - Rationale: aligns with requested policy and keeps implementation straightforward.
   - Alternatives considered:
     - Use `metadata.json.created_at`: requires parsing files for every candidate and handling malformed metadata.
     - Keep latest N runs: does not map to the explicit time-based requirement.

3. **Deletion method: recursive removal with per-directory error isolation**
   - Decision: delete expired directories via recursive filesystem removal; catch and log errors per directory and continue.
   - Rationale: one bad directory should not block all cleanup or orchestration.
   - Alternatives considered:
     - Fail-fast on first deletion error: safer for strict modes but violates desired non-blocking behavior.
     - Best-effort file-by-file deletion: unnecessary complexity versus existing recursive utilities.

4. **Observability: structured summary + per-failure details**
   - Decision: emit counts for scanned/deleted/failed/skipped directories and include directory paths in failure logs.
   - Rationale: retention changes should remain auditable without reading filesystem state manually.
   - Alternatives considered:
     - Silent cleanup: hard to diagnose retention issues.
     - Verbose per-directory success logs: noisy for normal operation.

## Risks / Trade-offs

- **[Risk] Unexpected timestamp drift or touched mtimes can preserve old directories longer than intended** → Mitigation: document mtime-based behavior and keep policy simple/transparent.
- **[Risk] Directory deletion races with external processes reading artifacts** → Mitigation: startup-only cleanup and non-fatal deletion errors reduce disruption.
- **[Trade-off] Fixed one-week retention may be too short/long for some operators** → Accepted for now; revisit with configuration support if requested later.
- **[Trade-off] Retention reduces long-term forensic history** → Accepted because bounded disk usage is the explicit operational priority.

## Migration Plan

1. Implement startup cleanup helper(s) in host orchestration flow and add targeted unit tests.
2. Update README and orchestrator spec delta to describe one-week retention and startup evaluation.
3. Rollout: no data migration needed; first run after deployment applies policy to existing directories.
4. Rollback: remove cleanup invocation and retention helper; no persistent schema changes to unwind.

## Open Questions

- None for this scope; policy and trigger are explicitly defined (startup, older than 7 days).
