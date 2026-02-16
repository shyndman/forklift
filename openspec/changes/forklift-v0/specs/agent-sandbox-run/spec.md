## ADDED Requirements

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

### Requirement: Agent instructions and STUCK reporting
Upon startup the harness SHALL instruct the agent to merge `upstream/main` into `main`, run any discoverable tests, craft meaningful commit messages, and, if blocked, create a `STUCK.md` file summarizing the problem, steps attempted, and current outcome. If `FORK.md` exists at the workspace root, the harness SHALL provide its contents to the agent before work begins.

#### Scenario: Blocked merge
- **WHEN** the agent determines the merge cannot be safely completed within the runtime (e.g., conflicting business logic) and writes `STUCK.md`
- **THEN** the file includes plain-language descriptions of the blocking issue, the attempts made, and the resulting state, enabling a human maintainer to decide next steps

### Requirement: Test execution best-effort
The agent SHALL attempt to detect and run the project's primary test command(s) within the time budget after applying upstream changes. Failures SHALL be reported at the end of the run via commit messages and/or `STUCK.md`, but a failing test does not automatically abort the merge attempt unless the agent judges it unrecoverable.

#### Scenario: Failing tests reported
- **WHEN** the agent runs tests discovered from the project metadata (e.g., `npm test`) and they fail
- **THEN** it records the failure details in its logs and any STUCK.md or final message so the maintainer can understand the issue
