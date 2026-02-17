## Context
Forklift’s v0 harness currently starts the kitchen-sink container but never launches a real agent. The harness script only prints instructions and, unless the operator injects `FORKLIFT_DOCKER_COMMAND`, nothing happens. Secrets are unmanaged: running an agent would require pushing API keys into the container environment with no guarantees about who can read them. The Docker image also lacks OpenCode binaries or any bootstrap scripts. As a result, every run is a no-op and users must orchestrate agents manually.

The OpenCode architecture we aligned on requires a split server/client model: root starts the server using crowned credentials and listens on a Unix socket; a non-privileged client connects to that socket and works inside `/workspace`. The host orchestrator must source secrets from a deterministic location (`~/.config/forklift/opencode.env`), surface only the safe knobs (model, variant, agent), and pass them through to the container. Harness logs must continue landing in `/harness-state` for auditing. Documentation must teach operators how to provision the `.env` file, rebuild the container, and interpret run artifacts.

## Goals / Non-Goals

**Goals:**
- Ship a deterministic OpenCode integration that boots automatically on every run (no ad-hoc commands).
- Keep OpenCode credentials confined to trusted contexts: host `.env` → container root server → Unix socket, never exposed to the `forklift` user or `/workspace`.
- Provide typed CLI controls for selecting model/variant/agent while preventing arbitrary shell injection.
- Capture full agent logs (server + client) under `/harness-state` so operators can audit each run.
- Document operator workflow for configuring secrets, selecting models, and understanding the new harness behavior.

**Non-Goals:**
- Supporting alternative agent providers (oh-my-pi, custom scripts) within this change.
- Building a UI or interactive selector for models/variants—only CLI/env overrides.
- Implementing dynamic download/update of OpenCode binaries post-build; they’re baked into the image at build time.
- Introducing new verification gates (tests, telemetry). Focus is just wiring the agent.

## Decisions

1. **Host secret & option plumbing**
   - Add a config loader (e.g., `src/forklift/config.py`) that reads `~/.config/forklift/opencode.env`. The loader parses simple `KEY=VALUE` lines (respecting comments/blank lines) and returns a dict.
   - `Forklift` CLI grows optional arguments `--model`, `--variant`, and `--agent` (with defaults configured in the env). Values are validated against a safe pattern (alphanumeric + `-`/`_`) and shell-quoted via `shlex.quote` before being forwarded to the harness command array.
   - Legacy `FORKLIFT_DOCKER_COMMAND` is removed. Instead, the CLI constructs a fixed container command like `['/opt/forklift/harness/run.sh']` plus env exports `OPENCODE_MODEL`, `OPENCODE_VARIANT`, `OPENCODE_AGENT`. This ensures there is no arbitrary command execution inside the container.
   - Only the env keys described in `opencode.env` (API key, org, default model/variant/agent) are forwarded via `docker run -e`. The CLI warns and aborts if required entries are missing.

2. **Container entrypoint layering**
   - The Dockerfile installs OpenCode via `curl -fsSL https://opencode.ai/install | bash` with `OPENCODE_VERSION` pinned. Binaries land under `/opt/opencode/bin`.
   - A new root-owned script `/opt/opencode/start_server.sh` reads the forwarded env vars, writes a temp config (if OpenCode needs one), and starts `opencode server` binding to `/opt/opencode/opencode.sock`. Logs are tee’d to `/harness-state/opencode-server.log` for auditing.
   - Entry point becomes `/opt/opencode/entrypoint.sh`:
     1. Start the server (as root) and poll until `/opt/opencode/opencode.sock` exists.
     2. `chown root:opencode` and `chmod 660` the socket. User `forklift` joins group `opencode` during image build so it can connect.
     3. `su -s /bin/bash -c /opt/forklift/harness/run.sh forklift` handing off to the existing harness script.
     4. Trap SIGTERM/SIGINT to shut down the server cleanly before exiting, ensuring API credentials don’t leak.

3. **Harness responsibilities**
   - `run.sh` continues to print instructions and FORK.md context into `/harness-state`. After that it launches the OpenCode client by default:
     ```bash
     /opt/opencode/bin/opencode run \
       --socket /opt/opencode/opencode.sock \
       --model "$OPENCODE_MODEL" \
       --variant "$OPENCODE_VARIANT" \
       --agent "$OPENCODE_AGENT" \
       --workspace /workspace \
       --instructions-file /harness-state/instructions.txt \
       "$(cat /harness-state/fork-context.md 2>/dev/null || printf 'No FORK context provided.')"
     ```
   - Client stdout/stderr are redirected to `/harness-state/opencode-client.log` so the host can inspect transcripts post-run.
   - If the host passes explicit overrides via CLI options, the harness simply consumes `OPENCODE_MODEL/VARIANT/AGENT` env variables. No shell parsing occurs inside the container.

4. **Documentation & tooling**
   - README & `forklift-v0-design.md` gain sections covering the OpenCode architecture, `.env` requirements, default model/variant choices, and expected log files.
   - FORK.md template may add guidance reminding repo owners their notes are fed verbatim into the agent’s positional argument.
   - A helper command (`uv run forklift --print-opencode-env`) is optional but not required; we’ll document manual editing instead.

## Risks / Trade-offs

- **Misconfigured `.env` (missing keys or bad permissions)** → Host CLI validation aborts early and logs a clear error about which variable is missing.
- **Server startup failure (bad installer, invalid version)** → Entry point detects non-zero exit from `opencode server`, writes the error to `/harness-state/opencode-server.log`, and exits with status 1 so the host surfaces the failure.
- **Socket permission drift (container image bug)** → Include automated chown/chmod in entry point and add a harness sanity check that aborts if it cannot connect, writing diagnostics to the client log.
- **Model/variant typos** → Host CLI uses regex validation to reject suspicious values; harness logs the chosen values at start to aid debugging.
- **Long-term maintenance of pinned installer** → Document the pinned `OPENCODE_VERSION` and keep the curl command isolated so updates require only changing the ARG and checksum.

## Migration Plan

1. Implement host CLI changes: add env loader, CLI flags, removal of `FORKLIFT_DOCKER_COMMAND`, and env validation.
2. Build the new Docker image that installs OpenCode, adds the entrypoint and server scripts, and configures user/groups.
3. Update harness scripts to run the OpenCode client by default and emit logs to `/harness-state`.
4. Update documentation (README, forklift-v0-design.md, FORK.md guidance) with the new setup and troubleshooting steps.
5. Smoke-test on a sample repo: ensure `$XDG_STATE_HOME/forklift/runs/.../harness-state` (defaults to `~/.local/state/forklift/runs/.../harness-state`) contains server/client logs and that the agent operates using the provided `.env` settings.
6. Roll out by publishing the new image tag (still `forklift/kitchen-sink:latest`) and instructing operators to rebuild via `docker build ...`.

## Open Questions
- What default values should we ship for model/variant/agent? (Need confirmation from stakeholders on preferred OpenCode stack.)
- Do we need to support multiple OpenCode versions in parallel, or is a single pinned version sufficient for v0?
- Should the host CLI provide a command to verify `~/.config/forklift/opencode.env` before running (e.g., `forklift check-opencode`)?
