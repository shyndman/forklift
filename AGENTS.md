# Repository Guidelines

## Project Overview
- Forklift is a host-side orchestrator that keeps a fork in sync with its upstream by snapshotting the repo into `~/forklift/runs/<project>_<timestamp>`, launching the kitchen-sink container for at most eight minutes, and either guiding a pull request or surfacing a `STUCK.md` with remediation details (see `README.md`).
- The workflow is intentionally filesystem-driven: a shared-clone workspace (no remotes) and a writable `harness-state/` directory are bind-mounted into the sandbox so the harness and agent communicate via files instead of services (documented in `forklift-v0-design.md`).

## Architecture & Data Flow
- Entry point: `Forklift` (`src/forklift/cli.py`) resolves the repo, configures logging, ensures `origin`/`upstream` using `git.py`, fetches both remotes, and orchestrates the run lifecycle.
- Run preparation: `RunDirectoryManager` (`run_manager.py`) clones the repo via `git clone --shared`, copies `FORK.md`, strips remotes, aligns ownership to UID/GID `1000`, captures `main_branch` and `upstream/main` SHA, and emits `RunPaths` (run directory, workspace, harness-state) plus a `metadata.json` manifest.
- Sandbox execution: `ContainerRunner` (`container_runner.py`) builds `docker run --rm --name ... -v workspace:/workspace -v harness-state:/harness-state` with overrides from `FORKLIFT_DOCKER_IMAGE`, `FORKLIFT_TIMEOUT_SECONDS` (default 480s), `FORKLIFT_DOCKER_ARGS`, and `FORKLIFT_DOCKER_COMMAND`, then captures stdout/stderr and timeouts.
- Post-run verification: CLI reloads `metadata.json`, previews `workspace/STUCK.md` (exits with code 4), runs `git merge-base --is-ancestor upstream/main <target_branch>` before logging PR instructions, and exits non-zero for timeout (2) or container failures (container exit code); otherwise it logs that no changes were detected.

## Key Directories
- `src/forklift/` – Python package housing the CLI (`cli.py`), Git helpers (`git.py`), run directory preparation (`run_manager.py`), container launcher (`container_runner.py`), and package entry (`__init__.py`).
- `docker/kitchen-sink/` – Dockerfile plus harness script that define the Ubuntu 24.04 sandbox with Git/build-essential, Node via `n`, Bun, Rust, jq, ripgrep, fd, tree, and `/opt/forklift/harness/run.sh`.
- `openspec/changes/forklift-v0/` – Proposal, specs, and artifacts documenting host-orchestrator and sandbox requirements, docker build logs, and harness transcripts.
- `FORK.md` – Template at repo root copied into every workspace so agents know mission themes, tests to run, risky areas, and contacts.
- `~/forklift/runs/<project>_<YYYYMMDD_HHMMSS>/` (created at runtime) – Contains `workspace/`, `harness-state/`, and `metadata.json`; inspect when debugging runs.

## Development Commands
- Bootstrap + run orchestration:
  - `uv run forklift --verbose` – primary command to kick off an orchestration run from the host.
  - `docker build -t forklift/kitchen-sink:latest docker/kitchen-sink` – rebuild the sandbox image after Dockerfile changes.
- Tooling:
  - `uv run basedpyright` – run static type checks (from README).
  - Set overrides before running: `export FORKLIFT_DOCKER_IMAGE=...`, `FORKLIFT_TIMEOUT_SECONDS=600`, `FORKLIFT_DOCKER_COMMAND="bash -lc '...''"` when you need non-default images, watchdogs, or entry commands.
- Design docs outline future host utilities (e.g., `forklift run <fork> --interactive`, `forklift scheduler start`, `forklift logs --transcript <id>`, `forklift test-notify --mock-conflict`)—treat them as reference commands when expanding the CLI surface.

