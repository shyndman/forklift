> **Validation Requirement:** Every implementation task must be followed by an explicit validation step describing the evidence that proves the feature works. Any task submitted without its paired validation artifact will be rejected and must be redone. Don't cut corners! Write specific types!

## 1. Host CLI & Config

- [ ] 1.1 Implement `.opencode_env` loader that reads `~/.config/forklift/opencode.env`, validates required keys, and exposes sanitized values
- [ ] 1.1 Validation: Capture automated test output or a CLI run that shows a valid config loads successfully while a malformed file is rejected with actionable errors, including the sanitized env payload that will be forwarded.
- [ ] 1.2 Add `--model`, `--variant`, and `--agent` options to the `forklift` CLI, including value validation and integration with the container env export path
- [ ] 1.2 Validation: Provide `uv run forklift --help` output plus a sample invocation proving the flags accept valid choices, reject invalid ones, and emit the selected values into the container env file.
- [ ] 1.3 Remove `FORKLIFT_DOCKER_COMMAND` usage, ensuring the container command is fixed to the harness entrypoint while still forwarding the validated OpenCode env values
- [ ] 1.3 Validation: Share container-launch logs demonstrating that the harness entrypoint is always invoked, `FORKLIFT_DOCKER_COMMAND` overrides are ignored, and the sanitized OpenCode env values reach the container.

## 2. Container Image & Entry Point

- [ ] 2.1 Update `docker/kitchen-sink/Dockerfile` to install the pinned OpenCode binaries via the official installer, add the `opencode` group, and place new server/client scripts
- [ ] 2.1 Validation: Produce a rebuilt image run log that confirms the installer pinned versions, the `opencode` group exists, and the placed scripts are executable inside the container.
- [ ] 2.2 Create a root-owned entrypoint script that starts the OpenCode server, manages `/opt/opencode/opencode.sock` permissions, tails logs into `/harness-state`, and hands off to the `forklift` user
- [ ] 2.2 Validation: Record startup logs showing the entrypoint boot sequence, socket permission adjustments, log tailing into `/harness-state`, and the final `forklift` user handoff.
- [ ] 2.3 Ensure container shutdown traps stop the server cleanly and remove any lingering sockets or sensitive files
- [ ] 2.3 Validation: Demonstrate via `docker stop` or signal injection that shutdown traps run, the server exits gracefully, and `/opt/opencode/opencode.sock` plus sensitive files are removed.

## 3. Harness Invocation & Logging

- [ ] 3.1 Rewrite `docker/kitchen-sink/harness/run.sh` (or add a sibling script) so that after rendering instructions/FORK.md it always executes `opencode run` with the configured model/variant/agent, capturing stdout/stderr to `/harness-state/opencode-client.log`
- [ ] 3.1 Validation: Provide harness transcript excerpts showing instructions are rendered before `opencode run`, along with the generated `/harness-state/opencode-client.log` containing full stdout/stderr.
- [ ] 3.2 Add logging to both server and client scripts so `/harness-state` contains deterministic logs for every run, including failures to connect or authenticate
- [ ] 3.2 Validation: Attach sample server and client log files demonstrating both successful and failure cases, verifying they land in `/harness-state` with timestamps and error context.

## 4. Documentation & Verification

- [ ] 4.1 Update README, FORK.md guidance, and `forklift-v0-design.md` to explain the OpenCode integration, `.env` setup, available CLI flags, and new harness log files
- [ ] 4.1 Validation: Secure a documentation review or include annotated screenshots confirming each document describes the integration, env setup, CLI flags, and log artifacts consistently.
- [ ] 4.2 Provide smoke-test instructions (e.g., sample `forklift` run) demonstrating the new flow and how to inspect server/client logs
- [ ] 4.2 Validation: Share output from executing the published smoke test (screenshots or log excerpts) proving the flow works end-to-end and that server/client logs are discoverable as documented.
