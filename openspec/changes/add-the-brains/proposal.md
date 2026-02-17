## Why
Forklift’s v0 harness launches a kitchen-sink container that prints instructions and exits. No real agent ever runs unless a human injects ad-hoc commands via `FORKLIFT_DOCKER_COMMAND`, so every scheduled run is a guaranteed no-op. Onboarding engineers now have to reverse-engineer how to wire OpenCode themselves, duplicate secrets into the container, and hope they do not leak credentials. This makes automation impossible and creates inconsistent security posture (API keys exposed to unprivileged users, no audit logs). We need a deterministic, documented bridge that boots OpenCode automatically while keeping secrets scoped to trusted processes that junior engineers can wire up without guesswork.

## What Changes
- **Host CLI configuration layer**
  - Introduce an `.opencode_env` loader that reads `~/.config/forklift/opencode.env` (simple `KEY=VALUE` format, comments allowed). Required keys: `OPENCODE_API_KEY`, `OPENCODE_MODEL`, `OPENCODE_VARIANT`, `OPENCODE_AGENT`, `OPENCODE_SERVER_PASSWORD`. Optional keys: `OPENCODE_ORG`, `OPENCODE_TIMEOUT`, `OPENCODE_SERVER_PORT` (default 4096). Loader validates file permissions (0600 recommended) and errors with actionable messages if any key is missing.
  - Add explicit CLI options `--model`, `--variant`, `--agent` that override env defaults. Because the upstream CLI only exposes `--model`/`--agent` flags (per official docs), Forklift will map the variant string into the model value (e.g., combine `provider/model` + `:variant`) before forwarding. Values must match `[A-Za-z0-9._-]+`; otherwise the CLI rejects the run with a clear error so we never forward injection-prone strings.
  - Remove `FORKLIFT_DOCKER_COMMAND`. Container invocation becomes fixed: `docker run ... forklift/kitchen-sink:latest /opt/opencode/entrypoint.sh`. The CLI only forwards sanitized OpenCode env vars via `-e`. This guarantees the harness always executes the same path.
  - Log (at INFO) exactly which env file path was loaded and (at DEBUG) which model/variant/agent values are being forwarded (mask the API key) so junior engineers can troubleshoot misconfigurations.

- **Container image and entrypoint overhaul**
  - Update `docker/kitchen-sink/Dockerfile` to install OpenCode via the official installer (`curl -fsSL https://opencode.ai/install | bash`), pinning `OPENCODE_VERSION=1.2.6` (latest GitHub release) in an `ARG`. Install under `/opt/opencode` with binaries in `/opt/opencode/bin`. Create an `opencode` group and add the `forklift` user to it.
  - Add `/opt/opencode/start_server.sh` (root-owned, 0700) that reads `OPENCODE_API_KEY`/`OPENCODE_ORG`, exports `OPENCODE_SERVER_PASSWORD`, runs `opencode serve --port "$OPENCODE_SERVER_PORT" --hostname 127.0.0.1`, redirects stdout/stderr to `/harness-state/opencode-server.log`, and blocks until the health endpoint at `http://127.0.0.1:$OPENCODE_SERVER_PORT/status` responds 200.
  - Replace the container entrypoint with `/opt/opencode/entrypoint.sh` that:
    1. Invokes `start_server.sh` (root context).
    2. Exports `OPENCODE_MODEL/VARIANT/AGENT/TIMEOUT/SERVER_PORT/SERVER_PASSWORD` for the client.
    3. `su -s /bin/bash -c /opt/forklift/harness/run.sh forklift` once the HTTP server is reachable.
    4. Traps SIGTERM/SIGINT to call `opencode session stop --all` (if available) or send SIGTERM to the `serve` PID, waits for shutdown, and removes `/harness-state/opencode-server.log` temp handles.

- **Harness rewrite**
  - `docker/kitchen-sink/harness/run.sh` gains three phases: (1) write instructions and FORK.md context into `/harness-state`, (2) dump the exact command it will run into `/harness-state/instructions.txt` under an “Agent Command” heading for auditing, (3) execute `/opt/opencode/bin/opencode run --attach "http://127.0.0.1:$OPENCODE_SERVER_PORT" --model "$OPENCODE_MODEL" --agent "$OPENCODE_AGENT" --format default --instructions-file /harness-state/instructions.txt "$(cat /harness-state/fork-context.md 2>/dev/null || printf 'No FORK context provided.')"`. (The `--attach` flag is documented in the official CLI guide and lets us reuse the server process.)
  - Client stdout/stderr are redirected to `/harness-state/opencode-client.log`. Failures (non-zero exit) should echo a concise error to stderr and exit 1 so the host surfaces it.

- **Documentation & ops updates**
  - README gains a section “Configuring OpenCode” detailing the `.opencode.env` format with a copy/paste template, instructions for rebuilding the Docker image, and a log-inspection guide referencing `/harness-state/opencode-{server,client}.log`.
  - `forklift-v0-design.md` is updated to describe the split server/client architecture, trust boundaries, and why Unix sockets are used.
  - FORK.md guidance reminds maintainers that the file content is fed as the sole positional argument to OpenCode, so it should stay concise and high-signal.
  - Provide a troubleshooting checklist (missing env keys, permissions, socket not created, OpenCode install failures) in docs so junior engineers know where to look.

## Capabilities

### New Capabilities
- `opencode-agent-bridge`: Defines the complete lifecycle of the OpenCode integration (env file parsing, secret scoping, server startup, socket permissions, client invocation, log capture, shutdown). Implementation must ensure secrets never reach `/workspace`, logs are always written to `/harness-state`, and the harness cannot be bypassed.

### Modified Capabilities
- `agent-sandbox-run`: Requirement updates so the harness always launches the bundled OpenCode client (no more operator-supplied shell), enforces socket-level access control, and records deterministic logs. Also captures how STUCK.md previewing ties into the new client logs.
- `forklift-orchestrator`: Requirement updates covering the new env loader, sanitized CLI overrides, removal of `FORKLIFT_DOCKER_COMMAND`, and guaranteeing the Docker invocation uses the fixed entrypoint with validated env exports only.

## Impact
- **Host code**: `src/forklift/cli.py` (new CLI flags, env validation, removal of custom command plumbing) plus a new helper module (e.g., `src/forklift/opencode_config.py`) for parsing `.opencode.env`.
- **Container build**: `docker/kitchen-sink/Dockerfile`, new scripts under `docker/kitchen-sink/opencode/` or `/opt/opencode/` (installer output, `start_server.sh`, `entrypoint.sh`), and updated `docker/kitchen-sink/.dockerignore` if needed.
- **Harness**: `docker/kitchen-sink/harness/run.sh` (command execution, logging), optional helper scripts for log rotation.
- **Operator assets**: Documented `.config/forklift/opencode.env` file, instructions to run `docker build -t forklift/kitchen-sink:latest docker/kitchen-sink`, and new troubleshooting/log-inspection steps in README/FORK.md guidance/forklift-v0-design.md.
- **Testing**: Need a manual smoke test documented in README verifying that running `uv run forklift --debug --model claude-35-sonnet` produces populated `/harness-state/opencode-{server,client}.log` files and that removing a required env key causes the CLI to abort before container launch.
