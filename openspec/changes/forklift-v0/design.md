## Context

The repository currently contains only a placeholder `forklife` CLI that prints "Hello". The goal for v0 is the radically simple workflow already captured in `forklift-v0-design.md` and reiterated in the proposal: the user runs `forklift` from a fork checkout, the host orchestrator fetches `origin`/`upstream`, duplicates the repo into `$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>/workspace` (defaults to `~/.local/state/forklift/runs/<project>_<timestamp>/workspace`), and launches an AI agent inside a prebuilt kitchen-sink container for at most eight minutes. The container has no remotes, no credentials, and no push authority; only the host process can verify success and create a PR. Communication is completely filesystem-based (success yields a PR, blockers leave `STUCK.md`). Fork context comes only from Git remotes plus an optional FORK.md, and scope is restricted to `main` → `main` merges for single repositories.

## Goals / Non-Goals

**Goals:**
- Provide a deterministic host orchestrator (`forklift.py`) that can run non-interactively (cron) yet leaves behind all artifacts needed for inspection.
- Guarantee isolation by copying the repo, stripping remotes, and running the agent inside a kitchen-sink container with no credentials.
- Enforce a hard eight-minute execution budget per attempt, with external enforcement so the agent cannot overrun.
- Keep configuration-free invocation: discover remotes, derive workspace/run names automatically, rely on FORK.md only when present.
- Produce verifiable outputs: upstream commits reachable from `main`, recorded timestamps, STUCK.md for blockers, and PRs created only when verification passes.

**Non-Goals:**
- Multi-branch synchronization, multi-repo orchestration, or configurable schedules beyond cron on the host.
- Notifications, response processing, retries, or human-in-the-loop tooling beyond checking filesystem outputs.
- Templates or structure for STUCK.md; we intentionally let the agent express blockers freely in v0.
- Any success metric beyond reachability of upstream commits (test execution is best-effort, not a gate yet).
- Container customization or per-project toolchains.

## Decisions

1. **Host-Orchestrated Workflow**
   - Steps: fetch `origin` and `upstream`, create `$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>` (defaults to `~/.local/state/forklift/runs/<project>_<timestamp>`), duplicate repo into `workspace`, remove remotes, start container with `/workspace` and `/harness-state` bind-mounted from the run directory, wait up to eight minutes, kill container if still running, verify upstream inclusion, and open a PR if needed.
   - Rationale: Keeps authority (network creds, PR creation) outside the agent, matching the security constraints.
   - Alternatives: letting the agent retain remotes and push directly, or running entirely inside the container. Rejected because it violates the trust boundary and complicates auditing.

2. **Timestamped Run Directories (`project_YYYYMMDD_HHMMSS`)**
   - Each invocation creates a new run folder containing `workspace/`, `harness-state`, and optional `STUCK.md`.
   - Rationale: Immutable history, easy manual inspection, no concurrency risk.
   - Alternative: single reusable workspace. Rejected due to contamination risk and difficult auditing.

3. **Repo Duplication with Remote Removal**
   - Use `git clone --shared` or copy-on-write to duplicate, then delete all remotes inside the workspace clone.
   - Rationale: Ensures the container cannot accidentally push and that upstream/origin refs are controlled solely by host fetches.
   - Alternative: mount user repo directly read-only and expose remotes. Rejected for safety.

4. **Kitchen-Sink Container Environment**
   - Docker context lives in `docker/kitchen-sink/` producing the `forklift/kitchen-sink:latest` image built FROM `ubuntu:24.04`. The Dockerfile installs system packages (`git`, `build-essential`, `cmake`, `pkg-config`, `python3`, `python3-venv`, `python3-pip`, `curl`, `wget`, `unzip`, `ca-certificates`, `openssl`, `libssl-dev`), language runtimes (Rust via rustup, Node.js via `n`, Bun installer, PyEnv), and CLI tooling (jq, ripgrep, fd, tree, make, bash-completion). It creates a non-root `forklift` user (UID/GID 1000) that owns `/workspace` inside the container.
   - Harness bits land in `/opt/forklift/harness` with entrypoint `/opt/forklift/harness/run.sh`. At runtime only two bind mounts exist: `/workspace` (copied repo) and `/harness-state` (agent logs/state), both read-write. The host must ensure those directories are writable by UID/GID 1000 (e.g., via `chown -R 1000:1000`). Outbound internet is allowed for dependency downloads; no credentials or Git remotes are present inside the container.
   - Rationale: eliminates per-project setup logic; agent infers needed tools from repo files.
   - Alternative: tailor container per repo. Rejected to keep v0 simple.