## Code Conventions & Common Patterns
- Logging: standard `logging` module with format `"%(asctime)s [%(levelname)s] %(message)s"`; verbose flag raises level to DEBUG. All git/container operations log both intent and captured stdout/stderr.
- Error handling: Git failures raise `GitError` that the CLI converts into `SystemExit(1)`; container timeouts raise exit code 2; upstream verification failures exit 3; `STUCK.md` presence exits 4 with the first 40 lines logged.
- Data carriers: use dataclasses (`RunPaths`, `ContainerRunResult`, `GitRemote`, `GitFetchResult`) for structured data; prefer `Path` throughout for filesystem interactions.
- Workspace hygiene: every run strips remotes, aligns ownership recursively to UID/GID `1000`, and keeps metadata in JSON for later verification—ensure any new host logic preserves these invariants.
- Async pattern: `Forklift.run` is `async` (required by `clypi.Command`). Keep long-running calls (git subprocesses, docker invocation) in blocking helpers so the CLI remains straightforward.

## Important Files
- `src/forklift/cli.py` – Top-level orchestration logic plus logging configuration, remote discovery, fetch loop, post-run verification.
- `src/forklift/run_manager.py` – Run directory lifecycle, metadata writing, `FORK.md` overlay, ownership adjustments.
- `src/forklift/container_runner.py` – Docker invocation, timeout enforcement, container naming.
- `src/forklift/git.py` – Single place for git subprocess execution and error handling.
- `docker/kitchen-sink/Dockerfile` & `docker/kitchen-sink/harness/run.sh` – Define the sandbox image/toolchain and how instructions/FORK context are surfaced to the agent.
- `README.md`, `forklift-design.md`, `forklift-v0-design.md` – Authoritative docs on purpose, workflows, and scoped v0 behavior.
- `openspec/changes/forklift-v0/specs/*.md` – Spec contracts for orchestrator and sandbox; reference when validating behavior or extending features.

## Runtime/Tooling Preferences
- Python: requires Python 3.13+ (`.python-version` pins 3.13) and uses `uv` for dependency management/build backend (`uv_build`). Entry point script is `forklift = "forklift:main"` defined in `pyproject.toml`.
- Dependencies: `clypi`, `pydantic`, and `pydantic-ai` are the core runtime libraries; install/update with `uv add` to keep `pyproject.toml` and `uv.lock` aligned.
- Sandbox: default Docker image `forklift/kitchen-sink:latest` (Ubuntu 24.04) ships Git, build-essential, Python toolchain, Node via `n`, Bun, Rust via rustup, jq, ripgrep, fd, and tree. Harness exposes `/workspace` and `/harness-state` only.
- Environment overrides: `FORKLIFT_DOCKER_IMAGE`, `FORKLIFT_DOCKER_COMMAND`, `FORKLIFT_TIMEOUT_SECONDS`, `FORKLIFT_DOCKER_ARGS`, and `DOCKER_BIN` adjust container behavior without code changes.

## Testing & QA
- The repository does not include a native `tests/` tree; testing relies on the fork’s own suites described in `FORK.md` and echoed by the harness (`run.sh`). Document commands like `uv run pytest` or `npm test` in `FORK.md` so agents know what to execute.
- Harness guidance (see `docker/kitchen-sink/harness/run.sh`) instructs agents to merge `upstream/main`, run the project’s primary tests when time allows, and write `STUCK.md` with actionable details when blocked.
- Always inspect `~/forklift/runs/.../harness-state/instructions.txt` and any generated `STUCK.md` after a run; they are the canonical QA artifacts.
- Static analysis (`uv run basedpyright`) is the only built-in check today; add further QA steps to `FORK.md` or integrate new host-side commands if broader coverage is needed.

## Dependency Management
- Always add or upgrade Python dependencies with `uv add <package>` so `pyproject.toml` and `uv.lock` stay in sync.
- Never edit dependency entries by hand; rerun `uv add` instead to capture hashes.
