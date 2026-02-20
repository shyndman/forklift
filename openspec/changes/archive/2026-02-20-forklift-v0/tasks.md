## 1. Host Orchestrator
> Verification: every implementation task is followed by a matching verification task (suffix `.a`). If verification cannot be automated, mark the checkbox label with `HUMAN_REQUIRED` to signal manual review.


- [x] 1.1 Rename the package entrypoint from `forklife` to `forklift` and update `pyproject.toml` script wiring
- [x] 1.1a Capture `git diff pyproject.toml src/forklift/__init__.py` (or entry module) showing the console script now points to `forklift` (see `artifacts/1.1a-diff.txt`)
- [x] 1.2 Implement Git remote discovery (`origin`, `upstream`) and fetch logic in the host CLI
- [x] 1.2a Attach CLI log output from a sample repo showing detected remotes and successful fetches (see `artifacts/1.2a-cli-log.txt`)
- [x] 1.3 Create run directory scaffolding (`$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>/workspace` + `harness-state`, defaulting to `~/.local/state/forklift/runs/<project>_<timestamp>/workspace`) and duplicate repo with remotes removed
- [x] 1.3a Provide a `tree`/`ls` snapshot plus `git remote -v` from the workspace proving remotes are absent (see `artifacts/1.3a-run-snapshot.txt`)
- [x] 1.4 Align workspace/harness-state ownership or permissions so container UID/GID 1000 can write (e.g., `chown -R 1000:1000`)
- [x] 1.4a Attach `ls -ln` output showing both directories owned by UID/GID 1000 (see `artifacts/1.4a-ls-ownership.txt`)
- [x] 1.5 Launch the agent container with workspace/harness mounts and enforce the external 8-minute timeout
- [x] 1.5a Provide orchestrator logs (or `docker` output) demonstrating container start and forced stop at 8 minutes (see `artifacts/1.5a-timeout-log.txt`)
- [x] 1.6 After container exit, verify upstream/main inclusion and open a PR via host credentials when integration succeeded
- [x] 1.6a Include command output showing `git merge-base --is-ancestor upstream/main main` succeeded and a PR creation log/link (see `artifacts/1.6a-merge-pr-log.txt`)
- [x] 1.7 Detect STUCK.md (if present), leave it in place, and surface stuck/timeout statuses via CLI exit codes/logging
- [x] 1.7a Attach sample STUCK.md contents plus CLI/log output indicating a "stuck" exit status (see `artifacts/1.7a-stuck-log.txt` and `artifacts/1.7a-stuck-file.txt`)

## 2. Sandbox Container & Harness
- [x] 2.1 Author the Dockerfile for the kitchen-sink image (Ubuntu 24.04 + Git/build-essential/Python/Node/Bun/Rust/jq/rg/fd/tree + agent harness)
- [x] 2.1a Attach `docker build -t forklift/kitchen-sink:latest docker/kitchen-sink` output showing a successful build (see `artifacts/2.1a-docker-build.txt`)
- [x] 2.2 Wire harness startup script to provide default instructions (merge upstream/main, run tests, write STUCK.md when blocked) and pass through FORK.md contents when available
- [x] 2.2a Provide container log excerpts showing the rendered instructions and confirmation that FORK.md contents were loaded (see `artifacts/2.2a-harness-log.txt`)
- [x] 2.3 Ensure container runtime has no Git remotes/credentials and allows outbound downloads only
- [x] 2.3a Attach an interactive session transcript showing `git remote -v` is empty, SSH keys are inaccessible, and an outbound package download succeeds (see `artifacts/2.3a-sandbox-proof.txt`)
- [x] 2.4 Validate toolchain availability inside the container via smoke tests (git, python3, npm, bun, cargo)
- [x] 2.4a Provide the captured version outputs for `git`, `python3`, `npm`, `bun`, and `cargo` (see `artifacts/2.4a-toolchain-versions.txt`)

## 3. Documentation & Ops
- [x] 3.1 Update `README.md` and `forklift-v0-design.md` with the finalized workflow (run directories, STUCK-only comms, no result.json)
- [x] 3.1a Attach diffs or rendered excerpts showing the new documentation text (see `artifacts/3.1a-doc-diff.txt`)
- [x] 3.2 Provide FORK.md guidance and example content
- [x] 3.2a Include the finalized FORK.md template snippet demonstrating how maintainers supply context (see `artifacts/3.2a-forkmd-snippet.txt`)