5. **Agent Instructions + FORK.md Context**
   - Default instructions: merge `upstream/main` into `main`, run any discoverable tests, create meaningful commits, write `STUCK.md` describing problem/attempts/outcome if blocked. Optional `FORK.md` gives fork-specific constraints.
   - Rationale: Minimal trust-first guidance aligned with philosophy.
   - Alternative: scriptable instruction templates or STUCK.md schema. Deferred until we observe real agent behavior.

6. **Success Verification Rule**
   - Host runs `git merge-base --is-ancestor upstream/main main` (or equivalent) within workspace to ensure all upstream commits are reachable from `main`. Only then does it open a PR from the agent branch to origin/main.
   - Rationale: Cheap, deterministic gate, matches "start with reachability" agreement.
   - Alternative: require full test pass or additional heuristics. Deferred.

7. **STUCK Communication Semantics**
   - When the agent cannot proceed it writes `STUCK.md` describing the problem, attempts, and outcome. The host leaves this file in place for the maintainer and does not generate any additional summary artifacts.
   - The host CLI inspects the workspace immediately after the container exits. If `STUCK.md` exists it logs a short preview, leaves the file untouched, and exits with a dedicated non-zero status so cron jobs/operators notice the "stuck" outcome.

   - Rationale: Keeps v0 maximally simple and lets the agent speak directly without an intermediate schema.
   - Alternative: host-authored summary files or structured logs. Rejected per the directive to avoid extra artifacts.

8. **External Timeout Enforcement**
   - Host process manages a watchdog that stops the container after eight minutes regardless of agent state.
   - Rationale: Prevents runaway compute spend and aligns with "hard time limits" philosophy.
   - Alternative: rely on agent to self-limit. Rejected.

## Risks / Trade-offs

- **Hard 8-minute cutoff may terminate legitimate long merges/tests** → Mitigation: start with small repos, document timeout constant, allow manual reruns with adjusted limit if needed.
- **Success defined solely by reachability may allow silent regressions** → Mitigation: rely on agent-run tests when time permits, monitor failures, plan to add stronger gates in future versions.
- **STUCK.md freeform output might be low-signal** → Mitigation: observe initial runs, provide human guidance/examples later if needed.
- **Kitchen-sink container is large and may slow startup** → Mitigation: build once, reuse image, accept overhead for simplicity.
- **Trust boundary assumes repo contents are non-sensitive** → Mitigation: document requirement clearly; owners must not run forklift on repos containing secrets.

## Migration Plan

1. Rename the package/entrypoint from `forklife` to `forklift` in `pyproject.toml` and CLI wiring.
2. Implement the host orchestrator script and supporting modules (Git operations, workspace prep, timeout management, PR creation via local GitHub CLI or API token kept outside container).
3. Add Dockerfile and build pipeline for the kitchen-sink container plus harness configuration.
4. Implement STUCK detection and verification logic; ensure PR creation uses host credentials only.
5. Document usage (`README`, `forklift-v0-design.md`, FORK.md guidance) and provide cron example.
6. Test locally on a sample fork, iterate on timeout and logging.
7. Rollback: stop cron jobs, delete `$XDG_STATE_HOME/forklift/runs/*` (or `~/.local/state/forklift/runs/*`), revert CLI rename if necessary.

## Open Questions
1. Should we surface additional context (e.g., failing test names) somewhere beyond STUCK.md to aid triage?

   - **Answer:** There is no host-authored result file; STUCK.md remains the only rich context channel in v0.
2. When (if ever) should success criteria expand beyond reachability—e.g., require passing tests, diff size limits, or files-of-interest checks?
   - **Answer:** Deferred. We will gather operational data first and revisit after v0 runs in the wild.
3. Do we need guardrails for concurrent manual + cron runs targeting the same fork, or is "one run directory per invocation" enough?
   - **Answer:** One run directory per invocation is sufficient; no additional locking needed in v0.
