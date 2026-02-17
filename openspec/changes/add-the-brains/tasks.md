## 1. Host CLI & Config

- [ ] 1.1 Implement `.opencode_env` loader that reads `~/.config/forklift/opencode.env`, validates required keys, and exposes sanitized values
- [ ] 1.2 Add `--model`, `--variant`, and `--agent` options to the `forklift` CLI, including value validation and integration with the container env export path
- [ ] 1.3 Remove `FORKLIFT_DOCKER_COMMAND` usage, ensuring the container command is fixed to the harness entrypoint while still forwarding the validated OpenCode env values

## 2. Container Image & Entry Point

- [ ] 2.1 Update `docker/kitchen-sink/Dockerfile` to install the pinned OpenCode binaries via the official installer, add the `opencode` group, and place new server/client scripts
- [ ] 2.2 Create a root-owned entrypoint script that starts the OpenCode server, manages `/opt/opencode/opencode.sock` permissions, tails logs into `/harness-state`, and hands off to the `forklift` user
- [ ] 2.3 Ensure container shutdown traps stop the server cleanly and remove any lingering sockets or sensitive files

## 3. Harness Invocation & Logging

- [ ] 3.1 Rewrite `docker/kitchen-sink/harness/run.sh` (or add a sibling script) so that after rendering instructions/FORK.md it always executes `opencode run` with the configured model/variant/agent, capturing stdout/stderr to `/harness-state/opencode-client.log`
- [ ] 3.2 Add logging to both server and client scripts so `/harness-state` contains deterministic logs for every run, including failures to connect or authenticate

## 4. Documentation & Verification

- [ ] 4.1 Update README, FORK.md guidance, and `forklift-v0-design.md` to explain the OpenCode integration, `.env` setup, available CLI flags, and new harness log files
- [ ] 4.2 Provide smoke-test instructions (e.g., sample `forklift` run) demonstrating the new flow and how to inspect server/client logs
