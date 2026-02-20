# Forklift

Forklift is a host-side orchestrator that keeps your fork of an upstream repository fresh. It discovers your `origin` and `upstream` remotes, snapshots the repo into `$XDG_STATE_HOME/forklift/runs/<project>_<timestamp>` (defaults to `~/.local/state/forklift/runs/<project>_<timestamp>`), launches an isolated "kitchen-sink" container for at most eight minutes, and either opens a pull request or leaves a `STUCK.md` explaining what blocked progress.

## Requirements

- A Git repository with `origin` (your fork) and `upstream` (source) remotes configured
- [Docker](https://docs.docker.com/get-started/) available on the host
- [uv](https://docs.astral.sh/uv/) for running the Python CLI (`pip install uv` if needed)

## Building the container once

```bash
docker build -t forklift/kitchen-sink:latest docker/kitchen-sink
```

The image contains Ubuntu 24.04 plus Git, build-essential, Python 3, Node (via `n`), Bun, Rust (via rustup), jq, ripgrep, fd, tree, the OpenCode CLI, and the harness stack located under `/opt/opencode` + `/opt/forklift`.

## Running Forklift

```bash
uv run forklift --debug

uv run forklift --version  # Print version and exit
```

What happens:

1. `forklift` resolves the current repo, verifies `origin`/`upstream`, and fetches both.
2. It creates `$XDG_STATE_HOME/forklift/runs/<project>_<YYYYMMDD_HHMMSS>/` (defaults to `~/.local/state/forklift/runs/...`) with:
   - `workspace/` – self-contained clone of your repo with remotes removed (we avoid `git clone --shared` to ensure the sandbox never depends on external object stores)
   - `harness-state/` – writable directory the container uses for logs and instructions
   - `metadata.json` – records source repo, timestamp, main branch, and upstream SHA
3. `FORK.md` (if present in your repo) is copied into the workspace before remotes are stripped so the agent sees your context.
4. The kitchen-sink container launches with `/workspace` and `/harness-state` bind-mounted read-write as UID/GID 1000. The entrypoint starts the OpenCode server on `127.0.0.1:$OPENCODE_SERVER_PORT`, waits for the HTTP health check to succeed, and then hands off to `/opt/forklift/harness/run.sh`. The harness prints default instructions into `/harness-state/instructions.txt`, echoes the FORK.md contents (or notes the absence), logs the OpenCode client command, and streams the client transcript into `/harness-state/opencode-client.log`.
5. The agent has eight minutes. If it finishes cleanly, the host verifies `git merge-base --is-ancestor upstream/main main` and prompts you to push + create a PR. If it cannot finish, it writes `STUCK.md` inside the run directory; the host surfaces that status via exit code 4 and leaves the file for you to inspect.

## Configuring OpenCode

Forklift reads OpenCode credentials and defaults from `~/.config/forklift/opencode.env`. The file is a simple `KEY=VALUE` format with `#` comments and blank lines allowed. Required keys:

- `OPENCODE_VARIANT`
- `OPENCODE_AGENT`
- `OPENCODE_SERVER_PASSWORD`

Optional keys:

- `OPENCODE_ORG`
- `OPENCODE_MODEL` (omit to let OpenCode select its default)
- `OPENCODE_TIMEOUT` (seconds)
- `OPENCODE_SERVER_PORT` (defaults to `4096`)
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `ANTHROPIC_API_KEY`
- `OPENROUTER_API_KEY`

Example template:

```
OPENCODE_API_KEY=sk-...
OPENCODE_MODEL=claude-35-sonnet
OPENCODE_VARIANT=production
OPENCODE_AGENT=default-agent
OPENCODE_SERVER_PASSWORD=server-passphrase
OPENCODE_ORG=acme
OPENCODE_TIMEOUT=480
OPENCODE_SERVER_PORT=4096
```

The file should be owned by you with `0600` permissions. At runtime the CLI logs which file path was used (masking secrets) to aid troubleshooting. At least one provider API key (`OPENCODE_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`) must be present; populate whichever ones your workflow requires.

The CLI also exposes typed overrides:

```
uv run forklift --model claude-35-sonnet --variant production --agent nightly
```

Each override must avoid shell metacharacters, but forward slashes are allowed for provider-scoped model names (e.g. `google/gemini-3-flash-preview`); invalid values abort the run before any secrets are forwarded. Overrides only adjust the client inputs—the Docker entrypoint is fixed to `/opt/opencode/entrypoint.sh`.

### Environment overrides

- `FORKLIFT_DOCKER_IMAGE` – alternate container image (defaults to `forklift/kitchen-sink:latest`)
- `FORKLIFT_DOCKER_ARGS` – extra `docker run` flags appended before the image (for GPU devices, proxies, etc.)
- `FORKLIFT_TIMEOUT_SECONDS` – adjust the watchdog (default 480 seconds / 8 minutes)
- `DOCKER_BIN` – override the Docker CLI binary name/path if needed

Because the entrypoint is fixed, `FORKLIFT_DOCKER_COMMAND` is no longer honored.

## Outputs to inspect

- Successful run: commits under `workspace/` plus host-side PR instructions in the log
- Blocked run: `workspace/STUCK.md` describing what the agent needs
- Always:
  - `/harness-state/instructions.txt` with rendered guidance
  - `/harness-state/fork-context.md` snapshotting FORK.md (or noting that none was provided)
  - `/harness-state/opencode-server.log` with the server bootstrap and shutdown transcript
  - `/harness-state/opencode-client.log` with the OpenCode client stdout/stderr

Old run directories remain under `$XDG_STATE_HOME/forklift/runs/` (or `~/.local/state/forklift/runs/` if `XDG_STATE_HOME` is unset) for auditing. Safe to delete when no longer needed.

## FORK.md guidance

Add a `FORK.md` at the repo root to explain what makes your fork special. Recommended sections:

```
# Fork Context

## Mission / Themes
- Why this fork exists
- Non-negotiable behaviors or files

## Test & Verification Guidance
- Commands to run (npm test, uv run pytest, etc.)
- Long-running suites that can be skipped

## Risky Areas
- Directories or files that should stay untouched
- Any vendor or generated assets to preserve

## Contacts
- Who to mention in STUCK.md for help
```

Forklift copies this file into every workspace and appends its contents to the harness instructions. The exact text is also forwarded as the positional argument to `opencode run`, so keep it short, high-signal, and updated.

## Smoke test

After editing the Docker image or OpenCode integration, rebuild the image and run a smoke test to confirm both logs populate:

```bash
docker build -t forklift/kitchen-sink:latest docker/kitchen-sink
uv run forklift --debug --model claude-35-sonnet --variant production --agent default
```

Inspect `~/.local/state/forklift/runs/<latest>/harness-state/opencode-{server,client}.log` to verify the server bootstraps, the instructions render, and the client transcript is captured end-to-end.

## Development

- Run type checking: `uv run basedpyright`
- Build container changes: `docker build -t forklift/kitchen-sink:latest docker/kitchen-sink`
