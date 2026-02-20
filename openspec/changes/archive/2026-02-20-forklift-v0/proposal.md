## Why

Managing forks manually wastes time and results in stale branches. Forklift v0 delivers a minimal automation loop that attempts a daily upstream merge, surfaces blockers via STUCK.md, and relies on hard constraints (8-minute runs, main-only, host-controlled pushes) to stay safe while we learn what further complexity is warranted.

## What Changes

- Build a Python orchestrator (`forklift.py`) that fetches origin/upstream, snapshots the repo into a run directory named `<project>_<YYYYMMDD_HHMMSS>`, strips remotes inside the workspace copy, and drives the agent container with an external 8-minute timeout.
  Forklift's top-level CLI class will extend `clypi.Command`, expose arguments via type annotations (per docs: annotated fields become options like `--name`), and route subcommands through a `subcommand` attribute if we later add modes.

- Implement the `forklift` CLI using Clypi 1.8.2's `Command` base class (`Command.parse()` + `Command.start()`) so orchestration logic can remain asynchronous and structured per the official docs.

- Ship a kitchen-sink Docker image plus harness configuration so the agent can merge, test, and report without any network credentials or push authority.
  The command name (what users type) defaults to the class name per Clypi's `Command.prog()` behavior; we'll either name the class `Forklift` or override `prog()` explicitly to ensure the binary is `forklift` (docs: https://github.com/danimelchor/clypi/blob/master/docs/api/cli.md#progcls).

- Define filesystem-based state for every run (`runs/<project>_<timestamp>/workspace`, `harness-state`, `STUCK.md`) and host-side verification that upstream commits are reachable from `main` before opening a PR.
- Document the minimal agent instructions (merge upstream/main into main, run tests, explain blockers in STUCK.md) and optional FORK.md context file that owners can author.
- Provide operator tooling notes (cron invocation, adjustable timeout constant) while keeping v0 scope limited to main-only forks and a single repo per invocation.

## Capabilities

### New Capabilities
- `forklift-orchestrator`: Host workflow that prepares workspaces, enforces the 8-minute window, verifies merge success, and opens PRs when upstream commits are integrated.
- `agent-sandbox-run`: Containerized execution environment plus agent instructions governing what the AI can do (merge/test/report) and how it communicates via filesystem outputs (STUCK.md plus commits/PRs).

### Modified Capabilities
- _None_

## Impact

- New orchestrator code under `forklift.py` and supporting modules/config.
- Documentation updates (`README.md`, `forklift-v0-design.md`, FORK.md guidance) describing usage, assumptions, and STUCK expectations.
- Addition of Docker build assets and harness configuration for the sandbox image.
- Cron/example scripts demonstrating daily execution.
- Project configuration (`pyproject.toml`, packaging) updated to expose the `forklift` entrypoint instead of the placeholder `forklife` scaffold.

## Dependencies

- `clypi` 1.8.2 — latest release on PyPI as of 2026-02-15 (verified via `python -m pip index versions clypi`)

- `pydantic` 2.12.5 — latest release on PyPI as of 2026-02-15 (verified via `python -m pip index versions pydantic`)
- `pydantic-ai` 1.59.0 — latest release on PyPI as of 2026-02-15 (verified via `python -m pip index versions pydantic-ai`)

