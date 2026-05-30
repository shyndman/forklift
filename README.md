# Forklift

Keep your fork fresh with AI-powered rebasing.

Forklift runs an AI agent in an isolated container to rebase your fork against upstream, then hands you a local branch to review. If it gets stuck, it writes a `STUCK.md` explaining what blocked progress.

## Quick Start

```bash
# Build the container (once)
docker build -t forklift/kitchen-sink:latest docker/kitchen-sink

# Configure OpenCode credentials
cp ~/.config/forklift/opencode.env.example ~/.config/forklift/opencode.env
# Edit opencode.env with your API keys and settings

# Run in your fork
cd your-fork-repo
uv run forklift --debug
```

## Prerequisites

- Git repo with `origin` (your fork) and `upstream` (source) remotes
- [Docker](https://docs.docker.com/get-started/)
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- [git-filter-repo](https://github.com/newren/git-filter-repo) 2.47.0+ (`pip install git-filter-repo==2.47.0` or `brew install git-filter-repo`)
- Git identity configured (`git config user.name` / `git config user.email`)

## Usage

### Basic run

```bash
uv run forklift --debug
uv run forklift --version
```

### Options

| Flag | Description |
|------|-------------|
| `--main-branch=<name>` | Target branch if not `main` |
| `--target-policy=tip` | Rebase to upstream branch tip (default) |
| `--target-policy=latest-version` | Rebase to latest stable tag (`X.Y.Z` or `vX.Y.Z`) |
| `--timeout-seconds=<n>` | Override agent timeout (default: 1500) |
| `--model`, `--variant`, `--agent` | Override OpenCode settings per-run |

### Changelog preflight

Preview what's changed upstream before running the full sync:

```bash
uv run forklift changelog
uv run forklift changelog --main-branch=dev
uv run forklift changelog --target-policy=latest-version
```

This runs entirely on the host with no container launch or history mutation. `forklift changelog`
accepts the same `--target-policy` values as the main command, so you can compare against
either `upstream/<main-branch>` or the latest stable upstream tag. Requires Git 2.38+.

### Fork-owned files

List files that are absent from upstream and are generally safer places to make fork-specific changes:

```bash
uv run forklift files
uv run forklift files --main-branch=dev
uv run forklift files --hash
```

This command uses local refs only, ignores uncommitted files, and prints plain text output for easy piping or agent consumption.

### First divergent commit

Print the first commit that exists on your fork branch but not on upstream:

```bash
uv run forklift first
uv run forklift first --main-branch=dev
```

This command uses local refs only, prints the full SHA of the earliest fork-only commit, and exits non-zero when no fork-only commits exist.

## Configuration

Create `~/.config/forklift/opencode.env` (mode `0600`):

```
OPENCODE_API_KEY=sk-...
OPENCODE_VARIANT=production
OPENCODE_AGENT=default-agent
OPENCODE_SERVER_PASSWORD=server-passphrase

# Optional
OPENCODE_ORG=acme
OPENCODE_MODEL=claude-35-sonnet
OPENCODE_TIMEOUT=1500
OPENCODE_SERVER_PORT=4096

# Provider keys (at least one required)
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GOOGLE_GENERATIVE_AI_API_KEY=...
OPENROUTER_API_KEY=...
```

### Environment overrides

| Variable | Description |
|----------|-------------|
| `FORKLIFT_DOCKER_IMAGE` | Alternate container image |
| `FORKLIFT_DOCKER_ARGS` | Extra `docker run` flags (GPU, proxies, etc.) |
| `DOCKER_BIN` | Override Docker CLI path |

## FORK.md

Add a `FORK.md` to your repo root to give the agent context about your fork. If the repo-root file is absent, Forklift falls back to `.agents/FORK.md`. See the [template](FORK.md) for format and examples.

### Front matter gates

`FORK.md` front matter is optional, but when present it is strict and machine-validated.

- `setup` runs once in `/workspace` before agent launch with a 180-second timeout.
- `rebase.continue_check` runs from `/workspace` before every mediated `git rebase --continue`.
- Continue checks must exit zero and leave tracked, staged, and untracked workspace state unchanged.
- Forklift snapshots the active continue check into `/harness-state/rebase-continue-check.sh` before the agent starts, so editing the workspace copy of `FORK.md` during the run does not change enforcement.
- Explicit `git rebase --skip` decisions are recorded and appended to the final terminal completion summary under `Skipped Commits`.
- `git rebase --abort` is rejected until `STUCK.md` exists and contains non-whitespace content.

## Outputs

Run artifacts are stored in `~/.local/state/forklift/runs/<project>_<timestamp>/`:

| Path | Description |
|------|-------------|
| `workspace/` | Cloned repo where the agent worked |
| `workspace/STUCK.md` | Written if the agent couldn't complete |
| `control/` | Host-owned control mount used for live structured rebase events during the run |
| `harness-state/rebase-continue-check.sh` | Frozen copy of `FORK.md` `rebase.continue_check` for the active run |
| `harness-state/rebase-skipped-commits.json` | Host-owned metadata for explicit agent `git rebase --skip` decisions |
| `harness-state/opencode-client.log` | Agent transcript |
| `harness-state/opencode-server.log` | Server bootstrap log |
| `harness-state/setup.log` | Mirrored setup diagnostics and command output |
| `opencode-logs/` | Full OpenCode debug traces |

Forklift now emits live top-level `Rebase N/Total`, `Conflict N/Total`, `Continue N/Total`, `Skip N/Total`, `Auto-skip N/Total`, and `Rebase complete` log lines while the container is still running. Those structured events travel over a dedicated bind-mounted Unix domain socket in `control/`; `Container stdout` / `Container stderr` remain post-exit human-log surfaces, and `opencode-client.log` remains the deep transcript artifact.

On success, Forklift publishes a review branch: `upstream-merge/<timestamp>/<branch>`.

Run directories older than 7 days are automatically pruned.

## How it works

```mermaid
flowchart LR
    A[Your repo] --> B[Snapshot clone]
    B --> C[Docker sandbox]
    C --> D{Success?}
    D -->|Yes| E[Review branch]
    D -->|No| F[STUCK.md]

    style A fill:#e1f5fe
    style C fill:#fff3e0
    style E fill:#e8f5e9
    style F fill:#ffebee
```

1. Fetches `origin` and `upstream`, checks if sync is needed
2. Creates isolated workspace clone (remotes stripped)
3. Launches container with AI agent
4. Agent rebases onto upstream target, while Forklift mediates `git rebase --continue|--skip|--abort`
5. Success: rewrites commits to your identity, publishes `upstream-merge/...` branch
6. Failure: writes `STUCK.md` for inspection, and the terminal completion summary always appends `Skipped Commits`

## Development

```bash
uv run basedpyright                                    # Type checking
docker build -t forklift/kitchen-sink:latest docker/kitchen-sink  # Rebuild container
```
