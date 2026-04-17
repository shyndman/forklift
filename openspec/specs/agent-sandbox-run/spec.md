# agent-sandbox-run Specification

## Purpose
TBD - created by archiving change forklift-v0. Update Purpose after archive.
## Requirements
### Requirement: Workspace isolation inside container
The agent container SHALL mount only the run's `workspace` and `harness-state` directories, mapped to `/workspace` and `/harness-state` respectively, exposing no Git remotes, SSH keys, or host credentials. Within the container the workspace MUST behave as a regular Git repository but without any configured remotes, and the container SHALL allow outbound network access solely for dependency downloads.

#### Scenario: Remote-free workspace
- **WHEN** the agent inspects `git remote -v` inside `/workspace`
- **THEN** it observes no configured remotes and cannot push to any external repository, while still being able to read/write files inside the workspace

### Requirement: Standard toolchain availability
The container SHALL provide Ubuntu 24.04 userland with preinstalled Git, common build-essential tools, Python 3 with pip/venv, Node.js via `n`, Bun, Rust via rustup, jq, ripgrep, fd, tree, and the selected agent harness (oh-my-pi or OpenCode). These tools MUST be available without further installation so the agent can build and test arbitrary projects.

#### Scenario: Tool discovery
- **WHEN** the agent attempts to run `git`, `python3`, `npm`, `bun`, or `cargo` inside the container
- **THEN** each command executes successfully using the preinstalled toolchain without requiring root access or additional downloads beyond project-specific dependencies

### Requirement: Container build definition
The repository SHALL provide `docker/kitchen-sink/Dockerfile` that builds the `forklift/kitchen-sink:latest` image FROM `ubuntu:24.04`, installs system packages (`git`, `build-essential`, `cmake`, `pkg-config`, `python3`, `python3-venv`, `python3-pip`, `curl`, `wget`, `unzip`, `ca-certificates`, `openssl`, `libssl-dev`), runtimes (Rust via rustup, Node.js via `n`, Bun), PyEnv, jq, ripgrep, fd, tree, make, bash-completion, and copies the agent harness into `/opt/forklift/harness` with entrypoint `/opt/forklift/harness/run.sh`.

#### Scenario: Image build succeeds
- **WHEN** a maintainer runs `docker build -t forklift/kitchen-sink:latest docker/kitchen-sink`
- **THEN** the build completes without manual intervention and the resulting image contains the listed toolchain and harness entrypoint

### Requirement: Agent instructions, rebase gating, and STUCK reporting
Upon startup the harness SHALL parse optional `FORK.md` front matter only when line 1 is `---` and a closing `---` delimiter is present. The front matter MAY define `setup` as a string command (including multiline block strings), MAY define `rebase.continue_check` as a single shell string or block string, and MAY define `changelog.exclude` as an ordered list of non-empty string patterns. Unknown front-matter keys, invalid `rebase` or `changelog` object shapes, or non-string exclusion entries SHALL cause parse failure. If parsing fails, the harness SHALL terminate before agent launch with a non-zero exit status. When `setup` is present, the harness SHALL execute it with `bash -lc` in `/workspace`, enforce a 180-second timeout, mirror stdout/stderr to `/harness-state/setup.log`, surface failures in top-level container stdout/stderr, and fail closed (non-zero exit before agent launch) if setup exits non-zero, times out, or leaves tracked git changes in the workspace. When `rebase.continue_check` is present, the harness SHALL snapshot it into `/harness-state/rebase-continue-check.sh` before agent launch, mediate paused-rebase `git rebase --continue`, and allow continuation only when the snapped command exits zero and leaves tracked, staged, and untracked workspace state unchanged. Explicit agent `git rebase --skip` decisions SHALL be recorded in `/harness-state/rebase-skipped-commits.json`, mechanically empty auto-skips SHALL remain unrecorded, and `git rebase --abort` SHALL be rejected until `STUCK.md` exists with non-whitespace content. If `FORK.md` exists, the harness SHALL provide only the body content (front matter stripped) to the agent context before work begins. Immediately after rendering these instructions the harness SHALL invoke the bundled `opencode run` client, passing the rendered instructions plus stripped FORK context as inputs, and MUST log the client’s stdout/stderr to `/harness-state/opencode-client.log` for auditing. `STUCK.md` SHALL remain dedicated to agent-authored blocked-work outcomes.

#### Scenario: Setup succeeds and agent launches
- **WHEN** `FORK.md` includes valid front matter with `setup: uv sync` and the command exits successfully within 180 seconds without tracked git changes
- **THEN** `/harness-state/setup.log` contains mirrored setup output, front matter is omitted from agent-visible context, and `opencode run` is launched normally with client transcript logging

#### Scenario: Setup fails closed
- **WHEN** `FORK.md` includes `setup`, and the setup command exits non-zero or exceeds 180 seconds
- **THEN** the harness exits non-zero before invoking `opencode run`, and operators can see the failure in the top-level container stdout/stderr without opening `/harness-state/setup.log`

#### Scenario: Continue check gates paused rebase progress
- **WHEN** `FORK.md` includes `rebase.continue_check`, the agent reaches a paused rebase, and it runs `git rebase --continue`
- **THEN** the harness executes the snapped `/harness-state/rebase-continue-check.sh`, blocks continuation when the command exits non-zero or changes workspace state, and surfaces the full failure output in top-level container stderr

#### Scenario: Explicit skip is recorded and abort requires STUCK
- **WHEN** the agent runs `git rebase --skip` during a paused rebase, or later attempts `git rebase --abort`
- **THEN** the harness records the skipped `REBASE_HEAD` SHA/subject in `/harness-state/rebase-skipped-commits.json`, and rejects abort until `STUCK.md` exists with non-whitespace content

#### Scenario: Changelog metadata is accepted without altering setup behavior
- **WHEN** `FORK.md` front matter includes a valid `changelog.exclude` list and an optional valid `setup` entry
- **THEN** front matter parsing succeeds, setup execution semantics remain unchanged, and agent launch proceeds when setup gates pass

#### Scenario: Malformed front matter prevents agent launch
- **WHEN** `FORK.md` begins with `---` but lacks valid closing front matter delimiters or parsable structure
- **THEN** the harness exits non-zero before invoking `opencode run` and does not treat the failure as an agent-authored `STUCK.md` outcome

### Requirement: Test execution best-effort
The agent SHALL attempt to detect and run the project's primary test command(s) within the time budget after applying upstream changes. Failures SHALL be reported at the end of the run via commit messages and/or `STUCK.md`, but a failing test does not automatically abort the merge attempt unless the agent judges it unrecoverable.

#### Scenario: Failing tests reported
- **WHEN** the agent runs tests discovered from the project metadata (e.g., `npm test`) and they fail
- **THEN** it records the failure details in its logs and any STUCK.md or final message so the maintainer can understand the issue

### Requirement: OpenCode socket access control
The container SHALL expose the OpenCode server only on `127.0.0.1:$OPENCODE_SERVER_PORT`, ensuring the bundled `forklift` user is the only process that can attach. Any additional user inside the container MUST be denied access by group membership and filesystem permissions on `/harness-state`.

#### Scenario: Socket restricted to forklift
- **WHEN** the harness attaches to `http://127.0.0.1:$OPENCODE_SERVER_PORT` as user `forklift`
- **THEN** the connection succeeds, while a different unprivileged user attempting the same connection receives a permission error, demonstrating that only the intended client can reach the server

